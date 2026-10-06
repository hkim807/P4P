"""Standard-library HTTP POST transport for RawObservationFrame."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class TransportError(RuntimeError):
    """The server could not be reached or returned an unusable response."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class ObservationResponse:
    status_code: int
    payload: dict[str, Any]


class ObservationTransport:
    def __init__(self, server_url: str, *, timeout_seconds: float = 5.0) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        parsed = urlsplit(server_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("server_url must be an HTTP(S) base URL without a path or query")
        self.server_url = server_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(_NoRedirects())

    def send(self, observation: dict[str, Any]) -> ObservationResponse:
        return self._post("/api/v1/observations", observation)

    def send_event(self, event: dict[str, Any]) -> ObservationResponse:
        return self._post("/api/v1/execution-events", event)

    def send_sdk_packet(self, record: dict[str, Any]) -> ObservationResponse:
        return self._post("/api/v1/sdk-packets", record)

    def send_camera_frame(self, record: dict[str, Any]) -> ObservationResponse:
        return self._post("/api/v1/camera-frames", record)

    def _post(self, path: str, payload: dict[str, Any]) -> ObservationResponse:
        try:
            body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise TransportError("request must be finite JSON data") from error
        request = Request(
            f"{self.server_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                return self._response(response.status, response.read())
        except HTTPError as error:
            with error:
                return self._response(error.code, error.read())
        except (URLError, TimeoutError, OSError) as error:
            raise TransportError(f"could not reach observation server: {error}") from error

    @staticmethod
    def _response(status_code: int, body: bytes) -> ObservationResponse:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransportError("observation server returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise TransportError("observation server response must be a JSON object")
        return ObservationResponse(status_code=status_code, payload=payload)
