"""All seven supplied Navel recordings are required regression inputs."""
import json
from pathlib import Path
import tempfile
import unittest

from app.pipeline import TrackingPipeline, trace_line
from app.recording import read_frames
from app.server import create_app
from app.state.tracks import TrackConfig
from app.validate_tracking import audit_recording


ROOT = Path(__file__).resolve().parents[1]
RECORDINGS = sorted((ROOT / "var/recordings").glob("0[1-7]_*.jsonl"))


class RecordingTrackingTests(unittest.TestCase):
    def test_all_seven_recordings_preserve_source_samples_and_replay_deterministically(self):
        self.assertEqual(len(RECORDINGS), 7, "The seven pilot recordings are required")
        reports = []
        for path in RECORDINGS:
            with self.subTest(recording=path.name):
                reports.append(audit_recording(path, TrackConfig()))
        self.assertEqual(sum(r["frames"] for r in reports), 682)
        for index in (2, 4):
            self.assertEqual(reports[index]["events"], {"ACQUIRED": 1})
            self.assertEqual(reports[index]["peak_active_tracks"], 1)

    def test_http_receiver_and_offline_replay_match_for_every_pilot_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in RECORDINGS:
                with self.subTest(recording=path.name):
                    raw = Path(directory) / path.name
                    trace = raw.with_suffix(".tracks.jsonl")
                    app = create_app(raw, tracking_output=trace, session_id=path.stem)
                    offline = TrackingPipeline(path.stem)
                    expected = []
                    with app.test_client() as client:
                        for frame in read_frames(path):
                            expected.append(trace_line(offline.process(frame)))
                            response = client.post("/api/v1/observations", json=frame)
                            self.assertEqual(response.status_code, 200)
                            self.assertEqual(response.json["processing_status"], "complete")
                    self.assertEqual(trace.read_text(), "".join(expected))
                    self.assertEqual(list(read_frames(raw)), list(read_frames(path)))

    def test_history_and_grace_sensitivity_retain_contract(self):
        # This tests software behavior under alternate settings, not calibrated accuracy.
        for history, grace in ((1, 0.25), (3, 0.75), (3, 1.5)):
            with self.subTest(history=history, grace=grace):
                result = audit_recording(RECORDINGS[-1], TrackConfig(history_window_s=history, missing_grace_s=grace))
                self.assertTrue(result["checks_passed"])
