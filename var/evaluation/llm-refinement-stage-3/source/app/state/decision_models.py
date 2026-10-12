"""Shared compact policy evidence, separate from diagnostics and policy settings.

Projection does not calculate intent or an action. Readiness and descriptive
labels come from revalidated SocialState; every numeric rule operand is retained.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from typing import Literal

from pydantic import ConfigDict, Field

from app.state.social_models import (Gaze, ObservationLabels, PathPosition,
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


class DecisionPerson(FrozenModel):
    visibility: Literal['OBSERVED'] = 'OBSERVED'
    latest_distance_m: float | None
    gaze_state: Gaze
    distance_zone: Zone
    relative_distance_trend: Trend
    path_relation: Literal['UNKNOWN', 'CLEAR', 'CONFLICT']
    pass_gesture: Literal['UNKNOWN', 'PASS']
    face_detected: bool | None
    path_position: PathPosition
    evidence: DecisionEvidence
    observation_labels: ObservationLabels


class DecisionRobot(FrozenModel):
    motion_state: Literal['STATIONARY', 'MOVING', 'UNKNOWN']


class DecisionState(FrozenModel):
    schema_version: Literal[1] = 1
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
            path_relation=p.path_relation, pass_gesture=p.pass_gesture,
            face_detected=p.face_detected, path_position=p.path_position,
            evidence=DecisionEvidence(**{name: getattr(p.evidence, name) for name in evidence_fields}),
            observation_labels=ObservationLabels.model_validate(p.observation_labels.model_dump()),
        ) for p in people],
        no_person_observed_s=snapshot.no_person_observed_s,
    )
    return DecisionInput(compact, rule_parameters(snapshot.config), DecisionContext(
        snapshot.state_id, snapshot.session_id, snapshot.robot_timestamp_us,
        tuple(PersonIdentity(p.uid, p.track_epoch) for p in people)))


def decision_hold_reason(state: DecisionState):
    return None if state.observation_readiness == 'READY' else state.readiness_reason
