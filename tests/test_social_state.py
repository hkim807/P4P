"""Known temporal stimuli, missing-data boundaries, and state isolation."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from app.pipeline import TrackingPipeline
from app.social_pipeline import SocialPipeline
from app.social_scenarios import controlled_scenarios
from app.state.estimator import SocialStateEstimator
from app.state.social_models import MotionContext, TemporalConfig, SocialState
from app.state.tracks import TrackConfig
from app.recording import TimestampOrderError
from tests.fixtures import frame


def sample(i, gaze=0.95, distance=2.0, uid=17, velocity=0.0):
    f = frame()
    f["timestamp"] = round(i * 100_000)
    f["people"] = [{"uid": uid, "gaze_overlap": gaze, "distance_m": distance}]
    f["robot"] = {"linear_velocity": velocity, "angular_velocity": 0.0}
    return f


def confirmed(start=0, end=100_000_000):
    return MotionContext.model_validate({"source": "test fixture", "stationary_intervals": [{"start_us": start, "end_us": end}]})


def person(pipeline, f):
    return pipeline.process(f)["social_state"]["people"][0]


class SocialTests(unittest.TestCase):
    def test_warmup_then_sustained_then_null_immediately_unknown(self):
        pipeline = SocialPipeline("a")
        self.assertEqual(person(pipeline, sample(0))["gaze_state"], "UNKNOWN")
        for i in range(1, 21):
            p = person(pipeline, sample(i))
        self.assertEqual(p["gaze_state"], "SUSTAINED")
        p = person(pipeline, sample(21, gaze=None))
        self.assertEqual(p["gaze_state"], "UNKNOWN")
        self.assertIn("GAZE_UNAVAILABLE", p["validity_flags"])

    def test_known_changing_patterns_from_recorded_template(self):
        frames, expectations, context = controlled_scenarios(frame())
        pipeline = SocialPipeline("stimulus", context=context)
        for i, f in enumerate(frames):
            p = person(pipeline, f)
            for field, expected in expectations.get(i, {}).items():
                self.assertEqual(p[field], expected, (i, field, p))

    def test_all_missing_cues_do_not_mean_no_attention_or_stationary(self):
        pipeline = SocialPipeline("a", context=confirmed())
        for i in range(30):
            p = person(pipeline, sample(i, None, None))
        self.assertEqual(p["gaze_state"], "UNKNOWN")
        self.assertEqual(p["distance_zone"], "UNKNOWN")
        self.assertEqual(p["relative_distance_trend"], "UNKNOWN")
        self.assertEqual(p["human_radial_motion"], "UNKNOWN")
        self.assertIsNone(p["evidence"]["gaze_fraction"])
        self.assertEqual(p["evidence"]["gaze_valid_coverage_s"], 0)

    def test_gaze_fraction_is_weighted_by_time_not_sample_count(self):
        pipeline = SocialPipeline("a")
        for t, gaze in [(0, .95), (1, .1), (3, .1), (3.5, .1)]:
            p = person(pipeline, sample(t, gaze))
        self.assertAlmostEqual(p["evidence"]["gaze_fraction"], .1/.35)
        self.assertAlmostEqual(p["evidence"]["gaze_valid_coverage_s"], .35)

    def test_sample_hysteresis_survives_window_eviction_and_resets_after_gap(self):
        pipeline = SocialPipeline("a")
        person(pipeline, sample(0, .95))
        for i in range(1, 45):
            p = person(pipeline, sample(i, .75))
        self.assertEqual(p["gaze_state"], "SUSTAINED")
        f = sample(45); f["people"] = []
        pipeline.process(f)
        p = person(pipeline, sample(46, .75))
        self.assertEqual(p["gaze_state"], "UNKNOWN")

    def test_null_and_absence_do_not_add_gaze_support(self):
        pipeline = SocialPipeline("a")
        person(pipeline, sample(0))
        person(pipeline, sample(1, None))
        p = person(pipeline, sample(2))
        self.assertEqual(p["evidence"]["gaze_valid_coverage_s"], 0)
        f = sample(3); f["people"] = []
        p = person(pipeline, f)
        self.assertEqual(p["gaze_state"], "UNKNOWN")
        p = person(pipeline, sample(4))
        self.assertEqual(p["evidence"]["gaze_valid_coverage_s"], 0)
        self.assertEqual(p["evidence"]["distance_valid_samples"], 1)

    def test_long_time_gap_below_tracking_grace_does_not_join_feature_windows(self):
        pipeline = SocialPipeline("a")
        person(pipeline, sample(0))
        p = person(pipeline, sample(6))
        self.assertEqual(p["track_epoch"], 1)
        self.assertEqual(p["evidence"]["gaze_valid_coverage_s"], 0)
        self.assertEqual(p["evidence"]["distance_valid_span_s"], 0)

    def test_category_dwell_does_not_bridge_direct_transport_gap(self):
        pipeline = SocialPipeline("a")
        for i in range(21):
            p = person(pipeline, sample(i))
        self.assertEqual(p["gaze_state"], "SUSTAINED")
        self.assertEqual(person(pipeline, sample(26))["gaze_state"], "UNKNOWN")

    def test_category_requires_source_time_dwell_after_minimum_evidence(self):
        pipeline = SocialPipeline("a")
        for i in range(13):
            p = person(pipeline, sample(i))
        self.assertEqual(p["gaze_state"], "UNKNOWN")
        self.assertEqual(person(pipeline, sample(13))["gaze_state"], "SUSTAINED")

    def test_distance_slope_sign_and_robot_motion_ambiguity(self):
        for rate, trend in [(-.2, "DECREASING"), (0, "STABLE"), (.2, "INCREASING")]:
            pipeline = SocialPipeline("a")
            for i in range(21):
                p = person(pipeline, sample(i, distance=2 + rate*i/10))
            self.assertAlmostEqual(p["evidence"]["distance_slope_mps"], rate)
            self.assertEqual(p["relative_distance_trend"], trend)
            self.assertEqual(p["human_radial_motion"], "UNKNOWN")

    def test_confirmed_stationarity_must_cover_whole_distance_segment(self):
        pipeline = SocialPipeline("a", context=confirmed(1_000_000))
        for i in range(21):
            p = person(pipeline, sample(i, distance=3-i*.02))
        self.assertEqual(p["human_radial_motion"], "UNKNOWN")
        for i in range(21, 32):
            p = person(pipeline, sample(i, distance=3-i*.02))
        self.assertEqual(p["human_radial_motion"], "TOWARD")

    def test_moving_sample_contradicts_stationary_metadata(self):
        pipeline = SocialPipeline("a", context=confirmed())
        for i in range(21):
            state = pipeline.process(sample(i, distance=3-i*.02, velocity=.2 if i == 10 else 0))["social_state"]
        self.assertEqual(state["people"][0]["human_radial_motion"], "UNKNOWN")
        state = pipeline.process(sample(21, velocity=.2))["social_state"]
        self.assertEqual(state["robot"]["motion_state"], "MOVING")
        self.assertIn("STATIONARITY_CONTEXT_CONTRADICTED", state["robot"]["measurement_validity"])

    def test_robust_fit_tolerates_small_outlier_and_large_jump_breaks_segment(self):
        pipeline = SocialPipeline("a")
        for i in range(21):
            p = person(pipeline, sample(i, distance=2-i*.02 + (.15 if i == 10 else 0)))
        self.assertAlmostEqual(p["evidence"]["distance_slope_mps"], -.2)
        p = person(pipeline, sample(21, distance=9))
        self.assertEqual(p["relative_distance_trend"], "UNKNOWN")
        self.assertEqual(p["distance_zone"], "UNKNOWN")
        self.assertIn("DISTANCE_SEGMENT_BROKEN_BY_JUMP", p["validity_flags"])

    def test_unreliable_fit_is_exposed_but_not_classified(self):
        pipeline = SocialPipeline("a", temporal_config=TemporalConfig(max_fit_residual_m=.01))
        for i in range(22):
            p = person(pipeline, sample(i, distance=2 + .2*(i%2)))
        self.assertEqual(p["relative_distance_trend"], "UNKNOWN")
        self.assertIsNotNone(p["evidence"]["distance_slope_mps"])
        self.assertIn("DISTANCE_FIT_UNRELIABLE", p["validity_flags"])

    def test_distance_zone_hysteresis_and_missing_distance(self):
        pipeline = SocialPipeline("a")
        p = person(pipeline, sample(0, distance=1.45))
        self.assertEqual(p["distance_zone"], "INTERACTION_RANGE")
        p = person(pipeline, sample(1, distance=1.55))
        self.assertEqual(p["distance_zone"], "INTERACTION_RANGE")
        p = person(pipeline, sample(2, distance=1.65))
        self.assertEqual(p["distance_zone"], "APPROACHABLE")
        p = person(pipeline, sample(3, distance=None))
        self.assertEqual(p["distance_zone"], "UNKNOWN")

    def test_uid_change_and_new_epoch_do_not_inherit_categories(self):
        pipeline = SocialPipeline("a")
        for i in range(21):
            person(pipeline, sample(i))
        state = pipeline.process(sample(21, uid=18))["social_state"]
        for p in state["people"]:
            self.assertEqual(p["gaze_state"], "UNKNOWN")
        p = person(pipeline, sample(40, uid=17))
        self.assertEqual(p["gaze_state"], "UNKNOWN")
        self.assertNotEqual(p["track_epoch"], 1)

    def test_estimator_ordering_session_reset_and_memory_pruning(self):
        tracks = TrackingPipeline("a")
        estimator = SocialStateEstimator()
        snapshot = tracks.process(sample(0))
        estimator.update(snapshot)
        with self.assertRaises(TimestampOrderError):
            estimator.update(snapshot)
        empty = sample(10); empty["people"] = []
        estimator.update(tracks.process(empty))
        self.assertEqual(len(estimator._people), 0)
        state = estimator.update(TrackingPipeline("b").process(sample(0)))
        self.assertEqual(state.session_id, "b")
        self.assertEqual(state.people[0].gaze_state, "UNKNOWN")

    def test_configuration_and_schema_contract(self):
        for values in ({"looking_exit": .9}, {"min_span_s": 4.0}, {"window_s": float("nan")},
                       {"max_fit_samples": 2}, {"too_close_m": 2.0}, {"bogus": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                TemporalConfig.model_validate(values)
        with self.assertRaises(ValueError):
            SocialPipeline("a", track_config=TrackConfig(history_window_s=1))
        schema = Path(__file__).resolve().parents[1] / "schemas/v1/social-state.schema.json"
        self.assertEqual(json.loads(schema.read_text()), SocialState.model_json_schema())
