"""Stage 6: independent causal temporal summaries with the v16 prompt fixed."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from app.policy.llm import SYSTEM_PROMPT, SOCIAL_STATE_PREFIX, PROMPT_VERSION, classify_llm
from app.replay.llm_inputs import iter_replay_states
from app.state.decision_models import TEMPORAL_DESCRIPTIONS, prepare_decision_input
from app.state.social_models import SocialState, TemporalConfig
from app.state.tracks import TrackConfig
from scripts import evaluate_compact_state as paired
from scripts.evaluate_llm_instructions import verify_requests
from scripts.evaluate_semantic_state import metrics, verify_stage3
from scripts.profile_llm_baseline import ProfiledClient, ReplayClock, profile_config, sha, timing_breakdown, write_json
from scripts.replay_first_action import request_json


def without_summary(value):
    value = deepcopy(value)
    value['schema_version'] = 3
    for person in value['people']:
        person.pop('temporal_summary', None)
    return value


def prepare_arm(baseline, control, output, mode):
    output.mkdir(parents=True)
    with patch('app.state.decision_models.TEMPORAL_SUMMARY_MODE', mode):
        protocol = paired.prepare(baseline, output, require_original_instructions=False)
        protocol.update(version='temporal-summary-ablation-v1', summary_mode=mode,
            selection_rule='Enable only a strictly higher primary alignment with no loss of valid first outputs; otherwise keep NONE.')
        active = {'first':[], 'later':[]}
        for scene in protocol['scenarios']:
            for point in active:
                file = f'snapshots/{scene["scenario_id"]}/{point}.decision-state.json'
                before = json.loads((control/file).read_text())
                after = json.loads((output/file).read_text())
                if without_summary(after) != before:
                    raise ValueError('Existing shared evidence changed')
                if any(p.get('temporal_summary') for p in after['people']):
                    active[point].append(scene['scenario_id'])
                elif after != before:
                    raise ValueError('Inactive summaries must retain exact baseline input')
                messages = json.loads((output/file.replace('decision-state', 'messages')).read_text())
                old = json.loads((control/file.replace('decision-state', 'messages')).read_text())
                if messages[0] != old[0] or messages[0]['content'] != SYSTEM_PROMPT:
                    raise ValueError('Prompt changed')
                if not messages[1]['content'].startswith(SOCIAL_STATE_PREFIX):
                    raise ValueError('User prefix changed')
        coverage = {'active_snapshots':active, 'frames_with_summary':0, 'eligible_frames_with_summary':0}
        for scene in protocol['scenarios']:
            frames = eligible = 0
            settings = protocol['settings']
            for item in iter_replay_states(scene['recording'], 'sdk',
                    track_config=TrackConfig.from_file(settings['track_config']),
                    temporal_config=TemporalConfig.from_file(settings['temporal_config'])):
                current = prepare_decision_input(item.state).state
                if any(p.temporal_summary for p in current.people):
                    frames += 1
                    eligible += current.observation_readiness == 'READY'
            coverage[scene['scenario_id']] = {'frames_with_summary':frames, 'eligible_frames_with_summary':eligible}
            coverage['frames_with_summary'] += frames
            coverage['eligible_frames_with_summary'] += eligible
        protocol['coverage'] = coverage
        protocol['primary_trials_planned'] = 27 if active['first'] else 0
        write_json(output/'protocol.json', protocol)
        write_json(output/'coverage.json', coverage)
    manifest_file = output/'manifest.json'
    manifest = json.loads(manifest_file.read_text())
    for file in ('scripts/evaluate_temporal_summaries.py', 'scripts/evaluate_llm_instructions.py',
                 'scripts/evaluate_semantic_state.py'):
        shutil.copyfile(file, output/'source'/file)
        manifest['implementation_sha256'][file] = sha(Path(file).read_bytes())
    manifest['system_instructions_unchanged_from_stage5'] = True
    write_json(manifest_file, manifest)
    return protocol


def run_later_only(output, protocol, baseline):
    settings = protocol['settings']
    runtime = {'ollama_version':request_json(settings['base_url'],'/api/version'),
               'ollama_tags':request_json(settings['base_url'],'/api/tags')}
    old = json.loads((baseline/'freeze/runtime.json').read_text())
    def digest(value):
        return next(m['digest'] for m in value['ollama_tags']['models'] if m['name'] == settings['model'])
    if runtime['ollama_version'] != old['ollama_version'] or digest(runtime) != digest(old):
        raise ValueError('Model/runtime changed')
    write_json(output/'runtime.json', runtime)
    request_json(settings['base_url'],'/api/generate',{'model':settings['model'],'prompt':'',
        'stream':False,'keep_alive':'5m','options':{'num_ctx':settings['num_ctx']}})
    rows = []
    for scene in protocol['scenarios']:
        print(f'Later diagnostic only: {scene["scenario_id"]}', flush=True)
        state = SocialState.model_validate_json((output/f'snapshots/{scene["scenario_id"]}/later.social-state.json').read_text())
        client = ProfiledClient(profile_config(settings), output/f'measurements/later/{scene["scenario_id"]}', ReplayClock())
        result = classify_llm(state, client)
        if client.calls[0]['state_sha256'] != scene['snapshots']['later']['state_sha256']:
            raise ValueError('Later input changed')
        request = json.loads((client.output/'call-001.request.json').read_text())
        expected = json.loads((output/f'snapshots/{scene["scenario_id"]}/later.messages.json').read_text())
        if request['messages'] != expected:
            raise ValueError('Later instructions differ')
        body = json.dumps(request, allow_nan=False, separators=(',',':')).encode()
        if sha(body) != client.calls[0]['request_sha256']:
            raise ValueError('Request bytes differ')
        (client.output/'call-001.request.raw.json').write_bytes(body)
        rows.append({'scenario_id':scene['scenario_id'],'snapshot':scene['snapshots']['later'],
            'llm':result,'timing':timing_breakdown(client.calls[0]),'primary_score':False})
        write_json(output/'measurements/later-summary.json', rows)
    write_json(output/'verification.json',{'primary_trials':0,'reason':'No frozen first input changes',
        'later_messages_and_bytes_verified':len(rows),'primary_score':False})


def report(root, arms):
    control = root/'control'
    baseline_rows = json.loads((control/'measurements/operational-summary.json').read_text())
    baseline_metrics = metrics(baseline_rows)
    comparison = {'stage5_control':baseline_metrics, 'arms':{}, 'selected_mode':'NONE',
        'prompt_version':PROMPT_VERSION, 'rule_frames_preserved_per_mode':574}
    lines = ['# Stage 6: additional temporal summaries', '',
        'Three causal additions were tested independently with DecisionState base evidence, v16 instructions, rules, estimator thresholds, model/runtime and generation/acceptance settings fixed. Both policies receive the same enriched state. Only a positive measured summary is added; inactive summaries produce byte-identical v3 inputs. v4 identifies inputs containing the optional temporal extension.', '',
        '## Protocol', '',
        'The sealed stage 5 v16 control is reused and its actual requests/state hashes verified. Original first eligible timestamps and separate later diagnostic timestamps remain frozen. A changed first-input arm receives three warm S1–S9 operational repetitions plus nine later probes. If all nine first inputs are identical to control, no new primary trials are run; later probes cannot substitute for a primary score. Expected-action sets, including broad S3/S8 sets, are unchanged.', '',
        'All 574 frames in every mode preserve readiness and complete frozen v7 decisions, reasons, targets and IDs; 402 remain eligible. Existing non-summary state fields and full SocialState snapshots remain unchanged. Every actual model request is checked against frozen messages and input hashes. No physical actions are executed.', '',
        '## Additions', '']
    for mode, description in TEMPORAL_DESCRIPTIONS.items():
        lines.append(f'- **{mode}**: {description}')
    lines += ['', '## First-decision results', '',
        '| Variant | Active first inputs | Alignment | Median complete output | Median prompt tokens | Accepted |',
        '| --- | --- | ---: | ---: | ---: | ---: |',
        f"| Control, no summary | — | {baseline_metrics['aligned']}/27 | {baseline_metrics['median_eligible_to_output_s']:.2f}s | {baseline_metrics['median_prompt_tokens']:g} | {baseline_metrics['accepted']}/27 |"]
    best = baseline_metrics['aligned']
    for mode, output, protocol in arms:
        coverage = protocol['coverage']
        item = {'coverage':coverage,'primary_trials_run':protocol['primary_trials_planned']}
        if protocol['primary_trials_planned']:
            rows = json.loads((output/'measurements/operational-summary.json').read_text())
            item['metrics'] = metrics(rows)
            current = item['metrics']
            if current['aligned'] > best and current['valid_first_outputs'] >= baseline_metrics['valid_first_outputs']:
                best = current['aligned']
                comparison['selected_mode'] = mode
            lines.append(f"| {mode} | {', '.join(coverage['active_snapshots']['first'])} | {current['aligned']}/27 | {current['median_eligible_to_output_s']:.2f}s | {current['median_prompt_tokens']:g} | {current['accepted']}/27 |")
            changes = []
            for before, after in zip(baseline_rows, rows):
                if (before['scenario_id'], before['repetition']) != (after['scenario_id'], after['repetition']):
                    raise ValueError('Trial order differs')
                changes.append({'scenario_id':after['scenario_id'],'repeat':after['repetition'],
                    'control_action':before['first_decision'],'candidate_action':after['first_decision'],
                    'aligned':after['action_aligned']})
            write_json(output/'first-action-comparison.json', changes)
        else:
            item['metrics'] = None
            item['interpretation'] = 'No added information at any frozen first decision; no new operational score or latency claimed.'
            lines.append(f'| {mode} | None | Not run | Not measured | Identical inputs | Not measured |')
        comparison['arms'][mode] = item
    write_json(root/'comparison.json', comparison)
    lines += ['', f"Selection under the predeclared action-alignment rule: **{comparison['selected_mode']}**. A summary must strictly improve primary alignment without losing valid outputs to be enabled; ties retain the smaller baseline. Latency, acceptance and explanations are reported separately. Sequential/reused-control timing is not a randomized causal latency estimate.", '',
        '## First explanations — distinct answers', '']
    for mode, output, protocol in arms:
        if not protocol['primary_trials_planned']:
            continue
        lines += [f'### {mode}', '']
        rows = json.loads((output/'measurements/operational-summary.json').read_text())
        for i in range(1, 10):
            for action, reason in sorted(set((r['first_decision']['action'],r['first_decision']['reason'])
                    for r in rows if r['scenario_id'] == f'S{i}' and r['first_decision'])):
                lines.append(f'- S{i}: **{action}** — {reason}')
    lines += ['', '## Later diagnostics — excluded from primary score', '',
        '| Scenario | Control | Looking change | Repeated looks | Detection resumed |',
        '| --- | --- | --- | --- | --- |']
    later = {mode:json.loads((output/'measurements/later-summary.json').read_text()) for mode,output,protocol in arms}
    base = json.loads((control/'measurements/later-summary.json').read_text())
    for i, row in enumerate(base):
        values = [row['scenario_id'],(row['llm'].get('decision') or {}).get('action','INVALID')]
        values += [(later[mode][i]['llm'].get('decision') or {}).get('action','INVALID') for mode,_,_ in arms]
        lines.append('| '+' | '.join(values)+' |')
    lines += ['', '## Coverage and limits', '']
    for mode,output,protocol in arms:
        coverage = protocol['coverage']
        lines.append(f"- {mode}: {coverage['frames_with_summary']} of 574 frames; {coverage['eligible_frames_with_summary']} eligible frames; first points {coverage['active_snapshots']['first']}; later points {coverage['active_snapshots']['later']}.")
    lines += ['',
        'Looking-change summaries require valid history, a known current score/classification, positive measured looking duration and latest not-looking. Repeated looks require at least two measured bouts with valid gaze; no short-duration claim is made. Detection resumption requires an exact current REACQUIRED event for this UID/epoch; REASSOCIATED, new acquisition, other tracks and past/future events do not qualify. Detector loss is not measured looking away or a person leaving/returning.', '',
        'A REACQUIRED pulse is deliberately limited to the current update; this experiment does not retain a new recent-gap history or introduce a new time threshold. UNKNOWN/unavailable summaries are omitted. The parser does not semantically validate model reasons, so explanations remain subject to qualitative review. Acceptance is not correctness, finite recordings can end before delivery, and S8 first resolves in the empty scene. These are tuning recordings, not held-out results.', '',
        f'- [Comparison]({root.resolve()}/comparison.json)',
        f'- [Looking-change inputs/results]({root.resolve()}/LOOKING_CHANGE/protocol.json)',
        f'- [Repeated-looking inputs/results]({root.resolve()}/REPEATED_LOOKING/protocol.json)',
        f'- [Detection-resumption inputs/results]({root.resolve()}/DETECTION_RESUMED/protocol.json)']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(comparison,indent=2),flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,default=Path('var/evaluation/llm-refinement-stage-1-2'))
    parser.add_argument('--stage5',type=Path,default=Path('var/evaluation/llm-refinement-stage-5'))
    parser.add_argument('--prepare-only',action='store_true')
    args = parser.parse_args(argv)
    verify_stage3(args.stage5)
    control_instructions = json.loads((args.stage5/'refined-instructions.json').read_text())
    if (control_instructions['system_prompt'],control_instructions['user_prefix'],control_instructions['prompt_version']) != (SYSTEM_PROMPT,SOCIAL_STATE_PREFIX,PROMPT_VERSION):
        raise ValueError('Stage 5 prompt changed')
    root = args.output
    root.mkdir(parents=True,exist_ok=False)
    shutil.copytree(args.stage5/'refined',root/'control')
    write_json(root/'control-provenance.json',{'source':str((args.stage5/'refined').resolve()),
        'stage5_manifest_sha256':sha((args.stage5/'results-manifest.json').read_bytes()),'control_reused':True})
    control_protocol = json.loads((root/'control/protocol.json').read_text())
    verify_requests(root/'control',control_protocol,
        json.loads((root/'control/measurements/operational-summary.json').read_text()),
        json.loads((root/'control/measurements/later-summary.json').read_text()))
    arms = [(mode,root/mode,prepare_arm(args.baseline,root/'control',root/mode,mode)) for mode in TEMPORAL_DESCRIPTIONS]
    if args.prepare_only:
        return 0
    for mode,output,protocol in arms:
        print(f'Temporal summary arm: {mode}',flush=True)
        with patch('app.state.decision_models.TEMPORAL_SUMMARY_MODE',mode):
            if protocol['primary_trials_planned']:
                with patch.object(paired,'report',lambda out,base,proto,rows,later:verify_requests(out,proto,rows,later)):
                    paired.run(protocol,output,args.baseline)
            else:
                run_later_only(output,protocol,args.baseline)
        manifest = json.loads((output/'manifest.json').read_text())
        if any(sha(Path(path).read_bytes()) != digest for path,digest in manifest['implementation_sha256'].items()):
            raise ValueError('Implementation changed during measurement')
    verify_stage3(args.stage5)
    report(root,arms)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
