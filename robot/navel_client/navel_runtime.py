"""Shared SDK streams, coordinate conversion, target association and exclusive motion.
Adapted from the supplied navel_approach_demo/navel_runtime.py.
The application feeds sensor packets; this runtime never starts sensor readers.
No SDK patching. All motion calls use public SDK methods.
"""
import asyncio
from collections import deque, OrderedDict
from dataclasses import dataclass
import inspect
import json
import math
import logging
from contextlib import asynccontextmanager
import time

def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def read_pose(packet):
    odom = getattr(packet, 'odometry', None)
    pos = getattr(odom, 'position', None)
    ori = getattr(odom, 'orientation', None)
    vel = getattr(odom, 'velocity', None)
    x, y = finite(getattr(pos, 'x', None)), finite(getattr(pos, 'y', None))
    q = [finite(getattr(ori, k, None)) for k in ('x', 'y', 'z', 'w')]
    if x is None or y is None or any(v is None for v in q):
        raise ValueError('Missing/nonfinite odometry pose')
    qx, qy, qz, qw = q
    if abs(qz) < 1e-9 and abs(qw) < 1e-9 and abs(math.hypot(qx, qy)-1) < 0.02:
        yaw = wrap(2 * math.atan2(qx, qy))
        angular = finite(getattr(vel, 'linear_y', None))
        layout = '0.15.3_positional_bug_workaround'
    elif abs(qx) < 1e-6 and abs(qy) < 1e-6 and abs(math.hypot(qz, qw)-1) < 0.02:
        yaw = wrap(2 * math.atan2(qz, qw))
        angular = finite(getattr(vel, 'angular_z', None))
        layout = 'standard_planar_quaternion'
    else:
        raise ValueError('Unrecognised odometry quaternion; no goal will be sent')
    linear = finite(getattr(vel, 'linear_x', None))
    if linear is None or angular is None:
        raise ValueError('Missing/nonfinite velocity')
    return dict(x=x, y=y, yaw=yaw, v=linear, w=angular, layout=layout,
                stamp=getattr(odom, 'time', None))


@dataclass
class Config:
    execute: bool = False
    approach_speed: float = 0.25
    stop_distance: float = 0.7  # horizontal base-centre to estimated nose
    distance_tolerance: float = 0.10
    heading_tolerance_deg: float = 4.0
    head_x: float = 0.0
    head_y: float = 0.0
    frame_yaw_deg: float = 0.0
    target_uid: int | None = None
    detection_range: float = 4.0
    speech_timeout_s: float = 5.0
    invert_yield_turn_direction: bool = False
    max_admission_age_ms: int = 15_000

    def __post_init__(self):
        if not math.isfinite(self.speech_timeout_s) or self.speech_timeout_s <= 0:
            raise ValueError('Speech timeout must be finite and positive')
        if not 250 <= self.max_admission_age_ms <= 15_000:
            raise ValueError('Local admission cap must be 250..15000 ms')


class EventLog:
    def emit(self, event, **data):
        logging.getLogger("robot.navel_client.motion").info(
            "%s %s", event, json.dumps(data, allow_nan=False, default=str))


def navel_uid(value):
    """Accept only canonical nonnegative integer UIDs, never foreign track labels."""
    if isinstance(value, bool):
        raise ValueError('Target is not a Navel UID')
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and str(int(value)) == value:
        return int(value)
    raise ValueError('Target is not a canonical Navel UID')


def nose_position(person, cfg):
    pts = [p for p in (getattr(person, 'g_nose', None) or [])
           if getattr(p, 'sys', None) == 3 or getattr(getattr(p, 'sys', None), 'name', None) == 'HEAD_STRAIGHT']
    if len(pts) != 1:
        return None
    xyz = [finite(getattr(pts[0], key, None)) for key in ('x', 'y', 'z')]
    if any(v is None for v in xyz):
        return None
    x, y, z = xyz
    a = math.radians(cfg.frame_yaw_deg)
    x, y = (cfg.head_x+math.cos(a)*x-math.sin(a)*y,
            cfg.head_y+math.sin(a)*x+math.cos(a)*y)
    if not 0.25 < math.hypot(x, y) < 6 or x <= 0:
        return None
    try:
        uid = navel_uid(getattr(person, 'uid', None))
    except ValueError:
        return None
    return dict(uid=uid, x=x, y=y, z=z)


