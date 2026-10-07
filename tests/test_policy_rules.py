"""Decision precedence and replay/live parity for the first rule policy."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from app.decide import main as decide_main
from app.domain.model_decision import FinalDecision
from app.policy.rules import decide, normalise_rule_decision
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
    def test_rule_normalisation_and_defer(self):
        for action, reason_code in (
            ("CONTINUE", "NO_VISIBLE_PERSON"),
            ("APPROACH", "SUSTAINED_GAZE_IN_APPROACHABLE_RANGE"),
            ("ENGAGE", "SUSTAINED_GAZE_IN_INTERACTION_RANGE"),
            ("YIELD", "ROUTE_CONFLICT"),
            ("DEFER", "HUMAN_MOTION_UNKNOWN"),
        ):
            with self.subTest(action=action):
                proposal = PolicyDecision(decision_id="test", source_state_id="test:1",
                    session_id="test", decision=action, reason_code=reason_code)
                final = normalise_rule_decision(proposal)
                if action == "DEFER":
                    self.assertIsNone(final)
                    self.assertEqual(proposal.reason_code, "HUMAN_MOTION_UNKNOWN")
                else:
                    self.assertIsInstance(final, FinalDecision)
                    self.assertEqual(final.action, action)
                    self.assertNotEqual(final.reason, reason_code)
                    self.assertEqual(set(final.model_dump()), {"action", "reason"})

    def test_sustained_gaze_and_known_motion_select_one_target(self):
        for distance, expected in ((1.0, "ENGAGE"), (2.0, "APPROACH")):
            with self.subTest(distance=distance):
                state = ready_state(distance)
                decision = decide(state)
                self.assertEqual(decision.decision, expected)
                self.assertEqual((decision.target_uid, decision.target_track_epoch), (17, 1))
                self.assertEqual(decision.source_state_id, state["state_id"])

    def test_unknown_motion_blocks_approach_and_engage(self):
        for distance in (1.0, 2.0):
            state = ready_state(distance)
            state["people"][0]["human_radial_motion"] = "UNKNOWN"
            decision = decide(state)
            self.assertEqual((decision.decision, decision.reason_code),
                             ("DEFER", "HUMAN_MOTION_UNKNOWN"))
            self.assertIsNone(decision.target_uid)

    def test_precedence_and_clear_non_engagement(self):
        state = ready_state()
        self.assertEqual(decide(state, stale=True).reason_code, "STATE_STALE")
        self.assertEqual(decide(state, processing_failed=True).reason_code, "PROCESSING_FAILED")
        two = deepcopy(state)
        other = deepcopy(two["people"][0])
        other["uid"] = 18
        two["people"].append(other)
        self.assertEqual(decide(two).reason_code, "MULTIPLE_VISIBLE_PEOPLE")
        empty = deepcopy(state)
        empty["people"] = []
        self.assertEqual(decide(empty).decision, "CONTINUE")
        self.assertIsNone(decide(empty).target_uid)

        person = state["people"][0]
        person["distance_zone"] = "TOO_CLOSE"
        self.assertEqual(decide(state).reason_code, "PERSON_TOO_CLOSE")
        person["distance_zone"] = "UNKNOWN"
        self.assertEqual(decide(state).reason_code, "DISTANCE_UNKNOWN")
        person["distance_zone"] = "APPROACHABLE"
        person["gaze_state"] = "NONE"
        self.assertEqual(decide(state).reason_code, "NO_ATTENTION")
        person["gaze_state"] = "SUSTAINED"
        person["human_radial_motion"] = "AWAY"
        self.assertEqual(decide(state).reason_code, "PERSON_MOVING_AWAY")
        person["human_radial_motion"] = "STATIONARY"
        person["gaze_state"] = "INTERMITTENT"
        self.assertEqual(decide(state).reason_code, "GAZE_INSUFFICIENT_OR_INTERMITTENT")
        person["gaze_state"] = "SUSTAINED"
        person["distance_zone"] = "FAR"
        self.assertEqual(decide(state).reason_code, "PERSON_FAR")

    def test_invalid_cue_evidence_cannot_propose_action(self):
        state = ready_state()
        state["people"][0]["evidence"]["gaze_valid"] = False
        self.assertEqual(decide(state).decision, "DEFER")
        state = ready_state()
        state["people"][0]["evidence"]["stationary_window_confirmed"] = False
        self.assertEqual(decide(state).reason_code, "HUMAN_MOTION_UNKNOWN")

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
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(decide_main([str(social), "--output", str(output)]), 0)
            replayed = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(responses, [{k: v for k, v in row.items() if k != "final_decision"}
                                         for row in replayed])
            self.assertEqual(finals, [row["final_decision"] for row in replayed])
            self.assertIsNone(finals[0])
            self.assertEqual(replayed[0]["decision"], "DEFER")
            self.assertEqual(replayed[0]["reason_code"], "GAZE_INSUFFICIENT_OR_INTERMITTENT")
            self.assertEqual(FinalDecision.model_validate(finals[-1]).action, "APPROACH")
            self.assertEqual(responses[-1]["target_uid"], 17)
            self.assertEqual(replayed[-1]["decision"], "APPROACH")
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
