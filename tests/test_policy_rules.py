"""Decision precedence and replay/live parity for the first rule policy."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from robot.navel_client.single_trial import SingleTrial
from robot.navel_client.decision_dispatch import parse_decision
from app.policy.target_lock import EffectiveDecision

from app.decide import main as decide_main
from app.domain.model_decision import FinalDecision
from app.policy.rules import decide, normalise_rule_decision, rule_readiness
from app.server import create_app
from app.social_pipeline import SocialPipeline
from app.policy.rules import PolicyDecision
from tests.test_social_state import sample


def ready_state(distance=2.0):
    pipeline = SocialPipeline("policy-test")
    for i in range(21):
        state = pipeline.process(sample(i, distance=distance))["social_state"]
    assert state["people"][0]["gaze_state"] == "SUSTAINED"
    assert state["people"][0]["human_radial_motion"] == "STATIONARY"
    return state


class PolicyRulesTests(unittest.TestCase):
    def test_rule_normalisation_and_four_actions(self):
        for action, reason_code in (
            ("CONTINUE", "NO_VISIBLE_PERSON"),
            ("APPROACH", "SUSTAINED_GAZE_IN_APPROACHABLE_RANGE"),
            ("ENGAGE", "SUSTAINED_GAZE_IN_INTERACTION_RANGE"),
            ("YIELD", "ROUTE_CONFLICT"),
        ):
            with self.subTest(action=action):
                proposal = PolicyDecision(decision_id="test", source_state_id="test:1",
                    session_id="test", decision=action, reason_code=reason_code)
                final = normalise_rule_decision(proposal)
                self.assertIsInstance(final, FinalDecision)
                self.assertEqual(final.action, action)
                self.assertNotEqual(final.reason, reason_code)
                self.assertEqual(set(final.model_dump()), {"action", "reason"})
        for model in (PolicyDecision, EffectiveDecision):
            with self.assertRaises(ValueError):
                model(decision_id="test", source_state_id="test:1", session_id="test",
                      decision="DEFER", reason_code="WAITING")
        with self.assertRaises(ValueError):
            normalise_rule_decision({"decision_id": "test", "source_state_id": "test:1",
                "session_id": "test", "decision": "DEFER", "reason_code": "WAITING"})

    def test_sustained_gaze_and_known_motion_select_one_target(self):
        for distance, expected in ((1.0, "ENGAGE"), (2.0, "APPROACH")):
            with self.subTest(distance=distance):
                state = ready_state(distance)
                decision = decide(state)
                self.assertEqual(decision.decision, expected)
                self.assertEqual((decision.target_uid, decision.target_track_epoch), (17, 1))
                self.assertEqual(decision.source_state_id, state["state_id"])

    def test_moving_gaze_and_proximity_pass_trial_acceptance(self):
        for distance, action in ((1.0, "ENGAGE"), (2.0, "APPROACH"), (.4, "YIELD"), (2.0, "CONTINUE")):
            with self.subTest(distance=distance):
                pipeline = SocialPipeline("moving-test")
                for i in range(21):
                    observation = sample(i, distance=distance + (20-i)*.02, velocity=.1,
                                         gaze=.1 if action == "CONTINUE" else .95)
                    response = pipeline.process(observation)
                person = response["social_state"]["people"][0]
                self.assertEqual(person["human_radial_motion"], "UNKNOWN")
                self.assertEqual(person["relative_distance_trend"], "DECREASING")
                self.assertFalse(person["evidence"]["stationary_window_confirmed"])
                self.assertEqual(response["policy_decision"]["decision"], action)
                self.assertEqual(response["final_decision"]["action"], action)
                payload = {**response, "accepted": True, "processing_status": "complete",
                           "timestamp": observation["timestamp"]}
                parsed = parse_decision(payload, observation, observation["timestamp"], 1_000_000)
                if action == "CONTINUE":
                    self.assertIsNone(parsed)
                    self.assertEqual(response["target_lock"]["execution_status"], "HOLD")
                else:
                    self.assertEqual(parsed.decision, action)
                trial = SingleTrial(monotonic_us=lambda: observation["timestamp"])
                self.assertTrue(trial.accept_rule_response(payload, observation))
                self.assertEqual((trial.phase, trial.decision["action"]), ("DECIDED", action))
                if action == "YIELD":
                    self.assertIn("proximity", response["final_decision"]["reason"])
                    self.assertIn("does not imply a blocked path", response["final_decision"]["reason"])

    def test_precedence_and_clear_non_engagement(self):
        state = ready_state()
        self.assertEqual(rule_readiness(state, stale=True), "STATE_STALE")
        self.assertEqual(rule_readiness(state, processing_failed=True), "PROCESSING_FAILED")
        for flags in ({"stale": True}, {"processing_failed": True}):
            with self.assertRaises(ValueError):
                decide(state, **flags)
        two = deepcopy(state)
        other = deepcopy(two["people"][0])
        other["uid"] = 18
        two["people"].append(other)
        self.assertEqual(rule_readiness(two), "MULTIPLE_VISIBLE_PEOPLE")
        empty = deepcopy(state)
        empty["people"] = []
        self.assertEqual(rule_readiness(empty), "NO_VISIBLE_PERSON")

        person = state["people"][0]
        person["distance_zone"] = "TOO_CLOSE"
        self.assertEqual(decide(state).decision, "YIELD")
        person["distance_zone"] = "UNKNOWN"
        self.assertEqual(rule_readiness(state), "DISTANCE_UNKNOWN")
        person["distance_zone"] = "APPROACHABLE"
        person["gaze_state"] = "NONE"
        self.assertEqual(decide(state).reason_code, "NO_ATTENTION")
        person["gaze_state"] = "SUSTAINED"
        person["human_radial_motion"] = "AWAY"
        self.assertEqual(decide(state).reason_code, "PERSON_MOVING_AWAY")
        person["human_radial_motion"] = "STATIONARY"
        person["gaze_state"] = "INTERMITTENT"
        self.assertEqual(decide(state).decision, "CONTINUE")
        person["gaze_state"] = "SUSTAINED"
        person["distance_zone"] = "FAR"
        self.assertEqual(decide(state).reason_code, "PERSON_FAR")

    def test_not_ready_observations_do_not_invoke_policy(self):
        pipeline = SocialPipeline("waiting")
        with patch("app.social_pipeline.decide", side_effect=AssertionError("policy called while waiting")):
            for i in range(10):
                observation = sample(i, velocity=.1)
                response = pipeline.process(observation)
                self.assertEqual(response["policy_readiness"]["status"], "OBSERVING")
                self.assertIsNone(response["policy_decision"])
                self.assertIsNone(response["final_decision"])
                self.assertIsNone(response["target_lock"]["effective_decision"])
                self.assertIsNone(response["robot_command"])
            empty = sample(10)
            empty["people"] = []
            self.assertEqual(pipeline.process(empty)["policy_readiness"]["reason_code"], "NO_VISIBLE_PERSON")
            multiple = sample(11)
            multiple["people"].append({"uid": 18, "distance_m": 2.0, "gaze_overlap": .95})
            self.assertEqual(pipeline.process(multiple)["policy_readiness"]["reason_code"], "MULTIPLE_VISIBLE_PEOPLE")
        state = ready_state()
        person = state["people"][0]
        for field in ("gaze_valid", "latest_distance_valid"):
            person["evidence"][field] = False
            with self.assertRaises(ValueError):
                decide(state)
            person["evidence"][field] = True
        person["gaze_state"] = "UNKNOWN"
        self.assertEqual(rule_readiness(state), "GAZE_CATEGORY_NOT_READY")
        with self.assertRaises(ValueError):
            decide(state)
        person["gaze_state"] = "SUSTAINED"
        person["evidence"]["stationary_window_confirmed"] = False
        person["evidence"]["distance_trend_valid"] = False
        person["human_radial_motion"] = "UNKNOWN"
        self.assertEqual(decide(state).decision, "APPROACH")

    def test_live_response_matches_decision_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, social = root/"raw.jsonl", root/"social.jsonl"
            client = create_app(raw, social_output=social, session_id="policy-test").test_client()
            responses = []
            finals = []
            for i in range(21):
                response = client.post("/api/v1/observations", json=sample(i))
                self.assertEqual(response.status_code, 200)
                responses.append(response.json["policy_decision"])
                finals.append(response.json["final_decision"])
                self.assertNotIn("command", response.json)
            output = root/"decisions.jsonl"
            summary = io.StringIO()
            with contextlib.redirect_stderr(summary):
                self.assertEqual(decide_main([str(social), "--output", str(output)]), 0)
            counters = json.loads(summary.getvalue())
            self.assertGreater(counters["observing"], 0)
            self.assertNotIn("OBSERVING", counters["decisions"])
            replayed = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(responses, [row["policy_decision"] for row in replayed])
            self.assertEqual(finals, [row["final_decision"] for row in replayed])
            self.assertIsNone(finals[0])
            self.assertIsNone(replayed[0]["policy_decision"])
            self.assertEqual(replayed[0]["policy_readiness"]["status"], "OBSERVING")
            self.assertEqual(FinalDecision.model_validate(finals[-1]).action, "APPROACH")
            self.assertEqual(responses[-1]["target_uid"], 17)
            self.assertEqual(replayed[-1]["policy_decision"]["decision"], "APPROACH")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(decide_main([str(social), "--output", str(output)]), 1)

    def test_decision_schema_and_invalid_trace(self):
        schema = Path(__file__).resolve().parents[1]/"schemas/v1/policy-decision.schema.json"
        self.assertEqual(json.loads(schema.read_text()), PolicyDecision.model_json_schema())
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/"invalid.jsonl", Path(directory)/"decisions.jsonl"
            source.write_text("{}\n")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(decide_main([str(source), "--output", str(output)]), 1)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
