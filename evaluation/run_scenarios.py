"""Compare rules and a structured-state LLM at declared causal endpoints.

Output folders must be new. Neither policy receives cameras or human labels.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import time
from urllib.request import urlopen

from app.inference.ollama import OllamaClient, OllamaConfig
from app.policy.llm import build_llm_prompt, decide_llm, PROMPT_VERSION, SYSTEM_PROMPT
from app.policy.rules import decide, rule_readiness, POLICY_VERSION
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import SocialState, TemporalConfig
from app.state.tracks import TrackConfig
from evaluation.audit_recordings import audit_recording
from evaluation.encounters import aggregate, build_contract, canonical, human_scores, select_prior, sha256


def classify_pair(item, *, timestamp_us, max_age_s, client=None):
    if item is None:
        return {'rule': {'status': 'NOT_READY', 'action': None, 'reason': 'NO_PRIOR_OBSERVATION'},
                'llm': {'status': 'NOT_READY', 'action': None, 'reason': 'NO_PRIOR_OBSERVATION'}}
    # Freeze once and deserialize identical bytes for both classifiers.
    state_json = build_llm_prompt(item.state).social_state_json
    state = SocialState.model_validate_json(state_json)
    age_s = (timestamp_us - state.robot_timestamp_us) / 1e6
    reason = rule_readiness(state, stale=age_s > max_age_s)
    common = {'state_sha256': hashlib.sha256(state_json.encode()).hexdigest(),
              'state_age_s': age_s, 'readiness': 'NOT_READY' if reason else 'READY',
              'readiness_reason': reason, 'source_state_id': state.state_id,
              'source_timestamp_us': state.robot_timestamp_us, 'source_line': item.source['line_number'],
              'social_state': json.loads(state_json), 'error': None, 'duration_s': None,
              'execution_status': 'NOT_EXECUTED_OFFLINE'}
    if reason:
        outcome = {**common, 'status': 'NOT_READY', 'action': None, 'reason': reason}
        return {'rule': dict(outcome), 'llm': dict(outcome)}
    started = time.perf_counter()
    rule = decide(state)
    rule_out = {**common, 'status': 'DECIDED', 'action': rule.decision, 'reason': rule.reason_code,
                'duration_s': time.perf_counter() - started}
    if client is None:
        model_out = {**common, 'status': 'NOT_RUN', 'action': None, 'reason': 'MODEL_NOT_REQUESTED'}
    else:
        result = decide_llm(SocialState.model_validate_json(state_json), client).to_dict()
        model_out = {**common, **result, 'status': 'DECIDED' if result['ok'] else 'ERROR',
                     'action': result['decision']['action'] if result['decision'] else None,
                     'reason': result['decision']['reason'] if result['decision'] else None,
                     'duration_s': result['request_duration_s']}
    return {'rule': rule_out, 'llm': model_out}


def evaluate(contract, reference, output, *, temporal=None, tracking=None, client=None,
             diagnostic_interval_s=None):
    temporal, tracking = temporal or TemporalConfig(), tracking or TrackConfig()
    if tracking.history_window_s < temporal.window_s:
        raise ValueError('tracking history must cover temporal window')
    output = Path(output)
    # Refuse source changes and invalid endpoint contracts before creating output.
    for row in contract['scenarios']:
        if sha256(row['recording']) != row['source_sha256'] or sha256(row['camera_manifest']) != row['camera_manifest_sha256']:
            raise ValueError('source hash differs from frozen evaluation contract')
        if any(type(t) is not int or t < 0 for t in row['endpoints'].values()):
            raise ValueError('endpoint timestamps must be nonnegative integers')
        if row['survey_video_mapping'] == 'VERIFIED' and 'survey_video_end' not in row['endpoints']:
            raise ValueError('verified video mapping requires an explicit survey_video_end timestamp')
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'version': 'encounter-comparison-v2', 'contract': contract,
                'human_reference': reference, 'temporal': temporal.model_dump(), 'tracking': asdict(tracking),
                'policy_version': POLICY_VERSION, 'prompt_version': PROMPT_VERSION,
                'system_prompt': SYSTEM_PROMPT, 'system_prompt_sha256': hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
                'model_config': client.config.model_dump() if client else None,
                'python': platform.python_version(),
                'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'code_sha256': {str(p): sha256(p) for folder in ('app', 'evaluation', 'robot/navel_client')
                                for p in sorted(Path(folder).rglob('*.py'))},
                'diagnostic_interval_s': diagnostic_interval_s,
                'design': 'development recordings; open-loop; no execution; unverified video mapping'}
    if client:
        for endpoint in ('tags', 'version'):
            try:
                with urlopen(client.config.base_url + '/api/' + endpoint, timeout=5) as response:
                    manifest['ollama_' + endpoint] = json.load(response)
            except OSError as error:
                manifest['ollama_' + endpoint] = {'error': str(error)}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    encounters, diagnostics, audits = [], [], {}
    with (output / 'states.jsonl').open('x') as trace:
        for row in contract['scenarios']:
            if row['survey_video_mapping'] != 'VERIFIED':
                for policy in ('rule', 'llm'):
                    encounters.append({'scenario_id': row['scenario_id'], 'condition': 'survey_video_end',
                                       'endpoint_timestamp_us': None, 'policy': policy,
                                       'status': 'NOT_READY', 'readiness': 'NOT_READY', 'action': None,
                                       'reason': 'TRIM_OFFSETS_MISSING', 'readiness_reason': 'TRIM_OFFSETS_MISSING',
                                       'error': None, 'duration_s': None, 'social_state': None,
                                       'execution_status': 'NOT_EXECUTED_OFFLINE',
                                       'survey_video_mapping': row['survey_video_mapping'],
                                       'human_scores_are_conditional': False,
                                       'human': human_scores(None, reference['scenarios'][row['scenario_id']], status='NOT_READY')})
            items = list(iter_replay_states(row['recording'], 'sdk', temporal_config=temporal, track_config=tracking))
            counts = Counter()
            for item in items:
                state = item.state
                hold = rule_readiness(state)
                counts['NOT_READY' if hold else decide(state).decision] += 1
                trace.write(canonical({'scenario_id': row['scenario_id'], 'social_state': state.model_dump(mode='json'),
                                       'readiness_reason': hold, 'source_line': item.source['line_number']}) + '\n')
            audits[row['scenario_id']] = {**audit_recording(row['recording']), 'frame_rule_counts': dict(counts)}
            for condition, timestamp in row['endpoints'].items():
                pair = classify_pair(select_prior(items, timestamp), timestamp_us=timestamp,
                                     max_age_s=contract['max_state_age_s'], client=client)
                for policy, result in pair.items():
                    encounters.append({'scenario_id': row['scenario_id'], 'condition': condition,
                                       'endpoint_timestamp_us': timestamp, 'policy': policy, **result,
                                       'survey_video_mapping': row['survey_video_mapping'],
                                       'human_scores_are_conditional': condition != 'survey_video_end' or row['survey_video_mapping'] != 'VERIFIED',
                                       'human': human_scores(result['action'], reference['scenarios'][row['scenario_id']],
                                                             status=result['status'])})
            if diagnostic_interval_s is not None:
                last = None
                for item in items:
                    now = item.state.robot_timestamp_us
                    if last is not None and now - last < round(diagnostic_interval_s * 1e6):
                        continue
                    last = now
                    pair = classify_pair(item, timestamp_us=now, max_age_s=contract['max_state_age_s'], client=client)
                    diagnostics.append({'scenario_id': row['scenario_id'], 'timestamp_us': now, 'policies': pair})
            print(row['scenario_id'] + ': complete', flush=True)
    (output / 'encounters.jsonl').write_text(''.join(canonical(r) + '\n' for r in encounters))
    (output / 'diagnostics.jsonl').write_text(''.join(canonical(r) + '\n' for r in diagnostics))
    (output / 'sensor-audit.json').write_text(json.dumps(audits, indent=2) + '\n')
    summary = {condition: {policy: aggregate([r for r in encounters if r['condition'] == condition and r['policy'] == policy])
                            for policy in ('rule', 'llm')} for condition in sorted({r['condition'] for r in encounters})}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract', type=Path, default=Path('config/evaluation-contract.json'))
    parser.add_argument('--create-contract', action='store_true')
    parser.add_argument('--recordings', type=Path, default=Path('var/recordings'))
    parser.add_argument('--human-reference', type=Path, default=Path('config/human-reference.json'))
    parser.add_argument('--temporal-config', type=Path, default=Path('config/temporal-state.json'))
    parser.add_argument('--track-config', type=Path, default=Path('config/person-tracking.json'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--model')
    parser.add_argument('--base-url', default='http://127.0.0.1:11434')
    parser.add_argument('--diagnostic-interval-s', type=float)
    args = parser.parse_args(argv)
    if args.create_contract:
        with args.contract.open('x') as stream:
            json.dump(build_contract(args.recordings), stream, indent=2)
        return 0
    if args.output is None:
        parser.error('--output is required')
    import math
    if args.diagnostic_interval_s is not None and (not math.isfinite(args.diagnostic_interval_s) or args.diagnostic_interval_s <= 0):
        parser.error('diagnostic interval must be finite and positive')
    client = OllamaClient(OllamaConfig(base_url=args.base_url, model=args.model,
                                      temperature=0.0, seed=42, num_ctx=8192, num_predict=192,
                                      timeout_seconds=120.0)) if args.model else None
    evaluate(json.loads(args.contract.read_text()), json.loads(args.human_reference.read_text()), args.output,
             temporal=TemporalConfig.from_file(args.temporal_config), tracking=TrackConfig.from_file(args.track_config),
             client=client, diagnostic_interval_s=args.diagnostic_interval_s)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
