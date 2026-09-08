"""Dry-run RESUME handler; future home of direct Navel route calls."""

from robot.navel_client.behavior.commands import ResumeCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class ResumeHandler(BehaviorHandler[ResumeCommand]):
    def execute(self, command: ResumeCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.RESUME, command)
        self._logger.info(message)
        return result
