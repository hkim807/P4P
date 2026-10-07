"""Image-only policy contracts; all inference responses are fake."""
import base64
from dataclasses import FrozenInstanceError
import inspect
import io
import json
import unittest
from unittest.mock import patch

from PIL import Image

from app.domain.model_decision import ModelDecision, model_decision_schema
from app.ollama import (
    HttpResponse, OllamaClient, OllamaConfig, OllamaError, OllamaErrorCategory, OllamaResult,
)
from app.policy import llm
from app.policy.vlm import (
    PROMPT_VERSION, SYSTEM_PROMPT, USER_PROMPT, build_vlm_prompt, decide_vlm,
)


def png_bytes(color=(120, 30, 200)):
    image = Image.new("RGB", (2, 3), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


PNG_BYTES = png_bytes()
ENCODED_IMAGE = base64.b64encode(PNG_BYTES).decode("ascii")


def success(action="ENGAGE"):
    decision = ModelDecision(action=action, reason="Fake explanation grounded in visible evidence.")
    return OllamaResult("caller-model", "returned-model:revision", decision.model_dump_json(),
                        0.375, decision, None)


class FakeClient:
    def __init__(self, result, mutate_messages=False):
        self.result = result
        self.mutate_messages = mutate_messages
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if self.mutate_messages:
            messages[1].images.clear()
        return self.result


class FakeTransport:
    def __init__(self, *, content=None, error=None, response=None):
        self.calls = []
        self.error = error
        self.response = response or HttpResponse(200, json.dumps({
            "model": "returned-vision-model", "done": True,
            "message": {"role": "assistant", "content": content},
        }).encode("utf-8"))

    def post(self, url, body, *, timeout_seconds):
        self.calls.append((url, body, timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.response


def ollama_client(transport):
    config = OllamaConfig(base_url="http://localhost:11434", model="caller-vision-model",
                          timeout_seconds=4.0, temperature=0.2, seed=42, num_predict=128)
    ticks = iter([50.0, 50.375])
    return OllamaClient(config, transport=transport, monotonic=lambda: next(ticks))


class VLMPromptTests(unittest.TestCase):
    def test_builder_accepts_only_encoded_image_input(self):
        self.assertEqual(list(inspect.signature(build_vlm_prompt).parameters), ["encoded_image"])
        with self.assertRaises(TypeError):
            build_vlm_prompt(ENCODED_IMAGE, source={"scenario": "secret"})

    def test_prompt_is_static_english_versioned_and_reproducible(self):
        first = build_vlm_prompt(ENCODED_IMAGE)
        second = build_vlm_prompt(base64.b64encode(png_bytes((5, 7, 9))).decode("ascii"))
        self.assertEqual(first.prompt_version, "image-only-vlm-v3")
        self.assertEqual(first.prompt_version, PROMPT_VERSION)
        self.assertEqual(first.instructions, SYSTEM_PROMPT)
        self.assertEqual(second.instructions, first.instructions)
        self.assertEqual(second.messages[1].content, first.messages[1].content)
        SYSTEM_PROMPT.encode("ascii")
        USER_PROMPT.encode("ascii")
        self.assertEqual(build_vlm_prompt(ENCODED_IMAGE), first)

    def test_task_context_and_definitions_match_llm_verbatim(self):
        text = build_vlm_prompt(ENCODED_IMAGE).instructions
        self.assertTrue(text.startswith(
            "A robot is assigned to travel along a fixed route inside a laboratory. "
            "It must choose its next behaviour around people."))
        definitions = [line for line in llm.SYSTEM_PROMPT.splitlines()
                       if line.startswith(("- CONTINUE:", "- YIELD:", "- APPROACH:", "- ENGAGE:"))]
        self.assertEqual(len(definitions), 4)
        for line in definitions:
            with self.subTest(definition=line):
                self.assertIn(line, text.splitlines())
        self.assertNotIn("The supplied robot state describes its actual movement", text)

    def test_visible_evidence_limits_and_strict_output_are_explicit(self):
        text = build_vlm_prompt(ENCODED_IMAGE).instructions
        for instruction in (
            "using only the attached image", "One image cannot establish movement over time or sustained gaze",
            "Do not invent measured distances, durations or velocities",
            "brief explanation grounded in visible evidence", "Select exactly one of the four actions",
            "only the required fields action and reason",
            "action must be exactly CONTINUE, APPROACH, ENGAGE or YIELD",
            "reason must be a string containing non-whitespace text",
            "Do not return prose, code fences or additional fields",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, text)

    def test_messages_contain_only_static_instructions_and_one_raw_png_base64(self):
        messages = build_vlm_prompt(ENCODED_IMAGE).messages
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].model_dump(exclude_none=True),
                         {"role": "system", "content": SYSTEM_PROMPT})
        self.assertEqual(messages[1].model_dump(exclude_none=True),
                         {"role": "user", "content": USER_PROMPT, "images": [ENCODED_IMAGE]})
        self.assertEqual(base64.b64decode(messages[1].images[0], validate=True), PNG_BYTES)
        for message in messages:
            for forbidden in ("SocialState", "state_id", "person_id", "uid", "gaze_overlap",
                              "latest_distance_m", "human_radial_motion", "motion_state", "config_version",
                              "source", "session_id", "timestamp", "scenario-", "filename", ".ppm",
                              "image_matching", "rule_decision", "llm_decision", "expected_action", "few-shot"):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, message.content)
        self.assertNotIn('"action":', SYSTEM_PROMPT)
        self.assertNotIn('"reason":', SYSTEM_PROMPT)

    def test_prompt_is_immutable_and_message_image_lists_are_detached(self):
        prompt = build_vlm_prompt(ENCODED_IMAGE)
        first = prompt.messages
        first[1].images.append("mutated")
        first[1].images[0] = "replaced"
        self.assertEqual(prompt.image_base64, ENCODED_IMAGE)
        self.assertEqual(prompt.messages[1].images, [ENCODED_IMAGE])
        self.assertIsNot(first[1].images, prompt.messages[1].images)
        with self.assertRaises(FrozenInstanceError):
            prompt.image_base64 = "replaced"

    def test_invalid_encoding_types_data_urls_whitespace_and_non_png_are_rejected(self):
        invalid = (None, {}, [], 17, True, b"encoded", "", " ", "%%%", "YWJj",
                   "data:image/png;base64," + ENCODED_IMAGE, ENCODED_IMAGE + "\n",
                   " " + ENCODED_IMAGE, "한글", ENCODED_IMAGE[:-2],
                   base64.b64encode(b"P6\n2 3\n255\n" + b"\0" * 18).decode("ascii"))
        for encoded in invalid:
            with self.subTest(encoded=encoded):
                with self.assertRaises(ValueError):
                    build_vlm_prompt(encoded)


