"""Immediate, provisional head focus driven by robot-local perception frames."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any


logger = logging.getLogger(__name__)


class HeadFocusController:
    """Select one visible SDK UID without treating it as a persistent identity.

    The SDK documents look_at_person as a setter, but does not document a
    matching cancel command. Expiry here clears only our local selection.
    """

    def __init__(
        self,
        robot: Any,
        *,
        magnitude: float = 0.5,
        grace_s: float = 0.75,
        retry_s: float = 1.0,
        server_lock_timeout_s: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.robot = robot
        self.magnitude = magnitude
        self.grace_s = grace_s
        self.retry_s = retry_s
        self.server_lock_timeout_s = server_lock_timeout_s
        self.clock = clock
        self.uid: int | None = None
        self.last_seen_at: float | None = None
        self.next_retry_at = 0.0
        self.server_lock_uid: int | None = None
        self.server_lock_id: str | None = None
        self.server_lock_received_at: float | None = None

    def apply_server_lock(self, lock: dict[str, Any]) -> None:
        """Pin acquisition to the validated server lock while it is current."""
        if lock["status"] in ("LOCKED", "MISSING", "TENTATIVE_RETURN", "AMBIGUOUS"):
            self.server_lock_uid = lock["target_uid"]
            self.server_lock_id = lock["lock_id"]
            self.server_lock_received_at = self.clock()
        else:
            self.server_lock_uid = None
            self.server_lock_id = None
            self.server_lock_received_at = None

    def _expire_server_lock(self, now: float) -> None:
        if (self.server_lock_received_at is not None
                and now - self.server_lock_received_at >= self.server_lock_timeout_s):
            logger.info("head_focus=server_lock_expired lock_id=%s", self.server_lock_id)
            self.server_lock_uid = None
            self.server_lock_id = None
            self.server_lock_received_at = None

    def observe(self, perception: Any) -> None:
        """Process a frame before it enters the rate-limited HTTP queue."""
        now = self.clock()
        self._expire_server_lock(now)
        persons = getattr(perception, "persons", None) or ()
        uids = [getattr(person, "uid", None) for person in persons]
        visible = {uid for uid in uids if isinstance(uid, int) and not isinstance(uid, bool) and uid >= 0}

        if self.uid is not None and self.uid in visible and (
                self.server_lock_uid is None or self.uid == self.server_lock_uid):
            self.last_seen_at = now
            return
        if self.uid is not None:
            if (self.last_seen_at is not None and now - self.last_seen_at < self.grace_s
                    and (self.server_lock_uid is None or self.uid == self.server_lock_uid)):
                return
            logger.info("head_focus=expired uid=%s", self.uid)
            self.uid = None
            self.last_seen_at = None

        if now < self.next_retry_at:
            return
        if self.server_lock_uid is not None:
            if self.server_lock_uid not in visible:
                return
            uid = self.server_lock_uid
        else:
            # Unknown UIDs or other visible people make new acquisition ambiguous.
            if len(uids) != 1 or len(visible) != 1:
                return
            uid = next(iter(visible))
        try:
            self.robot.look_at_person(uid, self.magnitude)
        except Exception as error:
            self.next_retry_at = now + self.retry_s
            logger.warning("head_focus=command_failed uid=%s error=%s", uid, error)
            return
        self.uid = uid
        self.last_seen_at = now
        logger.info("head_focus=acquired uid=%s magnitude=%s", uid, self.magnitude)

    def tick(self) -> None:
        """Expire local selection if perception stops delivering frames."""
        self._expire_server_lock(self.clock())
        if self.uid is not None and self.last_seen_at is not None:
            if self.clock() - self.last_seen_at >= self.grace_s:
                logger.info("head_focus=expired uid=%s", self.uid)
                self.uid = None
                self.last_seen_at = None

    def stop(self) -> None:
        """Discard local selection; SDK focus release is not documented."""
        if self.uid is not None:
            logger.info("head_focus=stopped uid=%s", self.uid)
        self.uid = None
        self.last_seen_at = None
        self.server_lock_uid = None
        self.server_lock_id = None
        self.server_lock_received_at = None
