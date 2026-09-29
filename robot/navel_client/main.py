"""Shared Navel observation client; motion requires explicit --execute."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import math
import signal
import time
from typing import Any

import navel

from robot.navel_client.adapter import NavelAdapterConfig, NavelObservationAdapter
from robot.navel_client.behavior import BehaviorController
from robot.navel_client.navel_runtime import Config, Runtime
from robot.navel_client.transport import ObservationTransport, TransportError


def _replace_queued(queue: asyncio.Queue[dict[str, Any]], observation: dict[str, Any]) -> None:
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(observation)


async def _collect_locomotion(robot: Any, runtime: Runtime) -> None:
    while True:
        try:
            packet = await robot.next_locomotion(timeout=.3)
            runtime.ingest_odometry(packet)
        except TimeoutError:
            continue
        except (ValueError, RuntimeError) as error:
            runtime.errors['odometry'] = str(error)


async def _collect_perception(robot, adapter, runtime, queue, behavior_controller) -> None:
    while True:
        try:
            frame = await robot.next_frame(timeout=.3)
        except TimeoutError:
            continue
        try:
            if not runtime.ingest_perception(frame):
                continue
            observation = adapter.convert(frame, runtime.raw_locomotion)
            observation['robot'].update(behavior_controller.robot_context())
            runtime.remember_observation(observation)
        except (ValueError, RuntimeError) as error:
            runtime.errors['perception'] = str(error)
            logging.getLogger(__name__).debug('observation skipped: %s', error)
            continue
        _replace_queued(queue, observation)


async def _send_observations(
    queue: asyncio.Queue[dict[str, Any]],
    transport: ObservationTransport,
    behavior_controller: BehaviorController,
    *,
    minimum_send_interval_s: float,
    force_decision: bool,
    print_only: bool,
) -> None:
    last_sent_at = 0.0
    while True:
        observation = await queue.get()
        delay = minimum_send_interval_s - (time.monotonic() - last_sent_at)
        if delay > 0:
            await asyncio.sleep(delay)

        while not queue.empty():
            try:
                observation = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        observation_id = observation["observation_id"]
        human_count = len(observation["humans"])
        if print_only:
            print(
                f"observation={observation_id} humans={human_count} print_only=true "
                f"json={json.dumps(observation, sort_keys=True, separators=(',', ':'))}"
            )
            last_sent_at = time.monotonic()
            continue

        try:
            response = await asyncio.to_thread(
                transport.send,
                observation,
                force_decision=force_decision,
            )
        except TransportError as error:
            print(f"observation={observation_id} humans={human_count} transport_error={error}")
            last_sent_at = time.monotonic()
            continue

        payload = response.payload
        accepted = bool(payload.get("accepted", False))
        triggered = bool(payload.get("decision_triggered", False))
        triggers = payload.get("triggers", [])
        print(
            f"observation={observation_id} humans={human_count} status={response.status_code} "
            f"accepted={str(accepted).lower()} decision_triggered={str(triggered).lower()} "
            f"triggers={','.join(str(item) for item in triggers) or '-'}"
        )
        behavior_controller.handle_response(payload)
        if payload.get("error") is not None:
            print(f"server_error: {payload['error']}")
        last_sent_at = time.monotonic()


async def run(args: argparse.Namespace) -> None:
    transport = ObservationTransport(args.server, timeout_seconds=args.request_timeout)
    queue = asyncio.Queue(maxsize=1)
    shutdown_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for sig in signals:
        loop.add_signal_handler(sig, shutdown_requested.set)
    try:
        async with navel.Robot() as robot:
            runtime = Runtime(robot, Config(execute=args.execute, head_x=args.head_x,
                head_y=args.head_y, frame_yaw_deg=args.frame_yaw_deg))
            adapter = NavelObservationAdapter(NavelAdapterConfig(adapter_id=args.adapter_id,
                stationary_velocity_fallback=args.stationary_velocity_fallback), runtime=runtime)
            controller = BehaviorController(runtime=runtime)
            readers = [asyncio.create_task(_collect_locomotion(robot, runtime)),
                       asyncio.create_task(_collect_perception(robot, adapter, runtime, queue, controller))]
            sender = asyncio.create_task(_send_observations(queue, transport, controller,
                minimum_send_interval_s=args.minimum_send_interval, force_decision=args.force_decision,
                print_only=args.print_only))
            stop_waiter = asyncio.create_task(shutdown_requested.wait())
            try:
                done, _ = await asyncio.wait([*readers, sender, stop_waiter],
                                             return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                # Stop admitting responses first. Readers stay alive through movement cleanup.
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
                await controller.shutdown()
                for task in [*readers, stop_waiter]:
                    task.cancel()
                await asyncio.gather(*readers, stop_waiter, return_exceptions=True)
                if runtime.stop_failure:
                    raise RuntimeError(runtime.stop_failure)
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send Navel observations and execute bounded approach intents only with --execute."
    )
    parser.add_argument(
        "--server",
        default=os.getenv("NAVEL_PIPELINE_SERVER", "http://127.0.0.1:6060"),
        help="Central server base URL",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=float(os.getenv("NAVEL_PIPELINE_TIMEOUT_SECONDS", "35")),
    )
    parser.add_argument(
        "--adapter-id",
        default=os.getenv("NAVEL_ADAPTER_ID", "navel-readonly-v1"),
        help="Unique source ID used to isolate server-side temporal state",
    )
    parser.add_argument(
        "--minimum-send-interval",
        type=float,
        default=float(os.getenv("NAVEL_MINIMUM_SEND_INTERVAL_SECONDS", "0.1")),
    )
    parser.add_argument("--stationary-velocity-fallback", action="store_true")
    parser.add_argument("--force-decision", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument('--execute', action='store_true', help='Enable bounded approach movement')
    parser.add_argument('--head-x', type=float, default=0.)
    parser.add_argument('--head-y', type=float, default=0.)
    parser.add_argument('--frame-yaw-deg', type=float, default=0.)
    args = parser.parse_args(argv)
    values = (args.request_timeout, args.minimum_send_interval, args.head_x, args.head_y, args.frame_yaw_deg)
    if not all(math.isfinite(v) for v in values):
        parser.error('Numeric arguments must be finite')
    if args.request_timeout <= 0 or args.minimum_send_interval < 0:
        parser.error('Timeouts must be positive and send interval non-negative')
    if max(abs(args.head_x), abs(args.head_y)) > .5 or abs(args.frame_yaw_deg) > 45:
        parser.error('Head offsets must be within .5 m; frame yaw within 45 degrees')
    if args.execute and (args.stationary_velocity_fallback or args.print_only):
        parser.error('--execute cannot be combined with stationary fallback or --print-only')
    return args


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        print("Navel observation client stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
