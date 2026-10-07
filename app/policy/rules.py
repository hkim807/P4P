"""Deterministic, stateless decisions from one SocialState."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.model_decision import FinalDecision
from app.state.social_models import SocialState, StrictModel


POLICY_VERSION = "social-rules-v2"
DecisionName = Literal["CONTINUE", "APPROACH", "ENGAGE", "YIELD"]


class PolicyDecision(StrictModel):
    decision_id: str
    source_state_id: str
    session_id: str
    policy_version: Literal["social-rules-v2"] = POLICY_VERSION
    decision: DecisionName
    reason_code: str
    target_uid: int | None = Field(default=None, ge=0)
    target_track_epoch: int | None = Field(default=None, ge=1)


_FINAL_REASONS = {
    "PERSON_TOO_CLOSE": "The person is in the TOO_CLOSE zone; give more space by moving aside and backwards, then remain stopped. This provisional proximity rule does not imply a blocked path.",
    "NO_INTERACTION_CUE": "Usable gaze evidence does not show sustained attention; continue along the fixed route.",
    "PERSON_FAR": "The person is outside the interaction and approach ranges; continue along the fixed route.",
    "NO_ATTENTION": "Gaze evidence is classified as no attention; continue along the fixed route.",
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


def normalise_rule_decision(proposal: PolicyDecision | dict) -> FinalDecision:
    """Normalise one of the four eligible rule actions."""
    proposal = PolicyDecision.model_validate(proposal)
    return FinalDecision(
        action=proposal.decision,
        reason=_FINAL_REASONS.get(proposal.reason_code, _ACTION_REASONS[proposal.decision]),
    )


def rule_readiness(state: SocialState | dict, *, stale: bool = False,
                   processing_failed: bool = False) -> str | None:
    """Return an observation hold reason, or None when classification is eligible."""
    state = SocialState.model_validate(state)
    if processing_failed:
        return "PROCESSING_FAILED"
    if stale:
        return "STATE_STALE"
    visible = [person for person in state.people if person.visibility == "OBSERVED"]
    if not visible:
        return "NO_VISIBLE_PERSON"
    if len(visible) != 1:
        return "MULTIPLE_VISIBLE_PEOPLE"
    person = visible[0]
    if (not person.evidence.latest_distance_valid or person.latest_distance_m is None
            or person.latest_distance_m < 0 or person.distance_zone == "UNKNOWN"):
        return "DISTANCE_UNKNOWN"
    if not person.evidence.gaze_valid:
        return "INSUFFICIENT_GAZE_EVIDENCE"
    if person.gaze_state == "UNKNOWN":
        return "GAZE_CATEGORY_NOT_READY"
    return None


def decide(state: SocialState | dict, *, stale: bool = False,
           processing_failed: bool = False) -> PolicyDecision:
    """Classify eligible observations only; waiting is never a policy action."""
    state = SocialState.model_validate(state)
    reason = rule_readiness(state, stale=stale, processing_failed=processing_failed)
    if reason is not None:
        raise ValueError(f"rule observations not ready: {reason}")
    person = next(p for p in state.people if p.visibility == "OBSERVED")

    def result(decision: DecisionName, reason: str) -> PolicyDecision:
        targeted = decision in ("APPROACH", "ENGAGE")
        return PolicyDecision(
            decision_id=f"{state.state_id}:{POLICY_VERSION}",
            source_state_id=state.state_id, session_id=state.session_id,
            decision=decision, reason_code=reason,
            target_uid=person.uid if targeted else None,
            target_track_epoch=person.track_epoch if targeted else None,
        )

    if person.distance_zone == "TOO_CLOSE":
        return result("YIELD", "PERSON_TOO_CLOSE")
    if (person.human_radial_motion == "AWAY" and person.evidence.distance_trend_valid
            and person.evidence.stationary_window_confirmed):
        return result("CONTINUE", "PERSON_MOVING_AWAY")
    if person.gaze_state == "SUSTAINED":
        if person.distance_zone == "INTERACTION_RANGE":
            return result("ENGAGE", "SUSTAINED_GAZE_IN_INTERACTION_RANGE")
        if person.distance_zone == "APPROACHABLE":
            return result("APPROACH", "SUSTAINED_GAZE_IN_APPROACHABLE_RANGE")
    if person.gaze_state == "NONE":
        return result("CONTINUE", "NO_ATTENTION")
    return result("CONTINUE", "PERSON_FAR" if person.distance_zone == "FAR" else "NO_INTERACTION_CUE")
