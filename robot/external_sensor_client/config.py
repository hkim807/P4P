"""Command-line configuration for the stationary D435 foundation."""

from __future__ import annotations

import argparse
import math
from urllib.parse import urlsplit

from pydantic import ValidationError

from app.domain.models import CapabilityManifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture D435 frames and display observation responses."
    )
    parser.add_argument(
        "--server", default="http://127.0.0.1:6060",
        help="Server base URL, without endpoint path",
    )
    parser.add_argument("--request-timeout", type=float, default=35.0)
    parser.add_argument(
        "--adapter-id", default="external-d435-01",
        help="Unique temporal source ID; use a new ID after a host reboot",
    )
    parser.add_argument("--minimum-send-interval", type=float, default=0.2)
    parser.add_argument("--realsense-serial")
    parser.add_argument("--stationary-rig", action="store_true")
    parser.add_argument("--camera-test", action="store_true")
    parser.add_argument("--camera-test-frames", type=int, default=30)
    parser.add_argument("--force-decision", action="store_true")
    parser.add_argument("--print-raw-json", action="store_true")
    args = parser.parse_args(argv)
    if not args.camera_test and not args.stationary_rig:
        parser.error(
            "normal mode requires --stationary-rig: odometry is not implemented; "
            "zero velocity is valid only for a fixed rig"
        )
    if not math.isfinite(args.request_timeout) or args.request_timeout <= 0:
        parser.error("--request-timeout must be finite and positive")
    if not math.isfinite(args.minimum_send_interval) or args.minimum_send_interval < 0:
        parser.error("--minimum-send-interval must be finite and non-negative")
    if args.camera_test_frames <= 0:
        parser.error("--camera-test-frames must be positive")
    if args.realsense_serial is not None:
        args.realsense_serial = args.realsense_serial.strip()
        if not args.realsense_serial:
            parser.error("--realsense-serial must not be empty")
    if not args.camera_test:
        try:
            url = urlsplit(args.server)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.path not in {"", "/"}
                or url.query or url.fragment or url.username or url.password
            ):
                raise ValueError(
                    "expected an HTTP(S) base URL, without endpoint path, "
                    "credentials, query, or fragment"
                )
            _ = url.port
            args.adapter_id = CapabilityManifest(
                adapter_id=args.adapter_id, robot_type="external-sensor-rig"
            ).adapter_id
            if len(args.adapter_id) > 128:
                raise ValueError(
                    "adapter ID must be at most 128 characters to leave room "
                    "for observation ID suffixes"
                )
        except (ValueError, ValidationError) as error:
            parser.error(str(error))
    return args
