"""Small, ordered JSONL recordings of the existing raw sensor contract."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from pydantic import ValidationError

from app.domain.models import RawObservationFrame


class TimestampOrderError(ValueError):
    """A recording must not combine duplicate or backward robot timestamps."""


class RecordingWriter:
    """One receiver run is one recording; existing files are never appended."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            if self.path.exists():
                raise FileExistsError(f"Recording already exists: {self.path}; choose a new --output path")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._last_timestamp: int | None = None

    def write(self, frame: RawObservationFrame) -> None:
        line = json.dumps(frame.model_dump(exclude_unset=True), allow_nan=False, separators=(",", ":"))
        with self._lock:
            if self._last_timestamp is not None and frame.timestamp <= self._last_timestamp:
                raise TimestampOrderError(
                    f"timestamp {frame.timestamp} must be greater than {self._last_timestamp}; "
                    "start a new recording if the robot clock restarted"
                )
            if self.path is None:
                print(line, flush=True)
            else:
                # Exclusive creation prevents accidental append to another run.
                mode = "x" if self._last_timestamp is None else "a"
                with self.path.open(mode, encoding="utf-8") as stream:
                    stream.write(line + "\n")
            # A failed write does not advance the accepted timestamp.
            self._last_timestamp = frame.timestamp


def read_frames(path: str | Path) -> Iterator[dict[str, Any]]:
    """Read lazily, validating frame shape and order with file/line errors."""
    path = Path(path)
    previous: int | None = None
    count = 0
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                frame = RawObservationFrame.model_validate(payload)
            except (json.JSONDecodeError, ValidationError) as error:
                raise ValueError(f"{path}:{line_number}: invalid raw frame: {error}") from error
            if previous is not None and frame.timestamp <= previous:
                raise TimestampOrderError(
                    f"{path}:{line_number}: timestamp {frame.timestamp} must be greater than {previous}"
                )
            previous = frame.timestamp
            count += 1
            yield frame.model_dump(exclude_unset=True)
    if count == 0:
        raise ValueError(f"{path}: recording contains no frames")
