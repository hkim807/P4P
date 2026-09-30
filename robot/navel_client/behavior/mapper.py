"""Pure translation from the shared server intent to explicit Navel commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias, TypeVar

from robot.navel_client.behavior.commands import (
    COMMAND_TYPES,
    ApproachCommand,
    ContinueCommand,
    EngageCommand,
    RobotBehaviorCommand,
    YieldCommand,
)
from robot.navel_client.behavior.intent import (
    NavelAction,
    NavelBehaviorIntent,
    _validate_mapper_requirements,
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
            NavelAction.ENGAGE: (EngageCommand, self._engage),
            NavelAction.CONTINUE: (ContinueCommand, lambda intent: ContinueCommand()),
            NavelAction.YIELD: (YieldCommand, self._yield),
            NavelAction.APPROACH: (ApproachCommand, self._approach),
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
        try:
            _validate_mapper_requirements(action, intent.target_human_id, intent.preferences)
        except ValueError as error:
            raise BehaviorMappingError(str(error)) from error
        command = mapper(intent)
        if type(command) is not expected_type:
            raise BehaviorMappingError(
                f"{action.value} mapper returned {type(command).__name__}; "
                f"expected {expected_type.__name__}"
            )
        return command

    @staticmethod
    def _yield(intent: NavelBehaviorIntent) -> YieldCommand:
        return YieldCommand(intent.target_human_id)

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
    def _engage(intent: NavelBehaviorIntent) -> EngageCommand:
        return EngageCommand(_required(intent.target_human_id, "target_human_id", NavelAction.ENGAGE))
