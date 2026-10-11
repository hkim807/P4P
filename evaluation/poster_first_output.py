"""Final-preset poster evaluation, selecting first output at recorded speed.

Three serial paired repetitions; no endpoint inference, robot transport or motion.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median
import time

from app.commands import CommandConfig
from app.inference.live import LiveModelConfig
from app.inference.ollama import OllamaConfig
from app.policy.llm import PROMPT_VERSION, SYSTEM_PROMPT, SOCIAL_STATE_PREFIX
from app.policy.rules import POLICY_VERSION
from app.policy.target_lock import LockConfig
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import TemporalConfig
from app.state.tracks import TrackConfig
from evaluation.encounters import sha256
from evaluation.poster_run import command, write
from evaluation.poster_report import coverage, operational, survey, table, summary_stats
from scripts.replay_first_action import replay, request_json

SETTINGS = Path('config/policy-comparison-final.json')
ORDERS = [('rules','llm'),('llm','rules'),('rules','llm')]


def verify_frozen(root):
    manifest=json.loads((root/'manifest.json').read_text())
    for p,digest in manifest['frozen_sha256'].items():
        if sha256(p)!=digest: raise ValueError('Frozen input changed: '+p)
    for p,digest in manifest['design_sha256'].items():
        if sha256(root/p)!=digest: raise ValueError('Frozen design changed: '+p)
    return manifest


def prepare(root):
    settings=json.loads(SETTINGS.read_text())
    settings['base_url']='http://127.0.0.1:11434'
    # Fail before creating a run if localhost permission/model is unavailable.
    tags=request_json(settings['base_url'],'/api/tags')
    selected=next((m for m in tags['models'] if m['name']==settings['model']),None)
    if not selected: raise RuntimeError('Configured local qwen2.5:7b is unavailable; no model substitution.')
    root.mkdir(parents=True,exist_ok=False)
    contract=json.loads(Path(settings['evaluation_contract']).read_text())
    for scene in contract['scenarios']:
        assert sha256(scene['recording'])==scene['source_sha256']
    write(root/'contract.json',contract)
    (root/'system-prompt.txt').write_text(SYSTEM_PROMPT)
    (root/'user-prefix.txt').write_text(SOCIAL_STATE_PREFIX)
    temporal=TemporalConfig.from_file(settings['temporal_config'])
    tracking=TrackConfig.from_file(settings['track_config'])
    model=OllamaConfig(**{k:settings[k] for k in ('base_url','model','temperature','seed','num_ctx','num_predict','timeout_seconds')})
    paths={str(p) for folder in ('app','robot/navel_client','scripts','evaluation','tests') for p in Path(folder).rglob('*.py')}
    paths.update(str(p) for p in Path('config').glob('*.json'))
    paths.update(s['recording'] for s in contract['scenarios'])
    paths.add('var/evaluation/final-scenarios-audit/audit.json')
    plan='''# Frozen final-preset first-output evaluation

Latest user instruction: use config/policy-comparison-final.json and select the first available policy output. This replaces the previous run's development preset and clip-end decision selection. Earlier runs remain separate. The nine trimmed final captures are assumed to represent survey clips as previously instructed, but the selected action now occurs at the first valid output, potentially before clip end.

Use current production rules and prompt with the exact final temporal/tracking preset and configured qwen2.5:7b. Preserve readiness, freshness, timeouts and all four actions. No tuning, fallback, action correction, majority voting, hardware or physical execution.

Run three complete paired repetitions at recorded speed, from first perception, with fresh receiver and SingleTrial per scenario/policy. Alternate policy order rules/LLM, LLM/rules, rules/LLM within each scenario. Warm model weights separately before each repetition; serialize all model calls. Reconstruct SDK observations with the production adapter, tracker and estimator. Ingest asynchronously during inference. Stop at first validated output including CONTINUE, whether accepted or rejected, or existing no-output termination. Retain failed/pending/expired/late audits. No five-repeat endpoint calls are made in this revised design.

Freeze effective configuration, code/source hashes, Git state, model digest/quantisation, Ollama version, prompt and runtime before measured calls. Verify the first eligible source state matches across policies for every pair. Count output and acceptance separately out of nine per repetition. Report all repeat actions and distributions; no single preferred repeat is chosen. Time from first perception is encounter elapsed time, not inference latency. Model request duration and source age at delivery remain distinct. Isolated rule inference and exact model-completion source age are not separately instrumented.

Audit all nine full recordings and causal prefixes through the first eligible state. Frame-weighted evidence coverage is availability, not accuracy. Missing person detections do not prove clear space; robot-relative distance change does not identify human movement; absent pass/conflict observations stay UNKNOWN. Empty-scene CONTINUE after one second is enabled by this preset and must be identified in interpretation.

No human preference or appropriateness scores are fabricated: the 42-response workbook remains unavailable, and its end-of-video question is different from first-output timing. Intended labels remain contextual development metadata, never inputs to either policy. Repeated calls and frames are not independent encounters. This development-set replay cannot establish human comfort, safety or navigation success.
'''
    (root/'evaluation-plan.md').write_text(plan)
    manifest={'created_utc':datetime.now(timezone.utc).isoformat(),'user_timezone':'Pacific/Auckland',
      'condition':'first_validated_policy_output','settings_path':str(SETTINGS),'settings':settings,
      'effective_temporal':temporal.model_dump(),'effective_tracking':asdict(tracking),'effective_model':model.model_dump(),
      'effective_live_model':LiveModelConfig(mode='llm',llm_config=model).model_dump(),
      'effective_lock':LockConfig.from_file('config/target-lock.json').model_dump(),
      'effective_commands':CommandConfig.from_file('config/commands.json').model_dump(),
      'other_defaults':{'sdk_max_locomotion_age_s':1.,'single_trial_wait_s':30.,'single_trial_current_max_age_s':1.,
        'model_trial_max_age_s':settings['model_trial_max_age_s'],'replay_rate':1.,'poll_interval_s':.02,'warm_keep_alive':'5m'},
      'actual_rule_version':POLICY_VERSION,'actual_prompt_version':PROMPT_VERSION,'operational_orders':ORDERS,
      'model_available':True,'ollama_tags':tags,'selected_model':selected,
      'ollama_version':request_json(settings['base_url'],'/api/version'),
      'ollama_show':request_json(settings['base_url'],'/api/show',{'model':settings['model']}),
      'git_head':command(['git','rev-parse','HEAD']),'git_status':command(['git','status','--short']),
      'runtime':{'python':command(['.venv/bin/python','--version']),'cpu':command(['lscpu']),
        'memory':command(['free','-b']),'gpu':command(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv']),
        'packages':command(['.venv/bin/python','-m','pip','freeze'])},
      'workbook_status':'MISSING','human_scores':'UNAVAILABLE; first output differs from survey-end task',
      'survey_clip_mapping':'ASSUMED_BY_USER','physical_execution':False,
      'frozen_sha256':{p:sha256(p) for p in sorted(paths)},
      'design_sha256':{p:sha256(root/p) for p in ('contract.json','system-prompt.txt','user-prefix.txt','evaluation-plan.md')}}
    assert settings['rule_version']==POLICY_VERSION and settings['prompt_version']==PROMPT_VERSION
    write(root/'manifest.json',manifest)
    print('Frozen '+str(root),flush=True)


def run(root):
    manifest=verify_frozen(root);settings=manifest['settings']
    scenes=json.loads((root/'contract.json').read_text())['scenarios']
    for repeat,order in enumerate(ORDERS,1):
        directory=root/f'operational-repeat-{repeat}';directory.mkdir()
        start=time.perf_counter()
        response=request_json(settings['base_url'],'/api/generate',{'model':settings['model'],'prompt':'',
            'stream':False,'keep_alive':'5m','options':{'num_ctx':settings['num_ctx']}})
        write(directory/'warmup.json',{'duration_s':time.perf_counter()-start,'response':response,'measured_encounter':False})
        for scene in scenes:
            for policy in order:
                print(f'Starting repetition {repeat} {scene["scenario_id"]} {policy}',flush=True)
                result=replay(scene,policy,directory/policy/scene['scenario_id'],settings)
                print(json.dumps({k:result[k] for k in ('scenario_id','policy','action','status','accepted_by_live_client','stop_elapsed_s')}),flush=True)
    verify_frozen(root)
    after=request_json(settings['base_url'],'/api/tags')
    assert next(m for m in after['models'] if m['name']==settings['model'])['digest']==manifest['selected_model']['digest']
    write(root/'ollama-tags-after.json',after)
    write(root/'run-complete.json',{'completed_utc':datetime.now(timezone.utc).isoformat(),'frozen_inputs_unchanged':True})


def report(root):
    manifest=verify_frozen(root)
    assert (root/'run-complete.json').exists()
    scenes=json.loads((root/'contract.json').read_text())['scenarios']
    rows,checks=operational(root)
    assert len(rows)==54 and len({(r['scenario_id'],r['policy'],r['repeat']) for r in rows})==54
    assert all(c['first_ready_state_match'] is not False for c in checks)
    table(root/'first_action_decisions.csv',rows)
    sensor=[]
    with (root/'causal-states.jsonl').open('w') as trace:
        for scene in scenes:
            items=list(iter_replay_states(scene['recording'],'sdk',
                temporal_config=TemporalConfig.model_validate(manifest['effective_temporal']),
                track_config=TrackConfig(**manifest['effective_tracking'])))
            first_index=next((i for i,x in enumerate(items) if x.state.observation_readiness=='READY'),None)
            sensor.append(coverage(scene['scenario_id'],'final_preset_trimmed',items,'whole_recording'))
            if first_index is not None:
                sensor.append(coverage(scene['scenario_id'],'final_preset_trimmed',items[:first_index+1],'first_eligible_prefix'))
            else:
                sensor.append({'scenario_id':scene['scenario_id'],'dataset':'final_preset_trimmed','audit_window':'first_eligible_prefix','status':'NO_READY_STATE'})
            for i,item in enumerate(items):
                trace.write(json.dumps({'scenario_id':scene['scenario_id'],'source_line':item.source['line_number'],
                    'in_first_eligible_prefix':first_index is not None and i<=first_index,'state':item.state.model_dump()})+'\n')
    table(root/'sensor_coverage.csv',sensor)
    survey(root,None)
    paired=[]
    for scene in scenes:
        sid=scene['scenario_id']
        r=[r for r in rows if r['scenario_id']==sid]
        entry={'scenario_id':sid,'condition':'first_validated_policy_output','human_alignment_status':'UNAVAILABLE',
            'intended_actions_context_only':scene.get('expected_actions')}
        for policy in ('rules','llm'):
            pr=[r for r in r if r['policy']==policy]
            entry[policy+'_actions_by_repeat']=[x['action'] for x in pr]
            entry[policy+'_action_distribution']=dict(Counter(x['action'] or 'NO_OUTPUT' for x in pr))
            entry[policy+'_outputs']=sum(x['action'] is not None for x in pr)
            entry[policy+'_accepted']=sum(x['accepted'] is True for x in pr)
            entry[policy+'_median_output_elapsed_s']=summary_stats(pr,'output_elapsed_s')['median']
        paired.append(entry)
    table(root/'scenario_comparison.csv',paired)
    sums=[]
    for repeat in range(1,4):
        for policy in ('rules','llm'):
            r=[r for r in rows if r['repeat']==repeat and r['policy']==policy]
            sums.append({'repeat':repeat,'policy':policy,'scenarios':len(r),
                'outputs':sum(x['action'] is not None for x in r),'accepted':sum(x['accepted'] is True for x in r),
                'rejections':dict(Counter(x['rejection_reason'] for x in r if x['action'] is not None and not x['accepted'])),
                'statuses':dict(Counter(x['status'] for x in r)),
                'model_request_duration_s':summary_stats(r,'model_request_duration_s'),
                'output_elapsed_s':summary_stats(r,'output_elapsed_s')})
    whole=[r for r in sensor if r['audit_window']=='whole_recording']
    summary={'condition':'first_validated_policy_output','settings_path':str(SETTINGS),'independent_scenarios':9,
        'repetitions':3,'scenario_policy_runs':54,'ready_scenarios':sum(r['ready_frames']>0 for r in whole),
        'total_frames':sum(r['perception_frames'] for r in whole),'ready_frames':sum(r['ready_frames'] for r in whole),
        'paired_first_state_matches':sum(c['first_ready_state_match'] is True for c in checks),
        'paired_first_state_unavailable':sum(c['first_ready_state_match'] is None for c in checks),
        'operational':sums,'scenario_comparison':paired,'human_alignment_status':'UNAVAILABLE',
        'physical_execution':False}
    write(root/'summary.json',summary)
    graphs=root/'graph_data';graphs.mkdir(exist_ok=True)
    table(graphs/'first_output_coverage.csv',sums);table(graphs/'first_actions.csv',rows)
    table(graphs/'evidence_readiness.csv',sensor);table(graphs/'scenario_comparison.csv',paired)
    text=['# Final-preset first-output poster evaluation','',
        f"Uses `{SETTINGS}`: {manifest['actual_rule_version']}, {manifest['actual_prompt_version']}, {manifest['settings']['model']}, with the final temporal and tracking settings.",'',
        '**Selection is the first validated policy output during each replay, including CONTINUE.** A valid output is retained even if the client rejects it. These results replace the previous development-preset/clip-end condition for the requested poster comparison. Earlier run artifacts are preserved; their 4/9 endpoint figure is not this condition.',
        '',f"All {summary['ready_scenarios']}/9 recordings reach readiness. Whole-recording coverage is {summary['ready_frames']}/{summary['total_frames']} ready perception frames ({100*summary['ready_frames']/summary['total_frames']:.1f}%, frame-weighted). The independent scenario count is nine; three repetitions do not create 27 independent encounters.",'',
        '| Repetition | Policy | Valid outputs / 9 | Client accepted / 9 |', '|---|---|---:|---:|']
    for s in sums:text.append(f"| {s['repeat']} | {s['policy']} | {s['outputs']} | {s['accepted']} |")
    text+=['','| Scenario | Rules by repetition | LLM by repetition | Rules accepted / 3 | LLM accepted / 3 |','|---|---|---|---:|---:|']
    for p in paired:text.append(f"| {p['scenario_id']} | {', '.join(x or 'NO_OUTPUT' for x in p['rules_actions_by_repeat'])} | {', '.join(x or 'NO_OUTPUT' for x in p['llm_actions_by_repeat'])} | {p['rules_accepted']} | {p['llm_accepted']} |")
    timing=summary_stats(rows,'model_request_duration_s')
    if timing['n']:text+=['',f"Across {timing['n']} measured model requests, median request duration was {timing['median']:.3f} s (range {timing['min']:.3f}–{timing['max']:.3f} s). Encounter output time also includes the wait for evidence. Timing columns preserve unavailable values rather than zero."]
    text+=['',f"First eligible canonical states match across policies in {summary['paired_first_state_matches']}/27 pairs; {summary['paired_first_state_unavailable']} pairs have no paired eligible state. Raw inputs, responses, failures, late outputs and delivery evidence are retained under operational-repeat-*/. The LLM makes its own decisions without rule fallback or correction.",'',
        'RQ1: the adapter, tracker and temporal estimator provide common, inspectable observation histories and validity flags. With the final preset, no-person observations can permit CONTINUE after one second. This explains early empty-scene outputs and does not prove physical route clearance. First decisions can precede later gaze events. Path-conflict/pass-gesture inputs remain UNKNOWN when unmeasured; distance change is robot-relative. These are evidence-availability measurements, not perception accuracy.',
        '', 'RQ2: policy outputs can be compared on identical initial evidence, but human preference/appropriateness scores remain unavailable. The 42-response workbook was not supplied, and first-output timing differs from the survey question at video end. Requested development labels are retained as context only, not human ground truth.',
        '', 'Three fresh recorded-speed repetitions used alternating policy order and serial model requests, with warmup logged separately. Client freshness/readiness/expiry checks were preserved. Rejection categories are derived from recorded delivery errors and failed acceptance predicates. Source age at delivery is recorded; exact model-completion source age and isolated rule inference duration were not separately measured.',
        '', 'These nine recordings were previously used for tuning. No claim of generalisation, human comfort, physical safety or navigation success follows from this replay. No robot transport or movement was executed. All frozen source, code and configuration hashes were checked unchanged before and after execution.']
    (root/'results.md').write_text('\n'.join(text)+'\n')
    r=[s for s in sums if s['policy']=='rules'];l=[s for s in sums if s['policy']=='llm']
    r_out='/'.join(str(s['outputs']) for s in r);l_out='/'.join(str(s['outputs']) for s in l)
    r_acc='/'.join(str(s['accepted']) for s in r);l_acc='/'.join(str(s['accepted']) for s in l)
    results=(f"Using the final scenario preset, {summary['ready_scenarios']} of nine recordings reached decision readiness. Across three repetitions, first valid outputs were {r_out} for rules and {l_out} for the LLM, each out of nine. Client-accepted decisions were {r_acc} and {l_acc}, respectively. "
        f"Both policies received identical first eligible states in {summary['paired_first_state_matches']} paired runs. These development replays measure output availability and delivery, not physical performance. Human preference and appropriateness alignment remains unresolved because the survey workbook is unavailable and first-output timing differs from video-end ratings.")
    conclusion=('The final configuration supports first-output comparison using shared temporal evidence. Output generation and client acceptance must be reported separately, alongside missing or stale observations. These reused development recordings cannot establish human preference alignment or general performance. Future evaluation needs human references matched to decision timing, timely delivery, better cue coverage and new live encounters.')
    assert 70<=len(results.split())<=100
    assert 45<=len(conclusion.split())<=65
    (root/'poster-copy.md').write_text(f'# Results ({len(results.split())} words)\n\n{results}\n\n# Conclusion ({len(conclusion.split())} words)\n\n{conclusion}\n\n# Captions\n\n'
        '1. First policy outputs and client acceptance across nine scenarios, shown separately for each of three repetitions.\n'
        '2. Actions at the first validated output; unavailable outputs remain visible. No physical actions were executed.\n'
        '3. Whole-recording temporal readiness; frame-weighted availability, not perception accuracy.\n')
    (root/'graph-plan.md').write_text('''# First-output graphs

Use graph_data/first_output_coverage.csv for output and accepted counts, denominator nine per policy/repetition. Use first_actions.csv for per-scenario action/acceptance cells and separate timing dots for encounter elapsed seconds and model-request seconds. Keep no-output cells and blank missing timings. Use evidence_readiness.csv with audit_window=whole_recording for ready/total frame bars; first_eligible_prefix is a separate causal-prefix panel. No endpoint graph or human-alignment scores belong in this first-output condition. Repetitions are not independent scenarios.
''')
    import csv
    with (root/'first_action_decisions.csv').open() as f:exported=list(csv.DictReader(f))
    assert len(exported)==54
    for s in sums:
        selected=[x for x in exported if x['policy']==s['policy'] and int(x['repeat'])==s['repeat']]
        assert len(selected)==9
        assert sum(bool(x['action']) for x in selected)==s['outputs']
        assert sum(x['accepted']=='True' for x in selected)==s['accepted']
    assert all(x['human_score']=='' and x['physical_execution']=='False' for x in exported)
    verify_frozen(root)
    write(root/'verification.json',{'frozen_inputs_unchanged':True,'complete_unique_scenario_policy_repeat_rows':54,
        'paired_first_states_match':all(c['first_ready_state_match'] is not False for c in checks),
        'paired_first_state_matches':summary['paired_first_state_matches'],'causal_ingestion_prefixes_verified':True,
        'csv_to_summary_reconciled':True,'human_scores_not_fabricated':True,'physical_execution':False,
        'condition':'first_validated_policy_output'})
    (root/'verification.md').write_text('''# Reproduction and verification

The immutable manifest identifies the final preset, effective defaults, prompt, model digest, runtime and frozen code/source hashes. All hashes match after replay/reporting. Every operational observation trace is checked against its exact causal source prefix; ingestion counts agree. First eligible canonical state hashes match across policies when available. All 54 unique scenario/policy/repetition rows are retained, and exported CSV counts reconcile to summary.json. Human scores remain blank. See tests.log for first-output stopping, asynchronous ingestion, stale/expired delivery and evaluation tests.

Reproduce with local Ollama access from the repository root; choose a new output directory:

```sh
.venv/bin/python -m evaluation.poster_first_output --output var/evaluation/poster-final-first-NEW --prepare --run --report
```

The original run's endpoint inference is intentionally absent. A first output is not the same as an accepted or physically executed action. Survey scores require human references for this decision moment.
''')
    hashes={str(p.relative_to(root)):sha256(p) for p in sorted(root.rglob('*')) if p.is_file() and p.name not in ('artifact-hashes.json','run.log')}
    write(root/'artifact-hashes.json',hashes)
    print(json.dumps({'ready_scenarios':summary['ready_scenarios'],'operational':sums},indent=2),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--prepare',action='store_true');p.add_argument('--run',action='store_true');p.add_argument('--report',action='store_true')
    a=p.parse_args()
    if a.prepare:prepare(a.output)
    if a.run:run(a.output)
    if a.report:report(a.output)
if __name__=='__main__':main()
