"""One encoded image -> static English instructions -> one Ollama model result."""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any

from app.domain.actions import ACTION_DEFINITIONS
from app.inference.ollama import OllamaClient, OllamaMessage, OllamaResult


PROMPT_VERSION = "image-only-vlm-v2"
SYSTEM_PROMPT = """A robot is assigned to travel along a fixed route inside a laboratory. It must choose its next behaviour around people.

Choose the most appropriate next action using only the attached image:
""" + "\n".join(f"- {a}: {d}" for a, d in ACTION_DEFINITIONS.items()) + """

One image cannot establish movement over time or sustained gaze. Do not invent measured distances, durations or velocities. Give a brief explanation grounded in visible evidence.

Select exactly one of the four actions. Return exactly one JSON object with only the required fields action and reason. action must be exactly CONTINUE, YIELD, APPROACH or ENGAGE; reason must be a string containing non-whitespace text. Do not return prose, code fences or additional fields."""
USER_PROMPT = "Choose one action based only on the attached image."
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class VLMPrompt:
    image_base64: str
    prompt_version: str
    instructions: str

    @property
    def messages(self) -> tuple[OllamaMessage, OllamaMessage]:
        # OllamaMessage.images is a list. Recreate it on access so a caller
        # mutating the returned list cannot mutate this frozen prompt.
        return (OllamaMessage(role="system", content=self.instructions),
                OllamaMessage(role="user", content=USER_PROMPT, images=[self.image_base64]))


def build_vlm_prompt(encoded_image: str) -> VLMPrompt:
    """Accept only raw PNG base64; source correlation cannot enter this interface.

    The runner verifies and converts the selected image. This boundary checks
    its encoding and PNG signature without loading files or duplicating a codec.
    """
    if not isinstance(encoded_image, str) or not encoded_image:
        raise ValueError("encoded_image must be a nonempty raw PNG base64 string")
    try:
        decoded = base64.b64decode(encoded_image, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("encoded_image must be valid raw base64 without a data URL") from error
    if not decoded.startswith(_PNG_SIGNATURE):
        raise ValueError("encoded_image must contain PNG bytes")
    return VLMPrompt(encoded_image, PROMPT_VERSION, SYSTEM_PROMPT)


@dataclass(frozen=True)
class VLMPolicyResult:
    prompt: VLMPrompt
    ollama_result: OllamaResult

    @property
    def ok(self) -> bool:
        return self.ollama_result.ok

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready model diagnostics; image and source metadata stay separate."""
        result = self.ollama_result
        error = result.error
        return {
            "prompt_version": self.prompt.prompt_version,
            "ok": result.ok,
            "decision": result.decision.model_dump(mode="json") if result.decision else None,
            "error": ({"category": error.category.value, "message": error.message,
                       "http_status": error.http_status} if error else None),
            "requested_model": result.requested_model,
            "returned_model": result.returned_model,
            "raw_content": result.raw_content,
            "request_duration_s": result.request_duration_s,
        }


def decide_vlm(encoded_image: str, client: OllamaClient) -> VLMPolicyResult:
    """Make exactly one client call and preserve its result without fallback."""
    prompt = build_vlm_prompt(encoded_image)
    result = client.chat(prompt.messages)
    return VLMPolicyResult(prompt, result)
