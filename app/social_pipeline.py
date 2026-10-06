"""Compose raw tracking and social estimation identically for live and replay."""
from threading import Lock

from app.commands import CommandConfig, CommandPlanner, ExecutionEvent
from app.pipeline import TrackingPipeline, TrackTraceWriter, TrackingProcessingError
from app.policy.rules import decide
from app.policy.target_lock import LockConfig, TargetLockController
from app.state.estimator import SocialStateEstimator
from app.state.social_models import TemporalConfig
from app.state.tracks import TrackConfig


class SocialPipeline:
    def __init__(self, session_id, track_config=None, temporal_config=None,
                 recording=None, tracking_trace=None, social_trace=None,
                 lock_config=None, lock_trace=None, command_config=None,
                 command_trace=None, execution_trace=None):
        track_config = track_config or TrackConfig()
        temporal_config = temporal_config or TemporalConfig()
        if track_config.history_window_s < temporal_config.window_s:
            raise ValueError("tracking history must cover the temporal window")
        if track_config.max_samples_per_track < temporal_config.min_samples:
            raise ValueError("tracking sample cap must cover minimum temporal samples")
        self.tracking = TrackingPipeline(session_id, track_config, recording, tracking_trace)
        self.estimator = SocialStateEstimator(temporal_config)
        self.social_trace = social_trace
        self.lock_trace = lock_trace
        self.command_trace = command_trace
        self.lock = TargetLockController(lock_config or LockConfig())
        self.commands = CommandPlanner(command_config or CommandConfig(), execution_trace)
        self._lock = Lock()

    def process(self, frame):
        with self._lock:
            snapshot = self.tracking.process(frame)
            try:
                state = self.estimator.update(snapshot).model_dump(mode="json")
            except Exception as error:
                raise TrackingProcessingError("social_estimation") from error
            if self.social_trace is not None:
                try:
                    self.social_trace.write(state)
                except Exception as error:
                    raise TrackingProcessingError("social_trace_write") from error
            try:
                proposal = decide(state).model_dump(mode="json")
            except Exception as error:
                raise TrackingProcessingError("policy_decision") from error
            try:
                target_lock = self.lock.update(state, proposal).model_dump(mode="json")
            except Exception as error:
                raise TrackingProcessingError("target_lock") from error
            if self.lock_trace is not None:
                try:
                    self.lock_trace.write(target_lock)
                except Exception as error:
                    raise TrackingProcessingError("lock_trace_write") from error
            try:
                command = self.commands.plan(state, target_lock)
            except Exception as error:
                raise TrackingProcessingError("command_planning") from error
            if self.command_trace is not None:
                try:
                    self.command_trace.write({"source_state_id": state["state_id"], "command": command})
                except Exception as error:
                    raise TrackingProcessingError("command_trace_write") from error
            return {**snapshot, "social_state": state, "policy_decision": proposal,
                    "target_lock": target_lock, "robot_command": command}

    def record_execution_event(self, payload):
        with self._lock:
            event = ExecutionEvent.model_validate(payload)
            duplicate = self.commands.record_event(event)
            if not duplicate and event.status in {"COMPLETED", "FAILED", "CANCELLED", "REJECTED"}:
                command = self.commands.commands[event.command_id]
                self.lock.finish_execution(event.lock_id, command.action, event.status,
                                           event.robot_timestamp_us)
            return duplicate
