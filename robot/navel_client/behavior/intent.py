"""Standard-library wire model for BehaviorIntent JSON received by Navel."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class NavelAction(str, Enum):
    CONTINUE = "CONTINUE"
    MONITOR = "MONITOR"
    ORIENT = "ORIENT"
    SLOW = "SLOW"
    YIELD = "YIELD"
    AVOID = "AVOID"
    APPROACH = "APPROACH"
    GREET = "GREET"
    GUIDE = "GUIDE"
    WAIT = "WAIT"
    RESUME = "RESUME"
    DISENGAGE = "DISENGAGE"


class NavelPassingSide(str, Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    EITHER = "EITHER"


class NavelIntentParseError(ValueError):
    """Server JSON is not a defensively valid Navel behavior intent."""


@dataclass(frozen=True)
class NavelBehaviorPreferences:
    target_speed_mps: float | None = None
    preferred_social_distance_m: float | None = None
    passing_side: NavelPassingSide | None = None
    orientation_target_rad: float | None = None
    hold_duration_s: float | None = None


@dataclass(frozen=True)
class NavelBehaviorIntent:
    """Immutable client-side representation of the server wire contract."""

    schema_version: str
    decision_id: str
    observation_id: str
    social_state_id: str
    created_at_us: int
    action: NavelAction
    target_human_id: str | None
    preferences: NavelBehaviorPreferences
    valid_for_ms: int
    reason_codes: tuple[str, ...]
    decision_confidence: float | None

    @classmethod
    def from_payload(cls, payload: object) -> "NavelBehaviorIntent":
        if not isinstance(payload, Mapping):
            raise NavelIntentParseError("behavior_intent must be a JSON object")
        _reject_unknown_fields(payload, _INTENT_FIELDS, "behavior_intent")

        schema_version = _required(payload, "schema_version")
        if schema_version != "1.0":
            raise NavelIntentParseError("schema_version must be '1.0'")

        action_value = _required(payload, "action")
        if not isinstance(action_value, str):
            raise NavelIntentParseError("action must be a string")
        try:
            action = NavelAction(action_value)
        except ValueError as error:
            raise NavelIntentParseError(
                f"unsupported behavior action: {action_value!r}"
            ) from error

        preferences = _parse_preferences(payload.get("preferences", {}))
        target_human_id = _optional_identifier(
            payload.get("target_human_id"), "target_human_id", maximum=128
        )
        _validate_mapper_requirements(action, target_human_id, preferences)

        confidence = _optional_number(
            payload.get("decision_confidence"), "decision_confidence"
        )
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise NavelIntentParseError("decision_confidence must be between 0 and 1")

        return cls(
            schema_version=schema_version,
            decision_id=_identifier(
                _required(payload, "decision_id"), "decision_id", maximum=256
            ),
            observation_id=_identifier(
                _required(payload, "observation_id"),
                "observation_id",
                maximum=256,
            ),
            social_state_id=_identifier(
                _required(payload, "social_state_id"),
                "social_state_id",
                maximum=256,
            ),
            created_at_us=_integer(
                _required(payload, "created_at_us"),
                "created_at_us",
                minimum=0,
            ),
            action=action,
            target_human_id=target_human_id,
            preferences=preferences,
            valid_for_ms=_integer(
                _required(payload, "valid_for_ms"),
                "valid_for_ms",
                minimum=1,
                maximum=60_000,
            ),
            reason_codes=_reason_codes(_required(payload, "reason_codes")),
            decision_confidence=confidence,
        )


_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "decision_id",
        "observation_id",
        "social_state_id",
        "created_at_us",
        "action",
        "target_human_id",
        "preferences",
        "valid_for_ms",
        "reason_codes",
        "decision_confidence",
    }
)
_PREFERENCE_FIELDS = frozenset(
    {
        "target_speed_mps",
        "preferred_social_distance_m",
        "passing_side",
        "orientation_target_rad",
        "hold_duration_s",
    }
)
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


def _required(payload: Mapping[str, Any], field: str) -> Any:
    if field not in payload:
        raise NavelIntentParseError(f"missing required field: {field}")
    return payload[field]


def _reject_unknown_fields(
    payload: Mapping[str, Any], allowed: frozenset[str], context: str
) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise NavelIntentParseError(
            f"{context} contains unexpected fields: {sorted(unknown)}"
        )


def _identifier(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise NavelIntentParseError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise NavelIntentParseError(
            f"{field} must contain 1 to {maximum} characters"
        )
    return normalized


def _optional_identifier(
    value: object, field: str, *, maximum: int
) -> str | None:
    if value is None:
        return None
    return _identifier(value, field, maximum=maximum)


def _integer(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NavelIntentParseError(f"{field} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        limit = f" through {maximum}" if maximum is not None else " or greater"
        raise NavelIntentParseError(f"{field} must be {minimum}{limit}")
    return value


def _optional_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NavelIntentParseError(f"{field} must be a number or null")
    converted = float(value)
    if not math.isfinite(converted):
        raise NavelIntentParseError(f"{field} must be finite")
    return converted


def _nonnegative_optional_number(value: object, field: str) -> float | None:
    converted = _optional_number(value, field)
    if converted is not None and converted < 0.0:
        raise NavelIntentParseError(f"{field} must be non-negative")
    return converted


def _parse_preferences(value: object) -> NavelBehaviorPreferences:
    if not isinstance(value, Mapping):
        raise NavelIntentParseError("preferences must be a JSON object")
    _reject_unknown_fields(value, _PREFERENCE_FIELDS, "preferences")
    passing_value = value.get("passing_side")
    if passing_value is None:
        passing_side = None
    elif isinstance(passing_value, str):
        try:
            passing_side = NavelPassingSide(passing_value)
        except ValueError as error:
            raise NavelIntentParseError(
                f"unsupported passing_side: {passing_value!r}"
            ) from error
    else:
        raise NavelIntentParseError("passing_side must be a string or null")
    return NavelBehaviorPreferences(
        target_speed_mps=_nonnegative_optional_number(
            value.get("target_speed_mps"), "target_speed_mps"
        ),
        preferred_social_distance_m=_nonnegative_optional_number(
            value.get("preferred_social_distance_m"),
            "preferred_social_distance_m",
        ),
        passing_side=passing_side,
        orientation_target_rad=_optional_number(
            value.get("orientation_target_rad"), "orientation_target_rad"
        ),
        hold_duration_s=_nonnegative_optional_number(
            value.get("hold_duration_s"), "hold_duration_s"
        ),
    )


def _reason_codes(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise NavelIntentParseError("reason_codes must be a list of 1 to 16 codes")
    if any(not isinstance(code, str) or not _REASON_CODE.fullmatch(code) for code in value):
        raise NavelIntentParseError("reason_codes contains an invalid code")
    return tuple(value)


def _validate_mapper_requirements(
    action: NavelAction,
    target_human_id: str | None,
    preferences: NavelBehaviorPreferences,
) -> None:
    targeted = {
        NavelAction.ORIENT,
        NavelAction.APPROACH,
        NavelAction.GREET,
        NavelAction.GUIDE,
        NavelAction.DISENGAGE,
    }
    if action in targeted and target_human_id is None:
        raise NavelIntentParseError(f"{action.value} requires target_human_id")
    required_preferences = {
        NavelAction.SLOW: ("target_speed_mps", preferences.target_speed_mps),
        NavelAction.APPROACH: (
            "preferred_social_distance_m",
            preferences.preferred_social_distance_m,
        ),
        NavelAction.WAIT: ("hold_duration_s", preferences.hold_duration_s),
    }
    required = required_preferences.get(action)
    if required is not None and required[1] is None:
        raise NavelIntentParseError(f"{action.value} requires {required[0]}")
