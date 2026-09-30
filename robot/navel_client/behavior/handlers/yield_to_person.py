"""Thin pipeline adapter for the reusable fixed YIELD manoeuvre."""
from robot.navel_client.behavior.actions.yield_to_person import yield_to_person
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class YieldHandler:
    def __init__(self, runtime):
        self.runtime = runtime

    async def execute(self, command, *, seed=None, deadline=None, decision_id=None):
        outcome = await yield_to_person(self.runtime, deadline=deadline)
        return BehaviorExecutionResult(
            action=NavelAction.YIELD, command_type=type(command).__name__,
            dry_run=not self.runtime.cfg.execute, parameters=outcome.requested_parameters,
            decision_id=decision_id, requested_target=command.target_human_id,
            yield_result=outcome)
