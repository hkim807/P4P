"""Dry-run DISENGAGE handler; future home of direct Navel interaction calls."""

from robot.navel_client.behavior.commands import DisengageCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class DisengageHandler(BehaviorHandler[DisengageCommand]):
    def execute(self, command: DisengageCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.DISENGAGE, command)
        self._logger.info(message)
        return result
