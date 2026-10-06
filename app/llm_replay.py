"""Replay SDK, raw or saved SocialState JSONL through the existing LLM policy."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any

from app.llm_replay_inputs import iter_replay_states
from app.ollama import OllamaClient, OllamaConfig
from app.policy.llm import build_llm_prompt, decide_llm
from app.state.social_models import TemporalConfig
from app.state.tracks import TrackConfig


def _source_duration_us(value: float, name: str) -> int:
    if (not math.isfinite(value) or value < 0
            or not math.isfinite(value * 1_000_000)
            or 0 < value < 0.000001):
        raise ValueError(f"{name} must be zero or finite and at least one microsecond")
    return round(value * 1_000_000)


def _print_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", nargs="+", type=Path, help="Ordered input JSONL path(s)")
    parser.add_argument("--format", required=True, choices=("sdk", "raw", "social"),
                        help="One explicit format for all inputs; historical schemas are rejected")
    parser.add_argument("--output", required=True, type=Path, help="New result JSONL; never overwritten")
    parser.add_argument("--base-url", required=True, help="Ollama HTTP(S) origin (metadata only in preparation)")
    parser.add_argument("--model", required=True, help="Caller-selected model (metadata only in preparation)")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--num-predict", type=int)
    parser.add_argument("--sample-interval", type=float, default=1.0,
                        help="Source seconds since last selection; 0 selects every snapshot")
    parser.add_argument("--warmup", type=float, default=0.0,
                        help="Source seconds from each session's first snapshot before first selection")
    parser.add_argument("--max-calls", type=int,
                        help="Global selection/call cap; all observations still undergo validation/estimation")
    parser.add_argument("--prepare-only", action="store_true", help="Write frozen inputs without constructing a client")
    parser.add_argument("--track-config", type=Path)
    parser.add_argument("--temporal-config", type=Path)
    parser.add_argument("--max-locomotion-age", type=float, default=1.0,
                        help="SDK receipt-time freshness in seconds, matching the live default")
    args = parser.parse_args(argv)
    summary: dict[str, Any] = {
        "status": "failed", "complete": False, "observations_processed": 0,
        "selected_moments": 0, "rows_written": 0, "successes": 0, "failures": 0, "prepared": 0,
        "output": str(args.output), "error": None,
    }
    stage = "configuration"
    write_started = False
    try:
        interval_us = _source_duration_us(args.sample_interval, "sample interval")
        warmup_us = _source_duration_us(args.warmup, "warmup")
        _source_duration_us(args.max_locomotion_age, "maximum locomotion age")
        if args.max_calls is not None and args.max_calls < 1:
            raise ValueError("max-calls must be a positive integer")
        if args.format == "social" and (args.track_config or args.temporal_config):
            raise ValueError("saved SocialState configuration cannot be overridden")
        track_config = TrackConfig.from_file(args.track_config) if args.track_config else None
        temporal_config = TemporalConfig.from_file(args.temporal_config) if args.temporal_config else None
        config = OllamaConfig(base_url=args.base_url, model=args.model, timeout_seconds=args.timeout,
                              temperature=args.temperature, seed=args.seed, num_predict=args.num_predict)
        model_settings = {**config.model_dump(mode="json"), "generation_options": config.generation_options()}
        stage = "input"
        for path in args.recordings:
            if not path.is_file():
                raise ValueError(f"input is not a file: {path}")
            if args.output.resolve() == path.resolve():
                raise ValueError(f"output resolves to input: {path}")
        stage = "output"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also protects existing hard links and racing writers.
        with args.output.open("x", encoding="utf-8") as output:
            client = None
            for path in args.recordings:
                session = None
                first_us = last_selected_us = None
                stage = "input"
                states = iter_replay_states(path, args.format, track_config=track_config,
                                           temporal_config=temporal_config,
                                           max_locomotion_age_s=args.max_locomotion_age)
                for item in states:
                    summary["observations_processed"] += 1
                    now = item.state.robot_timestamp_us
                    current_session = item.source["processing_session_id"]
                    if current_session != session:
                        session, first_us, last_selected_us = current_session, now, None
                    if now - first_us < warmup_us:
                        continue
                    if last_selected_us is not None and now - last_selected_us < interval_us:
                        continue
                    if args.max_calls is not None and summary["selected_moments"] >= args.max_calls:
                        continue
                    last_selected_us = now
                    summary["selected_moments"] += 1
                    if args.prepare_only:
                        prompt = build_llm_prompt(item.state)
                        diagnostics = {
                            "source_state_id": prompt.source_state_id, "session_id": prompt.session_id,
                            "source_robot_timestamp_us": prompt.source_robot_timestamp_us,
                            "prompt_version": prompt.prompt_version, "ok": None, "decision": None,
                            "error": None, "requested_model": config.model, "returned_model": None,
                            "raw_content": None, "request_duration_s": None,
                        }
                        status = "prepared"
                    else:
                        stage = "inference"
                        if client is None:
                            client = OllamaClient(config)
                        result = decide_llm(item.state, client)
                        prompt, diagnostics = result.prompt, result.to_dict()
                        status = "succeeded" if result.ok else "failed"
                    counter = {"prepared": "prepared", "succeeded": "successes", "failed": "failures"}[status]
                    summary[counter] += 1
                    # The returned prompt is the frozen input actually sent, even
                    # if the caller's state was changed while the HTTP call ran.
                    row = {
                        "schema_version": 1, "status": status,
                        "source": item.source, "processing": item.processing,
                        "sampling": {"interval_us": interval_us, "warmup_us": warmup_us,
                                     "max_calls": args.max_calls,
                                     "selected_index": summary["selected_moments"]},
                        "ollama_configuration": model_settings,
                        "social_state": json.loads(prompt.social_state_json),
                        "social_state_json": prompt.social_state_json,
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "completion_clock": "replay-host UTC wall clock; independent of source clocks",
                        **diagnostics,
                    }
                    stage = "output"
                    write_started = True
                    output.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
                    output.flush()
                    summary["rows_written"] += 1
                    stage = "input"
            stage = "output"
        summary["complete"] = True
        summary["status"] = ("prepared" if args.prepare_only else
                             "inference_failed" if summary["failures"] else "complete")
    except (OSError, ValueError, RecursionError, KeyboardInterrupt) as error:
        # A partial output is intentionally retained for diagnosis. Never call a
        # source-processing or write failure a completed run.
        if write_started:
            summary["status"] = "partial"
        summary["error"] = {"stage": stage, "message": str(error) or "interrupted"}
        print(f"LLM replay {summary['status']} ({stage}): {summary['error']['message']}", file=sys.stderr)
        _print_summary(summary)
        return 2
    _print_summary(summary)
    return 1 if summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
