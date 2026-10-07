"""Deterministic, stateless decisions from one SocialState."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.model_decision import FinalDecision
from app.state.social_models import SocialState, StrictModel


POLICY_VERSION = "social-rules-v1"
DecisionName = Literal["CONTINUE", "APPROACH", "ENGAGE", "YIELD", "DEFER"]


class PolicyDecision(StrictModel):
    decision_id: str
    source_state_id: str
    session_id: str
    policy_version: Literal["social-rules-v1"] = POLICY_VERSION
    decision: DecisionName
    reason_code: str
    target_uid: int | None = Field(default=None, ge=0)
    target_track_epoch: int | None = Field(default=None, ge=1)


_FINAL_REASONS = {
    "NO_VISIBLE_PERSON": "No person is currently visible; continue along the fixed route.",
    "NO_ATTENTION": "The person is not looking at the robot; continue along the fixed route.",
    "PERSON_MOVING_AWAY": "The person is moving away; continue along the fixed route.",
    "SUSTAINED_GAZE_IN_INTERACTION_RANGE": (
        "The person is looking steadily at the robot and is within conversation distance."
    ),
    "SUSTAINED_GAZE_IN_APPROACHABLE_RANGE": (
        "The person is looking steadily at the robot and is within approach distance."
    ),
}
_ACTION_REASONS = {
    "CONTINUE": "Continue along the existing fixed route without initiating an interaction.",
    "APPROACH": "Move towards the observed person and stop at conversation distance.",
    "ENGAGE": "Stop or remain stationary and initiate an interaction with the nearby person.",
    "YIELD": "Give the person room to pass, then remain stopped after moving aside and backwards.",
}


def normalise_rule_decision(proposal: PolicyDecision | dict) -> FinalDecision | None:
    """Normalise a pure rule proposal; DEFER has no final decision."""
    proposal = PolicyDecision.model_validate(proposal)
    if proposal.decision == "DEFER":
        return None
    return FinalDecision(
        action=proposal.decision,
        reason=_FINAL_REASONS.get(proposal.reason_code, _ACTION_REASONS[proposal.decision]),
    )


def decide(state: SocialState | dict, *, stale: bool = False,
           processing_failed: bool = False) -> PolicyDecision:
    """Classify one scene. Caller supplies freshness and processing status.

    DEFER is a decision to keep observing, never an executable robot command.
    This function has no interaction memory; target_lock.py handles the lock.
    """
    state = SocialState.model_validate(state)

    def result(decision: DecisionName, reason: str, person=None) -> PolicyDecision:
        return PolicyDecision(
            decision_id=f"{state.state_id}:{POLICY_VERSION}",
            source_state_id=state.state_id,
            session_id=state.session_id,
            decision=decision,
            reason_code=reason,
            target_uid=person.uid if person is not None else None,
            target_track_epoch=person.track_epoch if person is not None else None,
        )

    if processing_failed:
        return result("DEFER", "PROCESSING_FAILED")
    if stale:
        return result("DEFER", "STATE_STALE")

    visible = [person for person in state.people if person.visibility == "OBSERVED"]
    if len(visible) > 1:
        return result("DEFER", "MULTIPLE_VISIBLE_PEOPLE")
    if not visible:
        return result("CONTINUE", "NO_VISIBLE_PERSON")

    person = visible[0]
    if person.distance_zone == "TOO_CLOSE":
        return result("DEFER", "PERSON_TOO_CLOSE")
    if person.distance_zone == "UNKNOWN" or not person.evidence.latest_distance_valid:
        return result("DEFER", "DISTANCE_UNKNOWN")

    if person.gaze_state == "NONE" and person.evidence.gaze_valid:
        return result("CONTINUE", "NO_ATTENTION")
    if (person.human_radial_motion == "AWAY" and person.evidence.distance_trend_valid
            and person.evidence.stationary_window_confirmed):
        return result("CONTINUE", "PERSON_MOVING_AWAY")

    if person.gaze_state != "SUSTAINED" or not person.evidence.gaze_valid:
        return result("DEFER", "GAZE_INSUFFICIENT_OR_INTERMITTENT")
    if (person.human_radial_motion not in ("TOWARD", "STATIONARY")
            or not person.evidence.distance_trend_valid
            or not person.evidence.stationary_window_confirmed):
        return result("DEFER", "HUMAN_MOTION_UNKNOWN")

    if person.distance_zone == "INTERACTION_RANGE":
        return result("ENGAGE", "SUSTAINED_GAZE_IN_INTERACTION_RANGE", person)
    if person.distance_zone == "APPROACHABLE":
        return result("APPROACH", "SUSTAINED_GAZE_IN_APPROACHABLE_RANGE", person)
    return result("DEFER", "PERSON_FAR")
