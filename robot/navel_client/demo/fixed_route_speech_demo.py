"""Navel demo that announces policy decisions while following a fixed route."""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
from dataclasses import dataclass
from typing import Any

from robot.navel_client.adapter import NavelAdapterConfig, NavelObservationAdapter
from robot.navel_client.demo.intent_announcer import IntentAnnouncer
from robot.navel_client.main import (
    LatestLocomotion,
    _collect_locomotion,
    _collect_perception,
    _send_observations,
)
from robot.navel_client.transport import ObservationTransport


@dataclass
class ActiveMovement:
    task: Any | None = None


async def _run_straight_test(
    robot: Any,
    active: ActiveMovement,
    *,
    distance_m: float,
    segment_distance_m: float,
    speed_mps: float,
) -> None:
    remaining = distance_m
    segment_number = 0
    while remaining > 1e-9:
        segment = min(remaining, segment_distance_m)
        segment_number += 1
        print(
            f"route=straight_test segment={segment_number} "
            f"distance_m={segment:.3f} remaining_m={remaining:.3f}"
        )
        movement = robot.move_base(segment, speed=speed_mps)
        active.task = movement
        try:
            await movement
        finally:
            if active.task is movement:
                active.task = None
        remaining = max(0.0, remaining - segment)
    print("route=straight_test completed=true")


async def _cancel_active_movement(active: ActiveMovement) -> None:
    movement = active.task
    if movement is None or movement.done():
        return
    movement.cancel()
    await asyncio.gather(movement, return_exceptions=True)
    active.task = None


async def run(args: argparse.Namespace) -> None:
    import navel

    adapter = NavelObservationAdapter(
        NavelAdapterConfig(
            adapter_id=args.adapter_id,
            robot_task=args.robot_task,
            controller_status=args.controller_status,
            stationary_velocity_fallback=args.stationary_velocity_fallback,
            robot_base_coordinate_systems=tuple(args.robot_base_coordinate_system),
        )
    )
    transport = ObservationTransport(args.server, timeout_seconds=args.request_timeout)
    observation_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
    latest = LatestLocomotion()
    active_movement = ActiveMovement()

    print(
        "Navel fixed-route speech demo: "
        f"server={args.server} base_enabled={str(args.enable_base).lower()} "
        f"speech_enabled={str(args.enable_speech).lower()} "
        f"distance_m={args.distance_m:.3f} "
        f"segment_distance_m={args.segment_distance_m:.3f} "
        f"speed_mps={args.speed_mps:.3f}"
    )
    if args.enable_base:
        print("WARNING: BASE MOVEMENT ENABLED — keep the route clear and supervise Navel.")

    async with navel.Robot() as robot:
        announcer = IntentAnnouncer(
            robot,
            speech_enabled=args.enable_speech,
            cooldown_s=args.announcement_cooldown_s,
        )
        background = [
            asyncio.create_task(_collect_locomotion(robot, latest)),
            asyncio.create_task(
                _collect_perception(robot, adapter, latest, observation_queue)
            ),
            asyncio.create_task(
                _send_observations(
                    observation_queue,
                    transport,
                    announcer,
                    minimum_send_interval_s=args.minimum_send_interval,
                    force_decision=args.force_decision,
                    print_only=False,
                    handle_non_success_responses=False,
                )
            ),
            asyncio.create_task(announcer.run()),
        ]
        route_task: asyncio.Task[None] | None = None
        if args.enable_base:
            route_task = asyncio.create_task(
                _run_straight_test(
                    robot,
                    active_movement,
                    distance_m=args.distance_m,
                    segment_distance_m=args.segment_distance_m,
                    speed_mps=args.speed_mps,
                )
            )
        tasks = [*background, *([route_task] if route_task is not None else [])]
        try:
            if route_task is None:
                await asyncio.gather(*tasks)
            else:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                failures = [task.exception() for task in done if not task.cancelled()]
                failure = next((error for error in failures if error is not None), None)
                if failure is not None:
                    raise failure
                if route_task not in done:
                    raise RuntimeError("a background demo task stopped unexpectedly")
        finally:
            await _cancel_active_movement(active_movement)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Announce Navel behavior decisions during an optional fixed straight route."
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
        default=os.getenv("NAVEL_ADAPTER_ID", "navel-fixed-route-demo-v1"),
    )
    parser.add_argument(
        "--minimum-send-interval",
        type=float,
        default=float(os.getenv("NAVEL_MINIMUM_SEND_INTERVAL_SECONDS", "0.1")),
    )
    parser.add_argument("--stationary-velocity-fallback", action="store_true")
    parser.add_argument("--force-decision", action="store_true")
    parser.add_argument("--enable-speech", action="store_true")
    parser.add_argument("--enable-base", action="store_true")
    parser.add_argument("--route", choices=("straight_test",), default="straight_test")
    parser.add_argument("--distance-m", type=float, default=1.0)
    parser.add_argument("--segment-distance-m", type=float, default=1.0)
    parser.add_argument("--speed-mps", type=float, default=0.1)
    parser.add_argument("--announcement-cooldown-s", type=float, default=8.0)
    parser.add_argument(
        "--robot-task",
        choices=(
            "IDLE",
            "GUIDING",
            "APPROACHING",
            "INTERACTING",
            "PAUSED",
            "COMPLETE",
            "ERROR",
        ),
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
    args = parser.parse_args(argv)
    positive = {
        "request timeout": args.request_timeout,
        "distance": args.distance_m,
        "segment distance": args.segment_distance_m,
        "speed": args.speed_mps,
        "announcement cooldown": args.announcement_cooldown_s,
    }
    for name, value in positive.items():
        if not math.isfinite(value) or value <= 0:
            parser.error(f"{name} must be finite and positive")
    if not math.isfinite(args.minimum_send_interval) or args.minimum_send_interval < 0:
        parser.error("minimum send interval must be finite and non-negative")
    if args.segment_distance_m > 1.0:
        parser.error("segment distance must not exceed 1 m")
    if args.enable_base and args.stationary_velocity_fallback:
        parser.error("stationary velocity fallback cannot be used with base movement")
    return args


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        print("Navel fixed-route speech demo stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
