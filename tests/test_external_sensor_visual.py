"""Synthetic rendering with mocked HighGUI; no real windows are opened."""

import asyncio
import io
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

from robot.external_sensor_client.depth import DepthResult
from robot.external_sensor_client.config import parse_args
from robot.external_sensor_client.main import Counters, LiveStatus, camera_test, display_loop, run
from robot.external_sensor_client.perception import PerceivedFrame, TrackedPerson
from robot.external_sensor_client.visual import DisplayError, VisualDisplay, overlay_rgb, person_label, quit_key
from test_external_sensor_runtime import FakeCapture, frame

import threading


class VisualTests(unittest.TestCase):
    def test_overlay_does_not_mutate_input_and_labels_depth(self):
        cv = Mock(FONT_HERSHEY_SIMPLEX=0)
        rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        valid = TrackedPerson("d435:test:1", (10, 30, 80, 90), 0.8, DepthResult((2, -0.5, 1), 2.0615))
        missing = TrackedPerson("d435:test:2", (0, 30, 20, 80), 0.6, DepthResult())
        result = overlay_rgb(rgb, (valid, missing), cv, "action=MONITOR error=offline")
        self.assertIsNot(result, rgb)
        np.testing.assert_array_equal(rgb, 0)
        self.assertEqual(cv.rectangle.call_count, 2)
        labels = " ".join(call.args[1] for call in cv.putText.call_args_list)
        for text in ("d435:test:1", "conf=0.80", "xyz=(2.00,-0.50,1.00)", "depth unavailable", "action=MONITOR", "error=offline"):
            self.assertIn(text, labels)

    def test_q_escape_and_disabled_display(self):
        self.assertTrue(quit_key(ord("q")))
        self.assertTrue(quit_key(27))
        self.assertFalse(quit_key(-1))
        capture = FakeCapture()
        capture.frames.put(frame(100))
        with patch("robot.external_sensor_client.visual.import_module", side_effect=AssertionError("GUI import forbidden")), redirect_stdout(io.StringIO()):
            camera_test(capture, 1, threading.Event())
        self.assertTrue(capture.closed)

    def test_mock_gui_shows_rgb_depth_and_releases_on_quit(self):
        cv = Mock(WINDOW_NORMAL=0, COLORMAP_JET=0, FONT_HERSHEY_SIMPLEX=0)
        cv.getBuildInformation.return_value = "GUI: QT5"
        cv.applyColorMap.side_effect = lambda data, mode: np.repeat(data[:, :, None], 3, axis=2)
        cv.waitKey.return_value = ord("q")
        captured = frame(100)
        perceived = PerceivedFrame(captured, rgb_bgr=np.zeros((100, 100, 3), dtype=np.uint8), depth_m=np.ones((100, 100)))
        with patch.dict(os.environ, {"DISPLAY": ":0"}), patch("robot.external_sensor_client.visual.import_module", side_effect=lambda name: cv if name == "cv2" else np), patch("robot.external_sensor_client.visual.subprocess.run", return_value=NS(returncode=0)):
            with VisualDisplay() as display:
                self.assertTrue(display.show(perceived, "action=MONITOR"))
        self.assertEqual(cv.imshow.call_args.args[1].shape, (100, 200, 3))
        cv.destroyAllWindows.assert_called_once()

    def test_stale_display_native_gui_failure_is_isolated(self):
        cv = Mock()
        cv.getBuildInformation.return_value = "GUI: QT5"
        with patch.dict(os.environ, {"DISPLAY": ":99"}), patch("robot.external_sensor_client.visual.sys.platform", "linux"), patch("robot.external_sensor_client.visual.import_module", return_value=cv), patch("robot.external_sensor_client.visual.subprocess.run", return_value=NS(returncode=-6, stderr="cannot connect to display")) as probe:
            with self.assertRaisesRegex(DisplayError, "graphical session"):
                VisualDisplay().__enter__()
        probe.assert_called_once()
        cv.namedWindow.assert_not_called()

    def test_gui_missing_support_and_no_graphical_session_are_clear(self):
        cv = Mock()
        cv.getBuildInformation.return_value = "GUI: NONE"
        with patch.dict(os.environ, {"DISPLAY": ":0"}), patch("robot.external_sensor_client.visual.import_module", return_value=cv):
            with self.assertRaisesRegex(DisplayError, "GUI support"):
                VisualDisplay().__enter__()
        with patch.dict(os.environ, {}, clear=True), patch("robot.external_sensor_client.visual.sys.platform", "linux"):
            with self.assertRaisesRegex(DisplayError, "graphical session"):
                VisualDisplay().__enter__()

    def test_display_q_stops_worker_and_reports_latest_action(self):
        stop, stats, status = threading.Event(), Counters(), LiveStatus()
        status.update(frame=PerceivedFrame(frame(100)), action="MONITOR", transport_error="offline")
        display = Mock()
        display.__enter__ = Mock(return_value=display)
        display.__exit__ = Mock(return_value=False)
        display.show.return_value = True
        asyncio.run(display_loop(status, stats, stop, lambda: display))
        self.assertTrue(stop.is_set())
        text = display.show.call_args.args[1]
        self.assertIn("action=MONITOR", text)
        self.assertIn("error=offline", text)
        display.__exit__.assert_called_once()

    def test_camera_test_quit_closes_pipeline_and_window(self):
        capture, stop = FakeCapture(), threading.Event()
        capture.frames.put(frame(100))
        display = Mock()
        display.__enter__ = Mock(return_value=display)
        display.__exit__ = Mock(return_value=False)
        display.show.return_value = True
        display.poll_quit.return_value = False
        with redirect_stdout(io.StringIO()):
            # Supply enough frames to finish any read already pending on quit.
            for timestamp in range(101, 130):
                capture.frames.put(frame(timestamp))
            asyncio.run(run(parse_args(["--camera-test", "--display"]),
                            capture_factory=lambda serial: capture, display_factory=lambda: display))
        self.assertTrue(capture.closed)
        display.__exit__.assert_called_once()
