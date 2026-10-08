"""Freeze/check source and configuration hashes before collecting live study data."""
import argparse
import json
from pathlib import Path
import platform
import subprocess

from app.domain.actions import ACTION_VERSION
from app.policy.llm import PROMPT_VERSION, SYSTEM_PROMPT
from app.policy.rules import POLICY_VERSION
from app.state.social_models import TemporalConfig
from evaluation.encounters import sha256


def files():
    paths = [p for root in ('app', 'robot/navel_client', 'evaluation') for p in Path(root).rglob('*.py')]
    paths += list(Path('schemas/v1').glob('*.json'))
    paths += [Path(p) for p in ('config/temporal-development-frozen.json', 'config/person-tracking.json',
                               'config/policy-comparison.json',
                               'config/target-lock.json', 'config/commands.json', 'requirements.txt',
                               'requirements-navel.txt')]
    return {str(p): sha256(p) for p in sorted(paths)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--model-manifest', type=Path)
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args(argv)
    if args.verify:
        expected = json.loads(args.verify.read_text())['file_sha256']
        actual = files()
        changed = sorted(k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k))
        if changed:
            parser.exit(1, 'Frozen version differs: ' + ', '.join(changed) + '\n')
        print('Frozen source/configuration hashes verified')
        return 0
    if not args.output or not args.model_manifest:
        parser.error('--output and --model-manifest required when creating freeze')
    model = json.loads(args.model_manifest.read_text())
    freeze = {'version': 'social-development-freeze-v1',
              'scope': 'candidate for later live evaluation; development-recording results only',
              'git_head_at_freeze': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'file_sha256': files(), 'python': platform.python_version(),
              'action_version': ACTION_VERSION, 'policy_version': POLICY_VERSION,
              'prompt_version': PROMPT_VERSION, 'exact_system_prompt': SYSTEM_PROMPT,
              'temporal_config': TemporalConfig.from_file('config/temporal-development-frozen.json').model_dump(),
              'model_config': model['model_config'], 'ollama_tags': model.get('ollama_tags'),
              'ollama_version': model.get('ollama_version'),
              'inference_failure_policy': 'no rule fallback; invalid/error/expired output remains explicit',
              'runtime_information': 'identical structured observations for rule and LLM; VLM is a separate condition',
              'production_limitations': ['SDK has no path/gesture measurement in these recordings',
                                         'no hardware validation performed',
                                         'cold model latency may exceed the default 10-second active-trial expiry'],
              'future_study': 'Prespecify live encounter endpoint/onset before acquisition. Do not retune on new encounters.'}
    with args.output.open('x') as stream:
        json.dump(freeze, stream, indent=2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
