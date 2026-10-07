"""Focused offline replay CLI tests using recorded clocks and fake model results."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.domain.model_decision import ModelDecision
from app.llm_replay import main
from app.llm_replay_inputs import ReplayState
from app.ollama import OllamaError, OllamaErrorCategory, OllamaResult
from app.pipeline import TrackingPipeline
from app.policy.llm import PROMPT_VERSION, SOCIAL_STATE_PREFIX
from app.state.estimator import SocialStateEstimator
from tests.fixtures import frame


class FakeClient:
    def __init__(self, result=None, *, on_chat=None):
        self.result = result or OllamaResult(
            "caller-model", "returned-model", '{"action":"CONTINUE","reason":"Recorded evidence."}',
            0.125, ModelDecision(action="CONTINUE", reason="Recorded evidence."), None)
        self.on_chat = on_chat
        self.messages = []

    def chat(self, messages):
        self.messages.append(messages)
        if self.on_chat is not None:
            self.on_chat(messages)
        return self.result


class ReplayRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.input = self.root / "scenario-input.jsonl"
        self.output = self.root / "results.jsonl"
        self.frames = []
        for index in range(31):
            payload = frame()
            payload["timestamp"] += index * 100_000
            payload["people"][0]["gaze_overlap"] = 0.95
            payload["robot"] = {"linear_velocity": 0.0, "angular_velocity": 0.0}
            self.frames.append(payload)
        self.write_jsonl(self.input, self.frames)

    def write_jsonl(self, path, payloads):
        path.write_text("".join(json.dumps(payload) + "\n" for payload in payloads),
                        encoding="utf-8")

    def arguments(self, *settings, inputs=None, output=None, format="raw"):
        return [*(str(path) for path in (inputs or [self.input])),
                "--format", format, "--output", str(output or self.output),
                "--base-url", "http://127.0.0.1:11434", "--model", "caller-model",
                *settings]

    def run_main(self, *settings, client=None, inputs=None, output=None, format="raw"):
        stdout, stderr = io.StringIO(), io.StringIO()
        client = client or FakeClient()
        with patch("app.llm_replay.OllamaClient", return_value=client) as constructor:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(self.arguments(*settings, inputs=inputs, output=output,
                                             format=format))
        summary = json.loads(stdout.getvalue())
        target = output or self.output
        rows = []
        if target.is_file():
            rows = [json.loads(line) for line in target.read_text().splitlines() if line.strip()]
        return status, summary, rows, constructor, client, stderr.getvalue()

    def test_prepare_only_writes_complete_snapshots_and_never_constructs_client(self):
        original = self.input.read_bytes()
        status, summary, rows, constructor, _, _ = self.run_main("--prepare-only")
        self.assertEqual(status, 0)
        self.assertEqual(summary["status"], "prepared")
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["observations_processed"], 31)
        self.assertEqual(summary["selected_moments"], 4)
        self.assertEqual(summary["rows_written"], 4)
        self.assertEqual(summary["prepared"], 4)
        self.assertEqual(summary["successes"], 0)
        self.assertEqual(summary["failures"], 0)
        constructor.assert_not_called()
        self.assertEqual([row["source_robot_timestamp_us"] for row in rows],
                         [1_000_000, 2_000_000, 3_000_000, 4_000_000])
        for row in rows:
            self.assertEqual(row["schema_version"], 1)
            self.assertEqual(row["status"], "prepared")
            self.assertIsNone(row["ok"])
            self.assertEqual(row["prompt_version"], PROMPT_VERSION)
            self.assertEqual(json.loads(row["social_state_json"]), row["social_state"])
            self.assertEqual(row["source_state_id"], row["social_state"]["state_id"])
            self.assertEqual(row["session_id"], row["social_state"]["session_id"])
            self.assertEqual(row["source_robot_timestamp_us"],
                             row["social_state"]["robot_timestamp_us"])
            for field in ("decision", "error", "returned_model", "raw_content",
                          "request_duration_s"):
                self.assertIsNone(row[field], field)
            self.assertTrue(row["completed_at"].endswith(("+00:00", "Z")))
            self.assertEqual(row["ollama_configuration"]["model"], "caller-model")
        self.assertEqual(self.input.read_bytes(), original)

    def test_every_observation_builds_history_before_source_time_sampling(self):
        status, summary, rows, _, _, _ = self.run_main(
            "--prepare-only", "--sample-interval", "2")
        self.assertEqual(status, 0)
        self.assertEqual(summary["observations_processed"], 31)
        self.assertEqual(len(rows), 2)
        tracking = TrackingPipeline("independent-expected-state")
        estimator = SocialStateEstimator()
        expected = []
        for payload in self.frames:
            state = estimator.update(tracking.process(payload))
            if state.robot_timestamp_us in (1_000_000, 3_000_000):
                expected.append(state.model_dump(mode="json"))
        for row, expected_state in zip(rows, expected):
            self.assertEqual(row["social_state"]["people"], expected_state["people"])
            self.assertEqual(row["social_state"]["cue_changes"], expected_state["cue_changes"])
            self.assertEqual(row["social_state"]["track_events"], expected_state["track_events"])
        person = rows[1]["social_state"]["people"][0]
        self.assertEqual(person["gaze_state"], "SUSTAINED")
        self.assertGreater(person["evidence"]["gaze_valid_samples"], 5)

    def test_sampling_is_deterministic_and_ignores_model_latency(self):
        prepared = self.root / "prepared.jsonl"
        status, _, rows, _, _, _ = self.run_main(
            "--prepare-only", "--sample-interval", "0.7", output=prepared)
        self.assertEqual(status, 0)
        fake = FakeClient(result=OllamaResult(
            "caller-model", "returned-model", '{"action":"STOP","reason":"Fake."}',
            900.0, ModelDecision(action="STOP", reason="Fake."), None))
        with patch("time.monotonic", return_value=9_000_000.0):
            status, summary, inferred, _, _, _ = self.run_main(
                "--sample-interval", "0.7", client=fake)
        self.assertEqual(status, 0)
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(len(fake.messages), len(rows))
        self.assertEqual([row["source_robot_timestamp_us"] for row in inferred],
                         [row["source_robot_timestamp_us"] for row in rows])
        self.assertEqual([row["social_state"] for row in inferred],
                         [row["social_state"] for row in rows])
        self.assertTrue(all(row["request_duration_s"] == 900.0 for row in inferred))

    def test_warmup_processes_prior_history_and_selects_using_recorded_time(self):
        status, summary, rows, _, _, _ = self.run_main(
            "--prepare-only", "--warmup", "1.3", "--sample-interval", "1")
        self.assertEqual(status, 0)
        self.assertEqual(summary["observations_processed"], 31)
        self.assertEqual([row["source_robot_timestamp_us"] for row in rows],
                         [2_300_000, 3_300_000])
        self.assertEqual(rows[0]["social_state"]["ingest_sequence"], 14)
        self.assertEqual(rows[0]["social_state"]["people"][0]["gaze_state"], "SUSTAINED")

    def test_max_calls_caps_selection_and_still_validates_all_observations(self):
        status, summary, rows, constructor, client, _ = self.run_main(
            "--sample-interval", "0", "--max-calls", "2")
        self.assertEqual(status, 0)
        constructor.assert_called_once()
        self.assertEqual(summary["observations_processed"], 31)
        self.assertEqual(summary["selected_moments"], 2)
        self.assertEqual(len(client.messages), 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["sampling"]["selected_index"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["sampling"]["max_calls"], 2)

    def test_sampling_and_tracker_history_reset_for_each_input(self):
        second = self.root / "second-scenario.jsonl"
        self.write_jsonl(second, self.frames[:4])
        status, summary, rows, _, _, _ = self.run_main(
            "--prepare-only", "--sample-interval", "10", inputs=[self.input, second])
        self.assertEqual(status, 0)
        self.assertEqual(summary["observations_processed"], 35)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["source_robot_timestamp_us"] for row in rows],
                         [1_000_000, 1_000_000])
        self.assertNotEqual(rows[0]["session_id"], rows[1]["session_id"])
        for row in rows:
            self.assertEqual(row["social_state"]["ingest_sequence"], 1)
            self.assertEqual(row["social_state"]["people"][0]["gaze_state"], "UNKNOWN")
            self.assertNotIn("scenario", row["session_id"])

    def test_saved_states_are_used_exactly_and_session_sampling_resets(self):
        states = []
        for session, start in (("original-a", 8_000_000), ("original-b", 100_000)):
            tracking = TrackingPipeline(session)
            estimator = SocialStateEstimator()
            for index in range(3):
                payload = frame()
                payload["timestamp"] = start + index * 100_000
                states.append(estimator.update(tracking.process(payload)).model_dump(mode="json"))
        social = self.root / "social.jsonl"
        self.write_jsonl(social, states)
        status, summary, rows, _, _, _ = self.run_main(
            "--prepare-only", "--sample-interval", "10", inputs=[social], format="social")
        self.assertEqual(status, 0)
        self.assertEqual(summary["observations_processed"], 6)
        self.assertEqual([row["social_state"] for row in rows], [states[0], states[3]])
        self.assertEqual([row["session_id"] for row in rows], ["original-a", "original-b"])

    def test_inference_rows_correlate_exact_prompt_and_keep_metadata_outside_messages(self):
        status, summary, rows, _, client, _ = self.run_main(
            "--max-calls", "2", "--temperature", "0.2", "--seed", "7",
            "--num-predict", "80", "--timeout", "4.5")
        self.assertEqual(status, 0)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["successes"], 2)
        for row, messages in zip(rows, client.messages):
            self.assertEqual(row["status"], "succeeded")
            self.assertTrue(row["ok"])
            self.assertEqual([message.role for message in messages], ["system", "user"])
            self.assertEqual(messages[1].content,
                             SOCIAL_STATE_PREFIX + row["social_state_json"])
            self.assertEqual(json.loads(row["social_state_json"]), row["social_state"])
            self.assertTrue(all(message.images is None for message in messages))
            self.assertNotIn(self.input.name, messages[1].content)
            self.assertNotIn(str(self.input), messages[1].content)
            self.assertNotIn("processing", json.loads(row["social_state_json"]))
            self.assertEqual(set(row["decision"]), {"action", "reason"})
            self.assertEqual(row["requested_model"], "caller-model")
            self.assertEqual(row["returned_model"], "returned-model")
            self.assertEqual(row["ollama_configuration"]["generation_options"],
                             {"temperature": 0.2, "seed": 7, "num_predict": 80})
            self.assertEqual(row["ollama_configuration"]["timeout_seconds"], 4.5)

    def test_inference_failures_are_rows_without_fallback_and_exit_one(self):
        result = OllamaResult("caller-model", "response-model", "invalid output", 1.25,
                              None, OllamaError(OllamaErrorCategory.INVALID_DECISION,
                                                "Wrong action: YIELD.", 200))
        status, summary, rows, _, client, _ = self.run_main(
            "--max-calls", "2", client=FakeClient(result))
        self.assertEqual(status, 1)
        self.assertEqual(summary["status"], "inference_failed")
        # All inputs were processed; the distinct status and exit code report
        # unsuccessful inference rather than incomplete source processing.
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["successes"], 0)
        self.assertEqual(summary["failures"], 2)
        self.assertEqual(len(client.messages), 2)
        for row in rows:
            self.assertEqual(row["status"], "failed")
            self.assertFalse(row["ok"])
            self.assertIsNone(row["decision"])
            self.assertEqual(row["raw_content"], "invalid output")
            self.assertEqual(row["error"], {"category": "invalid_decision",
                                            "message": "Wrong action: YIELD.",
                                            "http_status": 200})
            self.assertEqual(row["request_duration_s"], 1.25)

    def test_output_uses_returned_frozen_prompt_when_input_mutates_during_chat(self):
        tracking = TrackingPipeline("opaque-session")
        state = SocialStateEstimator().update(tracking.process(self.frames[0]))
        expected = state.model_dump(mode="json")
        item = ReplayState(state, {"processing_session_id": "opaque-session",
                                   "input_path": str(self.input)}, {"mode": "test"})

        def mutate(messages):
            state.state_id = "changed-during-request"
            state.session_id = "changed-session"
            state.robot_timestamp_us = 999_999_999
            state.people.clear()

        fake = FakeClient(on_chat=mutate)
        with patch("app.llm_replay.iter_replay_states", return_value=iter([item])):
            status, _, rows, _, _, _ = self.run_main(client=fake)
        self.assertEqual(status, 0)
        self.assertEqual(rows[0]["social_state"], expected)
        self.assertEqual(rows[0]["source_state_id"], expected["state_id"])
        self.assertEqual(rows[0]["session_id"], expected["session_id"])
        self.assertEqual(rows[0]["source_robot_timestamp_us"], expected["robot_timestamp_us"])
        self.assertEqual(fake.messages[0][1].content,
                         SOCIAL_STATE_PREFIX + rows[0]["social_state_json"])
        self.assertNotIn(self.input.name, fake.messages[0][1].content)
        self.assertNotEqual(state.state_id, rows[0]["source_state_id"])

    def test_output_cannot_resolve_to_any_input_including_symlinks(self):
        original = self.input.read_bytes()
        aliases = [self.input, self.root / "alias.jsonl"]
        aliases[1].symlink_to(self.input)
        for output in aliases:
            with self.subTest(output=output):
                status, summary, _, constructor, _, _ = self.run_main(
                    "--prepare-only", output=output)
                self.assertEqual(status, 2)
                self.assertEqual(summary["status"], "failed")
                self.assertFalse(summary["complete"])
                constructor.assert_not_called()
                self.assertTrue(summary["error"])
                self.assertEqual(self.input.read_bytes(), original)

    def test_existing_output_is_refused_and_preserved(self):
        self.output.write_text('{"previous":"result"}\n')
        original = self.output.read_bytes()
        status, summary, _, constructor, _, _ = self.run_main("--prepare-only")
        self.assertEqual(status, 2)
        self.assertFalse(summary["complete"])
        self.assertTrue(summary["error"])
        constructor.assert_not_called()
        self.assertEqual(self.output.read_bytes(), original)

    def test_later_malformed_record_reports_partial_output_and_source_location(self):
        with self.input.open("a", encoding="utf-8") as stream:
            stream.write('{"timestamp":\n')
        status, summary, rows, constructor, _, _ = self.run_main(
            "--prepare-only", "--max-calls", "1")
        self.assertEqual(status, 2)
        self.assertEqual(summary["status"], "partial")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["observations_processed"], 31)
        self.assertEqual(summary["selected_moments"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "prepared")
        self.assertIn(str(self.input) + ":32", str(summary["error"]))
        constructor.assert_not_called()

    def test_invalid_replay_or_model_configuration_prevents_writing_and_calls(self):
        cases = (("--sample-interval", "-1"), ("--sample-interval", "nan"),
                 ("--sample-interval", "inf"), ("--warmup", "-1"),
                 ("--warmup", "nan"), ("--max-calls", "0"),
                 ("--max-locomotion-age", "-1"), ("--timeout", "0"),
                 ("--temperature", "nan"), ("--base-url", "ftp://localhost"))
        for settings in cases:
            with self.subTest(settings=settings):
                status, summary, _, constructor, _, _ = self.run_main(
                    "--prepare-only", *settings)
                self.assertEqual(status, 2)
                self.assertFalse(summary["complete"])
                self.assertTrue(summary["error"])
                self.assertFalse(self.output.exists())
                constructor.assert_not_called()

    def test_write_error_is_reported_without_success_summary(self):
        original_open = Path.open

        class BrokenWriter:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def write(self, text):
                raise OSError("output device full")

            def flush(self):
                pass

        def open_path(path, *args, **kwargs):
            if path == self.output:
                return BrokenWriter()
            return original_open(path, *args, **kwargs)

        with patch("app.llm_replay.Path.open", new=open_path):
            status, summary, _, constructor, _, _ = self.run_main("--prepare-only")
        self.assertEqual(status, 2)
        self.assertFalse(summary["complete"])
        self.assertIn("output device full", str(summary["error"]))
        self.assertEqual(summary["selected_moments"], 1)
        self.assertEqual(summary["prepared"], 1)
        self.assertEqual(summary["rows_written"], 0)
        constructor.assert_not_called()

    def test_later_write_failure_preserves_first_row_and_reports_partial(self):
        original_open = Path.open

        class OneRowWriter:
            def __init__(self, stream):
                self.stream = stream
                self.writes = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()
                return False

            def write(self, text):
                self.writes += 1
                if self.writes > 1:
                    raise OSError("failed after first row")
                return self.stream.write(text)

            def flush(self):
                self.stream.flush()

        def open_path(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path == self.output and args and args[0] == "x":
                return OneRowWriter(stream)
            return stream

        with patch("app.llm_replay.Path.open", new=open_path):
            status, summary, rows, _, client, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertEqual(summary["status"], "partial")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["selected_moments"], 2)
        self.assertEqual(summary["successes"], 2)
        self.assertEqual(summary["rows_written"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(client.messages), 2)
        self.assertEqual(summary["error"]["stage"], "output")
        self.assertIn("failed after first row", summary["error"]["message"])


if __name__ == "__main__":
    unittest.main()
