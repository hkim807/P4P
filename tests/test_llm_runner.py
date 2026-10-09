"""Single SocialState CLI validation and diagnostics, with no model/network calls."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.domain.model_decision import ModelDecision
from app.llm import main, read_social_state
from app.ollama import OllamaError, OllamaErrorCategory, OllamaResult
from app.pipeline import TrackingPipeline
from app.policy.llm import PROMPT_VERSION, SOCIAL_STATE_PREFIX
from app.state.estimator import SocialStateEstimator
from app.state.social_models import TemporalConfig
from tests.fixtures import frame


def saved_state():
    """An estimator snapshot with custom config, retained people and missing data."""
    tracking = TrackingPipeline("single-snapshot-session")
    estimator = SocialStateEstimator(TemporalConfig(window_s=3.0, min_span_s=1.2,
                                                   too_close_m=0.65))
    first = frame()
    first["people"] = [{"uid": 17, "distance_m": 1.85, "gaze_overlap": None},
                       {"uid": 31, "distance_m": None, "gaze_overlap": 0.93}]
    first["robot"] = {"linear_velocity": None, "angular_velocity": -0.1}
    estimator.update(tracking.process(first))
    second = frame()
    second["timestamp"] += 100_000
    second["people"] = [{"uid": 31, "distance_m": None, "gaze_overlap": 0.93}]
    second["robot"] = first["robot"]
    return estimator.update(tracking.process(second))


class SingleSnapshotRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.json"
        self.state = saved_state()
        self.payload = self.state.model_dump(mode="json")
        self.original_text = json.dumps(self.payload, indent=2, ensure_ascii=False)
        self.path.write_text(self.original_text, encoding="utf-8")

    def arguments(self, *settings, path=None):
        return [str(self.path if path is None else path),
                "--base-url", "http://127.0.0.1:11434", "--model", "caller-model",
                *settings]

    def run_main(self, *, result=None, settings=(), path=None):
        output = io.StringIO()
        with patch("app.llm.OllamaClient", autospec=True) as constructor:
            if result is not None:
                constructor.return_value.chat.return_value = result
            with redirect_stdout(output):
                status = main(self.arguments(*settings, path=path))
        return status, json.loads(output.getvalue()), constructor

    def assert_input_failure(self, status, payload, constructor, category="invalid_input",
                             requested_model="caller-model"):
        self.assertEqual(status, 2)
        constructor.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["decision"])
        self.assertEqual(payload["error"]["category"], category)
        self.assertTrue(payload["error"]["message"])
        self.assertIsNone(payload["error"]["http_status"])
        self.assertEqual(payload["requested_model"], requested_model)
        self.assertEqual(payload["prompt_version"], PROMPT_VERSION)
        for field in ("source_state_id", "session_id", "source_robot_timestamp_us",
                      "returned_model", "raw_content", "request_duration_s"):
            self.assertIsNone(payload[field], field)

    def test_reader_accepts_one_complete_object_with_every_value_preserved(self):
        state = read_social_state(self.path)
        self.assertEqual(state.model_dump(mode="json"), self.payload)
        self.assertEqual(len(state.people), 2)
        self.assertEqual(state.config.window_s, 3.0)
        self.assertEqual(state.people[0].visibility, "TEMPORARILY_MISSING")
        self.assertIsNone(state.people[1].latest_distance_m)

    def test_reader_and_cli_reject_malformed_nonobject_jsonl_and_duplicate_keys(self):
        nested_duplicate = self.original_text.replace('"window_s": 3.0',
                                                       '"window_s": 3.0, "window_s": 2.0')
        cases = {
            "malformed": '{"state_id":',
            "empty": "",
            "array": json.dumps([self.payload]),
            "null": "null",
            "string": '"one snapshot"',
            "number": "12",
            "jsonl": self.original_text + "\n" + self.original_text,
            "duplicate_top_level": '{"state_id": "one", "state_id": "two"}',
            "duplicate_nested": nested_duplicate,
        }
        for label, text in cases.items():
            with self.subTest(label=label):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_social_state(self.path)
                self.assert_input_failure(*self.run_main())

    def test_reader_and_cli_reject_nonstandard_constants(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                text = self.original_text.replace('"linear_velocity": null',
                                                   '"linear_velocity": ' + constant)
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_social_state(self.path)
                self.assert_input_failure(*self.run_main())

    def test_schema_errors_are_detected_before_client_construction(self):
        for field, value in (("robot_timestamp_us", "1100000"), ("schema_version", 2),
                             ("people", {}), ("active_target_uid", 17),
                             ("range_data_status", "CLEAR"), ("extra_sensor", {})):
            with self.subTest(field=field):
                payload = dict(self.payload)
                payload[field] = value
                self.path.write_text(json.dumps(payload), encoding="utf-8")
                self.assert_input_failure(*self.run_main())
        payload = dict(self.payload)
        del payload["state_id"]
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assert_input_failure(*self.run_main())

    def test_unreadable_and_non_utf8_input_never_constructs_client(self):
        missing = Path(self.directory.name) / "missing.json"
        self.assert_input_failure(*self.run_main(path=missing))
        self.assert_input_failure(*self.run_main(path=Path(self.directory.name)))
        with patch("app.llm.Path.read_text", side_effect=PermissionError("input denied")):
            status, payload, constructor = self.run_main()
        self.assert_input_failure(status, payload, constructor)
        self.assertIn("input denied", payload["error"]["message"])
        self.path.write_bytes(b"\xff\xfe")
        self.assert_input_failure(*self.run_main())

    def test_invalid_configuration_never_constructs_client(self):
        settings = [
            ("--timeout", "0"), ("--timeout", "-1"), ("--timeout", "nan"),
            ("--timeout", "inf"), ("--base-url", "ftp://localhost"),
            ("--base-url", "http://localhost/api/chat"),
            ("--base-url", "http://localhost:badport"),
            ("--model", ""), ("--model", " caller-model"),
            ("--temperature", "-0.1"), ("--temperature", "nan"),
            ("--temperature", "inf"), ("--num-predict", "0"),
            ("--num-predict", "-1"),
        ]
        for setting in settings:
            with self.subTest(settings=setting):
                status, payload, constructor = self.run_main(settings=setting)
                self.assert_input_failure(status, payload, constructor,
                                          category="invalid_configuration",
                                          requested_model=(setting[1] if setting[0] == "--model"
                                                           else "caller-model"))

    def test_input_is_validated_before_invalid_configuration(self):
        self.path.write_text("not JSON", encoding="utf-8")
        self.assert_input_failure(*self.run_main(settings=("--timeout", "0")))

    def test_invalid_generation_argument_is_rejected_without_constructing_client(self):
        for setting in (("--seed", "1.5"), ("--num-predict", "many")):
            with self.subTest(settings=setting):
                with patch("app.llm.OllamaClient", autospec=True) as constructor:
                    with patch("sys.stderr", new_callable=io.StringIO):
                        with self.assertRaises(SystemExit) as raised:
                            main(self.arguments(*setting))
                    self.assertEqual(raised.exception.code, 2)
                    constructor.assert_not_called()

    def test_success_passes_caller_config_and_whole_snapshot_once_without_writing(self):
        decision = ModelDecision(action="ENGAGE", reason="Caller-provided fake response.")
        raw_content = '{ "action": "ENGAGE", "reason": "Caller-provided fake response." }'
        result = OllamaResult("caller-model", "actual-model:tag", raw_content, 0.375,
                              decision, None)
        status, payload, constructor = self.run_main(result=result, settings=(
            "--base-url", "https://inference.example:11434/", "--timeout", "4.5",
            "--temperature", "0.2", "--seed", "123", "--num-predict", "80"))
        self.assertEqual(status, 0)
        constructor.assert_called_once()
        config = constructor.call_args.args[0]
        self.assertEqual(config.model_dump(), {
            "base_url": "https://inference.example:11434/", "model": "caller-model",
            "timeout_seconds": 4.5, "temperature": 0.2, "seed": 123, "num_predict": 80, "num_ctx": None,
        })
        client = constructor.return_value
        client.chat.assert_called_once()
        messages = client.chat.call_args.args[0]
        self.assertEqual([message.role for message in messages], ["system", "user"])
        self.assertTrue(messages[1].content.startswith(SOCIAL_STATE_PREFIX))
        self.assertEqual(json.loads(messages[1].content[len(SOCIAL_STATE_PREFIX):]), self.payload)
        self.assertTrue(all(message.images is None for message in messages))
        self.assertEqual(payload, {
            "source_state_id": self.state.state_id, "session_id": self.state.session_id,
            "source_robot_timestamp_us": self.state.robot_timestamp_us,
            "prompt_version": PROMPT_VERSION, "ok": True,
            "decision": {"action": "ENGAGE", "reason": "Caller-provided fake response."},
            "error": None, "requested_model": "caller-model",
            "returned_model": "actual-model:tag", "raw_content": raw_content,
            "request_duration_s": 0.375,
        })
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original_text)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])

    def test_all_inference_failures_preserve_diagnostics_and_return_one(self):
        for category in OllamaErrorCategory:
            with self.subTest(category=category):
                error = OllamaError(category, "Original error: 모델 unavailable.\nMore detail.", 503)
                result = OllamaResult("caller-model", "actual-model:tag", "unparseable output",
                                      1.75, None, error)
                status, payload, constructor = self.run_main(result=result)
                self.assertEqual(status, 1)
                constructor.assert_called_once()
                constructor.return_value.chat.assert_called_once()
                self.assertEqual(payload, {
                    "source_state_id": self.state.state_id, "session_id": self.state.session_id,
                    "source_robot_timestamp_us": self.state.robot_timestamp_us,
                    "prompt_version": PROMPT_VERSION, "ok": False, "decision": None,
                    "error": {"category": category.value, "message": error.message,
                              "http_status": 503},
                    "requested_model": "caller-model", "returned_model": "actual-model:tag",
                    "raw_content": "unparseable output", "request_duration_s": 1.75,
                })


if __name__ == "__main__":
    unittest.main()
