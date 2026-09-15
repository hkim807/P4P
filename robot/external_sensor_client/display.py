"""Format actual server fields for inspection, without interpreting intents."""

from __future__ import annotations

import json
from typing import TextIO

from robot.navel_client.transport import ObservationResponse


def display_response(response: ObservationResponse, *, print_raw_json: bool = False, file: TextIO | None = None) -> None:
    payload = response.payload
    fields = ("accepted", "observation_id", "social_state_id", "decision_triggered", "forced_decision", "triggers")
    print(f"HTTP status={response.status_code} " + " ".join(
        f"{field}={json.dumps(payload.get(field))}" for field in fields
    ), file=file)
    error = payload.get("error")
    if error is not None:
        print("server_error=" + json.dumps(error, sort_keys=True), file=file)
    intent = payload.get("behavior_intent")
    if intent is not None:
        # Display the wire value directly: no intent parser or execution path.
        print("behavior_intent=" + json.dumps(intent, indent=2, sort_keys=True), file=file)
    elif payload.get("accepted") is True and payload.get("decision_triggered") is False:
        print("Observation accepted without a decision.", file=file)
    else:
        print("No BehaviorIntent returned.", file=file)
    if print_raw_json:
        print("response_json=" + json.dumps(payload, indent=2, sort_keys=True), file=file)
