"""Immutable, action-specific commands for the Navel behavior boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias, get_args

from robot.navel_client.behavior.intent import NavelPassingSide


@dataclass(frozen=True)
class ContinueCommand:
    pass


@dataclass(frozen=True)
class MonitorCommand:
    pass


@dataclass(frozen=True)
class OrientCommand:
    target_human_id: str
    orientation_target_rad: float | None = None


@dataclass(frozen=True)
class SlowCommand:
    target_speed_mps: float
    target_human_id: str | None = None


@dataclass(frozen=True)
class YieldCommand:
    target_human_id: str | None = None
    target_speed_mps: float | None = None
    preferred_social_distance_m: float | None = None
    passing_side: NavelPassingSide | None = None
    hold_duration_s: float | None = None


@dataclass(frozen=True)
class AvoidCommand:
    target_human_id: str | None = None
    target_speed_mps: float | None = None
    preferred_social_distance_m: float | None = None
    passing_side: NavelPassingSide | None = None


@dataclass(frozen=True)
class ApproachCommand:
    target_human_id: str
    preferred_social_distance_m: float
    target_speed_mps: float | None = None


@dataclass(frozen=True)
class GreetCommand:
    target_human_id: str


@dataclass(frozen=True)
class GuideCommand:
    target_human_id: str
    target_speed_mps: float | None = None
    preferred_social_distance_m: float | None = None
    passing_side: NavelPassingSide | None = None


@dataclass(frozen=True)
class WaitCommand:
    hold_duration_s: float


@dataclass(frozen=True)
class ResumeCommand:
    pass


@dataclass(frozen=True)
class DisengageCommand:
    target_human_id: str


RobotBehaviorCommand: TypeAlias = (
    ContinueCommand
    | MonitorCommand
    | OrientCommand
    | SlowCommand
    | YieldCommand
    | AvoidCommand
    | ApproachCommand
    | GreetCommand
    | GuideCommand
    | WaitCommand
    | ResumeCommand
    | DisengageCommand
)

COMMAND_TYPES: frozenset[type[RobotBehaviorCommand]] = frozenset(
    get_args(RobotBehaviorCommand)
)
