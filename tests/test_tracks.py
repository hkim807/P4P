"""UID lifecycle, event-time boundaries, bounded memory, and validation atomicity."""
from copy import deepcopy
import unittest

from app.domain.models import RawObservationFrame
from app.recording import TimestampOrderError
from app.state.tracks import TrackCapacityError, TrackConfig, TrackManager
from tests.fixtures import frame


def observation(timestamp, *uids):
    payload = frame()
    payload["timestamp"] = timestamp
    payload["people"] = [{"uid": uid, "distance_m": uid + 0.5, "gaze_overlap": 0.8} for uid in uids]
    return payload


def event_types(snapshot):
    return [e["type"] for e in snapshot["events"]]


class TrackTests(unittest.TestCase):
    def test_continuous_history_prunes_by_source_time_not_track_age(self):
        tracker = TrackManager("a", TrackConfig(history_window_s=1, missing_grace_s=2))
        tracker.update(observation(0, 17))
        tracker.update(observation(500_000, 17))
        tracker.update(observation(1_000_000, 17))
        result = tracker.update(observation(1_500_000, 17))
        track = result["tracks"][0]
        self.assertEqual([s["timestamp_us"] for s in track["samples"]], [500_000, 1_000_000, 1_500_000])
        self.assertEqual(track["first_seen_us"], 0)
        self.assertEqual(track["observation_count"], 4)
        self.assertEqual(track["track_age_s"], 1.5)
        self.assertEqual(result["events"], [])

    def test_alternating_uids_never_share_samples(self):
        tracker = TrackManager("a")
        tracker.update(observation(0, 17))
        missing = tracker.update(observation(100_000, 18))
        self.assertEqual(event_types(missing), ["MISSING", "ACQUIRED"])
        result = tracker.update(observation(200_000, 17))
        self.assertEqual(event_types(result), ["MISSING", "REACQUIRED"])
        a, b = result["tracks"]
        self.assertEqual([s["distance_m"] for s in a["samples"]], [17.5, 17.5])
        self.assertEqual([s["distance_m"] for s in b["samples"]], [18.5])
        self.assertEqual(a["track_epoch"], 1)
        self.assertEqual(b["visibility"], "TEMPORARILY_MISSING")

    def test_exact_grace_keeps_epoch_but_one_microsecond_later_expires(self):
        for return_at, expected in [(750_000, ["REACQUIRED"]), (750_001, ["LOST", "ACQUIRED"])]:
            with self.subTest(return_at=return_at):
                tracker = TrackManager("a")
                tracker.update(observation(0, 17))
                tracker.update(observation(100_000))
                result = tracker.update(observation(return_at, 17))
                self.assertEqual(event_types(result), expected)
                self.assertEqual(result["tracks"][0]["track_epoch"], 1 if return_at == 750_000 else 2)

    def test_long_gap_expires_before_same_uid_matching(self):
        tracker = TrackManager("a")
        tracker.update(observation(0, 17))
        result = tracker.update(observation(2_000_000, 17))
        self.assertEqual(event_types(result), ["LOST", "ACQUIRED"])
        self.assertEqual(result["tracks"][0]["retained_sample_count"], 1)

    def test_empty_frames_emit_missing_once_then_loss_once(self):
        tracker = TrackManager("a")
        tracker.update(observation(0, 17))
        self.assertEqual(event_types(tracker.update(observation(100_000))), ["MISSING"])
        result = tracker.update(observation(200_000))
        self.assertEqual(result["events"], [])
        self.assertEqual(result["tracks"][0]["retained_sample_count"], 1)
        self.assertEqual(event_types(tracker.update(observation(750_001))), ["LOST"])
        self.assertEqual(tracker.update(observation(900_000))["tracks"], [])

    def test_missing_history_is_pruned_even_without_new_person_samples(self):
        tracker = TrackManager("a", TrackConfig(history_window_s=0.1))
        tracker.update(observation(0, 17))
        result = tracker.update(observation(200_000))
        self.assertEqual(result["tracks"][0]["samples"], [])
        self.assertEqual(result["tracks"][0]["visibility"], "TEMPORARILY_MISSING")

    def test_null_measurements_and_zero_uid_are_observed_not_absent(self):
        tracker = TrackManager("a")
        payload = observation(0, 0)
        payload["people"][0].update(distance_m=None, gaze_overlap=None)
        payload["robot"] = {"linear_velocity": None, "angular_velocity": None}
        result = tracker.update(payload)
        track = result["tracks"][0]
        self.assertEqual(track["uid"], 0)
        self.assertEqual(track["visibility"], "OBSERVED")
        self.assertEqual(track["identity_quality_flags"], ["UID_ZERO_UNVERIFIED"])
        self.assertIsNone(track["samples"][0]["gaze_overlap"])
        self.assertIsNone(track["samples"][0]["distance_m"])
        self.assertNotIn("robot", track["samples"][0])
        self.assertEqual(result["robot"], payload["robot"])
        self.assertNotIn("optional_relative_head_position", track["samples"][0])

    def test_sample_cap_and_active_cap_evict_absent_oldest_deterministically(self):
        tracker = TrackManager("a", TrackConfig(max_samples_per_track=2, max_tracks=2))
        tracker.update(observation(0, 17, 18))
        tracker.update(observation(1, 17))
        result = tracker.update(observation(2, 17, 19))
        self.assertEqual([t["uid"] for t in result["tracks"]], [17, 19])
        self.assertEqual(result["tracks"][0]["samples_dropped_for_capacity"], 1)
        self.assertEqual([s["timestamp_us"] for s in result["tracks"][0]["samples"]], [1, 2])
        self.assertIn({"type": "LOST", "uid": 18, "track_epoch": 2,
                       "timestamp_us": 2, "reason": "capacity_eviction"}, result["events"])

    def test_excess_observed_people_rejected_before_mutation(self):
        tracker = TrackManager("a", TrackConfig(max_tracks=1))
        tracker.update(observation(0, 17))
        with self.assertRaises(TrackCapacityError):
            tracker.update(observation(1, 17, 18))
        result = tracker.update(observation(1, 17))
        self.assertEqual(result["frame_sequence"], 2)
        self.assertEqual(result["tracks"][0]["observation_count"], 2)

    def test_invalid_duplicate_and_regressing_frames_do_not_mutate(self):
        tracker = TrackManager("a")
        initial = observation(100, 17)
        tracker.update(initial)
        for timestamp in (100, 99):
            with self.assertRaises(TimestampOrderError):
                tracker.update(observation(timestamp, 18))
        invalid = observation(101, 18)
        invalid["people"][0]["gaze_overlap"] = 9
        with self.assertRaises(ValueError):
            tracker.update(invalid)
        result = tracker.update(observation(101, 17))
        self.assertEqual(result["frame_sequence"], 2)
        self.assertEqual(result["events"], [])
        self.assertEqual(result["tracks"][0]["observation_count"], 2)

    def test_snapshot_and_input_mutation_do_not_change_tracker(self):
        tracker = TrackManager("a")
        payload = observation(0, 17)
        payload["people"][0]["optional_relative_head_position"] = {
            "coordinate_frame": "CAM_HEAD", "x": 1.0, "y": 2.0, "z": 3.0}
        result = tracker.update(payload)
        result["tracks"][0]["samples"][0]["optional_relative_head_position"]["x"] = 999
        payload["people"][0]["optional_relative_head_position"]["x"] = 888
        next_result = tracker.update(observation(1, 17))
        self.assertEqual(next_result["tracks"][0]["samples"][0]["optional_relative_head_position"]["x"], 1)

    def test_mutated_model_is_revalidated(self):
        model = RawObservationFrame.model_validate(observation(0, 17))
        model.timestamp = -1
        with self.assertRaises(ValueError):
            TrackManager("a").update(model)

    def test_new_session_resets_timestamp_history_and_epoch(self):
        tracker = TrackManager("a")
        tracker.update(observation(500, 17))
        tracker.reset("b")
        result = tracker.update(observation(0, 17))
        self.assertEqual(result["session_id"], "b")
        self.assertEqual(result["frame_sequence"], 1)
        self.assertEqual(result["tracks"][0]["observation_count"], 1)
        self.assertEqual(event_types(result), ["ACQUIRED"])

    def test_large_uid_churn_stays_bounded(self):
        tracker = TrackManager("a", TrackConfig(max_tracks=3, max_samples_per_track=2))
        for uid in range(1000):
            result = tracker.update(observation(uid, uid))
            self.assertLessEqual(len(result["tracks"]), 3)
        self.assertEqual(result["tracks"][-1]["track_epoch"], 1000)

    def test_bad_configuration_and_session_fail(self):
        for values in ({"history_window_s": 0}, {"missing_grace_s": float("nan")},
                       {"missing_grace_s": True}, {"max_tracks": 0},
                       {"max_samples_per_track": 2.5}, {"max_tracks": True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                TrackConfig(**values)
        with self.assertRaises(ValueError):
            TrackManager("")
