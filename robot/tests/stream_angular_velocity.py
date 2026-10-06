"""Print raw SDK angular velocity; run directly on Navel with Python 3.10+.

    python3 robot/tests/stream_angular_velocity.py --duration 30

This file needs only the robot's Navel SDK and the standard library. It does
not import the HTTP client or frame adapter, and issues no movement commands.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from dataclasses import dataclass
from typing import Any


MISSING = object()
VELOCITY_FIELDS = (
    "linear_x", "linear_y", "linear_z", "angular_x", "angular_y", "angular_z",
)


def display(value: Any) -> str:
    return "<missing>" if value is MISSING else repr(value)


@dataclass
class Summary:
    packets: int = 0
    timeouts: int = 0
    missing: int = 0
    invalid: int = 0
    zero: int = 0
    nonzero: int = 0
    minimum: float | None = None
    maximum: float | None = None

    def observe(self, value: Any) -> str:
        if value is MISSING or value is None:
            self.missing += 1
            return "unavailable"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.invalid += 1
            return "invalid"
        try:
            number = float(value)
        except OverflowError:
            number = math.inf
        if not math.isfinite(number):
            self.invalid += 1
            return "invalid"
        self.minimum = number if self.minimum is None else min(self.minimum, number)
        self.maximum = number if self.maximum is None else max(self.maximum, number)
        if number == 0:
            self.zero += 1
            return "zero"
        self.nonzero += 1
        return "nonzero"

    def report(self) -> None:
        print(
            f"\nSummary: packets={self.packets} timeouts={self.timeouts} "
            f"zero={self.zero} nonzero={self.nonzero} "
            f"unavailable={self.missing} invalid={self.invalid}\n"
            f"angular_z minimum={self.minimum!r} maximum={self.maximum!r}",
            flush=True,
        )
        if self.nonzero:
            print("Observed nonzero angular_z directly from the SDK.", flush=True)
        elif self.zero:
            print(
                "All finite angular_z samples were zero. If the base was turning "
                "during this run, check the SDK/odometry source.",
                flush=True,
            )
        else:
            print("No finite angular_z samples were received.", flush=True)


async def stream(robot: Any, *, duration: float, timeout: float) -> None:
    started = time.monotonic()
    summary = Summary()
    try:
        while True:
            remaining = duration - (time.monotonic() - started)
            if duration > 0 and remaining <= 0:
                break
            receive_timeout = min(timeout, remaining) if duration > 0 else timeout
            try:
                packet = await robot.next_locomotion(timeout=receive_timeout)
            except TimeoutError:
                summary.timeouts += 1
                print("Receive timeout: no new locomotion packet.", flush=True)
                continue

            summary.packets += 1
            odometry = getattr(packet, "odometry", None)
            velocity = getattr(odometry, "velocity", None)
            angular_z = getattr(velocity, "angular_z", MISSING)
            status = summary.observe(angular_z)
            fields = " ".join(
                f"{name}={display(getattr(velocity, name, MISSING))}"
                for name in VELOCITY_FIELDS
            )
            try:
                attributes = repr(vars(velocity))
            except TypeError:
                attributes = "<no __dict__; see raw object and fields>"
            print(
                f"\nsample={summary.packets} elapsed_s={time.monotonic() - started:.3f} "
                f"sdk_odometry_time={display(getattr(odometry, 'time', MISSING))} "
                f"angular_z={display(angular_z)} status={status}\n"
                f"  velocity_type={type(velocity).__module__}.{type(velocity).__name__}\n"
                f"  velocity_fields: {fields}\n"
                f"  velocity_raw: {velocity!r}\n"
                f"  velocity_attributes: {attributes}\n"
                f"  orientation_raw: {getattr(odometry, 'orientation', None)!r}",
                flush=True,
            )
    finally:
        summary.report()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect Navel's raw SDK angular velocity stream.")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="Run time in seconds (default 30); 0 runs until Ctrl-C")
    parser.add_argument("--timeout", type=float, default=1.0,
                        help="Maximum wait for each locomotion packet, in seconds")
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration) or args.duration < 0:
        parser.error("duration must be finite and nonnegative")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    return args


async def run(args: argparse.Namespace) -> None:
    import navel

    print(f"SDK module: {getattr(navel, '__file__', '<unknown>')}", flush=True)
    print(f"SDK version: {getattr(navel, '__version__', '<not exposed>')}", flush=True)
    print(
        "Reading next_locomotion().odometry.velocity.angular_z directly.\n"
        "Observe the readings while the robot base turns using your usual controls.\n"
        "This diagnostic only reads data. Stop with Ctrl-C.",
        flush=True,
    )
    async with navel.Robot() as robot:
        await stream(robot, duration=args.duration, timeout=args.timeout)


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Diagnostic stopped.", flush=True)
    except ImportError as error:
        print(f"SDK import failed: {error}. Use Navel's SDK-enabled Python.", file=sys.stderr)
        return 1
    except (ConnectionAbortedError, OSError) as error:
        print(f"SDK connection failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
