"""Offline tests for the versioned social-navigation contracts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.domain.models import BehaviorIntent, ObservationFrame, SocialState
from app.domain.schema import SCHEMA_MODELS, render_schema, write_schemas


def observation_payload() -> dict:
    return {
        "schema_version": "1.0",
        "observation_id": "obs-001",
        "timestamp_us": 1_000_000,
        "clock_domain": "MONOTONIC",
        "coordinate_frame": "ROBOT_BASE",
        "robot": {
            "linear_velocity_mps": {"x": 0.3, "y": 0.0},
            "angular_velocity_radps": 0.0,
            "task": "GUIDING",
            "controller_status": "ACTIVE",
            "destination_id": "exhibit-4",
            "free_space": {"left_m": 1.8, "forward_m": 2.6, "right_m": 0.9},
        },
        "humans": [
            {
                "track_id": "17",
                "observed": True,
                "source_timestamp_us": 999_900,
                "state_age_ms": 1,
                "position_robot_m": {"x": 1.82, "y": 0.31, "z": 1.55},
                "distance_m": 1.85,
                "detection_confidence": 0.93,
                "gaze_to_robot_score": 0.76,
                "sensor_sources": ["HEAD_RGB", "DEPTH"],
            }
        ],
        "images": [
            {
                "image_id": "head-999900",
                "camera_id": "head-rgb",
                "timestamp_us": 999_900,
                "coordinate_frame": "HEAD_CAMERA",
                "uri": "frames/head-999900.jpg",
                "width_px": 1280,
                "height_px": 720,
                "encoding": "JPEG",
            }
        ],
        "capabilities": {
            "adapter_id": "synthetic-v1",
            "robot_type": "test-robot",
            "available_fields": ["humans.position_robot_m", "humans.gaze_to_robot_score"],
            "unavailable_fields": ["humans.body_yaw_rad"],
        },
    }


def social_state_payload() -> dict:
    return {
        "schema_version": "1.0",
        "state_id": "state-001",
        "source_observation_id": "obs-001",
        "timestamp_us": 1_000_000,
        "clock_domain": "MONOTONIC",
        "coordinate_frame": "ROBOT_BASE",
        "history_window_s": 5.0,
        "robot": {
            "linear_velocity_mps": {"x": 0.3, "y": 0.0},
            "angular_velocity_radps": 0.0,
            "task": "GUIDING",
            "controller_status": "ACTIVE",
            "destination_id": "exhibit-4",
        },
        "humans": [
            {
                "track_id": "17",
                "observed": True,
                "predicted_only": False,
                "state_age_ms": 1,
                "track_age_s": 3.2,
                "time_since_seen_s": 0.0,
                "position_robot_m": {"x": 1.82, "y": 0.31, "z": 1.55},
                "distance_m": 1.85,
                "velocity_robot_mps": {"x": -0.52, "y": -0.14},
                "speed_mps": 0.54,
                "closing_speed_mps": 0.52,
                "motion_relation": "APPROACHING_CROSSING",
                "distance_trend": "DECREASING",
                "predicted_positions": [
                    {
                        "horizon_s": 1.0,
                        "position_robot_m": {"x": 1.30, "y": 0.17},
                        "position_std_m": 0.07,
                    }
                ],
                "time_to_closest_approach_s": 1.5,
                "distance_at_closest_approach_m": 0.34,
                "path_conflict_probability": 0.91,
                "proxemic_zone": "SOCIAL",
                "personal_space_cost": 0.72,
                "attention": {
                    "gaze_to_robot_score": 0.76,
                    "gaze_ratio_2s": 0.63,
                    "state": "INTERMITTENT",
                },
                "engagement": {
                    "state": "ATTENDING",
                    "engagement_probability": 0.81,
                },
                "group_id": "group-a",
                "uncertainty": {
                    "position_std_m": 0.07,
                    "velocity_std_mps": 0.11,
                },
                "evidence_codes": ["HIGH_PATH_CONFLICT"],
            },
            {
                "track_id": "18",
                "observed": True,
                "state_age_ms": 2,
                "track_age_s": 2.8,
                "time_since_seen_s": 0.0,
                "group_id": "group-a",
                "uncertainty": {},
            },
        ],
        "groups": [
            {
                "group_id": "group-a",
                "member_ids": ["17", "18"],
                "centroid_robot_m": {"x": 1.9, "y": 0.4},
                "interaction_space_radius_m": 0.8,
                "confidence": 0.77,
            }
        ],
        "crowd": {
            "people_within_1m": 0,
            "people_within_2m": 2,
            "people_within_3m": 2,
            "density_people_m2": 0.18,
        },
        "selected_image_ids": ["head-999900"],
    }


def intent_payload(action: str = "YIELD") -> dict:
    return {
        "schema_version": "1.0",
        "decision_id": "decision-001",
        "observation_id": "obs-001",
        "social_state_id": "state-001",
        "created_at_us": 1_000_100,
        "action": action,
        "target_human_id": "17",
        "preferences": {
            "target_speed_mps": 0.2,
            "preferred_social_distance_m": 1.0,
            "passing_side": "RIGHT",
            "hold_duration_s": 1.0,
        },
        "valid_for_ms": 750,
        "reason_codes": ["HIGH_PATH_CONFLICT", "HUMAN_APPROACHING"],
        "decision_confidence": 0.88,
    }


class ObservationFrameTests(unittest.TestCase):
    def test_accepts_a_robot_independent_observation(self):
        observation = ObservationFrame.model_validate(observation_payload())
        self.assertEqual(observation.observation_id, "obs-001")
        self.assertEqual(observation.humans[0].track_id, "17")

    def test_rejects_duplicate_track_ids(self):
        payload = observation_payload()
        payload["humans"].append(dict(payload["humans"][0]))
        with self.assertRaisesRegex(ValidationError, "track IDs must be unique"):
            ObservationFrame.model_validate(payload)

    def test_rejects_unknown_fields(self):
        payload = observation_payload()
        payload["robot"]["speed"] = 0.3
        with self.assertRaises(ValidationError):
            ObservationFrame.model_validate(payload)

    def test_rejects_non_finite_measurements(self):
        payload = observation_payload()
        payload["robot"]["linear_velocity_mps"]["x"] = float("nan")
        with self.assertRaises(ValidationError):
            ObservationFrame.model_validate(payload)


class SocialStateTests(unittest.TestCase):
    def test_accepts_temporal_state_and_entity_references(self):
        state = SocialState.model_validate(social_state_payload())
        self.assertEqual(state.history_window_s, 5.0)
        self.assertEqual(state.groups[0].member_ids, ["17", "18"])

    def test_rejects_a_group_with_an_unknown_human(self):
        payload = social_state_payload()
        payload["groups"][0]["member_ids"] = ["17", "missing"]
        with self.assertRaisesRegex(ValidationError, "references unknown humans"):
            SocialState.model_validate(payload)

    def test_rejects_observed_and_predicted_only_track(self):
        payload = social_state_payload()
        payload["humans"][0]["predicted_only"] = True
        with self.assertRaisesRegex(ValidationError, "observed track"):
            SocialState.model_validate(payload)

    def test_rejects_inconsistent_crowd_counts(self):
        payload = social_state_payload()
        payload["crowd"]["people_within_1m"] = 3
        with self.assertRaisesRegex(ValidationError, "nondecreasing"):
            SocialState.model_validate(payload)


class BehaviorIntentTests(unittest.TestCase):
    def test_accepts_a_schema_constrained_intent(self):
        intent = BehaviorIntent.model_validate(intent_payload())
        self.assertEqual(intent.action, "YIELD")
        self.assertEqual(intent.valid_for_ms, 750)

    def test_requires_a_target_for_human_targeted_actions(self):
        payload = intent_payload(action="APPROACH")
        payload["target_human_id"] = None
        with self.assertRaisesRegex(ValidationError, "requires target_human_id"):
            BehaviorIntent.model_validate(payload)

    def test_rejects_unbounded_validity(self):
        payload = intent_payload()
        payload["valid_for_ms"] = 60_001
        with self.assertRaises(ValidationError):
            BehaviorIntent.model_validate(payload)

    def test_rejects_unstructured_reason_codes(self):
        payload = intent_payload()
        payload["reason_codes"] = ["because the person is nearby"]
        with self.assertRaises(ValidationError):
            BehaviorIntent.model_validate(payload)


class JsonSchemaTests(unittest.TestCase):
    def test_committed_schemas_match_the_models(self):
        root = Path(__file__).resolve().parents[1]
        schema_directory = root / "schemas" / "v1"
        for filename, model in SCHEMA_MODELS.items():
            with self.subTest(filename=filename):
                committed = (schema_directory / filename).read_text(encoding="utf-8")
                self.assertEqual(committed, render_schema(filename, model))

    def test_schema_writer_creates_valid_json_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_schemas(Path(directory))
            self.assertEqual(len(paths), 3)
            for path in paths:
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    document["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertTrue(document["$id"].endswith(path.name))


if __name__ == "__main__":
    unittest.main()
