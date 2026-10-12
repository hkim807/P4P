"""Fixed-camera geometry, cue lifetime, and front/side policy separation."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from app.policy.rules import decide
from app.replay.llm_inputs import iter_replay_states
from app.social_pipeline import SocialPipeline
from app.state.path_position import classify_path_position
from app.state.social_models import TemporalConfig
from robot.navel_client.adapter import NavelObservationAdapter
from tests.fixtures import CoordSystem, perception, person
from tests.test_yield_closing_low_gaze import CONFIG, closing_observation


def nose(x=2.0, y=0.0):
    return {'coordinate_frame': 'CAM_HEAD', 'x': x, 'y': y, 'z': .1}


class PathPositionTests(unittest.TestCase):
    def test_adapter_preserves_camera_nose_separately_from_head(self):
        p = person(g_nose=[NS(sys=CoordSystem.HEAD_STRAIGHT, x=2., y=4., z=.1),
                           NS(sys=CoordSystem.CAM_HEAD, x=2., y=.6, z=.1)])
        converted = NavelObservationAdapter().convert(perception(p))['people'][0]
        self.assertEqual(converted['optional_relative_nose_position'], nose(y=.6))
        self.assertEqual(converted['optional_relative_head_position']['y'], .3)

    def test_missing_invalid_or_ambiguous_nose_never_uses_head_position(self):
        good = NS(sys=CoordSystem.CAM_HEAD, x=2., y=0., z=.1)
        for points in (None, [], [NS(sys=CoordSystem.HEAD_STRAIGHT, x=2., y=0., z=.1)],
                       [NS(sys=CoordSystem.CAM_HEAD, x=float('nan'), y=0., z=.1)],
                       [NS(sys=CoordSystem.CAM_HEAD, x=0., y=0., z=0.)], [good, good]):
            with self.subTest(points=points):
                converted = NavelObservationAdapter().convert(perception(person(g_nose=points)))['people'][0]
                self.assertIn('optional_relative_head_position', converted)
                self.assertNotIn('optional_relative_nose_position', converted)

    def test_corridor_bounds_both_sides_behind_and_unknown(self):
        for x, y, expected in ((2., .5, 'IN_WAY'), (2., -.5, 'IN_WAY'),
                               (2., .501, 'TO_SIDE'), (2., -.501, 'TO_SIDE'),
                               (0., 0., 'BEHIND'), (-1., 0., 'BEHIND')):
            with self.subTest(x=x, y=y):
                self.assertEqual(classify_path_position(nose(x, y), CONFIG)[0], expected)
        for point in (None, dict(nose(), coordinate_frame='HEAD_STRAIGHT'),
                      dict(nose(), x=float('inf')), dict(nose(), x=0., y=0., z=0.)):
            self.assertEqual(classify_path_position(point, CONFIG), ('UNKNOWN', None, None))

    def test_configurable_camera_transform_width_and_disabled_assumption(self):
        config = TemporalConfig(path_camera_yaw_deg=90., path_camera_x_m=1., path_camera_y_m=-2.)
        category, forward, lateral = classify_path_position(nose(2., 0.), config)
        self.assertEqual(category, 'IN_WAY')
        self.assertAlmostEqual(forward, 1.)
        self.assertAlmostEqual(lateral, 0.)
        self.assertEqual(classify_path_position(nose(y=.6), TemporalConfig(path_half_width_m=.7))[0], 'IN_WAY')
        self.assertEqual(classify_path_position(nose(), TemporalConfig(path_assume_fixed_head=False)), ('UNKNOWN', None, None))
        for kwargs in ({'path_half_width_m': 0.}, {'path_hysteresis_m': .5},
                       {'path_camera_yaw_deg': float('nan')}, {'path_camera_x_m': float('inf')}):
            with self.assertRaises(ValueError):
                TemporalConfig(**kwargs)

    def test_hysteresis_and_reset_on_missing_geometry_detection_or_gap(self):
        for interruption in ('geometry', 'detection', 'gap'):
            with self.subTest(interruption=interruption):
                pipeline = SocialPipeline('path', temporal_config=CONFIG)
                def update(i, y):
                    f = closing_observation(i)
                    f['people'][0]['optional_relative_nose_position']['y'] = y
                    return pipeline.process(f)['social_state']
                self.assertEqual(update(0, .45)['people'][0]['path_position'], 'IN_WAY')
                self.assertEqual(update(1, .55)['people'][0]['path_position'], 'IN_WAY')
                self.assertEqual(update(2, .61)['people'][0]['path_position'], 'TO_SIDE')
                self.assertEqual(update(3, .45)['people'][0]['path_position'], 'TO_SIDE')
                self.assertEqual(update(4, .39)['people'][0]['path_position'], 'IN_WAY')
                if interruption != 'gap':
                    f = closing_observation(5)
                    if interruption == 'geometry':
                        del f['people'][0]['optional_relative_nose_position']
                    else:
                        f['people'] = []
                    state = pipeline.process(f)['social_state']
                    p = state['people'][0]
                    self.assertEqual(p['path_position'], 'UNKNOWN')
                    self.assertIsNone(p['relative_nose_position'])
                    self.assertIsNone(p['nose_lateral_m'])
                # .55 would stay IN_WAY if pre-gap memory leaked through.
                state = update(8 if interruption == 'gap' else 6, .55)
                self.assertEqual(state['people'][0]['path_position'], 'TO_SIDE')
                self.assertTrue(any(c['field'] == 'path_position' for c in state['cue_changes']))

    def test_same_closing_low_gaze_front_yields_side_continues(self):
        for point, expected in ((nose(y=.3), 'YIELD'), (nose(y=.65), 'CONTINUE'),
                                (nose(x=-1.), 'CONTINUE'), (None, 'CONTINUE')):
            with self.subTest(point=point):
                pipeline = SocialPipeline('path', temporal_config=CONFIG)
                for i in range(24):
                    f = closing_observation(i)
                    f['people'][0]['optional_relative_nose_position'] = point
                    row = pipeline.process(f)
                self.assertEqual(row['final_decision']['action'], expected)

    def test_position_alone_does_not_yield_or_override_attention(self):
        for y in (.3, .65):
            for gaze, expected in ((.95, 'APPROACH'), (None, None)):
                pipeline = SocialPipeline('path', temporal_config=CONFIG)
                for i in range(24):
                    f = closing_observation(i, gaze=gaze)
                    f['people'][0]['optional_relative_nose_position']['y'] = y
                    row = pipeline.process(f)
                self.assertEqual(row['final_decision']['action'] if row['final_decision'] else None, expected)

    def test_sdk_replay_carries_nose_geometry_and_matches_live_pipeline(self):
        records = []
        for i in range(24):
            t = i * 100_000
            for stream, packet in (
                ('locomotion', {'odometry': {'velocity': {'linear_x': .1, 'angular_z': 0.}}}),
                ('perception', {'persons': [{'uid': 17, 'dist_mm': 2500.-20*i, 'gaze_overlap': .1,
                    'face': {'x1': 1, 'y1': 1, 'x2': 2, 'y2': 2},
                    'g_nose': [{'sys': 'CAM_HEAD', 'x': 2.5-.02*i, 'y': .3, 'z': .1}]}]})):
                records.append(dict(capture_version=1, session_id='nose-test', stream=stream,
                                    sequence=i+1, received_monotonic_us=t, received_unix_us=t, packet=packet))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sdk.jsonl'
            path.write_text(''.join(json.dumps(r)+'\n' for r in records))
            items = list(iter_replay_states(path, 'sdk', temporal_config=CONFIG))
        pipeline = SocialPipeline(items[0].state.session_id, temporal_config=CONFIG)
        for item in items:
            row = pipeline.process(item.source['reconstructed_raw_observation'])
            self.assertEqual(row['social_state'], item.state.model_dump(mode='json'))
        self.assertEqual(items[-1].state.people[0].path_position, 'IN_WAY')
        self.assertEqual(decide(items[-1].state).decision, 'YIELD')
