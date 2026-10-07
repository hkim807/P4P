"""Optional Navel RGB camera capture with independent failure reporting."""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any
from uuid import uuid4

from robot.navel_client.transport import ObservationTransport, TransportError


logger = logging.getLogger(__name__)
MAX_RGB_BYTES = 24 * 1024 * 1024


class CameraCapture:
    def __init__(self, transport: ObservationTransport, *, session_id: str | None = None,
                 queue_size: int = 8) -> None:
        self.transport = transport
        self.session_id = session_id or uuid4().hex
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self.sequences = {"head": 0, "chest": 0}

    async def collect(self, camera: str, camera_type: Any, interval_s: float) -> None:
        """Keep the camera context and all synchronous calls on one worker thread."""
        loop = asyncio.get_running_loop()
        stop = threading.Event()
        worker = asyncio.create_task(asyncio.to_thread(
            self._collect_sync, camera, camera_type, interval_s, loop, stop))
        try:
            await asyncio.shield(worker)
        finally:
            stop.set()
            await worker

    def _enqueue(self, loop: asyncio.AbstractEventLoop, record: dict[str, Any]) -> None:
        future = asyncio.run_coroutine_threadsafe(self.queue.put(record), loop)
        try:
            future.result(timeout=5)
        except FutureTimeoutError as error:
            future.cancel()
            raise RuntimeError("camera capture queue blocked") from error

    def _record(self, loop: asyncio.AbstractEventLoop, camera: str, event: str, **fields: Any) -> None:
        sequence = self.sequences[camera] + 1
        record = {
            "capture_version": 1,
            "session_id": self.session_id,
            "camera": camera,
            "sequence": sequence,
            "event": event,
            "received_monotonic_us": time.monotonic_ns() // 1000,
            "received_unix_us": time.time_ns() // 1000,
            **fields,
        }
        self._enqueue(loop, record)
        self.sequences[camera] = sequence

    def _collect_sync(self, camera: str, camera_type: Any, interval_s: float,
                      loop: asyncio.AbstractEventLoop, stop: threading.Event) -> None:
        if camera_type is None:
            self._unavailable(loop, camera, "camera class is absent from installed Navel SDK")
            return
        try:
            with camera_type() as device:
                get_frame = getattr(device, "get_frame", None)
                if not callable(get_frame):
                    raise NotImplementedError("get_frame is absent or not callable")
                timeouts = 0
                while not stop.is_set():
                    try:
                        frame = get_frame(timeout_ms=500)
                    except TimeoutError:
                        timeouts += 1
                        if timeouts >= 10:
                            raise RuntimeError("get_frame timed out 10 consecutive times")
                        continue
                    timeouts = 0
                    received_monotonic_us = time.monotonic_ns() // 1000
                    received_unix_us = time.time_ns() // 1000
                    width, height, timestamp_us, rgb = self._frame_data(frame)
                    sequence = self.sequences[camera] + 1
                    record = {
                        "capture_version": 1,
                        "session_id": self.session_id,
                        "camera": camera,
                        "sequence": sequence,
                        "event": "frame",
                        "received_monotonic_us": received_monotonic_us,
                        "received_unix_us": received_unix_us,
                        "timestamp_us": timestamp_us,
                        "width": width,
                        "height": height,
                        "rgb_b64": base64.b64encode(rgb).decode("ascii"),
                    }
                    self._enqueue(loop, record)
                    self.sequences[camera] = sequence
                    stop.wait(interval_s)
        except Exception as error:
            if not stop.is_set():
                try:
                    self._unavailable(loop, camera, f"{type(error).__name__}: {error}")
                except RuntimeError:
                    logger.exception("camera=%s could not report unavailability", camera)

    def _unavailable(self, loop: asyncio.AbstractEventLoop, camera: str, reason: str) -> None:
        logger.warning("camera=%s unavailable: %s", camera, reason)
        self._record(loop, camera, "unavailable", reason=reason[:500])

    @staticmethod
    def _frame_data(frame: Any) -> tuple[int, int, int, bytes]:
        width, height, timestamp_us = (getattr(frame, name, None)
                                       for name in ("width", "height", "timestamp_us"))
        if (type(width) is not int or type(height) is not int or type(timestamp_us) is not int
                or width <= 0 or height <= 0 or timestamp_us < 0
                or width * height * 3 > MAX_RGB_BYTES):
            raise ValueError("invalid camera dimensions or timestamp")
        data = getattr(frame, "data", None)
        if (getattr(data, "shape", None) != (height, width, 3)
                or str(getattr(data, "dtype", None)) != "uint8"
                or not callable(getattr(data, "tobytes", None))):
            raise ValueError("camera frame must contain an H×W×3 uint8 RGB array")
        rgb = data.tobytes()
        if len(rgb) != width * height * 3:
            raise ValueError("camera RGB byte count does not match dimensions")
        return width, height, timestamp_us, rgb

    async def send(self) -> None:
        while True:
            record = await self.queue.get()
            try:
                while True:
                    try:
                        response = await asyncio.to_thread(self.transport.send_camera_frame, record)
                    except TransportError as error:
                        logger.warning("camera_transport_error=%s; retrying", error)
                        await asyncio.sleep(0.5)
                        continue
                    if (response.status_code == 200 and response.payload.get("accepted") is True
                            and response.payload.get("camera") == record["camera"]
                            and response.payload.get("sequence") == record["sequence"]):
                        break
                    if 500 <= response.status_code < 600:
                        logger.warning("camera_server_error=%s; retrying", response.status_code)
                        await asyncio.sleep(0.5)
                        continue
                    raise RuntimeError(f"camera capture rejected: {response.status_code} {response.payload}")
            finally:
                self.queue.task_done()
