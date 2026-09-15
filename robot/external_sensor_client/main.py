"""D435 capture -> validated empty-human observation -> HTTP -> display."""

from __future__ import annotations

import argparse
import asyncio
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

from robot.external_sensor_client.adapter import ExternalObservationAdapter
from robot.external_sensor_client.capture import (
    CaptureError, CapturedFrame, DeviceInfo, RealSenseCapture,
)
from robot.external_sensor_client.config import parse_args
from robot.external_sensor_client.display import display_response
from robot.navel_client.transport import ObservationTransport, TransportError


@dataclass
class Counters:
    frames_captured: int = 0
    observations_generated: int = 0
    observations_sent: int = 0
    queued_frames_replaced: int = 0
    transport_failures: int = 0


def print_device(info: DeviceInfo) -> None:
    print(f"device={info.name} serial={info.serial} "
          f"RGB={info.rgb.width}x{info.rgb.height}@{info.rgb.fps} "
          f"depth={info.depth.width}x{info.depth.height}@{info.depth.fps} depth_aligned_to=color")


def replace_queued(queue: asyncio.Queue[CapturedFrame], frame: CapturedFrame, counters: Counters) -> None:
    if queue.maxsize != 1:
        raise ValueError("latest-frame queue must have capacity one")
    counters.frames_captured += 1
    if queue.full():
        queue.get_nowait()
        queue.task_done()
        counters.queued_frames_replaced += 1
    queue.put_nowait(frame)


def capture_worker(capture: RealSenseCapture, loop: asyncio.AbstractEventLoop,
                   queue: asyncio.Queue[CapturedFrame], counters: Counters,
                   stop: threading.Event) -> None:
    # SDK start/read/stop all belong to this same thread. Queue/counters belong
    # to the event loop; never mutate asyncio.Queue from the worker thread.
    with capture:
        loop.call_soon_threadsafe(print_device, capture.device_info)
        while not stop.is_set():
            try:
                frame = capture.read()
            except CaptureError:
                if stop.is_set():
                    return
                raise
            loop.call_soon_threadsafe(replace_queued, queue, frame, counters)


async def send_observations(queue: asyncio.Queue[CapturedFrame], adapter: ExternalObservationAdapter,
                            transport: ObservationTransport, counters: Counters, *,
                            minimum_send_interval: float, force_decision: bool,
                            print_raw_json: bool) -> None:
    last_sent_at = 0.0
    while True:
        delay = minimum_send_interval - (time.monotonic() - last_sent_at)
        if delay > 0:
            await asyncio.sleep(delay)
        frame = await queue.get()
        queue.task_done()
        observation = adapter.convert(frame)
        counters.observations_generated += 1
        # Serialization of an already validated canonical model. Only this
        # coroutine sends, and it awaits each request before taking another.
        payload = observation.model_dump(mode="json")
        counters.observations_sent += 1
        try:
            response = await asyncio.to_thread(transport.send, payload, force_decision=force_decision)
        except TransportError as error:
            counters.transport_failures += 1
            print(f"observation={observation.observation_id} transport_error={error}; discarded")
        else:
            display_response(response, print_raw_json=print_raw_json)
        last_sent_at = time.monotonic()
        print("counters=" + str(asdict(counters)))


def camera_test(capture: RealSenseCapture, count: int, stop: threading.Event) -> None:
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


async def run(args: argparse.Namespace, *, capture_factory: Callable[..., RealSenseCapture] = RealSenseCapture,
              transport_factory: Callable[..., ObservationTransport] = ObservationTransport) -> None:
    stop = threading.Event()
    capture = capture_factory(args.realsense_serial)
    if args.camera_test:
        worker = asyncio.create_task(asyncio.to_thread(camera_test, capture, args.camera_test_frames, stop))
        try:
            await asyncio.shield(worker)
        finally:
            stop.set()
            await worker
        return
    adapter = ExternalObservationAdapter(args.adapter_id, stationary_rig=args.stationary_rig)
    transport = transport_factory(args.server, timeout_seconds=args.request_timeout)
    counters = Counters()
    queue: asyncio.Queue[CapturedFrame] = asyncio.Queue(maxsize=1)
    worker = asyncio.create_task(asyncio.to_thread(
        capture_worker, capture, asyncio.get_running_loop(), queue, counters, stop,
    ))
    sender = asyncio.create_task(send_observations(
        queue, adapter, transport, counters,
        minimum_send_interval=args.minimum_send_interval,
        force_decision=args.force_decision, print_raw_json=args.print_raw_json,
    ))
    try:
        done, _ = await asyncio.wait((worker, sender), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        stop.set()
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        try:
            # Do not cancel to_thread: its SDK read must finish before close.
            await worker
        finally:
            print("final_counters=" + str(asdict(counters)))


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("External sensor client stopped.")
    except (CaptureError, ValueError) as error:
        print(f"external_sensor_error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
