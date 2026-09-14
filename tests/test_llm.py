"""Offline tests for the Ollama OpenAI-compatible request shape."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.llm import OllamaLLM


class FakeCompletions:
    def __init__(self):
        self.request = None

    def create(self, **request):
        self.request = request
        message = SimpleNamespace(content='{"action":"MONITOR"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def fake_llm():
    llm = OllamaLLM.__new__(OllamaLLM)
    llm.model = "test-model"
    llm.settings = SimpleNamespace(system_prompt="default system prompt")
    completions = FakeCompletions()
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm, completions


class OllamaLLMTests(unittest.TestCase):
    def test_structured_request_uses_openai_json_schema_format(self):
        llm, completions = fake_llm()
        schema = {
            "type": "object",
            "properties": {"action": {"type": "string"}},
            "required": ["action"],
        }

        output = llm.generate(
            "choose",
            system_prompt="policy",
            temperature=0.0,
            response_schema=schema,
        )

        self.assertEqual(output, '{"action":"MONITOR"}')
        self.assertEqual(completions.request["temperature"], 0.0)
        response_format = completions.request["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(response_format["json_schema"]["schema"], schema)

    def test_unstructured_chat_omits_response_format(self):
        llm, completions = fake_llm()
        llm.generate("hello")
        self.assertNotIn("response_format", completions.request)


if __name__ == "__main__":
    unittest.main()
