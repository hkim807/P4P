"""Robot-local head selection and collector ordering, without Navel hardware."""

import asyncio
import contextlib
import io
import unittest

from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.head_focus import HeadFocusController
from robot.navel_client.main import LatestLocomotion, _collect_perception, parse_args
from tests.fixtures import perception, person


class FakeRobot:
    def __init__(self):
        self.commands = []

    def look_at_person(self, uid, head):
        self.commands.append((uid, head))


class HeadFocusTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.robot = FakeRobot()
        self.focus = HeadFocusController(self.robot, clock=lambda: self.now)

    def test_single_person_is_acquired_once_and_kept_in_crowd(self):
        self.focus.observe(perception(person(17)))
        self.focus.observe(perception(person(17)))
        self.focus.observe(perception(person(17), person(18)))
        self.assertEqual(self.robot.commands, [(17, 0.5)])
        self.assertEqual(self.focus.uid, 17)

    def test_route_refresh_is_rate_limited_and_can_be_suspended(self):
        focus = HeadFocusController(self.robot, magnitude=1.0,
                                    command_interval_s=0.6, select_first_visible=True,
                                    clock=lambda: self.now)
        focus.observe(perception(person(None), person(17), person(18)))
        self.now += 0.3
        focus.observe(perception(person(17), person(18)))
        self.now += 0.4
        focus.observe(perception(person(17), person(18)))
        self.now += 0.7
        focus.observe(perception())
        self.assertEqual(self.robot.commands, [(17, 1.0), (17, 1.0)])
        focus.suspend()
        self.now += 1
        focus.observe(perception(person(18)))
        self.assertEqual(self.robot.commands, [(17, 1.0), (17, 1.0)])

    def test_no_arbitrary_acquisition_in_ambiguous_frame(self):
        self.focus.observe(perception(person(17), person(18)))
        self.focus.observe(perception(person(None)))
        self.focus.observe(perception(person(17), person(None)))
        self.assertEqual(self.robot.commands, [])

    def test_dropout_holds_uid_then_allows_new_single_candidate(self):
        self.focus.observe(perception(person(17)))
        self.now += 0.3
        self.focus.observe(perception())
        self.now += 0.3
        self.focus.observe(perception(person(18)))
        self.assertEqual(self.robot.commands, [(17, 0.5)])
        self.now += 0.2
        self.focus.observe(perception(person(18)))
        self.assertEqual(self.robot.commands, [(17, 0.5), (18, 0.5)])

    def test_expired_uid_does_not_switch_to_crowd(self):
        self.focus.observe(perception(person(17)))
        self.now += 1
        self.focus.observe(perception(person(18), person(19)))
        self.assertIsNone(self.focus.uid)
        self.assertEqual(self.robot.commands, [(17, 0.5)])

    def test_timeout_expires_local_focus_and_stop_clears_it(self):
        self.focus.observe(perception(person(17)))
        self.now += 1
        self.focus.tick()
        self.assertIsNone(self.focus.uid)
        self.focus.observe(perception(person(17)))
        self.focus.stop()
        self.assertIsNone(self.focus.uid)
        self.assertEqual(self.robot.commands, [(17, 0.5), (17, 0.5)])

    def test_server_lock_blocks_uid_switch_and_reacquires_known_target_in_crowd(self):
        self.focus.observe(perception(person(17)))
        self.focus.apply_server_lock({"status": "LOCKED", "target_uid": 17, "lock_id": "lock-1"})
        self.now += 1
        self.focus.observe(perception(person(18)))
        self.assertEqual(self.robot.commands, [(17, 0.5)])
        self.focus.observe(perception(person(17), person(18)))
        self.assertEqual(self.robot.commands, [(17, 0.5), (17, 0.5)])
        self.focus.apply_server_lock({"status": "COOLDOWN", "target_uid": None, "lock_id": "lock-1"})
        self.now += 1
        self.focus.observe(perception(person(18)))
        self.assertEqual(self.robot.commands[-1], (18, 0.5))

    def test_server_pin_expires_if_responses_stop(self):
        self.focus.apply_server_lock({"status": "MISSING", "target_uid": 17, "lock_id": "lock-1"})
        self.now += 3.1
        self.focus.observe(perception(person(18)))
        self.assertEqual(self.robot.commands, [(18, 0.5)])

    def test_server_rebind_redirects_head_to_new_uid(self):
        self.focus.observe(perception(person(17)))
        self.focus.apply_server_lock({"status": "LOCKED", "target_uid": 17, "lock_id": "lock-1"})
        self.focus.apply_server_lock({"status": "LOCKED", "target_uid": 18, "lock_id": "lock-1"})
        self.focus.observe(perception(person(18)))
        self.assertEqual(self.robot.commands, [(17, 0.5), (18, 0.5)])

    def test_failed_command_is_retried_after_cooldown(self):
        attempts = []

        def failing_command(uid, head):
            attempts.append((uid, head))
            if len(attempts) == 1:
                raise OSError("socket unavailable")

        self.robot.look_at_person = failing_command
        with self.assertLogs("robot.navel_client.head_focus", level="WARNING"):
            self.focus.observe(perception(person(17)))
        self.assertIsNone(self.focus.uid)
        self.now += 0.5
        self.focus.observe(perception(person(17)))
        self.assertEqual(len(attempts), 1)
        self.now += 0.5
        self.focus.observe(perception(person(17)))
        self.assertEqual(attempts, [(17, 0.5), (17, 0.5)])
        self.assertEqual(self.focus.uid, 17)

    def test_cli_rejects_invalid_focus_settings(self):
        for option, value in [
            ("--head-focus-magnitude", "nan"),
            ("--head-focus-magnitude", "1.1"),
            ("--head-focus-magnitude", "-0.1"),
            ("--head-focus-grace", "0"),
            ("--head-focus-grace", "inf"),
        ]:
            with self.subTest(option=option, value=value):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    parse_args([option, value])


