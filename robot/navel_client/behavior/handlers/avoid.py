"""Dry-run AVOID handler; future home of direct Navel avoidance calls."""

from robot.navel_client.behavior.commands import AvoidCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class AvoidHandler(BehaviorHandler[AvoidCommand]):
    def execute(self, command: AvoidCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.AVOID, command)
        self._logger.info(message)
        return result
