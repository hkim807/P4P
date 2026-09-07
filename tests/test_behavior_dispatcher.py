"""Unit tests for type-based behavior dispatch."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest.mock import Mock

from robot.navel_client.behavior.commands import COMMAND_TYPES, ContinueCommand
from robot.navel_client.behavior.dispatcher import (
    BehaviorDispatchError,
    BehaviorDispatcher,
)
from robot.navel_client.behavior.intent import NavelAction as Action
from robot.navel_client.behavior.registry import build_handler_registry
from robot.navel_client.behavior.results import BehaviorExecutionResult


class RecordingHandler:
    def __init__(self, action=Action.CONTINUE):
        self.action = action
        self.calls = []

    def execute(self, command):
        self.calls.append(command)
        return BehaviorExecutionResult(self.action, type(command).__name__, True, ())


@dataclass(frozen=True)
class UnknownCommand:
    value: str = "unknown"


class BehaviorDispatcherTests(unittest.TestCase):
    def test_every_command_routes_to_exactly_one_registered_handler(self):
        registry = {command_type: RecordingHandler() for command_type in COMMAND_TYPES}
        dispatcher = BehaviorDispatcher(registry)
        for command_type in COMMAND_TYPES:
            for handler in registry.values():
                handler.calls.clear()
            command = command_type.__new__(command_type)
            dispatcher.dispatch(command)
            self.assertEqual(len(registry[command_type].calls), 1)
            self.assertEqual(
                sum(len(handler.calls) for handler in registry.values()),
                1,
            )

    def test_registry_covers_every_concrete_command(self):
        self.assertEqual(set(build_handler_registry(Mock())), set(COMMAND_TYPES))

    def test_incomplete_registry_is_rejected_at_construction(self):
        registry = build_handler_registry(Mock())
        del registry[ContinueCommand]
        with self.assertRaisesRegex(BehaviorDispatchError, "ContinueCommand"):
            BehaviorDispatcher(registry)

    def test_unregistered_command_fails_clearly(self):
        dispatcher = BehaviorDispatcher(build_handler_registry(Mock()))
        with self.assertRaisesRegex(BehaviorDispatchError, "UnknownCommand"):
            dispatcher.dispatch(UnknownCommand())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
