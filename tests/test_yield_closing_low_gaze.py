"""Exercise measured closing/face/gaze evidence through the production pipeline."""
from pathlib import Path
import unittest

from app.policy.rules import decide
from app.social_pipeline import SocialPipeline
from app.state.social_models import TemporalConfig
from robot.navel_client.decision_dispatch import parse_decision
from robot.navel_client.single_trial import SingleTrial
from tests.test_social_state import sample


CONFIG = TemporalConfig.from_file(Path('config/temporal-development-frozen.json'))


def closing_observation(i, *, start=2.5, step=-.02, gaze=.1, face=True):
    observation = sample(i, distance=start + step*i, gaze=gaze, velocity=.1)
    observation['people'][0]['face_detected'] = face
    return observation


def sequence(**kwargs):
    pipeline = SocialPipeline('yield-test', temporal_config=CONFIG)
    rows = [pipeline.process(closing_observation(i, **kwargs)) for i in range(24)]
    return pipeline, rows


class ClosingYieldTests(unittest.TestCase):
    def test_closing_low_gaze_resolves_after_warmup_and_robot_accepts_v4(self):
        _, rows = sequence()
        first = next(row for row in rows if row['final_decision'])
        self.assertEqual(first['final_decision']['action'], 'YIELD')
        self.assertEqual(first['social_state']['robot_timestamp_us'], 1_100_000)
        self.assertTrue(all(row['final_decision'] is None for row in rows[:11]))
        person = rows[-1]['social_state']['people'][0]
        self.assertEqual(person['relative_distance_trend'], 'DECREASING')
        self.assertEqual(person['human_radial_motion'], 'UNKNOWN')
        self.assertTrue(person['face_detected'])
        self.assertEqual(person['evidence']['latest_gaze_overlap'], .1)
        self.assertEqual(rows[-1]['policy_decision']['reason_code'], 'CLOSING_DISTANCE_WITH_LOW_GAZE')
        observation = closing_observation(23)
        payload = {**rows[-1], 'accepted': True, 'processing_status': 'complete',
                   'timestamp': observation['timestamp']}
        parsed = parse_decision(payload, observation, observation['timestamp'], 1_000_000)
        self.assertEqual(parsed.decision, 'YIELD')
        trial = SingleTrial(monotonic_us=lambda: observation['timestamp'])
        self.assertTrue(trial.accept_rule_response(payload, observation))
        self.assertEqual(trial.decision['action'], 'YIELD')

    def test_cue_combinations_do_not_replace_missing_evidence(self):
        for inputs, expected in (({'face': None}, 'CONTINUE'), ({'face': False}, 'CONTINUE'),
                                 ({'gaze': .95}, 'APPROACH'), ({'step': 0}, 'CONTINUE'),
                                 ({'step': .02}, 'CONTINUE'), ({'gaze': None}, None)):
            with self.subTest(inputs=inputs):
                _, rows = sequence(**inputs)
                final = rows[-1]['final_decision']
                self.assertEqual(final['action'] if final else None, expected)

    def test_three_metre_cutoff_uses_current_distance_without_zone_hysteresis(self):
        _, rows = sequence(start=3.46)
        state = rows[-1]['social_state']
        self.assertEqual(state['people'][0]['latest_distance_m'], 3.0)
        self.assertEqual(state['people'][0]['distance_zone'], 'FAR')
        self.assertEqual(rows[-2]['final_decision']['action'], 'CONTINUE')
        self.assertEqual(rows[-1]['final_decision']['action'], 'YIELD')
        state['config']['yield_closing_max_distance_m'] = 2.9
        self.assertEqual(decide(state).decision, 'CONTINUE')

    def test_proximity_alone_no_longer_yields_or_bypasses_gaze_readiness(self):
        for gaze, expected in ((.1, 'CONTINUE'), (.95, 'ENGAGE'), (None, None)):
            with self.subTest(gaze=gaze):
                _, rows = sequence(start=.4, step=0, gaze=gaze)
                final = rows[-1]['final_decision']
                self.assertEqual(final['action'] if final else None, expected)

    def test_missing_face_gaze_distance_and_detection_are_current_only(self):
        for change in ('face', 'gaze', 'distance', 'detection', 'multiple'):
            with self.subTest(change=change):
                pipeline, _ = sequence()
                observation = closing_observation(24)
                if change == 'face':
                    del observation['people'][0]['face_detected']
                elif change == 'gaze':
                    observation['people'][0]['gaze_overlap'] = None
                elif change == 'distance':
                    observation['people'][0]['distance_m'] = None
                elif change == 'detection':
                    observation['people'] = []
                else:
                    observation['people'].append(dict(observation['people'][0], uid=18))
                row = pipeline.process(observation)
                self.assertNotEqual(row['final_decision']['action'] if row['final_decision'] else None, 'YIELD')
                if change in ('face', 'detection'):
                    self.assertIsNone(row['social_state']['people'][0]['face_detected'])
                if change in ('gaze', 'detection'):
                    self.assertIsNone(row['social_state']['people'][0]['evidence']['latest_gaze_overlap'])

    def test_implausible_jump_breaks_closing_trend(self):
        pipeline, _ = sequence()
        observation = closing_observation(24)
        observation['people'][0]['distance_m'] = .4
        row = pipeline.process(observation)
        self.assertFalse(row['social_state']['people'][0]['evidence']['distance_trend_valid'])
        self.assertIsNone(row['final_decision'])

    def test_current_low_gaze_required_even_when_history_holds_none(self):
        for gaze in (.75, .95):
            with self.subTest(gaze=gaze):
                pipeline, _ = sequence()
                state = pipeline.process(closing_observation(24, gaze=gaze))['social_state']
                self.assertEqual(state['people'][0]['gaze_state'], 'NONE')
                self.assertEqual(decide(state).decision, 'CONTINUE')

    def test_pass_overrides_closing_trigger_and_conflict_overrides_pass(self):
        _, rows = sequence()
        state = rows[-1]['social_state']
        state['people'][0]['pass_gesture'] = 'PASS'
        self.assertEqual(decide(state).decision, 'CONTINUE')
        state['people'][0].update(path_relation='CONFLICT', gaze_state='UNKNOWN')
        state['people'][0]['evidence']['gaze_valid'] = False
        self.assertEqual(decide(state).reason_code, 'HUMAN_PATH_CONFLICT')


if __name__ == '__main__':
    unittest.main()
