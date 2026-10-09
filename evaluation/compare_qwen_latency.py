"""Offline, warmed comparison of two Qwen models on frozen scenario snapshots."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
from statistics import mean, median
import subprocess
import time
from urllib.request import Request, urlopen

from app.domain.model_decision import model_decision_schema
from app.inference.ollama import OllamaClient, OllamaConfig
from app.policy.llm import PROMPT_VERSION, build_llm_prompt, decide_llm
from app.policy.rules import rule_readiness
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import SocialState, TemporalConfig
from app.state.tracks import TrackConfig
from evaluation.encounters import canonical, sha256


MODELS = ('qwen2.5:7b', 'qwen3:4b-instruct-2507-q4_K_M')
COMPARISON = Path('config/policy-comparison.json')
FIELDS = ('phase', 'recording_id', 'snapshot_id', 'source_line', 'timestamp_us',
          'repeat', 'block', 'input_sha256', 'prompt_sha256', 'requested_model',
          'returned_model', 'action', 'reason', 'success', 'error_category',
          'error', 'http_status', 'decision_latency_s', 'request_duration_s')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def prompt_hash(prompt):
    return digest(canonical([m.model_dump(exclude_none=True) for m in prompt.messages]))


@dataclass(frozen=True)
class Snapshot:
    state_json: str
    identity: dict


def freeze_inputs(settings):
    """Consume every observation once; first frame at/after each source-time grid tick."""
    temporal = TemporalConfig.from_file(settings['temporal_config'])
    tracking = TrackConfig.from_file(settings['track_config'])
    if tracking.history_window_s < temporal.window_s:
        raise ValueError('tracking history must cover temporal window')
    contract = json.loads(Path(settings['evaluation_contract']).read_text())
    eligible, recordings, selected = [], {}, []
    for row in contract['scenarios']:
        path = Path(row['recording'])
        if sha256(path) != row['source_sha256']:
            raise ValueError(f'{path}: source hash differs from evaluation contract')
        counts, reasons = Counter(), Counter()
        session = next_tick = None
        for item in iter_replay_states(path, 'sdk', track_config=tracking, temporal_config=temporal):
            counts['observations'] += 1
            now = item.state.robot_timestamp_us
            if session != item.source['processing_session_id']:
                session, next_tick = item.source['processing_session_id'], now
            if now < next_tick:
                continue
            # Gaps skip grid ticks, never duplicate a snapshot or replay delays.
            tick = next_tick + ((now - next_tick) // 1_000_000) * 1_000_000
            next_tick = tick + 1_000_000
            counts['selected'] += 1
            prompt = build_llm_prompt(item.state)
            state = SocialState.model_validate_json(prompt.social_state_json)
            hold = rule_readiness(state)
            identity = {'recording_id': row['scenario_id'], 'snapshot_id': state.state_id,
                        'source_line': item.source['line_number'], 'timestamp_us': now,
                        'grid_timestamp_us': tick, 'input_sha256': digest(prompt.social_state_json),
                        'prompt_sha256': prompt_hash(prompt), 'readiness_reason': hold}
            selected.append(identity)
            if hold:
                counts['not_ready'] += 1
                reasons[hold] += 1
            else:
                counts['eligible'] += 1
                eligible.append(Snapshot(prompt.social_state_json, identity))
        recordings[row['scenario_id']] = {'path': str(path), 'source_sha256': row['source_sha256'],
                                         **{k: counts[k] for k in ('observations', 'selected', 'eligible', 'not_ready')},
                                         'not_ready_reasons': dict(reasons)}
    return tuple(eligible), recordings, selected


def api(base_url, endpoint, payload=None):
    data = None if payload is None else canonical(payload).encode()
    request = Request(base_url.rstrip('/') + '/api/' + endpoint, data=data,
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def unload(base_url):
    models = api(base_url, 'ps')['models']
    for model in models:
        api(base_url, 'generate', {'model': model['name'], 'keep_alive': 0})
    if api(base_url, 'ps')['models']:
        raise RuntimeError('Ollama models remained resident after unloading')
    return [m['name'] for m in models]


def command(args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        return f'unavailable: {error}'


def latency(values):
    values = sorted(values)
    return {'n': len(values), 'mean_s': mean(values) if values else None,
            'median_s': median(values) if values else None,
            'p95_s': values[math.ceil(.95 * len(values)) - 1] if values else None}


def model_summary(rows, snapshots):
    good = [r for r in rows if r['success']]
    complete, stable, changed = 0, 0, []
    for snapshot in snapshots:
        attempts = [r for r in rows if r['snapshot_id'] == snapshot.identity['snapshot_id']]
        actions = [r['action'] if r['success'] else None for r in attempts]
        if len(actions) == 3 and all(a is not None for a in actions):
            complete += 1
            stable += len(set(actions)) == 1
            if len(set(actions)) > 1:
                changed.append({'snapshot_id': snapshot.identity['snapshot_id'], 'actions': actions})
    return {'attempts': len(rows), 'successful': len(good), 'failed': len(rows) - len(good),
            'failure_rate': (len(rows) - len(good)) / len(rows) if rows else None,
            'errors': dict(Counter(r['error_category'] for r in rows if not r['success'])),
            'decision_latency': latency([r['decision_latency_s'] for r in good]),
            'http_latency': latency([r['request_duration_s'] for r in good]),
            'actions': dict(Counter(r['action'] for r in good)),
            'action_consistency': {'fully_successful_snapshots': complete,
                                   'same_action_all_three': stable,
                                   'fraction': stable / complete if complete else None,
                                   'inconsistent': changed}}


def comparison(rows, snapshots):
    models = {model: model_summary([r for r in rows if r['requested_model'] == model], snapshots)
              for model in MODELS}
    index = {(r['snapshot_id'], r['repeat'], r['requested_model']): r for r in rows}
    paired, agreed, differences, deltas = 0, 0, [], []
    for snapshot in snapshots:
        for repeat in range(1, 4):
            pair = [index.get((snapshot.identity['snapshot_id'], repeat, m)) for m in MODELS]
            if not all(r is not None and r['success'] for r in pair):
                continue
            paired += 1
            deltas.append(pair[1]['decision_latency_s'] - pair[0]['decision_latency_s'])
            if pair[0]['action'] == pair[1]['action']:
                agreed += 1
            else:
                differences.append({'snapshot_id': snapshot.identity['snapshot_id'], 'repeat': repeat,
                                    'actions': {m: r['action'] for m, r in zip(MODELS, pair)}})
    return {'models': models, 'paired_actions': {'successful_pairs': paired,
            'failed_or_missing_pairs': 3 * len(snapshots) - paired, 'agreed': agreed,
            'agreement_fraction': agreed / paired if paired else None, 'differences': differences},
            'paired_qwen3_minus_qwen2_latency_s': {'mean': mean(deltas) if deltas else None,
                                                 'median': median(deltas) if deltas else None}}


def invoke(snapshot, client, phase, repeat, block):
    state = SocialState.model_validate_json(snapshot.state_json)
    started = time.perf_counter()
    result = decide_llm(state, client)
    elapsed = time.perf_counter() - started
    diagnostics = result.ollama_result
    row = {k: snapshot.identity[k] for k in FIELDS if k in snapshot.identity}
    row.update(phase=phase, repeat=repeat, block=block, requested_model=diagnostics.requested_model,
               returned_model=diagnostics.returned_model, success=result.ok,
               action=diagnostics.decision.action if diagnostics.decision else None,
               reason=diagnostics.decision.reason if diagnostics.decision else None,
               error_category=diagnostics.error.category.value if diagnostics.error else None,
               error=diagnostics.error.message if diagnostics.error else None,
               http_status=diagnostics.error.http_status if diagnostics.error else None,
               decision_latency_s=elapsed, request_duration_s=diagnostics.request_duration_s)
    # Check the actual prompt returned by the unchanged policy, outside its timer.
    if (digest(result.prompt.social_state_json) != row['input_sha256']
            or prompt_hash(result.prompt) != row['prompt_sha256']):
        row.update(success=False, action=None, reason=None, error_category='input_mismatch',
                   error='Actual input/prompt differs from frozen snapshot')
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    parser.add_argument('--base-url', default='http://127.0.0.1:11434')
    parser.add_argument('--prepare-only', action='store_true', help='Freeze/count inputs without Ollama')
    args = parser.parse_args(argv)
    settings = json.loads(COMPARISON.read_text())
    keys = ('temperature', 'seed', 'num_ctx', 'num_predict', 'timeout_seconds')
    common = {k: settings[k] for k in keys}
    clients = {m: OllamaClient(OllamaConfig(base_url=args.base_url, model=m, **common)) for m in MODELS}
    snapshots, recordings, selected = freeze_inputs(settings)
    if not snapshots:
        parser.exit(2, 'No eligible snapshots; no inference performed\n')
    args.output.mkdir(parents=True, exist_ok=False)
    summary = {'status': 'prepared' if args.prepare_only else 'running', 'error': None,
               'started_at_utc': datetime.now(timezone.utc).isoformat(),
               'git_head': command(['git', 'rev-parse', 'HEAD']),
               'runner_sha256': sha256(__file__), 'base_url': args.base_url,
               'models_requested': MODELS, 'settings': common, 'passes': 3,
               'prompt_version': PROMPT_VERSION, 'output_schema_sha256': digest(canonical(model_decision_schema())),
               'config_sha256': {p: sha256(p) for p in (str(COMPARISON), settings['temporal_config'],
                                                       settings['track_config'], settings['evaluation_contract'])},
               'hardware': {'platform': platform.platform(), 'python': platform.python_version(),
                            'cpu': command(['sysctl', '-n', 'machdep.cpu.brand_string']),
                            'memory_bytes': command(['sysctl', '-n', 'hw.memsize'])},
               'sampling': 'First observation at/after each 1s grid tick, anchored at session start; gaps skip ticks',
               'recordings': recordings, 'selected_snapshots': selected,
               'eligible_order_sha256': digest(canonical([s.identity for s in snapshots])),
               'timing': 'perf_counter around unchanged decide_llm; successful measured calls only in latency statistics',
               'p95_method': 'nearest rank', 'warmup_input': snapshots[0].identity['snapshot_id'],
               'limitations': ['Development recordings; open-loop decisions, no robot execution',
                               'Action agreement is not accuracy',
                               'Identical warmup input; server prompt caching and thermal effects may affect latency'],
               'blocks': []}
    rows = []
    try:
        with (args.output / 'results.csv').open('x', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            if not args.prepare_only:
                summary['ollama_version'] = api(args.base_url, 'version')
                tags = {m['name']: m for m in api(args.base_url, 'tags')['models']}
                missing = [m for m in MODELS if m not in tags]
                if missing:
                    raise RuntimeError('Missing exact Ollama model(s); pull: ' + ', '.join(missing))
                summary['model_identities'] = {m: tags[m] for m in MODELS}
                # Smoke then three measured passes: A/B, B/A, A/B.
                for phase, repeat, order in [('smoke', 0, MODELS)] + [
                        ('measured', n, MODELS if n % 2 else MODELS[::-1]) for n in range(1, 4)]:
                    for model in order:
                        block = len(summary['blocks']) + 1
                        info = {'block': block, 'phase': phase, 'repeat': repeat, 'model': model,
                                'unloaded_before': unload(args.base_url)}
                        summary['blocks'].append(info)
                        warmup = invoke(snapshots[0], clients[model], 'warmup', repeat, block)
                        rows.append(warmup)
                        writer.writerow(warmup)
                        stream.flush()
                        if warmup['error_category'] in {'connection', 'timeout', 'http', 'input_mismatch'}:
                            raise RuntimeError(f"Warmup failed for {model}: {warmup['error']}")
                        info['resident_after_warmup'] = api(args.base_url, 'ps')['models']
                        if [m['name'] for m in info['resident_after_warmup']] != [model]:
                            raise RuntimeError('Unexpected model residency after warmup')
                        if info['resident_after_warmup'][0].get('context_length', common['num_ctx']) != common['num_ctx']:
                            raise RuntimeError('Resident model context differs from common num_ctx')
                        inputs = snapshots[:3] if phase == 'smoke' else snapshots
                        for snapshot in inputs:
                            row = invoke(snapshot, clients[model], phase, repeat, block)
                            rows.append(row)
                            writer.writerow(row)
                            stream.flush()
                        print(f'{phase} pass {repeat}: {model}, {len(inputs)} calls', flush=True)
                summary['unloaded_at_end'] = unload(args.base_url)
                summary['status'] = 'complete'
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as error:
        summary['status'], summary['error'] = 'partial' if rows else 'blocked', str(error) or 'interrupted'
    measured = [r for r in rows if r['phase'] == 'measured']
    summary['comparison'] = comparison(measured, snapshots)
    summary['per_recording'] = {
        name: comparison([r for r in measured if r['recording_id'] == name],
                         [s for s in snapshots if s.identity['recording_id'] == name]) for name in recordings}
    summary['excluded_calls'] = {phase: {'attempts': sum(r['phase'] == phase for r in rows),
                                        'failed': sum(r['phase'] == phase and not r['success'] for r in rows)}
                                 for phase in ('smoke', 'warmup')}
    summary['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': summary['status'], 'error': summary['error'],
                      'recordings': recordings, 'comparison': summary['comparison']}, indent=2))
    return 2 if summary['error'] else 1 if any(not r['success'] for r in rows) else 0


if __name__ == '__main__':
    raise SystemExit(main())
