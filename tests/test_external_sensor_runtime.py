"""Latest-frame scheduling and transport integration with fake capture only."""

import asyncio
import io
import json
import queue as thread_queue
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from app.server import create_app
from robot.external_sensor_client.adapter import ExternalObservationAdapter
from robot.external_sensor_client.capture import CaptureError, CapturedFrame, DeviceInfo, StreamInfo
from robot.external_sensor_client.config import parse_args
from robot.external_sensor_client.perception import PerceivedFrame
from robot.external_sensor_client.main import Counters, replace_queued, run, send_observations
from robot.navel_client.transport import ObservationResponse, ObservationTransport, TransportError


def frame(timestamp):
    return CapturedFrame(timestamp, object(), object(), timestamp, 0.5)


class FakeCapture:
    def __init__(self):
        self.frames = thread_queue.Queue()
        self.device_info = DeviceInfo("Mock D435", "123", StreamInfo(640, 480, 30), StreamInfo(640, 480, 30))
        self.closed = False
        self.thread = None
        self.fourth_read = threading.Event()

    def __enter__(self):
        self.thread = threading.get_ident()
        return self

    def read(self):
        assert threading.get_ident() == self.thread
        item = self.frames.get(timeout=3)
        if isinstance(item, Exception):
            raise item
        if item.timestamp_us == 400:
            self.fourth_read.set()
        return item

    def __exit__(self, *_):
        assert threading.get_ident() == self.thread
        self.closed = True


