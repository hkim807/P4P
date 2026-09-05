"""Small client for a locally hosted Ollama model."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from app.config import Settings


class OllamaLLM:
    """Send chat-completion requests through Ollama's OpenAI-compatible API."""

    provider = "ollama"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.ollama_model
        self.endpoint = settings.ollama_base_url
        self._client = OpenAI(
            base_url=self.endpoint,
            api_key="ollama",
            timeout=settings.request_timeout_seconds,
        )

    def health(self) -> dict[str, Any]:
        """Check whether Ollama is reachable and report its installed models."""
        models = self._client.models.list()
        names = [model.id for model in models.data]
        return {
            "reachable": True,
            "model_available": self.model in names,
            "available_models": names,
        }

    def generate(
        self,
        message: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        """Return the model's text response for one input message."""
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt or self.settings.system_prompt,
                },
                {"role": "user", "content": message},
            ],
            temperature=temperature,
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("Ollama returned an empty response")
        return content.strip()
