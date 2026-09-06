"""Offline endpoint tests for canonical observation-to-LLM processing."""

from __future__ import annotations

import unittest

from app.adapters.synthetic import SyntheticObservationAdapter, museum_guide_scenarios
from app.decision.llm_policy import render_decision_prompt
from app.domain.models import ObservationFrame
from app.server import create_app
from app.state import TemporalSocialStateEstimator


class FakeLLM:
    provider = "ollama"
    model = "test-model"
    endpoint = "http://127.0.0.1:11434/v1"

    def __init__(self) -> None:
        self.calls = []
        self.fail = False

    def health(self):
        return {"reachable": True, "model_available": True, "available_models": [self.model]}

    def generate(self, message, *, system_prompt=None, temperature=0.2):
        self.calls.append((message, system_prompt, temperature))
        if self.fail:
            raise RuntimeError("simulated failure")
        return "Maintain distance and monitor the visitor."


def observation_payload(*, adapter_id="source-a", timestamp_us=1_000_000, humans=True):
    scenario = museum_guide_scenarios()["newcomer_requests_guidance"]
    payload = SyntheticObservationAdapter(scenario).sample_at(0.0).observation.model_dump(mode="json")
    payload["observation_id"] = f"{adapter_id}:{timestamp_us}"
    payload["timestamp_us"] = timestamp_us
    payload["capabilities"]["adapter_id"] = adapter_id
    for human in payload["humans"]:
        human["source_timestamp_us"] = timestamp_us
    if not humans:
        payload["humans"] = []
    return payload


class ObservationEndpointTests(unittest.TestCase):
    def setUp(self):
        self.llm = FakeLLM()
        self.client = create_app(llm=self.llm).test_client()

    def test_valid_frame_is_accepted_and_estimated(self):
        response = self.client.post(
            "/api/v1/observations", json=observation_payload(humans=False)
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["accepted"])
        self.assertTrue(body["social_state_id"].startswith("state-"))

    def test_invalid_contract_returns_400(self):
        response = self.client.post("/api/v1/observations", json={"schema_version": "1.0"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["accepted"])

    def test_malformed_json_returns_400(self):
        response = self.client.post(
            "/api/v1/observations",
            data="{not-json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_json")

    def test_unscheduled_state_does_not_call_llm(self):
        response = self.client.post(
            "/api/v1/observations", json=observation_payload(humans=False)
        )
        self.assertFalse(response.get_json()["decision_triggered"])
        self.assertEqual(self.llm.calls, [])

    def test_scheduler_trigger_calls_llm_once_and_returns_raw_text(self):
        response = self.client.post(
            "/api/v1/observations", json=observation_payload(humans=True)
        )
        body = response.get_json()
        self.assertEqual(len(self.llm.calls), 1)
        self.assertTrue(body["decision_triggered"])
        self.assertIn("HUMAN_DETECTED", body["triggers"])
        self.assertEqual(body["llm_output"], "Maintain distance and monitor the visitor.")

    def test_force_decision_calls_llm_once_without_inventing_scheduler_trigger(self):
        response = self.client.post(
            "/api/v1/observations?force_decision=1",
            json=observation_payload(humans=False),
        )
        body = response.get_json()
        self.assertTrue(body["decision_triggered"])
        self.assertTrue(body["forced_decision"])
        self.assertEqual(body["triggers"], [])
        self.assertEqual(len(self.llm.calls), 1)

    def test_llm_failure_preserves_observation_acceptance_and_state(self):
        self.llm.fail = True
        failed = self.client.post(
            "/api/v1/observations", json=observation_payload(timestamp_us=1_000_000)
        )
        self.assertEqual(failed.status_code, 502)
        self.assertTrue(failed.get_json()["accepted"])
        self.llm.fail = False
        later = self.client.post(
            "/api/v1/observations",
            json=observation_payload(timestamp_us=1_100_000),
        )
        self.assertEqual(later.status_code, 200)
        self.assertTrue(later.get_json()["accepted"])

    def test_state_is_isolated_between_adapter_ids(self):
        first = self.client.post(
            "/api/v1/observations",
            json=observation_payload(adapter_id="source-a", timestamp_us=2_000_000, humans=False),
        )
        second = self.client.post(
            "/api/v1/observations",
            json=observation_payload(adapter_id="source-b", timestamp_us=1_000_000, humans=False),
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

    def test_out_of_order_frame_returns_explicit_conflict(self):
        self.client.post(
            "/api/v1/observations",
            json=observation_payload(timestamp_us=2_000_000, humans=False),
        )
        response = self.client.post(
            "/api/v1/observations",
            json=observation_payload(timestamp_us=1_000_000, humans=False),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "observation_stream_conflict")

    def test_prompt_is_deterministic_compact_and_excludes_nulls(self):
        observation = ObservationFrame.model_validate(observation_payload())
        state = TemporalSocialStateEstimator().update(observation)
        first = render_decision_prompt(state, ["HUMAN_DETECTED"])
        second = render_decision_prompt(state, ["HUMAN_DETECTED"])
        self.assertEqual(first, second)
        self.assertNotIn(": null", first)
        compact_json = first.split("Input JSON: ", 1)[1]
        self.assertNotIn(": ", compact_json)
        self.assertIn('"scheduler_triggers":["HUMAN_DETECTED"]', first)


if __name__ == "__main__":
    unittest.main()
