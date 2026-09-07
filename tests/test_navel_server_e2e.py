"""Real-HTTP offline test for the complete Navel-to-intent server boundary."""

from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

from werkzeug.serving import make_server

from app.server import create_app
from robot.navel_client.adapter import NavelAdapterConfig, NavelObservationAdapter
from robot.navel_client.behavior import BehaviorController, BehaviorHandlingStatus
from robot.navel_client.transport import ObservationTransport


class StructuredFakeLLM:
    provider = "ollama"
    model = "test-model"
    endpoint = "http://127.0.0.1:11434/v1"

    def __init__(self) -> None:
        self.response_schema = None

    def health(self):
        return {
            "reachable": True,
            "model_available": True,
            "available_models": [self.model],
        }

    def generate(
        self,
        message,
        *,
        system_prompt=None,
        temperature=0.2,
        response_schema=None,
    ):
        self.response_schema = response_schema
        return json.dumps(
            {
                "action": "ORIENT",
                "target_human_id": "17",
                "preferences": {"orientation_target_rad": 0.0},
                "valid_for_ms": 1_000,
                "reason_codes": ["HUMAN_DETECTED"],
                "decision_confidence": 0.75,
            }
        )


def navel_perception():
    person = NS(
        uid=17,
        dist_mm=1_850.0,
        face=NS(x1=10, y1=20, x2=110, y2=220),
        head_position=NS(x=0.0, y=0.0, z=0.1),
        gaze=NS(x=1.0, y=0.0, z=0.0),
        gaze_overlap=0.75,
        facial_expression=NS(
            neutral=0.8,
            happy=0.2,
            sad=0.0,
            surprise=0.0,
            anger=0.0,
        ),
        g_head_position=[],
    )
    return NS(persons=[person])


def navel_locomotion():
    return NS(odometry=NS(velocity=NS(x=0.0, y=0.0, r=0.0)))


class NavelServerEndToEndTests(unittest.TestCase):
    def test_navel_shaped_input_returns_behavior_intent_over_http(self):
        llm = StructuredFakeLLM()
        server = make_server("127.0.0.1", 0, create_app(llm=llm), threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2.0)
        self.addCleanup(server.shutdown)

        adapter = NavelObservationAdapter(
            NavelAdapterConfig(
                adapter_id="navel-e2e-test",
                robot_task="IDLE",
                controller_status="STOPPED",
            ),
            monotonic_ns=lambda: 1_000_000_000,
        )
        observation = adapter.convert(navel_perception(), navel_locomotion())
        transport = ObservationTransport(
            f"http://127.0.0.1:{server.server_port}", timeout_seconds=2.0
        )

        response = transport.send(observation)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.payload["accepted"])
        self.assertTrue(response.payload["decision_triggered"])
        self.assertIn("HUMAN_DETECTED", response.payload["triggers"])
        intent = response.payload["behavior_intent"]
        self.assertEqual(intent["action"], "ORIENT")
        self.assertEqual(intent["target_human_id"], "17")
        self.assertEqual(intent["observation_id"], observation["observation_id"])
        self.assertEqual(
            llm.response_schema["properties"]["target_human_id"]["anyOf"][0][
                "enum"
            ],
            ["17"],
        )
        with self.assertLogs("robot.navel_client.behavior", "INFO") as logs:
            robot = Mock()
            handled = BehaviorController(robot).handle_response(response.payload)
        self.assertEqual(handled.status, BehaviorHandlingStatus.HANDLED)
        self.assertEqual(handled.execution.action.value, "ORIENT")
        self.assertIn("action=ORIENT dry_run=true", logs.output[-1])
        self.assertEqual(robot.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
