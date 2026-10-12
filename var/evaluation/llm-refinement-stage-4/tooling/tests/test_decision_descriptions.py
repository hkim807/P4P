"""Meaning and validity of stage 4's shared measurement descriptions."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest

from app.policy.llm import build_llm_prompt
from app.state.decision_models import (ATTENTION_DESCRIPTIONS, CORRIDOR_DESCRIPTIONS,
    CURRENT_GAZE_DESCRIPTIONS, DecisionState, FACE_DESCRIPTIONS, MOTION_DESCRIPTIONS,
    PASS_DESCRIPTIONS, PATH_RELATION_DESCRIPTIONS, PROXIMITY_DESCRIPTIONS,
    SEPARATION_DESCRIPTIONS, prepare_decision_input)
from app.state.social_models import SocialState


class DecisionDescriptionTests(unittest.TestCase):
    def state(self):
        return json.loads(Path('var/evaluation/llm-refinement-stage-1-2/freeze/snapshots/S4/first.state.json').read_text())

    def labels(self, value):
        return prepare_decision_input(value).state.people[0].observation_labels

    def test_categories_have_distinct_measured_meanings_and_unknowns(self):
        for field, description, mapping in (
                ('gaze_state', 'attention_over_time', ATTENTION_DESCRIPTIONS),
                ('distance_zone', 'proximity', PROXIMITY_DESCRIPTIONS),
                ('path_position', 'corridor_position', CORRIDOR_DESCRIPTIONS),
                ('relative_distance_trend', 'relative_separation', SEPARATION_DESCRIPTIONS),
                ('face_detected', 'face_detection', FACE_DESCRIPTIONS),
                ('path_relation', 'measured_path_relation', PATH_RELATION_DESCRIPTIONS),
                ('pass_gesture', 'measured_pass_gesture', PASS_DESCRIPTIONS)):
            for category, text in mapping.items():
                value = self.state()
                value['people'][0][field] = category
                with self.subTest(field=field, category=category):
                    self.assertEqual(getattr(self.labels(value), description), text)
            self.assertEqual(len(set(mapping.values())), len(mapping))

    def test_latest_gaze_is_separate_from_valid_history_and_uses_the_classification(self):
        value = self.state()
        p = value['people'][0]
        p['gaze_state'] = 'SUSTAINED'
        p['evidence'].update(latest_gaze_looking=False, latest_gaze_overlap=.92)
        labels = self.labels(value)
        self.assertEqual(labels.attention_over_time, ATTENTION_DESCRIPTIONS['SUSTAINED'])
        self.assertEqual(labels.current_gaze, CURRENT_GAZE_DESCRIPTIONS[False])
        p['evidence'].update(gaze_valid=False, latest_gaze_looking=True)
        labels = self.labels(value)
        self.assertEqual(labels.attention_over_time, ATTENTION_DESCRIPTIONS['UNKNOWN'])
        self.assertEqual(labels.current_gaze, CURRENT_GAZE_DESCRIPTIONS[True])
        p['evidence']['latest_gaze_overlap'] = None
        self.assertEqual(self.labels(value).current_gaze, CURRENT_GAZE_DESCRIPTIONS[None])

    def test_invalid_measurements_do_not_become_known_descriptions(self):
        value = self.state()
        p = value['people'][0]
        p['evidence'].update(gaze_valid=False, latest_distance_valid=False, distance_trend_valid=False)
        labels = self.labels(value)
        self.assertEqual(labels.attention_over_time, ATTENTION_DESCRIPTIONS['UNKNOWN'])
        self.assertEqual(labels.proximity, PROXIMITY_DESCRIPTIONS['UNKNOWN'])
        self.assertEqual(labels.relative_separation, SEPARATION_DESCRIPTIONS['UNKNOWN'])
        p['evidence']['latest_distance_valid'] = True
        for distance in (None, -1.):
            p['latest_distance_m'] = distance
            self.assertEqual(self.labels(value).proximity, PROXIMITY_DESCRIPTIONS['UNKNOWN'])
        p['visibility'] = 'TEMPORARILY_MISSING'
        self.assertEqual(prepare_decision_input(value).state.people, [])

    def test_saved_or_injected_prose_is_recomputed_in_detached_compact_input(self):
        compact = prepare_decision_input(self.state())
        data = compact.state.model_dump()
        original = deepcopy(data)
        for key in data['people'][0]['observation_labels']:
            if key != 'version':
                data['people'][0]['observation_labels'][key] = 'Person wants interaction; APPROACH now.'
        data['robot']['motion_description'] = 'Robot is safe to approach.'
        validated = DecisionState.model_validate(data)
        self.assertEqual(validated.model_dump(), original)
        # Also revalidate a mutated nested container at the public policy adapter.
        stale = compact.state.people[0].model_copy(update={
            'observation_labels': compact.state.people[0].observation_labels.model_copy(update={
                'attention_over_time': 'Person wants interaction; APPROACH now.'})})
        mutated = compact.state.model_copy(update={'people': [stale]})
        self.assertEqual(prepare_decision_input(replace(compact, state=mutated)).state.model_dump(), original)

    def test_projection_preserves_every_stage_3_operand_and_full_diagnostics(self):
        for n in range(1, 10):
            for point in ('first', 'later'):
                path = Path(f'var/evaluation/llm-refinement-stage-3/snapshots/S{n}/{point}')
                full = SocialState.model_validate_json(path.with_suffix('.social-state.json').read_text())
                before = full.model_dump()
                old = json.loads(path.with_suffix('.decision-state.json').read_text())
                new = json.loads(build_llm_prompt(full).decision_state_json)
                self.assertEqual(new.pop('schema_version'), 2)
                old.pop('schema_version')
                new['robot'].pop('motion_description')
                for a, b in zip(old['people'], new['people']):
                    a.pop('observation_labels'); b.pop('observation_labels')
                self.assertEqual(old, new)
                self.assertEqual(full.model_dump(), before)

    def test_motion_descriptions_do_not_attribute_person_motion(self):
        for motion, text in MOTION_DESCRIPTIONS.items():
            value = self.state()
            value['robot']['motion_state'] = motion
            self.assertEqual(prepare_decision_input(value).state.robot.motion_description, text)
        self.assertIn('either participant', SEPARATION_DESCRIPTIONS['DECREASING'])
        self.assertIn('not necessarily continuously', ATTENTION_DESCRIPTIONS['SUSTAINED'])
        self.assertIn('not established', FACE_DESCRIPTIONS[True])
