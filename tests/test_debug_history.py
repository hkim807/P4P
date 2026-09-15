"""Tests for durable Debug-mode decision history."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.monitor import DebugDecisionHistory, DebugHistoryError


def snapshot(
    request_id: str,
    *,
    source_id: str = "navel-robot-1",
    status: str = "COMPLETED",
    state_timestamp_us: int = 1,
) -> dict:
    failed = status == "FAILED"
    return {
        "request_id": request_id,
        "status": status,
        "source_id": source_id,
        "observation_id": f"observation-{request_id}",
        "social_state_id": f"state-{request_id}",
        "state_timestamp_us": state_timestamp_us,
        "clock_domain": "robot_monotonic",
        "raw_social_state": {"state_id": f"state-{request_id}", "humans": []},
        "rendered_messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "state"},
        ],
        "request_parameters": {"temperature": 0.0},
        "raw_response": None if failed else '{"recommended_action":"MONITOR"}',
        "validated_response": None
        if failed
        else {
            "recommended_action": "MONITOR",
            "decision_rationale": "No observed human requires a response.",
            "decision_confidence": 0.81,
        },
        "behavior_intent": None if failed else {"action": "MONITOR"},
        "metadata": {
            "provider": "ollama",
            "requested_model": "qwen2.5:7b",
            "returned_model": None if failed else "qwen2.5:7b",
            "requested_at": f"2026-09-15T00:00:0{state_timestamp_us}.000+00:00",
            "responded_at": f"2026-09-15T00:00:0{state_timestamp_us}.100+00:00",
            "latency_ms": 100.0,
        },
        "error": (
            {"code": "llm_request_failed", "message": "model unavailable"}
            if failed
            else None
        ),
    }


class DebugDecisionHistoryTests(unittest.TestCase):
    def test_complete_snapshots_survive_reopening_the_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "debug-decisions.sqlite3"
            original = snapshot("request-1")
            store = DebugDecisionHistory(path)

            created = store.record(original)
            duplicate = store.record(original)
            reopened = DebugDecisionHistory(path)

            self.assertIsNotNone(created)
            self.assertIsNone(duplicate)
            self.assertEqual(reopened.count("navel-robot-1"), 1)
            detail = reopened.get("request-1")
            self.assertEqual(detail["history_id"], created["history_id"])
            self.assertEqual(detail["snapshot"], original)

    def test_newest_first_pagination_includes_successes_and_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DebugDecisionHistory(Path(directory) / "history.sqlite3")
            store.record(snapshot("request-1", state_timestamp_us=1))
            store.record(
                snapshot("request-2", status="FAILED", state_timestamp_us=2)
            )
            store.record(snapshot("request-3", state_timestamp_us=3))

            first_page = store.list("navel-robot-1", limit=2)
            second_page = store.list(
                "navel-robot-1", before_sequence=first_page["next_cursor"]
            )

            self.assertEqual(
                [item["request_id"] for item in first_page["items"]],
                ["request-3", "request-2"],
            )
            self.assertIsNotNone(first_page["next_cursor"])
            self.assertEqual(first_page["items"][1]["status"], "FAILED")
            self.assertEqual(
                first_page["items"][1]["error_message"], "model unavailable"
            )
            self.assertEqual(
                [item["request_id"] for item in second_page["items"]],
                ["request-1"],
            )
            self.assertIsNone(second_page["next_cursor"])

    def test_rejects_invalid_snapshots_and_query_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DebugDecisionHistory(Path(directory) / "history.sqlite3")

            with self.assertRaises(DebugHistoryError):
                store.record({"request_id": "request-1"})
            with self.assertRaises(ValueError):
                store.list("navel-robot-1", limit=0)
            with self.assertRaises(ValueError):
                store.list("navel-robot-1", before_sequence=0)
            with self.assertRaises(KeyError):
                store.get("unknown")


if __name__ == "__main__":
    unittest.main()
