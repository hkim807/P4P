"""Standard-library parsing and server wire-compatibility tests."""

from __future__ import annotations

import ast
import math
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.domain.models import Action as ServerAction
from app.domain.models import BehaviorIntent as ServerBehaviorIntent
from robot.navel_client.behavior import BehaviorController, BehaviorHandlingStatus
from robot.navel_client.behavior.commands import COMMAND_TYPES
from robot.navel_client.behavior.intent import (
    NavelAction,
    NavelBehaviorIntent,
    NavelIntentParseError,
)


ROOT = Path(__file__).resolve().parents[1]


def intent_payload(action: str = "MONITOR") -> dict:
    targeted = {"ORIENT", "APPROACH", "GREET", "GUIDE", "DISENGAGE"}
    preferences = {}
    if action == "SLOW":
        preferences["target_speed_mps"] = 0.3
    elif action == "APPROACH":
        preferences["preferred_social_distance_m"] = 1.2
    elif action == "WAIT":
        preferences["hold_duration_s"] = 2.0
    return {
        "schema_version": "1.0",
        "decision_id": f"decision-{action.lower()}",
        "observation_id": "observation-1",
        "social_state_id": "state-1",
        "created_at_us": 1_000_000,
        "action": action,
        "target_human_id": "person-1" if action in targeted else None,
        "preferences": preferences,
        "valid_for_ms": 1_000,
        "reason_codes": ["TEST_DECISION"],
        "decision_confidence": 0.8,
    }


class NavelBehaviorIntentParserTests(unittest.TestCase):
    def test_parses_representative_server_model_dump(self):
        server_intent = ServerBehaviorIntent.model_validate(
            intent_payload("APPROACH")
        )
        parsed = NavelBehaviorIntent.from_payload(
            server_intent.model_dump(mode="json")
        )
        self.assertEqual(parsed.action, NavelAction.APPROACH)
        self.assertEqual(parsed.target_human_id, "person-1")
        self.assertEqual(parsed.preferences.preferred_social_distance_m, 1.2)
        self.assertIsNone(parsed.preferences.target_speed_mps)

    def test_client_and_server_action_values_are_identical(self):
        self.assertEqual(
            {action.value for action in NavelAction},
            {action.value for action in ServerAction},
        )
        self.assertEqual(len(NavelAction), 12)

    def test_all_server_actions_complete_the_client_behavior_path(self):
        robot = Mock()
        controller = BehaviorController(robot)
        command_types = set()
        for server_action in ServerAction:
            with self.subTest(action=server_action.value), self.assertLogs(
                "robot.navel_client.behavior", "INFO"
            ):
                server_intent = ServerBehaviorIntent.model_validate(
                    intent_payload(server_action.value)
                )
                result = controller.handle_response(
                    {
                        "behavior_intent": server_intent.model_dump(mode="json")
                    }
                )
                self.assertEqual(result.status, BehaviorHandlingStatus.HANDLED)
                self.assertEqual(result.execution.action.value, server_action.value)
                command_types.add(result.execution.command_type)
        self.assertEqual(command_types, {kind.__name__ for kind in COMMAND_TYPES})
        self.assertEqual(robot.mock_calls, [])

    def test_optional_fields_may_be_omitted_or_null(self):
        payload = intent_payload()
        payload.pop("target_human_id")
        payload.pop("decision_confidence")
        payload["preferences"] = {
            "target_speed_mps": None,
            "preferred_social_distance_m": None,
            "passing_side": None,
            "orientation_target_rad": None,
            "hold_duration_s": None,
        }
        parsed = NavelBehaviorIntent.from_payload(payload)
        self.assertIsNone(parsed.target_human_id)
        self.assertIsNone(parsed.decision_confidence)
        self.assertIsNone(parsed.preferences.passing_side)

    def test_missing_required_fields_fail(self):
        for field in (
            "schema_version",
            "decision_id",
            "observation_id",
            "social_state_id",
            "created_at_us",
            "action",
            "valid_for_ms",
            "reason_codes",
        ):
            payload = intent_payload()
            del payload[field]
            with self.subTest(field=field), self.assertRaisesRegex(
                NavelIntentParseError, field
            ):
                NavelBehaviorIntent.from_payload(payload)

    def test_invalid_action_target_and_preference_structures_fail(self):
        cases = (
            ({**intent_payload(), "action": "FLY"}, "action"),
            ({**intent_payload(), "target_human_id": 7}, "target_human_id"),
            ({**intent_payload(), "preferences": []}, "preferences"),
            ({**intent_payload(), "preferences": {"unknown": 1}}, "unexpected"),
            ({**intent_payload(), "unexpected": True}, "unexpected"),
        )
        for payload, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                NavelIntentParseError, message
            ):
                NavelBehaviorIntent.from_payload(payload)

    def test_non_numeric_boolean_and_non_finite_preferences_fail(self):
        invalid_values = ("fast", True, math.nan, math.inf, -math.inf)
        for value in invalid_values:
            payload = intent_payload("SLOW")
            payload["preferences"]["target_speed_mps"] = value
            with self.subTest(value=value), self.assertRaises(
                NavelIntentParseError
            ):
                NavelBehaviorIntent.from_payload(payload)

    def test_mapper_required_values_are_checked_at_parse_boundary(self):
        for action, field in (
            ("SLOW", "target_speed_mps"),
            ("APPROACH", "preferred_social_distance_m"),
            ("WAIT", "hold_duration_s"),
        ):
            payload = intent_payload(action)
            payload["preferences"][field] = None
            with self.subTest(action=action), self.assertRaisesRegex(
                NavelIntentParseError, field
            ):
                NavelBehaviorIntent.from_payload(payload)


class NavelDependencyBoundaryTests(unittest.TestCase):
    def test_navel_requirements_has_no_pypi_dependencies(self):
        requirements = (ROOT / "requirements-navel.txt").read_text(
            encoding="utf-8"
        )
        packages = [
            line
            for line in requirements.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(packages, [])

    def test_production_client_has_no_server_or_third_party_imports(self):
        forbidden = ("pydantic", "flask", "openai", "app.domain.models")
        client_root = ROOT / "robot" / "navel_client"
        violations = []
        for path in client_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    if any(
                        module == name or module.startswith(f"{name}.")
                        for name in forbidden
                    ):
                        violations.append(f"{path.relative_to(ROOT)}: {module}")
        self.assertEqual(violations, [])

    def test_behavior_package_imports_when_pydantic_is_blocked(self):
        code = """
import importlib.abc
import sys

class BlockPydantic(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pydantic' or fullname.startswith('pydantic.'):
            raise ModuleNotFoundError('pydantic deliberately blocked')
        return None

sys.meta_path.insert(0, BlockPydantic())
import robot.navel_client.behavior
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_main_import_graph_needs_only_the_robot_provided_navel_sdk(self):
        code = """
import importlib.abc
import sys
import types

class BlockServerDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ('pydantic', 'flask', 'openai', 'app.domain.models')
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise ModuleNotFoundError(fullname + ' deliberately blocked')
        return None

sys.meta_path.insert(0, BlockServerDependencies())
sys.modules['navel'] = types.ModuleType('navel')
import robot.navel_client.main
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
