"""Server-free action execution with SDK-shaped, stationary robot fakes."""

import asyncio
import contextlib
import io
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from robot.navel_client import main as client
from robot.navel_client.approach import ApproachResult
from robot.navel_client.behaviour_dispatch import BehaviourDispatcher, HANDLERS
from robot.navel_client.single_trial import SingleTrial
from robot.navel_client.trial_head import TrialHeadController
from tests.fixtures import frame, locomotion, perception, person
from tests.test_decision_dispatch import response_for


_sleep = asyncio.sleep


def positioned_person(uid, x=1.8, y=0.0):
    return person(uid, g_nose=[NS(sys=3, x=x, y=y, z=0.1)])


class Robot:
    """Keep local sensors fresh; SDK motion senders never move real hardware."""

    def __init__(self, *frames, fail_motion=None, hold_motion=False):
        self.frames = asyncio.Queue()
        for packet in frames:
            self.frames.put_nowait(packet)
        self.events = []
        self.odom_ready = asyncio.Event()
        self.motion_started = asyncio.Event()
        self.release_motion = asyncio.Event()
        self.fail_motion = fail_motion
        self.hold_motion = hold_motion
        self.motion_count = 0
        self.sender_active = False
        self.readers_stopped = set()

    async def __aenter__(self):
        self.events.append(("connected",))
        return self

    async def __aexit__(self, *exc):
        self.events.append(("disconnected",))

    async def next_locomotion(self, timeout):
        try:
            await _sleep(0.02)
            self.odom_ready.set()
            return locomotion(odometry=NS(
                position=NS(x=0.0, y=0.0),
                orientation=NS(x=0.0, y=0.0, z=0.0, w=1.0),
                velocity=NS(linear_x=0.0, linear_y=0.0, angular_z=0.0),
            ))
        except asyncio.CancelledError:
            self.readers_stopped.add("locomotion")
            raise

    async def next_frame(self, timeout):
        try:
            await self.odom_ready.wait()
            return await self.frames.get()
        except asyncio.CancelledError:
            self.readers_stopped.add("perception")
            raise

    def base_vel(self, linear, angular):
        assert not self.sender_active, "stop command before SDK sender settled"
        self.events.append(("zero", linear, angular))

    def _movement(self, event):
        self.motion_count += 1
        number = self.motion_count
        self.events.append(event)

        async def sender():
            self.sender_active = True
            self.motion_started.set()
            try:
                if number == self.fail_motion:
                    raise OSError("simulated SDK motion failure")
                if self.hold_motion:
                    await self.release_motion.wait()
            finally:
                await _sleep(0)
                self.sender_active = False
                self.events.append(("sender_settled", number))
        return asyncio.create_task(sender())

    def move_and_rotate_base(self, distance, angle, *, speed, acceleration):
        return self._movement(("arc", distance, angle, speed, acceleration))

    def move_base(self, distance, *, speed, acceleration):
        return self._movement(("advance", distance, speed, acceleration))

    def rotate_base(self, *args, **kwargs):
        raise AssertionError("debug tests must not introduce a separate turn")

    def say(self, text):
        self.events.append(("say", text))

        async def speech():
            await _sleep(0)
        return asyncio.create_task(speech())

    def look_at_person(self, uid, magnitude):
        self.events.append(("look", uid, magnitude))

    def look_at_cart(self, target, magnitude):
        self.events.append(("neutral", magnitude))

    def head_overlay_degrees(self, *angles):
        self.events.append(("overlay", *angles))


def sdk_for(robot):
    return NS(Robot=lambda: robot,
              CoordSystem=NS(HEAD_STRAIGHT=3),
              CartSys3d=lambda system, x, y, z: NS(sys=system, x=x, y=y, z=z))


class DebugActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dispatchers = []

    def dispatcher(self, *args, **kwargs):
        dispatcher = BehaviourDispatcher(*args, **kwargs)
        self.dispatchers.append(dispatcher)
        return dispatcher

    @contextlib.contextmanager
    def local_execution(self, robot):
        def head(*args, **kwargs):
            return TrialHeadController(*args, **kwargs, neutral_settle_s=0,
                                       look_settle_s=0, glance_s=0)

        async def sleep(delay):
            if delay == 3.0:
                robot.events.append(("wait", delay))
                await _sleep(0)
            else:
                await _sleep(delay)

        with patch.object(client, "TrialHeadController", side_effect=head), \
                patch.object(client, "BehaviourDispatcher", side_effect=self.dispatcher), \
                patch("robot.navel_client.behaviour_dispatch.asyncio.sleep", new=sleep):
            yield

    def assert_cleaned(self, robot):
        self.assertEqual(robot.readers_stopped, {"locomotion", "perception"})
        self.assertFalse(robot.sender_active)
        self.assertTrue(self.dispatchers[-1]._cleaned)
        self.assertTrue(all(task.done() for task in self.dispatchers[-1].context._tasks))

    async def test_debug_run_never_initialises_server_decision_or_route_components(self):
        robot = Robot()
        forbidden = ("ObservationTransport", "StraightRoute", "DecisionDispatcher",
                     "DryRunHandlers", "FakeCommandExecutor", "PhysicalCommandExecutor",
                     "SdkCapture", "CameraCapture", "collect_and_stream", "collect_sdk_only",
                     "_send_observations", "_poll_model_trial")
        with contextlib.ExitStack() as stack:
            for name in forbidden:
                stack.enter_context(patch.object(client, name, side_effect=AssertionError(name)))
            args = client.parse_args(["--debug-action", "ENGAGE", "--server", "not-a-url"])
            stack.enter_context(patch.dict(sys.modules, {"navel": sdk_for(robot)}))
            stack.enter_context(self.local_execution(robot))
            result = await asyncio.wait_for(client.run(args), 2)
        self.assertEqual(result.phase, "COMPLETED")
        self.assertEqual(robot.events[0], ("connected",))
        self.assertEqual(robot.events[-1], ("disconnected",))
        self.assert_cleaned(robot)

    async def test_approach_selects_first_valid_person_with_others_preserved(self):
        # The first valid person is farther away than the second: no ranking.
        robot = Robot(
            perception(positioned_person(None), positioned_person(41, 2.8),
                       positioned_person(42, 1.0)),
            # Replacing the latest frame must not replace the first selection.
            perception(positioned_person(99), positioned_person(42, 1.0),
                       positioned_person(41, 2.8)),
        )
        seen = []

        async def approach(runtime, uid):
            seen.append((uid, [candidate["uid"] for candidate in runtime.people]))
            runtime.target = {"uid": uid}
            return ApproachResult("APPROACHED_VERIFIED", 0.7, 0.0, True)

        args = client.parse_args(["--debug-action", "APPROACH"])
        with self.local_execution(robot), \
                patch("robot.navel_client.behaviour_dispatch.approach_human",
                      new=AsyncMock(side_effect=approach)) as execute:
            result = await asyncio.wait_for(client.execute_debug_action(robot, args, sdk_for(robot)), 2)
        self.assertEqual(result.phase, "COMPLETED")
        execute.assert_awaited_once()
        self.assertEqual(seen, [(41, [99, 42, 41])])
        self.assertIs(self.dispatchers[-1].handlers["APPROACH"], HANDLERS["APPROACH"])
        self.assertEqual(robot.events.count(("say", "Hi! Do you need any help?")), 1)
        look_index = robot.events.index(("look", 41, 1.0))
        speech_index = robot.events.index(("say", "Hi! Do you need any help?"))
        self.assertLess(look_index, speech_index)
        self.assertFalse(any(event[0] == "neutral" for event in robot.events[look_index + 1:speech_index]))
        self.assert_cleaned(robot)

    async def test_approach_target_acquisition_timeout_never_moves_or_greets(self):
        robot = Robot(perception(person(7)))  # No usable approach position.
        args = client.parse_args(["--debug-action", "APPROACH", "--decision-wait-timeout", "0.08"])
        with self.local_execution(robot), \
                patch("robot.navel_client.behaviour_dispatch.approach_human", new=AsyncMock()) as execute:
            result = await asyncio.wait_for(client.execute_debug_action(robot, args, sdk_for(robot)), 2)
        self.assertEqual((result.phase, result.failure_reason), ("FAILED", "TARGET_ACQUISITION_TIMEOUT"))
        execute.assert_not_awaited()
        self.assertFalse(any(event[0] in {"arc", "advance", "say"} for event in robot.events))
        self.assert_cleaned(robot)

    async def test_engage_without_person_measures_stop_and_uses_existing_speech_once(self):
        robot = Robot()
        args = client.parse_args(["--debug-action", "ENGAGE"])
        with self.local_execution(robot):
            result = await asyncio.wait_for(client.execute_debug_action(robot, args, sdk_for(robot)), 2)
        self.assertEqual(result.phase, "COMPLETED")
        self.assertIs(self.dispatchers[-1].handlers["ENGAGE"], HANDLERS["ENGAGE"])
        self.assertEqual(robot.events.count(("say", "Hello! Do you need any guidance in the lab?")), 1)
        speech = next(index for index, event in enumerate(robot.events) if event[0] == "say")
        self.assertGreater(sum(event[0] == "zero" for event in robot.events[:speech]), 1)
        self.assertFalse(any(event[0] in {"arc", "advance", "look"} for event in robot.events))
        self.assert_cleaned(robot)

    async def test_yield_uses_existing_sequence_once_without_starting_route(self):
        robot = Robot()
        args = client.parse_args(["--debug-action", "YIELD"])
        with self.local_execution(robot), patch.object(client, "StraightRoute", side_effect=AssertionError("route")):
            result = await asyncio.wait_for(client.execute_debug_action(robot, args, sdk_for(robot)), 3)
        self.assertEqual(result.phase, "COMPLETED")
        dispatcher = self.dispatchers[-1]
        self.assertIsNone(dispatcher.context.route)
        self.assertIs(dispatcher.handlers["YIELD"], HANDLERS["YIELD"])
        stages = [event for event in robot.events if event[0] in {"arc", "advance", "say", "wait"}]
        self.assertEqual(stages, [
            ("arc", -0.60, 100.0, 0.25, 0.35),
            ("say", "Please go ahead."), ("wait", 3.0),
            ("arc", 0.60, -100.0, 0.12, 0.15),
            ("advance", 0.15, 0.25, 0.35), ("say", "Yield complete."),
        ])
        # The real runtime confirms stopping between every sender and next stage.
        for number, next_event in (
                (1, ("say", "Please go ahead.")),
                (2, ("advance", 0.15, 0.25, 0.35)),
                (3, ("say", "Yield complete."))):
            start = robot.events.index(("sender_settled", number))
            stop = robot.events.index(next_event)
            self.assertGreater(sum(event[0] == "zero" for event in robot.events[start:stop]), 1)
        self.assert_cleaned(robot)

    async def test_yield_failure_or_timeout_stops_sender_and_prevents_later_stages(self):
        for failure in ("sdk", "timeout"):
            with self.subTest(failure=failure):
                robot = Robot(fail_motion=1 if failure == "sdk" else None,
                              hold_motion=failure == "timeout")
                args = client.parse_args(["--debug-action", "YIELD", "--behaviour-timeout",
                                          "2" if failure == "sdk" else "0.4"])
                with self.local_execution(robot), self.assertLogs("robot.navel_client", level="WARNING"):
                    result = await asyncio.wait_for(client.execute_debug_action(robot, args, sdk_for(robot)), 3)
                self.assertEqual(result.phase, "FAILED")
                self.assertEqual(result.failure_reason,
                                 "BEHAVIOUR_FAILED" if failure == "sdk" else "BEHAVIOUR_TIMEOUT")
                self.assertEqual(robot.motion_count, 1)
                self.assertFalse(any(event[0] in {"advance", "say"} for event in robot.events))
                settled = robot.events.index(("sender_settled", 1))
                self.assertTrue(any(event[0] == "zero" for event in robot.events[settled + 1:]))
                self.assert_cleaned(robot)

    async def test_cancellation_retains_sensing_until_sender_stops_and_cleanup_finishes(self):
        robot = Robot(hold_motion=True)
        args = client.parse_args(["--debug-action", "YIELD"])
        with self.local_execution(robot), self.assertLogs("robot.navel_client", level="WARNING"):
            task = asyncio.create_task(client.execute_debug_action(robot, args, sdk_for(robot)))
            await asyncio.wait_for(robot.motion_started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        self.assertEqual(robot.motion_count, 1)
        self.assertFalse(any(event[0] in {"advance", "say"} for event in robot.events))
        self.assertEqual(self.dispatchers[-1].trial.phase, "FAILED")
        self.assertIn(self.dispatchers[-1].trial.failure_reason, {"INTERRUPTED", "BEHAVIOUR_CANCELLED"})
        self.assert_cleaned(robot)

    async def test_sensor_failure_stops_active_action_before_any_later_stage(self):
        class DisconnectedRobot(Robot):
            async def next_frame(self, timeout):
                try:
                    await self.motion_started.wait()
                    raise ConnectionAbortedError("simulated perception disconnect")
                finally:
                    self.readers_stopped.add("perception")

        robot = DisconnectedRobot(hold_motion=True)
        args = client.parse_args(["--debug-action", "YIELD"])
        with self.local_execution(robot), self.assertLogs("robot.navel_client", level="WARNING"):
            result = await asyncio.wait_for(client.execute_debug_action(robot, args, sdk_for(robot)), 3)
        self.assertEqual((result.phase, result.failure_reason), ("FAILED", "LOCAL_SENSOR_FAILED"))
        self.assertEqual(robot.motion_count, 1)
        self.assertFalse(any(event[0] in {"advance", "say"} for event in robot.events))
        self.assert_cleaned(robot)

    async def test_local_dispatch_claims_action_once_before_awaiting_stop(self):
        robot = Robot()
        trial = SingleTrial()
        dispatcher = BehaviourDispatcher(trial, robot)
        dispatcher.context.approach.ingest_locomotion(await robot.next_locomotion(timeout=1))
        self.assertEqual(await asyncio.gather(dispatcher.dispatch_local("ENGAGE"),
                                            dispatcher.dispatch_local("ENGAGE")), [True, False])
        self.assertEqual(robot.events.count(("say", "Hello! Do you need any guidance in the lab?")), 1)
        self.assertTrue(dispatcher._cleaned)
        self.assertEqual(trial.phase, "COMPLETED")

    async def test_normal_dispatch_still_requires_a_decision_and_one_current_person(self):
        trial = SingleTrial(monotonic_us=lambda: frame()["timestamp"])
        robot = Robot()
        dispatcher = BehaviourDispatcher(trial, robot)
        self.assertFalse(await dispatcher.dispatch())
        observation = frame()
        payload = response_for(observation, 1, "ENGAGE", True)
        payload["final_decision"] = {"action": "ENGAGE", "reason": "Test."}
        self.assertTrue(trial.accept_rule_response(payload, observation))
        trial.note_observation({**observation, "people": observation["people"] * 2})
        self.assertFalse(await dispatcher.dispatch())
        self.assertEqual(trial.failure_reason, "CURRENT_PERSON_UNAVAILABLE")
        self.assertFalse(any(event[0] == "say" for event in robot.events))


class DebugActionArgumentTests(unittest.TestCase):
    def test_debug_rejects_unsupported_actions_and_conflicting_trial_options(self):
        conflicts = (["--debug-action", "CONTINUE"],
                     ["--debug-action", "YIELD", "--route-trial"],
                     ["--debug-action", "YIELD", "--route-tria"],
                     ["--debug-action", "YIELD", "--route-distance", "10"],
                     ["--debug-action", "YIELD", "--single-trial"],
                     ["--debug-action", "YIELD", "--single-trial-execute"],
                     ["--debug-action", "YIELD", "--single-trial-policy", "rules"],
                     ["--debug-action", "YIELD", "--decision-dry-run"],
                     ["--debug-action", "YIELD", "--physical-executor"])
        for argv in conflicts:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    client.parse_args(argv)
                self.assertEqual(error.exception.code, 2)

    def test_debug_validates_target_acquisition_and_action_timeouts(self):
        for option, value in (("--decision-wait-timeout", "0"),
                              ("--decision-wait-timeout", "nan"),
                              ("--behaviour-timeout", "0"),
                              ("--behaviour-timeout", "3601")):
            with self.subTest(option=option, value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    client.parse_args(["--debug-action", "APPROACH", option, value])
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
