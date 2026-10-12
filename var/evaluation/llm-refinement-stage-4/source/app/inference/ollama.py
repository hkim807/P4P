"""Synchronous, structured Ollama chat requests without policy or execution logic."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from http.client import HTTPException
import json
import time
from typing import Annotated, Any, Callable, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.model_decision import (
    ModelDecision, ModelDecisionValidationError, model_decision_schema, parse_model_decision,
)


class OllamaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)

    base_url: str
    model: str
    timeout_seconds: float = Field(default=30.0, gt=0)
    temperature: float = Field(default=0.0, ge=0)
    seed: int | None = None
    num_ctx: int | None = Field(default=None, gt=0, strict=True)
    num_predict: int | None = Field(default=None, ge=1)

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        # Accessing port also rejects malformed/non-numeric port values.
        parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or parsed.username is not None or parsed.password is not None
                or any(character.isspace() for character in value)):
            raise ValueError("base_url must be an HTTP(S) origin without credentials, path, or query")
        return value

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("model must be nonblank without surrounding whitespace")
        return value

    def generation_options(self) -> dict[str, float | int]:
        options: dict[str, float | int] = {"temperature": self.temperature}
        if self.seed is not None:
            options["seed"] = self.seed
        if self.num_predict is not None:
            options["num_predict"] = self.num_predict
        if self.num_ctx is not None:
            options["num_ctx"] = self.num_ctx
        return options


class OllamaMessage(BaseModel):
    """REST messages accept already-encoded base64 images, passed through unchanged."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    role: Literal["system", "user", "assistant"]
    content: str
    images: list[Annotated[str, Field(min_length=1)]] | None = None


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes


class HttpTransport(Protocol):
    def post(self, url: str, body: bytes, *, timeout_seconds: float) -> HttpResponse:
        """Return HTTP status/body, or raise a network/timeout exception."""
        ...


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    """One POST with no redirects or retries; HTTP errors retain their body."""

    def __init__(self) -> None:
        self._opener = build_opener(_NoRedirects())

    def post(self, url: str, body: bytes, *, timeout_seconds: float) -> HttpResponse:
        request = Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                return HttpResponse(response.status, response.read())
        except HTTPError as error:
            with error:
                return HttpResponse(error.code, error.read())


class OllamaErrorCategory(str, Enum):
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    HTTP = "http"
    RESPONSE_FORMAT = "response_format"
    INVALID_DECISION = "invalid_decision"


@dataclass(frozen=True)
class OllamaError:
    category: OllamaErrorCategory
    message: str
    http_status: int | None = None


@dataclass(frozen=True)
class OllamaResult:
    requested_model: str
    returned_model: str | None
    raw_content: str | None
    request_duration_s: float
    decision: ModelDecision | None
    error: OllamaError | None

    @property
    def ok(self) -> bool:
        return self.decision is not None and self.error is None


def extract_chat_content(payload: Any) -> tuple[str, str]:
    """Check the completed chat envelope separately from decision validation."""
    if not isinstance(payload, dict):
        raise ValueError("Ollama response must be a JSON object")
    if "error" in payload:
        raise ValueError(f"Ollama response contains an error: {payload['error']}")
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Ollama response model must be a nonblank string")
    if payload.get("done") is not True:
        raise ValueError("Ollama non-streaming response must have done=true")
    message = payload.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ValueError("Ollama response must contain an assistant message")
    if not isinstance(message.get("content"), str):
        raise ValueError("Ollama message.content must be a string")
    return model, message["content"]


def _response_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate Ollama response key: {key}")
        result[key] = value
    return result


def _reject_response_constant(value: str) -> None:
    raise ValueError(f"invalid Ollama response JSON constant: {value}")


class OllamaClient:
    def __init__(self, config: OllamaConfig, *, transport: HttpTransport | None = None,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.transport = transport if transport is not None else UrllibTransport()
        self.monotonic = monotonic

    def chat(self, messages: Sequence[OllamaMessage | Mapping[str, Any]]) -> OllamaResult:
        """Send caller messages only. Invalid caller configuration/messages raise ValueError.

        Inference failures return diagnostics with decision=None. Images are neither
        loaded nor decoded here, and no action, retry, or fallback is performed.
        """
        if not messages:
            raise ValueError("at least one chat message is required")
        validated = [OllamaMessage.model_validate(
            message.model_dump() if isinstance(message, OllamaMessage) else message)
            for message in messages]
        body = json.dumps({
            "model": self.config.model,
            "messages": [message.model_dump(exclude_none=True) for message in validated],
            "stream": False,
            "format": model_decision_schema(),
            "options": self.config.generation_options(),
        }, allow_nan=False, separators=(",", ":")).encode("utf-8")
        started = self.monotonic()
        try:
            response = self.transport.post(f"{self.config.base_url.rstrip('/')}/api/chat", body,
                                           timeout_seconds=self.config.timeout_seconds)
        except (URLError, OSError) as error:
            reason = error.reason if isinstance(error, URLError) else error
            category = (OllamaErrorCategory.TIMEOUT if isinstance(reason, TimeoutError)
                        else OllamaErrorCategory.CONNECTION)
            return OllamaResult(self.config.model, None, None, self.monotonic() - started,
                                None, OllamaError(category, str(reason)))
        except HTTPException as error:
            return OllamaResult(self.config.model, None, None, self.monotonic() - started,
                                None, OllamaError(OllamaErrorCategory.RESPONSE_FORMAT, str(error)))
        duration = self.monotonic() - started
        return self._read_response(response, duration)

    def _read_response(self, response: HttpResponse, duration: float) -> OllamaResult:
        model = content = None
        payload = None
        decode_error = None
        try:
            payload = json.loads(response.body.decode("utf-8"),
                                 object_pairs_hook=_response_object,
                                 parse_constant=_reject_response_constant)
        except (UnicodeError, ValueError, RecursionError) as error:
            decode_error = str(error)
        if isinstance(payload, dict):
            if isinstance(payload.get("model"), str):
                model = payload["model"]
            message = payload.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                content = message["content"]

        def failure(category: OllamaErrorCategory, message: str) -> OllamaResult:
            return OllamaResult(self.config.model, model, content, duration, None,
                                OllamaError(category, message, response.status_code))

        if not 200 <= response.status_code < 300:
            detail = payload.get("error") if isinstance(payload, dict) else None
            if not isinstance(detail, str):
                detail = response.body.decode("utf-8", errors="replace")
            return failure(OllamaErrorCategory.HTTP, f"HTTP {response.status_code}: {detail[:500]}")
        if decode_error is not None:
            return failure(OllamaErrorCategory.RESPONSE_FORMAT,
                           f"invalid Ollama response JSON: {decode_error}")
        try:
            model, content = extract_chat_content(payload)
        except ValueError as error:
            return failure(OllamaErrorCategory.RESPONSE_FORMAT, str(error))
        try:
            decision = parse_model_decision(content)
        except ModelDecisionValidationError as error:
            return failure(OllamaErrorCategory.INVALID_DECISION, str(error))
        return OllamaResult(self.config.model, model, content, duration, decision, None)
