"""Integration tests for the Navel client's server-response call site."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path

# The production entry point owns the read-only SDK import. A development-machine
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

    def test_behavior_package_has_no_sdk_or_waiting_dependency(self):
        behavior_dir = (
            Path(__file__).resolve().parents[1]
            / "robot"
            / "navel_client"
            / "behavior"
        )
        sources = "\n".join(
            path.read_text(encoding="utf-8") for path in behavior_dir.rglob("*.py")
        )
        self.assertNotIn("import navel", sources)
        self.assertNotIn("time.sleep", sources)
        self.assertNotIn("asyncio.sleep", sources)


if __name__ == "__main__":
    unittest.main()
