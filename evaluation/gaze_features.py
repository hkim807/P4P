"""Offline, uncalibrated gaze features from full Navel SDK capture JSONL."""

from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

from app.sdk_capture import validate_sdk_capture


COORDINATE_FIELDS = ("g_gaze", "g_eye_left", "g_eye_right", "g_nose", "g_head_position")
RAW_FIELDS = ("uid", "gaze", "gaze_overlap", "head_position", "dist_mm", "id_score",
              "face", "landmarks", *COORDINATE_FIELDS)


def finite_number(value: Any) -> float | None:
    # SDK serialization uses strings for NaN/Infinity; do not coerce those or bools.
    if type(value) not in (int, float):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def vector(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, dict):
        return None
    components = tuple(finite_number(value.get(axis)) for axis in "xyz")
    return components if all(v is not None for v in components) else None


def normalized(value: tuple[float, float, float]) -> tuple[float, float, float]:
    norm = math.hypot(*value)
    return tuple(v / norm for v in value)


@dataclass(frozen=True)
class FeatureConfig:
    reference_direction: tuple[float, float, float] = (0.0, 0.0, -1.0)
    zero_epsilon: float = 1e-8
    window_s: float = 0.3
    max_gap_s: float = 0.5

    def __post_init__(self) -> None:
        for name in ("zero_epsilon", "window_s", "max_gap_s"):
            value = finite_number(getattr(self, name))
            if value is None or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        reference = self.reference_direction
        if (len(reference) != 3 or any(finite_number(v) is None for v in reference)
                or not math.isfinite(math.hypot(*reference))
                or math.hypot(*reference) <= self.zero_epsilon):
            raise ValueError("reference_direction must be a finite nonzero 3-vector")


def person_features(person: dict[str, Any], config: FeatureConfig) -> dict[str, Any]:
    """Extract one sample without assuming gaze/head/coordinate-list frames agree."""
    gaze, head = vector(person.get("gaze")), vector(person.get("head_position"))
    norm = math.hypot(*gaze) if gaze is not None else None
    if norm is not None and not math.isfinite(norm):
        norm = None
    head_zero = head is not None and all(abs(v) <= config.zero_epsilon for v in head)
    overlap = finite_number(person.get("gaze_overlap"))
    suspected_sentinel = (
        overlap == 0.5 and norm is not None and norm <= config.zero_epsilon and head_zero
    )
    reason = None
    if suspected_sentinel:
        reason = "suspected_sdk_default"
    elif norm is None:
        reason = "missing_or_nonfinite_gaze"
    elif norm <= config.zero_epsilon:
        reason = "zero_gaze_vector"

    unit = normalized(gaze) if reason is None else None
    reference = normalized(config.reference_direction)
    angle = None
    if unit is not None:
        dot = sum(a * b for a, b in zip(unit, reference))
        angle = math.degrees(math.acos(max(-1.0, min(1.0, dot))))

    face = person.get("face")
    box = [finite_number(face.get(key)) for key in ("x1", "y1", "x2", "y2")] if isinstance(face, dict) else []
    face_valid = bool(box and all(v is not None for v in box)
                      and box[2] > box[0] and box[3] > box[1])
    distance = finite_number(person.get("dist_mm"))
    uid = person.get("uid")
    return {
        "uid": uid,
        "uid_valid": type(uid) is int and uid > 0,
        "raw": {name: person.get(name) for name in RAW_FIELDS},
        # AVAILABLE means the vector can be used, not that eye contact is established.
        "feature_status": "AVAILABLE" if reason is None else "UNKNOWN",
        "invalid_reason": reason,
        "suspected_sdk_default": suspected_sentinel,
        "gaze_norm": norm,
        "gaze_unit": dict(zip("xyz", unit)) if unit is not None else None,
        "reference_angle_deg": angle,
        "gaze_xz_angle_deg": math.degrees(math.atan2(unit[0], -unit[2])) if unit is not None else None,
        "gaze_elevation_deg": math.degrees(math.atan2(unit[1], math.hypot(unit[0], unit[2]))) if unit is not None else None,
        "gaze_overlap_raw_finite": overlap,
        "gaze_overlap_in_range": overlap is not None and 0 <= overlap <= 1,
        "head_pose_xyz_raw": dict(zip("xyz", head)) if head is not None else None,
        "head_pose_all_zero": head_zero if head is not None else None,
        "head_pose_usable": head is not None and not suspected_sentinel,
        "distance_m": distance / 1000 if distance is not None and distance > 0 else None,
        "id_score_raw_finite": finite_number(person.get("id_score")),
        "face_box_valid": face_valid,
        "face_center_px": [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2] if face_valid else None,
        "face_size_px": [box[2] - box[0], box[3] - box[1]] if face_valid else None,
    }


