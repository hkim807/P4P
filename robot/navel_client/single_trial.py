"""Opt-in single-trial acceptance; no motion or model transport."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Mapping
from types import MappingProxyType

from robot.navel_client.decision_dispatch import DECISIONS, DecisionRejected, parse_decision

logger = logging.getLogger(__name__)


class SingleTrial:
    def __init__(self, policy="rules", *, wait_timeout_s=30.0, max_age_s=1.0,
                 monotonic=time.monotonic,
                 monotonic_us=lambda: time.monotonic_ns() // 1000):
        if policy not in {"rules", "llm", "vlm"}:
            raise ValueError("trial policy must be rules, llm or vlm")
        if any(not math.isfinite(value) or value <= 0 for value in (wait_timeout_s, max_age_s)):
            raise ValueError("trial timeouts must be positive and finite")
        self.policy = policy
        self.phase = "OBSERVING"
        self.failure_reason = None
        self.ready = False
        self.session_id = None
        self._decision = self._source = self._latest_source = None
        self.monotonic, self.monotonic_us = monotonic, monotonic_us
        self.deadline = monotonic() + wait_timeout_s
        self.max_age_us = round(max_age_s * 1_000_000)

    @property
    def decision(self):
        return self._decision

    @property
    def source(self):
        return self._source

    @property
    def terminal(self):
        return self.phase in {"COMPLETED", "FAILED"}

    def tick(self):
        if self.phase == "OBSERVING" and self.monotonic() >= self.deadline:
            self.fail("NO_DECISION_TIMEOUT")

    def observe(self, payload, observation):
        """Use current correlated SocialState evidence, with no identity lock."""
        self.tick()
        self.ready = False
        if self.terminal or payload.get("accepted") is not True or payload.get("processing_status") != "complete":
            return
        state = payload.get("social_state")
        timestamp = observation.get("timestamp")
        if (not isinstance(state, Mapping) or type(timestamp) is not int or timestamp < 0
                or type(payload.get("timestamp")) is not int or payload["timestamp"] != timestamp
                or type(state.get("robot_timestamp_us")) is not int or state["robot_timestamp_us"] != timestamp
                or not isinstance(state.get("state_id"), str) or not state["state_id"]
                or not isinstance(state.get("session_id"), str) or not state["session_id"]):
            return
        if self.session_id is not None and state["session_id"] != self.session_id:
            self.fail("SESSION_INVALIDATED")
            return
        self.session_id = state["session_id"]
        source = {"session_id": self.session_id, "source_state_id": state["state_id"],
                  "source_robot_timestamp_us": timestamp}
        if self._latest_source is not None:
            previous = self._latest_source["source_robot_timestamp_us"]
            if timestamp < previous or (timestamp == previous and source != self._latest_source):
                return
        self._latest_source = source
        people = state.get("people", [])
        local_people = observation.get("people", [])
        if not isinstance(people, list) or not isinstance(local_people, list):
            return
        observed = [p for p in people if isinstance(p, Mapping) and p.get("visibility") == "OBSERVED"]
        if len(observed) != 1 or len(local_people) != 1:
            return
        evidence = observed[0].get("evidence", {})
        if not isinstance(evidence, Mapping):
            return
        gaze_ready = evidence.get("gaze_valid") is True
        if self.policy == "rules":
            gaze_ready = gaze_ready and observed[0].get("gaze_state") in ("NONE", "INTERMITTENT", "SUSTAINED")
        cue_ready = gaze_ready if self.policy == "rules" else gaze_ready or evidence.get("distance_trend_valid") is True
        self.ready = (self.phase == "OBSERVING" and 0 <= self.monotonic_us() - timestamp <= self.max_age_us
                      and (self.policy == "vlm" or (
                          evidence.get("latest_distance_valid") is True
                          and cue_ready)))

    def accept_decision(self, policy, decision, source):
        """Later model delivery must supply the exact observed source metadata.

        Validate the shared two-field FinalDecision wire contract locally without
        importing the computer's Pydantic dependency into the robot client.
        """
        self.tick()
        if (self.phase != "OBSERVING" or policy != self.policy or not self.ready
                or not isinstance(source, Mapping) or self._latest_source is None
                or type(source.get("source_robot_timestamp_us")) is not int
                or any(source.get(k) != v for k, v in self._latest_source.items())
                or not 0 <= self.monotonic_us() - self._latest_source["source_robot_timestamp_us"] <= self.max_age_us):
            return False
        if (not isinstance(decision, Mapping) or set(decision) != {"action", "reason"}
                or not isinstance(decision["action"], str) or decision["action"] not in DECISIONS
                or not isinstance(decision["reason"], str) or not decision["reason"].strip()):
            return False
        metadata = {**self._latest_source, "policy": policy}
        for key in ("decision_id", "policy_version", "prompt_version", "reason_code", "target_uid", "target_track_epoch"):
            value = source.get(key)
            if type(value) in (str, int) or value is None:
                if key in source:
                    metadata[key] = value
        self._decision = MappingProxyType(dict(decision))
        self._source = MappingProxyType(metadata)
        self.phase = "DECIDED"
        logger.info("single_trial phase=DECIDED policy=%s decision=%s source=%s", policy, dict(self.decision), metadata)
        return True

    def accept_rule_response(self, payload, observation):
        self.observe(payload, observation)
        if self.policy != "rules" or self.phase != "OBSERVING" or not self.ready:
            return False
        try:
            # Acceptance of a pure proposal does not authorise a robot command.
            parsed = parse_decision({**payload, "target_lock": None}, observation,
                                    self.monotonic_us(), self.max_age_us)
        except DecisionRejected:
            return False
        final = payload.get("final_decision")
        if parsed is None or not isinstance(final, Mapping) or final.get("action") != parsed.decision:
            return False
        return self.accept_decision("rules", final, {
            **payload["policy_decision"], "source_robot_timestamp_us": observation["timestamp"],
        })

    def start_execution(self):
        if self.phase != "DECIDED":
            return False
        self.phase = "EXECUTING"
        return True

    def complete_execution(self):
        if self.phase != "EXECUTING":
            return False
        self.phase = "COMPLETED"
        return True

    def fail(self, reason):
        if self.terminal:
            return False
        self.phase, self.failure_reason = "FAILED", reason
        logger.info("single_trial phase=FAILED reason=%s", reason)
        return True

    async def watchdog(self, *, stop_on_decision=False):
        while not self.terminal and not (stop_on_decision and self.phase == "DECIDED"):
            self.tick()
            await asyncio.sleep(0.1)
