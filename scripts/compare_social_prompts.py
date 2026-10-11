"""Compare prompt revisions on identical saved states without robot execution."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import time
from urllib.request import Request, urlopen

from app.inference.ollama import OllamaClient, OllamaConfig, OllamaMessage, UrllibTransport
from app.policy.llm import PROMPT_VERSION, SYSTEM_PROMPT, build_llm_prompt
from app.state.social_models import SocialState


class AuditTransport:
    def __init__(self):
        self.transport = UrllibTransport()
        self.request = self.response = None

    def post(self, url, body, *, timeout_seconds):
        self.request = json.loads(body)
        self.response = None
        response = self.transport.post(url, body, timeout_seconds=timeout_seconds)
        self.response = json.loads(response.body)
        return response


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--baseline-prompt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    settings = json.loads((args.source_run / 'manifest.json').read_text())['settings']
    config = OllamaConfig(**{key: settings[key] for key in (
        'base_url', 'model', 'temperature', 'seed', 'num_ctx', 'num_predict', 'timeout_seconds')})
    baseline = args.baseline_prompt.read_text().rstrip('\n')
    manifest = {'source_run': str(args.source_run), 'configuration': config.model_dump(mode='json'),
        'current_prompt_version': PROMPT_VERSION, 'baseline_prompt': baseline,
        'current_prompt': SYSTEM_PROMPT, 'physical_execution': False,
        'design': 'One sequential baseline sweep, then one current-prompt sweep on identical frozen states. '
                  'Saved states are upgraded to the current shared descriptive labels for BOTH prompts. '
                  'No expected-action labels or rule outputs supplied. Same model, schema and generation limits. '
                  'This measures offline decision latency, not live acceptance. Cache/load effects remain.',
        'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
            Path('app/policy/llm.py'), Path('app/state/social_models.py'),
            Path('app/inference/ollama.py'), Path(__file__))}}
    with urlopen(config.base_url + '/api/tags', timeout=10) as response:
        manifest['ollama_tags'] = json.load(response)
    write(args.output / 'manifest.json', manifest)
    inputs = []
    for index in range(1, 10):
        path = args.source_run / 'llm' / f'S{index}' / 'models.jsonl'
        audit = json.loads(path.read_text().splitlines()[0])
        prompt = build_llm_prompt(SocialState.model_validate(audit['social_state']))
        # Historical audits predate descriptive aliases. Require every original
        # measurement/category to survive; both prompt conditions get the upgrade.
        upgraded = json.loads(prompt.social_state_json)
        original = json.loads(audit['social_state_json'])
        for payload in (upgraded, original):
            for person in payload['people']:
                person.pop('observation_labels', None)
        assert upgraded == original
        inputs.append((f'S{index}', prompt, hashlib.sha256(path.read_bytes()).hexdigest()))
    results = []
    for label, system in [('baseline-v12', baseline), (PROMPT_VERSION, SYSTEM_PROMPT)]:
        for sid, prompt, source_hash in inputs:
            transport = AuditTransport()
            client = OllamaClient(config, transport=transport)
            messages = (OllamaMessage(role='system', content=system), prompt.messages[1])
            result = client.chat(messages)
            body = transport.response or {}
            row = {'scenario_id': sid, 'condition': label, 'source_audit_sha256': source_hash,
                'state_id': prompt.source_state_id,
                'state_sha256': hashlib.sha256(prompt.social_state_json.encode()).hexdigest(),
                'ok': result.ok, 'decision': result.decision.model_dump() if result.decision else None,
                'error': result.error.message if result.error else None,
                'http_duration_s': result.request_duration_s,
                'request': transport.request, 'response': body}
            results.append(row)
            write(args.output / 'results.json', results)
            print(label, sid, row['decision'], round(result.request_duration_s, 3), flush=True)
            if sid == 'S1':
                with urlopen(config.base_url + '/api/ps', timeout=10) as response:
                    manifest[label + '_placement'] = json.load(response)
                write(args.output / 'manifest.json', manifest)
    summary = []
    for label in ('baseline-v12', PROMPT_VERSION):
        cohort = [r for r in results if r['condition'] == label]
        summary.append({'condition': label, 'calls': len(cohort),
            'successful': sum(r['ok'] for r in cohort),
            'median_http_s': statistics.median(r['http_duration_s'] for r in cohort),
            'median_http_s_excluding_first': statistics.median(r['http_duration_s'] for r in cohort[1:]),
            'mean_output_tokens': statistics.mean(r['response'].get('eval_count', 0) for r in cohort)})
    write(args.output / 'summary.json', summary)


if __name__ == '__main__':
    main()
