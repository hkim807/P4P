"""Immutable snapshots of the accepted execution, never incoming rejections."""
import time
from dataclasses import dataclass, replace
from enum import Enum
from robot.navel_client.behavior.intent import NavelAction
from robot.navel_client.behavior.results import BehaviorExecutionResult


class BehaviorExecutionStatus(str, Enum):
    ACCEPTED = 'ACCEPTED'
    RUNNING = 'RUNNING'
    DRY_RUN_COMPLETED = 'DRY_RUN_COMPLETED'
    COMPLETED = 'COMPLETED'
    CANCELLED = 'CANCELLED'
    FAILED = 'FAILED'


@dataclass(frozen=True)
class BehaviorExecutionSnapshot:
    action: NavelAction
    status: BehaviorExecutionStatus
    decision_id: str
    target_human_id: str | None
    updated_at_us: int
    latest_error: str | None = None
    result: BehaviorExecutionResult | None = None
    resolved_target: str | None = None


class BehaviorExecutionState:
    def __init__(self, timestamp_us=lambda: time.monotonic_ns()//1000):
        self._timestamp_us = timestamp_us
        self.snapshot = None

    def accept(self, intent):
        self.snapshot = BehaviorExecutionSnapshot(intent.action, BehaviorExecutionStatus.ACCEPTED,
            intent.decision_id, intent.target_human_id, self._timestamp_us())

    def update(self, status, **fields):
        if self.snapshot is None:
            raise RuntimeError('No accepted execution')
        self.snapshot = replace(self.snapshot, status=status,
                                updated_at_us=self._timestamp_us(), **fields)