class CollectorFocusTests(unittest.IsolatedAsyncioTestCase):
    async def test_head_handoff_settles_command_without_stopping_shared_reader(self):
        started, second_frame = asyncio.Event(), asyncio.Event()
        commands, settled = [], []
        reads = 0

        class Robot:
            async def look_at_person(self, uid, head):
                commands.append(uid)
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.sleep(0)
                    settled.append(uid)

            async def next_frame(self, timeout):
                nonlocal reads
                reads += 1
                if reads == 1:
                    return perception(person(17))
                if reads == 2:
                    second_frame.set()
                    return perception(person(18))
                await asyncio.Event().wait()

        robot = Robot()
        focus = HeadFocusController(robot)
        queue = asyncio.Queue(maxsize=1)
        collector = asyncio.create_task(_collect_perception(robot, NavelObservationAdapter(),
            LatestLocomotion(), queue, max_locomotion_age_s=1, head_focus=focus))
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(focus.suspend_and_settle(), 1)
            await asyncio.wait_for(second_frame.wait(), 1)
            self.assertEqual((commands, settled), ([17], [17]))
            self.assertFalse(collector.done())
            self.assertEqual(queue.get_nowait()["people"][0]["uid"], 18)
            queue.task_done()
        finally:
            collector.cancel()
            await asyncio.gather(collector, return_exceptions=True)

    async def test_focus_command_precedes_conversion_and_http_queue(self):
        robot = FakeRobot()

        async def next_frame(timeout):
            if not robot.commands:
                return perception(person(17))
            await asyncio.Event().wait()

        robot.next_frame = next_frame

        class Adapter(NavelObservationAdapter):
            def convert(self, perception_packet, locomotion=None):
                self.assert_command_sent()
                return super().convert(perception_packet, locomotion)

            def assert_command_sent(self):
                assert robot.commands == [(17, 0.5)]

        queue = asyncio.Queue(maxsize=1)
        focus = HeadFocusController(robot)
        task = asyncio.create_task(_collect_perception(
            robot, Adapter(), LatestLocomotion(), queue,
            max_locomotion_age_s=1, head_focus=focus,
        ))
        try:
            frame = await asyncio.wait_for(queue.get(), 1)
            self.assertEqual(frame["people"][0]["uid"], 17)
            self.assertEqual(robot.commands, [(17, 0.5)])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
