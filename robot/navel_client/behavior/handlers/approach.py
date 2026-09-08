"""Dry-run APPROACH handler; future home of direct Navel approach calls."""

from robot.navel_client.behavior.commands import ApproachCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class ApproachHandler(BehaviorHandler[ApproachCommand]):
    def execute(self, command: ApproachCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.APPROACH, command)
        self._logger.info(message)
        return result
