"""Display wire responses directly, and reject unsafe startup configuration."""

import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from robot.external_sensor_client.config import parse_args
from robot.external_sensor_client.display import display_response
from robot.navel_client.transport import ObservationResponse


def response_payload():
    return {"accepted": True, "observation_id": "obs", "social_state_id": "state", "decision_triggered": False,
            "forced_decision": False, "triggers": [], "behavior_intent": None}


class DisplayTests(unittest.TestCase):
    def display(self, payload, status=200, raw=False):
        output = io.StringIO()
        # Trap both orchestration and parsing if display ever gains that coupling.
        with patch("robot.navel_client.behavior.controller.BehaviorController", side_effect=AssertionError("execution forbidden")), patch("robot.navel_client.behavior.intent.NavelBehaviorIntent.from_payload", side_effect=AssertionError("parsing forbidden")):
            display_response(ObservationResponse(status, payload), print_raw_json=raw, file=output)
        return output.getvalue()

    def test_accepted_without_decision(self):
        output = self.display(response_payload())
        for expected in ("HTTP status=200", "accepted=true", 'observation_id="obs"', 'social_state_id="state"', "decision_triggered=false", "forced_decision=false", "triggers=[]", "accepted without a decision"):
            self.assertIn(expected, output)

    def test_intent_is_printed_without_parsing_or_execution(self):
        payload = response_payload()
        intent = {"decision_id": "decision", "action": "MONITOR", "target_human_id": None, "preferences": {},
                  "reason_codes": ["INSUFFICIENT_EVIDENCE"], "decision_confidence": 0.55, "valid_for_ms": 1000}
        payload.update(decision_triggered=True, forced_decision=True, behavior_intent=intent)
        output = self.display(payload)
        self.assertEqual(json.loads(output.split("behavior_intent=", 1)[1]), intent)
        self.assertNotIn("without a decision", output)

    def test_validation_error(self):
        output = self.display({"accepted": False, "error": {"code": "invalid_observation_frame", "message": "invalid", "details": []}}, 400)
        self.assertIn("HTTP status=400", output)
        self.assertIn("invalid_observation_frame", output)
        self.assertIn('"message": "invalid"', output)
        self.assertNotIn("accepted without", output)

    def test_llm_failure(self):
        payload = response_payload()
        payload.update(decision_triggered=True, error={"code": "llm_request_failed", "message": "The observation was accepted, but the LLM request failed."})
        output = self.display(payload, 502)
        self.assertIn("accepted=true", output)
        self.assertIn("HTTP status=502", output)
        self.assertIn("llm_request_failed", output)
        self.assertIn("No BehaviorIntent returned", output)
        self.assertNotIn("accepted without a decision", output)

    def test_raw_json_is_complete_and_readable(self):
        payload = response_payload()
        payload["additional_diagnostic"] = {"nested": 42}
        output = self.display(payload, raw=True)
        raw = output.split("response_json=", 1)[1]
        self.assertEqual(json.loads(raw), payload)
        self.assertIn('\n  "accepted"', raw)


class CLITests(unittest.TestCase):
    def test_normal_requires_measured_height_camera_test_does_not(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--stationary-rig"])
        args = parse_args(["--camera-test", "--display"])
        self.assertIsNone(args.camera_height_m)
        self.assertTrue(args.display)
        self.assertEqual(args.person_model, "yolo11n.pt")
        self.assertEqual(args.person_confidence, 0.25)

    def test_invalid_perception_options_and_valid_overrides(self):
        for flag, value in (("--camera-height-m", "0"), ("--camera-height-m", "-1"),
                            ("--camera-height-m", "nan"), ("--camera-height-m", "inf"),
                            ("--person-confidence", "0"), ("--person-confidence", "1.1"),
                            ("--person-confidence", "nan"), ("--person-model", " ")):
            with self.subTest(flag=flag, value=value), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(["--stationary-rig", "--camera-height-m", "1.2", flag, value])
        args = parse_args(["--stationary-rig", "--camera-height-m", "1.5", "--person-model", "/tmp/person.pt", "--person-confidence", "0.6", "--display"])
        self.assertEqual(args.person_model, "/tmp/person.pt")
        self.assertEqual(args.person_confidence, 0.6)
        self.assertTrue(args.display)

    def test_normal_requires_explicit_stationary_rig(self):
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(SystemExit) as error:
            parse_args([])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("requires --stationary-rig", output.getvalue())

    def test_camera_test_and_defaults(self):
        camera = parse_args(["--camera-test", "--server", "unused"])
        self.assertFalse(camera.stationary_rig)
        self.assertEqual(camera.camera_test_frames, 30)
        normal = parse_args(["--stationary-rig", "--camera-height-m", "1.2"])
        self.assertEqual(normal.server, "http://127.0.0.1:6060")
        self.assertEqual(normal.minimum_send_interval, 0.2)
        self.assertEqual(normal.request_timeout, 35.0)

    def test_options_are_forwarded(self):
        args = parse_args(["--stationary-rig", "--camera-height-m", "1.2", "--force-decision", "--print-raw-json", "--realsense-serial", "123", "--adapter-id", " rig "])
        self.assertEqual(args.adapter_id, "rig")
        self.assertEqual(args.realsense_serial, "123")
        self.assertTrue(args.force_decision)
        self.assertTrue(args.print_raw_json)

    def test_invalid_config_fails_before_capture(self):
        invalid = (("--request-timeout", "nan"), ("--request-timeout", "0"), ("--minimum-send-interval", "inf"),
                   ("--minimum-send-interval", "-1"), ("--camera-test-frames", "0"), ("--adapter-id", " "),
                   ("--adapter-id", "x" * 129), ("--server", "http://localhost:6060/api/v1/observations"),
                   ("--server", "file:///tmp/server"), ("--server", "http://localhost:bad"), ("--realsense-serial", " "))
        for options in invalid:
            with self.subTest(options=options), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(["--stationary-rig", "--camera-height-m", "1.2", *options])
