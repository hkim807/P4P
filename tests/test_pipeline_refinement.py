"""General cue boundaries, causal selection, missingness and live/replay parity."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from app.inference.ollama import OllamaConfig
from app.policy.llm import classify_llm
from app.policy.rules import decide, rule_readiness
from app.replay.llm_inputs import iter_replay_states
from app.social_pipeline import SocialPipeline
from app.state.social_models import SocialState, TemporalConfig
from evaluation.encounters import aggregate, human_scores, select_prior
from evaluation.run_scenarios import classify_pair, evaluate
from tests.test_llm_policy import FakeClient, successful_result
from tests.test_policy_rules import ready_state
from tests.test_social_state import sample


class RefinedCueTests(unittest.TestCase):
    def test_brief_glance_is_not_interaction(self):
        p = SocialPipeline('glance')
        for i in range(30):
            row = p.process(sample(i, gaze=.95 if 23 <= i <= 25 else .1))
        self.assertEqual(row['policy_decision']['decision'], 'CONTINUE')

    def test_sustained_attention_while_robot_moves(self):
        for distance, action in ((1.0, 'ENGAGE'), (2.0, 'APPROACH')):
            p = SocialPipeline('attention')
            for i in range(24):
                row = p.process(sample(i, distance=distance, velocity=.15))
            self.assertEqual(row['final_decision']['action'], action)
            self.assertEqual(row['social_state']['people'][0]['human_radial_motion'], 'UNKNOWN')

    def test_attention_ends_before_category_dwell(self):
        p = SocialPipeline('change')
        for i in range(24):
            p.process(sample(i))
        row = p.process(sample(24, gaze=.1))
        self.assertEqual(row['social_state']['people'][0]['gaze_state'], 'SUSTAINED')
        self.assertEqual(row['final_decision']['action'], 'CONTINUE')
        row = p.process(sample(25))
        self.assertEqual(row['final_decision']['action'], 'CONTINUE')

    def test_recurring_attention_requires_multiple_bouts_and_duration(self):
        p = SocialPipeline('recurring')
        for i in range(30):
            row = p.process(sample(i, gaze=.95 if i % 10 < 6 else .1))
        row = p.process(sample(30))
        e = row['social_state']['people'][0]['evidence']
        self.assertGreaterEqual(e['looking_bouts'], 2)
        self.assertGreaterEqual(e['looking_time_s'], 1.0)
        self.assertEqual(row['social_state']['people'][0]['gaze_state'], 'INTERMITTENT')
        self.assertEqual(row['final_decision']['action'], 'APPROACH')
        state = deepcopy(row['social_state'])
        for change in ({'looking_bouts': 1}, {'looking_time_s': .99}, {'latest_gaze_looking': False}):
            s = deepcopy(state); s['people'][0]['evidence'].update(change)
            self.assertEqual(decide(s).decision, 'CONTINUE')

    def test_gaps_do_not_invent_attention_bouts(self):
        p = SocialPipeline('gaps')
        for i in range(24):
            observation = sample(i)
            if i in (10, 15):
                observation['people'] = []
            row = p.process(observation)
        self.assertLessEqual(row['social_state']['people'][0]['evidence']['looking_bouts'], 1)

    def test_missing_and_null_observations_remain_not_ready(self):
        state = ready_state()
        for people in ([], [dict(state['people'][0], visibility='TEMPORARILY_MISSING')]):
            s = deepcopy(state); s['people'] = people
            client = FakeClient(successful_result())
            out = classify_llm(SocialState.model_validate(s), client)
            self.assertEqual(out['status'], 'NOT_READY'); self.assertEqual(client.calls, [])
        s = deepcopy(state); s['people'][0]['evidence']['gaze_valid'] = False
        self.assertIsNotNone(rule_readiness(s))

    def test_conflict_proximity_and_pass_precede_gaze(self):
        state = ready_state()
        for updates, expected in (({'path_relation': 'CONFLICT'}, 'YIELD'),
                                  ({'pass_gesture': 'PASS'}, 'CONTINUE'),
                                  ({'distance_zone': 'TOO_CLOSE'}, 'YIELD'),
                                  ({'path_relation': 'CONFLICT', 'pass_gesture': 'PASS'}, 'YIELD')):
            s = deepcopy(state); person = s['people'][0]
            person.update(updates); person['gaze_state'] = 'UNKNOWN'; person['evidence']['gaze_valid'] = False
            self.assertIsNone(rule_readiness(s))
            self.assertEqual(decide(s).decision, expected)

    def test_raw_explicit_cues_roundtrip_and_are_not_retained_when_missing(self):
        p = SocialPipeline('explicit')
        observation = sample(0)
        observation['people'][0].update(path_relation='CONFLICT', pass_gesture='PASS')
        row = p.process(observation)
        self.assertEqual(row['final_decision']['action'], 'YIELD')
        observation = sample(1); observation['people'] = []
        row = p.process(observation)
        self.assertEqual(row['social_state']['people'][0]['path_relation'], 'UNKNOWN')
        self.assertEqual(row['social_state']['people'][0]['pass_gesture'], 'UNKNOWN')
        self.assertIsNone(row['final_decision'])

    def test_retreat_nearby_greeting_differs_from_distant_pursuit(self):
        for distance, expected in ((1.0, 'ENGAGE'), (2.0, 'CONTINUE')):
            state = ready_state(distance)
            state['people'][0].update(human_radial_motion='AWAY', relative_distance_trend='INCREASING')
            self.assertEqual(decide(state).decision, expected)

    def test_distance_transition_uses_hysteresis(self):
        p = SocialPipeline('distance')
        for i in range(24):
            p.process(sample(i, distance=1.4))
        self.assertEqual(p.process(sample(24, distance=1.55))['final_decision']['action'], 'ENGAGE')
        self.assertEqual(p.process(sample(25, distance=1.65))['final_decision']['action'], 'APPROACH')

    def test_recorded_replay_matches_full_production_pipeline(self):
        items = list(iter_replay_states('var/recordings/scenario-03.sdk.jsonl', 'sdk'))
        p = SocialPipeline(items[0].state.session_id)
        for item in items:
            row = p.process(item.source['reconstructed_raw_observation'])
            self.assertEqual(row['social_state'], item.state.model_dump(mode='json'))
            self.assertEqual(row['policy_readiness']['reason_code'], rule_readiness(item.state))


class EvaluationContractTests(unittest.TestCase):
    def test_unmapped_endpoints_are_explicit_and_changed_sources_rejected(self):
        import contextlib
        import io
        import tempfile
        contract = json.loads(Path('config/evaluation-contract.json').read_text())
        contract['scenarios'] = contract['scenarios'][:1]
        reference = json.loads(Path('config/human-reference.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'results'
            with contextlib.redirect_stdout(io.StringIO()):
                summary = evaluate(contract, reference, output)
            self.assertEqual(summary['survey_video_end']['rule']['coverage'], 0)
            rows = [json.loads(line) for line in (output / 'encounters.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 6)
            self.assertTrue(all(row['action'] is None for row in rows))
            self.assertTrue(all(row['human']['mean_appropriateness'] is None for row in rows))
            with self.assertRaises(FileExistsError):
                evaluate(contract, reference, output)
            contract['scenarios'][0]['source_sha256'] = 'changed'
            with self.assertRaises(ValueError):
                evaluate(contract, reference, Path(directory) / 'invalid')
            self.assertFalse((Path(directory) / 'invalid').exists())

    def test_prior_selection_never_uses_future_or_last_ready(self):
        items = list(iter_replay_states('var/recordings/scenario-03.sdk.jsonl', 'sdk'))
        timestamp = items[50].state.robot_timestamp_us
        self.assertIs(select_prior(items, timestamp), items[50])
        self.assertIsNone(select_prior(items, items[0].state.robot_timestamp_us - 1))
        self.assertIs(select_prior(items, items[-1].state.robot_timestamp_us), items[-1])

    def test_identical_bytes_and_errors_not_scored(self):
        items = list(iter_replay_states('var/recordings/scenario-03.sdk.jsonl', 'sdk'))
        item = next(x for x in items if rule_readiness(x.state) is None)
        client = FakeClient(successful_result())
        pair = classify_pair(item, timestamp_us=item.state.robot_timestamp_us, max_age_s=.25, client=client)
        self.assertEqual(pair['rule']['state_sha256'], pair['llm']['state_sha256'])
        self.assertEqual(pair['rule']['social_state'], pair['llm']['social_state'])
        pair = classify_pair(item, timestamp_us=item.state.robot_timestamp_us + 250001, max_age_s=.25, client=client)
        self.assertEqual(pair['rule']['status'], 'NOT_READY')
        self.assertEqual(pair['llm']['status'], 'NOT_READY')
        self.assertEqual(len(client.calls), 1)
        ref = json.loads(Path('config/human-reference.json').read_text())['scenarios']['S3']
        for status in ('NOT_READY', 'ERROR', 'NOT_RUN'):
            scores = human_scores('ENGAGE', ref, status=status)
            self.assertTrue(all(v is None for v in scores.values()))

    def test_preference_and_rating_are_independent_and_coverage_visible(self):
        refs = json.loads(Path('config/human-reference.json').read_text())['scenarios']
        scores = human_scores('ENGAGE', refs['S3'])
        self.assertEqual(scores['preferred_count'], 12)
        self.assertEqual(scores['preference_support'], 12 / 37)
        self.assertFalse(scores['modal_agreement']); self.assertTrue(scores['original_agreement'])
        rows = [{'status': 'DECIDED', 'human': scores}, {'status': 'NOT_READY', 'human': human_scores(None, refs['S3'])}]
        self.assertEqual(aggregate(rows)['coverage'], .5)
        self.assertIsNone(aggregate(rows[1:])['mean_appropriateness'])

    def test_explicit_context_limit_is_transmitted(self):
        c = OllamaConfig(base_url='http://localhost:11434', model='test', num_ctx=8192)
        self.assertEqual(c.generation_options()['num_ctx'], 8192)
        with self.assertRaises(ValueError):
            OllamaConfig(base_url='http://localhost:11434', model='test', num_ctx=0)
