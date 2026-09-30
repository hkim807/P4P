"""Mock-only fixed YIELD, shared ownership, speech and real pipeline coverage."""
import asyncio
import json
import math
import unittest
from unittest.mock import patch

from navel_test_support import SimRobot, setup_runtime, response, wait_for
from robot.navel_client.behavior.actions.yield_to_person import yield_to_person
from robot.navel_client.behavior.intent import NavelBehaviorIntent, NavelIntentParseError
from robot.navel_client.behavior.controller import BehaviorController
from robot.navel_client.main import _send_observations
from robot.navel_client.transport import ObservationResponse
from app.domain.models import Action, BehaviorIntent
from app.decision.llm_policy import LLMPolicyBridge, LLMPolicyError, behavior_selection_schema
from app.server import create_app
from test_llm_policy import RecordingLLM, selection_json, social_state


class YieldRobot(SimRobot):
    def __init__(self):
        super().__init__(visible=False)
        self.events = []
        self.block = None
        self.fail = None
        self.speech_delay = .01
        self.speech_active = 0
        self.owned = []

    def say(self, text):
        name = 'initial' if text == 'conflict person detected' else 'final'
        self.events.append(('say', text))
        async def speak():
            self.speech_active += 1
            try:
                if self.block == name:
                    await asyncio.Event().wait()
                await asyncio.sleep(self.speech_delay)
                if self.fail == name:
                    raise RuntimeError(name + ' speech failed')
                self.events.append((name + '_finished',))
            finally:
                self.speech_active -= 1
        task = asyncio.create_task(speak())
        self.owned.append(task)
        return task

    def _movement(self, name, distance, angle, speed, acceleration):
        self.events.append((name, distance or angle, speed, acceleration))
        assert not self.speech_active
        async def go():
            self.active += 1
            try:
                assert self.active == 1
                if self.block == name:
                    await asyncio.Event().wait()
                if self.fail == name:
                    raise RuntimeError(name + ' failed')
                # Mock kinematics: exercise negative velocity and post-turn heading.
                for _ in range(5):
                    self.v = distance / .1
                    self.w = math.radians(angle) / .1
                    self.x += distance / 5 * math.cos(self.yaw)
                    self.y += distance / 5 * math.sin(self.yaw)
                    self.yaw += math.radians(angle) / 5
                    await asyncio.sleep(.02)
                self.events.append((name + '_finished',))
            finally:
                self.active -= 1
                self.v = self.w = 0.
                self.events.append((name + '_released',))
        task = asyncio.create_task(go())
        self.owned.append(task)
        return task

    def rotate_base(self, angle, speed=None, acceleration=None):
        return self._movement('rotation', 0, angle, speed, acceleration)

    def move_base(self, distance, speed=None, acceleration=None):
        return self._movement('escape', distance, 0, speed, acceleration)

    def base_vel(self, x, r):
        super().base_vel(x, r)
        self.events.append(('zero',))


class FourIntentTests(unittest.TestCase):
    def test_exact_canonical_vocabulary_and_reject_all_old_names(self):
        self.assertEqual({a.value for a in Action}, {'CONTINUE', 'APPROACH', 'ENGAGE', 'YIELD'})
        state = social_state()
        self.assertEqual(set(behavior_selection_schema(state, [])['$defs']['Action']['enum']),
                         {a.value for a in Action})
        valid = LLMPolicyBridge(RecordingLLM(selection_json(action='YIELD'))).decide(state, [])
        payload = valid.model_dump(mode='json')
        for action in ('MONITOR', 'ORIENT', 'SLOW', 'AVOID', 'GREET', 'GUIDE', 'WAIT', 'RESUME', 'DISENGAGE'):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    BehaviorIntent.model_validate(dict(payload, action=action))
                with self.assertRaises(NavelIntentParseError):
                    NavelBehaviorIntent.from_payload(dict(payload, action=action))
                with self.assertRaises(LLMPolicyError):
                    LLMPolicyBridge(RecordingLLM(selection_json(action=action))).decide(state, [])

    def test_yield_rejects_all_variable_preferences_in_policy_domain_and_client(self):
        for key, value in {'target_speed_mps': .2, 'preferred_social_distance_m': 1.2,
                           'passing_side': 'LEFT', 'hold_duration_s': 1., 'orientation_target_rad': 0.}.items():
            state = social_state()
            valid = LLMPolicyBridge(RecordingLLM(selection_json(action='YIELD'))).decide(state, [])
            payload = valid.model_dump(mode='json')
            payload['preferences'] = {key: value}
            with self.subTest(key=key):
                with self.assertRaises(ValueError): BehaviorIntent.model_validate(payload)
                with self.assertRaises(NavelIntentParseError): NavelBehaviorIntent.from_payload(payload)
                with self.assertRaises(LLMPolicyError):
                    LLMPolicyBridge(RecordingLLM(selection_json(action='YIELD', preferences={key: value}))).decide(state, [])


