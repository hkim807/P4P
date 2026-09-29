"""Reusable approach action using g_nose and public Navel motion functions.

Adapted from the supplied navel_approach_demo/approach_human.py.
await approach_human(runtime, uid)

runtime owns the single perception/odometry readers and exclusive motion sender.
UID seeds identity only: geometric association follows ordinary UID changes.
Each arc freezes its target. A disappearing face does not cancel an arc.
One optional distance correction and up to two final heading corrections use
fresh observations. This is bounded approach, NOT continuous body following.
"""
import math
import statistics
import time
import asyncio
from dataclasses import dataclass, asdict
from robot.navel_client.navel_runtime import body_point, wrap


@dataclass(frozen=True)
class ApproachResult:
    status: str
    distance_m: float
    heading_error_deg: float
    live_position_verified: bool


def arc_plan(x, y, stand_off=.7, speed_limit=.25):
    if not all(math.isfinite(v) for v in (x, y, stand_off, speed_limit)) or not .6 <= stand_off <= 1.5 or speed_limit <= 0:
        raise ValueError('Finite position, stand-off .6..1.5 m and positive speed required')
    if x <= 0 or math.hypot(x, y) <= stand_off:
        return None
    theta = 2*math.atan2(y, x+stand_off)
    length = x-stand_off if abs(y) < 1e-8 else (x*x+y*y-stand_off**2)*theta/(2*y)
    if length < .08:
        return None
    if length > 4 or abs(theta) > math.radians(100):
        raise ValueError('Target outside supported single-arc geometry')
    # Maximise speed within both linear and angular SDK limits.
    speed = min(speed_limit, .25, math.radians(70)*length/max(abs(theta), 1e-9))
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
    plan = arc_plan(x, y, distance, rt.cfg.approach_speed)
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


async def approach_human(rt, uid, *, seed=None, deadline=None):
    """Use shared readers, reset identity per execution, and return an honest outcome.

    The caller supplies a Navel UID (and optionally an associated source-observation
    seed). Admission deadline is rechecked immediately before the first actuator call.
    Once started, the bounded action may finish after that deadline.
    """
    async with rt.action(uid, seed):
        return await _approach(rt, uid, deadline)


async def _approach(rt, uid, deadline):
    until = min(time.monotonic()+8, deadline or float('inf'))
    while rt.target is None and time.monotonic() < until:
        rt.detect()
        await asyncio.sleep(.02)
    if rt.target is None:
        raise RuntimeError('No fresh unambiguous target before acquisition deadline')
    target = await sample_target(rt)
    if target is None:
        raise RuntimeError('Fresh median target unavailable before movement')
    rt.check(require_perception=True)
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('Intent expired during target acquisition')
    if time.monotonic()-target['seen_at'] > .4:
        raise RuntimeError('Target stale before movement')
    x, y = body_point(target, rt.pose())
    # Validate supported geometry before the first actuator call in either mode.
    if abs(math.degrees(math.atan2(y, x))) > 45:
        x, y = math.hypot(x, y), 0.
    arc_plan(x, y, rt.cfg.stop_distance, rt.cfg.approach_speed)
    if not rt.cfg.execute:
        return None
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
