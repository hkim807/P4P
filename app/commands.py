"""Correlated, bounded command proposals and simulated execution feedback."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from app.state.social_models import SocialState, StrictModel


class CommandConfig(StrictModel):
    model_config = StrictModel.model_config | {"frozen": True}
    max_source_age_s: float = Field(default=1.0, gt=0, le=5)
    execution_lease_s: float = Field(default=2.0, gt=0, le=120)
    max_commands_per_session: int = Field(default=256, ge=1, le=10000)

    @classmethod
    def from_file(cls, path: str | Path) -> "CommandConfig":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class RobotCommand(StrictModel):
    schema_version: Literal[1] = 1
    command_version: Literal["robot-command-v1"] = "robot-command-v1"
    command_id: str
    session_id: str
    lock_id: str
    source_state_id: str
    source_decision_id: str
    source_frame_sequence: int = Field(ge=1)
    source_robot_timestamp_us: int = Field(ge=0)
    action: Literal["APPROACH", "ENGAGE"]
    target_uid: int = Field(ge=0)
    target_track_epoch: int = Field(ge=1)
    expires_at_robot_us: int = Field(ge=0)
    execution_lease_us: int = Field(gt=0)


class ExecutionEvent(StrictModel):
    schema_version: Literal[1] = 1
    event_version: Literal["execution-event-v1"] = "execution-event-v1"
    event_id: str
    command_id: str
    session_id: str
    lock_id: str
    source_state_id: str
    sequence: int = Field(ge=1)
    status: Literal["RECEIVED", "STARTED", "SIMULATED", "COMPLETED",
                    "FAILED", "CANCELLED", "REJECTED"]
    reason: str = Field(min_length=1, max_length=128)
    robot_timestamp_us: int = Field(ge=0)


class FeedbackRejected(ValueError):
    """An execution event is unknown, conflicting, or out of order."""


class CommandPlanner:
    """Issue once per lock/action key, resend one ID until feedback or expiry."""

    def __init__(self, config: CommandConfig | None = None, execution_trace=None) -> None:
        self.config = config or CommandConfig()
        self.execution_trace = execution_trace
        self.session_id: str | None = None
        self.commands: dict[str, RobotCommand] = {}
        self.issued_keys: dict[tuple, str] = {}
        self.events: dict[str, list[ExecutionEvent]] = {}
        self.failed_locks: set[str] = set()

    def plan(self, state: SocialState | dict, lock: dict) -> dict | None:
        state = SocialState.model_validate(state)
        if self.session_id is None:
            self.session_id = state.session_id
        elif self.session_id != state.session_id:
            raise ValueError("command planner cannot cross sessions")
        effective = lock["effective_decision"]
        action = effective["decision"]
        if (lock["status"] != "LOCKED" or action not in ("APPROACH", "ENGAGE")
                or lock["source_state_id"] != state.state_id
                or lock["session_id"] != state.session_id):
            return None
        uid, epoch, lock_id = effective["target_uid"], effective["target_track_epoch"], lock["lock_id"]
        if (uid is None or uid == 0 or epoch is None or not lock_id
                or lock_id in self.failed_locks
                or (uid, epoch) != (lock["target_uid"], lock["target_track_epoch"])
                or not any(p.uid == uid and p.track_epoch == epoch and p.visibility == "OBSERVED"
                           for p in state.people)):
            return None
        key = (lock_id, action) if action == "ENGAGE" else (lock_id, action, uid, epoch)
        command_id = self.issued_keys.get(key)
        if command_id is not None:
            command = self.commands[command_id]
            if (self.events.get(command_id)
                    and self.events[command_id][-1].status in
                    ("SIMULATED", "COMPLETED", "FAILED", "CANCELLED", "REJECTED")):
                return None
            return (command.model_dump(mode="json")
                    if state.robot_timestamp_us <= command.expires_at_robot_us else None)
        if len(self.commands) >= self.config.max_commands_per_session:
            raise ValueError("command capacity reached; refusing new command")
        command_id = (f"{lock_id}:{action}" if action == "ENGAGE"
                      else f"{lock_id}:{action}:{uid}:{epoch}")
        command = RobotCommand(
            command_id=command_id, session_id=state.session_id, lock_id=lock_id,
            source_state_id=state.state_id, source_decision_id=effective["decision_id"],
            source_frame_sequence=state.ingest_sequence,
            source_robot_timestamp_us=state.robot_timestamp_us, action=action,
            target_uid=uid, target_track_epoch=epoch,
            expires_at_robot_us=state.robot_timestamp_us + round(self.config.max_source_age_s * 1_000_000),
            execution_lease_us=round(self.config.execution_lease_s * 1_000_000),
        )
        self.commands[command_id] = command
        self.issued_keys[key] = command_id
        return command.model_dump(mode="json")

    def record_event(self, payload: ExecutionEvent | dict) -> bool:
        """Return True for a duplicate; write before advancing the ledger."""
        event = ExecutionEvent.model_validate(payload)
        command = self.commands.get(event.command_id)
        if command is None:
            raise FeedbackRejected("unknown_command")
        if ((event.session_id, event.lock_id, event.source_state_id)
                != (command.session_id, command.lock_id, command.source_state_id)):
            raise FeedbackRejected("command_correlation_mismatch")
        if event.event_id != f"{event.command_id}:{event.sequence}":
            raise FeedbackRejected("event_id_mismatch")
        previous = self.events.get(event.command_id, [])
        if event.sequence <= len(previous):
            if event == previous[event.sequence - 1]:
                return True
            raise FeedbackRejected("conflicting_duplicate_event")
        if event.sequence != len(previous) + 1:
            raise FeedbackRejected("event_sequence_gap")
        if not previous and event.status not in ("RECEIVED", "REJECTED"):
            raise FeedbackRejected("first_event_must_receive_or_reject")
        if previous:
            allowed = ({"RECEIVED": {"STARTED", "SIMULATED", "REJECTED", "FAILED", "CANCELLED"},
                        "STARTED": {"COMPLETED", "FAILED", "CANCELLED"}}
                       .get(previous[-1].status, set()))
            if event.status not in allowed:
                raise FeedbackRejected("invalid_event_transition")
            if event.robot_timestamp_us < previous[-1].robot_timestamp_us:
                raise FeedbackRejected("event_timestamp_regressed")
        if event.robot_timestamp_us < command.source_robot_timestamp_us:
            raise FeedbackRejected("event_before_source_frame")
        if self.execution_trace is not None:
            self.execution_trace.write(event.model_dump(mode="json"))
        self.events.setdefault(event.command_id, []).append(event)
        if event.status in ("FAILED", "CANCELLED", "REJECTED"):
            self.failed_locks.add(command.lock_id)
        return False
