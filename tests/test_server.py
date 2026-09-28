"""Raw contract validation and JSONL receiver behavior."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.schema import SCHEMA_PATH, render_schema
from app.server import create_app
from tests.fixtures import frame


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "observations.jsonl"
        self.app = create_app(self.output)
        self.client = self.app.test_client()

    def test_health_needs_no_model_or_robot(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")

    def test_accepts_and_appends_raw_frames(self):
        for timestamp in [100, 101]:
            payload = frame()
            payload["timestamp"] = timestamp
            response = self.client.post("/api/v1/observations", json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {"accepted": True, "timestamp": timestamp, "people_count": 1})
        stored = [json.loads(line) for line in self.output.read_text().splitlines()]
        self.assertEqual([f["timestamp"] for f in stored], [100, 101])
        self.assertNotIn("optional_relative_head_position", stored[0]["people"][0])

    def test_nulls_remain_nulls_in_storage(self):
        payload = frame()
        payload["robot"] = {"linear_velocity": None, "angular_velocity": None}
        payload["safety"] = {"lidar": [None, 0], "sonar": None}
        payload["people"][0].update(distance_m=None, gaze_overlap=None)
        self.assertEqual(self.client.post("/api/v1/observations", json=payload).status_code, 200)
        self.assertEqual(json.loads(self.output.read_text()), payload)

    def test_duplicate_and_backward_timestamps_are_not_recorded(self):
        payload = frame()
        self.assertEqual(self.client.post("/api/v1/observations", json=payload).status_code, 200)
        for timestamp in [payload["timestamp"], payload["timestamp"] - 1]:
            response = self.client.post("/api/v1/observations", json={**payload, "timestamp": timestamp})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json["error"], "timestamp_out_of_order")
            self.assertFalse(response.json["accepted"])
        self.assertEqual(len(self.output.read_text().splitlines()), 1)

    def test_existing_recording_is_not_modified_on_startup(self):
        self.output.write_text("existing recording\n")
        with self.assertRaises(FileExistsError):
            create_app(self.output)
        self.assertEqual(self.output.read_text(), "existing recording\n")

    def test_failed_write_does_not_advance_timestamp(self):
        with patch("app.recording.Path.open", side_effect=OSError("disk unavailable")):
            with self.assertLogs("app.server", level="ERROR"):
                self.assertEqual(self.client.post("/api/v1/observations", json=frame()).status_code, 503)
        self.assertEqual(self.client.post("/api/v1/observations", json=frame()).status_code, 200)

    def test_rejects_missing_fields_and_old_contract(self):
        for payload in [{}, {"timestamp_us": 100, "humans": []}, {**frame(), "safety": {}}]:
            with self.subTest(payload=payload):
                response = self.client.post("/api/v1/observations", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json["accepted"])
        self.assertFalse(self.output.exists())

    def test_rejects_invalid_person_and_duplicate_ids(self):
        for overrides in [{"uid": True}, {"uid": "17"}, {"distance_m": -1}, {"gaze_overlap": 1.1}]:
            with self.subTest(overrides=overrides):
                payload = frame()
                payload["people"][0].update(overrides)
                self.assertEqual(self.client.post("/api/v1/observations", json=payload).status_code, 400)
        payload = frame()
        payload["people"] *= 2
        self.assertEqual(self.client.post("/api/v1/observations", json=payload).status_code, 400)

    def test_rejects_nonfinite_and_coerced_velocity_values(self):
        for value in [float("nan"), float("inf"), True, "0.2"]:
            with self.subTest(value=value):
                payload = frame()
                payload["robot"]["linear_velocity"] = value
                response = self.client.post("/api/v1/observations", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json["accepted"])

    def test_rejects_invalid_timestamp_and_negative_sensor_ranges(self):
        for value in [True, -1, 0.1, "100"]:
            payload = frame()
            payload["timestamp"] = value
            self.assertEqual(self.client.post("/api/v1/observations", json=payload).status_code, 400)
        payload = frame()
        payload["safety"]["lidar"] = [-1, 1]
        self.assertEqual(self.client.post("/api/v1/observations", json=payload).status_code, 400)

    def test_malformed_json_and_wrong_content_type_return_json_errors(self):
        for content, content_type, status in [("{", "application/json", 400), ("{}", "text/plain", 415)]:
            response = self.client.post("/api/v1/observations", data=content, content_type=content_type)
            self.assertEqual(response.status_code, status)
            self.assertFalse(response.json["accepted"])

    def test_oversized_body_is_rejected(self):
        self.app.config["MAX_CONTENT_LENGTH"] = 10
        response = self.client.post("/api/v1/observations", json=frame())
        self.assertEqual(response.status_code, 413)
        self.assertFalse(response.json["accepted"])

    def test_storage_failure_is_not_acknowledged_as_accepted(self):
        with patch("app.server.Path.open", side_effect=OSError("disk unavailable")):
            with self.assertLogs("app.server", level="ERROR"):
                response = self.client.post("/api/v1/observations", json=frame())
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json["accepted"])

    def test_committed_schema_matches_contract(self):
        self.assertEqual(SCHEMA_PATH.read_text(), render_schema())


if __name__ == "__main__":
    unittest.main()