def world_point(p, pose):
    c, s = math.cos(pose['yaw']), math.sin(pose['yaw'])
    return dict(p, wx=pose['x']+c*p['x']-s*p['y'], wy=pose['y']+s*p['x']+c*p['y'])


def body_point(target, pose):
    dx, dy = target['wx']-pose['x'], target['wy']-pose['y']
    c, s = math.cos(pose['yaw']), math.sin(pose['yaw'])
    return c*dx+s*dy, -s*dx+c*dy


def associate(people, target, gate=.5):
    ranked = sorted((math.hypot(p['wx']-target['wx'], p['wy']-target['wy']), i)
                    for i, p in enumerate(people))
    if not ranked or ranked[0][0] > gate:
        return None
    if len(ranked) > 1 and ranked[1][0]-ranked[0][0] < .20:
        return None
    return people[ranked[0][1]]


class Runtime:
    def __init__(self, robot, cfg=None, log=None):
        self.robot, self.cfg, self.log = robot, cfg or Config(), log or EventLog()
        self.stop = asyncio.Event()  # per-action cancellation
        self.shutdown = asyncio.Event()
        self.stop_failure = None
        self.action_active = False
        self.contexts = OrderedDict()
        self.last_odom_stamp = self.last_frame_stamp = None
        self.raw_frame = self.raw_locomotion = None
        self._pose, self.pose_at, self.perception_at = None, 0., 0.
        self.history = deque(maxlen=40)
        self.people, self.target = [], None
        self.frame_seq, self.errors = 0, {}
        self.seed, self.seed_count, self.seed_time, self.seed_last = None, 0, 0., 0.
        self.detect_seq = -1
        self.motion_active = False

    @asynccontextmanager
    async def action(self, uid=None, seed=None, *, behavior="APPROACH"):
        """Lease wheels and reset acquisition without restarting any sensor reader."""
        if self.action_active or self.motion_active:
            raise RuntimeError('Another wheel-control behaviour is active')
        if self.stop_failure or self.shutdown.is_set():
            raise RuntimeError(self.stop_failure or 'Runtime is shutting down')
        if behavior == 'APPROACH':
            uid = navel_uid(uid)
        elif behavior != 'YIELD' or uid is not None or seed is not None:
            raise ValueError('Invalid action lease or YIELD target acquisition')
        self.action_active = True
        self.stop.clear()
        self.target = None
        self.seed = seed.copy() if seed else None
        self.seed_count, self.seed_time = 0, time.monotonic()
        self.seed_last = time.monotonic() if seed else 0.
        self.detect_seq = -1
        self.cfg.target_uid = uid
        try:
            self.check(require_perception=True)
            pose = self.pose()
            if abs(pose['v']) > .02 or abs(pose['w']) > .03:
                raise RuntimeError('Base already moving; stop other controllers first')
            yield
        except BaseException:
            if self.cfg.execute and not self.stop_failure:
                await self.cleanup_motion()
            raise
        finally:
            self.action_active = False

    def ingest_odometry(self, packet):
        """Called only by the application's single locomotion collector."""
        self.raw_locomotion = packet
        pose = read_pose(packet)
        stamp = pose['stamp']
        if stamp not in (None, 0) and self.last_odom_stamp is not None and stamp <= self.last_odom_stamp:
            return False
        self.last_odom_stamp = stamp
        self._pose, self.pose_at = pose, time.monotonic()
        self.history.append(pose)
        self.errors.pop('odometry', None)
        return True

    def ingest_perception(self, frame):
        """Called only by the application's single perception collector."""
        self.raw_frame = frame
        stamp = getattr(frame, 'time', None)
        if stamp not in (None, 0) and self.last_frame_stamp is not None and stamp <= self.last_frame_stamp:
            return False
        self.last_frame_stamp = stamp
        now = time.monotonic()
        self.perception_at, self.frame_seq = now, self.frame_seq+1
        pose = self.frame_pose(stamp) if self._pose is not None else None
        self.people = [dict(world_point(p, pose), seen_at=now, frame_seq=self.frame_seq)
                       for person in (getattr(frame, 'persons', None) or [])
                       if (p := nose_position(person, self.cfg)) is not None] if pose else []
        self.errors.pop('perception', None)
        if self.target is not None:
            chosen = self.follow(self.target)
            if chosen is not None:
                self.target = chosen.copy()
        # Preserve the identity selected in an older HTTP observation across ordinary UID changes.
        for context in self.contexts.values():
            for anchor in context['targets'].values():
                chosen = self.follow(anchor)
                if chosen is not None:
                    anchor.update(chosen)
        return True

    def follow(self, anchor):
        if time.monotonic()-anchor['seen_at'] >= 2:
            return None
        return associate(self.people, anchor, gate=.5)

    def remember_observation(self, observation):
        """Only locally converted Navel frames may establish response provenance."""
        targets = {}
        for human in observation['humans']:
            uid = human['track_id']
            candidates = [p for p in self.people if str(p['uid']) == uid]
            if len(candidates) == 1:
                targets[uid] = candidates[0].copy()
        self.contexts[observation['observation_id']] = dict(
            timestamp_us=observation['timestamp_us'], targets=targets)
        cutoff = time.monotonic_ns()//1000 - 60_000_000
        while self.contexts and (len(self.contexts) > 2048 or
                next(iter(self.contexts.values()))['timestamp_us'] < cutoff):
            self.contexts.popitem(last=False)

    def intent_context(self, intent):
        context = self.contexts.get(intent.observation_id)
        if context is None or intent.created_at_us != context['timestamp_us']:
            raise ValueError('Unknown observation source or mismatched intent clock origin')
        deadline = (context['timestamp_us'] + min(intent.valid_for_ms, self.cfg.max_admission_age_ms)*1000)/1e6
        if time.monotonic() >= deadline:
            raise TimeoutError('Intent expired since source observation (including HTTP/LLM latency)')
        return context, deadline

    def resolve(self, context, target_id):
        navel_uid(target_id)
        self.check(require_perception=True)
        anchor = context['targets'].get(target_id)
        candidate = self.follow(anchor) if anchor else None
        if candidate is None or time.monotonic()-candidate['seen_at'] > .4:
            raise ValueError('Requested target unavailable, stale, or ambiguous')
        return candidate.copy()

    def pose(self):
        if self._pose is None or time.monotonic()-self.pose_at > .6 or 'odometry' in self.errors:
            raise RuntimeError('Odometry unavailable/stale: '+str(self.errors))
        return self._pose.copy()

    def check(self, require_perception=False):
        if self.shutdown.is_set():
            raise RuntimeError('Runtime is shutting down')
        if self.stop.is_set() and self.action_active:
            raise asyncio.CancelledError('Operator stop')
        self.pose()
        if require_perception and (time.monotonic()-self.perception_at > .8 or 'perception' in self.errors):
            raise RuntimeError('Perception stream unavailable/stale: '+str(self.errors))

    def frame_pose(self, stamp):
        current = self.pose()
        if stamp and self.history and current['stamp']:
            stamped = [p for p in self.history if p['stamp']]
            nearest = min(stamped, key=lambda p: abs(p['stamp']-stamp))
            # Supplied SDK timestamps use microseconds; skip badly unsynchronised positions.
            return nearest if abs(nearest['stamp']-stamp) <= 180000 else None
        return current

    def detect(self):
        """Three fresh spatially associated detections; UID only for initial selection."""
        self.check(require_perception=True)
        if self.frame_seq == self.detect_seq:
            return None
        self.detect_seq = self.frame_seq
        now = time.monotonic()
        eligible = [p for p in self.people if math.hypot(p['x'], p['y']) <= self.cfg.detection_range
                    and abs(math.atan2(p['y'], p['x'])) <= math.radians(60)]
        if self.seed is not None and now-self.seed_last > .5:
            raise RuntimeError('Target acquisition lost or ambiguous')
        chosen = associate(eligible, self.seed, .35) if self.seed else None
        if chosen is None and self.seed is not None:
            self.seed_count = 0
            return None
        if chosen is None:
            eligible = [p for p in eligible if self.cfg.target_uid is None or p['uid'] == self.cfg.target_uid]
            if self.cfg.target_uid is not None and len(eligible) != 1:
                eligible = []
            if not eligible:
                self.seed, self.seed_count = None, 0
                return None
            if len(eligible) != 1:
                return None
            chosen = eligible[0]
            self.seed, self.seed_count, self.seed_time = chosen, 1, now
        else:
            self.seed_count += 1
            self.seed = chosen
        self.seed_last = now
        if self.seed_count >= 3 and now-self.seed_time >= .18:
            self.target = dict(chosen, seen_at=now, frame_seq=self.frame_seq)
            return self.target.copy()
        return None

    async def fresh_target(self, after, timeout=1.5):
        until = time.monotonic()+timeout
        while time.monotonic() < until:
            self.check()
            if self.target and self.target['seen_at'] > after and time.monotonic()-self.target['seen_at'] < .4:
                return self.target.copy()
            await asyncio.sleep(.02)
        return None

    async def cleanup_motion(self, sender=None):
        """Shield SDK cancellation and stop confirmation from action cancellation.

        A cancellation arriving during normal arc cleanup must not interrupt the
        stop procedure. Propagate it only after cleanup, or report stop failure.
        """
        async def finish():
            if sender is not None:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            await self.settle()

        cleanup = asyncio.create_task(finish())
        cancelled = False
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def settle(self):
        if not self.cfg.execute:
            return
        try:
            await self._settle()
        except BaseException as exc:
            self.stop_failure = 'Stop could not be confirmed: '+str(exc)
            raise RuntimeError(self.stop_failure) from exc

    async def _settle(self):
        """Confirm stopped using new odometry after zero commands; execute mode only."""
        start, stable = time.monotonic(), None
        last_pose_at = start
        while time.monotonic()-start < 3:
            result = self.robot.base_vel(0., 0.)
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, 1.)
            pose = self.pose()
            if self.pose_at <= last_pose_at:
                await asyncio.sleep(.01)
                continue
            last_pose_at = self.pose_at
            if abs(pose['v']) < .02 and abs(pose['w']) < .03:
                stable = stable or time.monotonic()
                if time.monotonic()-stable >= .25:
                    self.log.emit('BASE_STOPPED')
                    return
            else:
                stable = None
            await asyncio.sleep(.01)
        raise RuntimeError('Stop not confirmed within 3 s; use physical emergency stop')

    async def motion(self, factory, timeout, tick=None, require_perception=False):
        if not self.cfg.execute:
            raise RuntimeError('Motion is disabled in dry-run mode')
        if self.stop_failure:
            raise RuntimeError(self.stop_failure)
        if self.motion_active:
            raise RuntimeError('Another motion sender is already active')
        self.check(require_perception=require_perception)
        self.motion_active = True
        task = None
        last_log, start = 0., time.monotonic()
        try:
            result = factory()
            if not inspect.isawaitable(result):
                raise TypeError('SDK motion did not return an awaitable')
            task = asyncio.ensure_future(result)
            while True:
                self.check(require_perception=require_perception)
                if time.monotonic()-start > timeout:
                    raise RuntimeError('Motion timed out')
                if time.monotonic()-self.perception_at < .4:
                    pose = self.pose()
                    if any(math.hypot(*body_point(p, pose)) < .50 for p in self.people):
                        raise RuntimeError('Observed person within 0.50 m; stopping')
                decision = tick() if tick else None
                if decision:
                    return decision
                if task.done():
                    await task
                    return 'SDK_FINISHED'
                if time.monotonic()-last_log >= .3:
                    self.log.emit('MOTION', **self.pose())
                    last_log = time.monotonic()
                await asyncio.sleep(.02)
        finally:
            try:
                await self.cleanup_motion(task)
            finally:
                self.motion_active = False
