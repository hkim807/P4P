"""Synthetic-array person, depth and canonical mapping tests; no model downloads."""

import math
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

from app.domain.models import ObservationFrame
from robot.external_sensor_client.adapter import ExternalObservationAdapter
from robot.external_sensor_client.capture import CapturedFrame
from robot.external_sensor_client.depth import DepthResult, camera_to_robot, estimate_position, torso_roi
from robot.external_sensor_client.perception import PerceivedFrame, PersonPerception, TrackedPerson, extract_detections


def box(track=1, cls=0, confidence=0.8, bbox=(0, 0, 40, 100)):
    return NS(cls=np.array([cls]), conf=np.array([confidence]), xyxy=np.array([bbox]),
              id=None if track is None else np.array([track]))


def capture(depth=None, timestamp=1_000_000):
    rgb = np.zeros((100, 100, 3), dtype=np.uint8)
    rgb[:] = (10, 20, 30)
    raw = np.full((100, 100), 2000, dtype=float) if depth is None else depth
    return CapturedFrame(timestamp, Mock(get_data=Mock(return_value=rgb)),
                         Mock(get_data=Mock(return_value=raw)), 42, 0.8, 0.001, "intrinsics")


def model(boxes):
    return Mock(task="detect", names={0: "person"}, track=Mock(return_value=[NS(boxes=boxes)]))


def deproject(intrinsics, pixel, distance):
    # Test double for SDK deprojection; production calls RealSense itself.
    return [(pixel[0]-50)/100*distance, (pixel[1]-50)/100*distance, distance]


class DepthTests(unittest.TestCase):
    def test_torso_roi_and_clipping(self):
        self.assertEqual(torso_roi((0, 0, 100, 100), 100, 100), (30, 25, 70, 55))
        self.assertEqual(torso_roi((-10, -20, 120, 130), 100, 100), (30, 25, 70, 55))
        for bbox in ((110, 0, 120, 100), (40, 0, 20, 100), (0, 0, 1, 1), (0, 0, math.nan, 100)):
            self.assertIsNone(torso_roi(bbox, 100, 100))

    def estimate(self, raw, **kwargs):
        return estimate_position(raw, (0, 0, 100, 100), 0.001, "intrinsics", deproject, 1.2, **kwargs)

    def test_median_filters_zero_nan_infinity_and_range(self):
        raw = np.full((100, 100), 2000.0)
        raw[25, 30:36] = [0, math.nan, math.inf, -1, 299, 6001]
        raw[26, 30:35] = 3000
        result = self.estimate(raw)
        self.assertEqual(result.depth_m, 2.0)
        self.assertIsNone(result.failure)
        u, v = result.pixel
        self.assertTrue(30 <= u < 70 and 25 <= v < 55)
        self.assertEqual(raw[int(v), int(u)], 2000)

    def test_no_background_fallback_and_minimum_samples(self):
        raw = np.full((100, 100), 3000.0)
        raw[25:55, 30:70] = 0
        self.assertIsNone(self.estimate(raw).position_robot_m)
        raw[25, 30:49] = 2000
        self.assertIsNone(self.estimate(raw, min_valid_ratio=0.01).position_robot_m)
        raw[25, 49] = 2000
        self.assertIsNotNone(self.estimate(raw, min_valid_ratio=0.01).position_robot_m)
        self.assertIsNone(self.estimate(raw).distance_m)

    def test_excludes_other_person_depth(self):
        result = self.estimate(np.full((100, 100), 2000), exclude_boxes=((0, 0, 100, 100),))
        self.assertIsNone(result.position_robot_m)
        self.assertIn("insufficient", result.failure)

    def test_sdk_deprojection_receives_intrinsics_pixel_and_median(self):
        sdk = Mock(return_value=(0.5, 0.2, 2.0))
        result = estimate_position(np.full((100, 100), 2000), (0, 0, 100, 100), 0.001,
                                   "colour-calibration", sdk, 1.2)
        self.assertEqual(sdk.call_args.args, ("colour-calibration", list(result.pixel), 2.0))
        self.assertEqual(result.position_robot_m, (2.0, -0.5, 1.0))

    def test_invalid_calibration_and_deprojection(self):
        raw = np.full((100, 100), 2000)
        for scale, intrinsics, sdk in ((math.nan, "i", deproject), (0, "i", deproject),
                                       (0.001, None, deproject), (0.001, "i", Mock(return_value=(math.nan, 0, 2)))):
            result = estimate_position(raw, (0, 0, 100, 100), scale, intrinsics, sdk, 1.2)
            self.assertIsNone(result.position_robot_m)
            self.assertIsNone(result.distance_m)

    def test_fixed_transform_and_planar_distance(self):
        self.assertEqual(camera_to_robot((0, 0, 2), 1.2), (2, 0, 1.2))
        self.assertEqual(camera_to_robot((0.5, 0.2, 2), 1.2), (2, -0.5, 1.0))
        raw = np.full((100, 100), 2000)
        positions = []
        for y in (0, 1):
            result = estimate_position(raw, (0, 0, 100, 100), 0.001, "i", Mock(return_value=(3, y, 4)), 1.2)
            positions.append(result)
            self.assertEqual(result.distance_m, 5.0)
        self.assertNotEqual(positions[0].position_robot_m[2], positions[1].position_robot_m[2])
        for point, height in (((math.nan, 0, 1), 1.2), ((0, math.inf, 1), 1.2), ((0, 0, 1), 0), ((0, 0, 1), math.inf)):
            with self.assertRaises(ValueError):
                camera_to_robot(point, height)


