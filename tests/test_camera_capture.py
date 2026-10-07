"""Camera image streaming and graceful fallback when SDK support is absent."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from werkzeug.serving import make_server

from app.server import create_app
from robot.navel_client.camera_capture import CameraCapture
from robot.navel_client.transport import ObservationTransport


class RGB:
    shape = (1, 2, 3)
    dtype = "uint8"

    def tobytes(self):
        return bytes((255, 0, 0, 0, 255, 0))


class WorkingCamera:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get_frame(self, timeout_ms):
        return NS(width=2, height=1, timestamp_us=123456, data=RGB())


class UnsupportedCamera(WorkingCamera):
    def get_frame(self, timeout_ms):
        raise NotImplementedError("camera socket is not available")


class CameraCaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.camera_dir = Path(self.directory.name) / "cameras"
        self.app = create_app(Path(self.directory.name) / "observations.jsonl",
                              camera_output_dir=self.camera_dir)
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

    async def _capture(self, types):
        capture = CameraCapture(self.transport)
        sender = asyncio.create_task(capture.send())
        collectors = [asyncio.create_task(capture.collect(name, camera_type, 1.0))
                      for name, camera_type in types.items()]
        try:
            for _ in range(100):
                if sum(capture.sequences.values()) >= len(types):
                    break
                await asyncio.sleep(0.01)
            self.assertGreaterEqual(sum(capture.sequences.values()), len(types))
            for task in collectors:
                task.cancel()
            await asyncio.gather(*collectors, return_exceptions=True)
            await asyncio.wait_for(capture.queue.join(), 2)
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        return [json.loads(line) for line in (self.camera_dir / "frames.jsonl").read_text().splitlines()]

    async def test_streams_rgb_frame_to_ppm_and_jsonl_index(self):
        rows = await self._capture({"head": WorkingCamera})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["camera"], "head")
        self.assertEqual(rows[0]["event"], "frame")
        self.assertEqual(rows[0]["timestamp_us"], 123456)
        self.assertEqual(rows[0]["session_id"] != "", True)
        self.assertEqual((self.camera_dir / rows[0]["file"]).read_bytes(),
                         b"P6\n2 1\n255\n" + bytes((255, 0, 0, 0, 255, 0)))
        self.assertFalse((Path(self.directory.name) / "observations.jsonl").exists())

    async def test_missing_and_unimplemented_camera_are_reported_separately(self):
        rows = await self._capture({"head": None, "chest": UnsupportedCamera})
        self.assertEqual({row["camera"] for row in rows}, {"head", "chest"})
        self.assertTrue(all(row["event"] == "unavailable" for row in rows))
        self.assertTrue(any("absent" in row["reason"] for row in rows))
        self.assertTrue(any("NotImplementedError" in row["reason"] for row in rows))
        self.assertTrue(all(row["file"] is None for row in rows))

    async def test_identical_retry_and_invalid_dimensions(self):
        capture = CameraCapture(self.transport)
        record = {
            "capture_version": 1, "session_id": capture.session_id,
            "camera": "head", "sequence": 1, "event": "frame",
            "received_monotonic_us": 1, "received_unix_us": 2,
            "timestamp_us": 3, "width": 2, "height": 1,
            "rgb_b64": "/wAAAP8A",
        }
        first = await asyncio.to_thread(self.transport.send_camera_frame, record)
        self.assertEqual(first.status_code, 200)
        repeat = await asyncio.to_thread(self.transport.send_camera_frame, record)
        self.assertTrue(repeat.payload["duplicate"])
        bad = await asyncio.to_thread(self.transport.send_camera_frame,
                                      {**record, "sequence": 2, "width": 3})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(len((self.camera_dir / "frames.jsonl").read_text().splitlines()), 1)
