"""Recording replay, live parity, artifact isolation and derived-stage errors."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.pipeline import trace_line
from app.recording import read_frames
from app.server import create_app
from app.social import main, recording_session
from app.social_pipeline import SocialPipeline
from app.state.social_models import TemporalConfig
from app.validate_social import audit_frames
from tests.test_social_state import sample

ROOT = Path(__file__).resolve().parents[1]
RECORDINGS = sorted((ROOT / "var/recordings").glob("0[1-7]_*.jsonl"))


class SocialIntegrationTests(unittest.TestCase):
    def test_all_recordings_schema_validity_and_speed_determinism(self):
        self.assertEqual(len(RECORDINGS), 7)
        reports = []
        for path in RECORDINGS:
            with self.subTest(path=path.name):
                reports.append(audit_frames(list(read_frames(path)), recording_session(path)))
        self.assertEqual(sum(r["frames"] for r in reports), 682)
        self.assertEqual(sum(r["observed_person_states"] for r in reports), 544)
        self.assertGreater(reports[2]["observed_gaze_states"].get("SUSTAINED", 0), 0)
        # This exposes the known sensor/label mismatch rather than forcing the filename's label.
        self.assertGreater(reports[4]["observed_gaze_states"].get("SUSTAINED", 0), 0)

    def test_all_recordings_live_receiver_matches_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in RECORDINGS:
                with self.subTest(path=path.name):
                    raw = Path(directory) / path.name
                    social = raw.with_suffix(".social.jsonl")
                    app = create_app(raw, social_output=social, session_id=path.stem)
                    pipeline = SocialPipeline(path.stem)
                    expected = []
                    with app.test_client() as client:
                        for frame in read_frames(path):
                            state = pipeline.process(frame)["social_state"]
                            expected.append(trace_line(state))
                            response = client.post("/api/v1/observations", json=frame)
                            self.assertEqual(response.status_code, 200)
                            self.assertEqual(response.json["social_state"], state)
                    self.assertEqual(social.read_text(), "".join(expected))
                    self.assertEqual(list(read_frames(raw)), list(read_frames(path)))

    def test_cli_refuses_existing_paths_and_produces_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/"raw.jsonl", Path(directory)/"social.jsonl"
            source.write_text("".join(json.dumps(sample(i))+"\n" for i in range(25)))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(source), "--output", str(output)]), 0)
                self.assertEqual(main([str(source), "--output", str(source)]), 1)
                self.assertEqual(main([str(source), "--output", str(output)]), 1)
            states = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(states), 25)
            self.assertEqual(states[-1]["people"][0]["gaze_state"], "SUSTAINED")

    def test_cli_bad_input_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/"raw.jsonl", Path(directory)/"social.jsonl"
            source.write_text("{}\n")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(source), "--output", str(output)]), 1)
            self.assertFalse(output.exists())

    def test_social_failure_preserves_raw_and_reports_failed_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, social = Path(directory)/"raw.jsonl", Path(directory)/"social.jsonl"
            app = create_app(raw, social_output=social)
            for target, stage in (("app.state.estimator.SocialStateEstimator.update", "social_estimation"),
                                  ("app.pipeline.TrackTraceWriter.write", "social_trace_write")):
                with patch(target, side_effect=ValueError("test failure")), self.assertLogs("app.server", "ERROR"):
                    response = app.test_client().post("/api/v1/observations", json=sample(0 if stage == "social_estimation" else 1))
                self.assertTrue(response.json["accepted"])
                self.assertEqual(response.json["processing_status"], "failed")
                self.assertEqual(response.json["processing_stage"], stage)
            self.assertEqual(len(list(read_frames(raw))), 2)

    def test_duplicate_frame_cannot_advance_social_state(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, social = Path(directory)/"raw.jsonl", Path(directory)/"social.jsonl"
            client = create_app(raw, social_output=social).test_client()
            self.assertEqual(client.post("/api/v1/observations", json=sample(0)).status_code, 200)
            before = social.read_bytes()
            self.assertEqual(client.post("/api/v1/observations", json=sample(0)).status_code, 409)
            self.assertEqual(social.read_bytes(), before)

    def test_trace_paths_must_be_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"raw.jsonl"
            with self.assertRaises(ValueError):
                create_app(path, social_output=path)

    def test_effective_config_file_matches_defaults(self):
        self.assertEqual(TemporalConfig.from_file(ROOT/"config/temporal-state.json"), TemporalConfig())
