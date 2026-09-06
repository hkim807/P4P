"""Tests for validated streaming JSONL observation input."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.adapters.jsonl import JsonlObservationError, JsonlObservationIterator
from app.adapters.synthetic import SyntheticObservationAdapter, museum_guide_scenarios
from app.domain.models import ObservationFrame


class JsonlObservationIteratorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "observations.jsonl"
        scenario = museum_guide_scenarios()["newcomer_passes_without_engaging"]
        self.synthetic = SyntheticObservationAdapter(scenario)

    def test_iterates_validated_observations_without_ground_truth(self):
        self.synthetic.write_jsonl(self.path)

        observations = list(JsonlObservationIterator(self.path))

        self.assertEqual(len(observations), self.synthetic.frame_count)
        self.assertTrue(all(isinstance(item, ObservationFrame) for item in observations))
        self.assertEqual(observations[0].observation_id, "newcomer_passes_without_engaging:00000")

    def test_record_iterator_keeps_ground_truth_separate(self):
        self.synthetic.write_jsonl(self.path)

        first = next(JsonlObservationIterator(self.path).iter_records())

        self.assertEqual(first.line_number, 1)
        self.assertEqual(
            first.ground_truth["scenario_id"],
            "newcomer_passes_without_engaging",
        )
        self.assertFalse(hasattr(first.observation, "ground_truth"))

    def test_accepts_an_observation_only_recording(self):
        self.synthetic.write_jsonl(self.path, include_ground_truth=False)

        first = next(JsonlObservationIterator(self.path).iter_records())

        self.assertIsNone(first.ground_truth)

    def test_can_require_ground_truth_for_evaluation(self):
        self.synthetic.write_jsonl(self.path, include_ground_truth=False)

        with self.assertRaisesRegex(JsonlObservationError, "missing required 'ground_truth'"):
            next(
                JsonlObservationIterator(
                    self.path, require_ground_truth=True
                ).iter_records()
            )

    def test_rejects_malformed_json_with_a_line_number(self):
        self.path.write_text('{"observation":\n', encoding="utf-8")

        with self.assertRaisesRegex(
            JsonlObservationError, r"observations\.jsonl:1: invalid JSON"
        ):
            list(JsonlObservationIterator(self.path))

    def test_rejects_an_invalid_observation(self):
        self.path.write_text(
            json.dumps({"observation": {"schema_version": "1.0"}}) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(JsonlObservationError, "invalid ObservationFrame"):
            list(JsonlObservationIterator(self.path))

    def test_rejects_non_monotonic_timestamps(self):
        samples = list(self.synthetic.iter_samples())[:2]
        records = [
            {"observation": sample.observation.model_dump(mode="json")}
            for sample in reversed(samples)
        ]
        self.path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(JsonlObservationError, "must be greater"):
            list(JsonlObservationIterator(self.path))

    def test_rejects_an_empty_recording(self):
        self.path.write_text("\n", encoding="utf-8")

        with self.assertRaisesRegex(JsonlObservationError, "contains no observation"):
            list(JsonlObservationIterator(self.path))

    def test_preserves_additional_envelope_metadata(self):
        sample = self.synthetic.sample_at(0.0)
        record = {
            "observation": sample.observation.model_dump(mode="json"),
            "experiment_id": "dry-run-1",
        }
        self.path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        parsed = next(JsonlObservationIterator(self.path).iter_records())

        self.assertEqual(parsed.metadata, {"experiment_id": "dry-run-1"})


if __name__ == "__main__":
    unittest.main()
