"""Freeze a paired first-eligible evaluation and profile unchanged LLM requests.

No production source, prompt, configuration, acceptance rule or request is changed.
The profiling client observes the existing transport's request/response and returns
the original OllamaResult. Later snapshots and a cold-load probe are diagnostics.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import time
from unittest.mock import patch

from app.inference.ollama import OllamaClient, OllamaConfig, UrllibTransport
from app.policy.llm import build_llm_prompt, classify_llm
from app.policy.rules import decide, normalise_rule_decision
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import SocialState, TemporalConfig
from app.state.tracks import TrackConfig
from scripts.replay_first_action import replay, request_json


METRIC_KEYS = ('total_duration', 'load_duration', 'prompt_eval_count',
               'prompt_eval_cached_count', 'prompt_eval_duration', 'eval_count', 'eval_duration')


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(state):
    return build_llm_prompt(state).social_state_json


def extract_metrics(payload):
    """Optional telemetry never makes a valid model answer fail validation."""
    return {key: value if type(value := payload.get(key)) is int and value >= 0 else None
            for key in METRIC_KEYS}


class ReplayClock:
    def __init__(self):
        self.origin = None

    def now(self):
        value = time.monotonic()
        if self.origin is None:
            self.origin = value
        return value


class ProfilingTransport:
    def __init__(self, underlying):
        self.underlying = underlying
        self.last = None

    def post(self, url, body, *, timeout_seconds):
        capture = self.last = {'request_body': body, 'started': time.monotonic(),
                               'finished': None, 'response': None}
        try:
            response = self.underlying.post(url, body, timeout_seconds=timeout_seconds)
            capture['response'] = response
            return response
        finally:
            capture['finished'] = time.monotonic()


class ProfiledClient(OllamaClient):
    def __init__(self, config, output, clock, *, transport=None):
        self.output, self.clock = Path(output), clock
        self.output.mkdir(parents=True, exist_ok=False)
        self.calls = []
        super().__init__(config, transport=ProfilingTransport(transport or UrllibTransport()))

    def chat(self, messages):
        started = time.monotonic()
        result = super().chat(messages)
        finished = time.monotonic()
        capture = self.transport.last
        response = capture['response']
        try:
            payload = json.loads(response.body) if response else {}
        except (ValueError, UnicodeError):
            payload = {}
        metrics = extract_metrics(payload if isinstance(payload, dict) else {})
        request = json.loads(capture['request_body'])
        user = request['messages'][-1]['content']
        state_json = user.split('\n', 1)[1]
        state = json.loads(state_json)
        row = {
            # Compact payloads deliberately omit correlation identifiers. Their
            # source is linked from the model audit envelope by experiment tools.
            'source_state_id': state.get('state_id'),
            'source_robot_timestamp_us': state.get('robot_timestamp_us'),
            'state_sha256': sha(state_json.encode()),
            'request_sha256': sha(capture['request_body']),
            'request_bytes': len(capture['request_body']),
            'input_characters': sum(len(m['content']) for m in request['messages']),
            'chat_start_elapsed_s': started-self.clock.origin if self.clock.origin is not None else None,
            'chat_end_elapsed_s': finished-self.clock.origin if self.clock.origin is not None else None,
            'client_call_duration_s': finished-started,
            'request_preparation_s': capture['started']-started,
            'transport_duration_s': capture['finished']-capture['started'],
            'response_validation_s': finished-capture['finished'],
            'ollama_metrics': metrics,
            'request_duration_s': result.request_duration_s,
            'ok': result.ok,
            'decision': result.decision.model_dump() if result.decision else None,
            'error': asdict(result.error) if result.error else None,
        }
        index = len(self.calls)+1
        # Write only after the original HTTP call and validation have completed.
        write_json(self.output / f'call-{index:03d}.request.json', request)
        if response is not None:
            (self.output / f'call-{index:03d}.response.json').write_bytes(response.body)
        self.calls.append(row)
        write_json(self.output / 'calls.json', self.calls)
        return result


def source_paths():
    paths = {Path('scripts/replay_first_action.py'), Path('requirements.txt'),
             Path('requirements-navel.txt')}
    for directory, pattern in (('app', '*.py'), ('robot', '*.py'),
                               ('config', '*.json'), ('schemas', '*.json')):
        paths.update(Path(directory).rglob(pattern))
    return sorted(p for p in paths if p.is_file())


def verify_freeze(root, *, worktree=True):
    root = Path(root)
    manifest = json.loads((root / 'freeze/manifest.json').read_text())
    for path, expected in manifest['files_sha256'].items():
        if sha((root / 'freeze' / path).read_bytes()) != expected:
            raise ValueError(f'Frozen file changed: {path}')
    if worktree:
        for path, expected in manifest['implementation_sha256'].items():
            if sha(Path(path).read_bytes()) != expected:
                raise ValueError(f'Implementation differs from frozen baseline: {path}')
    return manifest


def freeze(root, settings_path, base_url):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    dest = root / 'freeze'
    dest.mkdir()
    settings = json.loads(Path(settings_path).read_text())
    settings['base_url'] = base_url
    scenes = json.loads(Path(settings['evaluation_contract']).read_text())['scenarios']
    tracking = TrackConfig.from_file(settings['track_config'])
    temporal = TemporalConfig.from_file(settings['temporal_config'])
    original_hashes = {}
    for path in source_paths():
        copied = dest / 'source' / path
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copied)
        original_hashes[str(path)] = sha(path.read_bytes())
    frozen_scenes = []
    for scene in scenes:
        path = Path(scene['recording'])
        if sha(path.read_bytes()) != scene['source_sha256']:
            raise ValueError(f'Recording changed: {path}')
        target = dest / 'recordings' / path.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, target)
        items = list(iter_replay_states(path, 'sdk', track_config=tracking, temporal_config=temporal))
        eligible = [item for item in items if item.state.observation_readiness == 'READY']
        if not eligible:
            raise ValueError(f'No eligible baseline observation: {scene["scenario_id"]}')
        record = {**scene, 'observations': len(items), 'eligible_observations': len(eligible),
                  'first_perception_us': items[0].state.robot_timestamp_us, 'snapshots': {}}
        for point, item in (('first', eligible[0]), ('later', eligible[-1])):
            folder = dest / 'snapshots' / scene['scenario_id']
            folder.mkdir(parents=True, exist_ok=True)
            prompt = build_llm_prompt(item.state)
            write_json(folder / f'{point}.state.json', json.loads(prompt.social_state_json))
            write_json(folder / f'{point}.messages.json', [m.model_dump(exclude_none=True) for m in prompt.messages])
            write_json(folder / f'{point}.source.json', item.source)
            rule = normalise_rule_decision(decide(item.state.model_dump())).model_dump()
            record['snapshots'][point] = {
                'timestamp_us': item.state.robot_timestamp_us,
                'since_motion_s': (item.state.robot_timestamp_us-scene['motion_start_monotonic_us'])/1e6,
                'state_id': item.state.state_id, 'state_sha256': sha(prompt.social_state_json.encode()),
                'rule_decision': rule,
            }
        frozen_scenes.append(record)
    protocol = {
        'version': 'llm-baseline-v1', 'settings': settings, 'scenarios': frozen_scenes,
        'primary': 'The output from the first eligible observation, never a later successful retry or best frame',
        'alignment': 'Equal scenario weight; action in unchanged expected_actions; invalid/failed/missing first outputs count as nonalignment',
        'primary_times': ['motion onset to first eligible observation',
                          'first eligible observation to complete validated output',
                          'motion onset to complete validated output'],
        'acceptance': 'Separate secondary measure; unchanged 30s wait, 10s model source age, 1s latest observation age',
        'later': 'Last eligible observation before each original recording ends; timestamps frozen before inference; diagnostic only',
        'repetitions': 3, 'order': [s['scenario_id'] for s in scenes],
        'model_condition': 'Warm model before each primary repetition; cold-load probe separate; unchanged generation options',
        'fairness': 'Rules and LLM receive identical canonical SocialState snapshots; assert content SHA-256 and timestamps',
        'limitations': ['Nine recordings are a tuning set, not held-out accuracy',
            'S3 and S8 retain their existing multiple allowed actions',
            'S8 first decision occurs before the detected person appears; later diagnosis is separate',
            'SDK replay cannot recover outgoing transport drops; receiver HTTP is in-process; no LAN timing or physical execution',
            'Finite clips can end while inference runs, making latest observation stale'],
    }
    write_json(dest / 'protocol.json', protocol)
    write_json(dest / 'runtime.json', {
        'platform': platform.platform(), 'python': platform.python_version(),
        'dependencies': {d.metadata['Name']: d.version for d in distributions() if d.metadata['Name']},
        'ollama_version': request_json(base_url, '/api/version'),
        'ollama_tags': request_json(base_url, '/api/tags'),
        'ollama_model': request_json(base_url, '/api/show', {'model': settings['model']}),
    })
    shutil.copyfile(__file__, dest / 'profiling-runner.py')
    manifest = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'branch': subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip(),
        'initial_git_status': subprocess.check_output(['git', 'status', '--short'], text=True),
        'implementation_sha256': original_hashes,
        'files_sha256': {str(p.relative_to(dest)): sha(p.read_bytes()) for p in sorted(dest.rglob('*')) if p.is_file()},
    }
    write_json(dest / 'manifest.json', manifest)
    verify_freeze(root)
    print(f'Frozen baseline: {root}', flush=True)


def profile_config(settings):
    return OllamaConfig(base_url=settings['base_url'], **{
        key: settings[key] for key in ('model', 'temperature', 'seed', 'num_ctx', 'num_predict', 'timeout_seconds')})


def timing_breakdown(call, elapsed=None, source_offset=None):
    m = call['ollama_metrics']
    result = {key+'_s': m[key]/1e9 if m[key] is not None else None
              for key in ('total_duration', 'load_duration', 'prompt_eval_duration', 'eval_duration')}
    result.update(prompt_tokens=m['prompt_eval_count'], cached_prompt_tokens=m['prompt_eval_cached_count'],
                  output_tokens=m['eval_count'], request_bytes=call['request_bytes'],
                  request_preparation_s=call['request_preparation_s'],
                  response_validation_s=call['response_validation_s'],
                  transport_duration_s=call['transport_duration_s'])
    native = result['total_duration_s']
    result['transport_minus_native_total_s'] = call['transport_duration_s']-native if native is not None else None
    parts = [result[key] for key in ('load_duration_s', 'prompt_eval_duration_s', 'eval_duration_s')]
    result['native_unattributed_s'] = native-sum(parts) if native is not None and all(x is not None for x in parts) else None
    if elapsed is not None and source_offset is not None:
        result.update(eligible_to_observed_output_s=elapsed-source_offset,
                      eligible_to_client_call_s=call['chat_start_elapsed_s']-source_offset,
                      client_finished_to_observed_output_s=elapsed-call['chat_end_elapsed_s'])
    return result


def operational_row(scene, result, calls, root):
    first = scene['snapshots']['first']
    call = next((c for c in calls if c['source_state_id'] == first['state_id']), None)
    if call is not None and call['state_sha256'] != first['state_sha256']:
        raise ValueError(f'Unequal policy input: {scene["scenario_id"]}')
    eligible_offset = (first['timestamp_us']-scene['first_perception_us'])/1e6
    events = [json.loads(l) for l in (Path(root) / 'events.jsonl').read_text().splitlines()]
    first_event = next((e for e in events if e['event'] == 'first_model_request'), None)
    decision = call['decision'] if call else None
    same_output = result.get('resolved_output', {}).get('source_state_id') == first['state_id'] if result.get('resolved_output') else False
    observed_output_elapsed = result['resolved_elapsed_s'] if same_output else None
    return {
        'scenario_id': scene['scenario_id'], 'first_source_state_id': first['state_id'],
        'first_state_sha256': first['state_sha256'], 'expected_actions': scene['expected_actions'],
        'first_decision': decision, 'first_output_valid': bool(call and call['ok']),
        'action_aligned': bool(decision and decision['action'] in scene['expected_actions']),
        'first_eligible_since_motion_s': first['since_motion_s'],
        'output_since_motion_s': observed_output_elapsed+(scene['first_perception_us']-scene['motion_start_monotonic_us'])/1e6 if observed_output_elapsed is not None else None,
        'first_request_observed_elapsed_s': first_event['elapsed_s'] if first_event else None,
        'first_client_finished_elapsed_s': call['chat_end_elapsed_s'] if call else None,
        'first_eligible_to_validated_client_output_s': call['chat_end_elapsed_s']-eligible_offset if call and call['ok'] else None,
        'accepted_by_live_client': result['accepted_by_live_client'] if same_output else False,
        'acceptance_details': result['acceptance_details'] if same_output else None,
        'delivery_status': result['resolved_output'].get('delivery_status') if same_output else None,
        'delivery_error': result['resolved_output'].get('delivery_error') if same_output else None,
        'total_calls': len(calls), 'runner_status': result['status'],
        'timing': timing_breakdown(call, observed_output_elapsed, eligible_offset) if call else None,
        'first_error': call['error'] if call else {'category': 'no_first_call'},
    }


def run(root):
    root = Path(root)
    verify_freeze(root)
    protocol = json.loads((root / 'freeze/protocol.json').read_text())
    settings, scenes = protocol['settings'], protocol['scenarios']
    dest = root / 'measurements'
    dest.mkdir(exist_ok=False)
    rows = []
    for repetition in range(1, protocol['repetitions']+1):
        warm = request_json(settings['base_url'], '/api/generate', {
            'model': settings['model'], 'prompt': '', 'stream': False,
            'keep_alive': '5m', 'options': {'num_ctx': settings['num_ctx']}})
        write_json(dest / f'repeat-{repetition}/warmup.json', warm)
        for scene in scenes:
            print(f'Repeat {repetition}: {scene["scenario_id"]} first eligible', flush=True)
            folder = dest / f'repeat-{repetition}' / scene['scenario_id']
            clock = ReplayClock()
            clients = []
            def factory(config):
                client = ProfiledClient(config, folder / f'profile-{len(clients)+1}', clock)
                clients.append(client)
                return client
            # Only the client's transport observations change; replay and production code stay untouched.
            with patch('app.inference.live.OllamaClient', factory):
                result = replay(scene, 'llm', folder / 'live', settings, now=clock.now)
            calls = [call for client in clients for call in client.calls]
            row = operational_row(scene, result, calls, folder / 'live')
            row['repetition'] = repetition
            rows.append(row)
            write_json(folder / 'operational.json', row)
            write_json(dest / 'operational-summary.json', rows)
            print(json.dumps({k: row[k] for k in ('scenario_id', 'repetition', 'first_decision', 'action_aligned', 'first_eligible_to_validated_client_output_s', 'accepted_by_live_client')}), flush=True)
    # Later calls are deliberately after every operational repetition to avoid warming their prompts first.
    diagnostics = []
    for scene in scenes:
        print(f'{scene["scenario_id"]}: fixed later diagnostic', flush=True)
        state = SocialState.model_validate_json((root / f'freeze/snapshots/{scene["scenario_id"]}/later.state.json').read_text())
        client = ProfiledClient(profile_config(settings), dest / 'later' / scene['scenario_id'], ReplayClock())
        result = classify_llm(state, client)
        assert client.calls[0]['state_sha256'] == scene['snapshots']['later']['state_sha256']
        diagnostics.append({'scenario_id': scene['scenario_id'], 'snapshot': scene['snapshots']['later'],
                            'llm': result, 'timing': timing_breakdown(client.calls[0]), 'primary_score': False})
        write_json(dest / 'later-summary.json', diagnostics)
    # Controlled cold model probe uses the identical first S1 snapshot, not a new operational trial.
    write_json(dest / 'cold/unload.json', request_json(settings['base_url'], '/api/generate', {
        'model': settings['model'], 'keep_alive': 0, 'stream': False}))
    state = SocialState.model_validate_json((root / 'freeze/snapshots/S1/first.state.json').read_text())
    client = ProfiledClient(profile_config(settings), dest / 'cold/S1', ReplayClock())
    result = classify_llm(state, client)
    write_json(dest / 'cold/summary.json', {'scenario_id': 'S1', 'llm': result,
        'timing': timing_breakdown(client.calls[0]), 'primary_score': False})
    write_json(dest / 'verification.json', {'frozen_files_and_implementation_verified': bool(verify_freeze(root)),
        'operational_runs': len(rows), 'later_diagnostics': len(diagnostics), 'cold_probes': 1})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('freeze', 'run', 'verify'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--settings', default='config/policy-comparison-final.json')
    parser.add_argument('--base-url', default='http://127.0.0.1:11434')
    args = parser.parse_args(argv)
    if args.mode == 'freeze':
        freeze(args.output, args.settings, args.base_url)
    elif args.mode == 'run':
        run(args.output)
    else:
        verify_freeze(args.output)
        print('Frozen baseline and implementation verified')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
