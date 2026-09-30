"""Collect Navel sensors concurrently and stream raw frames over HTTP."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Any

from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.decision_dispatch import DecisionDispatcher, DryRunHandlers
from robot.navel_client.transport import ObservationTransport, TransportError


logger = logging.getLogger(__name__)


@dataclass
class LatestLocomotion:
    packet: tuple[Any, float] | None = None

    def fresh_value(self, max_age_s: float) -> Any | None:
        if self.packet is None or time.monotonic() - self.packet[1] > max_age_s:
            return None
        return self.packet[0]


def _replace_queued(queue: asyncio.Queue[dict[str, Any]], observation: dict[str, Any]) -> None:
    if queue.full():
        queue.get_nowait()
        queue.task_done()
    queue.put_nowait(observation)


async def _collect_locomotion(robot: Any, latest: LatestLocomotion) -> None:
    while True:
        try:
            packet = await robot.next_locomotion(timeout=1.0)
        except TimeoutError:
            continue
        latest.packet = (packet, time.monotonic())


async def _collect_perception(
    robot: Any,
    adapter: NavelObservationAdapter,
    latest: LatestLocomotion,
    queue: asyncio.Queue[dict[str, Any]],
    *,
    max_locomotion_age_s: float,
) -> None:
    while True:
        try:
            perception = await robot.next_frame(timeout=1.0)
        except TimeoutError:
            continue
        observation = adapter.convert(perception, latest.fresh_value(max_locomotion_age_s))
        _replace_queued(queue, observation)


async def _send_observations(
    queue: asyncio.Queue[dict[str, Any]],
    transport: ObservationTransport,
    *,
    minimum_send_interval_s: float,
    print_only: bool,
    decision_dispatcher: DecisionDispatcher | None = None,
) -> None:
    last_sent_at = -math.inf
    while True:
        observation = await queue.get()
        try:
            delay = minimum_send_interval_s - (time.monotonic() - last_sent_at)
            if delay > 0:
                await asyncio.sleep(delay)
            while not queue.empty():
                queue.task_done()
                observation = queue.get_nowait()
            last_sent_at = time.monotonic()
            if print_only:
                print(json.dumps(observation, allow_nan=False, separators=(",", ":")), flush=True)
                continue
            try:
                response = await asyncio.to_thread(transport.send, observation)
            except TransportError as error:
                logger.warning("timestamp=%s transport_error=%s", observation["timestamp"], error)
                if decision_dispatcher is not None:
                    await decision_dispatcher.invalidate("transport_error")
                continue
            if 200 <= response.status_code < 300 and response.payload.get("accepted") is True:
                print(json.dumps(observation, allow_nan=False, indent=2), flush=True)
                logger.info("timestamp=%s people=%s accepted=true",
                            observation["timestamp"], len(observation["people"]))
                if decision_dispatcher is not None:
                    await decision_dispatcher.accept(response.payload, observation)
            else:
                logger.warning("timestamp=%s status=%s response=%s",
                               observation["timestamp"], response.status_code, response.payload)
                if decision_dispatcher is not None:
                    await decision_dispatcher.invalidate("observation_not_accepted")
        finally:
            queue.task_done()


async def collect_and_stream(robot: Any, args: argparse.Namespace) -> None:
    transport = ObservationTransport(args.server, timeout_seconds=args.request_timeout)
    adapter = NavelObservationAdapter()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
    latest = LatestLocomotion()
    decision_dispatcher = (DecisionDispatcher(DryRunHandlers(robot),
                           max_age_s=args.max_decision_age,
                           timeout_s=args.decision_timeout)
                           if args.decision_dry_run else None)
    tasks = [
        asyncio.create_task(_collect_locomotion(robot, latest)),
        asyncio.create_task(_collect_perception(
            robot, adapter, latest, queue, max_locomotion_age_s=args.max_locomotion_age,
        )),
        asyncio.create_task(_send_observations(
            queue, transport,
            minimum_send_interval_s=args.minimum_send_interval,
            print_only=args.print_only,
            decision_dispatcher=decision_dispatcher,
        )),
    ]
    if decision_dispatcher is not None:
        tasks.append(asyncio.create_task(decision_dispatcher.watchdog()))
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if decision_dispatcher is not None:
            await decision_dispatcher.invalidate("client_stopped")


async def run(args: argparse.Namespace) -> None:
    # Delay the robot-only dependency so --help and offline tests work anywhere.
    import navel

    async with navel.Robot() as robot:
        await collect_and_stream(robot, args)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream raw Navel sensors to a computer over HTTP.")
    parser.add_argument("--server", default=os.getenv("NAVEL_SENSOR_SERVER", "http://127.0.0.1:6060"),
                        help="Computer's HTTP base URL; use its LAN IP on Navel")
    parser.add_argument("--request-timeout", type=float, default=5.0)
    parser.add_argument("--minimum-send-interval", type=float, default=0.1,
                        help="Minimum seconds between POST starts (default: at most 10 Hz)")
    parser.add_argument("--max-locomotion-age", type=float, default=1.0,
                        help="Seconds before cached robot velocity/ranges become unavailable")
    parser.add_argument("--print-only", action="store_true", help="Print JSON frames without HTTP")
    parser.add_argument("--decision-dry-run", action="store_true",
                        help="Log validated policy handler calls without robot actions")
    parser.add_argument("--max-decision-age", type=float, default=1.0,
                        help="Maximum age of a source frame when its decision arrives (seconds)")
    parser.add_argument("--decision-timeout", type=float, default=2.0,
                        help="Expire a dry-run decision after this long without a valid response (seconds)")
    args = parser.parse_args(argv)
    if args.print_only and args.decision_dry_run:
        parser.error("--decision-dry-run requires HTTP; remove --print-only")
    for name in ("request_timeout", "max_locomotion_age", "minimum_send_interval",
                 "max_decision_age", "decision_timeout"):
        value = getattr(args, name)
        if not math.isfinite(value) or value < 0 or (name != "minimum_send_interval" and value == 0):
            parser.error("timeouts/maximum ages must be positive and finite; send interval may be zero")
    try:
        ObservationTransport(args.server, timeout_seconds=args.request_timeout)
    except ValueError as error:
        parser.error(str(error))
    return args


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.info("Navel sensor client stopped")
    except ModuleNotFoundError as error:
        if error.name != "navel":
            raise
        logger.error("Navel SDK is required to collect sensors; run this client on the robot.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
