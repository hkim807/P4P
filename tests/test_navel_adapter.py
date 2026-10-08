"""Sensor mapping and missing-data behavior using SDK-shaped packets."""

import json
import unittest
from types import SimpleNamespace as NS

from app.domain.models import RawObservationFrame
from robot.navel_client.adapter import NavelObservationAdapter
from tests.fixtures import CoordSystem, Sensor, locomotion, perception, person


class NavelObservationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = NavelObservationAdapter(monotonic_ns=lambda: 1_000_000_000)

    def test_complete_sdk_packet_matches_raw_contract(self):
        payload = self.adapter.convert(perception(person()), locomotion())
        model = RawObservationFrame.model_validate(payload)
        self.assertEqual(set(payload), {"timestamp", "people", "robot", "safety"})
        self.assertEqual(model.timestamp, 1_000_000)
        self.assertEqual(model.people[0].uid, 17)
        self.assertEqual(model.people[0].distance_m, 1.85)
        self.assertEqual(model.people[0].gaze_overlap, 0.75)
        self.assertEqual(model.robot.linear_velocity, 0.2)
        self.assertEqual(model.robot.angular_velocity, -0.1)
        self.assertEqual(model.safety.lidar, [2.0, 1.0])
        self.assertEqual(model.safety.sonar, [0.5, 0.7, 1.2])

    def test_no_people_and_no_locomotion_still_produce_frame(self):
        payload = self.adapter.convert(perception())
        self.assertEqual(payload["people"], [])
        self.assertEqual(payload["robot"], {"linear_velocity": None, "angular_velocity": None})
        self.assertEqual(payload["safety"], {"lidar": None, "sonar": None})
        RawObservationFrame.model_validate(payload)

    def test_invalid_and_duplicate_ids_do_not_become_people(self):
        payload = self.adapter.convert(perception(
            person(1), person(2), person(None), person(True), person(-1), person("3"), person(1),
        ))
        self.assertEqual([p["uid"] for p in payload["people"]], [1, 2])

    def test_missing_person_measurements_are_null_and_position_omitted(self):
        payload = self.adapter.convert(perception(NS(uid=5)))
        self.assertEqual(payload["people"], [{"uid": 5, "distance_m": None, "gaze_overlap": None}])

    def test_face_detection_requires_a_finite_positive_sdk_bounding_box(self):
        for face in ({'x1': 10, 'y1': 20, 'x2': 30, 'y2': 40},
                     NS(x1=10, y1=20, x2=30, y2=40)):
            p = self.adapter.convert(perception(person(face=face)))['people'][0]
            self.assertTrue(RawObservationFrame.model_validate({
                **self.adapter.convert(perception()), 'people': [p]}).people[0].face_detected)
        for face in (None, {}, NS(x1=0, y1=0, x2=0, y2=1),
                     NS(x1=0, y1=0, x2=1, y2=float('nan')),
                     NS(x1=0, y1=0, x2=True, y2=1)):
            p = self.adapter.convert(perception(person(face=face)))['people'][0]
            self.assertNotIn('face_detected', p)

    def test_invalid_measurements_are_unavailable_without_clamping(self):
        for distance, gaze in [(-1, 1.5), (float("nan"), float("inf")), (True, False)]:
            with self.subTest(distance=distance, gaze=gaze):
                p = self.adapter.convert(perception(person(dist_mm=distance, gaze_overlap=gaze)))["people"][0]
                self.assertIsNone(p["distance_m"])
                self.assertIsNone(p["gaze_overlap"])
                json.dumps(p, allow_nan=False)

    def test_cartesian_camera_position_is_used_instead_of_head_angles(self):
        p = self.adapter.convert(perception(person()))["people"][0]
        self.assertEqual(p["optional_relative_head_position"],
                         {"coordinate_frame": "CAM_HEAD", "x": 1.8, "y": 0.3, "z": 0.1})

    def test_other_coordinates_and_invalid_positions_are_not_relabelled(self):
        for positions in [
            [NS(sys=CoordSystem.HEAD_STRAIGHT, x=1, y=2, z=3)],
            [NS(sys=CoordSystem.UNDEFINED, x=1, y=2, z=3)],
            [NS(sys=CoordSystem.CAM_HEAD, x=float("nan"), y=2, z=3)],
        ]:
            with self.subTest(positions=positions):
                p = self.adapter.convert(perception(person(g_head_position=positions)))["people"][0]
                self.assertNotIn("optional_relative_head_position", p)

    def test_partial_velocity_does_not_fabricate_zero_values(self):
        packet = locomotion(odometry=NS(velocity=NS(linear_x=0.0)))
        payload = self.adapter.convert(perception(), packet)
        self.assertEqual(payload["robot"], {"linear_velocity": 0.0, "angular_velocity": None})
        self.assertEqual(payload["safety"]["sonar"], [0.5, 0.7, 1.2])

    def test_bad_ranges_preserve_sensor_slots(self):
        packet = locomotion(distances={Sensor.LIDAR: [float("inf"), -1],
                                      Sensor.SONAR: [None, 0, True]})
        payload = self.adapter.convert(perception(), packet)
        self.assertEqual(payload["safety"], {"lidar": [None, None], "sonar": [None, 0.0, None]})
        RawObservationFrame.model_validate(payload)

    def test_missing_empty_and_string_keyed_ranges_are_distinct(self):
        payload = self.adapter.convert(perception(), locomotion(distances={"lidar": []}))
        self.assertEqual(payload["safety"], {"lidar": [], "sonar": None})


if __name__ == "__main__":
    unittest.main()
