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


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def read_frame_records(path: str | Path, *, strict_json: bool = False
                       ) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield original raw objects and line numbers using the existing validation.

    Replay provenance needs the original object rather than a model dump. The
    optional stricter JSON decoder does not change the established read_frames
    interface or its decoding behaviour.
    """
    path = Path(path)
    previous: int | None = None
    count = 0
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                options = ({"object_pairs_hook": _unique_json_object,
                            "parse_constant": _reject_json_constant} if strict_json else {})
                payload = json.loads(line.decode("utf-8"), **options)
                frame = RawObservationFrame.model_validate(payload)
            except (ValueError, RecursionError, ValidationError) as error:
                raise ValueError(f"{path}:{line_number}: invalid raw frame: {error}") from error
            if previous is not None and frame.timestamp <= previous:
                raise TimestampOrderError(
                    f"{path}:{line_number}: timestamp {frame.timestamp} must be greater than {previous}"
                )
            previous = frame.timestamp
            count += 1
            yield line_number, payload
    if count == 0:
        raise ValueError(f"{path}: recording contains no frames")


def read_frames(path: str | Path) -> Iterator[dict[str, Any]]:
    """Read lazily, validating frame shape and order with file/line errors."""
    for _, payload in read_frame_records(path):
        yield RawObservationFrame.model_validate(payload).model_dump(exclude_unset=True)
