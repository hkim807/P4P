"""Admission is independent of owned async execution and its immutable state."""
import asyncio
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch
from navel_test_support import setup_runtime, response, wait_for
from robot.navel_client.behavior import BehaviorController


class BehaviorControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_and_immutable_lifecycle(self):
        robot, rt, controller, _, _ = await setup_runtime(self)
        admitted = controller.handle_response(response(rt, distance=1.2, speed=.1))
        self.assertEqual(admitted.status, 'ACCEPTED')
        self.assertEqual(controller.execution_state.status, 'ACCEPTED')
        state = await controller.active_task
        self.assertEqual(state.status, 'DRY_RUN_COMPLETED')
        self.assertEqual(state.result.requested_target, admitted.requested_target)
        self.assertEqual(dict(state.result.parameters)['applied_stand_off_m'], 1.2)
        self.assertEqual(dict(state.result.parameters)['applied_speed_cap_mps'], .1)
        self.assertEqual(robot.calls, [])
        self.assertIsNone(state.result.approach)
        with self.assertRaises(FrozenInstanceError):
            state.latest_error = 'mutate'
        await controller.shutdown()
        self.assertEqual(robot.calls, [])

    async def test_busy_duplicate_null_unsupported_do_not_overwrite_active(self):
        robot, rt, controller, _, _ = await setup_runtime(self, execute=True)
        payload = response(rt)
        self.assertEqual(controller.handle_response(payload).status, 'ACCEPTED')
        state = controller.execution_state
        self.assertEqual(controller.handle_response(payload).status, 'DUPLICATE')
        self.assertEqual(controller.handle_response(response(rt, decision='same', target=state.target_human_id)).status, 'ALREADY_RUNNING')
        self.assertEqual(controller.handle_response(response(rt, decision='other', target='777')).status, 'BUSY')
        for action in ('WAIT', 'AVOID', 'GREET', 'ORIENT', 'CONTINUE', 'MONITOR', 'GUIDE', 'YIELD', 'SLOW', 'RESUME', 'DISENGAGE'):
            result = controller.handle_response(response(rt, decision=action, action=action, speed=.2))
            self.assertEqual(result.status, 'UNSUPPORTED_ACTION')
        self.assertEqual(controller.handle_response({'behavior_intent': None}).status, 'NO_INTENT')
        self.assertIs(controller.execution_state, state)
        await controller.cancel_active()
        self.assertEqual(controller.execution_state.status, 'CANCELLED')
        self.assertEqual(robot.calls, [])  # cancelled before coroutine was first scheduled

    async def test_missing_malformed_and_mapper_failure(self):
        robot, rt, controller, _, _ = await setup_runtime(self)
        self.assertEqual(controller.handle_response({}).status, 'NO_INTENT')
        for payload in ({'behavior_intent': 'bad'}, response(rt, action='FLY'), []):
            self.assertEqual(controller.handle_response(payload).status, 'INVALID_INTENT')
        with patch.object(controller._mapper, 'map', side_effect=RuntimeError('mapper failure')):
            result = controller.handle_response(response(rt))
        self.assertIn('mapper failure', result.error)
        self.assertIsNone(controller.execution_state)
        self.assertEqual(robot.calls, [])

    async def test_handler_exception_is_observed(self):
        _, rt, controller, _, _ = await setup_runtime(self)
        with patch.object(controller._dispatcher, 'dispatch', side_effect=RuntimeError('handler failure')):
            controller.handle_response(response(rt))
            state = await controller.active_task
        self.assertEqual(state.status, 'FAILED')
        self.assertEqual(state.latest_error, 'handler failure')
        self.assertEqual(controller.robot_context(), {'task': 'ERROR', 'controller_status': 'FAULT'})

    async def test_expiry_counts_time_before_receipt_and_acquisition(self):
        robot, rt, controller, _, _ = await setup_runtime(self)
        payload = response(rt, validity=1)
        await asyncio.sleep(.01)
        self.assertEqual(controller.handle_response(payload).status, 'EXPIRED')
        payload = response(rt, decision='acquire', validity=100)
        self.assertEqual(controller.handle_response(payload).status, 'ACCEPTED')
        state = await controller.active_task
        self.assertEqual(state.status, 'FAILED')
        self.assertIn('deadline', state.latest_error)
        self.assertEqual(robot.calls, [])

    async def test_wrong_source_clock_uid_missing_ambiguous_and_stale_target(self):
        robot, rt, controller, _, _ = await setup_runtime(self)
        payload = response(rt)
        payload['behavior_intent']['created_at_us'] += 1
        self.assertEqual(controller.handle_response(payload).status, 'INVALID_INTENT')
        payload = response(rt, decision='foreign')
        payload['behavior_intent']['observation_id'] = 'another-sensor'
        self.assertEqual(controller.handle_response(payload).status, 'INVALID_INTENT')
        self.assertEqual(controller.handle_response(response(rt, decision='uid', target='person-123')).status, 'INVALID_INTENT')
        self.assertEqual(controller.handle_response(response(rt, decision='absent', target='999')).status, 'TARGET_UNAVAILABLE')
        rt.people.append(dict(rt.people[0], uid=888))
        self.assertEqual(controller.handle_response(response(rt, decision='ambiguous')).status, 'TARGET_UNAVAILABLE')
        rt.people.pop()
        rt.perception_at -= 1
        self.assertEqual(controller.handle_response(response(rt, decision='stale')).status, 'TARGET_UNAVAILABLE')
        self.assertEqual(robot.calls, [])

    async def test_uid_change_uses_retained_source_context(self):
        _, rt, controller, _, _ = await setup_runtime(self)
        payload = response(rt)
        original_uid = payload['behavior_intent']['target_human_id']
        await asyncio.sleep(.11)
        self.assertEqual(controller.handle_response(payload).status, 'ACCEPTED')
        state = await controller.active_task
        self.assertEqual(state.status, 'DRY_RUN_COMPLETED')
        self.assertEqual(state.result.requested_target, original_uid)

    async def test_reject_parameters_and_dedup_is_bounded(self):
        robot, rt, controller, _, _ = await setup_runtime(self)
        controller._dedup_limit = 2
        for i, distance in enumerate((.2, 1.6, float('nan'))):
            self.assertEqual(controller.handle_response(response(rt, decision=str(i), distance=distance)).status, 'INVALID_INTENT')
        self.assertEqual(controller.handle_response(response(rt, decision='speed', speed=0)).status, 'INVALID_INTENT')
        self.assertLessEqual(len(controller._seen), 2)
        self.assertEqual(robot.calls, [])

    async def test_dry_run_cancel_during_sampling_never_stops_or_speaks(self):
        robot, rt, controller, _, _ = await setup_runtime(self)
        controller.handle_response(response(rt))
        await wait_for(lambda: rt.action_active)
        await controller.cancel_active()
        self.assertEqual(controller.execution_state.status, 'CANCELLED')
        self.assertEqual(robot.calls, [])
