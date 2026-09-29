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


class StationaryInterval(StrictModel):
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_us < self.start_us:
            raise ValueError("end_us must be >= start_us")
        return self


class MotionContext(StrictModel):
    """Independent confirmation, not a label inferred from a recording filename."""
    source: str = Field(min_length=1)
    stationary_intervals: list[StationaryInterval]

    def covers(self, start_us: int, end_us: int) -> bool:
        return any(i.start_us <= start_us <= end_us <= i.end_us for i in self.stationary_intervals)

    @classmethod
    def from_file(cls, path: str | Path):
        return cls.model_validate_json(Path(path).read_text())


Gaze = Literal["NONE", "INTERMITTENT", "SUSTAINED", "UNKNOWN"]
Zone = Literal["TOO_CLOSE", "INTERACTION_RANGE", "APPROACHABLE", "FAR", "UNKNOWN"]
Trend = Literal["DECREASING", "STABLE", "INCREASING", "UNKNOWN"]


class TemporalEvidence(StrictModel):
    window_span_s: float
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
    estimator_version: Literal["temporal-social-v1"] = "temporal-social-v1"
    state_id: str
    session_id: str
    ingest_sequence: int
    robot_timestamp_us: int
    config_version: str
    calibration_status: Literal["PROVISIONAL"] = "PROVISIONAL"
    config: TemporalConfig
    motion_context: MotionContext | None
    robot: RobotState
    people: list[PersonSocialState]
    cue_changes: list[CueChange]
    track_events: list[dict[str, int | str]]
    # No target selection, collision interpretation, or engagement probability yet.
    active_target_uid: None = None
    active_target_track_epoch: None = None
    range_data_status: Literal["UNKNOWN"] = "UNKNOWN"
