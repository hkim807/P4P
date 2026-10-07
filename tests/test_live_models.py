"""Output-only live workers with fake models and event-synchronised concurrency."""
import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
from threading import Event, Thread, current_thread
import unittest
from unittest.mock import patch

from PIL import Image
from pydantic import ValidationError

from app.domain.model_decision import ModelDecision
from app.live_camera import LiveCameraCache, LiveFrame
from app.live_models import LiveModelConfig, LiveModelRunner
from app.ollama import OllamaConfig, OllamaError, OllamaErrorCategory, OllamaResult
from app.policy.llm import SOCIAL_STATE_PREFIX, SYSTEM_PROMPT as LLM_SYSTEM
from app.policy.vlm import SYSTEM_PROMPT as VLM_SYSTEM, USER_PROMPT as VLM_USER
from tests.test_live_camera import camera_record, model_source
from tests.test_llm_policy import social_state


def state(sequence=1, timestamp=None, session="receiver-session"):
    value = social_state()
    value.state_id = f"{session}:{sequence}"
    value.session_id = session
    value.ingest_sequence = sequence
    value.robot_timestamp_us = sequence * 1_000_000 if timestamp is None else timestamp
    return value


def source(value, **capture_changes):
    return {"observation_timestamp_us": value.robot_timestamp_us,
            "model_source": model_source(receipt=value.robot_timestamp_us - 100, **capture_changes),
            "receiver_received_monotonic_us": 999_999_999_999,
            "receiver_received_unix_us": 100,
            "receiver_receipt_clock": "receiver-host-monotonic-us"}


def success(action="CONTINUE"):
    decision = ModelDecision(action=action, reason="Fake visible-evidence explanation.")
    return OllamaResult("requested", "actual-returned", decision.model_dump_json(), 0.25, decision, None)


class FakeClient:
    def __init__(self, result=None, *, block=False, error=None):
        self.result = success() if result is None else result
        self.error = error
        self.calls = []
        self.threads = []
        self.entered, self.release = Event(), Event()
        if not block:
            self.release.set()

    def chat(self, messages):
        self.calls.append(messages)
        self.threads.append(current_thread())
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test client was not released")
        if self.error is not None:
            raise self.error
        return self.result


class LiveModelConfigTests(unittest.TestCase):
    def test_default_is_disabled_frozen_and_finite(self):
        config = LiveModelConfig()
        self.assertEqual(config.mode, "disabled")
        self.assertEqual(config.queue_capacity, 4)
        self.assertEqual(config.sample_interval_s, 1.0)
        self.assertFalse(config.allow_receipt_match)
        with self.assertRaises(ValidationError):
            config.mode = "llm"

    def test_selected_policies_require_independent_valid_configs(self):
        for mode in ("llm", "vlm", "both"):
            with self.subTest(mode=mode), self.assertRaises(ValidationError):
                LiveModelConfig(mode=mode)
        llm = OllamaConfig(base_url="http://localhost:11434", model="text-model")
        vlm = OllamaConfig(base_url="http://localhost:11434", model="vision-model")
        self.assertEqual(LiveModelConfig(mode="both", llm_config=llm, vlm_config=vlm).vlm_config, vlm)

    def test_invalid_settings_rejected(self):
        invalid = {"mode": "automatic", "queue_capacity": 0, "camera_cache_capacity": True,
                   "camera_cache_max_bytes": -1, "sample_interval_s": float("inf"),
                   "max_camera_age_s": 0.0000001, "allow_receipt_match": "yes", "extra": 1}
        for name, value in invalid.items():
            with self.subTest(name=name), self.assertRaises(ValidationError):
                LiveModelConfig(**{name: value})


class LiveModelRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.created = 0
        self.text_config = OllamaConfig(base_url="http://localhost:11434", model="text-model", timeout_seconds=1.0,
                                       temperature=0.1, seed=42, num_predict=128)
        self.vision_config = OllamaConfig(base_url="http://localhost:11434", model="vision-model", timeout_seconds=1.0)

    def runner(self, *, mode="llm", text=None, vision=None, cache=None, factory=None, **settings):
        self.created += 1
        self.output = self.root / f"models-{self.created}.jsonl"
        text, vision = text or FakeClient(), vision or FakeClient()
        self.factory_calls = []

        def clients(config):
            self.factory_calls.append(config)
            return text if config.model == "text-model" else vision

        config = LiveModelConfig(mode=mode, llm_config=self.text_config if mode in ("llm", "both") else None,
                                 vlm_config=self.vision_config if mode in ("vlm", "both") else None, **settings)
        runner = LiveModelRunner(config, self.output, camera_cache=cache,
                                 client_factory=clients if factory is None else factory)
        self.addCleanup(runner.close, 2.0)
        self.addCleanup(text.release.set)
        self.addCleanup(vision.release.set)
        return runner, text, vision

    def rows(self, output=None):
        return [json.loads(line) for line in (output or self.output).read_text().splitlines()]

    def cache(self, value, **kwargs):
        cache = LiveCameraCache()
        record, rgb = camera_record(receipt=value.robot_timestamp_us - 100_000, **kwargs)
        cache.ingest(record, rgb)
        return cache, rgb

    def test_disabled_mode_creates_no_output_thread_or_client(self):
        output = self.root / "disabled.jsonl"
        with patch("app.live_models.OllamaClient", side_effect=AssertionError("disabled")):
            runner = LiveModelRunner(LiveModelConfig(), output)
            self.assertEqual(runner.submit(None, None), {"status": "disabled", "selected": False})
            runner.start()
            self.assertFalse(runner.status()["running"])
            self.assertTrue(runner.close())
        self.assertFalse(output.exists())

    def test_llm_receives_full_state_and_original_diagnostics_in_background(self):
        runner, text, _ = self.runner()
        original = state()
        expected = original.model_dump(mode="json")
        metadata = source(original)
        self.assertEqual(runner.submit(original, metadata)["status"], "enqueued")
        self.assertTrue(runner.wait_idle(2))
        row = self.rows()[0]
        self.assertEqual(row["social_state"], expected)
        self.assertEqual(row["source"], metadata)
        self.assertEqual(row["source_robot_timestamp_us"], original.robot_timestamp_us)
        self.assertEqual(row["ingest_sequence"], original.ingest_sequence)
        self.assertEqual(text.calls[0][0].content, LLM_SYSTEM)
        self.assertEqual(text.calls[0][1].content, SOCIAL_STATE_PREFIX + row["social_state_json"])
        self.assertTrue(all(message.images is None for message in text.calls[0]))
        self.assertIsNot(text.threads[0], current_thread())
        inferred = row["llm_inference"]
        self.assertEqual(inferred["raw_content"], text.result.raw_content)
        self.assertEqual(inferred["returned_model"], "actual-returned")
        self.assertEqual(inferred["request_duration_s"], 0.25)
        self.assertEqual(inferred["ollama_configuration"]["generation_options"],
                         {"temperature": 0.1, "seed": 42, "num_predict": 128})
        self.assertEqual(row["vlm_inference"]["status"], "not_run_disabled")
        self.assertNotEqual(row["completed_at"], metadata["receiver_received_unix_us"])
        for forbidden in ("policy_decision", "final_decision", "target_lock", "robot_command"):
            self.assertNotIn(forbidden, row)

    def test_both_freeze_state_source_and_selected_image_before_slow_llm(self):
        original = state()
        expected = original.model_dump(mode="json")
        metadata = source(original)
        source_before = deepcopy(metadata)
        cache, rgb = self.cache(original)
        text = FakeClient(block=True)
        runner, _, vision = self.runner(mode="both", text=text, cache=cache)
        runner.submit(original, metadata)
        self.assertTrue(text.entered.wait(2))
        original.state_id = "mutated"
        original.people.clear()
        metadata["model_source"]["capture"]["packet"]["time"] = "mutated"
        metadata["receiver_received_monotonic_us"] = 1
        changed, changed_rgb = camera_record(session="different-capture", rgb=b"\x00" * 6)
        cache.ingest(changed, changed_rgb)
        text.release.set()
        self.assertTrue(runner.wait_idle(2))
        row = self.rows()[0]
        self.assertEqual(row["social_state"], expected)
        self.assertEqual(row["source"], source_before)
        for policy in ("llm", "vlm"):
            self.assertEqual(row[policy + "_inference"]["source_state_id"], expected["state_id"])
        messages = vision.calls[0]
        self.assertEqual(messages[0].content, VLM_SYSTEM)
        self.assertEqual(messages[1].content, VLM_USER)
        self.assertEqual(len(messages[1].images), 1)
        with Image.open(BytesIO(base64.b64decode(messages[1].images[0], validate=True))) as image:
            self.assertEqual(image.size, (2, 1))
            self.assertEqual(image.tobytes(), rgb)
        inferred = row["vlm_inference"]
        self.assertEqual(inferred["image_matching"]["frame"]["session_id"], "capture-a")
        self.assertEqual(inferred["image_matching"]["method"], "exact_recorded_sdk_timestamp")
        self.assertEqual(inferred["image_encoding"]["encoding_format"], "PNG")
        self.assertNotIn(messages[1].images[0], self.output.read_text())
        self.assertNotIn("rgb_b64", self.output.read_text())
        self.assertNotIn(expected["state_id"], messages[0].content + messages[1].content)

    def test_missing_stale_cross_session_and_clock_inputs_never_call_vlm(self):
        variations = ("missing", "stale", "cross_session", "incompatible_clock", "missing_model_source")
        for variation in variations:
            with self.subTest(variation=variation):
                original = state()
                cache, _ = self.cache(original)
                metadata = source(original)
                if variation == "missing":
                    cache = LiveCameraCache()
                elif variation == "stale":
                    original = state(3)
                    metadata = source(original)
                elif variation == "cross_session":
                    metadata["model_source"]["capture"]["session_id"] = "other-session"
                elif variation == "incompatible_clock":
                    metadata["model_source"]["clock"] = "receiver-host-monotonic-us"
                else:
                    metadata["model_source"] = None
                runner, text, vision = self.runner(mode="both", cache=cache)
                runner.submit(original, metadata)
                self.assertTrue(runner.wait_idle(2))
                row = self.rows()[0]
                self.assertEqual(row["llm_inference"]["status"], "succeeded")
                self.assertEqual(row["vlm_inference"]["status"], "input_unavailable")
                self.assertIsNone(row["vlm_inference"]["decision"])
                self.assertEqual(len(text.calls), 1)
                self.assertEqual(vision.calls, [])

    def test_image_encoding_error_does_not_call_model_or_replace_llm_result(self):
        original = state()
        cache, _ = self.cache(original)
        runner, _, vision = self.runner(mode="both", cache=cache)
        with patch.object(LiveFrame, "encode", side_effect=OSError("PNG encoding failed")):
            runner.submit(original, source(original))
            self.assertTrue(runner.wait_idle(2))
        row = self.rows()[0]
        self.assertEqual(row["llm_inference"]["status"], "succeeded")
        self.assertEqual(row["vlm_inference"]["status"], "input_invalid")
        self.assertEqual(row["vlm_inference"]["error_stage"], "image_input")
        self.assertIsNone(row["vlm_inference"]["request_duration_s"])
        self.assertEqual(vision.calls, [])

    def test_bounded_queue_drop_is_audited_while_network_waits_and_consumes_sample(self):
        text = FakeClient(block=True)
        runner, _, _ = self.runner(text=text, queue_capacity=1)
        first = state(1)
        runner.submit(first, source(first))
        self.assertTrue(text.entered.wait(2))
        second, dropped = state(2), state(3)
        self.assertEqual(runner.submit(second, source(second))["status"], "enqueued")
        self.assertEqual(runner.submit(dropped, source(dropped))["status"], "queue_dropped")
        too_soon = state(4, timestamp=3_500_000)
        self.assertEqual(runner.submit(too_soon, source(too_soon))["status"], "sampled_out")
        self.assertFalse(text.release.is_set())
        self.assertEqual(runner.status()["pending"], 1)
        drop_row = self.rows()[0]
        self.assertEqual(drop_row["source_state_id"], dropped.state_id)
        self.assertEqual(drop_row["llm_inference"]["status"], "queue_dropped")
        self.assertIsNone(drop_row["llm_inference"]["decision"])
        text.release.set()
        self.assertTrue(runner.wait_idle(2))
        self.assertEqual(len(text.calls), 2)
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(runner.status()["queue_dropped"], 1)

    def test_source_time_sampling_resets_at_session_boundary(self):
        runner, text, _ = self.runner(sample_interval_s=2.0)
        values = [state(1), state(2), state(3), state(1, session="new-session")]
        statuses = [runner.submit(value, source(value))["status"] for value in values]
        self.assertEqual(statuses, ["enqueued", "sampled_out", "enqueued", "enqueued"])
        self.assertTrue(runner.wait_idle(2))
        self.assertEqual(len(text.calls), 3)

    def test_all_ollama_errors_preserve_raw_duration_models_and_no_fallback(self):
        for category in OllamaErrorCategory:
            with self.subTest(category=category):
                error = OllamaError(category, "Original diagnostic.", 503)
                result = OllamaResult("original-requested", "returned-failure", "original raw", 1.5, None, error)
                runner, text, _ = self.runner(text=FakeClient(result))
                value = state()
                with patch("app.policy.rules.decide", side_effect=AssertionError("no rule fallback")):
                    runner.submit(value, source(value))
                    self.assertTrue(runner.wait_idle(2))
                inferred = self.rows()[0]["llm_inference"]
                self.assertEqual(inferred["status"], "failed")
                self.assertEqual(inferred["error"], {"category": category.value,
                                                      "message": "Original diagnostic.", "http_status": 503})
                self.assertEqual(inferred["raw_content"], "original raw")
                self.assertEqual(inferred["request_duration_s"], 1.5)
                self.assertEqual(inferred["requested_model"], "original-requested")
                self.assertEqual(inferred["returned_model"], "returned-failure")
                self.assertIsNone(inferred["decision"])
                self.assertEqual(len(text.calls), 1)

    def test_client_creation_failure_isolated_and_other_policy_still_runs(self):
        value = state()
        cache, _ = self.cache(value)
        vision = FakeClient()

        def factory(config):
            if config.model == "text-model":
                raise RuntimeError("model creation failed")
            return vision

        runner, _, _ = self.runner(mode="both", cache=cache, factory=factory)
        self.assertEqual(runner.submit(value, source(value))["status"], "enqueued")
        self.assertTrue(runner.wait_idle(2))
        row = self.rows()[0]
        self.assertEqual(row["llm_inference"]["status"], "failed")
        self.assertEqual(row["llm_inference"]["error"]["message"], "model creation failed")
        self.assertEqual(row["vlm_inference"]["status"], "succeeded")

    def test_default_client_factory_respects_patch_and_is_reused(self):
        text = FakeClient()
        config = LiveModelConfig(mode="llm", llm_config=self.text_config, sample_interval_s=0.0)
        with patch("app.live_models.OllamaClient", return_value=text) as factory:
            runner = LiveModelRunner(config, self.root / "default-factory.jsonl")
            self.addCleanup(runner.close, 2.0)
            for sequence in (1, 2):
                value = state(sequence)
                runner.submit(value, source(value))
            self.assertTrue(runner.wait_idle(2))
        self.assertEqual(len(text.calls), 2)
        factory.assert_called_once_with(self.text_config)

    def test_invalid_mutated_state_and_rule_metadata_do_not_escape_ingestion(self):
        runner, text, _ = self.runner()
        value = state()
        metadata = source(value)
        value.robot_timestamp_us = "invalid"
        self.assertEqual(runner.submit(value, metadata)["status"], "input_invalid")
        value = state()
        for field in ("final_decision", "robot_command"):
            with self.subTest(field=field):
                metadata = source(value)
                metadata[field] = {"action": "YIELD", "reason": "Rule output."}
                self.assertEqual(runner.submit(value, metadata)["status"], "input_invalid")
        self.assertEqual(text.calls, [])

    def test_exclusive_output_and_duplicate_start_and_fork_guards(self):
        config = LiveModelConfig(mode="llm", llm_config=self.text_config)
        output = self.root / "existing.jsonl"
        output.write_text("original\n")
        with self.assertRaises(FileExistsError):
            LiveModelRunner(config, output)
        self.assertEqual(output.read_text(), "original\n")
        runner, _, _ = self.runner()
        thread = runner._thread
        runner.start()
        self.assertIs(runner._thread, thread)
        with patch("app.live_models.os.getpid", return_value=runner._pid + 1):
            with self.assertRaisesRegex(ValueError, "different process"):
                runner.start()
            self.assertEqual(runner.submit(None, None)["status"], "not_run_process_mismatch")
            self.assertFalse(runner.close())
        self.assertTrue(runner.close())
        with self.assertRaisesRegex(ValueError, "cannot restart"):
            runner.start()

    def test_wait_and_close_timeout_explicit_then_flush_on_release(self):
        text = FakeClient(block=True)
        runner, _, _ = self.runner(text=text)
        value = state()
        runner.submit(value, source(value))
        self.assertTrue(text.entered.wait(2))
        self.assertFalse(runner.wait_idle(0))
        self.assertFalse(runner.close(timeout=0))
        self.assertTrue(runner.status()["closing"])
        text.release.set()
        self.assertTrue(runner.close(timeout=2))
        self.assertEqual(self.rows()[0]["llm_inference"]["status"], "succeeded")

    def test_shutdown_audits_pending_jobs_before_worker_closes_output(self):
        text = FakeClient(block=True)
        runner, _, _ = self.runner(text=text, queue_capacity=2)
        first, second, third = state(1), state(2), state(3)
        runner.submit(first, source(first))
        self.assertTrue(text.entered.wait(2))
        runner.submit(second, source(second))
        runner.submit(third, source(third))
        audit_started, audit_release, worker_waiting = Event(), Event(), Event()
        self.addCleanup(audit_release.set)
        original_write, original_wait = runner._write_row, runner._condition.wait

        def write(row):
            if row["source_state_id"] == second.state_id:
                audit_started.set()
                if not audit_release.wait(5):
                    raise RuntimeError("shutdown test audit was not released")
            return original_write(row)

        def wait(timeout=None):
            if runner._closing and runner._active is None:
                worker_waiting.set()
            return original_wait(timeout)

        results = []
        with patch.object(runner, "_write_row", side_effect=write), patch.object(runner._condition, "wait", side_effect=wait):
            closer = Thread(target=lambda: results.append(runner.close(timeout=2)))
            closer.start()
            self.assertTrue(audit_started.wait(2))
            text.release.set()
            self.assertTrue(worker_waiting.wait(2))
            self.assertFalse(runner._output.closed)
            audit_release.set()
            closer.join(2)
            self.assertFalse(closer.is_alive())
        self.assertEqual(results, [True])
        rows = {row["source_state_id"]: row for row in self.rows()}
        self.assertEqual(set(rows), {first.state_id, second.state_id, third.state_id})
        self.assertEqual(rows[first.state_id]["llm_inference"]["status"], "succeeded")
        for value in (second, third):
            self.assertEqual(rows[value.state_id]["llm_inference"]["status"], "not_run_shutdown")
        self.assertEqual(len(text.calls), 1)

    def test_audit_failure_does_not_raise_from_submit_or_make_more_requests(self):
        runner, text, _ = self.runner()
        value = state()
        with patch.object(runner._output, "write", side_effect=OSError("audit disk failed")), \
                self.assertLogs("app.live_models", level="ERROR"):
            runner.submit(value, source(value))
            self.assertTrue(runner.wait_idle(2))
        self.assertEqual(runner.status()["write_error"], "audit disk failed")
        second = state(2)
        self.assertEqual(runner.submit(second, source(second))["status"], "not_run_audit_failure")
        self.assertEqual(len(text.calls), 1)
        self.assertEqual(self.rows()[0]["llm_inference"]["status"], "not_run_audit_failure")


if __name__ == "__main__":
    unittest.main()
