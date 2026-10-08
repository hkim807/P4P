"""Deterministic, stateless decisions from one SocialState."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.state.social_models import SocialState, StrictModel, readiness
from app.domain.actions import Action


POLICY_VERSION = "social-rules-v2"
DecisionName = Action | Literal["DEFER"]


class PolicyDecision(StrictModel):
    decision_id: str
    source_state_id: str
    session_id: str
    policy_version: Literal["social-rules-v2"] = POLICY_VERSION
    decision: DecisionName
    reason_code: str
    target_uid: int | None = Field(default=None, ge=0)
    target_track_epoch: int | None = Field(default=None, ge=1)


    @property
    def action(self) -> Action | None:
        """Canonical experimental outcome; DEFER is legacy controller transport."""
        return None if self.decision == "DEFER" else self.decision

    @property
    def status(self) -> str:
        return "NOT_READY" if self.action is None else "DECIDED"


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
    status, reason = readiness(state)
    if status != "READY":
        return result("DEFER", reason)
    if any(p.path_relation == "CONFLICT" for p in visible):
        return result("YIELD", "HUMAN_PATH_CONFLICT")
    if not visible:
        return result("CONTINUE", "NO_VISIBLE_PERSON")
    person = visible[0]
    if person.pass_gesture == "PASS":
        return result("CONTINUE", "PASS_GESTURE")
    if person.gaze_state in ("NONE", "INTERMITTENT"):
        return result("CONTINUE", "NO_ATTENTION" if person.gaze_state == "NONE" else "INCIDENTAL_ATTENTION")
    if (person.human_radial_motion == "AWAY" and person.evidence.distance_trend_valid
            and person.evidence.stationary_window_confirmed):
        return result("CONTINUE", "PERSON_MOVING_AWAY")
    if person.distance_zone == "FAR":
        return result("CONTINUE", "PERSON_FAR")
    # Relative separation is not human intent. Avoid pursuing an increasingly
    # distant target, even when ego-motion prevents attributing that trend.
    if person.relative_distance_trend == "INCREASING" and person.evidence.distance_trend_valid:
        return result("CONTINUE", "RELATIVE_SEPARATION_INCREASING")
    if person.distance_zone == "INTERACTION_RANGE":
        return result("ENGAGE", "SUSTAINED_GAZE_IN_INTERACTION_RANGE", person)
    return result("APPROACH", "SUSTAINED_GAZE_IN_APPROACHABLE_RANGE", person)
