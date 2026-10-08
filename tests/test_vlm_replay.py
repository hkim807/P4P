"""Image-only replay preserves selected moments and records independent results."""
import base64
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app.domain.model_decision import ModelDecision
from app.image_match import main as associate_replay
from app.llm_replay import main as prepare_replay
from app.ollama import OllamaError, OllamaErrorCategory, OllamaResult
from app.policy.vlm import PROMPT_VERSION, SYSTEM_PROMPT, USER_PROMPT
from app.vlm_replay import main
from tests.test_llm_replay_inputs import sdk


class VLMReplayRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "sdk-source.jsonl"
        self.prepared = self.root / "prepared.jsonl"
        self.associated = self.root / "associated.jsonl"
        self.output = self.root / "vlm-results.jsonl"
        self.camera_root = self.root / "camera"
        self.camera_root.mkdir()
        self.manifest = self.camera_root / "frames.jsonl"
        self.sdk_records = [sdk("perception", index * 1_000_000, sequence=index)
                            for index in range(1, 5)]
        self.write_rows(self.source, self.sdk_records)
        with patch("app.llm_replay.OllamaClient", side_effect=AssertionError("preparation is offline")):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = prepare_replay([
                    str(self.source), "--format", "sdk", "--prepare-only",
                    "--sample-interval", "0", "--base-url", "http://127.0.0.1:11434",
                    "--model", "original-llm-model", "--output", str(self.prepared),
                ])
        self.assertEqual(result, 0)
        self.camera_records = []
        self.images = []
        for index, record in enumerate(self.sdk_records, 1):
            relative = f"head/{index:06d}.ppm"
            image = self.camera_root / relative
            image.parent.mkdir(exist_ok=True)
            image.write_bytes(b"P6\n2 1\n255\n" + bytes([index, 20, 30, 40, 50, 60]))
            self.images.append(image)
            self.camera_records.append({
                "capture_version": 1, "session_id": "capture-a", "camera": "head",
                "sequence": index, "event": "frame",
                "received_monotonic_us": index * 1_000_000 - 100_000,
                "received_unix_us": 1_790_000_000_000_000 + index * 1_000_000 - 100_000,
                "timestamp_us": record["packet"]["time"], "width": 2, "height": 1,
                "file": relative,
            })
        self.make_associated()
        self.calls = []
        self.configurations = []
        self.responses = []

    @staticmethod
    def write_rows(path, rows):
        path.write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in rows),
                        encoding="utf-8")

    @staticmethod
    def read_rows(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def make_associated(self, *, receipt=False):
        self.write_rows(self.manifest, self.camera_records)
        if self.associated.exists():
            self.associated.unlink()
        settings = ["--allow-receipt-match", "--max-frame-age", "1"] if receipt else []
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = associate_replay([
                str(self.prepared), "--manifests", str(self.manifest),
                "--output", str(self.associated), *settings,
            ])
        self.assertIn(status, (0, 1))
        self.originals = self.read_rows(self.associated)

    @staticmethod
    def success(action="CONTINUE", *, reason="Visible evidence."):
        content = json.dumps({"action": action, "reason": reason})
        return OllamaResult("vision-model", "actual-vision-model", content, 0.125,
                            ModelDecision(action=action, reason=reason), None)

    @staticmethod
    def failure(category=OllamaErrorCategory.INVALID_DECISION):
        return OllamaResult("vision-model", "actual-vision-model", '{"action":"MONITOR"}',
                            0.75, None, OllamaError(category, "Recorded model failure.", 422))

    def fake_client(self, config):
        self.configurations.append(config)
        owner = self

        class Client:
            def chat(self, messages):
                owner.calls.append(messages)
                index = len(owner.calls) - 1
                response = owner.responses[index] if index < len(owner.responses) else owner.success()
                if isinstance(response, BaseException):
                    raise response
                return response

        return Client()

    def arguments(self, *settings, replay=None, output=None):
        return [str(replay or self.associated), "--base-url", "http://127.0.0.1:11434",
                "--model", "vision-model", "--output", str(output or self.output), *settings]

    def run_main(self, *settings, replay=None, output=None, collect=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("app.vlm_replay.OllamaClient", side_effect=self.fake_client) as factory:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(self.arguments(*settings, replay=replay, output=output))
        summary = json.loads(stdout.getvalue())
        target = output or self.output
        rows = self.read_rows(target) if collect and target.is_file() else []
        return status, summary, rows, stderr.getvalue(), factory

    def assert_preserved(self, rows, originals=None):
        originals = self.originals if originals is None else originals
        self.assertEqual(len(rows), len(originals))
        for original, actual in zip(originals, rows):
            self.assertEqual({key: value for key, value in actual.items() if key != "vlm_inference"},
                             original)
            self.assertEqual(actual["social_state_json"], original["social_state_json"])

    def test_all_four_actions_preserve_rows_order_and_independent_source_correlation(self):
        actions = ["YIELD", "CONTINUE", "APPROACH", "ENGAGE"]
        self.responses = [self.success(action) for action in actions]
        status, summary, rows, stderr, _ = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(summary["status"], "complete")
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["total_rows"], 4)
        self.assertEqual(summary["rows_written"], 4)
        self.assertEqual(summary["calls"], 4)
        self.assertEqual(summary["successes"], 4)
        self.assertEqual(summary["failures"], 0)
        self.assertEqual(summary["unavailable_inputs"], 0)
        self.assertEqual(summary["skipped_rows"], 0)
        self.assert_preserved(rows)
        for row, action, source_image in zip(rows, actions, self.images):
            inference = row["vlm_inference"]
            self.assertEqual(inference["schema_version"], 1)
            self.assertEqual(inference["status"], "succeeded")
            self.assertTrue(inference["ok"])
            self.assertEqual(inference["prompt_version"], PROMPT_VERSION)
            self.assertEqual(inference["decision"]["action"], action)
            self.assertEqual(inference["source_state_id"], row["source_state_id"])
            self.assertEqual(inference["session_id"], row["session_id"])
            self.assertEqual(inference["source_robot_timestamp_us"], row["source_robot_timestamp_us"])
            self.assertEqual(inference["original_capture_session"], "capture-a")
            self.assertEqual(inference["selected_frame_reference"], row["image_matching"]["frame"])
            self.assertEqual(inference["verified_image_sha256"], hashlib.sha256(source_image.read_bytes()).hexdigest())
            self.assertEqual(inference["requested_model"], "vision-model")
            self.assertEqual(inference["returned_model"], "actual-vision-model")
            self.assertEqual(inference["request_duration_s"], 0.125)
            self.assertIsNone(inference["error"])
            self.assertIsNone(inference["error_stage"])
            self.assertTrue(inference["completed_at"])
            self.assertEqual(inference["image_encoding"]["encoding_format"], "PNG")
            self.assertEqual(inference["image_encoding"]["width"], 2)
            self.assertEqual(inference["image_encoding"]["height"], 1)
            self.assertEqual(row["status"], "prepared")
            self.assertIsNone(row["decision"])

    def test_requests_have_static_text_and_exact_png_pixels_without_metadata(self):
        original_files = {path: path.read_bytes() for path in
                          [self.source, self.prepared, self.associated, self.manifest, *self.images]}
        status, _, rows, stderr, _ = self.run_main()
        self.assertEqual(status, 0, stderr)
        for messages, source_image, row in zip(self.calls, self.images, rows):
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0].role, "system")
            self.assertEqual(messages[0].content, SYSTEM_PROMPT)
            self.assertIsNone(messages[0].images)
            self.assertEqual(messages[1].role, "user")
            self.assertEqual(messages[1].content, USER_PROMPT)
            self.assertEqual(len(messages[1].images), 1)
            encoded = messages[1].images[0]
            png = base64.b64decode(encoded, validate=True)
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(io.BytesIO(png)) as decoded:
                self.assertEqual(decoded.format, "PNG")
                self.assertEqual(decoded.mode, "RGB")
                self.assertEqual(decoded.size, (2, 1))
                self.assertEqual(decoded.tobytes(), source_image.read_bytes().split(b"255\n", 1)[1])
            self.assertNotIn(encoded, json.dumps(row))
            contents = messages[0].content + messages[1].content
            for secret in (row["social_state_json"], row["source_state_id"], "capture-a",
                           str(self.source), str(self.manifest), str(row["source_robot_timestamp_us"]),
                           "original-llm-model"):
                self.assertNotIn(secret, contents)
        for path, original in original_files.items():
            self.assertEqual(path.read_bytes(), original)

    def test_caller_selected_model_and_generation_configuration_are_recorded(self):
        status, _, rows, stderr, _ = self.run_main(
            "--timeout", "2.5", "--temperature", "0.2", "--seed", "73", "--num-predict", "128")
        self.assertEqual(status, 0, stderr)
        config = self.configurations[0]
        self.assertEqual(config.timeout_seconds, 2.5)
        self.assertEqual(config.temperature, .2)
        self.assertEqual(config.seed, 73)
        self.assertEqual(config.num_predict, 128)
        for row in rows:
            self.assertEqual(row["vlm_inference"]["ollama_configuration"], {
                **config.model_dump(mode="json"), "generation_options": config.generation_options(),
            })

    def test_existing_llm_decisions_errors_and_preparation_fields_are_unchanged(self):
        rows = deepcopy(self.originals)
        rows[0].update(status="succeeded", ok=True,
                       decision={"action": "YIELD", "reason": "Existing LLM evidence."},
                       raw_content='{"action":"YIELD","reason":"Existing LLM evidence."}',
                       returned_model="original-llm-model", request_duration_s=.5)
        rows[1].update(status="failed", ok=False, decision=None,
                       error={"category": "timeout", "message": "Prior request timed out.", "http_status": None},
                       raw_content=None, request_duration_s=3.0)
        self.write_rows(self.associated, rows)
        status, _, actual, stderr, _ = self.run_main()
        self.assertEqual(status, 0, stderr)
        self.assert_preserved(actual, rows)
        self.assertEqual(actual[0]["decision"]["action"], "YIELD")
        self.assertEqual(actual[0]["vlm_inference"]["decision"]["action"], "CONTINUE")
        self.assertFalse(actual[1]["ok"])
        self.assertTrue(actual[1]["vlm_inference"]["ok"])

    def test_all_original_ollama_error_categories_survive_without_fallback(self):
        for category in OllamaErrorCategory:
            with self.subTest(category=category):
                self.calls.clear()
                self.configurations.clear()
                self.responses = [self.failure(category)]
                status, summary, rows, _, _ = self.run_main(
                    "--max-calls", "1", output=self.root / f"failure-{category.value}.jsonl")
                self.assertEqual(status, 1)
                self.assertEqual(summary["status"], "complete_with_unsuccessful_rows")
                self.assertTrue(summary["complete"])
                self.assertEqual(summary["calls"], 1)
                self.assertEqual(summary["inference_failures"], 1)
                self.assertEqual(summary["failures"], 1)
                self.assertEqual(summary["skipped_rows"], 3)
                self.assert_preserved(rows)
                result = rows[0]["vlm_inference"]
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["ok"])
                self.assertIsNone(result["decision"])
                self.assertEqual(result["error"], {"category": category.value,
                                                 "message": "Recorded model failure.", "http_status": 422})
                self.assertEqual(result["error_stage"], "ollama")
                self.assertEqual(result["raw_content"], '{"action":"MONITOR"}')
                self.assertEqual(result["request_duration_s"], .75)

    def test_unmatched_rows_make_no_calls_and_remain_available_for_audit(self):
        for record in self.camera_records:
            record["timestamp_us"] += 1
        self.make_associated()
        status, summary, rows, _, factory = self.run_main()
        self.assertEqual(status, 1)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["calls"], 0)
        self.assertEqual(summary["unavailable_inputs"], 4)
        self.assertEqual(summary["failures"], 0)
        factory.assert_not_called()
        self.assert_preserved(rows)
        for row in rows:
            result = row["vlm_inference"]
            self.assertEqual(result["status"], "input_unavailable")
            self.assertIsNone(result["ok"])
            self.assertIsNone(result["decision"])
            self.assertIsNone(result["request_duration_s"])
            self.assertEqual(result["error_stage"], "image_input")
            self.assertEqual(result["error"]["category"], "matching_unavailable")
            self.assertIsNone(result["selected_frame_reference"])

    def test_changed_missing_and_invalid_images_are_separate_input_failures(self):
        self.images[0].unlink()
        self.images[1].write_bytes(b"P6\n2 1\n255\n" + b"\xff" * 6)
        self.images[2].write_bytes(b"not an image")
        self.images[3].write_bytes(b"P6\n1 1\n255\n\x00\x01\x02")
        status, summary, rows, _, factory = self.run_main()
        self.assertEqual(status, 1)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["calls"], 0)
        self.assertEqual(summary["invalid_inputs"], 4)
        self.assertEqual(summary["inference_failures"], 0)
        self.assertEqual(summary["failures"], 4)
        factory.assert_not_called()
        self.assert_preserved(rows)
        categories = []
        for row in rows:
            result = row["vlm_inference"]
            self.assertEqual(result["status"], "input_invalid")
            self.assertIsNone(result["ok"])
            self.assertEqual(result["error_stage"], "image_input")
            self.assertIsNone(result["decision"])
            self.assertIsNone(result["raw_content"])
            self.assertIsNone(result["request_duration_s"])
            self.assertIsNone(result["verified_image_sha256"])
            categories.append(result["error"]["category"])
        self.assertIn("missing_image", categories)
        self.assertIn("invalid_image", categories)
        self.assertIn("image_dimension_mismatch", categories)

    def test_selected_image_symlink_loop_is_one_row_input_failure_without_source_mutation(self):
        unchanged = {path: path.read_bytes() for path in
                     [self.source, self.prepared, self.associated, self.manifest, *self.images[1:]]}
        damaged_image = self.images[0]
        damaged_image.unlink()
        damaged_image.symlink_to(damaged_image.name)
        status, summary, rows, _, _ = self.run_main()
        self.assertEqual(status, 1)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["calls"], 3)
        self.assertEqual(summary["invalid_inputs"], 1)
        self.assertEqual(summary["successes"], 3)
        self.assertEqual(summary["rows_written"], 4)
        self.assertEqual([row["vlm_inference"]["status"] for row in rows],
                         ["input_invalid", "succeeded", "succeeded", "succeeded"])
        self.assertEqual(rows[0]["vlm_inference"]["error_stage"], "image_input")
        self.assert_preserved(rows)
        self.assertTrue(damaged_image.is_symlink())
        self.assertEqual(damaged_image.readlink(), Path(damaged_image.name))
        for path, original in unchanged.items():
            self.assertEqual(path.read_bytes(), original)

    def test_call_cap_counts_failed_requests_but_not_unavailable_or_invalid_images(self):
        self.camera_records[0]["timestamp_us"] += 1
        self.make_associated()
        self.images[1].unlink()
        self.responses = [self.failure(OllamaErrorCategory.TIMEOUT)]
        status, summary, rows, _, _ = self.run_main("--max-calls", "1")
        self.assertEqual(status, 1)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["calls"], 1)
        self.assertEqual(summary["unavailable_inputs"], 1)
        self.assertEqual(summary["invalid_inputs"], 1)
        self.assertEqual(summary["inference_failures"], 1)
        self.assertEqual(summary["failures"], 2)
        self.assertEqual(summary["skipped_rows"], 1)
        self.assertEqual([row["vlm_inference"]["status"] for row in rows],
                         ["input_unavailable", "input_invalid", "failed", "not_run_limit"])
        self.assert_preserved(rows)
        skipped = rows[-1]["vlm_inference"]
        self.assertIsNone(skipped["ok"])
        self.assertIsNone(skipped["decision"])
        self.assertIsNone(skipped["error"])
        self.assertIsNone(skipped["request_duration_s"])
        self.assertEqual(skipped["not_run_reason"], "call_limit")

    def test_no_new_sampling_or_rematching_and_limit_preserves_all_rows(self):
        with patch("app.image_matching.CameraMatcher.match", side_effect=AssertionError("no rematching")):
            with patch("app.llm_replay_inputs.iter_replay_states", side_effect=AssertionError("no estimation")):
                status, summary, rows, _, _ = self.run_main("--max-calls", "2")
        self.assertEqual(status, 1)
        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["skipped_rows"], 2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([row["vlm_inference"]["status"] for row in rows],
                         ["succeeded", "succeeded", "not_run_limit", "not_run_limit"])
        self.assert_preserved(rows)

    def test_approximate_match_provenance_and_signed_difference_are_not_changed(self):
        self.camera_records[1]["timestamp_us"] += 1
        self.make_associated(receipt=True)
        status, _, rows, stderr, _ = self.run_main()
        self.assertEqual(status, 0, stderr)
        matching = rows[1]["image_matching"]
        result = rows[1]["vlm_inference"]
        self.assertEqual(matching["method"], "reception_monotonic")
        self.assertEqual(matching["time_difference"]["value"], -100_000)
        self.assertEqual(result["matching_method"], matching["method"])
        self.assertEqual(result["matching_time_difference"], matching["time_difference"])
        self.assertEqual(result["matching_limitations"], matching["limitations"])
        self.assert_preserved(rows)

    def test_malformed_first_and_later_rows_fail_preflight_before_output_or_calls(self):
        valid = json.dumps(self.originals[0]) + "\n"
        for index, content in enumerate(('[]\n', valid + '{"broken":\n',
                                         '{"key":1,"key":2}\n', '{"bad":NaN}\n')):
            with self.subTest(content=content):
                self.associated.write_text(content, encoding="utf-8")
                target = self.root / f"malformed-{index}.jsonl"
                status, summary, rows, _, factory = self.run_main(output=target)
                self.assertEqual(status, 2)
                self.assertEqual(summary["status"], "failed")
                self.assertFalse(summary["complete"])
                self.assertEqual(summary["calls"], 0)
                self.assertEqual(summary["rows_written"], 0)
                self.assertFalse(target.exists())
                self.assertEqual(rows, [])
                self.assertIn(str(self.associated), summary["error"]["message"])
                factory.assert_not_called()

    def test_missing_matching_invalid_source_correlation_and_previous_vlm_are_rejected(self):
        mutations = {
            "missing_matching": lambda row: row.pop("image_matching"),
            "invalid_state_id": lambda row: row.update(source_state_id="wrong-state"),
            "previous_vlm": lambda row: row.update(vlm_inference={"status": "succeeded"}),
            "bad_matching": lambda row: row["image_matching"].update(ok=False),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                rows = deepcopy(self.originals)
                mutate(rows[1])
                self.write_rows(self.associated, rows)
                target = self.root / f"invalid-{name}.jsonl"
                status, summary, actual, _, factory = self.run_main(output=target)
                self.assertEqual(status, 2)
                self.assertFalse(summary["complete"])
                self.assertEqual(summary["rows_written"], 0)
                self.assertFalse(target.exists())
                self.assertEqual(actual, [])
                self.assertIn(str(self.associated) + ":2", summary["error"]["message"])
                factory.assert_not_called()

    def test_empty_input_is_failed_without_calls_or_output(self):
        self.associated.write_text("\n \n", encoding="utf-8")
        status, summary, rows, _, factory = self.run_main()
        self.assertEqual(status, 2)
        self.assertEqual(summary["status"], "failed")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["calls"], 0)
        self.assertEqual(summary["rows_written"], 0)
        self.assertEqual(rows, [])
        self.assertFalse(self.output.exists())
        factory.assert_not_called()

    def test_invalid_configuration_and_nonpositive_caps_do_not_create_output(self):
        invalid = [("--max-calls", "0"), ("--max-calls", "-1"),
                   ("--timeout", "0"), ("--timeout", "nan"),
                   ("--temperature", "-1"), ("--num-predict", "0"),
                   ("--base-url", "file:///tmp/model"), ("--model", " ")]
        for index, settings in enumerate(invalid):
            with self.subTest(settings=settings):
                target = self.root / f"invalid-settings-{index}.jsonl"
                status, summary, _, _, factory = self.run_main(*settings, output=target)
                self.assertEqual(status, 2)
                self.assertFalse(summary["complete"])
                self.assertEqual(summary["calls"], 0)
                self.assertFalse(target.exists())
                factory.assert_not_called()

    def test_existing_output_symlink_and_all_referenced_sources_are_protected(self):
        self.output.write_text('{"previous":"result"}\n', encoding="utf-8")
        paths = [self.output, self.associated, self.prepared, self.source, self.manifest, *self.images]
        for index, path in enumerate(paths):
            original = path.read_bytes()
            alias = self.root / f"protected-alias-{index}"
            alias.symlink_to(path)
            for target in (path, alias):
                with self.subTest(target=target):
                    status, summary, _, _, factory = self.run_main(output=target, collect=False)
                    self.assertEqual(status, 2)
                    self.assertFalse(summary["complete"])
                    self.assertEqual(summary["rows_written"], 0)
                    self.assertEqual(path.read_bytes(), original)
                    factory.assert_not_called()

    def test_missing_source_manifest_and_later_image_references_cannot_be_created_as_output(self):
        paths = [self.source, self.manifest, self.images[-1]]
        originals = {path: path.read_bytes() for path in paths}
        for path in paths:
            with self.subTest(path=path):
                path.unlink()
                status, summary, _, _, factory = self.run_main(output=path, collect=False)
                self.assertEqual(status, 2)
                self.assertFalse(summary["complete"])
                self.assertEqual(summary["calls"], 0)
                self.assertEqual(summary["rows_written"], 0)
                self.assertFalse(path.exists())
                factory.assert_not_called()
                path.write_bytes(originals[path])

    def test_output_parent_creation_cannot_replace_missing_protected_files_with_directories(self):
        paths = [self.source, self.manifest, self.images[-1]]
        for index, path in enumerate(paths):
            original = path.read_bytes()
            path.unlink()
            alias = self.root / f"missing-source-parent-alias-{index}"
            alias.symlink_to(path, target_is_directory=True)
            for parent in (path, alias):
                with self.subTest(path=path, parent=parent):
                    target = parent / "new-directory" / "results.jsonl"
                    status, summary, _, _, factory = self.run_main(output=target, collect=False)
                    self.assertEqual(status, 2)
                    self.assertFalse(summary["complete"])
                    self.assertEqual(summary["calls"], 0)
                    self.assertEqual(summary["rows_written"], 0)
                    self.assertFalse(path.exists())
                    self.assertFalse(target.exists())
                    self.assertTrue(alias.is_symlink())
                    self.assertEqual(alias.readlink(), path)
                    factory.assert_not_called()
            path.write_bytes(original)

    def test_missing_unmatched_candidate_image_is_protected_before_valid_row_calls(self):
        missing_image = self.images[0]
        missing_image.unlink()
        self.make_associated()
        self.assertFalse(self.originals[0]["image_matching"]["ok"])
        self.assertIsNone(self.originals[0]["image_matching"]["frame"])
        self.assertIn("candidate", self.originals[0]["image_matching"]["error"]["details"])
        status, summary, _, _, factory = self.run_main(output=missing_image, collect=False)
        self.assertEqual(status, 2)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["calls"], 0)
        self.assertEqual(summary["rows_written"], 0)
        self.assertFalse(missing_image.exists())
        factory.assert_not_called()

    def test_missing_unmatched_candidate_is_protected_when_another_image_path_cannot_resolve(self):
        missing_image = self.images[0]
        missing_image.unlink()
        self.make_associated()
        loop = self.images[1]
        loop.unlink()
        loop.symlink_to(loop.name)
        original_metadata = {path: path.read_bytes() for path in
                             [self.source, self.prepared, self.associated, self.manifest]}
        status, summary, _, _, factory = self.run_main(output=missing_image, collect=False)
        self.assertEqual(status, 2)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["calls"], 0)
        self.assertEqual(summary["rows_written"], 0)
        self.assertFalse(missing_image.exists())
        self.assertTrue(loop.is_symlink())
        self.assertEqual(loop.readlink(), Path(loop.name))
        factory.assert_not_called()
        for path, original in original_metadata.items():
            self.assertEqual(path.read_bytes(), original)

    def test_output_symlink_loop_is_failed_and_preserved(self):
        self.output.symlink_to(self.output.name)
        status, summary, _, _, factory = self.run_main(collect=False)
        self.assertEqual(status, 2)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["rows_written"], 0)
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(self.output.readlink(), Path(self.output.name))
        factory.assert_not_called()

    def test_first_write_failure_is_non_success_and_surfaces_output_error(self):
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

        with patch("app.vlm_replay.Path.open", new=open_path):
            status, summary, rows, _, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["rows_written"], 0)
        self.assertEqual(rows, [])
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
                    raise OSError("failed after first VLM row")
                return self.stream.write(text)

            def flush(self):
                self.stream.flush()

        def open_path(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path == self.output and args and args[0] == "x":
                return OneRowWriter(stream)
            return stream

        with patch("app.vlm_replay.Path.open", new=open_path):
            status, summary, rows, _, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertEqual(summary["status"], "partial")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["rows_written"], 1)
        self.assertEqual(summary["error"]["stage"], "output")
        self.assertIn("failed after first VLM row", summary["error"]["message"])
        self.assert_preserved(rows, self.originals[:1])

    def test_interrupted_second_inference_preserves_first_row_and_reports_partial(self):
        self.responses = [self.success(), KeyboardInterrupt()]
        status, summary, rows, _, _ = self.run_main()
        self.assertEqual(status, 2)
        self.assertEqual(summary["status"], "partial")
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["rows_written"], 1)
        self.assertEqual(summary["successes"], 1)
        self.assertEqual(summary["error"]["stage"], "inference")
        self.assertEqual(summary["error"]["category"], "KeyboardInterrupt")
        self.assertEqual(summary["error"]["message"], "interrupted")
        self.assert_preserved(rows, self.originals[:1])


if __name__ == "__main__":
    unittest.main()
