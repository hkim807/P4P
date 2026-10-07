"""Stored manifest and selected P6 image validation, without camera services."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.camera_capture import MAX_RGB_BYTES
from app.camera_recordings import (
    CameraManifestError, ImageValidationError, load_camera_events, validate_stored_image,
)


class CameraRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "cameras"
        self.directory.mkdir()
        self.manifest = self.directory / "frames.jsonl"
        self.frame = {
            "capture_version": 1, "session_id": "original-capture", "camera": "head",
            "sequence": 1, "event": "frame", "received_monotonic_us": 100,
            "received_unix_us": 200, "timestamp_us": 300,
            "width": 1, "height": 1, "file": "head/000001-300.ppm",
        }

    def write_manifest(self, records=None):
        rows = [self.frame] if records is None else records
        self.manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                                 encoding="utf-8")
        return load_camera_events([self.manifest])

    def write_image(self, data=b"P6\n1 1\n255\n\x00\xff\x01"):
        image = self.directory / self.frame["file"]
        image.parent.mkdir(exist_ok=True)
        image.write_bytes(data)
        return image

    def assert_image_error(self, category, event):
        with self.assertRaises(ImageValidationError) as caught:
            validate_stored_image(event)
        self.assertEqual(caught.exception.category, category)
        self.assertIn(f"{self.manifest}:1:", str(caught.exception))
        self.assertEqual(caught.exception.message, str(caught.exception))

    def test_loads_actual_writer_shape_preserving_all_events_and_lines(self):
        unavailable = {key: self.frame[key] for key in (
            "capture_version", "session_id", "camera", "sequence", "event",
            "received_monotonic_us", "received_unix_us")}
        unavailable.update(camera="chest", event="unavailable", reason="SDK camera absent", file=None)
        # Receipt ordering differs across worker streams and is not a reader constraint.
        self.manifest.write_text(json.dumps(unavailable) + "\n\n" + json.dumps(self.frame)
                                 + "\n" + json.dumps(self.frame) + "\n", encoding="utf-8")
        events = load_camera_events([str(self.manifest)])
        self.assertEqual([event.line_number for event in events], [1, 3, 4])
        self.assertEqual([event.record for event in events], [unavailable, self.frame, self.frame])
        self.assertIsNone(events[0].image_path)
        self.assertEqual(events[1].image_path, (self.directory / self.frame["file"]).resolve())
        self.assertEqual(events[1].manifest_path, self.manifest.resolve())
        self.assertEqual(len(events), 3)

    def test_explicit_manifest_order_preserved_without_sequence_deduplication(self):
        self.write_manifest()
        second = self.directory / "other.jsonl"
        second.write_text(json.dumps({**self.frame, "sequence": 8}) + "\n", encoding="utf-8")
        events = load_camera_events([second, self.manifest])
        self.assertEqual([event.record["sequence"] for event in events], [8, 1])

    def test_empty_and_missing_manifests_fail_with_location(self):
        self.manifest.write_text(" \n\n", encoding="utf-8")
        with self.assertRaisesRegex(CameraManifestError, "contains no events"):
            load_camera_events([self.manifest])
        self.manifest.unlink()
        with self.assertRaisesRegex(CameraManifestError, "cannot read camera manifest"):
            load_camera_events([self.manifest])

    def test_unreadable_manifest_error_is_source_located(self):
        self.write_manifest()
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(CameraManifestError, "frames.jsonl: cannot read"):
                load_camera_events([self.manifest])

    def test_manifest_symlink_loop_is_a_source_located_input_error(self):
        self.manifest.symlink_to(self.manifest)
        with self.assertRaisesRegex(CameraManifestError, "frames.jsonl: cannot resolve"):
            load_camera_events([self.manifest])

    def test_json_rejects_duplicate_keys_nonfinite_and_non_objects(self):
        for text in ('{"camera":"head","camera":"chest"}',
                     '{"timestamp_us":NaN}', '{"timestamp_us":Infinity}', '[{}]', '{bad'):
            with self.subTest(text=text):
                self.manifest.write_text("\n" + text + "\n", encoding="utf-8")
                with self.assertRaises(CameraManifestError) as caught:
                    load_camera_events([self.manifest])
                self.assertIn(f"{self.manifest}:2:", str(caught.exception))

    def test_invalid_utf8_reports_original_line_number(self):
        self.manifest.write_bytes(json.dumps(self.frame).encode() + b"\n\xff\n")
        with self.assertRaises(CameraManifestError) as caught:
            load_camera_events([self.manifest])
        self.assertIn(f"{self.manifest}:2:", str(caught.exception))

    def test_manifest_strict_fields_and_numeric_types(self):
        mutations = [
            {"capture_version": True}, {"capture_version": 2}, {"session_id": " "},
            {"session_id": "x" * 129}, {"camera": "other"}, {"event": "error"},
            {"sequence": 0}, {"sequence": True}, {"received_monotonic_us": -1},
            {"received_unix_us": 1.2}, {"timestamp_us": False}, {"width": 0},
            {"height": "1"}, {"width": MAX_RGB_BYTES}, {"file": None}, {"file": ""},
            {"rgb_b64": "AAAA"}, {"extra": 1},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(CameraManifestError):
                    self.write_manifest([{**self.frame, **mutation}])
        missing = dict(self.frame)
        del missing["received_unix_us"]
        with self.assertRaises(CameraManifestError):
            self.write_manifest([missing])

    def test_unavailable_manifest_requires_reason_and_no_image_reference(self):
        common = {key: self.frame[key] for key in (
            "capture_version", "session_id", "camera", "sequence", "event",
            "received_monotonic_us", "received_unix_us")}
        unavailable = {**common, "event": "unavailable", "reason": "camera absent", "file": None}
        event = self.write_manifest([unavailable])[0]
        self.assertIsNone(event.image_path)
        self.assert_image_error("invalid_image", event)
        for mutation in ({"file": "head/frame.ppm"}, {"reason": ""}, {"reason": 1},
                         {"timestamp_us": 3}):
            with self.subTest(mutation=mutation):
                with self.assertRaises(CameraManifestError):
                    self.write_manifest([{**unavailable, **mutation}])

    def test_missing_images_are_retained_for_selected_frame_failure(self):
        event = self.write_manifest()[0]
        self.assert_image_error("missing_image", event)

    def test_absolute_parent_and_null_paths_rejected(self):
        for relative in (str(Path(self.temp.name) / "outside.ppm"), "../outside.ppm", ".", "head/\x00.ppm"):
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(CameraManifestError, "image path"):
                    self.write_manifest([{**self.frame, "file": relative}])

    def test_symlink_path_escape_rejected(self):
        outside = Path(self.temp.name) / "outside.ppm"
        outside.write_bytes(b"P6\n1 1\n255\n\x00\x00\x00")
        head = self.directory / "head"
        head.mkdir()
        (head / "frame.ppm").symlink_to(outside)
        with self.assertRaisesRegex(CameraManifestError, "escapes the manifest directory"):
            self.write_manifest([{**self.frame, "file": "head/frame.ppm"}])

    def test_symlink_changed_after_loading_is_rechecked(self):
        target = self.write_image()
        link = target.parent / "link.ppm"
        link.symlink_to(target)
        event = self.write_manifest([{**self.frame, "file": "head/link.ppm"}])[0]
        outside = Path(self.temp.name) / "outside.ppm"
        outside.write_bytes(target.read_bytes())
        link.unlink()
        link.symlink_to(outside)
        self.assert_image_error("invalid_image", event)

    def test_valid_image_dimensions_file_size_and_content_digest(self):
        image = self.write_image()
        event = self.write_manifest()[0]
        before = image.read_bytes()
        result = validate_stored_image(event)
        self.assertEqual(result, {"format": "PPM P6", "width": 1, "height": 1,
                                  "byte_count": len(before),
                                  "sha256": hashlib.sha256(before).hexdigest()})
        self.assertEqual(image.read_bytes(), before)

    def test_ppm_preserves_initial_payload_whitespace_and_hash(self):
        for payload in (b" \n#", b"#\x00\xff", b"\n\r\t"):
            with self.subTest(payload=payload):
                data = b"P6\n1 1\n255\n" + payload
                self.write_image(data)
                result = validate_stored_image(self.write_manifest()[0])
                self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())

    def test_ppm_header_comments_and_crlf_do_not_consume_raster(self):
        for header, payload in ((b"P6\n# header comment\n1\t1\n255\n", b"# \n"),
                                (b"P6\r\n1 1\r\n255\r\n", b"\n# "),
                                (b"P6\n1 1\n255\r", b"\n# ")):
            with self.subTest(header=header):
                self.write_image(header + payload)
                self.assertEqual(validate_stored_image(self.write_manifest()[0])["width"], 1)

    def test_invalid_format_header_dimensions_and_payload_rejected(self):
        invalid_images = (
            b"P3\n1 1\n255\n\x00\x00\x00", b"\x89PNG\x00\x00\x00", b"P6\n1",
            b"P6\n0 1\n255\n", b"P6\n-1 1\n255\n\x00\x00\x00",
            b"P6\n1 1\n65535\n\x00\x00\x00", b"P6\n1 1\n255#\x00\x00\x00",
            b"P6\n1 1\n255\n\x00\x00", b"P6\n1 1\n255\n\x00\x00\x00\x00",
            b"P6\n#" + b"x" * 4096 + b"\n1 1\n255\n\x00\x00\x00",
        )
        for data in invalid_images:
            with self.subTest(data=data[:40]):
                self.write_image(data)
                self.assert_image_error("invalid_image", self.write_manifest()[0])

    def test_manifest_image_dimension_disagreement_has_distinct_failure(self):
        self.write_image(b"P6\n2 1\n255\n" + b"\x00" * 6)
        self.assert_image_error("image_dimension_mismatch", self.write_manifest()[0])

    def test_unreadable_file_has_distinct_failure(self):
        self.write_image()
        event = self.write_manifest()[0]
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            self.assert_image_error("unreadable_image", event)

    def test_image_directory_is_not_a_readable_image(self):
        image = self.directory / self.frame["file"]
        image.mkdir(parents=True)
        self.assert_image_error("unreadable_image", self.write_manifest()[0])

    def test_file_read_is_bounded_by_existing_capture_limit(self):
        image = self.write_image()
        with image.open("r+b") as stream:
            stream.truncate(MAX_RGB_BYTES + 4097)
        self.assert_image_error("invalid_image", self.write_manifest()[0])


if __name__ == "__main__":
    unittest.main()
