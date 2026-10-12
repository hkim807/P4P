"""Measure stage 3 against frozen first/later snapshots, without retuning policy."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from unittest.mock import patch

from app.policy.llm import PROMPT_VERSION, build_llm_prompt, classify_llm
from app.policy.rules import decide, rule_readiness
from app.replay.llm_inputs import iter_replay_states
from app.state.decision_models import prepare_decision_input
from app.state.social_models import SocialState, TemporalConfig
from app.state.tracks import TrackConfig
from scripts.profile_llm_baseline import (ProfiledClient, ReplayClock, operational_row,
    profile_config, sha, source_paths, timing_breakdown, verify_freeze, write_json)
from scripts.replay_first_action import replay, request_json


def load_reference(baseline):
    name = '_compact_experiment_frozen_rules'
    spec = importlib.util.spec_from_file_location(name, baseline/'freeze/source/app/policy/rules.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prepare(baseline, output):
    verify_freeze(baseline, worktree=False)
    protocol = json.loads((baseline/'freeze/protocol.json').read_text())
    reference = load_reference(baseline)
    original = deepcopy(protocol)
    protocol.update(version='compact-decision-state-v1', prompt_version=PROMPT_VERSION,
                    baseline=str(baseline.resolve()))
    count = ready = 0
    sizes = []
    for scene in protocol['scenarios']:
        settings = protocol['settings']
        items = iter_replay_states(scene['recording'], 'sdk',
            track_config=TrackConfig.from_file(settings['track_config']),
            temporal_config=TemporalConfig.from_file(settings['temporal_config']))
        for item in items:
            full = item.state.model_dump()
            compact = prepare_decision_input(item.state)
            old_reason = reference.rule_readiness(full)
            if rule_readiness(compact) != old_reason:
                raise ValueError(f'Readiness changed at {item.state.state_id}')
            if old_reason is None:
                if decide(compact).model_dump() != reference.decide(full).model_dump():
                    raise ValueError(f'Rule result changed at {item.state.state_id}')
                ready += 1
            count += 1
        for point in ('first','later'):
            state = SocialState.model_validate_json((baseline/f'freeze/snapshots/{scene["scenario_id"]}/{point}.state.json').read_text())
            prompt = build_llm_prompt(state)
            old = json.loads((baseline/f'freeze/snapshots/{scene["scenario_id"]}/{point}.messages.json').read_text())
            if prompt.instructions != old[0]['content']:
                raise ValueError('System instructions changed; stage 3 isolates payload reduction')
            snapshot = scene['snapshots'][point]
            if sha(prompt.social_state_json.encode()) != snapshot['state_sha256']:
                raise ValueError('Full source differs from frozen baseline')
            snapshot['full_social_state_sha256'] = snapshot['state_sha256']
            snapshot['state_sha256'] = sha(prompt.decision_state_json.encode())
            if decide(state).model_dump()['decision'] != snapshot['rule_decision']['action']:
                raise ValueError('Frozen first/later rule action changed')
            folder = output/'snapshots'/scene['scenario_id']
            write_json(folder/f'{point}.social-state.json',json.loads(prompt.social_state_json))
            write_json(folder/f'{point}.decision-state.json',json.loads(prompt.decision_state_json))
            write_json(folder/f'{point}.messages.json',[m.model_dump(exclude_none=True) for m in prompt.messages])
            sizes.append({'scenario_id':scene['scenario_id'],'point':point,
                          'full_state_bytes':len(prompt.social_state_json.encode()),
                          'compact_state_bytes':len(prompt.decision_state_json.encode())})
    if (count,ready)!=(574,402):
        raise ValueError('Observation/readiness counts changed')
    write_json(output/'protocol.json',protocol)
    write_json(output/'rule-parity.json',{'frames_compared':count,'eligible_frames':ready,
        'readiness_actions_reasons_targets_and_ids_unchanged':True,
        'reference_rule_sha256':sha((baseline/'freeze/source/app/policy/rules.py').read_bytes()),
        'first_actions':[s['snapshots']['first']['rule_decision'] for s in original['scenarios']]})
    write_json(output/'payload-sizes.json',sizes)
    paths = set(source_paths()) | {Path(__file__).resolve().relative_to(Path.cwd()),
                                  Path('scripts/profile_llm_baseline.py')}
    hashes={}
    for path in sorted(paths):
        target=output/'source'/path
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
        hashes[str(path)]=sha(path.read_bytes())
    write_json(output/'manifest.json',{'created_utc':datetime.now(timezone.utc).isoformat(),
        'git_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'implementation_sha256':hashes,'baseline_manifest_sha256':sha((baseline/'freeze/manifest.json').read_bytes()),
        'system_instructions_unchanged':True,'settings_and_acceptance_limits_unchanged':True})
    return protocol


def link_profiles(scene, folder, calls):
    models=[json.loads(line) for line in (folder/'live/models.jsonl').read_text().splitlines()]
    invoked=[m for m in models if m['llm_inference']['request_duration_s'] is not None]
    if len(invoked)!=len(calls):
        raise ValueError('Model audit and profiled calls do not align')
    for call,model in zip(calls,invoked):
        if call['state_sha256']!=sha(model['decision_state_json'].encode()):
            raise ValueError('Actual model input differs from its audit')
        call['source_state_id']=model['source_state_id']
        call['source_robot_timestamp_us']=model['source_robot_timestamp_us']
    first=scene['snapshots']['first']
    if not invoked or invoked[0]['source_state_id']!=first['state_id']:
        raise ValueError('First eligible snapshot changed')
    if sha(invoked[0]['social_state_json'].encode())!=first['full_social_state_sha256']:
        raise ValueError('Full first observation differs from baseline')
    events=[json.loads(line) for line in (folder/'live/events.jsonl').read_text().splitlines()]
    event=next(e for e in events if e.get('source_timestamp_us')==first['timestamp_us'])
    shared=prepare_decision_input(SocialState.model_validate(event['response']['social_state'])).state
    if event['response']['decision_state']!=shared.model_dump(mode='json') or shared.sha256!=calls[0]['state_sha256']:
        raise ValueError('Rule and LLM inputs differ')
    if event['response']['final_decision']!=first['rule_decision']:
        raise ValueError('Operational rule result changed')
    write_json(folder/'paired-input-verification.json',{'source_state_id':first['state_id'],
        'decision_state_sha256':shared.sha256,'rules_and_llm_inputs_identical':True,
        'rule_output_unchanged':True})
    return calls


def run(protocol, output, baseline):
    settings=protocol['settings']
    runtime={'ollama_version':request_json(settings['base_url'],'/api/version'),
             'ollama_tags':request_json(settings['base_url'],'/api/tags')}
    old=json.loads((baseline/'freeze/runtime.json').read_text())
    def digest(runtime):
        return next(m['digest'] for m in runtime['ollama_tags']['models'] if m['name']==settings['model'])
    if digest(runtime)!=digest(old) or runtime['ollama_version']!=old['ollama_version']:
        raise ValueError('Model digest or Ollama runtime differs from baseline')
    write_json(output/'runtime.json',runtime)
    rows=[]
    for repetition in range(1,protocol['repetitions']+1):
        write_json(output/f'measurements/repeat-{repetition}/warmup.json',request_json(settings['base_url'],'/api/generate',{
            'model':settings['model'],'prompt':'','stream':False,'keep_alive':'5m',
            'options':{'num_ctx':settings['num_ctx']}}))
        for scene in protocol['scenarios']:
            print(f'Compact repeat {repetition}: {scene["scenario_id"]}',flush=True)
            folder=output/f'measurements/repeat-{repetition}/{scene["scenario_id"]}'
            clock=ReplayClock()
            clients=[]
            def factory(config):
                client=ProfiledClient(config,folder/f'profile-{len(clients)+1}',clock)
                clients.append(client)
                return client
            with patch('app.inference.live.OllamaClient',factory):
                result=replay(scene,'llm',folder/'live',settings,now=clock.now)
            calls=link_profiles(scene,folder,[c for client in clients for c in client.calls])
            row=operational_row(scene,result,calls,folder/'live')
            row['repetition']=repetition
            rows.append(row)
            write_json(folder/'operational.json',row)
            write_json(output/'measurements/operational-summary.json',rows)
            print(json.dumps({k:row[k] for k in ('scenario_id','first_decision','action_aligned','first_eligible_to_validated_client_output_s','accepted_by_live_client')}),flush=True)
    later=[]
    for scene in protocol['scenarios']:
        state=SocialState.model_validate_json((output/f'snapshots/{scene["scenario_id"]}/later.social-state.json').read_text())
        client=ProfiledClient(profile_config(settings),output/f'measurements/later/{scene["scenario_id"]}',ReplayClock())
        result=classify_llm(state,client)
        if client.calls[0]['state_sha256']!=scene['snapshots']['later']['state_sha256']:
            raise ValueError('Later diagnostic point changed')
        later.append({'scenario_id':scene['scenario_id'],'snapshot':scene['snapshots']['later'],
                      'llm':result,'timing':timing_breakdown(client.calls[0]),'primary_score':False})
        write_json(output/'measurements/later-summary.json',later)
    manifest=json.loads((output/'manifest.json').read_text())
    for path,digest in manifest['implementation_sha256'].items():
        if sha(Path(path).read_bytes())!=digest:
            raise ValueError(f'Source changed during measurement: {path}')
    verify_freeze(baseline,worktree=False)
    report(output,baseline,protocol,rows,later)


def report(output,baseline,protocol,rows,later):
    original=json.loads((baseline/'measurements/operational-summary.json').read_text())
    expected={(n,s['scenario_id']) for n in range(1,4) for s in protocol['scenarios']}
    if {(r['repetition'],r['scenario_id']) for r in rows}!=expected or len(rows)!=27:
        raise ValueError('Operational trials incomplete')
    timings=[r['timing'] for r in rows]
    comparison={
        'rule_frames_unchanged':574,'eligible_frames_unchanged':402,'paired_inputs_verified':27,
        'baseline_aligned':sum(r['action_aligned'] for r in original),
        'compact_aligned':sum(r['action_aligned'] for r in rows),
        'baseline_median_eligible_to_output_s':statistics.median(r['timing']['eligible_to_observed_output_s'] for r in original),
        'compact_median_eligible_to_output_s':statistics.median(t['eligible_to_observed_output_s'] for t in timings),
        'baseline_median_prompt_tokens':statistics.median(r['timing']['prompt_tokens'] for r in original),
        'compact_median_prompt_tokens':statistics.median(t['prompt_tokens'] for t in timings),
        'baseline_accepted':sum(r['accepted_by_live_client'] is True for r in original),
        'compact_accepted':sum(r['accepted_by_live_client'] is True for r in rows),
        'valid_first_outputs':sum(r['first_output_valid'] for r in rows),
        'per_repetition':[{'repeat':n,'aligned':sum(r['action_aligned'] for r in rows if r['repetition']==n),
            'accepted':sum(r['accepted_by_live_client'] is True for r in rows if r['repetition']==n)} for n in range(1,4)],
    }
    comparison['median_latency_reduction_percent']=100*(1-comparison['compact_median_eligible_to_output_s']/comparison['baseline_median_eligible_to_output_s'])
    comparison['median_input_token_reduction_percent']=100*(1-comparison['compact_median_prompt_tokens']/comparison['baseline_median_prompt_tokens'])
    write_json(output/'comparison.json',comparison)
    text=['# Stage 3: compact shared decision state','',
        'The full SocialState remains the diagnostic record. Both policies now consume the same compact DecisionState, containing observed people, existing categories/labels and every numeric operand required by the tuned v7 rules. Policy constants are cached outside observation data, and identifiers remain in a correlation context. Raw geometry, unused fit statistics and configuration are not sent to the LLM. System instructions and generation/acceptance settings were unchanged.','',
        '## Rule preservation','',
        'The actual frozen v7 implementation was loaded as the reference. Readiness, actions, reason codes, target IDs/epochs and decision/source/session IDs match on every one of 574 frames (402 eligible). Independent boundary tests cover nulls, exact thresholds, recurring attention, path/pass precedence, multiple/missing people, empty intervals and failure flags.','',
        '## Frozen operational comparison','',
        'First eligible timestamps are the original frozen timestamps; later diagnostics use the separately frozen last eligible timestamp. All 27 operational rule/LLM inputs were checked for identical compact content and full source equivalence. No later success substitutes for an incorrect first output.','',
        '| Metric | Full baseline | Compact |','| --- | ---: | ---: |',
        f"| First-action alignment, 27 trials | {comparison['baseline_aligned']}/27 | {comparison['compact_aligned']}/27 |",
        f"| Median eligibility → complete observed output | {comparison['baseline_median_eligible_to_output_s']:.2f}s | {comparison['compact_median_eligible_to_output_s']:.2f}s |",
        f"| Median input tokens, including unchanged instructions | {comparison['baseline_median_prompt_tokens']:g} | {comparison['compact_median_prompt_tokens']:g} |",
        f"| Live replay acceptance | {comparison['baseline_accepted']}/27 | {comparison['compact_accepted']}/27 |",'',
        f"Median input reduction: {comparison['median_input_token_reduction_percent']:.1f}%. Median complete-output latency reduction: {comparison['median_latency_reduction_percent']:.1f}%. These are separate warm-model runs on the same model/runtime and scenario order, not simultaneous measurements; cache counts and timings are retained.",'',
        '| Scenario | Frozen rule action | Compact first action, repetitions 1 / 2 / 3 | Baseline median eligibility → output | Compact median eligibility → output |',
        '| --- | --- | --- | ---: | ---: |']
    for scene in protocol['scenarios']:
        rs=sorted((r for r in rows if r['scenario_id']==scene['scenario_id']),key=lambda r:r['repetition'])
        old=[r for r in original if r['scenario_id']==scene['scenario_id']]
        actions=' / '.join(r['first_decision']['action'] if r['first_decision'] else 'NO_VALID_OUTPUT' for r in rs)
        text.append(f"| {scene['scenario_id']} | {scene['snapshots']['first']['rule_decision']['action']} | {actions} | {statistics.median(r['timing']['eligible_to_observed_output_s'] for r in old):.2f}s | {statistics.median(r['timing']['eligible_to_observed_output_s'] for r in rs):.2f}s |")
    text+=['','## Later diagnostics (excluded from score)','',
           '| Scenario | Frozen later time | Rule action | Compact LLM action |','| --- | ---: | --- | --- |']
    for row in later:
        text.append(f"| {row['scenario_id']} | {row['snapshot']['since_motion_s']:.2f}s | {row['snapshot']['rule_decision']['action']} | {row['llm']['decision']['action']} |")
    text+=['','The original expected-action lists, including the multiple actions for S3/S8, remain unchanged. S8 first resolves before the person appears. Live acceptance can still fail because a finite recording ends or current readiness changes. These are tuning-set results, not held-out accuracy; no physical robot action was executed.','',
        f"- [Exact first S4 compact input]({output.resolve()}/snapshots/S4/first.decision-state.json)",
        f"- [First-action results and timing]({output.resolve()}/measurements/operational-summary.json)",
        f"- [All-frame rule parity]({output.resolve()}/rule-parity.json)",
        f"- [Comparison metrics]({output.resolve()}/comparison.json)",
        f"- [Frozen original baseline]({baseline.resolve()}/REPORT.md)"]
    (output/'REPORT.md').write_text('\n'.join(text)+'\n')
    print(json.dumps(comparison,indent=2),flush=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,default=Path('var/evaluation/llm-refinement-stage-1-2'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args(argv)
    args.output.mkdir(parents=True,exist_ok=False)
    protocol=prepare(args.baseline,args.output)
    if not args.prepare_only:
        run(protocol,args.output,args.baseline)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
