"""Robot dry-run response routing, idempotency, and loss handling."""
import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from copy import deepcopy

from app.server import create_app
from robot.navel_client.decision_dispatch import (
    DecisionDispatcher, DryRunHandlers, DecisionRejected, parse_decision,
)
from robot.navel_client.main import _send_observations, parse_args
from robot.navel_client.transport import ObservationResponse, TransportError
from robot.navel_client.single_trial import SingleTrial
from tests.test_social_state import sample


def response_for(observation, sequence, decision="APPROACH", target=True):
    state_id = f"session-a:{sequence}"
    uid, epoch = (17, 1) if target else (None, None)
    return {
        "accepted": True, "processing_status": "complete",
        "timestamp": observation["timestamp"],
        "social_state": {
            "state_id": state_id, "session_id": "session-a",
            "robot_timestamp_us": observation["timestamp"],
            "people": [{"uid": 17, "track_epoch": 1, "visibility": "OBSERVED",
                        "gaze_state": "SUSTAINED", "evidence": {"latest_distance_valid": True, "gaze_valid": True}}],
        },
        "policy_decision": {
            "decision_id": f"{state_id}:social-rules-v2",
            "source_state_id": state_id, "session_id": "session-a",
            "policy_version": "social-rules-v2", "decision": decision,
            "reason_code": "TEST", "target_uid": uid, "target_track_epoch": epoch,
        },
    }


class RecordingHandlers(DryRunHandlers):
    def __init__(self):
        self.calls = []

    async def continue_route(self, decision):
        self.calls.append(("CONTINUE", decision.target_uid))

    async def approach_person(self, decision):
        self.calls.append(("APPROACH", decision.target_uid))

    async def engage_person(self, decision):
        self.calls.append(("ENGAGE", decision.target_uid))

    async def yield_route(self, decision):
        self.calls.append(("YIELD", decision.target_uid))

    async def cancel_active(self, decision, reason):
        self.calls.append(("CANCEL", reason))


class DecisionDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now_us = 1_000_000
        self.now_s = 0.0
        self.handlers = RecordingHandlers()
        self.dispatcher = DecisionDispatcher(
            self.handlers, max_age_s=1, timeout_s=0.1,
            monotonic=lambda: self.now_s, monotonic_us=lambda: self.now_us,
        )

    async def test_calls_once_per_decision_and_target_transition(self):
        first = sample(10)
        self.now_us = first["timestamp"] + 1000
        self.assertTrue(await self.dispatcher.accept(response_for(first, 1), first))
        self.assertTrue(await self.dispatcher.accept(response_for(first, 1), first))
        second = sample(11)
        self.now_us = second["timestamp"] + 1000
        self.assertTrue(await self.dispatcher.accept(response_for(second, 2), second))
        self.assertEqual(self.handlers.calls, [("APPROACH", 17)])

        third = sample(12)
        self.now_us = third["timestamp"] + 1000
        self.assertTrue(await self.dispatcher.accept(response_for(third, 3, "ENGAGE"), third))
        self.assertEqual(self.handlers.calls[-2:], [("CANCEL", "decision_changed"), ("ENGAGE", 17)])
        fourth = sample(13)
        self.now_us = fourth["timestamp"] + 1000
        held = response_for(fourth, 4, "CONTINUE", False)
        held["policy_decision"] = None
        self.assertFalse(await self.dispatcher.accept(held, fourth))
        self.assertEqual(self.handlers.calls[-1], ("CANCEL", "observation_or_execution_hold"))
        with self.assertRaises(DecisionRejected):
            parse_decision(response_for(fourth, 4, "DEFER", False), fourth, self.now_us, 1_000_000)

    async def test_rejects_stale_mismatched_target_and_changed_session(self):
        observation = sample(10)
        self.now_us = observation["timestamp"] + 1_000_001
        with self.assertRaisesRegex(DecisionRejected, "source_frame_not_fresh"):
            parse_decision(response_for(observation, 1), observation, self.now_us, 1_000_000)
        self.now_us = observation["timestamp"] + 1000
        self.assertTrue(await self.dispatcher.accept(response_for(observation, 1), observation))
        next_frame = sample(11)
        self.now_us = next_frame["timestamp"] + 1000
        bad = response_for(next_frame, 2)
        bad["social_state"]["people"] = []
        with self.assertLogs("robot.navel_client.decision_dispatch", level="WARNING"):
            self.assertFalse(await self.dispatcher.accept(bad, next_frame))
        self.assertEqual(self.handlers.calls[-1], ("CANCEL", "target_not_observed"))
        local_missing = dict(next_frame)
        local_missing["people"] = []
        with self.assertRaisesRegex(DecisionRejected, "target_not_observed"):
            parse_decision(response_for(next_frame, 2), local_missing,
                           self.now_us, 1_000_000)
        changed = response_for(next_frame, 2)
        changed["social_state"]["session_id"] = "session-b"
        changed["policy_decision"]["session_id"] = "session-b"
        with self.assertLogs("robot.navel_client.decision_dispatch", level="WARNING"):
            self.assertFalse(await self.dispatcher.accept(changed, next_frame))

    async def test_watchdog_expires_active_placeholder_without_new_frame(self):
        observation = sample(10)
        self.now_us = observation["timestamp"] + 1000
        await self.dispatcher.accept(response_for(observation, 1), observation)
        task = asyncio.create_task(self.dispatcher.watchdog())
        try:
            self.now_s = 0.2
            with self.assertLogs("robot.navel_client.decision_dispatch", level="WARNING"):
                await asyncio.sleep(0.08)
            self.assertEqual(self.handlers.calls[-1], ("CANCEL", "decision_timeout"))
            self.assertIsNone(self.dispatcher.current)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_live_server_response_reaches_robot_placeholder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = create_app(root/"raw.jsonl", social_output=root/"social.jsonl",
                                session_id="live-test").test_client()
            queue = asyncio.Queue(maxsize=1)
            calls = self.handlers.calls

            class Transport:
                def send(_, observation):
                    self.now_us = observation["timestamp"] + 1000
                    result = client.post("/api/v1/observations", json=observation)
                    return ObservationResponse(result.status_code, result.json)

            sender = asyncio.create_task(_send_observations(
                queue, Transport(), minimum_send_interval_s=0, print_only=False,
                decision_dispatcher=self.dispatcher,
            ))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    for i in range(21):
                        queue.put_nowait(sample(i, distance=2.0))
                        await asyncio.wait_for(queue.join(), 1)
                self.assertEqual(calls.count(("APPROACH", 17)), 1)
                self.assertTrue((root/"social.jsonl").exists())
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)

    async def test_missing_decision_and_transport_error_clear_active(self):
        observation = sample(10)
        self.now_us = observation["timestamp"] + 1000
        await self.dispatcher.accept(response_for(observation, 1), observation)
        next_frame = sample(11)
        self.now_us = next_frame["timestamp"] + 1000
        with self.assertLogs("robot.navel_client.decision_dispatch", level="WARNING"):
            self.assertFalse(await self.dispatcher.accept({"accepted": True}, next_frame))
        self.assertEqual(self.handlers.calls[-1], ("CANCEL", "observation_not_fully_processed"))
        await self.dispatcher.accept(response_for(next_frame, 2), next_frame)
        await self.dispatcher.invalidate("transport_error")
        self.assertEqual(self.handlers.calls[-1], ("CANCEL", "transport_error"))

    def test_cli_mode_and_timeouts(self):
        args = parse_args(["--decision-dry-run"])
        self.assertTrue(args.decision_dry_run)
        args = parse_args(["--decision-dry-run", "--single-trial"])
        self.assertEqual((args.single_trial_policy, args.decision_wait_timeout), ("rules", 30.0))
        for option in ("--max-decision-age", "--decision-timeout", "--decision-wait-timeout"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args([option, "0"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--print-only", "--decision-dry-run"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--single-trial"])

    def test_single_trial_accepts_once_without_claiming_execution(self):
        observation = sample(10)
        trial = SingleTrial(monotonic=lambda: self.now_s, monotonic_us=lambda: observation["timestamp"])
        payload = response_for(observation, 1, "CONTINUE", False)
        payload["final_decision"] = {"action": "CONTINUE", "reason": "No attention."}
        payload["policy_readiness"] = {"status": "OBSERVING", "reason_code": "INSUFFICIENT_GAZE_EVIDENCE"}
        evidence = payload["social_state"]["people"][0]["evidence"]
        evidence.update(gaze_valid=False, gaze_valid_coverage_s=.267)
        with self.assertLogs("robot.navel_client.single_trial", level="INFO") as logs:
            self.assertFalse(trial.accept_rule_response(payload, observation))
            self.assertFalse(trial.accept_rule_response(payload, observation))
        self.assertEqual(len(logs.output), 1)
        self.assertIn("reason=INSUFFICIENT_GAZE_EVIDENCE", logs.output[0])
        self.assertIn("gaze_valid_coverage_s=0.267", logs.output[0])
        evidence["gaze_valid"] = True
        payload["policy_readiness"] = {"status": "READY", "reason_code": None}
        payload["social_state"]["people"] = []
        self.assertFalse(trial.accept_rule_response(payload, {**observation, "people": []}))
        self.assertFalse(trial.ready)
        payload["social_state"]["people"] = [{"visibility": "OBSERVED", "gaze_state": "SUSTAINED", "evidence": {
            "latest_distance_valid": True, "gaze_valid": True, "distance_trend_valid": False}}]
        payload["policy_decision"]["decision"] = "DEFER"
        payload["final_decision"] = None
        with self.assertLogs("robot.navel_client.single_trial", level="WARNING") as logs:
            self.assertFalse(trial.accept_rule_response(payload, observation))
        self.assertIn("decision_rejected=decision_or_reason_invalid", logs.output[0])
        self.assertEqual(trial.phase, "OBSERVING")
        source = {**payload["policy_decision"], "source_robot_timestamp_us": observation["timestamp"]}
        final = {"action": "CONTINUE", "reason": "No attention."}
        self.assertFalse(trial.accept_decision("llm", final, source))
        for invalid in ({**final, "uid": 17}, {**final, "action": "STOP"}, {**final, "action": "DEFER"}, {**final, "reason": " "}):
            self.assertFalse(trial.accept_decision("rules", invalid, source))
        payload["policy_decision"]["decision"] = "CONTINUE"
        payload["final_decision"] = final
        self.assertTrue(trial.accept_rule_response(payload, observation))
        self.assertEqual(trial.phase, "DECIDED")
        self.assertFalse(trial.complete_execution())
        final["action"] = "YIELD"
        self.assertFalse(trial.accept_rule_response(payload, observation))
        self.assertEqual(trial.decision["action"], "CONTINUE")
        with self.assertRaises(TypeError):
            trial.source["source_state_id"] = "other"
        self.assertTrue(trial.start_execution())
        self.assertFalse(trial.start_execution())
        self.assertTrue(trial.complete_execution())
        self.assertFalse(trial.accept_decision("rules", final, source))

    def test_trial_timeout_session_change_and_model_acceptance_boundary(self):
        observation = sample(10)
        payload = response_for(observation, 1, "CONTINUE", False)
        final = {"action": "YIELD", "reason": "Give room to pass."}
        source = {"session_id": "session-a", "source_state_id": "session-a:1",
                  "source_robot_timestamp_us": observation["timestamp"]}
        # An image policy does not need a temporal window; source/freshness still apply.
        trial = SingleTrial("vlm", wait_timeout_s=1, monotonic=lambda: self.now_s,
                            monotonic_us=lambda: observation["timestamp"])
        payload["model_trial"] = {"image_ready": True}
        trial.observe(payload, observation)
        source.update(trial_id=trial.trial_id, policy="vlm", request_id="request")
        self.assertTrue(trial.register_request(source))
        self.assertFalse(trial.accept_decision("vlm", final, {**source, "source_state_id": "old"}))
        self.assertTrue(trial.accept_decision("vlm", final, source))
        changed = deepcopy(payload)
        changed["social_state"]["session_id"] = "new-session"
        trial.observe(changed, observation)
        self.assertEqual((trial.phase, trial.failure_reason), ("DECIDED", None))
        self.assertFalse(trial.accept_decision("vlm", final, source))
        fresh = SingleTrial("vlm", monotonic_us=lambda: observation["timestamp"])
        fresh.observe(payload, observation)
        fresh.observe(changed, observation)
        self.assertEqual(fresh.failure_reason, "SESSION_INVALIDATED")
        trial = SingleTrial("vlm", wait_timeout_s=1, monotonic=lambda: self.now_s,
                            monotonic_us=lambda: observation["timestamp"])
        trial.observe(payload, observation)
        self.now_s = 1
        self.assertFalse(trial.accept_decision("vlm", final, source))
        self.assertEqual(trial.failure_reason, "NO_DECISION_TIMEOUT")

    async def test_trial_sender_latches_pure_rule_and_keeps_observations_flowing(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(Path(directory)/"raw.jsonl", social_output=Path(directory)/"social.jsonl",
                                session_id="trial").test_client()
            queue = asyncio.Queue(maxsize=1)
            trial = SingleTrial(monotonic_us=lambda: self.now_us)

            class Transport:
                def send(_, observation):
                    self.now_us = observation["timestamp"]
                    result = client.post("/api/v1/observations", json=observation)
                    return ObservationResponse(result.status_code, result.json)

            sender = asyncio.create_task(_send_observations(
                queue, Transport(), minimum_send_interval_s=0, print_only=False, trial=trial))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    for i in range(22):
                        queue.put_nowait(sample(i, distance=2.0, uid=0))
                        await asyncio.wait_for(queue.join(), 1)
                self.assertEqual(trial.phase, "DECIDED")
                self.assertEqual(trial.decision["action"], "APPROACH")
                self.assertEqual(len((Path(directory)/"raw.jsonl").read_text().splitlines()), 22)
                self.assertEqual(self.handlers.calls, [])
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
