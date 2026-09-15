"""Stage 1 canonical observations triggered by capture, without perception."""

from __future__ import annotations

import math

from app.domain.models import (
    CapabilityManifest, ClockDomain, ControllerStatus, CoordinateFrame,
    NavigationTask, ObservationFrame, RobotObservation, Vector2,
)
from robot.external_sensor_client.capture import CapturedFrame


class ExternalObservationAdapter:
    def __init__(self, adapter_id: str, *, stationary_rig: bool) -> None:
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

    def convert(self, frame: CapturedFrame) -> ObservationFrame:
        if type(frame.timestamp_us) is not int or frame.timestamp_us < 0:
            raise ValueError("captured timestamp must be a non-negative integer in microseconds")
        if (
            not math.isfinite(frame.depth_valid_sample_ratio)
            or not 0 <= frame.depth_valid_sample_ratio <= 1
        ):
            raise ValueError("depth-validity sample ratio must be finite and in [0, 1]")
        timestamp_us = max(frame.timestamp_us, self._last_timestamp_us + 1)
        counter = self._counter + 1
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
            humans=[], images=[], capabilities=self.capabilities,
        )
        self._last_timestamp_us, self._counter = timestamp_us, counter
        return observation
