"""Replay raw JSONL to stdout, or optionally POST frames to a receiver."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from typing import Any, Callable, Iterable

from app.recording import read_frames
from robot.navel_client.transport import ObservationTransport, TransportError


def replay(
    frames: Iterable[dict[str, Any]],
    emit: Callable[[dict[str, Any]], None],
    *,
    speed: float = 1.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Preserve timestamp gaps without accumulating output/request delays."""
    if not math.isfinite(speed) or speed < 0:
        raise ValueError("speed must be finite and nonnegative")
    first_timestamp: int | None = None
    started = monotonic()
    count = 0
    for frame in frames:
        if first_timestamp is None:
            first_timestamp = frame["timestamp"]
            started = monotonic()
        if speed > 0:
            due = started + (frame["timestamp"] - first_timestamp) / 1_000_000 / speed
            delay = due - monotonic()
            if delay > 0:
                sleep(delay)
        emit(frame)
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="View or replay a raw Navel JSONL recording.")
    parser.add_argument("recording", help="JSONL file containing raw sensor frames")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Playback multiplier (default 1); 0 outputs as fast as possible")
    parser.add_argument("--pretty", action="store_true", help="Print indented JSON instead of JSONL")
    parser.add_argument("--server", help="Optionally POST frames to this HTTP(S) receiver base URL")
    parser.add_argument("--request-timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.speed) or args.speed < 0:
        parser.error("speed must be finite and nonnegative")
    if not math.isfinite(args.request_timeout) or args.request_timeout <= 0:
        parser.error("request timeout must be finite and positive")
    transport = None
    if args.server:
        try:
            transport = ObservationTransport(args.server, timeout_seconds=args.request_timeout)
        except ValueError as error:
            parser.error(str(error))

    def emit(frame: dict[str, Any]) -> None:
        if transport is not None:
            response = transport.send(frame)
            if not 200 <= response.status_code < 300 or response.payload.get("accepted") is not True:
                raise TransportError(f"receiver rejected replay: HTTP {response.status_code} {response.payload}")
        print(json.dumps(frame, allow_nan=False, indent=2 if args.pretty else None,
                         separators=None if args.pretty else (",", ":")), flush=True)

    try:
        if transport is not None:
            # Validate the complete file before making any HTTP calls. Still O(1) memory.
            for _ in read_frames(args.recording):
                pass
        count = replay(read_frames(args.recording), emit, speed=args.speed)
    except BrokenPipeError:
        # Allow piping local playback into tools such as head.
        sys.stdout = open("/dev/null", "w")
        return 0
    except KeyboardInterrupt:
        print("Replay stopped.", file=sys.stderr)
        return 130
    except (OSError, ValueError, UnicodeError, TransportError) as error:
        print(f"Replay failed: {error}", file=sys.stderr)
        return 1
    print(f"Replayed {count} frames.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
