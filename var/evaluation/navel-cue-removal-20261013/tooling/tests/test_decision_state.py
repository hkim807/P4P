"""Compare compact rules with the actual frozen v7 policy, not a rewritten oracle."""
from copy import deepcopy
import itertools
import json
from pathlib import Path
import unittest

from app.policy.llm import build_llm_prompt
from app.policy.rules import decide, rule_readiness
from app.replay.llm_inputs import iter_replay_states
from app.state.decision_models import prepare_decision_input, rule_parameters
from app.state.social_models import SocialState, TemporalConfig
from app.state.tracks import TrackConfig
from app.replay.legacy import remove_legacy_unknown_cues
from scripts.frozen_rule_reference import load_frozen_rules


BASELINE = Path('var/evaluation/llm-refinement-stage-1-2/freeze')


def frozen_rules():
    return load_frozen_rules(BASELINE/'source')


class DecisionStateTests(unittest.TestCase):
    def assert_equivalent(self, value, **flags):
        old = frozen_rules()
        reason = old.rule_readiness(value, **flags)
        compact = prepare_decision_input(value)
        self.assertEqual(rule_readiness(value, **flags), reason)
        self.assertEqual(rule_readiness(compact, **flags), reason)
        if reason:
            with self.assertRaises(ValueError):
                decide(compact, **flags)
        else:
            expected = old.decide(value, **flags).model_dump()
            self.assertEqual(decide(value, **flags).model_dump(), expected)
            self.assertEqual(decide(compact, **flags).model_dump(), expected)

    def state(self):
        return remove_legacy_unknown_cues(json.loads((BASELINE/'snapshots/S4/first.state.json').read_text()))

    def test_all_574_recorded_frames_preserve_readiness_actions_reasons_targets_and_ids(self):
        protocol = json.loads((BASELINE/'protocol.json').read_text())
        settings = protocol['settings']
        count = eligible = 0
        for scene in protocol['scenarios']:
            for item in iter_replay_states(scene['recording'], 'sdk',
                    track_config=TrackConfig.from_file(settings['track_config']),
                    temporal_config=TemporalConfig.from_file(settings['temporal_config'])):
                self.assert_equivalent(item.state.model_dump())
                count += 1
                eligible += item.state.observation_readiness == 'READY'
        self.assertEqual((count, eligible), (574, 402))

    def test_policy_precedence_and_cue_combinations_against_frozen_code(self):
        template = self.state()
        for gaze, zone, trend, corridor, face, looking in itertools.product(
                ('NONE','INTERMITTENT','SUSTAINED'),
                ('TOO_CLOSE','INTERACTION_RANGE','APPROACHABLE','FAR'),
                ('DECREASING','STABLE','INCREASING'),
                ('IN_WAY','TO_SIDE','BEHIND','UNKNOWN'),
                (True,False,None), (True,False,None)):
            value = deepcopy(template)
            p = value['people'][0]
            p.update(gaze_state=gaze, distance_zone=zone, relative_distance_trend=trend,
                     path_position=corridor, face_detected=face)
            p['evidence']['latest_gaze_looking'] = looking
            self.assert_equivalent(value)

    def test_numeric_thresholds_nulls_and_fallbacks_are_not_rounded_or_replaced(self):
        template = self.state()
        for distance, score, threshold, limit in itertools.product(
                (3.499999999,3.5,3.500000001,None,-1.0),
                (.899999999,.9,.900000001,None), (None,.9), (None,3.5)):
            value = deepcopy(template)
            p = value['people'][0]
            p['latest_distance_m'] = distance
            p['evidence'].update(latest_gaze_overlap=score, looking_exit_threshold=threshold)
            value['config']['yield_closing_max_distance_m'] = limit
            self.assert_equivalent(value)
        for bouts, time, fraction, run in itertools.product(
                (1,2,3), (.999999999,1.,1.000000001), (.449999999,.45,.450000001), (.399999999,.4,.400000001)):
            value = deepcopy(template)
            value['config'].update(allow_recurring_attention=True, min_sustained_run_s=.4)
            p = value['people'][0]
            p.update(gaze_state='INTERMITTENT', distance_zone='APPROACHABLE')
            p['evidence'].update(latest_gaze_looking=True, looking_bouts=bouts,
                                 looking_time_s=time, gaze_fraction=fraction, sustained_gaze_s=run)
            self.assert_equivalent(value)
            p['gaze_state'] = 'SUSTAINED'
            self.assert_equivalent(value)

    def test_readiness_exceptions_missing_multiple_empty_and_failure_flags(self):
        template = self.state()
        for validity, gaze_valid in itertools.product((True,False), (True,False)):
            value = deepcopy(template)
            p = value['people'][0]
            p['gaze_state'] = 'SUSTAINED' if gaze_valid else 'UNKNOWN'
            p['evidence'].update(gaze_valid=gaze_valid, latest_distance_valid=validity)
            self.assert_equivalent(value)
            self.assert_equivalent(value, stale=True)
            self.assert_equivalent(value, stale=True, processing_failed=True)
        for count in (0,1,2):
            value = deepcopy(template)
            value['people'] = [deepcopy(template['people'][0]) for _ in range(count)]
            for i,p in enumerate(value['people']): p['uid'] += i
            self.assert_equivalent(value)
            for p in value['people']: p['visibility'] = 'TEMPORARILY_MISSING'
            self.assert_equivalent(value)
        for duration, count, enabled in itertools.product((.999999999,1.,1.000000001),(4,5,6),(None,1.)):
            value = deepcopy(template)
            value.update(people=[], no_person_observed_s=duration, no_person_observed_samples=count)
            value['config']['no_person_continue_after_s'] = enabled
            self.assert_equivalent(value)

    def test_model_input_omits_diagnostics_settings_identities_and_is_detached(self):
        state = SocialState.model_validate(self.state())
        prompt = build_llm_prompt(state)
        compact = json.loads(prompt.decision_state_json)
        self.assertNotIn('config', compact)
        self.assertNotIn('state_id', compact)
        p = compact['people'][0]
        for name in ('uid','track_epoch','relative_nose_position','relative_head_position',
                     'nose_forward_m','nose_lateral_m','validity_flags','track_age_s'):
            self.assertNotIn(name,p)
        for name in ('distance_slope_mps','distance_fit_residual_m','mean_gaze_overlap','window_span_s'):
            self.assertNotIn(name,p['evidence'])
        self.assertEqual(compact,prepare_decision_input(state).state.model_dump(mode='json'))
        original = prompt.messages
        state.people[0].evidence.latest_gaze_overlap = .99
        state.people[0].observation_labels.current_gaze = 'LOOKING_AT_ROBOT'
        self.assertEqual(prompt.messages, original)
        self.assertLess(len(prompt.decision_state_json), len(prompt.social_state_json)*.5)

    def test_policy_parameters_are_cached_and_not_in_shared_evidence(self):
        config = TemporalConfig.from_file('config/temporal-final-scenarios.json')
        self.assertIs(rule_parameters(config), rule_parameters(TemporalConfig.model_validate_json(config.model_dump_json())))
        compact = prepare_decision_input(self.state())
        self.assertNotIn('allow_recurring_attention',compact.state.canonical_json())
        self.assertEqual(compact.parameters.yield_closing_max_distance_m,3.5)
