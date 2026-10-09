"""Scoring semantics, leakage boundaries, and reproducible offline replay."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.gaze_features import FeatureConfig, GazeFeatureExtractor
from evaluation.gaze_score import (
    GazeScoreModel, fit_model, replay, train, training_samples, validate_labels,
)
from tests.test_gaze_features import ZERO, frame, person


def featured(angle=0, **updates):
    return GazeFeatureExtractor().extract(frame(people=[person(angle, **updates)]))["persons"][0]


class ModelTests(unittest.TestCase):
    def test_fit_is_bounded_monotone_and_separates_known_angles(self):
        model = fit_model([(2, 1), (4, 1), (6, 1), (20, 0), (25, 0), (30, 0)], FeatureConfig())
        scores = [model.score_angle(a) for a in range(181)]
        self.assertTrue(all(0 <= score <= 1 for score in scores))
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertGreater(model.score_angle(4), .7)
        self.assertLess(model.score_angle(25), .3)
        for angle in (-1, 181, float("nan")):
            with self.assertRaises(ValueError):
                model.score_angle(angle)

    def test_missing_gaze_and_duplicate_uid_never_get_half_score(self):
        model = GazeScoreModel(4, -3)
        f = featured(gaze=ZERO, gaze_overlap=.5)
        self.assertEqual(model.predict(f), {
            "gaze_score": None, "gaze_state": "UNKNOWN", "score_reason": "suspected_sdk_default"})
        for f in (featured(uid=0), {**featured(), "uid_unique_in_frame": False},
                  {**featured(), "reference_angle_median_deg": None}):
            self.assertIsNone(model.predict(f)["gaze_score"])
        self.assertIsNotNone(model.predict(featured(gaze_overlap=.5))["gaze_score"])

    def test_ambiguous_is_distinct_from_unknown_and_old_overlap_is_unused(self):
        model = GazeScoreModel(3, -3)
        f = featured(10, gaze_overlap=.99)
        self.assertEqual(model.predict(f)["gaze_state"], "AMBIGUOUS")
        self.assertAlmostEqual(model.predict(f)["gaze_score"], .5)
        low = featured(10, gaze_overlap=.01)
        self.assertEqual(model.predict(f), model.predict(low))

    def test_roundtrip_and_bad_contract_rejection(self):
        model = GazeScoreModel(3, -3)
        restored = GazeScoreModel.from_dict(json.loads(json.dumps(model.to_dict())))
        self.assertEqual(restored.predict(featured(5)), model.predict(featured(5)))
        for changes in ({"slope": 1}, {"feature_version": 2}, {"probability_calibrated": True},
                        {"away_max": .8, "direct_min": .2}, {"intercept": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                GazeScoreModel.from_dict({**model.to_dict(), **changes})

    def test_reversed_relationship_cannot_learn_increasing_score(self):
        model = fit_model([(0, 0), (2, 0), (20, 1), (22, 1)], FeatureConfig())
        self.assertEqual(model.slope, 0)
        self.assertAlmostEqual(model.score_angle(0), .5, places=6)
        with self.assertRaisesRegex(ValueError, "each class"):
            fit_model([(0, 1), (2, 1)], FeatureConfig())


class LabelAndReplayTests(unittest.TestCase):
    def setUp(self):
        self.rows = [frame(i / 2, i + 1, [person(4 if i < 4 or i >= 8 else 25)]) for i in range(12)]
        self.text = "".join(json.dumps(r) + "\n" for r in self.rows)
        self.labels = {
            "label_version": 1, "session_id": "a", "label_origin": "synthetic test actions",
            "source_sha256": hashlib.sha256(self.text.encode()).hexdigest(),
            "intervals": [
                {"name": "direct", "start_s": 0, "end_s": 2, "uid": 1, "target": "DIRECT", "split": "train"},
                {"name": "away", "start_s": 2, "end_s": 4, "uid": 1, "target": "AWAY", "split": "train"},
                {"name": "diagnostic", "start_s": 4, "end_s": 6, "uid": 1, "target": "DIRECT", "split": "diagnostic"},
            ]}
        extractor = GazeFeatureExtractor()
        self.frames = [extractor.extract(r) for r in self.rows]

    def test_diagnostic_samples_do_not_affect_fit_and_boundary_is_trimmed(self):
        samples = training_samples(self.frames, self.labels, FeatureConfig())
        self.assertEqual(len(samples), 6)  # Excludes the first 0.3 seconds of each train interval.
        changed = deepcopy(self.frames)
        for f in changed:
            if f["elapsed_s"] >= 4:
                f["persons"][0]["reference_angle_median_deg"] = 170
        self.assertEqual(samples, training_samples(changed, self.labels, FeatureConfig()))

    def test_labels_require_matching_recording_session_and_nonoverlap(self):
        validate_labels(self.labels, self.labels["source_sha256"], self.frames)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            validate_labels(self.labels, "wrong", self.frames)
        bad = deepcopy(self.labels)
        bad["intervals"][1]["start_s"] = 1
        with self.assertRaisesRegex(ValueError, "nonoverlapping"):
            validate_labels(bad, self.labels["source_sha256"], self.frames)
        with self.assertRaisesRegex(ValueError, "session"):
            validate_labels({**self.labels, "session_id": "other"}, self.labels["source_sha256"], self.frames)

    def test_train_replay_roundtrip_preserves_predictions_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, labels = root / "sdk.jsonl", root / "labels.json"
            source.write_text(self.text)
            labels.write_text(json.dumps(self.labels))
            report = train(source, labels, root / "trained")
            replay_report = replay(source, root / "trained/model.json", root / "replayed", labels)
            self.assertEqual(report, replay_report)
            self.assertEqual((root / "trained/scores.jsonl").read_bytes(), (root / "replayed/scores.jsonl").read_bytes())
            model = json.loads((root / "trained/model.json").read_text())
            self.assertFalse(model["probability_calibrated"])
            self.assertFalse(model["training"]["independently_validated"])
            with self.assertRaises(FileExistsError):
                replay(source, root / "trained/model.json", root / "replayed")


if __name__ == "__main__":
    unittest.main()
