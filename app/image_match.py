"""Associate Step 3 replay decision moments with recorded camera frames."""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import sys
from typing import Any

from app.camera_recordings import CameraManifestError, load_camera_events
from app.image_matching import CameraMatcher, ReplayInputError, iter_replay_rows


def _print_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path, help="Original Step 3 replay JSONL, prepared or inferred")
    parser.add_argument("--manifests", required=True, nargs="+", type=Path,
                        help="Explicit stored camera frames.jsonl path(s)")
    parser.add_argument("--camera", choices=("head", "chest"), default="head")
    parser.add_argument("--allow-receipt-match", action="store_true",
                        help="Opt in to latest prior reception matching when exact recorded SDK equality is absent")
    parser.add_argument("--max-frame-age",
                        help="Required source seconds for reception matching; inclusive age boundary")
    parser.add_argument("--output", required=True, type=Path, help="New associated replay JSONL; never overwritten")
    args = parser.parse_args(argv)
    summary: dict[str, Any] = {
        "status": "failed", "complete": False, "total_rows": 0, "rows_written": 0,
        "matched_rows": 0, "unmatched_rows": 0, "methods": Counter(),
        "failure_categories": Counter(), "output": str(args.output), "error": None,
    }
    stage = "configuration"
    write_started = False
    try:
        age_us = None
        if args.allow_receipt_match:
            try:
                age = Decimal(args.max_frame_age) if args.max_frame_age is not None else None
            except InvalidOperation as error:
                raise ValueError("max-frame-age must be a numeric duration") from error
            if (age is None or not age.is_finite() or age < 0
                    or not math.isfinite(float(age) * 1_000_000)
                    or 0 < age < Decimal("0.000001")):
                raise ValueError("reception matching requires an explicit finite nonnegative max-frame-age, at least one microsecond when positive")
            numerator, denominator = age.as_integer_ratio()
            # A maximum must never round upward and admit an older frame.
            age_us = numerator * 1_000_000 // denominator
        elif args.max_frame_age is not None:
            raise ValueError("max-frame-age requires --allow-receipt-match")
        stage = "manifest"
        events = load_camera_events(args.manifests)
        matcher = CameraMatcher(events, camera=args.camera, allow_receipt=args.allow_receipt_match,
                                max_age_us=age_us)
        stage = "input"
        if not args.replay.is_file():
            raise ReplayInputError(f"replay is not a file: {args.replay}")
        protected = [args.replay, *args.manifests, *(e.image_path for e in events if e.image_path is not None)]
        if any(args.output.resolve() == path.resolve() for path in protected):
            raise ValueError("output resolves to an input replay, manifest or stored camera image")
        stage = "output"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            stage = "input"
            for row in iter_replay_rows(args.replay):
                summary["total_rows"] += 1
                stage = "matching"
                result = matcher.match(row)
                if result["ok"]:
                    summary["matched_rows"] += 1
                    summary["methods"][result["method"]] += 1
                else:
                    summary["unmatched_rows"] += 1
                    summary["failure_categories"][result["error"]["category"]] += 1
                associated = {**row, "image_matching": result}
                stage = "output"
                serialized = json.dumps(associated, sort_keys=True, ensure_ascii=False, allow_nan=False)
                write_started = True
                output.write(serialized + "\n")
                output.flush()
                summary["rows_written"] += 1
                stage = "input"
            stage = "output"
        summary["complete"] = True
        summary["status"] = "complete_with_unmatched" if summary["unmatched_rows"] else "complete"
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as error:
        if write_started:
            summary["status"] = "partial"
        category = ("invalid_manifest" if isinstance(error, CameraManifestError) else
                    "invalid_replay" if isinstance(error, ReplayInputError) else
                    "write_error" if stage == "output" else "invalid_configuration" if stage == "configuration" else "processing_error")
        summary["error"] = {"stage": stage, "category": category, "message": str(error) or "interrupted"}
        print(f"Image matching {summary['status']} ({stage}): {summary['error']['message']}", file=sys.stderr)
        _print_summary(summary)
        return 2
    _print_summary(summary)
    return 1 if summary["unmatched_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
