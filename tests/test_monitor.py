"""Tests for pipeline tracing, local recording, and isolated replay."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from app.adapters.synthetic import SyntheticObservationAdapter, museum_guide_scenarios
from app.monitor import DeterministicReplayLLM, MonitorService
from app.server import ObservationPipeline, create_app


class DebugSnapshotStub:
    def __init__(self, payload):
        self.payload = deepcopy(payload)

    def model_dump(self, *, mode):
        self.assert_json_mode(mode)
        return deepcopy(self.payload)

    @staticmethod
    def assert_json_mode(mode):
        if mode != "json":
            raise AssertionError(f"unexpected model_dump mode {mode!r}")


def with_debug_snapshot(
    result,
    *,
    source_id,
    request_id,
    requested_at,
    status="COMPLETED",
):
    payload = {
        "request_id": request_id,
        "status": status,
        "source_id": source_id,
        "observation_id": result.state.source_observation_id,
        "social_state_id": result.state.state_id,
        "state_timestamp_us": result.state.timestamp_us,
        "clock_domain": result.state.clock_domain,
        "raw_social_state": result.state.model_dump(mode="json"),
        "rendered_messages": [],
        "request_parameters": {},
        "raw_response": "{}",
        "validated_response": None,
        "behavior_intent": None,
        "metadata": {"requested_at": requested_at},
        "error": None,
    }
    return replace(result, debug_snapshot=DebugSnapshotStub(payload))


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

    def test_latest_debug_snapshot_survives_a_skipped_cycle(self):
        adapter = SyntheticObservationAdapter(
            museum_guide_scenarios()["newcomer_requests_guidance"]
        )
        samples = iter(adapter.iter_samples())
        pipeline = ObservationPipeline(DeterministicReplayLLM())

        first = next(samples).observation
        first_result = pipeline.process(first)
        first_result = with_debug_snapshot(
            first_result,
            source_id=first.capabilities.adapter_id,
            request_id="request-1",
            requested_at="2026-09-15T00:00:00.000+00:00",
        )
        self.monitor.observe_live(first, first_result, [])
        retained = self.monitor.get_debug_snapshot(first.capabilities.adapter_id)

        second = next(samples).observation
        second_result = pipeline.process(second)
        self.assertFalse(second_result.decision_triggered)
        self.monitor.observe_live(second, second_result, [])
        after_skip = self.monitor.get_debug_snapshot(first.capabilities.adapter_id)

        self.assertEqual(retained, after_skip)
        self.assertEqual(after_skip["revision"], 1)
        self.assertEqual(after_skip["snapshot"]["request_id"], "request-1")
        source = self.monitor.list_sources()[0]
        self.assertEqual(source["latest_debug_request_id"], "request-1")
        self.assertEqual(source["debug_snapshot_revision"], 1)
        self.assertNotIn("latest_debug_snapshot", source)
        debug_events = [
            event
            for event in self.monitor.events_after(0, timeout_s=0)
            if event["type"] == "debug.snapshot.updated"
        ]
        self.assertEqual(len(debug_events), 1)
        self.assertEqual(debug_events[0]["data"]["revision"], 1)

    def test_older_completion_cannot_replace_newer_debug_snapshot(self):
        adapter = SyntheticObservationAdapter(
            museum_guide_scenarios()["newcomer_requests_guidance"]
        )
        samples = iter(adapter.iter_samples())
        pipeline = ObservationPipeline(DeterministicReplayLLM())
        older = next(samples).observation
        older_result = pipeline.process(older)
        newer = next(samples).observation
        newer_result = pipeline.process(newer)
        newer_result = with_debug_snapshot(
            newer_result,
            source_id=newer.capabilities.adapter_id,
            request_id="request-newer",
            requested_at="2026-09-15T00:00:00.200+00:00",
        )
        older_result = with_debug_snapshot(
            older_result,
            source_id=older.capabilities.adapter_id,
            request_id="request-older",
            requested_at="2026-09-15T00:00:00.100+00:00",
        )

        self.monitor.observe_live(newer, newer_result, [])
        self.monitor.observe_live(older, older_result, [])

        retained = self.monitor.get_debug_snapshot(newer.capabilities.adapter_id)
        self.assertEqual(retained["revision"], 1)
        self.assertEqual(retained["snapshot"]["request_id"], "request-newer")
        debug_events = [
            event
            for event in self.monitor.events_after(0, timeout_s=0)
            if event["type"] == "debug.snapshot.updated"
        ]
        self.assertEqual(len(debug_events), 1)

    def test_newer_failed_snapshot_is_retained_atomically(self):
        adapter = SyntheticObservationAdapter(
            museum_guide_scenarios()["newcomer_requests_guidance"]
        )
        samples = iter(adapter.iter_samples())
        pipeline = ObservationPipeline(DeterministicReplayLLM())
        first = next(samples).observation
        first_result = with_debug_snapshot(
            pipeline.process(first),
            source_id=first.capabilities.adapter_id,
            request_id="request-success",
            requested_at="2026-09-15T00:00:00.000+00:00",
        )
        second = next(samples).observation
        second_result = with_debug_snapshot(
            pipeline.process(second),
            source_id=second.capabilities.adapter_id,
            request_id="request-failed",
            requested_at="2026-09-15T00:00:00.100+00:00",
            status="FAILED",
        )

        self.monitor.observe_live(first, first_result, [])
        self.monitor.observe_live(second, second_result, [])
        retained = self.monitor.get_debug_snapshot(first.capabilities.adapter_id)
        retained["snapshot"]["request_id"] = "mutated-copy"
        unchanged = self.monitor.get_debug_snapshot(first.capabilities.adapter_id)

        self.assertEqual(unchanged["revision"], 2)
        self.assertEqual(unchanged["snapshot"]["request_id"], "request-failed")
        self.assertEqual(unchanged["snapshot"]["status"], "FAILED")

    def test_debug_snapshots_are_isolated_by_source(self):
        adapter = SyntheticObservationAdapter(
            museum_guide_scenarios()["newcomer_requests_guidance"]
        )
        observation_a = next(iter(adapter.iter_samples())).observation
        result = ObservationPipeline(DeterministicReplayLLM()).process(observation_a)
        result_a = with_debug_snapshot(
            result,
            source_id="source-a",
            request_id="request-a",
            requested_at="2026-09-15T00:00:00.000+00:00",
        )
        capabilities_b = observation_a.capabilities.model_copy(
            update={"adapter_id": "source-b"}
        )
        observation_a = observation_a.model_copy(
            update={"capabilities": observation_a.capabilities.model_copy(
                update={"adapter_id": "source-a"}
            )}
        )
        observation_b = observation_a.model_copy(update={"capabilities": capabilities_b})
        result_b = with_debug_snapshot(
            result,
            source_id="source-b",
            request_id="request-b",
            requested_at="2026-09-15T00:00:00.000+00:00",
        )

        self.monitor.observe_live(observation_a, result_a, [])
        self.monitor.observe_live(observation_b, result_b, [])

        self.assertEqual(
            self.monitor.get_debug_snapshot("source-a")["snapshot"]["request_id"],
            "request-a",
        )
        self.assertEqual(
            self.monitor.get_debug_snapshot("source-b")["snapshot"]["request_id"],
            "request-b",
        )


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

    def test_debug_snapshot_endpoint_handles_missing_and_unknown_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monitor = MonitorService(
                project_root=root,
                pipeline_factory=lambda mode: ObservationPipeline(
                    DeterministicReplayLLM()
                ),
            )
            client = create_app(
                llm=DeterministicReplayLLM(), monitor_service=monitor
            ).test_client()
            adapter = SyntheticObservationAdapter(
                museum_guide_scenarios()["newcomer_requests_guidance"]
            )
            observation = next(iter(adapter.iter_samples())).observation
            result = ObservationPipeline(DeterministicReplayLLM()).process(
                observation
            )
            monitor.observe_live(observation, result, [])
            source_id = observation.capabilities.adapter_id

            missing = client.get(
                f"/api/v1/monitor/sources/{source_id}/debug-snapshot"
            )
            unknown = client.get(
                "/api/v1/monitor/sources/unknown/debug-snapshot"
            )

            self.assertEqual(missing.status_code, 200)
            self.assertEqual(
                missing.get_json(),
                {"source_id": source_id, "revision": 0, "snapshot": None},
            )
            self.assertEqual(missing.headers["Cache-Control"], "no-store")
            self.assertEqual(missing.headers["X-Debug-Snapshot-Revision"], "0")
            self.assertEqual(unknown.status_code, 404)
            self.assertEqual(unknown.get_json()["error"]["code"], "unknown_source")


if __name__ == "__main__":
    unittest.main()
