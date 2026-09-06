"""Generate the committed JSON Schema artifacts from the Pydantic contracts."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from app.domain.models import BehaviorIntent, ObservationFrame, SocialState


SCHEMA_VERSION = "v1"
DEFAULT_OUTPUT_DIRECTORY = Path(__file__).resolve().parents[2] / "schemas" / SCHEMA_VERSION
SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "observation-frame.schema.json": ObservationFrame,
    "social-state.schema.json": SocialState,
    "behavior-intent.schema.json": BehaviorIntent,
}


def schema_document(filename: str, model: type[BaseModel]) -> dict:
    """Return one deterministic JSON Schema document."""
    schema = model.model_json_schema(mode="validation")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://p4p.local/schemas/{SCHEMA_VERSION}/{filename}",
        **schema,
    }


def render_schema(filename: str, model: type[BaseModel]) -> str:
    """Serialize a schema using the repository's canonical formatting."""
    return json.dumps(schema_document(filename, model), indent=2, sort_keys=True) + "\n"


def write_schemas(output_directory: Path = DEFAULT_OUTPUT_DIRECTORY) -> list[Path]:
    """Write all public schemas and return their paths."""
    output_directory.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, model in SCHEMA_MODELS.items():
        path = output_directory / filename
        path.write_text(render_schema(filename, model), encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    for path in write_schemas():
        print(path)


if __name__ == "__main__":
    main()
