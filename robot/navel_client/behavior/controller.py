"""Safe orchestration boundary for behavior intents returned by the server."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.domain.models import BehaviorIntent
from robot.navel_client.behavior.dispatcher import BehaviorDispatcher
from robot.navel_client.behavior.execution_state import (
    BehaviorExecutionSnapshot,
    BehaviorExecutionState,
)
from robot.navel_client.behavior.mapper import BehaviorIntentMapper
from robot.navel_client.behavior.registry import build_handler_registry
from robot.navel_client.behavior.results import (
    BehaviorHandlingResult,
    BehaviorHandlingStatus,
)


class BehaviorController:
    """Parse the HTTP boundary, map one intent, and dispatch one command."""

    def __init__(
        self,
        robot: Any,
        mapper: BehaviorIntentMapper | None = None,
        dispatcher: BehaviorDispatcher | None = None,
        execution_state: BehaviorExecutionState | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._mapper = mapper if mapper is not None else BehaviorIntentMapper()
        self._dispatcher = (
            dispatcher
            if dispatcher is not None
            else BehaviorDispatcher(build_handler_registry(robot, logger))
        )
        self._execution_state = (
            execution_state
            if execution_state is not None
            else BehaviorExecutionState()
        )
        self._logger = logger or logging.getLogger("robot.navel_client.behavior")

    @property
    def execution_state(self) -> BehaviorExecutionSnapshot | None:
        """Return an immutable snapshot for future RobotContext integration."""
        return self._execution_state.snapshot

    def handle_response(self, payload: Mapping[str, Any]) -> BehaviorHandlingResult:
        if "behavior_intent" not in payload:
            self._logger.warning(
                "[NAVEL BEHAVIOR] server response has no behavior_intent field"
            )
            return BehaviorHandlingResult(
                BehaviorHandlingStatus.NO_INTENT,
                error="server response has no behavior_intent field",
            )
        raw_intent = payload.get("behavior_intent")
        if raw_intent is None:
            return BehaviorHandlingResult(BehaviorHandlingStatus.NO_INTENT)
        try:
            intent = BehaviorIntent.model_validate(raw_intent)
        except ValidationError as error:
            self._logger.warning(
                "[NAVEL BEHAVIOR] invalid behavior_intent rejected: %s",
                error.errors(include_url=False, include_input=False),
            )
            return BehaviorHandlingResult(
                BehaviorHandlingStatus.INVALID_INTENT,
                error="behavior_intent failed schema validation",
            )
        return self.handle(intent)

    def handle(self, intent: BehaviorIntent) -> BehaviorHandlingResult:
        self._execution_state.accept(intent)
        try:
            command = self._mapper.map(intent)
            self._execution_state.mark_dispatched()
            execution = self._dispatcher.dispatch(command)
            self._execution_state.mark_completed()
        except Exception as error:
            self._execution_state.mark_failed(str(error))
            self._logger.error(
                "[NAVEL BEHAVIOR] action=%s dry_run=true handling_failed=%s",
                intent.action,
                error,
            )
            return BehaviorHandlingResult(
                BehaviorHandlingStatus.FAILED,
                error=str(error),
            )
        return BehaviorHandlingResult(
            BehaviorHandlingStatus.HANDLED,
            execution=execution,
        )
