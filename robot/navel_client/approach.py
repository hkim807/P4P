"""Reference bounded g_nose approach, fed by the application's shared readers.

Ported from navel-navigation/navel_approach_demo/{approach_human,navel_runtime}.py.
Geometry and tuning are retained; this adapter owns no SDK connection or readers.
"""
import asyncio
from collections import deque
from dataclasses import dataclass, asdict
import inspect
import json
import logging
import math
import statistics
import time

logger = logging.getLogger(__name__)


@dataclass
class ApproachConfig:
    stop_distance: float = 0.7
    distance_tolerance: float = 0.10
    heading_tolerance_deg: float = 4.0
    head_x: float = 0.0
    head_y: float = 0.0
    frame_yaw_deg: float = 0.0
    target_uid: int | None = None
    detection_range: float = 4.0


class ApproachNotVerified(RuntimeError):
    pass


class ApproachLog:
    def emit(self, event, **data):
        logger.info('approach_event=%s', json.dumps(dict(event=event, **data),
                    allow_nan=False, default=str))


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


def nose_position(person, cfg):
    pts = [p for p in (getattr(person, 'g_nose', None) or []) if int(p.sys) == 3]
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
    return dict(uid=getattr(person, 'uid', None), x=x, y=y, z=z)


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


class ApproachRuntime:
    def __init__(self, context):
        self.context = context
        self.robot, self.cfg, self.log = context.robot, ApproachConfig(), ApproachLog()
        self.stop = asyncio.Event()
        self._pose, self.pose_at, self.perception_at = None, 0., 0.
        self.history = deque(maxlen=40)
        self.people, self.target = [], None
        self.frame_seq, self.errors = 0, {}
        self.last_pose_stamp = self.last_frame_stamp = None
        self.last_perception_log = 0.
        self.seed, self.seed_count, self.seed_time, self.seed_last = None, 0, 0., 0.
        self.detect_seq = -1
        self.motion_active = False

    async def wait_ready(self, require_perception=True):
        until = time.monotonic()+4
        while (self._pose is None or (require_perception and self.perception_at == 0)) and time.monotonic() < until:
            if ('odometry' in self.errors or (require_perception and 'perception' in self.errors)
                    or self.stop.is_set()):
                break
            await asyncio.sleep(.02)
        self.check(require_perception=require_perception)

    def pose(self):
        if self._pose is None or time.monotonic()-self.pose_at > .6 or 'odometry' in self.errors:
            raise RuntimeError('Odometry unavailable/stale: '+str(self.errors))
        return self._pose.copy()

    def check(self, require_perception=False):
        if self.stop.is_set():
            raise RuntimeError('Operator stop')
        self.pose()
        if require_perception and (time.monotonic()-self.perception_at > .8 or 'perception' in self.errors):
            raise RuntimeError('Perception stream unavailable/stale: '+str(self.errors))

    def ingest_locomotion(self, packet):
        """Receive a packet from the application's sole odometry reader."""
        if 'odometry' in self.errors:
            return  # The reference reader stops after its first stream error.
        try:
            pose = read_pose(packet)
            if pose['stamp'] not in (None, 0) and pose['stamp'] == self.last_pose_stamp:
                return
            self.last_pose_stamp = pose['stamp']
            self._pose, self.pose_at = pose, time.monotonic()
            self.history.append(pose)
        except Exception as exc:
            self.errors['odometry'] = str(exc)

    def ingest_perception(self, frame):
        """Receive raw g_nose geometry without changing the policy payload."""
        if 'perception' in self.errors:
            return
        try:
            stamp = getattr(frame, 'time', None)
            if stamp not in (None, 0) and stamp == self.last_frame_stamp:
                return
            self.last_frame_stamp = stamp
            now = time.monotonic()
            self.perception_at, self.frame_seq = now, self.frame_seq+1
            people = list(getattr(frame, 'persons', None) or [])
            pose = self.frame_pose(stamp) if self._pose is not None else None
            candidates = [world_point(p, pose) for person in people
                          if (p := nose_position(person, self.cfg)) is not None] if pose else []
            self.people = candidates
            if self.target is not None:
                age = now-self.target['seen_at']
                # Preserve memory without reassociation after prolonged loss.
                chosen = associate(candidates, self.target, gate=.5) if age < 2 else None
                if chosen is not None:
                    self.target = dict(chosen, seen_at=now, frame_seq=self.frame_seq)
            if now-self.last_perception_log >= .5:
                self.log.emit('PERCEPTION', detected=len(people), usable_positions=len(candidates),
                              positions=[dict(uid=p['uid'], x=p['x'], y=p['y']) for p in candidates],
                              synchronised_pose=pose is not None)
                self.last_perception_log = now
        except Exception as exc:
            self.errors['perception'] = str(exc)

    def frame_pose(self, stamp):
        current = self.pose()
        if stamp and self.history and current['stamp']:
            nearest = min(self.history, key=lambda p: abs(p['stamp']-stamp))
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
        if now-self.seed_last > .5:
            self.seed = None
        chosen = associate(eligible, self.seed, .35) if self.seed else None
        if chosen is None:
            eligible = [p for p in eligible if self.cfg.target_uid is None or p['uid'] == self.cfg.target_uid]
            if self.cfg.target_uid is not None and len(eligible) != 1:
                eligible = []
            if not eligible:
                self.seed, self.seed_count = None, 0
                return None
            chosen = min(eligible, key=lambda p: math.hypot(p['x'], p['y']))
            self.seed, self.seed_count, self.seed_time = chosen, 1, now
        else:
            self.seed_count += 1
            self.seed = chosen
        self.seed_last = now
        if self.seed_count >= 3 and now-self.seed_time >= .18:
            self.target = dict(chosen, seen_at=now, frame_seq=self.frame_seq)
            return self.target.copy()
        return None

    async def settle(self):
        """Only called after this program commands movement; zero commands until stopped."""
        start, stable = time.monotonic(), None
        while time.monotonic()-start < 3:
            self.robot.base_vel(0., 0.)
            pose = self.pose()
            if abs(pose['v']) < .02 and abs(pose['w']) < .03:
                stable = stable or time.monotonic()
                if time.monotonic()-stable >= .25:
                    self.log.emit('BASE_STOPPED')
                    return
            else:
                stable = None
            await asyncio.sleep(.01)
        raise RuntimeError('Stop not confirmed within 3 s; use physical emergency stop')

    async def motion(self, factory, timeout, tick=None, require_perception=False, check_people=True):
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
            task = self.context.own_task(result)
            while True:
                self.check(require_perception=require_perception)
                if time.monotonic()-start > timeout:
                    raise RuntimeError('Motion timed out')
                if check_people and time.monotonic()-self.perception_at < .4:
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
            # A timeout/interruption may arrive during normal arc braking.
            # Keep cancellation from interrupting sender settling or measured stop.
            cleanup = asyncio.create_task(self._finish_motion(task))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await asyncio.shield(cleanup)
                raise

    async def _finish_motion(self, task):
        try:
            if task is not None:
                # Dispatcher cancellation may already have reached the SDK task.
                if not task.done() and not getattr(task, 'cancelling', lambda: 0)():
                    task.cancel()
                done, _ = await asyncio.wait([task], timeout=2.0)
                if not done:
                    raise RuntimeError('Movement sender did not settle; stop unconfirmed')
                await asyncio.gather(task, return_exceptions=True)
            await self.settle()
        except Exception as exc:
            self.log.emit('STOP_UNCONFIRMED', error=str(exc))
            raise
        finally:
            self.motion_active = False


