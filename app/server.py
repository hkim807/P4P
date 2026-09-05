"""HTTP gateway for sending inputs to, and receiving outputs from, Ollama."""

from __future__ import annotations

from typing import Any, Protocol

from flask import Flask, jsonify, request

from app.config import Settings
from app.llm import OllamaLLM


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


def create_app(
    llm: LLMClient | None = None,
    settings: Settings | None = None,
) -> Flask:
    """Create the Flask application; injectable arguments keep tests offline."""
    active_settings = settings or Settings()
    active_llm = llm or OllamaLLM(active_settings)
    flask_app = Flask(__name__)

    @flask_app.get("/")
    def index():
        return jsonify(
            {
                "service": "social-navigation-llm-gateway",
                "provider": active_llm.provider,
                "model": active_llm.model,
                "endpoints": {"health": "GET /health", "chat": "POST /chat"},
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
