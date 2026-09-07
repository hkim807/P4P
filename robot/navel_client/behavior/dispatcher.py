"""Type-based command dispatch with complete-registration safeguards."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from robot.navel_client.behavior.commands import (
    COMMAND_TYPES,
    RobotBehaviorCommand,
)
from robot.navel_client.behavior.handlers import BehaviorHandler
from robot.navel_client.behavior.results import BehaviorExecutionResult


class BehaviorDispatchError(RuntimeError):
    """A command has no valid registered handler."""


HandlerRegistry = Mapping[type[RobotBehaviorCommand], BehaviorHandler[Any]]


class BehaviorDispatcher:
    def __init__(self, handlers: HandlerRegistry) -> None:
        self._handlers = dict(handlers)
        registered = set(self._handlers)
        if registered != set(COMMAND_TYPES):
            missing = sorted(command.__name__ for command in COMMAND_TYPES - registered)
            extra = sorted(command.__name__ for command in registered - COMMAND_TYPES)
            raise BehaviorDispatchError(
                f"handler coverage mismatch; missing={missing}, extra={extra}"
            )

    def dispatch(self, command: RobotBehaviorCommand) -> BehaviorExecutionResult:
        handler = self._handlers.get(type(command))
        if handler is None:
            raise BehaviorDispatchError(
                f"no handler registered for command type {type(command).__name__}"
            )
        return handler.execute(command)
