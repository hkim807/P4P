"""Tests proving all handlers are structured, non-blocking dry runs."""

from __future__ import annotations

import logging
import unittest
from unittest.mock import Mock, patch

from app.domain.models import Action, PassingSide
from robot.navel_client.behavior.commands import (
    ApproachCommand,
    AvoidCommand,
    ContinueCommand,
    DisengageCommand,
    GreetCommand,
    GuideCommand,
    MonitorCommand,
    OrientCommand,
    ResumeCommand,
    SlowCommand,
    WaitCommand,
    YieldCommand,
)
from robot.navel_client.behavior.dispatcher import BehaviorDispatcher
from robot.navel_client.behavior.registry import build_handler_registry


class BehaviorHandlerTests(unittest.TestCase):
    def test_all_actions_log_meaningful_parameters_and_return_typed_dry_run_result(self):
        commands = {
            Action.CONTINUE: ContinueCommand(),
            Action.MONITOR: MonitorCommand(),
            Action.ORIENT: OrientCommand("person-1", 0.5),
            Action.SLOW: SlowCommand(0.3, "person-1"),
            Action.YIELD: YieldCommand("person-1", 0.2, 1.2, PassingSide.LEFT, 2.0),
            Action.AVOID: AvoidCommand("person-1", 0.25, 1.3, PassingSide.RIGHT),
            Action.APPROACH: ApproachCommand("person-1", 1.2, 0.3),
            Action.GREET: GreetCommand("person-1"),
            Action.GUIDE: GuideCommand("person-1", 0.4, 1.1, PassingSide.EITHER),
            Action.WAIT: WaitCommand(3.0),
            Action.RESUME: ResumeCommand(),
            Action.DISENGAGE: DisengageCommand("person-1"),
        }
        robot = Mock()
        registry = build_handler_registry(robot)
        dispatcher = BehaviorDispatcher(registry)
        with patch("time.sleep") as sleep, patch("asyncio.sleep") as async_sleep:
            for action, command in commands.items():
                with self.subTest(action=action.value), self.assertLogs(
                    "robot.navel_client.behavior", logging.INFO
                ) as logs:
                    result = dispatcher.dispatch(command)
                    message = logs.output[-1]
                    self.assertIn(f"action={action.value}", message)
                    self.assertIn("dry_run=true", message)
                    self.assertTrue(result.dry_run)
                    self.assertEqual(result.action, action)
                    self.assertEqual(result.command_type, type(command).__name__)
                    for name, value in result.parameters:
                        self.assertIn(f"{name}=", message)
                        self.assertIsNotNone(value)
        sleep.assert_not_called()
        async_sleep.assert_not_called()
        self.assertEqual(robot.mock_calls, [])
        self.assertTrue(all(handler._robot is robot for handler in registry.values()))

    def test_null_optional_parameters_are_not_logged_or_returned(self):
        with self.assertLogs("robot.navel_client.behavior", logging.INFO) as logs:
            result = BehaviorDispatcher(build_handler_registry(Mock())).dispatch(
                YieldCommand()
            )
        self.assertEqual(result.parameters, ())
        self.assertNotIn("target_human_id", logs.output[-1])
        self.assertNotIn("None", logs.output[-1])

    def test_each_handler_is_owned_by_its_dedicated_action_module(self):
        expected_modules = {
            "ContinueHandler": "continue_route",
            "MonitorHandler": "monitor",
            "OrientHandler": "orient",
            "SlowHandler": "slow",
            "YieldHandler": "yield_behavior",
            "AvoidHandler": "avoid",
            "ApproachHandler": "approach",
            "GreetHandler": "greet",
            "GuideHandler": "guide",
            "WaitHandler": "wait",
            "ResumeHandler": "resume",
            "DisengageHandler": "disengage",
        }
        handlers = tuple(build_handler_registry(Mock()).values())
        self.assertEqual(len(expected_modules), len(handlers))
        for handler in handlers:
            self.assertEqual(
                type(handler).__module__.rsplit(".", 1)[-1],
                expected_modules[type(handler).__name__],
            )


if __name__ == "__main__":
    unittest.main()
