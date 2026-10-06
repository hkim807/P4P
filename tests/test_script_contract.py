"""The documented script helper rejects a mismatched action before hardware use."""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from robot.navel_client.script_contract import read_request, report_success


class ScriptContractTests(unittest.TestCase):
    def test_expected_action_and_response(self):
        request = {"interface_version": "navel-action-script-v1", "reason": "EXECUTE",
                   "observation": {"timestamp": 1},
                   "command": {"action": "APPROACH", "command_id": "one",
                               "lock_id": "lock", "target_uid": 17,
                               "target_track_epoch": 1}}
        with patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")):
            self.assertEqual(read_request(expected_action="APPROACH"), request)
        with patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")):
            with self.assertRaisesRegex(ValueError, "action command mismatch"):
                read_request(expected_action="ENGAGE")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            report_success()
        self.assertEqual(json.loads(output.getvalue()), {"ok": True})
