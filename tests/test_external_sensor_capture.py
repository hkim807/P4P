"""Mock-only SDK acquisition, metadata and cleanup tests."""

import subprocess
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from robot.external_sensor_client.capture import CaptureError, RealSenseCapture


def sdk_device(name="Intel RealSense D435", serial="123"):
    device = Mock()
    device.get_info.side_effect = lambda key: {"name": name, "serial": serial}[key]
    return device


def fake_sdk():
    color = Mock()
    color.get_width.return_value = 640
    color.get_height.return_value = 480
    color.get_frame_number.return_value = 42
    depth = Mock()
    depth.get_width.return_value = 640
    depth.get_height.return_value = 480
    depth.get_distance.side_effect = lambda x, y: 1.0 if x < 320 else 0.0
    frames = Mock()
    frames.get_color_frame.return_value = color
    frames.get_depth_frame.return_value = depth
    device = sdk_device()
    video = Mock()
    video.width.return_value = 640
    video.height.return_value = 480
    video.fps.return_value = 30
    profile = Mock()
    profile.get_device.return_value = device
    profile.get_stream.return_value.as_video_stream_profile.return_value = video
    pipeline = Mock()
    pipeline.start.return_value = profile
    pipeline.wait_for_frames.return_value = frames
    align = Mock()
    align.process.return_value = frames
    sdk = NS(context=Mock(), pipeline=Mock(return_value=pipeline), config=Mock(),
             align=Mock(return_value=align), camera_info=NS(name="name", serial_number="serial"),
             stream=NS(color="color", depth="depth"), format=NS(rgb8="rgb8", z16="z16"))
    sdk.context.return_value.query_devices.return_value = [device]
    return sdk, pipeline, frames


class RealSenseCaptureTests(unittest.TestCase):
    def test_import_does_not_require_sdk_or_navel(self):
        code = '''
import importlib.abc, sys
class RejectSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"pyrealsense2", "navel"}:
            raise ImportError("hardware dependency forbidden")
sys.meta_path.insert(0, RejectSDK())
import robot.external_sensor_client.main
assert "pyrealsense2" not in sys.modules
assert "navel" not in sys.modules
'''
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_dependency_only_errors_on_start(self):
        with patch("robot.external_sensor_client.capture.import_module", side_effect=ImportError("missing")) as load:
            capture = RealSenseCapture()
            load.assert_not_called()
            with self.assertRaisesRegex(CaptureError, "pyrealsense2 is unavailable"):
                capture.start()

    def test_start_metadata_alignment_read_and_idempotent_close(self):
        sdk, pipeline, frames = fake_sdk()
        with patch("robot.external_sensor_client.capture.import_module", return_value=sdk), patch("robot.external_sensor_client.capture.time.monotonic_ns", side_effect=[100_000, 100_000]):
            with RealSenseCapture("123") as capture:
                info = capture.device_info
                self.assertEqual((info.name, info.serial), ("Intel RealSense D435", "123"))
                self.assertEqual((info.rgb.width, info.depth.height, info.rgb.fps, info.depth.fps), (640, 480, 30, 30))
                first, second = capture.read(), capture.read()
                self.assertEqual((first.timestamp_us, second.timestamp_us), (100, 101))
                self.assertEqual(first.frame_number, 42)
                self.assertEqual(first.depth_valid_sample_ratio, 0.5)
                self.assertIs(first.color, frames.get_color_frame.return_value)
                sdk.config.return_value.enable_device.assert_called_once_with("123")
                self.assertEqual(sdk.config.return_value.enable_stream.call_args_list[0].args, ("color", 640, 480, "rgb8", 30))
                self.assertEqual(sdk.config.return_value.enable_stream.call_args_list[1].args, ("depth", 640, 480, "z16", 30))
                sdk.align.assert_called_once_with("color")
                pipeline.wait_for_frames.assert_called_with(timeout_ms=1000)
            capture.close()
            pipeline.stop.assert_called_once()

    def test_no_compatible_device_or_serial(self):
        for devices, serial in (([], None), ([sdk_device("D455")], None), ([sdk_device()], "other")):
            sdk, pipeline, _ = fake_sdk()
            sdk.context.return_value.query_devices.return_value = devices
            with patch("robot.external_sensor_client.capture.import_module", return_value=sdk), self.assertRaisesRegex(CaptureError, "no compatible"):
                RealSenseCapture(serial).start()
            pipeline.start.assert_not_called()

    def test_start_and_metadata_failure_close_pipeline(self):
        for stage in ("start", "metadata", "align"):
            sdk, pipeline, _ = fake_sdk()
            if stage == "start":
                pipeline.start.side_effect = RuntimeError("unsupported streams")
            elif stage == "metadata":
                pipeline.start.return_value.get_stream.side_effect = RuntimeError("metadata failed")
            else:
                sdk.align.side_effect = RuntimeError("alignment unavailable")
            with patch("robot.external_sensor_client.capture.import_module", return_value=sdk), self.assertRaisesRegex(CaptureError, "failed to discover/start"):
                RealSenseCapture().start()
            pipeline.stop.assert_called_once()

    def test_frame_timeout_and_invalid_frames_close(self):
        for invalid in ("timeout", "depth", "alignment", "size"):
            sdk, pipeline, frames = fake_sdk()
            if invalid == "timeout":
                pipeline.wait_for_frames.side_effect = RuntimeError("Frame didn't arrive")
            elif invalid == "depth":
                frames.get_depth_frame.return_value = None
            elif invalid == "alignment":
                sdk.align.return_value.process.side_effect = RuntimeError("bad alignment")
            else:
                frames.get_color_frame.return_value.get_width.return_value = 0
            with patch("robot.external_sensor_client.capture.import_module", return_value=sdk):
                capture = RealSenseCapture()
                capture.start()
                with self.assertRaises(CaptureError):
                    capture.read()
                capture.close()
            pipeline.stop.assert_called_once()

    def test_context_cleans_up_on_exception_and_keyboard_interrupt(self):
        for exception in (ValueError("client failure"), KeyboardInterrupt()):
            sdk, pipeline, _ = fake_sdk()
            with patch("robot.external_sensor_client.capture.import_module", return_value=sdk):
                with self.assertRaises(type(exception)):
                    with RealSenseCapture():
                        raise exception
            pipeline.stop.assert_called_once()
