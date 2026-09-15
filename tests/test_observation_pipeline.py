"""Offline endpoint tests for canonical observation-to-LLM processing."""

from __future__ import annotations

import json
import unittest

from app.adapters.synthetic import SyntheticObservationAdapter, museum_guide_scenarios
from app.config import DecisionMode, Settings
from app.decision.llm_policy import render_decision_prompt
from app.domain.models import Action, ObservationFrame
from app.llm import CapturedLLMRequest, LLMGenerationResult, LLMRequestError
from app.server import create_app
from app.state import TemporalSocialStateEstimator


class FakeLLM:
    provider = "ollama"
    model = "test-model"
    endpoint = "http://127.0.0.1:11434/v1"

    def __init__(self) -> None:
        self.calls = []
        self.fail = False
        self.response_override = None

    def health(self):
        return {"reachable": True, "model_available": True, "available_models": [self.model]}

    def generate(
        self,
        message,
        *,
        system_prompt=None,
        temperature=0.2,
        response_schema=None,
    ):
        self.calls.append((message, system_prompt, temperature, response_schema))
        if self.fail:
            raise RuntimeError("simulated failure")
        if self.response_override is not None:
            return self.response_override
        return json.dumps(
            {
                "action": "MONITOR",
                "target_human_id": None,
                "preferences": {},
                "valid_for_ms": 1_000,
                "reason_codes": ["INSUFFICIENT_EVIDENCE"],
                "decision_confidence": 0.55,
            }
        )


class TraceableFakeLLM(FakeLLM):
    def generate_with_capture(
        self,
        message,
        *,
        system_prompt=None,
        temperature=0.2,
        response_schema=None,
        response_schema_name="social_navigation_behavior_selection",
    ):
        request = CapturedLLMRequest(
            provider=self.provider,
            model=self.model,
            endpoint=self.endpoint,
            requested_at="2026-09-15T00:00:00.000+00:00",
            messages=(
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ),
            temperature=temperature,
            response_schema_name=response_schema_name,
            response_schema=response_schema,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            },
        )
        self.calls.append(request)
        if self.fail:
            raise LLMRequestError(
                "simulated failure",
                request=request,
                failed_at="2026-09-15T00:00:00.025+00:00",
                latency_ms=25.0,
            )
        if self.response_override is not None:
            raw_response = self.response_override
        else:
            prompt_payload = json.loads(message.split("Input JSON: ", 1)[1])
            state = prompt_payload["social_state"]
            if state["humans"]:
                robot_inputs = [
                    {
                        "source": "/humans/0/distance_m",
                        "latest_value": state["humans"][0]["distance_m"],
                        "interpretation": "Current measured separation.",
                    }
                ]
                evidence = [
                    {
                        "type": "OBSERVATION",
                        "description": "The visitor is observed.",
                        "source_fields": ["/humans/0/observed"],
                    }
                ]
                social_summary = "One observed visitor is available."
                reason_codes = ["HUMAN_DETECTED"]
                uncertainties = ["The visitor's intent is unknown."]
            else:
                robot_inputs = [
                    {
                        "source": "/crowd/people_within_3m",
                        "latest_value": 0,
                        "interpretation": "No people are counted nearby.",
                    }
                ]
                evidence = [
                    {
                        "type": "OBSERVATION",
                        "description": "The nearby people count is zero.",
                        "source_fields": ["/crowd/people_within_3m"],
                    }
                ]
                social_summary = "No human is currently observed."
                reason_codes = ["INSUFFICIENT_EVIDENCE"]
                uncertainties = ["No human observation is available."]
            raw_response = "  " + json.dumps(
                {
                    "social_summary": social_summary,
                    "robot_inputs": robot_inputs,
                    "evidence": evidence,
                    "recommended_action": "MONITOR",
                    "target_human_id": None,
                    "preferences": {},
                    "valid_for_ms": 1_000,
                    "reason_codes": reason_codes,
                    "decision_confidence": 0.55,
                    "decision_rationale": "Monitor while evidence remains limited.",
                    "action_scores": [
                        {
                            "action": action.value,
                            "score": 0.23 if action == Action.MONITOR else 0.07,
                            "reason": "Best supported." if action == Action.MONITOR else "Less supported.",
                        }
                        for action in Action
                    ],
                    "uncertainties": uncertainties,
                },
                separators=(",", ":"),
            ) + "\n"
        return LLMGenerationResult(
            request=request,
            raw_content=raw_response,
            responded_at="2026-09-15T00:00:00.025+00:00",
            latency_ms=25.0,
            response_id="debug-response-1",
            returned_model="test-model",
            created=123,
            finish_reason="stop",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        )


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

    def test_scheduler_trigger_returns_validated_behavior_intent(self):
        response = self.client.post(
            "/api/v1/observations", json=observation_payload(humans=True)
        )
        body = response.get_json()
        self.assertEqual(len(self.llm.calls), 1)
        self.assertTrue(body["decision_triggered"])
        self.assertIn("HUMAN_DETECTED", body["triggers"])
        intent = body["behavior_intent"]
        self.assertEqual(intent["action"], "MONITOR")
        self.assertEqual(intent["observation_id"], body["observation_id"])
        self.assertEqual(intent["social_state_id"], body["social_state_id"])
        self.assertTrue(intent["decision_id"].startswith("decision-"))
        _, system_prompt, temperature, response_schema = self.llm.calls[0]
        self.assertIn("Decision priority", system_prompt)
        self.assertEqual(temperature, 0.0)
        self.assertEqual(
            response_schema["properties"]["target_human_id"]["anyOf"][0]["enum"],
            ["visitor-1"],
        )
        self.assertNotIn("debug_snapshot", body)

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
        self.assertEqual(body["behavior_intent"]["action"], "MONITOR")

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

    def test_invalid_llm_selection_is_rejected_without_losing_state(self):
        self.llm.response_override = "not JSON"
        response = self.client.post(
            "/api/v1/observations", json=observation_payload(timestamp_us=1_000_000)
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 502)
        self.assertTrue(body["accepted"])
        self.assertIsNone(body["behavior_intent"])
        self.assertEqual(body["error"]["code"], "invalid_llm_behavior_selection")

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
        self.assertIn('"policy_prompt_version":"llm-social-navigation-v2"', first)
        self.assertIn('"response_json_schema"', first)


