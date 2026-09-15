"""Tests for the isolated Debug Mode prompt and structured evidence response."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from app.adapters.synthetic import SyntheticObservationAdapter, museum_guide_scenarios
from app.config import DecisionMode, Settings
from app.decision.debug_policy import (
    DEBUG_PROMPT_VERSION,
    DEBUG_SYSTEM_PROMPT,
    DebugPolicyError,
    debug_response_schema,
    render_debug_prompt,
    social_state_leaf_values,
    validate_debug_response,
)
from app.domain.models import Action
from app.state import TemporalSocialStateEstimator


def social_state(*, humans: bool = True):
    scenario = museum_guide_scenarios()["newcomer_requests_guidance"]
    observation = SyntheticObservationAdapter(scenario).sample_at(0.0).observation
    if not humans:
        observation = observation.model_copy(update={"humans": []})
    return TemporalSocialStateEstimator().update(observation)


def valid_debug_payload(state=None):
    state = state or social_state()
    leaves = social_state_leaf_values(state)
    source = "/humans/0/distance_m" if state.humans else "/robot/task"
    return {
        "social_summary": "One tracked person is represented in the current state."
        if state.humans
        else "No people are represented in the current state.",
        "robot_inputs": [
            {
                "source": source,
                "latest_value": leaves[source],
                "interpretation": "Current scalar state value.",
            }
        ],
        "evidence": [
            {
                "type": "OBSERVATION",
                "description": "The cited value is present in SocialState.",
                "source_fields": [source],
            }
        ],
        "recommended_action": "MONITOR",
        "target_human_id": None,
        "preferences": {},
        "valid_for_ms": 1_000,
        "reason_codes": ["INSUFFICIENT_EVIDENCE"],
        "decision_confidence": 0.45,
        "decision_rationale": "More evidence is needed before changing behavior.",
        "action_scores": [
            {
                "action": action.value,
                "score": 0.45 if action == Action.MONITOR else 0.05,
                "reason": "Preferred while evidence is limited."
                if action == Action.MONITOR
                else "Less supported by the current evidence.",
            }
            for action in Action
        ],
        "uncertainties": ["Human intent is unavailable."],
    }


class DecisionModeTests(unittest.TestCase):
    def test_normal_is_the_default_mode(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DECISION_MODE", None)
            self.assertEqual(Settings().decision_mode, DecisionMode.NORMAL)

    def test_debug_mode_is_case_insensitive(self):
        with patch.dict(os.environ, {"DECISION_MODE": "debug"}):
            self.assertEqual(Settings().decision_mode, DecisionMode.DEBUG)
        self.assertEqual(
            Settings(decision_mode="debug").decision_mode, DecisionMode.DEBUG
        )

    def test_invalid_mode_fails_during_configuration(self):
        with patch.dict(os.environ, {"DECISION_MODE": "verbose"}):
            with self.assertRaisesRegex(ValueError, "NORMAL, DEBUG"):
                Settings()


class DebugPolicyContractTests(unittest.TestCase):
    def test_prompt_is_deterministic_and_includes_complete_state(self):
        state = social_state()
        first = render_debug_prompt(state, ["HUMAN_DETECTED"])
        second = render_debug_prompt(state, ["HUMAN_DETECTED"])

        self.assertEqual(first, second)
        payload = json.loads(first.split("Input JSON: ", 1)[1])
        self.assertEqual(payload["debug_prompt_version"], DEBUG_PROMPT_VERSION)
        self.assertEqual(
            payload["available_actions"], [action.value for action in Action]
        )
        self.assertEqual(payload["social_state"], state.model_dump(mode="json"))
        self.assertIn("/humans/0/distance_m", payload["available_source_fields"])
        self.assertIn("response_json_schema", payload)
        self.assertIn('"goal":null', first)

    def test_system_prompt_requests_evidence_without_hidden_reasoning(self):
        self.assertIn("Analyse only the supplied SocialState", DEBUG_SYSTEM_PROMPT)
        self.assertIn("OBSERVATION", DEBUG_SYSTEM_PROMPT)
        self.assertIn("INTERPRETATION", DEBUG_SYSTEM_PROMPT)
        self.assertIn("not calibrated probabilities", DEBUG_SYSTEM_PROMPT)
        self.assertIn(
            "rather than private or unrestricted chain-of-thought",
            DEBUG_SYSTEM_PROMPT,
        )

    def test_schema_uses_current_target_ids_and_reason_codes(self):
        schema = debug_response_schema(social_state(), ["HUMAN_DETECTED"])

        self.assertEqual(
            schema["properties"]["target_human_id"]["anyOf"][0]["enum"],
            ["visitor-1"],
        )
        self.assertIn(
            "HUMAN_DETECTED",
            schema["properties"]["reason_codes"]["items"]["enum"],
        )

    def test_valid_response_preserves_selection_for_existing_intent_path(self):
        state = social_state()
        response = validate_debug_response(
            json.dumps(valid_debug_payload(state)),
            state,
            ["HUMAN_DETECTED"],
        )

        self.assertEqual(response.recommended_action, "MONITOR")
        self.assertEqual(len(response.action_scores), len(Action))
        self.assertEqual(
            response.behavior_selection_payload(),
            {
                "action": "MONITOR",
                "target_human_id": None,
                "preferences": {
                    "target_speed_mps": None,
                    "preferred_social_distance_m": None,
                    "passing_side": None,
                    "orientation_target_rad": None,
                    "hold_duration_s": None,
                },
                "valid_for_ms": 1_000,
                "reason_codes": ["INSUFFICIENT_EVIDENCE"],
                "decision_confidence": 0.45,
            },
        )

    def test_no_person_response_can_be_valid_and_grounded(self):
        state = social_state(humans=False)
        response = validate_debug_response(
            json.dumps(valid_debug_payload(state)), state
        )

        self.assertEqual(response.recommended_action, "MONITOR")
        self.assertEqual(response.robot_inputs[0].source, "/robot/task")

    def test_missing_or_duplicate_action_scores_are_rejected(self):
        state = social_state()
        missing = valid_debug_payload(state)
        missing["action_scores"] = missing["action_scores"][:-1]
        with self.assertRaisesRegex(DebugPolicyError, "debug schema"):
            validate_debug_response(json.dumps(missing), state)

        duplicate = valid_debug_payload(state)
        duplicate["action_scores"][-1]["action"] = "CONTINUE"
        with self.assertRaisesRegex(DebugPolicyError, "every action exactly once"):
            validate_debug_response(json.dumps(duplicate), state)

        unknown = valid_debug_payload(state)
        unknown["recommended_action"] = "FLY"
        with self.assertRaisesRegex(DebugPolicyError, "debug schema"):
            validate_debug_response(json.dumps(unknown), state)

    def test_scores_must_be_normalized_and_recommendation_must_rank_first(self):
        state = social_state()
        unnormalized = valid_debug_payload(state)
        unnormalized["action_scores"][0]["score"] = 0.2
        with self.assertRaisesRegex(DebugPolicyError, "sum approximately"):
            validate_debug_response(json.dumps(unnormalized), state)

        wrong_winner = valid_debug_payload(state)
        wrong_winner["recommended_action"] = "CONTINUE"
        with self.assertRaisesRegex(DebugPolicyError, "highest model-reported score"):
            validate_debug_response(json.dumps(wrong_winner), state)

    def test_sources_must_exist_and_report_the_exact_state_value(self):
        state = social_state()
        unknown = valid_debug_payload(state)
        unknown["evidence"][0]["source_fields"] = ["/humans/0/smile"]
        with self.assertRaisesRegex(DebugPolicyError, "unknown evidence source"):
            validate_debug_response(json.dumps(unknown), state)

        mismatch = valid_debug_payload(state)
        mismatch["robot_inputs"][0]["latest_value"] = 999.0
        with self.assertRaisesRegex(DebugPolicyError, "does not match"):
            validate_debug_response(json.dumps(mismatch), state)

    def test_target_reason_and_action_contract_are_validated_at_runtime(self):
        state = social_state()
        unknown_target = valid_debug_payload(state)
        unknown_target["target_human_id"] = "invented-person"
        with self.assertRaisesRegex(DebugPolicyError, "not currently observed"):
            validate_debug_response(json.dumps(unknown_target), state)

        unsupported_reason = valid_debug_payload(state)
        unsupported_reason["reason_codes"] = ["MADE_UP_REASON"]
        with self.assertRaisesRegex(DebugPolicyError, "unsupported reason"):
            validate_debug_response(json.dumps(unsupported_reason), state)

        forbidden_target = valid_debug_payload(state)
        forbidden_target["target_human_id"] = "visitor-1"
        with self.assertRaisesRegex(DebugPolicyError, "MONITOR forbids"):
            validate_debug_response(json.dumps(forbidden_target), state)

    def test_required_target_and_preferences_follow_existing_action_contract(self):
        state = social_state()
        approach = valid_debug_payload(state)
        approach["recommended_action"] = "APPROACH"
        approach["target_human_id"] = "visitor-1"
        for item in approach["action_scores"]:
            if item["action"] == "MONITOR":
                item["score"] = 0.05
            elif item["action"] == "APPROACH":
                item["score"] = 0.45

        with self.assertRaisesRegex(
            DebugPolicyError, "requires preferences.preferred_social_distance_m"
        ):
            validate_debug_response(json.dumps(approach), state)

        approach["preferences"] = {"preferred_social_distance_m": 1.2}
        response = validate_debug_response(json.dumps(approach), state)
        self.assertEqual(response.recommended_action, "APPROACH")


if __name__ == "__main__":
    unittest.main()
