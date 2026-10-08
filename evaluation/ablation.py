"""Policy-only experiment on byte-identical frozen states from the refined replay.

Compare original rules/prompt with refined rules/prompt. No output correction.
This tests semantic consistency, not human agreement at unknown survey trims.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

from app.inference.ollama import OllamaClient, OllamaConfig, OllamaMessage
from app.policy.llm import build_llm_prompt
from app.policy.rules import decide, rule_readiness
from app.state.social_models import SocialState
from evaluation.encounters import canonical, sha256


def compare(directory, output):
    directory, output = Path(directory), Path(output)
    rows = [json.loads(line) for line in (directory / 'diagnostics.jsonl').read_text().splitlines()]
    selected = {}
    for r in rows:
        state = r['policies']['rule']['social_state']
        if rule_readiness(state) is None:
            selected[state['state_id']] = (r['scenario_id'], state)
    for r in [json.loads(line) for line in (directory / 'encounters.jsonl').read_text().splitlines()]:
        if r['policy'] == 'rule' and r['status'] == 'DECIDED':
            state = r['social_state']; selected[state['state_id']] = (r['scenario_id'], state)
    legacy = types.ModuleType('legacy_rule_ablation')
    sys.modules[legacy.__name__] = legacy
    old_rule = subprocess.check_output(['git', 'show', '33d9a78:app/policy/rules.py'], text=True)
    # A detached diagnostic module; no controller or production module mutation.
    exec(compile(old_rule, 'original-rule-policy', 'exec'), legacy.__dict__)
    old_llm = subprocess.check_output(['git', 'show', '33d9a78:app/policy/llm.py'], text=True)
    old_prompt_module = types.ModuleType('legacy_llm_ablation')
    sys.modules[old_prompt_module.__name__] = old_prompt_module
    exec(compile(old_llm, 'original-llm-policy', 'exec'), old_prompt_module.__dict__)
    client = OllamaClient(OllamaConfig(base_url='http://127.0.0.1:11434', model='qwen2.5:7b',
                                      timeout_seconds=120.0, temperature=0.0, seed=42,
                                      num_ctx=8192, num_predict=192))
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'design': 'fixed canonical refined states; policy-only ablation; no images or survey labels',
                'source_directory': str(directory),
                'source_digests': {name: sha256(directory / name) for name in ('diagnostics.jsonl', 'encounters.jsonl')},
                'original_commit': '33d9a78', 'old_prompt': old_prompt_module.SYSTEM_PROMPT,
                'new_prompt': build_llm_prompt(SocialState.model_validate(next(iter(selected.values()))[1])).instructions,
                'model_config': client.config.model_dump(), 'states': len(selected)}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (output / 'results.jsonl').open('x') as stream:
        for scenario, value in selected.values():
            state = SocialState.model_validate(value)
            frozen = build_llm_prompt(state)
            before, after = legacy.decide(state), decide(state)
            for version, instructions in (('original-v3', old_prompt_module.SYSTEM_PROMPT),
                                           ('refined-v6', frozen.instructions)):
                result = client.chat((OllamaMessage(role='system', content=instructions), frozen.messages[1]))
                stream.write(canonical({'scenario_id': scenario, 'state_id': state.state_id,
                                         'state_sha256': hashlib.sha256(frozen.social_state_json.encode()).hexdigest(),
                                         'social_state_json': frozen.social_state_json,
                                         'prompt_condition': version, 'rule_before': before.decision,
                                         'rule_after': after.decision,
                                         'action': result.decision.action if result.decision else None,
                                         'reason': result.decision.reason if result.decision else None,
                                         'raw_content': result.raw_content,
                                         'requested_model': result.requested_model, 'returned_model': result.returned_model,
                                         'duration_s': result.request_duration_s,
                                         'error': None if result.error is None else {'category': result.error.category.value,
                                                                                   'message': result.error.message}}) + '\n')
                stream.flush()
            print(scenario + ' ' + state.state_id + ': compared', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--states-from', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    compare(args.states_from, args.output)


if __name__ == '__main__':
    main()
