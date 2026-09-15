"""Environment-based configuration for the LLM gateway."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv


load_dotenv()


class DecisionMode(str, Enum):
    """Select the normal policy contract or the richer debug contract."""

    NORMAL = "NORMAL"
    DEBUG = "DEBUG"


def _decision_mode_from_env() -> DecisionMode:
    value = os.getenv("DECISION_MODE", DecisionMode.NORMAL.value).strip().upper()
    try:
        return DecisionMode(value)
    except ValueError as error:
        allowed = ", ".join(mode.value for mode in DecisionMode)
        raise ValueError(f"DECISION_MODE must be one of: {allowed}") from error


@dataclass(frozen=True)
class Settings:
    ollama_host: str = os.getenv("OLLAMA_HOST", "127.0.0.1")
    ollama_port: int = int(os.getenv("OLLAMA_PORT", "11434"))
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "6060"))
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    max_input_characters: int = int(os.getenv("MAX_INPUT_CHARACTERS", "20000"))
    decision_mode: DecisionMode = field(default_factory=_decision_mode_from_env)
    system_prompt: str = os.getenv(
        "SYSTEM_PROMPT",
        "You are an assistant for a robot social-navigation research project.",
    )

    def __post_init__(self) -> None:
        if isinstance(self.decision_mode, DecisionMode):
            return
        try:
            normalized = DecisionMode(str(self.decision_mode).strip().upper())
        except ValueError as error:
            allowed = ", ".join(mode.value for mode in DecisionMode)
            raise ValueError(f"decision_mode must be one of: {allowed}") from error
        object.__setattr__(self, "decision_mode", normalized)

    @property
    def ollama_base_url(self) -> str:
        return f"http://{self.ollama_host}:{self.ollama_port}/v1"