class DebugObservationEndpointTests(unittest.TestCase):
    def setUp(self):
        self.llm = TraceableFakeLLM()
        settings = Settings(decision_mode=DecisionMode.DEBUG)
        self.client = create_app(llm=self.llm, settings=settings).test_client()

    def test_debug_decision_returns_one_atomic_request_snapshot(self):
        response = self.client.post(
            "/api/v1/observations",
            json=observation_payload(humans=True),
        )
        body = response.get_json()

        self.assertEqual(response.status_code, 200)
        snapshot = body["debug_snapshot"]
        self.assertEqual(snapshot["status"], "COMPLETED")
        self.assertTrue(snapshot["request_id"].startswith("debug-request-"))
        self.assertEqual(snapshot["source_id"], "source-a")
        self.assertEqual(snapshot["observation_id"], body["observation_id"])
        self.assertEqual(snapshot["social_state_id"], body["social_state_id"])
        self.assertEqual(snapshot["behavior_intent"], body["behavior_intent"])
        self.assertEqual(snapshot["validated_response"]["recommended_action"], "MONITOR")
        self.assertEqual(len(snapshot["validated_response"]["action_scores"]), len(Action))
        self.assertTrue(snapshot["raw_response"].startswith("  {"))
        self.assertTrue(snapshot["raw_response"].endswith("\n"))

        captured = self.llm.calls[0]
        self.assertEqual(snapshot["rendered_messages"], list(captured.messages))
        prompt_payload = json.loads(
            snapshot["rendered_messages"][1]["content"].split("Input JSON: ", 1)[1]
        )
        self.assertEqual(prompt_payload["social_state"], snapshot["raw_social_state"])
        self.assertEqual(
            snapshot["request_parameters"]["response_schema_name"],
            "social_navigation_debug_decision",
        )
        self.assertTrue(
            snapshot["request_parameters"]["response_format"]["json_schema"][
                "strict"
            ]
        )
        self.assertEqual(snapshot["metadata"]["decision_mode"], "DEBUG")
        self.assertEqual(snapshot["metadata"]["prompt_version"], "llm-social-navigation-debug-v1")
        self.assertEqual(snapshot["metadata"]["latency_ms"], 25.0)
        self.assertEqual(snapshot["metadata"]["usage"]["total_tokens"], 150)
        self.assertIsNone(snapshot["error"])

    def test_invalid_debug_response_retains_raw_response_and_error(self):
        self.llm.response_override = "not JSON"

        response = self.client.post(
            "/api/v1/observations",
            json=observation_payload(humans=True),
        )
        body = response.get_json()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(body["error"]["code"], "invalid_llm_debug_response")
        self.assertIsNone(body["behavior_intent"])
        snapshot = body["debug_snapshot"]
        self.assertEqual(snapshot["status"], "FAILED")
        self.assertEqual(snapshot["raw_response"], "not JSON")
        self.assertIsNone(snapshot["validated_response"])
        self.assertEqual(snapshot["error"]["code"], "invalid_llm_debug_response")
        self.assertEqual(len(snapshot["rendered_messages"]), 2)

    def test_debug_transport_failure_retains_request_and_timing(self):
        self.llm.fail = True

        response = self.client.post(
            "/api/v1/observations",
            json=observation_payload(humans=True),
        )
        body = response.get_json()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(body["error"]["code"], "llm_request_failed")
        snapshot = body["debug_snapshot"]
        self.assertEqual(snapshot["status"], "FAILED")
        self.assertIsNone(snapshot["raw_response"])
        self.assertEqual(snapshot["error"]["message"], "simulated failure")
        self.assertEqual(snapshot["metadata"]["latency_ms"], 25.0)
        self.assertIn("Input JSON: ", snapshot["rendered_messages"][1]["content"])

    def test_forced_no_person_debug_decision_preserves_unknown_state(self):
        response = self.client.post(
            "/api/v1/observations?force_decision=1",
            json=observation_payload(humans=False),
        )
        body = response.get_json()

        self.assertEqual(response.status_code, 200)
        snapshot = body["debug_snapshot"]
        self.assertEqual(snapshot["raw_social_state"]["humans"], [])
        self.assertEqual(
            snapshot["validated_response"]["uncertainties"],
            ["No human observation is available."],
        )
        self.assertEqual(body["behavior_intent"]["action"], "MONITOR")

    def test_debug_requests_are_isolated_between_sources(self):
        first = self.client.post(
            "/api/v1/observations",
            json=observation_payload(adapter_id="source-a", humans=True),
        ).get_json()["debug_snapshot"]
        second = self.client.post(
            "/api/v1/observations",
            json=observation_payload(adapter_id="source-b", humans=True),
        ).get_json()["debug_snapshot"]

        self.assertEqual(first["source_id"], "source-a")
        self.assertEqual(second["source_id"], "source-b")
        self.assertNotEqual(first["request_id"], second["request_id"])
        self.assertNotEqual(first["social_state_id"], second["social_state_id"])

    def test_monitor_retrieves_the_same_snapshot_after_a_skipped_cycle(self):
        first_response = self.client.post(
            "/api/v1/observations",
            json=observation_payload(humans=True),
        )
        first = first_response.get_json()["debug_snapshot"]

        skipped_response = self.client.post(
            "/api/v1/observations",
            json=observation_payload(timestamp_us=1_100_000, humans=True),
        )
        skipped = skipped_response.get_json()
        retained_response = self.client.get(
            "/api/v1/monitor/sources/source-a/debug-snapshot"
        )
        retained = retained_response.get_json()

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(skipped_response.status_code, 200)
        self.assertFalse(skipped["decision_triggered"])
        self.assertNotIn("debug_snapshot", skipped)
        self.assertEqual(retained_response.status_code, 200)
        self.assertEqual(retained["revision"], 1)
        self.assertEqual(retained["snapshot"], first)


