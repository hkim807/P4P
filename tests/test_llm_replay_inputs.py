"""Offline recording reconstruction, provenance and session boundaries."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.llm_replay_inputs import iter_replay_states
from app.pipeline import TrackingPipeline
from app.state.estimator import SocialStateEstimator
from app.state.social_models import TemporalConfig
from app.state.tracks import TrackConfig
from robot.navel_client.adapter import NavelObservationAdapter
from robot.navel_client.sdk_capture import sdk_json
from tests.fixtures import CoordSystem, Sensor, frame, locomotion, perception, person
from types import SimpleNamespace as NS


def raw(timestamp, *, people=True, distance=2.0, gaze=.95):
    value = frame()
    value["timestamp"] = timestamp
    value["people"] = [{"uid": 17, "distance_m": distance, "gaze_overlap": gaze}] if people else []
    value["robot"] = {"linear_velocity": 0.0, "angular_velocity": 0.0}
    return value


def sdk(stream, timestamp, sequence=1, session="capture-a", packet=None):
    if packet is None:
        packet = (NS(time=9_000_000_000 + timestamp, persons=[person(gaze_overlap=.95)])
                  if stream == "perception" else locomotion())
    return {"capture_version": 1, "session_id": session, "stream": stream,
            "sequence": sequence, "received_monotonic_us": timestamp,
            "received_unix_us": 1_790_000_000_000_000 + timestamp,
            "packet": sdk_json(packet)}


def saved_states(*timestamps, config=None, session="recorded-session"):
    tracker = TrackingPipeline(session)
    estimator = SocialStateEstimator(config)
    return [estimator.update(tracker.process(raw(t))).model_dump(mode="json") for t in timestamps]


class ReplayInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def write(self, rows, name="input.jsonl"):
        path = self.directory / name
        path.write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in rows), encoding="utf-8")
        return path

    def read(self, rows, format="raw", **kwargs):
        return list(iter_replay_states(self.write(rows), format, **kwargs))

    def test_raw_history_uses_recorded_time_and_effective_configuration(self):
        tracks = TrackConfig(history_window_s=4.0, missing_grace_s=1.0)
        temporal = TemporalConfig(window_s=3.0, max_fit_samples=24)
        rows = [raw(1_000_000 + i*100_000, distance=2.5-i*.02) for i in range(22)]
        items = self.read(rows, track_config=tracks, temporal_config=temporal)
        self.assertEqual(len(items), len(rows))
        self.assertEqual([x.state.robot_timestamp_us for x in items], [x["timestamp"] for x in rows])
        final = items[-1].state
        self.assertEqual(final.ingest_sequence, 22)
        self.assertEqual(final.config, temporal)
        self.assertEqual(final.config_version, temporal.version)
        self.assertEqual(final.people[0].gaze_state, "SUSTAINED")
        self.assertEqual(final.people[0].relative_distance_trend, "DECREASING")
        self.assertAlmostEqual(final.people[0].evidence.distance_slope_mps, -.2)
        self.assertEqual(final.people[0].evidence.distance_valid_samples, 22)
        self.assertEqual(items[-1].processing["track_config"], asdict(tracks))
        self.assertEqual(items[-1].processing["temporal_config"], temporal.model_dump(mode="json"))
        self.assertEqual(items[-1].processing["temporal_config_version"], temporal.version)

    def test_raw_missing_grace_and_expiry_follow_source_time(self):
        items = self.read([raw(0), raw(400_000, people=False), raw(800_000, people=False),
                           raw(1_200_000)])
        self.assertEqual(items[1].state.people[0].visibility, "TEMPORARILY_MISSING")
        self.assertEqual(items[1].state.people[0].time_since_seen_s, .4)
        self.assertEqual(items[2].state.people, [])
        self.assertNotEqual(items[0].state.people[0].track_epoch, items[3].state.people[0].track_epoch)

    def test_raw_provenance_keeps_original_payload_and_actual_line(self):
        original = raw(123_456)
        path = self.directory / "raw-with-blank-lines.jsonl"
        path.write_text("\n\n" + json.dumps(original) + "\n", encoding="utf-8")
        item = list(iter_replay_states(path, "raw"))[0]
        self.assertEqual(item.source["input_path"], str(path.resolve()))
        self.assertEqual(item.source["input_format"], "raw")
        self.assertFalse(item.source["reconstructed"])
        self.assertEqual(item.source["line_number"], 3)
        self.assertEqual(item.source["record_number"], 1)
        self.assertEqual(item.source["source_record"], original)
        self.assertIsNone(item.source["original_capture_session"])
        self.assertEqual(item.source["processing_session_id"], item.state.session_id)
        self.assertEqual(item.source["timestamps"]["estimation_timestamp_us"], original["timestamp"])
        self.assertIn("monotonic", item.source["timestamps"]["estimation_clock"])
        self.assertIsNone(item.source["timestamps"]["sdk_perception_timestamp"])

    def test_separate_raw_recordings_have_independent_history(self):
        first = self.write([raw(i*100_000) for i in range(22)], "first.jsonl")
        second = self.write([raw(0, distance=1.0)], "second.jsonl")
        long = list(iter_replay_states(first, "raw"))
        fresh = list(iter_replay_states(second, "raw"))[0]
        self.assertEqual(long[-1].state.people[0].gaze_state, "SUSTAINED")
        self.assertEqual(fresh.state.ingest_sequence, 1)
        self.assertEqual(fresh.state.people[0].track_age_s, 0.0)
        self.assertEqual(fresh.state.people[0].gaze_state, "UNKNOWN")
        self.assertNotEqual(long[0].state.session_id, fresh.state.session_id)

    def test_generated_raw_session_is_opaque_deterministic_and_filename_independent(self):
        rows = [raw(10), raw(110)]
        first = self.write(rows, "expected-engage-scenario.jsonl")
        second = self.write(rows, "different-name.jsonl")
        a = list(iter_replay_states(first, "raw"))
        b = list(iter_replay_states(second, "raw"))
        self.assertEqual([x.state.model_dump() for x in a], [x.state.model_dump() for x in b])
        self.assertNotIn("expected-engage", a[0].state.session_id)
        self.assertNotIn("scenario", a[0].state.state_id)
        self.assertEqual(a[0].state.session_id, list(iter_replay_states(first, "raw"))[0].state.session_id)

    def test_saved_social_states_are_used_directly_with_every_recorded_value(self):
        config = TemporalConfig(window_s=3.0, too_close_m=.7, interaction_max_m=1.7,
                                approachable_max_m=3.5)
        rows = saved_states(1_000_000, 1_100_000, config=config)
        rows[0]["config_version"] = "recorded-config-version"
        rows[0]["state_id"] = "recorded-exact-state-id"
        path = self.write(rows)
        with patch.object(SocialStateEstimator, "update", side_effect=AssertionError("must not re-estimate")):
            items = list(iter_replay_states(path, "social"))
        self.assertEqual([x.state.model_dump(mode="json") for x in items], rows)
        self.assertEqual(items[0].state.session_id, "recorded-session")
        self.assertEqual(items[0].state.config, config)

    def test_saved_social_configuration_cannot_be_overridden(self):
        path = self.write(saved_states(1_000_000))
        for kwargs in ({"temporal_config": TemporalConfig()}, {"track_config": TrackConfig()}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "overrid|configuration"):
                list(iter_replay_states(path, "social", **kwargs))

    def test_saved_social_source_sessions_and_order_are_preserved(self):
        rows = saved_states(1_000_000, 1_100_000, session="saved-a")
        rows += saved_states(0, session="saved-b")
        items = self.read(rows, "social")
        self.assertEqual([x.state.model_dump(mode="json") for x in items], rows)
        self.assertEqual(items[0].source["source_session_id"], "saved-a")
        self.assertEqual(items[2].source["source_session_id"], "saved-b")
        self.assertNotEqual(items[0].source["processing_session_id"], items[2].source["processing_session_id"])
        self.assertIsNone(items[0].processing["track_config"])
        self.assertIsNone(items[0].processing["tracker_version"])
        self.assertEqual(items[0].source["timestamps"]["estimation_timestamp_us"], 1_000_000)
        for field in ("robot_timestamp_us", "ingest_sequence"):
            malformed = deepcopy(rows[:2])
            malformed[1][field] = malformed[0][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, r"input\.jsonl:2:"):
                self.read(malformed, "social")

    def test_closed_source_sessions_cannot_silently_reappear(self):
        sdk_rows = [sdk("perception", 100, session="a"),
                    sdk("perception", 0, session="b"),
                    sdk("perception", 200, 2, session="a")]
        social_rows = (saved_states(100, session="a") + saved_states(0, session="b")
                       + saved_states(200, session="a"))
        for format, rows in (("sdk", sdk_rows), ("social", social_rows)):
            with self.subTest(format=format), self.assertRaisesRegex(ValueError, r"input\.jsonl:3:.*session"):
                self.read(rows, format)

    def test_empty_files_and_wrong_explicit_formats_are_rejected(self):
        empty = self.write([])
        for format in ("sdk", "raw", "social"):
            with self.subTest(format=format), self.assertRaisesRegex(ValueError, "no |empty"):
                list(iter_replay_states(empty, format))
        fixtures = {"raw": raw(0), "sdk": sdk("perception", 0), "social": saved_states(0)[0]}
        for supplied, row in fixtures.items():
            for format in fixtures:
                if format == supplied:
                    continue
                with self.subTest(supplied=supplied, format=format), self.assertRaises(ValueError):
                    self.read([row], format)

    def test_historical_behaviour_intent_and_monitor_are_explicitly_incompatible(self):
        historical = {"observation": {"humans": [], "images": []},
                      "social_state": {"humans": [], "crowd": {}, "selected_image_ids": []},
                      "behavior_intent": {"action": "MONITOR"}}
        for format in ("sdk", "raw", "social"):
            with self.subTest(format=format), self.assertRaisesRegex(ValueError, "historical|incompatib|unsupported"):
                self.read([historical], format)

    def test_raw_backward_and_duplicate_timestamps_preserve_source_location(self):
        for timestamp in (99, 100):
            with self.subTest(timestamp=timestamp):
                path = self.write([raw(100), raw(timestamp)])
                with self.assertRaisesRegex(ValueError, r"input\.jsonl:2:"):
                    list(iter_replay_states(path, "raw"))

    def test_invalid_json_and_nonobject_records_have_source_line_errors(self):
        for format in ("sdk", "raw", "social"):
            for text in ("{broken}\n", "[]\n", "null\n"):
                path = self.directory / "invalid.jsonl"
                path.write_text(text, encoding="utf-8")
                with self.subTest(format=format, text=text), self.assertRaisesRegex(ValueError, r"invalid\.jsonl:1:"):
                    list(iter_replay_states(path, format))

    def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected(self):
        for format in ("sdk", "raw", "social"):
            for text in ('{"same":1,"same":2}\n', '{"value":NaN}\n', '{"value":Infinity}\n'):
                path = self.directory / "ambiguous.jsonl"
                path.write_text(text, encoding="utf-8")
                with self.subTest(format=format, text=text), self.assertRaisesRegex(ValueError, r"ambiguous\.jsonl:1:"):
                    list(iter_replay_states(path, format))

    def test_sdk_conversion_has_exact_live_adapter_semantics(self):
        live_people = [
            person(uid=17, dist_mm=1800.0, gaze_overlap=.9),
            person(uid=17, dist_mm=999.0),  # Duplicate UID is ignored.
            person(uid=True), person(uid=-1),
            person(uid=21, dist_mm=float("nan"), gaze_overlap=float("inf"),
                   g_head_position=[NS(sys=CoordSystem.CAM_HEAD, x=float("nan"), y=0.0, z=0.0)]),
            person(uid=22, dist_mm=-1.0, gaze_overlap=1.1,
                   g_head_position=[NS(sys=CoordSystem.HEAD_STRAIGHT, x=1.0, y=2.0, z=3.0)]),
        ]
        live_perception = perception(*live_people)
        live_perception.time = 99_999_999
        live_loco = locomotion(odometry=NS(velocity=NS(linear_x=-.25, angular_z=float("nan"))),
                               distances={Sensor.LIDAR: [1.0, float("inf"), -1.0, True],
                                          Sensor.SONAR: [None, 0.5]})
        expected = NavelObservationAdapter(monotonic_ns=lambda: 1_000_000_000).convert(live_perception, live_loco)
        converted = []
        original = NavelObservationAdapter.convert

        def spy(adapter, perception_value, locomotion_value=None):
            value = original(adapter, perception_value, locomotion_value)
            converted.append(deepcopy(value))
            return value

        rows = [sdk("locomotion", 900_000, packet=live_loco),
                sdk("perception", 1_000_000, packet=live_perception)]
        with patch.object(NavelObservationAdapter, "convert", spy):
            items = self.read(rows, "sdk")
        self.assertEqual(converted, [expected])
        self.assertEqual([p.uid for p in items[0].state.people], [17, 21, 22])
        self.assertEqual(items[0].state.robot.linear_velocity, -.25)
        self.assertIsNone(items[0].state.robot.angular_velocity)
        self.assertIsNone(items[0].state.people[1].latest_distance_m)

    def test_sdk_missing_future_and_stale_locomotion_remain_unavailable(self):
        rows = [sdk("perception", 100), sdk("locomotion", 200),
                sdk("perception", 1_000_200, 2), sdk("perception", 1_000_201, 3)]
        items = self.read(rows, "sdk")
        self.assertIsNone(items[0].state.robot.linear_velocity)
        self.assertEqual(items[1].state.robot.linear_velocity, .2)  # Exact freshness boundary.
        self.assertIsNone(items[2].state.robot.linear_velocity)
        self.assertIsNone(items[2].state.robot.angular_velocity)
        self.assertEqual([x.source["locomotion"]["status"] for x in items], ["missing", "used", "stale"])
        self.assertEqual(items[1].source["locomotion"]["age_s"], 1.0)

    def test_sdk_same_receipt_locomotion_only_applies_after_its_file_record(self):
        rows = [sdk("perception", 100), sdk("locomotion", 100), sdk("perception", 101, 2)]
        items = self.read(rows, "sdk")
        self.assertIsNone(items[0].state.robot.linear_velocity)
        self.assertEqual(items[1].state.robot.linear_velocity, .2)

    def test_sdk_provenance_preserves_distinct_clocks_and_original_records(self):
        loco = sdk("locomotion", 900_000)
        loco["packet"]["odometry"]["time"] = 9_123_456_789
        perception_record = sdk("perception", 1_000_000)
        path = self.directory / "sdk-with-blank-lines.jsonl"
        path.write_text(json.dumps(loco) + "\n\n" + json.dumps(perception_record) + "\n", encoding="utf-8")
        item = list(iter_replay_states(path, "sdk"))[0]
        source = item.source
        self.assertTrue(source["reconstructed"])
        self.assertEqual(source["input_format"], "sdk")
        self.assertEqual(source["line_number"], 3)
        self.assertEqual(source["record_number"], 2)
        self.assertEqual(source["source_record"], perception_record)
        self.assertEqual(source["original_capture_session"], "capture-a")
        self.assertEqual(source["processing_session_id"], item.state.session_id)
        timestamps = source["timestamps"]
        self.assertEqual(item.state.robot_timestamp_us, 1_000_000)
        self.assertEqual(timestamps["estimation_timestamp_us"], 1_000_000)
        self.assertEqual(timestamps["received_monotonic_us"], perception_record["received_monotonic_us"])
        self.assertEqual(timestamps["received_unix_us"], perception_record["received_unix_us"])
        self.assertEqual(timestamps["sdk_perception_timestamp"], perception_record["packet"]["time"])
        self.assertIn("monotonic", timestamps["estimation_clock"])
        self.assertIn("unverified", timestamps["sdk_perception_clock"])
        self.assertEqual(source["locomotion"]["source_record"], loco)
        self.assertEqual(source["locomotion"]["line_number"], 1)
        self.assertEqual(source["locomotion"]["sdk_odometry_timestamp"], 9_123_456_789)
        self.assertEqual(source["reconstructed_raw_observation"]["timestamp"], 1_000_000)
        self.assertEqual(item.processing["max_locomotion_age_s"], 1.0)
        self.assertIn("not guaranteed", item.processing["sdk_reconstruction"]["live_frame_equivalence"])

    def test_sdk_source_clock_values_do_not_control_estimation_or_order(self):
        rows = [sdk("perception", 100), sdk("perception", 100_100, 2)]
        rows[0]["packet"]["time"] = 9_999_999_999
        rows[1]["packet"]["time"] = 1
        rows[1]["received_unix_us"] = 1
        items = self.read(rows, "sdk")
        self.assertEqual([x.state.robot_timestamp_us for x in items], [100, 100_100])
        self.assertAlmostEqual(items[1].state.people[0].track_age_s, .1)
        self.assertEqual(items[1].source["timestamps"]["sdk_perception_timestamp"], 1)

    def test_generated_sdk_session_is_filename_independent(self):
        rows = [sdk("perception", 100)]
        a = self.write(rows, "scenario-expected-action.jsonl")
        b = self.write(rows, "renamed.jsonl")
        first = list(iter_replay_states(a, "sdk"))[0]
        second = list(iter_replay_states(b, "sdk"))[0]
        self.assertEqual(first.state.model_dump(), second.state.model_dump())
        self.assertNotIn("scenario", first.state.session_id)
        self.assertNotEqual(first.state.session_id, "capture-a")

    def test_sdk_session_boundary_resets_history_and_locomotion(self):
        rows = [sdk("locomotion", 0)]
        rows += [sdk("perception", 100_000+i*100_000, i+1) for i in range(22)]
        rows += [sdk("perception", 0, session="capture-b")]
        items = self.read(rows, "sdk", max_locomotion_age_s=10.0)
        self.assertEqual(items[-2].state.people[0].gaze_state, "SUSTAINED")
        fresh = items[-1].state
        self.assertEqual(fresh.ingest_sequence, 1)
        self.assertEqual(fresh.people[0].track_age_s, 0.0)
        self.assertEqual(fresh.people[0].gaze_state, "UNKNOWN")
        self.assertIsNone(fresh.robot.linear_velocity)
        self.assertNotEqual(items[0].state.session_id, fresh.session_id)

    def test_sdk_sequence_and_receipt_order_errors_are_not_repaired(self):
        bad_cases = [
            [sdk("perception", 100), sdk("perception", 200, 3)],
            [sdk("perception", 100), sdk("perception", 200, 1)],
            [sdk("perception", 100), sdk("locomotion", 99)],
            [sdk("perception", 100), sdk("perception", 100, 2)],
        ]
        for rows in bad_cases:
            with self.subTest(rows=rows):
                path = self.write(rows)
                iterator = iter_replay_states(path, "sdk")
                self.assertEqual(next(iterator).state.robot_timestamp_us, 100)
                with self.assertRaisesRegex(ValueError, r"input\.jsonl:2:"):
                    list(iterator)

    def test_malformed_sdk_payload_containers_are_rejected(self):
        packets = [
            {"persons": "invalid"}, {"persons": {"uid": 1}}, {"persons": ["person"]},
            {"persons": [{"uid": 1, "g_head_position": {"sys": "CAM_HEAD"}}]},
        ]
        for packet in packets:
            with self.subTest(packet=packet), self.assertRaisesRegex(ValueError, r"input\.jsonl:1:"):
                self.read([sdk("perception", 100, packet=packet)], "sdk")
        for packet in ({"odometry": []}, {"odometry": {"velocity": []}}, {"distances": []}):
            with self.subTest(packet=packet), self.assertRaisesRegex(ValueError, r"input\.jsonl:1:"):
                self.read([sdk("locomotion", 100, packet=packet)], "sdk")

    def test_locomotion_only_sdk_recording_does_not_prepare_fabricated_observations(self):
        with self.assertRaisesRegex(ValueError, "no perception"):
            self.read([sdk("locomotion", 100)], "sdk")


if __name__ == "__main__":
    unittest.main()
