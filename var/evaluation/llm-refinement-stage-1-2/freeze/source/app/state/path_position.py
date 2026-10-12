"""Provisional straight-route corridor from a fixed head camera's nose point."""
from __future__ import annotations

import math

from app.state.social_models import PathPosition, TemporalConfig


def classify_path_position(position: dict | None, config: TemporalConfig,
                           previous: PathPosition = "UNKNOWN") -> tuple[PathPosition, float | None, float | None]:
    """Use the offset/yaw transform pattern of the bounded APPROACH controller.

    CAM_HEAD x is treated as forward, y as lateral while the head is fixed.
    Width includes robot/person clearance; this is not a swept-body collision
    prediction. Missing geometry resets rather than retaining an old corridor.
    """
    if not config.path_assume_fixed_head or not position or position.get("coordinate_frame") != "CAM_HEAD":
        return "UNKNOWN", None, None
    xyz = [position.get(axis) for axis in "xyz"]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in xyz) or not any(xyz):
        return "UNKNOWN", None, None
    x, y, _ = xyz
    yaw = math.radians(config.path_camera_yaw_deg)
    forward = config.path_camera_x_m + math.cos(yaw)*x - math.sin(yaw)*y
    lateral = config.path_camera_y_m + math.sin(yaw)*x + math.cos(yaw)*y
    if not math.isfinite(forward) or not math.isfinite(lateral):
        return "UNKNOWN", None, None
    if forward <= 0:
        return "BEHIND", forward, lateral
    width = config.path_half_width_m
    if previous == "IN_WAY":
        width += config.path_hysteresis_m
    elif previous == "TO_SIDE":
        width -= config.path_hysteresis_m
    return ("IN_WAY" if abs(lateral) <= width else "TO_SIDE"), forward, lateral
