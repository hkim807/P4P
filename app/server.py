"""HTTP gateway for chat and robot-independent observation processing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, Callable, Protocol

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from pydantic import ValidationError

from app.config import DecisionMode, Settings
from app.decision.debug_policy import DebugDecisionSnapshot, DebugPolicyBridge
from app.decision.llm_policy import LLMPolicyBridge, LLMPolicyError
from app.decision.scheduler import DecisionScheduler, DecisionSchedulerError
from app.domain.models import BehaviorIntent, ObservationFrame, SocialState
from app.llm import OllamaLLM
from app.monitor import DeterministicReplayLLM, MonitorService
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
        response_schema: dict[str, Any] | None = None,
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
    behavior_intent: BehaviorIntent | None
    error_code: str | None = None
    decision_mode: DecisionMode = DecisionMode.NORMAL
    debug_snapshot: DebugDecisionSnapshot | None = None


class ObservationPipeline:
    """Keep isolated temporal state for each canonical observation source."""

    def __init__(
        self,
        llm: LLMClient,
        decision_mode: DecisionMode = DecisionMode.NORMAL,
    ) -> None:
        self.decision_mode = DecisionMode(decision_mode)
        self._policy = LLMPolicyBridge(llm)
        self._debug_policy = (
            DebugPolicyBridge(llm)
            if self.decision_mode == DecisionMode.DEBUG
            else None
        )
        self._sources: dict[str, _SourcePipeline] = {}
        self._lock = RLock()

    def process(
        self,
        observation: ObservationFrame,
        *,
        force_decision: bool = False,
        trace: Callable[[dict[str, Any]], None] | None = None,
    ) -> ObservationPipelineResult:
        def emit(
            stage: str,
            status: str,
            *,
            started_at: float,
            payload: dict[str, Any] | None = None,
            error: str | None = None,
        ) -> None:
            if trace is None:
                return
            trace(
                {
                    "stage": stage,
                    "status": status,
                    "duration_ms": round((perf_counter() - started_at) * 1_000, 3),
                    "payload": payload,
                    "error": error,
                }
            )

        source_id = observation.capabilities.adapter_id
        with self._lock:
            source = self._sources.get(source_id)
            if source is None:
                source = _SourcePipeline(
                    estimator=TemporalSocialStateEstimator(),
                    scheduler=DecisionScheduler(),
                )
                self._sources[source_id] = source
            state_started = perf_counter()
            try:
                state = source.estimator.update(observation)
            except Exception as error:
                emit("state", "failed", started_at=state_started, error=str(error))
                raise
            emit(
                "state",
                "completed",
                started_at=state_started,
                payload=state.model_dump(mode="json"),
            )

            scheduler_started = perf_counter()
            try:
                request_to_decide = source.scheduler.evaluate(state)
            except Exception as error:
                emit(
                    "scheduler",
                    "failed",
                    started_at=scheduler_started,
                    error=str(error),
                )
                raise
            emit(
                "scheduler",
                "triggered" if request_to_decide is not None else "skipped",
                started_at=scheduler_started,
                payload={
                    "decision_triggered": request_to_decide is not None,
                    "triggers": (
                        list(request_to_decide.trigger_codes)
                        if request_to_decide is not None
                        else []
                    ),
                },
            )

        triggers = (
            request_to_decide.trigger_codes if request_to_decide is not None else ()
        )
        decision_triggered = request_to_decide is not None or force_decision
        if not decision_triggered:
            now = perf_counter()
            emit("policy", "skipped", started_at=now)
            emit("intent", "skipped", started_at=now)
            return ObservationPipelineResult(
                state=state,
                decision_triggered=False,
                triggers=(),
                behavior_intent=None,
                decision_mode=self.decision_mode,
            )

        policy_started = perf_counter()
        debug_snapshot: DebugDecisionSnapshot | None = None
        try:
            if self.decision_mode == DecisionMode.DEBUG:
                if self._debug_policy is None:
                    raise RuntimeError("Debug policy is not configured")
                debug_decision = self._debug_policy.decide(
                    state,
                    triggers,
                    source_id=source_id,
                )
                intent = debug_decision.behavior_intent
                debug_snapshot = debug_decision.snapshot
                if debug_decision.error_code is not None:
                    error = debug_snapshot.error
                    message = (
                        error.message
                        if error is not None
                        else "Debug decision failed"
                    )
                    emit(
                        "policy",
                        "failed",
                        started_at=policy_started,
                        payload={
                            "request_id": debug_snapshot.request_id,
                            "decision_mode": self.decision_mode.value,
                        },
                        error=message,
                    )
                    now = perf_counter()
                    emit("intent", "rejected", started_at=now, error=message)
                    return ObservationPipelineResult(
                        state=state,
                        decision_triggered=True,
                        triggers=triggers,
                        behavior_intent=None,
                        error_code=debug_decision.error_code,
                        decision_mode=self.decision_mode,
                        debug_snapshot=debug_snapshot,
                    )
                if intent is None:  # Defensive guard for the bridge contract.
                    raise RuntimeError("Debug policy completed without an intent")
            else:
                intent = self._policy.decide(state, triggers)
        except LLMPolicyError as error:
            emit("policy", "failed", started_at=policy_started, error=str(error))
            now = perf_counter()
            emit("intent", "rejected", started_at=now, error=str(error))
            return ObservationPipelineResult(
                state=state,
                decision_triggered=True,
                triggers=triggers,
                behavior_intent=None,
                error_code="invalid_llm_behavior_selection",
                decision_mode=self.decision_mode,
                debug_snapshot=debug_snapshot,
            )
        except Exception as error:
            emit("policy", "failed", started_at=policy_started, error=str(error))
            now = perf_counter()
            emit("intent", "skipped", started_at=now, error=str(error))
            return ObservationPipelineResult(
                state=state,
                decision_triggered=True,
                triggers=triggers,
                behavior_intent=None,
                error_code="llm_request_failed",
                decision_mode=self.decision_mode,
                debug_snapshot=debug_snapshot,
            )
        emit(
            "policy",
            "completed",
            started_at=policy_started,
            payload={
                "action": intent.action,
                "decision_id": intent.decision_id,
                **(
                    {
                        "request_id": debug_snapshot.request_id,
                        "decision_mode": self.decision_mode.value,
                    }
                    if debug_snapshot is not None
                    else {}
                ),
            },
        )
        intent_started = perf_counter()
        emit(
            "intent",
            "completed",
            started_at=intent_started,
            payload=intent.model_dump(mode="json"),
        )
        return ObservationPipelineResult(
            state=state,
            decision_triggered=True,
            triggers=triggers,
            behavior_intent=intent,
            decision_mode=self.decision_mode,
            debug_snapshot=debug_snapshot,
        )


def create_app(
    llm: LLMClient | None = None,
    settings: Settings | None = None,
    observation_pipeline: ObservationPipeline | None = None,
    monitor_service: MonitorService | None = None,
) -> Flask:
    """Create the Flask application; injectable arguments keep tests offline."""
    active_settings = settings or Settings()
    active_llm = llm or OllamaLLM(active_settings)
    active_pipeline = observation_pipeline or ObservationPipeline(
        active_llm, decision_mode=active_settings.decision_mode
    )
    project_root = Path(__file__).resolve().parents[1]
    web_dist = project_root / "web" / "dist"
    active_monitor = monitor_service or MonitorService(
        project_root=project_root,
        pipeline_factory=lambda mode: ObservationPipeline(
            active_llm if mode == "current" else DeterministicReplayLLM(),
            decision_mode=(
                active_settings.decision_mode
                if mode == "current"
                else DecisionMode.NORMAL
            ),
        ),
    )
    flask_app = Flask(__name__, static_folder=None)

    @flask_app.get("/")
    def index():
        if (web_dist / "index.html").is_file():
            return send_from_directory(web_dist, "index.html")
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

    @flask_app.get("/assets/<path:filename>")
    def web_assets(filename: str):
        return send_from_directory(web_dist / "assets", filename)

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
        trace_events: list[dict[str, Any]] = [
            {
                "stage": "observation",
                "status": "completed",
                "duration_ms": 0.0,
                "payload": observation.model_dump(mode="json"),
                "error": None,
            },
            {
                "stage": "validation",
                "status": "completed",
                "duration_ms": 0.0,
                "payload": {"schema_version": observation.schema_version},
                "error": None,
            },
        ]
        try:
            result = active_pipeline.process(
                observation,
                force_decision=force_decision,
                trace=trace_events.append,
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

        active_monitor.observe_live(observation, result, trace_events)

        response = {
            "accepted": True,
            "observation_id": observation.observation_id,
            "social_state_id": result.state.state_id,
            "decision_triggered": result.decision_triggered,
            "forced_decision": force_decision,
            "triggers": list(result.triggers),
            "behavior_intent": (
                result.behavior_intent.model_dump(mode="json")
                if result.behavior_intent is not None
                else None
            ),
        }
        if result.debug_snapshot is not None:
            response["debug_snapshot"] = result.debug_snapshot.model_dump(mode="json")
        if result.error_code is not None:
            if result.error_code == "invalid_llm_behavior_selection":
                message = (
                    "The observation was accepted, but the LLM response was not "
                    "a valid behavior selection."
                )
            elif result.error_code == "invalid_llm_debug_response":
                message = (
                    "The observation was accepted, but the LLM response was not "
                    "a valid grounded debug decision."
                )
            else:
                message = "The observation was accepted, but the LLM request failed."
            response["error"] = {
                "code": result.error_code,
                "message": message,
            }
            return jsonify(response), 502
        return jsonify(response)

    @flask_app.get("/api/v1/monitor/bootstrap")
    def monitor_bootstrap():
        return jsonify(active_monitor.bootstrap())

    @flask_app.get("/api/v1/monitor/sources")
    def monitor_sources():
        return jsonify({"sources": active_monitor.list_sources()})

    @flask_app.get("/api/v1/monitor/recordings")
    def monitor_recordings():
        return jsonify({"recordings": active_monitor.list_recordings()})

    @flask_app.get("/api/v1/monitor/runs")
    def monitor_runs():
        return jsonify({"runs": active_monitor.list_runs()})

    @flask_app.get("/api/v1/monitor/runs/<run_id>")
    def monitor_run(run_id: str):
        try:
            return jsonify(active_monitor.get_run(run_id))
        except KeyError as error:
            return jsonify({"error": str(error)}), 404

    @flask_app.post("/api/v1/monitor/replays")
    def create_replay():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not isinstance(payload.get("recording_id"), str):
            return jsonify({"error": "recording_id is required"}), 400
        try:
            run = active_monitor.create_replay(
                payload["recording_id"], payload.get("policy_mode", "stub")
            )
        except (FileNotFoundError, ValueError) as error:
            return jsonify({"error": str(error)}), 400
        return jsonify(run), 201

    @flask_app.post("/api/v1/monitor/replays/<run_id>/step")
    def step_replay(run_id: str):
        payload = request.get_json(silent=True) or {}
        count = payload.get("count", 1) if isinstance(payload, dict) else 1
        if isinstance(count, bool) or not isinstance(count, int):
            return jsonify({"error": "count must be an integer"}), 400
        try:
            return jsonify(active_monitor.step_replay(run_id, count))
        except KeyError as error:
            return jsonify({"error": str(error)}), 404
        except ValueError as error:
            return jsonify({"error": str(error)}), 400

    @flask_app.post("/api/v1/monitor/replays/<run_id>/reset")
    def reset_replay(run_id: str):
        try:
            return jsonify(active_monitor.reset_replay(run_id))
        except KeyError as error:
            return jsonify({"error": str(error)}), 404

    @flask_app.post("/api/v1/monitor/recordings/start")
    def start_recording():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not isinstance(payload.get("source_id"), str):
            return jsonify({"error": "source_id is required"}), 400
        try:
            return jsonify(active_monitor.start_recording(payload["source_id"])), 201
        except KeyError as error:
            return jsonify({"error": str(error)}), 404
        except ValueError as error:
            return jsonify({"error": str(error)}), 409

    @flask_app.post("/api/v1/monitor/recordings/<run_id>/stop")
    def stop_recording(run_id: str):
        try:
            return jsonify(active_monitor.stop_recording(run_id))
        except KeyError as error:
            return jsonify({"error": str(error)}), 404

    @flask_app.get("/api/v1/monitor/events")
    def monitor_events():
        header_id = request.headers.get("Last-Event-ID", "0")
        query_id = request.args.get("after", header_id)
        try:
            after_id = max(0, int(query_id))
        except ValueError:
            after_id = 0

        @stream_with_context
        def stream():
            cursor = after_id
            while True:
                events = active_monitor.events_after(cursor)
                if not events:
                    yield ": keep-alive\n\n"
                    continue
                for event in events:
                    cursor = event["id"]
                    yield (
                        f"id: {event['id']}\n"
                        f"event: {event['type']}\n"
                        f"data: {json.dumps(event['data'], separators=(',', ':'))}\n\n"
                    )

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
