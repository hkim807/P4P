"""Append independent, ordered Navel SDK packet streams to diagnostic JSONL."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class SdkCaptureOrderError(ValueError):
    pass


class SdkCaptureWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.exists():
            raise FileExistsError(f"SDK capture already exists: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._session_id: str | None = None
        self._last: dict[str, tuple[int, str]] = {}

    def write(self, record: dict[str, Any]) -> bool:
        """Return True for an identical retry of the last packet in a stream."""
        line = json.dumps(record, allow_nan=False, separators=(",", ":"))
        stream, sequence = record["stream"], record["sequence"]
        with self._lock:
            if self._session_id is not None and record["session_id"] != self._session_id:
                raise SdkCaptureOrderError("capture belongs to a different robot session")
            previous = self._last.get(stream)
            if previous is not None:
                if sequence == previous[0] and line == previous[1]:
                    return True
                if sequence != previous[0] + 1:
                    raise SdkCaptureOrderError(
                        f"{stream} sequence {sequence} must follow {previous[0]}")
            elif sequence != 1:
                raise SdkCaptureOrderError(f"{stream} must begin at sequence 1")
            with self.path.open("x" if self._session_id is None else "a", encoding="utf-8") as file:
                file.write(line + "\n")
            self._session_id = record["session_id"]
            self._last[stream] = (sequence, line)
        return False


def validate_sdk_capture(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != {
            "capture_version", "session_id", "stream", "sequence",
            "received_monotonic_us", "received_unix_us", "packet"}:
        raise ValueError("invalid SDK capture envelope")
    if type(record["capture_version"]) is not int or record["capture_version"] != 1:
        raise ValueError("unsupported SDK capture version")
    if not isinstance(record["session_id"], str) or not record["session_id"] or len(record["session_id"]) > 128:
        raise ValueError("invalid session_id")
    if record["stream"] not in ("perception", "locomotion"):
        raise ValueError("invalid SDK stream")
    for name in ("sequence", "received_monotonic_us", "received_unix_us"):
        if type(record[name]) is not int or record[name] < (1 if name == "sequence" else 0):
            raise ValueError(f"invalid {name}")
    if not isinstance(record["packet"], dict):
        raise ValueError("SDK packet must be an object")
    try:
        json.dumps(record, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("SDK capture must be finite JSON") from error
    return record
