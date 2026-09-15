"""Durable, queryable history for Debug-mode LLM decision snapshots."""

from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DebugHistoryError(RuntimeError):
    """The durable Debug decision history could not be read or updated."""


class DebugDecisionHistory:
    """Store complete snapshots as compressed JSON with searchable list fields.

    A short-lived SQLite connection is used for each operation. This keeps the
    store safe to call from Flask worker threads without sharing a connection
    between them.
    """

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        except (OSError, sqlite3.Error) as error:
            raise DebugHistoryError(
                f"could not initialize Debug decision history at {self.path}"
            ) from error

    def record(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        """Insert one request-bound snapshot, returning its summary if new."""
        request_id = snapshot.get("request_id")
        source_id = snapshot.get("source_id")
        status = snapshot.get("status")
        required_values = (request_id, source_id, status)
        if not all(
            isinstance(value, str) and value for value in required_values
        ):
            raise DebugHistoryError(
                "Debug snapshot requires non-empty request_id, source_id and status"
            )

        metadata = snapshot.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        validated = snapshot.get("validated_response")
        validated = validated if isinstance(validated, dict) else {}
        error_payload = snapshot.get("error")
        error_payload = error_payload if isinstance(error_payload, dict) else {}
        try:
            serialized = json.dumps(
                snapshot,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            compressed = zlib.compress(serialized, level=6)
        except (TypeError, ValueError, zlib.error) as error:
            raise DebugHistoryError(
                "Debug snapshot is not JSON serializable"
            ) from error

        values = {
            "request_id": request_id,
            "source_id": source_id,
            "status": status,
            "observation_id": self._string_or_none(snapshot.get("observation_id")),
            "social_state_id": self._string_or_none(snapshot.get("social_state_id")),
            "state_timestamp_us": self._integer_or_none(
                snapshot.get("state_timestamp_us")
            ),
            "clock_domain": self._string_or_none(snapshot.get("clock_domain")),
            "requested_at": self._string_or_none(metadata.get("requested_at")),
            "responded_at": self._string_or_none(metadata.get("responded_at")),
            "latency_ms": self._number_or_none(metadata.get("latency_ms")),
            "provider": self._string_or_none(metadata.get("provider")),
            "requested_model": self._string_or_none(metadata.get("requested_model")),
            "returned_model": self._string_or_none(metadata.get("returned_model")),
            "recommended_action": self._string_or_none(
                validated.get("recommended_action")
            ),
            "decision_rationale": self._string_or_none(
                validated.get("decision_rationale")
            ),
            "decision_confidence": self._number_or_none(
                validated.get("decision_confidence")
            ),
            "error_code": self._string_or_none(error_payload.get("code")),
            "error_message": self._string_or_none(error_payload.get("message")),
            "snapshot_zlib": compressed,
            "snapshot_bytes": len(serialized),
            "recorded_at": _utc_now(),
        }

        try:
            with self._lock, closing(self._connect()) as connection:
                with connection:
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO debug_decisions (
                            request_id, source_id, status, observation_id,
                            social_state_id, state_timestamp_us, clock_domain,
                            requested_at, responded_at, latency_ms, provider,
                            requested_model, returned_model, recommended_action,
                            decision_rationale, decision_confidence, error_code,
                            error_message, snapshot_zlib, snapshot_bytes, recorded_at
                        ) VALUES (
                            :request_id, :source_id, :status, :observation_id,
                            :social_state_id, :state_timestamp_us, :clock_domain,
                            :requested_at, :responded_at, :latency_ms, :provider,
                            :requested_model, :returned_model, :recommended_action,
                            :decision_rationale, :decision_confidence, :error_code,
                            :error_message, :snapshot_zlib, :snapshot_bytes, :recorded_at
                        )
                        """,
                        values,
                    )
                    if cursor.rowcount == 0:
                        return None
                    row = connection.execute(
                        "SELECT * FROM debug_decisions WHERE sequence = ?",
                        (cursor.lastrowid,),
                    ).fetchone()
        except sqlite3.Error as error:
            raise DebugHistoryError(
                "could not persist Debug decision snapshot"
            ) from error
        if row is None:
            raise DebugHistoryError("persisted Debug decision could not be read back")
        return self._summary(row)

    def list(
        self,
        source_id: str,
        *,
        limit: int = 50,
        before_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Return newest-first summaries with an exclusive sequence cursor."""
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("source_id must be a non-empty string")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("limit must be between 1 and 100")
        if before_sequence is not None and (
            isinstance(before_sequence, bool)
            or not isinstance(before_sequence, int)
            or before_sequence < 1
        ):
            raise ValueError("before_sequence must be a positive integer")

        sql = "SELECT * FROM debug_decisions WHERE source_id = ?"
        parameters: list[Any] = [source_id]
        if before_sequence is not None:
            sql += " AND sequence < ?"
            parameters.append(before_sequence)
        sql += " ORDER BY sequence DESC LIMIT ?"
        parameters.append(limit + 1)
        try:
            with self._lock, closing(self._connect()) as connection:
                rows = connection.execute(sql, parameters).fetchall()
        except sqlite3.Error as error:
            raise DebugHistoryError("could not list Debug decisions") from error

        has_more = len(rows) > limit
        page = rows[:limit]
        return {
            "source_id": source_id,
            "items": [self._summary(row) for row in page],
            "next_cursor": page[-1]["sequence"] if has_more and page else None,
        }

    def get(self, request_id: str) -> dict[str, Any]:
        """Return the complete immutable snapshot for one request ID."""
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a non-empty string")
        try:
            with self._lock, closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM debug_decisions WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise DebugHistoryError("could not read Debug decision") from error
        if row is None:
            raise KeyError(f"unknown Debug decision {request_id!r}")
        try:
            snapshot = json.loads(zlib.decompress(row["snapshot_zlib"]))
        except (json.JSONDecodeError, UnicodeDecodeError, zlib.error) as error:
            raise DebugHistoryError(
                f"stored Debug decision {request_id!r} is corrupt"
            ) from error
        if not isinstance(snapshot, dict):
            raise DebugHistoryError(
                f"stored Debug decision {request_id!r} is not an object"
            )
        return {
            "history_id": row["sequence"],
            "recorded_at": row["recorded_at"],
            "snapshot": snapshot,
        }

    def count(self, source_id: str) -> int:
        try:
            with self._lock, closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM debug_decisions WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise DebugHistoryError("could not count Debug decisions") from error
        return int(row["total"] if row is not None else 0)

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection:
            version_row = connection.execute("PRAGMA user_version").fetchone()
            current_version = int(version_row[0])
            if current_version not in {0, SCHEMA_VERSION}:
                raise DebugHistoryError(
                    "unsupported Debug decision history schema version "
                    f"{current_version}"
                )
            connection.execute("PRAGMA journal_mode=WAL")
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS debug_decisions (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL UNIQUE,
                        source_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        observation_id TEXT,
                        social_state_id TEXT,
                        state_timestamp_us INTEGER,
                        clock_domain TEXT,
                        requested_at TEXT,
                        responded_at TEXT,
                        latency_ms REAL,
                        provider TEXT,
                        requested_model TEXT,
                        returned_model TEXT,
                        recommended_action TEXT,
                        decision_rationale TEXT,
                        decision_confidence REAL,
                        error_code TEXT,
                        error_message TEXT,
                        snapshot_zlib BLOB NOT NULL,
                        snapshot_bytes INTEGER NOT NULL,
                        recorded_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS debug_decisions_source_sequence
                    ON debug_decisions (source_id, sequence DESC);
                    """
                )
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "history_id": row["sequence"],
            "request_id": row["request_id"],
            "source_id": row["source_id"],
            "status": row["status"],
            "observation_id": row["observation_id"],
            "social_state_id": row["social_state_id"],
            "state_timestamp_us": row["state_timestamp_us"],
            "clock_domain": row["clock_domain"],
            "requested_at": row["requested_at"],
            "responded_at": row["responded_at"],
            "latency_ms": row["latency_ms"],
            "provider": row["provider"],
            "requested_model": row["requested_model"],
            "returned_model": row["returned_model"],
            "recommended_action": row["recommended_action"],
            "decision_rationale": row["decision_rationale"],
            "decision_confidence": row["decision_confidence"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "snapshot_bytes": row["snapshot_bytes"],
            "recorded_at": row["recorded_at"],
        }

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _integer_or_none(value: Any) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    @staticmethod
    def _number_or_none(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)