class EmptyPerception:
    def __init__(self, *args):
        pass

    def process(self, frame):
        return PerceivedFrame(frame)

    def close(self):
        pass


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_frame_is_not_sent(self):
        queue = asyncio.Queue(maxsize=1)
        counters = Counters()
        transport = Mock()
        replace_queued(queue, frame(float("nan")), counters)
        with self.assertRaises(ValueError):
            await send_observations(
                queue, ExternalObservationAdapter("rig", stationary_rig=True),
                transport, counters, minimum_send_interval=0,
                force_decision=False, print_raw_json=False,
            )
        transport.send.assert_not_called()
        self.assertEqual(counters.observations_generated, 0)
        self.assertEqual(counters.observations_sent, 0)

    def test_latest_queue_replaces_old_frame_and_counts(self):
        queue = asyncio.Queue(maxsize=1)
        counters = Counters()
        for timestamp in (100, 200, 300):
            replace_queued(queue, frame(timestamp), counters)
        self.assertEqual(queue.maxsize, 1)
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual(queue.get_nowait().timestamp_us, 300)
        queue.task_done()
        self.assertEqual(counters.frames_captured, 3)
        self.assertEqual(counters.queued_frames_replaced, 2)
        with self.assertRaises(ValueError):
            replace_queued(asyncio.Queue(maxsize=2), frame(400), counters)

    async def test_capture_continues_during_http_and_only_one_request_runs(self):
        capture = FakeCapture()
        counters = Counters()
        started, release, second = threading.Event(), threading.Event(), threading.Event()
        calls = []
        active = maximum_active = 0

        def send(payload, *, force_decision=False):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            calls.append((payload, force_decision))
            if len(calls) == 1:
                started.set()
                if not release.wait(3):
                    raise AssertionError("test HTTP gate timed out")
            else:
                second.set()
            active -= 1
            return ObservationResponse(200, {"accepted": True, "decision_triggered": False, "behavior_intent": None})

        transport = Mock(send=send)
        args = parse_args(["--stationary-rig", "--camera-height-m", "1.2", "--force-decision", "--minimum-send-interval", "0"])
        capture.frames.put(frame(100))
        with redirect_stdout(io.StringIO()), patch("robot.external_sensor_client.main.Counters", return_value=counters):
            task = asyncio.create_task(run(args, capture_factory=lambda serial: capture, transport_factory=lambda *a, **kw: transport, perception_factory=EmptyPerception))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                for timestamp in (200, 300, 400):
                    capture.frames.put(frame(timestamp))
                self.assertTrue(await asyncio.to_thread(capture.fourth_read.wait, 2))
                await asyncio.sleep(0)  # Deliver the worker's queued callbacks.
                self.assertEqual(len(calls), 1)
                self.assertEqual(counters.frames_captured, 4)
                self.assertEqual(counters.queued_frames_replaced + counters.perception_frames_replaced, 2)
                release.set()
                self.assertTrue(await asyncio.to_thread(second.wait, 2))
                self.assertEqual(calls[1][0]["timestamp_us"], 400)
                self.assertTrue(all(force for _, force in calls))
                self.assertEqual(maximum_active, 1)
                self.assertEqual(counters.observations_generated, 2)
                self.assertEqual(counters.observations_sent, 2)
                self.assertEqual(counters.transport_failures, 0)
            finally:
                release.set()
                task.cancel()
                await asyncio.sleep(0)  # Let run signal its capture worker.
                capture.frames.put(frame(500))  # Finish the pending SDK read.
                result = await asyncio.gather(task, return_exceptions=True)
                self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertTrue(capture.closed)
        self.assertNotEqual(capture.thread, threading.get_ident())

    async def test_transport_failure_discards_and_next_observation_is_sent(self):
        queue = asyncio.Queue(maxsize=1)
        counters = Counters()
        loop = asyncio.get_running_loop()
        completed = asyncio.Event()
        calls = []

        def send(payload, *, force_decision=False):
            calls.append(payload)
            if len(calls) == 1:
                loop.call_soon_threadsafe(replace_queued, queue, frame(200), counters)
                raise TransportError("server unavailable")
            loop.call_soon_threadsafe(completed.set)
            return ObservationResponse(200, {"accepted": True, "decision_triggered": False})

        replace_queued(queue, frame(100), counters)
        output = io.StringIO()
        with redirect_stdout(output):
            task = asyncio.create_task(send_observations(queue, ExternalObservationAdapter("rig", stationary_rig=True), Mock(send=send), counters,
                                                        minimum_send_interval=0, force_decision=False, print_raw_json=False))
            try:
                await asyncio.wait_for(completed.wait(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual([p["timestamp_us"] for p in calls], [100, 200])
        self.assertEqual(counters.transport_failures, 1)
        self.assertEqual(counters.observations_sent, 2)
        self.assertEqual(counters.observations_generated, 2)
        self.assertIn("server unavailable; discarded", output.getvalue())

    async def test_minimum_interval_keeps_latest_frame(self):
        queue = asyncio.Queue(maxsize=1)
        counters = Counters()
        loop = asyncio.get_running_loop()
        completed = asyncio.Event()
        calls = []

        def send(payload, **kwargs):
            calls.append((loop.time(), payload["timestamp_us"]))
            if len(calls) == 1:
                loop.call_soon_threadsafe(replace_queued, queue, frame(200), counters)
                loop.call_soon_threadsafe(replace_queued, queue, frame(300), counters)
            else:
                loop.call_soon_threadsafe(completed.set)
            return ObservationResponse(200, {})

        replace_queued(queue, frame(100), counters)
        with redirect_stdout(io.StringIO()):
            task = asyncio.create_task(send_observations(queue, ExternalObservationAdapter("rig", stationary_rig=True), Mock(send=send), counters,
                                                        minimum_send_interval=0.05, force_decision=False, print_raw_json=False))
            try:
                await asyncio.wait_for(completed.wait(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual([timestamp for _, timestamp in calls], [100, 300])
        self.assertGreaterEqual(calls[1][0] - calls[0][0], 0.05)

    async def test_camera_mode_is_finite_and_never_creates_transport_or_observation(self):
        capture = FakeCapture()
        for timestamp in (100, 200):
            capture.frames.put(frame(timestamp))
        transport = Mock(side_effect=AssertionError("POST forbidden"))
        with redirect_stdout(io.StringIO()) as output, patch("robot.external_sensor_client.main.ExternalObservationAdapter", side_effect=AssertionError("observation forbidden")):
            await run(parse_args(["--camera-test", "--camera-test-frames", "2"]), capture_factory=lambda serial: capture, transport_factory=transport)
        transport.assert_not_called()
        self.assertTrue(capture.closed)
        self.assertIn("frames_captured=2", output.getvalue())
        self.assertIn("depth_valid_sample_ratio=0.500", output.getvalue())

    async def test_capture_failure_stops_runtime_and_closes(self):
        capture = FakeCapture()
        capture.frames.put(CaptureError("invalid frames"))
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(CaptureError, "invalid frames"):
            await run(parse_args(["--stationary-rig", "--camera-height-m", "1.2"]), capture_factory=lambda serial: capture, transport_factory=Mock(), perception_factory=EmptyPerception)
        self.assertTrue(capture.closed)

    async def test_camera_mode_cancellation_closes_after_pending_read(self):
        capture = FakeCapture()
        entered = threading.Event()
        original_read = capture.read

        def read():
            entered.set()
            return original_read()

        capture.read = read
        with redirect_stdout(io.StringIO()):
            task = asyncio.create_task(run(parse_args(["--camera-test"]), capture_factory=lambda serial: capture))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            finally:
                task.cancel()
                await asyncio.sleep(0)
                capture.frames.put(CaptureError("timeout during shutdown"))
                result = await asyncio.gather(task, return_exceptions=True)
        self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertTrue(capture.closed)


class FakeLLM:
    provider, model, endpoint = "test", "test", "http://unused"

    def __init__(self):
        self.calls = 0

    def generate(self, *args, **kwargs):
        self.calls += 1
        return json.dumps({"action": "MONITOR", "preferences": {}, "valid_for_ms": 1000,
                           "reason_codes": ["INSUFFICIENT_EVIDENCE"], "decision_confidence": 0.55})


class TransportEndpointTests(unittest.TestCase):
    def test_reused_transport_posts_canonical_frame_and_displays_real_endpoint_results(self):
        llm = FakeLLM()
        client = create_app(llm=llm).test_client()
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            result = client.post(request.selector, data=request.data, content_type="application/json")
            response = Mock()
            response.status = result.status_code
            response.read.return_value = result.data
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            return response

        adapter = ExternalObservationAdapter("rig", stationary_rig=True)
        transport = ObservationTransport("http://127.0.0.1:6060")
        with patch("robot.navel_client.transport.urlopen", side_effect=urlopen):
            accepted = transport.send(adapter.convert(frame(100)).model_dump(mode="json"))
            forced = transport.send(adapter.convert(frame(200)).model_dump(mode="json"), force_decision=True)
        self.assertEqual(requests[0].full_url, "http://127.0.0.1:6060/api/v1/observations")
        self.assertEqual(requests[1].full_url, "http://127.0.0.1:6060/api/v1/observations?force_decision=1")
        self.assertTrue(accepted.payload["accepted"])
        self.assertFalse(accepted.payload["decision_triggered"])
        self.assertIsNone(accepted.payload["behavior_intent"])
        self.assertEqual(forced.payload["behavior_intent"]["action"], "MONITOR")
        self.assertEqual(llm.calls, 1)
