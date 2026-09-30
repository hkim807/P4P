"""Admission results are independent of the active execution lifecycle."""
from dataclasses import dataclass
from enum import Enum
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.actions.approach_human import ApproachResult
from robot.navel_client.behavior.actions.yield_to_person import YieldResult


@dataclass(frozen=True)
class BehaviorExecutionResult:
    action: NavelAction
    command_type: str
    dry_run: bool
    parameters: tuple[tuple[str, object], ...]
    decision_id: str | None = None
    requested_target: str | None = None
    resolved_target: str | None = None
    approach: ApproachResult | None = None
    yield_result: YieldResult | None = None


class BehaviorHandlingStatus(str, Enum):
    ACCEPTED = 'ACCEPTED'
    NO_INTENT = 'NO_INTENT'
    INVALID_INTENT = 'INVALID_INTENT'
    UNSUPPORTED_ACTION = 'UNSUPPORTED_ACTION'
    DUPLICATE = 'DUPLICATE'
    ALREADY_RUNNING = 'ALREADY_RUNNING'
    BUSY = 'BUSY'
    EXPIRED = 'EXPIRED'
    TARGET_UNAVAILABLE = 'TARGET_UNAVAILABLE'
    FAILED = 'FAILED'


@dataclass(frozen=True)
class BehaviorHandlingResult:
    status: BehaviorHandlingStatus
    execution: BehaviorExecutionResult | None = None
    error: str | None = None
    decision_id: str | None = None
    requested_target: str | None = None
