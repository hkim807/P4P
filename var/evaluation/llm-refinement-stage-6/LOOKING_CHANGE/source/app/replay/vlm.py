"""Run image-only VLM inference over already-associated frozen replay rows."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

from app.camera.recordings import (
    CameraManifestError, load_camera_events, read_selected_camera_event,
)
from app.inference.ollama import OllamaClient, OllamaConfig
from app.policy.vlm import PROMPT_VERSION, decide_vlm
from app.replay.vlm_inputs import VLMImageInputError, iter_associated_rows, load_vlm_image


def _protect_sources(input_path: Path, rows: list[dict[str, Any]], output_path: Path) -> None:
    """Protect references in every row before creating output or making calls.

    Stored paths protect missing files too. For unmatched candidate references,
    the current manifest may supply the image path absent from Step 4's error.
    An unreadable/changed manifest remains an image input failure, not a reason
    to discard otherwise valid replay rows during this output safety check.
    """
    protected: set[Path] = set()
    manifests: set[Path] = set()

    def protect(path: Path) -> None:
        protected.add(path.absolute())
        try:
            protected.add(path.resolve())
        except (OSError, RuntimeError, ValueError):
            # A broken image reference must still reach its per-row input
            # failure. Keep its literal path protected even if resolution fails.
            pass

    protect(input_path)

    def manifest_reference(reference: dict[str, Any]) -> None:
        path = Path(reference["manifest_path"])
        protect(path)
        manifests.add(path)
        record = reference.get("record", reference.get("camera_manifest_record"))
        if isinstance(record, dict) and isinstance(record.get("file"), str):
            relative = Path(record["file"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("referenced camera file must remain within its manifest directory")
            protect(path.parent / relative)
        else:
            # Step 4's missing-image error retains only the candidate line. A
            # different broken image must not prevent protecting that candidate.
            try:
                event = read_selected_camera_event(path, reference["line_number"])
            except CameraManifestError:
                event = None
            if event is not None and event.image_path is not None:
                protect(event.image_path)

    for row in rows:
        protect(Path(row["source"]["input_path"]))
        matching = row["image_matching"]
        frame = matching["frame"]
        if frame is not None:
            manifest_reference(frame)
            protect(Path(frame["image_path"]))
            protect(Path(frame["manifest_path"]).parent / frame["relative_image_path"])
            for reference in frame["equivalent_manifest_references"]:
                manifest_reference(reference)
        else:
            details = matching["error"]["details"]
            references = []
            if "candidate" in details:
                references.append(details["candidate"])
            for key in ("candidates", "events"):
                if key in details:
                    if not isinstance(details[key], list):
                        raise ValueError(f"image_matching.error.details.{key} must be a list")
                    references.extend(details[key])
            for reference in references:
                if (not isinstance(reference, dict)
                        or not isinstance(reference.get("manifest_path"), str)
                        or not Path(reference["manifest_path"]).is_absolute()):
                    raise ValueError("invalid unmatched manifest reference")
                manifest_reference(reference)

    for manifest in manifests:
        try:
            events = load_camera_events([manifest])
        except CameraManifestError:
            continue
        for event in events:
            if event.image_path is not None:
                protect(event.image_path)
    literal = output_path.absolute()
    if protected.intersection((literal, *literal.parents)):
        raise ValueError(f"output or its parent resolves to a protected source reference: {output_path}")
    resolved = output_path.resolve()
    if protected.intersection((resolved, *resolved.parents)):
        raise ValueError(f"output resolves to a protected source reference: {output_path}")


def _result_metadata(row: dict[str, Any], config: OllamaConfig, *, max_calls: int | None) -> dict[str, Any]:
    matching = row["image_matching"]
    return {
        "schema_version": 1, "status": None, "prompt_version": PROMPT_VERSION,
        "max_calls": max_calls,
        "ollama_configuration": {**config.model_dump(mode="json"),
                                 "generation_options": config.generation_options()},
        "source_state_id": row["source_state_id"], "session_id": row["session_id"],
        "source_robot_timestamp_us": row["source_robot_timestamp_us"],
        "original_capture_session": row["source"]["original_capture_session"],
        "selected_frame_reference": matching["frame"],
        "matching_method": matching["method"],
        "matching_time_difference": matching.get("time_difference"),
        "matching_limitations": matching["limitations"],
        "verified_image_sha256": None, "image_encoding": None,
        "ok": None, "requested_model": config.model, "returned_model": None,
        "decision": None, "error": None, "raw_content": None,
        "request_duration_s": None, "error_stage": None,
        "completion_clock": "replay-host UTC wall clock; independent of source clocks",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("associated_replay", type=Path, help="Existing Step 4 associated replay JSONL")
    parser.add_argument("--base-url", required=True, help="Ollama HTTP(S) origin")
    parser.add_argument("--model", required=True, help="Caller-selected installed vision model")
    parser.add_argument("--output", required=True, type=Path, help="New JSONL path; never overwritten")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--num-predict", type=int)
    parser.add_argument("--max-calls", type=int,
                        help="Positive actual request cap; failed requests count, image failures do not. "
                             "All rows are written, including rows after this cap.")
    args = parser.parse_args(argv)
    summary: dict[str, Any] = {
        "status": "failed", "complete": False, "total_rows": 0, "rows_written": 0,
        "calls": 0, "successes": 0, "failures": 0, "inference_failures": 0,
        "invalid_inputs": 0, "unavailable_inputs": 0, "skipped_rows": 0,
        "failure_categories": {}, "output": str(args.output), "error": None,
    }
    categories: Counter[str] = Counter()
    stage = "configuration"
    write_started = False
    try:
        if args.max_calls is not None and args.max_calls < 1:
            raise ValueError("max-calls must be a positive integer")
        config = OllamaConfig(base_url=args.base_url, model=args.model, timeout_seconds=args.timeout,
                              temperature=args.temperature, seed=args.seed, num_predict=args.num_predict)
        stage = "input"
        # A frozen preflight snapshot validates even later rows and protects all
        # their references before any output is created or any model is called.
        rows = list(iter_associated_rows(args.associated_replay))
        summary["total_rows"] = len(rows)
        _protect_sources(args.associated_replay, rows, args.output)
        stage = "output"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects existing paths, hard links and racing writers.
        with args.output.open("x", encoding="utf-8") as output:
            client = None
            for row in rows:
                inference = _result_metadata(row, config, max_calls=args.max_calls)
                matching = row["image_matching"]
                if matching["status"] == "unmatched":
                    inference.update(status="input_unavailable", error_stage="image_input",
                                     error={"category": "matching_unavailable",
                                            "message": matching["error"]["message"],
                                            "http_status": None})
                    summary["unavailable_inputs"] += 1
                elif args.max_calls is not None and summary["calls"] >= args.max_calls:
                    inference.update(status="not_run_limit", not_run_reason="call_limit")
                    summary["skipped_rows"] += 1
                else:
                    stage = "image_input"
                    try:
                        encoded = load_vlm_image(matching)
                    except VLMImageInputError as error:
                        inference.update(status="input_invalid", error_stage="image_input",
                                         error={"category": error.category, "message": error.message,
                                                "http_status": None})
                        summary["invalid_inputs"] += 1
                        summary["failures"] += 1
                        categories[error.category] += 1
                    else:
                        inference["verified_image_sha256"] = encoded.source_image_sha256
                        inference["image_encoding"] = encoded.to_dict()
                        stage = "inference"
                        if client is None:
                            client = OllamaClient(config)
                        summary["calls"] += 1
                        result = decide_vlm(encoded.image_base64, client)
                        inference.update(result.to_dict())
                        inference["status"] = "succeeded" if result.ok else "failed"
                        if result.ok:
                            summary["successes"] += 1
                        else:
                            inference["error_stage"] = "ollama"
                            summary["inference_failures"] += 1
                            summary["failures"] += 1
                            categories[inference["error"]["category"]] += 1
                inference["completed_at"] = datetime.now(timezone.utc).isoformat()
                stage = "output"
                write_started = True
                output.write(json.dumps({**row, "vlm_inference": inference}, ensure_ascii=False,
                                        sort_keys=True, allow_nan=False) + "\n")
                output.flush()
                summary["rows_written"] += 1
            stage = "output"
        summary["complete"] = True
        unsuccessful = summary["failures"] + summary["unavailable_inputs"] + summary["skipped_rows"]
        summary["status"] = "complete_with_unsuccessful_rows" if unsuccessful else "complete"
    except (OSError, ValueError, RuntimeError, RecursionError, KeyboardInterrupt) as error:
        if write_started:
            summary["status"] = "partial"
        summary["error"] = {"stage": stage, "category": type(error).__name__,
                            "message": str(error) or "interrupted"}
        print(f"VLM replay {summary['status']} ({stage}): {summary['error']['message']}", file=sys.stderr)
    summary["failure_categories"] = dict(categories)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
    if not summary["complete"]:
        return 2
    return 0 if summary["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
