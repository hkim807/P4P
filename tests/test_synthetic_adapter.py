"""Tests for guide-specific deterministic synthetic observations."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.adapters.synthetic import (
    NoiseProfile,
    SyntheticObservationAdapter,
    museum_guide_scenarios,
)
from app.domain.models import ObservationFrame


class SyntheticScenarioCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.scenarios = museum_guide_scenarios()

    def test_catalogue_covers_core_guide_robot_encounters(self):
        self.assertEqual(
            set(self.scenarios),
            {
                "newcomer_requests_guidance",
                "newcomer_passes_without_engaging",
                "newcomer_crosses_toward_exhibit",
                "newcomer_follows_guide",
                "newcomer_falls_behind",
                "newcomer_occluded_by_exhibit",
                "newcomer_pair_requests_guidance",
            },
        )

    def test_all_scenarios_emit_contract_valid_monotonic_frames(self):
        for scenario_id, scenario in self.scenarios.items():
            with self.subTest(scenario=scenario_id):
                adapter = SyntheticObservationAdapter(scenario)
                frames = list(adapter)
                self.assertEqual(len(frames), adapter.frame_count)
                self.assertEqual(frames[0].timestamp_us, 0)
                self.assertEqual(
                    frames[-1].timestamp_us,
                    round(scenario.duration_s * 1_000_000),
                )
                self.assertTrue(
                    all(
                        right.timestamp_us > left.timestamp_us
                        for left, right in zip(frames, frames[1:])
                    )
                )
                for frame in frames:
                    ObservationFrame.model_validate(frame.model_dump(mode="json"))

    def test_requesting_newcomer_approaches_attends_and_speaks(self):
        adapter = SyntheticObservationAdapter(
            self.scenarios["newcomer_requests_guidance"]
        )
        initial = adapter.sample_at(0.0).observation.humans[0]
        final = adapter.sample_at(7.0).observation.humans[0]
        self.assertGreater(initial.distance_m, final.distance_m)
        self.assertLess(initial.gaze_to_robot_score, final.gaze_to_robot_score)
        self.assertGreater(final.speech_activity, 0.5)

    def test_crossing_newcomer_intersects_the_robot_route(self):
        adapter = SyntheticObservationAdapter(
            self.scenarios["newcomer_crosses_toward_exhibit"]
        )
        sample = adapter.sample_at(3.0)
        person = sample.observation.humans[0]
        self.assertAlmostEqual(person.position_robot_m.x, 0.5)
        self.assertAlmostEqual(person.position_robot_m.y, 0.0)
        self.assertAlmostEqual(sample.observation.robot.linear_velocity_mps.x, 0.5)

    def test_following_distance_is_stable_in_normal_guidance(self):
        adapter = SyntheticObservationAdapter(
            self.scenarios["newcomer_follows_guide"]
        )
        start = adapter.sample_at(0.0).observation.humans[0].position_robot_m.x
        end = adapter.sample_at(10.0).observation.humans[0].position_robot_m.x
        self.assertAlmostEqual(start, -1.5)
        self.assertAlmostEqual(end, -1.5)

    def test_falling_behind_increases_robot_relative_distance(self):
        adapter = SyntheticObservationAdapter(
            self.scenarios["newcomer_falls_behind"]
        )
        start = adapter.sample_at(0.0).observation.humans[0].distance_m
        end = adapter.sample_at(10.0).observation.humans[0].distance_m
        self.assertGreater(end, start + 2.5)

    def test_exhibit_occlusion_removes_only_the_observation(self):
        adapter = SyntheticObservationAdapter(
            self.scenarios["newcomer_occluded_by_exhibit"]
        )
        occluded = adapter.sample_at(3.0)
        reappeared = adapter.sample_at(4.0)
        self.assertEqual(occluded.observation.humans, [])
        self.assertFalse(occluded.ground_truth.humans[0].visible)
        self.assertEqual(reappeared.observation.humans[0].track_id, "visitor-1")
        self.assertTrue(reappeared.ground_truth.humans[0].visible)

    def test_group_membership_is_ground_truth_not_an_observed_answer(self):
        adapter = SyntheticObservationAdapter(
            self.scenarios["newcomer_pair_requests_guidance"]
        )
        sample = adapter.sample_at(6.5)
        self.assertEqual(len(sample.observation.humans), 2)
        self.assertTrue(
            all(human.group_observation_id is None for human in sample.observation.humans)
        )
        self.assertEqual(
            {human.group_id for human in sample.ground_truth.humans},
            {"visitor-pair-1"},
        )


class SyntheticAdapterDeterminismTests(unittest.TestCase):
    def setUp(self):
        self.scenario = museum_guide_scenarios()["newcomer_requests_guidance"]

    def test_noise_and_dropout_are_repeatable_for_a_seed(self):
        noise = NoiseProfile(
            position_std_m=0.05,
            gaze_std=0.03,
            dropout_probability=0.1,
        )
        first = [
            frame.model_dump_json()
            for frame in SyntheticObservationAdapter(
                self.scenario, noise=noise, seed=42
            )
        ]
        second = [
            frame.model_dump_json()
            for frame in SyntheticObservationAdapter(
                self.scenario, noise=noise, seed=42
            )
        ]
        self.assertEqual(first, second)

    def test_dropout_does_not_change_physical_visibility_ground_truth(self):
        adapter = SyntheticObservationAdapter(
            self.scenario,
            noise=NoiseProfile(dropout_probability=1.0),
        )
        sample = adapter.sample_at(2.0)
        self.assertEqual(sample.observation.humans, [])
        self.assertTrue(sample.ground_truth.humans[0].visible)

    def test_jsonl_export_is_replayable(self):
        adapter = SyntheticObservationAdapter(self.scenario)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "scenario.jsonl"
            count = adapter.write_jsonl(output)
            records = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(count, adapter.frame_count)
        self.assertEqual(len(records), adapter.frame_count)
        self.assertIn("ground_truth", records[0])
        ObservationFrame.model_validate(records[0]["observation"])


if __name__ == "__main__":
    unittest.main()
