"""Boundary and orchestration tests for server-response behavior handling."""

from __future__ import annotations

import logging
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app.domain.models import Action
from robot.navel_client.behavior.controller import (
    BehaviorController,
    BehaviorHandlingStatus,
)
from robot.navel_client.behavior.dispatcher import BehaviorDispatcher
from robot.navel_client.behavior.execution_state import (
    BehaviorExecutionState,
    BehaviorExecutionStatus,
)
from robot.navel_client.behavior.registry import build_handler_registry
from robot.navel_client.behavior.results import BehaviorExecutionResult


def server_response(action="APPROACH"):
    return {
        "accepted": True,
        "observation_id": "navel-test:1000000:000001",
        "social_state_id": "state-1",
        "decision_triggered": True,
        "forced_decision": False,
        "triggers": ["HUMAN_DETECTED"],
        "behavior_intent": {
            "schema_version": "1.0",
            "decision_id": "decision-1",
            "observation_id": "navel-test:1000000:000001",
            "social_state_id": "state-1",
            "created_at_us": 1_000_000,
            "action": action,
            "target_human_id": "person-123",
            "preferences": {
                "target_speed_mps": 0.3,
                "preferred_social_distance_m": 1.2,
                "passing_side": None,
                "orientation_target_rad": None,
                "hold_duration_s": None,
            },
            "valid_for_ms": 1_000,
            "reason_codes": ["HUMAN_DETECTED"],
            "decision_confidence": 0.75,
        },
    }


class RecordingHandler:
    def __init__(self):
        self.calls = []

    def execute(self, command):
        self.calls.append(command)
        return BehaviorExecutionResult(Action.APPROACH, type(command).__name__, True, ())


class RaisingMapper:
    def map(self, intent):
        raise RuntimeError("simulated mapping failure")


class RaisingHandler:
    def __init__(self):
        self.calls = 0

    def execute(self, command):
        self.calls += 1
        raise RuntimeError("simulated handler failure")


class RecordingExecutionState(BehaviorExecutionState):
    def __init__(self):
        timestamps = iter(range(1_000, 2_000))
        super().__init__(timestamp_us=lambda: next(timestamps))
        self.statuses = []

    def accept(self, intent):
        super().accept(intent)
        self.statuses.append(self.snapshot.status)

    def mark_dispatched(self):
        super().mark_dispatched()
        self.statuses.append(self.snapshot.status)

    def mark_completed(self):
        super().mark_completed()
        self.statuses.append(self.snapshot.status)

    def mark_failed(self, error):
        super().mark_failed(error)
        self.statuses.append(self.snapshot.status)


