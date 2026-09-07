"""Dry-run GREET handler; future home of direct Navel interaction calls."""

from app.domain.models import Action
from robot.navel_client.behavior.commands import GreetCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.results import BehaviorExecutionResult


class GreetHandler(BehaviorHandler[GreetCommand]):
    def execute(self, command: GreetCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(Action.GREET, command)
        self._logger.info(message)
        return result
