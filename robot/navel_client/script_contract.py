"""Small standard-library helpers for user-provided Navel action scripts."""

from __future__ import annotations

import json
import sys
from typing import Any


INTERFACE_VERSION = "navel-action-script-v1"


def read_request(*, expected_action: str | None = None) -> dict[str, Any]:
    """Read one executor request; raise ValueError before touching hardware."""
    try:
        request = json.loads(sys.stdin.readline())
    except json.JSONDecodeError as error:
        raise ValueError("invalid action request JSON") from error
    if (not isinstance(request, dict)
            or request.get("interface_version") != INTERFACE_VERSION
            or not isinstance(request.get("reason"), str)
            or request.get("observation") is not None
            and not isinstance(request["observation"], dict)):
        raise ValueError("invalid action request")
    command = request.get("command")
    if expected_action is not None:
        if expected_action not in {"APPROACH", "ENGAGE"}:
            raise ValueError("unsupported expected action")
        if (not isinstance(command, dict) or command.get("action") != expected_action
                or not isinstance(command.get("command_id"), str)
                or not isinstance(command.get("lock_id"), str)
                or type(command.get("target_uid")) is not int or command["target_uid"] <= 0
                or type(command.get("target_track_epoch")) is not int
                or command["target_track_epoch"] < 1):
            raise ValueError("action command mismatch")
    elif command is not None and not isinstance(command, dict):
        raise ValueError("invalid command")
    return request


def report_success() -> None:
    print('{"ok":true}', flush=True)


def report_failure(reason: str) -> None:
    print(json.dumps({"ok": False, "reason": reason[:128]}, separators=(",", ":")), flush=True)
