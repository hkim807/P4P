"""Interaction lock with guarded short-gap UID reassociation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.policy.rules import PolicyDecision
from app.state.social_models import SocialState, StrictModel


LOCK_VERSION = "target-lock-v2"


class LockConfig(StrictModel):
    model_config = StrictModel.model_config | {"frozen": True}
    missing_hold_s: float = Field(default=2.0, gt=0)
    release_cooldown_s: float = Field(default=1.0, ge=0)
    rebind_window_s: float = Field(default=0.8, gt=0)
    rebind_min_frames: int = Field(default=3, ge=2)
    rebind_min_span_s: float = Field(default=0.15, gt=0)
    rebind_max_frame_gap_s: float = Field(default=0.35, gt=0)
    rebind_distance_slack_m: float = Field(default=0.25, ge=0)
    rebind_max_relative_speed_mps: float = Field(default=1.5, gt=0)
    max_rebinds_per_lock: int = Field(default=3, ge=0)

    @model_validator(mode="after")
    def feasible_rebind_window(self):
        if self.rebind_window_s > self.missing_hold_s:
            raise ValueError("rebind_window_s must fit within missing_hold_s")
        if self.rebind_min_span_s > self.rebind_window_s:
            raise ValueError("rebind_min_span_s must fit within rebind_window_s")
        return self

    @classmethod
    def from_file(cls, path: str | Path) -> "LockConfig":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    @property
    def version(self) -> str:
        digest = hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()[:12]
        return f"lock-config-v2-{digest}"


class EffectiveDecision(StrictModel):
    decision_id: str
    source_state_id: str
    session_id: str
    policy_version: Literal["target-lock-v2"] = LOCK_VERSION
    decision: Literal["CONTINUE", "APPROACH", "ENGAGE", "YIELD", "DEFER"]
    reason_code: str
    target_uid: int | None = Field(default=None, ge=0)
    target_track_epoch: int | None = Field(default=None, ge=1)
    lock_id: str | None = None


class TargetLockState(StrictModel):
    schema_version: int = 1
    lock_version: Literal["target-lock-v2"] = LOCK_VERSION
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
    candidate_frames: int
    bound_tracks: list[dict[str, int]]
    expires_at_us: int | None
    events: list[str]
    effective_decision: EffectiveDecision


class TargetLockController:
    """Keep one logical lock while requiring exclusive evidence for UID handoff."""

    def __init__(self, config: LockConfig | None = None) -> None:
        self.config = config or LockConfig()
        self.session_id: str | None = None
        self.last_timestamp_us: int | None = None
        self.counter = 0
        self.lock_id: str | None = None
        self.key: tuple[int, int] | None = None
        self.last_seen_us: int | None = None
        self.last_distance_m: float | None = None
        self.cooldown_until_us: int | None = None
        self.status = "UNLOCKED"
        self.candidate_key: tuple[int, int] | None = None
        self.candidate_first_us: int | None = None
        self.candidate_last_us: int | None = None
        self.candidate_last_distance_m: float | None = None
        self.candidate_frames = 0
        self.gap_contaminated = False
        self.bound_tracks: list[dict[str, int]] = []
        self.pending_events: list[str] = []

    def finish_execution(self, lock_id: str, action: str, status: str,
                         robot_timestamp_us: int) -> None:
        """Release only the current logical lock after a terminal physical outcome."""
        if self.lock_id != lock_id or self.key is None:
            return
        if status == "COMPLETED" and action != "ENGAGE":
            return
        if status not in {"COMPLETED", "FAILED", "CANCELLED", "REJECTED"}:
            return
        reason = ("RELEASED_ENGAGEMENT_COMPLETED" if status == "COMPLETED"
                  else f"RELEASED_EXECUTION_{status}")
        self._release(max(robot_timestamp_us, self.last_timestamp_us or 0),
                      self.pending_events, reason)
        self.status = "COOLDOWN"

    def _clear_candidate(self) -> None:
        self.candidate_key = None
        self.candidate_first_us = None
        self.candidate_last_us = None
        self.candidate_last_distance_m = None
        self.candidate_frames = 0

    def _plausible_distance(self, distance: float | None, previous: float | None,
                            delta_us: int) -> bool:
        if distance is None or previous is None:
            return False
        allowance = (self.config.rebind_distance_slack_m
                     + self.config.rebind_max_relative_speed_mps * delta_us / 1_000_000)
        return abs(distance - previous) <= allowance

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
        observed = {(p.uid, p.track_epoch): p for p in state.people if p.visibility == "OBSERVED"}
        visible = set(observed)
        events: list[str] = self.pending_events
        self.pending_events = []
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
                self.last_distance_m = observed[self.key].latest_distance_m
                self._clear_candidate()
                self.gap_contaminated = len(visible) > 1
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
                    person = observed[candidate]
                    gap_us = now - self.last_seen_us
                    newly_acquired = any(
                        event.get("type") == "ACQUIRED"
                        and (event.get("uid"), event.get("track_epoch")) == candidate
                        for event in state.track_events
                    )
                    if self.key[0] == 0 or candidate[0] == 0:
                        reason = "UID_ZERO_UNVERIFIED"
                        self._clear_candidate()
                    elif self.gap_contaminated:
                        reason = "RETURN_SCENE_AMBIGUOUS"
                        self._clear_candidate()
                    elif gap_us > round(self.config.rebind_window_s * 1_000_000):
                        reason = "RETURN_WINDOW_EXPIRED"
                        self._clear_candidate()
                    elif len(self.bound_tracks) - 1 >= self.config.max_rebinds_per_lock:
                        reason = "REBOUND_LIMIT_REACHED"
                        self._clear_candidate()
                    elif self.candidate_key is not None and self.candidate_key != candidate:
                        reason = "RETURN_SCENE_AMBIGUOUS"
                        self.gap_contaminated = True
                        self._clear_candidate()
                    elif self.candidate_key != candidate:
                        self._clear_candidate()
                        if (newly_acquired
                                and self._plausible_distance(person.latest_distance_m,
                                                             self.last_distance_m, gap_us)):
                            self.candidate_key = candidate
                            self.candidate_first_us = now
                            self.candidate_last_us = now
                            self.candidate_last_distance_m = person.latest_distance_m
                            self.candidate_frames = 1
                        else:
                            reason = "RETURN_EVIDENCE_INSUFFICIENT"
                    elif (now - self.candidate_last_us > round(self.config.rebind_max_frame_gap_s * 1_000_000)
                          or not self._plausible_distance(person.latest_distance_m,
                                                           self.candidate_last_distance_m,
                                                           now - self.candidate_last_us)
                          or not self._plausible_distance(person.latest_distance_m,
                                                           self.last_distance_m, gap_us)):
                        reason = "RETURN_EVIDENCE_INSUFFICIENT"
                        self._clear_candidate()
                    else:
                        self.candidate_last_us = now
                        self.candidate_last_distance_m = person.latest_distance_m
                        self.candidate_frames += 1
                        if (self.candidate_frames >= self.config.rebind_min_frames
                                and now - self.candidate_first_us >= round(self.config.rebind_min_span_s * 1_000_000)):
                            self.key = candidate
                            self.last_seen_us = now
                            self.last_distance_m = person.latest_distance_m
                            self.bound_tracks.append({"uid": candidate[0], "track_epoch": candidate[1]})
                            self._clear_candidate()
                            status, decision, reason = "LOCKED", "DEFER", "UID_REBOUND_OBSERVE"
                            events.append("REBOUND")
                            candidate = None
                elif len(visible) > 1:
                    status, reason = "AMBIGUOUS", "MULTIPLE_RETURN_CANDIDATES"
                    self.gap_contaminated = True
                    self._clear_candidate()
                else:
                    status, reason = "MISSING", "LOCKED_TARGET_MISSING"
                    self._clear_candidate()
                decision = "DEFER"
                if status != "LOCKED" and status != self.status:
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
                    self.bound_tracks = []
                if len(visible) == 1 and not (proposal.decision == "CONTINUE" and proposal.reason_code in ("NO_ATTENTION", "PERSON_MOVING_AWAY")):
                    self.counter += 1
                    self.lock_id = f"{state.session_id}:lock:{self.counter}"
                    self.key = next(iter(visible))
                    self.last_seen_us = now
                    self.last_distance_m = observed[self.key].latest_distance_m
                    self.bound_tracks = [{"uid": self.key[0], "track_epoch": self.key[1]}]
                    self.gap_contaminated = False
                    self._clear_candidate()
                    events.append("ACQUIRED")
                    status = "LOCKED"
                    if (proposal.decision in ("APPROACH", "ENGAGE")
                            and (proposal.target_uid, proposal.target_track_epoch) == self.key):
                        decision_target = self.key
                    else:
                        decision, reason = "DEFER", proposal.reason_code
                elif status != "COOLDOWN":
                    status = "UNLOCKED"

        if (status == "LOCKED" and self.status in ("MISSING", "TENTATIVE_RETURN", "AMBIGUOUS")
                and "REBOUND" not in events):
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
            candidate_frames=self.candidate_frames,
            bound_tracks=list(self.bound_tracks),
            expires_at_us=expiry, events=events, effective_decision=effective,
        )

    def _release(self, now: int, events: list[str], reason: str) -> None:
        events.append(reason)
        self.key = None
        self.last_seen_us = None
        self.last_distance_m = None
        self._clear_candidate()
        self.gap_contaminated = False
        self.cooldown_until_us = now + round(self.config.release_cooldown_s * 1_000_000)
