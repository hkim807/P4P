"""Common handler contract and dry-run result formatting."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import fields
from enum import Enum
from typing import Any, Generic, TypeVar

from app.domain.models import Action
from robot.navel_client.behavior.commands import RobotBehaviorCommand
from robot.navel_client.behavior.results import BehaviorExecutionResult


CommandT = TypeVar("CommandT", bound=RobotBehaviorCommand)


class BehaviorHandler(ABC, Generic[CommandT]):
    """Base for handlers that will directly use their injected Navel robot."""

    def __init__(self, robot: Any, logger: logging.Logger | None = None) -> None:
        self._robot = robot
        self._logger = logger or logging.getLogger("robot.navel_client.behavior")

    @abstractmethod
    def execute(self, command: CommandT) -> BehaviorExecutionResult:
        """Execute one exact command; current implementations remain dry-run."""

    def _dry_run(
        self, action: Action, command: CommandT
    ) -> tuple[BehaviorExecutionResult, str]:
        parameters = tuple(
            (field.name, value)
            for field in fields(command)
            if (value := getattr(command, field.name)) is not None
        )
        details = " ".join(
            f"{name}={self._format_value(value)}" for name, value in parameters
        )
        message = f"[NAVEL BEHAVIOR] action={action.value} dry_run=true"
        if details:
            message = f"{message} {details}"
        result = BehaviorExecutionResult(
            action=action,
            command_type=type(command).__name__,
            dry_run=True,
            parameters=parameters,
        )
        return result, message

    @staticmethod
    def _format_value(value: object) -> str:
        if isinstance(value, Enum):
            return str(value.value)
        if isinstance(value, float):
            return f"{value:.2f}"
        return str(value)
