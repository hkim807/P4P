"""Offline tests for duck-typed Navel ObservationFrame conversion."""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace as NS

from app.domain.models import ObservationFrame
from robot.navel_client.adapter import NavelAdapterConfig, NavelObservationAdapter


def locomotion(
    *, linear_x: float = 0.2, linear_y: float = 0.0, angular_z: float = 0.1
) -> NS:
    return NS(
        odometry=NS(
            velocity=NS(
                linear_x=linear_x,
                linear_y=linear_y,
                linear_z=0.0,
                angular_x=0.0,
                angular_y=0.0,
                angular_z=angular_z,
            )
        )
    )


def person(uid: int = 17, **overrides) -> NS:
    values = {
        "uid": uid,
        "dist_mm": 1850.0,
        "face": NS(x1=10, y1=20, x2=110, y2=220),
        "head_position": NS(x=0.1, y=-0.2, z=0.3),
        "gaze": NS(x=2.0, y=0.0, z=0.0),
        "gaze_overlap": 0.75,
        "facial_expression": NS(
            neutral=0.5, happy=0.4, sad=0.0, surprise=0.1, anger=0.0
        ),
        "g_head_position": [NS(sys="ROBOT_BASE", x=1.8, y=0.3, z=1.55)],
        "id_score": 0.99,
    }
    values.update(overrides)
    return NS(**values)


