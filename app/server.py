"""Receive validated raw sensor frames and append them to JSONL."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from app.domain.models import RawObservationFrame


logger = logging.getLogger(__name__)


def create_app(output_path: str | Path | None = None) -> Flask:
    """Use output_path for JSONL storage; None prints accepted frames to stdout."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    path = Path(output_path) if output_path is not None else None
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    write_lock = Lock()

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

        # exclude_unset preserves optional head-position omission and measured nulls.
        line = json.dumps(frame.model_dump(exclude_unset=True), allow_nan=False, separators=(",", ":"))
        try:
            with write_lock:
                if path is None:
                    print(line, flush=True)
                else:
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(line + "\n")
        except OSError:
            logger.exception("Could not store sensor observation")
            return jsonify(accepted=False, error="observation_storage_failed"), 503
        return jsonify(accepted=True, timestamp=frame.timestamp, people_count=len(frame.people))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Receive raw Navel observations over HTTP.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6060)
    parser.add_argument("--output", default="var/observations.jsonl",
                        help="JSONL append path, or '-' to print frames to stdout")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    app = create_app(None if args.output == "-" else args.output)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
