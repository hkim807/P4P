"""Generate the public JSON Schema from the raw sensor contract."""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import RawObservationFrame


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


if __name__ == "__main__":
    main()
