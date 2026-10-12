"""Generate the stage 1/2 report from frozen inputs and completed measurements."""
import argparse
from collections import Counter
import json
import hashlib
from pathlib import Path
import statistics

from scripts.profile_llm_baseline import verify_freeze, write_json
from app.policy.llm import build_llm_prompt
from app.state.social_models import SocialState


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    root = args.output.resolve()
    manifest = verify_freeze(root)
    protocol = json.loads((root/'freeze/protocol.json').read_text())
    rows = json.loads((root/'measurements/operational-summary.json').read_text())
    later = json.loads((root/'measurements/later-summary.json').read_text())
    cold = json.loads((root/'measurements/cold/summary.json').read_text())
    profiled_calls = 0
    for path in (root/'measurements').rglob('calls.json'):
        for index, call in enumerate(json.loads(path.read_text()), 1):
            request = json.loads((path.parent/f'call-{index:03d}.request.json').read_text())
            raw = json.dumps(request, allow_nan=False, separators=(',', ':')).encode()
            if hashlib.sha256(raw).hexdigest() != call['request_sha256']:
                raise ValueError(f'Request bytes differ from captured hash: {path}')
            # Reconstruct exact request bytes, verified against the transport hash.
            (path.parent/f'call-{index:03d}.request.raw.json').write_bytes(raw)
            profiled_calls += 1
    expected = {(n,s['scenario_id']) for n in range(1,protocol['repetitions']+1) for s in protocol['scenarios']}
    if Counter((r['repetition'],r['scenario_id']) for r in rows) != Counter(expected):
        raise ValueError('Operational results are incomplete or duplicated')
    paired = []
    by_scene = {s['scenario_id']: s for s in protocol['scenarios']}
    for row in rows:
        first = by_scene[row['scenario_id']]['snapshots']['first']
        folder = root / f'measurements/repeat-{row["repetition"]}/{row["scenario_id"]}'
        events = [json.loads(line) for line in (folder/'live/events.jsonl').read_text().splitlines()]
        event = next(e for e in events if e.get('source_timestamp_us') == first['timestamp_us'])
        response = event['response']
        prompt = build_llm_prompt(SocialState.model_validate(response['social_state']))
        call = json.loads((folder/'profile-1/calls.json').read_text())[0]
        if (hashlib.sha256(prompt.social_state_json.encode()).hexdigest() != first['state_sha256']
                or call['state_sha256'] != first['state_sha256']
                or response['final_decision'] != first['rule_decision']):
            raise ValueError(f'Policy input or frozen rule output differs: {row["scenario_id"]}')
        paired.append({'repetition': row['repetition'], 'scenario_id': row['scenario_id'],
            'input_state_sha256': first['state_sha256'], 'rule_decision': response['final_decision'],
            'receiver_ready_frame_processing_s': event['receiver_request_duration_s']})
    write_json(root/'measurements/paired-input-verification.json', paired)
    times = [r['timing'] for r in rows if r['timing']]
    complete = [t for t in times if all(t[k] is not None for k in
        ('total_duration_s','load_duration_s','prompt_eval_duration_s','eval_duration_s'))]
    primary_delay = [t['eligible_to_observed_output_s'] for t in times if 'eligible_to_observed_output_s' in t]
    summary = {
        'baseline_git_head': manifest['git_head'], 'operational_trials': len(rows),
        'profiled_calls_and_request_hashes_verified': profiled_calls,
        'valid_first_outputs': sum(r['first_output_valid'] for r in rows),
        'aligned_first_outputs': sum(r['action_aligned'] for r in rows),
        'accepted_first_outputs': sum(r['accepted_by_live_client'] is True for r in rows),
        'rules_aligned': sum(s['snapshots']['first']['rule_decision']['action'] in s['expected_actions'] for s in protocol['scenarios']),
        'rules_total': len(protocol['scenarios']),
        'median_eligible_to_output_s': statistics.median(primary_delay),
        'min_eligible_to_output_s': min(primary_delay), 'max_eligible_to_output_s': max(primary_delay),
        'native_total_s': sum(t['total_duration_s'] for t in complete),
        'native_load_s': sum(t['load_duration_s'] for t in complete),
        'native_prompt_s': sum(t['prompt_eval_duration_s'] for t in complete),
        'native_generation_s': sum(t['eval_duration_s'] for t in complete),
        'median_prompt_tokens': statistics.median(t['prompt_tokens'] for t in times if t['prompt_tokens'] is not None),
        'median_output_tokens': statistics.median(t['output_tokens'] for t in times if t['output_tokens'] is not None),
        'later_diagnostics': len(later), 'cold_probe': cold['timing'],
        'paired_inputs_verified': len(paired),
        'median_receiver_ready_frame_processing_s': statistics.median(p['receiver_ready_frame_processing_s'] for p in paired),
        'median_request_preparation_s': statistics.median(t['request_preparation_s'] for t in times),
        'median_response_validation_s': statistics.median(t['response_validation_s'] for t in times),
        'median_pre_inference_processing_and_scheduling_s': statistics.median(t['eligible_to_client_call_s'] for t in times if 'eligible_to_client_call_s' in t),
        'median_finished_to_observed_output_s': statistics.median(t['client_finished_to_observed_output_s'] for t in times if 'client_finished_to_observed_output_s' in t),
        'per_repetition': [{
            'repetition': n, 'aligned': sum(r['action_aligned'] for r in rows if r['repetition']==n),
            'accepted': sum(r['accepted_by_live_client'] is True for r in rows if r['repetition']==n),
            'median_eligible_to_output_s': statistics.median(r['timing']['eligible_to_observed_output_s'] for r in rows if r['repetition']==n and r['timing'] and 'eligible_to_observed_output_s' in r['timing'])
        } for n in range(1,protocol['repetitions']+1)],
    }
    for key in ('load','prompt','generation'):
        summary['native_'+key+'_percent'] = 100*summary['native_'+key+'_s']/summary['native_total_s']
    summary['native_other_percent'] = 100-sum(summary['native_'+key+'_percent'] for key in ('load','prompt','generation'))
    write_json(root/'measurements/aggregate.json', summary)
    parts = [
        '# Frozen baseline and inference profile — stages 1 and 2', '',
        f"Frozen commit: `{manifest['git_head']}` on `{manifest['branch']}`. All implementation/configuration and frozen-file hashes were verified after measurement.", '',
        'The first eligible decision and time to its complete output are the operational result. The full current SocialState, prompt v14, qwen2.5:7b, temperature 0, seed 42, 8192 context and 192 output tokens were preserved. No production files or policy thresholds were changed.', '',
        f"Three warm-model repetitions produced {summary['valid_first_outputs']}/{len(rows)} valid first outputs, {summary['aligned_first_outputs']}/{len(rows)} aligned outputs and {summary['accepted_first_outputs']}/{len(rows)} accepted first outputs. The frozen rule decisions align in {summary['rules_aligned']}/9 scenarios.", '',
        'This counts action agreement, not execution correctness. Acceptance is separate and can fail after a finite clip ends. S3 and S8 retain multiple allowed actions; S8 first resolves from an empty scene.', '',
        f"Median first-eligible-to-observed-output time: **{summary['median_eligible_to_output_s']:.2f} s**; range **{summary['min_eligible_to_output_s']:.2f}–{summary['max_eligible_to_output_s']:.2f} s**.", '',
        '## Primary first-decision results', '',
        '| Scenario | Expected action(s) | First eligible after motion | First output by repetition | Eligible → output, repetitions 1 / 2 / 3 | Motion → output, repetitions 1 / 2 / 3 | Accepted, repetitions 1 / 2 / 3 |',
        '| --- | --- | ---: | --- | --- | --- | --- |',
    ]
    for scene in protocol['scenarios']:
        rs=sorted((r for r in rows if r['scenario_id']==scene['scenario_id']),key=lambda r:r['repetition'])
        actions=' / '.join(r['first_decision']['action'] if r['first_decision'] else 'NO_VALID_OUTPUT' for r in rs)
        delays=' / '.join(f"{r['timing']['eligible_to_observed_output_s']:.2f}s" if r['timing'] and 'eligible_to_observed_output_s' in r['timing'] else '—' for r in rs)
        totals=' / '.join(f"{r['output_since_motion_s']:.2f}s" if r['output_since_motion_s'] is not None else '—' for r in rs)
        accepted=' / '.join('Yes' if r['accepted_by_live_client'] else 'No' for r in rs)
        parts.append(f"| {scene['scenario_id']} | {', '.join(scene['expected_actions'])} | {scene['snapshots']['first']['since_motion_s']:.2f}s | {actions} | {delays} | {totals} | {accepted} |")
    parts += ['', '## Where warm inference time goes', '',
        'These percentages use sums over all primary trials, rather than averages of individual percentages. Durations are reported by Ollama; prompt processing includes uncached tokens. Model warmth and prompt-cache warmth are different conditions.', '',
        '| Component | Sum across primary trials | Share of Ollama total |', '| --- | ---: | ---: |']
    for key,label in (('load','Model loading'),('prompt','Prompt processing'),('generation','Answer generation')):
        parts.append(f"| {label} | {summary['native_'+key+'_s']:.2f}s | {summary['native_'+key+'_percent']:.1f}% |")
    parts += [f"| Other native runtime work | — | {summary['native_other_percent']:.1f}% |", '',
        f"Median input count: {summary['median_prompt_tokens']:g} tokens; median output count: {summary['median_output_tokens']:g} tokens.", '',
        f"Outside Ollama: median pre-inference processing/scheduling was {summary['median_pre_inference_processing_and_scheduling_s']:.3f}s; request preparation {summary['median_request_preparation_s']:.6f}s; response validation {summary['median_response_validation_s']:.6f}s; and validation completion to observed output {summary['median_finished_to_observed_output_s']:.3f}s. The median first-ready observation endpoint took {summary['median_receiver_ready_frame_processing_s']:.3f}s (overlapping part of processing/scheduling; do not add these twice).", '',
        '| Scenario | Input tokens, repetitions 1 / 2 / 3 | Cached tokens, repetitions 1 / 2 / 3 | Mean load | Mean prompt | Mean generation | Mean transport minus native total |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: |']
    for scene in protocol['scenarios']:
        ts=[r['timing'] for r in sorted(rows,key=lambda r:r['repetition']) if r['scenario_id']==scene['scenario_id'] and r['timing']]
        tokens=' / '.join(str(t['prompt_tokens']) for t in ts)
        cached=' / '.join(str(t['cached_prompt_tokens']) for t in ts)
        means=[statistics.mean(t[k] for t in ts if t[k] is not None) for k in
            ('load_duration_s','prompt_eval_duration_s','eval_duration_s','transport_minus_native_total_s')]
        parts.append(f"| {scene['scenario_id']} | {tokens} | {cached} | "+' | '.join(f'{m:.3f}s' for m in means)+' |')
    parts += ['', '## Cold-loading probe', '',
        'The model was explicitly unloaded before a separate call on the frozen first S1 state. This is excluded from operational alignment and live acceptance.', '',
        '| Load | Prompt | Generation | Ollama total |', '| ---: | ---: | ---: | ---: |',
        '| '+' | '.join(f"{cold['timing'][k]:.2f}s" for k in ('load_duration_s','prompt_eval_duration_s','eval_duration_s','total_duration_s'))+' |', '',
        '## Later encounter diagnosis', '',
        'These are the frozen last eligible snapshots, evaluated only after all primary runs. They are not replacements for first decisions and do not enter the primary score.', '',
        '| Scenario | Later snapshot after motion | Rule action | LLM action | LLM reason |', '| --- | ---: | --- | --- | --- |']
    for row in later:
        decision=row['llm'].get('decision') or {}
        parts.append(f"| {row['scenario_id']} | {row['snapshot']['since_motion_s']:.2f}s | {row['snapshot']['rule_decision']['action']} | {decision.get('action','NO_VALID_OUTPUT')} | {decision.get('reason','—')} |")
    parts += ['', '## Interpretation and next experiment', '',
        f"Prompt processing accounted for {summary['native_prompt_percent']:.1f}% of warm Ollama time. This supports testing a compact shared decision state next, while preserving the tuned rules and comparing the same first snapshots. It does not yet measure the benefit of that reduction.", '',
        'A warmed model can still have uncached prompt tokens; the initially uncached S1 prompt and later partially cached prompts should not be conflated with loading delay. Repetition order and cache counts are preserved for future comparisons.', '',
        'The wrapper observes unmodified requests/responses. Its small audit-file writing cost occurs after validation and is included in receiver-observed output timing. Pre-inference delay combines replay dispatch, processing, scheduling and prompt construction; transport residual includes local overhead and is not a LAN benchmark. Full timing records remain available for inspection.', '',
        f"- [Frozen protocol]({root}/freeze/protocol.json)",
        f"- [Freeze manifest]({root}/freeze/manifest.json)",
        f"- [Runtime and model identity]({root}/freeze/runtime.json)",
        f"- [Primary results and detailed timing]({root}/measurements/operational-summary.json)",
        f"- [Aggregate metrics]({root}/measurements/aggregate.json)",
        f"- [Later diagnostics]({root}/measurements/later-summary.json)",
        f"- [Verification]({root}/measurements/verification.json)", '',
        f"- [Identical policy-input verification]({root}/measurements/paired-input-verification.json)", '',
        'Each repetition/scenario retains original endpoint events, model outputs and observations, plus the exact profiled request/response and native metrics. The source copy and profiler are preserved under freeze.', '',
        'Reproduction and method are documented in [the profiling guide](/Users/eddiekim/Documents/Projects/P4P/docs/llm-baseline-profiling.md).',
    ]
    (root/'REPORT.md').write_text('\n'.join(parts)+'\n')
    print(json.dumps(summary,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
