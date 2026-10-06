"""Bounded live camera association using explicitly declared robot provenance."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
from threading import Lock
from typing import Any

from app.camera_capture import CameraCaptureOrderError, validate_camera_record
from app.image_encoding import EncodedVLMImage, encode_rgb_png
from app.sdk_capture import validate_sdk_capture


MODEL_SOURCE_CLOCK = "robot-host-monotonic-us"


def validate_model_source(source: Any, observation_timestamp_us: int) -> dict[str, Any]:
    """Validate and detach the optional perception provenance beside raw input.

    Capture v1 SDK and camera collectors share a robot-host monotonic clock and
    capture session. The declaration must identify that clock; receiver clocks
    do not establish a mapping. Unknown clock names remain auditable metadata.
    """
    if type(observation_timestamp_us) is not int or observation_timestamp_us < 0:
        raise ValueError("observation timestamp must be a nonnegative integer")
    if not isinstance(source, dict) or set(source) != {"version", "clock", "capture"}:
        raise ValueError("model_source requires exactly version, clock and capture")
    if type(source["version"]) is not int or source["version"] != 1:
        raise ValueError("unsupported model_source version")
    if not isinstance(source["clock"], str) or not source["clock"].strip():
        raise ValueError("model_source clock must be a nonblank string")
    capture = validate_sdk_capture(source["capture"])
    if capture["stream"] != "perception":
        raise ValueError("model_source capture must describe the corresponding SDK perception")
    if (source["clock"] == MODEL_SOURCE_CLOCK
            and capture["received_monotonic_us"] > observation_timestamp_us):
        raise ValueError("model_source perception receipt cannot follow its robot-host raw collection timestamp")
    return json.loads(json.dumps(source, ensure_ascii=False, sort_keys=True, allow_nan=False))


@dataclass(frozen=True)
class LiveFrame:
    rgb: bytes
    width: int
    height: int
    rgb_sha256: str
    _metadata_json: str

    def to_dict(self) -> dict[str, Any]:
        """Detached provenance and source hash, with no RGB/base64 payload."""
        return json.loads(self._metadata_json)

    def encode(self) -> EncodedVLMImage:
        return encode_rgb_png(self.rgb, self.width, self.height,
                              source_image_sha256=self.rgb_sha256, source_byte_count=len(self.rgb))


@dataclass(frozen=True)
class LiveAssociation:
    frame: LiveFrame | None
    _metadata_json: str

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._metadata_json)


@dataclass(frozen=True)
class _CameraEvent:
    record_json: str
    frame: LiveFrame | None

    @property
    def record(self) -> dict[str, Any]:
        return json.loads(self.record_json)


class LiveCameraCache:
    """One bounded current capture session, with explicit stream retry/order rules.

    A new capture session replaces previous cached frames and stream counters.
    A first positive sequence can join an already-running stream. Later events increase contiguously;
    only an identical retry of the last stream event is accepted as a duplicate.
    """

    def __init__(self, max_frames: int = 8, max_bytes: int = 64 * 1024 * 1024) -> None:
        if type(max_frames) is not int or max_frames < 1:
            raise ValueError("max_frames must be a positive integer")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self.max_frames, self.max_bytes = max_frames, max_bytes
        self._lock = Lock()
        self._events: deque[_CameraEvent] = deque()
        self._bytes = 0
        self._session: str | None = None
        self._last: dict[str, tuple[int, str]] = {}

    @property
    def frame_count(self) -> int:
        with self._lock:
            return sum(event.frame is not None for event in self._events)

    @property
    def byte_count(self) -> int:
        with self._lock:
            return self._bytes

    @property
    def event_count(self) -> int:
        with self._lock:
            return len(self._events)

    def ingest(self, record: dict[str, Any], rgb: bytes | None, *,
               receiver_received_monotonic_us: int | None = None,
               receiver_received_unix_us: int | None = None) -> bool:
        """Copy a validated transport event; return True only for a last retry."""
        validated, decoded = validate_camera_record(record)
        if decoded != rgb or (rgb is not None and not isinstance(rgb, bytes)):
            raise ValueError("camera RGB bytes disagree with the validated transport envelope")
        for name, value in (("receiver_received_monotonic_us", receiver_received_monotonic_us),
                            ("receiver_received_unix_us", receiver_received_unix_us)):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a nonnegative integer or null")
        if validated["camera"] == "head" and rgb is not None and len(rgb) > self.max_bytes:
            raise ValueError("head image exceeds the configured camera cache byte limit")
        fingerprint = hashlib.sha256(json.dumps(validated, sort_keys=True, allow_nan=False).encode()).hexdigest()
        record_json = json.dumps({key: value for key, value in validated.items() if key != "rgb_b64"},
                                 sort_keys=True, ensure_ascii=False, allow_nan=False)
        frame = None
        if validated["camera"] == "head" and rgb is not None:
            digest = hashlib.sha256(rgb).hexdigest()
            metadata = {**json.loads(record_json), "source_clock": MODEL_SOURCE_CLOCK,
                        "rgb_sha256": digest, "rgb_byte_count": len(rgb),
                        "receiver_received_monotonic_us": receiver_received_monotonic_us,
                        "receiver_received_unix_us": receiver_received_unix_us,
                        "receiver_receipt_clock": "receiver-host receipts; never compared to robot-host clocks"}
            frame = LiveFrame(bytes(rgb), validated["width"], validated["height"], digest,
                              json.dumps(metadata, sort_keys=True, ensure_ascii=False, allow_nan=False))
        camera, sequence, session = validated["camera"], validated["sequence"], validated["session_id"]
        with self._lock:
            previous = self._last.get(camera) if self._session == session else None
            if previous is not None:
                if sequence == previous[0] and fingerprint == previous[1]:
                    return True
                if sequence != previous[0] + 1:
                    raise CameraCaptureOrderError(f"{camera} sequence {sequence} must follow {previous[0]}")
            if self._session != session:
                self._events.clear()
                self._bytes = 0
                self._last.clear()
                self._session = session
            self._last[camera] = (sequence, fingerprint)
            if camera == "head":
                self._events.append(_CameraEvent(record_json, frame))
                self._bytes += len(frame.rgb) if frame else 0
                while len(self._events) > self.max_frames or self._bytes > self.max_bytes:
                    removed = self._events.popleft()
                    self._bytes -= len(removed.frame.rgb) if removed.frame else 0
        return False

    def associate(self, model_source: dict[str, Any] | None, *, max_age_us: int,
                  allow_receipt: bool = False) -> LiveAssociation:
        """Freeze an association now, without waiting for another camera frame."""
        if type(max_age_us) is not int or max_age_us < 0 or type(allow_receipt) is not bool:
            raise ValueError("association requires nonnegative integer max_age_us and boolean allow_receipt")
        metadata: dict[str, Any] = {
            "schema_version": 1, "status": "unmatched", "ok": False, "method": None,
            "timestamp_basis": None, "time_difference": None, "reception_time_difference_us": None,
            "frame": None, "error": None,
            "policy": {"camera": "head", "allow_receipt": allow_receipt, "max_age_us": max_age_us},
            "limitations": ["SDK numeric timestamp equality does not establish exposure synchronization.",
                            "SDK clock units/mapping remain unverified; no comparison to receiver-host clocks is made."],
        }

        def result(frame: LiveFrame | None = None) -> LiveAssociation:
            return LiveAssociation(frame, json.dumps(metadata, sort_keys=True, ensure_ascii=False, allow_nan=False))

        def failure(category: str, message: str, **details: Any) -> LiveAssociation:
            metadata["error"] = {"category": category, "message": message, "details": details}
            return result()

        if model_source is None:
            return failure("missing_model_source", "Observation has no declared corresponding perception provenance")
        try:
            capture = model_source["capture"]
            source = validate_model_source(model_source, capture["received_monotonic_us"])
        except (ValueError, KeyError, TypeError) as error:
            return failure("invalid_model_source", str(error))
        if source["clock"] != MODEL_SOURCE_CLOCK:
            return failure("incompatible_clock", "Source does not declare the supported robot-host monotonic capture clock")
        capture = source["capture"]
        session, receipt = capture["session_id"], capture["received_monotonic_us"]
        metadata["original_capture_session"] = session
        metadata["perception_source"] = source
        with self._lock:
            cache_session, events = self._session, tuple(self._events)
        if cache_session is None:
            return failure("missing_camera_frame", "No head camera capture has been received")
        if cache_session != session:
            return failure("cross_session", "Cached camera capture belongs to a different source session")
        frames = [event.frame for event in events if event.frame is not None]
        if not frames:
            unavailable = [event.record for event in events if event.record["event"] == "unavailable"]
            if unavailable:
                return failure("camera_unavailable", "Head camera reported unavailability", events=unavailable)
            return failure("missing_camera_frame", "This source session has no cached head image")
        sdk_time = capture["packet"].get("time")
        exact = [frame for frame in frames if type(sdk_time) is int and sdk_time >= 0
                 and frame.to_dict()["timestamp_us"] == sdk_time]
        if exact:
            candidates, method = exact, "exact_recorded_sdk_timestamp"
        else:
            if not allow_receipt:
                return failure("no_exact_timestamp", "No equal recorded SDK timestamp; receipt matching is disabled")
            prior = [frame for frame in frames if frame.to_dict()["received_monotonic_us"] <= receipt]
            if not prior:
                return failure("no_prior_frame", "No head frame was received at or before the perception")
            latest = max(frame.to_dict()["received_monotonic_us"] for frame in prior)
            candidates = [frame for frame in prior if frame.to_dict()["received_monotonic_us"] == latest]
            method = "reception_monotonic"
        if len({frame._metadata_json for frame in candidates}) != 1:
            return failure("ambiguous_candidates", "Matching candidates have different source provenance/images",
                           matching_method=method, candidates=[frame.to_dict() for frame in candidates])
        frame = candidates[0]
        delta = frame.to_dict()["received_monotonic_us"] - receipt
        if abs(delta) > max_age_us:
            return failure("frame_too_old", "Selected head frame exceeds the configured source-receipt age limit",
                           matching_method=method, signed_receipt_delta_us=delta, max_age_us=max_age_us)
        if method == "exact_recorded_sdk_timestamp":
            metadata["timestamp_basis"] = "recorded SDK PerceptionData.time == camera frame.timestamp_us, without scaling"
            metadata["time_difference"] = {"value": 0, "units": "recorded SDK timestamp units (mapping unverified)",
                                           "sign_convention": "camera recorded value minus perception recorded value"}
        else:
            metadata["timestamp_basis"] = "shared robot-host monotonic reception microseconds in capture v1 co-capture session"
            metadata["time_difference"] = {"value": delta, "units": "microseconds",
                                           "sign_convention": "camera receipt minus perception receipt; negative means earlier camera"}
            metadata["limitations"].append("Receipt proximity is not exposure synchronization; SDK/transport delays remain.")
        if delta > 0:
            metadata["limitations"].append("The exact SDK-equal frame was received after perception; this does not prove live exposure simultaneity.")
        metadata.update(status="matched", ok=True, method=method, reception_time_difference_us=delta,
                        reception_clock_provenance="SDK/camera capture v1, same original session, robot-host time.monotonic_ns()",
                        frame=frame.to_dict())
        return result(frame)
