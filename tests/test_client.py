"""Async collection, latest-frame delivery, failures, and dependency boundary."""

import asyncio
import contextlib
import io
import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from robot.navel_client.main import (
    LatestLocomotion, _collect_perception, _replace_queued, _send_observations,
    collect_and_stream, parse_args,
)
from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.transport import ObservationResponse, ObservationTransport, TransportError
from robot.navel_client.single_trial import SingleTrial
from tests.fixtures import frame, locomotion, perception, person


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_frame_replaces_pending_frame(self):
        queue = asyncio.Queue(maxsize=1)
        _replace_queued(queue, {**frame(), "timestamp": 1})
        _replace_queued(queue, {**frame(), "timestamp": 2})
        self.assertEqual(queue.get_nowait()["timestamp"], 2)
        queue.task_done()
        await asyncio.wait_for(queue.join(), 1)

    async def test_stale_locomotion_becomes_unavailable(self):
        latest = LatestLocomotion((locomotion(), time.monotonic() - 2))
        queue = asyncio.Queue(maxsize=1)
        calls = 0

        class Robot:
            async def next_frame(self, timeout):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise TimeoutError
                if calls == 2:
                    return perception()
                await asyncio.Event().wait()

        task = asyncio.create_task(_collect_perception(
            Robot(), NavelObservationAdapter(), latest, queue, max_locomotion_age_s=1,
        ))
        try:
            observation = await asyncio.wait_for(queue.get(), 1)
            self.assertEqual(observation["robot"], {"linear_velocity": None, "angular_velocity": None})
            self.assertEqual(observation["safety"], {"lidar": None, "sonar": None})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_print_only_outputs_json_without_http(self):
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait(frame())
        transport = NS(send=lambda _: self.fail("HTTP should not be called"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            task = asyncio.create_task(_send_observations(
                queue, transport, minimum_send_interval_s=0, print_only=True,
            ))
            try:
                await asyncio.wait_for(queue.join(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(json.loads(output.getvalue()), frame())

    async def test_transport_failure_does_not_stop_sender(self):
        queue = asyncio.Queue(maxsize=1)
        calls = []

        class Transport:
            def send(self, observation):
                calls.append(observation["timestamp"])
                if len(calls) == 1:
                    raise TransportError("offline")
                return ObservationResponse(200, {"accepted": True})

        queue.put_nowait({**frame(), "timestamp": 1})
        with self.assertLogs("robot.navel_client.main", level="WARNING"):
            task = asyncio.create_task(_send_observations(
                queue, Transport(), minimum_send_interval_s=0, print_only=False,
            ))
            try:
                await asyncio.wait_for(queue.join(), 1)
                queue.put_nowait({**frame(), "timestamp": 2})
                await asyncio.wait_for(queue.join(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(calls, [1, 2])

    async def test_http_runs_in_worker_and_newest_frame_survives_slow_request(self):
        queue = asyncio.Queue(maxsize=1)
        started = threading.Event()
        release = threading.Event()
        observations = []
        thread_ids = []

        class Transport:
            def send(self, observation):
                observations.append(observation["timestamp"])
                thread_ids.append(threading.get_ident())
                started.set()
                release.wait(2)
                return ObservationResponse(200, {"accepted": True})

        queue.put_nowait({**frame(), "timestamp": 1})
        task = asyncio.create_task(_send_observations(
            queue, Transport(), minimum_send_interval_s=0, print_only=False,
        ))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            _replace_queued(queue, {**frame(), "timestamp": 2})
            _replace_queued(queue, {**frame(), "timestamp": 3})
            release.set()
            await asyncio.wait_for(queue.join(), 1)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(observations, [1, 3])
        self.assertTrue(all(t != threading.get_ident() for t in thread_ids))

    async def test_sdk_disconnect_cancels_other_tasks(self):
        stopped = asyncio.Event()

        class Robot:
            async def next_locomotion(self, timeout):
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            async def next_frame(self, timeout):
                await asyncio.sleep(0)
                raise ConnectionAbortedError("robot disconnected")

        with self.assertRaises(ConnectionAbortedError):
            await collect_and_stream(Robot(), parse_args(["--print-only"]))
        self.assertTrue(stopped.is_set())

    async def test_trial_timeout_and_interruption_use_collector_cleanup(self):
        class Robot:
            async def next_locomotion(self, timeout):
                await asyncio.Event().wait()

            async def next_frame(self, timeout):
                await asyncio.Event().wait()

        args = parse_args(["--decision-dry-run", "--single-trial"])
        for interrupt in (False, True):
            trial = SingleTrial(wait_timeout_s=0.01 if not interrupt else 30)
            with patch("robot.navel_client.main.SingleTrial", return_value=trial):
                task = asyncio.create_task(collect_and_stream(Robot(), args))
                if interrupt:
                    await asyncio.sleep(0)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertIs(await asyncio.wait_for(task, 1), trial)
            self.assertEqual(trial.failure_reason, "INTERRUPTED" if interrupt else "NO_DECISION_TIMEOUT")

    async def test_route_lifecycle_stops_one_sender_before_zero(self):
        # SDK-shaped tasks, including an asynchronously settling cancellation.
        for outcome in ("decision", "finished", "timeout", "slow_http", "transport", "rejected", "collector", "head", "interrupt"):
            with self.subTest(outcome=outcome):
                events = []
                readers = set()
                started = asyncio.Event()
                finish = asyncio.Event()
                tracking = asyncio.Event()
                release_http = threading.Event()
                http_done = threading.Event()
                packets = asyncio.Queue()
                packets.put_nowait(perception(person(17)))
                packets.put_nowait(perception(person(17), person(18)))
                packets.put_nowait(perception())
                active = False

                class Robot:
                    def move_base(self, distance, *, speed, acceleration):
                        events.append(("move", distance, speed, acceleration))

                        async def movement():
                            nonlocal active
                            active = True
                            started.set()
                            try:
                                await finish.wait()
                            finally:
                                await asyncio.sleep(0)
                                active = False
                                events.append("settled")
                        return asyncio.create_task(movement())

                    def base_vel(self, x, r):
                        assert not active, "zero velocity before movement sender settled"
                        if outcome == "slow_http":
                            assert not http_done.is_set(), "local stop waited for HTTP"
                        events.append(("zero", x, r))
                        release_http.set()

                    async def look_at_person(self, uid, head):
                        events.append(("head", uid, head))
                        tracking.set()
                        if outcome == "head":
                            raise OSError("head failed")

                    async def next_frame(self, timeout):
                        readers.add(asyncio.current_task())
                        await started.wait()
                        if outcome == "collector":
                            raise ConnectionAbortedError("perception failed")
                        packet = await packets.get()
                        await asyncio.sleep(0.001)
                        return packet

                    async def next_locomotion(self, timeout):
                        await asyncio.Event().wait()

                trial = SingleTrial(wait_timeout_s=0.01 if outcome in ("timeout", "slow_http") else 30)

                def send(observation):
                    if outcome == "slow_http":
                        release_http.wait(1)
                        http_done.set()
                    if outcome == "transport":
                        raise TransportError("offline")
                    if outcome == "rejected":
                        return ObservationResponse(503, {"accepted": False})
                    state_id = "session-a:1"
                    return ObservationResponse(200, {
                        "accepted": True, "processing_status": "complete",
                        "timestamp": observation["timestamp"],
                        "social_state": {
                            "state_id": state_id, "session_id": "session-a",
                            "robot_timestamp_us": observation["timestamp"],
                            "people": [{"visibility": "OBSERVED", "gaze_state": "SUSTAINED", "evidence": {
                                "latest_distance_valid": True, "gaze_valid": True}}]},
                        "policy_decision": {
                            "decision_id": f"{state_id}:social-rules-v2", "source_state_id": state_id,
                            "session_id": "session-a", "policy_version": "social-rules-v2",
                            "decision": "CONTINUE",
                            "reason_code": "TEST", "target_uid": None, "target_track_epoch": None} if outcome == "decision" else None,
                        "final_decision": ({"action": "CONTINUE", "reason": "Keep going."}
                                           if outcome == "decision" else None),
                    })

                args = parse_args(["--decision-dry-run", "--single-trial", "--route-trial",
                                   "--route-distance", "0.5", "--minimum-send-interval", "0"])
                with patch("robot.navel_client.main.SingleTrial", return_value=trial), \
                        patch.object(ObservationTransport, "send", side_effect=send), \
                        contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(collect_and_stream(Robot(), args))
                    await asyncio.wait_for(started.wait(), 1)
                    if outcome == "finished":
                        await asyncio.wait_for(tracking.wait(), 1)
                        finish.set()
                    elif outcome == "interrupt":
                        task.cancel()
                    if outcome in ("collector", "head", "interrupt"):
                        with self.assertRaises(asyncio.CancelledError if outcome == "interrupt" else OSError):
                            await asyncio.wait_for(task, 1)
                    else:
                        self.assertIs(await asyncio.wait_for(task, 1), trial)
                self.assertEqual([e for e in events if isinstance(e, tuple) and e[0] == "move"],
                                 [("move", 0.5, 0.1, 0.2)])
                self.assertEqual(events[-2:], ["settled", ("zero", 0.0, 0.0)])
                self.assertEqual(len(readers), 1)
                if outcome == "decision":
                    self.assertEqual(trial.phase, "DECIDED")
                    self.assertEqual(dict(trial.decision), {"action": "CONTINUE", "reason": "Keep going."})
                else:
                    self.assertEqual(trial.phase, "FAILED")
                    if outcome == "finished":
                        self.assertEqual(trial.failure_reason, "ROUTE_FINISHED_WITHOUT_DECISION")
                if outcome not in ("collector", "interrupt"):
                    self.assertIn(("head", 17, 1.0), events)


class TransportTests(unittest.TestCase):
    def test_invalid_urls_and_timeout_values_are_rejected(self):
        for url in ["file:///tmp/data", "http://", "http://localhost/api", "http://localhost?x=1"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                ObservationTransport(url)
        for timeout in [0, -1, float("nan"), float("inf")]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                ObservationTransport("http://localhost", timeout_seconds=timeout)

    def test_nonfinite_json_is_not_sent(self):
        with self.assertRaisesRegex(TransportError, "finite JSON"):
            ObservationTransport("http://localhost").send({"timestamp": float("nan")})

    def test_malformed_server_responses_are_reported(self):
        for body in [b"not-json", b"[]", b"\\xff"]:
            with self.subTest(body=body), self.assertRaises(TransportError):
                ObservationTransport._response(200, body)

    def test_url_failure_is_reported(self):
        transport = ObservationTransport("http://localhost")
        with patch.object(transport._opener, "open", side_effect=TimeoutError):
            with self.assertRaises(TransportError):
                transport.send(frame())

    def test_robot_imports_do_not_need_server_packages_or_sdk(self):
        # An isolated process blocks server/SDK imports, avoiding module-cache effects.
        code = """
import sys
class Blocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'navel', 'flask', 'pydantic', 'openai', 'dotenv', 'app'}:
            raise AssertionError('robot import reached forbidden dependency: ' + fullname)
sys.meta_path.insert(0, Blocker())
import robot.navel_client.main
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nonfinite_cli_settings_are_rejected(self):
        for option in ["--request-timeout", "--max-locomotion-age", "--minimum-send-interval", "--decision-wait-timeout"]:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args([option, "nan"])

    def test_route_requires_explicit_opt_in_and_valid_settings(self):
        self.assertFalse(parse_args(["--decision-dry-run", "--single-trial"]).route_trial)
        for argv in (["--route-trial"], ["--single-trial", "--route-trial"],
                     ["--route-speed", "1.7"], ["--route-acceleration", "1.3"],
                     ["--route-distance", "0"], ["--route-speed", "nan"],
                     ["--route-acceleration", "inf"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(argv)


if __name__ == "__main__":
    unittest.main()
