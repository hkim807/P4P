"""Dry-run ORIENT handler; future home of direct Navel orientation calls."""

from app.domain.models import Action
from robot.navel_client.behavior.commands import OrientCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.results import BehaviorExecutionResult


class OrientHandler(BehaviorHandler[OrientCommand]):
    def execute(self, command: OrientCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(Action.ORIENT, command)
        self._logger.info(message)
        return result
