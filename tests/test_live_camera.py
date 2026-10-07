"""Bounded live association uses source clocks, never receiver-host timing."""
import base64
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
from io import BytesIO
import json
from threading import Barrier, Thread
import unittest

from PIL import Image

from app.camera_capture import CameraCaptureOrderError
from app.live_camera import MODEL_SOURCE_CLOCK, LiveCameraCache, validate_model_source


def model_source(*, session="capture-a", sdk=123456, receipt=1000000, clock=MODEL_SOURCE_CLOCK):
    return {"version": 1, "clock": clock, "capture": {
        "capture_version": 1, "session_id": session, "stream": "perception", "sequence": 1,
        "received_monotonic_us": receipt, "received_unix_us": 1700000000000000 + receipt,
        "packet": {"time": sdk, "persons": []}}}


def camera_record(*, sequence=1, session="capture-a", sdk=123456, receipt=900000,
                  camera="head", rgb=b"\n# \xff\x00\x01", width=2, height=1, unavailable=False):
    record = {"capture_version": 1, "session_id": session, "camera": camera, "sequence": sequence,
              "event": "unavailable" if unavailable else "frame", "received_monotonic_us": receipt,
              "received_unix_us": 1700000000000000 + receipt}
    if unavailable:
        record["reason"] = "camera absent"
        return record, None
    record.update(timestamp_us=sdk, width=width, height=height,
                  rgb_b64=base64.b64encode(rgb).decode("ascii"))
    return record, rgb


