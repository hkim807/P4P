"""Grounded prompt and response contract for social-navigation debug mode.

This module is deliberately independent of transport and storage. It renders
one request from an immutable ``SocialState`` and validates a returned evidence
trace. The Ollama request and request-bound snapshot are integrated separately.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Protocol, Sequence
from uuid import uuid4

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
    LLMPolicyBridge,
    LLMPolicyError,
)
from app.domain.models import Action, BehaviorIntent, SocialState
from app.llm import CapturedLLMRequest, LLMGenerationResult, LLMRequestError


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


class DebugSnapshotStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DebugPromptMessage(DebugModel):
    role: Annotated[str, Field(min_length=1, max_length=32)]
    content: str


class DebugRequestParameters(DebugModel):
    temperature: Annotated[float, Field(allow_inf_nan=False)]
    response_schema_name: str | None
    response_schema: dict[str, Any] | None
    response_format: dict[str, Any] | None


class DebugInferenceMetadata(DebugModel):
    provider: str
    requested_model: str
    returned_model: str | None
    endpoint: str
    decision_mode: Annotated[str, Field(pattern="^DEBUG$")] = "DEBUG"
    prompt_version: str
    requested_at: str
    responded_at: str
    latency_ms: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    response_id: str | None = None
    created: int | None = None
    finish_reason: str | None = None
    usage: dict[str, int | None] | None = None


class DebugSnapshotError(DebugModel):
    code: Annotated[str, Field(min_length=1, max_length=128)]
    message: Annotated[str, Field(min_length=1, max_length=4_000)]


class DebugDecisionSnapshot(DebugModel):
    """All inputs and outputs for one atomic Debug-mode decision request."""

    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    status: DebugSnapshotStatus
    source_id: str
    observation_id: str
    social_state_id: str
    state_timestamp_us: int
    clock_domain: str
    raw_social_state: dict[str, Any]
    rendered_messages: list[DebugPromptMessage] = Field(min_length=2)
    request_parameters: DebugRequestParameters
    raw_response: str | None
    validated_response: DebugPolicyResponse | None
    behavior_intent: BehaviorIntent | None
    metadata: DebugInferenceMetadata
    error: DebugSnapshotError | None

    @model_validator(mode="after")
    def validate_status_payload(self) -> "DebugDecisionSnapshot":
        if self.status == DebugSnapshotStatus.COMPLETED:
            if self.validated_response is None or self.behavior_intent is None:
                raise ValueError(
                    "completed snapshot requires validated output and intent"
                )
            if self.error is not None:
                raise ValueError("completed snapshot cannot contain an error")
        elif self.error is None:
            raise ValueError("failed snapshot requires an error")
        return self


@dataclass(frozen=True)
class DebugPolicyDecision:
    behavior_intent: BehaviorIntent | None
    snapshot: DebugDecisionSnapshot
    error_code: str | None = None


class TraceableLLM(Protocol):
    provider: str
    model: str
    endpoint: str

    def generate_with_capture(
        self,
        message: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        response_schema: dict[str, Any] | None = None,
        response_schema_name: str = "social_navigation_behavior_selection",
    ) -> LLMGenerationResult: ...


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


class DebugPolicyBridge:
    """Run and capture one grounded Debug-mode policy request."""

    def __init__(self, llm: TraceableLLM) -> None:
        self.llm = llm
        self._intent_bridge = LLMPolicyBridge(llm)

    @staticmethod
    def _snapshot(
        *,
        request_id: str,
        source_id: str,
        state: SocialState,
        raw_social_state: dict[str, Any],
        request: CapturedLLMRequest,
        responded_at: str,
        latency_ms: float,
        raw_response: str | None,
        validated_response: DebugPolicyResponse | None,
        behavior_intent: BehaviorIntent | None,
        error: DebugSnapshotError | None,
        response: LLMGenerationResult | None = None,
    ) -> DebugDecisionSnapshot:
        return DebugDecisionSnapshot(
            request_id=request_id,
            status=(
                DebugSnapshotStatus.COMPLETED
                if error is None
                else DebugSnapshotStatus.FAILED
            ),
            source_id=source_id,
            observation_id=state.source_observation_id,
            social_state_id=state.state_id,
            state_timestamp_us=state.timestamp_us,
            clock_domain=raw_social_state["clock_domain"],
            raw_social_state=raw_social_state,
            rendered_messages=[
                DebugPromptMessage(role=item["role"], content=item["content"])
                for item in request.messages
            ],
            request_parameters=DebugRequestParameters(
                temperature=request.temperature,
                response_schema_name=request.response_schema_name,
                response_schema=request.response_schema,
                response_format=request.response_format,
            ),
            raw_response=raw_response,
            validated_response=validated_response,
            behavior_intent=behavior_intent,
            metadata=DebugInferenceMetadata(
                provider=request.provider,
                requested_model=request.model,
                returned_model=response.returned_model if response else None,
                endpoint=request.endpoint,
                prompt_version=DEBUG_PROMPT_VERSION,
                requested_at=request.requested_at,
                responded_at=responded_at,
                latency_ms=latency_ms,
                response_id=response.response_id if response else None,
                created=response.created if response else None,
                finish_reason=response.finish_reason if response else None,
                usage=response.usage if response else None,
            ),
            error=error,
        )

    def decide(
        self,
        state: SocialState,
        triggers: Sequence[Any],
        *,
        source_id: str,
    ) -> DebugPolicyDecision:
        request_id = f"debug-request-{uuid4().hex}"
        raw_social_state = deepcopy(state.model_dump(mode="json"))
        prompt = render_debug_prompt(state, triggers)
        response_schema = debug_response_schema(state, triggers)
        try:
            generation = self.llm.generate_with_capture(
                prompt,
                system_prompt=DEBUG_SYSTEM_PROMPT,
                temperature=0.0,
                response_schema=response_schema,
                response_schema_name="social_navigation_debug_decision",
            )
        except LLMRequestError as error:
            snapshot_error = DebugSnapshotError(
                code="llm_request_failed",
                message=str(error)[:4_000] or "LLM request failed",
            )
            snapshot = self._snapshot(
                request_id=request_id,
                source_id=source_id,
                state=state,
                raw_social_state=raw_social_state,
                request=error.request,
                responded_at=error.failed_at,
                latency_ms=error.latency_ms,
                raw_response=None,
                validated_response=None,
                behavior_intent=None,
                error=snapshot_error,
            )
            return DebugPolicyDecision(None, snapshot, snapshot_error.code)

        raw_response = generation.raw_content
        try:
            validated = validate_debug_response(raw_response, state, triggers)
        except DebugPolicyError as error:
            snapshot_error = DebugSnapshotError(
                code="invalid_llm_debug_response",
                message=str(error)[:4_000],
            )
            snapshot = self._snapshot(
                request_id=request_id,
                source_id=source_id,
                state=state,
                raw_social_state=raw_social_state,
                request=generation.request,
                responded_at=generation.responded_at,
                latency_ms=generation.latency_ms,
                raw_response=raw_response,
                validated_response=None,
                behavior_intent=None,
                error=snapshot_error,
                response=generation,
            )
            return DebugPolicyDecision(None, snapshot, snapshot_error.code)

        try:
            intent = self._intent_bridge.build_intent_from_payload(
                state,
                triggers,
                validated.behavior_selection_payload(),
                raw_response=raw_response,
                policy_prompt_version=DEBUG_PROMPT_VERSION,
            )
        except LLMPolicyError as error:
            snapshot_error = DebugSnapshotError(
                code="invalid_llm_behavior_selection",
                message=str(error)[:4_000],
            )
            snapshot = self._snapshot(
                request_id=request_id,
                source_id=source_id,
                state=state,
                raw_social_state=raw_social_state,
                request=generation.request,
                responded_at=generation.responded_at,
                latency_ms=generation.latency_ms,
                raw_response=raw_response,
                validated_response=validated,
                behavior_intent=None,
                error=snapshot_error,
                response=generation,
            )
            return DebugPolicyDecision(None, snapshot, snapshot_error.code)

        snapshot = self._snapshot(
            request_id=request_id,
            source_id=source_id,
            state=state,
            raw_social_state=raw_social_state,
            request=generation.request,
            responded_at=generation.responded_at,
            latency_ms=generation.latency_ms,
            raw_response=raw_response,
            validated_response=validated,
            behavior_intent=intent,
            error=None,
            response=generation,
        )
        return DebugPolicyDecision(intent, snapshot)


__all__ = [
    "ACTION_SCORE_TOTAL_TOLERANCE",
    "DEBUG_PROMPT_VERSION",
    "DEBUG_SYSTEM_PROMPT",
    "DebugActionScore",
    "DebugDecisionSnapshot",
    "DebugEvidence",
    "DebugInferenceMetadata",
    "DebugPolicyError",
    "DebugPolicyBridge",
    "DebugPolicyDecision",
    "DebugPolicyResponse",
    "DebugPromptMessage",
    "DebugRequestParameters",
    "DebugRobotInput",
    "DebugSnapshotError",
    "DebugSnapshotStatus",
    "EvidenceType",
    "debug_response_schema",
    "render_debug_prompt",
    "social_state_leaf_values",
    "validate_debug_response",
]
