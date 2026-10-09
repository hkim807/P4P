"""Fit and replay an experimental gaze alignment score from full SDK captures."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from evaluation.gaze_features import (
    FeatureConfig, GazeFeatureExtractor, distribution, finite_number,
)


FEATURE = "reference_angle_median_deg"
LIMITATIONS = [
    "Experimental alignment score, not a calibrated probability of eye contact.",
    "First-test labels and actor attribution are inferred from the same inspected signals.",
    "Diagnostic phases are excluded from fitting but are not independent validation.",
    "The raw gaze coordinate frame is unverified; off-centre positions and moving robot heads may change the meaning.",
    "Missing gaze remains UNKNOWN; valid-sample results do not describe tracking availability.",
    "Class balancing assumes equal training class weight, not deployment prevalence.",
    "New timestamp-labelled recordings are required before live policy integration.",
]


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1 + exponential)


@dataclass(frozen=True)
class GazeScoreModel:
    intercept: float
    slope: float
    feature_config: FeatureConfig = FeatureConfig()
    away_max: float = 0.3
    direct_min: float = 0.7

    def __post_init__(self) -> None:
        if any(finite_number(v) is None for v in (self.intercept, self.slope, self.away_max, self.direct_min)):
            raise ValueError("model coefficients and thresholds must be finite")
        if self.slope > 0:
            raise ValueError("slope must be nonpositive: alignment must decrease with angle")
        if not 0 <= self.away_max < self.direct_min <= 1:
            raise ValueError("require 0 <= away_max < direct_min <= 1")

    def score_angle(self, angle: float) -> float:
        if finite_number(angle) is None or not 0 <= angle <= 180:
            raise ValueError("angle must be finite and in [0, 180]")
        return sigmoid(self.intercept + self.slope * angle / 10)

    def predict(self, person: dict[str, Any]) -> dict[str, Any]:
        reason = None
        angle = finite_number(person.get(FEATURE))
        if person.get("feature_status") != "AVAILABLE":
            reason = person.get("invalid_reason") or "unavailable_gaze"
        elif not person.get("uid_valid") or not person.get("uid_unique_in_frame"):
            reason = "invalid_or_duplicate_uid"
        elif angle is None or not 0 <= angle <= 180:
            reason = "missing_or_invalid_angle"
        if reason:
            return {"gaze_score": None, "gaze_state": "UNKNOWN", "score_reason": reason}
        score = self.score_angle(angle)
        state = "DIRECT" if score >= self.direct_min else "AWAY" if score <= self.away_max else "AMBIGUOUS"
        return {"gaze_score": score, "gaze_state": state, "score_reason": "experimental_alignment"}

    def to_dict(self) -> dict[str, Any]:
        return {"model_version": 1, "feature_version": 1, "model_type": "monotone_logistic_angle",
                "feature_name": FEATURE, "angle_scale_deg": 10,
                "probability_calibrated": False, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GazeScoreModel:
        expected = {"model_version": 1, "feature_version": 1,
                    "model_type": "monotone_logistic_angle", "feature_name": FEATURE,
                    "angle_scale_deg": 10, "probability_calibrated": False}
        if any(data.get(key) != value for key, value in expected.items()):
            raise ValueError("unsupported gaze score model contract")
        return cls(intercept=data["intercept"], slope=data["slope"],
                   feature_config=FeatureConfig(**data["feature_config"]),
                   away_max=data["away_max"], direct_min=data["direct_min"])


def fit_model(samples: list[tuple[float, int]], config: FeatureConfig) -> GazeScoreModel:
    """Class-balanced logistic loss, fixed slope L2=0.01, projected gradient fit."""
    counts = Counter(target for _, target in samples)
    if set(counts) != {0, 1} or min(counts.values()) < 2:
        raise ValueError("training needs at least two usable samples of each class")
    if any(finite_number(angle) is None or not 0 <= angle <= 180 for angle, _ in samples):
        raise ValueError("training angles must be finite and in [0, 180]")
    weighted = [(angle / 10, target, 0.5 / counts[target]) for angle, target in samples]
    regularization = 0.01
    # Conservative step from a global Hessian bound, with the slope projected <= 0.
    step = 1 / (0.25 * sum(weight * (1 + x * x) for x, _, weight in weighted) + regularization)
    intercept, slope = 0.0, -1.0
    for _ in range(5000):
        db, dw = 0.0, regularization * slope
        for x, target, weight in weighted:
            error = weight * (sigmoid(intercept + slope * x) - target)
            db += error
            dw += error * x
        next_intercept, next_slope = intercept - step * db, min(0.0, slope - step * dw)
        change = max(abs(next_intercept - intercept), abs(next_slope - slope))
        intercept, slope = next_intercept, next_slope
        if change < 1e-9:
            break
    return GazeScoreModel(intercept, slope, config)


def read_features(source: Path, config: FeatureConfig) -> tuple[list[dict], str]:
    extractor = GazeFeatureExtractor(config)
    frames = []
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            digest.update(line)
            if not line.strip():
                continue
            try:
                frame = extractor.extract(json.loads(line))
            except (ValueError, TypeError, KeyError) as error:
                raise ValueError(f"{source}:{number}: {error}") from error
            if frame is not None:
                frames.append(frame)
    if not frames:
        raise ValueError("input has no perception frames")
    return frames, digest.hexdigest()


def validate_labels(labels: dict, source_sha256: str, frames: list[dict]) -> None:
    if labels.get("label_version") != 1 or labels.get("source_sha256") != source_sha256:
        raise ValueError("labels must have version 1 and match the SDK source SHA-256")
    if not isinstance(labels.get("label_origin"), str) or not labels["label_origin"].strip():
        raise ValueError("labels must declare label_origin")
    if {frame["session_id"] for frame in frames} != {labels.get("session_id")}:
        raise ValueError("label session must match the single recorded session")
    intervals = labels.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        raise ValueError("labels require nonempty intervals")
    end, names = 0.0, set()
    for interval in intervals:
        start, stop = finite_number(interval.get("start_s")), finite_number(interval.get("end_s"))
        if start is None or stop is None or not end <= start < stop:
            raise ValueError("label intervals must be ordered, nonoverlapping, nonnegative and nonempty")
        if start > frames[-1]["elapsed_s"] or stop > frames[-1]["elapsed_s"] + 1:
            raise ValueError("label interval is outside the recording")
        name = interval.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("interval names must be nonempty and unique")
        if interval.get("target") not in ("DIRECT", "AWAY") or interval.get("split") not in ("train", "diagnostic"):
            raise ValueError("interval target/split is unsupported")
        if type(interval.get("uid")) is not int or interval["uid"] <= 0:
            raise ValueError("each interval requires a positive actor uid")
        names.add(name)
        end = stop


def interval_people(frames: list[dict], interval: dict, warmup_s: float = 0) -> list[dict]:
    return [person for frame in frames
            if interval["start_s"] + warmup_s <= frame["elapsed_s"] < interval["end_s"]
            for person in frame["persons"] if person["uid"] == interval["uid"]]


def training_samples(frames: list[dict], labels: dict, config: FeatureConfig) -> list[tuple[float, int]]:
    samples = []
    for interval in labels["intervals"]:
        if interval["split"] != "train":
            continue
        # Do not let the rolling window pull observations across the label boundary.
        people = interval_people(frames, interval, warmup_s=config.window_s)
        usable = [p for p in people if p["feature_status"] == "AVAILABLE"
                  and p["uid_valid"] and p["uid_unique_in_frame"] and finite_number(p.get(FEATURE)) is not None]
        if not usable:
            raise ValueError(f"no usable training samples for {interval['name']}")
        samples.extend((p[FEATURE], int(interval["target"] == "DIRECT")) for p in usable)
    return samples


def score_summary(people: list[dict]) -> dict:
    scores = [p["gaze_score"] for p in people if p["gaze_score"] is not None]
    return {"person_samples": len(people), "scored_samples": len(scores),
            "score_coverage": len(scores) / len(people) if people else None,
            "states": {state: sum(p["gaze_state"] == state for p in people)
                       for state in ("DIRECT", "AWAY", "AMBIGUOUS", "UNKNOWN")},
            "score_distribution": distribution(scores)}


def write_outputs(output: Path, frames: list[dict], artifact: dict, source: Path,
                  source_sha256: str, labels: dict | None = None) -> dict:
    model = GazeScoreModel.from_dict(artifact)
    model_sha256 = hashlib.sha256(json.dumps(artifact, sort_keys=True).encode()).hexdigest()
    scored = [{**frame, "score_model_version": 1, "score_model_sha256": model_sha256,
               "persons": [{**p, **model.predict(p)} for p in frame["persons"]]} for frame in frames]
    report = {"source": str(source.resolve()), "source_sha256": source_sha256,
              "model_sha256": model_sha256,
              "same_source_as_training": source_sha256 == artifact.get("training", {}).get("source_sha256"),
              "perception_frames": len(scored),
              "empty_person_frames": sum(not f["persons"] for f in scored),
              "overall": score_summary([p for f in scored for p in f["persons"]]),
              "limitations": LIMITATIONS}
    if labels:
        report["label_origin"] = labels["label_origin"]
        report["phases"] = [{**interval, **score_summary(interval_people(scored, interval))}
                            for interval in labels["intervals"]]
    output.mkdir(parents=True, exist_ok=False)
    with (output / "scores.jsonl").open("x", encoding="utf-8") as handle:
        for frame in scored:
            handle.write(json.dumps(frame, allow_nan=False, separators=(",", ":")) + "\n")
    for name, data in (("model.json", artifact), ("report.json", report)):
        (output / name).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def train(source: Path, label_path: Path, output: Path) -> dict:
    config = FeatureConfig()
    frames, source_sha256 = read_features(source, config)
    labels = json.loads(label_path.read_text(encoding="utf-8"))
    validate_labels(labels, source_sha256, frames)
    samples = training_samples(frames, labels, config)
    model = fit_model(samples, config)
    artifact = {**model.to_dict(), "training": {
        "source_sha256": source_sha256, "labels": labels,
        "usable_samples": len(samples),
        "class_counts": {label: sum(target == number for _, target in samples)
                         for number, label in ((0, "AWAY"), (1, "DIRECT"))},
        "weighting": "equal total weight for each class", "slope_l2": 0.01,
        "boundary_exclusion_s": config.window_s,
        "independently_validated": False,
    }, "limitations": LIMITATIONS}
    return write_outputs(output, frames, artifact, source, source_sha256, labels)


def replay(source: Path, model_path: Path, output: Path, label_path: Path | None = None) -> dict:
    artifact = json.loads(model_path.read_text(encoding="utf-8"))
    model = GazeScoreModel.from_dict(artifact)
    frames, source_sha256 = read_features(source, model.feature_config)
    labels = json.loads(label_path.read_text(encoding="utf-8")) if label_path else None
    if labels is not None:
        validate_labels(labels, source_sha256, frames)
    return write_outputs(output, frames, artifact, source, source_sha256, labels)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("train", "score"):
        sub = commands.add_parser(command)
        sub.add_argument("source", type=Path, help="Full SDK JSONL; extraction runs automatically")
        sub.add_argument("--output-dir", type=Path, required=True)
        sub.add_argument("--labels", type=Path, required=command == "train")
        if command == "score":
            sub.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output_dir.exists():
            raise FileExistsError(f"output directory already exists: {args.output_dir}")
        if args.command == "train":
            report = train(args.source, args.labels, args.output_dir)
        else:
            report = replay(args.source, args.model, args.output_dir, args.labels)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"error: {error}\n")
    print(json.dumps({"output_dir": str(args.output_dir), "overall": report["overall"]}, indent=2))


if __name__ == "__main__":
    main()
