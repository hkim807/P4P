"""Unsupported sensor cues are absent, rejected live, and isolated offline."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.domain.models import RawObservationFrame
from app.policy.llm import build_llm_prompt
from app.policy.rules import rule_readiness
from app.replay.legacy import remove_legacy_unknown_cues
from app.replay.llm_inputs import iter_replay_states
from app.social_pipeline import SocialPipeline
from app.state.decision_models import DecisionState, prepare_decision_input
from app.state.social_models import SocialState
from tests.test_policy_rules import ready_state
from tests.test_social_state import sample


REMOVED = ('path_relation', 'pass_gesture', 'measured_path_relation', 'measured_pass_gesture')


class SupportedNavelCueTests(unittest.TestCase):
    def assert_absent(self, value):
        if isinstance(value, dict):
            self.assertFalse(set(value).intersection(REMOVED))
            for child in value.values():
                self.assert_absent(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_absent(child)

    def test_fields_absent_end_to_end_and_from_published_schemas(self):
        pipeline = SocialPipeline('supported')
        for i in range(24):
            result = pipeline.process(sample(i))
            self.assert_absent(result)
            self.assert_absent(RawObservationFrame.model_validate(sample(i)).model_dump())
        state = SocialState.model_validate(result['social_state'])
        self.assert_absent(prepare_decision_input(state).state.model_dump())
        self.assert_absent(json.loads(build_llm_prompt(state).decision_state_json))
        for model, name in ((RawObservationFrame, 'raw-observation-frame'),
                (SocialState, 'social-state'), (DecisionState, 'decision-state')):
            schema = model.model_json_schema()
            self.assert_absent(schema)
            self.assertEqual(json.loads(Path(f'schemas/v1/{name}.schema.json').read_text()), schema
                if model is not RawObservationFrame else {'$schema':'https://json-schema.org/draft/2020-12/schema', **schema})

    def test_removed_cues_are_rejected_in_current_live_contracts(self):
        for name, values in (('path_relation', ('UNKNOWN', 'CLEAR', 'CONFLICT')),
                              ('pass_gesture', ('UNKNOWN', 'PASS'))):
            for value in values:
                raw = sample(0)
                raw['people'][0][name] = value
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        RawObservationFrame.model_validate(raw)
                    state = ready_state()
                    state['people'][0][name] = value
                    with self.assertRaises(ValueError):
                        SocialState.model_validate(state)
                    with self.assertRaises(ValueError):
                        rule_readiness(state)

    def test_no_unsupported_readiness_shortcuts(self):
        state = ready_state()
        state['people'][0]['gaze_state'] = 'UNKNOWN'
        state['people'][0]['evidence']['gaze_valid'] = False
        self.assertEqual(rule_readiness(state), 'INSUFFICIENT_GAZE_EVIDENCE')

    def test_archived_unknowns_are_migrated_without_editing_original_records(self):
        old = ready_state()
        old['people'][0].update(path_relation='UNKNOWN', pass_gesture='UNKNOWN')
        original = deepcopy(old)
        clean = remove_legacy_unknown_cues(old)
        self.assertEqual(old, original)
        self.assert_absent(clean)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'old-state.jsonl'
            content = json.dumps(old)+'\n'
            path.write_text(content)
            result = list(iter_replay_states(path, 'social'))[0]
            self.assert_absent(result.state.model_dump())
            self.assertEqual(result.source['source_record'], old)
            self.assertEqual(path.read_text(), content)

    def test_archived_non_unknown_measurements_cannot_be_silently_erased(self):
        for name, value in (('path_relation', 'CLEAR'), ('path_relation', 'CONFLICT'),
                             ('pass_gesture', 'PASS')):
            old = ready_state()
            old['people'][0][name] = value
            with self.assertRaisesRegex(ValueError, 'use the frozen implementation'):
                remove_legacy_unknown_cues(old)
