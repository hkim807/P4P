"""Offline tests for the HTTP-to-LLM request pipeline."""

from __future__ import annotations

import unittest

from app.config import Settings
from app.server import create_app


class FakeLLM:
    provider = "ollama"
    model = "test-model"
    endpoint = "http://127.0.0.1:11434/v1"

    def health(self):
        return {
            "reachable": True,
            "model_available": True,
            "available_models": [self.model],
        }

    def generate(
        self,
        message,
        *,
        system_prompt=None,
        temperature=0.2,
        response_schema=None,
    ):
        return f"received: {message}"


class FailingLLM(FakeLLM):
    def generate(
        self,
        message,
        *,
        system_prompt=None,
        temperature=0.2,
        response_schema=None,
    ):
        raise RuntimeError("connection refused")


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app(
            llm=FakeLLM(), settings=Settings(max_input_characters=100)
        ).test_client()

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["model_available"])

    def test_debug_page_supports_direct_navigation(self):
        rules = {str(rule) for rule in self.client.application.url_map.iter_rules()}
        self.assertIn("/debug", rules)
        self.assertIn("/debug/", rules)
        root = self.client.get("/")
        root_status = root.status_code
        root_data = root.get_data()
        root.close()
        for path in ("/debug", "/debug/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, root_status)
            self.assertEqual(response.get_data(), root_data)
            response.close()

    def test_chat_passes_input_and_returns_output(self):
        response = self.client.post("/chat", json={"message": "Hello model"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["output"], "received: Hello model")

    def test_chat_rejects_an_empty_message(self):
        response = self.client.post("/chat", json={"message": "  "})
        self.assertEqual(response.status_code, 400)

    def test_chat_reports_provider_failure(self):
        client = create_app(llm=FailingLLM()).test_client()
        response = client.post("/chat", json={"message": "Hello"})
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
