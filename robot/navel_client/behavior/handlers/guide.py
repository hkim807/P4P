"""Dry-run GUIDE handler; future home of direct Navel guidance calls."""

from robot.navel_client.behavior.commands import GuideCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class GuideHandler(BehaviorHandler[GuideCommand]):
    def execute(self, command: GuideCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.GUIDE, command)
        self._logger.info(message)
        return result
