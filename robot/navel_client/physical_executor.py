"""Run one validated robot action script at a time with bounded stop hooks."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from robot.navel_client.command_dispatch import CommandRejected, parse_command


logger = logging.getLogger(__name__)


class ScriptError(RuntimeError):
    """A script did not return a successful bounded result."""


@dataclass(frozen=True)
class ScriptPaths:
    approach: Path
    engage: Path
    pause_route: Path
    resume_route: Path
    stop: Path

    def __post_init__(self) -> None:
        for name in ("approach", "engage", "pause_route", "resume_route", "stop"):
            path = Path(getattr(self, name)).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f"{name} script must be an existing file: {path}")
            if path.suffix != ".py" and not os.access(path, os.X_OK):
                raise ValueError(f"{name} script must be executable: {path}")
            object.__setattr__(self, name, path)

    def path_for(self, name: str) -> Path:
        return getattr(self, name)


@dataclass
class ActiveAction:
    command: dict[str, Any]
    observation: dict[str, Any]
    task: asyncio.Task | None = None
    process: asyncio.subprocess.Process | None = None
    cancel_reason: str | None = None
    started_at_us: int | None = None


class PhysicalCommandExecutor:
    """Keep route ownership local; subprocesses never run from unvalidated replies."""

    def __init__(self, scripts: ScriptPaths, *, max_age_s: float = 1.0,
                 response_timeout_s: float = 1.0, hook_timeout_s: float = 2.0,
                 stop_timeout_s: float = 2.0, max_commands: int = 256,
                 monotonic: Callable[[], float] = time.monotonic,
                 monotonic_us: Callable[[], int] = lambda: time.monotonic_ns() // 1000):
        if (not 0 < max_age_s <= 5 or not 0 < response_timeout_s <= 30
                or not 0 < hook_timeout_s <= 30 or not 0 < stop_timeout_s <= 30
                or not 1 <= max_commands <= 10000):
            raise ValueError("invalid physical executor limits")
        self.scripts = scripts
        self.max_age_us = round(max_age_s * 1_000_000)
        self.response_timeout_s = response_timeout_s
        self.hook_timeout_s = hook_timeout_s
        self.stop_timeout_s = stop_timeout_s
        self.max_commands = max_commands
        self.monotonic = monotonic
        self.monotonic_us = monotonic_us
        self.session_id: str | None = None
        self.commands: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.failed_locks: set[str] = set()
        self.event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.active: ActiveAction | None = None
        self.route_paused = False
        self.last_valid_response_at: float | None = None
        self.fault: str | None = None
        self._idle_stop_sent = False
        self.closed = False
        self._lock = asyncio.Lock()

    def _emit(self, command: Mapping[str, Any], status: str, reason: str) -> None:
        command_id = command["command_id"]
        sequence = len(self.events.get(command_id, [])) + 1
        event = {
            "schema_version": 1, "event_version": "execution-event-v1",
            "event_id": f"{command_id}:{sequence}", "command_id": command_id,
            "session_id": command["session_id"], "lock_id": command["lock_id"],
            "source_state_id": command["source_state_id"], "sequence": sequence,
            "status": status, "reason": reason[:128],
            "robot_timestamp_us": max(self.monotonic_us(), command["source_robot_timestamp_us"]),
        }
        self.events.setdefault(command_id, []).append(event)
        if status in {"FAILED", "CANCELLED", "REJECTED"}:
            self.failed_locks.add(command["lock_id"])
        self.event_queue.put_nowait(event)
        logger.info("physical_execution=%s", json.dumps({
            "command_id": command_id, "status": status, "reason": reason[:128]},
            separators=(",", ":")))

    @staticmethod
    async def _terminate_group(process: asyncio.subprocess.Process) -> None:
        # start_new_session gives each script an isolated process group.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.communicate(), 0.5)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.communicate()

    async def _script(self, name: str, request: dict[str, Any], timeout_s: float,
                      active: ActiveAction | None = None) -> None:
        path = self.scripts.path_for(name)
        argv = [sys.executable, str(path)] if path.suffix == ".py" else [str(path)]
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True))
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            try:
                process = await spawn
            except OSError:
                pass
            else:
                await self._terminate_group(process)
            raise
        except OSError as error:
            raise ScriptError(f"{name.upper()}_START_FAILED") from error
        if active is not None:
            active.process = process
            active.started_at_us = self.monotonic_us()
            self._emit(active.command, "STARTED", "SCRIPT_STARTED")
        body = (json.dumps(request, allow_nan=False, separators=(",", ":")) + "\n").encode()
        try:
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(body), timeout_s)
            except asyncio.TimeoutError as error:
                raise ScriptError(f"{name.upper()}_TIMEOUT") from error
            if process.returncode != 0:
                logger.warning("script=%s exit=%s stderr=%s", name, process.returncode,
                               stderr.decode("utf-8", "replace")[:300])
                raise ScriptError(f"{name.upper()}_EXIT_{process.returncode}")
            try:
                result = json.loads(stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ScriptError(f"{name.upper()}_INVALID_RESULT") from error
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ScriptError(f"{name.upper()}_NOT_CONFIRMED")
        finally:
            await self._terminate_group(process)
            if active is not None:
                active.process = None

    @staticmethod
    def _request(command: Mapping[str, Any] | None, observation: Mapping[str, Any] | None,
                 reason: str) -> dict[str, Any]:
        return {"interface_version": "navel-action-script-v1", "command": dict(command) if command else None,
                "observation": dict(observation) if observation else None, "reason": reason}

    async def _stop(self, command: Mapping[str, Any] | None,
                    observation: Mapping[str, Any] | None, reason: str) -> bool:
        try:
            await self._script("stop", self._request(command, observation, reason), self.stop_timeout_s)
            return True
        except (ScriptError, asyncio.CancelledError) as error:
            self.fault = "STOP_FAILED"
            logger.error("physical_stop_failed=%s", error)
            return False

    async def _pause_route(self, observation: Mapping[str, Any]) -> bool:
        if self.route_paused:
            return True
        try:
            await self._script("pause_route", self._request(None, observation, "TARGET_LOCKED"),
                               self.hook_timeout_s)
        except ScriptError as error:
            self.fault = "ROUTE_PAUSE_FAILED"
            logger.error("route_pause_failed=%s", error)
            await self._stop(None, observation, "ROUTE_PAUSE_FAILED")
            return False
        self.route_paused = True
        self._idle_stop_sent = False
        return True

    async def _resume_route(self, observation: Mapping[str, Any]) -> None:
        if not self.route_paused or self.active is not None or self.fault is not None:
            return
        try:
            await self._script("resume_route", self._request(None, observation, "LOCK_RELEASED"),
                               self.hook_timeout_s)
        except ScriptError as error:
            self.fault = "ROUTE_RESUME_FAILED"
            logger.error("route_resume_failed=%s", error)
            await self._stop(None, observation, "ROUTE_RESUME_FAILED")
        else:
            self.route_paused = False
            self._idle_stop_sent = False

    async def _run_action(self, active: ActiveAction) -> None:
        command = active.command
        status, reason = "COMPLETED", "SCRIPT_COMPLETED"
        try:
            await self._script(command["action"].lower(),
                               self._request(command, active.observation, "EXECUTE"),
                               command["execution_lease_us"] / 1_000_000, active)
        except asyncio.CancelledError:
            status, reason = "CANCELLED", active.cancel_reason or "EXECUTOR_CANCELLED"
        except ScriptError as error:
            status, reason = "FAILED", str(error)
        if not await self._stop(command, active.observation, reason):
            status, reason = "FAILED", "STOP_FAILED"
        self._idle_stop_sent = True
        if self.active is active:
            self.active = None
        self._emit(command, status, reason)

    async def _cancel_active(self, reason: str) -> None:
        active = self.active
        if active is None or active.task is None:
            return
        active.cancel_reason = reason
        active.task.cancel()
        await asyncio.gather(active.task, return_exceptions=True)
        if self.active is active:
            stopped = await self._stop(active.command, active.observation, reason)
            self._idle_stop_sent = True
            self.active = None
            self._emit(active.command, "REJECTED" if stopped else "FAILED",
                       reason if stopped else "STOP_FAILED")

    @staticmethod
    def _active_authorized(active: ActiveAction, payload: Mapping[str, Any]) -> bool:
        lock = payload.get("target_lock")
        if not isinstance(lock, Mapping) or lock.get("status") != "LOCKED":
            return False
        effective = lock.get("effective_decision")
        command = active.command
        return (isinstance(effective, Mapping)
                and lock.get("lock_id") == command["lock_id"]
                and effective.get("decision") == command["action"]
                and (effective.get("target_uid"), effective.get("target_track_epoch"))
                == (command["target_uid"], command["target_track_epoch"]))

    @staticmethod
    def _preflight(command: Mapping[str, Any], observation: Mapping[str, Any]) -> str | None:
        robot = observation.get("robot")
        if not isinstance(robot, Mapping):
            return "ROBOT_MOTION_UNKNOWN"
        linear, angular = robot.get("linear_velocity"), robot.get("angular_velocity")
        if (isinstance(linear, bool) or not isinstance(linear, (int, float))
                or isinstance(angular, bool) or not isinstance(angular, (int, float))
                or not math.isfinite(linear) or not math.isfinite(angular)):
            return "ROBOT_MOTION_UNKNOWN"
        if abs(linear) > 0.02 or abs(angular) > 0.03:
            return "ROBOT_NOT_STATIONARY"
        if command["action"] == "APPROACH":
            safety = observation.get("safety")
            if not isinstance(safety, Mapping):
                return "FRONT_RANGE_UNAVAILABLE"
            lidar, sonar = safety.get("lidar"), safety.get("sonar")
            front = ([lidar[0]] if isinstance(lidar, list) and len(lidar) >= 1 else [])
            front += sonar[:2] if isinstance(sonar, list) and len(sonar) >= 2 else []
            if (len(front) != 3 or any(isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(value)
                or value <= 0 for value in front)):
                return "FRONT_RANGE_UNAVAILABLE"
        return None

    async def accept(self, payload: Mapping[str, Any], observation: Mapping[str, Any]) -> None:
        async with self._lock:
            if self.closed:
                raise CommandRejected("executor_closed")
            supplied = payload.get("robot_command")
            command_id = supplied.get("command_id") if isinstance(supplied, Mapping) else None
            known = self.commands.get(command_id) if isinstance(command_id, str) else None
            try:
                command = parse_command(payload, observation, self.monotonic_us(),
                                        self.max_age_us, known_command=known)
            except CommandRejected:
                await self._cancel_active("INVALID_RESPONSE")
                raise
            social = payload["social_state"]
            if self.session_id is not None and social["session_id"] != self.session_id:
                await self._cancel_active("SESSION_CHANGED")
                raise CommandRejected("session_changed_restart_client")
            self.session_id = social["session_id"]
            self.last_valid_response_at = self.monotonic()
            self._idle_stop_sent = False
            cancelled = False
            if self.active is not None and not self._active_authorized(self.active, payload):
                await self._cancel_active("TARGET_OR_DECISION_CHANGED")
                cancelled = True
            lock_status = payload["target_lock"]["status"]
            if lock_status in {"LOCKED", "MISSING", "TENTATIVE_RETURN", "AMBIGUOUS"}:
                if self.fault is None:
                    await self._pause_route(observation)
            elif lock_status in {"UNLOCKED", "COOLDOWN"}:
                await self._resume_route(observation)
            if command is None or known is not None:
                return
            if len(self.commands) >= self.max_commands:
                self.fault = "COMMAND_CAPACITY_REACHED"
                raise CommandRejected("command_capacity_reached")
            self.commands[command_id] = command
            self._emit(command, "RECEIVED", "COMMAND_VALIDATED")
            if (self.monotonic_us() > command["expires_at_robot_us"]
                    or self.monotonic_us() - observation["timestamp"] > self.max_age_us
                    or self.monotonic() - self.last_valid_response_at > self.response_timeout_s):
                self._emit(command, "REJECTED", "COMMAND_EXPIRED_BEFORE_START")
                return
            preflight = self._preflight(command, observation)
            if preflight is not None:
                self._emit(command, "REJECTED", preflight)
                return
            if (cancelled or self.fault is not None or not self.route_paused
                    or self.active is not None or command["lock_id"] in self.failed_locks):
                self._emit(command, "REJECTED", "EXECUTOR_NOT_READY")
                return
            active = ActiveAction(command, dict(observation))
            self.active = active
            active.task = asyncio.create_task(self._run_action(active))

    async def invalidate(self, reason: str) -> None:
        async with self._lock:
            self.last_valid_response_at = None
            await self._cancel_active(reason.upper())
            if self.route_paused and not self._idle_stop_sent:
                await self._stop(None, None, reason.upper())
                self._idle_stop_sent = True

    async def feedback_failed(self, reason: str) -> None:
        async with self._lock:
            self.fault = reason
            await self._cancel_active("FEEDBACK_REJECTED")
            if self.route_paused and not self._idle_stop_sent:
                await self._stop(None, None, "FEEDBACK_REJECTED")
                self._idle_stop_sent = True

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            async with self._lock:
                if self.active is None:
                    if (self.route_paused and not self._idle_stop_sent
                            and self.last_valid_response_at is not None
                            and self.monotonic() - self.last_valid_response_at > self.response_timeout_s):
                        await self._stop(None, None, "RESPONSE_TIMEOUT")
                        self._idle_stop_sent = True
                    continue
                if (self.last_valid_response_at is None
                        or self.monotonic() - self.last_valid_response_at > self.response_timeout_s):
                    await self._cancel_active("RESPONSE_TIMEOUT")
                elif (self.active.started_at_us is not None
                      and self.monotonic_us() - self.active.started_at_us
                      > self.active.command["execution_lease_us"]):
                    await self._cancel_active("EXECUTION_LEASE_EXPIRED")

    async def close(self) -> None:
        async with self._lock:
            self.closed = True
            await self._cancel_active("CLIENT_STOPPED")
            if self.route_paused:
                await self._stop(None, None, "CLIENT_STOPPED")
