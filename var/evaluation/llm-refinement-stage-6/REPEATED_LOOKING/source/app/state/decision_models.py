"""Shared compact policy evidence, separate from diagnostics and policy settings.

Projection does not calculate intent or an action. Readiness and descriptive
labels come from revalidated SocialState; every numeric rule operand is retained.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.state.social_models import (Gaze, PathPosition,
    SocialState, StrictModel, TemporalConfig, Trend, Zone)


class FrozenModel(StrictModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False, frozen=True)


TemporalSummaryMode = Literal['NONE', 'LOOKING_CHANGE', 'REPEATED_LOOKING', 'DETECTION_RESUMED']
# One shared representation setting; experiments change it for both policies.
# Keep additions disabled unless their evaluation justifies enabling them.
TEMPORAL_SUMMARY_MODE: TemporalSummaryMode = 'NONE'
TEMPORAL_DESCRIPTIONS = {
    'LOOKING_CHANGE': 'Some measured looking occurred earlier in the recent window; the latest gaze sample is not looking at the robot.',
    'REPEATED_LOOKING': 'Multiple measured looking episodes with measured non-looking between them; individual episode durations are not established.',
    'DETECTION_RESUMED': 'This track is detected again after missing detections; its gaze during the detection gap is unknown.',
}


class TemporalSummary(FrozenModel):
    version: Literal[1] = 1
    kind: Literal['LOOKING_CHANGE', 'REPEATED_LOOKING', 'DETECTION_RESUMED']
    description: str = ''

    @model_validator(mode='after')
    def derive_description(self):
        object.__setattr__(self, 'description', TEMPORAL_DESCRIPTIONS[self.kind])
        return self


class DecisionEvidence(FrozenModel):
    gaze_valid: bool
    distance_trend_valid: bool
    latest_distance_valid: bool
    latest_gaze_looking: bool | None
    latest_gaze_overlap: float | None = Field(ge=0, le=1)
    looking_exit_threshold: float | None = Field(ge=0, le=1)
    looking_bouts: int
    looking_time_s: float
    gaze_fraction: float | None
    sustained_gaze_s: float


ATTENTION_DESCRIPTIONS = {
    'NONE': 'Little or no looking at the robot during the recent observation window.',
    'INTERMITTENT': 'Intermittent looking at the robot during the recent observation window.',
    'SUSTAINED': 'Looking at the robot for a high fraction of covered recent time; not necessarily continuously.',
    'UNKNOWN': 'Looking history is unavailable or insufficient.',
}
CURRENT_GAZE_DESCRIPTIONS = {
    True: 'Latest gaze sample is classified as looking at the robot.',
    False: 'Latest gaze sample is classified as not looking at the robot.',
    None: 'Latest gaze classification is unavailable.',
}
PROXIMITY_DESCRIPTIONS = {
    'TOO_CLOSE': 'Closer than the configured conversation distance.',
    'INTERACTION_RANGE': 'Within the configured conversation distance.',
    'APPROACHABLE': 'Beyond conversation distance, within the configured social range.',
    'FAR': 'Beyond the configured social range.',
    'UNKNOWN': 'Current distance category is unavailable.',
}
CORRIDOR_DESCRIPTIONS = {
    'IN_WAY': 'Position estimate is inside the robot forward corridor; future obstruction is unknown.',
    'TO_SIDE': 'Position estimate is beside the robot forward corridor; future clearance is unknown.',
    'BEHIND': 'Position estimate is behind the robot.',
    'UNKNOWN': 'Position relative to the robot forward corridor is unavailable.',
}
SEPARATION_DESCRIPTIONS = {
    'DECREASING': 'Robot-person distance is decreasing; either participant may cause this.',
    'STABLE': 'Robot-person distance is approximately steady; individual motion is unknown.',
    'INCREASING': 'Robot-person distance is increasing; either participant may cause this.',
    'UNKNOWN': 'Recent robot-person distance trend is unavailable or unreliable.',
}
FACE_DESCRIPTIONS = {
    True: 'A face was detected; facing the robot is not established.',
    False: 'No face was detected in the latest observation.',
    None: 'Face detection information is unavailable.',
}
MOTION_DESCRIPTIONS = {
    'MOVING': 'Measured robot motion exceeds stationary tolerances; it may be translating or turning.',
    'STATIONARY': 'Measured robot translation and rotation are within stationary tolerances.',
    'UNKNOWN': 'Robot motion information is unavailable.',
}


class DecisionObservationLabels(FrozenModel):
    """Deterministic descriptions of measurements, never inferred social intent."""
    version: Literal[3] = 3
    attention_over_time: str = ATTENTION_DESCRIPTIONS['UNKNOWN']
    current_gaze: str = CURRENT_GAZE_DESCRIPTIONS[None]
    proximity: str = PROXIMITY_DESCRIPTIONS['UNKNOWN']
    corridor_position: str = CORRIDOR_DESCRIPTIONS['UNKNOWN']
    relative_separation: str = SEPARATION_DESCRIPTIONS['UNKNOWN']
    face_detection: str = FACE_DESCRIPTIONS[None]


class DecisionPerson(FrozenModel):
    visibility: Literal['OBSERVED'] = 'OBSERVED'
    latest_distance_m: float | None
    gaze_state: Gaze
    distance_zone: Zone
    relative_distance_trend: Trend
    face_detected: bool | None
    path_position: PathPosition
    evidence: DecisionEvidence
    observation_labels: DecisionObservationLabels = Field(default_factory=DecisionObservationLabels)
    temporal_summary: TemporalSummary | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode='after')
    def derive_descriptions(self):
        # Recompute from exact categories/validity, including detached compact
        # input. Saved or caller-supplied prose cannot become policy evidence.
        e = self.evidence
        labels = DecisionObservationLabels(
            attention_over_time=ATTENTION_DESCRIPTIONS[self.gaze_state if e.gaze_valid else 'UNKNOWN'],
            current_gaze=CURRENT_GAZE_DESCRIPTIONS[e.latest_gaze_looking
                if e.latest_gaze_overlap is not None else None],
            proximity=PROXIMITY_DESCRIPTIONS[self.distance_zone
                if e.latest_distance_valid and self.latest_distance_m is not None
                and self.latest_distance_m >= 0 else 'UNKNOWN'],
            corridor_position=CORRIDOR_DESCRIPTIONS[self.path_position],
            relative_separation=SEPARATION_DESCRIPTIONS[self.relative_distance_trend
                if e.distance_trend_valid else 'UNKNOWN'],
            face_detection=FACE_DESCRIPTIONS[self.face_detected],
        )
        object.__setattr__(self, 'observation_labels', labels)
        summary = self.temporal_summary
        if summary is not None and summary.kind != 'DETECTION_RESUMED':
            if not gaze_summary_available(summary.kind, e):
                object.__setattr__(self, 'temporal_summary', None)
        return self


class DecisionRobot(FrozenModel):
    motion_state: Literal['STATIONARY', 'MOVING', 'UNKNOWN']
    motion_description: str = MOTION_DESCRIPTIONS['UNKNOWN']

    @model_validator(mode='after')
    def derive_description(self):
        object.__setattr__(self, 'motion_description', MOTION_DESCRIPTIONS[self.motion_state])
        return self


class DecisionState(FrozenModel):
    schema_version: Literal[3, 4] = 3
    observation_readiness: Literal['READY', 'NOT_READY']
    readiness_reason: str
    robot: DecisionRobot
    people: list[DecisionPerson]
    no_person_observed_s: float

    @model_validator(mode='after')
    def derive_schema_version(self):
        # No-summary states retain byte-identical v3 payloads. v4 identifies
        # states with the optional temporal extension, not a readiness change.
        object.__setattr__(self, 'schema_version',
                           4 if any(p.temporal_summary is not None for p in self.people) else 3)
        return self

    def canonical_json(self):
        return json.dumps(self.model_dump(mode='json'), sort_keys=True,
                          separators=(',', ':'), ensure_ascii=False, allow_nan=False)

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


class RuleParameters(FrozenModel):
    """Decision constants loaded/cached outside the shared observation payload."""
    looking_exit: float
    yield_closing_max_distance_m: float | None
    allow_recurring_attention: bool
    recurring_min_bouts: int
    recurring_min_looking_s: float
    recurring_min_fraction: float
    min_sustained_run_s: float


@lru_cache(maxsize=32)
def rule_parameters(config: TemporalConfig):
    return RuleParameters(**{name: getattr(config, name) for name in RuleParameters.model_fields})


@dataclass(frozen=True)
class PersonIdentity:
    uid: int
    track_epoch: int


@dataclass(frozen=True)
class DecisionContext:
    state_id: str
    session_id: str
    robot_timestamp_us: int
    people: tuple[PersonIdentity, ...]


@dataclass(frozen=True)
class DecisionInput:
    state: DecisionState
    parameters: RuleParameters
    context: DecisionContext


def gaze_summary_available(kind, evidence):
    known = (evidence.gaze_valid and evidence.latest_gaze_overlap is not None
             and evidence.latest_gaze_looking is not None)
    if kind == 'LOOKING_CHANGE':
        return known and evidence.latest_gaze_looking is False and evidence.looking_time_s > 0
    if kind == 'REPEATED_LOOKING':
        return known and evidence.looking_bouts >= 2
    return False


def temporal_summary(mode, person, snapshot):
    if mode in ('LOOKING_CHANGE', 'REPEATED_LOOKING'):
        return TemporalSummary(kind=mode) if gaze_summary_available(mode, person.evidence) else None
    if mode == 'DETECTION_RESUMED':
        # A nose-based UID reassociation is not a missing-detection interval.
        resumed = any(event.get('type') == 'REACQUIRED'
            and event.get('uid') == person.uid and event.get('track_epoch') == person.track_epoch
            and event.get('timestamp_us') == snapshot.robot_timestamp_us
            for event in snapshot.track_events)
        return TemporalSummary(kind=mode) if resumed else None
    return None


def prepare_decision_input(state: SocialState | dict | DecisionInput, *,
                           temporal_summary_mode: TemporalSummaryMode | None = None) -> DecisionInput:
    if isinstance(state, DecisionInput):
        if temporal_summary_mode is not None:
            raise ValueError('change temporal summary mode by projecting a SocialState')
        # Revalidate detached values because nested containers can be mutable.
        detached = DecisionState.model_validate(state.state.model_dump(mode='python'))
        parameters = RuleParameters.model_validate(state.parameters.model_dump(mode='python'))
        if len(detached.people) != len(state.context.people):
            raise ValueError('decision people and correlation identities must align')
        return DecisionInput(detached, parameters, state.context)
    snapshot = SocialState.model_validate(state.model_dump(mode='python', warnings=False)
                                         if isinstance(state, SocialState) else state)
    mode = TEMPORAL_SUMMARY_MODE if temporal_summary_mode is None else temporal_summary_mode
    if mode not in ('NONE', *TEMPORAL_DESCRIPTIONS):
        raise ValueError('unsupported temporal summary mode')
    people = [person for person in snapshot.people if person.visibility == 'OBSERVED']
    evidence_fields = DecisionEvidence.model_fields
    compact = DecisionState(
        observation_readiness=snapshot.observation_readiness,
        readiness_reason=snapshot.readiness_reason,
        robot=DecisionRobot(motion_state=snapshot.robot.motion_state),
        people=[DecisionPerson(
            latest_distance_m=p.latest_distance_m, gaze_state=p.gaze_state,
            distance_zone=p.distance_zone, relative_distance_trend=p.relative_distance_trend,
            face_detected=p.face_detected, path_position=p.path_position,
            evidence=DecisionEvidence(**{name: getattr(p.evidence, name) for name in evidence_fields}),
            temporal_summary=temporal_summary(mode, p, snapshot),
        ) for p in people],
        no_person_observed_s=snapshot.no_person_observed_s,
    )
    return DecisionInput(compact, rule_parameters(snapshot.config), DecisionContext(
        snapshot.state_id, snapshot.session_id, snapshot.robot_timestamp_us,
        tuple(PersonIdentity(p.uid, p.track_epoch) for p in people)))


def decision_hold_reason(state: DecisionState):
    return None if state.observation_readiness == 'READY' else state.readiness_reason
