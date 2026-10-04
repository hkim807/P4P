"""Conservative interaction lock over UID tracks and pure rule proposals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.policy.rules import PolicyDecision
from app.state.social_models import SocialState, StrictModel


LOCK_VERSION = "target-lock-v1"


class LockConfig(StrictModel):
    model_config = StrictModel.model_config | {"frozen": True}
    missing_hold_s: float = Field(default=2.0, gt=0)
    release_cooldown_s: float = Field(default=1.0, ge=0)

    @classmethod
    def from_file(cls, path: str | Path) -> "LockConfig":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    @property
    def version(self) -> str:
        digest = hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()[:12]
        return f"lock-config-v1-{digest}"


class EffectiveDecision(StrictModel):
    decision_id: str
    source_state_id: str
    session_id: str
    policy_version: Literal["target-lock-v1"] = LOCK_VERSION
    decision: Literal["CONTINUE", "APPROACH", "ENGAGE", "YIELD", "DEFER"]
    reason_code: str
    target_uid: int | None = Field(default=None, ge=0)
    target_track_epoch: int | None = Field(default=None, ge=1)
    lock_id: str | None = None


class TargetLockState(StrictModel):
    schema_version: int = 1
    lock_version: Literal["target-lock-v1"] = LOCK_VERSION
    config_version: str
    source_state_id: str
    session_id: str
    robot_timestamp_us: int
    status: Literal["UNLOCKED", "LOCKED", "MISSING", "TENTATIVE_RETURN", "AMBIGUOUS", "COOLDOWN"]
    lock_id: str | None
    target_uid: int | None
    target_track_epoch: int | None
    candidate_uid: int | None
    candidate_track_epoch: int | None
    expires_at_us: int | None
    events: list[str]
    effective_decision: EffectiveDecision


class TargetLockController:
    """Keep one exact track; never infer that another UID is the same human."""

    def __init__(self, config: LockConfig | None = None) -> None:
        self.config = config or LockConfig()
        self.session_id: str | None = None
        self.last_timestamp_us: int | None = None
        self.counter = 0
        self.lock_id: str | None = None
        self.key: tuple[int, int] | None = None
        self.last_seen_us: int | None = None
        self.cooldown_until_us: int | None = None
        self.status = "UNLOCKED"

    def update(self, state: SocialState | dict, proposal: PolicyDecision | dict) -> TargetLockState:
        state = SocialState.model_validate(state)
        proposal = PolicyDecision.model_validate(proposal)
        if proposal.source_state_id != state.state_id or proposal.session_id != state.session_id:
            raise ValueError("policy proposal does not match social state")
        if state.session_id != self.session_id:
            self.__init__(self.config)
            self.session_id = state.session_id
        now = state.robot_timestamp_us
        previous_timestamp = self.last_timestamp_us
        if previous_timestamp is not None and now <= previous_timestamp:
            raise ValueError("target lock states must increase in source time")
        self.last_timestamp_us = now
        visible = {(p.uid, p.track_epoch) for p in state.people if p.visibility == "OBSERVED"}
        events: list[str] = []
        candidate = None
        status = "UNLOCKED"
        reason = proposal.reason_code
        decision = proposal.decision
        decision_target = None
        released_this_frame = False

        if (self.key is not None and previous_timestamp is not None
                and now - previous_timestamp > round(self.config.missing_hold_s * 1_000_000)):
            self._release(now, events, "RELEASED_STREAM_GAP")
            released_this_frame = True
            status, decision, reason = "COOLDOWN", "DEFER", "LOCK_COOLDOWN"

        if self.key is not None:
            if self.key in visible:
                self.last_seen_us = now
                status = "LOCKED"
                if proposal.decision == "CONTINUE" and proposal.reason_code in ("NO_ATTENTION", "PERSON_MOVING_AWAY"):
                    self._release(now, events, "RELEASED_BY_POLICY")
                    released_this_frame = True
                    status, decision, reason = "COOLDOWN", "DEFER", "LOCK_COOLDOWN"
                elif (proposal.decision in ("APPROACH", "ENGAGE")
                      and (proposal.target_uid, proposal.target_track_epoch) == self.key):
                    decision_target = self.key
                else:
                    decision, reason = "DEFER", proposal.reason_code
            elif now - self.last_seen_us < round(self.config.missing_hold_s * 1_000_000):
                if len(visible) == 1:
                    candidate = next(iter(visible))
                    status, reason = "TENTATIVE_RETURN", "IDENTITY_UNRESOLVED"
                elif len(visible) > 1:
                    status, reason = "AMBIGUOUS", "MULTIPLE_RETURN_CANDIDATES"
                else:
                    status, reason = "MISSING", "LOCKED_TARGET_MISSING"
                decision = "DEFER"
                if status != self.status:
                    events.append(status)
            else:
                self._release(now, events, "RELEASED_MISSING_TIMEOUT")
                released_this_frame = True
                status, decision, reason = "COOLDOWN", "DEFER", "LOCK_COOLDOWN"

        if self.key is None:
            if released_this_frame or (self.cooldown_until_us is not None and now < self.cooldown_until_us):
                status, decision, reason = "COOLDOWN", "DEFER", "LOCK_COOLDOWN"
            else:
                if self.cooldown_until_us is not None:
                    events.append("COOLDOWN_EXPIRED")
                    self.cooldown_until_us = None
                    self.lock_id = None
                if len(visible) == 1 and not (proposal.decision == "CONTINUE" and proposal.reason_code in ("NO_ATTENTION", "PERSON_MOVING_AWAY")):
                    self.counter += 1
                    self.lock_id = f"{state.session_id}:lock:{self.counter}"
                    self.key = next(iter(visible))
                    self.last_seen_us = now
                    events.append("ACQUIRED")
                    status = "LOCKED"
                    if (proposal.decision in ("APPROACH", "ENGAGE")
                            and (proposal.target_uid, proposal.target_track_epoch) == self.key):
                        decision_target = self.key
                    else:
                        decision, reason = "DEFER", proposal.reason_code
                elif status != "COOLDOWN":
                    status = "UNLOCKED"

        if status == "LOCKED" and self.status in ("MISSING", "TENTATIVE_RETURN", "AMBIGUOUS"):
            events.append("REACQUIRED")
        expiry = (self.last_seen_us + round(self.config.missing_hold_s * 1_000_000)
                  if self.key is not None and status != "LOCKED" else
                  self.cooldown_until_us if status == "COOLDOWN" else None)
        effective = EffectiveDecision(
            decision_id=f"{state.state_id}:{LOCK_VERSION}",
            source_state_id=state.state_id, session_id=state.session_id,
            decision=decision, reason_code=reason,
            target_uid=decision_target[0] if decision_target else None,
            target_track_epoch=decision_target[1] if decision_target else None,
            lock_id=self.lock_id,
        )
        self.status = status
        return TargetLockState(
            config_version=self.config.version, source_state_id=state.state_id,
            session_id=state.session_id, robot_timestamp_us=now,
            status=status, lock_id=self.lock_id,
            target_uid=self.key[0] if self.key else None,
            target_track_epoch=self.key[1] if self.key else None,
            candidate_uid=candidate[0] if candidate else None,
            candidate_track_epoch=candidate[1] if candidate else None,
            expires_at_us=expiry, events=events, effective_decision=effective,
        )

    def _release(self, now: int, events: list[str], reason: str) -> None:
        events.append(reason)
        self.key = None
        self.last_seen_us = None
        self.cooldown_until_us = now + round(self.config.release_cooldown_s * 1_000_000)
