"""Fixed escape extracted from mid_year_demo/yield_to_approaching_person.py.

No social decision or target acquisition occurs here. SDK completion plus fresh
stopped odometry confirms command completion, not measured displacement accuracy.
"""
import asyncio
import inspect
import time
from dataclasses import dataclass, replace, asdict


@dataclass(frozen=True)
class YieldResult:
    status: str = 'NOT_STARTED'
    completed_phase: str = 'NONE'
    movement_completed: bool = False
    speech_error: str | None = None
    error: str | None = None
    requested_parameters: tuple[tuple[str, float], ...] = ()
    applied_parameters: tuple[tuple[str, float], ...] = ()
    verification: str = 'NONE'


async def _say(rt, text):
    task = None
    try:
        value = rt.robot.say(text)
        if not inspect.isawaitable(value):
            raise TypeError('SDK speech did not return an awaitable')
        task = asyncio.ensure_future(value)
        await asyncio.wait_for(asyncio.shield(task), rt.cfg.speech_timeout_s)
    finally:
        if task is not None:
            # Own and observe the SDK task even if it returns a Task or Future.
            async def finish():
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
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


def _admission(rt, deadline):
    rt.check(require_perception=True)
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('Intent expired before YIELD movement (including initial speech)')


async def yield_to_person(rt, *, deadline=None, invert_turn_direction=None):
    """Lease the shared runtime and escape; optional inversion is local configuration.

    Returns explicit outcomes even on cancellation, after owned tasks and stop
    cleanup finish. No target, extra sensor reader, route, or return movement.
    """
    invert = rt.cfg.invert_yield_turn_direction if invert_turn_direction is None else invert_turn_direction
    if not isinstance(invert, bool):
        raise ValueError('invert_turn_direction must be boolean')
    angle = -100. if invert else 100.
    rotation = (('angle_deg', angle), ('rotation_speed_deg_s', 30.), ('rotation_acceleration_deg_s2', 35.))
    escape = (('distance_m', -.60), ('speed_mps', .25), ('acceleration_mps2', .35))
    result = YieldResult(requested_parameters=rotation + escape)
    phase = 'ADMISSION'
    try:
        async with rt.action(behavior='YIELD'):
            _admission(rt, deadline)
            if not rt.cfg.execute:
                return replace(result, status='DRY_RUN_COMPLETED')
            phase = 'INITIAL_SPEECH'
            await _say(rt, 'conflict person detected')
            result = replace(result, completed_phase=phase)
            phase = 'ADMISSION'
            _admission(rt, deadline)
            phase = 'ROTATION'
            def rotate():
                nonlocal result
                result = replace(result, applied_parameters=rotation)
                return rt.robot.rotate_base(angle=angle, speed=30., acceleration=35.)
            await rt.motion(rotate,
                            timeout=abs(angle)/30.+30./35.+4., require_perception=True)
            result = replace(result, completed_phase='ROTATION_STOPPED',
                             verification='SDK_ROTATION_FINISHED_AND_FRESH_ODOMETRY_STOPPED')
            phase = 'ESCAPE'
            def reverse():
                nonlocal result
                result = replace(result, applied_parameters=rotation + escape)
                return rt.robot.move_base(distance=-.60, speed=.25, acceleration=.35)
            await rt.motion(reverse,
                            timeout=.60/.25+.25/.35+4., require_perception=True)
            result = replace(result, completed_phase='ESCAPE_STOPPED', movement_completed=True,
                             verification='SDK_MOTIONS_FINISHED_AND_FRESH_ODOMETRY_STOPPED; DISPLACEMENT_NOT_VERIFIED')
            phase = 'FINAL_SPEECH'
            await _say(rt, 'yield complete')
            result = replace(result, status='COMPLETED', completed_phase='FINAL_SPEECH')
    except asyncio.CancelledError:
        result = replace(result, status='CANCELLED', error=f'Cancelled during {phase}')
    except Exception as exc:
        error = f'{phase}: {type(exc).__name__}: {exc}'
        speech = phase in {'INITIAL_SPEECH', 'FINAL_SPEECH'}
        result = replace(result, status='SPEECH_FAILED' if speech and not rt.stop_failure else 'FAILED',
                         error=error, speech_error=error if speech else None)
    rt.log.emit('YIELD_RESULT', **asdict(result))
    return result
