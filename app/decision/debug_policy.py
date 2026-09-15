"""Grounded prompt and response contract for social-navigation debug mode.

This module is deliberately independent of transport and storage. It renders
one request from an immutable ``SocialState`` and validates a returned evidence
trace. The Ollama request and request-bound snapshot are integrated separately.
"""

from __future__ import annotations

import json
import math
from enum import Enum
from typing import Annotated, Any, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.decision.llm_policy import (
    ACTION_CONTRACTS,
    MAX_INTENT_VALIDITY_MS,
    MIN_INTENT_VALIDITY_MS,
    PREFERENCE_FIELD_NAMES,
    PolicyPreferences,
    SYSTEM_PROMPT,
    TargetRequirement,
    action_contract_payload,
    behavior_selection_schema,
)
from app.domain.models import Action, SocialState


DEBUG_PROMPT_VERSION = "llm-social-navigation-debug-v1"
ACTION_SCORE_TOTAL_TOLERANCE = 0.02

DebugText = Annotated[str, Field(min_length=1, max_length=1_000)]
SourceField = Annotated[str, Field(min_length=2, max_length=512, pattern=r"^/")]
JsonScalar = str | bool | int | float | None


class DebugPolicyError(ValueError):
    """The debug response is malformed or not grounded in the supplied state."""


class DebugModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        use_enum_values=True,
    )


class EvidenceType(str, Enum):
    OBSERVATION = "OBSERVATION"
    INTERPRETATION = "INTERPRETATION"


class DebugRobotInput(DebugModel):
    source: SourceField
    latest_value: JsonScalar
    interpretation: DebugText

    @model_validator(mode="after")
    def reject_non_finite_value(self) -> "DebugRobotInput":
        if isinstance(self.latest_value, float) and not math.isfinite(
            self.latest_value
        ):
            raise ValueError("latest_value must be finite")
        return self


class DebugEvidence(DebugModel):
    type: EvidenceType
    description: DebugText
    source_fields: list[SourceField] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def require_unique_sources(self) -> "DebugEvidence":
        if len(self.source_fields) != len(set(self.source_fields)):
            raise ValueError("evidence source_fields must be unique")
        return self


class DebugActionScore(DebugModel):
    action: Action
    score: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    reason: DebugText


