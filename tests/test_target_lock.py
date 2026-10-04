"""Logical lock transitions, replay/live parity, and robot response gating."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

from app.lock import main as replay_lock
from app.policy.target_lock import LockConfig
from app.server import create_app
from app.social import recording_session
from app.social_pipeline import SocialPipeline
from robot.navel_client.decision_dispatch import DecisionRejected, parse_decision
from robot.navel_client.head_focus import HeadFocusController
from robot.navel_client.main import _send_observations
from robot.navel_client.transport import ObservationResponse
import asyncio
from tests.test_social_state import sample


def no_person(i):
    frame = sample(i)
    frame["people"] = []
    return frame


class TargetLockTests(unittest.TestCase):
    def test_same_track_returns_but_new_uid_stays_tentative(self):
        pipeline = SocialPipeline("lock-test")
        first = pipeline.process(sample(0))["target_lock"]
        lock_id = first["lock_id"]
        self.assertEqual(first["status"], "LOCKED")
        self.assertEqual(first["target_uid"], 17)
        missing = pipeline.process(no_person(1))["target_lock"]
        self.assertEqual(missing["status"], "MISSING")
        self.assertEqual(missing["effective_decision"]["decision"], "DEFER")
        self.assertEqual(missing["effective_decision"]["reason_code"], "LOCKED_TARGET_MISSING")
        same = pipeline.process(sample(2))["target_lock"]
        self.assertEqual(same["status"], "LOCKED")
        self.assertEqual(same["lock_id"], lock_id)
        self.assertIn("REACQUIRED", same["events"])
        changed = pipeline.process(sample(3, uid=18))["target_lock"]
        self.assertEqual(changed["status"], "TENTATIVE_RETURN")
        self.assertEqual(changed["lock_id"], lock_id)
        self.assertEqual(changed["target_uid"], 17)
        self.assertEqual(changed["candidate_uid"], 18)
        self.assertIsNone(changed["effective_decision"]["target_uid"])
        self.assertEqual(changed["effective_decision"]["reason_code"], "IDENTITY_UNRESOLVED")

    def test_new_epoch_and_crowd_never_rebind_old_lock(self):
        pipeline = SocialPipeline("lock-test")
        first = pipeline.process(sample(0))["target_lock"]
        changed_epoch = pipeline.process(sample(8))["target_lock"]
        self.assertEqual(changed_epoch["status"], "TENTATIVE_RETURN")
        self.assertEqual(changed_epoch["target_track_epoch"], 1)
        self.assertNotEqual(changed_epoch["candidate_track_epoch"], 1)
        crowd = sample(9, uid=18)
        crowd["people"].append({"uid": 19, "distance_m": 2.0, "gaze_overlap": 0.95})
        ambiguous = pipeline.process(crowd)["target_lock"]
        self.assertEqual(ambiguous["status"], "AMBIGUOUS")
        self.assertEqual(ambiguous["lock_id"], first["lock_id"])
        self.assertIsNone(ambiguous["candidate_uid"])

    def test_timeout_cooldown_then_new_lock(self):
        pipeline = SocialPipeline("lock-test")
        old = pipeline.process(sample(0))["target_lock"]
        tentative = pipeline.process(sample(1, uid=18))["target_lock"]
        self.assertEqual(tentative["status"], "TENTATIVE_RETURN")
        timed_out = pipeline.process(sample(20, uid=18))["target_lock"]
        self.assertEqual(timed_out["status"], "COOLDOWN")
        self.assertIn("RELEASED_MISSING_TIMEOUT", timed_out["events"])
        self.assertIsNone(timed_out["target_uid"])
        self.assertEqual(timed_out["effective_decision"]["decision"], "DEFER")
        still_cooling = pipeline.process(sample(29, uid=18))["target_lock"]
        self.assertEqual(still_cooling["status"], "COOLDOWN")
        new = pipeline.process(sample(30, uid=18))["target_lock"]
        self.assertEqual(new["status"], "LOCKED")
        self.assertNotEqual(new["lock_id"], old["lock_id"])
        self.assertEqual(new["target_uid"], 18)

    def test_valid_no_attention_releases_lock(self):
        pipeline = SocialPipeline("no-attention")
        for i in range(21):
            locked = pipeline.process(sample(i))["target_lock"]
        self.assertEqual(locked["status"], "LOCKED")
        for i in range(21, 70):
            state = pipeline.process(sample(i, gaze=0.0))["target_lock"]
            if "RELEASED_BY_POLICY" in state["events"]:
                break
        else:
            self.fail("valid no-attention evidence did not release the lock")
        self.assertEqual(state["status"], "COOLDOWN")
        self.assertEqual(state["effective_decision"]["decision"], "DEFER")

    def test_live_response_and_replay_trace_match(self):
        frames = [sample(0), no_person(1), sample(2, uid=18), sample(3),
                  no_person(4), sample(25, uid=18), sample(36, uid=18)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_text("".join(json.dumps(frame) + "\n" for frame in frames))
            session = recording_session(source)
            app = create_app(root / "live-raw.jsonl", lock_output=root / "live-lock.jsonl",
                             session_id=session)
            with app.test_client() as client:
                responses = [client.post("/api/v1/observations", json=frame) for frame in frames]
            self.assertTrue(all(response.status_code == 200 for response in responses))
            self.assertTrue(all("target_lock" in response.json for response in responses))
            output = root / "replay-lock.jsonl"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(replay_lock([str(source), "--output", str(output)]), 0)
            self.assertEqual(output.read_text(), (root / "live-lock.jsonl").read_text())
            self.assertEqual([response.json["target_lock"] for response in responses],
                             [json.loads(line) for line in output.read_text().splitlines()])

    def test_robot_uses_lock_decision_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(Path(directory) / "raw.jsonl",
                                lock_output=Path(directory) / "locks.jsonl",
                                session_id="live-lock").test_client()
            for i in range(21):
                frame = sample(i)
                response = client.post("/api/v1/observations", json=frame)
            self.assertEqual(response.json["policy_decision"]["decision"], "APPROACH")
            parsed = parse_decision(response.json, frame, frame["timestamp"] + 1000, 1_000_000)
            self.assertEqual(parsed.decision, "APPROACH")
            self.assertIsNotNone(parsed.lock_id)

            missing = no_person(21)
            response = client.post("/api/v1/observations", json=missing)
            self.assertEqual(response.json["policy_decision"]["decision"], "CONTINUE")
            parsed = parse_decision(response.json, missing, missing["timestamp"] + 1000, 1_000_000)
            self.assertEqual((parsed.decision, parsed.reason_code), ("DEFER", "LOCKED_TARGET_MISSING"))

            candidate = sample(22, uid=18)
            response = client.post("/api/v1/observations", json=candidate)
            parsed = parse_decision(response.json, candidate, candidate["timestamp"] + 1000, 1_000_000)
            self.assertEqual(parsed.decision, "DEFER")
            self.assertEqual(response.json["target_lock"]["candidate_uid"], 18)
            forged = json.loads(json.dumps(response.json))
            forged["target_lock"]["effective_decision"].update(
                decision="ENGAGE", target_uid=18, target_track_epoch=2)
            with self.assertRaises(DecisionRejected):
                parse_decision(forged, candidate, candidate["timestamp"] + 1000, 1_000_000)
            for field, value in (("status", []), ("robot_timestamp_us", True)):
                malformed = json.loads(json.dumps(response.json))
                malformed["target_lock"][field] = value
                with self.subTest(field=field), self.assertRaises(DecisionRejected):
                    parse_decision(malformed, candidate, candidate["timestamp"] + 1000, 1_000_000)
            malformed = json.loads(json.dumps(response.json))
            malformed["target_lock"]["effective_decision"]["decision"] = []
            with self.assertRaises(DecisionRejected):
                parse_decision(malformed, candidate, candidate["timestamp"] + 1000, 1_000_000)

    def test_config_file_matches_defaults(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(LockConfig.from_file(root / "config/target-lock.json"), LockConfig())

    def test_stream_gap_releases_even_if_tracker_retains_same_uid(self):
        from app.state.tracks import TrackConfig
        pipeline = SocialPipeline("gap", track_config=TrackConfig(missing_grace_s=5))
        first = pipeline.process(sample(0))["target_lock"]
        after_gap = pipeline.process(sample(21))["target_lock"]
        self.assertEqual(after_gap["status"], "COOLDOWN")
        self.assertIn("RELEASED_STREAM_GAP", after_gap["events"])
        self.assertEqual(after_gap["lock_id"], first["lock_id"])


class HeadLockIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepted_response_pins_robot_head_to_server_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(Path(directory) / "raw.jsonl",
                                lock_output=Path(directory) / "lock.jsonl").test_client()
            commands = []
            robot = type("Robot", (), {"look_at_person": lambda _, uid, head: commands.append((uid, head))})()
            focus = HeadFocusController(robot, grace_s=0.01)
            observation = sample(0)
            observation["timestamp"] = time.monotonic_ns() // 1000
            focus.observe(type("Packet", (), {"persons": [type("Person", (), {"uid": 17})()]})())

            class Transport:
                def send(self, frame):
                    result = client.post("/api/v1/observations", json=frame)
                    return ObservationResponse(result.status_code, result.json)

            queue = asyncio.Queue(maxsize=1)
            queue.put_nowait(observation)
            sender = asyncio.create_task(_send_observations(
                queue, Transport(), minimum_send_interval_s=0, print_only=False,
                head_focus=focus,
            ))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    await asyncio.wait_for(queue.join(), 1)
                self.assertEqual(focus.server_lock_uid, 17)
                focus.last_seen_at -= 1
                focus.observe(type("Packet", (), {"persons": [type("Person", (), {"uid": 18})()]})())
                self.assertEqual(commands, [(17, 0.5)])
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
