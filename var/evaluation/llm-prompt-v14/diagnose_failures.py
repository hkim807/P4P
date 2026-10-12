"""Offline input ablation for failure diagnosis; no production-state changes."""
import json
from pathlib import Path

from app.inference.ollama import OllamaClient, OllamaConfig, OllamaMessage
from app.policy.llm import SOCIAL_STATE_PREFIX
from scripts.compare_social_prompts import AuditTransport


ROOT = Path(__file__).resolve().parent


def main():
    out = ROOT / 'failure-diagnostic-01'
    out.mkdir(exist_ok=False)
    settings = json.loads((ROOT / 'run-01/manifest.json').read_text())['settings']
    config = OllamaConfig(**{key: settings[key] for key in (
        'base_url', 'model', 'temperature', 'seed', 'num_ctx', 'num_predict', 'timeout_seconds')})
    system = (ROOT / 'run-01/system-prompt.txt').read_text().rstrip('\n')
    manifest = {'purpose': 'Input ablation only, not a fair policy comparison or production change',
                'settings': config.model_dump(mode='json'), 'system_prompt': system,
                'conditions': ['Reproduce S9 with complete original input',
                               'S1, S4, S9 with a reduced view of the same decisive social cues'],
                'omitted_from_reduced_view': ['configuration', 'cue_changes', 'tracking events',
                    'raw gaze overlaps and estimator fit statistics', 'raw nose coordinates',
                    'identifiers and versions', 'ages and sample counts'],
                'limitations': 'Single sample per input; changes can show sensitivity but do not reveal internal reasoning.',
                'physical_execution': False}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    results = []
    for condition, sid in [('full', 'S9'), ('reduced', 'S1'), ('reduced', 'S4'), ('reduced', 'S9')]:
        audit = json.loads((ROOT / 'run-01/llm' / sid / 'models.jsonl').read_text().splitlines()[0])
        state = audit['social_state']
        if condition == 'full':
            state_json = audit['social_state_json']
        else:
            state = {'observation_readiness': state['observation_readiness'],
                     'robot': state['robot'], 'people': [{key: p[key] for key in (
                         'visibility', 'latest_distance_m', 'gaze_state', 'distance_zone',
                         'relative_distance_trend', 'path_position', 'path_relation', 'pass_gesture',
                         'face_detected', 'observation_labels')}
                         | {'evidence': {key: p['evidence'][key] for key in (
                             'latest_gaze_looking', 'gaze_fraction', 'gaze_valid',
                             'latest_distance_valid', 'distance_trend_valid')}} for p in state['people']]}
            state_json = json.dumps(state, sort_keys=True, separators=(',', ':'))
        transport = AuditTransport()
        result = OllamaClient(config, transport=transport).chat((
            OllamaMessage(role='system', content=system),
            OllamaMessage(role='user', content=SOCIAL_STATE_PREFIX + state_json)))
        row = {'scenario_id': sid, 'condition': condition,
               'input_characters': len(state_json), 'ok': result.ok,
               'decision': result.decision.model_dump() if result.decision else None,
               'error': result.error.message if result.error else None,
               'request_duration_s': result.request_duration_s,
               'request': transport.request, 'response': transport.response}
        results.append(row)
        (out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps({k: row[k] for k in ('scenario_id', 'condition', 'decision', 'request_duration_s')}), flush=True)


if __name__ == '__main__':
    main()
