"""Lazy RealSense RGB/depth capture. No hardware is opened on import."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from importlib import import_module
from typing import Any


class CaptureError(RuntimeError):
    """RealSense dependency, device, stream, or frame failure."""


@dataclass(frozen=True)
class StreamInfo:
    width: int
    height: int
    fps: int


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    serial: str
    rgb: StreamInfo
    depth: StreamInfo


@dataclass(frozen=True)
class CapturedFrame:
    timestamp_us: int
    color: Any
    depth: Any
    frame_number: int | None
    depth_valid_sample_ratio: float
    depth_scale_m: float | None = None
    color_intrinsics: Any = None


class RealSenseCapture:
    """Own a D435-family pipeline on one worker thread, including its cleanup.

    Streams are RGB8/Z16, 640x480 at 30 FPS. Returned depth is aligned to
    colour. Validity is the fraction of positive finite distances on a sparse
    grid (up to 16x12 samples), not a full-image quality measurement.
    """

    def __init__(self, serial: str | None = None) -> None:
        self.serial = serial
        self.device_info: DeviceInfo | None = None
        self._pipeline: Any = None
        self._align: Any = None
        self._last_timestamp_us = -1
        self._depth_scale_m: float | None = None

    def start(self) -> DeviceInfo:
        if self._pipeline is not None:
            raise CaptureError("RealSense capture is already started")
        try:
            rs = import_module("pyrealsense2")
        except (ImportError, OSError) as error:
            raise CaptureError(
                "pyrealsense2 is unavailable; install requirements-external-sensors.txt "
                "on the Linux capture desktop"
            ) from error
        try:
            context = rs.context()
            device = next((
                device for device in context.query_devices()
                if "D435" in device.get_info(rs.camera_info.name).upper()
                and (
                    self.serial is None
                    or device.get_info(rs.camera_info.serial_number) == self.serial
                )
            ), None)
            if device is None:
                selection = f" with serial {self.serial}" if self.serial else ""
                raise CaptureError(f"no compatible D435-family device found{selection}")
            serial = device.get_info(rs.camera_info.serial_number)
            self._pipeline = rs.pipeline()
            config = rs.config()
            config.enable_device(serial)
            config.enable_stream(rs.stream.color, 640, 480, rs.format.rgb8, 30)
            config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
            profile = self._pipeline.start(config)
            self._align = rs.align(rs.stream.color)
            active_device = profile.get_device()
            self._depth_scale_m = active_device.first_depth_sensor().get_depth_scale()
            if not math.isfinite(self._depth_scale_m) or self._depth_scale_m <= 0:
                raise CaptureError("device depth scale must be positive and finite")

            def stream_info(stream: Any) -> StreamInfo:
                video = profile.get_stream(stream).as_video_stream_profile()
                return StreamInfo(video.width(), video.height(), video.fps())

            self.device_info = DeviceInfo(
                active_device.get_info(rs.camera_info.name),
                active_device.get_info(rs.camera_info.serial_number),
                stream_info(rs.stream.color), stream_info(rs.stream.depth),
            )
            return self.device_info
        except BaseException as error:
            self.close()
            if isinstance(error, (CaptureError, KeyboardInterrupt, SystemExit)):
                raise
            raise CaptureError(f"failed to discover/start D435 RGB/depth streams: {error}") from error

    def read(self) -> CapturedFrame:
        if self._pipeline is None:
            raise CaptureError("RealSense capture is not started")
        try:
            try:
                frames = self._pipeline.wait_for_frames(timeout_ms=1000)
            except RuntimeError as error:
                raise CaptureError(
                    f"RealSense frame timeout or receive failure (1000 ms): {error}"
                ) from error
            aligned = self._align.process(frames)
            timestamp_us = max(time.monotonic_ns() // 1000, self._last_timestamp_us + 1)
            self._last_timestamp_us = timestamp_us
            color, depth = aligned.get_color_frame(), aligned.get_depth_frame()
            if (
                not color or not depth
                or color.get_width() <= 0 or color.get_height() <= 0
                or depth.get_width() <= 0 or depth.get_height() <= 0
            ):
                raise CaptureError("RealSense returned invalid RGB/depth frames")
            valid = total = 0
            width, height = depth.get_width(), depth.get_height()
            columns, rows = min(16, width), min(12, height)
            for row in range(rows):
                y = row * height // rows
                for column in range(columns):
                    x = column * width // columns
                    distance = depth.get_distance(x, y)
                    valid += math.isfinite(distance) and distance > 0
                    total += 1
            number = color.get_frame_number() if hasattr(color, "get_frame_number") else None
            intrinsics = color.get_profile().as_video_stream_profile().get_intrinsics()
            return CapturedFrame(
                timestamp_us, color, depth, number, valid / total,
                self._depth_scale_m, intrinsics,
            )
        except BaseException as error:
            self.close()
            if isinstance(error, (CaptureError, KeyboardInterrupt, SystemExit)):
                raise
            raise CaptureError(f"failed to read/align D435 frames: {error}") from error

    def close(self) -> None:
        pipeline, self._pipeline = self._pipeline, None
        self._align = None
        self._depth_scale_m = None
        if pipeline is not None:
            try:
                pipeline.stop()
            except RuntimeError:
                # start() may have failed before streams became active.
                pass

    def __enter__(self) -> RealSenseCapture:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
