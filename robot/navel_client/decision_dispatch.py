"""Robot-side dry-run routing for PC policy decisions; standard library only."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable


logger = logging.getLogger(__name__)
POLICY_VERSION = "social-rules-v2"
DECISIONS = {"CONTINUE", "APPROACH", "ENGAGE", "YIELD", "DEFER"}


class DecisionRejected(ValueError):
    """A response cannot be trusted as a current decision for the sent frame."""


@dataclass(frozen=True)
class RobotDecision:
    decision_id: str
    source_state_id: str
    session_id: str
    decision: str
    reason_code: str
    target_uid: int | None
    target_track_epoch: int | None
    lock_id: str | None = None

    @property
    def action_key(self) -> tuple[str, int | None, int | None, str | None]:
        return (self.decision, self.target_uid, self.target_track_epoch, self.lock_id)


def _integer(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def parse_decision(payload: Mapping[str, Any], observation: Mapping[str, Any],
                   now_us: int, max_age_us: int) -> RobotDecision:
    """Check the response against the exact observation and current robot clock."""
    if payload.get("accepted") is not True or payload.get("processing_status") != "complete":
        raise DecisionRejected("observation_not_fully_processed")
    timestamp = observation.get("timestamp")
    if not _integer(timestamp) or not _integer(payload.get("timestamp")) or payload["timestamp"] != timestamp:
        raise DecisionRejected("response_timestamp_mismatch")
    age_us = now_us - timestamp
    if age_us < 0 or age_us > max_age_us:
        raise DecisionRejected("source_frame_not_fresh")

    social, raw = payload.get("social_state"), payload.get("policy_decision")
    if not isinstance(social, Mapping) or not isinstance(raw, Mapping):
        raise DecisionRejected("social_state_or_decision_missing")
    state_id, session_id = social.get("state_id"), social.get("session_id")
    if (not isinstance(state_id, str) or not state_id
            or not isinstance(session_id, str) or not session_id
            or not _integer(social.get("robot_timestamp_us"))
            or social["robot_timestamp_us"] != timestamp
            or raw.get("source_state_id") != state_id
            or raw.get("session_id") != session_id):
        raise DecisionRejected("decision_state_mismatch")
    decision_id = raw.get("decision_id")
    if (raw.get("policy_version") != POLICY_VERSION
            or decision_id != f"{state_id}:{POLICY_VERSION}"):
        raise DecisionRejected("decision_version_or_id_invalid")
    decision, reason = raw.get("decision"), raw.get("reason_code")
    if not isinstance(decision, str) or decision not in DECISIONS or not isinstance(reason, str) or not reason:
        raise DecisionRejected("decision_or_reason_invalid")
    uid, epoch = raw.get("target_uid"), raw.get("target_track_epoch")
    if decision in ("APPROACH", "ENGAGE"):
        if not _integer(uid) or not _integer(epoch, minimum=1):
            raise DecisionRejected("target_missing_or_invalid")
        people, local_people = social.get("people"), observation.get("people")
        observed = ([person for person in people if isinstance(person, Mapping)
                     and person.get("visibility") == "OBSERVED"]
                    if isinstance(people, list) else [])
        if (len(observed) != 1 or observed[0].get("uid") != uid
                or observed[0].get("track_epoch") != epoch
                or not isinstance(local_people, list)
                or not any(isinstance(person, Mapping) and person.get("uid") == uid
                           for person in local_people)):
            raise DecisionRejected("target_not_observed")
    elif uid is not None or epoch is not None:
        raise DecisionRejected("unexpected_target")
    lock = payload.get("target_lock")
    if lock is not None:
        if (not isinstance(lock, Mapping) or lock.get("lock_version") != "target-lock-v2"
                or lock.get("source_state_id") != state_id or lock.get("session_id") != session_id
                or not _integer(lock.get("robot_timestamp_us"))
                or lock.get("robot_timestamp_us") != timestamp):
            raise DecisionRejected("target_lock_state_mismatch")
        effective = lock.get("effective_decision")
        if (not isinstance(effective, Mapping)
                or effective.get("source_state_id") != state_id
                or effective.get("session_id") != session_id
                or effective.get("policy_version") != "target-lock-v2"
                or effective.get("decision_id") != f"{state_id}:target-lock-v2"
                or effective.get("lock_id") != lock.get("lock_id")):
            raise DecisionRejected("target_lock_decision_mismatch")
        status = lock.get("status")
        lock_id = lock.get("lock_id")
        if (not isinstance(status, str)
                or status not in {"UNLOCKED", "LOCKED", "MISSING", "TENTATIVE_RETURN", "AMBIGUOUS", "COOLDOWN"}):
            raise DecisionRejected("target_lock_status_invalid")
        if lock_id is not None and (not isinstance(lock_id, str) or not lock_id):
            raise DecisionRejected("target_lock_id_invalid")
        lock_uid, lock_epoch = lock.get("target_uid"), lock.get("target_track_epoch")
        if status in {"LOCKED", "MISSING", "TENTATIVE_RETURN", "AMBIGUOUS"}:
            if not lock_id or not _integer(lock_uid) or not _integer(lock_epoch, minimum=1):
                raise DecisionRejected("target_lock_target_invalid")
        elif lock_uid is not None or lock_epoch is not None:
            raise DecisionRejected("target_lock_unexpected_target")
        decision, reason = effective.get("decision"), effective.get("reason_code")
        if not isinstance(decision, str) or decision not in DECISIONS or not isinstance(reason, str) or not reason:
            raise DecisionRejected("target_lock_decision_invalid")
        uid, epoch = effective.get("target_uid"), effective.get("target_track_epoch")
        if decision in ("APPROACH", "ENGAGE"):
            if (status != "LOCKED" or not lock_id
                    or not _integer(uid) or not _integer(epoch, minimum=1)
                    or (uid, epoch) != (lock.get("target_uid"), lock.get("target_track_epoch"))
                    or (uid, epoch) != (raw.get("target_uid"), raw.get("target_track_epoch"))
                    or decision != raw.get("decision")):
                raise DecisionRejected("target_lock_does_not_authorize_target")
        elif uid is not None or epoch is not None:
            raise DecisionRejected("target_lock_unexpected_target")
        if status in {"MISSING", "TENTATIVE_RETURN", "AMBIGUOUS", "COOLDOWN"} and decision != "DEFER":
            raise DecisionRejected("target_lock_hold_requires_defer")
        if status == "LOCKED" and decision == "CONTINUE":
            raise DecisionRejected("target_lock_locked_cannot_continue")
        decision_id = effective["decision_id"]
    else:
        lock_id = None
    return RobotDecision(decision_id, state_id, session_id, decision, reason, uid, epoch, lock_id)


class DryRunHandlers:
    """Replace these methods with robot behaviors only after execution gates exist."""

    def __init__(self, robot: Any | None = None) -> None:
        # Kept for future SDK-backed handlers. Dry-run methods never use it.
        self.robot = robot

    async def continue_route(self, decision: RobotDecision) -> None:
        self._log("CONTINUE", decision)

    async def approach_person(self, decision: RobotDecision) -> None:
        self._log("APPROACH", decision)

    async def engage_person(self, decision: RobotDecision) -> None:
        self._log("ENGAGE", decision)

    async def yield_route(self, decision: RobotDecision) -> None:
        self._log("YIELD", decision)

    async def observe(self, decision: RobotDecision) -> None:
        self._log("DEFER", decision)

    async def cancel_active(self, decision: RobotDecision, reason: str) -> None:
        logger.info("decision_dry_run=%s", json.dumps({
            "event": "CANCEL", "decision": decision.decision,
            "decision_id": decision.decision_id, "target_uid": decision.target_uid,
            "target_track_epoch": decision.target_track_epoch, "lock_id": decision.lock_id,
            "reason": reason,
        }, separators=(",", ":")))

    @staticmethod
    def _log(handler: str, decision: RobotDecision) -> None:
        logger.info("decision_dry_run=%s", json.dumps({
            "event": "CALL", "handler": handler, "decision_id": decision.decision_id,
            "source_state_id": decision.source_state_id, "target_uid": decision.target_uid,
            "target_track_epoch": decision.target_track_epoch,
            "lock_id": decision.lock_id,
            "reason_code": decision.reason_code,
        }, separators=(",", ":")))


class DecisionDispatcher:
    """Route only fresh, matched decisions and suppress repeated action calls."""

    def __init__(self, handlers: DryRunHandlers, *, max_age_s: float = 1.0,
                 timeout_s: float = 2.0,
                 monotonic: Callable[[], float] = time.monotonic,
                 monotonic_us: Callable[[], int] = lambda: time.monotonic_ns() // 1000):
        if (not math.isfinite(max_age_s) or max_age_s <= 0
                or not math.isfinite(timeout_s) or timeout_s <= 0):
            raise ValueError("decision age and timeout must be positive and finite")
        self.handlers = handlers
        self.max_age_us = round(max_age_s * 1_000_000)
        self.timeout_s = timeout_s
        self.monotonic = monotonic
        self.monotonic_us = monotonic_us
        self.session_id: str | None = None
        self.last_timestamp_us: int | None = None
        self.last_decision_id: str | None = None
        self.last_valid_at: float | None = None
        self.current: RobotDecision | None = None
        self._lock = asyncio.Lock()

    async def accept(self, payload: Mapping[str, Any], observation: Mapping[str, Any]) -> bool:
        async with self._lock:
            try:
                decision = parse_decision(payload, observation, self.monotonic_us(), self.max_age_us)
                timestamp = observation["timestamp"]
                if self.session_id is not None and decision.session_id != self.session_id:
                    raise DecisionRejected("session_changed_restart_client")
                if self.last_timestamp_us is not None and timestamp < self.last_timestamp_us:
                    raise DecisionRejected("older_response")
                if (self.last_timestamp_us == timestamp
                        and decision.decision_id != self.last_decision_id):
                    raise DecisionRejected("conflicting_response_for_frame")
            except DecisionRejected as error:
                logger.warning("decision_rejected=%s", error)
                await self._clear(str(error))
                return False

            self.session_id = decision.session_id
            self.last_timestamp_us = timestamp
            self.last_valid_at = self.monotonic()
            if decision.decision_id == self.last_decision_id:
                return True
            self.last_decision_id = decision.decision_id
            if self.current is not None and self.current.action_key == decision.action_key:
                return True
            await self._clear("decision_changed")
            method = {
                "CONTINUE": self.handlers.continue_route,
                "APPROACH": self.handlers.approach_person,
                "ENGAGE": self.handlers.engage_person,
                "YIELD": self.handlers.yield_route,
                "DEFER": self.handlers.observe,
            }[decision.decision]
            await method(decision)
            self.current = decision
            return True

    async def invalidate(self, reason: str) -> None:
        async with self._lock:
            await self._clear(reason)

    async def _clear(self, reason: str) -> None:
        if self.current is not None:
            previous = self.current
            self.current = None
            await self.handlers.cancel_active(previous, reason)

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(min(0.1, self.timeout_s / 2))
            async with self._lock:
                if (self.current is not None and self.last_valid_at is not None
                        and self.monotonic() - self.last_valid_at >= self.timeout_s):
                    logger.warning("decision_watchdog=expired")
                    await self._clear("decision_timeout")
