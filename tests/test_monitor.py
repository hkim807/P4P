"""Tests for pipeline tracing, local recording, and isolated replay."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.adapters.synthetic import SyntheticObservationAdapter, museum_guide_scenarios
from app.monitor import DeterministicReplayLLM, MonitorService
from app.server import ObservationPipeline, create_app


class MonitorServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        recording = self.root / "recordings" / "synthetic" / "guidance.jsonl"
        SyntheticObservationAdapter(
            museum_guide_scenarios()["newcomer_requests_guidance"]
        ).write_jsonl(recording)
        self.monitor = MonitorService(
            project_root=self.root,
            pipeline_factory=lambda mode: ObservationPipeline(
                DeterministicReplayLLM()
            ),
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_replay_emits_every_pipeline_stage_and_semantic_changes(self):
        run = self.monitor.create_replay("synthetic/guidance.jsonl")
        stepped = self.monitor.step_replay(run["id"], 15)

        self.assertEqual(stepped["processed_count"], 15)
        self.assertEqual(
            [stage["stage"] for stage in stepped["current_cycle"]["stages"]],
            ["observation", "validation", "state", "scheduler", "policy", "intent"],
        )
        self.assertTrue(stepped["current_cycle"]["changes"])

    def test_fresh_replay_contexts_are_deterministic_and_isolated(self):
        first = self.monitor.create_replay("synthetic/guidance.jsonl")
        second = self.monitor.create_replay("synthetic/guidance.jsonl")

        first_result = self.monitor.step_replay(first["id"], 10)
        second_result = self.monitor.step_replay(second["id"], 10)

        self.assertEqual(
            first_result["current_cycle"]["social_state"],
            second_result["current_cycle"]["social_state"],
        )
        self.assertEqual(
            first_result["current_cycle"]["scheduler"],
            second_result["current_cycle"]["scheduler"],
        )

    def test_recording_path_cannot_escape_recordings_root(self):
        with self.assertRaises(ValueError):
            self.monitor.create_replay("../../outside.jsonl")

    def test_live_recording_persists_observation_and_trace(self):
        adapter = SyntheticObservationAdapter(
            museum_guide_scenarios()["newcomer_requests_guidance"]
        )
        samples = iter(adapter.iter_samples())
        pipeline = ObservationPipeline(DeterministicReplayLLM())

        first = next(samples).observation
        first_stages = []
        first_result = pipeline.process(first, trace=first_stages.append)
        self.monitor.observe_live(first, first_result, first_stages)
        recording = self.monitor.start_recording(first.capabilities.adapter_id)

        second = next(samples).observation
        second_stages = []
        second_result = pipeline.process(second, trace=second_stages.append)
        self.monitor.observe_live(second, second_result, second_stages)
        stopped = self.monitor.stop_recording(recording["id"])

        run_directory = self.root / "var" / "recordings" / stopped["id"]
        observation_lines = (run_directory / "observations.jsonl").read_text().splitlines()
        trace_lines = (run_directory / "trace.jsonl").read_text().splitlines()
        manifest = json.loads((run_directory / "manifest.json").read_text())
        self.assertEqual(len(observation_lines), 1)
        self.assertEqual(len(trace_lines), 1)
        self.assertEqual(manifest["frames"], 1)
        self.assertTrue(manifest["complete"])


class MonitorEndpointTests(unittest.TestCase):
    def test_bootstrap_and_replay_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recording = root / "recordings" / "synthetic" / "guidance.jsonl"
            SyntheticObservationAdapter(
                museum_guide_scenarios()["newcomer_requests_guidance"]
            ).write_jsonl(recording)
            monitor = MonitorService(
                project_root=root,
                pipeline_factory=lambda mode: ObservationPipeline(
                    DeterministicReplayLLM()
                ),
            )
            client = create_app(
                llm=DeterministicReplayLLM(), monitor_service=monitor
            ).test_client()

            bootstrap = client.get("/api/v1/monitor/bootstrap")
            self.assertEqual(bootstrap.status_code, 200)
            self.assertEqual(len(bootstrap.get_json()["recordings"]), 1)

            created = client.post(
                "/api/v1/monitor/replays",
                json={"recording_id": "synthetic/guidance.jsonl"},
            )
            self.assertEqual(created.status_code, 201)
            run_id = created.get_json()["id"]
            stepped = client.post(
                f"/api/v1/monitor/replays/{run_id}/step", json={"count": 2}
            )
            self.assertEqual(stepped.status_code, 200)
            self.assertEqual(stepped.get_json()["processed_count"], 2)


if __name__ == "__main__":
    unittest.main()
