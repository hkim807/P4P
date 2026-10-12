"""Reproducible recording audit: source fidelity, bounded histories, replay parity.

This offline validator intentionally loads short pilot recordings for independent
sample-by-sample checks. Runtime tracking and the single-file CLI remain streaming.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys

from app.pipeline import TrackingPipeline, trace_line
from app.recording import read_frames
from app.replay import replay
from app.state.tracks import TRACKER_VERSION, TrackConfig


def audit_recording(path: Path, config: TrackConfig, trace_output: Path | None = None) -> dict:
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    frames = list(read_frames(path))
    session = f"{path.stem}-{source_hash[:12]}"
    events: Counter = Counter()
    reasons: Counter = Counter()
    uids = sorted({p["uid"] for f in frames for p in f["people"]})
    peak_tracks = peak_samples = checked_samples = 0
    digest_by_speed = {}
    final_tracks = []
    source_index = {}
    for frame in frames:
        for person in frame["people"]:
            source_index[(frame["timestamp"], person["uid"])] = person

    def require(condition, message):
        if not condition:
            raise ValueError(f"{path.name}: {message}")

    output = trace_output.open("x", encoding="utf-8") if trace_output is not None else None
    try:
        for speed in (0, 1, 2):
            pipeline = TrackingPipeline(session, config)
            clock = [0.0]
            digest = hashlib.sha256()
            sequence = 0
            def emit(frame):
                nonlocal sequence, peak_tracks, peak_samples, checked_samples, final_tracks
                sequence += 1
                snapshot = pipeline.process(frame)
                line = trace_line(snapshot)
                digest.update(line.encode())
                if speed != 0:
                    return
                if output is not None:
                    output.write(line)
                require(snapshot["frame_sequence"] == sequence, "sequence mismatch")
                require(snapshot["robot_timestamp_us"] == frame["timestamp"], "timestamp mismatch")
                require(snapshot["robot"] == frame["robot"], "robot context changed")
                now = frame["timestamp"]
                observed = {p["uid"] for p in frame["people"]}
                actual_observed = {t["uid"] for t in snapshot["tracks"] if t["visibility"] == "OBSERVED"}
                require(actual_observed == observed, "observed UID set mismatch")
                require(len(snapshot["tracks"]) <= config.max_tracks, "track capacity exceeded")
                peak_tracks = max(peak_tracks, len(snapshot["tracks"]))
                for track in snapshot["tracks"]:
                    uid = track["uid"]
                    samples = track["samples"]
                    require(now - track["last_seen_us"] <= config.missing_grace_us, "expired history retained")
                    require(track["retained_sample_count"] == len(samples), "sample count mismatch")
                    require(len(samples) <= config.max_samples_per_track, "sample cap exceeded")
                    # Independent oracle: select original samples in this epoch/window.
                    expected_all = [(f["timestamp"], i + 1) for i, f in enumerate(frames[:sequence])
                                    if f["timestamp"] >= track["first_seen_us"]
                                    and (f["timestamp"], uid) in source_index]
                    expected_window = [(t, seq) for t, seq in expected_all
                                       if t >= now - config.history_window_us][-config.max_samples_per_track:]
                    require(track["observation_count"] == len(expected_all), "epoch observation count mismatch")
                    require([(s["timestamp_us"], s["frame_sequence"]) for s in samples] == expected_window,
                            "history does not match original samples within epoch/window")
                    for sample in samples:
                        original = source_index[(sample["timestamp_us"], uid)]
                        copied = {k: v for k, v in sample.items() if k not in ("timestamp_us", "frame_sequence")}
                        require(copied == {k: v for k, v in original.items() if k != "uid"},
                                "measurement changed or crossed UID")
                        checked_samples += 1
                    peak_samples = max(peak_samples, len(samples))
                events.update(e["type"] for e in snapshot["events"])
                reasons.update(e["reason"] for e in snapshot["events"] if e["type"] == "LOST")
                final_tracks = [{k: t[k] for k in ("uid", "track_epoch", "visibility")}
                                for t in snapshot["tracks"]]
            replay(frames, emit, speed=speed, monotonic=lambda: clock[0],
                   sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
            digest_by_speed[str(speed)] = digest.hexdigest()
    finally:
        if output is not None:
            output.close()
    require(len(set(digest_by_speed.values())) == 1, "replay speeds produce different traces")
    require(hashlib.sha256(path.read_bytes()).hexdigest() == source_hash, "source file changed during audit")
    return {"file": path.name, "source_sha256": source_hash, "session_id": session,
            "frames": len(frames), "duration_s": (frames[-1]["timestamp"] - frames[0]["timestamp"]) / 1e6,
            "raw_uids": uids, "empty_frames": sum(not f["people"] for f in frames),
            "person_observations": sum(len(f["people"]) for f in frames),
            "events": dict(sorted(events.items())), "loss_reasons": dict(sorted(reasons.items())),
            "peak_active_tracks": peak_tracks, "peak_samples_per_track": peak_samples,
            "sample_copies_checked": checked_samples, "final_tracks": final_tracks,
            "trace_sha256_by_speed": digest_by_speed, "checks_passed": True}


def markdown_report(report: dict) -> str:
    lines = ["# Person tracking: pilot recording validation", "",
             "Generated by `python -m app.validate_tracking`. Design and operation: `docs/person-tracking.md`.", "",
             f"Tracker: `{report['tracker_version']}`. Configuration: `{json.dumps(report['config'], sort_keys=True)}`.",
             "", "Replay speeds 0, 1, and 2 use a virtual pacing clock; their complete trace hashes must match.",
             "Source timestamps, per-UID sample fidelity, epoch counts, window/cap bounds, and visibility were checked.",
             "This is software-contract validation, not physical-person identity accuracy or engagement accuracy.", "",
             "| Recording | Frames | UIDs | Empty | Acquired epochs | Reacquired | Lost | Peak tracks | Peak samples | Result |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for r in report["recordings"]:
        e = r["events"]
        lines.append(f"| {r['file']} | {r['frames']} | {len(r['raw_uids'])} | {r['empty_frames']} | "
                     f"{e.get('ACQUIRED', 0)} | {e.get('REACQUIRED', 0)} | {e.get('LOST', 0)} | "
                     f"{r['peak_active_tracks']} | {r['peak_samples_per_track']} | PASS |")
    lines += ["", f"Total: {sum(r['frames'] for r in report['recordings'])} frames; "
              f"{sum(r['person_observations'] for r in report['recordings'])} person observations; "
              f"{sum(r['sample_copies_checked'] for r in report['recordings'])} retained sample copies verified.",
              "", "An acquired epoch is a history segment, not a count of real people. Reacquired means the same UID",
              "returned within grace after an absent frame. EOF does not synthesize LOST events.",
              "Missing histories can coexist with one visible person; peak tracks is not a crowd count.",
              "", "## Grace-period sensitivity", "",
              "All other configuration is held constant. Counts are acquired epochs, not real people.",
              "Fewer epochs is not evidence of more accurate identity association without ground truth.", "",
              "| Recording | 0.25 s | 0.75 s | 1.5 s |", "| --- | ---: | ---: | ---: |"]
    for index, r in enumerate(report["recordings"]):
        counts = [report["grace_sweep"][str(grace)][index]["acquired_epochs"] for grace in (0.25, 0.75, 1.5)]
        lines.append(f"| {r['file']} | {counts[0]} | {counts[1]} | {counts[2]} |")
    lines += ["", "## Reproducibility", "",
              "Input hashes and output trace hashes (identical for all three speeds):", ""]
    for r in report["recordings"]:
        lines += [f"- `{r['file']}`", f"  - Input SHA-256: `{r['source_sha256']}`",
                  f"  - Trace SHA-256: `{r['trace_sha256_by_speed']['0']}`"]
    lines += ["", "Full counters, final track states, effective configuration, implementation source hashes,",
              "grace sensitivity results, and per-speed hashes are in `summary.json`.",
              "JSONL traces are stored in the chosen output directory. The sources are never rewritten.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", nargs="+")
    parser.add_argument("--config", help="Configuration JSON; otherwise defaults")
    parser.add_argument("--output-dir", required=True, help="New directory for traces and reports")
    args = parser.parse_args(argv)
    try:
        config = TrackConfig.from_file(args.config) if args.config else TrackConfig()
        paths = [Path(p) for p in args.recordings]
        if len({p.stem for p in paths}) != len(paths):
            raise ValueError("recording basenames must be unique")
        for path in paths:
            for _ in read_frames(path):
                pass
        destination = Path(args.output_dir)
        destination.mkdir(parents=True, exist_ok=False)
        results = [audit_recording(p, config, destination / f"{p.stem}.tracks.jsonl") for p in paths]
        sweep = {}
        for grace in (0.25, 0.75, 1.5):
            sweep[str(grace)] = []
            for path in paths:
                result = audit_recording(path, replace(config, missing_grace_s=grace))
                sweep[str(grace)].append({"file": path.name,
                                          "acquired_epochs": result["events"].get("ACQUIRED", 0),
                                          "checks_passed": result["checks_passed"]})
        root = Path(__file__).resolve().parents[1]
        source_files = ("app/state/tracks.py", "app/pipeline.py", "app/replay/track.py", "app/replay/__init__.py",
                        "app/recording.py", "app/domain/models.py", "app/server.py", "app/validate_tracking.py")
        report = {"tracker_version": TRACKER_VERSION, "config": asdict(config), "recordings": results,
                  "grace_sweep": sweep,
                  "implementation_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                             for name in source_files}}
        (destination / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (destination / "report.md").write_text(markdown_report(report), encoding="utf-8")
        print(f"Validated {sum(r['frames'] for r in results)} frames in {len(results)} recordings. Reports: {destination}")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Tracking validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
