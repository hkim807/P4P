"""SDK-shaped fixtures; no Navel SDK dependency."""

from enum import Enum
from types import SimpleNamespace as NS


class Sensor(Enum):
    LIDAR = "lidar"
    SONAR = "sonar"


class CoordSystem(Enum):
    CAM_HEAD = 5
    HEAD_STRAIGHT = 3
    UNDEFINED = 0


def person(uid=17, **overrides):
    values = {
        "uid": uid, "dist_mm": 1850.0, "gaze_overlap": 0.75,
        "head_position": NS(x=9.0, y=8.0, z=7.0),  # Angles, not Cartesian position.
        "g_head_position": [NS(sys=CoordSystem.CAM_HEAD, x=1.8, y=0.3, z=0.1)],
    }
    values.update(overrides)
    return NS(**values)


def perception(*people):
    return NS(persons=list(people))


def locomotion(**overrides):
    values = {
        "odometry": NS(velocity=NS(linear_x=0.2, linear_y=0.0, angular_z=-0.1)),
        "distances": {Sensor.LIDAR: [2.0, 1.0], Sensor.SONAR: [0.5, 0.7, 1.2]},
    }
    values.update(overrides)
    return NS(**values)


def frame():
    return {
        "timestamp": 1_000_000,
        "people": [{"uid": 17, "distance_m": 1.85, "gaze_overlap": 0.75}],
        "robot": {"linear_velocity": 0.2, "angular_velocity": -0.1},
        "safety": {"lidar": [2.0, 1.0], "sonar": [0.5, 0.7, 1.2]},
    }
