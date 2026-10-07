"""Image association preserves Step 3 moments and reports offline run failures."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.image_match import main
from app.llm_replay import main as prepare_replay
from tests.test_llm_replay_inputs import sdk


class ImageMatchingRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sdk_input = self.root / "source.sdk.jsonl"
        self.input = self.root / "prepared.jsonl"
        self.output = self.root / "associated.jsonl"
        self.camera_directory = self.root / "camera-recording"
        self.camera_directory.mkdir()
        self.manifest = self.camera_directory / "frames.jsonl"
        self.sdk_records = [sdk("perception", 1_000_000),
                            sdk("perception", 2_000_000, sequence=2)]
        self.write_jsonl(self.sdk_input, self.sdk_records)
        with patch("app.llm_replay.OllamaClient", side_effect=AssertionError("offline only")):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = prepare_replay([
                    str(self.sdk_input), "--format", "sdk", "--prepare-only",
                    "--sample-interval", "0", "--base-url", "http://127.0.0.1:11434",
                    "--model", "recorded-model", "--output", str(self.input),
                ])
        self.assertEqual(result, 0)
        self.original_rows = [json.loads(line) for line in self.input.read_text().splitlines()]
        self.camera_records = [self.camera_record(index) for index in (1, 2)]
        self.write_jsonl(self.manifest, self.camera_records)

    @staticmethod
    def write_jsonl(path, records):
        path.write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in records),
                        encoding="utf-8")

    def camera_record(self, index, *, session="capture-a", camera="head", timestamp=None,
                      receipt=None, image=True):
        sdk_record = self.sdk_records[index - 1]
        relative = f"{camera}/{index:06d}.ppm"
        if image:
            path = self.camera_directory / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"P6\n2 1\n255\n" + b"\x10\x20\x30" * 2)
        return {
            "capture_version": 1, "session_id": session, "camera": camera,
            "sequence": index, "event": "frame",
            "received_monotonic_us": receipt if receipt is not None else index * 1_000_000 - 100_000,
            "received_unix_us": 1_790_000_000_000_000 + index * 1_000_000 - 100_000,
            "timestamp_us": timestamp if timestamp is not None else sdk_record["packet"]["time"],
            "width": 2, "height": 1, "file": relative,
        }

    def arguments(self, *settings, replay=None, manifests=None, output=None):
        return [str(replay or self.input), "--manifests",
                *(str(path) for path in (manifests or [self.manifest])),
                "--output", str(output or self.output), *settings]

    def run_main(self, *settings, replay=None, manifests=None, output=None, collect=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("app.llm_replay.OllamaClient", side_effect=AssertionError("matching must not infer")) as client:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(self.arguments(*settings, replay=replay,
                                             manifests=manifests, output=output))
        client.assert_not_called()
        summary = json.loads(stdout.getvalue())
        target = output or self.output
        rows = []
        if collect and target.is_file():
            rows = [json.loads(line) for line in target.read_text().splitlines() if line.strip()]
        return status, summary, rows, stderr.getvalue()

    def assert_original_rows(self, output_rows, originals=None):
        originals = originals or self.original_rows
        self.assertEqual(len(output_rows), len(originals))
        for original, actual in zip(originals, output_rows):
            self.assertEqual({key: value for key, value in actual.items() if key != "image_matching"},
                             original)
            self.assertEqual(actual["social_state_json"], original["social_state_json"])

    def test_preparation_rows_preserve_every_original_field_and_canonical_snapshot(self):
        input_bytes = self.input.read_bytes()
        manifest_bytes = self.manifest.read_bytes()
        images = {record["file"]: (self.camera_directory / record["file"]).read_bytes()
                  for record in self.camera_records}
        status, summary, rows, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(summary["status"], "complete")
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["total_rows"], 2)
        self.assertEqual(summary["rows_written"], 2)
        self.assertEqual(summary["matched_rows"], 2)
        self.assertEqual(summary["unmatched_rows"], 0)
        self.assertEqual(summary["methods"], {"exact_recorded_sdk_timestamp": 2})
        self.assertEqual(summary["failure_categories"], {})
        self.assert_original_rows(rows)
        for row in rows:
            matching = row["image_matching"]
            self.assertEqual(matching["schema_version"], 1)
            self.assertEqual(matching["status"], "matched")
            self.assertTrue(matching["ok"])
            self.assertIsNone(matching["error"])
            self.assertEqual(matching["method"], "exact_recorded_sdk_timestamp")
            self.assertIsNotNone(matching["frame"])
            self.assertEqual(row["status"], "prepared")
            self.assertIsNone(row["ok"])
        self.assertEqual(self.input.read_bytes(), input_bytes)
        self.assertEqual(self.manifest.read_bytes(), manifest_bytes)
        for relative, original in images.items():
            self.assertEqual((self.camera_directory / relative).read_bytes(), original)

    def test_existing_success_and_failure_inference_results_are_preserved(self):
        rows = deepcopy(self.original_rows)
        rows[0].update(status="succeeded", ok=True,
                       decision={"action": "YIELD", "reason": "Recorded decision."},
                       error=None, returned_model="recorded-model", request_duration_s=0.5,
                       raw_content='{"action":"YIELD","reason":"Recorded decision."}')
        rows[1].update(status="failed", ok=False, decision=None,
                       error={"category": "invalid_decision", "message": "Wrong action.",
                              "http_status": 200}, returned_model="recorded-model",
                       request_duration_s=0.8, raw_content='{"action":"MONITOR"}')
        self.write_jsonl(self.input, rows)
        status, summary, associated, stderr = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(summary["matched_rows"], 2)
        self.assert_original_rows(associated, rows)
        self.assertFalse(associated[1]["ok"])
        self.assertTrue(associated[1]["image_matching"]["ok"])

    def test_unmatched_rows_remain_in_order_and_complete_pass_returns_one(self):
        self.camera_records[1]["timestamp_us"] += 1
        self.write_jsonl(self.manifest, self.camera_records)
        status, summary, rows, _ = self.run_main()
        self.assertEqual(status, 1)
        self.assertEqual(summary["status"], "complete_with_unmatched")
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["total_rows"], 2)
        self.assertEqual(summary["rows_written"], 2)
        self.assertEqual(summary["matched_rows"], 1)
        self.assertEqual(summary["unmatched_rows"], 1)
        self.assertEqual(summary["methods"], {"exact_recorded_sdk_timestamp": 1})
        self.assertEqual(sum(summary["failure_categories"].values()), 1)
        self.assert_original_rows(rows)
        unmatched = rows[1]["image_matching"]
        self.assertEqual(unmatched["status"], "unmatched")
        self.assertFalse(unmatched["ok"])
        self.assertIsNone(unmatched["frame"])
        self.assertIsNone(unmatched["method"])
        self.assertTrue(unmatched["error"]["category"])
        self.assertTrue(unmatched["error"]["message"])

    def test_missing_camera_session_retains_all_rows_and_counts_failures(self):
        for record in self.camera_records:
            record["session_id"] = "different-capture"
        self.write_jsonl(self.manifest, self.camera_records)
        status, summary, rows, _ = self.run_main()
        self.assertEqual(status, 1)
        self.assertEqual(summary["matched_rows"], 0)
        self.assertEqual(summary["unmatched_rows"], 2)
        self.assertEqual(sum(summary["failure_categories"].values()), 2)
        self.assert_original_rows(rows)
        self.assertTrue(all(not row["image_matching"]["ok"] for row in rows))

    def test_explicit_chest_camera_does_not_borrow_head_frames(self):
        status, summary, rows, _ = self.run_main("--camera", "chest")
        self.assertEqual(status, 1)
        self.assertEqual(summary["unmatched_rows"], 2)
        self.assertTrue(all(row["image_matching"]["policy"]["camera"] == "chest" for row in rows))

    def test_missing_image_is_a_matching_failure_and_does_not_drop_a_row(self):
        (self.camera_directory / self.camera_records[1]["file"]).unlink()
        status, summary, rows, _ = self.run_main()
        self.assertEqual(status, 1)
        self.assertEqual(summary["status"], "complete_with_unmatched")
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["matched_rows"], 1)
        self.assertEqual(summary["unmatched_rows"], 1)
        self.assertEqual(sum(summary["failure_categories"].values()), 1)
        self.assert_original_rows(rows)
        self.assertIsNone(rows[1]["image_matching"]["frame"])
        self.assertFalse(rows[1]["image_matching"]["ok"])

    def test_opt_in_receipt_matching_summary_is_separate_from_exact(self):
        self.camera_records[1]["timestamp_us"] += 1
        self.write_jsonl(self.manifest, self.camera_records)
        status, summary, rows, stderr = self.run_main(
            "--allow-receipt-match", "--max-frame-age", "0.1")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(summary["methods"],
                         {"exact_recorded_sdk_timestamp": 1, "reception_monotonic": 1})
        self.assertEqual(rows[1]["image_matching"]["method"], "reception_monotonic")
        self.assert_original_rows(rows)

    def test_reception_age_requires_explicit_opt_in_and_explicit_age(self):
        invalid = [("--allow-receipt-match",), ("--max-frame-age", "0.1"),
                   ("--allow-receipt-match", "--max-frame-age", "-1"),
                   ("--allow-receipt-match", "--max-frame-age", "nan"),
                   ("--allow-receipt-match", "--max-frame-age", "inf")]
        for settings in invalid:
            with self.subTest(settings=settings):
                status, summary, _, _ = self.run_main(*settings)
                self.assertEqual(status, 2)
                self.assertEqual(summary["status"], "failed")
                self.assertFalse(summary["complete"])
                self.assertTrue(summary["error"])
                self.assertFalse(self.output.exists())

    def test_fractional_microsecond_maximum_never_rounds_up_to_older_frame(self):
        self.camera_records[0]["timestamp_us"] += 1
        self.camera_records[0]["received_monotonic_us"] = 999_998
        self.write_jsonl(self.manifest, self.camera_records)
        status, summary, rows, _ = self.run_main(
            "--allow-receipt-match", "--max-frame-age", "0.0000016")
        self.assertEqual(status, 1)
        self.assertTrue(summary["complete"])
        self.assertEqual(rows[0]["image_matching"]["policy"]["max_age_us"], 1)
        self.assertEqual(rows[0]["image_matching"]["error"]["category"], "frame_too_old")
        self.assertEqual(rows[0]["image_matching"]["error"]["details"]["age_us"], 2)
        status, summary, rows, stderr = self.run_main(
            "--allow-receipt-match", "--max-frame-age", "0.000002",
            output=self.root / "inclusive-age-boundary.jsonl")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(summary["matched_rows"], 2)
        self.assertEqual(rows[0]["image_matching"]["policy"]["max_age_us"], 2)
        self.assertEqual(rows[0]["image_matching"]["method"], "reception_monotonic")
        self.assertEqual(rows[0]["image_matching"]["time_difference"]["value"], -2)

    def test_later_invalid_json_is_partial_with_exact_input_line(self):
        with self.input.open("a", encoding="utf-8") as stream:
            stream.write('{"broken":\n')
        status, summary, rows, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertEqual(summary["status"], "partial")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["total_rows"], 2)
        self.assertEqual(summary["rows_written"], 2)
        self.assertIn(str(self.input) + ":3", summary["error"]["message"])
        self.assert_original_rows(rows)

    def test_invalid_snapshot_or_correlation_is_source_located_and_partial(self):
        mutations = {
            "source_state_id": "different-state", "session_id": "different-session",
            "source_robot_timestamp_us": 3_000_000,
            "social_state_json": '{"incompatible":true}',
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                rows = deepcopy(self.original_rows)
                rows[1][field] = value
                self.write_jsonl(self.input, rows)
                output = self.root / f"invalid-{field}.jsonl"
                status, summary, actual, _ = self.run_main(output=output)
                self.assertEqual(status, 2)
                self.assertEqual(summary["status"], "partial")
                self.assertFalse(summary["complete"])
                self.assertEqual(summary["rows_written"], 1)
                self.assertEqual(actual[0]["social_state_json"], rows[0]["social_state_json"])
                self.assertIn(str(self.input) + ":2", summary["error"]["message"])

    def test_overflowing_number_in_nonsocial_metadata_is_source_located_and_partial(self):
        second_line = json.dumps(self.original_rows[1])
        second_line = second_line[:-1] + ', "extra_metadata": {"overflow": 1e999}}'
        self.input.write_text(json.dumps(self.original_rows[0]) + "\n" + second_line + "\n",
                              encoding="utf-8")
        status, summary, rows, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertEqual(summary["status"], "partial")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["error"]["category"], "invalid_replay")
        self.assertEqual(summary["total_rows"], 1)
        self.assertEqual(summary["rows_written"], 1)
        self.assertIn(str(self.input) + ":2", summary["error"]["message"])
        self.assert_original_rows(rows, self.original_rows[:1])

    def test_missing_required_step_three_metadata_is_rejected_as_partial_input(self):
        for field in ("processing", "sampling", "completed_at", "completion_clock", "ok"):
            with self.subTest(field=field):
                rows = deepcopy(self.original_rows)
                del rows[1][field]
                self.write_jsonl(self.input, rows)
                status, summary, actual, _ = self.run_main(
                    output=self.root / f"missing-{field}.jsonl")
                self.assertEqual(status, 2)
                self.assertEqual(summary["status"], "partial")
                self.assertFalse(summary["complete"])
                self.assertEqual(summary["error"]["category"], "invalid_replay")
                self.assertEqual(summary["total_rows"], 1)
                self.assertEqual(summary["rows_written"], 1)
                self.assertIn(str(self.input) + ":2", summary["error"]["message"])
                self.assert_original_rows(actual, self.original_rows[:1])

    def test_existing_image_matching_field_is_rejected_without_overwriting_it(self):
        rows = deepcopy(self.original_rows)
        rows[0]["image_matching"] = {"from_previous_run": True}
        self.write_jsonl(self.input, rows)
        original = self.input.read_bytes()
        status, summary, output, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["rows_written"], 0)
        self.assertEqual(output, [])
        self.assertIn("image_matching", summary["error"]["message"])
        self.assertEqual(self.input.read_bytes(), original)

    def test_duplicate_keys_nonfinite_and_nonobject_input_are_rejected(self):
        for index, invalid in enumerate(('{"same":1,"same":2}\n', '{"value":NaN}\n',
                                         '[]\n', 'null\n')):
            with self.subTest(invalid=invalid):
                self.input.write_text(invalid, encoding="utf-8")
                status, summary, rows, _ = self.run_main(output=self.root / f"invalid-{index}.jsonl")
                self.assertEqual(status, 2)
                self.assertFalse(summary["complete"])
                self.assertEqual(summary["rows_written"], 0)
                self.assertEqual(rows, [])
                self.assertIn(str(self.input) + ":1", summary["error"]["message"])

    def test_empty_replay_is_a_failed_run_without_successful_rows(self):
        self.input.write_text("\n \n", encoding="utf-8")
        status, summary, rows, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertEqual(summary["status"], "failed")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["total_rows"], 0)
        self.assertEqual(summary["rows_written"], 0)
        self.assertEqual(rows, [])
        self.assertTrue(summary["error"])

    def test_output_cannot_resolve_to_replay_manifest_or_stored_image(self):
        inputs = [self.input, self.manifest,
                  self.camera_directory / self.camera_records[0]["file"]]
        for index, path in enumerate(inputs):
            original = path.read_bytes()
            alias = self.root / f"input-alias-{index}"
            alias.symlink_to(path)
            for output in (path, alias):
                with self.subTest(output=output):
                    status, summary, _, _ = self.run_main(output=output, collect=False)
                    self.assertEqual(status, 2)
                    self.assertFalse(summary["complete"])
                    self.assertEqual(summary["rows_written"], 0)
                    self.assertEqual(path.read_bytes(), original)

    def test_output_cannot_create_a_missing_referenced_camera_image(self):
        image = self.camera_directory / self.camera_records[0]["file"]
        image.unlink()
        status, summary, _, _ = self.run_main(output=image, collect=False)
        self.assertEqual(status, 2)
        self.assertFalse(summary["complete"])
        self.assertFalse(image.exists())
        self.assertEqual(summary["rows_written"], 0)

    def test_existing_output_and_symlink_to_output_are_refused(self):
        self.output.write_text('{"previous":"result"}\n', encoding="utf-8")
        original = self.output.read_bytes()
        alias = self.root / "output-alias.jsonl"
        alias.symlink_to(self.output)
        for target in (self.output, alias):
            with self.subTest(target=target):
                status, summary, _, _ = self.run_main(output=target, collect=False)
                self.assertEqual(status, 2)
                self.assertFalse(summary["complete"])
                self.assertEqual(summary["rows_written"], 0)
                self.assertEqual(self.output.read_bytes(), original)

    def test_output_symlink_loop_is_reported_as_failed_without_exception(self):
        self.output.symlink_to(self.output.name)
        status, summary, _, _ = self.run_main(collect=False)
        self.assertEqual(status, 2)
        self.assertEqual(summary["status"], "failed")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["total_rows"], 0)
        self.assertEqual(summary["rows_written"], 0)
        self.assertTrue(summary["error"])
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(self.output.readlink(), Path(self.output.name))

    def test_all_explicit_manifests_are_protected(self):
        second_directory = self.root / "second-camera-recording"
        second_directory.mkdir()
        second_manifest = second_directory / "frames.jsonl"
        self.write_jsonl(second_manifest, self.camera_records)
        original = second_manifest.read_bytes()
        status, summary, _, _ = self.run_main(manifests=[self.manifest, second_manifest],
                                             output=second_manifest, collect=False)
        self.assertEqual(status, 2)
        self.assertFalse(summary["complete"])
        self.assertEqual(second_manifest.read_bytes(), original)

    def test_first_write_failure_is_non_success_and_surfaces_error(self):
        original_open = Path.open

        class BrokenWriter:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def write(self, text):
                raise OSError("output device full")

            def flush(self):
                pass

        def open_path(path, *args, **kwargs):
            if path == self.output and args and args[0] == "x":
                return BrokenWriter()
            return original_open(path, *args, **kwargs)

        with patch("app.image_match.Path.open", new=open_path):
            status, summary, _, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["rows_written"], 0)
        self.assertEqual(summary["error"]["stage"], "output")
        self.assertIn("output device full", summary["error"]["message"])

    def test_second_write_failure_retains_first_row_and_reports_partial(self):
        original_open = Path.open

        class OneRowWriter:
            def __init__(self, stream):
                self.stream = stream
                self.writes = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()
                return False

            def write(self, text):
                self.writes += 1
                if self.writes == 2:
                    raise OSError("failed after first row")
                return self.stream.write(text)

            def flush(self):
                self.stream.flush()

        def open_path(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path == self.output and args and args[0] == "x":
                return OneRowWriter(stream)
            return stream

        with patch("app.image_match.Path.open", new=open_path):
            status, summary, rows, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertEqual(summary["status"], "partial")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["total_rows"], 2)
        self.assertEqual(summary["rows_written"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual({key: value for key, value in rows[0].items() if key != "image_matching"},
                         self.original_rows[0])
        self.assertEqual(summary["error"]["stage"], "output")
        self.assertIn("failed after first row", summary["error"]["message"])


if __name__ == "__main__":
    unittest.main()
