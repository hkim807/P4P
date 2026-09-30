"""Shared decoding, clock alignment, freshness and stop confirmation."""
import math
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from navel_test_support import setup_runtime
from robot.navel_client.navel_runtime import Config, Runtime, read_pose, nose_position
from robot.navel_client.adapter import NavelObservationAdapter
from app.domain.models import ObservationFrame


def packet(stamp, *, bug=False, v=.2, w=.3):
    orientation = NS(x=0, y=1, z=0, w=0) if bug else NS(x=0, y=0, z=0, w=1)
    return NS(odometry=NS(time=stamp, position=NS(x=0, y=0), orientation=orientation,
        velocity=NS(linear_x=v, linear_y=w if bug else 0, angular_z=0 if bug else w)))


class RuntimeTests(unittest.TestCase):
    def test_adapter_and_action_share_nose_transform_and_bug_velocity(self):
        rt = Runtime(None, Config(head_x=.1, head_y=-.1, frame_yaw_deg=10))
        rt.ingest_odometry(packet(1_000_000, bug=True))
        person = NS(uid=17, dist_mm=9999, g_head_position=[NS(sys=3, x=99, y=99, z=99)],
                    g_nose=[NS(sys=3, x=2, y=.3, z=.2)])
        frame = NS(time=1_000_000, persons=[person])
        rt.ingest_perception(frame)
        out = NavelObservationAdapter(runtime=rt).convert(frame)
        ObservationFrame.model_validate(out)
        p = nose_position(person, rt.cfg)
        self.assertAlmostEqual(out['humans'][0]['position_robot_m']['x'], p['x'])
        self.assertAlmostEqual(out['humans'][0]['distance_m'], math.hypot(p['x'], p['y']))
        self.assertEqual(out['humans'][0]['position_robot_m']['z'], 0)
        self.assertEqual(out['robot']['linear_velocity_mps'], {'x': .2, 'y': 0})
        self.assertEqual(out['robot']['angular_velocity_radps'], .3)
        self.assertEqual(read_pose(packet(1))['w'], .3)

    def test_repeated_and_out_of_order_packets_do_not_refresh(self):
        rt = Runtime(None)
        rt.ingest_odometry(packet(100))
        pose_at = rt.pose_at
        self.assertFalse(rt.ingest_odometry(packet(100)))
        self.assertFalse(rt.ingest_odometry(packet(99)))
        self.assertEqual(rt.pose_at, pose_at)
        rt.ingest_perception(NS(time=100, persons=[]))
        seq = rt.frame_seq
        self.assertFalse(rt.ingest_perception(NS(time=100, persons=[])))
        self.assertEqual(rt.frame_seq, seq)
        rt.pose_at -= 1
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            rt.pose()

    def test_unsynchronized_positions_and_long_occlusion_are_not_reassociated(self):
        rt = Runtime(None)
        rt.ingest_odometry(packet(1_000_000))
        frame = NS(time=1_200_000, persons=[NS(uid=17, g_nose=[NS(sys=3,x=2,y=0,z=.2)])])
        rt.ingest_perception(frame)
        self.assertEqual(rt.people, [])
        anchor = dict(wx=2, wy=0, seen_at=time.monotonic()-2.1)
        rt.people = [dict(wx=2, wy=0, uid=17)]
        self.assertIsNone(rt.follow(anchor))


class StopTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_cancel_allows_a_new_execution(self):
        from navel_test_support import response, wait_for
        robot, rt, controller, _, _ = await setup_runtime(self, execute=True)
        controller.handle_response(response(rt))
        await wait_for(lambda: robot.active > 0)
        await controller.cancel_active()
        self.assertIsNone(rt.stop_failure)
        self.assertEqual(controller.handle_response(response(rt, decision='new')).status, 'ACCEPTED')
        state = await controller.active_task
        self.assertEqual(state.status, 'COMPLETED')

    async def test_cancellation_during_stop_confirmation_finishes_cleanup(self):
        import asyncio
        from navel_test_support import response, wait_for
        robot, rt, controller, _, _ = await setup_runtime(self, execute=True)
        controller.handle_response(response(rt))
        await wait_for(lambda: 'zero' in robot.calls)
        cancellation = asyncio.create_task(controller.cancel_active())
        await asyncio.sleep(.01)
        incoming = controller.handle_response(response(rt, decision='while-stopping', target='999'))
        self.assertEqual(incoming.status, 'BUSY')
        await cancellation
        self.assertEqual(controller.execution_state.status, 'CANCELLED')
        self.assertIsNone(rt.stop_failure)
        self.assertEqual(robot.active, 0)
        self.assertTrue(any(event == 'BASE_STOPPED' for event, _ in rt.log.events))
