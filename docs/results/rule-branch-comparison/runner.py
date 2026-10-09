"""Execute one isolated repository's rules; no server, Ollama or robot calls."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo', required=True)
parser.add_argument('--contract', required=True)
parser.add_argument('--source-root', required=True)
parser.add_argument('--temporal', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--shared-states')
parser.add_argument('--cases', required=True)
args = parser.parse_args()
sys.path.insert(0, args.repo)

from app.policy.rules import decide
from app.replay.llm_inputs import iter_replay_states
from app.social_pipeline import SocialPipeline
from app.state.social_models import (PersonSocialState, SocialState, TemporalConfig,
                                     TemporalEvidence)
from app.state.tracks import TrackConfig


def project(state):
    """Drop only fields absent from the selected branch's wire schema."""
    s = {k: v for k, v in state.items() if k in SocialState.model_fields}
    s['config'] = {k: v for k, v in s['config'].items() if k in TemporalConfig.model_fields}
    people = []
    for person in s['people']:
        p = {k: v for k, v in person.items() if k in PersonSocialState.model_fields}
        p['evidence'] = {k: v for k, v in p['evidence'].items() if k in TemporalEvidence.model_fields}
        people.append(p)
    s['people'] = people
    return s


def classify(state):
    parsed = SocialState.model_validate(project(state))
    try:
        decision = decide(parsed)
    except ValueError as error:
        if 'not ready' not in str(error):
            raise
        return {'action': None, 'transport_decision': None,
                'reason': parsed.readiness_reason, 'status': 'NOT_READY'}
    return {'action': None if decision.decision == 'DEFER' else decision.decision,
            'transport_decision': decision.decision, 'reason': decision.reason_code,
            'status': 'NOT_READY' if decision.decision == 'DEFER' else 'DECIDED'}


output = Path(args.output)
output.mkdir(parents=True, exist_ok=False)
contract = json.loads(Path(args.contract).read_text())
results = []
states_path = output / 'states.jsonl.gz'
rows_path = output / 'frames.jsonl.gz'
if args.shared_states:
    with gzip.open(args.shared_states, 'rt') as stream:
        shared = [json.loads(line) for line in stream]
    rows = [{**{k: r[k] for k in ('scenario_id', 'frame', 'offset_s', 'line_number', 'source_sha256')},
             **classify(r['state'])} for r in shared]
    with gzip.open(rows_path, 'wt') as stream:
        for row in rows:
            stream.write(json.dumps(row) + '\n')
    for scene in contract['scenarios']:
        selected = [r for r in rows if r['scenario_id'] == scene['scenario_id']]
        results.append({'scenario_id': scene['scenario_id'], 'frames': len(selected),
                        'action_counts': dict(Counter(r['action'] or 'NO_ACTION' for r in selected)),
                        'first_action': next((r for r in selected if r['action']), None),
                        'sdk_end': selected[-1]})
else:
    temporal = TemporalConfig.from_file(args.temporal)
    tracking = TrackConfig.from_file(Path(args.repo) / 'config/person-tracking.json')
    with gzip.open(states_path, 'wt') as states, gzip.open(rows_path, 'wt') as rows:
        for scene in contract['scenarios']:
            pipe = None
            start = None
            selected = []
            source = Path(args.source_root) / scene['recording']
            for index, item in enumerate(iter_replay_states(source, 'sdk', track_config=tracking,
                                                            temporal_config=temporal), 1):
                if pipe is None:
                    pipe = SocialPipeline(item.state.session_id, tracking, temporal)
                raw = item.source['reconstructed_raw_observation']
                start = raw['timestamp'] if start is None else start
                snapshot = pipe.process(raw)
                metadata = {'scenario_id': scene['scenario_id'], 'frame': index,
                            'offset_s': (raw['timestamp'] - start) / 1e6,
                            'line_number': item.source['line_number'],
                            'source_sha256': item.source['file_sha256']}
                states.write(json.dumps({**metadata, 'state': snapshot['social_state']}) + '\n')
                row = {**metadata, **classify(snapshot['social_state'])}
                proposal = snapshot['policy_decision']
                assert row['transport_decision'] == (proposal['decision'] if proposal else None)
                rows.write(json.dumps(row) + '\n')
                selected.append(row)
            results.append({'scenario_id': scene['scenario_id'], 'frames': len(selected),
                'action_counts': dict(Counter(r['action'] or 'NO_ACTION' for r in selected)),
                'first_action': next((r for r in selected if r['action']), None), 'sdk_end': selected[-1]})
(output / 'summary.json').write_text(json.dumps(results, indent=2) + '\n')
cases = json.loads(Path(args.cases).read_text())
(output / 'cases.json').write_text(json.dumps([
    {'condition': case['condition'], **classify(case['state'])} for case in cases], indent=2) + '\n')
print(json.dumps(results, indent=2))
