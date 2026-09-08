"""Dry-run SLOW handler; future home of direct Navel speed calls."""

from robot.navel_client.behavior.commands import SlowCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class SlowHandler(BehaviorHandler[SlowCommand]):
    def execute(self, command: SlowCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.SLOW, command)
        self._logger.info(message)
        return result
