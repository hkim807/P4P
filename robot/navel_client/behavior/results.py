"""Typed results returned by behavior handlers and the controller."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.domain.models import Action


@dataclass(frozen=True)
class BehaviorExecutionResult:
    action: Action
    command_type: str
    dry_run: bool
    parameters: tuple[tuple[str, object], ...]


class BehaviorHandlingStatus(str, Enum):
    HANDLED = "HANDLED"
    NO_INTENT = "NO_INTENT"
    INVALID_INTENT = "INVALID_INTENT"
    FAILED = "FAILED"


@dataclass(frozen=True)
class BehaviorHandlingResult:
    status: BehaviorHandlingStatus
    execution: BehaviorExecutionResult | None = None
    error: str | None = None