class DecisionModeAcceptanceTests(unittest.TestCase):
    def test_normal_and_debug_modes_share_state_but_keep_contracts_separate(self):
        payload = observation_payload(humans=True)
        normal_llm = FakeLLM()
        debug_llm = TraceableFakeLLM()
        normal_client = create_app(
            llm=normal_llm,
            settings=Settings(decision_mode=DecisionMode.NORMAL),
        ).test_client()
        debug_client = create_app(
            llm=debug_llm,
            settings=Settings(decision_mode=DecisionMode.DEBUG),
        ).test_client()

        normal_response = normal_client.post("/api/v1/observations", json=payload)
        debug_response = debug_client.post("/api/v1/observations", json=payload)
        normal = normal_response.get_json()
        debug = debug_response.get_json()

        self.assertEqual(normal_response.status_code, 200)
        self.assertEqual(debug_response.status_code, 200)
        self.assertEqual(normal["observation_id"], debug["observation_id"])
        self.assertEqual(normal["social_state_id"], debug["social_state_id"])
        self.assertEqual(
            normal["behavior_intent"]["action"],
            debug["behavior_intent"]["action"],
        )
        self.assertNotIn("debug_snapshot", normal)
        self.assertEqual(debug["debug_snapshot"]["status"], "COMPLETED")
        self.assertEqual(
            debug["debug_snapshot"]["raw_social_state"]["state_id"],
            debug["social_state_id"],
        )
        self.assertNotIn("Debug-mode evidence rules", normal_llm.calls[0][1])
        self.assertIn(
            "Debug-mode evidence rules",
            debug_llm.calls[0].messages[0]["content"],
        )


if __name__ == "__main__":
    unittest.main()
