"""Replay captures at recorded speed through the live receiver; stop at first output.

Run from the repository root with ``python -m scripts.replay_first_action``.
No robot transport or action executor is instantiated.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen

from app.commands import CommandConfig
from app.inference.live import LiveModelConfig
from app.inference.ollama import OllamaConfig
from app.policy.target_lock import LockConfig
from app.replay.llm_inputs import iter_replay_states
from app.server import create_app
from app.state.social_models import TemporalConfig
from app.state.tracks import TrackConfig
from robot.navel_client.single_trial import SingleTrial


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def request_json(base, path, body=None):
    request = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def rejection_details(trial, result, now_us):
    local = trial.current_observation
    return {'current_readiness': trial.ready,
            'latest_observation_age_s': (now_us - local['timestamp']) / 1e6 if local else None,
            'model_source_age_s': (now_us - result['source_robot_timestamp_us']) / 1e6,
            'trial_phase': trial.phase, 'failure_reason': trial.failure_reason}


def replay(scene, policy, output, settings, *, iterator=None, now=time.monotonic,
           sleep=time.sleep):
    """A fresh receiver and client for one encounter, with asynchronous inference.

    Policy resolution and robot acceptance are separate outcomes: an otherwise
    valid model decision can arrive after current sensor evidence has expired.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    tracking = TrackConfig.from_file(settings['track_config'])
    temporal = TemporalConfig.from_file(settings['temporal_config'])
    items = iter(iterator if iterator is not None else iter_replay_states(
        scene['recording'], 'sdk', track_config=tracking, temporal_config=temporal))
    current = next(items)
    origin = current.source['reconstructed_raw_observation']['timestamp']
    model = LiveModelConfig(mode='disabled')
    if policy == 'llm':
        config = OllamaConfig(base_url=settings['base_url'], **{
            key: settings[key] for key in ('model', 'temperature', 'seed', 'num_ctx',
                                           'num_predict', 'timeout_seconds')})
        model = LiveModelConfig(mode='llm', llm_config=config)
    app = create_app(output / 'observations.jsonl', session_id=current.state.session_id,
        social_output=output / 'states.jsonl', track_config=tracking, temporal_config=temporal,
        lock_config=LockConfig.from_file('config/target-lock.json'),
        command_config=CommandConfig.from_file('config/commands.json'),
        model_config=model, model_output=output / 'models.jsonl' if policy == 'llm' else None)
    runner = app.extensions.get('live_models')
    client = app.test_client()
    started = now()
    clock_us = lambda: origin + round((now() - started) * 1e6)
    trial = SingleTrial(policy, monotonic=now, monotonic_us=clock_us,
                        model_max_age_s=settings['model_trial_max_age_s'])
    trial.session_id = current.state.session_id
    if policy == 'llm':
        opened = client.post('/api/v1/model-trials', json={
            'trial_id': trial.trial_id, 'policy': policy, 'wait_s': 30.0,
            'max_age_s': settings['model_trial_max_age_s']})
        if opened.status_code != 200:
            raise RuntimeError(opened.json)
    holds = Counter()
    frames = ready_frames = 0
    last_response = None
    last_source = None
    first_request = None
    resolved = None
    accepted = False
    failures = []
    eof = False
    timing_lag = 0.0
    status = None
    events = (output / 'events.jsonl').open('w')

    def event(kind, **fields):
        events.write(json.dumps({'event': kind, 'elapsed_s': now() - started, **fields}) + '\n')
        events.flush()

    def poll():
        nonlocal resolved, accepted
        # Delivery can expire while Ollama is still computing. Its validated
        # audit output is still the first policy output the user asked to see;
        # it must never be mistaken for a decision accepted by the live client.
        audit = output / 'models.jsonl'
        if audit.exists():
            for line in audit.read_text().splitlines(keepends=True):
                if not line.endswith('\n'):
                    continue
                row = json.loads(line)
                inference = row['llm_inference']
                if inference['status'] == 'succeeded' and inference['decision'] is not None:
                    delivery = failures[-1] if failures else None
                    if trial.pending_request is not None:
                        response = client.post('/api/v1/model-trials/result', json=trial.pending_request)
                        if response.status_code == 200:
                            delivery = response.json['result']
                            accepted = trial.accept_model_result(delivery)
                    resolved = {**row.get('trial', {}), **inference,
                        'delivery_status': delivery['status'] if delivery else 'unavailable',
                        'delivery_error': delivery.get('error') if delivery else None}
                    return True
        if trial.pending_request is None:
            return False
        response = client.post('/api/v1/model-trials/result', json=trial.pending_request)
        if response.status_code != 200:
            raise RuntimeError(response.json)
        result = response.json['result']
        if result['status'] == 'pending':
            return False
        event('model_result', result=result)
        accepted = trial.accept_model_result(result)
        if result['status'] == 'succeeded' and result['decision'] is not None:
            resolved = result
            return True
        failures.append(result)
        return False

    try:
        while True:
            if policy == 'llm' and poll():
                status = 'ACTION_RESOLVED'
                break
            trial.tick()
            if trial.terminal:
                status = trial.failure_reason
                break
            if current is None:
                if trial.pending_request is None and not (runner and runner.status()['active']):
                    status = 'RECORDING_ENDED_NO_ACTION'
                    break
                sleep(0.02)
                continue
            raw = current.source['reconstructed_raw_observation']
            due = started + (raw['timestamp'] - origin) / 1e6
            if now() < due:
                sleep(min(0.02, due - now()))
                continue
            timing_lag = max(timing_lag, now() - due)
            trial.note_observation(raw)
            body = raw
            if policy == 'llm':
                identity = trial.model_identity()
                if trial.retry_request_id:
                    identity['retry_request_id'] = trial.retry_request_id
                body = {'observation': raw, 'model_source': {
                    'version': 1, 'clock': 'robot-host-monotonic-us',
                    'capture': current.source['source_record']}, 'trial': identity}
            response = client.post('/api/v1/observations', json=body)
            if response.status_code != 200 or response.json.get('processing_status') != 'complete':
                raise RuntimeError(response.json)
            last_response, last_source = response.json, current.source
            frames += 1
            readiness = last_response['policy_readiness']
            ready_frames += readiness['status'] == 'READY'
            holds[readiness['reason_code'] or 'READY'] += 1
            event('observation', line_number=last_source['line_number'],
                  source_timestamp_us=raw['timestamp'], response=last_response)
            if policy == 'rules':
                accepted = trial.accept_rule_response(last_response, raw)
                if last_response['final_decision'] is not None:
                    resolved = {'decision': last_response['final_decision'],
                        'source_robot_timestamp_us': raw['timestamp'],
                        'source_state_id': last_response['social_state']['state_id']}
                    status = 'ACTION_RESOLVED'
                    break
            else:
                trial.observe(last_response, raw)
                result = last_response['model_trial']['result']
                if result is not None:
                    trial.register_request(result)
                    if first_request is None:
                        first_request = dict(result)
                        event('first_model_request', result=result)
                if poll():
                    status = 'ACTION_RESOLVED'
                    break
            current = next(items, None)
            if current is None:
                eof = True
                event('recording_end')
        elapsed = now() - started
        result = {'scenario_id': scene['scenario_id'], 'policy': policy, 'status': status,
            'action': resolved['decision']['action'] if resolved else None,
            'decision': resolved['decision'] if resolved else None,
            'accepted_by_live_client': accepted if resolved else None,
            'acceptance_details': rejection_details(trial, resolved, clock_us()) if resolved else None,
            'resolved_elapsed_s': elapsed if resolved else None,
            'stop_elapsed_s': elapsed, 'frames_ingested': frames, 'ready_frames': ready_frames,
            'readiness_counts': dict(holds), 'recording_exhausted': eof,
            'last_ingested_source_offset_s': (last_source['reconstructed_raw_observation']['timestamp'] - origin) / 1e6,
            'last_ingested_line': last_source['line_number'],
            'decision_source_offset_s': (resolved['source_robot_timestamp_us'] - origin) / 1e6 if resolved else None,
            'max_dispatch_lag_s': timing_lag, 'model_failures': failures,
            'first_model_request': first_request, 'resolved_output': resolved,
            'last_readiness': last_response['policy_readiness'],
            'source_sha256': last_source['file_sha256'], 'trial_id': trial.trial_id,
            'clock_mapping': 'original first perception receipt + real elapsed replay time',
            'physical_execution': False}
        event('stop', status=status)
        write_json(output / 'result.json', result)
        return result
    finally:
        events.close()
        if runner:
            try:
                client.post('/api/v1/model-trials/close', json=trial.model_identity())
            finally:
                runner.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--policies', nargs='+', choices=['rules', 'llm'], default=['rules', 'llm'])
    parser.add_argument('--scenarios', nargs='+')
    parser.add_argument('--base-url', default='http://127.0.0.1:11434')
    parser.add_argument('--warm-model', action='store_true', help='Load model weights before encounter clocks start')
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    settings = json.loads(Path('config/policy-comparison.json').read_text())
    settings['base_url'] = args.base_url
    manifest = {'created_utc': datetime.now(timezone.utc).isoformat(),
        'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'settings': settings, 'rate': 1.0, 'stop': 'first validated policy output (including expired delivery), or source exhausted with no pending/active inference',
        'receiver': 'production Flask endpoints using in-process HTTP test client',
        'client': 'production SingleTrial, 30s timeout, 1s latest observation age, 10s model source age',
        'limitations': ['SDK reconstruction cannot recover outgoing queue drops or collection delay',
            'structured LLM/rules consume SDK observations; camera pixels are not a VLM condition',
            'no physical actions; generated commands are recorded in events only',
            'first action timing differs from the trimmed survey endpoint task'],
        'warm_model': args.warm_model}
    if 'llm' in args.policies:
        manifest['ollama_tags'] = request_json(args.base_url, '/api/tags')
        manifest['ollama_version'] = request_json(args.base_url, '/api/version')
        if args.warm_model:
            manifest['warmup'] = request_json(args.base_url, '/api/generate', {
                'model': settings['model'], 'prompt': '', 'stream': False,
                'keep_alive': '5m', 'options': {'num_ctx': settings['num_ctx']}})
    write_json(args.output / 'manifest.json', manifest)
    scenes = json.loads(Path(settings['evaluation_contract']).read_text())['scenarios']
    results = []
    for policy in args.policies:
        for scene in scenes:
            if args.scenarios and scene['scenario_id'] not in args.scenarios:
                continue
            print(f"Starting {scene['scenario_id']} {policy}", flush=True)
            row = replay(scene, policy, args.output / policy / scene['scenario_id'], settings)
            results.append(row)
            write_json(args.output / 'summary.json', results)
            print(json.dumps({key: row[key] for key in ('scenario_id', 'policy', 'status',
                'action', 'accepted_by_live_client', 'stop_elapsed_s', 'frames_ingested')}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
