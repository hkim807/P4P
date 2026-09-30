"""Unit tests for the pure BehaviorIntent-to-command mapping."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from robot.navel_client.behavior.commands import (
    COMMAND_TYPES,
    ApproachCommand,
    ContinueCommand,
    EngageCommand,
    YieldCommand,
)
from robot.navel_client.behavior.intent import (
    NavelAction as Action,
    NavelBehaviorIntent,
    NavelPassingSide as PassingSide,
)
from robot.navel_client.behavior.mapper import BehaviorIntentMapper, BehaviorMappingError


def intent(
    action: Action, *, target=None, preferences=None
) -> NavelBehaviorIntent:
    return NavelBehaviorIntent.from_payload(
        {
            "schema_version": "1.0",
            "decision_id": f"decision-{action.value.lower()}",
            "observation_id": "observation-1",
            "social_state_id": "state-1",
            "created_at_us": 1_000_000,
            "action": action.value,
            "target_human_id": target,
            "preferences": preferences or {},
            "valid_for_ms": 1_000,
            "reason_codes": ["TEST_DECISION"],
            "decision_confidence": 0.8,
        }
    )


class BehaviorIntentMapperTests(unittest.TestCase):
    def setUp(self):
        self.mapper = BehaviorIntentMapper()

    def test_maps_every_action_to_its_exact_typed_command(self):
        cases = {
            Action.CONTINUE: (intent(Action.CONTINUE), ContinueCommand()),
            Action.APPROACH: (intent(Action.APPROACH, target="person-1",
                preferences={"preferred_social_distance_m": 1.25, "target_speed_mps": .35}),
                ApproachCommand("person-1", 1.25, .35)),
            Action.ENGAGE: (intent(Action.ENGAGE, target="person-1"), EngageCommand("person-1")),
            Action.YIELD: (intent(Action.YIELD, target="person-1"), YieldCommand("person-1")),
        }
        self.assertEqual(set(cases), set(Action))
        for action, (source, expected) in cases.items():
            with self.subTest(action=action.value):
                self.assertEqual(self.mapper.map(source), expected)
        self.assertEqual(
            {type(expected) for _, expected in cases.values()}, set(COMMAND_TYPES)
        )

    def test_omitted_optional_values_stay_none(self):
        command = self.mapper.map(intent(Action.YIELD))
        self.assertEqual(
            command,
            YieldCommand(),
        )

    def test_social_distance_remains_standoff_distance_not_travel_distance(self):
        command = self.mapper.map(
            intent(
                Action.APPROACH,
                target="person-1",
                preferences={"preferred_social_distance_m": 1.37},
            )
        )
        self.assertEqual(command.preferred_social_distance_m, 1.37)
        self.assertFalse(hasattr(command, "travel_distance_m"))

    def test_missing_required_preference_fails_instead_of_silently_mapping(self):
        valid = intent(
            Action.APPROACH, target="person-1", preferences={"preferred_social_distance_m": 1.2}
        )
        impossible = replace(
            valid,
            preferences=replace(valid.preferences, preferred_social_distance_m=None),
        )
        with self.assertRaisesRegex(BehaviorMappingError, "preferred_social_distance_m"):
            self.mapper.map(impossible)

    def test_unsupported_action_fails_clearly(self):
        with self.assertRaisesRegex(BehaviorMappingError, "FLY"):
            self.mapper.map(SimpleNamespace(action="FLY"))  # type: ignore[arg-type]

    def test_canonical_action_enum_and_command_coverage_are_exhaustive(self):
        self.assertEqual(len(Action), 4)
        self.assertEqual(len(COMMAND_TYPES), len(Action))


if __name__ == "__main__":
    unittest.main()
