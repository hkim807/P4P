"""Gap-aware temporal measurements. Original raw track samples are never changed."""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from math import sqrt
from statistics import median

from app.state.social_models import TemporalConfig, TemporalEvidence


def adjacent(a: dict, b: dict, config: TemporalConfig) -> bool:
    return (b["frame_sequence"] == a["frame_sequence"] + 1
            and 0 < b["timestamp_us"] - a["timestamp_us"] <= round(config.max_gap_s * 1e6))


def moving(robot: dict, config: TemporalConfig) -> bool:
    return ((robot["linear_velocity"] is not None
             and abs(robot["linear_velocity"]) > config.stationary_linear_tolerance_mps)
            or (robot["angular_velocity"] is not None
                and abs(robot["angular_velocity"]) > config.stationary_angular_tolerance_rps))


def stationary(robot: dict, config: TemporalConfig) -> bool:
    return (robot["linear_velocity"] is not None
            and robot["angular_velocity"] is not None
            and not moving(robot, config))


@dataclass
class GazeMemory:
    looking: bool | None = None
    last_sample: dict | None = None
    states: dict[int, bool | None] = field(default_factory=dict)


def extract_features(track: dict, now: int, config: TemporalConfig,
                     memory: GazeMemory) -> tuple[TemporalEvidence, list[str]]:
    samples = [s for s in track["samples"] if s["timestamp_us"] >= now - round(config.window_s * 1e6)]
    flags = list(track["identity_quality_flags"])
    observed = track["visibility"] == "OBSERVED"
    if not observed:
        flags.append("PERSON_NOT_OBSERVED")
    # Preserve sample-level hysteresis across rolling-window eviction, but reset at gaps/nulls.
    for sample in samples:
        if memory.last_sample is not None and sample["timestamp_us"] <= memory.last_sample["timestamp_us"]:
            continue
        if memory.last_sample is None or not adjacent(memory.last_sample, sample, config):
            memory.looking = None
        gaze = sample["gaze_overlap"]
        if gaze is None:
            memory.looking = None
        elif gaze >= config.looking_enter:
            memory.looking = True
        elif gaze <= config.looking_exit:
            memory.looking = False
        memory.states[sample["timestamp_us"]] = memory.looking
        memory.last_sample = sample
    times = {s["timestamp_us"] for s in samples}
    memory.states = {t: v for t, v in memory.states.items() if t in times}
    coverage = looking_time = sustained = 0.0
    # Count only distinct observed false->true transitions. Missing intervals
    # cannot manufacture repeated attention from fragmented detections.
    looking_bouts = 1 if samples and memory.states.get(samples[0]["timestamp_us"]) is True else 0
    for a, b in zip(samples, samples[1:]):
        sa, sb = memory.states.get(a["timestamp_us"]), memory.states.get(b["timestamp_us"])
        if adjacent(a, b, config) and sa is not None and sb is not None:
            dt = (b["timestamp_us"] - a["timestamp_us"]) / 1e6
            coverage += dt
            looking_time += dt if sa else 0.0
            sustained = sustained + dt if sa and sb else 0.0
            if sa is False and sb is True:
                looking_bouts += 1
        else:
            sustained = 0.0
    span = (now - samples[0]["timestamp_us"]) / 1e6 if samples else 0.0
    valid_gaze_samples = sum(memory.states.get(s["timestamp_us"]) is not None for s in samples)
    coverage_fraction = coverage / span if span else 0.0
    fraction = looking_time / coverage if coverage else None
    latest_gaze_valid = bool(samples and memory.states.get(samples[-1]["timestamp_us"]) is not None and observed)
    gaze_valid = (latest_gaze_valid and valid_gaze_samples >= config.min_samples
                  and span + 1e-9 >= config.min_span_s
                  and coverage + 1e-9 >= config.min_gaze_coverage_s
                  and coverage_fraction + 1e-9 >= config.min_gaze_coverage_fraction)
    if not latest_gaze_valid:
        flags.append("GAZE_UNAVAILABLE")
    if not gaze_valid:
        flags.append("INSUFFICIENT_GAZE_EVIDENCE")

    # Fit only the newest contiguous valid-distance segment. Gaps/nulls/jumps break it.
    segment: list[dict] = []
    jumps = 0
    latest_distance_valid = False
    for sample in samples:
        distance = sample["distance_m"]
        latest_distance_valid = distance is not None
        if distance is None:
            segment = []
            continue
        if segment:
            previous = segment[-1]
            dt = (sample["timestamp_us"] - previous["timestamp_us"]) / 1e6
            if not adjacent(previous, sample, config):
                segment = []
            elif abs(distance - previous["distance_m"]) > config.max_distance_speed_mps * dt + config.distance_jump_allowance_m:
                segment = []
                jumps += 1
                latest_distance_valid = False
        segment.append(sample)
    latest_distance_valid = latest_distance_valid and observed
    distance_span = (segment[-1]["timestamp_us"] - segment[0]["timestamp_us"]) / 1e6 if segment else 0.0
    fit = segment
    if len(fit) > config.max_fit_samples:
        fit = [segment[round(i * (len(segment)-1)/(config.max_fit_samples-1))]
               for i in range(config.max_fit_samples)]
    slope = residual = None
    if len(fit) >= config.min_samples and distance_span + 1e-9 >= config.min_span_s:
        points = [((s["timestamp_us"] - fit[0]["timestamp_us"]) / 1e6, s["distance_m"]) for s in fit]
        slope = median((y2-y1)/(t2-t1) for (t1, y1), (t2, y2) in combinations(points, 2))
        intercept = median(y - slope*t for t, y in points)
        residual = sqrt(sum((y - (slope*t + intercept))**2 for t, y in points) / len(points))
    distance_valid = bool(latest_distance_valid and slope is not None and residual <= config.max_fit_residual_m)
    if not latest_distance_valid:
        flags.append("DISTANCE_UNAVAILABLE_OR_JUMP")
    if slope is None:
        flags.append("INSUFFICIENT_DISTANCE_EVIDENCE")
    elif residual > config.max_fit_residual_m:
        flags.append("DISTANCE_FIT_UNRELIABLE")
    if jumps:
        flags.append("DISTANCE_SEGMENT_BROKEN_BY_JUMP")
    stationary_segment = bool(segment and all(stationary(s["robot"], config) for s in segment))
    if not stationary_segment:
        flags.append("STATIONARY_BASE_UNVERIFIED")
    return TemporalEvidence(
        window_span_s=span, gaze_fraction=fraction, gaze_valid_coverage_s=coverage,
        mean_gaze_overlap=(sum(s["gaze_overlap"] for s in samples if s["gaze_overlap"] is not None) /
                           sum(s["gaze_overlap"] is not None for s in samples)
                           if any(s["gaze_overlap"] is not None for s in samples) else None),
        latest_gaze_looking=memory.states.get(samples[-1]["timestamp_us"]) if samples and observed else None,
        looking_time_s=looking_time, looking_bouts=looking_bouts,
        gaze_coverage_fraction=coverage_fraction, gaze_valid_samples=valid_gaze_samples,
        sustained_gaze_s=sustained if observed else 0.0, distance_slope_mps=slope,
        distance_valid_span_s=distance_span, distance_fit_residual_m=residual,
        distance_valid_samples=len(segment), distance_fit_samples=len(fit),
        distance_window_start_us=segment[0]["timestamp_us"] if segment else None,
        distance_jump_count=jumps, gaze_valid=gaze_valid, distance_trend_valid=distance_valid,
        latest_distance_valid=latest_distance_valid, stationary_window_confirmed=stationary_segment,
    ), flags
