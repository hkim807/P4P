"""Replay production states once; compare independent policies on identical JSON.

python -m evaluation.run_scenarios --output var/evaluation/baseline
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import time
from urllib.request import urlopen

from app.domain.actions import ACTIONS, ACTION_DEFINITIONS, ACTION_VERSION
from app.inference.ollama import OllamaClient, OllamaConfig
from app.policy.llm import build_llm_prompt, classify_llm, PROMPT_VERSION, SYSTEM_PROMPT
from app.policy.rules import decide, POLICY_VERSION
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import SocialState, TemporalConfig, readiness
from app.state.tracks import TrackConfig

VERSION = 'recorded-comparison-v1'

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def load_humans(path):
    if path is None:
        return {}
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError('survey must map scenario IDs to distributions')
    for scenario, row in data.items():
        if not isinstance(row, dict) or set(row) != {'preferred', 'least_appropriate'}:
            raise ValueError(f'{scenario}: require preferred and least_appropriate distributions')
        for name, distribution in row.items():
            if (not isinstance(distribution, dict) or not distribution or
                set(distribution) - set(ACTIONS) or
                any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in distribution.values()) or
                not math.isclose(sum(distribution.values()), 1.0, abs_tol=1e-6)):
                raise ValueError(f'{scenario}.{name}: canonical probabilities must sum to one')
    return data

def raw_audit(path):
    records = [json.loads(line) for line in path.read_text().splitlines()]
    frames = [r for r in records if r['stream'] == 'perception']
    people = [p for r in frames for p in r['packet'].get('persons', [])]
    loco = [r['packet']['odometry'] for r in records if r['stream'] == 'locomotion']
    gazes = [p['gaze_overlap'] for p in people if p.get('gaze_overlap') is not None]
    cameras = path.with_name(path.name.replace('.sdk.jsonl', '.cameras')) / 'frames.jsonl'
    camera_rows = [json.loads(l) for l in cameras.read_text().splitlines()] if cameras.exists() else []
    return {'perception_frames': len(frames), 'locomotion_frames': len(loco),
            'person_frames': sum(bool(r['packet'].get('persons')) for r in frames),
            'person_instances': len(people), 'uids': sorted({p['uid'] for p in people}),
            'nonempty_person_fields': dict(Counter(k for p in people for k,v in p.items() if v not in (None, [], {}))),
            'gaze_min': min(gazes, default=None), 'gaze_max': max(gazes, default=None),
            'distance_min_m': min((p['dist_mm']/1000 for p in people if p.get('dist_mm') is not None), default=None),
            'distance_max_m': max((p['dist_mm']/1000 for p in people if p.get('dist_mm') is not None), default=None),
            'linear_velocity_range_mps': [min(o['velocity']['linear_x'] for o in loco), max(o['velocity']['linear_x'] for o in loco)] if loco else None,
            'odometry_displacement_m': math.dist(list(loco[0]['position'].values()), list(loco[-1]['position'].values())) if loco else None,
            'camera_records': len(camera_rows),
            'camera_events': dict(Counter(str(r.get('event')) for r in camera_rows)),
            'camera_streams': dict(Counter(str(r.get('camera')) for r in camera_rows))}

def evaluate(paths, output, *, temporal=None, tracking=None, client=None, repeats=3,
             sample_interval_s=1.0, humans=None, metadata=None):
    """Every frame is processed; sampling applies only to model comparisons.

    Rule decisions are repeated on detached canonical states to assert determinism.
    All model repeats receive byte-identical state JSON and identical settings.
    """
    if repeats < 1 or not math.isfinite(sample_interval_s) or sample_interval_s < 0:
        raise ValueError('repeats must be positive; interval finite and nonnegative')
    temporal, tracking = temporal or TemporalConfig(), tracking or TrackConfig()
    if tracking.history_window_s < temporal.window_s or tracking.max_samples_per_track < temporal.min_samples:
        raise ValueError('tracking history/capacity must cover temporal evidence requirements')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'version': VERSION, 'action_version': ACTION_VERSION, 'actions': ACTION_DEFINITIONS,
                'rule_version': POLICY_VERSION, 'prompt_version': PROMPT_VERSION,
                'system_prompt': SYSTEM_PROMPT, 'system_prompt_sha256': digest(SYSTEM_PROMPT),
                'temporal_config': temporal.model_dump(), 'track_config': asdict(tracking),
                'model_config': client.config.model_dump() if client else None,
                'repeats': repeats, 'sample_interval_s': sample_interval_s,
                'python': platform.python_version(), 'created_unix_s': time.time(),
                'human_reference': humans or None, 'metadata': metadata or {},
                'sources': [{'name': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
                'design': 'development/calibration set; open-loop decisions; no robot commands'}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    with (output/'states.jsonl').open('x') as states_file, (output/'decisions.jsonl').open('x') as decisions_file:
        for path in paths:
            scenario = path.name.removesuffix('.sdk.jsonl')
            last_sample = None
            for item in iter_replay_states(path, 'sdk', temporal_config=temporal, track_config=tracking):
                prompt = build_llm_prompt(item.state)
                # Both policies receive the same detached, fully validated payload.
                state = SocialState.model_validate_json(prompt.social_state_json)
                sha = digest(prompt.social_state_json)
                started = time.perf_counter()
                rule = decide(state)
                rule_latency = time.perf_counter()-started
                assert rule.model_dump() == decide(SocialState.model_validate_json(prompt.social_state_json)).model_dump()
                status, reason = readiness(state)
                row = {'scenario_id': scenario, 'state_id': state.state_id,
                       'timestamp_us': state.robot_timestamp_us, 'state_sha256': sha,
                       'social_state': json.loads(prompt.social_state_json),
                       'readiness': status, 'readiness_reason': reason,
                       'source_line': item.source['line_number'],
                       'rule': {'status': rule.status, 'action': rule.action, 'reason': rule.reason_code,
                                'target_uid': rule.target_uid, 'target_track_epoch': rule.target_track_epoch,
                                'duration_s': rule_latency}}
                states_file.write(canonical(row)+'\n')
                now = state.robot_timestamp_us
                if last_sample is not None and now-last_sample < round(sample_interval_s*1e6):
                    continue
                last_sample = now
                llm = []
                for repeat in range(repeats if client else 0):
                    result = classify_llm(SocialState.model_validate_json(prompt.social_state_json), client)
                    llm.append({'repeat': repeat+1, 'input_sha256': sha, **result})
                decisions_file.write(canonical({k: v for k, v in row.items() if k != 'social_state'} | {'llm': llm})+'\n')
                decisions_file.flush()
            print(f'{scenario}: replay complete', flush=True)
    from evaluation.audit_recordings import audit_recording
    audits = {p.name.removesuffix('.sdk.jsonl'): audit_recording(p) for p in paths}
    (output/'sensor-audit.json').write_text(json.dumps(audits, indent=2)+'\n')
    from evaluation.summarize import summarize
    return summarize(output)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recordings', type=Path, default=Path('var/recordings'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', help='Omit for rule-only; never auto-download models')
    parser.add_argument('--base-url', default='http://127.0.0.1:11434')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--sample-interval-s', type=float, default=1.0)
    parser.add_argument('--temperature', type=float, default=0.0)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--num-ctx', type=int, default=8192)
    parser.add_argument('--timeout', type=float, default=120.0)
    parser.add_argument('--temporal-config', type=Path, default=Path('config/temporal-state.json'))
    parser.add_argument('--track-config', type=Path, default=Path('config/person-tracking.json'))
    parser.add_argument('--human-reference', type=Path)
    args = parser.parse_args(argv)
    paths = sorted(args.recordings.glob('scenario-*.sdk.jsonl'))
    if not paths:
        parser.error('no scenario-*.sdk.jsonl recordings')
    metadata = {'git_head': subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                'git_dirty': bool(subprocess.check_output(['git','status','--porcelain'], text=True)),
                'code_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                for folder in ('app','evaluation','robot/navel_client') for p in sorted(Path(folder).rglob('*.py'))}}
    client = None
    if args.model:
        client = OllamaClient(OllamaConfig(base_url=args.base_url, model=args.model,
                              timeout_seconds=args.timeout, temperature=args.temperature,
                              seed=args.seed, num_predict=192, num_ctx=args.num_ctx))
        for endpoint in ('tags', 'version'):
            try:
                with urlopen(args.base_url+'/api/'+endpoint, timeout=5) as response:
                    metadata['ollama_'+endpoint] = json.load(response)
            except OSError as error:
                metadata['ollama_'+endpoint] = {'error': str(error)}
    try:
        evaluate(paths, args.output, temporal=TemporalConfig.from_file(args.temporal_config),
                 tracking=TrackConfig.from_file(args.track_config), client=client,
                 repeats=args.repeats, sample_interval_s=args.sample_interval_s,
                 humans=load_humans(args.human_reference), metadata=metadata)
    except (ValueError, OSError) as error:
        parser.exit(1, f'evaluation failed (partial files retained): {error}\n')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
