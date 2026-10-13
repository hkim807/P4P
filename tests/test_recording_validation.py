"""Loss detection, motion boundary preservation and existing replay compatibility."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.recording_validation import ValidationConfig, inspect_recording, trim_recording
from app.replay.llm_inputs import iter_replay_states
from scripts.validate_trim_recordings import main


def capture(samples=8, moving_from=3):
    rows = []
    for index in range(samples):
        for stream in ("locomotion", "perception"):
            timestamp = 1_000_000 + index * 100_000 + (1_000 if stream == "perception" else 0)
            packet = ({"time": timestamp, "persons": []} if stream == "perception" else
                      {"odometry": {"time": timestamp,
                                    "velocity": {"linear_x": 0.1 if index >= moving_from else 0.0,
                                                 "angular_z": 0.0}}, "distances": {}})
            rows.append({"capture_version": 1, "session_id": "test-session", "stream": stream,
                         "sequence": index + 1, "received_monotonic_us": timestamp,
                         "received_unix_us": timestamp + 1_000_000, "packet": packet})
    return rows


class RecordingValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "scenario-sdk.jsonl"
        self.output = self.root / "trimmed-sdk.jsonl"

    def inspect(self, rows, config=None):
        self.source.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return inspect_recording(self.source, config)

    def assert_issue(self, inspection, code):
        self.assertEqual(inspection.report["validation"], "INVALID")
        self.assertIn(code, inspection.report["issue_counts"])
        with self.assertRaises(ValueError):
            trim_recording(inspection, self.output)
        self.assertFalse(self.output.exists())

    def test_trim_starts_with_motion_and_retains_packets_times_and_original_file(self):
        rows = capture()
        inspection = self.inspect(rows)
        original_bytes = self.source.read_bytes()
        report = trim_recording(inspection, self.output)
        trimmed = [json.loads(line) for line in self.output.read_text().splitlines()]
        self.assertEqual(self.source.read_bytes(), original_bytes)
        self.assertEqual(report["motion_start"]["offset_s"], 0.3)
        self.assertEqual(report["removed_packets"], 6)
        self.assertEqual(trimmed[0]["stream"], "locomotion")
        self.assertEqual(trimmed[0]["packet"]["odometry"]["velocity"]["linear_x"], 0.1)
        self.assertEqual(report["original_first_kept_sequences"], {"locomotion": 4, "perception": 4})
        for trimmed_row, original_row in zip(trimmed, rows[6:]):
            self.assertEqual({**trimmed_row, "sequence": original_row["sequence"]}, original_row)
        self.assertEqual(inspect_recording(self.output).report["validation"], "VALID")
        states = list(iter_replay_states(self.output, 'sdk'))
        self.assertEqual(len(states), 5)
        self.assertTrue(all(item.state.robot.linear_velocity == 0.1 for item in states))

    def test_missing_received_packet_is_rejected(self):
        rows = capture()
        del rows[4]
        inspection = self.inspect(rows)
        self.assert_issue(inspection, "SEQUENCE_GAP")
        self.assertEqual(inspection.report["streams"]["locomotion"]["missing_capture_packets"], 1)

    def test_missing_initial_sequence_is_rejected(self):
        self.assert_issue(self.inspect(capture()[2:]), "SEQUENCE_GAP")

    def test_skipped_sdk_sample_detected_even_with_continuous_capture_sequences(self):
        rows = capture()
        for row in rows:
            if row["stream"] == "perception" and row["sequence"] >= 4:
                row["packet"]["time"] += 100_000
        inspection = self.inspect(rows)
        self.assert_issue(inspection, "SDK_TIMESTAMP_GAP")
        self.assertEqual(inspection.report["streams"]["perception"]["missing_capture_packets"], 0)
        self.assertEqual(inspection.report["streams"]["perception"]["estimated_missing_sdk_samples"], 1)

    def test_receipt_gap_detected_independently_of_native_sdk_clock(self):
        rows = capture()
        for row in rows[6:]:
            row["received_monotonic_us"] += 500_000
        self.assert_issue(self.inspect(rows), "RECEIPT_GAP")

    def test_duplicate_packet_and_backwards_time_are_rejected(self):
        rows = capture()
        rows.insert(4, deepcopy(rows[2]))
        inspection = self.inspect(rows)
        self.assert_issue(inspection, "SEQUENCE_ORDER")
        self.assertIn("RECEIPT_ORDER", inspection.report["issue_counts"])
        self.assertIn("SDK_TIME_ORDER", inspection.report["issue_counts"])

    def test_missing_stream_or_payload_is_rejected(self):
        with self.subTest(kind="missing_stream"):
            self.assert_issue(self.inspect([row for row in capture() if row["stream"] == "locomotion"]),
                              "MISSING_STREAM")
        with self.subTest(kind="empty_perception"):
            rows = capture()
            rows[1]["packet"] = {}
            self.assert_issue(self.inspect(rows), "MISSING_PERCEPTION_DATA")
        with self.subTest(kind="missing_velocity"):
            rows = capture()
            rows[0]["packet"]["odometry"]["velocity"]["linear_x"] = None
            self.assert_issue(self.inspect(rows), "MISSING_VELOCITY")

    def test_stream_stopping_early_is_rejected(self):
        rows = [row for row in capture() if row["stream"] != "locomotion" or row["sequence"] < 5]
        self.assert_issue(self.inspect(rows), "STREAM_END_GAP")

    def test_unequal_independent_stream_counts_are_allowed(self):
        rows = capture()[:-1]
        self.assertEqual(self.inspect(rows).report["validation"], "VALID")

    def test_empty_and_corrupt_files_are_rejected(self):
        self.assert_issue(self.inspect([]), "EMPTY_CAPTURE")
        self.source.write_text('{"stream":\n')
        self.assert_issue(inspect_recording(self.source), "INVALID_PACKET")

    def test_duplicate_json_keys_and_nonfinite_json_are_rejected(self):
        for bad in ('{"a":1,"a":2}\n', '{"a": NaN}\n'):
            self.source.write_text(bad)
            self.assert_issue(inspect_recording(self.source), "INVALID_PACKET")

    def test_session_changes_are_rejected(self):
        rows = capture()
        rows[-1]["session_id"] = "another-session"
        self.assert_issue(self.inspect(rows), "SESSION_CHANGED")

    def test_stationary_or_noise_only_recording_is_not_trimmed(self):
        rows = capture(moving_from=99)
        for row in rows:
            if row["stream"] == "locomotion":
                row["packet"]["odometry"]["velocity"]["linear_x"] = 0.012
        inspection = self.inspect(rows)
        self.assertEqual(inspection.report["validation"], "VALID")
        self.assertEqual(inspection.report["status"], "NO_MOVEMENT")
        with self.assertRaises(ValueError):
            trim_recording(inspection, self.output)

    def test_yaw_and_reverse_movement_are_detected(self):
        for velocity in ({"linear_x": -0.1, "angular_z": 0.0},
                         {"linear_x": 0.0, "angular_z": -0.04}):
            rows = capture(moving_from=99)
            rows[6]["packet"]["odometry"]["velocity"] = velocity
            self.assertEqual(self.inspect(rows).report["motion_start"]["offset_s"], 0.3)

    def test_missing_native_times_are_explicit_warnings(self):
        rows = capture()
        del rows[1]["packet"]["time"]
        inspection = self.inspect(rows)
        self.assertEqual(inspection.report["validation"], "VALID_WITH_WARNINGS")
        self.assertIn("UNAVAILABLE_SDK_TIMING", inspection.report["issue_counts"])

    def test_trim_without_both_retained_streams_is_refused(self):
        rows = capture(moving_from=99)
        rows.pop()
        rows[-1]["packet"]["odometry"]["velocity"]["linear_x"] = 0.1
        inspection = self.inspect(rows)
        self.assertEqual(inspection.report["validation"], "VALID")
        with self.assertRaisesRegex(ValueError, "suffix lacks"):
            trim_recording(inspection, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_file_and_source_are_never_overwritten(self):
        inspection = self.inspect(capture())
        self.output.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):
            trim_recording(inspection, self.output)
        self.assertEqual(self.output.read_bytes(), b'existing')
        with self.assertRaisesRegex(ValueError, "original"):
            trim_recording(inspection, self.source)

    def test_cli_continues_after_invalid_file_and_reports_every_input(self):
        self.inspect(capture())
        bad = self.root / 'bad-sdk.jsonl'
        bad.write_text('bad json\n')
        output = self.root / 'prepared'
        self.assertEqual(main([str(self.root), '--output-dir', str(output)]), 1)
        report = json.loads((output / 'report.json').read_text())
        self.assertEqual(report['status_counts'], {'INVALID': 1, 'TRIMMED': 1})
        self.assertEqual(len(report['recordings']), 2)
        self.assertTrue((output / 'trimmed' / self.source.name).is_file())
        self.assertFalse((output / 'trimmed' / bad.name).exists())
        self.assertEqual(main([str(self.source), '--output-dir', str(output)]), 2)

    def test_invalid_configuration_is_rejected(self):
        for config in ({'max_gap_s': 0}, {'source_gap_factor': 1},
                       {'linear_threshold_mps': -1}, {'angular_threshold_rps': float('nan')}):
            with self.assertRaises(ValueError):
                ValidationConfig(**config)


if __name__ == '__main__':
    unittest.main()
