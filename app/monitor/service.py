"""In-process run catalog and deterministic replay engine.

The monitor is deliberately downstream of robot ingestion and has no dependency
on any execution adapter. It can observe live cycles and reprocess immutable
observation recordings, but it cannot send a command to a robot.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from copy import deepcopy
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.adapters.jsonl import JsonlObservationIterator, JsonlObservationRecord
from app.domain.models import ObservationFrame, SocialState
from app.monitor.debug_history import DebugDecisionHistory, DebugHistoryError


logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _human_title(value: str) -> str:
    return re.sub(r"[_-]+", " ", value).strip().title()


class DeterministicReplayLLM:
    """Offline policy response used for repeatable monitor development."""

    provider = "offline"
    model = "deterministic-monitor-stub-v1"
    endpoint = "in-process"

    def health(self) -> dict[str, Any]:
        return {
            "reachable": True,
            "model_available": True,
            "available_models": [self.model],
        }

    def generate(
        self,
        message: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        del message, system_prompt, temperature, response_schema
        return json.dumps(
            {
                "action": "MONITOR",
                "target_human_id": None,
                "preferences": {},
                "valid_for_ms": 1_000,
                "reason_codes": ["INSUFFICIENT_EVIDENCE"],
                "decision_confidence": 0.55,
            }
        )


@dataclass
class ReplayRun:
    run_id: str
    name: str
    recording_id: str
    policy_mode: str
    records: list[JsonlObservationRecord]
    pipeline: Any
    created_at: str = field(default_factory=_utc_now)
    status: str = "ready"
    current_index: int = -1
    cycles: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LiveRecording:
    run_id: str
    source_id: str
    name: str
    path: Path
    created_at: str = field(default_factory=_utc_now)
    cycles: list[dict[str, Any]] = field(default_factory=list)
    status: str = "recording"


class MonitorService:
    """Thread-safe source monitor, recorder, and replay run manager."""

    def __init__(
        self,
        *,
        project_root: Path,
        pipeline_factory: Callable[[str], Any],
        debug_history: DebugDecisionHistory | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.recordings_root = self.project_root / "recordings"
        self.runtime_root = self.project_root / "var" / "recordings"
        self.debug_history = debug_history or DebugDecisionHistory(
            self.project_root / "var" / "debug-decisions.sqlite3"
        )
        self.pipeline_factory = pipeline_factory
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._runs: dict[str, ReplayRun | LiveRecording] = {}
        self._sources: dict[str, dict[str, Any]] = {}
        self._active_recordings: dict[str, str] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=1_000)
        self._event_id = 0
        self._debug_snapshot_revision = 0

    def list_recordings(self) -> list[dict[str, Any]]:
        recordings: list[dict[str, Any]] = []
        candidates: list[tuple[Path, str]] = []
        if self.recordings_root.exists():
            candidates.extend(
                (path, path.relative_to(self.recordings_root).as_posix())
                for path in self.recordings_root.rglob("*.jsonl")
            )
        if self.runtime_root.exists():
            candidates.extend(
                (path, f"runtime/{path.relative_to(self.runtime_root).as_posix()}")
                for path in self.runtime_root.rglob("observations.jsonl")
            )
        for path, recording_id in sorted(candidates, key=lambda item: item[1]):
            try:
                records = list(JsonlObservationIterator(path).iter_records())
            except (OSError, ValueError):
                continue
            first = records[0].observation
            last = records[-1].observation
            duration_s = max(0.0, (last.timestamp_us - first.timestamp_us) / 1_000_000)
            ground_truth = records[0].ground_truth or {}
            manifest_path = path.parent / "manifest.json"
            manifest = {}
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    manifest = {}
            recordings.append(
                {
                    "id": recording_id,
                    "name": manifest.get("name", _human_title(path.stem)),
                    "source_id": first.capabilities.adapter_id,
                    "robot_type": first.capabilities.robot_type,
                    "frames": len(records),
                    "duration_s": round(duration_s, 2),
                    "expected_event": ground_truth.get("expected_event"),
                    "schema_version": first.schema_version,
                }
            )
        return recordings

    def create_replay(self, recording_id: str, policy_mode: str = "stub") -> dict[str, Any]:
        if policy_mode not in {"stub", "current"}:
            raise ValueError("policy_mode must be 'stub' or 'current'")
        path = self._recording_path(recording_id)
        records = list(JsonlObservationIterator(path).iter_records())
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        run = ReplayRun(
            run_id=run_id,
            name=_human_title(path.stem),
            recording_id=recording_id,
            policy_mode=policy_mode,
            records=records,
            pipeline=self.pipeline_factory(policy_mode),
        )
        with self._lock:
            self._runs[run_id] = run
            self._publish("run.created", {"run_id": run_id, "mode": "replay"})
        return self._run_payload(run, include_cycles=True)

    def step_replay(self, run_id: str, count: int = 1) -> dict[str, Any]:
        if count < 1 or count > 500:
            raise ValueError("count must be between 1 and 500")
        with self._lock:
            run = self._require_replay(run_id)
            for _ in range(count):
                if run.current_index + 1 >= len(run.records):
                    run.status = "complete"
                    break
                run.status = "running"
                record = run.records[run.current_index + 1]
                cycle = self._process_record(run, record)
                run.current_index += 1
                run.cycles.append(cycle)
                self._publish(
                    "cycle.completed",
                    {
                        "run_id": run_id,
                        "sequence": cycle["sequence"],
                        "status": cycle["status"],
                    },
                )
            if run.current_index + 1 >= len(run.records):
                run.status = "complete"
                self._publish("run.completed", {"run_id": run_id})
            elif run.status == "running":
                run.status = "paused"
            return self._run_payload(run, include_cycles=True)

    def reset_replay(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._require_replay(run_id)
            run.pipeline = self.pipeline_factory(run.policy_mode)
            run.current_index = -1
            run.cycles.clear()
            run.status = "ready"
            self._publish("run.reset", {"run_id": run_id})
            return self._run_payload(run, include_cycles=True)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(f"unknown run {run_id!r}")
            return self._run_payload(run, include_cycles=True)

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._run_payload(run, include_cycles=False)
                for run in reversed(list(self._runs.values()))
            ]

    def observe_live(
        self,
        observation: ObservationFrame,
        result: Any,
        stages: list[dict[str, Any]],
    ) -> None:
        source_id = observation.capabilities.adapter_id
        cycle = self._cycle_from_result(
            sequence=0,
            observation=observation,
            result=result,
            stages=stages,
            previous_state=None,
            ground_truth=None,
        )
        with self._lock:
            previous = self._sources.get(source_id)
            cycle["sequence"] = (previous or {}).get("frame_count", 0) + 1
            retained_snapshot = (previous or {}).get("latest_debug_snapshot")
            retained_revision = (previous or {}).get("debug_snapshot_revision", 0)
            snapshot_updated = False
            history_item = None
            candidate = getattr(result, "debug_snapshot", None)
            if candidate is not None:
                candidate_payload = deepcopy(candidate.model_dump(mode="json"))
                if candidate_payload.get("source_id") == source_id:
                    try:
                        history_item = self.debug_history.record(candidate_payload)
                    except DebugHistoryError:
                        logger.exception(
                            "Could not persist Debug decision %r",
                            candidate_payload.get("request_id"),
                        )
                    if self._is_newer_debug_snapshot(
                        candidate_payload, retained_snapshot
                    ):
                        self._debug_snapshot_revision += 1
                        retained_snapshot = candidate_payload
                        retained_revision = self._debug_snapshot_revision
                        snapshot_updated = True
            previous_history_count = (previous or {}).get("debug_decision_count")
            if previous_history_count is None:
                try:
                    history_count = self.debug_history.count(source_id)
                except DebugHistoryError:
                    logger.exception(
                        "Could not count Debug decisions for source %r", source_id
                    )
                    history_count = 1 if history_item is not None else 0
            else:
                history_count = previous_history_count + (
                    1 if history_item is not None else 0
                )
            self._sources[source_id] = {
                "id": source_id,
                "name": _human_title(source_id),
                "robot_type": observation.capabilities.robot_type,
                "status": "online",
                "last_seen_at": _utc_now(),
                "last_seen_monotonic": time.monotonic(),
                "frame_count": cycle["sequence"],
                "human_count": len(observation.humans),
                "controller_status": observation.robot.controller_status,
                "task": observation.robot.task,
                "clock_domain": observation.clock_domain,
                "latest_cycle": cycle,
                "recording_run_id": self._active_recordings.get(source_id),
                "latest_debug_snapshot": retained_snapshot,
                "debug_snapshot_revision": retained_revision,
                "debug_decision_count": history_count,
            }
            recording_id = self._active_recordings.get(source_id)
            if recording_id is not None:
                recording = self._runs[recording_id]
                assert isinstance(recording, LiveRecording)
                recorded_cycle = deepcopy(cycle)
                live_previous_state = (
                    recording.cycles[-1].get("social_state")
                    if recording.cycles
                    else None
                )
                recorded_cycle["changes"] = semantic_changes(
                    live_previous_state, recorded_cycle.get("social_state")
                )
                recorded_cycle["sequence"] = len(recording.cycles) + 1
                recording.cycles.append(recorded_cycle)
                self._append_observation(recording.path, observation)
                self._append_trace(recording.path.parent / "trace.jsonl", recorded_cycle)
            if snapshot_updated:
                assert retained_snapshot is not None
                self._publish(
                    "debug.snapshot.updated",
                    {
                        "source_id": source_id,
                        "request_id": retained_snapshot["request_id"],
                        "state_timestamp_us": retained_snapshot[
                            "state_timestamp_us"
                        ],
                        "status": retained_snapshot["status"],
                        "revision": retained_revision,
                    },
                )
            if history_item is not None:
                self._publish(
                    "debug.decision.recorded",
                    {
                        "source_id": source_id,
                        "request_id": history_item["request_id"],
                        "history_id": history_item["history_id"],
                        "state_timestamp_us": history_item["state_timestamp_us"],
                        "status": history_item["status"],
                        "recommended_action": history_item["recommended_action"],
                    },
                )
            self._publish(
                "source.observed",
                {"source_id": source_id, "sequence": cycle["sequence"]},
            )

    def list_sources(self) -> list[dict[str, Any]]:
        with self._lock:
            now = time.monotonic()
            sources = []
            for source in self._sources.values():
                snapshot = source.get("latest_debug_snapshot")
                item = deepcopy(
                    {
                        key: value
                        for key, value in source.items()
                        if key != "latest_debug_snapshot"
                    }
                )
                item["latest_debug_request_id"] = (
                    snapshot.get("request_id") if snapshot is not None else None
                )
                item["latest_debug_snapshot_status"] = (
                    snapshot.get("status") if snapshot is not None else None
                )
                item["latest_debug_state_timestamp_us"] = (
                    snapshot.get("state_timestamp_us")
                    if snapshot is not None
                    else None
                )
                age_s = now - item.pop("last_seen_monotonic")
                item["age_s"] = round(age_s, 2)
                item["status"] = "online" if age_s <= 2.5 else "stale" if age_s <= 10 else "offline"
                sources.append(item)
            return sorted(sources, key=lambda item: item["name"])

    def get_debug_snapshot(self, source_id: str) -> dict[str, Any]:
        """Return one source's retained inference snapshot and monotonic revision."""
        with self._lock:
            source = self._sources.get(source_id)
            if source is None:
                raise KeyError(f"unknown source {source_id!r}")
            return {
                "source_id": source_id,
                "revision": source["debug_snapshot_revision"],
                "snapshot": deepcopy(source["latest_debug_snapshot"]),
            }

    def list_debug_decisions(
        self,
        source_id: str,
        *,
        limit: int = 50,
        before_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Return durable Debug decision summaries for one source."""
        return self.debug_history.list(
            source_id,
            limit=limit,
            before_sequence=before_sequence,
        )

    def get_debug_decision(self, request_id: str) -> dict[str, Any]:
        """Return one complete durable Debug decision snapshot."""
        return self.debug_history.get(request_id)

    def start_recording(self, source_id: str) -> dict[str, Any]:
        with self._lock:
            if source_id not in self._sources:
                raise KeyError(f"unknown source {source_id!r}")
            if source_id in self._active_recordings:
                raise ValueError("source is already being recorded")
            run_id = f"live-{uuid.uuid4().hex[:12]}"
            path = self.runtime_root / run_id / "observations.jsonl"
            path.parent.mkdir(parents=True, exist_ok=False)
            recording = LiveRecording(
                run_id=run_id,
                source_id=source_id,
                name=f"{self._sources[source_id]['name']} recording",
                path=path,
            )
            self._runs[run_id] = recording
            self._active_recordings[source_id] = run_id
            self._sources[source_id]["recording_run_id"] = run_id
            self._publish("recording.started", {"run_id": run_id, "source_id": source_id})
            return self._run_payload(recording, include_cycles=False)

    def stop_recording(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(run_id)
            if not isinstance(run, LiveRecording):
                raise KeyError(f"unknown live recording {run_id!r}")
            run.status = "complete"
            self._active_recordings.pop(run.source_id, None)
            if run.source_id in self._sources:
                self._sources[run.source_id]["recording_run_id"] = None
            manifest = {
                "run_id": run.run_id,
                "name": run.name,
                "source_id": run.source_id,
                "created_at": run.created_at,
                "completed_at": _utc_now(),
                "frames": len(run.cycles),
                "complete": True,
            }
            (run.path.parent / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self._publish("recording.stopped", {"run_id": run_id})
            return self._run_payload(run, include_cycles=False)

    def bootstrap(self) -> dict[str, Any]:
        return {
            "sources": self.list_sources(),
            "recordings": self.list_recordings(),
            "runs": self.list_runs(),
            "health": {
                "status": "healthy",
                "mode": "local-shadow",
                "actuation_enabled": False,
            },
        }

    def events_after(self, after_id: int, timeout_s: float = 15.0) -> list[dict[str, Any]]:
        with self._condition:
            found = [event for event in self._events if event["id"] > after_id]
            if found:
                return found
            self._condition.wait(timeout_s)
            return [event for event in self._events if event["id"] > after_id]

    def _process_record(
        self, run: ReplayRun, record: JsonlObservationRecord
    ) -> dict[str, Any]:
        observation = record.observation
        stages: list[dict[str, Any]] = [
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
        started = time.perf_counter()
        try:
            result = run.pipeline.process(observation, trace=stages.append)
        except Exception as error:
            return {
                "sequence": run.current_index + 2,
                "observation_id": observation.observation_id,
                "timestamp_us": observation.timestamp_us,
                "elapsed_s": self._elapsed_s(run, observation),
                "status": "failed",
                "duration_ms": round((time.perf_counter() - started) * 1_000, 3),
                "observation": observation.model_dump(mode="json"),
                "social_state": None,
                "scheduler": None,
                "behavior_intent": None,
                "stages": stages,
                "changes": [],
                "error": str(error),
                "ground_truth": record.ground_truth,
            }
        previous_state = run.cycles[-1].get("social_state") if run.cycles else None
        return self._cycle_from_result(
            sequence=run.current_index + 2,
            observation=observation,
            result=result,
            stages=stages,
            previous_state=previous_state,
            ground_truth=record.ground_truth,
            duration_ms=round((time.perf_counter() - started) * 1_000, 3),
            elapsed_s=self._elapsed_s(run, observation),
        )

    def _cycle_from_result(
        self,
        *,
        sequence: int,
        observation: ObservationFrame,
        result: Any,
        stages: list[dict[str, Any]],
        previous_state: dict[str, Any] | None,
        ground_truth: dict[str, Any] | None,
        duration_ms: float | None = None,
        elapsed_s: float = 0.0,
    ) -> dict[str, Any]:
        state = result.state.model_dump(mode="json")
        intent = (
            result.behavior_intent.model_dump(mode="json")
            if result.behavior_intent is not None
            else None
        )
        scheduler = {
            "decision_triggered": result.decision_triggered,
            "triggers": list(result.triggers),
        }
        return {
            "sequence": sequence,
            "observation_id": observation.observation_id,
            "timestamp_us": observation.timestamp_us,
            "elapsed_s": round(elapsed_s, 3),
            "status": "warning" if result.error_code else "completed",
            "duration_ms": duration_ms if duration_ms is not None else sum(
                float(stage.get("duration_ms", 0)) for stage in stages
            ),
            "observation": observation.model_dump(mode="json"),
            "social_state": state,
            "scheduler": scheduler,
            "behavior_intent": intent,
            "stages": stages,
            "changes": semantic_changes(previous_state, state),
            "error": result.error_code,
            "ground_truth": ground_truth,
        }

    def _run_payload(
        self, run: ReplayRun | LiveRecording, *, include_cycles: bool
    ) -> dict[str, Any]:
        if isinstance(run, ReplayRun):
            payload: dict[str, Any] = {
                "id": run.run_id,
                "name": run.name,
                "mode": "replay",
                "recording_id": run.recording_id,
                "policy_mode": run.policy_mode,
                "policy_name": (
                    "Current Ollama policy"
                    if run.policy_mode == "current"
                    else "Deterministic stub"
                ),
                "status": run.status,
                "created_at": run.created_at,
                "current_index": run.current_index,
                "frame_count": len(run.records),
                "processed_count": len(run.cycles),
                "duration_s": self._duration_s(run.records),
                "current_cycle": run.cycles[-1] if run.cycles else None,
            }
            if include_cycles:
                payload["cycles"] = run.cycles
            return payload
        payload = {
            "id": run.run_id,
            "name": run.name,
            "mode": "live-recording",
            "source_id": run.source_id,
            "status": run.status,
            "created_at": run.created_at,
            "frame_count": len(run.cycles),
            "processed_count": len(run.cycles),
            "current_cycle": run.cycles[-1] if run.cycles else None,
        }
        if include_cycles:
            payload["cycles"] = run.cycles
        return payload

    def _recording_path(self, recording_id: str) -> Path:
        if recording_id.startswith("runtime/"):
            root = self.runtime_root.resolve()
            relative = recording_id.removeprefix("runtime/")
        else:
            root = self.recordings_root.resolve()
            relative = recording_id
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("recording path escapes the recording directory") from error
        if not candidate.is_file() or candidate.suffix != ".jsonl":
            raise FileNotFoundError(f"recording does not exist: {recording_id}")
        return candidate

    def _require_replay(self, run_id: str) -> ReplayRun:
        run = self._runs.get(run_id)
        if not isinstance(run, ReplayRun):
            raise KeyError(f"unknown replay run {run_id!r}")
        return run

    def _elapsed_s(self, run: ReplayRun, observation: ObservationFrame) -> float:
        return (
            observation.timestamp_us - run.records[0].observation.timestamp_us
        ) / 1_000_000

    def _duration_s(self, records: list[JsonlObservationRecord]) -> float:
        return round(
            (
                records[-1].observation.timestamp_us
                - records[0].observation.timestamp_us
            )
            / 1_000_000,
            3,
        )

    def _append_observation(self, path: Path, observation: ObservationFrame) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"observation": observation.model_dump(mode="json")},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )

    def _append_trace(self, path: Path, cycle: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(cycle, sort_keys=True, separators=(",", ":")) + "\n"
            )

    @staticmethod
    def _is_newer_debug_snapshot(
        candidate: dict[str, Any], current: dict[str, Any] | None
    ) -> bool:
        if current is None:
            return True
        candidate_clock = candidate.get("clock_domain")
        current_clock = current.get("clock_domain")
        if candidate_clock != current_clock:
            return False
        candidate_state_time = candidate.get("state_timestamp_us")
        current_state_time = current.get("state_timestamp_us")
        if isinstance(candidate_state_time, int) and isinstance(
            current_state_time, int
        ):
            if candidate_state_time != current_state_time:
                return candidate_state_time > current_state_time
        candidate_request_time = candidate.get("metadata", {}).get("requested_at")
        current_request_time = current.get("metadata", {}).get("requested_at")
        if isinstance(candidate_request_time, str) and isinstance(
            current_request_time, str
        ):
            return candidate_request_time > current_request_time
        return False

    def _publish(self, event_type: str, data: dict[str, Any]) -> None:
        self._event_id += 1
        self._events.append({"id": self._event_id, "type": event_type, "data": data})
        self._condition.notify_all()


def semantic_changes(
    previous: dict[str, Any] | None, current: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Return concise typed changes used by the transition inspector."""
    if current is None:
        return []
    current_humans = {
        item["track_id"]: item for item in current.get("humans", [])
    }
    if previous is None:
        changes = [
            {"entity": track_id, "field": "track", "from": None, "to": "DETECTED"}
            for track_id in current_humans
        ]
        if not changes:
            changes.append(
                {"entity": "scene", "field": "state", "from": None, "to": "INITIALIZED"}
            )
        return changes

    previous_humans = {
        item["track_id"]: item for item in previous.get("humans", [])
    }
    changes: list[dict[str, Any]] = []
    for track_id in sorted(current_humans.keys() - previous_humans.keys()):
        changes.append(
            {"entity": track_id, "field": "track", "from": None, "to": "DETECTED"}
        )
    for track_id in sorted(previous_humans.keys() - current_humans.keys()):
        changes.append(
            {"entity": track_id, "field": "track", "from": "TRACKED", "to": "REMOVED"}
        )
    paths = (
        ("motion_relation",),
        ("distance_trend",),
        ("proxemic_zone",),
        ("observed",),
        ("predicted_only",),
        ("distance_m",),
        ("closing_speed_mps",),
        ("attention", "state"),
        ("engagement", "state"),
    )
    for track_id in sorted(current_humans.keys() & previous_humans.keys()):
        before = previous_humans[track_id]
        after = current_humans[track_id]
        for path in paths:
            old = _nested(before, path)
            new = _nested(after, path)
            if isinstance(old, float):
                old = round(old, 3)
            if isinstance(new, float):
                new = round(new, 3)
            if old != new:
                changes.append(
                    {
                        "entity": track_id,
                        "field": ".".join(path),
                        "from": old,
                        "to": new,
                    }
                )
    return changes


def _nested(value: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
