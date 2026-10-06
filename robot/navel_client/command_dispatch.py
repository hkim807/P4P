"""Validate server commands and report simulated execution, without SDK actions."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from typing import Any, Callable

from robot.navel_client.decision_dispatch import DecisionRejected, parse_decision


logger = logging.getLogger(__name__)


class CommandRejected(ValueError):
    """A command is stale, mismatched, or outside the fake executor contract."""


def _integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def parse_command(payload: Mapping[str, Any], observation: Mapping[str, Any],
                  now_us: int, max_age_us: int, *,
                  known_command: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    try:
        decision = parse_decision(payload, observation, now_us, max_age_us)
    except DecisionRejected as error:
        raise CommandRejected(str(error)) from error
    command = payload.get("robot_command")
    if command is None:
        return None
    if not isinstance(command, Mapping):
        raise CommandRejected("command_not_object")
    required_strings = ("command_id", "session_id", "lock_id", "source_state_id",
                        "source_decision_id", "action")
    if (type(command.get("schema_version")) is not int or command["schema_version"] != 1
            or command.get("command_version") != "robot-command-v1"
            or any(not isinstance(command.get(key), str) or not command[key]
                   for key in required_strings)):
        raise CommandRejected("command_contract_invalid")
    lock = payload.get("target_lock")
    social = payload.get("social_state")
    if (not isinstance(lock, Mapping) or not isinstance(social, Mapping)
            or lock.get("status") != "LOCKED"
            or command["session_id"] != decision.session_id
            or command["lock_id"] != decision.lock_id
            or command["action"] not in {"APPROACH", "ENGAGE"}
            or command["action"] != decision.decision
            or not _integer(command.get("target_uid"), 1)
            or not _integer(command.get("target_track_epoch"), 1)
            or (command["target_uid"], command["target_track_epoch"])
            != (decision.target_uid, decision.target_track_epoch)):
        raise CommandRejected("command_not_authorized_by_current_lock")
    if (not _integer(social.get("ingest_sequence"), 1)
            or not _integer(command.get("source_frame_sequence"), 1)
            or not _integer(command.get("source_robot_timestamp_us"))
            or not _integer(command.get("expires_at_robot_us"))
            or not _integer(command.get("execution_lease_us"), 1)
            or command["execution_lease_us"] > 120_000_000
            or not 0 < command["expires_at_robot_us"] - command["source_robot_timestamp_us"] <= 5_000_000
            or command["source_robot_timestamp_us"] > observation["timestamp"]
            or command["source_frame_sequence"] > social.get("ingest_sequence", -1)
            or now_us > command["expires_at_robot_us"]):
        raise CommandRejected("command_expired_or_invalid_lease")
    if known_command is None:
        # The first response carrying a command may be lost; accept its later
        # retransmission only while the same lock still authorizes that action.
        if (command["source_state_id"] != f"{decision.session_id}:{command['source_frame_sequence']}"
                or command["source_decision_id"] != f"{command['source_state_id']}:target-lock-v2"
                or (command["source_frame_sequence"] == social["ingest_sequence"]
                    and command["source_robot_timestamp_us"] != observation["timestamp"])):
            raise CommandRejected("new_command_source_mismatch")
    elif dict(command) != dict(known_command):
        raise CommandRejected("conflicting_duplicate_command")
    expected_id = (f"{command['lock_id']}:{command['action']}" if command["action"] == "ENGAGE"
                   else f"{command['lock_id']}:{command['action']}:{command['target_uid']}:{command['target_track_epoch']}")
    if command["command_id"] != expected_id:
        raise CommandRejected("command_id_mismatch")
    return dict(command)


class FakeCommandExecutor:
    """A bounded ledger of simulated actions and replayable feedback events."""

    def __init__(self, *, max_age_s: float = 1.0, max_commands: int = 256,
                 monotonic_us: Callable[[], int] = lambda: time.monotonic_ns() // 1000):
        if not 0 < max_age_s <= 5 or not 1 <= max_commands <= 10000:
            raise ValueError("invalid fake executor limits")
        self.max_age_us = round(max_age_s * 1_000_000)
        self.max_commands = max_commands
        self.monotonic_us = monotonic_us
        self.session_id: str | None = None
        self.commands: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}

    def accept(self, payload: Mapping[str, Any], observation: Mapping[str, Any]) -> list[dict[str, Any]]:
        now_us = self.monotonic_us()
        supplied = payload.get("robot_command")
        command_id = supplied.get("command_id") if isinstance(supplied, Mapping) else None
        known = self.commands.get(command_id) if isinstance(command_id, str) else None
        command = parse_command(payload, observation, now_us, self.max_age_us,
                                known_command=known)
        if command is None:
            return []
        if self.session_id is not None and command["session_id"] != self.session_id:
            raise CommandRejected("session_changed_restart_client")
        if known is not None:
            return self.events[command_id]
        if len(self.commands) >= self.max_commands:
            raise CommandRejected("fake_executor_capacity_reached")
        self.session_id = command["session_id"]
        self.commands[command_id] = command
        events = []
        for sequence, (status, reason) in enumerate((("RECEIVED", "COMMAND_VALIDATED"),
                                                      ("SIMULATED", "NO_PHYSICAL_ACTION")), 1):
            events.append({
                "schema_version": 1, "event_version": "execution-event-v1",
                "event_id": f"{command_id}:{sequence}", "command_id": command_id,
                "session_id": command["session_id"], "lock_id": command["lock_id"],
                "source_state_id": command["source_state_id"], "sequence": sequence,
                "status": status, "reason": reason, "robot_timestamp_us": now_us,
            })
        self.events[command_id] = events
        logger.info("command_dry_run=%s", json.dumps({
            "command_id": command_id, "action": command["action"],
            "target_uid": command["target_uid"], "lock_id": command["lock_id"],
            "status": "SIMULATED"}, separators=(",", ":")))
        return events
