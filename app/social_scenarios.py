"""Explicit synthetic transformations of a recorded frame template for validation."""
from copy import deepcopy

def controlled_scenarios(template):
    """Return raw-schema-compatible inputs and expected checkpoints.

    These are synthetic stimuli, not labels for the recorded source scenario.
    """
    origin = template["timestamp"]
    person = deepcopy(template["people"][0]) if template["people"] else {"uid": 101}
    frames = []
    for i in range(160):
        t = i / 10
        frame = deepcopy(template)
        frame["timestamp"] = origin + i * 100_000
        p = deepcopy(person)
        p.pop("optional_relative_head_position", None)  # Old geometry would contradict injected distance.
        p.update(uid=101, gaze_overlap=(0.1 if t < 4 or t >= 12 else 0.95 if t < 8
                                      else 0.95 if i % 6 < 3 else 0.1),
                 distance_m=(3.0 if t < 4 else 3.0 - 0.2*(t-4) if t < 8
                             else 2.2 if t < 12 else 2.2 + 0.2*(t-12)))
        frame["people"] = [p]
        frame["robot"] = {"linear_velocity": 0.0, "angular_velocity": 0.0}
        frame["safety"] = {"lidar": None, "sonar": None}
        frames.append(frame)
    expectations = {
        35: {"gaze_state": "NONE", "relative_distance_trend": "STABLE"},
        75: {"gaze_state": "SUSTAINED", "relative_distance_trend": "DECREASING"},
        115: {"gaze_state": "INTERMITTENT", "relative_distance_trend": "STABLE"},
        155: {"gaze_state": "NONE", "relative_distance_trend": "INCREASING"},
    }
    return frames, expectations
