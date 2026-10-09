"""The replay must stop at output, preserve live timing, and expose stale delivery."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.domain.model_decision import ModelDecision
from app.inference.ollama import OllamaResult
from app.replay.llm_inputs import ReplayState
from scripts.replay_first_action import replay
from tests.fixtures import frame


def item(index, *, people=True):
    raw = frame()
    raw['timestamp'] += index * 100_000
    raw['people'] = [{'uid': 17, 'distance_m': .5, 'gaze_overlap': .95,
                      'path_relation': 'CONFLICT'}] if people else []
    capture = {'capture_version': 1, 'session_id': 'test', 'stream': 'perception',
        'sequence': index + 1, 'received_monotonic_us': raw['timestamp'],
        'received_unix_us': 2_000_000 + index * 100_000,
        'packet': {'time': index, 'persons': [{'uid': 17, 'dist_mm': 500, 'gaze_overlap': .95}] if people else []}}
    return ReplayState(SimpleNamespace(session_id='test'), {
        'reconstructed_raw_observation': raw, 'source_record': capture,
        'line_number': index + 1, 'file_sha256': 'synthetic'}, {})


class FirstActionReplayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.settings = json.loads(Path('config/policy-comparison.json').read_text())
        self.settings['base_url'] = 'http://127.0.0.1:11434'
        self.scene = {'scenario_id': 'test', 'recording': 'unused'}

    def test_stops_without_reading_the_next_observation(self):
        def observations():
            yield item(0)
            self.fail('Source advanced after first action')
        result = replay(self.scene, 'rules', self.root / 'stop', self.settings,
                        iterator=observations())
        self.assertEqual(result['action'], 'YIELD')
        self.assertTrue(result['accepted_by_live_client'])
        self.assertEqual(result['frames_ingested'], 1)
        self.assertFalse(result['recording_exhausted'])

    def test_recording_end_is_explicit_without_an_empty_scene_fallback(self):
        clock = [0.0]
        def sleep(seconds):
            clock[0] += seconds
        result = replay(self.scene, 'rules', self.root / 'empty', self.settings,
            iterator=[item(i, people=False) for i in range(4)],
            now=lambda: clock[0], sleep=sleep)
        self.assertEqual(result['status'], 'RECORDING_ENDED_NO_ACTION')
        self.assertIsNone(result['action'])
        self.assertEqual(result['readiness_counts'], {'NO_VISIBLE_PERSON': 4})

    def model_patch(self, delay):
        class Client:
            def __init__(self, config):
                self.config = config
            def chat(self, messages):
                time.sleep(delay)
                decision = ModelDecision(action='YIELD', reason='An upstream observation reports a path conflict.')
                return OllamaResult(self.config.model, self.config.model,
                                    decision.model_dump_json(), delay, decision, None)
        return patch('app.inference.live.OllamaClient', Client)

    def test_new_observations_continue_during_inference_then_stop(self):
        with self.model_patch(.25):
            result = replay(self.scene, 'llm', self.root / 'async', self.settings,
                            iterator=[item(i) for i in range(12)])
        self.assertEqual(result['action'], 'YIELD')
        self.assertTrue(result['accepted_by_live_client'])
        self.assertGreater(result['frames_ingested'], 1)
        self.assertLess(result['frames_ingested'], 12)
        self.assertEqual(result['decision_source_offset_s'], 0.0)

    def test_output_after_eof_is_resolved_but_current_evidence_is_stale(self):
        with self.model_patch(1.1):
            result = replay(self.scene, 'llm', self.root / 'stale', self.settings,
                            iterator=[item(0)])
        self.assertEqual(result['status'], 'ACTION_RESOLVED')
        self.assertEqual(result['action'], 'YIELD')
        self.assertFalse(result['accepted_by_live_client'])
        self.assertGreater(result['acceptance_details']['latest_observation_age_s'], 1.0)
        self.assertTrue(result['recording_exhausted'])

    def test_waits_for_first_policy_output_even_when_delivery_has_expired(self):
        settings = {**self.settings, 'model_trial_max_age_s': .1}
        with self.model_patch(.25):
            result = replay(self.scene, 'llm', self.root / 'expired', settings,
                            iterator=[item(0)])
        self.assertEqual(result['action'], 'YIELD')
        self.assertFalse(result['accepted_by_live_client'])
        self.assertEqual(result['resolved_output']['delivery_error']['category'], 'request_expired')
        self.assertGreaterEqual(result['stop_elapsed_s'], .25)


if __name__ == '__main__':
    unittest.main()
