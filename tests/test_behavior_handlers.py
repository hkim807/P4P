"""The sole real handler uses the shared runtime and preserves results."""
import unittest
from unittest.mock import AsyncMock, patch
from robot.navel_client.behavior.actions.approach_human import ApproachResult
from robot.navel_client.behavior.commands import ApproachCommand
from robot.navel_client.behavior.handlers import ApproachHandler
from robot.navel_client.navel_runtime import Config, Runtime


class BehaviorHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_parameters_and_every_outcome(self):
        rt = Runtime(None, Config(execute=True))
        handler = ApproachHandler(rt)
        for status in ('APPROACHED_VERIFIED', 'APPROACHED_UNVERIFIED', 'OUTSIDE_TOLERANCE'):
            outcome = ApproachResult(status, .8, 2., status != 'APPROACHED_UNVERIFIED')
            async def action(runtime, uid, **kw):
                self.assertIs(runtime, rt)
                self.assertEqual(uid, 17)
                self.assertEqual(runtime.cfg.stop_distance, .8)
                self.assertEqual(runtime.cfg.approach_speed, .12)
                runtime.target = {'uid': 18}
                return outcome
            with patch('robot.navel_client.behavior.handlers.approach_human.approach_human', side_effect=action):
                result = await handler.execute(ApproachCommand('17', .8, .12), decision_id='d')
            self.assertIs(result.approach, outcome)
            self.assertEqual(result.resolved_target, '18')
            self.assertEqual(result.decision_id, 'd')
            self.assertEqual(rt.cfg.stop_distance, .7)
