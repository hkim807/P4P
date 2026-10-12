"""Summarise saved results; never call a model or replace its decisions.

Run from the repository root after replay finishes:
python3 var/evaluation/final-scenario-tuning/build_report.py
"""
import json
from pathlib import Path


ROOT = Path(__file__).parent
RUN = ROOT / 'run-02'


def read(path):
    return json.loads(path.read_text())


def main():
    results = read(RUN / 'summary.json')
    indexed = {(r['scenario_id'], r['policy']): r for r in results}
    manifest = read(RUN / 'manifest.json')
    scenes = read(Path(manifest['settings']['evaluation_contract']))['scenarios']
    if len(indexed) != 18:
        raise ValueError('Wait for all nine rule and nine LLM replays to finish')
    rows = []
    for scene in scenes:
        sid = scene['scenario_id']
        rule, llm = (indexed[sid, policy] for policy in ('rules', 'llm'))
        state = json.loads((RUN / 'rules' / sid / 'states.jsonl').read_text().splitlines()[-1])
        visible = [p for p in state['people'] if p['visibility'] == 'OBSERVED']
        row = {'scenario_id': sid, 'expected_actions': scene['expected_actions'],
               'rules': rule, 'llm': llm, 'rule_evidence': visible,
               'empty_observed_s': state['no_person_observed_s']}
        rows.append(row)
    (ROOT / 'report.json').write_text(json.dumps(rows, indent=2) + '\n')
    lines = [
        '# Final-scenario tuning results', '',
        'This report records prompt v12. The current v13 prompt and its separate comparison '
        'are documented in [the prompt revision report](../llm-prompt-v13/README.md).', '',
        'All nine rule-based first actions match the requested options and were accepted by the live client. '
        'The actual `qwen2.5:7b` outputs are recorded separately below; they were not corrected by the rules.', '',
        'Primary run: [run-02 manifest](run-02/manifest.json), '
        '[raw summary](run-02/summary.json), [combined evidence](report.json). '
        'The manifest stores the model digest, generation settings, full effective configurations and working-tree source hashes. '
        'Inputs are the nine motion-trimmed recordings in `../final-scenarios-audit/trimmed/`. '
        'These recordings were used to tune the policy and are not a held-out test set.', '',
        '## First outputs', '',
        'Rule time is the source observation time after robot motion starts. LLM time is elapsed replay wall time '
        'after motion starts, including inference. Both policies submit the same first eligible source state. '
        '“Accepted” means the production `SingleTrial` accepted the output with its ordinary freshness limits. '
        'No physical robot action was executed.', '',
        '| Scenario | Requested | Rule first output | Rule time (s) | LLM first output | LLM time (s) | LLM accepted? |',
        '| --- | --- | --- | ---: | --- | ---: | --- |',
    ]
    for row in rows:
        rule, llm = row['rules'], row['llm']
        offset = (llm['perception_origin_monotonic_us'] - llm['motion_start_monotonic_us']) / 1e6
        elapsed = llm['resolved_elapsed_s']
        when = f'{elapsed + offset:.2f}' if elapsed is not None else '—'
        action = llm['action'] or llm['status']
        lines.append(f"| {row['scenario_id']} | {' / '.join(row['expected_actions'])} | "
                     f"{rule['action']} | {rule['decision_source_since_motion_s']:.2f} | "
                     f"{action} | {when} | {'Yes' if llm['accepted_by_live_client'] else 'No'} |")
    lines += ['', '## Rule evidence at the first decision', '',
              '| Scenario | Distance (m) | Nose lateral (m) | Position | Gaze state | Looking fraction | Relative distance trend |',
              '| --- | ---: | ---: | --- | --- | ---: | --- |']
    for row in rows:
        sid = row['scenario_id']
        if not row['rule_evidence']:
            lines.append(f"| {sid} | — | — | No detection | Unavailable | — | Unknown |")
            continue
        person = row['rule_evidence'][0]
        lines.append(f"| {sid} | {person['latest_distance_m']:.3f} | {person['nose_lateral_m']:.3f} | "
                     f"{person['path_position']} | {person['gaze_state']} | "
                     f"{person['evidence']['gaze_fraction']:.3f} | {person['relative_distance_trend']} |")
    lines += ['',
        '- S1 and S6 have low temporal attention and a nose to the side of the corridor. S4 is inside the corridor with valid closing and low gaze, so it yields.',
        '- S2 and S9 have sustained gaze beyond 2 m, so they approach. S3 is at 1.663 m and engages.',
        '- S5 continues after a contiguous empty interval. Missing detections do not prove the human is absent from the physical scene.',
        '- S6 decides before its later brief glance. Its first output does not demonstrate recognition of that later event.',
        '- S7 resolves with INTERMITTENT gaze. Recurring attention is disabled as an interaction trigger in this preset.',
        '- S8 decides before the human first appears, from an initial empty interval. It does not establish walking-away or sustained-gaze reasoning for that first output.',
        '- S9 uses gaze and distance; no gesture recognition was introduced.', '',
        '## LLM interpretation and delivery', '',
        'The LLM returned CONTINUE for all nine inputs in this run. It disagreed with the desired action in S2, S3, S4 and S9. '
        'Its reasons sometimes contradict the supplied evidence: for example, treating sustained gaze within interaction range '
        'as a reason to continue, or comparing a distance incorrectly. Matching prompt guidance did not produce matching decisions.', '',
        'Only S5 was accepted by the live client. S1–S4, S6, S7 and S9 exceeded the 10 s model-source age limit '
        'and also arrived after the available recording had ended. S8 returned within 10 s, but its current observation '
        'was no longer eligible when the response arrived. Freshness checks were preserved.', '',
        'Each `run-02/llm/S*/models.jsonl` contains the exact model input, raw response, parsed decision and inference duration. '
        'The same directory contains source states, incoming observations, events and the acceptance result. '
        'The earlier [run-01](run-01/summary.json) is also retained: S1 reached its 30 s trial timeout, '
        'then produced a CONTINUE in the model audit after approximately 37.83 s of inference. '
        'That late audit output was not accepted and is not presented as a successful trial.', '',
        '## Reproduction and verification', '',
        'Use the [preset instructions](../../../docs/final-scenario-tuning.md). '
        'Run the report builder from the repository root to regenerate this report from saved run-02 results.', '',
        'The 14 focused tuning/replay tests passed. The full suite ran 632 tests and reported 12 subtest errors '
        'in two existing cancellation tests: this Python 3.10 environment lacks `asyncio.Task.cancelling()`. '
        'The same 12 errors were reproduced on the unmodified baseline. '
        'See [full-suite log](tests-full.log) and [baseline comparison](tests-baseline.log).', '',
    ]
    (ROOT / 'README.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    main()
