"""Dry-run MONITOR handler; future home of direct Navel behavior calls."""

from robot.navel_client.behavior.commands import MonitorCommand
from robot.navel_client.behavior.handlers.base import BehaviorHandler
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class MonitorHandler(BehaviorHandler[MonitorCommand]):
    def execute(self, command: MonitorCommand) -> BehaviorExecutionResult:
        result, message = self._dry_run(NavelAction.MONITOR, command)
        self._logger.info(message)
        return result