class YieldTests(unittest.IsolatedAsyncioTestCase):
    async def setup(self, **config):
        robot, rt, controller, queue, readers = await setup_runtime(self, YieldRobot(), **config)
        emit = rt.log.emit
        def log(event, **data):
            emit(event, **data)
            if event == 'BASE_STOPPED': robot.events.append(('stopped',))
        rt.log.emit = log
        return robot, rt, controller, queue, readers

    async def execute(self, controller, rt, **kw):
        payload = response(rt, action='YIELD', **kw)
        self.assertEqual(controller.handle_response(payload).status, 'ACCEPTED')
        return await controller.active_task

    async def test_target_free_exact_order_parameters_and_no_return(self):
        robot, rt, controller, _, _ = await self.setup(execute=True)
        with patch.object(rt, 'resolve', side_effect=AssertionError('target resolution')), \
             patch.object(rt, 'detect', side_effect=AssertionError('target acquisition')):
            state = await self.execute(controller, rt)
        self.assertEqual(state.status, 'COMPLETED')
        events = [e for e in robot.events if e[0] != 'zero' and not e[0].endswith('_released')]
        self.assertEqual(events, [('say', 'conflict person detected'), ('initial_finished',),
            ('rotation', 100., 30., 35.), ('rotation_finished',), ('stopped',),
            ('escape', -.60, .25, .35), ('escape_finished',), ('stopped',),
            ('say', 'yield complete'), ('final_finished',)])
        self.assertAlmostEqual(robot.yaw, math.radians(100))
        self.assertAlmostEqual(robot.x, -.60 * math.cos(math.radians(100)))
        self.assertAlmostEqual(robot.y, -.60 * math.sin(math.radians(100)))
        self.assertTrue(state.result.yield_result.movement_completed)
        self.assertIn('DISPLACEMENT_NOT_VERIFIED', state.result.yield_result.verification)
        self.assertIsNone(state.result.resolved_target)
        self.assertFalse(rt.action_active)
        self.assertEqual(controller.robot_context(), {'task': 'COMPLETE', 'controller_status': 'STOPPED'})
        count = len(robot.events)
        self.assertEqual(controller.handle_response(response(rt, action='YIELD')).status, 'DUPLICATE')
        self.assertEqual(len(robot.events), count)
        later = await self.execute(controller, rt, decision='later')
        self.assertEqual(later.status, 'COMPLETED')

    async def test_dry_run_is_silent_and_inversion_is_local(self):
        robot, rt, controller, _, _ = await self.setup(invert_yield_turn_direction=True)
        state = await self.execute(controller, rt)
        self.assertEqual(state.status, 'DRY_RUN_COMPLETED')
        self.assertEqual(robot.events, [])
        self.assertEqual(robot.calls, [])
        self.assertEqual(dict(state.result.parameters)['angle_deg'], -100.)
        self.assertEqual(state.result.yield_result.applied_parameters, ())
        rt.cfg.execute = True
        state = await self.execute(controller, rt, decision='inverted')
        self.assertIn(('rotation', -100., 30., 35.), robot.events)
        self.assertEqual(state.status, 'COMPLETED')

    async def test_cancellation_each_phase_awaits_owned_tasks_and_releases_lease(self):
        for phase in ('initial', 'rotation', 'escape', 'final'):
            with self.subTest(phase=phase):
                robot, rt, controller, _, readers = await self.setup(execute=True)
                robot.block = phase
                controller.handle_response(response(rt, action='YIELD'))
                def phase_started():
                    if phase == 'initial':
                        return robot.speech_active > 0
                    if phase == 'final':
                        return robot.speech_active > 0 and ('say', 'yield complete') in robot.events
                    return robot.active > 0 and any(e[0] == phase for e in robot.events)
                await wait_for(phase_started)
                self.assertEqual(controller.robot_context()['task'], 'YIELDING')
                await controller.cancel_active()
                state = controller.execution_state
                self.assertEqual(state.status, 'CANCELLED')
                self.assertEqual(state.result.yield_result.movement_completed, phase == 'final')
                self.assertEqual(robot.active, 0)
                self.assertEqual(robot.speech_active, 0)
                self.assertTrue(all(t.done() for t in robot.owned))
                self.assertTrue(all(not t.done() for t in readers))
                self.assertFalse(rt.action_active)
                if phase != 'final': self.assertNotIn(('say', 'yield complete'), robot.events)
                robot.block = None
                later = await self.execute(controller, rt, decision='later')
                self.assertEqual(later.status, 'COMPLETED')

    async def test_failure_and_timeout_outcomes(self):
        for phase in ('initial', 'rotation', 'escape', 'final'):
            with self.subTest(phase=phase):
                robot, rt, controller, _, _ = await self.setup(execute=True)
                robot.fail = phase
                state = await self.execute(controller, rt)
                result = state.result.yield_result
                self.assertEqual(state.status, 'FAILED')
                self.assertEqual(result.movement_completed, phase == 'final')
                self.assertEqual(bool(result.speech_error), phase in {'initial', 'final'})
                self.assertTrue(all(t.done() for t in robot.owned))
                if phase != 'final': self.assertNotIn(('say', 'yield complete'), robot.events)
                if phase == 'initial': self.assertFalse(any(e[0] == 'rotation' for e in robot.events))
        robot, rt, controller, _, _ = await self.setup(execute=True, speech_timeout_s=.03)
        robot.block = 'initial'
        state = await self.execute(controller, rt)
        self.assertIn('TimeoutError', state.result.yield_result.speech_error)
        self.assertEqual(robot.speech_active, 0)
        self.assertFalse(any(e[0] == 'rotation' for e in robot.events))

    async def test_expiry_after_speech_and_latency_and_local_cap(self):
        robot, rt, controller, _, _ = await self.setup(execute=True)
        robot.speech_delay = .3
        state = await self.execute(controller, rt, validity=250)
        self.assertEqual(state.status, 'FAILED')
        self.assertIn('expired', state.latest_error)
        self.assertEqual(state.result.yield_result.completed_phase, 'INITIAL_SPEECH')
        self.assertFalse(any(e[0] == 'rotation' for e in robot.events))
        payload = response(rt, action='YIELD', decision='late', validity=15000)
        context = rt.contexts[payload['behavior_intent']['observation_id']]
        context['timestamp_us'] -= 15_000_001
        payload['behavior_intent']['created_at_us'] = context['timestamp_us']
        self.assertEqual(controller.handle_response(payload).status, 'EXPIRED')
        # No receipt-time renewal and the source timestamp must match exactly.
        payload['behavior_intent']['decision_id'] = 'wrong-source'
        payload['behavior_intent']['created_at_us'] += 1
        self.assertEqual(controller.handle_response(payload).status, 'INVALID_INTENT')

    async def test_admission_window_does_not_bound_started_movement(self):
        robot, rt, controller, _, _ = await self.setup(execute=True)
        state = await self.execute(controller, rt, validity=250)
        self.assertEqual(state.status, 'COMPLETED')  # stop confirmations alone exceed 250 ms

    async def test_busy_action_and_target_comparisons_and_shared_owner(self):
        robot, rt, controller, _, _ = await self.setup(execute=True)
        robot.block = 'initial'
        controller.handle_response(response(rt, action='YIELD', target='17'))
        await wait_for(lambda: robot.speech_active > 0)
        snapshot = controller.execution_state
        self.assertEqual(controller.handle_response(response(rt, action='YIELD', target='17', decision='same')).status, 'ALREADY_RUNNING')
        self.assertEqual(controller.handle_response(response(rt, action='APPROACH', target='17', decision='approach')).status, 'BUSY')
        self.assertEqual(controller.handle_response(response(rt, action='YIELD', target='18', decision='different')).status, 'BUSY')
        self.assertIs(controller.execution_state, snapshot)
        result = await yield_to_person(rt)
        self.assertEqual(result.status, 'FAILED')
        self.assertIn('active', result.error)
        self.assertTrue(rt.action_active)
        await controller.cancel_active()

    async def test_future_and_coroutine_speech_are_awaited(self):
        robot, rt, controller, _, _ = await self.setup(execute=True)
        original = robot.say
        def future_say(text):
            future = asyncio.get_running_loop().create_future()
            async def finish():
                await original(text)
                future.set_result(None)
            task = asyncio.create_task(finish())
            robot.owned.append(task)
            return future
        robot.say = future_say
        self.assertEqual((await self.execute(controller, rt)).status, 'COMPLETED')
        async def coroutine_say(text): return await original(text)
        robot.say = coroutine_say
        self.assertEqual((await self.execute(controller, rt, decision='coroutine')).status, 'COMPLETED')

    async def test_server_produced_yield_and_observation_sending_continue(self):
        robot, rt, controller, queue, readers = await self.setup(execute=True)
        robot.speech_delay = .15
        llm = RecordingLLM(selection_json(action='YIELD', valid_for_ms=15000))
        client = create_app(llm=llm).test_client()
        owners = {'frame': set(), 'odom': set()}
        frame, odom = robot.next_frame, robot.next_locomotion
        async def next_frame(timeout):
            owners['frame'].add(asyncio.current_task())
            return await frame(timeout)
        async def next_odom(timeout):
            owners['odom'].add(asyncio.current_task())
            return await odom(timeout)
        robot.next_frame, robot.next_locomotion = next_frame, next_odom
        class Transport:
            def __init__(self): self.calls = []
            def send(self, observation, **kwargs):
                self.calls.append((observation, robot.speech_active, robot.active))
                if len(self.calls) == 1:
                    result = client.post('/api/v1/observations?force_decision=1', json=observation)
                    return ObservationResponse(result.status_code, result.get_json())
                return ObservationResponse(200, {'behavior_intent': None})
        transport = Transport()
        sender = asyncio.create_task(_send_observations(queue, transport, controller,
            minimum_send_interval_s=0, force_decision=True, print_only=False))
        try:
            await wait_for(lambda: controller.active_task is not None)
            state = await controller.active_task
            self.assertEqual(state.status, 'COMPLETED')
            self.assertTrue(any(speech for _, speech, _ in transport.calls))
            self.assertTrue(any(moving for _, _, moving in transport.calls))
            self.assertTrue(any(o['robot']['task'] == 'YIELDING' for o, _, _ in transport.calls))
            self.assertEqual([len(owners[k]) for k in ('frame', 'odom')], [1, 1])
            self.assertTrue(all(not t.done() for t in readers))
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    async def test_motion_timeout_and_stop_failure_lockout(self):
        robot, rt, controller, _, readers = await self.setup(execute=True)
        robot.block = 'rotation'
        motion = rt.motion
        async def bounded(factory, timeout, **kwargs):
            return await motion(factory, timeout=.04, **kwargs)
        with patch.object(rt, 'motion', side_effect=bounded):
            state = await self.execute(controller, rt)
        self.assertEqual(state.status, 'FAILED')
        self.assertIn('Motion timed out', state.latest_error)
        self.assertNotIn(('say', 'yield complete'), robot.events)
        self.assertEqual(robot.active, 0)
        self.assertTrue(all(t.done() for t in robot.owned))
        self.assertIsNone(rt.stop_failure)
        # Missing fresh stop odometry must lock out every subsequent behaviour.
        robot.block = 'escape'
        controller.handle_response(response(rt, action='YIELD', decision='stop-failure'))
        await wait_for(lambda: robot.active and any(e[0] == 'escape' for e in robot.events))
        robot.odometry_on = False
        rt.pose_at -= 1
        await controller.cancel_active()
        self.assertEqual(controller.execution_state.status, 'FAILED')
        self.assertIsNotNone(rt.stop_failure)
        self.assertEqual(controller.handle_response(response(rt, action='YIELD', decision='locked')).status, 'FAILED')
        self.assertEqual(controller.robot_context(), {'task': 'ERROR', 'controller_status': 'FAULT'})
        self.assertTrue(all(not t.done() for t in readers))

    async def test_speech_timeout_after_completed_escape_preserves_movement(self):
        robot, rt, controller, _, _ = await self.setup(execute=True, speech_timeout_s=.03)
        robot.block = 'final'
        state = await self.execute(controller, rt)
        outcome = state.result.yield_result
        self.assertEqual(state.status, 'FAILED')
        self.assertEqual(outcome.completed_phase, 'ESCAPE_STOPPED')
        self.assertTrue(outcome.movement_completed)
        self.assertIn('TimeoutError', outcome.speech_error)
        self.assertTrue(all(t.done() for t in robot.owned))

    async def test_sensor_checks_and_configurable_admission_cap(self):
        robot, rt, controller, _, _ = await self.setup(execute=True, max_admission_age_ms=250)
        payload = response(rt, action='YIELD', validity=15000)
        await asyncio.sleep(.26)
        self.assertEqual(controller.handle_response(payload).status, 'EXPIRED')
        self.assertEqual(robot.events, [])
        # No people is valid; a stale perception stream is not.
        rt.perception_at -= 1
        state = await self.execute(controller, rt, decision='stale')
        self.assertEqual(state.status, 'FAILED')
        self.assertIn('Perception', state.latest_error)
        self.assertFalse(any(e[0] == 'say' for e in robot.events))
        # The default lease still refuses target-free APPROACH.
        with self.assertRaises(ValueError):
            async with rt.action():
                self.fail('Approach must require a canonical UID')

    async def test_sdk_completion_does_not_claim_displacement(self):
        robot, rt, controller, _, _ = await self.setup(execute=True)
        def stationary_motion(**kwargs):
            future = asyncio.get_running_loop().create_future()
            future.set_result(None)
            return future
        robot.rotate_base = robot.move_base = stationary_motion
        state = await self.execute(controller, rt)
        self.assertEqual(state.status, 'COMPLETED')
        self.assertEqual((robot.x, robot.y, robot.yaw), (0., 0., 0.))
        self.assertIn('DISPLACEMENT_NOT_VERIFIED', state.result.yield_result.verification)

    async def test_approach_can_acquire_after_yield_releases_ownership(self):
        robot, rt, controller, _, _ = await self.setup(execute=True)
        self.assertEqual((await self.execute(controller, rt)).status, 'COMPLETED')
        robot.person = (robot.x + 2.8 * math.cos(robot.yaw), robot.y + 2.8 * math.sin(robot.yaw))
        robot.visible = True
        await wait_for(lambda: bool(rt.people))
        payload = response(rt, action='APPROACH', decision='after-yield')
        self.assertEqual(controller.handle_response(payload).status, 'ACCEPTED')
        await wait_for(lambda: rt.target is not None and robot.active > 0)
        self.assertEqual(controller.handle_response(response(rt, action='YIELD', decision='during-approach',
            target=str(rt.target['uid']))).status, 'BUSY')
        state = await controller.active_task
        self.assertEqual(state.status, 'COMPLETED')
        self.assertIsNotNone(state.result.approach)
        self.assertIsNone(state.result.yield_result)

    async def test_repeated_cancellation_during_stop_keeps_readers_and_lock(self):
        robot, rt, controller, _, readers = await self.setup(execute=True)
        controller.handle_response(response(rt, action='YIELD'))
        await wait_for(lambda: ('zero',) in robot.events)
        controller.active_task.cancel()
        await asyncio.sleep(.02)
        controller.active_task.cancel()
        self.assertEqual(controller.handle_response(response(rt, action='YIELD', decision='while-stopping')).status, 'BUSY')
        state = await controller.active_task
        self.assertEqual(state.status, 'CANCELLED')
        self.assertIsNone(rt.stop_failure)
        self.assertEqual(robot.active, 0)
        self.assertFalse(rt.action_active)
        self.assertTrue(all(t.done() for t in robot.owned))
        self.assertTrue(all(not t.done() for t in readers))
        self.assertNotIn(('say', 'yield complete'), robot.events)


