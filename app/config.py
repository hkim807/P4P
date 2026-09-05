"""Environment-based configuration for the LLM gateway."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    ollama_host: str = os.getenv("OLLAMA_HOST", "127.0.0.1")
    ollama_port: int = int(os.getenv("OLLAMA_PORT", "11434"))
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "6000"))
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    max_input_characters: int = int(os.getenv("MAX_INPUT_CHARACTERS", "20000"))
    system_prompt: str = os.getenv(
        "SYSTEM_PROMPT",
        "You are an assistant for a robot social-navigation research project.",
    )

    @property
    def ollama_base_url(self) -> str:
        return f"http://{self.ollama_host}:{self.ollama_port}/v1"
