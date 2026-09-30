"""Preserved mathematical tests plus the integrated bounded action."""
import asyncio
import math
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from navel_test_support import setup_runtime, SimRobot, response, wait_for
from robot.navel_client.navel_runtime import associate, read_pose, world_point, body_point
from robot.navel_client.behavior.actions.approach_human import arc_plan, approach_human, ApproachResult

class MathsTests(unittest.TestCase):
    def test_arc_goal_faces_person_at_07m(self):
        for x,y in [(2,0),(1.8,.5),(1.8,-.5),(1,.6)]:
            plan=arc_plan(x,y)
            theta=math.radians(plan['angle'])
            gx=plan['distance'] if abs(theta)<1e-9 else plan['distance']/theta*math.sin(theta)
            gy=0 if abs(theta)<1e-9 else plan['distance']/theta*(1-math.cos(theta))
            self.assertAlmostEqual(math.hypot(x-gx,y-gy),.7)
            self.assertAlmostEqual(math.atan2(y-gy,x-gx),theta)
            self.assertLessEqual(plan['speed'],.25)

    def test_association_ignores_uid_change_rejects_ambiguity(self):
        old=dict(wx=2.,wy=.5,uid=123)
        a=dict(wx=2.01,wy=.5,uid=99)
        b=dict(wx=4.,wy=.5,uid=123)
        self.assertIs(associate([a,b],old),a)
        self.assertIsNone(associate([a,dict(wx=2.02,wy=.5,uid=87)],old))

    def test_odometry_bug_and_frame_roundtrip(self):
        packet=NS(odometry=NS(position=NS(x=2.,y=3.),orientation=NS(x=math.sin(.4),y=math.cos(.4),z=0,w=0),
                   velocity=NS(linear_x=.2,linear_y=.3,angular_z=0),time=1))
        pose=read_pose(packet)
        self.assertAlmostEqual(pose['yaw'],.8)
        self.assertEqual(pose['w'],.3)
        target=world_point(dict(x=1.,y=-.4),pose)
        x,y=body_point(target,pose)
        self.assertAlmostEqual(x,1.)
        self.assertAlmostEqual(y,-.4)



class ApproachTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_approach_alignment_and_parameter_propagation(self):
        robot, rt, controller, _, _ = await setup_runtime(self, execute=True)
        admission = controller.handle_response(response(rt, distance=.9, speed=.8))
        self.assertEqual(admission.status, 'ACCEPTED')
        state = await controller.active_task
        self.assertEqual(state.status, 'COMPLETED')
        result = state.result
        self.assertEqual(result.approach.status, 'APPROACHED_VERIFIED')
        self.assertTrue(result.approach.live_position_verified)
        self.assertLess(abs(result.approach.distance_m-.9), .1)
        self.assertLess(abs(result.approach.heading_error_deg), 4)
        self.assertEqual(dict(result.parameters)['applied_speed_cap_mps'], .25)
        self.assertEqual(dict(result.parameters)['target_speed_mps'], .8)
        self.assertGreaterEqual(robot.calls.count('rotate'), 1)
        self.assertNotIn('say', robot.calls)
        self.assertNotIn('cruise', robot.calls)
        self.assertEqual(robot.active, 0)

    async def test_face_loss_is_unverified(self):
        robot, rt, controller, _, _ = await setup_runtime(self, SimRobot(disappear_on_arc=True), execute=True)
        controller.handle_response(response(rt))
        state = await controller.active_task
        self.assertEqual(state.status, 'COMPLETED')
        self.assertEqual(state.result.approach.status, 'APPROACHED_UNVERIFIED')
        self.assertFalse(state.result.approach.live_position_verified)
        self.assertIn('arc_finished', robot.calls)

    async def test_second_execution_targets_new_person(self):
        robot, rt, controller, _, _ = await setup_runtime(self, execute=True)
        controller.handle_response(response(rt))
        first = await controller.active_task
        self.assertEqual(first.result.approach.status, 'APPROACHED_VERIFIED')
        # A new person, a different UID, and a new local observation context.
        original = robot.next_frame
        async def frame(timeout):
            f = await original(timeout)
            for p in f.persons:
                p.uid = 500
            return f
        robot.next_frame = frame
        robot.person = (robot.x+1.5*math.cos(robot.yaw), robot.y+1.5*math.sin(robot.yaw))
        await wait_for(lambda: '500' in next(reversed(rt.contexts.values()))['targets'])
        controller.handle_response(response(rt, decision='second', target='500'))
        second = await controller.active_task
        self.assertEqual(second.status, 'COMPLETED')
        self.assertEqual(second.result.requested_target, '500')
        self.assertEqual(second.result.resolved_target, '500')
        self.assertEqual(second.result.approach.status, 'APPROACHED_VERIFIED')

    async def test_cancel_stops_sender_before_zero_readers_remain_alive(self):
        robot, rt, controller, _, readers = await setup_runtime(self, execute=True)
        controller.handle_response(response(rt))
        await wait_for(lambda: robot.active > 0)
        await controller.shutdown()
        self.assertEqual(controller.execution_state.status, 'CANCELLED')
        self.assertEqual(robot.active, 0)
        self.assertLess(robot.calls.index('arc_finished'), robot.calls.index('zero'))
        self.assertTrue(all(not t.done() for t in readers))
        self.assertIsNone(rt.stop_failure)

    async def test_odometry_loss_latches_stop_failure(self):
        robot, rt, controller, _, _ = await setup_runtime(self, execute=True)
        controller.handle_response(response(rt))
        await wait_for(lambda: robot.active > 0)
        robot.odometry_on = False
        state = await controller.active_task
        self.assertEqual(state.status, 'FAILED')
        self.assertIn('Stop could not be confirmed', state.latest_error)
        self.assertIn('zero', robot.calls)
        self.assertEqual(robot.active, 0)
        self.assertEqual(controller.handle_response(response(rt, decision='again')).status, 'FAILED')

    async def test_stop_command_failure_blocks_next_action(self):
        robot, rt, controller, _, _ = await setup_runtime(self, execute=True)
        def bad_stop(*args):
            raise RuntimeError('stop link failed')
        robot.base_vel = bad_stop
        controller.handle_response(response(rt))
        await wait_for(lambda: robot.active > 0)
        await controller.cancel_active()
        self.assertEqual(controller.execution_state.status, 'FAILED')
        self.assertIn('stop link failed', controller.execution_state.latest_error)
        self.assertEqual(robot.active, 0)
        self.assertEqual(controller.handle_response(response(rt, decision='again')).status, 'FAILED')

    async def test_distance_correction_and_outside_tolerance_propagate(self):
        robot, rt, controller, _, _ = await setup_runtime(self, execute=True)
        # Deliberately undertravel; each arc retains its fixed target.
        original = robot.move_and_rotate_base
        def short(distance, angle, **kw):
            return original(distance*.4, angle*.4, **kw)
        robot.move_and_rotate_base = short
        controller.handle_response(response(rt))
        state = await controller.active_task
        self.assertEqual(state.status, 'COMPLETED')
        self.assertEqual(robot.arcs, 2)
        self.assertLessEqual(robot.calls.count('rotate'), 2)
        self.assertEqual(state.result.approach.status, 'OUTSIDE_TOLERANCE')
        self.assertTrue(state.result.approach.live_position_verified)

    async def test_reusable_action_refuses_overlapping_lease(self):
        robot, rt, controller, _, _ = await setup_runtime(self, execute=True)
        controller.handle_response(response(rt))
        await wait_for(lambda: rt.action_active)
        with self.assertRaisesRegex(RuntimeError, 'active'):
            await approach_human(rt, 500)
        await controller.cancel_active()

    def test_speed_and_geometry_limits(self):
        self.assertEqual(arc_plan(2, 0, .8, .1)['speed'], .1)
        self.assertLessEqual(arc_plan(1, 1, .6, .9)['speed'], .25)
        for values in [(float('nan'), 0, .7), (2, 0, .5), (2, 0, 2), (6, 0, .7)]:
            with self.assertRaises(ValueError):
                arc_plan(*values)
