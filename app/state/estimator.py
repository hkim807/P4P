"""Temporal evidence -> categorical SocialState, with stateful hysteresis."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from app.recording import TimestampOrderError
from app.state.features import GazeMemory, adjacent, extract_features, moving, stationary
from app.state.social_models import (CueChange, PersonSocialState, RobotState,
                                    SocialState, TemporalConfig)

ZONES = ("TOO_CLOSE", "INTERACTION_RANGE", "APPROACHABLE", "FAR")
CUES = ("gaze_state", "distance_zone", "relative_distance_trend", "human_radial_motion")


@dataclass
class PersonMemory:
    gaze: GazeMemory = field(default_factory=GazeMemory)
    category: str = "UNKNOWN"
    candidate: str = "UNKNOWN"
    candidate_since: int = 0
    zone: str = "UNKNOWN"
    previous: dict = field(default_factory=dict)


class SocialStateEstimator:
    def __init__(self, config: TemporalConfig | None = None):
        self.config = config or TemporalConfig()
        self._session = None
        self._last_timestamp = None
        self._last_sequence = 0
        self._people: dict[tuple[int, int], PersonMemory] = {}

    def _gaze_category(self, evidence, memory, now):
        c = self.config
        if not evidence.gaze_valid:
            memory.category = memory.candidate = "UNKNOWN"
            memory.candidate_since = now
            return "UNKNOWN"
        fraction = evidence.gaze_fraction
        if (fraction >= (c.sustained_exit if memory.category == "SUSTAINED" else c.sustained_enter)
                and evidence.sustained_gaze_s + 1e-9 >= c.min_sustained_run_s):
            candidate = "SUSTAINED"
        elif fraction <= (c.none_exit if memory.category == "NONE" else c.none_enter):
            candidate = "NONE"
        else:
            candidate = "INTERMITTENT"
        if candidate != memory.candidate:
            memory.candidate, memory.candidate_since = candidate, now
        if now - memory.candidate_since >= round(c.category_dwell_s * 1e6):
            memory.category = candidate
        return memory.category

    def _zone(self, distance, valid, memory):
        if not valid:
            memory.zone = "UNKNOWN"
            return memory.zone
        c = self.config
        bounds = (c.too_close_m, c.interaction_max_m, c.approachable_max_m)
        if memory.zone == "UNKNOWN":
            index = bisect_right(bounds, distance)
        else:
            index = ZONES.index(memory.zone)
            while index < 3 and distance > bounds[index] + c.zone_hysteresis_m:
                index += 1
            while index > 0 and distance < bounds[index-1] - c.zone_hysteresis_m:
                index -= 1
        memory.zone = ZONES[index]
        return memory.zone

    def update(self, snapshot: dict) -> SocialState:
        now, sequence, session = snapshot["robot_timestamp_us"], snapshot["frame_sequence"], snapshot["session_id"]
        if session == self._session and (now <= self._last_timestamp or sequence <= self._last_sequence):
            raise TimestampOrderError("social snapshots must increase in source time and sequence")
        if session != self._session:
            self._people.clear()
            self._session = session
        current_keys = {(t["uid"], t["track_epoch"]) for t in snapshot["tracks"]}
        self._people = {k: v for k, v in self._people.items() if k in current_keys}
        people, changes = [], []
        for track in snapshot["tracks"]:
            key = (track["uid"], track["track_epoch"])
            memory = self._people.setdefault(key, PersonMemory())
            latest = track["samples"][-1] if track["samples"] else None
            if (track["visibility"] == "OBSERVED" and latest and memory.gaze.last_sample
                    and latest["timestamp_us"] > memory.gaze.last_sample["timestamp_us"]
                    and not adjacent(memory.gaze.last_sample, latest, self.config)):
                # Do not carry a category's dwell through an unobserved interval.
                memory.category = memory.candidate = "UNKNOWN"
                memory.candidate_since = now
            evidence, flags = extract_features(track, now, self.config, memory.gaze)
            gaze = self._gaze_category(evidence, memory, now)
            if gaze == "UNKNOWN" and evidence.gaze_valid:
                flags.append("GAZE_CATEGORY_PENDING_DWELL")
            distance = track["samples"][-1]["distance_m"] if track["samples"] else None
            zone = self._zone(distance, evidence.latest_distance_valid, memory)
            trend = "UNKNOWN"
            if evidence.distance_trend_valid:
                slope = evidence.distance_slope_mps
                trend = ("DECREASING" if slope < -self.config.distance_deadband_mps
                         else "INCREASING" if slope > self.config.distance_deadband_mps else "STABLE")
            human = {"DECREASING": "TOWARD", "INCREASING": "AWAY", "STABLE": "STATIONARY"}.get(trend, "UNKNOWN")
            if not evidence.stationary_window_confirmed:
                human = "UNKNOWN"
            person = PersonSocialState(
                uid=key[0], track_epoch=key[1], visibility=track["visibility"],
                track_age_s=track["track_age_s"], time_since_seen_s=track["time_since_seen_s"],
                latest_distance_m=distance, gaze_state=gaze, distance_zone=zone,
                relative_distance_trend=trend, human_radial_motion=human,
                evidence=evidence, validity_flags=flags)
            for cue in CUES:
                value = getattr(person, cue)
                previous = memory.previous.get(cue, "UNKNOWN")
                if value != previous:
                    changes.append(CueChange(uid=key[0], track_epoch=key[1], field=cue, previous=previous, current=value))
                memory.previous[cue] = value
            people.append(person)
        robot = snapshot["robot"]
        robot_moving = moving(robot, self.config)
        validity = []
        if any(v is None for v in robot.values()):
            validity.append("VELOCITY_INCOMPLETE")
        state = SocialState(
            state_id=f"{session}:{sequence}", session_id=session, ingest_sequence=sequence,
            robot_timestamp_us=now, config_version=self.config.version, config=self.config,
            robot=RobotState(**robot,
                motion_state="MOVING" if robot_moving else "STATIONARY" if stationary(robot, self.config) else "UNKNOWN",
                measurement_validity=validity), people=people, cue_changes=changes,
            track_events=snapshot["events"])
        self._last_timestamp, self._last_sequence = now, sequence
        return state
