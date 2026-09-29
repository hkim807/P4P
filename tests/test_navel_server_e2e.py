"""Real HTTP tests from SDK-shaped data through the receiver to JSONL."""

import asyncio
import contextlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from app.server import create_app
from app.replay import main as replay_main
from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.main import collect_and_stream, parse_args
from robot.navel_client.transport import ObservationTransport
from tests.fixtures import frame, locomotion, perception, person


class NavelServerEndToEndTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.output = Path(self.directory.name) / "observations.jsonl"
        self.tracking_output = Path(self.directory.name) / "tracks.jsonl"
        self.social_output = Path(self.directory.name) / "social.jsonl"
        self.app = create_app(self.output, tracking_output=self.tracking_output, session_id="http-test",
                              social_output=self.social_output)
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
        self.assertEqual(response.payload["processing_status"], "complete")
        self.assertEqual(json.loads(self.output.read_text()), payload)
        track = json.loads(self.tracking_output.read_text())["tracks"][0]
        self.assertEqual(track["uid"], payload["people"][0]["uid"])
        self.assertEqual(track["samples"][0]["timestamp_us"], payload["timestamp"])
        self.assertEqual(response.payload["social_state"], json.loads(self.social_output.read_text()))
        self.assertEqual(response.payload["social_state"]["people"][0]["gaze_state"], "UNKNOWN")

    async def test_http_validation_error_is_returned_to_client(self):
        response = await asyncio.to_thread(self.transport.send, {"timestamp": 1})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.payload["accepted"])
        self.assertFalse(self.output.exists())

    async def test_http_recording_can_be_replayed_to_a_fresh_receiver(self):
        payloads = [frame(), {**frame(), "timestamp": 1_200_000}]
        for payload in payloads:
            response = await asyncio.to_thread(self.transport.send, payload)
            self.assertEqual(response.status_code, 200)
        destination = self.output.with_name("replayed.jsonl")
        server = make_server("127.0.0.1", 0, create_app(destination))
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                result = await asyncio.to_thread(replay_main, [
                    str(self.output), "--speed", "0", "--server", f"http://127.0.0.1:{server.server_port}",
                ])
            self.assertEqual(result, 0)
            self.assertEqual([json.loads(line) for line in destination.read_text().splitlines()], payloads)
            self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()], payloads)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

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
