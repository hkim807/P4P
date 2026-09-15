"""Canonical Stage 1 observations without hardware or perception."""

import unittest
from dataclasses import replace

from app.domain.models import ObservationFrame
from robot.external_sensor_client.adapter import ExternalObservationAdapter
from robot.external_sensor_client.capture import CapturedFrame


def frame(timestamp_us=1_000_000):
    return CapturedFrame(timestamp_us, object(), object(), 42, 0.75)


class ExternalAdapterTests(unittest.TestCase):
    def test_complete_observation_is_canonical_and_honest(self):
        observation = ExternalObservationAdapter("external-d435-01", stationary_rig=True).convert(frame())
        validated = ObservationFrame.model_validate_json(observation.model_dump_json())
        self.assertEqual(validated, observation)
        self.assertEqual(observation.humans, [])
        self.assertEqual(observation.images, [])
        self.assertEqual(observation.timestamp_us, 1_000_000)
        self.assertEqual(observation.clock_domain, "MONOTONIC")
        self.assertEqual(observation.coordinate_frame, "ROBOT_BASE")
        self.assertEqual(observation.robot.linear_velocity_mps.model_dump(), {"x": 0.0, "y": 0.0})
        self.assertEqual(observation.robot.angular_velocity_radps, 0.0)
        self.assertEqual(observation.robot.task, "IDLE")
        self.assertEqual(observation.robot.controller_status, "STOPPED")
        self.assertEqual(observation.capabilities.adapter_id, "external-d435-01")
        self.assertEqual(observation.capabilities.robot_type, "external-sensor-rig")
        available = observation.capabilities.available_fields
        self.assertIn("capture.rgb", available)
        self.assertIn("capture.depth", available)
        self.assertIn("robot.linear_velocity_mps", available)
        for field in ("humans.track_id", "humans.position_robot_m", "humans.distance_m", "humans.face_bbox", "humans.head_rpy_rad", "humans.gaze_unit", "humans.gaze_to_robot_score", "robot.free_space"):
            self.assertNotIn(field, available)
            self.assertIn(field, observation.capabilities.unavailable_fields)
        self.assertIn("known to be zero", " ".join(observation.capabilities.notes))

    def test_timestamp_ties_and_ids_remain_unique(self):
        adapter = ExternalObservationAdapter("rig", stationary_rig=True)
        observations = [adapter.convert(frame(t)) for t in (100, 100, 99, 200)]
        self.assertEqual([o.timestamp_us for o in observations], [100, 101, 102, 200])
        self.assertEqual(len({o.observation_id for o in observations}), 4)
        self.assertEqual(observations[0].observation_id, "rig:100:000001")

    def test_stationary_assertion_and_identifier_validation(self):
        for identifier, stationary in (("rig", False), (" ", True), ("x" * 129, True)):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                ExternalObservationAdapter(identifier, stationary_rig=stationary)

    def test_invalid_capture_metadata_is_never_converted(self):
        adapter = ExternalObservationAdapter("rig", stationary_rig=True)
        for timestamp in (-1, float("nan"), float("inf"), True, "100"):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                adapter.convert(frame(timestamp))
        for ratio in (float("nan"), float("inf"), -0.1, 1.1):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                adapter.convert(replace(frame(), depth_valid_sample_ratio=ratio))
