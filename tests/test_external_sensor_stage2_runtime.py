"""Deterministic worker gates prove both pipeline boundaries remain independent."""

import asyncio
import io
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from robot.external_sensor_client.config import parse_args
from robot.external_sensor_client.depth import DepthResult
from robot.external_sensor_client.main import Counters, LatestQueue, LiveStatus, run
from robot.external_sensor_client.perception import PerceivedFrame, TrackedPerson
from robot.navel_client.transport import ObservationResponse
from test_external_sensor_runtime import FakeCapture, frame


class Stage2RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def wait_event(self, event):
        self.assertTrue(await asyncio.to_thread(event.wait, 2), "worker gate timed out")

    def test_capacity_one_latest_slots(self):
        queue, stop = LatestQueue(), threading.Event()
        self.assertEqual(queue.maxsize, 1)
        self.assertFalse(queue.replace("old"))
        self.assertTrue(queue.replace("new"))
        self.assertEqual(queue.take(stop), "new")
        stop.set()
        self.assertIsNone(queue.take(stop))

    async def test_capture_inference_http_independence_single_owners_and_counters(self):
        capture = FakeCapture()
        stats = Counters()
        infer_entered, infer_release = threading.Event(), threading.Event()
        processed400, processed500 = threading.Event(), threading.Event()
        http_entered, http_release, http_second = threading.Event(), threading.Event(), threading.Event()
        owner_ids, model_ids, sent = [], [], []
        closed = threading.Event()
        active_http = maximum_http = 0

        class Processor:
            def __init__(self, *args):
                owner_ids.append(threading.get_ident())

            def process(self, captured):
                model_ids.append(threading.get_ident())
                if captured.timestamp_us == 100:
                    infer_entered.set()
                    assert infer_release.wait(3)
                if captured.timestamp_us == 400:
                    assert http_entered.wait(3)
                    processed400.set()
                if captured.timestamp_us == 500:
                    processed500.set()
                humans = tuple(TrackedPerson(f"d435:fake:{i}", (0, 0, 10, 20), 0.8, DepthResult()) for i in (1, 2))
                return PerceivedFrame(captured, humans, inference_ms=10,
                                      detections_without_track_id=1, humans_without_valid_depth=2)

            def close(self):
                owner_ids.append(threading.get_ident())
                closed.set()

        def send(payload, **kwargs):
            nonlocal active_http, maximum_http
            active_http += 1
            maximum_http = max(maximum_http, active_http)
            sent.append(payload)
            if len(sent) == 1:
                http_entered.set()
                assert http_release.wait(3)
            else:
                http_second.set()
            active_http -= 1
            return ObservationResponse(200, {"accepted": True, "decision_triggered": True,
                                             "behavior_intent": {"action": "MONITOR"}})

        args = parse_args(["--stationary-rig", "--camera-height-m", "1.2", "--minimum-send-interval", "0"])
        capture.frames.put(frame(100))
        with redirect_stdout(io.StringIO()), patch("robot.external_sensor_client.main.Counters", return_value=stats):
            task = asyncio.create_task(run(
                args, capture_factory=lambda serial: capture, perception_factory=Processor,
                transport_factory=lambda *a, **kw: Mock(send=send),
                display_factory=Mock(side_effect=AssertionError("GUI forbidden")),
            ))
            try:
                await self.wait_event(infer_entered)
                for timestamp in (200, 300, 400):
                    capture.frames.put(frame(timestamp))
                await self.wait_event(capture.fourth_read)
                # Synchronize to a worker callback after it replaced frame 400.
                while stats.frames_captured < 4:
                    await asyncio.sleep(0)
                self.assertEqual(stats.frames_processed, 0)
                self.assertEqual(stats.queued_frames_replaced, 2)
                infer_release.set()
                await self.wait_event(http_entered)
                await self.wait_event(processed400)
                capture.frames.put(frame(500))
                await self.wait_event(processed500)
                while stats.frames_processed < 3:
                    await asyncio.sleep(0)
                self.assertEqual(len(sent), 1)
                self.assertEqual(stats.perception_frames_replaced, 1)
                self.assertEqual(stats.frames_processed, 3)
                self.assertEqual(stats.tracked_humans, 2)
                self.assertEqual(stats.detections_without_track_id, 3)
                self.assertEqual(stats.humans_without_valid_depth, 6)
                http_release.set()
                await self.wait_event(http_second)
                self.assertEqual([p["timestamp_us"] for p in sent], [100, 500])
                self.assertEqual(maximum_http, 1)
                self.assertEqual(len(set(model_ids)), 1)
                self.assertNotEqual(model_ids[0], capture.thread)
                self.assertNotEqual(model_ids[0], threading.get_ident())
            finally:
                infer_release.set()
                http_release.set()
                task.cancel()
                await asyncio.sleep(0)
                capture.frames.put(frame(600))
                results = await asyncio.gather(task, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertTrue(capture.closed)
        self.assertTrue(closed.is_set())
        self.assertEqual(owner_ids, [model_ids[0], model_ids[0]])

    def test_latest_server_status_retains_action_between_decisions(self):
        status = LiveStatus()
        status.update(action="MONITOR", transport_error="offline")
        self.assertEqual(status.snapshot()[1:], ("MONITOR", "offline"))
        status.update(transport_error=None)
        self.assertEqual(status.snapshot()[1:], ("MONITOR", None))

    async def test_cancellation_waits_for_pending_inference_and_closes_owner_resources(self):
        capture = FakeCapture()
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        owners = []

        class Processor:
            def __init__(self, *args):
                owners.append(threading.get_ident())

            def process(self, captured):
                entered.set()
                assert release.wait(3)
                return PerceivedFrame(captured)

            def close(self):
                owners.append(threading.get_ident())
                closed.set()

        args = parse_args(["--stationary-rig", "--camera-height-m", "1.2"])
        capture.frames.put(frame(100))
        with redirect_stdout(io.StringIO()):
            task = asyncio.create_task(run(args, capture_factory=lambda serial: capture,
                                          perception_factory=Processor, transport_factory=Mock()))
            try:
                await self.wait_event(entered)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertFalse(closed.is_set())
            finally:
                release.set()
                capture.frames.put(frame(200))
                result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertTrue(capture.closed)
        self.assertTrue(closed.is_set())
        self.assertEqual(owners[0], owners[1])
