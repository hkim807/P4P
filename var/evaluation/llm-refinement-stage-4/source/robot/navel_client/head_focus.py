"""Immediate, provisional head focus driven by robot-local perception frames."""

from __future__ import annotations

import asyncio
import inspect
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
        command_interval_s: float | None = None,
        raise_on_error: bool = False,
        select_first_visible: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.robot = robot
        self.magnitude = magnitude
        self.grace_s = grace_s
        self.retry_s = retry_s
        self.server_lock_timeout_s = server_lock_timeout_s
        self.clock = clock
        self.command_interval_s = command_interval_s
        self.raise_on_error = raise_on_error
        self.select_first_visible = select_first_visible
        self.last_command_at = -float("inf")
        self.suspended = False
        self._command_task = None
        self._command_uid = None
        self._command_error = None
        self._command_failed = asyncio.Event()
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

    def observe(self, perception: Any) -> Any:
        """Process a received frame without waiting for SDK head completion."""
        if self._command_task is not None and self._command_task.done():
            self._command_finished(self._command_task)
        if self._command_error is not None:
            raise self._command_error
        if self.suspended:
            return
        now = self.clock()
        self._expire_server_lock(now)
        persons = getattr(perception, "persons", None) or ()
        uids = [getattr(person, "uid", None) for person in persons]
        visible = {uid for uid in uids if isinstance(uid, int) and not isinstance(uid, bool) and uid >= 0}

        if self.uid is not None and self.uid in visible and (
                self.server_lock_uid is None or self.uid == self.server_lock_uid):
            self.last_seen_at = now
            if self.command_interval_s is None:
                return
        elif self.uid is not None:
            if (self.last_seen_at is not None and now - self.last_seen_at < self.grace_s
                    and (self.server_lock_uid is None or self.uid == self.server_lock_uid)):
                return
            logger.info("head_focus=expired uid=%s", self.uid)
            self.uid = None
            self.last_seen_at = None

        if (self._command_task is not None or now < self.next_retry_at or (self.command_interval_s is not None
                and now - self.last_command_at < self.command_interval_s)):
            return
        if self.uid in visible:
            uid = self.uid
        elif self.server_lock_uid is not None:
            if self.server_lock_uid not in visible:
                return
            uid = self.server_lock_uid
        else:
            if self.select_first_visible:
                # Route baseline follows the reference's first detected valid UID.
                uids = [uid for uid in uids if type(uid) is int and uid >= 0]
            # Ordinary focus keeps its existing conservative acquisition rule.
            if not uids or (not self.select_first_visible and (len(uids) != 1 or len(visible) != 1)):
                return
            uid = uids[0]
        try:
            command = self.robot.look_at_person(uid, self.magnitude)
        except Exception as error:
            if self.raise_on_error:
                raise
            self.next_retry_at = now + self.retry_s
            logger.warning("head_focus=command_failed uid=%s error=%s", uid, error)
            return
        self.uid = uid
        self.last_seen_at = now
        self.last_command_at = now
        logger.info("head_focus=acquired uid=%s magnitude=%s", uid, self.magnitude)
        if inspect.isawaitable(command):
            self._command_task = asyncio.ensure_future(command)
            self._command_uid = uid
            self._command_task.add_done_callback(self._command_finished)
            return self._command_task
        return command

    def _command_finished(self, task) -> None:
        if task is not self._command_task:
            return  # Already observed synchronously before the callback ran.
        uid = self._command_uid
        self._command_task = self._command_uid = None
        if task.cancelled():
            error = None if self.suspended else RuntimeError("head command cancelled unexpectedly")
        else:
            error = task.exception()
        if error is None:
            return
        if self.raise_on_error:
            self._command_error = error
            self._command_failed.set()
        else:
            self.uid = self.last_seen_at = None
            self.next_retry_at = self.clock() + self.retry_s
            logger.warning("head_focus=command_failed uid=%s error=%s", uid, error)

    async def watch_failures(self) -> None:
        """Wake route supervision even if the next perception read is blocked."""
        await self._command_failed.wait()
        raise self._command_error

    def suspend(self) -> None:
        """Yield baseline head ownership to a future behaviour controller."""
        self.suspended = True
        self.stop()

    async def suspend_and_settle(self) -> None:
        """Prevent new baseline commands and settle an in-flight SDK command."""
        self.suspend()
        task = self._command_task
        if task is not None:
            if not task.done() and not getattr(task, "cancelling", lambda: 0)():
                task.cancel()
            done, _ = await asyncio.wait([task], timeout=2.0)
            if not done:
                raise RuntimeError("baseline head command did not settle")
            await asyncio.gather(task, return_exceptions=True)
            self._command_finished(task)
        if self._command_error is not None:
            raise self._command_error

    def tick(self) -> None:
        """Expire local selection if perception stops delivering frames."""
        if self._command_error is not None:
            raise self._command_error
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
