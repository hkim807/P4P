"""Shared offline/live processing, failures, concurrency and CLI compatibility."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.pipeline import TrackingPipeline
from app.server import create_app
from app.track import main
from tests.test_tracks import observation


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = Path(self.temp.name) / "raw.jsonl"
        self.trace = Path(self.temp.name) / "tracks.jsonl"
        self.app = create_app(self.raw, tracking_output=self.trace, session_id="test")
        self.client = self.app.test_client()

    def test_live_trace_equals_offline_pipeline_and_preserves_raw(self):
        offline = TrackingPipeline("test")
        frames = [observation(0, 17), observation(100_000), observation(200_000, 17)]
        expected = [offline.process(f) for f in frames]
        for frame in frames:
            response = self.client.post("/api/v1/observations", json=frame)
            self.assertEqual(response.json["processing_status"], "complete")
        self.assertEqual([json.loads(line) for line in self.raw.read_text().splitlines()], frames)
        self.assertEqual([json.loads(line) for line in self.trace.read_text().splitlines()], expected)

    def test_raw_failure_does_not_advance_tracking(self):
        with patch("app.recording.RecordingWriter.write", side_effect=OSError("disk")):
            with self.assertLogs("app.server", level="ERROR"):
                response = self.client.post("/api/v1/observations", json=observation(0, 17))
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.trace.exists())
        response = self.client.post("/api/v1/observations", json=observation(0, 17))
        self.assertEqual(response.json["tracking"]["frame_sequence"], 1)

    def test_trace_failure_keeps_raw_and_reports_separate_processing_status(self):
        with patch("app.pipeline.TrackTraceWriter.write", side_effect=OSError("disk")):
            with self.assertLogs("app.server", level="ERROR"):
                response = self.client.post("/api/v1/observations", json=observation(0, 17))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["accepted"])
        self.assertEqual(response.json["processing_status"], "failed")
        self.assertEqual(response.json["processing_stage"], "trace_write")
        self.assertEqual(len(self.raw.read_text().splitlines()), 1)
        self.assertEqual(self.client.post("/api/v1/observations", json=observation(0, 17)).status_code, 409)
        result = self.client.post("/api/v1/observations", json=observation(1, 17))
        self.assertEqual(result.json["tracking"]["frame_sequence"], 2)

    def test_tracking_failure_keeps_raw_without_claiming_success(self):
        with patch("app.state.tracks.TrackManager.update", side_effect=ValueError("failed")):
            with self.assertLogs("app.server", level="ERROR"):
                response = self.client.post("/api/v1/observations", json=observation(0, 17))
        self.assertTrue(response.json["accepted"])
        self.assertEqual(response.json["processing_stage"], "update")
        self.assertFalse(self.trace.exists())

    def test_duplicate_requests_do_not_duplicate_trace(self):
        for expected in (200, 409):
            self.assertEqual(self.client.post("/api/v1/observations", json=observation(0, 17)).status_code, expected)
        self.assertEqual(len(self.trace.read_text().splitlines()), 1)

    def test_concurrent_requests_keep_raw_and_tracking_order_identical(self):
        def send(timestamp):
            with self.app.test_client() as client:
                return client.post("/api/v1/observations", json=observation(timestamp, 17)).status_code
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(send, range(60)))
        self.assertTrue(all(s in (200, 409) for s in statuses))
        raw_times = [json.loads(line)["timestamp"] for line in self.raw.read_text().splitlines()]
        traces = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual([s["robot_timestamp_us"] for s in traces], raw_times)
        self.assertEqual([s["frame_sequence"] for s in traces], list(range(1, len(raw_times) + 1)))
        self.assertEqual(len(raw_times), statuses.count(200))

    def test_path_collisions_and_existing_trace_are_refused(self):
        with self.assertRaises(ValueError):
            create_app(self.raw, tracking_output=self.raw)
        self.trace.write_text("preserve")
        with self.assertRaises(FileExistsError):
            create_app(self.raw, tracking_output=self.trace)
        self.assertEqual(self.trace.read_text(), "preserve")

    def test_cli_replays_and_refuses_to_overwrite_raw_or_output(self):
        self.raw.write_text(json.dumps(observation(0, 17)) + "\n")
        with contextlib.redirect_stderr(io.StringIO()) as summary:
            self.assertEqual(main([str(self.raw), "--output", str(self.trace)]), 0)
        self.assertEqual(json.loads(summary.getvalue())["frames"], 1)
        before = self.raw.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.raw), "--output", str(self.trace)]), 1)
            self.assertEqual(main([str(self.raw), "--output", str(self.raw)]), 1)
        self.assertEqual(self.raw.read_bytes(), before)

    def test_cli_invalid_recording_creates_no_trace(self):
        self.raw.write_text(json.dumps(observation(0, 17)) + "\n{\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.raw), "--output", str(self.trace)]), 1)
        self.assertFalse(self.trace.exists())

    def test_cli_session_keys_are_stable_per_file_and_distinct_across_files(self):
        self.raw.write_text(json.dumps(observation(0, 17)) + "\n")
        other = self.raw.with_name("other.jsonl")
        other.write_text(self.raw.read_text())
        sessions = []
        for source in (self.raw, self.raw, other):
            with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(source)]), 0)
            sessions.append(json.loads(output.getvalue())["session_id"])
        self.assertEqual(sessions[0], sessions[1])
        self.assertNotEqual(sessions[0], sessions[2])
