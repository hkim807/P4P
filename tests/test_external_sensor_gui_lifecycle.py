"""Main-thread HighGUI ownership with synthetic frames and mocked windows."""

import asyncio
import io
import os
import subprocess
import sys
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

from robot.external_sensor_client.capture import CaptureError, CapturedFrame
from robot.external_sensor_client.config import parse_args
from robot.external_sensor_client.main import run
from robot.external_sensor_client.perception import PerceivedFrame
from robot.external_sensor_client.visual import DisplayError, VisualDisplay
from robot.navel_client.transport import ObservationResponse
from test_external_sensor_runtime import FakeCapture


def rgbd(timestamp=100):
    return CapturedFrame(timestamp,
                         NS(get_data=lambda: np.full((8, 8, 3), timestamp % 256, dtype=np.uint8)),
                         NS(get_data=lambda: np.full((8, 8), 1000, dtype=np.uint16)),
                         timestamp, 1.0, 0.001)


@contextmanager
def highgui(key=-1, fail_show=False):
    calls = []
    cv = Mock(WINDOW_NORMAL=0, COLORMAP_JET=0, FONT_HERSHEY_SIMPLEX=0)
    cv.getBuildInformation.return_value = "GUI: QT5"
    cv.applyColorMap.side_effect = lambda data, mode: np.repeat(data[:, :, None], 3, axis=2)

    def record(name):
        def call(*args):
            calls.append((name, threading.get_ident()))
            if name == "imshow" and fail_show:
                raise RuntimeError("mock render failure")
            if name == "waitKey":
                return key if any(c[0] == "imshow" for c in calls) else -1
        return call

    for name in ("namedWindow", "imshow", "waitKey", "destroyWindow", "destroyAllWindows"):
        getattr(cv, name).side_effect = record(name)
    with patch.dict(os.environ, {"DISPLAY": ":0"}), patch("robot.external_sensor_client.visual.import_module", side_effect=lambda name: cv if name == "cv2" else np), patch("robot.external_sensor_client.visual.subprocess.run", return_value=NS(returncode=0)):
        yield cv, calls


