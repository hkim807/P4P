"""Offline tests for the Ollama OpenAI-compatible request shape."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.llm import LLMRequestError, OllamaLLM


class FakeCompletions:
    def __init__(self, *, error=None, content='  {"action":"MONITOR"}\n'):
        self.request = None
        self.error = error
        self.content = content

    def create(self, **request):
        self.request = request
        if self.error is not None:
            raise self.error
        message = SimpleNamespace(content=self.content)
        usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=4,
            total_tokens=14,
        )
        return SimpleNamespace(
            id="response-1",
            model="returned-model",
            created=123,
            usage=usage,
            choices=[
                SimpleNamespace(message=message, finish_reason="stop")
            ],
        )


def fake_llm(*, error=None, content='  {"action":"MONITOR"}\n'):
    llm = OllamaLLM.__new__(OllamaLLM)
    llm.model = "test-model"
    llm.endpoint = "http://test.invalid/v1"
    llm.settings = SimpleNamespace(system_prompt="default system prompt")
    completions = FakeCompletions(error=error, content=content)
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

    def test_capture_retains_exact_messages_raw_response_and_metadata(self):
        llm, completions = fake_llm()
        schema = {"type": "object"}

        result = llm.generate_with_capture(
            "choose exactly",
            system_prompt="debug system",
            temperature=0.0,
            response_schema=schema,
            response_schema_name="social_navigation_debug_decision",
        )

        self.assertEqual(
            result.request.messages,
            (
                {"role": "system", "content": "debug system"},
                {"role": "user", "content": "choose exactly"},
            ),
        )
        self.assertEqual(completions.request["messages"], list(result.request.messages))
        self.assertEqual(result.raw_content, '  {"action":"MONITOR"}\n')
        self.assertEqual(result.request.response_schema, schema)
        self.assertEqual(
            result.request.response_schema_name,
            "social_navigation_debug_decision",
        )
        self.assertEqual(
            result.request.response_format,
            completions.request["response_format"],
        )
        self.assertEqual(result.response_id, "response-1")
        self.assertEqual(result.returned_model, "returned-model")
        self.assertEqual(result.created, 123)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.usage["total_tokens"], 14)
        self.assertGreaterEqual(result.latency_ms, 0.0)

    def test_request_failure_retains_request_and_timing(self):
        llm, _ = fake_llm(error=ConnectionError("offline"))

        with self.assertRaises(LLMRequestError) as raised:
            llm.generate_with_capture(
                "choose",
                system_prompt="debug system",
                temperature=0.0,
            )

        error = raised.exception
        self.assertEqual(str(error), "offline")
        self.assertEqual(error.request.messages[1]["content"], "choose")
        self.assertGreaterEqual(error.latency_ms, 0.0)

    def test_empty_response_retains_request_and_timing(self):
        llm, _ = fake_llm(content="")

        with self.assertRaises(LLMRequestError) as raised:
            llm.generate_with_capture("choose", system_prompt="debug system")

        error = raised.exception
        self.assertEqual(str(error), "Ollama returned an empty response")
        self.assertEqual(error.request.messages[0]["content"], "debug system")
        self.assertGreaterEqual(error.latency_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