class VLMPolicyTests(unittest.TestCase):
    def test_all_four_actions_original_result_identity_and_one_call_are_preserved(self):
        for action in ("YIELD", "CONTINUE", "APPROACH", "ENGAGE"):
            with self.subTest(action=action):
                original = success(action)
                client = FakeClient(original)
                result = decide_vlm(ENCODED_IMAGE, client)
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(client.calls[0], result.prompt.messages)
                self.assertIs(result.ollama_result, original)
                self.assertIs(result.ollama_result.decision, original.decision)
                self.assertTrue(result.ok)
                self.assertEqual(result.to_dict(), {
                    "prompt_version": PROMPT_VERSION, "ok": True,
                    "decision": original.decision.model_dump(mode="json"), "error": None,
                    "requested_model": original.requested_model,
                    "returned_model": original.returned_model,
                    "raw_content": original.raw_content,
                    "request_duration_s": original.request_duration_s,
                })
                self.assertEqual(set(result.to_dict()["decision"]), {"action", "reason"})

    def test_all_ollama_errors_and_original_diagnostics_survive_without_fallback(self):
        for category in OllamaErrorCategory:
            with self.subTest(category=category):
                error = OllamaError(category, "Original failure diagnostic.", 503)
                original = OllamaResult("requested", "returned", "original raw", 2.5, None, error)
                client = FakeClient(original)
                with patch("app.policy.rules.decide", side_effect=AssertionError("No fallback")):
                    result = decide_vlm(ENCODED_IMAGE, client)
                self.assertIs(result.ollama_result, original)
                self.assertIs(result.ollama_result.error, error)
                self.assertFalse(result.ok)
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(result.to_dict(), {
                    "prompt_version": PROMPT_VERSION, "ok": False, "decision": None,
                    "error": {"category": category.value, "message": error.message, "http_status": 503},
                    "requested_model": "requested", "returned_model": "returned",
                    "raw_content": "original raw", "request_duration_s": 2.5,
                })

    def test_absent_model_error_diagnostics_stay_null(self):
        original = OllamaResult("requested", None, None, 0.5, None,
                                OllamaError(OllamaErrorCategory.CONNECTION, "refused"))
        result = decide_vlm(ENCODED_IMAGE, FakeClient(original))
        self.assertIsNone(result.to_dict()["returned_model"])
        self.assertIsNone(result.to_dict()["raw_content"])
        self.assertIsNone(result.to_dict()["error"]["http_status"])

    def test_model_diagnostics_do_not_serialize_images_or_source_correlation(self):
        data = decide_vlm(ENCODED_IMAGE, FakeClient(success())).to_dict()
        serialized = json.dumps(data, allow_nan=False)
        self.assertNotIn(ENCODED_IMAGE, serialized)
        self.assertNotIn("images", data)
        self.assertNotIn("image_base64", data)
        self.assertNotIn("source_state_id", data)
        self.assertNotIn("session_id", data)
        self.assertNotIn("source_robot_timestamp_us", data)

    def test_prompt_retains_encoded_image_when_client_mutates_message_images(self):
        result = decide_vlm(ENCODED_IMAGE, FakeClient(success(), mutate_messages=True))
        self.assertEqual(result.prompt.image_base64, ENCODED_IMAGE)
        self.assertEqual(result.prompt.messages[1].images, [ENCODED_IMAGE])

    def test_invalid_encoded_image_fails_before_any_client_call(self):
        client = FakeClient(success())
        with self.assertRaises(ValueError):
            decide_vlm("not base64", client)
        self.assertEqual(client.calls, [])


