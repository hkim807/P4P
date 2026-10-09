"""Small declared temporal screen; encounter outcomes stay separate from frames."""
from itertools import product
import argparse
from collections import Counter
import json
from pathlib import Path

from app.policy.rules import decide, rule_readiness
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import TemporalConfig
from evaluation.encounters import human_scores


def screen():
    base = json.loads(Path('config/temporal-state.json').read_text())
    humans = json.loads(Path('config/human-reference.json').read_text())['scenarios']
    results = []
    # Coverage targets 0.6/0.8 s, dwell 0.1/0.2/0.3 s, valid gaps .25/.35 s.
    # Keep distance span 1 s, gaze coverage fraction .6 and identity unchanged.
    for coverage, dwell, gap in product((0.8, 0.6), (0.3, 0.2, 0.1), (0.25, 0.35)):
        config = TemporalConfig(**(base | {'min_gaze_coverage_s': coverage,
                                          'category_dwell_s': dwell, 'max_gap_s': gap}))
        endpoints, counts = {}, Counter()
        for n in range(1, 10):
            items = list(iter_replay_states(f'var/recordings/scenario-{n:02d}.sdk.jsonl', 'sdk', temporal_config=config))
            for item in items:
                hold = rule_readiness(item.state)
                counts['NOT_READY' if hold else decide(item.state).decision] += 1
            hold = rule_readiness(items[-1].state)
            action = None if hold else decide(items[-1].state).decision
            endpoints[f'S{n}'] = {'status': 'NOT_READY' if hold else 'DECIDED', 'action': action,
                                  'reason': hold, 'conditional_human': human_scores(action, humans[f'S{n}'])}
        results.append({'config': config.model_dump(), 'config_version': config.version,
                        'sdk_end_diagnostic': endpoints, 'frame_diagnostics': dict(counts),
                        'rationale': 'Test redundant attention warmup delay while retaining current detection, gap reset, 60% coverage and 1-second distance fit.'})
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    with args.output.open('x') as stream:
        json.dump(screen(), stream, indent=2)


if __name__ == '__main__':
    main()