class NavelObservationAdapterTests(unittest.TestCase):
    def adapter(
        self, *, times=(1_000_000_000,), position_coordinates=()
    ) -> NavelObservationAdapter:
        iterator = iter(times)
        return NavelObservationAdapter(
            NavelAdapterConfig(robot_base_coordinate_systems=position_coordinates),
            monotonic_ns=lambda: next(iterator),
        )

    def test_no_person_frame(self):
        payload = self.adapter().convert(NS(persons=[]), locomotion())
        observation = ObservationFrame.model_validate(payload)
        self.assertEqual(observation.humans, [])

    def test_complete_person_maps_verified_fields(self):
        payload = self.adapter(position_coordinates=("ROBOT_BASE",)).convert(
            NS(persons=[person()]), locomotion()
        )
        human = ObservationFrame.model_validate(payload).humans[0]
        self.assertEqual(human.track_id, "17")
        self.assertEqual(human.position_robot_m.x, 1.8)
        self.assertAlmostEqual(human.distance_m, 1.85)
        self.assertEqual(human.head_rpy_rad.model_dump(), {"x": 0.1, "y": -0.2, "z": 0.3})
        self.assertEqual(human.gaze_unit.model_dump(), {"x": 1.0, "y": 0.0, "z": 0.0})
        self.assertEqual(human.gaze_to_robot_score, 0.75)
        self.assertEqual(human.identity_confidence, None)

    def test_multiple_people_and_invalid_or_duplicate_uids(self):
        payload = self.adapter().convert(
            NS(persons=[person(1), person(2), person(None), person(1)]), locomotion()
        )
        self.assertEqual([human["track_id"] for human in payload["humans"]], ["1", "2"])

    def test_missing_optional_fields_are_omitted(self):
        sparse = NS(
            uid=5,
            dist_mm=None,
            face=None,
            head_position=None,
            gaze=None,
            gaze_overlap=None,
            facial_expression=None,
            g_head_position=[],
        )
        human = self.adapter().convert(NS(persons=[sparse]), locomotion())["humans"][0]
        self.assertNotIn("distance_m", human)
        self.assertNotIn("face_bbox", human)
        self.assertNotIn("position_robot_m", human)

    def test_millimetres_are_converted_to_metres(self):
        human = self.adapter().convert(
            NS(persons=[person(dist_mm=2345.0)]), locomotion()
        )["humans"][0]
        self.assertAlmostEqual(human["distance_m"], 2.345)

    def test_face_box_preserves_pixel_coordinates(self):
        human = self.adapter().convert(NS(persons=[person()]), locomotion())["humans"][0]
        self.assertEqual(
            human["face_bbox"],
            {
                "x_min_px": 10,
                "y_min_px": 20,
                "x_max_px": 110,
                "y_max_px": 220,
                "image_id": None,
            },
        )

    def test_head_angles_and_gaze_vector_are_finite_and_normalized(self):
        human = self.adapter().convert(
            NS(persons=[person(gaze=NS(x=3.0, y=4.0, z=0.0))]), locomotion()
        )["humans"][0]
        self.assertAlmostEqual(human["gaze_unit"]["x"], 0.6)
        self.assertAlmostEqual(human["gaze_unit"]["y"], 0.8)
        self.assertTrue(all(math.isfinite(value) for value in human["head_rpy_rad"].values()))

    def test_facial_expression_is_clamped_to_documented_probability_range(self):
        expression = NS(neutral=-0.1, happy=1.2, sad=0.2, surprise=0.3, anger=0.4)
        human = self.adapter().convert(
            NS(persons=[person(facial_expression=expression)]), locomotion()
        )["humans"][0]
        self.assertEqual(human["facial_expression"]["neutral"], 0.0)
        self.assertEqual(human["facial_expression"]["happy"], 1.0)

    def test_selects_only_explicit_robot_base_position(self):
        positions = [
            NS(sys="CAM_HEAD", x=9.0, y=9.0, z=9.0),
            NS(sys="ROBOT_BASE", x=1.0, y=2.0, z=3.0),
        ]
        human = self.adapter(position_coordinates=("ROBOT_BASE",)).convert(
            NS(persons=[person(g_head_position=positions)]), locomotion()
        )["humans"][0]
        self.assertEqual(human["position_robot_m"], {"x": 1.0, "y": 2.0, "z": 3.0})

    def test_refuses_undefined_or_incompatible_position(self):
        positions = [
            NS(sys="UNDEFINED", x=1.0, y=2.0, z=3.0),
            NS(sys="HEAD_STRAIGHT", x=1.0, y=2.0, z=3.0),
        ]
        human = self.adapter().convert(
            NS(persons=[person(g_head_position=positions)]), locomotion()
        )["humans"][0]
        self.assertNotIn("position_robot_m", human)

    def test_capabilities_and_sensor_provenance_are_accurate(self):
        payload = self.adapter(position_coordinates=("ROBOT_BASE",)).convert(
            NS(persons=[person()]), locomotion()
        )
        capabilities = payload["capabilities"]
        self.assertIn("humans.position_robot_m", capabilities["available_fields"])
        self.assertIn("humans.speech_activity", capabilities["unavailable_fields"])
        self.assertIn("images", capabilities["unavailable_fields"])
        self.assertEqual(payload["humans"][0]["sensor_sources"], ["HEAD_RGB"])

    def test_stationary_sdk_velocity_is_a_valid_measurement(self):
        payload = self.adapter().convert(
            NS(persons=[]), locomotion(linear_x=0.0, linear_y=0.0, angular_z=0.0)
        )

        self.assertEqual(payload["robot"]["linear_velocity_mps"], {"x": 0.0, "y": 0.0})
        self.assertEqual(payload["robot"]["angular_velocity_radps"], 0.0)
        self.assertIn(
            "robot.linear_velocity_mps",
            payload["capabilities"]["available_fields"],
        )

    def test_moving_sdk_linear_x_maps_to_canonical_x(self):
        payload = self.adapter().convert(
            NS(persons=[]), locomotion(linear_x=0.35)
        )

        self.assertEqual(payload["robot"]["linear_velocity_mps"]["x"], 0.35)

    def test_sdk_linear_y_maps_to_canonical_y(self):
        payload = self.adapter().convert(
            NS(persons=[]), locomotion(linear_y=-0.12)
        )

        self.assertEqual(payload["robot"]["linear_velocity_mps"]["y"], -0.12)

    def test_sdk_angular_z_maps_to_canonical_yaw_velocity(self):
        payload = self.adapter().convert(
            NS(persons=[]), locomotion(angular_z=0.4)
        )

        self.assertEqual(payload["robot"]["angular_velocity_radps"], 0.4)

    def test_missing_sdk_linear_x_keeps_existing_failure_behavior(self):
        packet = NS(
            odometry=NS(velocity=NS(linear_y=0.0, angular_z=0.0))
        )

        with self.assertRaisesRegex(ValueError, "locomotion velocity is unavailable"):
            self.adapter().convert(NS(persons=[]), packet)

    def test_stationary_fallback_is_explicit_and_disabled_by_default(self):
        with self.assertRaisesRegex(ValueError, "stationary_velocity_fallback"):
            self.adapter().convert(NS(persons=[]), None)
        configured = NavelObservationAdapter(
            NavelAdapterConfig(stationary_velocity_fallback=True),
            monotonic_ns=lambda: 1_000_000_000,
        ).convert(NS(persons=[]), None)
        self.assertEqual(configured["robot"]["linear_velocity_mps"], {"x": 0.0, "y": 0.0})
        self.assertIn(
            "robot.linear_velocity_mps",
            configured["capabilities"]["unavailable_fields"],
        )

    def test_stationary_fallback_applies_when_sdk_linear_x_is_unavailable(self):
        packet = NS(
            odometry=NS(velocity=NS(linear_y=0.3, angular_z=0.2))
        )
        payload = NavelObservationAdapter(
            NavelAdapterConfig(stationary_velocity_fallback=True),
            monotonic_ns=lambda: 1_000_000_000,
        ).convert(NS(persons=[]), packet)

        self.assertEqual(payload["robot"]["linear_velocity_mps"], {"x": 0.0, "y": 0.0})
        self.assertEqual(payload["robot"]["angular_velocity_radps"], 0.0)

    def test_real_sdk_measurement_takes_precedence_over_stationary_fallback(self):
        payload = NavelObservationAdapter(
            NavelAdapterConfig(stationary_velocity_fallback=True),
            monotonic_ns=lambda: 1_000_000_000,
        ).convert(
            NS(persons=[]),
            locomotion(linear_x=0.25, linear_y=0.05, angular_z=-0.15),
        )

        self.assertEqual(payload["robot"]["linear_velocity_mps"], {"x": 0.25, "y": 0.05})
        self.assertEqual(payload["robot"]["angular_velocity_radps"], -0.15)
        self.assertIn(
            "robot.linear_velocity_mps",
            payload["capabilities"]["available_fields"],
        )

    def test_observation_ids_and_timestamps_are_monotonically_unique(self):
        adapter = self.adapter(times=(1_000_000_000, 1_000_000_000))
        first = adapter.convert(NS(persons=[]), locomotion())
        second = adapter.convert(NS(persons=[]), locomotion())
        self.assertGreater(second["timestamp_us"], first["timestamp_us"])
        self.assertNotEqual(second["observation_id"], first["observation_id"])


if __name__ == "__main__":
    unittest.main()
