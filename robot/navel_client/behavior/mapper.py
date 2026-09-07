"""Pure translation from the shared server intent to explicit Navel commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias, TypeVar

from app.domain.models import Action, BehaviorIntent, PassingSide
from robot.navel_client.behavior.commands import (
    COMMAND_TYPES,
    ApproachCommand,
    AvoidCommand,
    ContinueCommand,
    DisengageCommand,
    GreetCommand,
    GuideCommand,
    MonitorCommand,
    OrientCommand,
    ResumeCommand,
    RobotBehaviorCommand,
    SlowCommand,
    WaitCommand,
    YieldCommand,
)


class BehaviorMappingError(ValueError):
    """A validated intent cannot be represented by a safe typed command."""


MapperEntry: TypeAlias = tuple[
    type[RobotBehaviorCommand], Callable[[BehaviorIntent], RobotBehaviorCommand]
]
RequiredT = TypeVar("RequiredT", str, float)


def _required(
    value: RequiredT | None, field_name: str, action: Action
) -> RequiredT:
    if value is None:
        raise BehaviorMappingError(f"{action.value} requires {field_name}")
    return value


def _passing_side(value: PassingSide | str | None) -> PassingSide | None:
    return PassingSide(value) if value is not None else None


class BehaviorIntentMapper:
    """Deterministically map every canonical action without runtime side effects."""

    def __init__(self) -> None:
        self._mappers: dict[Action, MapperEntry] = {
            Action.CONTINUE: (ContinueCommand, lambda intent: ContinueCommand()),
            Action.MONITOR: (MonitorCommand, lambda intent: MonitorCommand()),
            Action.ORIENT: (OrientCommand, self._orient),
            Action.SLOW: (SlowCommand, self._slow),
            Action.YIELD: (YieldCommand, self._yield),
            Action.AVOID: (AvoidCommand, self._avoid),
            Action.APPROACH: (ApproachCommand, self._approach),
            Action.GREET: (GreetCommand, self._greet),
            Action.GUIDE: (GuideCommand, self._guide),
            Action.WAIT: (WaitCommand, self._wait),
            Action.RESUME: (ResumeCommand, lambda intent: ResumeCommand()),
            Action.DISENGAGE: (DisengageCommand, self._disengage),
        }
        mapped_types = {entry[0] for entry in self._mappers.values()}
        if set(self._mappers) != set(Action) or mapped_types != set(COMMAND_TYPES):
            raise RuntimeError("behavior mapper does not cover every action and command type")

    def map(self, intent: BehaviorIntent) -> RobotBehaviorCommand:
        try:
            action = Action(intent.action)
            expected_type, mapper = self._mappers[action]
        except (ValueError, KeyError) as error:
            raise BehaviorMappingError(
                f"unsupported behavior action: {intent.action!r}"
            ) from error
        command = mapper(intent)
        if type(command) is not expected_type:
            raise BehaviorMappingError(
                f"{action.value} mapper returned {type(command).__name__}; "
                f"expected {expected_type.__name__}"
            )
        return command

    @staticmethod
    def _orient(intent: BehaviorIntent) -> OrientCommand:
        return OrientCommand(
            target_human_id=_required(
                intent.target_human_id, "target_human_id", Action.ORIENT
            ),
            orientation_target_rad=intent.preferences.orientation_target_rad,
        )

    @staticmethod
    def _slow(intent: BehaviorIntent) -> SlowCommand:
        return SlowCommand(
            target_speed_mps=_required(
                intent.preferences.target_speed_mps,
                "target_speed_mps",
                Action.SLOW,
            ),
            target_human_id=intent.target_human_id,
        )

    @staticmethod
    def _yield(intent: BehaviorIntent) -> YieldCommand:
        preferences = intent.preferences
        return YieldCommand(
            target_human_id=intent.target_human_id,
            target_speed_mps=preferences.target_speed_mps,
            preferred_social_distance_m=preferences.preferred_social_distance_m,
            passing_side=_passing_side(preferences.passing_side),
            hold_duration_s=preferences.hold_duration_s,
        )

    @staticmethod
    def _avoid(intent: BehaviorIntent) -> AvoidCommand:
        preferences = intent.preferences
        return AvoidCommand(
            target_human_id=intent.target_human_id,
            target_speed_mps=preferences.target_speed_mps,
            preferred_social_distance_m=preferences.preferred_social_distance_m,
            passing_side=_passing_side(preferences.passing_side),
        )

    @staticmethod
    def _approach(intent: BehaviorIntent) -> ApproachCommand:
        return ApproachCommand(
            target_human_id=_required(
                intent.target_human_id, "target_human_id", Action.APPROACH
            ),
            preferred_social_distance_m=_required(
                intent.preferences.preferred_social_distance_m,
                "preferred_social_distance_m",
                Action.APPROACH,
            ),
            target_speed_mps=intent.preferences.target_speed_mps,
        )

    @staticmethod
    def _greet(intent: BehaviorIntent) -> GreetCommand:
        return GreetCommand(
            target_human_id=_required(
                intent.target_human_id, "target_human_id", Action.GREET
            )
        )

    @staticmethod
    def _guide(intent: BehaviorIntent) -> GuideCommand:
        preferences = intent.preferences
        return GuideCommand(
            target_human_id=_required(
                intent.target_human_id, "target_human_id", Action.GUIDE
            ),
            target_speed_mps=preferences.target_speed_mps,
            preferred_social_distance_m=preferences.preferred_social_distance_m,
            passing_side=_passing_side(preferences.passing_side),
        )

    @staticmethod
    def _wait(intent: BehaviorIntent) -> WaitCommand:
        return WaitCommand(
            hold_duration_s=_required(
                intent.preferences.hold_duration_s, "hold_duration_s", Action.WAIT
            )
        )

    @staticmethod
    def _disengage(intent: BehaviorIntent) -> DisengageCommand:
        return DisengageCommand(
            target_human_id=_required(
                intent.target_human_id, "target_human_id", Action.DISENGAGE
            )
        )
