"""Telemetry must preserve policy requests/results and the first-source score."""
import json
from pathlib import Path
import tempfile
import unittest

from app.inference.ollama import OllamaClient, OllamaConfig
from app.policy.llm import build_llm_prompt
from scripts.profile_llm_baseline import (ProfiledClient, ReplayClock, extract_metrics,
    freeze, operational_row, verify_freeze)
from tests.test_llm_policy import social_state
from tests.test_ollama import FakeTransport, chat_payload, json_response
from unittest.mock import patch


class ProfileTests(unittest.TestCase):
    def test_telemetry_preserves_the_exact_request_and_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            config = OllamaConfig(base_url='http://127.0.0.1:11434', model='test',
                                  temperature=0.0, seed=42, num_ctx=8192, num_predict=192)
            messages = build_llm_prompt(social_state()).messages
            payload = chat_payload(total_duration=900_000_000, load_duration=100_000_000,
                prompt_eval_count=1200, prompt_eval_duration=600_000_000,
                eval_count=24, eval_duration=200_000_000)
            normal_transport = FakeTransport(json_response(payload))
            profiled_transport = FakeTransport(json_response(payload))
            original = OllamaClient(config, transport=normal_transport).chat(messages)
            client = ProfiledClient(config, Path(directory)/'profile', ReplayClock(),
                                    transport=profiled_transport)
            result = client.chat(messages)
            self.assertEqual(normal_transport.calls, profiled_transport.calls)
            self.assertEqual(result.decision, original.decision)
            self.assertEqual(result.raw_content, original.raw_content)
            self.assertEqual(result.error, original.error)
            self.assertEqual(client.calls[0]['ollama_metrics']['prompt_eval_count'], 1200)
            self.assertEqual(client.calls[0]['ollama_metrics']['prompt_eval_duration'], 600_000_000)
            self.assertIsNone(client.calls[0]['ollama_metrics']['prompt_eval_cached_count'])
            self.assertGreaterEqual(client.calls[0]['response_validation_s'], 0)

    def test_bad_optional_metrics_do_not_invalidate_an_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ProfiledClient(OllamaConfig(base_url='http://localhost', model='test'),
                Path(directory)/'profile', ReplayClock(), transport=FakeTransport(json_response(
                    chat_payload(total_duration='invalid', load_duration=-1,
                                 prompt_eval_count=True, eval_duration=0))))
            result = client.chat(build_llm_prompt(social_state()).messages)
            self.assertTrue(result.ok)
            self.assertIsNone(client.calls[0]['ollama_metrics']['total_duration'])
            self.assertIsNone(client.calls[0]['ollama_metrics']['prompt_eval_count'])
            self.assertEqual(client.calls[0]['ollama_metrics']['eval_duration'], 0)

    def test_later_success_cannot_replace_a_failed_first_request_in_primary_score(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'events.jsonl').write_text('')
            first = {'state_id': 'first', 'timestamp_us': 1_000_000,
                     'state_sha256': 'hash', 'since_motion_s': 1.0}
            scene = {'scenario_id': 'S1', 'snapshots': {'first': first},
                     'first_perception_us': 0, 'motion_start_monotonic_us': 0,
                     'expected_actions': ['CONTINUE']}
            call = {'source_state_id': 'first', 'state_sha256': 'hash', 'decision': None,
                    'ok': False, 'error': {'category': 'invalid_decision'},
                    'chat_start_elapsed_s': 1.01, 'chat_end_elapsed_s': 2.0,
                    'ollama_metrics': extract_metrics({}), 'request_bytes': 100,
                    'request_preparation_s': .01, 'response_validation_s': .01,
                    'transport_duration_s': .98}
            result = {'resolved_output': {'source_state_id': 'later'},
                      'resolved_elapsed_s': 3.0, 'accepted_by_live_client': True,
                      'acceptance_details': {}, 'status': 'ACTION_RESOLVED'}
            row = operational_row(scene, result, [call], directory)
            self.assertFalse(row['action_aligned'])
            self.assertFalse(row['accepted_by_live_client'])
            self.assertIsNone(row['output_since_motion_s'])
            self.assertIsNone(row['first_decision'])

    def test_freeze_is_exclusive_and_detects_recording_or_snapshot_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'baseline'
            with patch('scripts.profile_llm_baseline.request_json', return_value={'test': True}):
                freeze(root, 'config/policy-comparison-final.json', 'http://localhost:11434')
            manifest = verify_freeze(root)
            protocol = json.loads((root/'freeze/protocol.json').read_text())
            self.assertEqual(len(protocol['scenarios']), 9)
            self.assertEqual(sum(s['observations'] for s in protocol['scenarios']), 574)
            self.assertEqual(protocol['scenarios'][7]['expected_actions'], ['APPROACH','ENGAGE','CONTINUE'])
            self.assertTrue(manifest['implementation_sha256'])
            with self.assertRaises(FileExistsError):
                freeze(root, 'config/policy-comparison-final.json', 'http://localhost')
            (root/'freeze/snapshots/S1/first.state.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Frozen file changed'):
                verify_freeze(root)


if __name__ == '__main__':
    unittest.main()
