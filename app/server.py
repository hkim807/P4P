"""Receive validated raw sensor frames and append them to JSONL."""

from __future__ import annotations

import argparse
import atexit
from contextlib import nullcontext
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from flask import Flask, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from app.domain.models import RawObservationFrame
from app.commands import CommandConfig, ExecutionEvent, FeedbackRejected
from app.recording import RecordingWriter, TimestampOrderError
from app.sdk_capture import SdkCaptureOrderError, SdkCaptureWriter, validate_sdk_capture
from app.camera.capture import CameraCaptureOrderError, CameraCaptureWriter, validate_camera_record
from app.pipeline import TrackTraceWriter, TrackingPipeline, TrackingProcessingError
from app.policy.target_lock import LockConfig
from app.state.tracks import TrackConfig
from app.social_pipeline import SocialPipeline
from app.state.social_models import TemporalConfig
from app.camera.live import LiveCameraCache, validate_model_source
from app.inference.live import LiveModelConfig, LiveModelRunner
from app.inference.ollama import OllamaConfig


logger = logging.getLogger(__name__)


def _validate_model_outputs(paths: list[str | Path | None]) -> None:
    """Preflight every destination before enabling the exclusive model writer."""
    resolved = []
    for value in paths:
        if value is None:
            continue
        path = Path(value)
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"Receiver output already exists: {path}; choose a new path")
        resolved.append(path.resolve())
    for index, path in enumerate(resolved):
        for other in resolved[index + 1:]:
            if path == other or path in other.parents or other in path.parents:
                raise ValueError("Receiver output paths must be distinct and cannot contain one another")


