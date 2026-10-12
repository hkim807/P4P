"""Replay one raw recording through UID tracking; JSONL stdout, summary stderr."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import sys

from app.pipeline import TrackingPipeline, TrackingProcessingError, trace_line
from app.recording import read_frames
from app.replay import replay
from app.state.tracks import TrackConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording")
    parser.add_argument("--config", help="Tracking configuration JSON; otherwise built-in defaults")
    parser.add_argument("--session-id", help="Override the deterministic file-name/content session key")
    parser.add_argument("--speed", type=float, default=0.0, help="0 = immediate, 1 = original pacing")
    parser.add_argument("--output", help="New derived JSONL file; defaults to stdout")
    args = parser.parse_args(argv)
    if not math.isfinite(args.speed) or args.speed < 0:
        parser.error("speed must be finite and nonnegative")
    events: Counter = Counter()
    try:
        config = TrackConfig.from_file(args.config) if args.config else TrackConfig()
        # Preflight before creating an output artifact; read again with bounded memory.
        for _ in read_frames(args.recording):
            pass
        if args.session_id is None:
            digest = hashlib.sha256()
            with open(args.recording, "rb") as source:
                for chunk in iter(lambda: source.read(65536), b""):
                    digest.update(chunk)
            session_id = f"{Path(args.recording).stem}-{digest.hexdigest()[:12]}"
        else:
            session_id = args.session_id
        pipeline = TrackingPipeline(session_id, config)
        context = open(args.output, "x", encoding="utf-8") if args.output else nullcontext(sys.stdout)
        with context as output:
            def emit(payload):
                snapshot = pipeline.process(payload)
                output.write(trace_line(snapshot))
                events.update(e["type"] for e in snapshot["events"])
            count = replay(read_frames(args.recording), emit, speed=args.speed)
        print(json.dumps({"frames": count, "events": dict(events), "session_id": session_id}),
              file=sys.stderr)
        return 0
    except (OSError, ValueError, UnicodeError, TrackingProcessingError) as error:
        print(f"Tracking failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Tracking stopped; output may be partial.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
