"""Async handler contract; handlers share the application runtime."""
from typing import Protocol, TypeVar
from robot.navel_client.behavior.commands import RobotBehaviorCommand
from robot.navel_client.behavior.results import BehaviorExecutionResult

CommandT = TypeVar('CommandT', bound=RobotBehaviorCommand, contravariant=True)


class BehaviorHandler(Protocol[CommandT]):
    async def execute(self, command: CommandT, **context) -> BehaviorExecutionResult: ...
