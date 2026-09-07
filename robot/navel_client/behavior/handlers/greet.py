"""Dry-run GREET handler; future home of direct Navel interaction calls."""

from robot.navel_client.behavior.commands import GreetCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class GreetHandler(BehaviorHandler[GreetCommand]):
    def execute(self, command: GreetCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.GREET, command)
        self._logger.info(message)
        return result
