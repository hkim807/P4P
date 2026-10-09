"""Pose/gaze fitting, missing-data resets and recording-isolated evaluation."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.gaze_features import FeatureConfig, GazeFeatureExtractor
from evaluation.gaze_score_v2 import (
    PoseGazeModel, add_pose_features, basis, fit_pose_model, load_runs, metrics,
    replay, sample_weights, samples_for, train,
)
from tests.test_gaze_features import ZERO, frame, person


def prepare(rows):
    extractor = GazeFeatureExtractor()
    return add_pose_features([extractor.extract(r) for r in rows], FeatureConfig())


def sample(x, target, run="a", phase=None):
    return {"x": x, "target": target, "run": run, "phase": phase or str(target)}


def feature_person(values):
    return {"feature_status": "AVAILABLE", "uid_valid": True, "uid_unique_in_frame": True,
            "head_pose_usable": True, "pose_gaze_features": values, "pose_gaze_reason": None}


class FeatureTests(unittest.TestCase):
    def test_joint_median_resets_when_head_pose_disappears(self):
        rows = prepare([frame(0, 1, [person(0, head_position={"x": 1, "y": 2, "z": 3})]),
                        frame(.1, 2, [person(20, head_position={"x": 3, "y": 4, "z": 5})]),
                        frame(.2, 3, [person(head_position=None)]),
                        frame(.3, 4, [person(40, head_position={"x": 8, "y": 9, "z": 10})])])
        p = rows[1]["persons"][0]
        self.assertAlmostEqual(p["pose_gaze_features"][0], 10)
        self.assertEqual(p["pose_gaze_features"][2:], [2, 3, 4])
        self.assertEqual(rows[2]["persons"][0]["pose_gaze_reason"], "missing_or_invalid_head_pose")
        self.assertEqual(rows[3]["persons"][0]["pose_gaze_sample_count"], 1)

    def test_uid_session_gap_and_unknown_isolation(self):
        cases = [frame(.1, 2, [person(uid=2)]), frame(1, 2), frame(.1, 3),
                 frame(0, 1, session="new")]
        for after in cases:
            with self.subTest(after=after):
                rows = prepare([frame(), after])
                self.assertEqual(rows[-1]["persons"][0]["pose_gaze_sample_count"], 1)
        rows = prepare([frame(), frame(.1, 2, [person(gaze=ZERO, gaze_overlap=.5)]), frame(.2, 3)])
        self.assertIsNone(rows[1]["persons"][0]["pose_gaze_features"])
        self.assertEqual(rows[2]["persons"][0]["pose_gaze_sample_count"], 1)
        rows = prepare([frame(), frame(.1, 2, [person(), person(90)]), frame(.2, 3)])
        self.assertTrue(all(p["pose_gaze_features"] is None for p in rows[1]["persons"]))
        self.assertEqual(rows[2]["persons"][0]["pose_gaze_sample_count"], 1)


class ModelTests(unittest.TestCase):
    def test_interaction_can_distinguish_eye_head_combinations(self):
        # XOR-like labels require the gaze/head interaction rather than angle alone.
        samples = [sample([x, 0, head, 0, 0], int(x == head))
                   for x in (-1, 1) for head in (-1, 1)]
        model, fit = fit_pose_model(samples, FeatureConfig())
        self.assertTrue(fit["converged"])
        for s in samples:
            score = model.predict(feature_person(s["x"]))["gaze_score"]
            self.assertGreater(score, .7) if s["target"] else self.assertLess(score, .3)

    def test_scaling_and_run_phase_weights_only_use_fitting_samples(self):
        samples = [sample([0, 0, 0, 0, 0], 0), sample([4, 0, 0, 0, 0], 1)]
        weights = sample_weights(samples)
        self.assertEqual(weights, [.5, .5])
        duplicated = [samples[0]] * 10 + [samples[1]]
        self.assertAlmostEqual(sum(sample_weights(duplicated)), 1)
        model, _ = fit_pose_model(samples, FeatureConfig())
        repeated, _ = fit_pose_model(duplicated, FeatureConfig())
        self.assertAlmostEqual(model.means[0], 2)
        self.assertAlmostEqual(repeated.means[0], 2)
        self.assertAlmostEqual(model.scales[0], 2)
        # Reading an extreme held-out input never updates the fitted normalizer.
        before = deepcopy(model.to_dict())
        model.predict(feature_person([1000, 0, 0, 0, 0]))
        self.assertEqual(model.to_dict(), before)
        with self.assertRaisesRegex(ValueError, "each training recording"):
            fit_pose_model([samples[0]], FeatureConfig())

    def test_unknown_is_not_a_half_probability_and_raw_overlap_unused(self):
        model = PoseGazeModel([0] * 5, [1] * 5, [0] * 20, 0)
        good = feature_person([0] * 5)
        self.assertEqual(model.predict(good)["gaze_state"], "AMBIGUOUS")
        self.assertEqual(model.predict(good)["gaze_score"], .5)
        for changes in ({"feature_status": "UNKNOWN"}, {"uid_valid": False},
                        {"uid_unique_in_frame": False}, {"head_pose_usable": False},
                        {"pose_gaze_features": None}, {"pose_gaze_features": [float("nan")] * 5}):
            with self.subTest(changes=changes):
                self.assertIsNone(model.predict({**good, **changes})["gaze_score"])
        self.assertEqual(model.predict({**good, "gaze_overlap_raw_finite": .5}), model.predict(good))

    def test_contract_serialization_and_basis_order(self):
        values = [1, 2, 3, 4, 5]
        self.assertEqual(basis(values)[:10], [1, 2, 3, 4, 5, 1, 4, 9, 16, 25])
        self.assertEqual(basis(values)[10:], [2, 3, 4, 5, 6, 8, 10, 12, 15, 20])
        model = PoseGazeModel([0] * 5, [1] * 5, [.01] * 20, 0)
        restored = PoseGazeModel.from_dict(json.loads(json.dumps(model.to_dict())))
        self.assertEqual(model.predict(feature_person(values)), restored.predict(feature_person(values)))
        for changes in ({"model_version": 1}, {"feature_names": []}, {"weights": [0]},
                        {"scales": [0] * 5}, {"direct_min": .2, "away_max": .8}):
            with self.assertRaises(ValueError):
                PoseGazeModel.from_dict({**model.to_dict(), **changes})

    def test_metrics_expose_abstention_and_unknown_coverage(self):
        people = [{"gaze_score": .9, "gaze_state": "DIRECT"},
                  {"gaze_score": .4, "gaze_state": "AMBIGUOUS"},
                  {"gaze_score": None, "gaze_state": "UNKNOWN"}]
        result = metrics(people, [1, 0, 1])
        self.assertAlmostEqual(result["score_coverage"], 2 / 3)
        self.assertAlmostEqual(result["decisive_coverage"], 1 / 3)
        self.assertEqual(result["binary_at_0_5_on_scored_samples"]["balanced_accuracy"], 1)


class ReplayTests(unittest.TestCase):
    def make_manifest(self, root):
        entries = []
        for index in (1, 2):
            name = f"run{index}"
            source = root / f"{name}.jsonl"
            rows = [frame(i * .2, i + 1, [person(2 if i < 6 else 30)], session=name) for i in range(12)]
            source.write_text("".join(json.dumps(r) + "\n" for r in rows))
            labels = {"label_version": 1, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                      "session_id": name, "label_origin": "synthetic actions", "intervals": [
                          {"name": "direct", "start_s": 0, "end_s": 1.2, "uid": 1, "target": "DIRECT", "split": "train"},
                          {"name": "away", "start_s": 1.2, "end_s": 2.3, "uid": 1, "target": "AWAY", "split": "train"}]}
            (root / f"{name}-labels.json").write_text(json.dumps(labels))
            entries.append({"name": name, "source": source.name, "labels": f"{name}-labels.json"})
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"manifest_version": 1, "recordings": entries}))
        return manifest

    def test_train_folds_replay_and_source_protection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.make_manifest(root)
            runs = load_runs(manifest)
            self.assertEqual(len(samples_for(runs[0])), 8)  # 0.3s warm-up per interval.
            report = train(manifest, root / "trained")
            for fold in report["cross_recording_diagnostics"]:
                self.assertNotIn(fold["evaluated_on"], fold["trained_on"])
                expected, _ = fit_pose_model(samples_for(next(r for r in runs if r["name"] == fold["trained_on"][0])), FeatureConfig())
                self.assertEqual(fold["model"], expected.to_dict())
            replay(root / "run2.jsonl", root / "trained/model.json", root / "replay")
            self.assertEqual((root / "trained/run2-scores.jsonl").read_bytes(), (root / "replay/scores.jsonl").read_bytes())
            with self.assertRaises(FileExistsError):
                train(manifest, root / "trained")
            (root / "run1.jsonl").write_text((root / "run1.jsonl").read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_runs(manifest)

    def test_diagnostic_intervals_and_invalid_features_do_not_train(self):
        with tempfile.TemporaryDirectory() as temp:
            runs = load_runs(self.make_manifest(Path(temp)))
            run = runs[0]
            run["labels"]["intervals"][1]["split"] = "diagnostic"
            samples = samples_for(run)
            self.assertTrue(all(s["target"] == 1 for s in samples))
            self.assertEqual(len(samples), 4)


if __name__ == "__main__":
    unittest.main()
