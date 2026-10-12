"""Shared descriptive evidence must not change policy inputs or readiness."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.policy.llm import build_llm_prompt
from app.policy.rules import decide
from app.social_pipeline import SocialPipeline
from app.state.social_models import PersonSocialState, SocialState
from tests.test_llm_policy import social_state
from tests.test_social_state import sample


class ObservationLabelTests(unittest.TestCase):
    def person(self, **updates):
        data = social_state().people[0].model_dump()
        data.update(updates)
        return PersonSocialState.model_validate(data)

    def test_attention_is_a_history_description_not_an_intent(self):
        for category, label in (
            ('NONE', 'LITTLE_OR_NO_LOOKING_AT_ROBOT'),
            ('INTERMITTENT', 'INTERMITTENT_LOOKING_AT_ROBOT'),
            ('SUSTAINED', 'SUSTAINED_LOOKING_AT_ROBOT'), ('UNKNOWN', 'UNKNOWN'),
        ):
            with self.subTest(category=category):
                p = self.person(gaze_state=category)
                self.assertEqual(p.observation_labels.attention_over_time, label)

    def test_current_gaze_can_differ_from_history_or_precede_readiness(self):
        data = self.person(gaze_state='SUSTAINED').model_dump()
        data['evidence'].update(latest_gaze_looking=False, latest_gaze_overlap=.3)
        p = PersonSocialState.model_validate(data)
        self.assertEqual(p.observation_labels.attention_over_time, 'SUSTAINED_LOOKING_AT_ROBOT')
        self.assertEqual(p.observation_labels.current_gaze, 'NOT_LOOKING_AT_ROBOT')
        data['evidence'].update(gaze_valid=False, latest_gaze_looking=True, latest_gaze_overlap=.98)
        p = PersonSocialState.model_validate(data)
        self.assertEqual(p.observation_labels.attention_over_time, 'UNKNOWN')
        self.assertEqual(p.observation_labels.current_gaze, 'LOOKING_AT_ROBOT')
        data['evidence']['latest_gaze_overlap'] = None
        self.assertEqual(PersonSocialState.model_validate(data).observation_labels.current_gaze, 'UNKNOWN')

    def test_proximity_and_position_do_not_prescribe_an_action(self):
        for zone, label in (
            ('TOO_CLOSE', 'CLOSER_THAN_CONVERSATION_DISTANCE'),
            ('INTERACTION_RANGE', 'WITHIN_CONVERSATION_DISTANCE'),
            ('APPROACHABLE', 'BEYOND_CONVERSATION_DISTANCE'),
            ('FAR', 'BEYOND_CONFIGURED_SOCIAL_RANGE'), ('UNKNOWN', 'UNKNOWN'),
        ):
            with self.subTest(zone=zone):
                self.assertEqual(self.person(distance_zone=zone).observation_labels.proximity, label)
        for position, label in (
            ('IN_WAY', 'IN_ROBOT_FORWARD_CORRIDOR'), ('TO_SIDE', 'BESIDE_ROBOT_FORWARD_CORRIDOR'),
            ('BEHIND', 'BEHIND_ROBOT'), ('UNKNOWN', 'UNKNOWN'),
        ):
            self.assertEqual(self.person(path_position=position).observation_labels.corridor_position, label)
        for trend, label in (('DECREASING', 'GETTING_CLOSER'), ('STABLE', 'STEADY'),
                             ('INCREASING', 'GETTING_FARTHER'), ('UNKNOWN', 'UNKNOWN')):
            self.assertEqual(self.person(relative_distance_trend=trend).observation_labels.relative_separation, label)

    def test_invalid_and_missing_evidence_cannot_support_current_labels(self):
        data = self.person(gaze_state='SUSTAINED', path_position='IN_WAY').model_dump()
        data['evidence'].update(gaze_valid=False, latest_distance_valid=False, distance_trend_valid=False)
        labels = PersonSocialState.model_validate(data).observation_labels
        self.assertEqual((labels.attention_over_time, labels.proximity, labels.relative_separation),
                         ('UNKNOWN', 'UNKNOWN', 'UNKNOWN'))
        data['visibility'] = 'TEMPORARILY_MISSING'
        data['evidence'].update(gaze_valid=True, latest_distance_valid=True, distance_trend_valid=True,
                                latest_gaze_looking=True, latest_gaze_overlap=.98)
        labels = PersonSocialState.model_validate(data).observation_labels.model_dump(exclude={'version'})
        self.assertEqual(set(labels.values()), {'UNKNOWN'})

    def test_supplied_stale_labels_are_recomputed_without_altering_evidence(self):
        data = self.person(gaze_state='NONE').model_dump()
        data['observation_labels']['attention_over_time'] = 'SUSTAINED_LOOKING_AT_ROBOT'
        before = deepcopy(data)
        result = PersonSocialState.model_validate(data).model_dump()
        self.assertEqual(result['observation_labels']['attention_over_time'], 'LITTLE_OR_NO_LOOKING_AT_ROBOT')
        self.assertEqual(data, before)
        result.pop('observation_labels'); before.pop('observation_labels')
        self.assertEqual(result, before)

    def test_rules_and_llm_receive_the_same_complete_pipeline_state(self):
        pipeline = SocialPipeline('shared-label-test')
        for i in range(20):
            pipeline.process(sample(i))
        with patch('app.social_pipeline.decide', wraps=decide) as rule:
            result = pipeline.process(sample(20))
        rule_input = rule.call_args.args[0]
        llm_input = json.loads(build_llm_prompt(SocialState.model_validate(result['social_state'])).social_state_json)
        self.assertEqual(rule_input, llm_input)
        self.assertIn('observation_labels', rule_input['people'][0])

    def test_historical_nine_states_retain_every_original_field_and_rule_action(self):
        expected = ['CONTINUE', 'APPROACH', 'ENGAGE', 'YIELD', 'CONTINUE',
                    'CONTINUE', 'CONTINUE', 'CONTINUE', 'APPROACH']
        for i, action in enumerate(expected, 1):
            with self.subTest(scenario=i):
                path = Path(f'var/evaluation/final-scenario-tuning/run-02/llm/S{i}/models.jsonl')
                original = json.loads(path.read_text().splitlines()[0])['social_state']
                state = SocialState.model_validate(original)
                self.assertEqual(decide(state).decision, action)
                upgraded = json.loads(build_llm_prompt(state).social_state_json)
                for person in upgraded['people']:
                    person.pop('observation_labels')
                self.assertEqual(upgraded, original)
