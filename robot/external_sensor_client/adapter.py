"""Canonical observations for stationary capture and tracked torso positions."""

from __future__ import annotations

import math
import time

from app.domain.models import (
    CapabilityManifest, ClockDomain, ControllerStatus, CoordinateFrame,
    HumanObservation, NavigationTask, ObservationFrame, RobotObservation, Vector2, Vector3,
)
from robot.external_sensor_client.capture import CapturedFrame
from robot.external_sensor_client.perception import PerceivedFrame


class ExternalObservationAdapter:
    def __init__(self, adapter_id: str, *, stationary_rig: bool,
                 camera_height_m: float | None = None) -> None:
        if not stationary_rig:
            raise ValueError("--stationary-rig is required until odometry is implemented")
        if len(adapter_id.strip()) > 128:
            raise ValueError(
                "adapter ID must be at most 128 characters to leave room "
                "for observation ID suffixes"
            )
        self.capabilities = CapabilityManifest(
            adapter_id=adapter_id,
            robot_type="external-sensor-rig",
            available_fields=[
                "capture.rgb", "capture.depth", "robot.linear_velocity_mps",
                "robot.angular_velocity_radps", "robot.task", "robot.controller_status",
            ],
            unavailable_fields=[
                "humans.track_id", "humans.position_robot_m", "humans.distance_m",
                "humans.face_bbox", "humans.head_rpy_rad", "humans.gaze_unit",
                "humans.gaze_to_robot_score", "robot.free_space", "images",
            ],
            notes=[
                "Velocity is known to be zero because the external sensor rig is "
                "explicitly configured as stationary; no odometry measurement is available.",
                "Stage 1 captures RGB and aligned depth locally but sends no images "
                "or human perception. ROBOT_BASE declares the fixed rig frame; "
                "no spatial transform is applied.",
                "Host monotonic receive time triggers the observation; microsecond "
                "ties are advanced by one microsecond to preserve stream ordering.",
            ],
        )
        self._last_timestamp_us = -1
        self._counter = 0
        if camera_height_m is not None:
            if not math.isfinite(camera_height_m) or camera_height_m <= 0:
                raise ValueError("camera height must be positive and finite")
            supported = ["humans.track_id", "humans.detection_confidence", "humans.position_robot_m", "humans.distance_m"]
            self.capabilities = CapabilityManifest(
                adapter_id=self.capabilities.adapter_id,
                robot_type=self.capabilities.robot_type,
                available_fields=[*self.capabilities.available_fields, *supported],
                unavailable_fields=[
                    "images", "humans.identity_confidence", "humans.face_bbox",
                    "humans.head_rpy_rad", "humans.body_yaw_rad", "humans.gaze_unit",
                    "humans.gaze_to_robot_score", "humans.facial_expression",
                    "humans.speech_activity", "humans.group_observation_id",
                    "humans.uncertainty", "robot.free_space", "robot.pose", "robot.odometry",
                    "robot.moving_rig_velocity",
                ],
                notes=[self.capabilities.notes[0],
                       f"Level forward-facing camera, no roll/pitch/yaw correction or lateral offset; optical-centre height {camera_height_m} m.",
                       "ROBOT_BASE origin is the floor directly below the camera; X forward, Y left, Z up. Position is a central torso depth proxy; distance is hypot(X,Y).",
                       "Tracking IDs are stable only within a ByteTrack track in one session, not person identity. Capabilities describe support; individual depth can be unavailable.",
                       "Source and frame timestamps use host monotonic aligned-frame receive time; state_age_ms includes inference and send-queue delay. Images stay local."],
            )

    def convert(self, frame: CapturedFrame | PerceivedFrame) -> ObservationFrame:
        perceived = frame if isinstance(frame, PerceivedFrame) else None
        frame = perceived.capture if perceived is not None else frame
        if type(frame.timestamp_us) is not int or frame.timestamp_us < 0:
            raise ValueError("captured timestamp must be a non-negative integer in microseconds")
        if (
            not math.isfinite(frame.depth_valid_sample_ratio)
            or not 0 <= frame.depth_valid_sample_ratio <= 1
        ):
            raise ValueError("depth-validity sample ratio must be finite and in [0, 1]")
        timestamp_us = max(frame.timestamp_us, self._last_timestamp_us + 1)
        counter = self._counter + 1
        humans = []
        if perceived is not None:
            age_ms = max(0, time.monotonic_ns() // 1000 - frame.timestamp_us) // 1000
            for person in perceived.humans:
                position = person.depth.position_robot_m
                humans.append(HumanObservation(
                    track_id=person.track_id, observed=True,
                    source_timestamp_us=frame.timestamp_us, state_age_ms=age_ms,
                    position_robot_m=Vector3(x=position[0], y=position[1], z=position[2]) if position is not None else None,
                    distance_m=person.depth.distance_m if position is not None else None,
                    detection_confidence=person.confidence,
                    sensor_sources=["EXTERNAL", "DEPTH"] if position is not None else ["EXTERNAL"],
                ))
        observation = ObservationFrame(
            schema_version="1.0",
            observation_id=f"{self.capabilities.adapter_id}:{timestamp_us}:{counter:06d}",
            timestamp_us=timestamp_us,
            clock_domain=ClockDomain.MONOTONIC,
            coordinate_frame=CoordinateFrame.ROBOT_BASE,
            robot=RobotObservation(
                linear_velocity_mps=Vector2(x=0.0, y=0.0),
                angular_velocity_radps=0.0,
                task=NavigationTask.IDLE,
                controller_status=ControllerStatus.STOPPED,
            ),
            humans=sorted(humans, key=lambda human: human.track_id),
            images=[], capabilities=self.capabilities,
        )
        self._last_timestamp_us, self._counter = timestamp_us, counter
        return observation