class VLMOllamaRequestTests(unittest.TestCase):
    def test_fake_http_requests_contain_static_text_exact_png_bytes_and_four_action_schema(self):
        for action in ("YIELD", "CONTINUE", "APPROACH", "ENGAGE"):
            with self.subTest(action=action):
                content = json.dumps({"action": action, "reason": "Fake visible-evidence explanation."})
                transport = FakeTransport(content=content)
                result = decide_vlm(ENCODED_IMAGE, ollama_client(transport))
                self.assertTrue(result.ok)
                self.assertEqual(result.ollama_result.decision.action, action)
                self.assertEqual(result.ollama_result.raw_content, content)
                self.assertEqual(len(transport.calls), 1)
                url, body, timeout = transport.calls[0]
                self.assertEqual(url, "http://localhost:11434/api/chat")
                self.assertEqual(timeout, 4.0)
                payload = json.loads(body)
                self.assertEqual(payload, {
                    "model": "caller-vision-model", "stream": False,
                    "format": model_decision_schema(),
                    "options": {"temperature": 0.2, "seed": 42, "num_predict": 128},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": USER_PROMPT, "images": [ENCODED_IMAGE]},
                    ],
                })
                png = base64.b64decode(payload["messages"][1]["images"][0], validate=True)
                self.assertEqual(png, PNG_BYTES)
                with Image.open(io.BytesIO(png)) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.mode, "RGB")
                    self.assertEqual(image.size, (2, 3))

    def test_invalid_model_outputs_remain_failures_without_action_substitution(self):
        contents = ("not JSON",
                    '{"action":"DEFER","reason":"Fake."}',
                    '{"action":"STOP","reason":"Obsolete model action."}',
                    '{"action":"YIELD","reason":"   "}',
                    '{"action":"YIELD","reason":"Fake.","uid":12}',
                    '```json\n{"action":"YIELD","reason":"Fake."}\n```')
        for content in contents:
            with self.subTest(content=content):
                transport = FakeTransport(content=content)
                result = decide_vlm(ENCODED_IMAGE, ollama_client(transport))
                self.assertFalse(result.ok)
                self.assertIsNone(result.ollama_result.decision)
                self.assertEqual(result.ollama_result.error.category, OllamaErrorCategory.INVALID_DECISION)
                self.assertEqual(result.ollama_result.raw_content, content)
                self.assertEqual(len(transport.calls), 1)

    def test_request_failures_remain_ollama_failures_without_retries(self):
        failures = (
            (FakeTransport(error=ConnectionRefusedError("refused")), OllamaErrorCategory.CONNECTION),
            (FakeTransport(error=TimeoutError("deadline")), OllamaErrorCategory.TIMEOUT),
            (FakeTransport(response=HttpResponse(503, b"unavailable")), OllamaErrorCategory.HTTP),
            (FakeTransport(response=HttpResponse(200, b"broken JSON")), OllamaErrorCategory.RESPONSE_FORMAT),
        )
        for transport, category in failures:
            with self.subTest(category=category):
                result = decide_vlm(ENCODED_IMAGE, ollama_client(transport))
                self.assertFalse(result.ok)
                self.assertIsNone(result.ollama_result.decision)
                self.assertEqual(result.ollama_result.error.category, category)
                self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
