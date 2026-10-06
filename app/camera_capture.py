"""Store streamed RGB camera frames as PPM images plus a JSONL manifest."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any


MAX_RGB_BYTES = 24 * 1024 * 1024


class CameraCaptureOrderError(ValueError):
    pass


def validate_camera_record(record: Any) -> tuple[dict[str, Any], bytes | None]:
    common = {"capture_version", "session_id", "camera", "sequence", "event",
              "received_monotonic_us", "received_unix_us"}
    if not isinstance(record, dict) or not common <= set(record):
        raise ValueError("invalid camera capture envelope")
    if type(record["capture_version"]) is not int or record["capture_version"] != 1:
        raise ValueError("unsupported camera capture version")
    if not isinstance(record["session_id"], str) or not record["session_id"] or len(record["session_id"]) > 128:
        raise ValueError("invalid session_id")
    if record["camera"] not in ("head", "chest"):
        raise ValueError("invalid camera")
    for name in ("sequence", "received_monotonic_us", "received_unix_us"):
        if type(record[name]) is not int or record[name] < (1 if name == "sequence" else 0):
            raise ValueError(f"invalid {name}")
    if record["event"] == "unavailable":
        if set(record) != common | {"reason"}:
            raise ValueError("invalid unavailable event")
        if not isinstance(record["reason"], str) or not record["reason"] or len(record["reason"]) > 500:
            raise ValueError("invalid unavailable reason")
        return record, None
    if record["event"] != "frame" or set(record) != common | {
            "timestamp_us", "width", "height", "rgb_b64"}:
        raise ValueError("invalid camera frame event")
    for name in ("timestamp_us", "width", "height"):
        if type(record[name]) is not int or record[name] < (0 if name == "timestamp_us" else 1):
            raise ValueError(f"invalid {name}")
    expected = record["width"] * record["height"] * 3
    if expected > MAX_RGB_BYTES or not isinstance(record["rgb_b64"], str):
        raise ValueError("camera frame is too large or image data is invalid")
    try:
        rgb = base64.b64decode(record["rgb_b64"], validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("invalid camera image encoding") from error
    if len(rgb) != expected:
        raise ValueError("camera RGB byte count does not match dimensions")
    return record, rgb


class CameraCaptureWriter:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self._lock = Lock()
        self._session_id: str | None = None
        self._last: dict[str, tuple[int, str]] = {}

    def write(self, record: dict[str, Any], rgb: bytes | None) -> bool:
        """Return True for an identical retry of the last event per camera."""
        fingerprint = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()
        camera, sequence = record["camera"], record["sequence"]
        with self._lock:
            if self._session_id is not None and record["session_id"] != self._session_id:
                raise CameraCaptureOrderError("camera capture belongs to another robot session")
            previous = self._last.get(camera)
            if previous is not None:
                if sequence == previous[0] and fingerprint == previous[1]:
                    return True
                if sequence != previous[0] + 1:
                    raise CameraCaptureOrderError(
                        f"{camera} sequence {sequence} must follow {previous[0]}")
            elif sequence != 1:
                raise CameraCaptureOrderError(f"{camera} must begin at sequence 1")
            relative = None
            image_path = None
            if rgb is not None:
                relative = f"{camera}/{sequence:06d}-{record['timestamp_us']}.ppm"
                image_path = self.directory / relative
                image_path.parent.mkdir(exist_ok=True)
                with image_path.open("xb") as file:
                    file.write(f"P6\n{record['width']} {record['height']}\n255\n".encode("ascii"))
                    file.write(rgb)
            entry = {key: value for key, value in record.items() if key != "rgb_b64"}
            entry["file"] = relative
            manifest = self.directory / "frames.jsonl"
            try:
                with manifest.open("x" if self._session_id is None else "a", encoding="utf-8") as file:
                    file.write(json.dumps(entry, separators=(",", ":")) + "\n")
            except OSError:
                if image_path is not None:
                    image_path.unlink(missing_ok=True)
                raise
            self._session_id = record["session_id"]
            self._last[camera] = (sequence, fingerprint)
        return False
