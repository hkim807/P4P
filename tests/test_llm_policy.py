"""Offline tests for schema-constrained LLM behavior selection."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from app.adapters.synthetic import SyntheticObservationAdapter, museum_guide_scenarios
from app.decision.llm_policy import (
    ACTION_CONTRACTS,
    PREFERENCE_FIELD_NAMES,
    LLMPolicyBridge,
    LLMPolicyError,
    TargetRequirement,
    action_contract_payload,
    behavior_selection_schema,
    render_decision_prompt,
)
from app.domain.models import Action, BehaviorIntent
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


VALID_SELECTIONS = {
    "CONTINUE": {"target_human_id": None, "preferences": {}},
    "MONITOR": {"target_human_id": None, "preferences": {}},
    "ORIENT": {"target_human_id": "visitor-1", "preferences": {}},
    "SLOW": {
        "target_human_id": None,
        "preferences": {"target_speed_mps": 0.2},
    },
    "YIELD": {"target_human_id": None, "preferences": {}},
    "AVOID": {"target_human_id": None, "preferences": {}},
    "APPROACH": {
        "target_human_id": "visitor-1",
        "preferences": {"preferred_social_distance_m": 1.2},
    },
    "GREET": {"target_human_id": "visitor-1", "preferences": {}},
    "GUIDE": {"target_human_id": "visitor-1", "preferences": {}},
    "WAIT": {
        "target_human_id": None,
        "preferences": {"hold_duration_s": 1.0},
    },
    "RESUME": {"target_human_id": None, "preferences": {}},
    "DISENGAGE": {"target_human_id": "visitor-1", "preferences": {}},
}

VALID_PREFERENCE_VALUES = {
    "target_speed_mps": 0.2,
    "preferred_social_distance_m": 1.2,
    "passing_side": "EITHER",
    "orientation_target_rad": 0.0,
    "hold_duration_s": 1.0,
}


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
    def assert_selection_valid(self, action, **updates):
        payload = {"action": action, **VALID_SELECTIONS[action], **updates}
        return LLMPolicyBridge(
            RecordingLLM(selection_json(**payload))
        ).decide(social_state(), ["HUMAN_DETECTED"])

    def assert_selection_invalid(self, action, **updates):
        payload = {"action": action, **VALID_SELECTIONS[action], **updates}
        with self.assertRaises(LLMPolicyError):
            LLMPolicyBridge(
                RecordingLLM(selection_json(**payload))
            ).decide(social_state(), ["HUMAN_DETECTED"])

    def test_every_action_has_exactly_one_complete_contract(self):
        action_values = {action.value for action in Action}
        self.assertEqual(set(ACTION_CONTRACTS), action_values)
        self.assertEqual(set(VALID_SELECTIONS), action_values)
        for action, contract in ACTION_CONTRACTS.items():
            with self.subTest(action=action):
                classifications = (
                    contract.required_preferences,
                    contract.optional_preferences,
                    contract.forbidden_preferences,
                )
                self.assertEqual(
                    set().union(*classifications), set(PREFERENCE_FIELD_NAMES)
                )
                for index, left in enumerate(classifications):
                    for right in classifications[index + 1 :]:
                        self.assertFalse(left & right)

    def test_smallest_valid_selection_for_every_action(self):
        for action in VALID_SELECTIONS:
            with self.subTest(action=action):
                intent = self.assert_selection_valid(action)
                self.assertEqual(intent.action, action)

    def test_every_required_target_rejects_omission_and_null(self):
        required_actions = {
            action
            for action, contract in ACTION_CONTRACTS.items()
            if contract.target == TargetRequirement.REQUIRED
        }
        for action in required_actions:
            base = {"action": action, **VALID_SELECTIONS[action]}
            with self.subTest(action=action, value="omitted"):
                payload = json.loads(selection_json(**base))
                del payload["target_human_id"]
                with self.assertRaises(LLMPolicyError):
                    LLMPolicyBridge(
                        RecordingLLM(json.dumps(payload))
                    ).decide(social_state(), ["HUMAN_DETECTED"])
            with self.subTest(action=action, value="null"):
                self.assert_selection_invalid(action, target_human_id=None)

    def test_every_forbidden_target_rejects_a_supplied_target(self):
        for action, contract in ACTION_CONTRACTS.items():
            if contract.target != TargetRequirement.FORBIDDEN:
                continue
            with self.subTest(action=action):
                self.assert_selection_invalid(
                    action, target_human_id="visitor-1"
                )

    def test_optional_targets_accept_null_or_a_known_human(self):
        for action, contract in ACTION_CONTRACTS.items():
            if contract.target != TargetRequirement.OPTIONAL:
                continue
            with self.subTest(action=action, value="null"):
                self.assert_selection_valid(action, target_human_id=None)
            with self.subTest(action=action, value="known"):
                self.assert_selection_valid(
                    action, target_human_id="visitor-1"
                )

    def test_required_preferences_reject_omission_and_explicit_null(self):
        for action, contract in ACTION_CONTRACTS.items():
            for field_name in contract.required_preferences:
                omitted = dict(VALID_SELECTIONS[action]["preferences"])
                omitted.pop(field_name)
                with self.subTest(action=action, field=field_name, value="omitted"):
                    self.assert_selection_invalid(action, preferences=omitted)

                explicit_null = dict(VALID_SELECTIONS[action]["preferences"])
                explicit_null[field_name] = None
                with self.subTest(action=action, field=field_name, value="null"):
                    self.assert_selection_invalid(action, preferences=explicit_null)

    def test_every_action_rejects_a_representative_forbidden_preference(self):
        for action, contract in ACTION_CONTRACTS.items():
            field_name = next(
                name
                for name in PREFERENCE_FIELD_NAMES
                if name in contract.forbidden_preferences
            )
            preferences = dict(VALID_SELECTIONS[action]["preferences"])
            preferences[field_name] = VALID_PREFERENCE_VALUES[field_name]
            with self.subTest(action=action, field=field_name):
                self.assert_selection_invalid(action, preferences=preferences)

    def test_optional_preferences_accept_omission_null_and_valid_values(self):
        for action, contract in ACTION_CONTRACTS.items():
            for field_name in contract.optional_preferences:
                with self.subTest(action=action, field=field_name, value="omitted"):
                    self.assert_selection_valid(action)

                preferences = dict(VALID_SELECTIONS[action]["preferences"])
                preferences[field_name] = None
                with self.subTest(action=action, field=field_name, value="null"):
                    self.assert_selection_valid(action, preferences=preferences)

                preferences[field_name] = VALID_PREFERENCE_VALUES[field_name]
                with self.subTest(action=action, field=field_name, value="valid"):
                    self.assert_selection_valid(action, preferences=preferences)

    def test_contradictory_wait_preferences_and_target_are_rejected(self):
        self.assert_selection_invalid(
            "WAIT",
            target_human_id="visitor-1",
            preferences={
                "target_speed_mps": 0.8,
                "preferred_social_distance_m": 1.2,
                "orientation_target_rad": 3.0,
            },
        )

    def test_slow_without_target_speed_is_rejected(self):
        self.assert_selection_invalid("SLOW", preferences={})

    def test_existing_numeric_and_enum_constraints_remain_authoritative(self):
        invalid_selections = (
            {"action": "SLOW", "preferences": {"target_speed_mps": -0.1}},
            {"action": "SLOW", "preferences": {"target_speed_mps": 0.9}},
            {
                "action": "APPROACH",
                "target_human_id": "visitor-1",
                "preferences": {"preferred_social_distance_m": 0.9},
            },
            {
                "action": "APPROACH",
                "target_human_id": "visitor-1",
                "preferences": {"preferred_social_distance_m": 1.5},
            },
            {
                "action": "ORIENT",
                "target_human_id": "visitor-1",
                "preferences": {"orientation_target_rad": 3.2},
            },
            {"action": "YIELD", "preferences": {"passing_side": "MIDDLE"}},
            {"action": "WAIT", "preferences": {"hold_duration_s": -0.1}},
            {"action": "WAIT", "preferences": {"hold_duration_s": 10.1}},
            {"action": "MONITOR", "valid_for_ms": 249},
            {"action": "MONITOR", "valid_for_ms": 2_001},
            {"action": "MONITOR", "decision_confidence": -0.1},
            {"action": "MONITOR", "decision_confidence": 1.1},
            {"action": "FLY"},
        )
        for updates in invalid_selections:
            with self.subTest(updates=updates):
                policy = LLMPolicyBridge(
                    RecordingLLM(selection_json(**updates))
                )
                with self.assertRaises(LLMPolicyError):
                    policy.decide(social_state(), ["HUMAN_DETECTED"])

    def test_prompt_action_contract_is_generated_from_authoritative_contract(self):
        prompt = render_decision_prompt(social_state(), ["HUMAN_DETECTED"])
        payload = json.loads(prompt.split("Input JSON: ", 1)[1])
        self.assertEqual(payload["action_contract"], action_contract_payload())
        self.assertEqual(
            set(payload["action_contract"]), {item.value for item in Action}
        )
        for action, rules in payload["action_contract"].items():
            contract = ACTION_CONTRACTS[action]
            self.assertEqual(rules["target_human_id"], contract.target.value)

    def test_documented_action_contract_matches_authoritative_contract(self):
        document = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "llm-policy-prompt-design.md"
        ).read_text(encoding="utf-8")
        documented = {}
        for line in document.splitlines():
            match = re.match(r"^\| `([A-Z]+)` \| (Required|Optional|Forbidden) \|", line)
            if match:
                documented[match.group(1)] = line

        prompt_contract = action_contract_payload()
        self.assertEqual(set(documented), set(prompt_contract))
        for action, rules in prompt_contract.items():
            cells = [cell.strip() for cell in documented[action].strip("|").split("|")]
            self.assertEqual(cells[1].upper(), rules["target_human_id"])
            for cell, key in zip(
                cells[2:5],
                (
                    "required_preferences",
                    "optional_preferences",
                    "forbidden_preferences",
                ),
            ):
                names = re.findall(r"`([^`]+)`", cell)
                self.assertEqual(names, rules[key])

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

    def test_policy_rejects_extra_output_fields(self):
        policy = LLMPolicyBridge(
            RecordingLLM(selection_json(explanation="I chose to monitor"))
        )
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
