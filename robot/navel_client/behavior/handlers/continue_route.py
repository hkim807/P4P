"""Dry-run CONTINUE handler; future home of direct Navel route calls."""

from robot.navel_client.behavior.commands import ContinueCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class ContinueHandler(BehaviorHandler[ContinueCommand]):
    def execute(self, command: ContinueCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.CONTINUE, command)
        self._logger.info(message)
        return result
