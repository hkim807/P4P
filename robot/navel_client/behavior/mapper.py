"""Pure translation from the shared server intent to explicit Navel commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias, TypeVar

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
from robot.navel_client.behavior.intent import (
    NavelAction,
    NavelBehaviorIntent,
)


class BehaviorMappingError(ValueError):
    """A validated intent cannot be represented by a safe typed command."""


MapperEntry: TypeAlias = tuple[
    type[RobotBehaviorCommand],
    Callable[[NavelBehaviorIntent], RobotBehaviorCommand],
]
RequiredT = TypeVar("RequiredT", str, float)


def _required(
    value: RequiredT | None, field_name: str, action: NavelAction
) -> RequiredT:
    if value is None:
        raise BehaviorMappingError(f"{action.value} requires {field_name}")
    return value


class BehaviorIntentMapper:
    """Deterministically map every canonical action without runtime side effects."""

    def __init__(self) -> None:
        self._mappers: dict[NavelAction, MapperEntry] = {
            NavelAction.CONTINUE: (ContinueCommand, lambda intent: ContinueCommand()),
            NavelAction.MONITOR: (MonitorCommand, lambda intent: MonitorCommand()),
            NavelAction.ORIENT: (OrientCommand, self._orient),
            NavelAction.SLOW: (SlowCommand, self._slow),
            NavelAction.YIELD: (YieldCommand, self._yield),
            NavelAction.AVOID: (AvoidCommand, self._avoid),
            NavelAction.APPROACH: (ApproachCommand, self._approach),
            NavelAction.GREET: (GreetCommand, self._greet),
            NavelAction.GUIDE: (GuideCommand, self._guide),
            NavelAction.WAIT: (WaitCommand, self._wait),
            NavelAction.RESUME: (ResumeCommand, lambda intent: ResumeCommand()),
            NavelAction.DISENGAGE: (DisengageCommand, self._disengage),
        }
        mapped_types = {entry[0] for entry in self._mappers.values()}
        if set(self._mappers) != set(NavelAction) or mapped_types != set(COMMAND_TYPES):
            raise RuntimeError("behavior mapper does not cover every action and command type")

    def map(self, intent: NavelBehaviorIntent) -> RobotBehaviorCommand:
        try:
            action = NavelAction(intent.action)
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
    def _orient(intent: NavelBehaviorIntent) -> OrientCommand:
        return OrientCommand(
            target_human_id=_required(
                intent.target_human_id, "target_human_id", NavelAction.ORIENT
            ),
            orientation_target_rad=intent.preferences.orientation_target_rad,
        )

    @staticmethod
    def _slow(intent: NavelBehaviorIntent) -> SlowCommand:
        return SlowCommand(
            target_speed_mps=_required(
                intent.preferences.target_speed_mps,
                "target_speed_mps",
                NavelAction.SLOW,
            ),
            target_human_id=intent.target_human_id,
        )

    @staticmethod
    def _yield(intent: NavelBehaviorIntent) -> YieldCommand:
        preferences = intent.preferences
        return YieldCommand(
            target_human_id=intent.target_human_id,
            target_speed_mps=preferences.target_speed_mps,
            preferred_social_distance_m=preferences.preferred_social_distance_m,
            passing_side=preferences.passing_side,
            hold_duration_s=preferences.hold_duration_s,
        )

    @staticmethod
    def _avoid(intent: NavelBehaviorIntent) -> AvoidCommand:
        preferences = intent.preferences
        return AvoidCommand(
            target_human_id=intent.target_human_id,
            target_speed_mps=preferences.target_speed_mps,
            preferred_social_distance_m=preferences.preferred_social_distance_m,
            passing_side=preferences.passing_side,
        )

    @staticmethod
    def _approach(intent: NavelBehaviorIntent) -> ApproachCommand:
        return ApproachCommand(
            target_human_id=_required(
                intent.target_human_id, "target_human_id", NavelAction.APPROACH
            ),
            preferred_social_distance_m=_required(
                intent.preferences.preferred_social_distance_m,
                "preferred_social_distance_m",
                NavelAction.APPROACH,
            ),
            target_speed_mps=intent.preferences.target_speed_mps,
        )

    @staticmethod
    def _greet(intent: NavelBehaviorIntent) -> GreetCommand:
        return GreetCommand(
            target_human_id=_required(
                intent.target_human_id, "target_human_id", NavelAction.GREET
            )
        )

    @staticmethod
    def _guide(intent: NavelBehaviorIntent) -> GuideCommand:
        preferences = intent.preferences
        return GuideCommand(
            target_human_id=_required(
                intent.target_human_id, "target_human_id", NavelAction.GUIDE
            ),
            target_speed_mps=preferences.target_speed_mps,
            preferred_social_distance_m=preferences.preferred_social_distance_m,
            passing_side=preferences.passing_side,
        )

    @staticmethod
    def _wait(intent: NavelBehaviorIntent) -> WaitCommand:
        return WaitCommand(
            hold_duration_s=_required(
                intent.preferences.hold_duration_s,
                "hold_duration_s",
                NavelAction.WAIT,
            )
        )

    @staticmethod
    def _disengage(intent: NavelBehaviorIntent) -> DisengageCommand:
        return DisengageCommand(
            target_human_id=_required(
                intent.target_human_id,
                "target_human_id",
                NavelAction.DISENGAGE,
            )
        )
