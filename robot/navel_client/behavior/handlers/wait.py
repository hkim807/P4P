"""Dry-run WAIT handler; it deliberately performs no sleeping."""

from app.domain.models import Action
from robot.navel_client.behavior.commands import WaitCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.results import BehaviorExecutionResult


class WaitHandler(BehaviorHandler[WaitCommand]):
    def execute(self, command: WaitCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(Action.WAIT, command)
        self._logger.info(message)
        return result
