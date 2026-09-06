"""Deterministic event-driven scheduling for high-level model decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from app.domain.models import HumanSocialState, SocialState


MICROSECONDS_PER_SECOND = 1_000_000


class DecisionSchedulerError(ValueError):
    """A social-state stream cannot be scheduled deterministically."""


class DecisionTrigger(str, Enum):
    """Observable reasons for requesting a fresh policy decision."""

    HUMAN_DETECTED = "HUMAN_DETECTED"
    HUMAN_LEFT = "HUMAN_LEFT"
    TRACK_OCCLUDED = "TRACK_OCCLUDED"
    TRACK_REACQUIRED = "TRACK_REACQUIRED"
    HUMAN_STARTED_SPEAKING = "HUMAN_STARTED_SPEAKING"
    HUMAN_STOPPED_SPEAKING = "HUMAN_STOPPED_SPEAKING"
    ATTENTION_CHANGED = "ATTENTION_CHANGED"
    ENGAGEMENT_CHANGED = "ENGAGEMENT_CHANGED"
    MOTION_CHANGED = "MOTION_CHANGED"
    DISTANCE_TREND_CHANGED = "DISTANCE_TREND_CHANGED"
    PROXEMIC_ZONE_CHANGED = "PROXEMIC_ZONE_CHANGED"
    ROBOT_CONTEXT_CHANGED = "ROBOT_CONTEXT_CHANGED"
    PERIODIC_REFRESH = "PERIODIC_REFRESH"


@dataclass(frozen=True)
class SchedulerConfig:
    """Timing controls for event coalescing and active-scene refreshes."""

    minimum_decision_interval_s: float = 0.5
    active_refresh_interval_s: float = 2.0
    enable_active_refresh: bool = True
    invoke_when_human_leaves: bool = True

    def __post_init__(self) -> None:
        for name in ("minimum_decision_interval_s", "active_refresh_interval_s"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.active_refresh_interval_s < self.minimum_decision_interval_s:
            raise ValueError(
                "active_refresh_interval_s must not be shorter than "
                "minimum_decision_interval_s"
            )


@dataclass(frozen=True)
class DecisionRequest:
    """A state selected for policy invocation and its deterministic triggers."""

    state: SocialState
    triggers: tuple[DecisionTrigger, ...]

    @property
    def trigger_codes(self) -> tuple[str, ...]:
        return tuple(trigger.value for trigger in self.triggers)


@dataclass(frozen=True)
class _HumanSnapshot:
    observed: bool
    predicted_only: bool
    motion_relation: str
    distance_trend: str
    attention_state: str
    engagement_state: str
    proxemic_zone: str

    @classmethod
    def from_state(cls, human: HumanSocialState) -> "_HumanSnapshot":
        return cls(
            observed=human.observed,
            predicted_only=human.predicted_only,
            motion_relation=human.motion_relation,
            distance_trend=human.distance_trend,
            attention_state=human.attention.state,
            engagement_state=human.engagement.state,
            proxemic_zone=human.proxemic_zone,
        )


class DecisionScheduler:
    """Select social states for an LLM/VLM without invoking it at sensor rate.

    A ``None`` result means the robot should maintain its existing controller
    behaviour. For the initial project this is the fixed roaming route. A
    returned request asks the policy to reconsider whether to continue roaming,
    approach, greet, yield, wait, or choose another allowed high-level action.
    """

    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or SchedulerConfig()
        self.reset()

    def reset(self) -> None:
        """Clear prior scene and invocation state before another experiment."""
        self._previous_humans: dict[str, _HumanSnapshot] | None = None
        self._previous_robot_context: tuple[str, str, str | None] | None = None
        self._last_state_timestamp_us: int | None = None
        self._last_invocation_timestamp_us: int | None = None
        self._pending_triggers: set[DecisionTrigger] = set()

    def evaluate(self, state: SocialState) -> DecisionRequest | None:
        """Return a model request when an event or active refresh is due."""
        self._validate_timestamp(state)
        current_humans = {
            human.track_id: _HumanSnapshot.from_state(human)
            for human in state.humans
        }
        current_robot_context = (
            state.robot.task,
            state.robot.controller_status,
            state.robot.destination_id,
        )

        triggers = self._detect_events(current_humans, current_robot_context)
        self._pending_triggers.update(triggers)

        elapsed_since_invocation_s = self._elapsed_since_invocation(state.timestamp_us)
        event_due = bool(self._pending_triggers) and (
            self._last_invocation_timestamp_us is None
            or elapsed_since_invocation_s >= self.config.minimum_decision_interval_s
        )
        refresh_due = (
            self.config.enable_active_refresh
            and bool(current_humans)
            and self._last_invocation_timestamp_us is not None
            and elapsed_since_invocation_s >= self.config.active_refresh_interval_s
        )
        if refresh_due:
            self._pending_triggers.add(DecisionTrigger.PERIODIC_REFRESH)

        request = None
        if event_due or refresh_due:
            request = DecisionRequest(
                state=state,
                triggers=tuple(
                    sorted(self._pending_triggers, key=lambda trigger: trigger.value)
                ),
            )
            self._pending_triggers.clear()
            self._last_invocation_timestamp_us = state.timestamp_us

        self._previous_humans = current_humans
        self._previous_robot_context = current_robot_context
        self._last_state_timestamp_us = state.timestamp_us
        return request

    def _detect_events(
        self,
        current_humans: dict[str, _HumanSnapshot],
        current_robot_context: tuple[str, str, str | None],
    ) -> set[DecisionTrigger]:
        triggers: set[DecisionTrigger] = set()
        previous_humans = self._previous_humans

        if previous_humans is None:
            for human in current_humans.values():
                triggers.add(DecisionTrigger.HUMAN_DETECTED)
                self._add_initial_human_events(triggers, human)
            return triggers

        new_ids = current_humans.keys() - previous_humans.keys()
        departed_ids = previous_humans.keys() - current_humans.keys()
        if new_ids:
            triggers.add(DecisionTrigger.HUMAN_DETECTED)
            for track_id in new_ids:
                self._add_initial_human_events(triggers, current_humans[track_id])
        if departed_ids and self.config.invoke_when_human_leaves:
            triggers.add(DecisionTrigger.HUMAN_LEFT)

        for track_id in current_humans.keys() & previous_humans.keys():
            previous = previous_humans[track_id]
            current = current_humans[track_id]
            if previous.observed and current.predicted_only:
                triggers.add(DecisionTrigger.TRACK_OCCLUDED)
            if previous.predicted_only and current.observed:
                triggers.add(DecisionTrigger.TRACK_REACQUIRED)
            if previous.motion_relation != current.motion_relation:
                triggers.add(DecisionTrigger.MOTION_CHANGED)
            if previous.distance_trend != current.distance_trend:
                triggers.add(DecisionTrigger.DISTANCE_TREND_CHANGED)
            if previous.attention_state != current.attention_state:
                triggers.add(DecisionTrigger.ATTENTION_CHANGED)
            if previous.proxemic_zone != current.proxemic_zone:
                triggers.add(DecisionTrigger.PROXEMIC_ZONE_CHANGED)
            self._add_engagement_events(triggers, previous, current)

        if (
            self._previous_robot_context is not None
            and current_robot_context != self._previous_robot_context
            and (current_humans or previous_humans)
        ):
            triggers.add(DecisionTrigger.ROBOT_CONTEXT_CHANGED)
        return triggers

    def _add_initial_human_events(
        self,
        triggers: set[DecisionTrigger],
        human: _HumanSnapshot,
    ) -> None:
        if human.predicted_only:
            triggers.add(DecisionTrigger.TRACK_OCCLUDED)
        if human.engagement_state == "HUMAN_SPEAKING":
            triggers.add(DecisionTrigger.HUMAN_STARTED_SPEAKING)
        if human.attention_state not in {"UNKNOWN", "NOT_ATTENDING"}:
            triggers.add(DecisionTrigger.ATTENTION_CHANGED)
        if human.motion_relation not in {"UNKNOWN", "STATIONARY"}:
            triggers.add(DecisionTrigger.MOTION_CHANGED)

    def _add_engagement_events(
        self,
        triggers: set[DecisionTrigger],
        previous: _HumanSnapshot,
        current: _HumanSnapshot,
    ) -> None:
        if previous.engagement_state == current.engagement_state:
            return
        if current.engagement_state == "HUMAN_SPEAKING":
            triggers.add(DecisionTrigger.HUMAN_STARTED_SPEAKING)
        elif previous.engagement_state == "HUMAN_SPEAKING":
            triggers.add(DecisionTrigger.HUMAN_STOPPED_SPEAKING)
        else:
            triggers.add(DecisionTrigger.ENGAGEMENT_CHANGED)

    def _elapsed_since_invocation(self, timestamp_us: int) -> float:
        if self._last_invocation_timestamp_us is None:
            return math.inf
        return (
            timestamp_us - self._last_invocation_timestamp_us
        ) / MICROSECONDS_PER_SECOND

    def _validate_timestamp(self, state: SocialState) -> None:
        if (
            self._last_state_timestamp_us is not None
            and state.timestamp_us <= self._last_state_timestamp_us
        ):
            raise DecisionSchedulerError(
                f"state timestamp_us {state.timestamp_us} must be greater than "
                f"the previous timestamp {self._last_state_timestamp_us}"
            )
