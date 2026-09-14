"""Duck-typed conversion from Navel SDK data to canonical observation dictionaries.

This module deliberately does not import :mod:`navel`, which keeps conversion
unit-testable away from the robot.  The adapter reads only documented SDK
attributes and never calls a robot method.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _vector3(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    values = tuple(_finite_number(getattr(value, axis, None)) for axis in "xyz")
    if any(component is None for component in values):
        return None
    return {axis: component for axis, component in zip("xyz", values)}


def _coordinate_name(value: Any) -> str | None:
    """Return a stable uppercase coordinate label from an enum-like value."""
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().upper()
    if isinstance(value, str) and value.strip():
        return value.strip().upper().split(".")[-1]
    return None


@dataclass(frozen=True)
class NavelAdapterConfig:
    """Explicit assumptions used to satisfy required canonical robot fields."""

    adapter_id: str = "navel-readonly-v1"
    robot_type: str = "navel"
    robot_task: str = "IDLE"
    controller_status: str = "STOPPED"
    stationary_velocity_fallback: bool = False
    robot_base_coordinate_systems: tuple[str, ...] = ()
    odometry_pose_compatible: bool = False

    def __post_init__(self) -> None:
        if not self.adapter_id.strip() or not self.robot_type.strip():
            raise ValueError("adapter_id and robot_type must not be empty")
        if len(self.adapter_id) > 128 or len(self.robot_type) > 256:
            raise ValueError("adapter_id or robot_type is too long for the canonical schema")
        if self.robot_task not in {
            "IDLE", "GUIDING", "APPROACHING", "INTERACTING", "PAUSED", "COMPLETE", "ERROR"
        }:
            raise ValueError("robot_task is not a canonical NavigationTask")
        if self.controller_status not in {
            "IDLE", "ACTIVE", "STOPPED", "FAULT", "EMERGENCY_STOP"
        }:
            raise ValueError("controller_status is not a canonical ControllerStatus")
        normalized = tuple(
            name.strip().upper() for name in self.robot_base_coordinate_systems if name.strip()
        )
        object.__setattr__(self, "robot_base_coordinate_systems", normalized)


class NavelObservationAdapter:
    """Convert perception plus the latest locomotion packet to a plain dictionary."""

    def __init__(
        self,
        config: NavelAdapterConfig | None = None,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.config = config or NavelAdapterConfig()
        self._monotonic_ns = monotonic_ns
        self._last_timestamp_us = -1
        self._counter = 0

    def convert(self, perception: Any, locomotion: Any | None = None) -> dict[str, Any]:
        """Return one contract-shaped observation, skipping unusable people."""
        timestamp_us = max(self._monotonic_ns() // 1_000, self._last_timestamp_us + 1)
        self._last_timestamp_us = timestamp_us
        self._counter += 1

        robot, robot_available, robot_notes = self._robot_observation(locomotion)
        humans: list[dict[str, Any]] = []
        seen_track_ids: set[str] = set()
        for person in self._iter_people(perception):
            converted = self._human_observation(person, timestamp_us)
            if converted is None or converted["track_id"] in seen_track_ids:
                continue
            seen_track_ids.add(converted["track_id"])
            humans.append(converted)

        available_fields = [
            "robot.controller_status",
            "robot.task",
            "humans.track_id",
            "humans.distance_m",
            "humans.face_bbox",
            "humans.head_rpy_rad",
            "humans.gaze_unit",
            "humans.gaze_to_robot_score",
            "humans.facial_expression",
            *robot_available,
        ]
        if self.config.robot_base_coordinate_systems:
            available_fields.append("humans.position_robot_m")

        unavailable_fields = [
            "images",
            "humans.body_yaw_rad",
            "humans.detection_confidence",
            "humans.group_observation_id",
            "humans.identity_confidence",
            "humans.speech_activity",
            "robot.free_space",
        ]
        if not self.config.robot_base_coordinate_systems:
            unavailable_fields.append("humans.position_robot_m")
        if "robot.pose" not in robot_available:
            unavailable_fields.append("robot.pose")
        if "robot.linear_velocity_mps" not in robot_available:
            unavailable_fields.extend(
                ["robot.linear_velocity_mps", "robot.angular_velocity_radps"]
            )

        return {
            "schema_version": "1.0",
            "observation_id": (
                f"{self.config.adapter_id}:{timestamp_us}:{self._counter:06d}"
            ),
            "timestamp_us": timestamp_us,
            "clock_domain": "MONOTONIC",
            "coordinate_frame": "ROBOT_BASE",
            "robot": robot,
            "humans": humans,
            "images": [],
            "capabilities": {
                "adapter_id": self.config.adapter_id,
                "robot_type": self.config.robot_type,
                "available_fields": sorted(set(available_fields)),
                "unavailable_fields": sorted(unavailable_fields),
                "notes": [
                    "PerceptionData.time is not used because its clock unit is undocumented; host monotonic time is used.",
                    "id_score is not mapped because its semantics are undocumented.",
                    "Only explicitly configured robot-base coordinate labels are accepted for 3D positions.",
                    *robot_notes,
                ],
            },
        }

    def _iter_people(self, perception: Any) -> Iterable[Any]:
        people = getattr(perception, "persons", None)
        if people is None:
            return ()
        try:
            return iter(people)
        except TypeError:
            return ()

    def _human_observation(
        self, person: Any, source_timestamp_us: int
    ) -> dict[str, Any] | None:
        track_id = self._track_id(getattr(person, "uid", None))
        if track_id is None:
            return None

        result: dict[str, Any] = {
            "track_id": track_id,
            "observed": True,
            "source_timestamp_us": source_timestamp_us,
            "state_age_ms": 0,
            "sensor_sources": ["HEAD_RGB"],
        }

        distance_mm = _finite_number(getattr(person, "dist_mm", None))
        if distance_mm is not None and distance_mm >= 0:
            result["distance_m"] = distance_mm / 1_000.0

        face = self._face_box(getattr(person, "face", None))
        if face is not None:
            result["face_bbox"] = face

        head = _vector3(getattr(person, "head_position", None))
        if head is not None:
            result["head_rpy_rad"] = head

        gaze = _vector3(getattr(person, "gaze", None))
        if gaze is not None:
            norm = math.sqrt(sum(component * component for component in gaze.values()))
            if norm > 0:
                result["gaze_unit"] = {
                    axis: component / norm for axis, component in gaze.items()
                }

        gaze_overlap = _finite_number(getattr(person, "gaze_overlap", None))
        if gaze_overlap is not None:
            result["gaze_to_robot_score"] = min(1.0, max(0.0, gaze_overlap))

        expression = self._facial_expression(
            getattr(person, "facial_expression", None)
        )
        if expression is not None:
            result["facial_expression"] = expression

        position = self._robot_base_position(
            getattr(person, "g_head_position", None)
        )
        if position is not None:
            result["position_robot_m"] = position
        return result

    def _track_id(self, uid: Any) -> str | None:
        if isinstance(uid, bool) or uid is None:
            return None
        if isinstance(uid, int):
            return str(uid) if uid >= 0 else None
        if isinstance(uid, str) and uid.strip():
            return uid.strip()
        return None

    def _face_box(self, face: Any) -> dict[str, int] | None:
        if face is None:
            return None
        raw = tuple(getattr(face, name, None) for name in ("x1", "y1", "x2", "y2"))
        if any(isinstance(value, bool) or not isinstance(value, int) for value in raw):
            return None
        x_min, x_max = sorted((raw[0], raw[2]))
        y_min, y_max = sorted((raw[1], raw[3]))
        if x_min < 0 or y_min < 0 or x_max <= x_min or y_max <= y_min:
            return None
        return {
            "x_min_px": x_min,
            "y_min_px": y_min,
            "x_max_px": x_max,
            "y_max_px": y_max,
            "image_id": None,
        }

    def _facial_expression(self, expression: Any) -> dict[str, float] | None:
        if expression is None:
            return None
        result: dict[str, float] = {}
        for name in ("neutral", "happy", "sad", "surprise", "anger"):
            value = _finite_number(getattr(expression, name, None))
            if value is not None:
                result[name] = min(1.0, max(0.0, value))
        return result or None

    def _robot_base_position(self, positions: Any) -> dict[str, float] | None:
        try:
            iterator = iter(positions or ())
        except TypeError:
            return None
        accepted = set(self.config.robot_base_coordinate_systems)
        for position in iterator:
            coordinate = _coordinate_name(getattr(position, "sys", None))
            if coordinate == "UNDEFINED" or coordinate not in accepted:
                continue
            vector = _vector3(position)
            if vector is not None:
                return vector
        return None

    def _robot_observation(
        self, locomotion: Any | None
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        odometry = getattr(locomotion, "odometry", None)
        velocity = getattr(odometry, "velocity", None)
        linear_x = _finite_number(getattr(velocity, "linear_x", None))
        linear_y = _finite_number(getattr(velocity, "linear_y", None))
        angular = _finite_number(getattr(velocity, "angular_z", None))

        available: list[str] = []
        notes: list[str] = []
        if linear_x is None and self.config.stationary_velocity_fallback:
            linear_x, linear_y, angular = 0.0, 0.0, 0.0
            notes.append(
                "Zero velocity is a configured stationary-only fallback, not a locomotion measurement."
            )
        elif linear_x is None:
            raise ValueError(
                "locomotion velocity is unavailable; use stationary_velocity_fallback only while the robot is stationary"
            )
        else:
            linear_y = linear_y if linear_y is not None else 0.0
            angular = angular if angular is not None else 0.0
            available.extend(
                ["robot.linear_velocity_mps", "robot.angular_velocity_radps"]
            )

        robot: dict[str, Any] = {
            "linear_velocity_mps": {"x": linear_x, "y": linear_y},
            "angular_velocity_radps": angular,
            "task": self.config.robot_task,
            "controller_status": self.config.controller_status,
        }

        if self.config.odometry_pose_compatible:
            position = getattr(odometry, "position", None)
            orientation = getattr(odometry, "orientation", None)
            x = _finite_number(getattr(position, "x", None))
            y = _finite_number(getattr(position, "y", None))
            heading = self._first_finite(orientation, ("heading", "yaw", "z"))
            if x is not None and y is not None and heading is not None:
                robot["pose"] = {"x_m": x, "y_m": y, "heading_rad": heading}
                available.append("robot.pose")
        return robot, available, notes

    def _first_finite(self, value: Any, names: tuple[str, ...]) -> float | None:
        for name in names:
            converted = _finite_number(getattr(value, name, None))
            if converted is not None:
                return converted
        return None
