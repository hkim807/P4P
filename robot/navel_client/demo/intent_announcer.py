"""Bounded, demo-only speech sink for validated behavior intents."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from robot.navel_client.behavior.intent import (
    NavelBehaviorIntent,
    NavelIntentParseError,
)


class IntentAnnouncer:
    """Parse server responses and announce eligible actions one at a time."""

    def __init__(
        self,
        robot: Any,
        *,
        speech_enabled: bool = False,
        cooldown_s: float = 8.0,
        monotonic: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        if cooldown_s <= 0:
            raise ValueError("cooldown_s must be positive")
        self._robot = robot
        self._speech_enabled = speech_enabled
        self._cooldown_s = cooldown_s
        self._monotonic = monotonic
        self._logger = logger or logging.getLogger("robot.navel_client.demo")
        self._queue: asyncio.Queue[NavelBehaviorIntent] = asyncio.Queue(maxsize=1)
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._last_action: str | None = None
        self._last_announced_at: float | None = None

    def handle_response(self, payload: Mapping[str, Any]) -> None:
        if "behavior_intent" not in payload:
            self._logger.warning("demo intent ignored: response has no behavior_intent")
            return
        raw_intent = payload.get("behavior_intent")
        if raw_intent is None:
            error = payload.get("error")
            if error is None:
                self._logger.info("demo intent: no decision")
            else:
                self._logger.warning("demo intent: no decision server_error=%s", error)
            return
        try:
            intent = NavelBehaviorIntent.from_payload(raw_intent)
        except NavelIntentParseError as error:
            self._logger.warning("demo intent ignored: invalid behavior_intent: %s", error)
            return

        if intent.decision_id in self._seen_ids:
            self._logger.info("demo intent ignored: duplicate decision_id=%s", intent.decision_id)
            return
        self._remember(intent.decision_id)

        now = self._monotonic()
        if (
            intent.action.value == self._last_action
            and self._last_announced_at is not None
            and now - self._last_announced_at < self._cooldown_s
        ):
            self._logger.info(
                "demo intent ignored: action=%s cooldown_remaining_s=%.1f",
                intent.action.value,
                self._cooldown_s - (now - self._last_announced_at),
            )
            return

        if self._queue.full():
            replaced = self._queue.get_nowait()
            self._logger.info(
                "demo intent replaced: pending_action=%s new_action=%s",
                replaced.action.value,
                intent.action.value,
            )
        self._queue.put_nowait(intent)

    async def run(self) -> None:
        while True:
            intent = await self._queue.get()
            now = self._monotonic()
            if (
                intent.action.value == self._last_action
                and self._last_announced_at is not None
                and now - self._last_announced_at < self._cooldown_s
            ):
                self._logger.info(
                    "demo intent ignored: action=%s cooldown_remaining_s=%.1f",
                    intent.action.value,
                    self._cooldown_s - (now - self._last_announced_at),
                )
                continue
            message = f"Behavior {intent.action.value} is decided!"
            try:
                if self._speech_enabled:
                    await self._robot.say(message)
                else:
                    print(message)
            except Exception:
                self._logger.exception(
                    "demo announcement failed: action=%s", intent.action.value
                )
                raise
            self._last_action = intent.action.value
            self._last_announced_at = self._monotonic()

    def _remember(self, decision_id: str) -> None:
        if len(self._seen_order) >= 128:
            self._seen_ids.remove(self._seen_order.popleft())
        self._seen_order.append(decision_id)
        self._seen_ids.add(decision_id)
