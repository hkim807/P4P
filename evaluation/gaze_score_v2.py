"""Experimental multi-recording gaze/head-pose logistic scoring; stdlib only."""

from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from evaluation.gaze_features import FeatureConfig, finite_number, vector
from evaluation.gaze_score import (
    GazeScoreModel, interval_people, read_features, score_summary, sigmoid, validate_labels,
)


FEATURE_NAMES = ["gaze_xz_deg", "gaze_elevation_deg", "head_x_raw", "head_y_raw", "head_z_raw"]
L2 = 0.05
LIMITATIONS = [
    "Experimental alignment score, not a calibrated probability of eye contact.",
    "Both recordings' action intervals are provisional and informed by inspection of their signals.",
    "Later head-action intervals in calibration 1 are excluded because their timing cannot be resolved.",
    "Cross-recording diagnostics use disjoint fitting data but are not independent validation of the feature design or labels.",
    "The final combined model has seen both recordings; its per-phase results are fitting diagnostics.",
    "SDK gaze/head axes and units remain unverified; learned associations are not physical coordinate compensation.",
    "Unknown gaze or head pose remains UNKNOWN; the model never fills missing measurements with 0.5.",
    "An unseen, independently timestamp-labelled trial is required before live policy integration.",
]


def add_pose_features(frames: list[dict], config: FeatureConfig) -> list[dict]:
    """Median five raw features together, resetting on base-extractor gaps and missing head pose."""
    history: dict[int, deque] = {}
    for frame in frames:
        now = frame["received_monotonic_us"]
        eligible = {p["uid"] for p in frame["persons"] if p["uid_valid"] and p["uid_unique_in_frame"]}
        history = {uid: values for uid, values in history.items() if uid in eligible}
        for p in frame["persons"]:
            p["pose_gaze_features"] = None
            p["pose_gaze_sample_count"] = 0
            p["pose_gaze_span_s"] = 0.0
            if not p["uid_valid"] or not p["uid_unique_in_frame"]:
                p["pose_gaze_reason"] = "invalid_or_duplicate_uid"
                continue
            uid = p["uid"]
            head = vector(p.get("head_pose_xyz_raw"))
            horizontal, vertical = p.get("gaze_xz_angle_deg"), p.get("gaze_elevation_deg")
            reason = None
            if p["feature_status"] != "AVAILABLE":
                reason = p["invalid_reason"] or "unavailable_gaze"
            elif not p.get("head_pose_usable") or head is None:
                reason = "missing_or_invalid_head_pose"
            elif finite_number(horizontal) is None or finite_number(vertical) is None:
                reason = "missing_or_invalid_angles"
            p["pose_gaze_reason"] = reason
            if reason:
                history.pop(uid, None)
                continue
            # Base extractor already isolates UID, sessions, receipt/sequence gaps.
            if p["temporal_sample_count"] <= 1:
                history.pop(uid, None)
            values = history.setdefault(uid, deque())
            values.append((now, [horizontal, vertical, *head]))
            while values[0][0] < now - config.window_s * 1e6:
                values.popleft()
            p["pose_gaze_features"] = [median(row[i] for _, row in values) for i in range(5)]
            p["pose_gaze_sample_count"] = len(values)
            p["pose_gaze_span_s"] = (now - values[0][0]) / 1e6
    return frames


def basis(values: list[float]) -> list[float]:
    """Five standardized inputs, their squares and ten pairwise interactions."""
    return values + [v * v for v in values] + [values[i] * values[j] for i in range(5) for j in range(i + 1, 5)]


