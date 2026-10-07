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
from tests.fixtures import frame, locomotion, perception


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


if __name__ == "__main__":
    unittest.main()
