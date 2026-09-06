"""Small standard-library HTTP transport for canonical observations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TransportError(RuntimeError):
    """The central server could not be reached or returned unusable data."""


@dataclass(frozen=True)
class ObservationResponse:
    status_code: int
    payload: dict[str, Any]


class ObservationTransport:
    def __init__(self, server_url: str, *, timeout_seconds: float = 35.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.server_url = server_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def send(
        self, observation: dict[str, Any], *, force_decision: bool = False
    ) -> ObservationResponse:
        query = urlencode({"force_decision": "1"}) if force_decision else ""
        url = f"{self.server_url}/api/v1/observations"
        if query:
            url = f"{url}?{query}"
        request = Request(
            url,
            data=json.dumps(observation, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return self._response(response.status, response.read())
        except HTTPError as error:
            return self._response(error.code, error.read())
        except (URLError, TimeoutError, OSError) as error:
            raise TransportError(f"could not reach observation server: {error}") from error

    def _response(self, status_code: int, body: bytes) -> ObservationResponse:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransportError("observation server returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise TransportError("observation server response must be a JSON object")
        return ObservationResponse(status_code=status_code, payload=payload)
