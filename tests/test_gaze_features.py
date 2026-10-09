"""Gaze math, missing-data semantics, temporal isolation and capture replay."""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

from evaluation.gaze_features import (
    FeatureConfig, GazeFeatureExtractor, extract_recording, person_features,
)


def person(angle=0, uid=1, **updates):
    radians = math.radians(angle)
    return {"uid": uid, "gaze": {"x": math.sin(radians), "y": 0, "z": -math.cos(radians)},
            "gaze_overlap": 0.95, "head_position": {"x": 0, "y": 0, "z": 0},
            "dist_mm": 2500, "face": {"x1": 10, "y1": 20, "x2": 50, "y2": 80},
            **updates}


def frame(t=0, sequence=1, people=None, session="a"):
    return {"capture_version": 1, "session_id": session, "stream": "perception",
            "sequence": sequence, "received_monotonic_us": int(1e6 + t * 1e6),
            "received_unix_us": int(2e6 + t * 1e6),
            "packet": {"time": 12345, "persons": people if people is not None else [person()]}}


ZERO = {"x": 0, "y": 0, "z": 0}


class PersonFeatureTests(unittest.TestCase):
    def extract(self, p):
        return person_features(p, FeatureConfig())

    def test_angular_math_normalizes_and_supports_reference(self):
        for angle in (0, 30, 90, 180):
            with self.subTest(angle=angle):
                p = person(angle)
                p["gaze"] = {a: v * 3 for a, v in p["gaze"].items()}
                f = self.extract(p)
                self.assertAlmostEqual(f["gaze_norm"], 3)
                self.assertAlmostEqual(f["reference_angle_deg"], angle)
                self.assertAlmostEqual(f["gaze_xz_angle_deg"], angle)
        f = person_features(person(90), FeatureConfig(reference_direction=(2, 0, 0)))
        self.assertAlmostEqual(f["reference_angle_deg"], 0)
        up = self.extract(person(gaze={"x": 0, "y": 1, "z": -1}))
        self.assertAlmostEqual(up["gaze_elevation_deg"], 45)

    def test_suspected_default_unknown_but_half_overlap_alone_is_valid(self):
        f = self.extract(person(gaze=ZERO, gaze_overlap=0.5))
        self.assertEqual(f["feature_status"], "UNKNOWN")
        self.assertEqual(f["invalid_reason"], "suspected_sdk_default")
        self.assertIsNone(f["reference_angle_deg"])
        self.assertFalse(f["head_pose_usable"])
        f = self.extract(person(gaze_overlap=0.5))
        self.assertEqual(f["feature_status"], "AVAILABLE")
        self.assertTrue(f["head_pose_usable"])  # Zero rotation can be legitimate.
        self.assertFalse(f["suspected_sdk_default"])

    def test_missing_nonfinite_and_zero_gaze_never_become_angles(self):
        for gaze in (None, {}, ZERO, {"x": "NaN", "y": 1, "z": 1},
                     {"x": float("inf"), "y": 0, "z": 1},
                     {"x": True, "y": 0, "z": -1}):
            with self.subTest(gaze=gaze):
                f = self.extract(person(gaze=gaze))
                self.assertEqual(f["feature_status"], "UNKNOWN")
                self.assertIsNone(f["gaze_unit"])
                self.assertIsNone(f["reference_angle_deg"])

    def test_geometry_raw_fields_and_input_preserved(self):
        p = person(id_score="NaN", landmarks={"nose": {"x": 25, "y": 40}},
                   g_gaze=[{"sys": "CAM_HEAD", "x": .001, "y": 0, "z": 0}])
        original = deepcopy(p)
        f = self.extract(p)
        self.assertEqual(p, original)
        self.assertEqual(f["raw"]["g_gaze"], p["g_gaze"])
        self.assertEqual(f["raw"]["landmarks"], p["landmarks"])
        self.assertEqual(f["distance_m"], 2.5)
        self.assertEqual(f["face_center_px"], [30, 50])
        self.assertEqual(f["face_size_px"], [40, 60])
        self.assertIsNone(f["id_score_raw_finite"])
        f = self.extract(person(dist_mm=-1, gaze_overlap=2,
                                face={"x1": 50, "x2": 10, "y1": 0, "y2": 20}))
        self.assertIsNone(f["distance_m"])
        self.assertFalse(f["face_box_valid"])
        self.assertFalse(f["gaze_overlap_in_range"])

    def test_invalid_config(self):
        for kwargs in ({"reference_direction": (0, 0, 0)}, {"window_s": 0},
                       {"max_gap_s": float("nan")}, {"zero_epsilon": -1},
                       {"reference_direction": (0, 0, float("inf"))}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FeatureConfig(**kwargs)


class TemporalFeatureTests(unittest.TestCase):
    def test_receipt_window_and_uid_isolation(self):
        e = GazeFeatureExtractor(FeatureConfig(window_s=0.25))
        e.extract(frame(0, 1, [person(0), person(80, uid=2)]))
        f = e.extract(frame(.1, 2, [person(20), person(80, uid=2)]))
        self.assertAlmostEqual(f["persons"][0]["reference_angle_median_deg"], 10)
        self.assertAlmostEqual(f["persons"][1]["reference_angle_median_deg"], 80)
        e.extract(frame(.2, 3, [person(40)]))
        f = e.extract(frame(.4, 4, [person(60), person(10, uid=2)]))
        self.assertAlmostEqual(f["persons"][0]["reference_angle_median_deg"], 50)
        self.assertEqual(f["persons"][0]["temporal_sample_count"], 2)
        self.assertEqual(f["persons"][1]["temporal_sample_count"], 1)

    def test_unknown_or_disappearance_resets_window(self):
        for middle in ([person(gaze=ZERO, gaze_overlap=.5)], []):
            with self.subTest(middle=middle):
                e = GazeFeatureExtractor()
                e.extract(frame())
                f = e.extract(frame(.1, 2, middle))
                if middle:
                    self.assertIsNone(f["persons"][0]["reference_angle_median_deg"])
                f = e.extract(frame(.2, 3, [person(40)]))
                self.assertEqual(f["persons"][0]["temporal_sample_count"], 1)
                self.assertAlmostEqual(f["persons"][0]["reference_angle_median_deg"], 40)

    def test_session_sequence_gap_and_time_gap_reset(self):
        for next_frame in (frame(.1, 3), frame(1, 2), frame(0, 1, session="b")):
            with self.subTest(frame=next_frame):
                e = GazeFeatureExtractor(FeatureConfig(window_s=10))
                e.extract(frame())
                f = e.extract(next_frame)
                self.assertEqual(f["persons"][0]["temporal_sample_count"], 1)

    def test_invalid_and_duplicate_ids_do_not_share_temporal_state(self):
        e = GazeFeatureExtractor()
        e.extract(frame())
        f = e.extract(frame(.1, 2, [person(10), person(80), person(uid=0), person(uid=None)]))
        for p in f["persons"]:
            self.assertIsNone(p["reference_angle_median_deg"])
            self.assertEqual(p["feature_status"], "AVAILABLE")
        f = e.extract(frame(.2, 3))
        self.assertEqual(f["persons"][0]["temporal_sample_count"], 1)

    def test_ordering_error_not_silently_smoothed(self):
        for bad in (frame(0, 2), frame(.1, 1)):
            e = GazeFeatureExtractor()
            e.extract(frame())
            with self.assertRaisesRegex(ValueError, "strictly increase"):
                e.extract(bad)


class RecordingTests(unittest.TestCase):
    def test_replay_preserves_empty_frames_provenance_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root / "sdk.jsonl", root / "features"
            locomotion = {**frame(), "stream": "locomotion", "packet": {}}
            rows = [frame(people=[]), locomotion,
                    frame(.1, 2, [person(), person(uid=2, gaze=ZERO, gaze_overlap=.5)])]
            source.write_text("".join(json.dumps(r) + "\n" for r in rows))
            summary = extract_recording(source, output)
            self.assertEqual(summary["counts"]["perception_frames"], 2)
            self.assertEqual(summary["counts"]["empty_person_frames"], 1)
            self.assertEqual(summary["counts"]["available_samples"], 1)
            self.assertEqual(summary["counts"]["suspected_sdk_default_samples"], 1)
            self.assertEqual(summary["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            features = [json.loads(line) for line in (output / "features.jsonl").read_text().splitlines()]
            self.assertEqual(features[0]["persons"], [])
            self.assertEqual(features[1]["elapsed_s"], .1)
            self.assertEqual(features[1]["source_time_raw"], 12345)
            self.assertFalse(features[1]["reference_frame_verified"])
            self.assertTrue((output / "summary.json").exists())
            with self.assertRaises(FileExistsError):
                extract_recording(source, output)

    def test_bad_input_reports_line_and_never_writes_success_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "bad.jsonl"
            source.write_text(json.dumps(frame()) + "\n{broken\n")
            with self.assertRaisesRegex(ValueError, "bad.jsonl:2:"):
                extract_recording(source, root / "out")
            self.assertFalse((root / "out" / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