class GazeFeatureExtractor:
    """Receipt-time rolling medians, isolated by session and positive unique UID."""

    def __init__(self, config: FeatureConfig | None = None) -> None:
        self.config = config or FeatureConfig()
        self._session: str | None = None
        self._start_us: int | None = None
        self._last: tuple[int, int] | None = None
        self._history: dict[int, deque[tuple[int, float]]] = {}

    def extract(self, record: dict[str, Any]) -> dict[str, Any] | None:
        validate_sdk_capture(record)
        if record["stream"] != "perception":
            return None
        people = record["packet"].get("persons")
        if people is not None and (not isinstance(people, list)
                                   or any(not isinstance(p, dict) for p in people)):
            raise ValueError("packet.persons must be a list of objects or null")
        people = people or []
        session = record["session_id"]
        now, sequence = record["received_monotonic_us"], record["sequence"]
        if session != self._session:
            self._session, self._start_us, self._last = session, now, None
            self._history.clear()
        if self._last is not None:
            if now <= self._last[0] or sequence <= self._last[1]:
                raise ValueError("perception receipt times and sequences must strictly increase")
            if sequence != self._last[1] + 1 or (now - self._last[0]) / 1e6 > self.config.max_gap_s:
                self._history.clear()
        self._last = (now, sequence)
        features = [person_features(p, self.config) for p in people]
        counts = Counter(p["uid"] for p in features if p["uid_valid"])
        eligible = {uid for uid, count in counts.items() if count == 1}
        self._history = {uid: history for uid, history in self._history.items() if uid in eligible}
        for p in features:
            uid = p["uid"]
            p["uid_unique_in_frame"] = p["uid_valid"] and counts[uid] == 1
            p.update(reference_angle_median_deg=None, temporal_sample_count=0, temporal_span_s=0.0)
            if not p["uid_unique_in_frame"]:
                continue
            if p["feature_status"] == "UNKNOWN":
                self._history.pop(uid, None)
                continue
            history = self._history.setdefault(uid, deque())
            history.append((now, p["reference_angle_deg"]))
            cutoff = now - self.config.window_s * 1e6
            while history[0][0] < cutoff:
                history.popleft()
            p.update(reference_angle_median_deg=median(value for _, value in history),
                     temporal_sample_count=len(history),
                     temporal_span_s=(now - history[0][0]) / 1e6)
        return {
            "feature_version": 1,
            "session_id": session,
            "sequence": sequence,
            "received_monotonic_us": now,
            "received_unix_us": record["received_unix_us"],
            "source_time_raw": record["packet"].get("time"),
            "elapsed_s": (now - self._start_us) / 1e6,
            "reference_direction": list(normalized(self.config.reference_direction)),
            "reference_frame_verified": False,
            "persons": features,
        }