class DebugPolicyResponse(DebugModel):
    """Complete model-reported interpretation and behavior selection."""

    social_summary: DebugText
    robot_inputs: list[DebugRobotInput] = Field(max_length=32)
    evidence: list[DebugEvidence] = Field(max_length=32)
    recommended_action: Action
    target_human_id: Annotated[str, Field(min_length=1, max_length=128)] | None
    preferences: PolicyPreferences
    valid_for_ms: Annotated[
        int, Field(ge=MIN_INTENT_VALIDITY_MS, le=MAX_INTENT_VALIDITY_MS)
    ]
    reason_codes: list[
        Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]
    ] = Field(min_length=1, max_length=8)
    decision_confidence: Annotated[
        float, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    decision_rationale: DebugText
    action_scores: list[DebugActionScore] = Field(
        min_length=len(Action), max_length=len(Action)
    )
    uncertainties: list[DebugText] = Field(max_length=16)

    @model_validator(mode="after")
    def validate_complete_action_ranking(self) -> "DebugPolicyResponse":
        expected = {action.value for action in Action}
        scored = [item.action for item in self.action_scores]
        if len(scored) != len(set(scored)) or set(scored) != expected:
            missing = sorted(expected - set(scored))
            duplicates = sorted(
                action for action in set(scored) if scored.count(action) > 1
            )
            raise ValueError(
                "action_scores must contain every action exactly once; "
                f"missing={missing}, duplicates={duplicates}"
            )
        total = sum(item.score for item in self.action_scores)
        if not math.isclose(
            total,
            1.0,
            rel_tol=0.0,
            abs_tol=ACTION_SCORE_TOTAL_TOLERANCE,
        ):
            raise ValueError(
                "action_scores must sum approximately to 1.0; "
                f"received {total:.6f}"
            )
        scores = {item.action: item.score for item in self.action_scores}
        highest = max(scores.values())
        if scores[self.recommended_action] < highest - 1e-12:
            raise ValueError(
                "recommended_action must have a highest model-reported score"
            )
        sources = [item.source for item in self.robot_inputs]
        if len(sources) != len(set(sources)):
            raise ValueError("robot_inputs sources must be unique")
        return self

    def behavior_selection_payload(self) -> dict[str, Any]:
        """Return fields consumed by the existing BehaviorIntent validation path."""
        return {
            "action": self.recommended_action,
            "target_human_id": self.target_human_id,
            "preferences": self.preferences.model_dump(mode="json"),
            "valid_for_ms": self.valid_for_ms,
            "reason_codes": list(self.reason_codes),
            "decision_confidence": self.decision_confidence,
        }


DEBUG_SYSTEM_PROMPT = SYSTEM_PROMPT + """

Debug-mode evidence rules:
- Analyse only the supplied SocialState. Unknown, absent, or null information remains unknown. Never invent observations, intentions, emotions, trajectories, or environmental facts.
- `robot_inputs` identifies important available values. Use an exact JSON Pointer from `available_source_fields` as `source`, copy its scalar value exactly into `latest_value`, and add only a brief interpretation.
- Label direct state facts as OBSERVATION and derived social conclusions as INTERPRETATION. Every evidence item must cite one or more exact source fields.
- Use temporal conclusions only when SocialState contains a derived temporal field that supports them. SocialState does not contain the estimator's raw sample sequence, so never invent individual historical readings or sample counts.
- Select `recommended_action` only from `available_actions` and connect `decision_rationale` to cited state evidence.
- Include every available action exactly once in `action_scores`. Scores are model-reported preference/confidence scores, not calibrated probabilities; keep each in 0.0-1.0, make them sum approximately to 1.0, and give the recommended action a highest score.
- State important missing or ambiguous information in `uncertainties`.
- Return concise conclusions and supporting evidence rather than private or unrestricted chain-of-thought.
- Return only one JSON object matching `response_json_schema`, with no Markdown or prose outside it."""


def _escape_json_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def social_state_leaf_values(state: SocialState) -> dict[str, JsonScalar]:
    """Map every scalar SocialState value to its RFC 6901 JSON Pointer."""
    leaves: dict[str, JsonScalar] = {}

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{path}/{_escape_json_pointer_part(str(key))}")
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}/{index}")
            return
        leaves[path] = value

    visit(state.model_dump(mode="json"), "")
    return leaves


def debug_response_schema(
    state: SocialState, triggers: Sequence[Any]
) -> dict[str, Any]:
    """Return the debug JSON schema with request-specific IDs and reason codes."""
    schema = DebugPolicyResponse.model_json_schema()
    selection_schema = behavior_selection_schema(state, triggers)
    schema["properties"]["target_human_id"] = selection_schema["properties"][
        "target_human_id"
    ]
    schema["properties"]["reason_codes"]["items"] = selection_schema[
        "properties"
    ]["reason_codes"]["items"]
    return schema


def render_debug_prompt(state: SocialState, triggers: Sequence[Any]) -> str:
    """Render a deterministic debug request without omitting unknown state fields."""
    trigger_codes = sorted(
        {str(getattr(trigger, "value", trigger)) for trigger in triggers}
    )
    payload = {
        "debug_prompt_version": DEBUG_PROMPT_VERSION,
        "task_context": {
            "environment": "unmanned_museum_or_laboratory",
            "default_robot_behavior": "follow_fixed_roaming_route",
            "decision_scope": "one_high_level_behavior_intent_with_evidence_trace",
            "measurement_units": "SI",
        },
        "scheduler_triggers": trigger_codes,
        "available_actions": [action.value for action in Action],
        "action_contract": action_contract_payload(),
        "available_source_fields": sorted(social_state_leaf_values(state)),
        "social_state": state.model_dump(mode="json"),
        "response_json_schema": debug_response_schema(state, trigger_codes),
    }
    compact = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return (
        "Select and explain the next high-level social-navigation behavior using "
        f"only this canonical temporal state. Input JSON: {compact}"
    )


