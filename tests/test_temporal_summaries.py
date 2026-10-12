"""Causal, descriptive temporal additions must preserve rules and shared input."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.policy.llm import build_llm_prompt
from app.policy.rules import decide, rule_readiness
from app.state.decision_models import (DecisionState, TEMPORAL_DESCRIPTIONS,
    prepare_decision_input)
from app.state.social_models import SocialState
from scripts.frozen_rule_reference import load_frozen_rules
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import TemporalConfig
from app.state.tracks import TrackConfig
from tests.test_policy_rules import ready_state


class TemporalSummaryTests(unittest.TestCase):
    def test_none_retains_all_18_stage5_payloads_exactly(self):
        for i in range(1, 10):
            for point in ('first', 'later'):
                root = Path(f'var/evaluation/llm-refinement-stage-5/refined/snapshots/S{i}')
                state = SocialState.model_validate_json((root/f'{point}.social-state.json').read_text())
                old = json.loads((root/f'{point}.decision-state.json').read_text())
                current = prepare_decision_input(state, temporal_summary_mode='NONE').state
                self.assertEqual(current.model_dump(mode='json'), old)
                self.assertNotIn('temporal_summary', current.canonical_json())

    def test_earlier_looking_requires_measured_time_and_known_current_not_looking(self):
        state = ready_state()
        e = state['people'][0]['evidence']
        e.update(latest_gaze_looking=False, latest_gaze_overlap=.1, looking_time_s=.2)
        summary = prepare_decision_input(state, temporal_summary_mode='LOOKING_CHANGE').state
        self.assertEqual(summary.people[0].temporal_summary.kind, 'LOOKING_CHANGE')
        self.assertEqual(summary.schema_version, 4)
        for update in ({'gaze_valid':False}, {'latest_gaze_looking':True},
                       {'latest_gaze_looking':None}, {'latest_gaze_overlap':None}, {'looking_time_s':0.}):
            value = deepcopy(state)
            value['people'][0]['evidence'].update(update)
            self.assertIsNone(prepare_decision_input(value, temporal_summary_mode='LOOKING_CHANGE').state.people[0].temporal_summary)

    def test_repeated_looking_requires_multiple_measured_bouts_without_short_duration_claim(self):
        state = ready_state()
        e = state['people'][0]['evidence']
        e['looking_bouts'] = 2
        summary = prepare_decision_input(state, temporal_summary_mode='REPEATED_LOOKING').state.people[0].temporal_summary
        self.assertEqual(summary.kind, 'REPEATED_LOOKING')
        self.assertIn('durations are not established', summary.description)
        for update in ({'looking_bouts':1}, {'gaze_valid':False}, {'latest_gaze_overlap':None}):
            value = deepcopy(state)
            value['people'][0]['evidence'].update(update)
            self.assertIsNone(prepare_decision_input(value, temporal_summary_mode='REPEATED_LOOKING').state.people[0].temporal_summary)

    def test_resumption_is_matching_current_track_event_not_uid_reassociation_or_future(self):
        state = ready_state()
        p = state['people'][0]
        event = {'type':'REACQUIRED', 'uid':p['uid'], 'track_epoch':p['track_epoch'],
                 'timestamp_us':state['robot_timestamp_us'], 'reason':'same_uid_within_grace'}
        state['track_events'] = [event]
        summary = prepare_decision_input(state, temporal_summary_mode='DETECTION_RESUMED').state.people[0].temporal_summary
        self.assertEqual(summary.kind, 'DETECTION_RESUMED')
        self.assertIn('gaze during the detection gap is unknown', summary.description)
        for update in ({'type':'REASSOCIATED'}, {'type':'ACQUIRED'}, {'uid':p['uid']+1},
                       {'track_epoch':p['track_epoch']+1},
                       {'timestamp_us':state['robot_timestamp_us']-1},
                       {'timestamp_us':state['robot_timestamp_us']+1}):
            value = deepcopy(state)
            value['track_events'][0].update(update)
            self.assertIsNone(prepare_decision_input(value, temporal_summary_mode='DETECTION_RESUMED').state.people[0].temporal_summary)
        state['people'][0]['visibility'] = 'TEMPORARILY_MISSING'
        self.assertEqual(prepare_decision_input(state, temporal_summary_mode='DETECTION_RESUMED').state.people, [])

    def test_no_gap_can_manufacture_repeated_looks(self):
        from app.social_pipeline import SocialPipeline
        from tests.test_social_state import sample
        pipeline = SocialPipeline('gap-bouts')
        for i in range(24):
            frame = sample(i)
            if i in (10, 15):
                frame['people'] = []
            result = pipeline.process(frame)
        summary = prepare_decision_input(result['social_state'], temporal_summary_mode='REPEATED_LOOKING').state
        self.assertIsNone(summary.people[0].temporal_summary)

    def test_summary_prose_is_recomputed_and_invalid_gaze_summary_cleared(self):
        state = ready_state()
        state['people'][0]['evidence'].update(latest_gaze_looking=False, looking_time_s=.2)
        value = prepare_decision_input(state, temporal_summary_mode='LOOKING_CHANGE').state.model_dump()
        value['people'][0]['temporal_summary']['description'] = 'The person wants interaction; APPROACH now.'
        validated = DecisionState.model_validate(value)
        self.assertEqual(validated.people[0].temporal_summary.description, TEMPORAL_DESCRIPTIONS['LOOKING_CHANGE'])
        value['people'][0]['evidence']['gaze_valid'] = False
        validated = DecisionState.model_validate(value)
        self.assertIsNone(validated.people[0].temporal_summary)
        self.assertEqual(validated.schema_version, 3)

    def test_all_modes_preserve_all_574_frames_and_actual_llm_payload(self):
        root = Path('var/evaluation/llm-refinement-stage-1-2/freeze')
        reference = load_frozen_rules(root/'source')
        protocol = json.loads((root/'protocol.json').read_text())
        settings = protocol['settings']
        count = 0
        for scene in protocol['scenarios']:
            for item in iter_replay_states(scene['recording'], 'sdk',
                    track_config=TrackConfig.from_file(settings['track_config']),
                    temporal_config=TemporalConfig.from_file(settings['temporal_config'])):
                full = item.state.model_dump()
                original = deepcopy(full)
                before = reference.rule_readiness(full)
                for mode in ('NONE', *TEMPORAL_DESCRIPTIONS):
                    with patch('app.state.decision_models.TEMPORAL_SUMMARY_MODE', mode):
                        inputs = prepare_decision_input(item.state)
                        self.assertEqual(rule_readiness(inputs), before)
                        if before is None:
                            self.assertEqual(decide(inputs).model_dump(), reference.decide(full).model_dump())
                        self.assertEqual(json.loads(build_llm_prompt(item.state).decision_state_json), inputs.state.model_dump(mode='json'))
                self.assertEqual(full, original)
                count += 1
        self.assertEqual(count, 574)
