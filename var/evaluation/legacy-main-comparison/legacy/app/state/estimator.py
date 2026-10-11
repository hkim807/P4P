"""Deterministic MVP temporal social-state estimation.

This first implementation deliberately uses transparent finite differences and
thresholds. It establishes the streaming contract and a testable baseline
before adding filtering, learned classifiers, group inference, or richer path
prediction.
"""

from __future__ import annotations

import hashlib
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from app.domain.models import (
    AttentionEvidence,
    AttentionState,
    CoordinateFrame,
    CrowdState,
    DistanceTrend,
    EngagementEvidence,
    EngagementState,
    HumanObservation,
    HumanSocialState,
    MotionRelation,
    ObservationFrame,
    ProxemicZone,
    RobotSocialState,
    SocialState,
    Uncertainty,
    Vector2,
    Vector3,
)


MICROSECONDS_PER_SECOND = 1_000_000
EPSILON = 1e-9


class TemporalSocialStateError(ValueError):
    """An observation stream cannot be processed safely by the estimator."""


@dataclass(frozen=True)
class EstimatorConfig:
    """Thresholds and time windows for the deterministic MVP estimator."""

    history_window_s: float = 5.0
    motion_window_s: float = 0.5
    gaze_short_window_s: float = 0.5
    gaze_long_window_s: float = 2.0
    occlusion_retention_s: float = 2.0
    prediction_uncertainty_growth_mps: float = 0.20
    stationary_speed_mps: float = 0.05
    radial_motion_threshold_mps: float = 0.08
    crossing_speed_threshold_mps: float = 0.30
    gaze_threshold: float = 0.50
    sustained_gaze_mean: float = 0.70
    sustained_gaze_ratio: float = 0.75
    intermittent_gaze_ratio: float = 0.25
    speech_threshold: float = 0.50
    intimate_distance_m: float = 0.45
    personal_distance_m: float = 1.20
    social_distance_m: float = 3.60

    def __post_init__(self) -> None:
        positive = (
            "history_window_s",
            "motion_window_s",
            "gaze_short_window_s",
            "gaze_long_window_s",
            "occlusion_retention_s",
            "prediction_uncertainty_growth_mps",
            "stationary_speed_mps",
            "radial_motion_threshold_mps",
            "crossing_speed_threshold_mps",
            "intimate_distance_m",
            "personal_distance_m",
            "social_distance_m",
        )
        for name in positive:
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        probabilities = (
            "gaze_threshold",
            "sustained_gaze_mean",
            "sustained_gaze_ratio",
            "intermittent_gaze_ratio",
            "speech_threshold",
        )
        for name in probabilities:
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.motion_window_s > self.history_window_s:
            raise ValueError("motion_window_s must not exceed history_window_s")
        if self.gaze_short_window_s > self.gaze_long_window_s:
            raise ValueError(
                "gaze_short_window_s must not exceed gaze_long_window_s"
            )
        if self.gaze_long_window_s > self.history_window_s:
            raise ValueError("gaze_long_window_s must not exceed history_window_s")
        if not (
            self.intimate_distance_m
            < self.personal_distance_m
            < self.social_distance_m
        ):
            raise ValueError("proxemic distance thresholds must be strictly increasing")


@dataclass(frozen=True)
class _TrackSample:
    timestamp_us: int
    human: HumanObservation
    position: tuple[float, float, float] | None
    distance_m: float | None
    robot_velocity: tuple[float, float]


@dataclass
class _Track:
    track_id: str
    first_seen_us: int
    last_seen_us: int
    samples: deque[_TrackSample] = field(default_factory=deque)
    relative_velocity_mps: tuple[float, float] | None = None
    human_velocity_mps: tuple[float, float] | None = None
    acceleration_mps2: float | None = None
    velocity_timestamp_us: int | None = None