class BehaviorControllerTests(unittest.TestCase):
    def controller_with_recorder(self):
        robot = Mock()
        registry = build_handler_registry(robot)
        command_type = next(
            command_type for command_type in registry if command_type.__name__ == "ApproachCommand"
        )
        recorder = RecordingHandler()
        registry[command_type] = recorder
        dispatcher = BehaviorDispatcher(registry)
        return BehaviorController(robot, dispatcher=dispatcher), recorder, dispatcher

    def test_representative_real_server_response_reaches_correct_handler_once(self):
        controller, recorder, _ = self.controller_with_recorder()
        result = controller.handle_response(server_response())
        self.assertEqual(result.status, BehaviorHandlingStatus.HANDLED)
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(recorder.calls[0].target_human_id, "person-123")
        self.assertEqual(recorder.calls[0].preferred_social_distance_m, 1.2)

    def test_missing_or_null_intent_is_safe(self):
        controller, recorder, _ = self.controller_with_recorder()
        with self.assertLogs("robot.navel_client.behavior", logging.WARNING):
            missing = controller.handle_response({"accepted": True})
        no_decision = controller.handle_response(
            {"accepted": True, "behavior_intent": None}
        )
        self.assertEqual(missing.status, BehaviorHandlingStatus.NO_INTENT)
        self.assertIsNotNone(missing.error)
        self.assertEqual(no_decision.status, BehaviorHandlingStatus.NO_INTENT)
        self.assertIsNone(no_decision.error)
        self.assertEqual(recorder.calls, [])

    def test_malformed_and_unknown_intents_never_reach_a_handler(self):
        controller, recorder, _ = self.controller_with_recorder()
        payloads = [server_response("FLY"), {"behavior_intent": "not-an-object"}]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertLogs(
                "robot.navel_client.behavior", logging.WARNING
            ):
                result = controller.handle_response(payload)
                self.assertEqual(result.status, BehaviorHandlingStatus.INVALID_INTENT)
        self.assertEqual(recorder.calls, [])

    def test_mapper_failure_is_logged_and_handler_is_not_called(self):
        _, recorder, dispatcher = self.controller_with_recorder()
        controller = BehaviorController(
            Mock(),
            mapper=RaisingMapper(),  # type: ignore[arg-type]
            dispatcher=dispatcher,
        )
        with self.assertLogs("robot.navel_client.behavior", logging.ERROR) as logs:
            result = controller.handle_response(server_response())
        self.assertEqual(result.status, BehaviorHandlingStatus.FAILED)
        self.assertIn("simulated mapping failure", logs.output[-1])
        self.assertEqual(recorder.calls, [])

    def test_handler_failure_is_logged_and_called_only_once(self):
        robot = Mock()
        registry = build_handler_registry(robot)
        command_type = next(
            command_type
            for command_type in registry
            if command_type.__name__ == "ApproachCommand"
        )
        handler = RaisingHandler()
        registry[command_type] = handler
        controller = BehaviorController(
            robot, dispatcher=BehaviorDispatcher(registry)
        )
        with self.assertLogs("robot.navel_client.behavior", logging.ERROR) as logs:
            result = controller.handle_response(server_response())
        self.assertEqual(result.status, BehaviorHandlingStatus.FAILED)
        self.assertEqual(handler.calls, 1)
        self.assertIn("simulated handler failure", logs.output[-1])

    def test_execution_state_tracks_accept_dispatch_and_completion(self):
        state = RecordingExecutionState()
        controller = BehaviorController(Mock(), execution_state=state)
        result = controller.handle_response(server_response())
        self.assertEqual(result.status, BehaviorHandlingStatus.HANDLED)
        self.assertEqual(
            state.statuses,
            [
                BehaviorExecutionStatus.ACCEPTED,
                BehaviorExecutionStatus.DISPATCHED,
                BehaviorExecutionStatus.DRY_RUN_COMPLETED,
            ],
        )
        snapshot = controller.execution_state
        self.assertEqual(snapshot.action, Action.APPROACH)
        self.assertEqual(snapshot.decision_id, "decision-1")
        self.assertEqual(snapshot.target_human_id, "person-123")
        self.assertEqual(snapshot.updated_at_us, 1_002)
        self.assertIsNone(snapshot.latest_error)
        with self.assertRaises(FrozenInstanceError):
            snapshot.latest_error = "cannot mutate"  # type: ignore[misc]

    def test_execution_state_records_failure(self):
        state = RecordingExecutionState()
        controller = BehaviorController(
            Mock(),
            mapper=RaisingMapper(),  # type: ignore[arg-type]
            execution_state=state,
        )
        with self.assertLogs("robot.navel_client.behavior", logging.ERROR):
            controller.handle_response(server_response())
        snapshot = controller.execution_state
        self.assertEqual(
            state.statuses,
            [BehaviorExecutionStatus.ACCEPTED, BehaviorExecutionStatus.FAILED],
        )
        self.assertEqual(snapshot.status, BehaviorExecutionStatus.FAILED)
        self.assertEqual(snapshot.latest_error, "simulated mapping failure")


if __name__ == "__main__":
    unittest.main()
