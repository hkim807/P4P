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
        return self


class DecisionRobot(FrozenModel):
    motion_state: Literal['STATIONARY', 'MOVING', 'UNKNOWN']
    motion_description: str = MOTION_DESCRIPTIONS['UNKNOWN']

    @model_validator(mode='after')
    def derive_description(self):
        object.__setattr__(self, 'motion_description', MOTION_DESCRIPTIONS[self.motion_state])
        return self


class DecisionState(FrozenModel):
    schema_version: Literal[3] = 3
    observation_readiness: Literal['READY', 'NOT_READY']
    readiness_reason: str
    robot: DecisionRobot
    people: list[DecisionPerson]
    no_person_observed_s: float

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


def prepare_decision_input(state: SocialState | dict | DecisionInput) -> DecisionInput:
    if isinstance(state, DecisionInput):
        # Revalidate detached values because nested containers can be mutable.
        detached = DecisionState.model_validate(state.state.model_dump(mode='python'))
        parameters = RuleParameters.model_validate(state.parameters.model_dump(mode='python'))
        if len(detached.people) != len(state.context.people):
            raise ValueError('decision people and correlation identities must align')
        return DecisionInput(detached, parameters, state.context)
    snapshot = SocialState.model_validate(state.model_dump(mode='python', warnings=False)
                                         if isinstance(state, SocialState) else state)
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
        ) for p in people],
        no_person_observed_s=snapshot.no_person_observed_s,
    )
    return DecisionInput(compact, rule_parameters(snapshot.config), DecisionContext(
        snapshot.state_id, snapshot.session_id, snapshot.robot_timestamp_us,
        tuple(PersonIdentity(p.uid, p.track_epoch) for p in people)))


def decision_hold_reason(state: DecisionState):
    return None if state.observation_readiness == 'READY' else state.readiness_reason