def distribution(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    values = sorted(values)

    def quantile(q: float) -> float:
        index = (len(values) - 1) * q
        low, high = math.floor(index), math.ceil(index)
        return values[low] + (values[high] - values[low]) * (index - low)

    return {"count": len(values), "min": values[0], "q25": quantile(0.25),
            "median": quantile(0.5), "q75": quantile(0.75), "max": values[-1]}


def extract_recording(source: Path, output_dir: Path,
                      config: FeatureConfig | None = None) -> dict[str, Any]:
    """Write features.jsonl and, only on success, summary.json in a new directory."""
    config = config or FeatureConfig()
    extractor = GazeFeatureExtractor(config)
    counts: Counter = Counter()
    reasons: Counter = Counter()
    angles: list[float] = []
    norms: list[float] = []
    sessions: dict[str, dict[str, Any]] = {}
    digest = hashlib.sha256()
    with source.open("rb") as input_file:
        output_dir.mkdir(parents=True, exist_ok=False)
        with (output_dir / "features.jsonl").open("x", encoding="utf-8") as output:
            for line_number, line in enumerate(input_file, 1):
                digest.update(line)
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    frame = extractor.extract(record)
                except (ValueError, TypeError, KeyError) as error:
                    raise ValueError(f"{source}:{line_number}: {error}") from error
                counts["sdk_records"] += 1
                if frame is None:
                    counts["locomotion_records_skipped"] += 1
                    continue
                output.write(json.dumps(frame, allow_nan=False, separators=(",", ":")) + "\n")
                counts["perception_frames"] += 1
                counts["empty_person_frames"] += not frame["persons"]
                session = sessions.setdefault(frame["session_id"], {
                    "first_received_monotonic_us": frame["received_monotonic_us"],
                    "last_received_monotonic_us": frame["received_monotonic_us"],
                    "person_samples_by_uid": Counter(),
                })
                session["last_received_monotonic_us"] = frame["received_monotonic_us"]
                for p in frame["persons"]:
                    counts["person_samples"] += 1
                    counts["available_samples"] += p["feature_status"] == "AVAILABLE"
                    counts["unknown_samples"] += p["feature_status"] == "UNKNOWN"
                    counts["suspected_sdk_default_samples"] += p["suspected_sdk_default"]
                    counts["invalid_uid_samples"] += not p["uid_valid"]
                    session["person_samples_by_uid"][str(p["uid"])] += 1
                    if p["invalid_reason"]:
                        reasons[p["invalid_reason"]] += 1
                    if p["reference_angle_deg"] is not None:
                        angles.append(p["reference_angle_deg"])
                    if p["gaze_norm"] is not None:
                        norms.append(p["gaze_norm"])
    if not counts["perception_frames"]:
        raise ValueError("input contains no perception frames; no success summary written")
    for session in sessions.values():
        session["duration_s"] = (session["last_received_monotonic_us"]
                                 - session["first_received_monotonic_us"]) / 1e6
    summary = {
        "feature_version": 1,
        "source": str(source.resolve()),
        "source_sha256": digest.hexdigest(),
        "config": asdict(config),
        "counts": dict(counts),
        "invalid_reasons": dict(reasons),
        "reference_angle_deg": distribution(angles),
        "gaze_norm": distribution(norms),
        "sessions": sessions,
        "limitations": [
            "Angles use an unverified reference axis; they are not calibrated eye-contact probabilities.",
            "The 0.5/zero-gaze/zero-head pattern is a suspected SDK default, not vendor-confirmed.",
            "Head pose and coordinate-labelled geometry are retained without assuming shared axes or units.",
            "Smoothing uses receipt time; source clock units and measurement freshness are unverified.",
            "No action labels, gaze classes, score calibration, or live policy changes are applied.",
        ],
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Full SDK capture JSONL, not raw observations")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; existing paths refused")
    parser.add_argument("--reference-direction", type=float, nargs=3, default=(0.0, 0.0, -1.0), metavar=("X", "Y", "Z"))
    parser.add_argument("--window-s", type=float, default=0.3)
    parser.add_argument("--max-gap-s", type=float, default=0.5)
    args = parser.parse_args()
    try:
        config = FeatureConfig(reference_direction=tuple(args.reference_direction),
                               window_s=args.window_s, max_gap_s=args.max_gap_s)
        summary = extract_recording(args.source, args.output_dir, config)
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
    print(json.dumps({"output_dir": str(args.output_dir), "counts": summary["counts"]}, indent=2))


if __name__ == "__main__":
    main()
