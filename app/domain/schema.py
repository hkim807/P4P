"""Generate public raw sensor and shared final decision JSON Schemas."""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import RawObservationFrame
from app.domain.model_decision import model_decision_schema
from app.policy.rules import PolicyDecision


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas/v1/raw-observation-frame.schema.json"


def render_schema() -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **RawObservationFrame.model_json_schema(mode="validation"),
    }
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> None:
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(render_schema(), encoding="utf-8")
    print(SCHEMA_PATH)
    path = SCHEMA_PATH.with_name("model-decision.schema.json")
    path.write_text(json.dumps(model_decision_schema(), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(path)
    path = SCHEMA_PATH.with_name("policy-decision.schema.json")
    path.write_text(json.dumps(PolicyDecision.model_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
