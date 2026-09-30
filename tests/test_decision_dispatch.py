"""Robot dry-run response routing, idempotency, and loss handling."""
import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from app.server import create_app
from robot.navel_client.decision_dispatch import (
    DecisionDispatcher, DryRunHandlers, DecisionRejected, parse_decision,
)
from robot.navel_client.main import _send_observations, parse_args
from robot.navel_client.transport import ObservationResponse, TransportError
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
            "people": [{"uid": 17, "track_epoch": 1, "visibility": "OBSERVED"}],
        },
        "policy_decision": {
            "decision_id": f"{state_id}:social-rules-v1",
            "source_state_id": state_id, "session_id": "session-a",
            "policy_version": "social-rules-v1", "decision": decision,
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

    async def observe(self, decision):
        self.calls.append(("DEFER", decision.target_uid))

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
        self.assertTrue(await self.dispatcher.accept(response_for(fourth, 4, "DEFER", False), fourth))
        self.assertEqual(self.handlers.calls[-2:], [("CANCEL", "decision_changed"), ("DEFER", None)])

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
        for option in ("--max-decision-age", "--decision-timeout"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args([option, "0"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--print-only", "--decision-dry-run"])


if __name__ == "__main__":
    unittest.main()
