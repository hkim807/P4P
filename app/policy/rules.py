"""Deterministic, stateless decisions from one SocialState."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain.model_decision import FinalDecision
from app.state.social_models import SocialState, StrictModel, observation_hold_reason


POLICY_VERSION = "social-rules-v4"
DecisionName = Literal["CONTINUE", "APPROACH", "ENGAGE", "YIELD"]


class PolicyDecision(StrictModel):
    decision_id: str
    source_state_id: str
    session_id: str
    policy_version: Literal["social-rules-v2", "social-rules-v3", "social-rules-v4"] = POLICY_VERSION
    decision: DecisionName
    reason_code: str
    target_uid: int | None = Field(default=None, ge=0)
    target_track_epoch: int | None = Field(default=None, ge=1)


_FINAL_REASONS = {
    "HUMAN_PATH_CONFLICT": "An upstream observation reports a likely route conflict; give the person priority.",
    "PASS_GESTURE": "An upstream observation reports a gesture inviting the robot to pass; continue along the route.",
    "ATTENTION_ENDED": "The latest valid gaze observation no longer shows attention; continue along the route.",
    "RELATIVE_SEPARATION_INCREASING": "Separation is increasing outside conversation range; avoid pursuing the person.",
    "RECURRING_ATTENTION_IN_INTERACTION_RANGE": "Repeated measured attention supports a conversation at the current distance.",
    "RECURRING_ATTENTION_IN_APPROACHABLE_RANGE": "Repeated measured attention supports approaching to conversation distance.",
    "CLOSING_DISTANCE_WITH_LOW_GAZE": "Measured separation is decreasing within the configured yield range, with a currently detected face and established low gaze towards the robot; give the person room to pass. Relative closing does not establish which participant is moving or a path conflict.",
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
    "YIELD": "Temporarily move aside and backwards to give the person room to pass, wait briefly, return towards the route, advance a short distance and end.",
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
    return observation_hold_reason(state)


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

    if person.path_relation == "CONFLICT":
        return result("YIELD", "HUMAN_PATH_CONFLICT")
    if person.pass_gesture == "PASS":
        return result("CONTINUE", "PASS_GESTURE")
    c, e = state.config, person.evidence
    if (person.face_detected is True and e.distance_trend_valid
            and person.relative_distance_trend == "DECREASING"
            and (c.yield_closing_max_distance_m is None
                 or person.latest_distance_m <= c.yield_closing_max_distance_m)
            and e.gaze_valid and person.gaze_state == "NONE"
            and e.latest_gaze_looking is False
            and e.latest_gaze_overlap is not None and e.latest_gaze_overlap <= c.looking_exit):
        return result("YIELD", "CLOSING_DISTANCE_WITH_LOW_GAZE")
    if person.evidence.latest_gaze_looking is False and person.gaze_state == "SUSTAINED":
        return result("CONTINUE", "ATTENTION_ENDED")
    recurring = (person.gaze_state == "INTERMITTENT" and e.latest_gaze_looking is True
                 and e.looking_bouts >= c.recurring_min_bouts
                 and e.looking_time_s >= c.recurring_min_looking_s
                 and e.gaze_fraction is not None and e.gaze_fraction >= c.recurring_min_fraction)
    sustained = (person.gaze_state == "SUSTAINED" and
                 (e.latest_gaze_looking is None or e.sustained_gaze_s + 1e-9 >= c.min_sustained_run_s))
    attentive = sustained or recurring
    # A greeting does not move closer: nearby attentive retreat differs from pursuit.
    if attentive and person.distance_zone in ("TOO_CLOSE", "INTERACTION_RANGE"):
        return result("ENGAGE", "RECURRING_ATTENTION_IN_INTERACTION_RANGE" if recurring
                      else "SUSTAINED_GAZE_IN_INTERACTION_RANGE")
    if (person.human_radial_motion == "AWAY" and e.distance_trend_valid
            and e.stationary_window_confirmed):
        return result("CONTINUE", "PERSON_MOVING_AWAY")
    if person.relative_distance_trend == "INCREASING" and e.distance_trend_valid:
        return result("CONTINUE", "RELATIVE_SEPARATION_INCREASING")
    if attentive:
        if person.distance_zone == "INTERACTION_RANGE":
            return result("ENGAGE", "SUSTAINED_GAZE_IN_INTERACTION_RANGE")
        if person.distance_zone == "APPROACHABLE":
            return result("APPROACH", "RECURRING_ATTENTION_IN_APPROACHABLE_RANGE" if recurring
                          else "SUSTAINED_GAZE_IN_APPROACHABLE_RANGE")
    if person.gaze_state == "NONE":
        return result("CONTINUE", "NO_ATTENTION")
    return result("CONTINUE", "PERSON_FAR" if person.distance_zone == "FAR" else "NO_INTERACTION_CUE")