class PersonPerceptionTests(unittest.TestCase):
    def test_person_filter_id_validation_and_confidence(self):
        detections = extract_detections([box(1), box(2, cls=2), box(3, confidence=0.1), box(None), box(math.nan), box(1.5)], 0.25)
        self.assertEqual(len(detections), 4)
        self.assertEqual(detections[0].tracker_id, 1)
        self.assertEqual(detections[0].confidence, 0.8)
        self.assertTrue(all(d.tracker_id is None for d in detections[1:]))

    def test_tracking_api_bgr_stability_session_namespacing_and_missing_ids(self):
        detector = model([box(2, bbox=(60, 0, 100, 100)), box(1), box(None, bbox=(110, 0, 120, 100)), box(9, cls=3)])
        first = PersonPerception("unused", 0.25, 1.2, model=detector, deproject=deproject, session_id="session-a")
        f1, f2 = first.process(capture()), first.process(capture(timestamp=1_100_000))
        self.assertEqual([h.track_id for h in f1.humans], ["d435:session-a:1", "d435:session-a:2"])
        self.assertEqual([h.track_id for h in f1.humans], [h.track_id for h in f2.humans])
        self.assertEqual(f1.detections_without_track_id, 1)
        kwargs = detector.track.call_args.kwargs
        self.assertEqual(kwargs["classes"], [0])
        self.assertTrue(kwargs["persist"])
        self.assertEqual(kwargs["tracker"], "bytetrack.yaml")
        self.assertEqual(kwargs["conf"], 0.25)
        np.testing.assert_array_equal(detector.track.call_args.args[0][0, 0], [30, 20, 10])
        other = PersonPerception("unused", 0.25, 1.2, model=model([box(1)]), deproject=deproject, session_id="session-b")
        self.assertNotEqual(f1.humans[0].track_id, other.process(capture()).humans[0].track_id)
        first.close()
        self.assertIsNone(detector.predictor)

    def test_depth_unavailable_preserves_track(self):
        processor = PersonPerception("unused", 0.25, 1.2, model=model([box(1)]), deproject=deproject)
        perceived = processor.process(capture(np.zeros((100, 100))))
        self.assertEqual(len(perceived.humans), 1)
        self.assertEqual(perceived.humans_without_valid_depth, 1)
        self.assertIsNone(perceived.humans[0].depth.position_robot_m)

    def test_no_weight_download_in_camera_paths_and_bad_model_is_clear(self):
        from robot.external_sensor_client.perception import PerceptionError
        with self.assertRaisesRegex(PerceptionError, "class 0"):
            PersonPerception("unused", 0.25, 1.2, model=Mock(task="detect", names={0: "cat"}), deproject=deproject)
        with patch("robot.external_sensor_client.perception.import_module", side_effect=ImportError("missing")):
            with self.assertRaisesRegex(PerceptionError, "install requirements"):
                PersonPerception("never-download", 0.25, 1.2)


class HumanMappingTests(unittest.TestCase):
    def test_valid_humans_age_sensor_sources_and_unavailable_fields(self):
        processor = PersonPerception("unused", 0.25, 1.2, model=model([box(1)]), deproject=deproject, session_id="test")
        perceived = processor.process(capture())
        adapter = ExternalObservationAdapter("rig", stationary_rig=True, camera_height_m=1.2)
        with patch("robot.external_sensor_client.adapter.time.monotonic_ns", return_value=1_700_000_000):
            observation = adapter.convert(perceived)
        ObservationFrame.model_validate_json(observation.model_dump_json())
        human = observation.humans[0]
        self.assertEqual(human.state_age_ms, 700)
        self.assertEqual(human.source_timestamp_us, 1_000_000)
        self.assertEqual(observation.timestamp_us, 1_000_000)
        self.assertEqual(human.sensor_sources, ["EXTERNAL", "DEPTH"])
        self.assertEqual(human.detection_confidence, 0.8)
        self.assertTrue(human.observed)
        self.assertEqual(human.distance_m, math.hypot(human.position_robot_m.x, human.position_robot_m.y))
        for name in ("face_bbox", "identity_confidence", "head_rpy_rad", "body_yaw_rad", "gaze_unit", "gaze_to_robot_score", "facial_expression", "speech_activity", "group_observation_id", "uncertainty"):
            self.assertIsNone(getattr(human, name), name)
        self.assertEqual(observation.images, [])
        self.assertIn("humans.position_robot_m", observation.capabilities.available_fields)
        self.assertIn("humans.face_bbox", observation.capabilities.unavailable_fields)
        self.assertEqual(observation.robot.task, "IDLE")

    def test_missing_depth_nulls_and_empty_frames(self):
        adapter = ExternalObservationAdapter("rig", stationary_rig=True, camera_height_m=1.2)
        person = TrackedPerson("d435:test:1", (0, 0, 40, 100), 0.7, DepthResult(failure="invalid"))
        observation = adapter.convert(PerceivedFrame(capture(), (person,)))
        human = observation.humans[0]
        self.assertIsNone(human.position_robot_m)
        self.assertIsNone(human.distance_m)
        self.assertEqual(human.sensor_sources, ["EXTERNAL"])
        self.assertEqual(adapter.convert(PerceivedFrame(capture())).humans, [])

    def test_multiple_humans_sorted_unique_and_duplicate_rejected(self):
        adapter = ExternalObservationAdapter("rig", stationary_rig=True, camera_height_m=1.2)
        humans = tuple(TrackedPerson(f"d435:test:{i}", (0, 0, 40, 100), 0.8, DepthResult()) for i in (2, 1))
        result = adapter.convert(PerceivedFrame(capture(), humans))
        self.assertEqual([h.track_id for h in result.humans], ["d435:test:1", "d435:test:2"])
        with self.assertRaises(ValueError):
            adapter.convert(PerceivedFrame(capture(), (humans[0], humans[0])))
