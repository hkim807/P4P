"""Validate SDK recordings and save copies starting at measured robot movement.

Run from the repository root with python -m scripts.validate_trim_recordings.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys

from app.recording_validation import ValidationConfig, inspect_recording, trim_recording


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", nargs="*", type=Path, default=[Path("var/recordings")],
                        help="SDK files or directories (default: var/recordings; directory match: *sdk.jsonl)")
    parser.add_argument("--output-dir", required=True, type=Path, help="New directory for report and trimmed copies")
    parser.add_argument("--recursive", action="store_true", help="Also inspect SDK recordings in subdirectories")
    parser.add_argument("--max-gap-s", type=float, default=0.25, help="Maximum receipt gap per stream")
    parser.add_argument("--source-gap-factor", type=float, default=1.5,
                        help="Flag native SDK time intervals above this multiple of the median cadence")
    parser.add_argument("--linear-threshold", type=float, default=0.02, help="Forward motion threshold, m/s")
    parser.add_argument("--angular-threshold", type=float, default=0.03, help="Yaw motion threshold, rad/s")
    args = parser.parse_args(argv)
    try:
        config = ValidationConfig(args.max_gap_s, args.source_gap_factor,
                                  args.linear_threshold, args.angular_threshold)
        paths = set()
        for path in args.recordings:
            if path.is_dir():
                matches = path.rglob("*sdk.jsonl") if args.recursive else path.glob("*sdk.jsonl")
                paths.update(match.resolve() for match in matches if match.is_file())
            elif path.is_file():
                paths.add(path.resolve())
            else:
                raise ValueError(f"Input does not exist: {path}")
        if not paths:
            raise ValueError("No SDK recordings found")
        names = [path.name for path in paths]
        if len(names) != len(set(names)):
            raise ValueError("Inputs have duplicate filenames; use separate output directories")
        args.output_dir.mkdir(parents=True, exist_ok=False)
        reports = []
        for path in sorted(paths):
            inspection = inspect_recording(path, config)
            report = inspection.report
            if report["status"] == "READY_TO_TRIM":
                try:
                    trim_recording(inspection, args.output_dir / "trimmed" / path.name)
                except ValueError as error:
                    report.update(status="UNTRIMMABLE", trim_error=str(error))
            reports.append(report)
            print(f"{path.name}: {report['status']} "
                  f"(errors={report['error_count']}, warnings={report['warning_count']})", flush=True)
        summary = {"settings": inspection.report["settings"], "recording_count": len(reports),
            "status_counts": dict(Counter(report["status"] for report in reports)),
            "source_integrity_limit": "Capture sequences detect missing received packets. Native SDK time gaps suggest skipped samples, but these files cannot prove every hardware-generated frame was received or detect loss before capture start/after capture end.",
            "motion_definition": "First locomotion packet with abs(linear_x) > linear_threshold or abs(angular_z) > angular_threshold; no sustained-motion filter.",
            "recordings": reports}
        with (args.output_dir / "report.json").open("x") as target:
            json.dump(summary, target, indent=2, allow_nan=False)
            target.write("\n")
        with (args.output_dir / "summary.csv").open("x", newline="") as target:
            writer = csv.writer(target)
            writer.writerow(["recording", "validation", "status", "perception_packets", "locomotion_packets",
                             "motion_start_s", "removed_packets", "kept_perception", "kept_locomotion",
                             "trimmed_duration_s", "errors", "warnings"])
            for report in reports:
                streams = report["streams"]
                writer.writerow([Path(report["input"]).name, report["validation"], report["status"],
                    streams["perception"]["packet_count"], streams["locomotion"]["packet_count"],
                    (report["motion_start"] or {}).get("offset_s", ""), report.get("removed_packets", ""),
                    report.get("kept_packets", {}).get("perception", ""),
                    report.get("kept_packets", {}).get("locomotion", ""),
                    report.get("trimmed_duration_s", ""), report["error_count"], report["warning_count"]])
        print(json.dumps({"report": str((args.output_dir / 'report.json').resolve()),
                          "status_counts": summary["status_counts"]}))
        return 1 if any(report["status"] in {"INVALID", "UNTRIMMABLE"} for report in reports) else 0
    except (OSError, ValueError) as error:
        print(f"Recording preparation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
