"""Validated streaming input for normalized observation recordings."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from pydantic import ValidationError

from app.domain.models import ObservationFrame


class JsonlObservationError(ValueError):
    """A recording cannot be interpreted as a valid observation stream."""


@dataclass(frozen=True)
class JsonlObservationRecord:
    """One validated input frame plus evaluation-only recording data."""

    observation: ObservationFrame
    line_number: int
    ground_truth: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class JsonlObservationIterator:
    """Iterate over a JSONL recording without loading it all into memory.

    Iterating over this object yields only ``ObservationFrame`` instances. Use
    ``iter_records`` when an evaluator also needs ground truth or envelope
    metadata. This separation keeps evaluation labels out of the state and
    policy input path by default.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        require_ground_truth: bool = False,
        enforce_monotonic_timestamps: bool = True,
    ) -> None:
        self.path = Path(path)
        self.require_ground_truth = require_ground_truth
        self.enforce_monotonic_timestamps = enforce_monotonic_timestamps

    def __iter__(self) -> Iterator[ObservationFrame]:
        for record in self.iter_records():
            yield record.observation

    def iter_records(self) -> Iterator[JsonlObservationRecord]:
        """Yield validated observations with separately retained annotations."""
        if not self.path.exists():
            raise FileNotFoundError(f"JSONL recording does not exist: {self.path}")
        if not self.path.is_file():
            raise JsonlObservationError(
                f"JSONL recording is not a regular file: {self.path}"
            )

        previous_timestamp_us: int | None = None
        previous_line_number: int | None = None
        record_count = 0

        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                if not raw_line.strip():
                    continue

                payload = self._parse_envelope(raw_line, line_number)
                observation = self._parse_observation(payload, line_number)
                ground_truth = self._parse_ground_truth(payload, line_number)

                if (
                    self.enforce_monotonic_timestamps
                    and previous_timestamp_us is not None
                    and observation.timestamp_us <= previous_timestamp_us
                ):
                    raise self._error(
                        line_number,
                        (
                            f"timestamp_us {observation.timestamp_us} must be greater "
                            f"than {previous_timestamp_us} from line "
                            f"{previous_line_number}"
                        ),
                    )

                previous_timestamp_us = observation.timestamp_us
                previous_line_number = line_number
                record_count += 1
                yield JsonlObservationRecord(
                    observation=observation,
                    line_number=line_number,
                    ground_truth=ground_truth,
                    metadata={
                        key: value
                        for key, value in payload.items()
                        if key not in {"observation", "ground_truth"}
                    },
                )

        if record_count == 0:
            raise JsonlObservationError(
                f"JSONL recording contains no observation records: {self.path}"
            )

    def _parse_envelope(self, raw_line: str, line_number: int) -> dict[str, Any]:
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise self._error(
                line_number,
                f"invalid JSON at column {error.colno}: {error.msg}",
            ) from error
        if not isinstance(payload, dict):
            raise self._error(line_number, "record must be a JSON object")
        return payload

    def _parse_observation(
        self, payload: dict[str, Any], line_number: int
    ) -> ObservationFrame:
        if "observation" not in payload:
            raise self._error(line_number, "record is missing 'observation'")
        try:
            return ObservationFrame.model_validate(payload["observation"])
        except ValidationError as error:
            raise self._error(line_number, f"invalid ObservationFrame: {error}") from error

    def _parse_ground_truth(
        self, payload: dict[str, Any], line_number: int
    ) -> dict[str, Any] | None:
        ground_truth = payload.get("ground_truth")
        if self.require_ground_truth and ground_truth is None:
            raise self._error(line_number, "record is missing required 'ground_truth'")
        if ground_truth is not None and not isinstance(ground_truth, dict):
            raise self._error(line_number, "'ground_truth' must be a JSON object or null")
        return ground_truth

    def _error(self, line_number: int, message: str) -> JsonlObservationError:
        return JsonlObservationError(f"{self.path}:{line_number}: {message}")