class ModelSourceTests(unittest.TestCase):
    def test_known_source_is_detached_and_keeps_all_capture_fields(self):
        original = model_source()
        result = validate_model_source(original, 1000001)
        self.assertEqual(result, original)
        original["capture"]["packet"]["persons"].append({"uid": 7})
        self.assertEqual(result["capture"]["packet"]["persons"], [])

    def test_known_clock_receipt_cannot_follow_collection_but_equal_is_valid(self):
        self.assertEqual(validate_model_source(model_source(), 1000000), model_source())
        with self.assertRaisesRegex(ValueError, "cannot follow"):
            validate_model_source(model_source(), 999999)

    def test_unknown_clock_is_preserved_without_comparing_to_raw_clock(self):
        source = model_source(clock="unmapped-clock")
        self.assertEqual(validate_model_source(source, 1), source)

    def test_rejects_bad_shapes_versions_clocks_and_nonperception(self):
        invalid = [None, {}, {**model_source(), "extra": 1}, {**model_source(), "version": True},
                   {**model_source(), "version": 2}, {**model_source(), "clock": " "},
                   {**model_source(), "clock": None}]
        other_stream = model_source()
        other_stream["capture"]["stream"] = "locomotion"
        invalid.append(other_stream)
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_model_source(source, 1000000)

    def test_rejects_capture_duplicate_types_and_nonfinite_packet(self):
        for key, value in (("received_monotonic_us", True), ("sequence", 0), ("capture_version", False),
                           ("packet", {"time": float("inf")})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                source = model_source()
                source["capture"][key] = value
                validate_model_source(source, 1000000)


class LiveCameraTests(unittest.TestCase):
    def setUp(self):
        self.cache = LiveCameraCache()

    def ingest(self, **kwargs):
        record, rgb = camera_record(**kwargs)
        return self.cache.ingest(record, rgb, receiver_received_monotonic_us=999999999999,
                                 receiver_received_unix_us=100), record, rgb

    def match(self, source=None, *, age=100000, allow_receipt=False):
        return self.cache.associate(model_source() if source is None else source,
                                    max_age_us=age, allow_receipt=allow_receipt)

    def assert_failure(self, association, category):
        data = association.to_dict()
        self.assertIsNone(association.frame)
        self.assertFalse(data["ok"])
        self.assertEqual(data["status"], "unmatched")
        self.assertEqual(data["error"]["category"], category)

    def test_exact_source_session_timestamp_match_and_receipts_separate(self):
        _, record, rgb = self.ingest()
        association = self.match()
        result = association.to_dict()
        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "exact_recorded_sdk_timestamp")
        self.assertEqual(result["reception_time_difference_us"], -100000)
        self.assertEqual(result["time_difference"]["value"], 0)
        self.assertEqual(association.frame.rgb, rgb)
        self.assertEqual(association.frame.rgb_sha256, hashlib.sha256(rgb).hexdigest())
        self.assertEqual(result["frame"]["received_monotonic_us"], record["received_monotonic_us"])
        self.assertEqual(result["frame"]["receiver_received_monotonic_us"], 999999999999)
        self.assertNotIn("rgb_b64", result["frame"])
        self.assertNotIn(base64.b64encode(rgb).decode(), json.dumps(result))

    def test_exact_future_receipt_within_age_is_explicitly_limited(self):
        self.ingest(receipt=1000010)
        result = self.match().to_dict()
        self.assertTrue(result["ok"])
        self.assertEqual(result["reception_time_difference_us"], 10)
        self.assertIn("after perception", str(result["limitations"]))

    def test_exact_age_limit_applies_in_both_directions_and_boundary_is_inclusive(self):
        for receipt in (900000, 1100000):
            self.cache = LiveCameraCache()
            self.ingest(receipt=receipt)
            self.assertTrue(self.match().to_dict()["ok"])
            self.assert_failure(self.match(age=99999), "frame_too_old")

    def test_exact_is_preferred_to_a_closer_receipt_candidate(self):
        self.ingest(receipt=900000)
        self.ingest(sequence=2, sdk=7, receipt=999999)
        result = self.match(allow_receipt=True).to_dict()
        self.assertEqual(result["method"], "exact_recorded_sdk_timestamp")
        self.assertEqual(result["frame"]["sequence"], 1)

    def test_stale_exact_does_not_silently_fall_back_to_closer_receipt(self):
        self.ingest(receipt=1)
        self.ingest(sequence=2, sdk=7, receipt=999999)
        self.assert_failure(self.match(allow_receipt=True), "frame_too_old")

    def test_receipt_requires_opt_in_selects_latest_prior_and_excludes_future(self):
        self.ingest(sdk=1, receipt=900000)
        self.ingest(sequence=2, sdk=2, receipt=950000)
        self.ingest(sequence=3, sdk=3, receipt=1000001)
        self.assert_failure(self.match(), "no_exact_timestamp")
        result = self.match(allow_receipt=True).to_dict()
        self.assertEqual(result["method"], "reception_monotonic")
        self.assertEqual(result["frame"]["sequence"], 2)
        self.assertEqual(result["reception_time_difference_us"], -50000)

    def test_receipt_no_prior_and_too_old_are_explicit(self):
        self.ingest(sdk=1, receipt=1000001)
        self.assert_failure(self.match(allow_receipt=True), "no_prior_frame")
        self.cache = LiveCameraCache()
        self.ingest(sdk=1, receipt=899999)
        self.assert_failure(self.match(allow_receipt=True), "frame_too_old")

    def test_receipt_can_preserve_noninteger_sdk_time_without_conversion(self):
        self.ingest(sdk=1)
        result = self.match(model_source(sdk=123456.5), allow_receipt=True).to_dict()
        self.assertTrue(result["ok"])
        self.assertEqual(result["perception_source"]["capture"]["packet"]["time"], 123456.5)

    def test_missing_source_empty_cache_cross_session_and_unmapped_clock(self):
        self.assert_failure(self.cache.associate(None, max_age_us=100000), "missing_model_source")
        self.assert_failure(self.match(), "missing_camera_frame")
        self.ingest()
        self.assert_failure(self.match(model_source(session="other")), "cross_session")
        self.assert_failure(self.match(model_source(clock="receiver-host-monotonic-us")), "incompatible_clock")

    def test_malformed_direct_model_source_becomes_unavailable_not_exception(self):
        self.ingest()
        for source in ({}, {"capture": None}, {**model_source(), "version": False}):
            with self.subTest(source=source):
                self.assert_failure(self.match(source), "invalid_model_source")

    def test_head_unavailable_event_is_saved_without_rgb(self):
        self.ingest(unavailable=True)
        self.assert_failure(self.match(), "camera_unavailable")
        self.assertEqual(self.cache.frame_count, 0)
        self.assertEqual(self.cache.event_count, 1)
        self.assertEqual(self.cache.byte_count, 0)

    def test_chest_image_is_not_used_for_head_inference(self):
        self.ingest(camera="chest")
        self.assert_failure(self.match(), "missing_camera_frame")
        self.assertEqual(self.cache.event_count, 0)
        self.assertEqual(self.cache.byte_count, 0)

    def test_duplicate_last_event_is_idempotent_with_new_receiver_receipts(self):
        _, record, rgb = self.ingest()
        self.assertTrue(self.cache.ingest(record, rgb, receiver_received_monotonic_us=1))
        self.assertEqual(self.cache.frame_count, 1)
        self.assertEqual(self.match().frame.to_dict()["receiver_received_monotonic_us"], 999999999999)

    def test_concurrent_identical_ingestion_is_one_frame_and_one_duplicate(self):
        record, rgb = camera_record()
        barrier = Barrier(3)
        results, errors = [], []

        def ingest():
            barrier.wait()
            try:
                results.append(self.cache.ingest(record, rgb))
            except Exception as error:
                errors.append(error)

        threads = [Thread(target=ingest) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.cache.frame_count, 1)
        self.assertTrue(self.match().to_dict()["ok"])

    def test_join_midstream_then_require_contiguous_progress(self):
        self.ingest(sequence=7)
        self.ingest(sequence=8, sdk=8)
        self.assertTrue(self.ingest(sequence=8, sdk=8)[0])
        for sequence in (6, 7, 10):
            with self.subTest(sequence=sequence), self.assertRaises(CameraCaptureOrderError):
                self.ingest(sequence=sequence, sdk=sequence)

    def test_changed_last_retry_is_rejected(self):
        self.ingest()
        with self.assertRaises(CameraCaptureOrderError):
            self.ingest(rgb=b"abcdef")

    def test_new_session_resets_frames_and_order_counters(self):
        self.ingest()
        self.ingest(session="capture-b", sequence=25)
        self.assertEqual(self.cache.frame_count, 1)
        self.assert_failure(self.match(), "cross_session")
        self.assertTrue(self.match(model_source(session="capture-b")).to_dict()["ok"])

    def test_frame_count_and_byte_limits_evict_oldest(self):
        self.cache = LiveCameraCache(max_frames=2, max_bytes=12)
        for sequence in (1, 2, 3):
            self.ingest(sequence=sequence, sdk=sequence)
        self.assertEqual(self.cache.frame_count, 2)
        self.assertEqual(self.cache.byte_count, 12)
        self.assert_failure(self.match(model_source(sdk=1)), "no_exact_timestamp")
        self.cache = LiveCameraCache(max_frames=8, max_bytes=6)
        self.ingest(sdk=1)
        self.ingest(sequence=2, sdk=2)
        self.assertEqual(self.cache.frame_count, 1)
        self.assertEqual(self.cache.byte_count, 6)

    def test_unavailable_events_are_also_bounded(self):
        self.cache = LiveCameraCache(max_frames=2)
        for sequence in range(1, 8):
            self.ingest(sequence=sequence, unavailable=True)
        self.assertEqual(self.cache.event_count, 2)

    def test_frame_over_byte_budget_is_rejected_without_cache_mutation(self):
        self.cache = LiveCameraCache(max_bytes=5)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.ingest()
        self.assertEqual(self.cache.frame_count, 0)

    def test_exact_and_receipt_distinct_candidates_are_ambiguous(self):
        self.ingest()
        self.ingest(sequence=2)
        self.assert_failure(self.match(allow_receipt=True), "ambiguous_candidates")
        self.cache = LiveCameraCache()
        self.ingest(sdk=1)
        self.ingest(sequence=2, sdk=2)
        self.assert_failure(self.match(allow_receipt=True), "ambiguous_candidates")

    def test_snapshot_metadata_is_detached_and_frame_survives_cache_eviction(self):
        _, record, _ = self.ingest()
        snapshot = self.match()
        record["session_id"] = "mutated"
        data = snapshot.to_dict()
        data["frame"]["width"] = 999
        data["perception_source"]["capture"]["session_id"] = "mutated"
        self.ingest(session="new")
        self.assertEqual(snapshot.frame.width, 2)
        self.assertEqual(snapshot.to_dict()["frame"]["session_id"], "capture-a")
        with self.assertRaises(FrozenInstanceError):
            snapshot.frame.width = 999

    def test_live_frame_encoding_preserves_rgb_pixels_without_payload_metadata(self):
        _, _, rgb = self.ingest()
        frame = self.match().frame
        encoded = frame.encode()
        with Image.open(BytesIO(base64.b64decode(encoded.image_base64))) as image:
            self.assertEqual(image.size, (2, 1))
            self.assertEqual(image.tobytes(), rgb)
        self.assertEqual(encoded.source_image_sha256, frame.rgb_sha256)
        self.assertEqual(encoded.source_byte_count, len(rgb))

    def test_constructor_and_association_options_are_strict(self):
        for options in ({"max_frames": True}, {"max_frames": 0}, {"max_bytes": -1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                LiveCameraCache(**options)
        for age, allow in ((True, False), (-1, False), (0.5, False), (1, 1)):
            with self.subTest(age=age, allow=allow), self.assertRaises(ValueError):
                self.cache.associate(model_source(), max_age_us=age, allow_receipt=allow)

    def test_invalid_transport_or_rgb_disagreement_does_not_mutate_cache(self):
        record, rgb = camera_record()
        for invalid_rgb in (None, b"abcdef", bytearray(rgb)):
            with self.subTest(invalid_rgb=invalid_rgb), self.assertRaises(ValueError):
                self.cache.ingest(record, invalid_rgb)
        self.assertEqual(self.cache.frame_count, 0)


if __name__ == "__main__":
    unittest.main()
