"""Deterministic synthetic observations for museum and laboratory guide scenarios.

The adapter generates raw ``ObservationFrame`` objects, not inferred social
state or robot decisions. Exact world trajectories and scenario labels are
returned separately as ground truth so future state estimators can be measured
without leaking answers into their inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from app.domain.models import (
    CapabilityManifest,
    ClockDomain,
    ControllerStatus,
    CoordinateFrame,
    FacialExpressionScores,
    FreeSpaceObservation,
    HumanObservation,
    NavigationTask,
    ObservationFrame,
    RobotObservation,
    SensorSource,
    Uncertainty,
    Vector2,
    Vector3,
)


EPSILON = 1e-9


def _require_finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _validate_increasing_times(items: Sequence[object], name: str) -> None:
    times = [float(getattr(item, "time_s")) for item in items]
    if not times:
        raise ValueError(f"{name} must not be empty")
    if any(current <= previous for previous, current in zip(times, times[1:])):
        raise ValueError(f"{name} times must be strictly increasing")


def _normalize_angle(angle_rad: float) -> float:
    return math.atan2(math.sin(angle_rad), math.cos(angle_rad))


@dataclass(frozen=True)
class PositionWaypoint:
    """A person's head position in the synthetic map frame."""

    time_s: float
    x_m: float
    y_m: float
    z_m: float = 1.55

    def __post_init__(self) -> None:
        for name in ("time_s", "x_m", "y_m", "z_m"):
            _require_finite(float(getattr(self, name)), name)
        if self.time_s < 0:
            raise ValueError("waypoint time must be non-negative")


@dataclass(frozen=True)
class RobotWaypoint:
    """A robot pose in the synthetic map frame."""

    time_s: float
    x_m: float
    y_m: float
    heading_rad: float = 0.0

    def __post_init__(self) -> None:
        for name in ("time_s", "x_m", "y_m", "heading_rad"):
            _require_finite(float(getattr(self, name)), name)
        if self.time_s < 0:
            raise ValueError("waypoint time must be non-negative")


@dataclass(frozen=True)
class SignalKeyframe:
    """A scalar observation signal linearly interpolated over time."""

    time_s: float
    value: float

    def __post_init__(self) -> None:
        _require_finite(self.time_s, "signal time")
        _require_finite(self.value, "signal value")
        if self.time_s < 0:
            raise ValueError("signal time must be non-negative")
        if not 0.0 <= self.value <= 1.0:
            raise ValueError("signal value must be between 0 and 1")


@dataclass(frozen=True)
class TimeInterval:
    """Half-open interval ``[start_s, end_s)`` used for sensor occlusion."""

    start_s: float
    end_s: float

    def __post_init__(self) -> None:
        _require_finite(self.start_s, "interval start")
        _require_finite(self.end_s, "interval end")
        if self.start_s < 0 or self.end_s <= self.start_s:
            raise ValueError("time interval must have 0 <= start < end")

    def contains(self, time_s: float) -> bool:
        return self.start_s <= time_s < self.end_s


