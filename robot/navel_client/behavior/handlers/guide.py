"""Dry-run GUIDE handler; future home of direct Navel guidance calls."""

from app.domain.models import Action
from robot.navel_client.behavior.commands import GuideCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.results import BehaviorExecutionResult


class GuideHandler(BehaviorHandler[GuideCommand]):
    def execute(self, command: GuideCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(Action.GUIDE, command)
        self._logger.info(message)
        return result
