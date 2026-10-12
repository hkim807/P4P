"""Stage 4: measure descriptions against the sealed stage 3 compact baseline."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import statistics
from unittest.mock import patch

from scripts import evaluate_compact_state as paired
from scripts.profile_llm_baseline import sha, write_json


def verify_stage3(root):
    manifest = json.loads((root/'results-manifest.json').read_text())
    for path, digest in manifest['sha256'].items():
        if sha((root/path).read_bytes()) != digest:
            raise ValueError(f'Stage 3 artifact changed: {path}')
    return sha((root/'results-manifest.json').read_bytes())


def metrics(rows):
    return {
        'trials': len(rows), 'aligned': sum(r['action_aligned'] for r in rows),
        'accepted': sum(r['accepted_by_live_client'] is True for r in rows),
        'aligned_and_accepted': sum(r['action_aligned'] and r['accepted_by_live_client'] is True for r in rows),
        'valid_first_outputs': sum(r['first_output_valid'] for r in rows),
        'median_eligible_to_output_s': statistics.median(r['timing']['eligible_to_observed_output_s'] for r in rows),
        'median_prompt_tokens': statistics.median(r['timing']['prompt_tokens'] for r in rows),
        'per_repetition': [{'repeat': n,
            'aligned': sum(r['action_aligned'] for r in rows if r['repetition'] == n),
            'accepted': sum(r['accepted_by_live_client'] is True for r in rows if r['repetition'] == n)} for n in range(1, 4)],
    }


def report(output, baseline, protocol, rows, later):
    stage3 = Path(protocol['stage3_baseline'])
    if verify_stage3(stage3) != protocol['stage3_manifest_sha256']:
        raise ValueError('Stage 3 comparison manifest changed')
    old = json.loads((stage3/'measurements/operational-summary.json').read_text())
    original = json.loads((baseline/'measurements/operational-summary.json').read_text())
    expected = {(n, s['scenario_id']) for n in range(1, 4) for s in protocol['scenarios']}
    if len(rows) != 27 or {(r['repetition'], r['scenario_id']) for r in rows} != expected:
        raise ValueError('First-decision trials incomplete')
    previous, current = metrics(old), metrics(rows)
    comparison = {'original_full_baseline': metrics(original), 'stage3': previous, 'stage4': current,
        'rule_frames_unchanged': 574, 'eligible_frames_unchanged': 402, 'paired_inputs_verified': 27,
        'median_latency_change_percent': 100*(current['median_eligible_to_output_s']/previous['median_eligible_to_output_s']-1),
        'median_prompt_token_change_percent': 100*(current['median_prompt_tokens']/previous['median_prompt_tokens']-1),
    }
    write_json(output/'comparison.json', comparison)
    lines = ['# Stage 4: shared semantic descriptions', '',
        'DecisionState v2 replaces terse observation aliases with deterministic measurement descriptions and adds face, upstream path/gesture and robot-motion descriptions. Both policies receive the same object. Raw categories, numeric operands, full SocialState, tracking, readiness, rule conditions and precedence are unchanged. No intent, willingness, pass permission or predicted collision is inferred.', '',
        '## Experimental controls', '',
        'The stage 1/2 first eligible and separate later timestamps, expected-action lists, model digest, Ollama version, generation options, scenario order and acceptance limits are reused. Three warm repetitions are scored only on their first output. The system instructions and user prefix are byte-identical to stage 3 and the original baseline; prompt metadata alone changes. The sealed stage 3 results are verified before and after this experiment. No physical robot action is executed.', '',
        'All 574 frames match the original frozen rule code for readiness, actions, reasons, target IDs/epochs and source/decision IDs; 402 remain eligible. All 27 operational inputs match between production rules and actual model messages. All 18 first/later snapshots retain every stage 3 non-label operand exactly.', '',
        '## First-decision results', '',
        '| Metric | Stage 3 compact | Stage 4 descriptions |', '| --- | ---: | ---: |',
        f"| Action alignment | {previous['aligned']}/27 | {current['aligned']}/27 |",
        f"| Median eligibility → complete observed output | {previous['median_eligible_to_output_s']:.2f}s | {current['median_eligible_to_output_s']:.2f}s |",
        f"| Median prompt tokens | {previous['median_prompt_tokens']:g} | {current['median_prompt_tokens']:g} |",
        f"| Live replay acceptance | {previous['accepted']}/27 | {current['accepted']}/27 |",
        f"| Aligned and accepted | {previous['aligned_and_accepted']}/27 | {current['aligned_and_accepted']}/27 |",
        f"| Valid first outputs | {previous['valid_first_outputs']}/27 | {current['valid_first_outputs']}/27 |", '',
        f"Median latency changed by {comparison['median_latency_change_percent']:+.1f}% and prompt tokens by {comparison['median_prompt_token_change_percent']:+.1f}% relative to stage 3. These are separate warm runs, with cache/native timing counts retained; wall time also depends on runtime conditions.", '',
        '| Scenario | Allowed actions | Stage 3 first actions | Stage 4 first actions (repeat 1 / 2 / 3) | Stage 4 median output time | Accepted |',
        '| --- | --- | --- | --- | ---: | ---: |']
    for scene in protocol['scenarios']:
        rs = sorted((r for r in rows if r['scenario_id'] == scene['scenario_id']), key=lambda r: r['repetition'])
        before = sorted((r for r in old if r['scenario_id'] == scene['scenario_id']), key=lambda r: r['repetition'])
        def actions(values):
            return ' / '.join(r['first_decision']['action'] if r['first_decision'] else 'NO_VALID_OUTPUT' for r in values)
        lines.append(f"| {scene['scenario_id']} | {', '.join(rs[0]['expected_actions'])} | {actions(before)} | {actions(rs)} | {statistics.median(r['timing']['eligible_to_observed_output_s'] for r in rs):.2f}s | {sum(r['accepted_by_live_client'] is True for r in rs)}/3 |")
    lines += ['', '## Actual first-output explanations', '',
        'Distinct answers and their frequency are shown below; every original answer and native response is retained in the measurements.', '']
    for scene in protocol['scenarios']:
        answers = Counter((r['first_decision']['action'], r['first_decision']['reason'])
            for r in rows if r['scenario_id'] == scene['scenario_id'] and r['first_decision'])
        for (action, reason), count in answers.items():
            lines.append(f'- {scene["scenario_id"]}: **{action}** ({count}/3): {reason}')
    lines += ['', '## Rejected delivery', '']
    rejected = [r for r in rows if r['accepted_by_live_client'] is not True]
    if not rejected:
        lines.append('All first outputs were accepted by the replay client.')
    for row in rejected:
        lines.append(f"- {row['scenario_id']} repeat {row['repetition']}: `{json.dumps(row['acceptance_details'], sort_keys=True)}`")
    lines += ['', '## Later observations — diagnostic only', '',
        '| Scenario | Frozen later time | Rule action | Stage 4 model action | Reason |', '| --- | ---: | --- | --- | --- |']
    for row in later:
        answer = row['llm'].get('decision') or {}
        lines.append(f"| {row['scenario_id']} | {row['snapshot']['since_motion_s']:.2f}s | {row['snapshot']['rule_decision']['action']} | {answer.get('action', 'NO_VALID_OUTPUT')} | {answer.get('reason', '')} |")
    lines += ['',
        'Alignment and acceptance measure different things: acceptance checks freshness/readiness and output validity, not whether the selected action is correct. S8 first resolves during the empty scene before a person appears. The original broad allowed-action sets for S3 and S8 remain. These nine recordings are a tuning set; improvement here is not evidence of held-out accuracy.', '',
        f'- [Exact S4 first decision input]({output.resolve()}/snapshots/S4/first.decision-state.json)',
        f'- [All first outputs and timing]({output.resolve()}/measurements/operational-summary.json)',
        f'- [Metrics]({output.resolve()}/comparison.json)',
        f'- [All-frame rule parity]({output.resolve()}/rule-parity.json)',
        f'- [Stage 3 reference]({stage3.resolve()}/REPORT.md)']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(comparison, indent=2), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=Path('var/evaluation/llm-refinement-stage-1-2'))
    parser.add_argument('--stage3', type=Path, default=Path('var/evaluation/llm-refinement-stage-3'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args(argv)
    stage3_digest = verify_stage3(args.stage3)
    args.output.mkdir(parents=True, exist_ok=False)
    protocol = paired.prepare(args.baseline, args.output)
    protocol.update(version='semantic-decision-state-v2', stage3_baseline=str(args.stage3.resolve()),
        stage3_manifest_sha256=stage3_digest, experiment='descriptive labels only; no prompt or policy changes')
    write_json(args.output/'protocol.json', protocol)
    # Freeze both harness and stage-specific reporting code.
    path = Path(__file__).resolve().relative_to(Path.cwd())
    target = args.output/'source'/path
    shutil.copyfile(path, target)
    manifest_path = args.output/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['implementation_sha256'][str(path)] = sha(path.read_bytes())
    manifest['stage3_manifest_sha256'] = stage3_digest
    write_json(manifest_path, manifest)
    if not args.prepare_only:
        with patch.object(paired, 'report', report):
            paired.run(protocol, args.output, args.baseline)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
