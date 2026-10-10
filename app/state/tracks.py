"""Bounded, event-time UID histories; no identity inference or social decisions.

Design rationale and research boundaries: docs/person-tracking.md.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from typing import Any

from app.domain.models import RawObservationFrame
from app.recording import TimestampOrderError

TRACKER_VERSION = "uid-tracking-v2"

@dataclass(frozen=True)
class TrackConfig:
    history_window_s: float = 3.0
    missing_grace_s: float = 0.75
    max_samples_per_track: int = 64
    max_tracks: int = 32

    def __post_init__(self) -> None:
        for name in ("history_window_s", "missing_grace_s"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (float, int))
                    or not math.isfinite(value) or not math.isfinite(value * 1_000_000)
                    or value < 0.000001):
                raise ValueError(f"{name} must be finite and at least one microsecond")
        for name in ("max_samples_per_track", "max_tracks"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    @property
    def history_window_us(self) -> int:
        return round(self.history_window_s * 1_000_000)

    @property
    def missing_grace_us(self) -> int:
        return round(self.missing_grace_s * 1_000_000)

    @classmethod
    def from_file(cls, path: str | Path) -> TrackConfig:
        values = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("tracking config must be a JSON object")
        try:
            return cls(**values)
        except TypeError as error:
            raise ValueError(f"invalid tracking config: {error}") from error


class TrackCapacityError(ValueError):
    """One frame contains more observed people than the configured capacity."""


@dataclass
class _Track:
    uid: int
    epoch: int
    first_seen_us: int
    last_seen_us: int
    observation_count: int = 0
    visibility: str = "OBSERVED"
    samples: deque = field(default_factory=deque)


class TrackManager:
    """One ordered session. Callers serialize update/reset; snapshots are detached."""

    def __init__(self, session_id: str, config: TrackConfig | None = None) -> None:
        self.config = config or TrackConfig()
        self.reset(session_id)

    def reset(self, new_session_id: str) -> None:
        if not isinstance(new_session_id, str) or not new_session_id.strip():
            raise ValueError("session_id must be a nonempty string")
        self.session_id = new_session_id
        self._tracks: dict[int, _Track] = {}
        self._last_timestamp: int | None = None
        self._sequence = 0
        # A global acquisition counter avoids an unbounded map of retired UIDs.
        self._next_epoch = 1

    def update(self, frame: RawObservationFrame | dict[str, Any]) -> dict[str, Any]:
        # Revalidate models too: callers could have mutated a Pydantic instance.
        payload = frame.model_dump(exclude_unset=True) if isinstance(frame, RawObservationFrame) else frame
        validated = RawObservationFrame.model_validate(payload)
        now = validated.timestamp
        if self._last_timestamp is not None and now <= self._last_timestamp:
            raise TimestampOrderError(f"timestamp {now} must be greater than {self._last_timestamp}")
        people = {p.uid: p for p in validated.people}
        if len(people) > self.config.max_tracks:
            raise TrackCapacityError("observed people exceed max_tracks; tracker unchanged")
        # Every expected validation failure occurs before any mutation.
        events: list[dict[str, Any]] = []

        def event(kind: str, track: _Track, reason: str) -> None:
            events.append({"type": kind, "uid": track.uid, "track_epoch": track.epoch,
                           "timestamp_us": now, "reason": reason})

        # Expire before matching, even if an old UID is visible in this frame.
        for uid, track in sorted(list(self._tracks.items())):
            if now - track.last_seen_us > self.config.missing_grace_us:
                event("LOST", track, "missing_grace_exceeded")
                del self._tracks[uid]
            elif uid not in people and track.visibility == "OBSERVED":
                track.visibility = "TEMPORARILY_MISSING"
                event("MISSING", track, "absent_from_frame")

        incoming = len(set(people) - self._tracks.keys())
        overflow = len(self._tracks) + incoming - self.config.max_tracks
        # Current observations take precedence over retained absent histories.
        candidates = sorted((t for uid, t in self._tracks.items() if uid not in people),
                            key=lambda t: (t.last_seen_us, t.uid))
        for track in candidates[:max(0, overflow)]:
            event("LOST", track, "capacity_eviction")
            del self._tracks[track.uid]

        self._sequence += 1
        for uid, person in sorted(people.items()):
            track = self._tracks.get(uid)
            if track is None:
                track = _Track(uid, self._next_epoch, now, now)
                self._next_epoch += 1
                self._tracks[uid] = track
                event("ACQUIRED", track, "new_track")
            elif track.visibility == "TEMPORARILY_MISSING":
                event("REACQUIRED", track, "same_uid_within_grace")
            track.visibility = "OBSERVED"
            track.last_seen_us = now
            track.observation_count += 1
            sample = person.model_dump(exclude_unset=True)
            del sample["uid"]
            sample.update(timestamp_us=now, frame_sequence=self._sequence)
            track.samples.append(sample)

        cutoff = now - self.config.history_window_us
        snapshots = []
        for uid, track in sorted(self._tracks.items()):
            while track.samples and track.samples[0]["timestamp_us"] < cutoff:
                track.samples.popleft()
            removed_for_capacity = 0
            while len(track.samples) > self.config.max_samples_per_track:
                track.samples.popleft()
                removed_for_capacity += 1
            samples = deepcopy(list(track.samples))
            snapshots.append({
                "uid": uid, "track_epoch": track.epoch, "visibility": track.visibility,
                "first_seen_us": track.first_seen_us, "last_seen_us": track.last_seen_us,
                "track_age_s": (now - track.first_seen_us) / 1_000_000,
                "time_since_seen_s": (now - track.last_seen_us) / 1_000_000,
                "observation_count": track.observation_count,
                "retained_sample_count": len(samples),
                "retained_span_s": ((samples[-1]["timestamp_us"] - samples[0]["timestamp_us"])
                                    / 1_000_000 if samples else 0.0),
                "samples_dropped_for_capacity": removed_for_capacity,
                "identity_quality_flags": ["UID_ZERO_UNVERIFIED"] if uid == 0 else [],
                "samples": samples,
            })
        self._last_timestamp = now
        return {"schema_version": 1, "tracker_version": TRACKER_VERSION,
                "session_id": self.session_id, "frame_sequence": self._sequence,
                "robot_timestamp_us": now, "config": asdict(self.config),
                "robot": validated.robot.model_dump(),
                "tracks": snapshots, "events": events}
