"""Opt-in single-trial acceptance; no motion or model transport."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Mapping
from types import MappingProxyType
from uuid import uuid4

from robot.navel_client.decision_dispatch import DECISIONS, DecisionRejected, parse_decision

logger = logging.getLogger(__name__)


class SingleTrial:
    def __init__(self, policy="rules", *, wait_timeout_s=30.0, max_age_s=1.0, model_max_age_s=10.0,
                 monotonic=time.monotonic,
                 monotonic_us=lambda: time.monotonic_ns() // 1000):
        if policy not in {"rules", "llm", "vlm"}:
            raise ValueError("trial policy must be rules, llm or vlm")
        if any(not math.isfinite(value) or value <= 0 for value in (wait_timeout_s, max_age_s, model_max_age_s)):
            raise ValueError("trial timeouts must be positive and finite")
        self.trial_id = uuid4().hex
        self.pending_request = None
        self.retry_request_id = None
        self._local_observation = None
        self._latest_trend_valid = False
        self._readiness_key = None
        self.policy = policy
        self.phase = "OBSERVING"
        self.failure_reason = None
        self.approach_result = None
        self.ready = False
        self.session_id = None
        self._decision = self._source = self._latest_source = None
        self.monotonic, self.monotonic_us = monotonic, monotonic_us
        self.deadline = monotonic() + wait_timeout_s
        self.max_age_us = round(max_age_s * 1_000_000)
        self.model_max_age_us = round(model_max_age_s * 1_000_000)

    @property
    def decision(self):
        return self._decision

    @property
    def source(self):
        return self._source

    @property
    def current_observation(self):
        """Latest robot-local frame, independent of the frozen decision source."""
        return self._local_observation

    @property
    def terminal(self):
        return self.phase in {"COMPLETED", "FAILED"}

    def tick(self):
        if self.phase == "OBSERVING" and self.monotonic() >= self.deadline:
            self.fail("NO_DECISION_TIMEOUT")

    def note_observation(self, observation):
        # Called by the collector, including while either HTTP task is waiting.
        self._local_observation = observation

    def model_identity(self):
        return {"trial_id": self.trial_id, "session_id": self.session_id, "policy": self.policy}

    def register_request(self, result):
        if self.phase != "OBSERVING" or not isinstance(result, Mapping):
            return False
        if (any(result.get(key) != value for key, value in self.model_identity().items())
                or self._latest_source is None
                or any(result.get(key) != value for key, value in self._latest_source.items())
                or not isinstance(result.get("request_id"), str) or not result["request_id"]
                or result["request_id"] == self.retry_request_id):
            return False
        if self.pending_request is not None:
            return result["request_id"] == self.pending_request["request_id"]
        self.pending_request = {key: result[key] for key in (
            "trial_id", "session_id", "policy", "request_id", "source_state_id", "source_robot_timestamp_us")}
        return True

    def expire_request(self):
        if self.pending_request is not None:
            self.retry_request_id = self.pending_request["request_id"]
            self.pending_request = None

    def accept_model_result(self, result):
        self.tick()
        if (self.phase != "OBSERVING" or self.pending_request is None or not isinstance(result, Mapping)
                or any(result.get(key) != value for key, value in self.pending_request.items())):
            return False
        if (result.get("status") == "failed" or not 0 <= self.monotonic_us() -
                self.pending_request["source_robot_timestamp_us"] <= self.model_max_age_us):
            logger.info("model_trial request=%s error=%s", result.get("request_id"), result.get("error"))
            self.expire_request()
            return False
        if result.get("status") != "succeeded":
            return False
        if self.policy == "vlm" and (not isinstance(result.get("image_matching"), Mapping)
                or result["image_matching"].get("ok") is not True
                or not result.get("verified_image_sha256")):
            return False
        return self.accept_decision(self.policy, result.get("decision"), result)

    def observe(self, payload, observation):
        """Use current correlated SocialState evidence, with no identity lock."""
        self.tick()
        self.ready = False
        if self.phase != "OBSERVING" or payload.get("accepted") is not True or payload.get("processing_status") != "complete":
            return
        state = payload.get("social_state")
        timestamp = observation.get("timestamp")
        if (not isinstance(state, Mapping) or type(timestamp) is not int or timestamp < 0
                or type(payload.get("timestamp")) is not int or payload["timestamp"] != timestamp
                or type(state.get("robot_timestamp_us")) is not int or state["robot_timestamp_us"] != timestamp
                or not isinstance(state.get("state_id"), str) or not state["state_id"]
                or not isinstance(state.get("session_id"), str) or not state["session_id"]):
            return
        if self._local_observation is None or timestamp >= self._local_observation["timestamp"]:
            self.note_observation(observation)
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
        readiness = payload.get("policy_readiness")
        if self.policy == "rules" and isinstance(readiness, Mapping):
            key = (readiness.get("status"), readiness.get("reason_code"))
            if key != self._readiness_key:
                self._readiness_key = key
                person = observed[0] if len(observed) == 1 else {}
                evidence = person.get("evidence", {})
                coverage = evidence.get("gaze_valid_coverage_s") if isinstance(evidence, Mapping) else None
                logger.info("single_trial policy_readiness=%s reason=%s gaze_state=%s gaze_valid_coverage_s=%s",
                            *key, person.get("gaze_state"), coverage)
        if len(observed) != 1 or len(local_people) != 1:
            return
        evidence = observed[0].get("evidence", {})
        if not isinstance(evidence, Mapping):
            return
        self._latest_trend_valid = evidence.get("distance_trend_valid") is True
        gaze_ready = evidence.get("gaze_valid") is True
        if self.policy == "rules":
            gaze_ready = gaze_ready and observed[0].get("gaze_state") in ("NONE", "INTERMITTENT", "SUSTAINED")
        if self.policy == "vlm":
            delivery = payload.get("model_trial")
            evidence_ready = isinstance(delivery, Mapping) and delivery.get("image_ready") is True
        else:
            cue_ready = gaze_ready if self.policy == "rules" else gaze_ready or self._latest_trend_valid
            evidence_ready = evidence.get("latest_distance_valid") is True and cue_ready
            # The server derives one shared rule/structured-LLM eligibility from
            # the correlated state. This also permits conflict/proximity/pass
            # observations before gaze history is ready.
            if isinstance(readiness, Mapping):
                evidence_ready = readiness.get("status") == "READY" and readiness.get("reason_code") is None
        self.ready = (self.phase == "OBSERVING" and evidence_ready
                      and 0 <= self.monotonic_us() - timestamp <= self.max_age_us)

    def accept_decision(self, policy, decision, source):
        """Freeze a rule source or the registered asynchronous model source.

        Validate the shared two-field FinalDecision wire contract locally without
        importing the computer's Pydantic dependency into the robot client.
        """
        self.tick()
        if (self.phase != "OBSERVING" or policy != self.policy or not self.ready
                or not isinstance(source, Mapping) or self._latest_source is None
                or type(source.get("source_robot_timestamp_us")) is not int
                or not 0 <= self.monotonic_us() - self._latest_source["source_robot_timestamp_us"] <= self.max_age_us):
            return False
        expected = self._latest_source if policy == "rules" else self.pending_request
        age_limit = self.max_age_us if policy == "rules" else self.model_max_age_us
        if (expected is None or any(source.get(key) != value for key, value in expected.items())
                or not 0 <= self.monotonic_us() - source["source_robot_timestamp_us"] <= age_limit):
            return False
        local = self._local_observation
        if policy != "rules" and (local is None or len(local.get("people", [])) != 1
                or not 0 <= self.monotonic_us() - local["timestamp"] <= self.max_age_us):
            return False
        if policy == "llm":
            person = local["people"][0]
            distance = person.get("distance_m")
            gaze = person.get("gaze_overlap")
            if (type(distance) not in (int, float) or not math.isfinite(distance) or distance < 0
                    or (gaze is None and not self._latest_trend_valid)):
                return False
        if (not isinstance(decision, Mapping) or set(decision) != {"action", "reason"}
                or not isinstance(decision["action"], str) or decision["action"] not in DECISIONS
                or not isinstance(decision["reason"], str) or not decision["reason"].strip()):
            return False
        metadata = {**expected, "policy": policy}
        for key in ("decision_id", "policy_version", "prompt_version", "reason_code", "target_uid", "target_track_epoch"):
            value = source.get(key)
            if type(value) in (str, int) or value is None:
                if key in source:
                    metadata[key] = value
        self._decision = MappingProxyType(dict(decision))
        self._source = MappingProxyType(metadata)
        self.phase = "DECIDED"
        self.pending_request = None
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
        except DecisionRejected as error:
            logger.warning("single_trial decision_rejected=%s", error)
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
