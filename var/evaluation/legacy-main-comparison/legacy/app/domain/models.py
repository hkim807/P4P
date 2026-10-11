"""Strict, robot-independent domain models for social navigation.

All physical measurements use SI units. Timestamps use integer microseconds and
must be interpreted using the accompanying ``clock_domain`` value. Models reject
unknown fields so producers cannot silently drift away from the contract.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SchemaVersion = Literal["1.0"]
EntityId = Annotated[str, Field(min_length=1, max_length=128)]
Identifier = Annotated[str, Field(min_length=1, max_length=256)]
Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class DomainModel(BaseModel):
    """Base configuration shared by all public contracts and nested models."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class ClockDomain(str, Enum):
    MONOTONIC = "MONOTONIC"
    ROBOT = "ROBOT"
    UNIX_EPOCH = "UNIX_EPOCH"
    REPLAY = "REPLAY"


class CoordinateFrame(str, Enum):
    ROBOT_BASE = "ROBOT_BASE"
    ODOMETRY = "ODOMETRY"
    MAP = "MAP"
    HEAD_CAMERA = "HEAD_CAMERA"
    CHEST_CAMERA = "CHEST_CAMERA"
    UNDEFINED = "UNDEFINED"


class SensorSource(str, Enum):
    HEAD_RGB = "HEAD_RGB"
    CHEST_RGB = "CHEST_RGB"
    DEPTH = "DEPTH"
    LIDAR = "LIDAR"
    SONAR = "SONAR"
    ODOMETRY = "ODOMETRY"
    MICROPHONE = "MICROPHONE"
    EXTERNAL = "EXTERNAL"
    SYNTHETIC = "SYNTHETIC"
    REPLAY = "REPLAY"


class NavigationTask(str, Enum):
    IDLE = "IDLE"
    GUIDING = "GUIDING"
    APPROACHING = "APPROACHING"
    INTERACTING = "INTERACTING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"


