"""Bounded, output-only live model work; model decisions never enter the pipeline."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
from threading import Condition, Lock, Thread
import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.camera.live import LiveCameraCache, LiveFrame
from app.inference.ollama import OllamaClient, OllamaConfig
from app.policy.llm import PROMPT_VERSION as LLM_PROMPT_VERSION, build_llm_prompt, decide_llm
from app.policy.vlm import PROMPT_VERSION as VLM_PROMPT_VERSION, decide_vlm
from app.state.social_models import SocialState


logger = logging.getLogger("app.live_models")


class LiveModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)

    mode: Literal["disabled", "llm", "vlm", "both"] = "disabled"
    llm_config: OllamaConfig | None = None
    vlm_config: OllamaConfig | None = None
    sample_interval_s: float = Field(default=1.0, ge=0)
    queue_capacity: int = Field(default=4, ge=1)
    max_camera_age_s: float = Field(default=1.0, ge=0)
    allow_receipt_match: bool = False
    camera_cache_capacity: int = Field(default=8, ge=1)
    camera_cache_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1)

    @field_validator("sample_interval_s", "max_camera_age_s")
    @classmethod
    def microsecond_duration(cls, value: float) -> float:
        if not math.isfinite(value * 1_000_000) or 0 < value < 0.000001:
            raise ValueError("duration must be zero or at least one microsecond, with finite microseconds")
        return value

    @model_validator(mode="after")
    def required_models(self) -> LiveModelConfig:
        if self.mode in ("llm", "both") and self.llm_config is None:
            raise ValueError("selected LLM mode requires llm_config")
        if self.mode in ("vlm", "both") and self.vlm_config is None:
            raise ValueError("selected VLM mode requires vlm_config")
        return self


@dataclass(frozen=True)
class _Job:
    state_id: str
    session_id: str
    ingest_sequence: int
    timestamp_us: int
    state_json: str
    source_json: str
    matching_json: str | None
    frame: LiveFrame | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveModelRunner:
    """One worker plus a bounded waiting queue, with drop-newest overload.

    Sampling consumes a selected source moment even when the queue is full.
    Dropped/cancelled jobs are audited without making model calls. Network calls
    and image encoding never hold the scheduling or output lock. Audit failures
    are reported through health and stop subsequent model work, not ingestion.
    """

    def __init__(self, config: LiveModelConfig, output_path: Path | str | None, *,
                 camera_cache: LiveCameraCache | None = None,
                 client_factory: Callable[[OllamaConfig], Any] | None = None) -> None:
        self.config = LiveModelConfig.model_validate(config.model_dump(mode="python"))
        if self.config.mode != "disabled" and output_path is None:
            raise ValueError("enabled model inference requires a new output path")
        self.output_path = Path(output_path) if output_path is not None else None
        self.camera_cache = camera_cache
        self.client_factory = client_factory if client_factory is not None else OllamaClient
        self._pid = os.getpid()
        self._condition = Condition()
        self._writer_lock = Lock()
        self._pending: deque[_Job] = deque()
        self._thread: Thread | None = None
        self._output = None
        self._closing = self._closed = False
        self._shutdown_audits = 0
        self._active: _Job | None = None
        self._sample_session: str | None = None
        self._last_selected_us: int | None = None
        self._clients: dict[str, Any] = {}
        self._write_error: str | None = None
        self._last_error: str | None = None
        self._stats = dict.fromkeys(("submitted", "selected", "sampled_out", "enqueued", "queue_dropped",
                                    "not_run", "completed", "successes", "failures", "llm_calls",
                                    "vlm_calls", "image_unavailable", "image_invalid", "write_failures"), 0)
        self.start()

    def start(self) -> None:
        """Start once in the creating process; never duplicate a forked worker."""
        if os.getpid() != self._pid:
            raise ValueError("a live model runner cannot start in a different process")
        with self._condition:
            if self.config.mode == "disabled":
                return
            if self._closed or self._closing:
                raise ValueError("a closed live model runner cannot restart")
            if self._thread is not None:
                return
            assert self.output_path is not None
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self._output = self.output_path.open("x", encoding="utf-8")
            self._thread = Thread(target=self._work, name=f"live-model-{self._pid}", daemon=True)
            try:
                self._thread.start()
            except Exception:
                self._output.close()
                self._closed = True
                raise

    def _freeze(self, state: SocialState | dict[str, Any], source: dict[str, Any]) -> _Job:
        snapshot = SocialState.model_validate(state.model_dump(mode="python", warnings=False)
                                             if isinstance(state, SocialState) else state)
        prompt = build_llm_prompt(snapshot)
        if not isinstance(source, dict):
            raise ValueError("model source metadata must be an object")
        if any(key in source for key in ("policy_decision", "target_lock", "robot_command")):
            raise ValueError("rule, target lock and command data must not enter model work")
        if (type(source.get("observation_timestamp_us")) is not int
                or source["observation_timestamp_us"] != snapshot.robot_timestamp_us):
            raise ValueError("observation source timestamp must equal the SocialState collection timestamp")
        source_json = json.dumps(source, ensure_ascii=False, sort_keys=True, allow_nan=False)
        detached_source = json.loads(source_json)
        matching_json = None
        frame = None
        if self.config.mode in ("vlm", "both"):
            if self.camera_cache is None:
                matching = {"status": "unmatched", "ok": False, "method": None, "frame": None,
                            "error": {"category": "camera_cache_unavailable",
                                      "message": "No live head-camera cache is configured", "details": {}},
                            "limitations": ["No camera association was established."]}
            else:
                association = self.camera_cache.associate(
                    detached_source.get("model_source"),
                    max_age_us=math.floor(self.config.max_camera_age_s * 1_000_000),
                    allow_receipt=self.config.allow_receipt_match)
                matching, frame = association.to_dict(), association.frame
            matching_json = json.dumps(matching, ensure_ascii=False, sort_keys=True, allow_nan=False)
        return _Job(snapshot.state_id, snapshot.session_id, snapshot.ingest_sequence,
                    snapshot.robot_timestamp_us, prompt.social_state_json, source_json, matching_json, frame)

    def submit(self, social_state: SocialState | dict[str, Any], source_metadata: dict[str, Any]) -> dict[str, Any]:
        """Freeze and schedule promptly; model/audit errors never escape to ingestion."""
        if os.getpid() != self._pid:
            return {"status": "not_run_process_mismatch", "selected": False}
        if self.config.mode == "disabled":
            return {"status": "disabled", "selected": False}
        try:
            job = self._freeze(social_state, source_metadata)
        except Exception as error:
            with self._condition:
                self._stats["failures"] += 1
                self._last_error = f"invalid model input: {error}"
            return {"status": "input_invalid", "selected": False, "error": str(error)}
        audit_status = None
        with self._condition:
            self._stats["submitted"] += 1
            if self._closed or self._closing:
                audit_status = "not_run_shutdown"
                self._stats["not_run"] += 1
            elif self._write_error:
                audit_status = "not_run_audit_failure"
                self._stats["not_run"] += 1
            else:
                if self._sample_session != job.session_id:
                    self._sample_session, self._last_selected_us = job.session_id, None
                interval_us = round(self.config.sample_interval_s * 1_000_000)
                if self._last_selected_us is not None and (
                        job.timestamp_us <= self._last_selected_us
                        or job.timestamp_us - self._last_selected_us < interval_us):
                    self._stats["sampled_out"] += 1
                    return {"status": "sampled_out", "selected": False, "source_state_id": job.state_id}
                self._last_selected_us = job.timestamp_us
                self._stats["selected"] += 1
                if len(self._pending) >= self.config.queue_capacity:
                    audit_status = "queue_dropped"
                    self._stats["queue_dropped"] += 1
                else:
                    self._pending.append(job)
                    self._stats["enqueued"] += 1
                    self._condition.notify_all()
                    return {"status": "enqueued", "selected": True, "source_state_id": job.state_id,
                            "pending": len(self._pending)}
        self._write_row(self._not_run_row(job, audit_status))
        return {"status": audit_status, "selected": audit_status == "queue_dropped",
                "source_state_id": job.state_id}

    def _policy_base(self, job: _Job, policy: str) -> dict[str, Any]:
        config = self.config.llm_config if policy == "llm" else self.config.vlm_config
        data = {"status": "not_run_disabled", "prompt_version": (
            LLM_PROMPT_VERSION if policy == "llm" else VLM_PROMPT_VERSION),
                "source_state_id": job.state_id, "session_id": job.session_id,
                "source_robot_timestamp_us": job.timestamp_us, "ok": None,
                "decision": None, "error": None, "raw_content": None, "request_duration_s": None,
                "requested_model": config.model if config else None, "returned_model": None,
                "ollama_configuration": ({**config.model_dump(mode="json"),
                                           "generation_options": config.generation_options()} if config else None),
                "error_stage": None}
        if policy == "vlm":
            data.update(image_matching=json.loads(job.matching_json) if job.matching_json else None,
                        verified_image_sha256=None, image_encoding=None)
        return data

    def _base_row(self, job: _Job) -> dict[str, Any]:
        return {"schema_version": 1, "mode": self.config.mode, "source_state_id": job.state_id,
                "session_id": job.session_id, "ingest_sequence": job.ingest_sequence,
                "source_robot_timestamp_us": job.timestamp_us, "social_state": json.loads(job.state_json),
                "social_state_json": job.state_json, "source": json.loads(job.source_json),
                "scheduling": {"sample_interval_us": round(self.config.sample_interval_s * 1_000_000),
                               "queue_capacity": self.config.queue_capacity,
                               "overload_policy": "drop_newest; selected sample interval is consumed"},
                "completion_clock": "inference-host UTC wall clock; independent of robot and receiver source clocks"}

    def _enabled(self, policy: str) -> bool:
        return self.config.mode in (policy, "both")

    def _not_run_row(self, job: _Job, status: str) -> dict[str, Any]:
        row = self._base_row(job)
        row["scheduling"]["status"] = status
        for policy in ("llm", "vlm"):
            inference = self._policy_base(job, policy)
            if self._enabled(policy):
                inference.update(status=status, error_stage="scheduling",
                                 error={"category": status, "message": "Selected model work was not run",
                                        "http_status": None})
            inference["completed_at"] = _utc_now()
            row[policy + "_inference"] = inference
        row["completed_at"] = _utc_now()
        return row

    def _infer(self, job: _Job, policy: str) -> dict[str, Any]:
        inference = self._policy_base(job, policy)
        if not self._enabled(policy):
            inference["completed_at"] = _utc_now()
            return inference
        stage = "image_input" if policy == "vlm" else "ollama"
        try:
            encoded = None
            if policy == "vlm":
                if job.frame is None:
                    matching = inference["image_matching"]
                    error = matching.get("error") or {}
                    inference.update(status="input_unavailable", error_stage="image_input",
                                     error={"category": error.get("category", "image_unavailable"),
                                            "message": error.get("message", "No usable head image"),
                                            "http_status": None})
                    with self._condition:
                        self._stats["image_unavailable"] += 1
                    inference["completed_at"] = _utc_now()
                    return inference
                encoded = job.frame.encode()
                inference.update(verified_image_sha256=encoded.source_image_sha256,
                                 image_encoding=encoded.to_dict())
            stage = "ollama"
            config = self.config.llm_config if policy == "llm" else self.config.vlm_config
            if policy not in self._clients:
                self._clients[policy] = self.client_factory(config)
            with self._condition:
                self._stats[policy + "_calls"] += 1
            if policy == "llm":
                state = SocialState.model_validate_json(job.state_json)
                result = decide_llm(state, self._clients[policy])
            else:
                result = decide_vlm(encoded.image_base64, self._clients[policy])
            inference.update(result.to_dict(), status="succeeded" if result.ok else "failed")
            if not result.ok:
                inference["error_stage"] = "ollama"
            with self._condition:
                self._stats["successes" if result.ok else "failures"] += 1
        except Exception as error:
            inference.update(status="input_invalid" if stage == "image_input" else "failed",
                             ok=None if stage == "image_input" else False,
                             error_stage=stage, error={"category": (
                                 "image_encoding_error" if stage == "image_input" else "model_worker_error"),
                                 "message": str(error), "http_status": None})
            with self._condition:
                self._stats["failures"] += 1
                self._stats["image_invalid"] += stage == "image_input"
                self._last_error = str(error)
        inference["completed_at"] = _utc_now()
        return inference

    def _write_row(self, row: dict[str, Any]) -> bool:
        try:
            encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
            with self._writer_lock:
                if self._output is None or self._output.closed:
                    raise ValueError("model audit output is closed")
                self._output.write(encoded)
                self._output.flush()
            return True
        except Exception as error:
            logger.exception("Could not write live model audit output")
            with self._condition:
                self._write_error = str(error)
                self._stats["write_failures"] += 1
                self._last_error = f"model audit write failed: {error}"
            return False

    def _work(self) -> None:
        try:
            while True:
                with self._condition:
                    while not self._pending and (not self._closing or self._shutdown_audits):
                        self._condition.wait()
                    if not self._pending:
                        break
                    job = self._pending.popleft()
                    self._active = job
                    audit_failed = self._write_error is not None
                if audit_failed:
                    row = self._not_run_row(job, "not_run_audit_failure")
                    with self._condition:
                        self._stats["not_run"] += 1
                else:
                    row = self._base_row(job)
                    row["scheduling"]["status"] = "completed"
                    row["llm_inference"] = self._infer(job, "llm")
                    row["vlm_inference"] = self._infer(job, "vlm")
                    row["completed_at"] = _utc_now()
                self._write_row(row)
                with self._condition:
                    self._stats["completed"] += 1
                    self._active = None
                    self._condition.notify_all()
        finally:
            with self._writer_lock:
                if self._output is not None:
                    try:
                        self._output.close()
                    except OSError as error:
                        with self._condition:
                            self._write_error = str(error)
            with self._condition:
                self._active = None
                self._closed = True
                self._condition.notify_all()

    def wait_idle(self, timeout: float) -> bool:
        """Wait for all accepted work/audit flushing, using host wait time only."""
        if os.getpid() != self._pid:
            return False
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._pending or self._active is not None or self._shutdown_audits:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def status(self) -> dict[str, Any]:
        if os.getpid() != self._pid:
            return {"mode": self.config.mode, "running": False, "process_mismatch": True,
                    "pid": self._pid, "active": False, "pending": 0}
        with self._condition:
            return {"mode": self.config.mode, "enabled": self.config.mode != "disabled", "pid": self._pid,
                    "running": self._thread is not None and self._thread.is_alive(),
                    "active": self._active is not None,
                    "active_state_id": self._active.state_id if self._active else None,
                    "pending": len(self._pending), "queue_capacity": self.config.queue_capacity,
                    "closing": self._closing, "closed": self._closed,
                    "write_error": self._write_error, "last_error": self._last_error,
                    "output": str(self.output_path) if self.output_path else None, **self._stats}

    def close(self, timeout: float | None = None) -> bool:
        """Cancel queued jobs, audit them, then flush the active job within a bound.

        An in-flight synchronous HTTP call cannot be cancelled. The default wait
        is the sum of selected model timeouts plus five seconds. A false return
        explicitly reports that the daemon worker has not finished flushing yet.
        """
        if os.getpid() != self._pid:
            return False
        if timeout is None:
            timeout = sum(config.timeout_seconds for policy, config in (
                ("llm", self.config.llm_config), ("vlm", self.config.vlm_config))
                if self._enabled(policy) and config is not None) + 5.0
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        with self._condition:
            if self.config.mode == "disabled":
                self._closed = True
                return True
            if self._closed:
                return True
            self._closing = True
            cancelled = list(self._pending)
            self._pending.clear()
            self._stats["not_run"] += len(cancelled)
            self._shutdown_audits += len(cancelled)
        for job in cancelled:
            self._write_row(self._not_run_row(job, "not_run_shutdown"))
        with self._condition:
            self._shutdown_audits -= len(cancelled)
            self._condition.notify_all()
        assert self._thread is not None
        self._thread.join(timeout)
        return not self._thread.is_alive()
