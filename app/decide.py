"""Apply the rule policy to a SocialState JSONL trace."""
import argparse
from collections import Counter
from contextlib import nullcontext
import json
from pathlib import Path
import sys

from app.pipeline import trace_line
from app.policy.rules import decide
from app.state.social_models import SocialState


def read_states(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                yield SocialState.model_validate_json(line)
            except ValueError as error:
                raise ValueError(f"{path}:{line_number}: invalid SocialState: {error}") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("social_trace", help="SocialState JSONL produced by app.social or the live receiver")
    parser.add_argument("--output", help="New decision JSONL; stdout by default")
    args = parser.parse_args(argv)
    try:
        # Validate the whole input before creating an output file.
        for _ in read_states(args.social_trace):
            pass
        counts = Counter()
        with (open(args.output, "x", encoding="utf-8") if args.output else nullcontext(sys.stdout)) as output:
            for state in read_states(args.social_trace):
                decision = decide(state)
                output.write(trace_line(decision.model_dump(mode="json")))
                counts[decision.decision] += 1
        print(json.dumps({"decisions": dict(counts)}), file=sys.stderr)
        return 0
    except (OSError, ValueError) as error:
        print(f"Decision replay failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
