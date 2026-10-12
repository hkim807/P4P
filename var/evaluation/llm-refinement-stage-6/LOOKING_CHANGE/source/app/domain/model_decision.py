"""Shared strict final output for rule-based, LLM and VLM policies."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


# Spell out whitespace so Pydantic and JSON Schema regex engines agree, including
# Python's separator controls U+001C..U+001F. Valid explanations are not trimmed.
NON_WHITESPACE_PATTERN = (
    r"[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680"
    r"\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"
)


class FinalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)

    action: Literal["CONTINUE", "APPROACH", "ENGAGE", "YIELD"]
    reason: str = Field(min_length=1, pattern=NON_WHITESPACE_PATTERN)


# Preserve existing imports for model inference and replay consumers.
ModelDecision = FinalDecision


class ModelDecisionValidationError(ValueError):
    """Model content is not exactly one JSON object satisfying ModelDecision."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ModelDecisionValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ModelDecisionValidationError(f"invalid JSON constant: {value}")


def parse_model_decision(content: str) -> FinalDecision:
    """Validate the complete text; never repair, extract, or default a decision."""
    if not isinstance(content, str):
        raise ModelDecisionValidationError("model content must be a string")
    try:
        payload = json.loads(content, object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise ModelDecisionValidationError(f"invalid model decision JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ModelDecisionValidationError("model decision must be a JSON object")
    try:
        return FinalDecision.model_validate(payload)
    except ValidationError as error:
        raise ModelDecisionValidationError(f"invalid model decision: {error}") from error


def model_decision_schema() -> dict[str, Any]:
    """Fresh JSON Schema shared by the exported artifact and Ollama requests."""
    return FinalDecision.model_json_schema(mode="validation")