@dataclass(frozen=True)
class HumanTrajectory:
    """Ground-truth movement and directly observable signals for one newcomer."""

    track_id: str
    waypoints: tuple[PositionWaypoint, ...]
    gaze: tuple[SignalKeyframe, ...] = (
        SignalKeyframe(0.0, 0.0),
    )
    speech_activity: tuple[SignalKeyframe, ...] = (
        SignalKeyframe(0.0, 0.0),
    )
    occlusions: tuple[TimeInterval, ...] = ()
    ground_truth_group_id: str | None = None
    facial_expression: FacialExpressionScores | None = None
    detection_confidence: float = 0.97
    identity_confidence: float = 0.95

    def __post_init__(self) -> None:
        if not self.track_id.strip():
            raise ValueError("track_id must not be empty")
        _validate_increasing_times(self.waypoints, "human waypoints")
        _validate_increasing_times(self.gaze, "gaze keyframes")
        _validate_increasing_times(self.speech_activity, "speech keyframes")
        for name in ("detection_confidence", "identity_confidence"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class NoiseProfile:
    """Optional repeatable sensor degradation applied after ground-truth motion."""

    position_std_m: float = 0.0
    gaze_std: float = 0.0
    dropout_probability: float = 0.0

    def __post_init__(self) -> None:
        if self.position_std_m < 0 or self.gaze_std < 0:
            raise ValueError("noise standard deviations must be non-negative")
        if not 0.0 <= self.dropout_probability <= 1.0:
            raise ValueError("dropout_probability must be between 0 and 1")


@dataclass(frozen=True)
class SyntheticScenario:
    """A complete guide-robot encounter defined in a shared map frame."""

    scenario_id: str
    title: str
    description: str
    expected_event: str
    duration_s: float
    robot_waypoints: tuple[RobotWaypoint, ...]
    humans: tuple[HumanTrajectory, ...]
    robot_task: NavigationTask
    destination_id: str | None = None
    step_s: float = 0.1
    free_space: FreeSpaceObservation = field(
        default_factory=lambda: FreeSpaceObservation(
            left_m=3.0,
            forward_m=6.0,
            right_m=3.0,
            rear_m=2.0,
        )
    )
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("scenario_id must not be empty")
        if self.duration_s <= 0 or self.step_s <= 0:
            raise ValueError("duration_s and step_s must be positive")
        _validate_increasing_times(self.robot_waypoints, "robot waypoints")
        if abs(self.robot_waypoints[0].time_s) > EPSILON:
            raise ValueError("robot trajectory must start at t=0")
        if self.robot_waypoints[-1].time_s + EPSILON < self.duration_s:
            raise ValueError("robot trajectory must cover the scenario duration")
        if not self.humans:
            raise ValueError("scenario must include at least one human")
        track_ids = [human.track_id for human in self.humans]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("scenario human track IDs must be unique")
        for human in self.humans:
            if abs(human.waypoints[0].time_s) > EPSILON:
                raise ValueError("human trajectories must start at t=0")
            if human.waypoints[-1].time_s + EPSILON < self.duration_s:
                raise ValueError("human trajectories must cover the scenario duration")
            if human.gaze[-1].time_s + EPSILON < self.duration_s:
                raise ValueError("gaze keyframes must cover the scenario duration")
            if human.speech_activity[-1].time_s + EPSILON < self.duration_s:
                raise ValueError("speech keyframes must cover the scenario duration")


@dataclass(frozen=True)
class HumanGroundTruth:
    track_id: str
    position_map_m: tuple[float, float, float]
    velocity_map_mps: tuple[float, float, float]
    gaze_to_robot_score: float
    speech_activity: float
    visible: bool
    group_id: str | None

    def to_dict(self) -> dict:
        return {
            "track_id": self.track_id,
            "position_map_m": list(self.position_map_m),
            "velocity_map_mps": list(self.velocity_map_mps),
            "gaze_to_robot_score": self.gaze_to_robot_score,
            "speech_activity": self.speech_activity,
            "visible": self.visible,
            "group_id": self.group_id,
        }


@dataclass(frozen=True)
class SyntheticGroundTruth:
    scenario_id: str
    expected_event: str
    time_s: float
    robot_pose_map: tuple[float, float, float]
    humans: tuple[HumanGroundTruth, ...]
    tags: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "expected_event": self.expected_event,
            "time_s": self.time_s,
            "robot_pose_map": list(self.robot_pose_map),
            "humans": [human.to_dict() for human in self.humans],
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class SyntheticSample:
    observation: ObservationFrame
    ground_truth: SyntheticGroundTruth


def _segment(items: Sequence[object], time_s: float) -> tuple[object, object]:
    if time_s < float(getattr(items[0], "time_s")):
        return items[0], items[0]
    for left, right in zip(items, items[1:]):
        if time_s <= float(getattr(right, "time_s")) + EPSILON:
            return left, right
    return items[-1], items[-1]


def _fraction(left: object, right: object, time_s: float) -> float:
    left_time = float(getattr(left, "time_s"))
    right_time = float(getattr(right, "time_s"))
    if abs(right_time - left_time) < EPSILON:
        return 0.0
    return min(1.0, max(0.0, (time_s - left_time) / (right_time - left_time)))


def _lerp(left: float, right: float, fraction: float) -> float:
    return left + (right - left) * fraction


def _interpolate_signal(keyframes: Sequence[SignalKeyframe], time_s: float) -> float:
    left, right = _segment(keyframes, time_s)
    fraction = _fraction(left, right, time_s)
    return _lerp(left.value, right.value, fraction)


def _person_state(
    waypoints: Sequence[PositionWaypoint], time_s: float
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    left, right = _segment(waypoints, time_s)
    fraction = _fraction(left, right, time_s)
    position = (
        _lerp(left.x_m, right.x_m, fraction),
        _lerp(left.y_m, right.y_m, fraction),
        _lerp(left.z_m, right.z_m, fraction),
    )
    elapsed = right.time_s - left.time_s
    if elapsed <= EPSILON:
        velocity = (0.0, 0.0, 0.0)
    else:
        velocity = (
            (right.x_m - left.x_m) / elapsed,
            (right.y_m - left.y_m) / elapsed,
            (right.z_m - left.z_m) / elapsed,
        )
    return position, velocity


def _robot_state(
    waypoints: Sequence[RobotWaypoint], time_s: float
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    left, right = _segment(waypoints, time_s)
    fraction = _fraction(left, right, time_s)
    heading_delta = _normalize_angle(right.heading_rad - left.heading_rad)
    pose = (
        _lerp(left.x_m, right.x_m, fraction),
        _lerp(left.y_m, right.y_m, fraction),
        _normalize_angle(left.heading_rad + heading_delta * fraction),
    )
    elapsed = right.time_s - left.time_s
    if elapsed <= EPSILON:
        velocity = (0.0, 0.0, 0.0)
    else:
        velocity = (
            (right.x_m - left.x_m) / elapsed,
            (right.y_m - left.y_m) / elapsed,
            heading_delta / elapsed,
        )
    return pose, velocity


def _map_vector_to_robot(x: float, y: float, robot_heading: float) -> tuple[float, float]:
    cosine = math.cos(robot_heading)
    sine = math.sin(robot_heading)
    return cosine * x + sine * y, -sine * x + cosine * y


class SyntheticObservationAdapter:
    """Generate contract-valid observations from one deterministic scenario."""

    def __init__(
        self,
        scenario: SyntheticScenario,
        *,
        noise: NoiseProfile | None = None,
        seed: int = 0,
        start_time_us: int = 0,
    ) -> None:
        if start_time_us < 0:
            raise ValueError("start_time_us must be non-negative")
        self.scenario = scenario
        self.noise = noise or NoiseProfile()
        self.seed = seed
        self.start_time_us = start_time_us

    @property
    def frame_count(self) -> int:
        return round(self.scenario.duration_s / self.scenario.step_s) + 1

    def __iter__(self) -> Iterator[ObservationFrame]:
        for sample in self.iter_samples():
            yield sample.observation

    def iter_samples(self) -> Iterator[SyntheticSample]:
        for frame_index in range(self.frame_count):
            time_s = min(frame_index * self.scenario.step_s, self.scenario.duration_s)
            yield self.sample_at(time_s, frame_index=frame_index)

    def sample_at(self, time_s: float, *, frame_index: int | None = None) -> SyntheticSample:
        if not 0.0 <= time_s <= self.scenario.duration_s + EPSILON:
            raise ValueError("time_s is outside the scenario duration")
        if frame_index is None:
            frame_index = round(time_s / self.scenario.step_s)

        timestamp_us = self.start_time_us + round(time_s * 1_000_000)
        robot_pose, robot_velocity_map = _robot_state(
            self.scenario.robot_waypoints, time_s
        )
        robot_x, robot_y, robot_heading = robot_pose
        robot_vx, robot_vy = _map_vector_to_robot(
            robot_velocity_map[0], robot_velocity_map[1], robot_heading
        )

        observations: list[HumanObservation] = []
        truth_humans: list[HumanGroundTruth] = []
        for trajectory in self.scenario.humans:
            position_map, velocity_map = _person_state(trajectory.waypoints, time_s)
            gaze_truth = _interpolate_signal(trajectory.gaze, time_s)
            speech_truth = _interpolate_signal(trajectory.speech_activity, time_s)
            physically_visible = not any(
                interval.contains(time_s) for interval in trajectory.occlusions
            )

            generator = self._random_generator(frame_index, trajectory.track_id)
            detected = physically_visible
            if detected and generator.random() < self.noise.dropout_probability:
                detected = False

            truth_humans.append(
                HumanGroundTruth(
                    track_id=trajectory.track_id,
                    position_map_m=position_map,
                    velocity_map_mps=velocity_map,
                    gaze_to_robot_score=gaze_truth,
                    speech_activity=speech_truth,
                    visible=physically_visible,
                    group_id=trajectory.ground_truth_group_id,
                )
            )
            if not detected:
                continue

            relative_x, relative_y = _map_vector_to_robot(
                position_map[0] - robot_x,
                position_map[1] - robot_y,
                robot_heading,
            )
            relative_z = position_map[2]
            if self.noise.position_std_m:
                relative_x += generator.gauss(0.0, self.noise.position_std_m)
                relative_y += generator.gauss(0.0, self.noise.position_std_m)
                relative_z += generator.gauss(0.0, self.noise.position_std_m)

            gaze_observed = gaze_truth
            if self.noise.gaze_std:
                gaze_observed = min(
                    1.0,
                    max(0.0, gaze_truth + generator.gauss(0.0, self.noise.gaze_std)),
                )

            body_yaw_map = math.atan2(velocity_map[1], velocity_map[0])
            if math.hypot(velocity_map[0], velocity_map[1]) <= EPSILON:
                body_yaw_map = math.atan2(robot_y - position_map[1], robot_x - position_map[0])
            body_yaw_robot = _normalize_angle(body_yaw_map - robot_heading)

            toward_robot_x = -relative_x
            toward_robot_y = -relative_y
            toward_robot_norm = math.hypot(toward_robot_x, toward_robot_y)
            if toward_robot_norm > EPSILON and gaze_observed >= 0.5:
                gaze_x = toward_robot_x / toward_robot_norm
                gaze_y = toward_robot_y / toward_robot_norm
                head_yaw = math.atan2(toward_robot_y, toward_robot_x)
            else:
                gaze_x = math.cos(body_yaw_robot)
                gaze_y = math.sin(body_yaw_robot)
                head_yaw = body_yaw_robot

            observations.append(
                HumanObservation(
                    track_id=trajectory.track_id,
                    observed=True,
                    source_timestamp_us=timestamp_us,
                    state_age_ms=0,
                    position_robot_m=Vector3(
                        x=relative_x,
                        y=relative_y,
                        z=relative_z,
                    ),
                    distance_m=math.hypot(relative_x, relative_y),
                    detection_confidence=trajectory.detection_confidence,
                    identity_confidence=trajectory.identity_confidence,
                    head_rpy_rad=Vector3(x=0.0, y=0.0, z=head_yaw),
                    body_yaw_rad=body_yaw_robot,
                    gaze_unit=Vector3(x=gaze_x, y=gaze_y, z=0.0),
                    gaze_to_robot_score=gaze_observed,
                    facial_expression=trajectory.facial_expression,
                    speech_activity=speech_truth,
                    uncertainty=Uncertainty(
                        position_std_m=self.noise.position_std_m,
                        occlusion_probability=0.0,
                    ),
                    sensor_sources=[SensorSource.SYNTHETIC],
                )
            )

        controller_status = (
            ControllerStatus.ACTIVE
            if math.hypot(robot_vx, robot_vy) > EPSILON
            else ControllerStatus.STOPPED
        )
        observation = ObservationFrame(
            schema_version="1.0",
            observation_id=f"{self.scenario.scenario_id}:{frame_index:05d}",
            timestamp_us=timestamp_us,
            clock_domain=ClockDomain.MONOTONIC,
            coordinate_frame=CoordinateFrame.ROBOT_BASE,
            robot=RobotObservation(
                linear_velocity_mps=Vector2(x=robot_vx, y=robot_vy),
                angular_velocity_radps=robot_velocity_map[2],
                task=self.scenario.robot_task,
                controller_status=controller_status,
                destination_id=self.scenario.destination_id,
                stopping_distance_m=0.45,
                free_space=self.scenario.free_space,
            ),
            humans=observations,
            images=[],
            capabilities=CapabilityManifest(
                adapter_id="synthetic-museum-v1",
                robot_type="synthetic-guide-robot",
                available_fields=[
                    "robot.linear_velocity_mps",
                    "humans.position_robot_m",
                    "humans.distance_m",
                    "humans.head_rpy_rad",
                    "humans.body_yaw_rad",
                    "humans.gaze_to_robot_score",
                    "humans.speech_activity",
                ],
                unavailable_fields=[
                    "robot.pose",
                    "images",
                    "humans.face_bbox",
                    "humans.group_observation_id",
                ],
                notes=[
                    "Exact map-frame trajectories are stored only in separate ground truth.",
                    "Observation positions and velocities are expressed in ROBOT_BASE.",
                ],
            ),
        )
        ground_truth = SyntheticGroundTruth(
            scenario_id=self.scenario.scenario_id,
            expected_event=self.scenario.expected_event,
            time_s=time_s,
            robot_pose_map=robot_pose,
            humans=tuple(truth_humans),
            tags=self.scenario.tags,
        )
        return SyntheticSample(observation=observation, ground_truth=ground_truth)

    def write_jsonl(self, path: Path, *, include_ground_truth: bool = True) -> int:
        """Write the scenario as replayable newline-delimited JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("w", encoding="utf-8") as stream:
            for sample in self.iter_samples():
                record: dict = {
                    "observation": sample.observation.model_dump(mode="json")
                }
                if include_ground_truth:
                    record["ground_truth"] = sample.ground_truth.to_dict()
                stream.write(json.dumps(record, sort_keys=True) + "\n")
                count += 1
        return count

    def _random_generator(self, frame_index: int, track_id: str) -> random.Random:
        material = f"{self.seed}:{frame_index}:{track_id}".encode("utf-8")
        seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        return random.Random(seed)


def _stationary_robot(duration_s: float) -> tuple[RobotWaypoint, ...]:
    return (
        RobotWaypoint(0.0, 0.0, 0.0, 0.0),
        RobotWaypoint(duration_s, 0.0, 0.0, 0.0),
    )


def _signals(duration_s: float, *values: tuple[float, float]) -> tuple[SignalKeyframe, ...]:
    items = [SignalKeyframe(time_s, value) for time_s, value in values]
    if not items or items[0].time_s > EPSILON:
        raise ValueError("signals must start at t=0")
    if items[-1].time_s + EPSILON < duration_s:
        items.append(SignalKeyframe(duration_s, items[-1].value))
    return tuple(items)


def museum_guide_scenarios() -> dict[str, SyntheticScenario]:
    """Return the initial unmanned museum/laboratory scenario catalogue."""
    scenarios = [
        SyntheticScenario(
            scenario_id="newcomer_requests_guidance",
            title="Newcomer approaches and asks for guidance",
            description=(
                "A newcomer enters, approaches the stationary guide robot, slows at "
                "social distance, establishes gaze, and begins speaking."
            ),
            expected_event="REQUEST_GUIDANCE",
            duration_s=8.0,
            robot_waypoints=_stationary_robot(8.0),
            humans=(
                HumanTrajectory(
                    track_id="visitor-1",
                    waypoints=(
                        PositionWaypoint(0.0, 5.0, -1.2),
                        PositionWaypoint(4.5, 2.1, -0.25),
                        PositionWaypoint(6.0, 1.8, -0.20),
                        PositionWaypoint(8.0, 1.8, -0.20),
                    ),
                    gaze=_signals(
                        8.0,
                        (0.0, 0.08),
                        (2.5, 0.20),
                        (4.5, 0.72),
                        (6.0, 0.92),
                        (8.0, 0.94),
                    ),
                    speech_activity=_signals(
                        8.0,
                        (0.0, 0.0),
                        (5.4, 0.0),
                        (5.8, 0.85),
                        (7.2, 0.80),
                        (8.0, 0.15),
                    ),
                    facial_expression=FacialExpressionScores(
                        neutral=0.65, happy=0.25, surprise=0.10
                    ),
                ),
            ),
            robot_task=NavigationTask.IDLE,
            tags=("newcomer", "approach", "attention", "speech", "guidance"),
        ),
        SyntheticScenario(
            scenario_id="newcomer_passes_without_engaging",
            title="Newcomer passes the guide without engaging",
            description=(
                "A newcomer walks through the lobby while looking toward exhibits, "
                "without approaching or addressing the robot."
            ),
            expected_event="PASS_WITHOUT_ENGAGEMENT",
            duration_s=6.0,
            robot_waypoints=_stationary_robot(6.0),
            humans=(
                HumanTrajectory(
                    track_id="visitor-1",
                    waypoints=(
                        PositionWaypoint(0.0, 4.0, -1.8),
                        PositionWaypoint(3.0, 0.5, -1.8),
                        PositionWaypoint(6.0, -3.0, -1.8),
                    ),
                    gaze=_signals(6.0, (0.0, 0.08), (3.0, 0.16), (6.0, 0.05)),
                    speech_activity=_signals(6.0, (0.0, 0.0), (6.0, 0.0)),
                ),
            ),
            robot_task=NavigationTask.IDLE,
            tags=("newcomer", "passerby", "negative-engagement"),
        ),
        SyntheticScenario(
            scenario_id="newcomer_crosses_toward_exhibit",
            title="Newcomer crosses the robot's route toward an exhibit",
            description=(
                "While the robot is guiding, a newcomer crosses its route to reach "
                "an exhibit without attempting to interact."
            ),
            expected_event="PATH_CROSSING",
            duration_s=6.0,
            robot_waypoints=(
                RobotWaypoint(0.0, 0.0, 0.0),
                RobotWaypoint(6.0, 3.0, 0.0),
            ),
            humans=(
                HumanTrajectory(
                    track_id="visitor-1",
                    waypoints=(
                        PositionWaypoint(0.0, 2.0, -2.4),
                        PositionWaypoint(3.0, 2.0, 0.0),
                        PositionWaypoint(6.0, 2.0, 2.4),
                    ),
                    gaze=_signals(6.0, (0.0, 0.05), (3.0, 0.12), (6.0, 0.04)),
                    speech_activity=_signals(6.0, (0.0, 0.0), (6.0, 0.0)),
                ),
            ),
            robot_task=NavigationTask.GUIDING,
            destination_id="exhibit-4",
            tags=("newcomer", "crossing", "path-conflict", "exhibit"),
        ),
        SyntheticScenario(
            scenario_id="newcomer_follows_guide",
            title="Newcomer follows the guide robot",
            description=(
                "The robot leads a newcomer along a corridor while the person keeps "
                "a stable following distance and intermittently attends to the robot."
            ),
            expected_event="FOLLOW_GUIDE",
            duration_s=10.0,
            robot_waypoints=(
                RobotWaypoint(0.0, 0.0, 0.0),
                RobotWaypoint(10.0, 5.0, 0.0),
            ),
            humans=(
                HumanTrajectory(
                    track_id="visitor-1",
                    waypoints=(
                        PositionWaypoint(0.0, -1.5, 0.2),
                        PositionWaypoint(10.0, 3.5, 0.2),
                    ),
                    gaze=_signals(
                        10.0,
                        (0.0, 0.65),
                        (2.5, 0.35),
                        (5.0, 0.72),
                        (7.5, 0.38),
                        (10.0, 0.70),
                    ),
                    speech_activity=_signals(10.0, (0.0, 0.0), (10.0, 0.0)),
                ),
            ),
            robot_task=NavigationTask.GUIDING,
            destination_id="laboratory-demo-bench",
            tags=("newcomer", "guidance", "following", "corridor"),
        ),
        SyntheticScenario(
            scenario_id="newcomer_falls_behind",
            title="Newcomer falls behind during guidance",
            description=(
                "The newcomer initially follows but stops to inspect an exhibit while "
                "the guide robot continues moving."
            ),
            expected_event="FOLLOWER_FALLING_BEHIND",
            duration_s=10.0,
            robot_waypoints=(
                RobotWaypoint(0.0, 0.0, 0.0),
                RobotWaypoint(10.0, 5.0, 0.0),
            ),
            humans=(
                HumanTrajectory(
                    track_id="visitor-1",
                    waypoints=(
                        PositionWaypoint(0.0, -1.4, 0.2),
                        PositionWaypoint(4.0, 0.6, 0.2),
                        PositionWaypoint(10.0, 0.6, 0.2),
                    ),
                    gaze=_signals(
                        10.0,
                        (0.0, 0.65),
                        (4.0, 0.30),
                        (7.0, 0.18),
                        (10.0, 0.60),
                    ),
                    speech_activity=_signals(10.0, (0.0, 0.0), (10.0, 0.0)),
                ),
            ),
            robot_task=NavigationTask.GUIDING,
            destination_id="exhibit-7",
            tags=("newcomer", "guidance", "falling-behind", "stop"),
        ),
        SyntheticScenario(
            scenario_id="newcomer_occluded_by_exhibit",
            title="Approaching newcomer is temporarily occluded by an exhibit",
            description=(
                "A newcomer approaching the guide passes behind a large exhibit, "
                "disappears from perception, and then reappears with the same identity."
            ),
            expected_event="OCCLUSION_REACQUISITION",
            duration_s=7.0,
            robot_waypoints=_stationary_robot(7.0),
            humans=(
                HumanTrajectory(
                    track_id="visitor-1",
                    waypoints=(
                        PositionWaypoint(0.0, 5.0, 0.8),
                        PositionWaypoint(7.0, 1.8, 0.2),
                    ),
                    gaze=_signals(7.0, (0.0, 0.35), (3.0, 0.70), (7.0, 0.90)),
                    speech_activity=_signals(7.0, (0.0, 0.0), (7.0, 0.0)),
                    occlusions=(TimeInterval(2.5, 4.0),),
                ),
            ),
            robot_task=NavigationTask.IDLE,
            tags=("newcomer", "approach", "occlusion", "reacquisition", "exhibit"),
        ),
        SyntheticScenario(
            scenario_id="newcomer_pair_requests_guidance",
            title="Two newcomers arrive together and request guidance",
            description=(
                "Two newcomers enter side by side, stop as a pair at social distance, "
                "and one member addresses the robot."
            ),
            expected_event="GROUP_REQUEST_GUIDANCE",
            duration_s=8.0,
            robot_waypoints=_stationary_robot(8.0),
            humans=(
                HumanTrajectory(
                    track_id="visitor-1",
                    waypoints=(
                        PositionWaypoint(0.0, 5.0, -0.55),
                        PositionWaypoint(5.5, 2.1, -0.40),
                        PositionWaypoint(8.0, 2.1, -0.40),
                    ),
                    gaze=_signals(8.0, (0.0, 0.12), (4.0, 0.55), (8.0, 0.90)),
                    speech_activity=_signals(
                        8.0,
                        (0.0, 0.0),
                        (5.8, 0.0),
                        (6.2, 0.85),
                        (7.5, 0.75),
                        (8.0, 0.2),
                    ),
                    ground_truth_group_id="visitor-pair-1",
                ),
                HumanTrajectory(
                    track_id="visitor-2",
                    waypoints=(
                        PositionWaypoint(0.0, 5.0, 0.55),
                        PositionWaypoint(5.5, 2.1, 0.40),
                        PositionWaypoint(8.0, 2.1, 0.40),
                    ),
                    gaze=_signals(8.0, (0.0, 0.10), (4.0, 0.40), (8.0, 0.70)),
                    speech_activity=_signals(8.0, (0.0, 0.0), (8.0, 0.0)),
                    ground_truth_group_id="visitor-pair-1",
                ),
            ),
            robot_task=NavigationTask.IDLE,
            tags=("newcomer", "group", "approach", "speech", "guidance"),
        ),
    ]
    return {scenario.scenario_id: scenario for scenario in scenarios}


def _list_scenarios(scenarios: Iterable[SyntheticScenario]) -> None:
    for scenario in scenarios:
        print(f"{scenario.scenario_id}\t{scenario.title}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic museum/laboratory guide-robot observations."
    )
    parser.add_argument("--list", action="store_true", help="List available scenarios")
    parser.add_argument("--scenario", help="Scenario ID to generate")
    parser.add_argument("--output", type=Path, help="Output JSONL path")
    parser.add_argument("--seed", type=int, default=0, help="Deterministic noise seed")
    parser.add_argument("--position-noise-std-m", type=float, default=0.0)
    parser.add_argument("--gaze-noise-std", type=float, default=0.0)
    parser.add_argument("--dropout-probability", type=float, default=0.0)
    parser.add_argument(
        "--without-ground-truth",
        action="store_true",
        help="Write only observations, without separate exact ground truth",
    )
    args = parser.parse_args()

    scenarios = museum_guide_scenarios()
    if args.list:
        _list_scenarios(scenarios.values())
        return
    if not args.scenario or not args.output:
        parser.error("--scenario and --output are required unless --list is used")
    if args.scenario not in scenarios:
        parser.error(
            f"unknown scenario {args.scenario!r}; choose from {', '.join(scenarios)}"
        )

    adapter = SyntheticObservationAdapter(
        scenarios[args.scenario],
        noise=NoiseProfile(
            position_std_m=args.position_noise_std_m,
            gaze_std=args.gaze_noise_std,
            dropout_probability=args.dropout_probability,
        ),
        seed=args.seed,
    )
    count = adapter.write_jsonl(
        args.output,
        include_ground_truth=not args.without_ground_truth,
    )
    print(f"wrote {count} observations to {args.output}")


if __name__ == "__main__":
    main()
