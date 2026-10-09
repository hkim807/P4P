"""Deterministic head commands for an executable single-trial route."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
from typing import Any


logger = logging.getLogger(__name__)


class TrialHeadController:
    """Own neutral and person-directed head commands during one trial."""

    def __init__(self, robot: Any, sdk: Any, *, neutral_distance_m: float = 2.0,
                 neutral_settle_s: float = 2.0, look_settle_s: float = 0.5,
                 glance_s: float = 1.0) -> None:
        values = (neutral_distance_m, neutral_settle_s, look_settle_s, glance_s)
        if any(not math.isfinite(value) or value < 0 for value in values) or neutral_distance_m == 0:
            raise ValueError("trial head distances must be positive and timings nonnegative")
        self.robot = robot
        self.sdk = sdk
        self.neutral_distance_m = neutral_distance_m
        self.neutral_settle_s = neutral_settle_s
        self.look_settle_s = look_settle_s
        self.glance_s = glance_s

    def preflight(self) -> None:
        for method in ("look_at_cart", "look_at_person"):
            if not callable(getattr(self.robot, method, None)):
                raise ValueError(f"trial head control requires SDK robot.{method}")
        if not hasattr(self.sdk, "CartSys3d") or not hasattr(self.sdk, "CoordSystem"):
            raise ValueError("trial head control requires SDK CartSys3d and CoordSystem")
        if not hasattr(self.sdk.CoordSystem, "HEAD_STRAIGHT"):
            raise ValueError("trial head control requires SDK CoordSystem.HEAD_STRAIGHT")
        if (not callable(getattr(self.robot, "head_overlay_degrees", None))
                and (not callable(getattr(self.robot, "head_overlay", None))
                     or not hasattr(self.sdk, "Bryan"))):
            raise ValueError("trial head control requires an SDK head-overlay method")

    async def _command(self, result: Any) -> None:
        if inspect.isawaitable(result):
            await result

    async def _clear_overlay(self) -> str:
        degrees = getattr(self.robot, "head_overlay_degrees", None)
        if callable(degrees):
            await self._command(degrees(0.0, 0.0, 0.0))
            return "head_overlay_degrees"
        await self._command(self.robot.head_overlay(self.sdk.Bryan(0.0, 0.0, 0.0)))
        return "head_overlay"

    async def neutral(self, *, settle_s: float | None = None) -> None:
        """Replace the current focus with a point directly ahead."""
        overlay = await self._clear_overlay()
        target = self.sdk.CartSys3d(
            self.sdk.CoordSystem.HEAD_STRAIGHT, self.neutral_distance_m, 0.0, 0.0)
        await self._command(self.robot.look_at_cart(target, 1.0))
        delay = self.neutral_settle_s if settle_s is None else settle_s
        if delay:
            await asyncio.sleep(delay)
        logger.info("trial_head=neutral overlay=%s", overlay)

    async def look_at_person(self, uid: int, *, settle_s: float | None = None) -> None:
        if type(uid) is not int or uid < 0:
            raise RuntimeError("trial head command requires one valid person UID")
        await self._command(self.robot.look_at_person(uid, 1.0))
        delay = self.look_settle_s if settle_s is None else settle_s
        if delay:
            await asyncio.sleep(delay)
        logger.info("trial_head=person uid=%s", uid)

    async def glance(self, uid: int) -> None:
        await self.look_at_person(uid)
        if self.glance_s:
            await asyncio.sleep(self.glance_s)
        await self.neutral()
