"""Versioned, inspectable temporal configuration and SocialState contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    sustained_enter: float = Field(default=0.8, ge=0, le=1)
    sustained_exit: float = Field(default=0.65, ge=0, le=1)
    none_enter: float = Field(default=0.2, ge=0, le=1)
    none_exit: float = Field(default=0.35, ge=0, le=1)
    category_dwell_s: float = Field(default=0.3, ge=0)
    min_sustained_run_s: float = Field(default=0.4, ge=0)
    recurring_min_looking_s: float = Field(default=1.0, gt=0)
    recurring_min_fraction: float = Field(default=0.45, gt=0, le=1)
    recurring_min_bouts: int = Field(default=2, ge=2)
    yield_closing_max_distance_m: float | None = Field(default=3.0, gt=0)
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


class TemporalEvidence(StrictModel):
    window_span_s: float
    mean_gaze_overlap: float | None = None
    latest_gaze_looking: bool | None = None
    latest_gaze_overlap: float | None = Field(default=None, ge=0, le=1)
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
    stationary_window_confirmed: bool


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
    human_radial_motion: Literal["TOWARD", "STATIONARY", "AWAY", "UNKNOWN"]
    evidence: TemporalEvidence
    path_relation: Literal["UNKNOWN", "CLEAR", "CONFLICT"] = "UNKNOWN"
    pass_gesture: Literal["UNKNOWN", "PASS"] = "UNKNOWN"
    face_detected: bool | None = None
    relative_head_position: dict[str, str | float] | None = None
    validity_flags: list[str]


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
    estimator_version: Literal["temporal-social-v1", "temporal-social-v2"] = "temporal-social-v2"
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

    @model_validator(mode="after")
    def derive_readiness(self):
        reason = observation_hold_reason(self)
        self.observation_readiness = "NOT_READY" if reason else "READY"
        self.readiness_reason = reason or "OBSERVATIONS_AVAILABLE"
        return self


def observation_hold_reason(state: SocialState) -> str | None:
    """Identical eligibility for rules, structured LLM, production and replay.

    Empty detections never establish that the encounter is socially clear.
    Explicit conflict and pass cues need no gaze history. Proximity alone
    does not establish a social action.
    """
    visible = [p for p in state.people if p.visibility == "OBSERVED"]
    if not visible:
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
