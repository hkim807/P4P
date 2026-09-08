"""Dry-run YIELD handler; future home of direct Navel yielding calls."""

from robot.navel_client.behavior.commands import YieldCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class YieldHandler(BehaviorHandler[YieldCommand]):
    def execute(self, command: YieldCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.YIELD, command)
        self._logger.info(message)
        return result
