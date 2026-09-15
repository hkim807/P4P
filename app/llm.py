"""Small client for a locally hosted Ollama model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from openai import OpenAI

from app.config import Settings


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class CapturedLLMRequest:
    """The exact request content handed to the OpenAI-compatible client."""

    provider: str
    model: str
    endpoint: str
    requested_at: str
    messages: tuple[dict[str, str], ...]
    temperature: float
    response_schema_name: str | None
    response_schema: dict[str, Any] | None
    response_format: dict[str, Any] | None


@dataclass(frozen=True)
class LLMGenerationResult:
    """One model response together with request-bound transport metadata."""

    request: CapturedLLMRequest
    raw_content: str
    responded_at: str
    latency_ms: float
    response_id: str | None = None
    returned_model: str | None = None
    created: int | None = None
    finish_reason: str | None = None
    usage: dict[str, int | None] | None = None


class LLMRequestError(RuntimeError):
    """Transport or empty-response failure retaining the originating request."""

    def __init__(
        self,
        message: str,
        *,
        request: CapturedLLMRequest,
        failed_at: str,
        latency_ms: float,
    ) -> None:
        super().__init__(message)
        self.request = request
        self.failed_at = failed_at
        self.latency_ms = latency_ms


class OllamaLLM:
    """Send chat-completion requests through Ollama's OpenAI-compatible API."""

    provider = "ollama"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.ollama_model
        self.endpoint = settings.ollama_base_url
        self._client = OpenAI(
            base_url=self.endpoint,
            api_key="ollama",
            timeout=settings.request_timeout_seconds,
        )

    def health(self) -> dict[str, Any]:
        """Check whether Ollama is reachable and report its installed models."""
        models = self._client.models.list()
        names = [model.id for model in models.data]
        return {
            "reachable": True,
            "model_available": self.model in names,
            "available_models": names,
        }

    def generate(
        self,
        message: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        """Return the model's text response for one input message."""
        result = self.generate_with_capture(
            message,
            system_prompt=system_prompt,
            temperature=temperature,
            response_schema=response_schema,
        )
        return result.raw_content.strip()

    def generate_with_capture(
        self,
        message: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        response_schema: dict[str, Any] | None = None,
        response_schema_name: str = "social_navigation_behavior_selection",
    ) -> LLMGenerationResult:
        """Generate text while retaining the exact request and raw response."""
        request_options: dict[str, Any] = {}
        if response_schema is not None:
            request_options["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            }
        messages = (
            {
                "role": "system",
                "content": system_prompt or self.settings.system_prompt,
            },
            {"role": "user", "content": message},
        )
        captured_request = CapturedLLMRequest(
            provider=self.provider,
            model=self.model,
            endpoint=self.endpoint,
            requested_at=_utc_timestamp(),
            messages=tuple(deepcopy(messages)),
            temperature=temperature,
            response_schema_name=(
                response_schema_name if response_schema is not None else None
            ),
            response_schema=deepcopy(response_schema),
            response_format=deepcopy(request_options.get("response_format")),
        )
        started_at = perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=list(messages),
                temperature=temperature,
                **request_options,
            )
        except Exception as error:
            raise LLMRequestError(
                str(error),
                request=captured_request,
                failed_at=_utc_timestamp(),
                latency_ms=round((perf_counter() - started_at) * 1_000, 3),
            ) from error
        responded_at = _utc_timestamp()
        latency_ms = round((perf_counter() - started_at) * 1_000, 3)
        try:
            choice = response.choices[0]
            content = choice.message.content
            if not content:
                raise LLMRequestError(
                    "Ollama returned an empty response",
                    request=captured_request,
                    failed_at=responded_at,
                    latency_ms=latency_ms,
                )
            usage = getattr(response, "usage", None)
            captured_usage = None
            if usage is not None:
                captured_usage = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                }
            return LLMGenerationResult(
                request=captured_request,
                raw_content=content,
                responded_at=responded_at,
                latency_ms=latency_ms,
                response_id=getattr(response, "id", None),
                returned_model=getattr(response, "model", None),
                created=getattr(response, "created", None),
                finish_reason=getattr(choice, "finish_reason", None),
                usage=captured_usage,
            )
        except LLMRequestError:
            raise
        except Exception as error:
            raise LLMRequestError(
                f"Ollama returned an invalid response: {error}",
                request=captured_request,
                failed_at=responded_at,
                latency_ms=latency_ms,
            ) from error