@dataclass
class ApproachResult:
    status: str
    distance_m: float
    heading_error_deg: float
    live_position_verified: bool


def arc_plan(x, y, stand_off=.7):
    if not all(math.isfinite(v) for v in (x, y, stand_off)) or stand_off < .6:
        raise ValueError('Nonfinite position or stand-off below 0.6 m')
    if x <= 0 or math.hypot(x, y) <= stand_off:
        return None
    theta = 2*math.atan2(y, x+stand_off)
    length = x-stand_off if abs(y) < 1e-8 else (x*x+y*y-stand_off**2)*theta/(2*y)
    if length < .08:
        return None
    if length > 4 or abs(theta) > math.radians(100):
        raise ValueError('Target outside supported single-arc geometry')
    # Maximise speed within both linear and angular SDK limits.
    speed = min(.25, math.radians(70)*length/max(abs(theta), 1e-9))
    return dict(distance=length, angle=math.degrees(theta), speed=speed, acceleration=1.,
                goal_x=x-stand_off*math.cos(theta), goal_y=y-stand_off*math.sin(theta))


async def sample_target(rt, timeout=1.2):
    """Median of at least 3 NEW world-coordinate observations while base is stopped."""
    start, last_seq, samples = time.monotonic(), -1, []
    while time.monotonic()-start < timeout:
        rt.check()
        p = rt.target
        if p and p['seen_at'] > start and p['frame_seq'] != last_seq:
            last_seq = p['frame_seq']
            samples.append(p.copy())
            if len(samples) >= 3:
                x = statistics.median(p['wx'] for p in samples[-5:])
                y = statistics.median(p['wy'] for p in samples[-5:])
                spread = max(math.hypot(p['wx']-x, p['wy']-y) for p in samples[-5:])
                if spread <= .15:
                    return dict(samples[-1], wx=x, wy=y)
        await asyncio.sleep(.02)
    return None


