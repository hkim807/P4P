"""Build the prompt comparison report from saved model calls."""
import json
from pathlib import Path


ROOT = Path(__file__).parent
RUN = ROOT / 'comparison-01'


def main():
    manifest = json.loads((RUN / 'manifest.json').read_text())
    results = json.loads((RUN / 'results.json').read_text())
    summary = json.loads((RUN / 'summary.json').read_text())
    version = manifest['current_prompt_version']
    lookup = {(r['condition'], r['scenario_id']): r for r in results}
    old, new = summary
    reduction = 100 * (1 - new['median_http_s_excluding_first'] / old['median_http_s_excluding_first'])
    lines = ['# Independent social-reasoning prompt: v13', '',
        'The system prompt now asks the LLM to make its own social-navigation judgment from the shared '
        'SocialState. It defines the four executable actions, social objectives, essential evidence meanings, '
        'and a strict JSON response. It removes the rule algorithm, numeric action cutoffs, estimator internals '
        'and repeated exception lists. One sentence of at most 25 words is requested for the explanation.', '',
        '**System prompt: 1,556 → 363 words (77% shorter).** The complete SocialState, shared readiness gate, '
        'output schema, model and generation settings are unchanged. This comparison still uses engineered '
        'temporal categories, not raw camera perception. The 25-word instruction is not enforced by truncating '
        'or rewriting responses.', '',
        '[Old prompt](prompt-v12.txt) · [New prompt](prompt-v13.txt) · '
        '[Manifest](comparison-01/manifest.json) · [Exact requests and responses](comparison-01/results.json)', '',
        '## Identical-state comparison', '',
        'One sweep of v12 followed by one sweep of v13 on the nine saved first-eligible SocialStates from '
        'the trimmed recordings. Each pair uses identical serialized observation bytes. Scenario labels, '
        'desired actions and rule outputs were not sent to the model. No output correction or rule fallback '
        'was applied, and the prompt was not revised in response to these nine outputs.', '',
        'This is an offline prompt comparison: timings are HTTP inference durations, not elapsed time '
        'since motion starts. It does not test live freshness acceptance or execute robot actions.', '',
        '| Scenario | v12 action | v13 action | v12 inference (s) | v13 inference (s) |',
        '| --- | --- | --- | ---: | ---: |']
    for i in range(1, 10):
        sid = f'S{i}'
        a, b = lookup['baseline-v12', sid], lookup[version, sid]
        assert a['state_sha256'] == b['state_sha256']
        assert a['request']['messages'][1] == b['request']['messages'][1]
        actions = [(r['decision']['action'] if r['decision'] else 'ERROR') for r in (a, b)]
        lines.append(f"| {sid} | {actions[0]} | {actions[1]} | {a['http_duration_s']:.2f} | {b['http_duration_s']:.2f} |")
    lines += ['',
        f"Median inference over all nine calls: **{old['median_http_s']:.2f} → {new['median_http_s']:.2f} s**. "
        f"Excluding the first call of each sweep: **{old['median_http_s_excluding_first']:.2f} → "
        f"{new['median_http_s_excluding_first']:.2f} s**, a {reduction:.0f}% reduction in this run. "
        f"Valid outputs: {old['successful']}/9 before, {new['successful']}/9 after.", '',
        '**Decision quality remains limited.** In this run v13 selected APPROACH for every state with '
        'a detected person and CONTINUE for the two empty states. S1, S4, S6 and S7 differ from the '
        'requested scenario actions. S3 selects APPROACH despite already being within conversation '
        'range, contrary to the action definition. The S1/S4 explanations also attribute relative '
        'closing to human movement without supporting evidence. Valid JSON is not evidence of correct '
        'social judgment. This result supports a latency improvement, not an accuracy improvement.', '',
        'Both sweeps used local `qwen2.5:7b`, temperature 0, seed 42, context 8192 and output limit 192. '
        'The manifest records model identity and placement after the first call of each sweep. '
        'Raw responses retain prompt-token counts, generated-token counts and load/input/generation durations. '
        'Cache residency, model loading and fixed sweep order affect timing; this is one local comparison, '
        'not a general performance benchmark. Shorter instructions do not resolve unavailable GPU acceleration.', '',
        '## Model explanations', '',
    ]
    for i in range(1, 10):
        r = lookup[version, f'S{i}']
        reason = r['decision']['reason'] if r['decision'] else r['error']
        lines.append(f'- **S{i}:** {reason}')
    lines += ['', '## Reproduce', '', 'From the repository root, using a new output directory:', '',
        '```bash', 'python3 -m scripts.compare_social_prompts \\',
        '  --source-run var/evaluation/final-scenario-tuning/run-02 \\',
        '  --baseline-prompt var/evaluation/llm-prompt-v13/prompt-v12.txt \\',
        '  --output var/evaluation/llm-prompt-v13/new-comparison', '```', '',
        'For a real-time replay with v13, use `scripts.replay_first_action` and '
        '`config/policy-comparison-final.json` as described in '
        '[the preset guide](../../../docs/final-scenario-tuning.md). '
        'The original v12 live results in `final-scenario-tuning/run-02` remain unchanged.', '',
        'Verification: 131 prompt, replay, transport and live-model integration tests passed. '
        'See [test log](tests.log). No hardware configuration or physical action code was changed.', '']
    (ROOT / 'README.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    main()
