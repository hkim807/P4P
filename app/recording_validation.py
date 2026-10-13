"""Validate closed SDK captures and trim them at measured motion onset."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import tempfile
from typing import Any

from app.sdk_capture import validate_sdk_capture


STREAMS = ("perception", "locomotion")


@dataclass(frozen=True)
class ValidationConfig:
    max_gap_s: float = 0.25
    source_gap_factor: float = 1.5
    linear_threshold_mps: float = 0.02
    angular_threshold_rps: float = 0.03

    def __post_init__(self):
        for name, value in asdict(self).items():
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.max_gap_s <= 0 or self.source_gap_factor <= 1:
            raise ValueError("max_gap_s must be positive and source_gap_factor greater than 1")
        if self.linear_threshold_mps < 0 or self.angular_threshold_rps < 0:
            raise ValueError("movement thresholds must be nonnegative")


@dataclass
class Inspection:
    report: dict[str, Any]
    records: list[tuple[int, dict[str, Any]]] = field(default_factory=list, repr=False)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"non-finite JSON number: {value}")


def inspect_recording(path: str | Path, config: ValidationConfig | None = None) -> Inspection:
    """Validate both streams before permitting any trimmed output.

    Capture sequence gaps prove received packets are missing. Native SDK time
    gaps only suggest missing generated samples; units are not assumed. Unequal
    stream counts are allowed because perception and locomotion are independent.
    """
    path = Path(path)
    config = config or ValidationConfig()
    report = {"input": str(path.resolve()), "validation": "INVALID", "status": "INVALID",
              "source_sha256": None, "settings": asdict(config), "issues": [],
              "issue_counts": {}, "error_count": 0, "warning_count": 0,
              "streams": {}, "motion_start": None, "trimmed_output": None}
    inspection = Inspection(report)
    counts = Counter()
    session = None
    last_receipt = None
    digest = hashlib.sha256()
    metadata = {name: {"packet_count": 0, "first_sequence": None, "last_sequence": None,
                      "first_receipt_us": None, "last_receipt_us": None,
                      "max_receipt_gap_s": 0.0, "missing_capture_packets": 0,
                      "source_timestamp_count": 0, "missing_source_timestamp_count": 0,
                      "nominal_source_interval": None, "estimated_missing_sdk_samples": 0}
                for name in STREAMS}
    source_deltas = {name: [] for name in STREAMS}
    last_source = {}

    def issue(code, message, line=None, stream=None, *, severity="error", **details):
        counts[code] += 1
        report[f"{severity}_count"] += 1
        # Keep reports manageable for completely malformed historical files.
        if len(report["issues"]) < 200:
            report["issues"].append({"code": code, "severity": severity,
                "line": line, "stream": stream, "message": message, **details})

    try:
        with path.open("rb") as source:
            for line_number, line in enumerate(source, 1):
                digest.update(line)
                if not line.strip():
                    continue
                try:
                    record = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                                        parse_constant=_reject_constant)
                    validate_sdk_capture(record)
                except (ValueError, UnicodeError, RecursionError) as error:
                    issue("INVALID_PACKET", str(error), line_number)
                    continue
                inspection.records.append((line_number, record))
                stream, sequence, receipt = (record[key] for key in
                                            ("stream", "sequence", "received_monotonic_us"))
                if session is None:
                    session = record["session_id"]
                elif record["session_id"] != session:
                    issue("SESSION_CHANGED", "A recording must contain one capture session",
                          line_number, stream)
                if last_receipt is not None and receipt < last_receipt:
                    issue("RECEIPT_ORDER", "File receipt timestamps moved backwards",
                          line_number, stream)
                last_receipt = receipt
                info = metadata[stream]
                expected = 1 if info["last_sequence"] is None else info["last_sequence"] + 1
                if sequence != expected:
                    missing = max(0, sequence - expected)
                    info["missing_capture_packets"] += missing
                    issue("SEQUENCE_GAP" if missing else "SEQUENCE_ORDER",
                          f"Expected {stream} sequence {expected}, found {sequence}",
                          line_number, stream, missing_packets=missing)
                if info["last_receipt_us"] is not None:
                    gap = (receipt - info["last_receipt_us"]) / 1e6
                    info["max_receipt_gap_s"] = max(info["max_receipt_gap_s"], gap)
                    if gap <= 0:
                        issue("STREAM_RECEIPT_ORDER", "Stream receipt timestamps must increase",
                              line_number, stream)
                    elif gap > config.max_gap_s:
                        issue("RECEIPT_GAP", "Packet receipt gap exceeds configured limit",
                              line_number, stream, gap_s=gap)
                if info["packet_count"] == 0:
                    info["first_sequence"], info["first_receipt_us"] = sequence, receipt
                info["packet_count"] += 1
                info["last_sequence"], info["last_receipt_us"] = sequence, receipt
                packet = record["packet"]
                if stream == "perception":
                    if "persons" not in packet or (packet["persons"] is not None
                                                     and not isinstance(packet["persons"], list)):
                        issue("MISSING_PERCEPTION_DATA", "Perception packet needs a persons list or null",
                              line_number, stream)
                    elif any(not isinstance(person, dict) for person in packet["persons"] or []):
                        issue("INVALID_PERSON", "Each detected person must be an object",
                              line_number, stream)
                    source_time = packet.get("time")
                else:
                    odometry = packet.get("odometry")
                    if not isinstance(odometry, dict):
                        odometry = {}
                    source_time = odometry.get("time")
                    velocity = odometry.get("velocity")
                    if not isinstance(velocity, dict):
                        velocity = {}
                    linear, angular = velocity.get("linear_x"), velocity.get("angular_z")
                    if not (_finite(linear) and _finite(angular)):
                        issue("MISSING_VELOCITY", "Locomotion needs finite linear_x and angular_z to locate motion",
                              line_number, stream)
                    elif report["motion_start"] is None and (
                            abs(linear) > config.linear_threshold_mps
                            or abs(angular) > config.angular_threshold_rps):
                        report["motion_start"] = {"source_line": line_number,
                            "source_record_index": len(inspection.records) - 1,
                            "received_monotonic_us": receipt, "received_unix_us": record["received_unix_us"],
                            "original_sequence": sequence, "linear_x_mps": linear,
                            "angular_z_rps": angular}
                if _finite(source_time) and source_time >= 0:
                    info["source_timestamp_count"] += 1
                    if stream in last_source:
                        previous_time, previous_line = last_source[stream]
                        delta = source_time - previous_time
                        if delta <= 0:
                            issue("SDK_TIME_ORDER", "SDK timestamps repeated or moved backwards",
                                  line_number, stream, previous_line=previous_line)
                        else:
                            source_deltas[stream].append((line_number, previous_line, delta))
                    last_source[stream] = source_time, line_number
                else:
                    info["missing_source_timestamp_count"] += 1
                    last_source.pop(stream, None)
        report["source_sha256"] = digest.hexdigest()
    except OSError as error:
        issue("READ_ERROR", str(error))

    if not inspection.records:
        issue("EMPTY_CAPTURE", "No valid SDK packets found")
    for stream, info in metadata.items():
        if not info["packet_count"]:
            issue("MISSING_STREAM", f"No {stream} packets", stream=stream)
        if info["missing_source_timestamp_count"]:
            issue("UNAVAILABLE_SDK_TIMING", "Some SDK timestamps are unavailable; upstream loss cannot be checked there",
                  stream=stream, severity="warning", packets=info["missing_source_timestamp_count"])
        deltas = source_deltas[stream]
        if len(deltas) >= 3:
            nominal = median(delta for _, _, delta in deltas)
            info["nominal_source_interval"] = nominal
            for line, previous_line, delta in deltas:
                if delta > nominal * config.source_gap_factor:
                    missing = max(1, round(delta / nominal) - 1)
                    info["estimated_missing_sdk_samples"] += missing
                    issue("SDK_TIMESTAMP_GAP", "SDK timestamp gap suggests skipped sensor samples",
                          line, stream, previous_line=previous_line,
                          interval_ratio=delta / nominal, estimated_missing_samples=missing)
        elif info["packet_count"]:
            issue("INSUFFICIENT_SDK_TIMING", "Too few SDK intervals to infer the native sampling cadence",
                  stream=stream, severity="warning")
    report["streams"] = metadata
    report["session_id"] = session
    report["issue_counts"] = dict(counts)
    report["issues_truncated"] = sum(counts.values()) > len(report["issues"])
    if inspection.records:
        first_us = inspection.records[0][1]["received_monotonic_us"]
        last_us = inspection.records[-1][1]["received_monotonic_us"]
        report["duration_s"] = (last_us - first_us) / 1e6
        for stream, info in metadata.items():
            if not info["packet_count"]:
                continue
            for code, gap in (("STREAM_START_GAP", (info["first_receipt_us"] - first_us) / 1e6),
                              ("STREAM_END_GAP", (last_us - info["last_receipt_us"]) / 1e6)):
                if gap > config.max_gap_s:
                    issue(code, "Stream does not cover the recording boundary within the gap limit",
                          stream=stream, gap_s=gap)
        if report["motion_start"] is not None:
            report["motion_start"]["offset_s"] = (report["motion_start"]["received_monotonic_us"] - first_us) / 1e6
    report["issue_counts"] = dict(counts)
    report["issues_truncated"] = sum(counts.values()) > len(report["issues"])
    if report["error_count"] == 0:
        report["validation"] = "VALID_WITH_WARNINGS" if report["warning_count"] else "VALID"
        report["status"] = "READY_TO_TRIM" if report["motion_start"] else "NO_MOVEMENT"
    return inspection


def trim_recording(inspection: Inspection, output_path: str | Path) -> dict[str, Any]:
    """Write a validated suffix, rebasing only envelope sequences for replay.

    The first record is the locomotion packet proving movement. Source packets,
    session identity and every original timestamp remain unchanged. A manifest
    records the original first sequence of each stream for reconstruction.
    """
    report = inspection.report
    if report["validation"] == "INVALID" or report["motion_start"] is None:
        raise ValueError("Only valid recordings with measured movement can be trimmed")
    output_path = Path(output_path)
    if output_path.resolve() == Path(report["input"]).resolve():
        raise ValueError("Trimmed output cannot replace the original recording")
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(f"Output already exists: {output_path}")
    kept = inspection.records[report["motion_start"]["source_record_index"]:]
    counts = Counter(record["stream"] for _, record in kept)
    if any(not counts[stream] for stream in STREAMS):
        raise ValueError("Motion suffix lacks a perception or locomotion stream")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sequences = Counter()
    original_first = {}
    digest = hashlib.sha256()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output_path.parent, delete=False) as target:
            temporary = Path(target.name)
            for _, record in kept:
                stream = record["stream"]
                original_first.setdefault(stream, record["sequence"])
                sequences[stream] += 1
                data = (json.dumps({**record, "sequence": sequences[stream]},
                                   allow_nan=False, separators=(",", ":")) + "\n").encode()
                target.write(data)
                digest.update(data)
        # Publish a complete file without overwriting an existing destination.
        output_path.hardlink_to(temporary)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    report.update({"status": "TRIMMED", "trimmed_output": str(output_path.resolve()),
        "trimmed_sha256": digest.hexdigest(), "removed_packets": len(inspection.records) - len(kept),
        "kept_packets": dict(counts), "original_first_kept_sequences": original_first,
        "trimmed_duration_s": (kept[-1][1]["received_monotonic_us"] - kept[0][1]["received_monotonic_us"]) / 1e6,
        "sequence_mapping": "original sequence = trimmed sequence + original_first_kept_sequences[stream] - 1"})
    return report
