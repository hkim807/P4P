"""Receive validated raw sensor frames and append them to JSONL."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from app.domain.models import RawObservationFrame
from app.recording import RecordingWriter, TimestampOrderError


logger = logging.getLogger(__name__)


def create_app(output_path: str | Path | None = None) -> Flask:
    """Start a fresh JSONL recording; None prints accepted frames to stdout."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    recording = RecordingWriter(output_path)

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
            recording.write(frame)
        except TimestampOrderError as error:
            return jsonify(accepted=False, error="timestamp_out_of_order", message=str(error)), 409
        except OSError:
            logger.exception("Could not store sensor observation")
            return jsonify(accepted=False, error="observation_storage_failed"), 503
        return jsonify(accepted=True, timestamp=frame.timestamp, people_count=len(frame.people))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Receive raw Navel observations over HTTP.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6060)
    parser.add_argument("--output", help="New JSONL path, or '-' for stdout; default is a timestamped file in var/recordings")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = args.output
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        output = f"var/recordings/{stamp}.jsonl"
    try:
        app = create_app(None if output == "-" else output)
    except OSError as error:
        parser.error(str(error))
    logger.info("Recording raw sensor frames to %s", "stdout" if output == "-" else output)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
