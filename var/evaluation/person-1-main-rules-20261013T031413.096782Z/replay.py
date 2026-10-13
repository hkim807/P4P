import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--code', required=True)
parser.add_argument('--workspace', required=True)
parser.add_argument('--commit', required=True)
args = parser.parse_args()
code = Path(args.code)
workspace = Path(args.workspace)
sys.path.insert(0, str(code))
os.chdir(code)

from app.replay.llm_inputs import iter_replay_states
from app.server import create_app
from app.state.tracks import TrackConfig
from app.state.social_models import TemporalConfig
from app.policy.rules import POLICY_VERSION
from app.policy.target_lock import LockConfig
from app.commands import CommandConfig

def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')

stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
output = workspace / 'var/evaluation' / ('person-1-main-rules-' + stamp)
output.mkdir(parents=True, exist_ok=False)
track_path = Path('config/person-tracking-final-scenarios.json')
temporal_path = Path('config/temporal-final-scenarios.json')
track_config = TrackConfig.from_file(track_path)
temporal_config = TemporalConfig.from_file(temporal_path)
summaries = []

for number in range(1, 10):
    scenario = f'S{number}'
    recording = workspace / f'var/recordings/person-1-{scenario}-sdk.jsonl'
    rows = [json.loads(line) for line in recording.read_text().splitlines() if line.strip()]
    packet_counts = Counter(row['stream'] for row in rows)
    sessions = set(row['session_id'] for row in rows)
    if len(sessions) != 1:
        raise ValueError(f'{recording}: expected one capture session')
    directory = output / scenario
    directory.mkdir()
    app = None
    client = None
    origin = None
    first = first_person = last = None
    actions = Counter()
    reasons = Counter()
    holds = Counter()
    observed_people = Counter()
    changes = []
    frames = 0
    with (directory / 'pipeline.jsonl').open('x') as trace:
        for item in iter_replay_states(recording, 'sdk', track_config=track_config,
                                       temporal_config=temporal_config):
            raw = item.source['reconstructed_raw_observation']
            if app is None:
                origin = raw['timestamp']
                app = create_app(directory / 'observations.jsonl',
                    social_output=directory / 'social.jsonl',
                    session_id=item.state.session_id,
                    track_config=track_config, temporal_config=temporal_config,
                    lock_config=LockConfig.from_file('config/target-lock.json'),
                    command_config=CommandConfig.from_file('config/commands.json'))
                client = app.test_client()
            response = client.post('/api/v1/observations', json=raw)
            payload = response.json
            if response.status_code != 200 or payload.get('processing_status') != 'complete':
                raise RuntimeError(f'{scenario}: {response.status_code}: {payload}')
            if payload['social_state'] != item.state.model_dump(mode='json'):
                raise AssertionError(f'{scenario}: receiver and replay SocialState differ')
            offset = (raw['timestamp'] - origin) / 1e6
            frames += 1
            observed_people[len(raw['people'])] += 1
            decision = payload['final_decision']
            if decision is None:
                holds[payload['policy_readiness']['reason_code']] += 1
            else:
                action = decision['action']
                proposal = payload['policy_decision']
                entry = {'offset_s': offset, 'source_line': item.source['line_number'],
                         'decision': decision, 'reason_code': proposal['reason_code'],
                         'decision_state': payload['decision_state']}
                first = first or entry
                if raw['people']:
                    first_person = first_person or entry
                last = entry
                actions[action] += 1
                reasons[proposal['reason_code']] += 1
                if not changes or changes[-1]['action'] != action:
                    changes.append({'offset_s': offset, 'action': action,
                                    'reason_code': proposal['reason_code']})
            trace.write(json.dumps({'source_line': item.source['line_number'],
                'source_offset_s': offset, 'response': payload}, separators=(',', ':'),
                allow_nan=False) + '\n')
    summary = {'scenario': scenario, 'input': str(recording),
        'source_sha256': hashlib.sha256(recording.read_bytes()).hexdigest(),
        'sdk_packet_counts': dict(packet_counts), 'perception_frames': frames,
        'duration_s': offset, 'person_count_frames': dict(observed_people),
        'first_decision': first, 'first_decision_with_person': first_person,
        'last_decision': last, 'action_counts': dict(actions),
        'reason_counts': dict(reasons), 'observing_counts': dict(holds),
        'action_transitions': changes}
    if frames != packet_counts['perception'] or frames != sum(actions.values()) + sum(holds.values()):
        raise AssertionError(f'{scenario}: frame count mismatch')
    write_json(directory / 'summary.json', summary)
    summaries.append(summary)
    print(json.dumps({'scenario': scenario, 'frames': frames, 'duration_s': offset,
        'first': first, 'actions': dict(actions), 'holds': dict(holds),
        'transitions': changes}, allow_nan=False), flush=True)

manifest = {'main_commit': args.commit, 'policy_version': POLICY_VERSION,
    'track_config_path': str(track_path), 'temporal_config_path': str(temporal_path),
    'track_config': json.loads(track_path.read_text()),
    'temporal_config': json.loads(temporal_path.read_text()),
    'physical_execution': False, 'model_inference': 'disabled',
    'method': 'All captured perception packets in recorded order through production receiver; fresh state per scenario; recorded receipt timestamps, latest fresh preceding locomotion packet; no transport rate downsampling.',
    'checks': ['Every SDK packet validated by existing SDK replay reader',
               'Every receiver response accepted and processing complete',
               'Receiver SocialState identical to replay reconstruction for every frame',
               'All perception packets accounted for as ready or observing'],
    'replay_limitation': 'SDK receipt-time reconstruction cannot reproduce live outgoing queue drops or the delay between packet receipt and observation collection.',
    'output_directory': str(output), 'scenarios': summaries}
write_json(output / 'summary.json', manifest)
print('OUTPUT_DIRECTORY=' + str(output), flush=True)
