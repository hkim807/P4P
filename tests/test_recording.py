"""Recording boundaries, ordered data, replay timing, and CLI failures."""

import contextlib
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.domain.models import RawObservationFrame
from app.recording import RecordingWriter, TimestampOrderError, read_frames
from app.replay import main, replay
from tests.fixtures import frame


class RecordingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "recording.jsonl"

    def write_lines(self, *payloads):
        self.path.write_text("\n".join(json.dumps(p) for p in payloads) + "\n")

    def test_reader_preserves_missing_optional_fields_and_explicit_nulls(self):
        payload = frame()
        payload["people"][0]["gaze_overlap"] = None
        payload["safety"]["sonar"] = [None, 0.0, 1.2]
        self.write_lines(payload)
        self.assertEqual(list(read_frames(self.path)), [payload])

    def test_error_identifies_bad_line_and_stops_at_invalid_frame(self):
        self.path.write_text(json.dumps(frame()) + "\n{\n")
        reader = read_frames(self.path)
        self.assertEqual(next(reader), frame())
        with self.assertRaisesRegex(ValueError, r"recording.jsonl:2: invalid raw frame"):
            next(reader)

    def test_wrong_schema_and_out_of_order_input_fail(self):
        self.write_lines({"timestamp": 1})
        with self.assertRaisesRegex(ValueError, "invalid raw frame"):
            list(read_frames(self.path))
        self.write_lines(frame(), frame())
        with self.assertRaises(TimestampOrderError):
            list(read_frames(self.path))

    def test_empty_recording_fails(self):
        self.path.write_text("\n")
        with self.assertRaisesRegex(ValueError, "contains no frames"):
            list(read_frames(self.path))

    def test_duplicate_concurrent_requests_write_exactly_one_frame(self):
        writer = RecordingWriter(self.path)
        barrier = threading.Barrier(2)

        def write():
            barrier.wait(timeout=1)
            try:
                writer.write(RawObservationFrame.model_validate(frame()))
                return True
            except TimestampOrderError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: write(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(list(read_frames(self.path)), [frame()])

    def test_fresh_recordings_have_independent_timestamp_order(self):
        first = RecordingWriter(self.path)
        first.write(RawObservationFrame.model_validate(frame()))
        second_path = self.path.with_name("new-session.jsonl")
        second = RecordingWriter(second_path)
        second.write(RawObservationFrame.model_validate({**frame(), "timestamp": 1}))
        self.assertEqual(next(read_frames(second_path))["timestamp"], 1)

    def test_replay_cli_prints_jsonl_deterministically_without_network(self):
        self.write_lines(frame(), {**frame(), "timestamp": 1_200_000})
        outputs = []
        for _ in range(2):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                with patch("app.replay.ObservationTransport") as transport:
                    self.assertEqual(main([str(self.path), "--speed", "0"]), 0)
                    transport.assert_not_called()
            outputs.append(output.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual([json.loads(line) for line in outputs[0].splitlines()],
                         [frame(), {**frame(), "timestamp": 1_200_000}])

    def test_http_replay_validates_entire_file_before_sending(self):
        self.path.write_text(json.dumps(frame()) + "\n{\n")
        with contextlib.redirect_stderr(io.StringIO()), patch("app.replay.ObservationTransport") as transport:
            self.assertEqual(main([str(self.path), "--server", "http://localhost:6060"]), 1)
            transport.return_value.send.assert_not_called()

    def test_pretty_output_and_missing_file_errors(self):
        self.write_lines(frame())
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.path), "--speed", "0", "--pretty"]), 0)
        self.assertEqual(json.loads(output.getvalue()), frame())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.path.with_name("missing.jsonl"))]), 1)


class ReplayTimingTests(unittest.TestCase):
    def test_timestamp_gaps_and_speed_without_accumulating_send_delays(self):
        clock = [100.0]
        sleeps = []
        emitted = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        def emit(payload):
            emitted.append((payload["timestamp"], clock[0]))
            clock[0] += 0.02  # Simulate output/HTTP work for each frame.

        frames = [{**frame(), "timestamp": t} for t in [1_000_000, 1_200_000, 1_600_000]]
        self.assertEqual(replay(frames, emit, speed=2, monotonic=lambda: clock[0], sleep=sleep), 3)
        for (_, actual), expected in zip(emitted, [100.0, 100.1, 100.3]):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(sleeps, [0.08, 0.18]):
            self.assertAlmostEqual(actual, expected)

    def test_fast_replay_never_sleeps(self):
        frames = [frame(), {**frame(), "timestamp": 99_000_000}]
        emitted = []
        with patch("time.sleep") as sleep:
            self.assertEqual(replay(frames, emitted.append, speed=0, sleep=sleep), 2)
            sleep.assert_not_called()
        self.assertEqual(emitted, frames)

    def test_invalid_speed_fails(self):
        for speed in [-1, float("nan"), float("inf")]:
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                replay([frame()], lambda _: None, speed=speed)


if __name__ == "__main__":
    unittest.main()
