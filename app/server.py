"""Receive validated raw sensor frames and append them to JSONL."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from app.domain.models import RawObservationFrame
from app.recording import RecordingWriter, TimestampOrderError
from app.pipeline import TrackTraceWriter, TrackingPipeline, TrackingProcessingError
from app.state.tracks import TrackConfig
from app.social_pipeline import SocialPipeline
from app.state.social_models import TemporalConfig


logger = logging.getLogger(__name__)


def create_app(output_path: str | Path | None = None, *,
               tracking_output: str | Path | None = None,
               track_config: TrackConfig | None = None,
               session_id: str | None = None,
               social_output: str | Path | None = None,
               temporal_config: TemporalConfig | None = None) -> Flask:
    """Start a fresh JSONL recording; None prints accepted frames to stdout."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    paths = [Path(p).resolve() for p in (output_path, tracking_output, social_output) if p is not None]
    if len(set(paths)) != len(paths):
        raise ValueError("Raw, tracking, and social outputs must use different paths")
    recording = RecordingWriter(output_path)
    pipeline = None
    if social_output is not None:
        pipeline = SocialPipeline(session_id or f"live-{uuid4().hex}", track_config,
                                  temporal_config, recording,
                                  TrackTraceWriter(tracking_output) if tracking_output else None,
                                  TrackTraceWriter(social_output))
        app.extensions["social_pipeline"] = pipeline
    elif tracking_output is not None:
        pipeline = TrackingPipeline(session_id or f"live-{uuid4().hex}", track_config,
                                    recording, TrackTraceWriter(tracking_output))
        app.extensions["tracking_pipeline"] = pipeline

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        return jsonify(accepted=False, error=error.name, message=error.description), error.code

    @app.get("/health")
    def health():
        return jsonify(status="ok", service="navel-raw-sensor-receiver")

    @app.post("/api/v1/observations")
    def observations():
        payload = request.get_json()
        try:
            frame = RawObservationFrame.model_validate(payload)
        except ValidationError as error:
            return jsonify(
                accepted=False,
                error="invalid_raw_observation",
                details=error.errors(include_url=False, include_input=False, include_context=False),
            ), 400

        try:
            snapshot = pipeline.process(frame) if pipeline is not None else None
            if pipeline is None:
                recording.write(frame)
        except TimestampOrderError as error:
            return jsonify(accepted=False, error="timestamp_out_of_order", message=str(error)), 409
        except OSError:
            logger.exception("Could not store sensor observation")
            return jsonify(accepted=False, error="observation_storage_failed"), 503
        except TrackingProcessingError as error:
            logger.exception("Raw observation saved, but tracking failed")
            return jsonify(accepted=True, timestamp=frame.timestamp, people_count=len(frame.people),
                           processing_status="failed", processing_stage=error.stage), 200
        if snapshot is not None:
            social = snapshot.get("social_state")
            extra = {"social_state": social} if social is not None else {}
            return jsonify(accepted=True, timestamp=frame.timestamp, people_count=len(frame.people),
                           processing_status="complete", tracking={
                               "session_id": snapshot["session_id"],
                               "frame_sequence": snapshot["frame_sequence"],
                               "active_track_count": len(snapshot["tracks"]),
                               "events": snapshot["events"],
                           }, **extra)
        return jsonify(accepted=True, timestamp=frame.timestamp, people_count=len(frame.people))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Receive raw Navel observations over HTTP.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6060)
    parser.add_argument("--output", help="New JSONL path, or '-' for stdout; default is a timestamped file in var/recordings")
    parser.add_argument("--tracking-output", help="Enable UID tracking and write a new derived JSONL trace")
    parser.add_argument("--tracking-config", help="Tracking configuration JSON (requires tracking or social output)")
    parser.add_argument("--social-output", help="Enable temporal SocialState and write a new JSONL trace")
    parser.add_argument("--temporal-config", help="Temporal configuration JSON (requires --social-output)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.tracking_config and not (args.tracking_output or args.social_output):
        parser.error("--tracking-config requires --tracking-output or --social-output")
    if args.temporal_config and not args.social_output:
        parser.error("--temporal-config requires --social-output")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = args.output
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        output = f"var/recordings/{stamp}.jsonl"
    try:
        config = TrackConfig.from_file(args.tracking_config) if args.tracking_config else None
        app = create_app(None if output == "-" else output, tracking_output=args.tracking_output,
                         track_config=config, social_output=args.social_output,
                         temporal_config=TemporalConfig.from_file(args.temporal_config) if args.temporal_config else None)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    logger.info("Recording raw sensor frames to %s", "stdout" if output == "-" else output)
    if args.tracking_output:
        logger.info("Recording UID track snapshots to %s", args.tracking_output)
    if args.social_output:
        logger.info("Recording temporal SocialState to %s", args.social_output)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
