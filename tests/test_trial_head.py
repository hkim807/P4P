"""Single-trial neutral and action-specific head choreography."""

import unittest
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import patch

from robot.navel_client.behaviour_dispatch import BehaviourDispatcher, HANDLERS
from robot.navel_client.main import collect_and_stream, parse_args
from robot.navel_client.single_trial import SingleTrial
from robot.navel_client.trial_head import TrialHeadController
from tests.fixtures import frame
from tests.test_decision_dispatch import response_for


class TrialHeadControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_neutral_replaces_focus_and_glance_returns_to_neutral(self):
        calls = []

        class CoordSystem:
            HEAD_STRAIGHT = "HEAD_STRAIGHT"

        class Robot:
            def head_overlay(self, value):
                calls.append(("overlay", value.x, value.y, value.z))

            def look_at_cart(self, target, head):
                calls.append(("cart", target.sys, target.x, target.y, target.z, head))

            async def look_at_person(self, uid, head):
                calls.append(("person", uid, head))

        sdk = NS(
            CoordSystem=CoordSystem,
            Bryan=lambda x, y, z: NS(x=x, y=y, z=z),
            CartSys3d=lambda sys, x, y, z: NS(sys=sys, x=x, y=y, z=z),
        )
        head = TrialHeadController(Robot(), sdk, neutral_settle_s=0, look_settle_s=0, glance_s=0)
        head.preflight()
        await head.neutral()
        await head.glance(17)
        self.assertEqual(calls, [
            ("overlay", 0.0, 0.0, 0.0),
            ("cart", "HEAD_STRAIGHT", 2.0, 0.0, 0.0, 1.0),
            ("person", 17, 1.0),
            ("overlay", 0.0, 0.0, 0.0),
            ("cart", "HEAD_STRAIGHT", 2.0, 0.0, 0.0, 1.0),
        ])

    async def test_dispatcher_sequences_each_action_around_normal_handler(self):
        expected = {
            "CONTINUE": [("glance", 17), ("handler", "CONTINUE")],
            "YIELD": [("look", 17), ("handler", "YIELD"), ("neutral",)],
            "APPROACH": [("neutral",), ("handler", "APPROACH"), ("look", 17)],
            "ENGAGE": [("look", 17), ("handler", "ENGAGE")],
        }
        for action, sequence in expected.items():
            with self.subTest(action=action):
                observation = frame()
                trial = SingleTrial(monotonic_us=lambda: observation["timestamp"])
                payload = response_for(observation, 1, action, action in ("APPROACH", "ENGAGE"))
                payload["final_decision"] = {"action": action, "reason": "Test."}
                self.assertTrue(trial.accept_rule_response(payload, observation))
                self.assertEqual(trial.decision_person_uid, 17)
                events = []

                class Head:
                    def preflight(self):
                        events.append(("preflight",))

                    async def neutral(self):
                        events.append(("neutral",))

                    async def look_at_person(self, uid):
                        events.append(("look", uid))

                    async def glance(self, uid):
                        events.append(("glance", uid))

                async def handler(context):
                    events.append(("handler", context.decision["action"]))

                handlers = {name: handler for name in HANDLERS}
                dispatcher = BehaviourDispatcher(
                    trial, NS(base_vel=lambda x, r: events.append(("zero",))),
                    trial_head=Head(), handlers=handlers)
                dispatcher.preflight()
                self.assertTrue(await dispatcher.dispatch())
                self.assertEqual(events[1:-1], sequence)
                self.assertEqual(events[-1], ("zero",))

    async def test_failed_approach_does_not_look_and_failed_yield_returns_neutral(self):
        for action, expected in (
                ("APPROACH", [("neutral",), ("handler", "APPROACH")]),
                ("YIELD", [("look", 17), ("handler", "YIELD"), ("neutral",)])):
            with self.subTest(action=action):
                observation = frame()
                trial = SingleTrial(monotonic_us=lambda: observation["timestamp"])
                payload = response_for(observation, 1, action, action == "APPROACH")
                payload["final_decision"] = {"action": action, "reason": "Test."}
                self.assertTrue(trial.accept_rule_response(payload, observation))
                events = []

                class Head:
                    neutral_settle_s = 0.0

                    def preflight(self):
                        pass

                    async def neutral(self):
                        events.append(("neutral",))

                    async def look_at_person(self, uid):
                        events.append(("look", uid))

                    async def glance(self, uid):
                        events.append(("glance", uid))

                async def handler(context):
                    events.append(("handler", context.decision["action"]))
                    raise RuntimeError("test failure")

                dispatcher = BehaviourDispatcher(
                    trial, NS(base_vel=lambda x, r: events.append(("zero",))),
                    trial_head=Head(), handlers={name: handler for name in HANDLERS})
                with self.assertLogs("robot.navel_client.behaviour_dispatch", level="ERROR"):
                    self.assertFalse(await dispatcher.dispatch())
                self.assertEqual(events[:-1], expected)
                self.assertEqual(events[-1], ("zero",))
                self.assertEqual(trial.failure_reason, "BEHAVIOUR_FAILED")

    async def test_executable_route_centres_head_before_starting_base(self):
        events = []
        route_started = asyncio.Event()

        class Head:
            neutral_settle_s = 0.0

            def preflight(self):
                events.append(("head_preflight",))

            async def neutral(self):
                events.append(("neutral",))

        class Robot:
            async def next_frame(self, timeout):
                await asyncio.Event().wait()

            async def next_locomotion(self, timeout):
                await asyncio.Event().wait()

            def move_base(self, distance, *, speed, acceleration):
                events.append(("route", distance))
                route_started.set()

                async def move():
                    await asyncio.Event().wait()
                return asyncio.create_task(move())

            def base_vel(self, x, r):
                events.append(("zero",))

            def move_and_rotate_base(self, *args, **kwargs):
                raise AssertionError

            def rotate_base(self, *args, **kwargs):
                raise AssertionError

            def say(self, text):
                raise AssertionError

        trial = SingleTrial(wait_timeout_s=0.01)
        args = parse_args(["--single-trial", "--single-trial-execute", "--route-trial"])
        head = Head()
        with patch("robot.navel_client.main.SingleTrial", return_value=trial), \
                patch("robot.navel_client.main.TrialHeadController", return_value=head):
            result = await asyncio.wait_for(
                collect_and_stream(Robot(), args, navel_module=object()), 1)
        self.assertIs(result, trial)
        self.assertEqual(events[:3], [("head_preflight",), ("neutral",), ("route", 10.0)])


if __name__ == "__main__":
    unittest.main()