async def turn_to_target(rt, target, max_angle=80):
    x, y = body_point(target, rt.pose())
    angle = math.degrees(math.atan2(y, x))
    if abs(angle) <= rt.cfg.heading_tolerance_deg:
        return
    if abs(angle) > max_angle:
        raise ValueError('Target bearing beyond supported turn; no blind full rotation')
    # A short turn cannot physically reach 70 deg/s at 60 deg/s².
    # Keep maximum acceleration, with a reachable triangular-profile peak.
    speed = min(70., math.sqrt(abs(angle)*60.))
    rt.log.emit('ALIGN', requested_angle_deg=angle, speed_deg_s=speed)
    await rt.motion(lambda: rt.robot.rotate_base(angle, speed=speed, acceleration=60.),
                    timeout=abs(angle)/speed+speed/60+4)


async def run_arc(rt, target, distance):
    origin = rt.pose()
    x, y = body_point(target, origin)
    plan = arc_plan(x, y, distance)
    if plan is None:
        return
    rt.log.emit('APPROACH_ARC', person_x=x, person_y=y, stop_distance=distance, **plan)
    previous, travelled = origin, 0.

    def monitor():
        nonlocal previous, travelled
        pose = rt.pose()
        travelled += math.hypot(pose['x']-previous['x'], pose['y']-previous['y'])
        previous = pose
        yaw = wrap(pose['yaw']-origin['yaw'])
        if abs(yaw) > abs(math.radians(plan['angle']))+math.radians(15):
            raise RuntimeError('Arc rotation exceeded planned angle by 15 degrees')
        if travelled > plan['distance']+.25:
            raise RuntimeError('Arc travel exceeded planned distance')
        # Stop a time-driven SDK sender when odometry reaches the planned arc.
        # 1 m/s² is the SDK arc acceleration, not a calibrated stopping model.
        allowance = max(.015, max(pose['v'], 0.)**2/2)
        if travelled >= plan['distance']-allowance:
            return 'ODOMETRY_ARC_END'
        return None

    reason = await rt.motion(lambda: rt.robot.move_and_rotate_base(
        plan['distance'], plan['angle'], speed=plan['speed'], acceleration=plan['acceleration']),
        timeout=plan['distance']/plan['speed']+5, tick=monitor)
    rt.log.emit('ARC_ENDED', reason=reason, odometry_path_length=travelled)


async def approach_human(rt, uid=None):
    """Approach rt.target; if absent, acquire uid as the initial target.

    Returns an explicit result; APPROACHED_VERIFIED means fresh nose estimate
    within configured tolerances, not independently measured ground truth.
    Does not inspect gaze/intent or resume cruise after completion.
    """
    if rt.target is None:
        old_uid = rt.cfg.target_uid
        rt.cfg.target_uid = uid
        try:
            until = time.monotonic()+8
            while rt.target is None and time.monotonic() < until:
                rt.detect()
                await asyncio.sleep(.02)
        finally:
            rt.cfg.target_uid = old_uid
        if rt.target is None:
            raise RuntimeError('No target with usable g_nose coordinates')
    target = await sample_target(rt) or rt.target.copy()
    rt.log.emit('APPROACH_BEGIN', initial_uid=uid, target=target,
                stop_distance=rt.cfg.stop_distance, position_source='g_nose')
    x, y = body_point(target, rt.pose())
    if abs(math.degrees(math.atan2(y, x))) > 45:
        await turn_to_target(rt, target)
        target = await sample_target(rt) or target

    await run_arc(rt, target, rt.cfg.stop_distance)
    fresh = await sample_target(rt)
    # One bounded terminal distance correction, based ONLY on fresh coordinates.
    if fresh is not None:
        target = fresh
        x, y = body_point(target, rt.pose())
        if math.hypot(x, y) > rt.cfg.stop_distance+rt.cfg.distance_tolerance:
            if abs(math.degrees(math.atan2(y, x))) > 45:
                await turn_to_target(rt, target)
                target = await sample_target(rt) or target
            await run_arc(rt, target, rt.cfg.stop_distance)
            fresh = await sample_target(rt)
            if fresh is not None:
                target = fresh
    # Correct base heading using the measured post-arrival position.
    for _ in range(2):
        x, y = body_point(target, rt.pose())
        if abs(math.degrees(math.atan2(y, x))) <= rt.cfg.heading_tolerance_deg:
            break
        await turn_to_target(rt, target)
        fresh = await sample_target(rt)
        if fresh is not None:
            target = fresh
        else:
            break  # memory-only alignment once; never pretend live tracking
    fresh = await sample_target(rt)
    if fresh is not None:
        target = fresh
    x, y = body_point(target, rt.pose())
    remaining, bearing = math.hypot(x, y), math.degrees(math.atan2(y, x))
    status = 'APPROACHED_UNVERIFIED'
    if fresh is not None:
        status = ('APPROACHED_VERIFIED' if abs(remaining-rt.cfg.stop_distance) <= rt.cfg.distance_tolerance
                  and abs(bearing) <= rt.cfg.heading_tolerance_deg else 'OUTSIDE_TOLERANCE')
    result = ApproachResult(status, remaining, bearing, fresh is not None)
    rt.log.emit('APPROACH_RESULT', **asdict(result))
    return result
