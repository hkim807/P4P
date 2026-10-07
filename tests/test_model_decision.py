"""Strict model-output parsing and exported JSON Schema contract."""

import json
from pathlib import Path
import re
import unittest

from pydantic import ValidationError

from app.domain.model_decision import (
    ModelDecision,
    ModelDecisionValidationError,
    model_decision_schema,
    parse_model_decision,
)


class ModelDecisionTests(unittest.TestCase):
    def assert_invalid(self, content):
        with self.assertRaises(ModelDecisionValidationError):
            parse_model_decision(content)

    def test_all_four_actions_are_accepted(self):
        for action in ("STOP", "CONTINUE", "APPROACH", "ENGAGE"):
            with self.subTest(action=action):
                decision = parse_model_decision(json.dumps({
                    "action": action,
                    "reason": "Brief explanation.",
                }))
                self.assertIsInstance(decision, ModelDecision)
                self.assertEqual(decision.model_dump(), {
                    "action": action,
                    "reason": "Brief explanation.",
                })

    def test_non_whitespace_reason_is_preserved_exactly(self):
        for reason in ("x", "  Brief explanation.\n", "설명입니다.", "\t!\r"):
            with self.subTest(reason=reason):
                decision = parse_model_decision(json.dumps({
                    "action": "STOP", "reason": reason,
                }))
                self.assertEqual(decision.reason, reason)
        decision = parse_model_decision(
            ' \n { "reason": "A reason.", "action": "STOP" } \t '
        )
        self.assertEqual(decision.action, "STOP")

    def test_missing_and_extra_fields_are_rejected(self):
        for value in (
            {},
            {"action": "STOP"},
            {"reason": "A reason."},
            {"action": "STOP", "reason": "A reason.", "target_uid": 17},
            {"action": "STOP", "reason": "A reason.", "confidence": None},
            {"Action": "STOP", "reason": "A reason."},
        ):
            with self.subTest(value=value):
                self.assert_invalid(json.dumps(value))

    def test_invalid_actions_are_rejected_without_normalization(self):
        for action in ("YIELD", "DEFER", "stop", "Stop", " STOP", "STOP ", "", "WAIT"):
            with self.subTest(action=action):
                self.assert_invalid(json.dumps({"action": action, "reason": "A reason."}))

    def test_incorrect_field_types_are_not_coerced(self):
        for field in ("action", "reason"):
            for value in (0, 1.5, True, False, None, [], {}):
                with self.subTest(field=field, value=value):
                    payload = {"action": "STOP", "reason": "A reason."}
                    payload[field] = value
                    self.assert_invalid(json.dumps(payload))
                    with self.assertRaises(ValidationError):
                        ModelDecision.model_validate(payload)

    def test_empty_and_whitespace_only_reasons_are_rejected(self):
        for reason in ("", " ", "\t\r\n", " \t\n ", "\u2003", "\u00a0"):
            with self.subTest(reason=reason):
                self.assert_invalid(json.dumps({"action": "STOP", "reason": reason}))

    def test_all_python_whitespace_has_matching_schema_and_runtime_validation(self):
        whitespace_characters = tuple(
            chr(codepoint) for codepoint in range(0x110000)
            if chr(codepoint).isspace()
        )
        pattern = model_decision_schema()["properties"]["reason"]["pattern"]
        for whitespace in whitespace_characters:
            with self.subTest(codepoint=ord(whitespace)):
                self.assertIsNone(re.search(pattern, whitespace))
                self.assert_invalid(json.dumps({"action": "STOP", "reason": whitespace}))
                with self.assertRaises(ValidationError):
                    ModelDecision(action="STOP", reason=whitespace)

                reason = whitespace + "Brief explanation." + whitespace
                self.assertIsNotNone(re.search(pattern, reason))
                self.assertEqual(parse_model_decision(json.dumps({
                    "action": "STOP", "reason": reason,
                })).reason, reason)
                self.assertEqual(ModelDecision(action="STOP", reason=reason).reason, reason)

    def test_malformed_and_nonstandard_json_is_rejected(self):
        for content in (
            "",
            " ",
            '{"action": "STOP", "reason": "A reason."',
            '{"action": "STOP", "reason": "A reason.",}',
            "{'action': 'STOP', 'reason': 'A reason.'}",
            '{"action": "STOP", "reason": "A reason."} {}',
            '{"action": "STOP", "reason": NaN}',
            '{"action": "STOP", "reason": Infinity}',
            '{"action": "STOP", "reason": -Infinity}',
        ):
            with self.subTest(content=content):
                self.assert_invalid(content)

    def test_duplicate_keys_are_rejected_including_identical_values(self):
        for content in (
            '{"action": "STOP", "action": "ENGAGE", "reason": "A reason."}',
            '{"action": "STOP", "action": "STOP", "reason": "A reason."}',
            '{"action": "STOP", "reason": "First", "reason": "Second"}',
            '{"action": "STOP", "reason": "Same", "reason": "Same"}',
            '{"action": "STOP", "\\u0061ction": "STOP", "reason": "A reason."}',
        ):
            with self.subTest(content=content):
                self.assert_invalid(content)

    def test_non_object_json_is_rejected(self):
        for value in (
            None, True, False, 12, 1.5, "A reason.", [],
            [{"action": "STOP", "reason": "A reason."}],
        ):
            with self.subTest(value=value):
                self.assert_invalid(json.dumps(value))

    def test_prose_and_code_fences_are_not_repaired(self):
        valid = '{"action": "STOP", "reason": "A reason."}'
        for content in (
            "Here is the decision: " + valid,
            valid + "\nThis is my decision.",
            "```json\n" + valid + "\n```",
            "```\n" + valid + "\n```",
        ):
            with self.subTest(content=content):
                self.assert_invalid(content)

    def test_contract_is_frozen_and_validation_error_is_a_value_error(self):
        self.assertTrue(issubclass(ModelDecisionValidationError, ValueError))
        decision = ModelDecision(action="STOP", reason="A reason.")
        with self.assertRaises(ValidationError):
            decision.action = "CONTINUE"
        with self.assertRaises(ValidationError):
            decision.reason = "Another reason."

    def test_schema_artifact_matches_generated_runtime_schema(self):
        schema_path = Path(__file__).resolve().parents[1] / "schemas/v1/model-decision.schema.json"
        expected = ModelDecision.model_json_schema(mode="validation")
        self.assertEqual(model_decision_schema(), expected)
        self.assertEqual(json.loads(schema_path.read_text(encoding="utf-8")), expected)

    def test_schema_exposes_the_same_field_constraints(self):
        schema = model_decision_schema()
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), {"action", "reason"})
        self.assertEqual(set(schema["properties"]), {"action", "reason"})

        action_schema = schema["properties"]["action"]
        self.assertEqual(action_schema["type"], "string")
        self.assertEqual(set(action_schema["enum"]), {
            "STOP", "CONTINUE", "APPROACH", "ENGAGE",
        })
        reason_schema = schema["properties"]["reason"]
        self.assertEqual(reason_schema["type"], "string")
        self.assertEqual(reason_schema["minLength"], 1)
        for reason, expected_valid in (
            ("", False), (" \t\n", False), ("\u2003", False),
            ("\u001c", False), ("\u001d", False),
            ("\u001e", False), ("\u001f", False),
            ("x", True), ("  Explanation.\n", True), ("설명", True),
            ("\u001cExplanation.", True), ("\u001dExplanation.", True),
            ("\u001eExplanation.", True), ("\u001fExplanation.", True),
        ):
            with self.subTest(reason=reason):
                schema_valid = (
                    len(reason) >= reason_schema["minLength"]
                    and re.search(reason_schema["pattern"], reason) is not None
                )
                self.assertEqual(schema_valid, expected_valid)
                content = json.dumps({"action": "STOP", "reason": reason})
                if schema_valid:
                    self.assertEqual(parse_model_decision(content).reason, reason)
                else:
                    self.assert_invalid(content)

    def test_schema_calls_return_independent_mutable_dictionaries(self):
        schema = model_decision_schema()
        schema["properties"]["action"]["enum"].append("YIELD")
        schema["required"].clear()
        schema["properties"]["reason"]["pattern"] = ".*"
        fresh = model_decision_schema()
        self.assertNotIn("YIELD", fresh["properties"]["action"]["enum"])
        self.assertEqual(set(fresh["required"]), {"action", "reason"})
        self.assertEqual(fresh, ModelDecision.model_json_schema(mode="validation"))


if __name__ == "__main__":
    unittest.main()
