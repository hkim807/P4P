"""Shared tracking entry point and serialized receiver processing."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from app.domain.models import RawObservationFrame
from app.recording import RecordingWriter
from app.state.tracks import TrackConfig, TrackManager


def trace_line(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"


class TrackingProcessingError(RuntimeError):
    """Derived processing failed; raw persistence, when configured, succeeded."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"tracking {stage} failed")
        self.stage = stage


class TrackTraceWriter:
    """One new trace per run; owned by the pipeline's lock."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.exists():
            raise FileExistsError(f"Tracking trace already exists: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._started = False

    def write(self, snapshot: dict[str, Any]) -> None:
        with self.path.open("a" if self._started else "x", encoding="utf-8") as stream:
            stream.write(trace_line(snapshot))
        self._started = True


class TrackingPipeline:
    """Serializes raw persistence, tracking, and derived trace emission together.

    Offline replay calls process without a raw writer. Tracking failures after
    successful raw persistence are returned separately and never erase raw data.
    """

    def __init__(self, session_id: str, config: TrackConfig | None = None,
                 recording: RecordingWriter | None = None,
                 trace: TrackTraceWriter | None = None) -> None:
        self.tracker = TrackManager(session_id, config)
        self.recording = recording
        self.trace = trace
        self._lock = Lock()

    def process(self, frame: RawObservationFrame | dict[str, Any]) -> dict[str, Any]:
        payload = frame.model_dump(exclude_unset=True) if isinstance(frame, RawObservationFrame) else frame
        validated = RawObservationFrame.model_validate(payload)
        with self._lock:
            if self.recording is not None:
                self.recording.write(validated)
            try:
                snapshot = self.tracker.update(validated)
            except Exception as error:
                raise TrackingProcessingError("update") from error
            if self.trace is not None:
                try:
                    self.trace.write(snapshot)
                except Exception as error:
                    raise TrackingProcessingError("trace_write") from error
            return snapshot
