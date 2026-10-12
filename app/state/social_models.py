"""Versioned, inspectable temporal configuration and SocialState contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.models import RelativeHeadPosition


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class TemporalConfig(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)
    window_s: float = Field(default=2.0, gt=0)
    max_gap_s: float = Field(default=0.25, gt=0)
    min_span_s: float = Field(default=1.0, gt=0)
    min_samples: int = Field(default=5, ge=3)
    min_gaze_coverage_s: float = Field(default=0.8, gt=0)
    min_gaze_coverage_fraction: float = Field(default=0.6, gt=0, le=1)
    looking_enter: float = Field(default=0.8, ge=0, le=1)
    looking_exit: float = Field(default=0.7, ge=0, le=1)
    gaze_unavailable_score: float | None = Field(default=None, ge=0, le=1)
    far_gaze_distance_m: float | None = Field(default=None, gt=0)
    far_looking_enter: float = Field(default=0.97, ge=0, le=1)
    far_looking_exit: float = Field(default=0.95, ge=0, le=1)
    sustained_enter: float = Field(default=0.8, ge=0, le=1)
    sustained_exit: float = Field(default=0.65, ge=0, le=1)
    none_enter: float = Field(default=0.2, ge=0, le=1)
    none_exit: float = Field(default=0.35, ge=0, le=1)
    category_dwell_s: float = Field(default=0.3, ge=0)
    min_sustained_run_s: float = Field(default=0.4, ge=0)
    recurring_min_looking_s: float = Field(default=1.0, gt=0)
    recurring_min_fraction: float = Field(default=0.45, gt=0, le=1)
    recurring_min_bouts: int = Field(default=2, ge=2)
    allow_recurring_attention: bool = True
    no_person_continue_after_s: float | None = Field(default=None, gt=0)
    yield_closing_max_distance_m: float | None = Field(default=3.0, gt=0)
    path_assume_fixed_head: bool = True
    path_half_width_m: float = Field(default=0.5, gt=0)
    path_hysteresis_m: float = Field(default=0.1, ge=0)
    path_camera_x_m: float = 0.0
    path_camera_y_m: float = 0.0
    path_camera_yaw_deg: float = Field(default=0.0, ge=-180, le=180)
    distance_deadband_mps: float = Field(default=0.1, gt=0)
    max_distance_speed_mps: float = Field(default=3.0, gt=0)
    distance_jump_allowance_m: float = Field(default=0.05, ge=0)
    max_fit_residual_m: float = Field(default=0.1, gt=0)
    max_fit_samples: int = Field(default=32, ge=3, le=128)
    too_close_m: float = Field(default=0.6, gt=0)
    interaction_max_m: float = Field(default=1.5, gt=0)
    approachable_max_m: float = Field(default=3.0, gt=0)
    zone_hysteresis_m: float = Field(default=0.1, ge=0)
    stationary_linear_tolerance_mps: float = Field(default=0.02, ge=0)
    stationary_angular_tolerance_rps: float = Field(default=0.03, ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if not self.far_looking_exit < self.far_looking_enter:
            raise ValueError("far_looking_exit must be below far_looking_enter")
        if self.path_hysteresis_m >= self.path_half_width_m:
            raise ValueError("path hysteresis must be smaller than path half width")
        if not self.looking_exit < self.looking_enter:
            raise ValueError("looking_exit must be below looking_enter")
        if not 0 <= self.none_enter < self.none_exit < self.sustained_exit < self.sustained_enter <= 1:
            raise ValueError("gaze category thresholds must have separated entry/exit bands")
        if max(self.min_span_s, self.min_gaze_coverage_s, self.min_sustained_run_s) > self.window_s:
            raise ValueError("minimum evidence requirements must fit within window_s")
        if self.max_gap_s > self.window_s:
            raise ValueError("max_gap_s must fit within window_s")
        if not self.too_close_m < self.interaction_max_m < self.approachable_max_m:
            raise ValueError("distance boundaries must increase")
        if self.zone_hysteresis_m >= min(self.too_close_m, (self.interaction_max_m-self.too_close_m)/2,
                                         (self.approachable_max_m-self.interaction_max_m)/2):
            raise ValueError("zone hysteresis must not overlap adjacent boundaries")
        if self.max_fit_samples < self.min_samples:
            raise ValueError("max_fit_samples must be at least min_samples")
        return self

    @classmethod
    def from_file(cls, path: str | Path):
        return cls.model_validate_json(Path(path).read_text())

    @property
    def version(self):
        digest = hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()[:12]
        return f"temporal-v1-{digest}"


Gaze = Literal["NONE", "INTERMITTENT", "SUSTAINED", "UNKNOWN"]
Zone = Literal["TOO_CLOSE", "INTERACTION_RANGE", "APPROACHABLE", "FAR", "UNKNOWN"]
Trend = Literal["DECREASING", "STABLE", "INCREASING", "UNKNOWN"]
PathPosition = Literal["IN_WAY", "TO_SIDE", "BEHIND", "UNKNOWN"]


class TemporalEvidence(StrictModel):
    window_span_s: float
    mean_gaze_overlap: float | None = None
    latest_gaze_looking: bool | None = None
    latest_gaze_overlap: float | None = Field(default=None, ge=0, le=1)
    looking_enter_threshold: float | None = Field(default=None, ge=0, le=1)
    looking_exit_threshold: float | None = Field(default=None, ge=0, le=1)
    looking_time_s: float = 0.0
    looking_bouts: int = 0
    gaze_fraction: float | None
    gaze_valid_coverage_s: float
    gaze_coverage_fraction: float
    gaze_valid_samples: int
    sustained_gaze_s: float
    distance_slope_mps: float | None
    distance_valid_span_s: float
    distance_fit_residual_m: float | None
    distance_valid_samples: int
    distance_fit_samples: int
    distance_window_start_us: int | None
    distance_jump_count: int
    gaze_valid: bool
    distance_trend_valid: bool
    latest_distance_valid: bool


class ObservationLabels(StrictModel):
    """Shared descriptive aliases, never interaction intent or policy advice."""

    version: Literal[1] = 1
    attention_over_time: Literal["LITTLE_OR_NO_LOOKING_AT_ROBOT", "INTERMITTENT_LOOKING_AT_ROBOT",
                                 "SUSTAINED_LOOKING_AT_ROBOT", "UNKNOWN"] = "UNKNOWN"
    current_gaze: Literal["LOOKING_AT_ROBOT", "NOT_LOOKING_AT_ROBOT", "UNKNOWN"] = "UNKNOWN"
    proximity: Literal["CLOSER_THAN_CONVERSATION_DISTANCE", "WITHIN_CONVERSATION_DISTANCE",
                       "BEYOND_CONVERSATION_DISTANCE", "BEYOND_CONFIGURED_SOCIAL_RANGE", "UNKNOWN"] = "UNKNOWN"
    corridor_position: Literal["IN_ROBOT_FORWARD_CORRIDOR", "BESIDE_ROBOT_FORWARD_CORRIDOR",
                               "BEHIND_ROBOT", "UNKNOWN"] = "UNKNOWN"
    relative_separation: Literal["GETTING_CLOSER", "STEADY", "GETTING_FARTHER", "UNKNOWN"] = "UNKNOWN"


class PersonSocialState(StrictModel):
    uid: int
    track_epoch: int
    visibility: Literal["OBSERVED", "TEMPORARILY_MISSING"]
    track_age_s: float
    time_since_seen_s: float
    latest_distance_m: float | None
    gaze_state: Gaze
    distance_zone: Zone
    relative_distance_trend: Trend
    evidence: TemporalEvidence
    path_relation: Literal["UNKNOWN", "CLEAR", "CONFLICT"] = "UNKNOWN"
    pass_gesture: Literal["UNKNOWN", "PASS"] = "UNKNOWN"
    face_detected: bool | None = None
    relative_head_position: dict[str, str | float] | None = None
    relative_nose_position: RelativeHeadPosition | None = None
    path_position: PathPosition = "UNKNOWN"
    nose_forward_m: float | None = None
    nose_lateral_m: float | None = None
    validity_flags: list[str]
    observation_labels: ObservationLabels = Field(default_factory=ObservationLabels)

    @model_validator(mode="after")
    def derive_observation_labels(self):
        # Recompute even when labels were supplied by a saved state. Categories
        # and validity remain authoritative, so stale aliases cannot override them.
        labels = ObservationLabels()
        if self.visibility == "OBSERVED":
            e = self.evidence
            if e.gaze_valid:
                labels.attention_over_time = {
                    "NONE": "LITTLE_OR_NO_LOOKING_AT_ROBOT",
                    "INTERMITTENT": "INTERMITTENT_LOOKING_AT_ROBOT",
                    "SUSTAINED": "SUSTAINED_LOOKING_AT_ROBOT",
                    "UNKNOWN": "UNKNOWN",
                }[self.gaze_state]
            # A valid current sample can exist before enough history accumulates.
            if e.latest_gaze_looking is not None and e.latest_gaze_overlap is not None:
                labels.current_gaze = ("LOOKING_AT_ROBOT" if e.latest_gaze_looking
                                       else "NOT_LOOKING_AT_ROBOT")
            if e.latest_distance_valid and self.latest_distance_m is not None and self.latest_distance_m >= 0:
                labels.proximity = {
                    "TOO_CLOSE": "CLOSER_THAN_CONVERSATION_DISTANCE",
                    "INTERACTION_RANGE": "WITHIN_CONVERSATION_DISTANCE",
                    "APPROACHABLE": "BEYOND_CONVERSATION_DISTANCE",
                    "FAR": "BEYOND_CONFIGURED_SOCIAL_RANGE",
                    "UNKNOWN": "UNKNOWN",
                }[self.distance_zone]
            labels.corridor_position = {
                "IN_WAY": "IN_ROBOT_FORWARD_CORRIDOR",
                "TO_SIDE": "BESIDE_ROBOT_FORWARD_CORRIDOR",
                "BEHIND": "BEHIND_ROBOT", "UNKNOWN": "UNKNOWN",
            }[self.path_position]
            if e.distance_trend_valid:
                labels.relative_separation = {
                    "DECREASING": "GETTING_CLOSER", "STABLE": "STEADY",
                    "INCREASING": "GETTING_FARTHER", "UNKNOWN": "UNKNOWN",
                }[self.relative_distance_trend]
        self.observation_labels = labels
        return self


class RobotState(StrictModel):
    linear_velocity: float | None
    angular_velocity: float | None
    motion_state: Literal["STATIONARY", "MOVING", "UNKNOWN"]
    measurement_validity: list[str]


class CueChange(StrictModel):
    uid: int
    track_epoch: int
    field: str
    previous: str
    current: str


class SocialState(StrictModel):
    schema_version: Literal[1] = 1
    estimator_version: Literal["temporal-social-v1", "temporal-social-v2", "temporal-social-v3", "temporal-social-v4", "temporal-social-v5", "temporal-social-v6"] = "temporal-social-v6"
    observation_readiness: Literal["READY", "NOT_READY"] = "NOT_READY"
    readiness_reason: str = "UNASSESSED"
    state_id: str
    session_id: str
    ingest_sequence: int
    robot_timestamp_us: int
    config_version: str
    calibration_status: Literal["PROVISIONAL"] = "PROVISIONAL"
    config: TemporalConfig
    robot: RobotState
    people: list[PersonSocialState]
    cue_changes: list[CueChange]
    track_events: list[dict[str, int | str]]
    no_person_observed_s: float = Field(default=0.0, ge=0)
    no_person_observed_samples: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def derive_readiness(self):
        reason = observation_hold_reason(self)
        self.observation_readiness = "NOT_READY" if reason else "READY"
        self.readiness_reason = reason or "OBSERVATIONS_AVAILABLE"
        return self


def observation_hold_reason(state: SocialState) -> str | None:
    """Identical eligibility for rules, structured LLM, production and replay.

    Configured sustained empty observations permit a nominal CONTINUE.
    Explicit conflict and pass cues need no gaze history. Proximity alone
    does not establish a social action.
    """
    visible = [p for p in state.people if p.visibility == "OBSERVED"]
    if not visible:
        if (state.config.no_person_continue_after_s is not None
                and state.no_person_observed_s >= state.config.no_person_continue_after_s
                and state.no_person_observed_samples >= state.config.min_samples):
            return None
        return "NO_VISIBLE_PERSON"
    if len(visible) != 1:
        return "MULTIPLE_VISIBLE_PEOPLE"
    person = visible[0]
    if person.path_relation == "CONFLICT":
        return None
    if (not person.evidence.latest_distance_valid or person.latest_distance_m is None
            or person.latest_distance_m < 0 or person.distance_zone == "UNKNOWN"):
        return "DISTANCE_UNKNOWN"
    if person.pass_gesture == "PASS":
        return None
    if not person.evidence.gaze_valid:
        return "INSUFFICIENT_GAZE_EVIDENCE"
    if person.gaze_state == "UNKNOWN":
        return "GAZE_CATEGORY_NOT_READY"
    return None