@dataclass(frozen=True)
class PoseGazeModel:
    means: list[float]
    scales: list[float]
    weights: list[float]
    intercept: float
    feature_config: FeatureConfig = FeatureConfig()
    away_max: float = 0.3
    direct_min: float = 0.7

    def __post_init__(self) -> None:
        if len(self.means) != 5 or len(self.scales) != 5 or len(self.weights) != 20:
            raise ValueError("V2 requires five raw features and twenty basis coefficients")
        if any(finite_number(v) is None for v in [*self.means, *self.scales, *self.weights,
                                                self.intercept, self.away_max, self.direct_min]):
            raise ValueError("model parameters must be finite")
        if any(v <= 0 for v in self.scales) or not 0 <= self.away_max < self.direct_min <= 1:
            raise ValueError("invalid scales or thresholds")

    def predict(self, person: dict) -> dict:
        values = person.get("pose_gaze_features")
        reason = person.get("pose_gaze_reason")
        if person.get("feature_status") != "AVAILABLE":
            reason = person.get("invalid_reason") or "unavailable_gaze"
        elif not person.get("uid_valid") or not person.get("uid_unique_in_frame"):
            reason = "invalid_or_duplicate_uid"
        elif not person.get("head_pose_usable"):
            reason = "missing_or_invalid_head_pose"
        if reason or not isinstance(values, list) or len(values) != 5 or any(finite_number(v) is None for v in values):
            return {"gaze_score": None, "gaze_state": "UNKNOWN", "score_reason": reason or "missing_pose_gaze_features"}
        scaled = [(v - mean) / scale for v, mean, scale in zip(values, self.means, self.scales)]
        terms = basis(scaled)
        logit = self.intercept + sum(w * x for w, x in zip(self.weights, terms))
        if not math.isfinite(logit):
            return {"gaze_score": None, "gaze_state": "UNKNOWN", "score_reason": "nonfinite_model_input"}
        score = sigmoid(logit)
        state = "DIRECT" if score >= self.direct_min else "AWAY" if score <= self.away_max else "AMBIGUOUS"
        return {"gaze_score": score, "gaze_state": state, "score_reason": "experimental_pose_gaze"}

    def to_dict(self) -> dict:
        return {"model_version": 2, "feature_version": 1, "model_type": "quadratic_pose_gaze_logistic",
                "feature_names": FEATURE_NAMES, "basis": "linear_then_squares_then_i_lt_j_products",
                "probability_calibrated": False, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> PoseGazeModel:
        contract = {"model_version": 2, "feature_version": 1, "model_type": "quadratic_pose_gaze_logistic",
                    "feature_names": FEATURE_NAMES, "basis": "linear_then_squares_then_i_lt_j_products",
                    "probability_calibrated": False}
        if any(data.get(k) != v for k, v in contract.items()):
            raise ValueError("unsupported V2 model contract")
        return cls(means=data["means"], scales=data["scales"], weights=data["weights"],
                   intercept=data["intercept"], feature_config=FeatureConfig(**data["feature_config"]),
                   away_max=data["away_max"], direct_min=data["direct_min"])


def samples_for(run: dict) -> list[dict]:
    samples = []
    for interval in run["labels"]["intervals"]:
        if interval["split"] != "train":
            continue
        people = interval_people(run["frames"], interval, warmup_s=run["config"].window_s)
        samples.extend({"x": p["pose_gaze_features"], "target": int(interval["target"] == "DIRECT"),
                        "run": run["name"], "phase": interval["name"]}
                       for p in people if p["pose_gaze_features"] is not None)
    return samples


def sample_weights(samples: list[dict]) -> list[float]:
    """Equal run weight, then equal class weight, then equal phase weight."""
    counts = Counter((s["run"], s["target"], s["phase"]) for s in samples)
    runs = {s["run"] for s in samples}
    if not runs or any({s["target"] for s in samples if s["run"] == run} != {0, 1} for run in runs):
        raise ValueError("each training recording needs usable DIRECT and AWAY samples")
    phases = Counter((run, target) for run, target, _ in counts)
    return [1 / (len(runs) * 2 * phases[s["run"], s["target"]]
                 * counts[s["run"], s["target"], s["phase"]]) for s in samples]


def fit_pose_model(samples: list[dict], config: FeatureConfig) -> tuple[PoseGazeModel, dict]:
    if any(len(s["x"]) != 5 or any(finite_number(v) is None for v in s["x"])
           or type(s["target"]) is not int or s["target"] not in (0, 1) for s in samples):
        raise ValueError("training needs finite five-component vectors and binary targets")
    weights = sample_weights(samples)
    # Means/scales are fitted only on the data supplied to this fold.
    means = [sum(w * s["x"][i] for w, s in zip(weights, samples)) for i in range(5)]
    scales = [max(math.sqrt(sum(w * (s["x"][i] - means[i]) ** 2 for w, s in zip(weights, samples))),
                  1e-3) for i in range(5)]
    design = [basis([(v - m) / scale for v, m, scale in zip(s["x"], means, scales)]) for s in samples]
    step = 1 / (0.25 * sum(w * (1 + sum(v * v for v in row)) for w, row in zip(weights, design)) + L2)
    coefficients, intercept = [0.0] * 20, 0.0
    change = float("inf")
    for iteration in range(4000):
        gradient, db = [L2 * c for c in coefficients], 0.0
        for row, s, weight in zip(design, samples, weights):
            error = weight * (sigmoid(intercept + sum(c * x for c, x in zip(coefficients, row))) - s["target"])
            db += error
            for i, x in enumerate(row):
                gradient[i] += error * x
        change = max(abs(db), max(abs(g) for g in gradient))
        if change < 1e-6:
            break
        intercept -= step * db
        coefficients = [c - step * g for c, g in zip(coefficients, gradient)]
    model = PoseGazeModel(means, scales, coefficients, intercept, config)
    return model, {"iterations": iteration + 1, "max_gradient": change, "converged": change < 1e-6,
                   "usable_samples": len(samples), "class_counts": dict(Counter(s["target"] for s in samples)),
                   "slope_l2": L2, "weighting": "equal recording, equal class within recording, equal phase within class"}


def metrics(people: list[dict], targets: list[int]) -> dict:
    result = score_summary(people)
    scored = [(p, y) for p, y in zip(people, targets) if p["gaze_score"] is not None]
    tp = sum(y == 1 and p["gaze_score"] >= .5 for p, y in scored)
    fn = sum(y == 1 and p["gaze_score"] < .5 for p, y in scored)
    tn = sum(y == 0 and p["gaze_score"] < .5 for p, y in scored)
    fp = sum(y == 0 and p["gaze_score"] >= .5 for p, y in scored)
    result["binary_at_0_5_on_scored_samples"] = {"tp": tp, "fn": fn, "tn": tn, "fp": fp,
        "balanced_accuracy": ((tp / (tp + fn) + tn / (tn + fp)) / 2) if tp + fn and tn + fp else None}
    decisions = [(p, y) for p, y in scored if p["gaze_state"] in ("DIRECT", "AWAY")]
    result["decisive_samples"] = len(decisions)
    result["decisive_coverage"] = len(decisions) / len(people) if people else None
    result["decisive_accuracy"] = sum((p["gaze_state"] == "DIRECT") == bool(y) for p, y in decisions) / len(decisions) if decisions else None
    return result


def evaluate(run: dict, model) -> dict:
    phases, all_people, targets = [], [], []
    for interval in run["labels"]["intervals"]:
        if interval["split"] != "train":
            continue
        people = interval_people(run["frames"], interval, warmup_s=run["config"].window_s)
        predictions = [{**p, **model.predict(p)} for p in people]
        truth = [int(interval["target"] == "DIRECT")] * len(predictions)
        phases.append({**interval, **metrics(predictions, truth)})
        all_people.extend(predictions)
        targets.extend(truth)
    return {"recording": run["name"], "phases": phases, "overall": metrics(all_people, targets)}


def load_runs(manifest_path: Path) -> list[dict]:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("manifest_version") != 1 or len(manifest.get("recordings", [])) < 2:
        raise ValueError("manifest must contain at least two recordings")
    runs, names, hashes = [], set(), set()
    for entry in manifest["recordings"]:
        name = entry["name"]
        if not isinstance(name, str) or not name or name in names or not all(c.isalnum() or c in "_-" for c in name):
            raise ValueError("recording names must be unique and use letters, digits, underscores or hyphens")
        source = (manifest_path.parent / entry["source"]).resolve()
        labels = json.loads((manifest_path.parent / entry["labels"]).read_text())
        config = FeatureConfig()
        frames, digest = read_features(source, config)
        validate_labels(labels, digest, frames)
        if digest in hashes:
            raise ValueError("duplicate recording cannot be used as an independent fold")
        names.add(name)
        hashes.add(digest)
        runs.append({"name": name, "source": str(source), "source_sha256": digest, "labels": labels,
                     "config": config, "frames": add_pose_features(frames, config)})
    return runs


def dump(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_scores(path: Path, frames: list[dict], model: PoseGazeModel, artifact: dict) -> dict:
    digest = hashlib.sha256(json.dumps(artifact, sort_keys=True).encode()).hexdigest()
    people = []
    with path.open("x", encoding="utf-8") as handle:
        for frame in frames:
            predictions = [{**p, **model.predict(p)} for p in frame["persons"]]
            output = {**frame, "score_model_version": 2, "score_model_sha256": digest, "persons": predictions}
            handle.write(json.dumps(output, allow_nan=False, separators=(",", ":")) + "\n")
            people.extend(predictions)
    return score_summary(people)


def train(manifest: Path, output: Path, baseline_path: Path | None = None) -> dict:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    runs = load_runs(manifest)
    baseline = GazeScoreModel.from_dict(json.loads(baseline_path.read_text())) if baseline_path else None
    if baseline and asdict(baseline.feature_config) != asdict(runs[0]["config"]):
        # JSON round-trips turn reference tuples into lists; compare JSON forms.
        if json.dumps(asdict(baseline.feature_config), sort_keys=True) != json.dumps(asdict(runs[0]["config"]), sort_keys=True):
            raise ValueError("baseline feature configuration must match for a fair comparison")
    folds = []
    for held in runs:
        fitting = [r for r in runs if r["name"] != held["name"]]
        model, fit = fit_pose_model([s for r in fitting for s in samples_for(r)], runs[0]["config"])
        folds.append({"trained_on": [r["name"] for r in fitting], "evaluated_on": held["name"],
                      "fit": fit, "model": model.to_dict(), "v2": evaluate(held, model),
                      "v1": evaluate(held, baseline) if baseline else None})
    model, fit = fit_pose_model([s for r in runs for s in samples_for(r)], runs[0]["config"])
    artifact = {**model.to_dict(), "training": {"fit": fit, "independently_validated": False,
        "boundary_exclusion_s": model.feature_config.window_s,
        "recordings": [{k: r[k] for k in ("name", "source_sha256", "labels")} for r in runs]},
        "limitations": LIMITATIONS}
    report = {"model_version": 2, "limitations": LIMITATIONS, "cross_recording_diagnostics": folds,
              "combined_fit_diagnostics": [{"v2": evaluate(r, model), "v1": evaluate(r, baseline) if baseline else None} for r in runs]}
    output.mkdir(parents=True, exist_ok=False)
    dump(output / "model.json", artifact)
    for run in runs:
        summary = write_scores(output / f"{run['name']}-scores.jsonl", run["frames"], model, artifact)
        report.setdefault("all_frame_scoring", {})[run["name"]] = summary
    dump(output / "report.json", report)
    return report


def replay(source: Path, model_path: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    artifact = json.loads(model_path.read_text())
    model = PoseGazeModel.from_dict(artifact)
    frames, digest = read_features(source, model.feature_config)
    add_pose_features(frames, model.feature_config)
    output.mkdir(parents=True, exist_ok=False)
    summary = write_scores(output / "scores.jsonl", frames, model, artifact)
    report = {"source_sha256": digest, "overall": summary, "limitations": LIMITATIONS,
              "same_source_as_training": digest in {r["source_sha256"] for r in artifact.get("training", {}).get("recordings", [])}}
    dump(output / "model.json", artifact)
    dump(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fit = commands.add_parser("train")
    fit.add_argument("manifest", type=Path)
    fit.add_argument("--baseline-model", type=Path)
    fit.add_argument("--output-dir", type=Path, required=True)
    score = commands.add_parser("score")
    score.add_argument("source", type=Path)
    score.add_argument("--model", type=Path, required=True)
    score.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "train":
            report = train(args.manifest, args.output_dir, args.baseline_model)
            result = {"output_dir": str(args.output_dir), "folds": [
                {"evaluated_on": f["evaluated_on"], "v2": f["v2"]["overall"]["binary_at_0_5_on_scored_samples"],
                 "fit": f["fit"]} for f in report["cross_recording_diagnostics"]]}
        else:
            result = replay(args.source, args.model, args.output_dir)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"error: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
