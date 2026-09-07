"""Offline tests for schema-constrained LLM behavior selection."""

from __future__ import annotations

import json
import unittest

from app.adapters.synthetic import SyntheticObservationAdapter, museum_guide_scenarios
from app.decision.llm_policy import (
    LLMPolicyBridge,
    LLMPolicyError,
    behavior_selection_schema,
)
from app.domain.models import BehaviorIntent
from app.state import TemporalSocialStateEstimator


def social_state(*, humans: bool = True):
    scenario = museum_guide_scenarios()["newcomer_requests_guidance"]
    observation = SyntheticObservationAdapter(scenario).sample_at(0.0).observation
    if not humans:
        observation = observation.model_copy(update={"humans": []})
    return TemporalSocialStateEstimator().update(observation)


def selection_json(**updates):
    payload = {
        "action": "MONITOR",
        "target_human_id": None,
        "preferences": {},
        "valid_for_ms": 1_000,
        "reason_codes": ["INSUFFICIENT_EVIDENCE"],
        "decision_confidence": 0.55,
    }
    payload.update(updates)
    return json.dumps(payload)


class RecordingLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def generate(
        self,
        message,
        *,
        system_prompt=None,
        temperature=0.2,
        response_schema=None,
    ):
        self.calls.append(
            {
                "message": message,
                "system_prompt": system_prompt,
                "temperature": temperature,
                "response_schema": response_schema,
            }
        )
        return self.response


class LLMPolicyTests(unittest.TestCase):
    def test_policy_builds_canonical_intent_and_owns_metadata(self):
        state = social_state()
        llm = RecordingLLM(selection_json())
        policy = LLMPolicyBridge(llm)

        first = policy.decide(state, ["HUMAN_DETECTED"])
        second = policy.decide(state, ["HUMAN_DETECTED"])

        self.assertIsInstance(first, BehaviorIntent)
        self.assertEqual(first.observation_id, state.source_observation_id)
        self.assertEqual(first.social_state_id, state.state_id)
        self.assertEqual(first.created_at_us, state.timestamp_us)
        self.assertEqual(first.decision_id, second.decision_id)
        self.assertEqual(llm.calls[0]["temperature"], 0.0)
        self.assertEqual(
            llm.calls[0]["response_schema"],
            behavior_selection_schema(state, ["HUMAN_DETECTED"]),
        )

    def test_schema_restricts_targets_to_current_tracks(self):
        state = social_state()
        schema = behavior_selection_schema(state, ["HUMAN_DETECTED"])
        target = schema["properties"]["target_human_id"]
        self.assertEqual(target["anyOf"][0]["enum"], ["visitor-1"])

        empty_state = social_state(humans=False)
        empty_schema = behavior_selection_schema(empty_state, [])
        self.assertEqual(
            empty_schema["properties"]["target_human_id"], {"type": "null"}
        )

    def test_policy_accepts_a_grounded_targeted_action(self):
        state = social_state()
        llm = RecordingLLM(
            selection_json(
                action="ORIENT",
                target_human_id="visitor-1",
                reason_codes=["HUMAN_DETECTED"],
                decision_confidence=0.72,
            )
        )
        intent = LLMPolicyBridge(llm).decide(state, ["HUMAN_DETECTED"])
        self.assertEqual(intent.action, "ORIENT")
        self.assertEqual(intent.target_human_id, "visitor-1")

    def test_policy_rejects_non_json_output(self):
        policy = LLMPolicyBridge(RecordingLLM("Approach the visitor."))
        with self.assertRaisesRegex(LLMPolicyError, "does not match"):
            policy.decide(social_state(), ["HUMAN_DETECTED"])

    def test_policy_rejects_unknown_target_even_without_schema_enforcement(self):
        llm = RecordingLLM(
            selection_json(
                action="ORIENT",
                target_human_id="invented-person",
                reason_codes=["HUMAN_DETECTED"],
            )
        )
        with self.assertRaisesRegex(LLMPolicyError, "unknown target"):
            LLMPolicyBridge(llm).decide(social_state(), ["HUMAN_DETECTED"])

    def test_policy_rejects_out_of_bounds_preferences(self):
        llm = RecordingLLM(
            selection_json(
                action="SLOW",
                preferences={"target_speed_mps": 1.5},
                reason_codes=["HUMAN_NEARBY"],
            )
        )
        with self.assertRaisesRegex(LLMPolicyError, "does not match"):
            LLMPolicyBridge(llm).decide(social_state(), ["HUMAN_DETECTED"])


if __name__ == "__main__":
    unittest.main()
