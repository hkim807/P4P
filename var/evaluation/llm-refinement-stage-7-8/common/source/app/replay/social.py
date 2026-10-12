"""Replay raw recordings into temporal SocialState JSONL, preserving source data."""
import argparse
from collections import Counter
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import sys

from app.pipeline import trace_line, TrackingProcessingError
from app.recording import read_frames
from app.replay import replay
from app.social_pipeline import SocialPipeline
from app.state.social_models import TemporalConfig
from app.state.tracks import TrackConfig


def recording_session(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return f"{Path(path).stem}-{digest.hexdigest()[:12]}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording")
    parser.add_argument("--config", help="Temporal configuration JSON")
    parser.add_argument("--tracking-config", help="Tracking configuration JSON")
    parser.add_argument("--speed", type=float, default=0)
    parser.add_argument("--output", help="New SocialState JSONL; stdout by default")
    args = parser.parse_args(argv)
    if not math.isfinite(args.speed) or args.speed < 0:
        parser.error("speed must be finite and nonnegative")
    try:
        config = TemporalConfig.from_file(args.config) if args.config else TemporalConfig()
        tracks = TrackConfig.from_file(args.tracking_config) if args.tracking_config else TrackConfig()
        for _ in read_frames(args.recording):
            pass
        pipeline = SocialPipeline(recording_session(args.recording), tracks, config)
        counters = Counter()
        with (open(args.output, "x", encoding="utf-8") if args.output else nullcontext(sys.stdout)) as output:
            def emit(frame):
                state = pipeline.process(frame)["social_state"]
                output.write(trace_line(state))
                counters.update(p["gaze_state"] for p in state["people"] if p["visibility"] == "OBSERVED")
            count = replay(read_frames(args.recording), emit, speed=args.speed)
        print(json.dumps({"frames": count, "observed_gaze_states": dict(counters),
                          "calibration_status": "PROVISIONAL"}), file=sys.stderr)
        return 0
    except (ValueError, OSError, TrackingProcessingError) as error:
        print(f"Social replay failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Social replay stopped; output may be partial.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