def _values_match(expected: JsonScalar, reported: JsonScalar) -> bool:
    expected_is_number = isinstance(expected, (int, float)) and not isinstance(
        expected, bool
    )
    reported_is_number = isinstance(reported, (int, float)) and not isinstance(
        reported, bool
    )
    if expected_is_number and reported_is_number:
        return math.isclose(
            float(expected), float(reported), rel_tol=1e-9, abs_tol=1e-12
        )
    return type(expected) is type(reported) and expected == reported


def validate_debug_response(
    raw_response: str,
    state: SocialState,
    triggers: Sequence[Any] = (),
) -> DebugPolicyResponse:
    """Parse a debug response and verify all cited state fields and copied values."""
    try:
        response = DebugPolicyResponse.model_validate_json(raw_response)
    except ValidationError as error:
        details = error.errors(include_url=False, include_input=False)
        raise DebugPolicyError(
            f"LLM response does not match the debug schema: {details}"
        ) from error

    leaves = social_state_leaf_values(state)
    errors: list[str] = []
    selection_schema = behavior_selection_schema(state, triggers)
    target_schema = selection_schema["properties"]["target_human_id"]
    allowed_targets = (
        set(target_schema["anyOf"][0]["enum"])
        if "anyOf" in target_schema
        else set()
    )
    if (
        response.target_human_id is not None
        and response.target_human_id not in allowed_targets
    ):
        errors.append(
            f"target_human_id {response.target_human_id!r} is not currently observed"
        )
    allowed_reasons = set(
        selection_schema["properties"]["reason_codes"]["items"]["enum"]
    )
    unsupported_reasons = set(response.reason_codes) - allowed_reasons
    if unsupported_reasons:
        errors.append(f"unsupported reason codes {sorted(unsupported_reasons)}")

    contract = ACTION_CONTRACTS[response.recommended_action]
    if (
        contract.target == TargetRequirement.REQUIRED
        and response.target_human_id is None
    ):
        errors.append(f"{response.recommended_action} requires target_human_id")
    if (
        contract.target == TargetRequirement.FORBIDDEN
        and response.target_human_id is not None
    ):
        errors.append(f"{response.recommended_action} forbids target_human_id")
    preferences = response.preferences.model_dump(mode="json")
    for field_name in PREFERENCE_FIELD_NAMES:
        value = preferences[field_name]
        if field_name in contract.required_preferences and value is None:
            errors.append(
                f"{response.recommended_action} requires preferences.{field_name}"
            )
        if field_name in contract.forbidden_preferences and value is not None:
            errors.append(
                f"{response.recommended_action} forbids preferences.{field_name}"
            )

    for item in response.robot_inputs:
        if item.source not in leaves:
            errors.append(f"unknown robot input source {item.source!r}")
            continue
        if not _values_match(leaves[item.source], item.latest_value):
            errors.append(
                f"robot input {item.source!r} does not match the supplied SocialState"
            )
    for item in response.evidence:
        for source in item.source_fields:
            if source not in leaves:
                errors.append(f"unknown evidence source {source!r}")
    if errors:
        raise DebugPolicyError("; ".join(errors))
    return response


__all__ = [
    "ACTION_SCORE_TOTAL_TOLERANCE",
    "DEBUG_PROMPT_VERSION",
    "DEBUG_SYSTEM_PROMPT",
    "DebugActionScore",
    "DebugEvidence",
    "DebugPolicyError",
    "DebugPolicyResponse",
    "DebugRobotInput",
    "EvidenceType",
    "debug_response_schema",
    "render_debug_prompt",
    "social_state_leaf_values",
    "validate_debug_response",
]
