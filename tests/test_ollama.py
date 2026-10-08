"""Ollama request and failure contracts, using only in-memory transports."""
import io
import json
from http.client import HTTPException
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from pydantic import ValidationError

from app.domain.model_decision import model_decision_schema
from app.ollama import (
    HttpResponse, OllamaClient, OllamaConfig, OllamaErrorCategory, OllamaMessage,
    UrllibTransport, extract_chat_content,
)


def chat_payload(content='{"action":"YIELD","reason":"Pause briefly."}', **overrides):
    payload = {
        "model": "returned-model:latest",
        "done": True,
        "message": {"role": "assistant", "content": content},
    }
    payload.update(overrides)
    return payload


def json_response(payload, status=200):
    return HttpResponse(status, json.dumps(payload).encode("utf-8"))


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, body, *, timeout_seconds):
        self.calls.append((url, body, timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.response


class OllamaClientTests(unittest.TestCase):
    def make_client(self, response=None, error=None, **settings):
        config = OllamaConfig(base_url="http://localhost:11434/", model="requested-model",
                              **settings)
        transport = FakeTransport(response=response, error=error)
        ticks = iter([10.0, 10.25])
        return OllamaClient(config, transport=transport, monotonic=lambda: next(ticks)), transport

    def assert_failure(self, result, category, *, raw_content=None, returned_model=None,
                       status=None):
        self.assertFalse(result.ok)
        self.assertIsNone(result.decision)
        self.assertEqual(result.requested_model, "requested-model")
        self.assertEqual(result.returned_model, returned_model)
        self.assertEqual(result.raw_content, raw_content)
        self.assertEqual(result.request_duration_s, 0.25)
        self.assertEqual(result.error.category, category)
        self.assertEqual(result.error.http_status, status)
        self.assertTrue(result.error.message)

    def test_request_passes_exact_caller_messages_and_encoded_images(self):
        messages = [
            {"role": "system", "content": "Choose a permitted action and explain it."},
            OllamaMessage(role="user", content="Generic input.", images=["YWJj", "AAEC"]),
            {"role": "assistant", "content": "Earlier caller-provided text."},
        ]
        client, transport = self.make_client(json_response(chat_payload()), timeout_seconds=2.5,
                                             temperature=0.2, seed=7, num_predict=64)
        result = client.chat(messages)
        self.assertTrue(result.ok)
        self.assertEqual(len(transport.calls), 1)
        url, body, timeout = transport.calls[0]
        self.assertEqual(url, "http://localhost:11434/api/chat")
        self.assertEqual(timeout, 2.5)
        self.assertEqual(json.loads(body), {
            "model": "requested-model",
            "messages": [
                messages[0],
                {"role": "user", "content": "Generic input.", "images": ["YWJj", "AAEC"]},
                messages[2],
            ],
            "stream": False,
            "format": model_decision_schema(),
            "options": {"temperature": 0.2, "seed": 7, "num_predict": 64},
        })

    def test_default_options_and_optional_images_are_omitted(self):
        client, transport = self.make_client(json_response(chat_payload()))
        client.chat([{"role": "user", "content": "입력"}])
        payload = json.loads(transport.calls[0][1])
        self.assertEqual(payload["options"], {"temperature": 0.0})
        self.assertEqual(payload["messages"], [{"role": "user", "content": "입력"}])

    def test_all_four_decisions_preserve_content_identity_and_duration(self):
        for action in ("YIELD", "CONTINUE", "APPROACH", "ENGAGE"):
            with self.subTest(action=action):
                content = json.dumps({"action": action, "reason": "Brief explanation."}, indent=2)
                client, transport = self.make_client(json_response(chat_payload(content)))
                result = client.chat([{"role": "user", "content": "Generic input."}])
                self.assertTrue(result.ok)
                self.assertIsNone(result.error)
                self.assertEqual(result.decision.action, action)
                self.assertEqual(result.decision.reason, "Brief explanation.")
                self.assertEqual(result.requested_model, "requested-model")
                self.assertEqual(result.returned_model, "returned-model:latest")
                self.assertEqual(result.raw_content, content)
                self.assertEqual(result.request_duration_s, 0.25)
                self.assertEqual(len(transport.calls), 1)

    def test_connection_and_timeout_failures_do_not_retry_or_fabricate_decisions(self):
        errors = [
            (ConnectionRefusedError("refused"), OllamaErrorCategory.CONNECTION),
            (URLError(ConnectionRefusedError("refused")), OllamaErrorCategory.CONNECTION),
            (TimeoutError("deadline"), OllamaErrorCategory.TIMEOUT),
            (URLError(TimeoutError("deadline")), OllamaErrorCategory.TIMEOUT),
        ]
        for error, category in errors:
            with self.subTest(error=repr(error)):
                client, transport = self.make_client(error=error)
                result = client.chat([{"role": "user", "content": "Generic input."}])
                self.assert_failure(result, category)
                self.assertEqual(len(transport.calls), 1)

    def test_http_protocol_failure_is_response_format_failure(self):
        client, transport = self.make_client(error=HTTPException("invalid status line"))
        result = client.chat([{"role": "user", "content": "Generic input."}])
        self.assert_failure(result, OllamaErrorCategory.RESPONSE_FORMAT)
        self.assertIn("invalid status line", result.error.message)
        self.assertEqual(len(transport.calls), 1)

    def test_non_success_http_json_and_plain_text_errors(self):
        responses = [
            (json_response({"error": "model unavailable"}, 404), "model unavailable"),
            (HttpResponse(503, b"temporarily unavailable"), "temporarily unavailable"),
            (HttpResponse(302, b"moved"), "moved"),
            (HttpResponse(500, b"\xffbroken"), "broken"),
        ]
        for response, detail in responses:
            with self.subTest(status=response.status_code, detail=detail):
                client, transport = self.make_client(response)
                result = client.chat([{"role": "user", "content": "Generic input."}])
                self.assert_failure(result, OllamaErrorCategory.HTTP, status=response.status_code)
                self.assertIn(str(response.status_code), result.error.message)
                self.assertIn(detail, result.error.message)
                self.assertEqual(len(transport.calls), 1)

    def test_http_failure_preserves_available_model_and_raw_content(self):
        content = '{"action":"CONTINUE","reason":"Available raw content."}'
        response = json_response(chat_payload(content, error="inference failed"), 500)
        client, transport = self.make_client(response)
        result = client.chat([{"role": "user", "content": "Generic input."}])
        self.assert_failure(result, OllamaErrorCategory.HTTP, raw_content=content,
                            returned_model="returned-model:latest", status=500)
        self.assertIn("inference failed", result.error.message)
        self.assertEqual(len(transport.calls), 1)

    def test_invalid_json_utf8_and_nonobject_ollama_responses(self):
        bodies = [
            b"{", b"\xff", b"null", b"[]", b'"text"', b"true", b"17",
            b'{"done":true,"done":false}', b'{"total_duration":NaN}',
            b'{"total_duration":Infinity}', b'{"total_duration":-Infinity}',
        ]
        for body in bodies:
            with self.subTest(body=body):
                client, transport = self.make_client(HttpResponse(200, body))
                result = client.chat([{"role": "user", "content": "Generic input."}])
                self.assert_failure(result, OllamaErrorCategory.RESPONSE_FORMAT, status=200)
                self.assertEqual(len(transport.calls), 1)

    def test_missing_or_invalid_ollama_envelope_fields(self):
        content = '{"action":"YIELD","reason":"Keep the exact raw text."}'
        invalid = []
        for field in ("model", "done", "message"):
            payload = chat_payload(content)
            del payload[field]
            invalid.append(payload)
        invalid.extend(chat_payload(content, model=value) for value in (None, False, 5, "", " "))
        invalid.extend(chat_payload(content, done=value) for value in (False, None, 1, "true"))
        invalid.extend(chat_payload(content, message=value) for value in (None, "text", []))
        invalid.extend(chat_payload(content, message={"role": role, "content": content})
                       for role in (None, "user", "system", "Assistant"))
        invalid.append(chat_payload(content, message={"content": content}))
        invalid.append(chat_payload(content, message={"role": "assistant"}))
        invalid.extend(chat_payload(content, message={"role": "assistant", "content": value})
                       for value in (None, 5, False, [], {}))
        invalid.append(chat_payload(content, error="failure despite HTTP 200"))
        for payload in invalid:
            with self.subTest(payload=payload):
                model = payload.get("model") if isinstance(payload.get("model"), str) else None
                message = payload.get("message")
                raw = message.get("content") if isinstance(message, dict) else None
                raw = raw if isinstance(raw, str) else None
                client, transport = self.make_client(json_response(payload))
                result = client.chat([{"role": "user", "content": "Generic input."}])
                self.assert_failure(result, OllamaErrorCategory.RESPONSE_FORMAT,
                                    raw_content=raw, returned_model=model, status=200)
                self.assertEqual(len(transport.calls), 1)

    def test_invalid_decision_preserves_raw_content_without_retry_or_fallback(self):
        contents = [
            "", "not JSON", '```json\n{"action":"YIELD","reason":"Pause."}\n```',
            '{"action":"STOP","reason":"Unavailable action."}',
            '{"action":"DEFER","reason":"Unavailable action."}',
            '{"action":"YIELD","reason":" "}',
            '{"action":"YIELD","action":"CONTINUE","reason":"Duplicate."}',
            '{"action":"YIELD","reason":true}',
        ]
        for content in contents:
            with self.subTest(content=content):
                client, transport = self.make_client(json_response(chat_payload(content)))
                result = client.chat([{"role": "user", "content": "Generic input."}])
                self.assert_failure(result, OllamaErrorCategory.INVALID_DECISION,
                                    raw_content=content, returned_model="returned-model:latest",
                                    status=200)
                self.assertEqual(len(transport.calls), 1)

    def test_invalid_caller_messages_fail_before_transport(self):
        messages = [
            [], [None], ["text"], [{"content": "missing role"}],
            [{"role": "tool", "content": "unsupported role"}],
        ]
        messages.extend([[{"role": "user", "content": value}]]
                        for value in (None, 5, False, [], {}))
        messages.extend([[{"role": "user", "content": "text", "images": value}]]
                        for value in ("YWJj", [""], [5], [False], [None]))
        messages.append([{"role": "user", "content": "text", "unexpected": True}])
        for value in messages:
            with self.subTest(messages=value):
                client, transport = self.make_client(json_response(chat_payload()))
                with self.assertRaises(ValueError):
                    client.chat(value)
                self.assertEqual(transport.calls, [])


class OllamaConfigurationTests(unittest.TestCase):
    def test_origins_and_minimal_settings(self):
        for base_url in ("http://localhost:11434", "https://example.com/", "http://[::1]:11434"):
            with self.subTest(base_url=base_url):
                config = OllamaConfig(base_url=base_url, model="test:latest")
                self.assertEqual(config.base_url, base_url)
                self.assertEqual(config.timeout_seconds, 30.0)
                self.assertEqual(config.generation_options(), {"temperature": 0.0})

    def test_invalid_base_urls_and_model_names(self):
        values = {
            "base_url": ["", "localhost:11434", "ftp://example.com", "http://", "http://host/api",
                         "http://user:pass@host", "http://host?x=1", "http://host#fragment",
                         "http://host:notaport", "http://host:65536", "http://host/ ", False],
            "model": ["", " ", " model", "model ", None, 17, False],
        }
        for setting, invalid in values.items():
            for value in invalid:
                with self.subTest(setting=setting, value=value):
                    options = {"base_url": "http://localhost:11434", "model": "test"}
                    options[setting] = value
                    with self.assertRaises(ValidationError):
                        OllamaConfig(**options)

    def test_timeout_is_finite_positive_and_strict(self):
        for value in (0, -1, float("nan"), float("inf"), -float("inf"), True, "1", None):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    OllamaConfig(base_url="http://localhost:11434", model="test", timeout_seconds=value)

    def test_generation_settings_reject_incorrect_types_and_ranges(self):
        invalid = {
            "temperature": [-1, float("nan"), float("inf"), True, "0.1", None],
            "seed": [True, 1.5, "7"],
            "num_predict": [0, -1, True, 1.5, "64"],
            "num_ctx": [0, 511, True, 512.5, "8192"],
            "unexpected": [True],
        }
        for setting, values in invalid.items():
            for value in values:
                with self.subTest(setting=setting, value=value):
                    with self.assertRaises(ValidationError):
                        OllamaConfig(base_url="http://localhost:11434", model="test", **{setting: value})


class OllamaContentExtractionTests(unittest.TestCase):
    def test_extraction_leaves_decision_parsing_to_contract(self):
        self.assertEqual(extract_chat_content(chat_payload("not a decision")),
                         ("returned-model:latest", "not a decision"))

    def test_server_error_is_reported_without_requiring_model_identity(self):
        with self.assertRaisesRegex(ValueError, "model unavailable"):
            extract_chat_content({"error": "model unavailable"})


class UrllibTransportTests(unittest.TestCase):
    def test_request_headers_body_timeout_and_http_success(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"done":true}'
        opener = MagicMock()
        opener.open.return_value = response
        with patch("app.ollama.build_opener", return_value=opener) as build:
            result = UrllibTransport().post("http://localhost:11434/api/chat", b'{"model":"test"}',
                                            timeout_seconds=1.75)
        build.assert_called_once()
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:11434/api/chat")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b'{"model":"test"}')
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 1.75})
        self.assertEqual(result, HttpResponse(200, b'{"done":true}'))

    def test_http_error_status_and_body_are_preserved(self):
        body = b'{"error":"model not found"}'
        error = HTTPError("http://localhost:11434/api/chat", 404, "missing", {}, io.BytesIO(body))
        opener = MagicMock()
        opener.open.side_effect = error
        with patch("app.ollama.build_opener", return_value=opener):
            result = UrllibTransport().post("http://localhost:11434/api/chat", b"{}", timeout_seconds=2.0)
        self.assertEqual(result, HttpResponse(404, body))
        opener.open.assert_called_once()

    def test_connection_failure_is_propagated_without_retry(self):
        error = URLError(ConnectionRefusedError("refused"))
        opener = MagicMock()
        opener.open.side_effect = error
        with patch("app.ollama.build_opener", return_value=opener):
            with self.assertRaises(URLError) as raised:
                UrllibTransport().post("http://localhost:11434/api/chat", b"{}", timeout_seconds=1.0)
        self.assertIs(raised.exception, error)
        opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
