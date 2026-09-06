"""Tests for deterministic MVP temporal social-state estimation."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.adapters.jsonl import JsonlObservationIterator
from app.adapters.synthetic import SyntheticObservationAdapter, museum_guide_scenarios
from app.domain.models import ObservationFrame, SocialState
from app.state import TemporalSocialStateError, TemporalSocialStateEstimator


class TemporalSocialStateEstimatorTests(unittest.TestCase):
    def setUp(self):
        self.scenarios = museum_guide_scenarios()

    def states_for(self, scenario_id: str):
        observations = SyntheticObservationAdapter(self.scenarios[scenario_id])
        return list(TemporalSocialStateEstimator().process(observations))

    def test_all_scenarios_produce_contract_valid_state_for_every_frame(self):
        for scenario_id, scenario in self.scenarios.items():
            with self.subTest(scenario=scenario_id):
                observations = list(SyntheticObservationAdapter(scenario))
                states = list(
                    TemporalSocialStateEstimator().process(observations)
                )
                self.assertEqual(
                    len(states), SyntheticObservationAdapter(scenario).frame_count
                )
                for observation, state in zip(observations, states):
                    SocialState.model_validate(state.model_dump(mode="json"))
                    self.assertEqual(
                        state.source_observation_id,
                        observation.observation_id,
                    )

    def test_requester_becomes_sustained_and_speaking(self):
        states = self.states_for("newcomer_requests_guidance")
        state = states[70].humans[0]

        self.assertEqual(state.attention.state, "SUSTAINED")
        self.assertEqual(state.engagement.state, "HUMAN_SPEAKING")
        self.assertGreater(state.engagement.engagement_probability, 0.75)
        self.assertEqual(state.proxemic_zone, "SOCIAL")

    def test_non_engaging_passerby_remains_not_attending(self):
        states = self.states_for("newcomer_passes_without_engaging")
        state = states[30].humans[0]

        self.assertEqual(state.attention.state, "NOT_ATTENDING")
        self.assertEqual(state.engagement.state, "DISENGAGED")

    def test_normal_follower_is_parallel_and_distance_is_stable(self):
        states = self.states_for("newcomer_follows_guide")
        state = states[80].humans[0]

        self.assertEqual(state.motion_relation, "PARALLEL")
        self.assertEqual(state.distance_trend, "STABLE")
        self.assertAlmostEqual(state.velocity_robot_mps.x, 0.5, places=6)

    def test_follower_that_stops_is_receding_from_robot(self):
        states = self.states_for("newcomer_falls_behind")
        state = states[80].humans[0]

        self.assertEqual(state.motion_relation, "RECEDING")
        self.assertEqual(state.distance_trend, "INCREASING")
        self.assertLess(state.closing_speed_mps, 0.0)

    def test_crossing_motion_is_classified_from_temporal_positions(self):
        states = self.states_for("newcomer_crosses_toward_exhibit")
        relations = {state.humans[0].motion_relation for state in states[10:50]}

        self.assertTrue(relations & {"CROSSING", "APPROACHING_CROSSING"})

    def test_occluded_track_is_predicted_then_reacquired(self):
        states = self.states_for("newcomer_occluded_by_exhibit")
        occluded = states[30].humans[0]
        reacquired = states[40].humans[0]

        self.assertFalse(occluded.observed)
        self.assertTrue(occluded.predicted_only)
        self.assertGreater(occluded.time_since_seen_s, 0.0)
        self.assertGreater(occluded.uncertainty.position_std_m, 0.0)
        self.assertEqual(occluded.attention.state, "UNKNOWN")
        self.assertTrue(reacquired.observed)
        self.assertFalse(reacquired.predicted_only)

    def test_pair_populates_basic_crowd_counts(self):
        states = self.states_for("newcomer_pair_requests_guidance")
        crowd = states[70].crowd

        self.assertEqual(crowd.people_within_1m, 0)
        self.assertEqual(crowd.people_within_2m, 0)
        self.assertEqual(crowd.people_within_3m, 2)
        self.assertGreater(crowd.density_people_m2, 0.0)

    def test_missing_gaze_remains_unknown(self):
        original = SyntheticObservationAdapter(
            self.scenarios["newcomer_requests_guidance"]
        ).sample_at(0.0).observation
        payload = original.model_dump(mode="json")
        payload["humans"][0]["gaze_to_robot_score"] = None
        observation = ObservationFrame.model_validate(payload)

        state = TemporalSocialStateEstimator().update(observation).humans[0]

        self.assertEqual(state.attention.state, "UNKNOWN")
        self.assertIsNone(state.attention.gaze_mean_500ms)
        self.assertEqual(state.engagement.state, "AVAILABLE")

    def test_single_high_gaze_reading_is_a_glance_not_sustained(self):
        original = SyntheticObservationAdapter(
            self.scenarios["newcomer_requests_guidance"]
        ).sample_at(0.0).observation
        payload = original.model_dump(mode="json")
        payload["humans"][0]["gaze_to_robot_score"] = 0.95
        observation = ObservationFrame.model_validate(payload)

        state = TemporalSocialStateEstimator().update(observation).humans[0]

        self.assertEqual(state.attention.state, "GLANCE")

    def test_checked_in_jsonl_flows_through_the_estimator(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "recordings/synthetic/newcomer_requests_guidance.jsonl"
        )

        states = list(
            TemporalSocialStateEstimator().process(JsonlObservationIterator(path))
        )

        self.assertEqual(len(states), 81)
        self.assertEqual(states[-1].source_observation_id, "newcomer_requests_guidance:00080")

    def test_rejects_non_monotonic_input(self):
        observation = SyntheticObservationAdapter(
            self.scenarios["newcomer_requests_guidance"]
        ).sample_at(0.0).observation
        estimator = TemporalSocialStateEstimator()
        estimator.update(observation)

        with self.assertRaisesRegex(TemporalSocialStateError, "must be greater"):
            estimator.update(observation)

    def test_reset_allows_a_new_recording(self):
        observation = SyntheticObservationAdapter(
            self.scenarios["newcomer_requests_guidance"]
        ).sample_at(0.0).observation
        estimator = TemporalSocialStateEstimator()
        first = estimator.update(observation)
        estimator.reset()
        second = estimator.update(observation)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
