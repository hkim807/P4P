"""Deterministic torso depth and isolated level-camera coordinate conversion."""

from __future__ import annotations

import math
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable


BBox = tuple[float, float, float, float]
Point3 = tuple[float, float, float]


@dataclass(frozen=True)
class DepthResult:
    position_robot_m: Point3 | None = None
    distance_m: float | None = None
    depth_m: float | None = None
    pixel: tuple[float, float] | None = None
    failure: str | None = None


def camera_to_robot(point: Point3, camera_height_m: float) -> Point3:
    if len(point) != 3 or not all(math.isfinite(v) for v in point):
        raise ValueError("camera point must contain three finite coordinates")
    if not math.isfinite(camera_height_m) or camera_height_m <= 0:
        raise ValueError("camera height must be positive and finite")
    x, y, z = point
    result = (z, -x, camera_height_m - y)
    if not all(math.isfinite(v) for v in result):
        raise ValueError("transformed point must be finite")
    return result


def torso_roi(bbox: BBox, width: int, height: int) -> tuple[int, int, int, int] | None:
    """Clip body box, then use horizontal 30-70%, vertical 25-55%.

    Coordinates are half-open image ranges. Empty/subpixel boxes are rejected.
    This is a torso proxy, not a face, body centre, or segmentation mask.
    """
    if width <= 0 or height <= 0 or not all(math.isfinite(v) for v in bbox):
        return None
    x1, y1, x2, y2 = bbox
    x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
    y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    roi = (math.ceil(x1 + 0.30 * (x2 - x1)), math.ceil(y1 + 0.25 * (y2 - y1)),
           math.floor(x1 + 0.70 * (x2 - x1)), math.floor(y1 + 0.55 * (y2 - y1)))
    return roi if roi[2] > roi[0] and roi[3] > roi[1] else None


def estimate_position(raw_depth: Any, bbox: BBox, depth_scale_m: float,
                      intrinsics: Any, deproject: Callable, camera_height_m: float,
                      *, exclude_boxes: tuple[BBox, ...] = (),
                      min_samples: int = 20, min_valid_ratio: float = 0.50,
                      min_depth_m: float = 0.30, max_depth_m: float = 6.0) -> DepthResult:
    np = import_module("numpy")
    if not math.isfinite(depth_scale_m) or depth_scale_m <= 0:
        return DepthResult(failure="invalid depth scale")
    if raw_depth.ndim != 2:
        return DepthResult(failure="invalid depth image")
    height, width = raw_depth.shape
    roi = torso_roi(bbox, width, height)
    if roi is None:
        return DepthResult(failure="empty torso ROI")
    x1, y1, x2, y2 = roi
    values = raw_depth[y1:y2, x1:x2].astype(float) * depth_scale_m
    valid = np.isfinite(values) & (values >= min_depth_m) & (values <= max_depth_m)
    # Conservatively exclude pixels inside any other detected body's box.
    # No other person's depth or fallback region is substituted.
    for other in exclude_boxes:
        if not all(math.isfinite(v) for v in other):
            continue
        ox1, oy1 = max(x1, math.floor(other[0])), max(y1, math.floor(other[1]))
        ox2, oy2 = min(x2, math.ceil(other[2])), min(y2, math.ceil(other[3]))
        if ox2 > ox1 and oy2 > oy1:
            valid[oy1-y1:oy2-y1, ox1-x1:ox2-x1] = False
    count = int(np.count_nonzero(valid))
    if count < min_samples or count / values.size < min_valid_ratio:
        return DepthResult(failure="insufficient valid torso depth")
    median = float(np.median(values[valid]))
    # Pick the eligible pixel nearest ROI centre whose depth matches the median.
    # This avoids deprojecting a masked/invalid pixel or mixing people.
    rows, columns = np.nonzero(valid)
    depths = values[valid]
    delta = np.abs(depths - median)
    nearest = delta == delta.min()
    rows, columns = rows[nearest], columns[nearest]
    centre_x, centre_y = (x2-x1-1)/2, (y2-y1-1)/2
    index = int(np.argmin((columns-centre_x)**2 + (rows-centre_y)**2))
    pixel = (float(x1 + columns[index]), float(y1 + rows[index]))
    try:
        if intrinsics is None:
            raise ValueError("colour intrinsics unavailable")
        point = deproject(intrinsics, list(pixel), median)
        position = camera_to_robot(tuple(point), camera_height_m)
        distance = math.hypot(position[0], position[1])
        if not math.isfinite(distance):
            raise ValueError("non-finite planar distance")
    except (ValueError, RuntimeError, TypeError, OverflowError) as error:
        return DepthResult(failure=f"deprojection failed: {error}")
    return DepthResult(position, distance, median, pixel)
