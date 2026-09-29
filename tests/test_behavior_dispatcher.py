"""Partial registration, exact type routing, and async handler contracts."""
import unittest
from dataclasses import dataclass
from robot.navel_client.behavior.commands import COMMAND_TYPES, ApproachCommand, ContinueCommand
from robot.navel_client.behavior.dispatcher import BehaviorDispatcher, BehaviorDispatchError
from robot.navel_client.behavior.registry import build_handler_registry
from robot.navel_client.navel_runtime import Runtime


class RecordingHandler:
    def __init__(self):
        self.calls = []
    async def execute(self, command, **context):
        self.calls.append(command)
        return command


class BehaviorDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_command_routes_to_exactly_one_registered_handler(self):
        registry = {cls: RecordingHandler() for cls in COMMAND_TYPES}
        dispatcher = BehaviorDispatcher(registry)
        for cls in COMMAND_TYPES:
            command = cls.__new__(cls)
            self.assertIs(await dispatcher.dispatch(command), command)
            self.assertEqual(registry[cls].calls, [command])
        self.assertEqual(sum(len(h.calls) for h in registry.values()), len(COMMAND_TYPES))

    async def test_only_approach_registered_and_missing_actions_are_explicit(self):
        registry = build_handler_registry(Runtime(None))
        self.assertEqual(set(registry), {ApproachCommand})
        dispatcher = BehaviorDispatcher(registry)
        self.assertFalse(dispatcher.supports(ContinueCommand()))
        with self.assertRaisesRegex(BehaviorDispatchError, 'UNSUPPORTED_ACTION'):
            await dispatcher.dispatch(ContinueCommand())

    def test_unknown_registration_rejected(self):
        with self.assertRaises(BehaviorDispatchError):
            BehaviorDispatcher({str: RecordingHandler()})
