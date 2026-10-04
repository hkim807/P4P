"""Replay raw observations through the shared target-lock pipeline."""

import argparse
from collections import Counter
from contextlib import nullcontext
import json
from pathlib import Path
import sys

from app.pipeline import TrackingProcessingError, trace_line
from app.policy.target_lock import LockConfig
from app.recording import read_frames
from app.social import recording_session
from app.social_pipeline import SocialPipeline
from app.state.social_models import TemporalConfig
from app.state.tracks import TrackConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording")
    parser.add_argument("--output", help="New target-lock JSONL; stdout by default")
    parser.add_argument("--tracking-config")
    parser.add_argument("--temporal-config")
    parser.add_argument("--lock-config")
    args = parser.parse_args(argv)
    try:
        for _ in read_frames(args.recording):
            pass
        pipeline = SocialPipeline(
            recording_session(args.recording),
            TrackConfig.from_file(args.tracking_config) if args.tracking_config else None,
            TemporalConfig.from_file(args.temporal_config) if args.temporal_config else None,
            lock_config=LockConfig.from_file(args.lock_config) if args.lock_config else None,
        )
        counts = Counter()
        with (Path(args.output).open("x", encoding="utf-8") if args.output else nullcontext(sys.stdout)) as output:
            for frame in read_frames(args.recording):
                snapshot = pipeline.process(frame)["target_lock"]
                output.write(trace_line(snapshot))
                counts[snapshot["status"]] += 1
        print(json.dumps({"lock_states": dict(counts)}), file=sys.stderr)
        return 0
    except (OSError, ValueError, TrackingProcessingError) as error:
        print(f"Target-lock replay failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
