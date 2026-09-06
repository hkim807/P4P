"""Tests for event-driven high-level decision scheduling."""

from __future__ import annotations

import unittest

from app.adapters.synthetic import SyntheticObservationAdapter, museum_guide_scenarios
from app.decision import (
    DecisionScheduler,
    DecisionSchedulerError,
    DecisionTrigger,
    SchedulerConfig,
)
from app.domain.models import ObservationFrame
from app.state import TemporalSocialStateEstimator


class DecisionSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.scenarios = museum_guide_scenarios()

    def states_for(self, scenario_id: str):
        observations = SyntheticObservationAdapter(self.scenarios[scenario_id])
        return list(TemporalSocialStateEstimator().process(observations))

    def test_initial_human_detection_requests_a_decision(self):
        state = self.states_for("newcomer_requests_guidance")[0]

        request = DecisionScheduler().evaluate(state)

        self.assertIsNotNone(request)
        self.assertIn(DecisionTrigger.HUMAN_DETECTED, request.triggers)
        self.assertIs(request.state, state)

    def test_empty_scene_maintains_default_route_without_model_call(self):
        observation = SyntheticObservationAdapter(
            self.scenarios["newcomer_requests_guidance"]
        ).sample_at(0.0).observation
        payload = observation.model_dump(mode="json")
        payload["humans"] = []
        empty_state = TemporalSocialStateEstimator().update(
            ObservationFrame.model_validate(payload)
        )

        request = DecisionScheduler().evaluate(empty_state)

        self.assertIsNone(request)

    def test_new_human_after_empty_scene_requests_a_decision(self):
        adapter = SyntheticObservationAdapter(
            self.scenarios["newcomer_requests_guidance"]
        )
        first_payload = adapter.sample_at(0.0).observation.model_dump(mode="json")
        first_payload["humans"] = []
        observations = [
            ObservationFrame.model_validate(first_payload),
            adapter.sample_at(0.1).observation,
        ]
        states = list(TemporalSocialStateEstimator().process(observations))
        scheduler = DecisionScheduler()

        self.assertIsNone(scheduler.evaluate(states[0]))
        request = scheduler.evaluate(states[1])

        self.assertIn(DecisionTrigger.HUMAN_DETECTED, request.triggers)

    def test_events_are_coalesced_until_the_minimum_interval(self):
        states = self.states_for("newcomer_follows_guide")
        scheduler = DecisionScheduler(
            SchedulerConfig(
                minimum_decision_interval_s=0.5,
                active_refresh_interval_s=2.0,
            )
        )

        first = scheduler.evaluate(states[0])
        immediate = [scheduler.evaluate(state) for state in states[1:5]]
        coalesced = scheduler.evaluate(states[5])

        self.assertIsNotNone(first)
        self.assertTrue(all(request is None for request in immediate))
        self.assertIsNotNone(coalesced)
        self.assertTrue(
            set(coalesced.triggers)
            & {
                DecisionTrigger.MOTION_CHANGED,
                DecisionTrigger.ATTENTION_CHANGED,
            }
        )

    def test_active_scene_receives_periodic_refresh(self):
        states = self.states_for("newcomer_follows_guide")
        scheduler = DecisionScheduler(
            SchedulerConfig(
                minimum_decision_interval_s=0.1,
                active_refresh_interval_s=0.5,
            )
        )

        requests = [
            request
            for state in states[:31]
            if (request := scheduler.evaluate(state)) is not None
        ]

        self.assertTrue(
            any(
                DecisionTrigger.PERIODIC_REFRESH in request.triggers
                for request in requests
            )
        )

    def test_speech_start_is_an_event(self):
        scheduler = DecisionScheduler()
        requests = [
            request
            for state in self.states_for("newcomer_requests_guidance")
            if (request := scheduler.evaluate(state)) is not None
        ]

        self.assertTrue(
            any(
                DecisionTrigger.HUMAN_STARTED_SPEAKING in request.triggers
                for request in requests
            )
        )

    def test_occlusion_and_reacquisition_are_events(self):
        scheduler = DecisionScheduler()
        requests = [
            request
            for state in self.states_for("newcomer_occluded_by_exhibit")
            if (request := scheduler.evaluate(state)) is not None
        ]
        triggers = {
            trigger for request in requests for trigger in request.triggers
        }

        self.assertIn(DecisionTrigger.TRACK_OCCLUDED, triggers)
        self.assertIn(DecisionTrigger.TRACK_REACQUIRED, triggers)

    def test_expired_human_track_requests_departure_decision(self):
        adapter = SyntheticObservationAdapter(
            self.scenarios["newcomer_requests_guidance"]
        )
        first = adapter.sample_at(0.0).observation
        empty_payload = adapter.sample_at(2.1).observation.model_dump(mode="json")
        empty_payload["humans"] = []
        observations = [first, ObservationFrame.model_validate(empty_payload)]
        states = list(TemporalSocialStateEstimator().process(observations))
        scheduler = DecisionScheduler()
        scheduler.evaluate(states[0])

        request = scheduler.evaluate(states[1])

        self.assertIn(DecisionTrigger.HUMAN_LEFT, request.triggers)

    def test_scheduler_does_not_invoke_at_observation_rate(self):
        for scenario_id in self.scenarios:
            with self.subTest(scenario=scenario_id):
                states = self.states_for(scenario_id)
                scheduler = DecisionScheduler()
                requests = [
                    request
                    for state in states
                    if (request := scheduler.evaluate(state)) is not None
                ]
                self.assertGreater(len(requests), 0)
                self.assertLess(len(requests), len(states) / 2)

    def test_rejects_non_monotonic_states(self):
        state = self.states_for("newcomer_requests_guidance")[0]
        scheduler = DecisionScheduler()
        scheduler.evaluate(state)

        with self.assertRaisesRegex(DecisionSchedulerError, "must be greater"):
            scheduler.evaluate(state)

    def test_reset_allows_another_experiment(self):
        state = self.states_for("newcomer_requests_guidance")[0]
        scheduler = DecisionScheduler()
        first = scheduler.evaluate(state)
        scheduler.reset()
        second = scheduler.evaluate(state)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
