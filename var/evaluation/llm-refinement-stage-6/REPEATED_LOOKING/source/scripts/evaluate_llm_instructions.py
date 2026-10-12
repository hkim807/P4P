"""Stage 5: paired v14/v15 instructions on identical frozen DecisionState v3."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import statistics
from unittest.mock import patch

from app.policy import llm
from scripts import evaluate_compact_state as paired
from scripts.evaluate_semantic_state import metrics, verify_stage3
from scripts.profile_llm_baseline import sha, write_json


def verify_requests(output, protocol, rows, later):
    count = 0
    for scene in protocol['scenarios']:
        for repetition in range(1, 4):
            folder = output/f'measurements/repeat-{repetition}/{scene["scenario_id"]}'
            expected = json.loads((output/f'snapshots/{scene["scenario_id"]}/first.messages.json').read_text())
            requests = list(folder.glob('profile-*/call-*.request.json'))
            if len(requests) != 1:
                raise ValueError('Operational evaluation must contain exactly one first request')
            request = json.loads(requests[0].read_text())
            if request['messages'] != expected:
                raise ValueError('Actual prompt differs from frozen arm instructions/input')
            count += 1
        expected = json.loads((output/f'snapshots/{scene["scenario_id"]}/later.messages.json').read_text())
        request = json.loads((output/f'measurements/later/{scene["scenario_id"]}/call-001.request.json').read_text())
        if request['messages'] != expected:
            raise ValueError('Later prompt differs from frozen arm instructions/input')
    # Reconstruct and verify exact transport bytes as well as decoded messages.
    for path in output.glob('measurements/**/calls.json'):
        for index, call in enumerate(json.loads(path.read_text()), 1):
            request = path.with_name(f'call-{index:03d}.request.json')
            body = json.dumps(json.loads(request.read_text()), allow_nan=False, separators=(',', ':')).encode()
            if sha(body) != call['request_sha256']:
                raise ValueError('Transport request hash differs')
            request.with_name(f'call-{index:03d}.request.raw.json').write_bytes(body)
    write_json(output/'verification.json', {'primary_messages_verified':count,
        'later_messages_verified':len(later), 'rule_and_llm_state_identical':True,
        'exact_transport_bytes_verified':count+len(later)})
    write_json(output/'metrics.json', metrics(rows))


def make_arm(root, common, name, instructions):
    output = root/name
    output.mkdir()
    shutil.copytree(common/'snapshots', output/'snapshots')
    for file in ('manifest.json', 'rule-parity.json', 'payload-sizes.json'):
        shutil.copyfile(common/file, output/file)
    protocol = json.loads((common/'protocol.json').read_text())
    protocol.update(version='stage5-prompt-only-v1', arm=name,
        prompt_version=instructions['prompt_version'], instructions_sha256=sha(instructions['system_prompt'].encode()))
    protocol['settings']['prompt_version'] = instructions['prompt_version']
    for scene in protocol['scenarios']:
        for point in ('first', 'later'):
            folder = output/'snapshots'/scene['scenario_id']
            state = json.loads((folder/f'{point}.decision-state.json').read_text())
            canonical = json.dumps(state, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
            write_json(folder/f'{point}.messages.json', [
                {'role':'system', 'content':instructions['system_prompt']},
                {'role':'user', 'content':instructions['user_prefix']+canonical}])
    write_json(output/'protocol.json', protocol)
    write_json(output/'instructions.json', instructions)
    return output, protocol


def report(root):
    old = json.loads((root/'control/measurements/operational-summary.json').read_text())
    new = json.loads((root/'refined/measurements/operational-summary.json').read_text())
    before, after = metrics(old), metrics(new)
    verification = []
    for previous, current in zip(old, new):
        if (previous['scenario_id'], previous['repetition']) != (current['scenario_id'], current['repetition']):
            raise ValueError('Paired trial order changed')
        if previous['first_state_sha256'] != current['first_state_sha256']:
            raise ValueError('Shared DecisionState changed between prompts')
        verification.append({'scenario_id':current['scenario_id'], 'repeat':current['repetition'],
            'decision_state_sha256':current['first_state_sha256'], 'state_identical':True})
    write_json(root/'paired-arm-verification.json', verification)
    candidate = json.loads((root/'refined-instructions.json').read_text())['prompt_version'].split('-')[-1]
    reuse = json.loads((root/'refined/protocol.json').read_text()).get('reused_control_from')
    comparison = {'control_v14_on_v3':before, f'refined_{candidate}_on_v3':after,
        'paired_trials':len(verification), 'rule_frames_unchanged':574, 'eligible_frames':402,
        'median_latency_change_percent':100*(after['median_eligible_to_output_s']/before['median_eligible_to_output_s']-1),
        'median_prompt_token_change_percent':100*(after['median_prompt_tokens']/before['median_prompt_tokens']-1)}
    write_json(root/'comparison.json', comparison)
    lines = ['# Stage 5: refine LLM instructions', '',
        f'The experiment compares the previous v14 instructions and new {candidate} instructions on the exact same current DecisionState v3, after unsupported Navel cues were removed. Both policies still receive the same shared state. Only system instructions, user prefix and prompt-version metadata differ. The old prompt is supplied through temporary runtime overrides; model results and acceptance checks are not patched.', '',
        '## Controls and scoring', '',
        'All 574 frames preserve complete frozen v7 decisions/readiness (402 eligible). Original first eligible timestamps, later diagnostic timestamps, expected-action sets and nine-scenario weighting are unchanged. Both arms use the same model digest/Ollama version, temperature 0, seed 42, context 8192, output limit 192, scenario order, three warm repetitions and acceptance limits. All control trials precede all refined trials; these are sequential runs, not randomized or simultaneous timing measurements. Native cache/token/timing measurements are saved.', '',
        'First complete outputs are the primary score. No later answer replaces a failed first decision. Each arm has 27 operational trials and nine later diagnostics. Actual requests are checked against frozen instructions and shared-state snapshots; all 27 paired operational state hashes are identical. Prior stage 3/4 results are historical context, not the prompt-only control, because their state payloads differ.', '',
        '## Instruction changes', '',
        '- Name the current DecisionState and remove references to missing-track history and old uppercase label aliases.',
        '- Separate current gaze, recent gaze history, coverage fraction and continuous looking. Gaze scores are not probabilities of interest.',
        '- Treat proximity as a configured reference band and distinguish moving closer from speaking at the current distance; it is not an invitation.',
        '- Interpret corridor position and separation together while preserving uncertainty about individual motion, clearance and collision.',
        '- Consider room for passage separately from interaction. Giving space does not require claiming a measured collision or a wish to pass.',
        '- Explain decisive observations without asserting interest, willingness, gestures or permission as facts. Preserve the two-field JSON contract and 25-word sentence request.', '',
        '## Operational results', '',
        f'| Metric | v14 control on v3 | {candidate} refined on v3 |', '| --- | ---: | ---: |',
        f"| First-action alignment | {before['aligned']}/27 | {after['aligned']}/27 |",
        f"| Median eligibility → complete observed output | {before['median_eligible_to_output_s']:.2f}s | {after['median_eligible_to_output_s']:.2f}s |",
        f"| Median prompt tokens | {before['median_prompt_tokens']:g} | {after['median_prompt_tokens']:g} |",
        f"| Accepted by live replay | {before['accepted']}/27 | {after['accepted']}/27 |",
        f"| Aligned and accepted | {before['aligned_and_accepted']}/27 | {after['aligned_and_accepted']}/27 |",
        f"| Valid first outputs | {before['valid_first_outputs']}/27 | {after['valid_first_outputs']}/27 |", '',
        f'| Scenario | Allowed actions | v14 first actions | {candidate} first actions | v14 median output | {candidate} median output |',
        '| --- | --- | --- | --- | ---: | ---: |']
    for i in range(1, 10):
        scenario = f'S{i}'
        a = [r for r in old if r['scenario_id'] == scenario]
        b = [r for r in new if r['scenario_id'] == scenario]
        def actions(rows):
            return ' / '.join(r['first_decision']['action'] if r['first_decision'] else 'NO_VALID_OUTPUT' for r in rows)
        lines.append(f"| {scenario} | {', '.join(b[0]['expected_actions'])} | {actions(a)} | {actions(b)} | {statistics.median(r['timing']['eligible_to_observed_output_s'] for r in a):.2f}s | {statistics.median(r['timing']['eligible_to_observed_output_s'] for r in b):.2f}s |")
    lines += ['', '## First-output explanations', '']
    for arm, rows in (('v14 control', old), (f'{candidate} refined', new)):
        lines += [f'### {arm}', '']
        for i in range(1, 10):
            reasons = sorted(set((r['first_decision']['action'], r['first_decision']['reason'])
                for r in rows if r['scenario_id'] == f'S{i}' and r['first_decision']))
            for action, reason in reasons:
                lines.append(f'- S{i}: **{action}** — {reason}')
    lines += ['', '## Later diagnostics — excluded from operational score', '',
        f'| Scenario | Rule | v14 | {candidate} | {candidate} reason |', '| --- | --- | --- | --- | --- |']
    a = json.loads((root/'control/measurements/later-summary.json').read_text())
    b = json.loads((root/'refined/measurements/later-summary.json').read_text())
    for old_row, new_row in zip(a, b):
        previous = old_row['llm'].get('decision') or {}
        answer = new_row['llm'].get('decision') or {}
        lines.append(f"| {new_row['scenario_id']} | {new_row['snapshot']['rule_decision']['action']} | {previous.get('action', 'INVALID')} | {answer.get('action', 'INVALID')} | {answer.get('reason', '')} |")
    lines += ['', '## Delivery rejections', '']
    for arm, rows in (('control', old), ('refined', new)):
        for row in rows:
            if row['accepted_by_live_client'] is not True:
                lines.append(f"- {arm} {row['scenario_id']} repeat {row['repetition']}: `{json.dumps(row['acceptance_details'], sort_keys=True)}`")
    lines += ['',
        'Acceptance is distinct from correctness and is affected by finite-clip freshness/readiness. S8 first resolves in the empty scene before a person appears. The broad S3/S8 allowed sets are unchanged. These recordings are a tuning set, not held-out evidence of real-world performance. Reason text is retained for qualitative review; no automatic semantic-validation claim is made. No physical robot action was executed.', '',
        f'- [Old instructions]({root.resolve()}/control-instructions.txt)',
        f'- [New instructions]({root.resolve()}/refined-instructions.txt)',
        f'- [Metrics]({root.resolve()}/comparison.json)',
        f'- [Paired state verification]({root.resolve()}/paired-arm-verification.json)']
    if reuse:
        lines += ['', f'The previously measured v14 control was reused from {reuse}; it was not measured again for this candidate. Its sealed artifacts, actual requests and all state hashes were verified. Candidate v15 was rejected for over-selecting YIELD (5/9 alignment in each run); this follow-up balances interaction and progress with an observed spatial need for space.']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(comparison, indent=2), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('var/evaluation/llm-refinement-stage-5'))
    parser.add_argument('--baseline', type=Path, default=Path('var/evaluation/llm-refinement-stage-1-2'))
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--reuse-control', type=Path)
    args = parser.parse_args(argv)
    root = args.root
    control = json.loads((root/'control-instructions.json').read_text())
    previous = json.loads((root/'pre-change-source.json').read_text())
    for file, digest in previous['non_prompt_implementation_sha256'].items():
        if sha(Path(file).read_bytes()) != digest:
            raise ValueError(f'Non-prompt production code changed: {file}')
    refined = {'prompt_version':llm.PROMPT_VERSION, 'system_prompt':llm.SYSTEM_PROMPT,
               'user_prefix':llm.SOCIAL_STATE_PREFIX}
    write_json(root/'refined-instructions.json', refined)
    (root/'refined-instructions.txt').write_text(refined['system_prompt']+'\n')
    common = root/'common'
    common.mkdir()
    paired.prepare(args.baseline, common, require_original_instructions=False)
    # Freeze stage-specific tooling as part of the measured implementation.
    manifest_file = common/'manifest.json'
    manifest = json.loads(manifest_file.read_text())
    for file in ('scripts/evaluate_llm_instructions.py', 'scripts/evaluate_semantic_state.py'):
        shutil.copyfile(file, common/'source'/file)
        manifest['implementation_sha256'][file] = sha(Path(file).read_bytes())
    write_json(manifest_file, manifest)
    supported = Path('var/evaluation/navel-cue-removal-20261013')
    for i in range(1, 10):
        for point in ('first', 'later'):
            name = f'snapshots/S{i}/{point}.decision-state.json'
            if json.loads((common/name).read_text()) != json.loads((supported/name).read_text()):
                raise ValueError('DecisionState changed before the prompt experiment')
    arms = []
    if args.reuse_control:
        verify_stage3(args.reuse_control)
        shutil.copytree(args.reuse_control/'control', root/'control')
        old_protocol = json.loads((root/'control/protocol.json').read_text())
        if json.loads((root/'control/instructions.json').read_text()) != control:
            raise ValueError('Reused control instructions differ')
        for i in range(1, 10):
            for point in ('first', 'later'):
                file = f'snapshots/S{i}/{point}.decision-state.json'
                if json.loads((root/'control'/file).read_text()) != json.loads((common/file).read_text()):
                    raise ValueError('Reused control state differs')
        verify_requests(root/'control', old_protocol,
            json.loads((root/'control/measurements/operational-summary.json').read_text()),
            json.loads((root/'control/measurements/later-summary.json').read_text()))
    else:
        arms.append(('control', control, *make_arm(root, common, 'control', control)))
    output, protocol = make_arm(root, common, 'refined', refined)
    if args.reuse_control:
        protocol['reused_control_from'] = str(args.reuse_control.resolve())
        write_json(output/'protocol.json', protocol)
    arms.append(('refined', refined, output, protocol))
    if args.prepare_only:
        return 0
    for name, instructions, output, protocol in arms:
        print(f'Stage 5 arm: {name}', flush=True)
        with patch('app.policy.llm.SYSTEM_PROMPT', instructions['system_prompt']), \
             patch('app.policy.llm.SOCIAL_STATE_PREFIX', instructions['user_prefix']), \
             patch('app.policy.llm.PROMPT_VERSION', instructions['prompt_version']), \
             patch('app.inference.live.LLM_PROMPT_VERSION', instructions['prompt_version']), \
             patch('app.inference.llm.PROMPT_VERSION', instructions['prompt_version']), \
             patch.object(paired, 'report', lambda out, base, proto, rows, later:
                 verify_requests(out, proto, rows, later)):
            paired.run(protocol, output, args.baseline)
    for directory in ('llm-refinement-stage-3', 'llm-refinement-stage-4', 'navel-cue-removal-20261013'):
        verify_stage3(Path('var/evaluation')/directory)
    report(root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
