"""Free-form LLM decision-support bridge for canonical social state."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Protocol, Sequence

from app.domain.models import SocialState


SYSTEM_PROMPT = (
    "You are a read-only social-navigation decision-support assistant. Review the "
    "provided temporal SocialState and scheduler triggers, then give a concise, "
    "cautious high-level recommendation with a short explanation. Do not claim to "
    "control the robot. The response is advisory free-form text, not BehaviorIntent."
)


class TextLLM(Protocol):
    def generate(
        self,
        message: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.2,
    ) -> str: ...


def _trigger_code(trigger: Any) -> str:
    if isinstance(trigger, Enum):
        return str(trigger.value)
    return str(trigger)


def render_decision_prompt(state: SocialState, triggers: Sequence[Any]) -> str:
    """Render stable compact JSON without null-valued fields or image bytes."""
    state_payload = state.model_dump(mode="json", exclude_none=True)
    payload = {
        "scheduler_triggers": [_trigger_code(trigger) for trigger in triggers],
        "social_state": state_payload,
    }
    compact = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return (
        "Recommend the next high-level social-navigation behavior from this canonical "
        f"state. All measurements use SI units. Input JSON: {compact}"
    )


class LLMPolicyBridge:
    """Invoke the existing LLM service without parsing or executing its text."""

    def __init__(self, llm: TextLLM) -> None:
        self.llm = llm

    def decide(self, state: SocialState, triggers: Sequence[Any]) -> str:
        return self.llm.generate(
            render_decision_prompt(state, triggers),
            system_prompt=SYSTEM_PROMPT,
            temperature=0.2,
        )