class AdmissionContractTests(unittest.TestCase):
    def test_producer_domain_client_bounds_agree(self):
        from app.decision.llm_policy import MIN_INTENT_VALIDITY_MS, MAX_INTENT_VALIDITY_MS
        state = social_state(humans=False)
        schema = behavior_selection_schema(state, [])['properties']['valid_for_ms']
        domain_schema = BehaviorIntent.model_json_schema()['properties']['valid_for_ms']
        self.assertEqual((schema['minimum'], schema['maximum']), (250, 15000))
        self.assertEqual((domain_schema['minimum'], domain_schema['maximum']),
                         (MIN_INTENT_VALIDITY_MS, MAX_INTENT_VALIDITY_MS))
        for validity in (250, 15000):
            intent = LLMPolicyBridge(RecordingLLM(selection_json(action='YIELD', valid_for_ms=validity))).decide(state, [])
            parsed = NavelBehaviorIntent.from_payload(intent.model_dump(mode='json'))
            self.assertEqual(parsed.valid_for_ms, validity)
            self.assertIsNone(parsed.target_human_id)
        for validity in (249, 15001):
            with self.assertRaises(LLMPolicyError):
                LLMPolicyBridge(RecordingLLM(selection_json(valid_for_ms=validity))).decide(state, [])
            payload = intent.model_dump(mode='json')
            payload['valid_for_ms'] = validity
            with self.assertRaises(ValueError): BehaviorIntent.model_validate(payload)
            with self.assertRaises(NavelIntentParseError): NavelBehaviorIntent.from_payload(payload)
