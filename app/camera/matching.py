"""Deterministic recorded camera association, without rebuilding SocialState."""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.camera.recordings import CameraEvent, ImageValidationError, validate_stored_image
from app.domain.model_decision import ModelDecision
from app.sdk_capture import validate_sdk_capture
from app.state.social_models import SocialState


class ReplayInputError(ValueError):
    """The input is not an intact Step 3 replay row."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _validate_replay_row(row: Any, *, allow_image_matching: bool = False) -> None:
    if not isinstance(row, dict) or type(row.get("schema_version")) is not int or row["schema_version"] != 1:
        raise ValueError("requires a Step 3 replay object with schema_version 1")
    if "image_matching" in row and not allow_image_matching:
        raise ValueError("input already contains image_matching; use the original Step 3 replay")
    # Overflowing JSON numeric literals can decode to infinity even when the
    # decoder rejects NaN/Infinity tokens. Check metadata as well as the state.
    json.dumps(row, allow_nan=False)
    state = SocialState.model_validate(row.get("social_state"))
    if state.model_dump(mode="json") != row["social_state"]:
        raise ValueError("social_state must contain the complete frozen snapshot")
    canonical = json.dumps(row["social_state"], sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False)
    if row.get("social_state_json") != canonical:
        raise ValueError("social_state_json must be the exact canonical frozen social_state")
    if (row.get("source_state_id") != state.state_id or row.get("session_id") != state.session_id
            or type(row.get("source_robot_timestamp_us")) is not int
            or row["source_robot_timestamp_us"] != state.robot_timestamp_us):
        raise ValueError("replay correlation IDs/timestamp disagree with frozen SocialState")
    source = row.get("source")
    if not isinstance(source, dict) or source.get("input_format") not in ("sdk", "raw", "social"):
        raise ValueError("requires Step 3 source provenance with input_format sdk/raw/social")
    if not isinstance(source.get("source_record"), dict) or not isinstance(source.get("timestamps"), dict):
        raise ValueError("requires original source_record and timestamps provenance")
    if "original_capture_session" not in source or "processing_session_id" not in source:
        raise ValueError("requires original capture and processing session provenance")
    if not isinstance(row.get("prompt_version"), str) or not row["prompt_version"]:
        raise ValueError("requires recorded prompt_version")
    if not isinstance(row.get("ollama_configuration"), dict):
        raise ValueError("requires recorded ollama_configuration")
    if any(not isinstance(row.get(key), dict) for key in ("processing", "sampling")):
        raise ValueError("requires recorded processing and sampling metadata")
    if any(not isinstance(row.get(key), str) or not row[key] for key in ("completed_at", "completion_clock")):
        raise ValueError("requires recorded completion time and clock metadata")
    for key in ("ok", "decision", "error", "raw_content", "request_duration_s", "requested_model", "returned_model"):
        if key not in row:
            raise ValueError(f"requires existing model diagnostic field {key}")
    if not isinstance(row["requested_model"], str) or not row["requested_model"]:
        raise ValueError("requires recorded requested_model")
    if row["returned_model"] is not None and not isinstance(row["returned_model"], str):
        raise ValueError("returned_model must be a string or null")
    if row["raw_content"] is not None and not isinstance(row["raw_content"], str):
        raise ValueError("raw_content must be a string or null")
    status = row.get("status")
    if status == "prepared":
        if row.get("ok") is not None or any(row[key] is not None for key in
                ("decision", "error", "raw_content", "request_duration_s", "returned_model")):
            raise ValueError("prepared row must retain null inference diagnostics")
    elif status == "succeeded":
        if row.get("ok") is not True or row["error"] is not None:
            raise ValueError("succeeded row must have ok true and no inference error")
        ModelDecision.model_validate(row["decision"])
    elif status == "failed":
        if row.get("ok") is not False or row["decision"] is not None or not isinstance(row["error"], dict):
            raise ValueError("failed inference row must have ok false, no decision and an error")
    else:
        raise ValueError("requires prepared, succeeded or failed Step 3 status")
    if status != "prepared":
        duration = row["request_duration_s"]
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            raise ValueError("inference duration must be a finite nonnegative number")


def iter_replay_rows(path: Path | str) -> Iterator[dict[str, Any]]:
    """Validate replay JSONL lazily; never normalize or mutate original row data."""
    path = Path(path)
    count = 0
    try:
        with path.open("rb") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                                     parse_constant=_reject_constant)
                    _validate_replay_row(row)
                except (ValueError, RecursionError) as error:
                    raise ReplayInputError(f"{path}:{line_number}: invalid Step 3 replay: {error}") from error
                count += 1
                yield row
    except OSError as error:
        raise ReplayInputError(f"{path}: cannot read replay: {error}") from error
    if not count:
        raise ReplayInputError(f"{path}: replay contains no rows")


def _reference(event: CameraEvent) -> dict[str, Any]:
    return {"manifest_path": str(event.manifest_path.resolve()), "line_number": event.line_number}


def _equivalence_key(event: CameraEvent) -> str:
    # Equivalence requires the same complete recorded provenance and the same
    # resolved image, not merely equal timestamps or similar image pixels.
    normalized = {**event.record, "file": str(event.image_path.resolve()) if event.image_path else None}
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _recorded_perception(source: dict[str, Any]) -> tuple[int | None, int]:
    """Known v1 co-capture uses one session and one robot-host monotonic clock.

    main.py passes SdkCapture.session_id to CameraCapture. Both collectors call
    time.monotonic_ns() on that host. A filename or an estimation time is not
    evidence of that clock domain. Other input formats have no such provenance.
    """
    if source.get("input_format") != "sdk":
        raise ValueError("only SDK capture v1 provenance establishes compatible reception clocks")
    record = validate_sdk_capture(source.get("source_record"))
    timestamps = source.get("timestamps")
    if (record["stream"] != "perception" or record["session_id"] != source.get("original_capture_session")
            or not isinstance(timestamps, dict)
            or type(timestamps.get("received_monotonic_us")) is not int
            or timestamps["received_monotonic_us"] != record["received_monotonic_us"]
            or type(timestamps.get("received_unix_us")) is not int
            or timestamps["received_unix_us"] != record["received_unix_us"]):
        raise ValueError("source capture/session/reception provenance is insufficient or inconsistent")
    sdk_time = record["packet"].get("time")
    recorded_sdk_time = timestamps.get("sdk_perception_timestamp")
    if type(recorded_sdk_time) is not type(sdk_time) or recorded_sdk_time != sdk_time:
        raise ValueError("SDK perception timestamp disagrees with original captured packet")
    if type(sdk_time) is not int or sdk_time < 0:
        sdk_time = None
    return sdk_time, record["received_monotonic_us"]


class CameraMatcher:
    """One reusable offline matching index; matching never edits a replay row."""

    def __init__(self, events: Iterable[CameraEvent], *, camera: str = "head",
                 allow_receipt: bool = False, max_age_us: int | None = None) -> None:
        if camera not in ("head", "chest"):
            raise ValueError("camera must be head or chest")
        if type(allow_receipt) is not bool:
            raise ValueError("allow_receipt must be a boolean")
        if allow_receipt:
            if type(max_age_us) is not int or max_age_us < 0:
                raise ValueError("reception matching requires an explicit nonnegative maximum age in microseconds")
        elif max_age_us is not None:
            raise ValueError("maximum age requires opt-in reception matching")
        self.camera, self.allow_receipt, self.max_age_us = camera, allow_receipt, max_age_us
        self.sessions: dict[str, list[CameraEvent]] = defaultdict(list)
        for event in events:
            self.sessions[event.record["session_id"]].append(event)

    def match(self, row: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": 1, "status": "unmatched", "ok": False, "method": None,
            "timestamp_basis": None, "time_difference": None,
            "reception_time_difference_us": None, "frame": None, "error": None,
            "policy": {"camera": self.camera, "allow_receipt": self.allow_receipt,
                       "max_age_us": self.max_age_us},
            "limitations": [
                "Recorded SDK timestamp equality does not establish hardware or exposure synchronization.",
                "SDK source clock units/mapping remain unverified; no conversion to receipt or estimation time is made.",
            ],
        }

        def failure(category: str, message: str, **details: Any) -> dict[str, Any]:
            result["error"] = {"category": category, "message": message, "details": details}
            return result

        source = row.get("source", {})
        capture_session = source.get("original_capture_session")
        if not isinstance(capture_session, str) or not capture_session:
            return failure("missing_capture_session", "Replay has no original capture session; processing IDs cannot substitute it")
        result["original_capture_session"] = capture_session
        if capture_session not in self.sessions:
            return failure("missing_camera_session", "No explicit camera manifest contains this original capture session")
        events = [event for event in self.sessions[capture_session] if event.record["camera"] == self.camera]
        frames = [event for event in events if event.record["event"] == "frame"]
        if not frames:
            unavailable = [event for event in events if event.record["event"] == "unavailable"]
            if unavailable:
                return failure("camera_unavailable", "Recorded camera unavailable event; no frame for this session/camera",
                               events=[{**_reference(e), "record": e.record} for e in unavailable])
            return failure("no_camera_frame", "No recorded frame for this session and selected camera")
        try:
            sdk_time, receipt = _recorded_perception(source)
        except ValueError as error:
            return failure("unsupported_timestamp_provenance", str(error))
        exact = [e for e in frames if sdk_time is not None and e.record["timestamp_us"] == sdk_time]
        if exact:
            candidates = exact
            method = "exact_recorded_sdk_timestamp"
            result["timestamp_basis"] = "recorded SDK PerceptionData.time == camera frame.timestamp_us, without scaling"
            result["time_difference"] = {"value": 0, "units": "recorded SDK timestamp units (mapping unverified)",
                                         "sign_convention": "camera recorded value minus perception recorded value"}
        else:
            if not self.allow_receipt:
                if sdk_time is None:
                    return failure("unsupported_timestamp_provenance", "No usable recorded SDK perception timestamp for exact matching")
                return failure("no_exact_timestamp", "No equal recorded SDK timestamp in this session/camera",
                               sdk_perception_timestamp=sdk_time)
            past = [e for e in frames if e.record["received_monotonic_us"] <= receipt]
            if not past:
                return failure("no_prior_frame", "No camera frame was received at or before this perception")
            latest_receipt = max(e.record["received_monotonic_us"] for e in past)
            age = receipt - latest_receipt
            if age > self.max_age_us:
                return failure("frame_too_old", "Latest prior frame exceeds the configured maximum age",
                               age_us=age, max_age_us=self.max_age_us)
            candidates = [e for e in past if e.record["received_monotonic_us"] == latest_receipt]
            method = "reception_monotonic"
            result["timestamp_basis"] = "shared robot-host monotonic reception microseconds in capture v1 co-capture session"
            result["time_difference"] = {"value": -age, "units": "microseconds",
                                         "sign_convention": "camera receipt minus perception receipt; negative means earlier camera"}
            result["limitations"].append("Reception proximity is not exposure synchronization; worker/SDK delivery delays remain.")
        if len({_equivalence_key(e) for e in candidates}) != 1:
            return failure("ambiguous_candidates", "Candidates share the matching key but have different recorded provenance or image references",
                           matching_method=method, candidates=[{**_reference(e), "record": e.record}
                           for e in sorted(candidates, key=lambda e: (str(e.manifest_path.resolve()), e.line_number))])
        # Sorting is only used after proving equivalence; retain every source
        # reference so the canonical reference is transparent and reproducible.
        candidates = sorted(candidates, key=lambda e: (str(e.manifest_path.resolve()), e.line_number))
        selected = candidates[0]
        try:
            image = validate_stored_image(selected)
        except ImageValidationError as error:
            return failure(error.category, str(error), matching_method=method, candidate=_reference(selected))
        camera_record = selected.record
        delta = camera_record["received_monotonic_us"] - receipt
        result.update(status="matched", ok=True, method=method,
                      reception_time_difference_us=delta,
                      reception_clock_provenance="SDK/camera capture v1, same original session, same robot-host time.monotonic_ns()",
                      frame={
                          "original_capture_session": capture_session, "camera": self.camera,
                          "sequence": camera_record["sequence"], **_reference(selected),
                          "equivalent_manifest_references": [_reference(e) for e in candidates],
                          "relative_image_path": camera_record["file"],
                          "image_path": str(selected.image_path.resolve()),
                          "width": camera_record["width"], "height": camera_record["height"],
                          "sdk_camera_timestamp_us": camera_record["timestamp_us"],
                          "sdk_perception_timestamp": source["timestamps"].get("sdk_perception_timestamp"),
                          "camera_received_monotonic_us": camera_record["received_monotonic_us"],
                          "camera_received_unix_us": camera_record["received_unix_us"],
                          "perception_received_monotonic_us": receipt,
                          "perception_received_unix_us": source["timestamps"]["received_unix_us"],
                          "camera_manifest_record": camera_record, "image_validation": image,
                      })
        if delta > 0:
            result["limitations"].append("Exact recorded SDK match arrived after perception receipt; this is offline association, not a frame available at that live decision.")
        return result
