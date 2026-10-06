"""Subprocess action contract and fail-closed physical executor behavior."""

import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from app.commands import CommandConfig
from app.server import create_app
from robot.navel_client.main import _send_execution_events, _send_observations, parse_args
from robot.navel_client.physical_executor import PhysicalCommandExecutor, ScriptPaths
from robot.navel_client.transport import ObservationResponse
from tests.test_social_state import sample


def make_scripts(root: Path, *, action_delay: float = 0, fail_pause: bool = False,
                 fail_stop: bool = False, pause_delay: float = 0):
    log = root / "scripts.jsonl"
    paths = {}
    for name in ("approach", "engage", "pause_route", "resume_route", "stop"):
        path = root / f"{name}.py"
        path.write_text(
            "import json, sys, time\n"
            "request = json.loads(sys.stdin.readline())\n"
            f"with open({str(log)!r}, 'a') as log:\n"
            f"    log.write(json.dumps({{'name': {name!r}, 'reason': request['reason'], "
            "'command_id': request['command']['command_id'] if request['command'] else None}) + '\\n')\n"
            f"time.sleep({action_delay if name in ('approach', 'engage') else pause_delay if name == 'pause_route' else 0!r})\n"
            f"print(json.dumps({{'ok': {not ((fail_pause and name == 'pause_route') or (fail_stop and name == 'stop'))!r}}}))\n"
        )
        paths[name] = path
    return ScriptPaths(paths["approach"], paths["engage"], paths["pause_route"],
                       paths["resume_route"], paths["stop"]), log


class PhysicalExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def _ready(self, client, *, distance=2.0):
        for index in range(13):
            self.assertEqual(client.post("/api/v1/observations",
                                         json=sample(index, distance=distance)).status_code, 200)
        observation = sample(13, distance=distance)
        payload = client.post("/api/v1/observations", json=observation).json
        self.assertIsNotNone(payload["robot_command"])
        return observation, payload

    async def _drain(self, executor, client, count):
        events = []
        for _ in range(count):
            event = await asyncio.wait_for(executor.event_queue.get(), 2)
            events.append(event)
            self.assertEqual(client.post("/api/v1/execution-events", json=event).status_code, 200)
            executor.event_queue.task_done()
        return events

    async def test_engage_completes_once_and_releases_lock_after_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, log = make_scripts(root)
            client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                session_id="physical-engage").test_client()
            observation, payload = await self._ready(client, distance=1.0)
            clock_us = [observation["timestamp"]]
            executor = PhysicalCommandExecutor(scripts,
                monotonic_us=lambda: clock_us[0])
            await executor.accept(payload, observation)
            events = await self._drain(executor, client, 3)
            self.assertEqual([event["status"] for event in events],
                             ["RECEIVED", "STARTED", "COMPLETED"])
            self.assertIsNone(executor.active)
            self.assertTrue(executor.route_paused)
            entries = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([entry["name"] for entry in entries],
                             ["pause_route", "engage", "stop"])
            later_observation = sample(14, distance=1.0)
            clock_us[0] = later_observation["timestamp"]
            later_payload = client.post("/api/v1/observations", json=later_observation).json
            self.assertEqual(later_payload["target_lock"]["status"], "COOLDOWN")
            self.assertIn("RELEASED_ENGAGEMENT_COMPLETED", later_payload["target_lock"]["events"])
            self.assertIsNone(later_payload["robot_command"])
            await executor.accept(later_payload, later_observation)
            self.assertFalse(executor.route_paused)
            entries = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(entries[-1]["name"], "resume_route")
            await executor.close()

    async def test_target_loss_cancels_script_and_calls_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, log = make_scripts(root, action_delay=5)
            client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                session_id="physical-loss").test_client()
            observation, payload = await self._ready(client)
            clock_us = [observation["timestamp"]]
            executor = PhysicalCommandExecutor(scripts, monotonic_us=lambda: clock_us[0])
            await executor.accept(payload, observation)
            events = await self._drain(executor, client, 2)
            self.assertEqual([event["status"] for event in events], ["RECEIVED", "STARTED"])
            gone = sample(14)
            gone["people"] = []
            clock_us[0] = gone["timestamp"]
            gone_payload = client.post("/api/v1/observations", json=gone).json
            await executor.accept(gone_payload, gone)
            cancelled = (await self._drain(executor, client, 1))[0]
            self.assertEqual(cancelled["status"], "CANCELLED")
            self.assertIsNone(executor.active)
            self.assertTrue(executor.route_paused)
            entries = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertIn("stop", [entry["name"] for entry in entries])
            self.assertNotIn("resume_route", [entry["name"] for entry in entries])
            return_observation = sample(15)
            returned = client.post("/api/v1/observations", json=return_observation).json
            self.assertEqual(returned["target_lock"]["status"], "COOLDOWN")
            self.assertIsNone(returned["robot_command"])
            await executor.close()

    async def test_failed_route_pause_rejects_without_starting_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, log = make_scripts(root, fail_pause=True)
            client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                session_id="physical-pause-fail").test_client()
            observation, payload = await self._ready(client)
            executor = PhysicalCommandExecutor(scripts,
                monotonic_us=lambda: observation["timestamp"])
            await executor.accept(payload, observation)
            events = await self._drain(executor, client, 2)
            self.assertEqual([event["status"] for event in events], ["RECEIVED", "REJECTED"])
            entries = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([entry["name"] for entry in entries], ["pause_route", "stop"])
            self.assertEqual(executor.fault, "ROUTE_PAUSE_FAILED")
            await executor.close()

    async def test_response_watchdog_cancels_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, _ = make_scripts(root, action_delay=5)
            client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                session_id="physical-watchdog").test_client()
            observation, payload = await self._ready(client)
            executor = PhysicalCommandExecutor(scripts, response_timeout_s=0.1,
                monotonic_us=lambda: observation["timestamp"])
            watcher = asyncio.create_task(executor.watchdog())
            try:
                await executor.accept(payload, observation)
                events = await self._drain(executor, client, 3)
                self.assertEqual(events[-1]["status"], "CANCELLED")
                self.assertEqual(events[-1]["reason"], "RESPONSE_TIMEOUT")
                self.assertIsNone(executor.active)
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
                await executor.close()

    async def test_stop_failure_latches_fault_and_blocks_route_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, _ = make_scripts(root, fail_stop=True)
            client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                session_id="physical-stop-fail").test_client()
            observation, payload = await self._ready(client)
            clock_us = [observation["timestamp"]]
            executor = PhysicalCommandExecutor(scripts, monotonic_us=lambda: clock_us[0])
            await executor.accept(payload, observation)
            events = await self._drain(executor, client, 3)
            self.assertEqual(events[-1]["status"], "FAILED")
            self.assertEqual(events[-1]["reason"], "STOP_FAILED")
            self.assertEqual(executor.fault, "STOP_FAILED")
            later = sample(14)
            clock_us[0] = later["timestamp"]
            result = client.post("/api/v1/observations", json=later).json
            self.assertEqual(result["target_lock"]["status"], "COOLDOWN")
            await executor.accept(result, later)
            self.assertTrue(executor.route_paused)

    async def test_execution_lease_terminates_slow_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, log = make_scripts(root, action_delay=5)
            client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                command_config=CommandConfig(execution_lease_s=0.1),
                                session_id="physical-lease").test_client()
            observation, payload = await self._ready(client)
            executor = PhysicalCommandExecutor(scripts,
                monotonic_us=lambda: observation["timestamp"])
            await executor.accept(payload, observation)
            events = await self._drain(executor, client, 3)
            self.assertEqual(events[-1]["status"], "FAILED")
            self.assertEqual(events[-1]["reason"], "APPROACH_TIMEOUT")
            self.assertIn("stop", [json.loads(line)["name"] for line in log.read_text().splitlines()])
            await executor.close()

    async def test_slow_route_pause_cannot_launch_expired_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, log = make_scripts(root, pause_delay=0.15)
            client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                command_config=CommandConfig(max_source_age_s=0.05),
                                session_id="physical-stale-after-pause").test_client()
            observation, payload = await self._ready(client)
            offset = observation["timestamp"] - int(asyncio.get_running_loop().time() * 1_000_000)
            executor = PhysicalCommandExecutor(
                scripts, monotonic_us=lambda: int(asyncio.get_running_loop().time() * 1_000_000) + offset)
            await executor.accept(payload, observation)
            events = await self._drain(executor, client, 2)
            self.assertEqual([event["status"] for event in events], ["RECEIVED", "REJECTED"])
            self.assertEqual(events[-1]["reason"], "COMMAND_EXPIRED_BEFORE_START")
            self.assertNotIn("approach", [json.loads(line)["name"] for line in log.read_text().splitlines()])
            await executor.close()

    async def test_idle_route_lock_stops_once_on_transport_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, log = make_scripts(root)
            client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                session_id="physical-idle-loss").test_client()
            observation = sample(0)
            payload = client.post("/api/v1/observations", json=observation).json
            self.assertIsNone(payload["robot_command"])
            executor = PhysicalCommandExecutor(scripts,
                monotonic_us=lambda: observation["timestamp"])
            await executor.accept(payload, observation)
            self.assertTrue(executor.route_paused)
            await executor.invalidate("transport_error")
            await executor.invalidate("transport_error")
            entries = [json.loads(line)["name"] for line in log.read_text().splitlines()]
            self.assertEqual(entries, ["pause_route", "stop"])
            await executor.close()

    async def test_approach_needs_stationary_base_and_front_ranges(self):
        for missing in ("motion", "ranges"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                scripts, log = make_scripts(root)
                client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                    session_id=f"physical-{missing}").test_client()
                observation, payload = await self._ready(client)
                local = json.loads(json.dumps(observation))
                if missing == "motion":
                    local["robot"]["linear_velocity"] = None
                else:
                    local["safety"]["lidar"][0] = None
                executor = PhysicalCommandExecutor(scripts,
                    monotonic_us=lambda: observation["timestamp"])
                await executor.accept(payload, local)
                events = await self._drain(executor, client, 2)
                self.assertEqual(events[-1]["status"], "REJECTED")
                self.assertNotIn("approach", [json.loads(line)["name"] for line in log.read_text().splitlines()])
                await executor.close()

    async def test_sender_runs_script_and_posts_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, _ = make_scripts(root)
            app = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                             session_id="physical-sender")
            clock_us = [0]
            executor = PhysicalCommandExecutor(scripts, monotonic_us=lambda: clock_us[0])
            queue = asyncio.Queue(maxsize=1)

            class Transport:
                def send(self, observation):
                    clock_us[0] = observation["timestamp"]
                    result = app.test_client().post("/api/v1/observations", json=observation)
                    return ObservationResponse(result.status_code, result.json)

                def send_event(self, event):
                    result = app.test_client().post("/api/v1/execution-events", json=event)
                    return ObservationResponse(result.status_code, result.json)

            transport = Transport()
            sender = asyncio.create_task(_send_observations(
                queue, transport, minimum_send_interval_s=0, print_only=False,
                physical_executor=executor))
            reporter = asyncio.create_task(_send_execution_events(executor, transport))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    for index in range(14):
                        queue.put_nowait(sample(index, distance=1.0))
                        await asyncio.wait_for(queue.join(), 2)
                async def feedback_done():
                    while True:
                        ledger = app.extensions["social_pipeline"].commands.events
                        if ledger and next(iter(ledger.values()))[-1].status == "COMPLETED":
                            return
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(feedback_done(), 2)
                self.assertIsNone(executor.active)
                self.assertEqual(next(iter(executor.events.values()))[-1]["status"], "COMPLETED")
            finally:
                sender.cancel()
                reporter.cancel()
                await asyncio.gather(sender, reporter, return_exceptions=True)
                await executor.close()

    def test_cli_requires_all_scripts_and_excludes_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts, _ = make_scripts(root)
            script_args = ["--approach-script", str(scripts.approach),
                           "--engage-script", str(scripts.engage),
                           "--pause-route-script", str(scripts.pause_route),
                           "--resume-route-script", str(scripts.resume_route),
                           "--stop-script", str(scripts.stop)]
            self.assertTrue(parse_args(["--physical-executor", *script_args]).physical_executor)
            for args in (["--physical-executor"],
                         ["--physical-executor", "--command-dry-run", *script_args],
                         [*script_args]):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    parse_args(args)
