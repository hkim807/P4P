"""Diagnostic SDK capture, validation, and real HTTP recording."""

import asyncio
import json
import tempfile
import threading
import unittest
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace as NS

from werkzeug.serving import make_server

from app.server import create_app
from robot.navel_client.main import collect_sdk_only, parse_args
from robot.navel_client.sdk_capture import SdkCapture, sdk_json
from robot.navel_client.transport import ObservationTransport
from tests.fixtures import locomotion, perception, person


class Sensor(IntEnum):
    LIDAR = 1


@dataclass
class Data:
    field: object


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "sdk.jsonl"
        self.camera_dir = Path(self.directory.name) / "cameras"
        self.app = create_app(Path(self.directory.name) / "observations.jsonl",
                              sdk_output=self.path, camera_output_dir=self.camera_dir)
        self.server = make_server("127.0.0.1", 0, self.app)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.transport = ObservationTransport(f"http://127.0.0.1:{self.server.server_port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def test_sdk_serializer_retains_nested_data_and_enum_names(self):
        self.assertEqual(sdk_json(Data({Sensor.LIDAR: [NS(x=1.0, y=float("nan"))]})),
                         {"field": {"LIDAR": [{"x": 1.0, "y": "NaN"}]}})

    def test_queue_overflow_is_visible(self):
        capture = SdkCapture(self.transport, queue_size=1)
        capture.record("perception", perception())
        with self.assertRaisesRegex(RuntimeError, "queue full"):
            capture.record("locomotion", locomotion())
        self.assertEqual(capture.sequences, {"perception": 1, "locomotion": 0})

    def test_receiver_refuses_existing_sdk_output(self):
        self.path.write_text("prior run\n")
        with self.assertRaises(FileExistsError):
            create_app(Path(self.directory.name) / "new-observations.jsonl",
                       sdk_output=self.path)
        self.assertEqual(self.path.read_text(), "prior run\n")

    async def test_real_http_capture_of_both_streams(self):
        capture = SdkCapture(self.transport)
        incoming = perception(person(uid=None, id_score=0.4,
                                     face=NS(x1=1, y1=2, x2=3, y2=4),
                                     g_gaze=[NS(sys=Sensor.LIDAR, x=0.1, y=0.2, z=0.3)]))
        incoming.time = 1234
        moving = locomotion(odometry=NS(time=5678, position=NS(x=1.5, y=2.5),
                                         velocity=NS(linear_x=0.2, angular_z=0.1)))
        capture.record("perception", incoming)
        capture.record("locomotion", moving)
        task = asyncio.create_task(capture.send())
        try:
            await asyncio.wait_for(capture.queue.join(), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        rows = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual([row["stream"] for row in rows], ["perception", "locomotion"])
        self.assertEqual(rows[0]["packet"]["time"], 1234)
        self.assertIsNone(rows[0]["packet"]["persons"][0]["uid"])
        self.assertEqual(rows[0]["packet"]["persons"][0]["face"],
                         {"x1": 1, "y1": 2, "x2": 3, "y2": 4})
        self.assertEqual(rows[1]["packet"]["odometry"]["position"], {"x": 1.5, "y": 2.5})
        self.assertEqual(rows[1]["packet"]["distances"]["SONAR"], [0.5, 0.7, 1.2])
        self.assertGreater(rows[0]["received_unix_us"], 0)
        self.assertGreater(rows[0]["received_monotonic_us"], 0)

        repeat = await asyncio.to_thread(self.transport.send_sdk_packet, rows[0])
        self.assertTrue(repeat.payload["duplicate"])
        self.assertEqual(len(self.path.read_text().splitlines()), 2)
        bad = await asyncio.to_thread(self.transport.send_sdk_packet,
                                      {**rows[0], "sequence": 3})
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(len(self.path.read_text().splitlines()), 2)

    async def test_capture_only_never_runs_observation_pipeline(self):
        received = threading.Event()

        @self.app.after_request
        def signal(response):
            if response.status_code == 200:
                received.set()
            return response

        class Robot:
            def __init__(self):
                self.frame_sent = False
                self.motion_sent = False

            async def next_frame(self, timeout):
                if not self.frame_sent:
                    self.frame_sent = True
                    return perception(person())
                await asyncio.Event().wait()

            async def next_locomotion(self, timeout):
                if not self.motion_sent:
                    self.motion_sent = True
                    return locomotion()
                await asyncio.Event().wait()

        args = parse_args(["--server", self.transport.server_url, "--sdk-capture-only"])
        task = asyncio.create_task(collect_sdk_only(Robot(), args))
        try:
            self.assertTrue(await asyncio.to_thread(received.wait, 2))
            for _ in range(40):
                if self.path.exists() and len(self.path.read_text().splitlines()) == 2:
                    break
                await asyncio.sleep(0.025)
            self.assertEqual(len(self.path.read_text().splitlines()), 2)
            self.assertFalse((Path(self.directory.name) / "observations.jsonl").exists())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_unsupported_cameras_do_not_stop_sdk_packets(self):
        class Robot:
            async def next_frame(self, timeout):
                if not hasattr(self, "frame_sent"):
                    self.frame_sent = True
                    return perception(person())
                await asyncio.Event().wait()

            async def next_locomotion(self, timeout):
                if not hasattr(self, "motion_sent"):
                    self.motion_sent = True
                    return locomotion()
                await asyncio.Event().wait()

        args = parse_args(["--server", self.transport.server_url,
                           "--sdk-capture-only", "--camera-capture"])
        task = asyncio.create_task(collect_sdk_only(
            Robot(), args, {"head": None, "chest": None}))
        try:
            for _ in range(100):
                sdk_count = len(self.path.read_text().splitlines()) if self.path.exists() else 0
                manifest = self.camera_dir / "frames.jsonl"
                camera_count = len(manifest.read_text().splitlines()) if manifest.exists() else 0
                if sdk_count == 2 and camera_count == 2:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(sdk_count, 2)
            self.assertEqual(camera_count, 2)
            events = [json.loads(line) for line in manifest.read_text().splitlines()]
            self.assertEqual({row["event"] for row in events}, {"unavailable"})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
