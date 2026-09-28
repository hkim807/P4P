"""Real HTTP tests from SDK-shaped data through the receiver to JSONL."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from app.server import create_app
from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.main import collect_and_stream, parse_args
from robot.navel_client.transport import ObservationTransport
from tests.fixtures import frame, locomotion, perception, person


class NavelServerEndToEndTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.output = Path(self.directory.name) / "observations.jsonl"
        self.app = create_app(self.output)
        self.received = threading.Event()

        @self.app.after_request
        def signal_received(response):
            if response.status_code == 200:
                self.received.set()
            return response

        self.server = make_server("127.0.0.1", 0, self.app)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.transport = ObservationTransport(f"http://127.0.0.1:{self.server.server_port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.directory.cleanup()

    async def test_sdk_mapping_round_trips_via_real_http(self):
        payload = NavelObservationAdapter().convert(perception(person()), locomotion())
        response = await asyncio.to_thread(self.transport.send, payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.payload["accepted"])
        self.assertEqual(json.loads(self.output.read_text()), payload)

    async def test_http_validation_error_is_returned_to_client(self):
        response = await asyncio.to_thread(self.transport.send, {"timestamp": 1})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.payload["accepted"])
        self.assertFalse(self.output.exists())

    async def test_concurrent_collectors_stream_to_computer_receiver(self):
        ready = asyncio.Event()
        calls = []

        class Robot:
            async def next_locomotion(self, timeout):
                calls.append("next_locomotion")
                if not ready.is_set():
                    ready.set()
                    return locomotion()
                await asyncio.Event().wait()

            async def next_frame(self, timeout):
                calls.append("next_frame")
                await ready.wait()
                if calls.count("next_frame") == 1:
                    return perception(person())
                await asyncio.Event().wait()

        args = parse_args(["--server", self.transport.server_url, "--minimum-send-interval", "0"])
        task = asyncio.create_task(collect_and_stream(Robot(), args))
        try:
            self.assertTrue(await asyncio.to_thread(self.received.wait, 2))
            payload = json.loads(self.output.read_text())
            self.assertEqual(payload["people"][0]["uid"], 17)
            self.assertEqual(payload["robot"]["linear_velocity"], 0.2)
            self.assertEqual(payload["safety"]["sonar"], [0.5, 0.7, 1.2])
            self.assertEqual(set(calls), {"next_frame", "next_locomotion"})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
