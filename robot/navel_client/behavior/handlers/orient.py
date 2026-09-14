"""Dry-run ORIENT handler; future home of direct Navel orientation calls."""

from robot.navel_client.behavior.commands import OrientCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class OrientHandler(BehaviorHandler[OrientCommand]):
    def execute(self, command: OrientCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.ORIENT, command)
        self._logger.info(message)
        return result
