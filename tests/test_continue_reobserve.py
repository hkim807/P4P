"""Decision cycles share a route and sensors, but never share accepted model results."""
import asyncio
import contextlib
import io
import unittest
from unittest.mock import patch

from robot.navel_client.behaviour_dispatch import continue_route, engage_person
from robot.navel_client.main import _send_observations, collect_and_stream, parse_args
from robot.navel_client.single_trial import SingleTrial
from robot.navel_client.transport import ObservationResponse
from tests.fixtures import frame, locomotion, perception, person
from tests.test_decision_dispatch import response_for


class RearmTests(unittest.TestCase):
    def test_continue_rearm_requires_new_evidence_and_rejects_previous_model_result(self):
        now = [1_000_000]
        trial = SingleTrial('llm', monotonic=lambda: now[0] / 1e6,
                            monotonic_us=lambda: now[0])

        def observe(sequence):
            observation = {**frame(), 'timestamp': now[0]}
            trial.note_observation(observation)
            trial.observe(response_for(observation, sequence), observation)
            return {**trial.model_identity(), 'request_id': str(sequence),
                    'source_state_id': f'session-a:{sequence}',
                    'source_robot_timestamp_us': now[0]}

        source = observe(1)
        self.assertTrue(trial.register_request(source))
        old_result = {**source, 'status': 'succeeded',
                      'decision': {'action': 'CONTINUE', 'reason': 'First cycle.'}}
        self.assertTrue(trial.accept_model_result(old_result))
        trial.start_execution()
        now[0] += 100_000
        trial.reobserve_after_continue()
        self.assertEqual(trial.session_id, 'session-a')
        self.assertFalse(trial.ready)
        self.assertIsNone(trial.pending_request)
        self.assertNotEqual(trial.trial_id, source['trial_id'])
        observation = {**frame(), 'timestamp': source['source_robot_timestamp_us']}
        trial.observe(response_for(observation, 1), observation)
        self.assertFalse(trial.ready)
        self.assertFalse(trial.register_request(source))
        now[0] += 1
        new_source = observe(2)
        self.assertTrue(trial.register_request(new_source))
        self.assertFalse(trial.accept_model_result(old_result))
        self.assertTrue(trial.accept_model_result({**new_source, 'status': 'succeeded',
                        'decision': {'action': 'ENGAGE', 'reason': 'New cycle.'}}))
        trial.start_execution()
        with self.assertRaises(ValueError):
            trial.reobserve_after_continue()

    def test_rearm_preserves_overall_deadline_but_renews_cycle_deadline(self):
        now = [0.0]
        trial = SingleTrial(wait_timeout_s=2, monotonic=lambda: now[0], monotonic_us=lambda: 0)
        trial.overall_deadline = 5
        trial._decision = {'action': 'CONTINUE', 'reason': 'Test.'}
        trial.phase = 'EXECUTING'
        now[0] = 4
        trial.reobserve_after_continue()
        self.assertEqual(trial.deadline, 6)
        self.assertEqual(trial.overall_deadline, 5)
        now[0] = 5
        trial.tick()
        self.assertEqual(trial.failure_reason, 'REOBSERVE_TIMEOUT')

    def test_cli_requires_execution_route_and_valid_loop_limits(self):
        base = ['--single-trial', '--single-trial-execute', '--route-trial']
        self.assertFalse(parse_args(base).reobserve_on_continue)
        self.assertTrue(parse_args(base + ['--reobserve-on-continue']).reobserve_on_continue)
        for flags in (['--reobserve-on-continue'],
                      base + ['--reobserve-on-continue', '--continue-reobserve-delay', '-1'],
                      base + ['--reobserve-on-continue', '--reobserve-timeout', 'nan'],
                      ['--debug-action', 'ENGAGE', '--reobserve-on-continue']):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse_args(flags)


class ReobserveIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_frame_from_before_rearm_never_starts_new_request(self):
        now = [1_000_000]
        trial = SingleTrial('llm', monotonic_us=lambda: now[0])
        trial._decision = {'action': 'CONTINUE', 'reason': 'Test.'}
        trial.phase = 'EXECUTING'
        trial.reobserve_after_continue()
        trial.session_id = 'session-a'
        queue = asyncio.Queue()
        queue.put_nowait(frame())
        calls = []

        class Transport:
            def send_trial_observation(self, observation, capture, identity):
                calls.append(observation['timestamp'])
                payload = response_for(observation, 2)
                payload['model_trial'] = {'result': {**identity, 'request_id': 'new',
                    'source_state_id': 'session-a:2', 'source_robot_timestamp_us': observation['timestamp']}}
                return ObservationResponse(200, payload)

        with contextlib.redirect_stdout(io.StringIO()):
            sender = asyncio.create_task(_send_observations(queue, Transport(),
                minimum_send_interval_s=0, print_only=False, trial=trial,
                decision_event=asyncio.Event()))
            try:
                await asyncio.wait_for(queue.join(), 1)
                self.assertEqual(calls, [])
                now[0] += 1
                fresh = {**frame(), 'timestamp': now[0]}
                trial.note_observation(fresh)
                queue.put_nowait(fresh)
                await asyncio.wait_for(queue.join(), 1)
                self.assertEqual(calls, [now[0]])
                self.assertEqual(trial.pending_request['request_id'], 'new')
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)

    async def run_client(self, policy='rules', final_action='ENGAGE', *, repeat=True,
                         finish_route=False, cancel=False, head_failure=False, timeout=False):
        events, holder, requests = [], {}, {}
        glancing, release_head, route_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        observations = []

        class Robot:
            async def next_frame(self, timeout):
                await asyncio.sleep(.005)
                return perception(person(gaze_overlap=.99))

            async def next_locomotion(self, timeout):
                await asyncio.sleep(.005)
                return locomotion()

            async def move_base(self, *args, **kwargs):
                events.append('route_start')
                try:
                    await route_finished.wait()
                finally:
                    events.append('route_end')

            def base_vel(self, *args):
                events.append('zero')

            async def say(self, text):
                events.append('say')

        class Head:
            def __init__(self, *args):
                pass

            def preflight(self):
                pass

            async def neutral(self):
                events.append('neutral')

            async def look_at_person(self, uid):
                events.append('look')

            async def glance(self, uid):
                events.append('glance')
                glancing.set()
                if head_failure:
                    raise RuntimeError('head failed')
                if cancel or finish_route or timeout or not repeat:
                    await release_head.wait()
                else:
                    await asyncio.sleep(.02)
                events.append('neutral_after_glance')

        class Transport:
            def __init__(self, *args, **kwargs):
                pass

            def payload(self, observation):
                trial = holder['trial']
                observations.append((observation['timestamp'], trial.phase, trial.cycle))
                action = 'CONTINUE' if trial.cycle < 3 else final_action
                payload = response_for(observation, len(observations), action,
                                       action in ('APPROACH', 'ENGAGE'))
                payload['final_decision'] = {'action': action, 'reason': 'Test cycle.'}
                return payload

            def send(self, observation):
                return ObservationResponse(200, self.payload(observation))

            def open_model_trial(self, trial_id, policy, wait_s, max_age_s):
                events.append(('open', trial_id))
                requests[trial_id] = None
                return ObservationResponse(200, {'accepted': True, 'trial_id': trial_id,
                                                  'policy': policy, 'session_id': 'session-a'})

            def send_trial_observation(self, observation, capture, identity):
                payload = self.payload(observation)
                trial_id = identity['trial_id']
                if requests[trial_id] is None:
                    requests[trial_id] = {**identity, 'request_id': trial_id,
                        'source_state_id': payload['social_state']['state_id'],
                        'source_robot_timestamp_us': observation['timestamp'], 'status': 'pending',
                        'decision': payload['final_decision']}
                payload['model_trial'] = {'result': requests[trial_id]}
                return ObservationResponse(200, payload)

            def poll_model_trial(self, identity):
                return ObservationResponse(200, {'accepted': True,
                    'result': {**requests[identity['trial_id']], 'status': 'succeeded'}})

            def close_model_trial(self, identity):
                events.append(('close', identity['trial_id']))
                del requests[identity['trial_id']]
                return ObservationResponse(200, {'accepted': True})

        def make_trial(*args, **kwargs):
            holder['trial'] = SingleTrial(*args, **kwargs)
            return holder['trial']

        async def final_handler(context):
            events.append(('final', context.decision['action']))

        handlers = {'CONTINUE': continue_route, 'ENGAGE': engage_person,
                    'APPROACH': final_handler, 'YIELD': final_handler}
        flags = ['--single-trial', '--single-trial-execute', '--route-trial',
                 '--single-trial-policy', policy, '--minimum-send-interval', '0',
                 '--continue-reobserve-delay', '.01']
        if repeat:
            flags += ['--reobserve-on-continue']
        if timeout:
            flags += ['--reobserve-timeout', '.05']
        with patch('robot.navel_client.main.ObservationTransport', Transport), \
                patch('robot.navel_client.main.SingleTrial', side_effect=make_trial), \
                patch('robot.navel_client.main.TrialHeadController', Head), \
                patch('robot.navel_client.behaviour_dispatch.HANDLERS', handlers), \
                contextlib.redirect_stdout(io.StringIO()):
            task = asyncio.create_task(collect_and_stream(Robot(), parse_args(flags), navel_module=object()))
            try:
                if cancel or finish_route or not repeat:
                    await asyncio.wait_for(glancing.wait(), 1)
                    if cancel:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        route_finished.set()
                        release_head.set()
                        await asyncio.wait_for(task, 2)
                else:
                    await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        return holder['trial'], events, observations, requests

    async def test_rules_and_llm_continue_twice_then_execute_each_final_action_once(self):
        for policy in ('rules', 'llm'):
            for action in ('ENGAGE', 'APPROACH', 'YIELD'):
                with self.subTest(policy=policy, action=action):
                    trial, events, observations, requests = await self.run_client(policy, action)
                    self.assertEqual(trial.phase, 'COMPLETED')
                    self.assertEqual(trial.decision['action'], action)
                    self.assertEqual(trial.cycle, 3)
                    self.assertEqual(events.count('route_start'), 1)
                    self.assertEqual(events.count('glance'), 2)
                    self.assertTrue(any(phase == 'EXECUTING' for _, phase, _ in observations))
                    self.assertEqual(sorted(t for t, _, _ in observations), [t for t, _, _ in observations])
                    self.assertGreater(events.index('zero'), events.index('neutral_after_glance'))
                    if action == 'ENGAGE':
                        self.assertEqual(events.count('say'), 1)
                    else:
                        self.assertEqual(events.count(('final', action)), 1)
                    if policy == 'llm':
                        opened = [e[1] for e in events if isinstance(e, tuple) and e[0] == 'open']
                        closed = [e[1] for e in events if isinstance(e, tuple) and e[0] == 'close']
                        self.assertEqual(len(set(opened)), 3)
                        self.assertEqual(opened, closed)
                        self.assertEqual(requests, {})

    async def test_original_continue_still_waits_for_route_and_ends_trial(self):
        trial, events, _, _ = await self.run_client(repeat=False)
        self.assertEqual(trial.phase, 'COMPLETED')
        self.assertEqual(trial.cycle, 1)
        self.assertEqual(trial.decision['action'], 'CONTINUE')
        self.assertEqual(events.count('route_start'), 1)

    async def test_route_finish_cancellation_head_failure_and_overall_timeout_stop_loop(self):
        for outcome in ('finish_route', 'cancel', 'head_failure', 'timeout'):
            with self.subTest(outcome=outcome):
                trial, events, _, _ = await self.run_client(**{outcome: True})
                self.assertEqual(trial.phase, 'COMPLETED' if outcome == 'finish_route' else 'FAILED')
                self.assertEqual(trial.cycle, 1)
                self.assertIn('zero', events)
                if outcome == 'timeout':
                    self.assertEqual(trial.failure_reason, 'REOBSERVE_TIMEOUT')


if __name__ == '__main__':
    unittest.main()
