"""Robot-local head selection and collector ordering, without Navel hardware."""

import asyncio
import contextlib
import io
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from app.social_pipeline import SocialPipeline
from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.head_focus import HeadFocusController
from robot.navel_client.main import LatestLocomotion, _collect_perception, collect_and_stream, parse_args
from robot.navel_client.sdk_capture import SdkCapture
from robot.navel_client.single_trial import SingleTrial
from robot.navel_client.transport import ObservationResponse, ObservationTransport
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
        for argv in (["--head-focus", "--no-head-focus"], ["--no-head-focus", "--head-focus"]):
            with self.subTest(argv=argv):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                    parse_args(argv)
                self.assertIn("not allowed with argument", stderr.getvalue())
                self.assertIn("--head-focus", stderr.getvalue())
                self.assertIn("--no-head-focus", stderr.getvalue())
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

    async def test_observation_is_published_before_head_submission(self):
        robot = FakeRobot()
        queue = asyncio.Queue(maxsize=1)
        trial = SingleTrial()

        async def next_frame(timeout):
            if not robot.commands:
                return perception(person(17))
            await asyncio.Event().wait()

        robot.next_frame = next_frame

        class Adapter(NavelObservationAdapter):
            def convert(self, perception_packet, locomotion=None):
                assert not robot.commands
                return super().convert(perception_packet, locomotion)

        def look_at_person(uid, head):
            assert queue.qsize() == 1
            assert trial.current_observation['people'][0]['uid'] == uid
            robot.commands.append((uid, head))

        robot.look_at_person = look_at_person
        focus = HeadFocusController(robot)
        task = asyncio.create_task(_collect_perception(
            robot, Adapter(), LatestLocomotion(), queue,
            max_locomotion_age_s=1, head_focus=focus, trial=trial,
        ))
        try:
            frame = await asyncio.wait_for(queue.get(), 1)
            self.assertEqual(frame["people"][0]["uid"], 17)
            self.assertEqual(robot.commands, [(17, 0.5)])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_slow_sdk_head_task_keeps_regular_perception_and_gaze_coverage(self):
        started = asyncio.Event()
        tasks, settled, ingested, observations = [], [], [], []

        class Robot:
            def look_at_person(self, uid, head):
                async def delayed():
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        await asyncio.sleep(.02)
                        settled.append(uid)
                task = asyncio.create_task(delayed())
                tasks.append(task)
                return task

            async def next_frame(self, timeout):
                await asyncio.sleep(.05)
                return perception(person(17, gaze_overlap=.98))

        robot = Robot()
        focus = HeadFocusController(robot, magnitude=1., command_interval_s=.6,
                                    select_first_visible=True, raise_on_error=True)
        trial = SingleTrial()
        queue = asyncio.Queue(maxsize=1)
        capture = SdkCapture(NS())
        collector = asyncio.create_task(_collect_perception(robot, NavelObservationAdapter(),
            LatestLocomotion(), queue, max_locomotion_age_s=1, head_focus=focus,
            sdk_capture=capture, model_provenance=True, trial=trial,
            approach=NS(ingest_perception=ingested.append)))
        pipeline = SocialPipeline('regular-head-test')
        try:
            await asyncio.wait_for(started.wait(), 1)
            for _ in range(41):
                queued = await asyncio.wait_for(queue.get(), 1)
                queue.task_done()
                observations.append(queued.observation)
                self.assertEqual(queued.capture['packet']['persons'][0]['uid'], 17)
                self.assertLessEqual(queued.capture['received_monotonic_us'], queued.observation['timestamp'])
                state = pipeline.process(queued.observation)['social_state']['people'][0]
            self.assertEqual(len(tasks), 1)
            self.assertIs(focus._command_task, tasks[0])  # SDK Task retained without resubmission.
            self.assertFalse(tasks[0].done())
            gaps = [(b['timestamp']-a['timestamp'])/1e6 for a, b in zip(observations, observations[1:])]
            self.assertLess(max(gaps), pipeline.estimator.config.max_gap_s)
            self.assertTrue(state['evidence']['gaze_valid'])
            self.assertEqual(state['gaze_state'], 'SUSTAINED')
            self.assertGreaterEqual(state['evidence']['gaze_valid_coverage_s'], .8)
            self.assertIs(trial.current_observation, observations[-1])
            self.assertEqual(len(ingested), len(observations))
            self.assertEqual(capture.sequences['perception'], len(observations))
            await focus.suspend_and_settle()
            self.assertEqual(settled, [17])
            self.assertFalse(collector.done())
        finally:
            collector.cancel()
            await asyncio.gather(collector, return_exceptions=True)
            await focus.suspend_and_settle()

    async def test_async_head_failure_is_supervised_and_nonroute_retries(self):
        for route in (False, True):
            with self.subTest(route=route):
                now = [10.]
                calls = []

                class Robot:
                    def look_at_person(self, uid, head):
                        async def command():
                            if len(calls) == 1:
                                raise OSError('async head failure')
                        task = asyncio.create_task(command())
                        calls.append(task)
                        return task

                focus = HeadFocusController(Robot(), clock=lambda: now[0], raise_on_error=route)
                watcher = asyncio.create_task(focus.watch_failures()) if route else None
                try:
                    with self.assertLogs('robot.navel_client.head_focus', level='WARNING') if not route else contextlib.nullcontext():
                        task = focus.observe(perception(person(17)))
                        await asyncio.wait([task])
                        await asyncio.sleep(0)
                    if route:
                        with self.assertRaisesRegex(OSError, 'async head failure'):
                            await asyncio.wait_for(watcher, 1)
                        with self.assertRaisesRegex(OSError, 'async head failure'):
                            await focus.suspend_and_settle()
                    else:
                        self.assertIsNone(focus.uid)
                        now[0] += .5
                        focus.observe(perception(person(17)))
                        self.assertEqual(len(calls), 1)
                        now[0] += .5
                        task = focus.observe(perception(person(17)))
                        await asyncio.wait([task])
                        self.assertEqual(len(calls), 2)
                        await focus.suspend_and_settle()
                finally:
                    if watcher is not None and not watcher.done():
                        watcher.cancel()
                        await asyncio.gather(watcher, return_exceptions=True)

    async def test_live_route_supervises_head_error_and_shutdown_settles_pending_head(self):
        for outcome in ('error', 'decision', 'cancel', 'stop_error'):
            with self.subTest(outcome=outcome):
                started, fail = asyncio.Event(), asyncio.Event()
                settled, zeros, head_tasks = [], [], []
                trial = SingleTrial()

                class Robot:
                    def look_at_person(self, uid, head):
                        async def command():
                            started.set()
                            try:
                                await fail.wait()
                                raise OSError('async route head failure')
                            finally:
                                await asyncio.sleep(.02)
                                settled.append('head')
                        task = asyncio.create_task(command())
                        head_tasks.append(task)
                        return task

                    def move_base(self, distance, *, speed, acceleration):
                        async def command():
                            try:
                                await asyncio.Event().wait()
                            finally:
                                settled.append('base')
                        return asyncio.create_task(command())

                    def base_vel(self, x, r):
                        assert 'base' in settled
                        zeros.append(time.monotonic())
                        if outcome == 'stop_error':
                            raise OSError('mock stop failure')

                    async def next_frame(self, timeout):
                        await asyncio.sleep(0)
                        if not started.is_set():
                            return perception(person(17))
                        await asyncio.Event().wait()  # Error must wake supervision without another frame.

                    async def next_locomotion(self, timeout):
                        await asyncio.Event().wait()

                def send(observation):
                    from tests.test_decision_dispatch import response_for
                    payload = response_for(observation, 1, 'CONTINUE', False)
                    payload['final_decision'] = {'action': 'CONTINUE', 'reason': 'Continue.'} if outcome in ('decision', 'stop_error') else None
                    if outcome not in ('decision', 'stop_error'):
                        payload['policy_decision'] = None
                    return ObservationResponse(200, payload)

                args = parse_args(['--decision-dry-run', '--single-trial', '--route-trial',
                                   '--minimum-send-interval', '0'])
                with patch('robot.navel_client.main.SingleTrial', return_value=trial), \
                        patch.object(ObservationTransport, 'send', side_effect=send), \
                        contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(collect_and_stream(Robot(), args))
                    await asyncio.wait_for(started.wait(), 1)
                    if outcome == 'error':
                        fail.set()
                        with self.assertRaisesRegex(OSError, 'async route head failure'):
                            await asyncio.wait_for(task, 1)
                        self.assertEqual(trial.phase, 'FAILED')
                    elif outcome == 'cancel':
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 1)
                        self.assertEqual(trial.phase, 'FAILED')
                    else:
                        self.assertIs(await asyncio.wait_for(task, 1), trial)
                        self.assertEqual(trial.phase, 'FAILED' if outcome == 'stop_error' else 'DECIDED')
                self.assertEqual(settled.count('head'), 1)
                self.assertTrue(all(t.done() for t in head_tasks))
                self.assertTrue(zeros)


if __name__ == "__main__":
    unittest.main()
