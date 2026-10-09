"""Convert SDK perception/locomotion packets into raw JSON dictionaries.

This module imports neither Navel nor server dependencies. No robot methods
are called here. Missing or invalid measurements remain unavailable.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any, Callable


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _nonnegative_number(value: Any) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number >= 0 else None


def _coordinate_name(value: Any) -> str | None:
    name = getattr(value, "name", value)
    return name.upper().split(".")[-1] if isinstance(name, str) else None


class NavelObservationAdapter:
    """Retain measured fields without temporal estimates or policy features."""

    def __init__(self, *, monotonic_ns: Callable[[], int] = time.monotonic_ns) -> None:
        self._monotonic_ns = monotonic_ns

    def convert(self, perception: Any, locomotion: Any | None = None) -> dict[str, Any]:
        people = []
        seen = set()
        for person in getattr(perception, "persons", None) or ():
            uid = getattr(person, "uid", None)
            if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0 or uid in seen:
                continue
            seen.add(uid)
            distance_mm = _nonnegative_number(getattr(person, "dist_mm", None))
            gaze = _finite_number(getattr(person, "gaze_overlap", None))
            if gaze is not None and not 0 <= gaze <= 1:
                gaze = None
            converted = {
                "uid": uid,
                "distance_m": distance_mm / 1000 if distance_mm is not None else None,
                "gaze_overlap": gaze,
            }
            # SDK face is a bounding box. A missing/invalid box supplies no
            # positive detection evidence; person identity alone is insufficient.
            face = getattr(person, "face", None)
            coordinates = [_finite_number(face.get(key) if isinstance(face, Mapping)
                                           else getattr(face, key, None))
                           for key in ("x1", "y1", "x2", "y2")]
            if (all(value is not None for value in coordinates)
                    and coordinates[2] > coordinates[0] and coordinates[3] > coordinates[1]):
                converted["face_detected"] = True
            position = self._relative_head_position(getattr(person, "g_head_position", None))
            if position is not None:
                converted["optional_relative_head_position"] = position
            people.append(converted)

        velocity = getattr(getattr(locomotion, "odometry", None), "velocity", None)
        return {
            "timestamp": self._monotonic_ns() // 1000,
            "people": people,
            "robot": {
                "linear_velocity": _finite_number(getattr(velocity, "linear_x", None)),
                "angular_velocity": _finite_number(getattr(velocity, "angular_z", None)),
            },
            "safety": {
                "lidar": self._ranges(locomotion, "LIDAR"),
                "sonar": self._ranges(locomotion, "SONAR"),
            },
        }

    @staticmethod
    def _relative_head_position(positions: Any) -> dict[str, Any] | None:
        for position in positions or ():
            if _coordinate_name(getattr(position, "sys", None)) != "CAM_HEAD":
                continue
            vector = {axis: _finite_number(getattr(position, axis, None)) for axis in "xyz"}
            if all(value is not None for value in vector.values()):
                return {"coordinate_frame": "CAM_HEAD", **vector}
        return None

    @staticmethod
    def _ranges(locomotion: Any, sensor: str) -> list[float | None] | None:
        distances = getattr(locomotion, "distances", None)
        if not isinstance(distances, Mapping):
            return None
        for key, readings in distances.items():
            # Navel Sensor enums expose .name; string keys also work for fixtures.
            if _coordinate_name(key) != sensor:
                continue
            if not isinstance(readings, (list, tuple)):
                return None
            # Keep positions so an invalid front reading never becomes a back reading.
            return [_nonnegative_number(reading) for reading in readings]
        return None
