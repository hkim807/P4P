"""Run the LLM policy once on one saved SocialState JSON object."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.inference.ollama import OllamaClient, OllamaConfig
from app.policy.llm import PROMPT_VERSION, decide_llm
from app.state.social_models import SocialState


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate SocialState JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid SocialState JSON constant: {value}")


def read_social_state(path: str | Path) -> SocialState:
    """Accept a complete file containing one object, never JSONL or replay input."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"),
                         object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if not isinstance(payload, dict):
        raise ValueError("input must be one SocialState JSON object")
    return SocialState.model_validate(payload)


def _print_result(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2))


def _input_failure(category: str, message: str, model: str) -> dict[str, Any]:
    return {
        "source_state_id": None, "session_id": None, "source_robot_timestamp_us": None,
        "prompt_version": PROMPT_VERSION, "ok": False, "decision": None,
        "error": {"category": category, "message": message, "http_status": None},
        "requested_model": model, "returned_model": None, "raw_content": None,
        "request_duration_s": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("social_state", help="File containing exactly one SocialState JSON object")
    parser.add_argument("--base-url", required=True, help="Ollama HTTP(S) origin")
    parser.add_argument("--model", required=True, help="Model already available on that server")
    parser.add_argument("--timeout", type=float, default=30.0, help="Finite positive HTTP timeout in seconds")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--num-predict", type=int, help="Positive maximum generated token count")
    args = parser.parse_args(argv)
    try:
        state = read_social_state(args.social_state)
    except (OSError, ValueError, RecursionError) as error:
        _print_result(_input_failure("invalid_input", str(error), args.model))
        return 2
    try:
        config = OllamaConfig(base_url=args.base_url, model=args.model, timeout_seconds=args.timeout,
                              temperature=args.temperature, seed=args.seed, num_predict=args.num_predict)
    except ValueError as error:
        _print_result(_input_failure("invalid_configuration", str(error), args.model))
        return 2
    result = decide_llm(state, OllamaClient(config))
    _print_result(result.to_dict())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
