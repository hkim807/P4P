"""Integration tests for the Navel client's server-response call site."""

from __future__ import annotations

import asyncio
import ast
import time
from unittest.mock import patch
import sys
import types
import unittest
from pathlib import Path

# The production entry point owns the SDK import. A development-machine
# test supplies only an import stub; no Robot is constructed.
sys.modules.setdefault("navel", types.ModuleType("navel"))

from robot.navel_client.main import _send_observations
from robot.navel_client.transport import ObservationResponse


class StopAfterHandling(RuntimeError):
    pass


class RecordingController:
    def __init__(self):
        self.payloads = []

    def handle_response(self, payload):
        self.payloads.append(payload)
        raise StopAfterHandling


class RecordingTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def send(self, observation, *, force_decision=False):
        self.calls.append((observation, force_decision))
        return ObservationResponse(200, self.payload)


class NavelBehaviorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_loop_passes_each_server_decision_to_controller_once(self):
        response_payload = {
            "accepted": True,
            "decision_triggered": True,
            "triggers": ["HUMAN_DETECTED"],
            "behavior_intent": None,
        }
        transport = RecordingTransport(response_payload)
        controller = RecordingController()
        queue = asyncio.Queue(maxsize=1)
        observation = {"observation_id": "observation-1", "humans": []}
        queue.put_nowait(observation)

        with self.assertRaises(StopAfterHandling):
            await _send_observations(
                queue,
                transport,  # type: ignore[arg-type]
                controller,  # type: ignore[arg-type]
                minimum_send_interval_s=0.0,
                force_decision=False,
                print_only=False,
            )

        self.assertEqual(transport.calls, [(observation, False)])
        self.assertEqual(controller.payloads, [response_payload])

    def test_behavior_package_has_no_sdk_or_blocking_wait_dependency(self):
        behavior_dir = (
            Path(__file__).resolve().parents[1]
            / "robot"
            / "navel_client"
            / "behavior"
        )
        sources = "\n".join(
            path.read_text(encoding="utf-8") for path in behavior_dir.rglob("*.py")
        )
        tree = ast.parse(sources)
        imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        imports += [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertNotIn('navel', imports)
        self.assertNotIn("time.sleep", sources)
        self.assertNotIn("asyncio.run", sources)

    async def test_sending_and_single_readers_continue_during_movement(self):
        from navel_test_support import setup_runtime, wait_for
        robot, rt, controller, queue, readers = await setup_runtime(self, execute=True)
        owners = {'frame': set(), 'odom': set()}
        original_frame, original_odom = robot.next_frame, robot.next_locomotion
        async def frame(timeout):
            owners['frame'].add(asyncio.current_task())
            return await original_frame(timeout)
        async def odom(timeout):
            owners['odom'].add(asyncio.current_task())
            return await original_odom(timeout)
        robot.next_frame, robot.next_locomotion = frame, odom
        class Transport:
            def __init__(self): self.calls = []
            def send(self, observation, **kwargs):
                self.calls.append(observation)
                time.sleep(.025)  # blocking HTTP work runs outside the event loop
                intent = None
                if len(self.calls) == 1:
                    intent = {'schema_version': '1.0', 'decision_id': 'wire-approach',
                        'observation_id': observation['observation_id'], 'social_state_id': 'state-1',
                        'created_at_us': observation['timestamp_us'], 'action': 'APPROACH',
                        'target_human_id': observation['humans'][0]['track_id'],
                        'preferences': {'preferred_social_distance_m': .7}, 'valid_for_ms': 15000,
                        'reason_codes': ['HUMAN_DETECTED']}
                return ObservationResponse(200, {'behavior_intent': intent})
        transport = Transport()
        sender = asyncio.create_task(_send_observations(queue, transport, controller,
            minimum_send_interval_s=0, force_decision=False, print_only=False))
        try:
            await wait_for(lambda: robot.active > 0)
            count, frames = len(transport.calls), rt.frame_seq
            await asyncio.sleep(.3)
            self.assertGreater(len(transport.calls), count+2)
            self.assertGreater(rt.frame_seq, frames+2)
            self.assertTrue(any(o['robot']['task'] == 'APPROACHING' and
                                o['robot']['controller_status'] == 'ACTIVE' for o in transport.calls))
            self.assertEqual(len(owners['frame']), 1)
            self.assertEqual(len(owners['odom']), 1)
            self.assertEqual(controller.execution_state.status, 'RUNNING')
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            await controller.shutdown()
        self.assertEqual(robot.active, 0)
        self.assertTrue(all(not t.done() for t in readers))

    async def test_main_shutdown_finishes_stop_before_closing_connection(self):
        from navel_test_support import SimRobot, wait_for
        from robot.navel_client.main import parse_args, run
        robot = SimRobot()
        signals = {}
        class Connection:
            async def __aenter__(self): return robot
            async def __aexit__(self, *args):
                self_stopped = robot.active == 0 and 'zero' in robot.calls
                if not self_stopped: raise AssertionError('connection closed before stopping')
        class Transport:
            def __init__(self, *args, **kwargs): pass
            def send(self, o, **kwargs):
                return ObservationResponse(200, {'behavior_intent': {
                    'schema_version': '1.0', 'decision_id': 'one',
                    'observation_id': o['observation_id'], 'social_state_id': 'state',
                    'created_at_us': o['timestamp_us'], 'action': 'APPROACH',
                    'target_human_id': o['humans'][0]['track_id'],
                    'preferences': {'preferred_social_distance_m': .7}, 'valid_for_ms': 15000,
                    'reason_codes': ['HUMAN_DETECTED']}})
        loop = asyncio.get_running_loop()
        with patch('robot.navel_client.main.navel.Robot', return_value=Connection(), create=True) as factory, \
             patch('robot.navel_client.main.ObservationTransport', Transport), \
             patch.object(loop, 'add_signal_handler', side_effect=lambda sig, fn: signals.update({sig: fn})), \
             patch.object(loop, 'remove_signal_handler'):
            task = asyncio.create_task(run(parse_args(['--execute'])))
            try:
                await wait_for(lambda: robot.active > 0)
                next(iter(signals.values()))()
                await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            factory.assert_called_once()
        self.assertLess(robot.calls.index('arc_finished'), robot.calls.index('zero'))


if __name__ == "__main__":
    unittest.main()