class TemporalSocialStateEstimator:
    """Convert a monotonic stream of robot-base observations into social state."""

    def __init__(self, config: EstimatorConfig | None = None) -> None:
        self.config = config or EstimatorConfig()
        self.reset()

    def reset(self) -> None:
        """Clear all stream and track history before processing another run."""
        self._tracks: dict[str, _Track] = {}
        self._last_timestamp_us: int | None = None
        self._clock_domain: str | None = None

    def process(
        self, observations: Iterable[ObservationFrame]
    ) -> Iterator[SocialState]:
        """Yield one updated social state for every incoming observation."""
        for observation in observations:
            yield self.update(observation)

    def update(self, observation: ObservationFrame) -> SocialState:
        """Ingest one frame and return the state derived at its timestamp."""
        self._validate_stream(observation)
        now_us = observation.timestamp_us
        observed_by_id = {
            human.track_id: human for human in observation.humans if human.observed
        }

        for human in observed_by_id.values():
            track = self._tracks.get(human.track_id)
            if track is None:
                track = _Track(
                    track_id=human.track_id,
                    first_seen_us=now_us,
                    last_seen_us=now_us,
                )
                self._tracks[human.track_id] = track
            self._append_observation(track, human, observation)

        self._remove_expired_tracks(now_us)
        humans = [
            self._build_human_state(
                track,
                now_us=now_us,
                currently_observed=track.track_id in observed_by_id,
            )
            for track in sorted(self._tracks.values(), key=lambda item: item.track_id)
        ]

        state = SocialState(
            schema_version="1.0",
            state_id=self._state_id(observation),
            source_observation_id=observation.observation_id,
            timestamp_us=now_us,
            clock_domain=observation.clock_domain,
            coordinate_frame=observation.coordinate_frame,
            history_window_s=self.config.history_window_s,
            robot=RobotSocialState.model_validate(
                observation.robot.model_dump(mode="python")
            ),
            humans=humans,
            groups=[],
            crowd=self._build_crowd_state(humans),
            selected_image_ids=[image.image_id for image in observation.images],
        )
        self._last_timestamp_us = now_us
        return state

    def _validate_stream(self, observation: ObservationFrame) -> None:
        if observation.coordinate_frame != CoordinateFrame.ROBOT_BASE.value:
            raise TemporalSocialStateError(
                "the MVP estimator requires ROBOT_BASE observations"
            )
        if (
            self._last_timestamp_us is not None
            and observation.timestamp_us <= self._last_timestamp_us
        ):
            raise TemporalSocialStateError(
                f"timestamp_us {observation.timestamp_us} must be greater than "
                f"the previous timestamp {self._last_timestamp_us}"
            )
        if self._clock_domain is None:
            self._clock_domain = observation.clock_domain
        elif observation.clock_domain != self._clock_domain:
            raise TemporalSocialStateError(
                "clock_domain must remain constant within an estimator run"
            )

    def _append_observation(
        self,
        track: _Track,
        human: HumanObservation,
        observation: ObservationFrame,
    ) -> None:
        position = None
        if human.position_robot_m is not None:
            position = (
                human.position_robot_m.x,
                human.position_robot_m.y,
                human.position_robot_m.z,
            )
        distance_m = human.distance_m
        if distance_m is None and position is not None:
            distance_m = math.hypot(position[0], position[1])

        track.last_seen_us = observation.timestamp_us
        track.samples.append(
            _TrackSample(
                timestamp_us=observation.timestamp_us,
                human=human,
                position=position,
                distance_m=distance_m,
                robot_velocity=(
                    observation.robot.linear_velocity_mps.x,
                    observation.robot.linear_velocity_mps.y,
                ),
            )
        )
        self._prune_samples(track, observation.timestamp_us)
        self._update_motion(track)

    def _prune_samples(self, track: _Track, now_us: int) -> None:
        storage_window_s = max(
            self.config.history_window_s,
            self.config.occlusion_retention_s,
        )
        cutoff_us = now_us - round(storage_window_s * MICROSECONDS_PER_SECOND)
        while len(track.samples) > 1 and track.samples[0].timestamp_us < cutoff_us:
            track.samples.popleft()

    def _update_motion(self, track: _Track) -> None:
        positioned = [sample for sample in track.samples if sample.position is not None]
        if len(positioned) < 2:
            return
        latest = positioned[-1]
        cutoff_us = latest.timestamp_us - round(
            self.config.motion_window_s * MICROSECONDS_PER_SECOND
        )
        candidates = [sample for sample in positioned if sample.timestamp_us >= cutoff_us]
        if len(candidates) < 2:
            return
        earliest = candidates[0]
        elapsed_s = (latest.timestamp_us - earliest.timestamp_us) / MICROSECONDS_PER_SECOND
        if elapsed_s <= EPSILON:
            return

        relative_velocity = (
            (latest.position[0] - earliest.position[0]) / elapsed_s,
            (latest.position[1] - earliest.position[1]) / elapsed_s,
        )
        human_velocity = (
            relative_velocity[0] + latest.robot_velocity[0],
            relative_velocity[1] + latest.robot_velocity[1],
        )
        speed = math.hypot(*human_velocity)

        acceleration = None
        if (
            track.human_velocity_mps is not None
            and track.velocity_timestamp_us is not None
            and latest.timestamp_us > track.velocity_timestamp_us
        ):
            previous_speed = math.hypot(*track.human_velocity_mps)
            velocity_elapsed_s = (
                latest.timestamp_us - track.velocity_timestamp_us
            ) / MICROSECONDS_PER_SECOND
            acceleration = (speed - previous_speed) / velocity_elapsed_s

        track.relative_velocity_mps = relative_velocity
        track.human_velocity_mps = human_velocity
        track.acceleration_mps2 = acceleration
        track.velocity_timestamp_us = latest.timestamp_us

    def _remove_expired_tracks(self, now_us: int) -> None:
        retention_us = round(
            self.config.occlusion_retention_s * MICROSECONDS_PER_SECOND
        )
        expired = [
            track_id
            for track_id, track in self._tracks.items()
            if now_us - track.last_seen_us > retention_us
        ]
        for track_id in expired:
            del self._tracks[track_id]

    def _build_human_state(
        self,
        track: _Track,
        *,
        now_us: int,
        currently_observed: bool,
    ) -> HumanSocialState:
        latest = track.samples[-1]
        time_since_seen_s = (now_us - track.last_seen_us) / MICROSECONDS_PER_SECOND
        position = latest.position
        distance_m = latest.distance_m
        if not currently_observed and position is not None:
            position = self._predict_position(
                position,
                track.relative_velocity_mps,
                time_since_seen_s,
            )
            distance_m = math.hypot(position[0], position[1])

        relative_velocity = track.relative_velocity_mps
        human_velocity = track.human_velocity_mps
        closing_speed = self._closing_speed(position, relative_velocity)
        distance_trend = self._distance_trend(closing_speed)
        motion_relation = self._motion_relation(
            position=position,
            relative_velocity=relative_velocity,
            human_velocity=human_velocity,
            robot_velocity=latest.robot_velocity,
            closing_speed=closing_speed,
        )
        attention = self._attention(track.samples, now_us, currently_observed)
        engagement = self._engagement(
            track.samples,
            attention,
            now_us,
            currently_observed,
            distance_m,
        )
        uncertainty = self._uncertainty(
            latest.human.uncertainty,
            time_since_seen_s,
            currently_observed,
        )
        state_age_ms = max(
            latest.human.state_age_ms,
            max(0, now_us - latest.human.source_timestamp_us) // 1_000,
        )
        if not currently_observed:
            state_age_ms = max(
                state_age_ms,
                latest.human.state_age_ms + round(time_since_seen_s * 1_000),
            )

        evidence_codes = self._evidence_codes(
            currently_observed=currently_observed,
            position=position,
            motion_relation=motion_relation,
            distance_trend=distance_trend,
            attention=attention,
            engagement=engagement,
        )
        return HumanSocialState(
            track_id=track.track_id,
            observed=currently_observed,
            predicted_only=not currently_observed,
            state_age_ms=state_age_ms,
            track_age_s=(now_us - track.first_seen_us) / MICROSECONDS_PER_SECOND,
            time_since_seen_s=time_since_seen_s,
            position_robot_m=(
                Vector3(x=position[0], y=position[1], z=position[2])
                if position is not None
                else None
            ),
            distance_m=distance_m,
            velocity_robot_mps=(
                Vector2(x=human_velocity[0], y=human_velocity[1])
                if human_velocity is not None
                else None
            ),
            speed_mps=math.hypot(*human_velocity) if human_velocity is not None else None,
            acceleration_mps2=track.acceleration_mps2,
            heading_rad=(
                math.atan2(human_velocity[1], human_velocity[0])
                if human_velocity is not None
                and math.hypot(*human_velocity) >= self.config.stationary_speed_mps
                else None
            ),
            closing_speed_mps=closing_speed,
            motion_relation=motion_relation,
            distance_trend=distance_trend,
            predicted_positions=[],
            proxemic_zone=self._proxemic_zone(distance_m),
            head_rpy_rad=latest.human.head_rpy_rad if currently_observed else None,
            body_yaw_rad=latest.human.body_yaw_rad if currently_observed else None,
            attention=attention,
            engagement=engagement,
            gesture=None,
            group_id=None,
            facial_expression=(
                latest.human.facial_expression if currently_observed else None
            ),
            uncertainty=uncertainty,
            evidence_codes=evidence_codes,
        )

    def _predict_position(
        self,
        position: tuple[float, float, float],
        relative_velocity: tuple[float, float] | None,
        elapsed_s: float,
    ) -> tuple[float, float, float]:
        if relative_velocity is None:
            return position
        return (
            position[0] + relative_velocity[0] * elapsed_s,
            position[1] + relative_velocity[1] * elapsed_s,
            position[2],
        )

    def _closing_speed(
        self,
        position: tuple[float, float, float] | None,
        relative_velocity: tuple[float, float] | None,
    ) -> float | None:
        if position is None or relative_velocity is None:
            return None
        distance = math.hypot(position[0], position[1])
        if distance <= EPSILON:
            return None
        radial_velocity = (
            position[0] * relative_velocity[0]
            + position[1] * relative_velocity[1]
        ) / distance
        return -radial_velocity

    def _distance_trend(self, closing_speed: float | None) -> str:
        if closing_speed is None:
            return DistanceTrend.UNKNOWN.value
        if closing_speed > self.config.radial_motion_threshold_mps:
            return DistanceTrend.DECREASING.value
        if closing_speed < -self.config.radial_motion_threshold_mps:
            return DistanceTrend.INCREASING.value
        return DistanceTrend.STABLE.value

    def _motion_relation(
        self,
        *,
        position: tuple[float, float, float] | None,
        relative_velocity: tuple[float, float] | None,
        human_velocity: tuple[float, float] | None,
        robot_velocity: tuple[float, float],
        closing_speed: float | None,
    ) -> str:
        if position is None or relative_velocity is None or closing_speed is None:
            return MotionRelation.UNKNOWN.value
        distance = math.hypot(position[0], position[1])
        if distance <= EPSILON:
            return MotionRelation.UNKNOWN.value

        relative_speed = math.hypot(*relative_velocity)
        robot_speed = math.hypot(*robot_velocity)
        human_speed = math.hypot(*human_velocity) if human_velocity is not None else 0.0
        if relative_speed < self.config.stationary_speed_mps:
            if (
                robot_speed >= self.config.stationary_speed_mps
                and human_speed >= self.config.stationary_speed_mps
            ):
                return MotionRelation.PARALLEL.value
            return MotionRelation.STATIONARY.value

        transverse_speed = abs(
            position[0] * relative_velocity[1]
            - position[1] * relative_velocity[0]
        ) / distance
        if transverse_speed >= self.config.crossing_speed_threshold_mps:
            if closing_speed > self.config.radial_motion_threshold_mps:
                return MotionRelation.APPROACHING_CROSSING.value
            return MotionRelation.CROSSING.value
        if closing_speed > self.config.radial_motion_threshold_mps:
            return MotionRelation.APPROACHING.value
        if closing_speed < -self.config.radial_motion_threshold_mps:
            return MotionRelation.RECEDING.value
        return MotionRelation.PARALLEL.value

    def _attention(
        self,
        samples: Sequence[_TrackSample],
        now_us: int,
        currently_observed: bool,
    ) -> AttentionEvidence:
        short = self._windowed_signal(
            samples,
            now_us,
            self.config.gaze_short_window_s,
            "gaze_to_robot_score",
        )
        long = self._windowed_signal(
            samples,
            now_us,
            self.config.gaze_long_window_s,
            "gaze_to_robot_score",
        )
        current = (
            samples[-1].human.gaze_to_robot_score if currently_observed else None
        )
        short_mean = self._mean(short)
        long_mean = self._mean(long)
        ratio = (
            sum(value >= self.config.gaze_threshold for _, value in long) / len(long)
            if long
            else None
        )
        longest = self._longest_active_duration(long, self.config.gaze_threshold)
        long_coverage_s = (
            (long[-1][0] - long[0][0]) / MICROSECONDS_PER_SECOND
            if len(long) >= 2
            else 0.0
        )
        since_gaze = self._time_since_active(
            samples,
            now_us,
            "gaze_to_robot_score",
            self.config.gaze_threshold,
        )
        switch_rate = self._switch_rate(long, self.config.gaze_threshold)

        if not currently_observed:
            state = AttentionState.UNKNOWN.value
        elif not long:
            state = AttentionState.UNKNOWN.value
        elif (
            long_mean is not None
            and ratio is not None
            and long_mean >= self.config.sustained_gaze_mean
            and ratio >= self.config.sustained_gaze_ratio
            and longest is not None
            and longest >= self.config.gaze_short_window_s
        ):
            state = AttentionState.SUSTAINED.value
        elif (
            ratio is not None
            and ratio >= self.config.intermittent_gaze_ratio
            and long_coverage_s >= self.config.gaze_short_window_s
        ):
            state = AttentionState.INTERMITTENT.value
        elif current is not None and current >= self.config.gaze_threshold:
            state = AttentionState.GLANCE.value
        else:
            state = AttentionState.NOT_ATTENDING.value

        return AttentionEvidence(
            gaze_to_robot_score=current,
            gaze_mean_500ms=short_mean,
            gaze_mean_2s=long_mean,
            gaze_ratio_2s=ratio,
            longest_mutual_gaze_2s_s=longest,
            time_since_gaze_s=since_gaze,
            gaze_switch_rate_2s_hz=switch_rate,
            state=state,
        )

    def _engagement(
        self,
        samples: Sequence[_TrackSample],
        attention: AttentionEvidence,
        now_us: int,
        currently_observed: bool,
        distance_m: float | None,
    ) -> EngagementEvidence:
        speech = samples[-1].human.speech_activity if currently_observed else None
        since_speech = self._time_since_active(
            samples,
            now_us,
            "speech_activity",
            self.config.speech_threshold,
        )
        if not currently_observed:
            state = EngagementState.UNKNOWN.value
            probability = None
            addressee_probability = None
        else:
            signals = [
                value
                for value in (speech, attention.gaze_mean_500ms)
                if value is not None
            ]
            probability = max(signals) if signals else None
            addressee_probability = attention.gaze_mean_500ms
            if speech is not None and speech >= self.config.speech_threshold:
                state = EngagementState.HUMAN_SPEAKING.value
            elif attention.state in {
                AttentionState.SUSTAINED.value,
                AttentionState.INTERMITTENT.value,
            }:
                state = EngagementState.ATTENDING.value
            elif attention.state == AttentionState.NOT_ATTENDING.value:
                state = EngagementState.DISENGAGED.value
            elif distance_m is not None:
                state = EngagementState.AVAILABLE.value
            else:
                state = EngagementState.UNKNOWN.value

        return EngagementEvidence(
            state=state,
            engagement_probability=probability,
            addressee_probability=addressee_probability,
            speech_activity=speech,
            time_since_speech_s=since_speech,
        )

    def _windowed_signal(
        self,
        samples: Sequence[_TrackSample],
        now_us: int,
        window_s: float,
        field_name: str,
    ) -> list[tuple[int, float]]:
        cutoff_us = now_us - round(window_s * MICROSECONDS_PER_SECOND)
        values: list[tuple[int, float]] = []
        for sample in samples:
            value = getattr(sample.human, field_name)
            if sample.timestamp_us >= cutoff_us and value is not None:
                values.append((sample.timestamp_us, value))
        return values

    def _mean(self, values: Sequence[tuple[int, float]]) -> float | None:
        if not values:
            return None
        return sum(value for _, value in values) / len(values)

    def _longest_active_duration(
        self, values: Sequence[tuple[int, float]], threshold: float
    ) -> float | None:
        if not values:
            return None
        longest_us = 0
        run_start_us: int | None = None
        run_end_us: int | None = None
        for timestamp_us, value in values:
            if value >= threshold:
                if run_start_us is None:
                    run_start_us = timestamp_us
                run_end_us = timestamp_us
            elif run_start_us is not None and run_end_us is not None:
                longest_us = max(longest_us, run_end_us - run_start_us)
                run_start_us = None
                run_end_us = None
        if run_start_us is not None and run_end_us is not None:
            longest_us = max(longest_us, run_end_us - run_start_us)
        return longest_us / MICROSECONDS_PER_SECOND

    def _time_since_active(
        self,
        samples: Sequence[_TrackSample],
        now_us: int,
        field_name: str,
        threshold: float,
    ) -> float | None:
        for sample in reversed(samples):
            value = getattr(sample.human, field_name)
            if value is not None and value >= threshold:
                return (now_us - sample.timestamp_us) / MICROSECONDS_PER_SECOND
        return None

    def _switch_rate(
        self, values: Sequence[tuple[int, float]], threshold: float
    ) -> float | None:
        if len(values) < 2:
            return None
        switches = sum(
            (left[1] >= threshold) != (right[1] >= threshold)
            for left, right in zip(values, values[1:])
        )
        duration_s = (values[-1][0] - values[0][0]) / MICROSECONDS_PER_SECOND
        if duration_s <= EPSILON:
            return None
        return switches / duration_s

    def _uncertainty(
        self,
        observed: Uncertainty | None,
        time_since_seen_s: float,
        currently_observed: bool,
    ) -> Uncertainty:
        if currently_observed:
            return observed or Uncertainty()
        base_position_std = (
            observed.position_std_m
            if observed is not None and observed.position_std_m is not None
            else 0.0
        )
        return Uncertainty(
            position_std_m=(
                base_position_std
                + self.config.prediction_uncertainty_growth_mps * time_since_seen_s
            ),
            velocity_std_mps=(observed.velocity_std_mps if observed else None),
            heading_std_rad=(observed.heading_std_rad if observed else None),
            occlusion_probability=min(
                1.0, time_since_seen_s / self.config.occlusion_retention_s
            ),
        )

    def _proxemic_zone(self, distance_m: float | None) -> str:
        if distance_m is None:
            return ProxemicZone.UNKNOWN.value
        if distance_m <= self.config.intimate_distance_m:
            return ProxemicZone.INTIMATE.value
        if distance_m <= self.config.personal_distance_m:
            return ProxemicZone.PERSONAL.value
        if distance_m <= self.config.social_distance_m:
            return ProxemicZone.SOCIAL.value
        return ProxemicZone.PUBLIC.value

    def _evidence_codes(
        self,
        *,
        currently_observed: bool,
        position: tuple[float, float, float] | None,
        motion_relation: str,
        distance_trend: str,
        attention: AttentionEvidence,
        engagement: EngagementEvidence,
    ) -> list[str]:
        evidence = ["TRACK_OBSERVED" if currently_observed else "TRACK_PREDICTED"]
        evidence.append("POSITION_AVAILABLE" if position is not None else "POSITION_UNKNOWN")
        if motion_relation != MotionRelation.UNKNOWN.value:
            evidence.append(f"MOTION_{motion_relation}")
        if distance_trend != DistanceTrend.UNKNOWN.value:
            evidence.append(f"DISTANCE_{distance_trend}")
        if attention.state == AttentionState.UNKNOWN.value:
            evidence.append("ATTENTION_UNKNOWN")
        else:
            evidence.append(f"ATTENTION_{attention.state}")
        if engagement.state == EngagementState.HUMAN_SPEAKING.value:
            evidence.append("HUMAN_SPEAKING")
        return evidence

    def _build_crowd_state(
        self, humans: Sequence[HumanSocialState]
    ) -> CrowdState:
        distances = [human.distance_m for human in humans if human.distance_m is not None]
        within_1m = sum(distance <= 1.0 for distance in distances)
        within_2m = sum(distance <= 2.0 for distance in distances)
        within_3m = sum(distance <= 3.0 for distance in distances)
        return CrowdState(
            people_within_1m=within_1m,
            people_within_2m=within_2m,
            people_within_3m=within_3m,
            density_people_m2=within_3m / (math.pi * 3.0**2),
        )

    def _state_id(self, observation: ObservationFrame) -> str:
        material = (
            f"{observation.schema_version}:{observation.observation_id}:"
            f"{observation.timestamp_us}"
        ).encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()[:24]
        return f"state-{digest}"
