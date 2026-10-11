"""Frozen poster evaluation: assumed clip endpoints and recorded-speed replay.

No hardware transport is constructed. Use --prepare once, then --run on that folder.
The user's explicit clip assumption is retained, never promoted to verified alignment.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import time
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from app.commands import CommandConfig
from app.inference.live import LiveModelConfig
from app.inference.ollama import OllamaClient, OllamaConfig
from app.policy.llm import build_llm_prompt, decide_llm, PROMPT_VERSION, SYSTEM_PROMPT, SOCIAL_STATE_PREFIX
from app.policy.rules import decide, rule_readiness, POLICY_VERSION
from app.policy.target_lock import LockConfig
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import SocialState, TemporalConfig
from app.state.tracks import TrackConfig
from evaluation.encounters import canonical, select_prior, sha256
from evaluation.audit_recordings import audit_recording
from scripts.replay_first_action import replay, request_json


def write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n')


def command(args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.CalledProcessError) as e:
        return str(e)


def check_frozen(root):
    manifest = json.loads((root / 'manifest.json').read_text())
    for path, digest in manifest['frozen_sha256'].items():
        if sha256(path) != digest:
            raise ValueError('Frozen input changed: ' + path)
    return manifest


def prepare(root):
    root.mkdir(parents=True, exist_ok=False)
    settings = json.loads(Path('config/policy-comparison.json').read_text())
    settings['base_url'] = 'http://127.0.0.1:11434'
    temporal = TemporalConfig.from_file(settings['temporal_config'])
    tracking = TrackConfig.from_file(settings['track_config'])
    model = OllamaConfig(**{k: settings[k] for k in ('base_url','model','temperature','seed','num_ctx','num_predict','timeout_seconds')})
    scenes = json.loads(Path('config/final-scenarios.json').read_text())['scenarios']
    alignment=[]
    for scene in scenes:
        path=Path(scene['recording'])
        assert sha256(path)==scene['source_sha256']
        packets=[json.loads(x) for x in path.read_text().splitlines()]
        perceptions=[p for p in packets if p['stream']=='perception']
        n=int(scene['scenario_id'][1:])
        original=Path(f'var/recordings/final_scenario_{n}-sdk.jsonl')
        original_packets=[json.loads(x) for x in original.read_text().splitlines()]
        original_keys={(p['stream'],p['received_monotonic_us']):p for p in original_packets}
        for p in packets:
            prior=original_keys[(p['stream'],p['received_monotonic_us'])]
            assert all(p[k]==prior[k] for k in ('session_id','packet','received_unix_us'))
        scene['endpoints']={'assumed_survey_video_end':perceptions[-1]['received_monotonic_us']}
        scene['survey_video_mapping']='ASSUMED_BY_USER'
        scene['capture_session']=perceptions[0]['session_id']
        scene['original_recording']=str(original)
        # Intended labels are not included in the new endpoint contract.
        scene.pop('expected_actions',None)
        alignment.append({'scenario_id':scene['scenario_id'],'mapping_status':'ASSUMED_BY_USER',
            'survey_clip_identity':str(path),'survey_clip_sha256':None,'sdk_sha256':sha256(path),
            'capture_session':scene['capture_session'],'original_capture':str(original),
            'trim_start_us':scene['motion_start_monotonic_us'],
            'endpoint_timestamp_us':perceptions[-1]['received_monotonic_us'],
            'timing_uncertainty_s':None,'evidence':'User instructed treating these trimmed SDK recordings as exact survey clips. No video asset or independently verified exposure/end mapping.',
            'history_start':'first retained perception; prior setup excluded by user-selected clip assumption'})
    contract={'version':'poster-assumed-endpoint-v1','max_state_age_s':.25,'scenarios':scenes,
        'selection':'last perception <= assumed endpoint, never last READY',
        'mapping_assumption':'User explicitly substituted final-scenarios-audit/trimmed for survey clips; NOT independently verified.'}
    write(root/'contract.json',contract)
    write(root/'alignment.json',alignment)
    (root/'system-prompt.txt').write_text(SYSTEM_PROMPT)
    (root/'user-prefix.txt').write_text(SOCIAL_STATE_PREFIX)
    poster=Path('/home/anyone/Downloads/Poster_59.pptx')
    with ZipFile(poster) as z:
        text='\n'.join(e.text or '' for name in z.namelist() if name.startswith('ppt/slides/slide') and name.endswith('.xml')
                       for e in ET.fromstring(z.read(name)).iter() if e.tag.endswith('}t'))
    (root/'poster-source.txt').write_text(text)
    paths=set()
    for folder in ('app','robot/navel_client','evaluation','scripts'):
        paths.update(str(p) for p in Path(folder).rglob('*.py'))
    paths.update(str(p) for p in Path('config').glob('*.json'))
    paths.update([str(poster),'var/evaluation/final-scenarios-audit/audit.json',
                  'var/evaluation/final-scenarios-audit/README.md'])
    for scene in scenes:
        paths.update((scene['recording'],scene['original_recording']))
    old=json.loads(Path(settings['evaluation_contract']).read_text())
    for scene in old['scenarios']:
        paths.update((scene['recording'],scene['camera_manifest']))
    runtime={'platform':platform.platform(),'python':platform.python_version(),
             'cpu':command(['lscpu']),'memory':command(['free','-b']),
             'gpu':command(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv']),
             'packages':command(['.venv/bin/python','-m','pip','freeze'])}
    try:
        tags=request_json(settings['base_url'],'/api/tags')
        version=request_json(settings['base_url'],'/api/version')
        selected=next((m for m in tags['models'] if m['name']==settings['model']),None)
        model_error=None if selected else 'Configured model absent; install qwen2.5:7b in local Ollama.'
    except OSError as e:
        tags=version=selected=None
        model_error=str(e)+'; start Ollama at http://127.0.0.1:11434 with qwen2.5:7b.'
    manifest={'created_utc':datetime.now(timezone.utc).isoformat(),
      'user_timezone':'Pacific/Auckland','settings':settings,'effective_temporal':temporal.model_dump(),
      'effective_tracking':asdict(tracking),'effective_model':model.model_dump(),
      'effective_live_model':LiveModelConfig(mode='llm',llm_config=model).model_dump(),
      'effective_lock':LockConfig.from_file('config/target-lock.json').model_dump(),
      'effective_commands':CommandConfig.from_file('config/commands.json').model_dump(),
      'other_defaults':{'sdk_max_locomotion_age_s':1.,'endpoint_max_state_age_s':.25,'single_trial_wait_s':30.,
                        'single_trial_current_max_age_s':1.,'model_trial_max_age_s':settings['model_trial_max_age_s'],
                        'replay_rate':1.,'poll_interval_s':.02,'warm_keep_alive':'5m'},
      'actual_rule_version':POLICY_VERSION,'actual_prompt_version':PROMPT_VERSION,
      'version_discrepancy':{'declared_rule':settings['rule_version'],'declared_prompt':settings['prompt_version']},
      'git_head':command(['git','rev-parse','HEAD']),'git_status':command(['git','status','--short']),
      'runtime':runtime,'ollama_tags':tags,'ollama_version':version,'selected_model':selected,
      'model_available':selected is not None,'model_error':model_error,
      'endpoint_llm_repeats':5,'operational_repeats':3,'operational_orders':[['rules','llm'],['llm','rules'],['rules','llm']],
      'source_selection':'User-updated: trimmed final captures for assumed endpoints and operational replay; original scenario-01..09 retained as separate RQ1 audit.',
      'workbook_status':'MISSING','workbook_expected_sha256':'0f9ee6940c8c1687d38c00565e1f0e46b44e4b3fcafea960be1e4aa3640be0f2',
      'protocol_status':'NOT_FOUND_NOT_VERIFIED','physical_execution':False,
      'frozen_sha256':{p:sha256(p) for p in sorted(paths)}}
    plan='''# Frozen evaluation design

RQ1: structure gaze/distance histories into a shared SocialState and measure evidence availability.
RQ2: compare policy decisions with preferred actions and independent 1–5 appropriateness ratings.

The latest user instruction treats final-scenarios-audit/trimmed SDK recordings as the exact survey clips. This is an explicit assumption, not independently verified synchronisation. The last retained perception defines each assumed clip endpoint; unknown video timing uncertainty prevents a verified-endpoint claim. Nine original scenario-01..09 captures are audited separately, never pooled with the final captures as additional independent scenarios.

Reconstruct each selected capture causally from its first perception via the existing adapter, tracker and estimator. Select last perception at/before endpoint, max age 0.25s, unchanged production readiness. Freeze one canonical SocialState for both policies. One rule call and five sequential LLM calls per eligible endpoint, fixed model/seed/settings, no retries, fallback or majority vote. All nine scenarios remain in coverage tables. No diagnostic framewise model calls.

Operational: three complete recorded-speed repetitions, fresh production receiver and SingleTrial per scenario/policy. Within each scenario run rules/LLM, then LLM/rules, then rules/LLM. Warm weights separately before each repetition. Stop at first validated output including CONTINUE or existing termination. Ingest asynchronously; preserve pending, expired, rejected and late output. No hardware or physical execution.

Use 42-response source workbook if supplied; never substitute historical 37-response counts or preparation checks. Anonymous row indices only; no timestamps, identifying details or free text. Valid preference denominator includes unable-to-judge; missing and unexpected excluded and counted. Independent valid integer ratings 1–5 only, with missing/non-rating separate. Modal action set retains ties; highest mean rating separate.

Verified-endpoint metrics require verified mapping and workbook. Assumed-endpoint human metrics, if the workbook becomes available, must be labelled conditional on the user's assumption. SDK/camera proxies and first actions receive no human scores. Preference support = action votes / valid preference responses; appropriateness = mean valid action rating; modal agreement = membership in modal action set. Per-scenario LLM repeat means before equal-scenario paired differences; both policies must have valid outputs. Failures get no score. No intervals/significance planned, no generalisation from this development set; repeats/frames are not independent encounters.

Report whole-recording and causal endpoint-prefix evidence coverage, first readiness, UID gaps/epochs, exact holds. Coverage is frame-weighted with explicit numerator/denominator. Distance change is robot-relative, not human motion; missing people do not prove clear space; absent conflict/pass cues remain UNKNOWN. Separate request durations, elapsed encounter times, source age and acceptance. Rule receiver-request time includes processing and is not isolated inference latency.

Sources/configuration/code hashes and actual versions are frozen in manifest.json before inference. Preparation metadata says v5/v10; current code takes precedence. Missing workbook or mappings do not stop independent audits/replay. Reports may not claim comfort, safety, navigation success or physically executed ratings.
'''
    (root/'evaluation-plan.md').write_text(plan)
    manifest['design_sha256']={str(root/p):sha256(root/p) for p in ('contract.json','alignment.json','evaluation-plan.md','system-prompt.txt','user-prefix.txt')}
    write(root/'manifest.json',manifest)
    print(root,flush=True)


def endpoint_rows(scene, items, client):
    timestamp=scene['endpoints']['assumed_survey_video_end']
    chosen=select_prior(items,timestamp)
    if chosen is None:
        raise ValueError('No prior perception')
    exact=build_llm_prompt(chosen.state).social_state_json
    state=SocialState.model_validate_json(exact)
    age=(timestamp-state.robot_timestamp_us)/1e6
    assert age>=0
    hold=rule_readiness(state,stale=age>.25)
    common={'scenario_id':scene['scenario_id'],'mapping_status':'ASSUMED_BY_USER',
            'condition':'assumed_survey_video_end','endpoint_timestamp_us':timestamp,
            'source_timestamp_us':state.robot_timestamp_us,'source_line':chosen.source['line_number'],
            'state_age_s':age,'state_sha256':hashlib.sha256(exact.encode()).hexdigest(),
            'readiness':'NOT_READY' if hold else 'READY','readiness_reason':hold,
            'score_eligibility_reason':'WORKBOOK_MISSING; ALIGNMENT_ASSUMED_NOT_VERIFIED',
            'preference_support':None,'appropriateness':None,'modal_agreement':None,'physical_execution':False}
    rows=[]
    for policy, repeats in (('rules',1),('llm',5)):
        for repeat in range(1,repeats+1):
            row={**common,'policy':policy,'repeat':repeat,'action':None,'reason':hold,
                 'duration_s':None,'error':None,'status':'NOT_READY' if hold else 'NOT_RUN'}
            if not hold:
                frozen=SocialState.model_validate_json(exact)
                assert build_llm_prompt(frozen).social_state_json==exact
                if policy=='rules':
                    start=time.perf_counter(); result=decide(frozen)
                    row.update(status='DECIDED',action=result.decision,reason=result.reason_code,
                               duration_s=time.perf_counter()-start,output=result.model_dump())
                elif client is not None:
                    output=decide_llm(frozen,client).to_dict()
                    row.update(status='DECIDED' if output['ok'] else 'ERROR',output=output,
                               action=output['decision']['action'] if output['decision'] else None,
                               reason=output['decision']['reason'] if output['decision'] else None,
                               duration_s=output['request_duration_s'],error=output['error'])
                else:
                    row['reason']='CONFIGURED_MODEL_UNAVAILABLE'
            rows.append({**row,'input_state_json':exact})
    return rows


def run(root):
    manifest=check_frozen(root)
    for p,h in manifest['design_sha256'].items():
        assert sha256(p)==h
    contract=json.loads((root/'contract.json').read_text())
    settings=manifest['settings']
    temporal=TemporalConfig.model_validate(manifest['effective_temporal'])
    tracking=TrackConfig(**manifest['effective_tracking'])
    client=OllamaClient(OllamaConfig.model_validate(manifest['effective_model'])) if manifest['model_available'] else None
    with (root/'endpoint-inputs-outputs.jsonl').open('x') as stream:
        for scene in contract['scenarios']:
            items=list(iter_replay_states(scene['recording'],'sdk',track_config=tracking,temporal_config=temporal))
            print('Endpoint '+scene['scenario_id'],flush=True)
            for row in endpoint_rows(scene,items,client):
                stream.write(canonical(row)+'\n'); stream.flush()
    for repeat,order in enumerate(manifest['operational_orders'],1):
        directory=root/f'operational-repeat-{repeat}'
        directory.mkdir()
        if client:
            started=time.perf_counter()
            warm=request_json(settings['base_url'],'/api/generate',{'model':settings['model'],
                 'prompt':'','stream':False,'keep_alive':'5m','options':{'num_ctx':settings['num_ctx']}})
            write(directory/'warmup.json',{'duration_s':time.perf_counter()-started,'response':warm,'measured_encounter':False})
        for scene in contract['scenarios']:
            for policy in order:
                print(f'Operational {repeat} {scene["scenario_id"]} {policy}',flush=True)
                if policy=='llm' and not client:
                    (directory/policy/scene['scenario_id']).mkdir(parents=True)
                    write(directory/policy/scene['scenario_id']/'result.json',{'scenario_id':scene['scenario_id'],
                          'policy':policy,'status':'NOT_RUN','action':None,'reason':manifest['model_error']})
                    continue
                result=replay(scene,policy,directory/policy/scene['scenario_id'],settings)
                print(json.dumps({k:result[k] for k in ('action','accepted_by_live_client','stop_elapsed_s')}),flush=True)
    check_frozen(root)
    write(root/'run-complete.json',{'completed_utc':datetime.now(timezone.utc).isoformat(),'frozen_inputs_unchanged':True})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    if args.prepare: prepare(args.output)
    if args.run: run(args.output)

if __name__=='__main__': main()