def create_app(output_path: str | Path | None = None, *,
               tracking_output: str | Path | None = None,
               track_config: TrackConfig | None = None,
               session_id: str | None = None,
               social_output: str | Path | None = None,
               temporal_config: TemporalConfig | None = None,
               lock_output: str | Path | None = None,
               lock_config: LockConfig | None = None,
               command_output: str | Path | None = None,
               execution_output: str | Path | None = None,
               command_config: CommandConfig | None = None,
               sdk_output: str | Path | None = None,
               camera_output_dir: str | Path | None = None,
               model_config: LiveModelConfig | None = None,
               model_output: str | Path | None = None) -> Flask:
    """Start a fresh JSONL recording; None prints accepted frames to stdout."""
    app = Flask(__name__)
    model_config = model_config or LiveModelConfig()
    # Revalidate detached values, including callers' modified nested models.
    model_config = LiveModelConfig.model_validate(model_config.model_dump(mode="python"))
    models_enabled = model_config.mode != "disabled"
    vision_enabled = model_config.mode in {"vlm", "both"}
    if models_enabled != (model_output is not None):
        raise ValueError("Enabled model inference requires --model-output; disabled mode cannot use it")
    outputs = (output_path, tracking_output, social_output, lock_output, command_output,
               execution_output, sdk_output, camera_output_dir, model_output)
    if models_enabled:
        _validate_model_outputs(list(outputs))
    app.config["MAX_CONTENT_LENGTH"] = 36 * 1024 * 1024 if camera_output_dir or vision_enabled else 1024 * 1024
    paths = [Path(p).resolve() for p in (output_path, tracking_output, social_output,
                                        lock_output, command_output, execution_output,
                                        sdk_output, camera_output_dir, model_output) if p is not None]
    if len(set(paths)) != len(paths):
        raise ValueError("All receiver output paths must be different")
    recording = RecordingWriter(output_path)
    sdk_recording = SdkCaptureWriter(sdk_output) if sdk_output is not None else None
    camera_recording = CameraCaptureWriter(camera_output_dir) if camera_output_dir is not None else None
    pipeline = None
    if models_enabled or any(p is not None for p in (social_output, lock_output, command_output, execution_output)):
        pipeline = SocialPipeline(session_id or f"live-{uuid4().hex}", track_config,
                                  temporal_config, recording,
                                  TrackTraceWriter(tracking_output) if tracking_output else None,
                                  TrackTraceWriter(social_output) if social_output else None,
                                  lock_config, TrackTraceWriter(lock_output) if lock_output else None,
                                  command_config, TrackTraceWriter(command_output) if command_output else None,
                                  TrackTraceWriter(execution_output) if execution_output else None)
        app.extensions["social_pipeline"] = pipeline
    elif tracking_output is not None:
        pipeline = TrackingPipeline(session_id or f"live-{uuid4().hex}", track_config,
                                    recording, TrackTraceWriter(tracking_output))
        app.extensions["tracking_pipeline"] = pipeline

    camera_cache = LiveCameraCache(max_frames=model_config.camera_cache_capacity,
                                  max_bytes=model_config.camera_cache_max_bytes) if vision_enabled else None
    models = LiveModelRunner(model_config, model_output, camera_cache=camera_cache) if models_enabled else None
    if models is not None:
        app.extensions["live_models"] = models
        atexit.register(models.close)
    if camera_cache is not None:
        app.extensions["live_camera"] = camera_cache
    # Maintain processing/submission order under concurrent Flask requests. No
    # model request runs here or while SocialPipeline's estimator lock is held.
    submission_lock = Lock()
    camera_ingest_lock = Lock()

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        return jsonify(accepted=False, error=error.name, message=error.description), error.code

    @app.get("/health")
    def health():
        extra = {"model_inference": models.status()} if models is not None else {}
        return jsonify(status="ok", service="navel-raw-sensor-receiver", **extra)

    def trial_identity(payload):
        if not isinstance(payload, dict) or any(
                not isinstance(payload.get(key), str) or not 0 < len(payload[key]) <= 128
                for key in ("trial_id", "session_id", "policy")):
            raise ValueError("Trial requires trial_id, session_id and policy")
        if not isinstance(pipeline, SocialPipeline) or payload["session_id"] != pipeline.tracking.tracker.session_id:
            raise ValueError("Trial server session is unavailable or mismatched")
        if models is None:
            raise ValueError("Model trial requires enabled --model-inference and --model-output")
        return payload

    @app.post("/api/v1/model-trials")
    def open_model_trial():
        try:
            payload = request.get_json()
            if not isinstance(payload, dict) or set(payload) != {"trial_id", "policy", "wait_s", "max_age_s"}:
                raise ValueError("Expected trial_id, policy, wait_s and max_age_s")
            if not isinstance(pipeline, SocialPipeline):
                raise ValueError("Model trial requires a social/model pipeline")
            identity = trial_identity({**payload, "session_id": pipeline.tracking.tracker.session_id})
            for key in ("wait_s", "max_age_s"):
                if type(payload[key]) not in (int, float) or not 0 < payload[key] <= 3600:
                    raise ValueError("Trial wait and model age must be positive and at most 3600 seconds")
            if payload["policy"] == "vlm" and sdk_recording is None:
                raise ValueError("VLM trial capture requires receiver --sdk-output")
            models.open_trial(identity["trial_id"], identity["session_id"], identity["policy"],
                              payload["wait_s"], payload["max_age_s"])
            return jsonify(accepted=True, **{key: identity[key] for key in ("trial_id", "session_id", "policy")})
        except ValueError as error:
            return jsonify(accepted=False, error="invalid_model_trial", message=str(error)), 409

    @app.post("/api/v1/model-trials/result")
    def model_trial_result():
        try:
            identity = trial_identity(request.get_json())
            result = models.trial_result(identity)
            return jsonify(accepted=True, result=result)
        except ValueError as error:
            return jsonify(accepted=False, error="invalid_model_trial", message=str(error)), 409

    @app.post("/api/v1/model-trials/close")
    def close_model_trial():
        try:
            identity = trial_identity(request.get_json())
            models.close_trial(identity)
            return jsonify(accepted=True)
        except ValueError as error:
            return jsonify(accepted=False, error="invalid_model_trial", message=str(error)), 409

    @app.post("/api/v1/observations")
    def observations():
        received_monotonic_us = time.monotonic_ns() // 1000
        received_unix_us = time.time_ns() // 1000
        payload = request.get_json()
        model_source = None
        trial = None
        if models is not None and isinstance(payload, dict) and "observation" in payload:
            if set(payload) not in ({"observation", "model_source"}, {"observation", "model_source", "trial"}):
                return jsonify(accepted=False, error="invalid_model_observation",
                               message="Expected observation, model_source and optional trial"), 400
            raw_payload = payload["observation"]
        else:
            raw_payload = payload
        if isinstance(payload, dict) and "trial" in payload:
            try:
                trial = trial_identity(payload["trial"])
                models.validate_trial(trial)
            except ValueError as error:
                return jsonify(accepted=False, error="invalid_model_trial", message=str(error)), 409
        try:
            frame = RawObservationFrame.model_validate(raw_payload)
        except ValidationError as error:
            return jsonify(
                accepted=False,
                error="invalid_raw_observation",
                details=error.errors(include_url=False, include_input=False, include_context=False),
            ), 400
        if raw_payload is not payload and payload["model_source"] is not None:
            try:
                model_source = validate_model_source(payload["model_source"], frame.timestamp)
            except ValueError as error:
                return jsonify(accepted=False, error="invalid_model_source", message=str(error)), 400

        try:
            with submission_lock if models is not None else nullcontext():
                snapshot = pipeline.process(frame) if pipeline is not None else None
                if pipeline is None:
                    recording.write(frame)
                model_status = None
                if models is not None and snapshot is not None:
                    try:
                        source_metadata = {
                            "observation_timestamp_us": frame.timestamp,
                            "model_source": model_source,
                            "receiver_received_monotonic_us": received_monotonic_us,
                            "receiver_received_unix_us": received_unix_us,
                            "receiver_receipt_clock": "receiver-host-monotonic-us",
                        }
                        model_status = (models.submit_trial(snapshot["social_state"], source_metadata, trial)
                                        if trial is not None else models.submit(snapshot["social_state"], source_metadata))
                    except Exception:
                        logger.exception("Observation processed, but model submission failed")
                        model_status = {"status": "not_run", "reason": "submission_failed"}
        except TimestampOrderError as error:
            return jsonify(accepted=False, error="timestamp_out_of_order", message=str(error)), 409
        except OSError:
            logger.exception("Could not store sensor observation")
            return jsonify(accepted=False, error="observation_storage_failed"), 503
        except TrackingProcessingError as error:
            logger.exception("Raw observation saved, but tracking failed")
            return jsonify(accepted=True, timestamp=frame.timestamp, people_count=len(frame.people),
                           processing_status="failed", processing_stage=error.stage,
                           policy_decision=None, final_decision=None), 200
        if snapshot is not None:
            social = snapshot.get("social_state")
            if social is not None:
                extra = {"social_state": social, "policy_decision": snapshot["policy_decision"],
                         "policy_readiness": snapshot["policy_readiness"],
                         "final_decision": snapshot["final_decision"],
                         "target_lock": snapshot["target_lock"],
                         "robot_command": snapshot["robot_command"]}
                if models is not None:
                    extra["model_trial" if trial is not None else "model_inference"] = model_status
            else:
                extra = {}
            return jsonify(accepted=True, timestamp=frame.timestamp, people_count=len(frame.people),
                           processing_status="complete", tracking={
                               "session_id": snapshot["session_id"],
                               "frame_sequence": snapshot["frame_sequence"],
                               "active_track_count": len(snapshot["tracks"]),
                               "events": snapshot["events"],
                           }, **extra)
        return jsonify(accepted=True, timestamp=frame.timestamp, people_count=len(frame.people))

    @app.post("/api/v1/sdk-packets")
    def sdk_packets():
        if sdk_recording is None:
            return jsonify(accepted=False, error="sdk_capture_unavailable"), 409
        try:
            record = validate_sdk_capture(request.get_json())
        except ValueError as error:
            return jsonify(accepted=False, error="invalid_sdk_capture", message=str(error)), 400
        try:
            duplicate = sdk_recording.write(record)
        except SdkCaptureOrderError as error:
            return jsonify(accepted=False, error="sdk_capture_out_of_order", message=str(error)), 409
        except OSError:
            logger.exception("Could not store SDK packet")
            return jsonify(accepted=False, error="sdk_capture_storage_failed"), 503
        return jsonify(accepted=True, stream=record["stream"],
                       sequence=record["sequence"], duplicate=duplicate)

    @app.post("/api/v1/camera-frames")
    def camera_frames():
        received_monotonic_us = time.monotonic_ns() // 1000
        received_unix_us = time.time_ns() // 1000
        if camera_recording is None and camera_cache is None:
            return jsonify(accepted=False, error="camera_capture_unavailable"), 409
        try:
            record, rgb = validate_camera_record(request.get_json())
            if camera_cache is not None and record["camera"] == "head" and rgb is not None and len(rgb) > camera_cache.max_bytes:
                raise ValueError("head image exceeds the configured camera cache byte limit")
        except ValueError as error:
            return jsonify(accepted=False, error="invalid_camera_capture", message=str(error)), 400
        try:
            with camera_ingest_lock:
                duplicate = camera_recording.write(record, rgb) if camera_recording is not None else False
                if camera_cache is not None:
                    duplicate = camera_cache.ingest(record, rgb,
                        receiver_received_monotonic_us=received_monotonic_us,
                        receiver_received_unix_us=received_unix_us) or duplicate
        except CameraCaptureOrderError as error:
            return jsonify(accepted=False, error="camera_capture_out_of_order", message=str(error)), 409
        except ValueError as error:
            return jsonify(accepted=False, error="invalid_camera_capture", message=str(error)), 400
        except OSError:
            logger.exception("Could not store camera frame")
            return jsonify(accepted=False, error="camera_capture_storage_failed"), 503
        return jsonify(accepted=True, camera=record["camera"],
                       sequence=record["sequence"], duplicate=duplicate)

    @app.post("/api/v1/execution-events")
    def execution_events():
        if not isinstance(pipeline, SocialPipeline):
            return jsonify(accepted=False, error="command_pipeline_unavailable"), 409
        try:
            event = ExecutionEvent.model_validate(request.get_json())
        except ValidationError as error:
            return jsonify(accepted=False, error="invalid_execution_event",
                           details=error.errors(include_url=False, include_input=False,
                                                include_context=False)), 400
        try:
            duplicate = pipeline.record_execution_event(event)
        except FeedbackRejected as error:
            return jsonify(accepted=False, error=str(error)), 409
        except OSError:
            logger.exception("Could not store execution event")
            return jsonify(accepted=False, error="execution_storage_failed"), 503
        return jsonify(accepted=True, duplicate=duplicate, event_id=event.event_id)

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Receive raw Navel observations over HTTP.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6060)
    parser.add_argument("--output", help="New JSONL path, or '-' for stdout; default is a timestamped file in var/recordings")
    parser.add_argument("--sdk-output", help="New JSONL path for unfiltered next_frame/next_locomotion packets")
    parser.add_argument("--camera-output-dir", help="New directory for head/chest PPM images and frames.jsonl")
    parser.add_argument("--tracking-output", help="Enable UID tracking and write a new derived JSONL trace")
    parser.add_argument("--tracking-config", help="Tracking configuration JSON (requires tracking, social, or lock output)")
    parser.add_argument("--social-output", help="Enable temporal SocialState and write a new JSONL trace")
    parser.add_argument("--lock-output", help="Enable social processing and write target lock JSONL")
    parser.add_argument("--command-output", help="Enable social processing and write command proposals JSONL")
    parser.add_argument("--execution-output", help="Enable social processing and write robot feedback JSONL")
    parser.add_argument("--command-config", help="Command timing and capacity JSON (requires social processing)")
    parser.add_argument("--lock-config", help="Target lock configuration JSON (requires social or lock output)")
    parser.add_argument("--temporal-config", help="Temporal configuration JSON (requires social or lock output)")
    parser.add_argument("--model-inference", choices=("disabled", "llm", "vlm", "both"), default="disabled",
                        help="Optional live model inference and audit; default disabled")
    parser.add_argument("--model-output", help="New exclusive JSONL path for model results")
    parser.add_argument("--model-sample-interval", type=float, default=1.0,
                        help="Minimum selected SocialState interval in robot seconds; zero selects every input")
    parser.add_argument("--model-queue-capacity", type=int, default=4,
                        help="Pending model jobs; overload drops the newest selected input")
    parser.add_argument("--model-max-camera-age", type=float, default=1.0,
                        help="Maximum robot-host receipt difference in seconds")
    parser.add_argument("--model-allow-receipt-match", action="store_true",
                        help="Allow prior common-clock head frame when no exact SDK timestamp match exists")
    parser.add_argument("--model-camera-cache-capacity", type=int, default=8,
                        help="Maximum cached head events, also capped at 64 MiB RGB bytes")
    for policy in ("llm", "vlm"):
        parser.add_argument(f"--{policy}-model", help=f"Explicit Ollama model for {policy.upper()}")
        parser.add_argument(f"--{policy}-base-url", default="http://127.0.0.1:11434")
        parser.add_argument(f"--{policy}-timeout", type=float, default=120.0)
        parser.add_argument(f"--{policy}-temperature", type=float, default=0.0)
        parser.add_argument(f"--{policy}-seed", type=int)
        parser.add_argument(f"--{policy}-num-predict", type=int)
        parser.add_argument(f"--{policy}-num-ctx", type=int)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    social_enabled = args.model_inference != "disabled" or any((args.social_output, args.lock_output, args.command_output, args.execution_output))
    if args.tracking_config and not (args.tracking_output or social_enabled):
        parser.error("--tracking-config requires a derived output")
    if (args.temporal_config or args.lock_config or args.command_config) and not social_enabled:
        parser.error("temporal, lock, and command config require a social, lock, command, or execution output")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = args.output
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        output = f"var/recordings/{stamp}.jsonl"
    try:
        policy_configs = {}
        for policy in ("llm", "vlm"):
            selected = args.model_inference in {policy, "both"}
            name = getattr(args, f"{policy}_model")
            if selected and name is None:
                raise ValueError(f"--{policy}-model is required for {args.model_inference} mode")
            if not selected and name is not None:
                raise ValueError(f"--{policy}-model requires {policy} or both mode")
            checked_config = OllamaConfig(
                base_url=getattr(args, f"{policy}_base_url"), model=name if name is not None else "not-selected",
                timeout_seconds=getattr(args, f"{policy}_timeout"),
                temperature=getattr(args, f"{policy}_temperature"),
                seed=getattr(args, f"{policy}_seed"),
                num_predict=getattr(args, f"{policy}_num_predict"),
                num_ctx=getattr(args, f"{policy}_num_ctx"))
            policy_configs[f"{policy}_config"] = checked_config if selected else None
        model_config = LiveModelConfig(mode=args.model_inference,
            sample_interval_s=args.model_sample_interval,
            queue_capacity=args.model_queue_capacity,
            max_camera_age_s=args.model_max_camera_age,
            allow_receipt_match=args.model_allow_receipt_match,
            camera_cache_capacity=args.model_camera_cache_capacity, **policy_configs)
        config = TrackConfig.from_file(args.tracking_config) if args.tracking_config else None
        app = create_app(None if output == "-" else output, tracking_output=args.tracking_output,
                         track_config=config, social_output=args.social_output,
                         temporal_config=TemporalConfig.from_file(args.temporal_config) if args.temporal_config else None,
                         lock_output=args.lock_output,
                         lock_config=LockConfig.from_file(args.lock_config) if args.lock_config else None,
                         command_output=args.command_output, execution_output=args.execution_output,
                         command_config=CommandConfig.from_file(args.command_config) if args.command_config else None,
                         sdk_output=args.sdk_output, camera_output_dir=args.camera_output_dir,
                         model_config=model_config, model_output=args.model_output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    logger.info("Recording raw sensor frames to %s", "stdout" if output == "-" else output)
    if args.sdk_output:
        logger.info("Recording SDK packets to %s", args.sdk_output)
    if args.camera_output_dir:
        logger.info("Recording camera images and manifest to %s", args.camera_output_dir)
    if args.tracking_output:
        logger.info("Recording UID track snapshots to %s", args.tracking_output)
    if args.social_output:
        logger.info("Recording temporal SocialState to %s", args.social_output)
    if args.lock_output:
        logger.info("Recording target locks to %s", args.lock_output)
    if args.command_output:
        logger.info("Recording command proposals to %s", args.command_output)
    if args.execution_output:
        logger.info("Recording execution events to %s", args.execution_output)
    if args.model_output:
        logger.info("Live %s model results to %s", args.model_inference, args.model_output)
    try:
        app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    finally:
        models = app.extensions.get("live_models")
        if models is not None:
            if not models.close():
                logger.error("Model worker shutdown deadline elapsed; active result may not be flushed")


if __name__ == "__main__":
    main()
