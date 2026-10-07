"""Associated replay integrity and pixel-preserving offline image encoding."""
import base64
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app.camera_recordings import (
    CameraManifestError, ImageValidationError, load_camera_events,
    read_selected_camera_event, read_validated_stored_image,
)
from app.image_matching import CameraMatcher, ReplayInputError, iter_replay_rows
from app.llm_replay import main as prepare_replay
from app.vlm_inputs import (
    AssociatedReplayError, VLMImageInputError, iter_associated_rows, load_vlm_image,
)


def make_associated_fixture(root, *, header=None, pixels=None, width=2, height=1,
                            sdk_timestamp=123456, camera_timestamp=None,
                            receipt=900000, allow_receipt=False, max_age_us=None):
    """One actual Step 3 row and Step 4 association, with no model requests."""
    root = Path(root)
    source = root / "source.sdk.jsonl"
    prepared = root / "prepared.jsonl"
    record = {"capture_version": 1, "session_id": "capture-a", "stream": "perception",
              "sequence": 1, "received_monotonic_us": 1000000,
              "received_unix_us": 1700000001000000,
              "packet": {"time": sdk_timestamp, "persons": []}}
    source.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()), patch(
            "app.llm_replay.OllamaClient", side_effect=AssertionError("preparation only")):
        result = prepare_replay([str(source), "--format", "sdk", "--prepare-only",
                                 "--sample-interval", "0", "--base-url", "http://127.0.0.1:11434",
                                 "--model", "original-model", "--output", str(prepared)])
    if result != 0:
        raise AssertionError("fixture preparation failed")
    row = json.loads(prepared.read_text(encoding="utf-8"))
    directory = root / "cameras"
    directory.mkdir()
    manifest = directory / "frames.jsonl"
    relative = "head/000001.ppm"
    image = directory / relative
    image.parent.mkdir()
    image.write_bytes((header or f"P6\n{width} {height}\n255\n".encode())
                      + (pixels if pixels is not None else b"\x01\x20\xff" * width * height))
    camera = {"capture_version": 1, "session_id": "capture-a", "camera": "head",
              "sequence": 1, "event": "frame", "received_monotonic_us": receipt,
              "received_unix_us": 1700000000000000 + receipt,
              "timestamp_us": sdk_timestamp if camera_timestamp is None else camera_timestamp,
              "width": width, "height": height, "file": relative}
    manifest.write_text(json.dumps(camera) + "\n", encoding="utf-8")
    row["image_matching"] = CameraMatcher(load_camera_events([manifest]),
        allow_receipt=allow_receipt, max_age_us=max_age_us).match(row)
    return row, image, manifest


class VLMImageInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.row, self.image, self.manifest = make_associated_fixture(self.root)

    def load(self):
        return load_vlm_image(self.row["image_matching"])

    def assert_failure(self, category):
        with self.assertRaises(VLMImageInputError) as caught:
            self.load()
        self.assertEqual(caught.exception.category, category)
        self.assertEqual(caught.exception.message, str(caught.exception))
        return caught.exception

    def test_png_raw_base64_pixels_dimensions_and_digests(self):
        source = self.image.read_bytes()
        row_before = deepcopy(self.row)
        encoded = self.load()
        png = base64.b64decode(encoded.image_base64, validate=True)
        self.assertFalse(encoded.image_base64.startswith("data:"))
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(BytesIO(png)) as decoded:
            self.assertEqual(decoded.format, "PNG")
            self.assertEqual(decoded.mode, "RGB")
            self.assertEqual(decoded.size, (2, 1))
            self.assertEqual(decoded.tobytes(), b"\x01\x20\xff" * 2)
        self.assertEqual(encoded.source_image_sha256, hashlib.sha256(source).hexdigest())
        self.assertEqual(encoded.png_sha256, hashlib.sha256(png).hexdigest())
        self.assertEqual(encoded.source_byte_count, len(source))
        self.assertEqual(encoded.png_byte_count, len(png))
        self.assertEqual((encoded.width, encoded.height), (2, 1))
        self.assertEqual(encoded.to_dict()["encoding_format"], "PNG")
        self.assertNotIn("image_base64", encoded.to_dict())
        self.assertNotIn(encoded.image_base64, json.dumps(encoded.to_dict()))
        self.assertEqual(self.image.read_bytes(), source)
        self.assertEqual(self.row, row_before)
        self.assertEqual(sorted(path.name for path in self.image.parent.iterdir()), ["000001.ppm"])

    def test_pixel_fidelity_for_comments_crlf_and_initial_whitespace_or_hash(self):
        variants = [(b"P6\n1 1\n255\n", b" \n#"),
                    (b"P6\n# header\n1\t1\n255\n", b"# \n"),
                    (b"P6\r\n1 1\r\n255\r\n", b"\n# "),
                    (b"P6\n1 1\n255\r", b"\n# ")]
        for index, (header, pixels) in enumerate(variants):
            with self.subTest(header=header, pixels=pixels):
                root = self.root / str(index)
                root.mkdir()
                row, image, _ = make_associated_fixture(root, width=1, header=header, pixels=pixels)
                original = image.read_bytes()
                encoded = load_vlm_image(row["image_matching"])
                with Image.open(BytesIO(base64.b64decode(encoded.image_base64))) as png:
                    self.assertEqual(png.tobytes(), pixels)
                    self.assertEqual(png.size, (1, 1))
                self.assertEqual(image.read_bytes(), original)

    def test_read_helper_returns_exact_validated_hash_bytes_in_one_read(self):
        event = load_camera_events([self.manifest])[0]
        original = self.image.read_bytes()
        stream = BytesIO(original)
        with patch.object(Path, "open", return_value=stream) as opened:
            metadata, data = read_validated_stored_image(event)
        opened.assert_called_once_with("rb")
        self.assertEqual(data, original)
        self.assertEqual(metadata["sha256"], hashlib.sha256(data).hexdigest())

    def test_encoding_uses_the_same_validated_read_not_a_second_file_read(self):
        event = load_camera_events([self.manifest])[0]
        metadata, data = read_validated_stored_image(event)
        with patch("app.vlm_inputs.read_validated_stored_image", return_value=(metadata, data)) as reader:
            with patch.object(Path, "open", side_effect=AssertionError("unexpected second file read")):
                with patch("app.vlm_inputs.read_selected_camera_event", return_value=event):
                    encoded = self.load()
        reader.assert_called_once_with(event)
        self.assertEqual(encoded.source_image_sha256, metadata["sha256"])

    def test_missing_image_is_input_failure(self):
        self.image.unlink()
        self.assert_failure("missing_image")

    def test_changed_rgb_pixels_are_rejected_against_step4_hash(self):
        data = self.image.read_bytes()
        self.image.write_bytes(data[:-1] + b"\x00")
        self.assert_failure("image_changed")

    def test_valid_changed_ppm_header_and_size_are_rejected(self):
        self.image.write_bytes(b"P6\n# changed header\n2 1\n255\n" + b"\x01\x20\xff" * 2)
        self.assert_failure("image_changed")

    def test_invalid_image_is_input_failure(self):
        self.image.write_bytes(b"P3\n2 1\n255\n1 2 3")
        self.assert_failure("invalid_image")

    def test_truncated_image_is_input_failure(self):
        self.image.write_bytes(self.image.read_bytes()[:-1])
        self.assert_failure("invalid_image")

    def test_dimensions_disagreeing_with_manifest_are_input_failure(self):
        self.image.write_bytes(b"P6\n1 1\n255\n\x01\x20\xff")
        self.assert_failure("image_dimension_mismatch")

    def test_image_directory_is_unreadable_failure(self):
        self.image.unlink()
        self.image.mkdir()
        self.assert_failure("unreadable_image")

    def test_image_permission_error_is_input_failure(self):
        with patch("app.vlm_inputs.read_validated_stored_image", side_effect=
                   ImageValidationError("unreadable_image", "denied")):
            self.assert_failure("unreadable_image")

    def test_missing_manifest_is_input_failure(self):
        self.manifest.unlink()
        self.assert_failure("manifest_unavailable")

    def test_invalid_manifest_is_input_failure(self):
        self.manifest.write_text('{"invalid":true}\n', encoding="utf-8")
        self.assert_failure("manifest_unavailable")

    def test_valid_changed_manifest_provenance_is_rejected_without_rematching(self):
        record = json.loads(self.manifest.read_text())
        record["sequence"] += 1
        self.manifest.write_text(json.dumps(record) + "\n", encoding="utf-8")
        self.assert_failure("manifest_changed")

    def test_shifted_source_line_is_rejected(self):
        self.manifest.write_text("\n" + self.manifest.read_text(), encoding="utf-8")
        self.assert_failure("manifest_changed")

    def test_changed_image_symlink_escape_is_rejected(self):
        outside = self.root / "outside.ppm"
        outside.write_bytes(self.image.read_bytes())
        self.image.unlink()
        self.image.symlink_to(outside)
        self.assert_failure("manifest_unavailable")

    def test_unrelated_image_symlink_loop_does_not_invalidate_selected_frame(self):
        record = json.loads(self.manifest.read_text())
        bad = {**record, "sequence": 2, "file": "head/loop.ppm"}
        loop = self.image.parent / "loop.ppm"
        loop.symlink_to(loop)
        self.manifest.write_text(json.dumps(record) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
        self.assertEqual(self.load().source_image_sha256,
                         self.row["image_matching"]["frame"]["image_validation"]["sha256"])
        with self.assertRaises(CameraManifestError):
            load_camera_events([self.manifest])

    def test_selected_event_reader_uses_only_exact_line_including_blanks(self):
        original = self.manifest.read_bytes()
        self.manifest.write_bytes(b'{"unrelated":"bad envelope"}\n\n' + original + b'\xff\n')
        self.assertIsNone(read_selected_camera_event(self.manifest, 2))
        event = read_selected_camera_event(self.manifest, 3)
        self.assertEqual(event.record, self.row["image_matching"]["frame"]["camera_manifest_record"])
        self.assertEqual(event.line_number, 3)
        self.assertIsNone(read_selected_camera_event(self.manifest, 8))
        with self.assertRaisesRegex(CameraManifestError, ":4:"):
            read_selected_camera_event(self.manifest, 4)

    def test_in_memory_encoding_failure_is_separate_input_failure(self):
        with patch("app.image_encoding.Image.frombytes", side_effect=OSError("codec failed")):
            self.assert_failure("encoding_error")

    def test_unmatched_image_is_unavailable(self):
        self.row["image_matching"] = {"status": "unmatched", "ok": False}
        self.assert_failure("image_unavailable")


class AssociatedReplayReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.row, self.image, self.manifest = make_associated_fixture(self.root)
        self.replay = self.root / "associated.jsonl"

    def write(self, rows=None):
        rows = [self.row] if rows is None else rows
        self.replay.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                               encoding="utf-8")

    def assert_bad(self, row=None):
        self.write([self.row if row is None else row])
        with self.assertRaises(AssociatedReplayError) as caught:
            list(iter_associated_rows(self.replay))
        self.assertIn(f"{self.replay}:1:", str(caught.exception))

    def test_preserves_complete_original_rows_and_canonical_strings(self):
        before = deepcopy(self.row)
        self.write([self.row, self.row])
        result = list(iter_associated_rows(self.replay))
        self.assertEqual(result, [before, before])
        self.assertEqual(result[0]["social_state_json"], before["social_state_json"])
        self.assertEqual(self.row, before)

    def test_accepts_prepared_succeeded_and_failed_llm_results(self):
        success = deepcopy(self.row)
        success.update(status="succeeded", ok=True, decision={"action": "STOP", "reason": "Original decision."},
                       error=None, raw_content='{"action":"STOP","reason":"Original decision."}',
                       request_duration_s=0.01, returned_model="original-model")
        failed = deepcopy(success)
        failed.update(status="failed", ok=False, decision=None,
                      error={"category": "invalid_decision", "message": "original failure"})
        self.write([self.row, success, failed])
        self.assertEqual(list(iter_associated_rows(self.replay)), [self.row, success, failed])

    def test_accepts_original_matching_failure_for_unavailable_image(self):
        self.image.unlink()
        self.row["image_matching"] = CameraMatcher(load_camera_events([self.manifest])).match(self.row)
        self.write()
        self.assertEqual(list(iter_associated_rows(self.replay)), [self.row])

    def test_receipt_match_keeps_signed_delta_and_unverified_sdk_values(self):
        root = self.root / "receipt"
        root.mkdir()
        row, _, _ = make_associated_fixture(root, sdk_timestamp=123456.5, camera_timestamp=7,
                                            allow_receipt=True, max_age_us=100000)
        self.write([row])
        actual = list(iter_associated_rows(self.replay))[0]
        self.assertEqual(actual, row)
        self.assertEqual(actual["image_matching"]["method"], "reception_monotonic")
        self.assertEqual(actual["image_matching"]["reception_time_difference_us"], -100000)

    def test_exact_match_allows_recorded_frame_received_later(self):
        root = self.root / "future-exact"
        root.mkdir()
        row, _, _ = make_associated_fixture(root, receipt=1000001)
        self.write([row])
        self.assertEqual(list(iter_associated_rows(self.replay)), [row])

    def test_step4_reader_still_rejects_already_associated_rows(self):
        self.write()
        with self.assertRaisesRegex(ReplayInputError, "already contains image_matching"):
            list(iter_replay_rows(self.replay))

    def test_rejects_already_inferred_rows(self):
        self.row["vlm_inference"] = {"status": "succeeded"}
        self.assert_bad()

    def test_rejects_changed_canonical_state_or_correlation(self):
        for key, value in (("social_state_json", "{}"), ("source_state_id", "other"),
                           ("source_robot_timestamp_us", False)):
            with self.subTest(key=key):
                row = deepcopy(self.row)
                row[key] = value
                self.assert_bad(row)

    def test_rejects_malformed_matching_status_policy_or_image_metadata(self):
        mutations = [
            ("schema_version", True), ("schema_version", 2), ("status", "complete"),
            ("ok", 1), ("method", "nearest_any_clock"), ("frame", None),
            ("error", {"category": "unexpected"}), ("policy", {}),
            ("limitations", "unverified"), ("reception_time_difference_us", False),
            ("timestamp_basis", "UTC simultaneous"), ("time_difference", {"value": 0}),
            ("reception_clock_provenance", "guessed same host"),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                row = deepcopy(self.row)
                row["image_matching"][key] = value
                self.assert_bad(row)

    def test_rejects_frame_provenance_duplicates_that_disagree(self):
        mutations = {"original_capture_session": "other", "camera": "chest", "sequence": True,
                     "line_number": 0, "manifest_path": "relative/frames.jsonl", "image_path": "relative.ppm",
                     "relative_image_path": "../outside.ppm", "width": 1, "height": False,
                     "sdk_camera_timestamp_us": 7, "sdk_perception_timestamp": 123456.0,
                     "camera_received_monotonic_us": 1, "camera_received_unix_us": 2,
                     "perception_received_monotonic_us": 3, "perception_received_unix_us": 4,
                     "equivalent_manifest_references": []}
        for key, value in mutations.items():
            with self.subTest(key=key):
                row = deepcopy(self.row)
                row["image_matching"]["frame"][key] = value
                self.assert_bad(row)

    def test_rejects_invalid_recorded_image_validation(self):
        mutations = {"format": "PNG", "width": True, "height": 0, "byte_count": -1, "sha256": "0" * 63}
        for key, value in mutations.items():
            with self.subTest(key=key):
                row = deepcopy(self.row)
                row["image_matching"]["frame"]["image_validation"][key] = value
                self.assert_bad(row)

    def test_rejects_source_clock_provenance_and_signed_delta_changes(self):
        mutations = [("original_capture_session", "different"), ("received_monotonic_us", 1)]
        for key, value in mutations:
            with self.subTest(key=key):
                row = deepcopy(self.row)
                if key == "original_capture_session":
                    row["source"][key] = value
                else:
                    row["source"]["timestamps"][key] = value
                self.assert_bad(row)

    def test_rejects_missing_nonstring_or_relative_source_paths(self):
        for source_path in (None, False, "", "relative/source.sdk.jsonl", "\x00"):
            with self.subTest(source_path=source_path):
                row = deepcopy(self.row)
                row["source"]["input_path"] = source_path
                self.assert_bad(row)
        row = deepcopy(self.row)
        del row["source"]["input_path"]
        self.assert_bad(row)

    def test_rejects_source_estimation_session_and_reconstruction_disagreement(self):
        for key, value in (("estimation_timestamp_us", 7), ("estimation_timestamp_us", True),
                           ("processing_session_id", "different"), ("reconstructed", False),
                           ("reconstructed", 1)):
            with self.subTest(key=key, value=value):
                row = deepcopy(self.row)
                owner = row["source"]["timestamps"] if key == "estimation_timestamp_us" else row["source"]
                owner[key] = value
                self.assert_bad(row)

    def test_rejects_sdk_estimation_different_from_receipt_even_if_source_record_agrees(self):
        row = deepcopy(self.row)
        row["source"]["timestamps"]["received_monotonic_us"] = 500000
        row["source"]["source_record"]["received_monotonic_us"] = 500000
        self.assert_bad(row)

    def test_actual_raw_and_saved_social_sources_are_accepted_without_camera(self):
        for input_format in ("raw", "social"):
            with self.subTest(input_format=input_format):
                source = self.root / f"actual-{input_format}.jsonl"
                prepared = self.root / f"prepared-{input_format}.jsonl"
                record = (self.row["source"]["reconstructed_raw_observation"] if input_format == "raw"
                          else self.row["social_state"])
                source.write_text(json.dumps(record) + "\n", encoding="utf-8")
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    result = prepare_replay([str(source), "--format", input_format, "--prepare-only",
                        "--sample-interval", "0", "--base-url", "http://127.0.0.1:11434",
                        "--model", "original-model", "--output", str(prepared)])
                self.assertEqual(result, 0)
                row = json.loads(prepared.read_text(encoding="utf-8"))
                if input_format == "social":
                    self.assertNotEqual(row["source"]["processing_session_id"], row["session_id"])
                row["image_matching"] = CameraMatcher([]).match(row)
                self.write([row])
                self.assertEqual(list(iter_associated_rows(self.replay)), [row])

    def test_rejects_receipt_match_without_opt_in_or_with_impossible_age(self):
        root = self.root / "approx"
        root.mkdir()
        row, _, _ = make_associated_fixture(root, camera_timestamp=7, allow_receipt=True, max_age_us=100000)
        for policy in ({"camera": "head", "allow_receipt": False, "max_age_us": None},
                       {"camera": "head", "allow_receipt": True, "max_age_us": 99999}):
            with self.subTest(policy=policy):
                changed = deepcopy(row)
                changed["image_matching"]["policy"] = policy
                self.assert_bad(changed)

    def test_absent_null_sdk_time_is_a_located_provenance_error(self):
        root = self.root / "missing-sdk-time"
        root.mkdir()
        row, _, _ = make_associated_fixture(root, sdk_timestamp=None, camera_timestamp=7,
                                            allow_receipt=True, max_age_us=100000)
        del row["source"]["timestamps"]["sdk_perception_timestamp"]
        self.assert_bad(row)

    def test_unmatched_requires_failure_and_no_selected_frame(self):
        for matching in ({"status": "unmatched", "ok": True},
                         {"status": "unmatched", "ok": False, "frame": {}},
                         {"status": "unmatched", "ok": False, "frame": None, "error": None}):
            with self.subTest(matching=matching):
                row = deepcopy(self.row)
                row["image_matching"].update(matching)
                self.assert_bad(row)

    def test_unmatched_requires_explicit_null_frame_and_method_fields(self):
        row = deepcopy(self.row)
        row["image_matching"] = CameraMatcher([]).match(row)
        for key in ("frame", "method", "error", "reception_time_difference_us"):
            with self.subTest(key=key):
                malformed = deepcopy(row)
                del malformed["image_matching"][key]
                self.assert_bad(malformed)

    def test_unmatched_candidate_references_require_absolute_paths_and_positive_lines(self):
        row = deepcopy(self.row)
        row["image_matching"] = CameraMatcher([]).match(row)
        for reference in (None, {}, {"manifest_path": "relative.jsonl", "line_number": 1},
                          {"manifest_path": str(self.manifest), "line_number": 0},
                          {"manifest_path": str(self.manifest), "line_number": True}):
            with self.subTest(reference=reference):
                malformed = deepcopy(row)
                malformed["image_matching"]["error"]["details"]["candidate"] = reference
                self.assert_bad(malformed)
        malformed = deepcopy(row)
        malformed["image_matching"]["error"]["details"]["candidates"] = "invalid"
        self.assert_bad(malformed)

    def test_strict_json_errors_are_located_and_lazy(self):
        invalid_lines = [b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1e9999}', b'\xff', b'{bad', b'[]']
        valid = json.dumps(self.row).encode()
        for invalid in invalid_lines:
            with self.subTest(invalid=invalid):
                self.replay.write_bytes(valid + b"\n\n" + invalid + b"\n")
                rows = iter_associated_rows(self.replay)
                self.assertEqual(next(rows), self.row)
                with self.assertRaises(AssociatedReplayError) as caught:
                    next(rows)
                self.assertIn(f"{self.replay}:3:", str(caught.exception))

    def test_empty_and_missing_inputs_are_errors(self):
        self.replay.write_text("\n \n", encoding="utf-8")
        with self.assertRaisesRegex(AssociatedReplayError, "contains no rows"):
            list(iter_associated_rows(self.replay))
        self.replay.unlink()
        with self.assertRaisesRegex(AssociatedReplayError, "cannot read associated replay"):
            list(iter_associated_rows(self.replay))


if __name__ == "__main__":
    unittest.main()
