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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.robot = robot
        self.magnitude = magnitude
        self.grace_s = grace_s
        self.retry_s = retry_s
        self.clock = clock
        self.uid: int | None = None
        self.last_seen_at: float | None = None
        self.next_retry_at = 0.0

    def observe(self, perception: Any) -> None:
        """Process a frame before it enters the rate-limited HTTP queue."""
        now = self.clock()
        persons = getattr(perception, "persons", None) or ()
        uids = [getattr(person, "uid", None) for person in persons]
        visible = {uid for uid in uids if isinstance(uid, int) and not isinstance(uid, bool) and uid >= 0}

        if self.uid is not None and self.uid in visible:
            self.last_seen_at = now
            return
        if self.uid is not None:
            if self.last_seen_at is not None and now - self.last_seen_at < self.grace_s:
                return
            logger.info("head_focus=expired uid=%s", self.uid)
            self.uid = None
            self.last_seen_at = None

        # Unknown UIDs or other visible people also make acquisition ambiguous.
        if len(uids) != 1 or len(visible) != 1 or now < self.next_retry_at:
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
