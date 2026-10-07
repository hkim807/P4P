"""Output-only receiver integration, including slow background inference."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
from threading import Event
import unittest
from unittest.mock import patch

from app.domain.model_decision import ModelDecision
from app.live_models import LiveModelConfig
from app.ollama import OllamaConfig, OllamaResult
from app.policy.llm import SOCIAL_STATE_PREFIX
from app.policy.vlm import SYSTEM_PROMPT as VLM_SYSTEM_PROMPT, USER_PROMPT as VLM_USER_PROMPT
from app.server import create_app, main
from tests.fixtures import frame
from robot.navel_client.single_trial import SingleTrial
from robot.navel_client.main import ModelObservation, _poll_model_trial, _send_observations
from robot.navel_client.transport import ObservationResponse, ObservationTransport


def source(timestamp=1_000_000, *, session="capture", clock="robot-host-monotonic-us", sdk_time=500):
    return {"version": 1, "clock": clock, "capture": {
        "capture_version": 1, "session_id": session, "stream": "perception", "sequence": 1,
        "received_monotonic_us": timestamp - 10, "received_unix_us": 2_000_000,
        "packet": {"time": sdk_time, "persons": []},
    }}


def camera(*, session="capture", receipt=999_900, sdk_time=500):
    return {"capture_version": 1, "session_id": session, "camera": "head", "sequence": 1,
            "event": "frame", "received_monotonic_us": receipt, "received_unix_us": 2_000_000,
            "timestamp_us": sdk_time, "width": 2, "height": 1,
            "rgb_b64": base64.b64encode(bytes([0, 20, 40, 60, 80, 100])).decode("ascii")}


class LiveReceiverTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.messages = []
        self.model_error = None
        self.entered, self.release = Event(), Event()
        self.release.set()
        owner = self

        class Client:
            def __init__(self, config):
                self.config = config

            def chat(self, messages):
                owner.messages.append((self.config.model, messages))
                owner.entered.set()
                if not owner.release.wait(10):
                    raise TimeoutError("test did not release client")
                if owner.model_error is not None:
                    raise owner.model_error
                decision = ModelDecision(action="ENGAGE", reason="Model output only")
                return OllamaResult(self.config.model, self.config.model,
                    decision.model_dump_json(), 0.1, decision, None)

        self.patch = patch("app.live_models.OllamaClient", Client)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.release.set)

    def app(self, mode="both", *, sdk_output=None, **settings):
        config = LiveModelConfig(mode=mode, sample_interval_s=0.0,
            llm_config=OllamaConfig(base_url="http://localhost:11434", model="test-llm") if mode in {"llm", "both"} else None,
            vlm_config=OllamaConfig(base_url="http://localhost:11434", model="test-vlm") if mode in {"vlm", "both"} else None,
            **settings)
        app = create_app(self.root / "raw.jsonl", session_id="receiver",
                         model_config=config, model_output=self.root / "models.jsonl", sdk_output=sdk_output)
        self.addCleanup(app.extensions["live_models"].close)
        return app

    def rows(self):
        return [json.loads(line) for line in (self.root / "models.jsonl").read_text().splitlines()]

    def ready_trial_observation(self, client, identity, policy):
        for index in range(22):
            observation = {**frame(), "timestamp": 1_000_000 + index * 100_000}
            if policy == "vlm":
                observation["people"][0]["gaze_overlap"] = None
            response = client.post("/api/v1/observations", json={"observation": observation,
                "model_source": source(observation["timestamp"]), "trial": identity}).json
            if response["model_trial"]["result"] is not None:
                return observation, response
        self.fail("Structured evidence never became ready")

    def test_selected_trial_round_trip_preserves_source_and_current_readiness(self):
        root = self.root
        for policy in ("llm", "vlm"):
            with self.subTest(policy=policy):
                self.root = root / policy
                self.root.mkdir()
                self.release.clear()
                self.entered.clear()
                self.messages.clear()
                app = self.app(sdk_output=self.root / "sdk.jsonl")
                client, runner = app.test_client(), app.extensions["live_models"]
                now = [1_000_000]
                trial = SingleTrial(policy, monotonic_us=lambda: now[0])
                opened = client.post("/api/v1/model-trials", json={"trial_id": trial.trial_id,
                    "policy": policy, "wait_s": 30, "max_age_s": 10})
                self.assertEqual(opened.status_code, 200)
                trial.session_id = opened.json["session_id"]
                identity = trial.model_identity()
                client.post("/api/v1/camera-frames", json=camera())
                observation, first = self.ready_trial_observation(client, identity, policy)
                now[0] = observation["timestamp"]
                self.assertFalse(trial.accept_rule_response(first, observation))
                result = first["model_trial"]["result"]
                self.assertTrue(trial.register_request(result))
                self.assertTrue(self.entered.wait(2))
                poll = lambda: client.post("/api/v1/model-trials/result", json=trial.pending_request)
                self.assertEqual(poll().json["result"]["status"], "pending")
                self.assertFalse(trial.accept_model_result(poll().json["result"]))
                # Two seconds of inference: source is old, current evidence is fresh.
                now[0] += 2_000_000
                client.post("/api/v1/camera-frames", json={**camera(receipt=now[0] - 100, sdk_time=501), "sequence": 2})
                for index in range(1, 21):
                    latest = {**observation, "timestamp": observation["timestamp"] + index * 100_000}
                    second = client.post("/api/v1/observations", json={"observation": latest,
                        "model_source": source(latest["timestamp"], sdk_time=501), "trial": identity}).json
                trial.observe(second, latest)
                self.assertEqual(second["model_trial"]["result"]["request_id"], result["request_id"])
                self.assertEqual(runner.status()["pending"], 0)
                self.release.set()
                self.assertTrue(runner.wait_idle(2))
                success = poll().json["result"]
                self.assertEqual(success["status"], "succeeded")
                self.assertEqual([name for name, _ in self.messages], ["test-" + policy])
                for key in ("trial_id", "session_id", "policy", "request_id", "source_state_id"):
                    self.assertFalse(trial.accept_model_result({**success, key: "wrong"}))
                self.assertEqual(client.post("/api/v1/model-trials/result",
                    json={**trial.pending_request, "request_id": "unknown"}).status_code, 409)
                expired = SingleTrial(policy, model_max_age_s=1, monotonic_us=lambda: now[0])
                expired.trial_id = trial.trial_id
                expired.observe(first, observation)
                self.assertTrue(expired.register_request(result))
                expired.observe(second, latest)
                self.assertTrue(expired.ready)
                self.assertFalse(expired.accept_model_result(success))
                self.assertIsNone(expired.decision)
                trial.note_observation({**latest, "people": []})
                self.assertFalse(trial.accept_model_result(success))
                trial.note_observation(latest)
                now[0] = observation["timestamp"] + 11_000_000
                self.assertFalse(trial.accept_decision(policy, success["decision"], success))
                now[0] = latest["timestamp"]
                # Exercise the actual robot sender and polling path with HTTP
                # bridged to Flask; inference still runs on the existing worker.
                transport = ObservationTransport("http://receiver")

                def post(path, body):
                    response = app.test_client().post(path, json=body)
                    return ObservationResponse(response.status_code, response.json)

                async def deliver():
                    queue = asyncio.Queue(maxsize=1)
                    now[0] += 1
                    queue.put_nowait(ModelObservation({**latest, "timestamp": now[0]},
                        source(now[0], sdk_time=501)["capture"]))
                    sender = asyncio.create_task(_send_observations(queue, transport,
                        minimum_send_interval_s=0, print_only=False, trial=trial, route_trial=True))
                    try:
                        await asyncio.wait_for(queue.join(), 2)
                        await asyncio.wait_for(_poll_model_trial(trial, transport), 2)
                    finally:
                        sender.cancel()
                        await asyncio.gather(sender, return_exceptions=True)

                with patch.object(transport, "_post", side_effect=post), contextlib.redirect_stdout(io.StringIO()):
                    asyncio.run(deliver())
                self.assertEqual(trial.phase, "DECIDED")
                self.assertEqual(trial.source["source_robot_timestamp_us"], observation["timestamp"])
                self.assertFalse(trial.accept_model_result(success))
                client.post("/api/v1/model-trials/close", json=identity)
                self.assertEqual(client.post("/api/v1/observations", json={"observation": latest,
                    "model_source": source(now[0]), "trial": identity}).status_code, 409)

    def test_failed_trial_request_retries_and_close_ignores_late_completion(self):
        self.model_error = TimeoutError("mock model unavailable")
        app = self.app("llm")
        client, runner = app.test_client(), app.extensions["live_models"]
        now = [1_000_000]
        trial = SingleTrial("llm", monotonic_us=lambda: now[0])
        opened = client.post("/api/v1/model-trials", json={"trial_id": trial.trial_id,
            "policy": "llm", "wait_s": 30, "max_age_s": 10}).json
        trial.session_id = opened["session_id"]
        identity = trial.model_identity()
        observation, response = self.ready_trial_observation(client, identity, "llm")
        now[0] = observation["timestamp"]
        trial.observe(response, observation)
        trial.register_request(response["model_trial"]["result"])
        self.assertTrue(runner.wait_idle(2))
        failed = client.post("/api/v1/model-trials/result", json=trial.pending_request).json["result"]
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["decision"])
        self.assertFalse(trial.accept_model_result(failed))
        self.assertEqual(trial.phase, "OBSERVING")
        self.model_error = None
        self.release.clear()
        self.entered.clear()
        now[0] += 1
        observation = {**frame(), "timestamp": now[0]}
        response = client.post("/api/v1/observations", json={"observation": observation,
            "model_source": None, "trial": {**identity, "retry_request_id": trial.retry_request_id}}).json
        trial.observe(response, observation)
        self.assertTrue(trial.register_request(response["model_trial"]["result"]))
        pending = dict(trial.pending_request)
        self.assertNotEqual(pending["request_id"], failed["request_id"])
        self.assertTrue(self.entered.wait(2))
        with patch("app.live_models.time.monotonic", return_value=time.monotonic() + 11):
            expired = client.post("/api/v1/model-trials/result", json=pending).json["result"]
        self.assertEqual(expired["status"], "failed")
        self.assertEqual(expired["error"]["category"], "request_expired")
        self.assertIsNone(expired["decision"])
        trial.fail("INTERRUPTED")
        self.assertEqual(client.post("/api/v1/model-trials/close", json=identity).status_code, 200)
        self.release.set()
        self.assertTrue(runner.wait_idle(2))
        self.assertEqual(client.post("/api/v1/model-trials/result", json=pending).status_code, 409)
        self.assertFalse(trial.accept_model_result({**pending, "status": "succeeded",
            "decision": {"action": "ENGAGE", "reason": "Late result."}}))
        self.assertIsNone(trial.decision)
        self.assertEqual(len(self.rows()), 2)

    def test_disabled_mode_preserves_exact_response_and_has_no_worker(self):
        app = create_app(self.root / "raw.jsonl")
        self.assertNotIn("live_models", app.extensions)
        response = app.test_client().post("/api/v1/observations", json=frame())
        self.assertEqual(response.json, {"accepted": True, "timestamp": 1_000_000, "people_count": 1})
        self.assertEqual(app.test_client().get("/health").json,
                         {"status": "ok", "service": "navel-raw-sensor-receiver"})
        self.assertEqual(app.test_client().post("/api/v1/camera-frames", json=camera()).status_code, 409)
        self.assertEqual(self.messages, [])

    def test_paired_llm_vlm_and_rule_command_path_are_independent(self):
        app = self.app()
        client = app.test_client()
        self.assertEqual(client.post("/api/v1/camera-frames", json=camera()).status_code, 200)
        response = client.post("/api/v1/observations", json={"observation": frame(), "model_source": source()})
        self.assertEqual(response.status_code, 200)
        runner = app.extensions["live_models"]
        self.assertTrue(runner.wait_idle(3))
        baseline = create_app(self.root / "baseline.jsonl", social_output=self.root / "baseline-social.jsonl",
                              session_id="receiver").test_client().post("/api/v1/observations", json=frame())
        for key in ("social_state", "policy_decision", "final_decision", "target_lock", "robot_command"):
            self.assertEqual(response.json[key], baseline.json[key])
        self.assertEqual(len(self.messages), 2)
        llm = self.messages[0][1]
        self.assertEqual(json.loads(llm[1].content.removeprefix(SOCIAL_STATE_PREFIX)), response.json["social_state"])
        vlm = self.messages[1][1]
        self.assertEqual(vlm[0].content, VLM_SYSTEM_PROMPT)
        self.assertEqual(vlm[1].content, VLM_USER_PROMPT)
        self.assertEqual(len(vlm[1].images), 1)
        self.assertTrue(base64.b64decode(vlm[1].images[0]).startswith(b"\x89PNG\r\n\x1a\n"))
        row = self.rows()[0]
        self.assertEqual(row["source_state_id"], response.json["social_state"]["state_id"])
        self.assertEqual(row["llm_inference"]["decision"]["action"], "ENGAGE")
        self.assertEqual(row["vlm_inference"]["decision"]["action"], "ENGAGE")
        self.assertNotIn("robot_command", row)
        self.assertNotIn("target_lock", row)
        self.assertEqual(json.loads((self.root / "raw.jsonl").read_text()), frame())

    def test_slow_models_do_not_hold_estimator_lock_or_block_http_ingestion(self):
        self.release.clear()
        app = self.app("llm", queue_capacity=1)
        first = app.test_client().post("/api/v1/observations", json=frame())
        self.assertEqual(first.status_code, 200)
        self.assertTrue(self.entered.wait(2))
        pipeline_lock = app.extensions["social_pipeline"]._lock
        self.assertTrue(pipeline_lock.acquire(blocking=False))
        pipeline_lock.release()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                def post(timestamp):
                    return app.test_client().post("/api/v1/observations", json={**frame(), "timestamp": timestamp})
                second = pool.submit(post, 1_000_001).result(timeout=2)
                third = pool.submit(post, 1_000_002).result(timeout=2)
                self.assertEqual(second.status_code, 200)
                self.assertEqual(third.status_code, 200)
                self.assertEqual(third.json["model_inference"]["status"], "queue_dropped")
                self.assertEqual(len((self.root / "raw.jsonl").read_text().splitlines()), 3)
        finally:
            self.release.set()
        self.assertTrue(app.extensions["live_models"].wait_idle(3))
        self.assertEqual(len(self.rows()), 3)

    def test_raw_without_provenance_saves_unavailable_vision_and_keeps_processing(self):
        app = self.app("vlm")
        response = app.test_client().post("/api/v1/observations", json=frame())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(app.extensions["live_models"].wait_idle(3))
        self.assertEqual(self.messages, [])
        self.assertIsNone(self.rows()[0]["vlm_inference"]["decision"])

    def test_bad_provenance_rejected_before_recording(self):
        app = self.app("llm")
        bad = source()
        bad["capture"]["received_monotonic_us"] = frame()["timestamp"] + 1
        response = app.test_client().post("/api/v1/observations", json={"observation": frame(), "model_source": bad})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "invalid_model_source")
        self.assertFalse((self.root / "raw.jsonl").exists())

    def test_camera_budget_rejection_precedes_optional_camera_recording(self):
        config = LiveModelConfig(mode="vlm", camera_cache_max_bytes=3,
            vlm_config=OllamaConfig(base_url="http://localhost:11434", model="test-vlm"))
        app = create_app(self.root / "raw.jsonl", camera_output_dir=self.root / "cameras",
                         model_config=config, model_output=self.root / "models.jsonl")
        self.addCleanup(app.extensions["live_models"].close)
        response = app.test_client().post("/api/v1/camera-frames", json=camera())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "invalid_camera_capture")
        self.assertEqual(app.extensions["live_camera"].frame_count, 0)
        self.assertFalse((self.root / "cameras" / "head").exists())

    def test_output_protection_precedes_creation(self):
        config = LiveModelConfig(mode="llm", llm_config=OllamaConfig(base_url="http://localhost:11434", model="test"))
        existing = self.root / "existing.jsonl"
        existing.write_text("saved\n")
        with self.assertRaises(FileExistsError):
            create_app(self.root / "new-raw.jsonl", model_config=config, model_output=existing)
        self.assertFalse((self.root / "new-raw.jsonl").exists())
        self.assertEqual(existing.read_text(), "saved\n")
        with self.assertRaises(ValueError):
            create_app(self.root / "raw.jsonl", camera_output_dir=self.root / "capture",
                       model_config=config, model_output=self.root / "capture" / "models.jsonl")
        self.assertFalse((self.root / "capture").exists())
        (self.root / "link.jsonl").symlink_to(self.root / "missing.jsonl")
        with self.assertRaises(FileExistsError):
            create_app(self.root / "raw.jsonl", model_config=config, model_output=self.root / "link.jsonl")

    def test_invalid_cli_configuration_never_serves(self):
        cases = [["--model-inference", "llm"],
                 ["--model-output", str(self.root / "result.jsonl")],
                 ["--model-inference", "llm", "--llm-model", "test", "--model-output", str(self.root / "result.jsonl"),
                  "--model-queue-capacity", "0"],
                 ["--model-sample-interval", "nan"], ["--vlm-timeout", "-1"],
                 ["--llm-base-url", "http://localhost:11434/api/chat"]]
        for args in cases:
            with self.subTest(args=args), patch("flask.Flask.run") as serve:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(["--output", str(self.root / "cli-raw.jsonl"), *args])
                serve.assert_not_called()
                self.assertFalse((self.root / "cli-raw.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
