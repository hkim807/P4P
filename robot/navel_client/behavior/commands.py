"""Immutable commands for the four canonical intents."""
from dataclasses import dataclass
from typing import TypeAlias, get_args


@dataclass(frozen=True)
class ContinueCommand:
    pass


@dataclass(frozen=True)
class ApproachCommand:
    target_human_id: str
    preferred_social_distance_m: float
    target_speed_mps: float | None = None


@dataclass(frozen=True)
class YieldCommand:
    target_human_id: str | None = None


@dataclass(frozen=True)
class EngageCommand:
    target_human_id: str


RobotBehaviorCommand: TypeAlias = ContinueCommand | ApproachCommand | EngageCommand | YieldCommand
COMMAND_TYPES = frozenset(get_args(RobotBehaviorCommand))