class ControllerStatus(str, Enum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    FAULT = "FAULT"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class MotionRelation(str, Enum):
    UNKNOWN = "UNKNOWN"
    STATIONARY = "STATIONARY"
    APPROACHING = "APPROACHING"
    RECEDING = "RECEDING"
    CROSSING = "CROSSING"
    PARALLEL = "PARALLEL"
    APPROACHING_CROSSING = "APPROACHING_CROSSING"


class DistanceTrend(str, Enum):
    UNKNOWN = "UNKNOWN"
    DECREASING = "DECREASING"
    STABLE = "STABLE"
    INCREASING = "INCREASING"


class AttentionState(str, Enum):
    UNKNOWN = "UNKNOWN"
    NOT_ATTENDING = "NOT_ATTENDING"
    GLANCE = "GLANCE"
    INTERMITTENT = "INTERMITTENT"
    SUSTAINED = "SUSTAINED"


class EngagementState(str, Enum):
    UNKNOWN = "UNKNOWN"
    DISENGAGED = "DISENGAGED"
    AVAILABLE = "AVAILABLE"
    ATTENDING = "ATTENDING"
    HUMAN_SPEAKING = "HUMAN_SPEAKING"
    ROBOT_SPEAKING = "ROBOT_SPEAKING"
    TRANSITION = "TRANSITION"


class ProxemicZone(str, Enum):
    UNKNOWN = "UNKNOWN"
    INTIMATE = "INTIMATE"
    PERSONAL = "PERSONAL"
    SOCIAL = "SOCIAL"
    PUBLIC = "PUBLIC"


class Action(str, Enum):
    CONTINUE = "CONTINUE"
    MONITOR = "MONITOR"
    ORIENT = "ORIENT"
    SLOW = "SLOW"
    YIELD = "YIELD"
    AVOID = "AVOID"
    APPROACH = "APPROACH"
    GREET = "GREET"
    GUIDE = "GUIDE"
    WAIT = "WAIT"
    RESUME = "RESUME"
    DISENGAGE = "DISENGAGE"


class PassingSide(str, Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    EITHER = "EITHER"


class ImageEncoding(str, Enum):
    JPEG = "JPEG"
    PNG = "PNG"
    RGB8 = "RGB8"


class Vector2(DomainModel):
    x: FiniteFloat
    y: FiniteFloat


class Vector3(DomainModel):
    x: FiniteFloat
    y: FiniteFloat
    z: FiniteFloat


class Pose2D(DomainModel):
    x_m: FiniteFloat
    y_m: FiniteFloat
    heading_rad: FiniteFloat


class BoundingBox2D(DomainModel):
    x_min_px: NonNegativeInt
    y_min_px: NonNegativeInt
    x_max_px: NonNegativeInt
    y_max_px: NonNegativeInt
    image_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_extent(self) -> "BoundingBox2D":
        if self.x_max_px <= self.x_min_px or self.y_max_px <= self.y_min_px:
            raise ValueError("bounding-box maxima must exceed minima")
        return self


class Uncertainty(DomainModel):
    position_std_m: NonNegativeFloat | None = None
    velocity_std_mps: NonNegativeFloat | None = None
    heading_std_rad: NonNegativeFloat | None = None
    occlusion_probability: Probability | None = None


class FacialExpressionScores(DomainModel):
    neutral: Probability | None = None
    happy: Probability | None = None
    sad: Probability | None = None
    surprise: Probability | None = None
    anger: Probability | None = None


class FreeSpaceObservation(DomainModel):
    left_m: NonNegativeFloat | None = None
    forward_m: NonNegativeFloat | None = None
    right_m: NonNegativeFloat | None = None
    rear_m: NonNegativeFloat | None = None


class RobotObservation(DomainModel):
    pose: Pose2D | None = None
    linear_velocity_mps: Vector2
    angular_velocity_radps: FiniteFloat = 0.0
    task: NavigationTask
    controller_status: ControllerStatus
    destination_id: Identifier | None = None
    goal: Pose2D | None = None
    stopping_distance_m: NonNegativeFloat | None = None
    free_space: FreeSpaceObservation | None = None


class HumanObservation(DomainModel):
    track_id: EntityId
    observed: bool = True
    source_timestamp_us: NonNegativeInt
    state_age_ms: NonNegativeInt = 0
    position_robot_m: Vector3 | None = None
    distance_m: NonNegativeFloat | None = None
    detection_confidence: Probability | None = None
    identity_confidence: Probability | None = None
    face_bbox: BoundingBox2D | None = None
    head_rpy_rad: Vector3 | None = None
    body_yaw_rad: FiniteFloat | None = None
    gaze_unit: Vector3 | None = None
    gaze_to_robot_score: Probability | None = None
    facial_expression: FacialExpressionScores | None = None
    speech_activity: Probability | None = None
    group_observation_id: EntityId | None = None
    uncertainty: Uncertainty | None = None
    sensor_sources: list[SensorSource] = Field(min_length=1)


class ImageFrameReference(DomainModel):
    image_id: Identifier
    camera_id: Identifier
    timestamp_us: NonNegativeInt
    coordinate_frame: CoordinateFrame
    uri: Annotated[str, Field(min_length=1, max_length=2048)]
    width_px: Annotated[int, Field(gt=0)]
    height_px: Annotated[int, Field(gt=0)]
    encoding: ImageEncoding
    calibration_id: Identifier | None = None
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


class CapabilityManifest(DomainModel):
    adapter_id: Identifier
    robot_type: Identifier
    available_fields: list[Identifier] = Field(default_factory=list)
    unavailable_fields: list[Identifier] = Field(default_factory=list)
    notes: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list
    )


class ObservationFrame(DomainModel):
    """Robot-independent observations at one synchronized pipeline instant."""

    schema_version: SchemaVersion
    observation_id: Identifier
    timestamp_us: NonNegativeInt
    clock_domain: ClockDomain
    coordinate_frame: CoordinateFrame
    robot: RobotObservation
    humans: list[HumanObservation] = Field(default_factory=list)
    images: list[ImageFrameReference] = Field(default_factory=list)
    capabilities: CapabilityManifest

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "ObservationFrame":
        track_ids = [human.track_id for human in self.humans]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("human track IDs must be unique within an observation")
        image_ids = [image.image_id for image in self.images]
        if len(image_ids) != len(set(image_ids)):
            raise ValueError("image IDs must be unique within an observation")
        return self


class RobotSocialState(DomainModel):
    pose: Pose2D | None = None
    linear_velocity_mps: Vector2
    angular_velocity_radps: FiniteFloat = 0.0
    task: NavigationTask
    controller_status: ControllerStatus
    destination_id: Identifier | None = None
    goal: Pose2D | None = None
    stopping_distance_m: NonNegativeFloat | None = None
    free_space: FreeSpaceObservation | None = None


class PredictedPosition(DomainModel):
    horizon_s: Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
    position_robot_m: Vector2
    position_std_m: NonNegativeFloat | None = None


class AttentionEvidence(DomainModel):
    gaze_to_robot_score: Probability | None = None
    gaze_mean_500ms: Probability | None = None
    gaze_mean_2s: Probability | None = None
    gaze_ratio_2s: Probability | None = None
    longest_mutual_gaze_2s_s: NonNegativeFloat | None = None
    time_since_gaze_s: NonNegativeFloat | None = None
    gaze_switch_rate_2s_hz: NonNegativeFloat | None = None
    state: AttentionState = AttentionState.UNKNOWN


class EngagementEvidence(DomainModel):
    state: EngagementState = EngagementState.UNKNOWN
    engagement_probability: Probability | None = None
    addressee_probability: Probability | None = None
    speech_activity: Probability | None = None
    time_since_speech_s: NonNegativeFloat | None = None


class GestureEvidence(DomainModel):
    gesture_class: Identifier
    probability: Probability
    started_at_us: NonNegativeInt | None = None
    ended_at_us: NonNegativeInt | None = None
    target_id: EntityId | None = None

    @model_validator(mode="after")
    def validate_times(self) -> "GestureEvidence":
        if (
            self.started_at_us is not None
            and self.ended_at_us is not None
            and self.ended_at_us < self.started_at_us
        ):
            raise ValueError("gesture end time must not precede start time")
        return self


class HumanSocialState(DomainModel):
    track_id: EntityId
    observed: bool
    predicted_only: bool = False
    state_age_ms: NonNegativeInt
    track_age_s: NonNegativeFloat
    time_since_seen_s: NonNegativeFloat
    position_robot_m: Vector3 | None = None
    distance_m: NonNegativeFloat | None = None
    velocity_robot_mps: Vector2 | None = None
    speed_mps: NonNegativeFloat | None = None
    acceleration_mps2: FiniteFloat | None = None
    heading_rad: FiniteFloat | None = None
    closing_speed_mps: FiniteFloat | None = None
    motion_relation: MotionRelation = MotionRelation.UNKNOWN
    distance_trend: DistanceTrend = DistanceTrend.UNKNOWN
    predicted_positions: list[PredictedPosition] = Field(default_factory=list)
    time_to_closest_approach_s: NonNegativeFloat | None = None
    distance_at_closest_approach_m: NonNegativeFloat | None = None
    path_conflict_probability: Probability | None = None
    proxemic_zone: ProxemicZone = ProxemicZone.UNKNOWN
    personal_space_cost: Probability | None = None
    head_rpy_rad: Vector3 | None = None
    body_yaw_rad: FiniteFloat | None = None
    attention: AttentionEvidence = Field(default_factory=AttentionEvidence)
    engagement: EngagementEvidence = Field(default_factory=EngagementEvidence)
    gesture: GestureEvidence | None = None
    group_id: EntityId | None = None
    facial_expression: FacialExpressionScores | None = None
    uncertainty: Uncertainty
    evidence_codes: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_visibility(self) -> "HumanSocialState":
        if self.observed and self.predicted_only:
            raise ValueError("an observed track cannot also be predicted-only")
        return self


class GroupState(DomainModel):
    group_id: EntityId
    member_ids: list[EntityId] = Field(min_length=2)
    centroid_robot_m: Vector2 | None = None
    interaction_space_radius_m: NonNegativeFloat | None = None
    openness_score: Probability | None = None
    confidence: Probability | None = None

    @model_validator(mode="after")
    def validate_members(self) -> "GroupState":
        if len(self.member_ids) != len(set(self.member_ids)):
            raise ValueError("group member IDs must be unique")
        return self


class CrowdState(DomainModel):
    people_within_1m: NonNegativeInt = 0
    people_within_2m: NonNegativeInt = 0
    people_within_3m: NonNegativeInt = 0
    density_people_m2: NonNegativeFloat | None = None
    flow_velocity_mps: Vector2 | None = None
    flow_entropy: NonNegativeFloat | None = None
    free_space_fraction: Probability | None = None

    @model_validator(mode="after")
    def validate_nested_counts(self) -> "CrowdState":
        if not (
            self.people_within_1m
            <= self.people_within_2m
            <= self.people_within_3m
        ):
            raise ValueError("crowd counts must be nondecreasing with radius")
        return self


class SocialState(DomainModel):
    """Temporally derived, uncertainty-aware state supplied to a policy."""

    schema_version: SchemaVersion
    state_id: Identifier
    source_observation_id: Identifier
    timestamp_us: NonNegativeInt
    clock_domain: ClockDomain
    coordinate_frame: CoordinateFrame
    history_window_s: Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
    robot: RobotSocialState
    humans: list[HumanSocialState] = Field(default_factory=list)
    groups: list[GroupState] = Field(default_factory=list)
    crowd: CrowdState = Field(default_factory=CrowdState)
    selected_image_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_entity_references(self) -> "SocialState":
        human_ids = [human.track_id for human in self.humans]
        if len(human_ids) != len(set(human_ids)):
            raise ValueError("human track IDs must be unique within a social state")

        group_ids = [group.group_id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("group IDs must be unique within a social state")

        known_humans = set(human_ids)
        known_groups = set(group_ids)
        for group in self.groups:
            unknown_members = set(group.member_ids) - known_humans
            if unknown_members:
                raise ValueError(
                    f"group {group.group_id!r} references unknown humans: "
                    f"{sorted(unknown_members)}"
                )
        for human in self.humans:
            if human.group_id is not None and human.group_id not in known_groups:
                raise ValueError(
                    f"human {human.track_id!r} references unknown group "
                    f"{human.group_id!r}"
                )
        return self


class BehaviorPreferences(DomainModel):
    target_speed_mps: NonNegativeFloat | None = None
    preferred_social_distance_m: NonNegativeFloat | None = None
    passing_side: PassingSide | None = None
    orientation_target_rad: FiniteFloat | None = None
    hold_duration_s: NonNegativeFloat | None = None


class BehaviorIntent(DomainModel):
    """High-level policy proposal that must be validated before execution."""

    schema_version: SchemaVersion
    decision_id: Identifier
    observation_id: Identifier
    social_state_id: Identifier
    created_at_us: NonNegativeInt
    action: Action
    target_human_id: EntityId | None = None
    preferences: BehaviorPreferences = Field(default_factory=BehaviorPreferences)
    valid_for_ms: Annotated[int, Field(gt=0, le=60_000)]
    reason_codes: list[
        Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]
    ] = Field(min_length=1, max_length=16)
    decision_confidence: Probability | None = None

    @model_validator(mode="after")
    def validate_action_target(self) -> "BehaviorIntent":
        targeted_actions = {
            Action.ORIENT.value,
            Action.APPROACH.value,
            Action.GREET.value,
            Action.GUIDE.value,
        }
        if self.action in targeted_actions and self.target_human_id is None:
            raise ValueError(f"{self.action} requires target_human_id")
        return self
