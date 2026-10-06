"""Deterministic offline camera association without model or robot calls."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.camera_recordings import CameraEvent
from app.image_matching import CameraMatcher


def decision_moment(*, session="capture-a", sdk=123_456, receipt=1_000_000):
    record = {
        "capture_version": 1, "session_id": session, "stream": "perception",
        "sequence": 7, "received_monotonic_us": receipt,
        "received_unix_us": 1_700_000_000_000_000 + receipt,
        "packet": {"time": sdk, "persons": []},
    }
    return {
        "schema_version": 1, "status": "prepared", "ok": None,
        "source_state_id": "opaque-processing:7", "session_id": "opaque-processing",
        "source_robot_timestamp_us": receipt,
        "social_state": {"state_id": "opaque-processing:7", "frozen_marker": [1, 2]},
        "social_state_json": '{"frozen_marker":[1,2],"state_id":"opaque-processing:7"}',
        "decision": None, "error": None, "raw_content": None,
        "source": {
            "input_format": "sdk", "input_path": "/original/scenario.sdk.jsonl",
            "original_capture_session": session,
            "source_session_id": session, "source_record": record,
            "line_number": 13, "record_number": 13,
            "timestamps": {
                "received_monotonic_us": receipt,
                "received_unix_us": record["received_unix_us"],
                "sdk_perception_timestamp": sdk,
                "sdk_perception_clock": "SDK PerceptionData.time; units and clock mapping unverified",
                "estimation_timestamp_us": receipt,
                "estimation_clock": "robot-host monotonic microseconds",
            },
        },
    }


class CameraMatcherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.manifest = self.root / "frames.jsonl"
        self.manifest.write_text("", encoding="utf-8")

    def frame(self, *, sequence=1, sdk=123_456, receipt=900_000,
              session="capture-a", camera="head", manifest=None, line=1,
              width=2, height=1, file=None, data=None):
        manifest = manifest or self.manifest
        relative = file or f"{camera}/{sequence:06d}-{sdk}.ppm"
        image = (manifest.parent / relative).resolve()
        image.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            data = f"P6\n{width} {height}\n255\n".encode() + bytes((1, 2, 3)) * width * height
        image.write_bytes(data)
        record = {
            "capture_version": 1, "session_id": session, "camera": camera,
            "sequence": sequence, "event": "frame",
            "received_monotonic_us": receipt,
            "received_unix_us": 1_700_000_000_000_000 + receipt,
            "timestamp_us": sdk, "width": width, "height": height, "file": relative,
        }
        return CameraEvent(manifest.resolve(), line, record, image)

    def unavailable(self, *, receipt=900_000, session="capture-a", camera="head"):
        return CameraEvent(self.manifest.resolve(), 1, {
            "capture_version": 1, "session_id": session, "camera": camera,
            "sequence": 1, "event": "unavailable",
            "received_monotonic_us": receipt,
            "received_unix_us": 1_700_000_000_000_000 + receipt,
            "reason": "camera class is absent from installed Navel SDK", "file": None,
        }, None)

    def assert_matched(self, result, method, sequence=None):
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], "matched")
        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], method)
        self.assertIsNone(result["error"])
        self.assertIsNotNone(result["frame"])
        if sequence is not None:
            self.assertEqual(result["frame"]["sequence"], sequence)

    def assert_unmatched(self, result, category):
        self.assertEqual(result["status"], "unmatched")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["method"])
        self.assertIsNone(result["frame"])
        self.assertEqual(result["error"]["category"], category)
        self.assertTrue(result["error"]["message"])

    def test_exact_match_uses_capture_session_and_selected_camera(self):
        events = [self.frame(session="other", sequence=1),
                  self.frame(camera="chest", sequence=2),
                  self.frame(sequence=3)]
        result = CameraMatcher(events).match(decision_moment())
        self.assert_matched(result, "exact_recorded_sdk_timestamp", 3)
        self.assertEqual(result["frame"]["camera"], "head")
        self.assertEqual(result["frame"]["original_capture_session"], "capture-a")

    def test_camera_selection_can_explicitly_select_chest(self):
        result = CameraMatcher([self.frame(camera="chest")], camera="chest").match(decision_moment())
        self.assert_matched(result, "exact_recorded_sdk_timestamp", 1)
        self.assertEqual(result["frame"]["camera"], "chest")

    def test_missing_original_capture_session_is_not_processing_session(self):
        row = decision_moment()
        row["source"]["original_capture_session"] = None
        self.assert_unmatched(CameraMatcher([self.frame()]).match(row), "missing_capture_session")

    def test_different_camera_capture_session_cannot_be_borrowed(self):
        result = CameraMatcher([self.frame(session="other")]).match(decision_moment())
        self.assert_unmatched(result, "missing_camera_session")

    def test_camera_with_no_recorded_frame_does_not_borrow_other_camera(self):
        result = CameraMatcher([self.frame(camera="chest")]).match(decision_moment())
        self.assert_unmatched(result, "no_camera_frame")

    def test_head_unavailable_has_explicit_failure(self):
        result = CameraMatcher([self.unavailable(), self.frame(camera="chest")]).match(decision_moment())
        self.assert_unmatched(result, "camera_unavailable")

    def test_default_does_not_use_reception_approximation(self):
        result = CameraMatcher([self.frame(sdk=111)]).match(decision_moment())
        self.assert_unmatched(result, "no_exact_timestamp")

    def test_exact_preference_over_more_recent_receipt_frame_and_age_cap(self):
        events = [self.frame(sequence=1, receipt=1),
                  self.frame(sequence=2, sdk=999, receipt=999_999)]
        result = CameraMatcher(events, allow_receipt=True, max_age_us=0).match(decision_moment())
        self.assert_matched(result, "exact_recorded_sdk_timestamp", 1)
        self.assertEqual(result["time_difference"]["value"], 0)
        self.assertEqual(result["reception_time_difference_us"], -999_999)

    def test_exact_recorded_sdk_equality_does_not_claim_exposure_sync(self):
        result = CameraMatcher([self.frame()]).match(decision_moment())
        self.assert_matched(result, "exact_recorded_sdk_timestamp")
        self.assertEqual(result["time_difference"]["value"], 0)
        self.assertIn("recorded", result["time_difference"]["units"].lower())
        limitations = str(result["limitations"]).lower()
        self.assertIn("exposure", limitations)
        self.assertIn("unverified", limitations)

    def test_exact_future_reception_is_visible_and_explicitly_limited(self):
        result = CameraMatcher([self.frame(receipt=1_000_001)]).match(decision_moment())
        self.assert_matched(result, "exact_recorded_sdk_timestamp")
        self.assertEqual(result["reception_time_difference_us"], 1)
        self.assertIn("after", str(result["limitations"]).lower())

    def test_approximation_selects_latest_past_frame_and_excludes_future(self):
        events = [self.frame(sequence=3, sdk=300, receipt=1_000_001),
                  self.frame(sequence=1, sdk=100, receipt=800_000),
                  self.frame(sequence=2, sdk=200, receipt=950_000)]
        result = CameraMatcher(events, allow_receipt=True, max_age_us=200_000).match(decision_moment())
        self.assert_matched(result, "reception_monotonic", 2)
        self.assertEqual(result["time_difference"]["value"], -50_000)
        self.assertEqual(result["time_difference"]["units"], "microseconds")
        self.assertEqual(result["reception_time_difference_us"], -50_000)

    def test_approximation_age_boundary_is_inclusive(self):
        event = self.frame(sdk=111, receipt=900_000)
        for cap, category in ((100_000, None), (99_999, "frame_too_old")):
            with self.subTest(max_age_us=cap):
                result = CameraMatcher([event], allow_receipt=True, max_age_us=cap).match(decision_moment())
                if category is None:
                    self.assert_matched(result, "reception_monotonic")
                else:
                    self.assert_unmatched(result, category)

    def test_zero_age_allows_only_equal_receipt(self):
        event = self.frame(sdk=111, receipt=1_000_000)
        result = CameraMatcher([event], allow_receipt=True, max_age_us=0).match(decision_moment())
        self.assert_matched(result, "reception_monotonic")
        self.assertEqual(result["time_difference"]["value"], 0)

    def test_approximation_cannot_choose_any_future_frame(self):
        result = CameraMatcher([self.frame(sdk=111, receipt=1_000_001)],
                               allow_receipt=True, max_age_us=1_000_000).match(decision_moment())
        self.assert_unmatched(result, "no_prior_frame")

    def test_deterministic_selection_ignores_event_iteration_order(self):
        events = [self.frame(sequence=1, sdk=111, receipt=800_000),
                  self.frame(sequence=2, sdk=222, receipt=900_000),
                  self.frame(sequence=3, sdk=333, receipt=1_100_000)]
        options = {"allow_receipt": True, "max_age_us": 200_000}
        first = CameraMatcher(events, **options).match(decision_moment())
        second = CameraMatcher(list(reversed(events)), **options).match(decision_moment())
        self.assertEqual(first, second)
        self.assert_matched(first, "reception_monotonic", 2)

    def test_raw_and_social_rows_lack_supported_capture_clock_provenance(self):
        for input_format in ("raw", "social"):
            with self.subTest(input_format=input_format):
                row = decision_moment()
                row["source"]["input_format"] = input_format
                result = CameraMatcher([self.frame()], allow_receipt=True, max_age_us=100_000).match(row)
                self.assert_unmatched(result, "unsupported_timestamp_provenance")

    def test_source_receipt_must_agree_with_original_capture_envelope(self):
        for name in ("received_monotonic_us", "received_unix_us"):
            with self.subTest(field=name):
                row = decision_moment(sdk=999)
                row["source"]["timestamps"][name] += 1
                result = CameraMatcher([self.frame()], allow_receipt=True, max_age_us=100_000).match(row)
                self.assert_unmatched(result, "unsupported_timestamp_provenance")

    def test_source_sdk_timestamp_must_agree_with_original_capture_envelope(self):
        row = decision_moment()
        row["source"]["timestamps"]["sdk_perception_timestamp"] += 1
        result = CameraMatcher([self.frame()], allow_receipt=True, max_age_us=100_000).match(row)
        self.assert_unmatched(result, "unsupported_timestamp_provenance")

    def test_source_sdk_timestamp_type_must_agree_with_original_capture_envelope(self):
        row = decision_moment()
        row["source"]["timestamps"]["sdk_perception_timestamp"] = float(
            row["source"]["source_record"]["packet"]["time"])
        result = CameraMatcher([self.frame()], allow_receipt=True, max_age_us=100_000).match(row)
        self.assert_unmatched(result, "unsupported_timestamp_provenance")

    def test_original_session_must_agree_with_sdk_capture_record(self):
        row = decision_moment()
        row["source"]["source_record"]["session_id"] = "different-capture"
        result = CameraMatcher([self.frame()], allow_receipt=True, max_age_us=100_000).match(row)
        self.assert_unmatched(result, "unsupported_timestamp_provenance")

    def test_non_perception_sdk_packet_is_not_a_decision_capture_moment(self):
        row = decision_moment()
        row["source"]["source_record"]["stream"] = "locomotion"
        self.assert_unmatched(CameraMatcher([self.frame()]).match(row), "unsupported_timestamp_provenance")

    def test_unsupported_or_malformed_sdk_capture_envelope_is_not_clock_provenance(self):
        for change in ({"capture_version": 2}, {"capture_version": True}, {"sequence": 0}):
            with self.subTest(change=change):
                row = decision_moment()
                row["source"]["source_record"].update(change)
                result = CameraMatcher([self.frame()], allow_receipt=True, max_age_us=100_000).match(row)
                self.assert_unmatched(result, "unsupported_timestamp_provenance")

    def test_estimation_and_processing_timestamps_do_not_affect_matching(self):
        row = decision_moment()
        row["source_robot_timestamp_us"] = 987_654_321
        row["source"]["timestamps"]["estimation_timestamp_us"] = 5
        row["session_id"] = "different-processing-session"
        result = CameraMatcher([self.frame()]).match(row)
        self.assert_matched(result, "exact_recorded_sdk_timestamp", 1)

    def test_no_sdk_timestamp_can_still_use_supported_receipt_clock_when_opted_in(self):
        row = decision_moment(sdk=None)
        result = CameraMatcher([self.frame()], allow_receipt=True, max_age_us=100_000).match(row)
        self.assert_matched(result, "reception_monotonic")

    def test_receipt_match_preserves_original_float_sdk_timestamp_without_conversion(self):
        row = decision_moment(sdk=123_456.5)
        before = deepcopy(row)
        result = CameraMatcher([self.frame()], allow_receipt=True, max_age_us=100_000).match(row)
        self.assert_matched(result, "reception_monotonic")
        self.assertEqual(result["frame"]["sdk_perception_timestamp"], 123_456.5)
        self.assertIsInstance(result["frame"]["sdk_perception_timestamp"], float)
        self.assertEqual(row, before)

    def test_invalid_sdk_timestamp_is_not_guessed_or_converted_for_exact_match(self):
        for sdk in (None, "123456", 123456.0, True, -1):
            with self.subTest(sdk=sdk):
                row = decision_moment(sdk=sdk)
                result = CameraMatcher([self.frame()]).match(row)
                self.assert_unmatched(result, "unsupported_timestamp_provenance")

    def test_constructor_requires_explicit_nonnegative_integer_age_for_approximation(self):
        for options in ({"allow_receipt": True},
                        {"allow_receipt": True, "max_age_us": -1},
                        {"allow_receipt": True, "max_age_us": True},
                        {"allow_receipt": True, "max_age_us": 0.5},
                        {"max_age_us": 100_000}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                CameraMatcher([], **options)

    def test_constructor_rejects_unsupported_camera_and_nonboolean_opt_in(self):
        for options in ({"camera": "rear"}, {"allow_receipt": 1, "max_age_us": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                CameraMatcher([], **options)

    def test_equivalent_duplicate_records_resolve_deterministically(self):
        event = self.frame()
        alias_record = deepcopy(event.record)
        alias_record["file"] = "head/./000001-123456.ppm"
        alias = CameraEvent(self.root / "another-manifest.jsonl", 7,
                            alias_record, event.image_path)
        first = CameraMatcher([event, alias]).match(decision_moment())
        second = CameraMatcher([alias, event]).match(decision_moment())
        self.assert_matched(first, "exact_recorded_sdk_timestamp", 1)
        self.assertEqual(first, second)
        self.assertIn(str(alias.manifest_path), str(first["frame"]))
        self.assertIn(str(event.manifest_path), str(first["frame"]))

    def test_distinct_exact_candidates_are_ambiguous_without_receipt_fallback(self):
        events = [self.frame(sequence=1, receipt=800_000),
                  self.frame(sequence=2, receipt=900_000),
                  self.frame(sequence=3, sdk=999, receipt=999_999)]
        result = CameraMatcher(events, allow_receipt=True, max_age_us=1_000_000).match(decision_moment())
        self.assert_unmatched(result, "ambiguous_candidates")

    def test_equal_receipt_candidates_with_different_sdk_frames_are_ambiguous(self):
        events = [self.frame(sequence=1, sdk=111), self.frame(sequence=2, sdk=222)]
        result = CameraMatcher(events, allow_receipt=True, max_age_us=1_000_000).match(decision_moment())
        self.assert_unmatched(result, "ambiguous_candidates")

    def test_identical_record_metadata_at_different_image_paths_is_not_equivalent(self):
        event = self.frame()
        other_root = self.root / "other-recording"
        other_root.mkdir()
        alias = self.frame(manifest=other_root / "frames.jsonl")
        result = CameraMatcher([event, alias]).match(decision_moment())
        self.assert_unmatched(result, "ambiguous_candidates")

    def test_same_image_and_sdk_key_with_different_recorded_receipt_is_ambiguous(self):
        event = self.frame()
        different_record = deepcopy(event.record)
        different_record["received_unix_us"] += 1
        other = CameraEvent(event.manifest_path, 2, different_record, event.image_path)
        result = CameraMatcher([event, other]).match(decision_moment())
        self.assert_unmatched(result, "ambiguous_candidates")

    def test_missing_selected_image_is_failure_without_substitution(self):
        chosen = self.frame(sequence=1)
        chosen.image_path.unlink()
        other = self.frame(sequence=2, sdk=999, receipt=999_999)
        result = CameraMatcher([chosen, other], allow_receipt=True, max_age_us=1_000_000).match(decision_moment())
        self.assert_unmatched(result, "missing_image")

    def test_bad_selected_image_has_explicit_failure(self):
        result = CameraMatcher([self.frame(data=b"not a stored P6 image")]).match(decision_moment())
        self.assert_unmatched(result, "invalid_image")

    def test_selected_image_must_be_a_readable_regular_file(self):
        event = self.frame()
        event.image_path.unlink()
        event.image_path.mkdir()
        result = CameraMatcher([event]).match(decision_moment())
        self.assert_unmatched(result, "unreadable_image")

    def test_truncated_selected_ppm_has_explicit_failure(self):
        result = CameraMatcher([self.frame(data=b"P6\n2 1\n255\n\x01\x02")]).match(decision_moment())
        self.assert_unmatched(result, "invalid_image")

    def test_manifest_and_selected_ppm_dimensions_must_agree(self):
        result = CameraMatcher([self.frame(data=b"P6\n1 1\n255\n\x01\x02\x03")]).match(decision_moment())
        self.assert_unmatched(result, "image_dimension_mismatch")

    def test_matching_does_not_modify_frozen_state_or_original_result(self):
        for status in ("prepared", "succeeded", "failed"):
            with self.subTest(status=status):
                row = decision_moment()
                row["status"] = status
                if status != "prepared":
                    row["ok"] = status == "succeeded"
                    row["decision"] = {"action": "CONTINUE", "reason": "Original inference."}
                    row["raw_content"] = '{"action":"CONTINUE","reason":"Original inference."}'
                    row["error"] = None if status == "succeeded" else {"category": "connection"}
                before = deepcopy(row)
                event = self.frame()
                event_before = deepcopy(event.record)
                result = CameraMatcher([event]).match(row)
                self.assert_matched(result, "exact_recorded_sdk_timestamp")
                self.assertEqual(row, before)
                self.assertEqual(row["social_state_json"], before["social_state_json"])
                self.assertEqual(event.record, event_before)


if __name__ == "__main__":
    unittest.main()
