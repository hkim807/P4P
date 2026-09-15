"""Independent capture, single-owner perception, and serial HTTP display client."""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import time
from dataclasses import dataclass, field, fields
from typing import Any, Callable

from robot.external_sensor_client.adapter import ExternalObservationAdapter
from robot.external_sensor_client.capture import CaptureError, CapturedFrame, DeviceInfo, RealSenseCapture
from robot.external_sensor_client.config import parse_args
from robot.external_sensor_client.display import display_response
from robot.external_sensor_client.perception import PerceivedFrame, PerceptionError, PersonPerception
from robot.external_sensor_client.visual import DisplayError, VisualDisplay
from robot.navel_client.transport import ObservationTransport, TransportError


@dataclass
class Counters:
    frames_captured: int = 0
    observations_generated: int = 0
    observations_sent: int = 0
    queued_frames_replaced: int = 0  # Capture boundary; preserved Stage 1 name.
    transport_failures: int = 0
    frames_processed: int = 0
    perception_frames_replaced: int = 0
    tracked_humans: int = 0
    detections_without_track_id: int = 0
    humans_without_valid_depth: int = 0
    inference_ms: float = 0.0
    _started: float = field(default_factory=time.perf_counter, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def add(self, name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + amount)

    def processed(self, frame: PerceivedFrame) -> None:
        with self._lock:
            self.frames_processed += 1
            self.tracked_humans = len(frame.humans)
            self.detections_without_track_id += frame.detections_without_track_id
            self.humans_without_valid_depth += frame.humans_without_valid_depth
            self.inference_ms = frame.inference_ms

    def snapshot(self) -> dict:
        with self._lock:
            result = {f.name: getattr(self, f.name) for f in fields(self) if not f.name.startswith("_")}
            elapsed = max(1e-9, time.perf_counter() - self._started)
            result.update(capture_fps=self.frames_captured/elapsed, perception_fps=self.frames_processed/elapsed)
            return result


class LatestQueue:
    """A thread-safe capacity-one slot; replacement never blocks a producer."""

    maxsize = 1

    def __init__(self):
        self._condition = threading.Condition()
        self._item = None

    def replace(self, item: Any) -> bool:
        with self._condition:
            replaced = self._item is not None
            self._item = item
            self._condition.notify()
            return replaced

    def take(self, stop: threading.Event):
        with self._condition:
            while self._item is None and not stop.is_set():
                self._condition.wait(0.05)
            if stop.is_set():
                return None
            item, self._item = self._item, None
            return item

    def clear(self):
        with self._condition:
            self._item = None
            self._condition.notify_all()


class LiveStatus:
    def __init__(self):
        self._lock = threading.Lock()
        self.frame = None
        self.action = None
        self.transport_error = None

    def update(self, **values):
        with self._lock:
            for key, value in values.items():
                setattr(self, key, value)

    def snapshot(self):
        with self._lock:
            return self.frame, self.action, self.transport_error


def print_device(info: DeviceInfo) -> None:
    print(f"device={info.name} serial={info.serial} "
          f"RGB={info.rgb.width}x{info.rgb.height}@{info.rgb.fps} "
          f"depth={info.depth.width}x{info.depth.height}@{info.depth.fps} depth_aligned_to=color")


def replace_queued(queue: asyncio.Queue[CapturedFrame], frame: CapturedFrame, counters: Counters) -> None:
    """Preserved asyncio helper for dependency-free Stage 1 sender integrations."""
    if queue.maxsize != 1:
        raise ValueError("latest-frame queue must have capacity one")
    counters.add("frames_captured")
    if queue.full():
        queue.get_nowait()
        queue.task_done()
        counters.add("queued_frames_replaced")
    queue.put_nowait(frame)


def capture_worker(capture, queue: LatestQueue, counters: Counters, stop: threading.Event) -> None:
    with capture:
        print_device(capture.device_info)
        while not stop.is_set():
            try:
                frame = capture.read()
            except CaptureError:
                if stop.is_set():
                    return
                raise
            counters.add("frames_captured")
            if queue.replace(frame):
                counters.add("queued_frames_replaced")


def perception_worker(queue: LatestQueue, output: LatestQueue, counters: Counters,
                      status: LiveStatus, stop: threading.Event, factory: Callable,
                      args: argparse.Namespace) -> None:
    processor = None
    try:
        processor = factory(args.person_model, args.person_confidence, args.camera_height_m)
        while not stop.is_set():
            captured = queue.take(stop)
            if captured is None:
                break
            perceived = processor.process(captured)
            counters.processed(perceived)
            status.update(frame=perceived)
            if output.replace(perceived):
                counters.add("perception_frames_replaced")
    finally:
        if processor is not None:
            processor.close()


async def send_observations(queue, adapter: ExternalObservationAdapter,
                            transport: ObservationTransport, counters: Counters, *,
                            minimum_send_interval: float, force_decision: bool,
                            print_raw_json: bool, stop: threading.Event | None = None,
                            status: LiveStatus | None = None) -> None:
    stop = stop or threading.Event()
    last_sent_at = 0.0
    while not stop.is_set():
        delay = minimum_send_interval - (time.monotonic() - last_sent_at)
        if delay > 0:
            await asyncio.sleep(delay)
        if isinstance(queue, LatestQueue):
            frame = await asyncio.to_thread(queue.take, stop)
            if frame is None:
                return
        else:
            frame = await queue.get()
            queue.task_done()
        observation = adapter.convert(frame)
        counters.add("observations_generated")
        payload = observation.model_dump(mode="json")
        counters.add("observations_sent")
        try:
            response = await asyncio.to_thread(transport.send, payload, force_decision=force_decision)
        except TransportError as error:
            counters.add("transport_failures")
            if status is not None:
                status.update(transport_error=str(error))
            print(f"observation={observation.observation_id} transport_error={error}; discarded")
        else:
            display_response(response, print_raw_json=print_raw_json)
            if status is not None:
                intent = response.payload.get("behavior_intent")
                values = {"transport_error": None}
                if isinstance(intent, dict):
                    values["action"] = intent.get("action")
                status.update(**values)
        last_sent_at = time.monotonic()
        print("counters=" + str(counters.snapshot()))


def camera_test(capture, count: int, stop: threading.Event, display_factory=None) -> None:
    display = None
    try:
        if display_factory is not None:
            display = display_factory().__enter__()
        with capture:
            print_device(capture.device_info)
            for index in range(count):
                if stop.is_set():
                    break
                try:
                    frame = capture.read()
                except CaptureError:
                    if stop.is_set():
                        break
                    raise
                print(f"frames_captured={index + 1} frame_number={frame.frame_number} "
                      f"timestamp_us={frame.timestamp_us} depth_valid_sample_ratio={frame.depth_valid_sample_ratio:.3f}")
                if display is not None and display.show(frame):
                    stop.set()
                    break
    finally:
        if display is not None:
            display.close()


def display_worker(status: LiveStatus, counters: Counters, stop: threading.Event, factory):
    with factory() as display:
        while not stop.is_set():
            frame, action, error = status.snapshot()
            stats = counters.snapshot()
            text = (f"capture={stats['capture_fps']:.1f}FPS perception={stats['perception_fps']:.1f}FPS "
                    f"infer={stats['inference_ms']:.0f}ms humans={stats['tracked_humans']} "
                    f"action={action or '-'} error={error or '-'}")
            quit_requested = display.show(frame, text) if frame is not None else display.poll_quit()
            if quit_requested:
                stop.set()
                return
            stop.wait(0.03)


async def run(args: argparse.Namespace, *, capture_factory=RealSenseCapture,
              transport_factory=ObservationTransport, perception_factory=PersonPerception,
              display_factory=VisualDisplay) -> None:
    stop = threading.Event()
    capture = capture_factory(args.realsense_serial)
    if args.camera_test:
        worker = asyncio.create_task(asyncio.to_thread(
            camera_test, capture, args.camera_test_frames, stop,
            display_factory if args.display else None,
        ))
        try:
            await asyncio.shield(worker)
        finally:
            stop.set()
            await worker
        return
    adapter = ExternalObservationAdapter(
        args.adapter_id, stationary_rig=args.stationary_rig, camera_height_m=args.camera_height_m,
    )
    print(f"ROBOT_BASE: X forward, Y left, Z up; floor origin below camera; "
          f"optical-centre height={args.camera_height_m}m. Camera level/forward, no lateral offset or roll/pitch/yaw correction.")
    transport = transport_factory(args.server, timeout_seconds=args.request_timeout)
    counters, status = Counters(), LiveStatus()
    captured, perceived = LatestQueue(), LatestQueue()
    workers = [
        asyncio.create_task(asyncio.to_thread(capture_worker, capture, captured, counters, stop)),
        asyncio.create_task(asyncio.to_thread(perception_worker, captured, perceived, counters, status, stop, perception_factory, args)),
    ]
    if args.display:
        workers.append(asyncio.create_task(asyncio.to_thread(display_worker, status, counters, stop, display_factory)))
    sender = asyncio.create_task(send_observations(
        perceived, adapter, transport, counters, minimum_send_interval=args.minimum_send_interval,
        force_decision=args.force_decision, print_raw_json=args.print_raw_json, stop=stop, status=status,
    ))
    try:
        done, _ = await asyncio.wait((*workers, sender), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        unwinding = sys.exc_info()[0] is not None
        stop.set()
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        # Never cancel a to_thread worker: wait for SDK/inference to finish and
        # release its resources on the owner thread. HTTP is serial throughout.
        results = await asyncio.gather(*workers, return_exceptions=True)
        captured.clear()
        perceived.clear()
        status.update(frame=None)
        print("final_counters=" + str(counters.snapshot()))
        if not unwinding:
            for result in results:
                if isinstance(result, BaseException):
                    raise result


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("External sensor client stopped.")
    except (CaptureError, PerceptionError, DisplayError, ValueError) as error:
        print(f"external_sensor_error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
