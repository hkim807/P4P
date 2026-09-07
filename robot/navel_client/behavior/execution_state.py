"""Read-only snapshots of the most recent Navel behavior lifecycle."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from threading import RLock

from app.domain.models import Action, BehaviorIntent


class BehaviorExecutionStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    DISPATCHED = "DISPATCHED"
    DRY_RUN_COMPLETED = "DRY_RUN_COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class BehaviorExecutionSnapshot:
    action: Action
    status: BehaviorExecutionStatus
    decision_id: str
    target_human_id: str | None
    updated_at_us: int
    latest_error: str | None = None


class BehaviorExecutionState:
    """Own mutable lifecycle state while exposing only immutable snapshots."""

    def __init__(
        self,
        timestamp_us: Callable[[], int] = lambda: time.monotonic_ns() // 1_000,
    ) -> None:
        self._timestamp_us = timestamp_us
        self._snapshot: BehaviorExecutionSnapshot | None = None
        self._lock = RLock()

    @property
    def snapshot(self) -> BehaviorExecutionSnapshot | None:
        with self._lock:
            return self._snapshot

    def accept(self, intent: BehaviorIntent) -> None:
        with self._lock:
            self._snapshot = BehaviorExecutionSnapshot(
                action=Action(intent.action),
                status=BehaviorExecutionStatus.ACCEPTED,
                decision_id=intent.decision_id,
                target_human_id=intent.target_human_id,
                updated_at_us=self._timestamp_us(),
            )

    def mark_dispatched(self) -> None:
        self._update(BehaviorExecutionStatus.DISPATCHED)

    def mark_completed(self) -> None:
        self._update(BehaviorExecutionStatus.DRY_RUN_COMPLETED)

    def mark_failed(self, error: str) -> None:
        self._update(BehaviorExecutionStatus.FAILED, latest_error=error)

    def _update(
        self,
        status: BehaviorExecutionStatus,
        *,
        latest_error: str | None = None,
    ) -> None:
        with self._lock:
            if self._snapshot is None:
                raise RuntimeError(
                    "cannot update behavior execution before intent acceptance"
                )
            self._snapshot = replace(
                self._snapshot,
                status=status,
                updated_at_us=self._timestamp_us(),
                latest_error=latest_error,
            )
