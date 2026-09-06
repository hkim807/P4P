"""HTTP gateway for chat and robot-independent observation processing."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from flask import Flask, jsonify, request
from pydantic import ValidationError

from app.config import Settings
from app.decision.llm_policy import LLMPolicyBridge
from app.decision.scheduler import DecisionScheduler, DecisionSchedulerError
from app.domain.models import ObservationFrame, SocialState
from app.llm import OllamaLLM
from app.state.estimator import TemporalSocialStateError, TemporalSocialStateEstimator


class LLMClient(Protocol):
    provider: str
    model: str
    endpoint: str

    def health(self) -> dict[str, Any]: ...

    def generate(
        self,
        message: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.2,
    ) -> str: ...


@dataclass
class _SourcePipeline:
    estimator: TemporalSocialStateEstimator
    scheduler: DecisionScheduler


@dataclass(frozen=True)
class ObservationPipelineResult:
    state: SocialState
    decision_triggered: bool
    triggers: tuple[str, ...]
    llm_output: str | None
    llm_failed: bool = False


class ObservationPipeline:
    """Keep isolated temporal state for each canonical observation source."""

    def __init__(self, llm: LLMClient) -> None:
        self._policy = LLMPolicyBridge(llm)
        self._sources: dict[str, _SourcePipeline] = {}
        self._lock = RLock()

    def process(
        self, observation: ObservationFrame, *, force_decision: bool = False
    ) -> ObservationPipelineResult:
        source_id = observation.capabilities.adapter_id
        with self._lock:
            source = self._sources.get(source_id)
            if source is None:
                source = _SourcePipeline(
                    estimator=TemporalSocialStateEstimator(),
                    scheduler=DecisionScheduler(),
                )
                self._sources[source_id] = source
            state = source.estimator.update(observation)
            request_to_decide = source.scheduler.evaluate(state)

        triggers = (
            request_to_decide.trigger_codes if request_to_decide is not None else ()
        )
        decision_triggered = request_to_decide is not None or force_decision
        if not decision_triggered:
            return ObservationPipelineResult(
                state=state,
                decision_triggered=False,
                triggers=(),
                llm_output=None,
            )

        try:
            output = self._policy.decide(state, triggers)
        except Exception:
            return ObservationPipelineResult(
                state=state,
                decision_triggered=True,
                triggers=triggers,
                llm_output=None,
                llm_failed=True,
            )
        return ObservationPipelineResult(
            state=state,
            decision_triggered=True,
            triggers=triggers,
            llm_output=output,
        )


def create_app(
    llm: LLMClient | None = None,
    settings: Settings | None = None,
    observation_pipeline: ObservationPipeline | None = None,
) -> Flask:
    """Create the Flask application; injectable arguments keep tests offline."""
    active_settings = settings or Settings()
    active_llm = llm or OllamaLLM(active_settings)
    active_pipeline = observation_pipeline or ObservationPipeline(active_llm)
    flask_app = Flask(__name__)

    @flask_app.get("/")
    def index():
        return jsonify(
            {
                "service": "social-navigation-llm-gateway",
                "provider": active_llm.provider,
                "model": active_llm.model,
                "endpoints": {
                    "health": "GET /health",
                    "chat": "POST /chat",
                    "observations": "POST /api/v1/observations",
                },
            }
        )

    @flask_app.get("/health")
    def health():
        try:
            provider_health = active_llm.health()
            model_available = provider_health.get("model_available", False)
            return (
                jsonify(
                    {
                        "status": "ok" if model_available else "degraded",
                        "provider": active_llm.provider,
                        "model": active_llm.model,
                        "endpoint": active_llm.endpoint,
                        **provider_health,
                    }
                ),
                200 if model_available else 503,
            )
        except Exception as error:
            return (
                jsonify(
                    {
                        "status": "unavailable",
                        "provider": active_llm.provider,
                        "model": active_llm.model,
                        "endpoint": active_llm.endpoint,
                        "error": str(error),
                    }
                ),
                503,
            )

    @flask_app.post("/chat")
    def chat():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON request body is required."}), 400

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return jsonify({"error": "'message' must be a non-empty string."}), 400
        message = message.strip()
        if len(message) > active_settings.max_input_characters:
            return (
                jsonify(
                    {
                        "error": (
                            "'message' exceeds the configured limit of "
                            f"{active_settings.max_input_characters} characters."
                        )
                    }
                ),
                400,
            )

        system_prompt = payload.get("system_prompt")
        if system_prompt is not None and not isinstance(system_prompt, str):
            return jsonify({"error": "'system_prompt' must be a string."}), 400

        temperature = payload.get("temperature", 0.2)
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not 0 <= temperature <= 2
        ):
            return jsonify({"error": "'temperature' must be between 0 and 2."}), 400

        try:
            output = active_llm.generate(
                message,
                system_prompt=system_prompt,
                temperature=float(temperature),
            )
        except Exception as error:
            return (
                jsonify(
                    {
                        "error": "The LLM request failed.",
                        "detail": str(error),
                        "provider": active_llm.provider,
                        "model": active_llm.model,
                    }
                ),
                502,
            )

        return jsonify(
            {
                "provider": active_llm.provider,
                "model": active_llm.model,
                "input": message,
                "output": output,
            }
        )

    @flask_app.post("/api/v1/observations")
    def observations():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return (
                jsonify(
                    {
                        "accepted": False,
                        "error": {
                            "code": "invalid_json",
                            "message": "A canonical ObservationFrame JSON object is required.",
                        },
                    }
                ),
                400,
            )

        try:
            observation = ObservationFrame.model_validate(payload)
        except ValidationError as error:
            return (
                jsonify(
                    {
                        "accepted": False,
                        "error": {
                            "code": "invalid_observation_frame",
                            "message": "The request does not match ObservationFrame schema version 1.0.",
                            "details": error.errors(include_url=False, include_input=False),
                        },
                    }
                ),
                400,
            )

        force_decision = request.args.get("force_decision", "").lower() in {
            "1",
            "true",
            "yes",
        }
        try:
            result = active_pipeline.process(
                observation,
                force_decision=force_decision,
            )
        except (TemporalSocialStateError, DecisionSchedulerError) as error:
            return (
                jsonify(
                    {
                        "accepted": False,
                        "observation_id": observation.observation_id,
                        "error": {
                            "code": "observation_stream_conflict",
                            "message": str(error),
                        },
                    }
                ),
                409,
            )

        response = {
            "accepted": True,
            "observation_id": observation.observation_id,
            "social_state_id": result.state.state_id,
            "decision_triggered": result.decision_triggered,
            "forced_decision": force_decision,
            "triggers": list(result.triggers),
            "llm_output": result.llm_output,
        }
        if result.llm_failed:
            response["error"] = {
                "code": "llm_request_failed",
                "message": "The observation was accepted, but the LLM request failed.",
            }
            return jsonify(response), 502
        return jsonify(response)

    return flask_app


def main() -> None:
    settings = Settings()
    flask_app = create_app(settings=settings)
    flask_app.run(
        host=settings.api_host,
        port=settings.api_port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
