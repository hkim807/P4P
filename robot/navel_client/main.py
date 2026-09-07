"""Executable read-only Navel perception client.

This is the only module in the repository that imports the Navel SDK.  It only
calls receive methods and contains no actuator command path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import navel

from robot.navel_client.adapter import NavelAdapterConfig, NavelObservationAdapter
from robot.navel_client.behavior import BehaviorController
from robot.navel_client.transport import ObservationTransport, TransportError


@dataclass
class LatestLocomotion:
    value: Any | None = None


def _replace_queued(queue: asyncio.Queue[dict[str, Any]], observation: dict[str, Any]) -> None:
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(observation)


async def _collect_locomotion(robot: Any, latest: LatestLocomotion) -> None:
    while True:
        try:
            latest.value = await robot.next_locomotion(timeout=1.0)
        except TimeoutError:
            continue


async def _collect_perception(
    robot: Any,
    adapter: NavelObservationAdapter,
    latest: LatestLocomotion,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    while True:
        try:
            perception = await robot.next_frame(timeout=1.0)
        except TimeoutError:
            continue
        try:
            observation = adapter.convert(perception, latest.value)
        except ValueError as error:
            print(f"observation skipped: {error}")
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
    adapter = NavelObservationAdapter(
        NavelAdapterConfig(
            adapter_id=args.adapter_id,
            robot_task=args.robot_task,
            controller_status=args.controller_status,
            stationary_velocity_fallback=args.stationary_velocity_fallback,
            robot_base_coordinate_systems=tuple(args.robot_base_coordinate_system),
        )
    )
    transport = ObservationTransport(
        args.server,
        timeout_seconds=args.request_timeout,
    )
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
    latest = LatestLocomotion()

    async with navel.Robot() as robot:
        behavior_controller = BehaviorController(robot)
        tasks = [
            asyncio.create_task(_collect_locomotion(robot, latest)),
            asyncio.create_task(_collect_perception(robot, adapter, latest, queue)),
            asyncio.create_task(
                _send_observations(
                    queue,
                    transport,
                    behavior_controller,
                    minimum_send_interval_s=args.minimum_send_interval,
                    force_decision=args.force_decision,
                    print_only=args.print_only,
                )
            ),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read Navel observations and send canonical frames without robot control."
    )
    parser.add_argument(
        "--server",
        default=os.getenv("NAVEL_PIPELINE_SERVER", "http://127.0.0.1:6000"),
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
    parser.add_argument(
        "--robot-task",
        choices=("IDLE", "GUIDING", "APPROACHING", "INTERACTING", "PAUSED", "COMPLETE", "ERROR"),
        default="IDLE",
    )
    parser.add_argument(
        "--controller-status",
        choices=("IDLE", "ACTIVE", "STOPPED", "FAULT", "EMERGENCY_STOP"),
        default="STOPPED",
    )
    parser.add_argument(
        "--robot-base-coordinate-system",
        action="append",
        default=[],
        help="Verified SDK coordinate label equivalent to ROBOT_BASE; may be repeated",
    )
    args = parser.parse_args()
    if args.request_timeout <= 0 or args.minimum_send_interval < 0:
        parser.error("timeouts must be positive and send interval must be non-negative")
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
