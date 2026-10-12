"""Tuning regressions plus independent gap, identity and acceptance boundaries."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app.policy.rules import decide
from app.social_pipeline import SocialPipeline
from app.state.social_models import TemporalConfig
from app.state.tracks import TrackConfig, TrackManager
from robot.navel_client.single_trial import SingleTrial
from scripts.replay_first_action import replay
from tests.test_social_state import sample


TEMPORAL = TemporalConfig.from_file('config/temporal-final-scenarios.json')
TRACKING = TrackConfig.from_file('config/person-tracking-final-scenarios.json')


def nose_sample(i, uid=17, x=2.0, y=.2, gaze=.95):
    raw = sample(i, gaze=gaze, uid=uid)
    raw['people'][0]['optional_relative_nose_position'] = {
        'coordinate_frame': 'CAM_HEAD', 'x': x, 'y': y, 'z': .1}
    return raw


def empty_sample(i):
    raw = sample(i)
    raw['people'] = []
    return raw


def payload(result, raw):
    return {**result, 'accepted': True, 'processing_status': 'complete',
            'timestamp': raw['timestamp']}


class FinalScenarioTuningTests(unittest.TestCase):
    def pipeline(self):
        return SocialPipeline('tuning-test', TRACKING, TEMPORAL)

    def test_all_trimmed_recordings_first_action_and_live_acceptance(self):
        settings = json.loads(Path('config/policy-comparison-final.json').read_text())
        scenes = json.loads(Path(settings['evaluation_contract']).read_text())['scenarios']
        expected = ['CONTINUE', 'APPROACH', 'ENGAGE', 'YIELD', 'CONTINUE',
                    'CONTINUE', 'CONTINUE', 'CONTINUE', 'APPROACH']
        with tempfile.TemporaryDirectory() as directory:
            for scene, action in zip(scenes, expected):
                with self.subTest(scenario=scene['scenario_id']):
                    clock = [0.0]
                    def sleep(seconds):
                        clock[0] += seconds
                    result = replay(scene, 'rules', Path(directory) / scene['scenario_id'],
                                    settings, now=lambda: clock[0], sleep=sleep)
                    self.assertEqual(result['action'], action)
                    self.assertTrue(result['accepted_by_live_client'])
                    self.assertLess(result['decision_source_offset_s'], 2.2)
                    self.assertEqual(result['source_sha256'], hashlib.sha256(
                        Path(scene['recording']).read_bytes()).hexdigest())

    def test_nose_reassociation_preserves_contiguous_evidence_and_marks_uncertainty(self):
        pipeline = self.pipeline()
        for i in range(16):
            result = pipeline.process(nose_sample(i, uid=17 if i < 8 else 0))
        track = result['tracks'][0]
        person = result['social_state']['people'][0]
        self.assertEqual((track['uid'], track['track_epoch'], track['observation_count']), (0, 1, 16))
        self.assertIn('NOSE_REASSOCIATED_UNVERIFIED', person['validity_flags'])
        self.assertEqual(person['gaze_state'], 'SUSTAINED')
        self.assertAlmostEqual(person['evidence']['gaze_valid_coverage_s'], 1.5)

    def test_reassociation_cannot_bridge_missing_gap_ambiguity_or_displacement(self):
        for boundary in ('missing', 'long_gap', 'multiple', 'no_nose', 'far', 'zero'):
            with self.subTest(boundary=boundary):
                tracker = TrackManager('t', TRACKING)
                tracker.update(nose_sample(0))
                if boundary == 'missing':
                    tracker.update(empty_sample(1))
                raw = nose_sample(3 if boundary == 'long_gap' else 2, uid=18)
                if boundary == 'multiple':
                    raw['people'].append(nose_sample(2, uid=19)['people'][0])
                elif boundary == 'no_nose':
                    raw['people'][0].pop('optional_relative_nose_position')
                elif boundary == 'far':
                    raw['people'][0]['optional_relative_nose_position']['x'] = 4.0
                elif boundary == 'zero':
                    raw['people'][0]['optional_relative_nose_position'].update(x=0., y=0., z=0.)
                result = tracker.update(raw)
                self.assertNotIn('REASSOCIATED', [e['type'] for e in result['events']])
                new = next(t for t in result['tracks'] if t['uid'] == 18)
                self.assertEqual(new['observation_count'], 1)

    def test_half_gaze_is_low_attention_and_raw_value_is_retained(self):
        pipeline = self.pipeline()
        for i in range(16):
            result = pipeline.process(sample(i, gaze=.5))
        person = result['social_state']['people'][0]
        self.assertEqual(result['tracks'][0]['samples'][-1]['gaze_overlap'], .5)
        self.assertEqual(person['evidence']['latest_gaze_overlap'], .5)
        self.assertTrue(person['evidence']['gaze_valid'])
        self.assertAlmostEqual(person['evidence']['gaze_valid_coverage_s'], 1.5)
        self.assertEqual(person['gaze_state'], 'NONE')
        self.assertEqual(result['final_decision']['action'], 'CONTINUE')

    def test_same_gaze_score_has_distance_appropriate_threshold(self):
        for distance, category, threshold in ((2.5, 'SUSTAINED', .93), (3., 'NONE', .97)):
            with self.subTest(distance=distance):
                pipeline = self.pipeline()
                for i in range(16):
                    result = pipeline.process(sample(i, gaze=.94, distance=distance))
                person = result['social_state']['people'][0]
                self.assertEqual(person['gaze_state'], category)
                self.assertEqual(person['evidence']['looking_enter_threshold'], threshold)

    def test_two_metre_boundary_and_current_attention(self):
        for distance, action in ((2., 'ENGAGE'), (2.001, 'APPROACH')):
            with self.subTest(distance=distance):
                pipeline = self.pipeline()
                for i in range(16):
                    result = pipeline.process(sample(i, distance=distance))
                self.assertEqual(result['final_decision']['action'], action)
                result = pipeline.process(sample(16, gaze=.8, distance=distance))
                self.assertNotEqual(result['social_state']['people'][0]['gaze_state'], 'SUSTAINED')
                self.assertEqual(result['final_decision']['action'], 'CONTINUE')

    def test_recurring_attention_requires_explicit_opt_in(self):
        pipeline = self.pipeline()
        for i in range(16):
            result = pipeline.process(sample(i))
        state = result['social_state']
        person = state['people'][0]
        person['gaze_state'] = 'INTERMITTENT'
        person['evidence'].update(looking_bouts=3, looking_time_s=1.2, gaze_fraction=.6)
        self.assertEqual(decide(state).decision, 'CONTINUE')
        state['config']['allow_recurring_attention'] = True
        self.assertEqual(decide(state).decision, 'ENGAGE')

    def test_empty_interval_requires_continuity_and_resets_on_person_and_session(self):
        pipeline = self.pipeline()
        for i in range(11):
            result = pipeline.process(empty_sample(i))
            self.assertEqual(result['final_decision'] is not None, i == 10)
        self.assertEqual(result['final_decision']['action'], 'CONTINUE')
        result = pipeline.process(empty_sample(14))
        self.assertIsNone(result['final_decision'])
        self.assertEqual(result['social_state']['no_person_observed_samples'], 1)
        pipeline.process(sample(15))
        result = pipeline.process(empty_sample(16))
        self.assertEqual(result['social_state']['no_person_observed_s'], 0)
        pipeline.tracking.tracker.reset('new-session')
        state = pipeline.estimator.update(pipeline.tracking.process(empty_sample(0)))
        self.assertEqual(state.no_person_observed_samples, 1)

    def test_empty_live_readiness_only_accepts_fresh_continue(self):
        pipeline = self.pipeline()
        for i in range(11):
            raw = empty_sample(i)
            result = pipeline.process(raw)
        response = payload(result, raw)
        for policy in ('rules', 'llm', 'vlm'):
            for action in ('CONTINUE', 'APPROACH', 'YIELD', 'ENGAGE'):
                with self.subTest(policy=policy, action=action):
                    now = [raw['timestamp']]
                    trial = SingleTrial(policy, monotonic_us=lambda: now[0])
                    trial.observe(response, raw)
                    source = {**trial.model_identity(), 'request_id': 'r',
                              'source_state_id': result['social_state']['state_id'],
                              'source_robot_timestamp_us': raw['timestamp']}
                    if policy != 'rules':
                        trial.register_request(source)
                    self.assertEqual(trial.accept_decision(policy, {'action': action, 'reason': 'test'}, source),
                                     policy != 'vlm' and action == 'CONTINUE')
        for changed in ('stale', 'person'):
            with self.subTest(changed=changed):
                now = [raw['timestamp']]
                trial = SingleTrial('llm', monotonic_us=lambda: now[0])
                trial.observe(response, raw)
                source = {**trial.model_identity(), 'request_id': 'r',
                          'source_state_id': result['social_state']['state_id'],
                          'source_robot_timestamp_us': raw['timestamp']}
                trial.register_request(source)
                if changed == 'stale':
                    now[0] += 1_000_001
                else:
                    trial.note_observation(sample(11))
                self.assertFalse(trial.accept_decision('llm', {'action': 'CONTINUE', 'reason': 'test'}, source))


if __name__ == '__main__':
    unittest.main()
