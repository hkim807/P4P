"""Causal endpoint selection and human scores; no labels enter robot policies."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.domain.actions import ACTIONS


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_contract(recordings):
    """Declare endpoints before tuning. Filenames map references only in evaluation.

    Survey-video trims are absent: camera end is explicitly an unverified proxy.
    SDK end is a separate diagnostic condition, never a substitute video verdict.
    """
    scenarios = []
    for n in range(1, 10):
        path = Path(recordings) / f'scenario-{n:02d}.sdk.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        perceptions = [r for r in rows if r['stream'] == 'perception']
        sessions = {r['session_id'] for r in rows}
        camera = path.with_name(path.name.replace('.sdk.jsonl', '.cameras')) / 'frames.jsonl'
        images = [json.loads(line) for line in camera.read_text().splitlines()]
        images = [r for r in images if r['event'] == 'frame' and r['camera'] == 'head']
        if len(sessions) != 1 or any(r['session_id'] not in sessions for r in images):
            raise ValueError(f'{path}: incompatible capture sessions')
        end = max(images, key=lambda r: r['received_monotonic_us'])
        scenarios.append({
            'scenario_id': f'S{n}', 'recording': str(path), 'source_sha256': sha256(path),
            'camera_manifest': str(camera), 'camera_manifest_sha256': sha256(camera),
            'capture_session': perceptions[0]['session_id'],
            'survey_video_mapping': 'UNVERIFIED',
            'endpoints': {
                'camera_end_proxy': end['received_monotonic_us'],
                'sdk_end_diagnostic': perceptions[-1]['received_monotonic_us'],
            },
            'camera_end_frame': end['file'], 'camera_end_sequence': end['sequence'],
            'camera_tail_without_sdk_s': (end['received_monotonic_us'] - perceptions[-1]['received_monotonic_us']) / 1e6,
        })
    return {'version': 'survey-endpoint-v1', 'clock': 'co-capture robot-host receipt monotonic microseconds',
            'max_state_age_s': 0.25,
            'selection': 'last perception at or before endpoint; never last READY or future frame',
            'survey_contract': 'immediate next action at video end; video-to-capture trims unavailable',
            'scenarios': scenarios}


def select_prior(items, timestamp_us):
    """Future observations cannot affect the chosen snapshot."""
    selected = None
    for item in items:
        if item.state.robot_timestamp_us > timestamp_us:
            break
        selected = item
    return selected


def human_scores(action, reference, *, status='DECIDED'):
    if status != 'DECIDED' or action not in ACTIONS:
        return {key: None for key in ('preferred_count', 'preference_support', 'mean_appropriateness',
                                     'rating_n', 'modal_agreement', 'original_agreement')}
    counts = reference['preferred_counts']
    maximum = max(counts.get(a, 0) for a in ACTIONS)
    return {'preferred_count': counts.get(action, 0),
            'preference_support': counts.get(action, 0) / reference['respondents'],
            'mean_appropriateness': reference['mean_appropriateness'][action],
            'rating_n': reference['rating_n'][action],
            'modal_agreement': counts.get(action, 0) == maximum,
            'original_agreement': action == reference['original_intended']}


def aggregate(rows):
    valid = [r for r in rows if r['status'] == 'DECIDED']
    from statistics import mean
    return {'encounters': len(rows), 'decided': len(valid),
            'coverage': len(valid) / len(rows) if rows else 0,
            'mean_preference_support': mean(r['human']['preference_support'] for r in valid) if valid else None,
            'mean_appropriateness': mean(r['human']['mean_appropriateness'] for r in valid) if valid else None,
            'modal_agreement_count': sum(r['human']['modal_agreement'] for r in valid),
            'original_agreement_count': sum(r['human']['original_agreement'] for r in valid)}
