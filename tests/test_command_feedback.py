"""Command correlation, retry semantics, and simulated feedback boundaries."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.command import main as replay_commands
from app.social import recording_session
from app.social_pipeline import SocialPipeline
from app.server import create_app
from robot.navel_client.command_dispatch import CommandRejected, FakeCommandExecutor
from tests.test_social_state import sample


class CommandFeedbackTests(unittest.TestCase):
    def _ready(self, client):
        for index in range(13):
            self.assertEqual(client.post("/api/v1/observations", json=sample(index)).status_code, 200)
        observation = sample(13)
        response = client.post("/api/v1/observations", json=observation)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json["robot_command"])
        return observation, response.json

    def test_retry_feedback_deduplication_and_trace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                execution_output=root / "execution.jsonl", session_id="commands").test_client()
            observation, payload = self._ready(client)
            command = payload["robot_command"]
            self.assertEqual(command["action"], "APPROACH")
            self.assertEqual(command["target_uid"], 17)
            self.assertEqual(command["source_state_id"], payload["social_state"]["state_id"])

            next_observation = sample(14)
            retry = client.post("/api/v1/observations", json=next_observation).json
            self.assertEqual(retry["robot_command"], command)
            executor = FakeCommandExecutor(monotonic_us=lambda: next_observation["timestamp"])
            events = executor.accept(retry, next_observation)
            self.assertEqual([e["status"] for e in events], ["RECEIVED", "SIMULATED"])
            self.assertEqual(executor.accept(retry, next_observation), events)
            self.assertEqual(len(executor.commands), 1)
            for event in events:
                accepted = client.post("/api/v1/execution-events", json=event)
                self.assertEqual(accepted.status_code, 200)
                self.assertFalse(accepted.json["duplicate"])
                duplicate = client.post("/api/v1/execution-events", json=event)
                self.assertEqual(duplicate.status_code, 200)
                self.assertTrue(duplicate.json["duplicate"])
            self.assertIsNone(client.post("/api/v1/observations", json=sample(15)).json["robot_command"])
            self.assertEqual(len((root / "execution.jsonl").read_text().splitlines()), 2)
            self.assertEqual(len((root / "commands.jsonl").read_text().splitlines()), 16)

    def test_rejects_wrong_lock_expired_command_and_conflicting_feedback(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            client = create_app(root / "raw.jsonl", command_output=root / "commands.jsonl",
                                session_id="commands").test_client()
            observation, payload = self._ready(client)
            executor = FakeCommandExecutor(monotonic_us=lambda: observation["timestamp"])
            wrong_lock = deepcopy(payload)
            wrong_lock["robot_command"]["lock_id"] = "other"
            with self.assertRaises(CommandRejected):
                executor.accept(wrong_lock, observation)
            expired = FakeCommandExecutor(monotonic_us=lambda: payload["robot_command"]["expires_at_robot_us"] + 1)
            with self.assertRaises(CommandRejected):
                expired.accept(payload, observation)
            events = executor.accept(payload, observation)
            self.assertEqual(client.post("/api/v1/execution-events", json=events[1]).status_code, 409)
            self.assertEqual(client.post("/api/v1/execution-events", json=events[0]).status_code, 200)
            conflicting = {**events[0], "reason": "OTHER"}
            self.assertEqual(client.post("/api/v1/execution-events", json=conflicting).status_code, 409)
            unknown = {**events[0], "command_id": "missing", "event_id": "missing:1"}
            self.assertEqual(client.post("/api/v1/execution-events", json=unknown).status_code, 409)

    def test_missing_target_and_uid_zero_produce_no_command(self):
        with TemporaryDirectory() as directory:
            client = create_app(Path(directory) / "raw.jsonl",
                                command_output=Path(directory) / "commands.jsonl").test_client()
            for index in range(16):
                response = client.post("/api/v1/observations", json=sample(index, uid=0))
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json["robot_command"])

    def test_engage_is_issued_once_per_logical_lock(self):
        with TemporaryDirectory() as directory:
            client = create_app(Path(directory) / "raw.jsonl",
                                command_output=Path(directory) / "commands.jsonl",
                                session_id="engage").test_client()
            for index in range(13):
                client.post("/api/v1/observations", json=sample(index, distance=1.0))
            observation = sample(13, distance=1.0)
            payload = client.post("/api/v1/observations", json=observation).json
            self.assertEqual(payload["robot_command"]["action"], "ENGAGE")
            executor = FakeCommandExecutor(monotonic_us=lambda: observation["timestamp"])
            for event in executor.accept(payload, observation):
                self.assertEqual(client.post("/api/v1/execution-events", json=event).status_code, 200)
            for index in range(14, 20):
                result = client.post("/api/v1/observations", json=sample(index, distance=1.0)).json
                self.assertEqual(result["target_lock"]["status"], "LOCKED")
                self.assertIsNone(result["robot_command"])

    def test_offline_replay_matches_live_command_proposals(self):
        import contextlib
        import io
        import json

        with TemporaryDirectory() as directory:
            root = Path(directory)
            recording = root / "pilot.jsonl"
            recording.write_text("".join(json.dumps(sample(i)) + "\n" for i in range(18)))
            output = root / "commands.jsonl"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(replay_commands([str(recording), "--output", str(output)]), 0)
            replay = [json.loads(line) for line in output.read_text().splitlines()]
            pipeline = SocialPipeline(recording_session(recording))
            live = [pipeline.process(sample(i)) for i in range(18)]
            self.assertEqual(replay, [
                {"source_state_id": snapshot["social_state"]["state_id"],
                 "command": snapshot["robot_command"]} for snapshot in live
            ])
