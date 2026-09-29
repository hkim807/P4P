"""Adapt a pipeline command to the supplied reusable approach action."""
import math
from dataclasses import asdict, replace
from robot.navel_client.behavior.actions.approach_human import approach_human
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult
from robot.navel_client.navel_runtime import navel_uid


def validate_parameters(command):
    navel_uid(command.target_human_id)
    distance = command.preferred_social_distance_m
    speed = .25 if command.target_speed_mps is None else command.target_speed_mps
    if not math.isfinite(distance) or not .6 <= distance <= 1.5:
        raise ValueError('Supported stand-off is .6..1.5 m from base centre to nose')
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError('Approach speed must be finite and positive')
    return distance, min(speed, .25)


class ApproachHandler:
    def __init__(self, runtime):
        self.runtime = runtime

    async def execute(self, command, *, seed=None, deadline=None, decision_id=None):
        distance, speed = validate_parameters(command)
        rt = self.runtime
        # The controller serializes actions; the reusable action also leases the runtime.
        if rt.action_active:
            raise RuntimeError('Another action owns the runtime')
        previous = rt.cfg
        rt.cfg = replace(previous, stop_distance=distance, approach_speed=speed)
        parameters = tuple(asdict(command).items()) + (('applied_speed_cap_mps', speed),
                                                     ('applied_stand_off_m', distance))
        try:
            rt.log.emit('APPROACH_PARAMETERS', decision_id=decision_id, **dict(parameters))
            outcome = await approach_human(rt, navel_uid(command.target_human_id),
                                           seed=seed, deadline=deadline)
            return BehaviorExecutionResult(NavelAction.APPROACH, type(command).__name__,
                not rt.cfg.execute, parameters, decision_id, command.target_human_id,
                str(rt.target['uid']) if rt.target else None, outcome)
        finally:
            rt.cfg = previous