class RepeatingCapture(FakeCapture):
    def read(self):
        assert threading.get_ident() == self.thread
        time.sleep(0.002)
        return rgbd(time.monotonic_ns() // 1000)


class Processor:
    def __init__(self, *args):
        self.owner = threading.get_ident()
        self.closed = False

    def process(self, frame):
        assert threading.get_ident() == self.owner
        return PerceivedFrame(frame, rgb_bgr=np.zeros((8, 8, 3), dtype=np.uint8), depth_m=np.ones((8, 8)))

    def close(self):
        assert threading.get_ident() == self.owner
        self.closed = True


class GuiLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def assert_owned_cleanup(self, calls):
        self.assertTrue(calls)
        self.assertEqual({owner for _, owner in calls}, {threading.main_thread().ident})
        names = [name for name, _ in calls]
        self.assertEqual(names[0], "namedWindow")
        self.assertEqual(names.count("destroyAllWindows"), 1)
        self.assertEqual(names[-2:], ["destroyAllWindows", "waitKey"])

    async def test_camera_test_stops_after_exactly_thirty_reads_and_shows_final_frame(self):
        capture = FakeCapture()
        for timestamp in range(1, 31):
            capture.frames.put(rgbd(timestamp))
        capture.read = Mock(wraps=capture.read)
        with highgui() as (cv, calls), redirect_stdout(io.StringIO()) as output:
            await run(parse_args(["--camera-test", "--display", "--camera-test-frames", "30"]),
                      capture_factory=lambda serial: capture,
                      transport_factory=Mock(side_effect=AssertionError("transport forbidden")),
                      perception_factory=Mock(side_effect=AssertionError("perception forbidden")))
        self.assertEqual(capture.read.call_count, 30)
        self.assertTrue(capture.closed)
        self.assertNotEqual(capture.thread, threading.main_thread().ident)
        self.assertIn("frames_captured=30", output.getvalue())
        cv.imshow.assert_called()
        self.assertEqual(cv.imshow.call_args.args[1][0, 0, 0], 30)
        self.assert_owned_cleanup(calls)

    async def test_q_and_escape_close_camera_and_normal_modes_on_main_thread(self):
        for camera_mode in (True, False):
            for key in (ord("q"), 27):
                capture, processors = RepeatingCapture(), []

                def factory(*args):
                    processor = Processor(*args)
                    processors.append(processor)
                    return processor

                options = ["--camera-test", "--camera-test-frames", "100"] if camera_mode else ["--stationary-rig", "--camera-height-m", "1.2"]
                with self.subTest(camera=camera_mode, key=key), highgui(key) as (cv, calls), redirect_stdout(io.StringIO()):
                    await run(parse_args([*options, "--display"]), capture_factory=lambda serial: capture,
                              perception_factory=factory,
                              transport_factory=lambda *a, **kw: Mock(send=Mock(return_value=ObservationResponse(200, {"accepted": True, "behavior_intent": None}))))
                self.assertTrue(capture.closed)
                self.assert_owned_cleanup(calls)
                if not camera_mode:
                    self.assertTrue(processors[0].closed)
                    self.assertNotEqual(processors[0].owner, threading.main_thread().ident)

    async def test_cancel_closes_gui_before_joining_pending_camera_read(self):
        capture, entered = FakeCapture(), threading.Event()
        original_read = capture.read

        def read():
            entered.set()
            return original_read()

        capture.read = read
        with highgui() as (cv, calls), redirect_stdout(io.StringIO()):
            task = asyncio.create_task(run(parse_args(["--camera-test", "--display"]), capture_factory=lambda serial: capture))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                cv.destroyAllWindows.assert_called_once()
                self.assertFalse(task.done())
            finally:
                capture.frames.put(CaptureError("shutdown timeout"))
                result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertTrue(capture.closed)
        self.assert_owned_cleanup(calls)

    async def test_capture_failure_and_render_failure_cleanup_on_main_thread(self):
        for render_failure in (False, True):
            capture = FakeCapture()
            capture.frames.put(rgbd() if render_failure else CaptureError("mock capture failure"))
            options = ["--camera-test", "--camera-test-frames", "1", "--display"]
            with self.subTest(render=render_failure), highgui(fail_show=render_failure) as (cv, calls), redirect_stdout(io.StringIO()):
                with self.assertRaises(DisplayError if render_failure else CaptureError):
                    await run(parse_args(options), capture_factory=lambda serial: capture)
            self.assertTrue(capture.closed)
            self.assert_owned_cleanup(calls)

    async def test_cross_thread_lifecycle_calls_are_rejected_without_gui_side_effects(self):
        with highgui() as (cv, calls):
            with VisualDisplay() as display:
                before = list(calls)
                for method, args in ((display.__enter__, ()), (display.show, (rgbd(),)),
                                     (display.poll_quit, ()), (display.close, ())):
                    with self.subTest(method=method.__name__), self.assertRaisesRegex(DisplayError, "main OS thread"):
                        await asyncio.to_thread(method, *args)
                self.assertEqual(calls, before)
        self.assert_owned_cleanup(calls)

    def test_interrupt_during_window_creation_cleans_partial_initialization(self):
        with highgui() as (cv, calls):
            def interrupted(*args):
                calls.append(("namedWindow", threading.get_ident()))
                raise KeyboardInterrupt()

            cv.namedWindow.side_effect = interrupted
            with self.assertRaises(KeyboardInterrupt):
                VisualDisplay().__enter__()
        self.assert_owned_cleanup(calls)


class CtrlCEntryPointTests(unittest.TestCase):
    def test_actual_sigint_closes_mock_gui_and_workers_in_both_modes(self):
        # Signal a separate Python process so unittest's own SIGINT handling is
        # unaffected. No SDK, YOLO, cv2, or real window is used in the child.
        code = '''
import asyncio, os, signal, threading, time
from types import SimpleNamespace as NS
from unittest.mock import patch
import robot.external_sensor_client.main as runtime
from robot.external_sensor_client.capture import CapturedFrame
from robot.external_sensor_client.config import parse_args
from robot.external_sensor_client.perception import PerceivedFrame
calls = []
class Capture:
    device_info = NS(name="mock", serial="mock", rgb=NS(width=8,height=8,fps=30), depth=NS(width=8,height=8,fps=30))
    closed = False
    def __enter__(self):
        self.owner = threading.get_ident()
        return self
    def read(self):
        assert threading.get_ident() == self.owner
        time.sleep(.002)
        return CapturedFrame(time.monotonic_ns()//1000, None, None, 1, 1.0)
    def __exit__(self, *args):
        assert threading.get_ident() == self.owner
        self.closed = True
class Processor:
    closed = False
    def __init__(self, *args): self.owner = threading.get_ident()
    def process(self, frame): return PerceivedFrame(frame)
    def close(self):
        assert threading.get_ident() == self.owner
        self.closed = True
class Display:
    sent = False
    def record(self, name):
        assert threading.current_thread() is threading.main_thread()
        calls.append(name)
    def __enter__(self): self.record("open"); return self
    def poll_quit(self):
        self.record("poll")
        if not self.sent:
            self.sent = True
            os.kill(os.getpid(), signal.SIGINT)
        return False
    def show(self, *args): return self.poll_quit()
    def __exit__(self, *args): self.record("close")
capture, display, processors = Capture(), Display(), []
def factory(*args):
    processor = Processor(*args)
    processors.append(processor)
    return processor
original = runtime.run
async def injected(args):
    await original(args, capture_factory=lambda serial: capture, perception_factory=factory, display_factory=lambda: display)
options = ["--camera-test"] if MODE == "camera" else ["--stationary-rig", "--camera-height-m", "1.2"]
with patch.object(runtime, "parse_args", return_value=parse_args(options+["--display"])), patch.object(runtime, "run", injected):
    assert runtime.main() == 0
assert capture.closed
assert calls[0] == "open" and calls[-1] == "close" and calls.count("close") == 1
assert all(processor.closed for processor in processors)
print("SIGINT cleanup passed")
'''
        for mode in ("camera", "normal"):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, "-B", "-c", "MODE = " + repr(mode) + "\n" + code], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("SIGINT cleanup passed", result.stdout)
