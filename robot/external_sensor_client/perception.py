"""Lazy person-only YOLO/ByteTrack, owned by exactly one worker."""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from importlib import import_module
from typing import Any
from uuid import uuid4

from robot.external_sensor_client.capture import CapturedFrame
from robot.external_sensor_client.depth import BBox, DepthResult, estimate_position


class PerceptionError(RuntimeError):
    """Missing dependency, unsupported model, or inference failure."""


@dataclass(frozen=True)
class PersonDetection:
    bbox: BBox
    confidence: float
    tracker_id: int | None


@dataclass(frozen=True)
class TrackedPerson:
    track_id: str
    bbox: BBox
    confidence: float
    depth: DepthResult


@dataclass(frozen=True)
class PerceivedFrame:
    capture: CapturedFrame
    humans: tuple[TrackedPerson, ...] = ()
    rgb_bgr: Any = None
    depth_m: Any = None
    inference_ms: float = 0.0
    detections_without_track_id: int = 0
    humans_without_valid_depth: int = 0


def extract_detections(boxes: Any, confidence: float) -> tuple[PersonDetection, ...]:
    if boxes is None:
        return ()
    detections = []
    for box in boxes:
        class_id = float(box.cls.item())
        score = float(box.conf.item())
        if class_id != 0 or not math.isfinite(score) or not confidence <= score <= 1:
            continue
        bbox = tuple(float(v) for v in box.xyxy[0].tolist())
        if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox) or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        raw_id = None if box.id is None else float(box.id.item())
        tracker_id = None
        if raw_id is not None and math.isfinite(raw_id) and raw_id >= 0 and raw_id.is_integer():
            tracker_id = int(raw_id)
        detections.append(PersonDetection(bbox, score, tracker_id))
    return tuple(detections)


class PersonPerception:
    def __init__(self, model_name: str, confidence: float, camera_height_m: float,
                 *, model: Any = None, deproject: Any = None, session_id: str | None = None):
        if not math.isfinite(confidence) or not 0 < confidence <= 1:
            raise ValueError("person confidence must be in (0, 1]")
        if not math.isfinite(camera_height_m) or camera_height_m <= 0:
            raise ValueError("camera height must be positive and finite")
        self.confidence, self.camera_height_m = confidence, camera_height_m
        self.session_id = session_id or uuid4().hex
        if not self.session_id or len(self.session_id) > 64 or not all(c.isalnum() or c in "-_" for c in self.session_id):
            raise ValueError("session ID must be 1-64 identifier characters")
        self.model = None
        try:
            self.np = import_module("numpy")
            if model is None:
                # All declared dependencies must be installed explicitly; do not
                # let Ultralytics auto-install ByteTrack's solver at runtime.
                os.environ["YOLO_AUTOINSTALL"] = "false"
                import_module("lap")
                self.model = import_module("ultralytics").YOLO(model_name)
            else:
                self.model = model
            if getattr(self.model, "task", None) != "detect" or self.model.names.get(0) != "person":
                raise PerceptionError("person model must be a detection model with class 0 named 'person'")
            self.deproject = deproject or import_module("pyrealsense2").rs2_deproject_pixel_to_point
        except Exception as error:
            self.close()
            if isinstance(error, PerceptionError):
                raise
            raise PerceptionError(f"failed to load person perception; install requirements-external-sensors.txt and check model weights: {error}") from error

    def process(self, frame: CapturedFrame) -> PerceivedFrame:
        started = time.perf_counter()
        np = self.np
        try:
            rgb = np.asanyarray(frame.color.get_data())
            depth = np.asanyarray(frame.depth.get_data())
            if rgb.ndim != 3 or rgb.shape[2] != 3 or depth.shape != rgb.shape[:2]:
                raise PerceptionError("aligned RGB/depth array dimensions do not match")
            bgr = np.ascontiguousarray(rgb[:, :, ::-1])
            results = self.model.track(
                bgr, persist=True, classes=[0], tracker="bytetrack.yaml",
                conf=self.confidence, verbose=False, show=False, save=False,
            )
            detections = extract_detections(results[0].boxes if results else None, self.confidence)
            humans, seen = [], set()
            missing_id = missing_depth = 0
            for index, detection in enumerate(detections):
                if detection.tracker_id is None:
                    missing_id += 1
                    continue
                track_id = f"d435:{self.session_id}:{detection.tracker_id}"
                if len(track_id) > 128:
                    raise PerceptionError("tracker ID exceeds canonical identifier length")
                if track_id in seen:
                    continue
                seen.add(track_id)
                result = DepthResult(failure="depth scale unavailable")
                if frame.depth_scale_m is not None:
                    result = estimate_position(
                        depth, detection.bbox, frame.depth_scale_m,
                        frame.color_intrinsics, self.deproject, self.camera_height_m,
                        exclude_boxes=tuple(d.bbox for i, d in enumerate(detections) if i != index),
                    )
                missing_depth += result.position_robot_m is None
                humans.append(TrackedPerson(track_id, detection.bbox, detection.confidence, result))
            depth_m = depth.astype(float) * (frame.depth_scale_m or 0.0)
            return PerceivedFrame(
                frame, tuple(sorted(humans, key=lambda human: human.track_id)),
                bgr, depth_m, (time.perf_counter()-started)*1000, missing_id, missing_depth,
            )
        except Exception as error:
            if isinstance(error, PerceptionError):
                raise
            raise PerceptionError(f"person inference failed: {error}") from error

    def close(self) -> None:
        # Ultralytics has no model close API. Release predictor/tracker/model
        # references; executor shutdown waits for the current inference first.
        if self.model is not None:
            self.model.predictor = None
            self.model = None
