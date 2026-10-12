"""Tidy poster data/reporting, independent of policy execution and historical scores."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean, median
from app.policy.llm import build_llm_prompt
from app.policy.rules import decide, rule_readiness
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import SocialState, TemporalConfig
from app.state.tracks import TrackConfig
from evaluation.audit_recordings import audit_recording
from evaluation.encounters import select_prior, sha256
from evaluation.poster_run import write
from evaluation.poster_survey import ACTIONS, UNABLE, verify_workbook, score


def validate_execution_inputs(root):
    """Keep the pre-call manifest immutable and disclose reporting-only edits.

    poster_survey is not imported by poster_run or any production module and
    does not participate in input reconstruction, inference or acceptance.
    Its later workbook validation improvements cannot change measured outputs.
    """
    manifest=json.loads((root/'manifest.json').read_text())
    changed={p:{'frozen':h,'current':sha256(p)} for p,h in manifest['frozen_sha256'].items() if sha256(p)!=h}
    if set(changed)-{'evaluation/poster_survey.py'}:
        raise ValueError('Execution input changed: '+str(changed))
    for p,h in manifest['design_sha256'].items():
        assert sha256(p)==h, 'Frozen design changed'
    manifest['_reporting_only_changes']=changed
    return manifest


def readlines(p):
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()] if Path(p).exists() else []


def table(path, rows):
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with Path(path).open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys); writer.writeheader()
        writer.writerows({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in rows)


def digest_state(state):
    return hashlib.sha256(build_llm_prompt(SocialState.model_validate(state)).social_state_json.encode()).hexdigest()


def coverage(sid,dataset,items,window):
    holds=Counter(rule_readiness(i.state) or 'READY' for i in items)
    origin=items[0].state.robot_timestamp_us
    frames=[]; missing=[]; uids=set(); epochs=set(); gaps=[]
    previous={}; previous_index={}
    for index,item in enumerate(items):
        s=item.state
        observed=[p for p in s.people if p.visibility=='OBSERVED']
        uids.update(p.uid for p in observed)
        epochs.update((p.uid,p.track_epoch) for p in observed)
        for p in observed:
            if p.uid in previous and index>previous_index[p.uid]+1:
                gaps.append({'uid':p.uid,'last_seen_offset_s':(previous[p.uid]-origin)/1e6,
                             'returned_offset_s':(s.robot_timestamp_us-origin)/1e6,
                             'between_detections_s':(s.robot_timestamp_us-previous[p.uid])/1e6})
            previous[p.uid]=s.robot_timestamp_us; previous_index[p.uid]=index
        frames.append({'detected':bool(observed),
            'current_gaze':any(p.evidence.latest_gaze_looking is not None for p in observed),
            'gaze_evidence':any(p.evidence.gaze_valid for p in observed),
            'distance':any(p.evidence.latest_distance_valid for p in observed),
            'distance_trend':any(p.evidence.distance_trend_valid for p in observed),
            'path_conflict_measured':any(p.path_relation!='UNKNOWN' for p in observed),
            'pass_gesture_measured':any(p.pass_gesture!='UNKNOWN' for p in observed)})
        if not observed: missing.append(index)
    intervals=[]
    for index in missing:
        if not intervals or index!=intervals[-1][-1]+1: intervals.append([index])
        else: intervals[-1].append(index)
    missing_intervals=[{'first_offset_s':(items[g[0]].state.robot_timestamp_us-origin)/1e6,
                        'last_offset_s':(items[g[-1]].state.robot_timestamp_us-origin)/1e6,
                        'frames':len(g)} for g in intervals]
    ready=[i for i in items if rule_readiness(i.state) is None]
    row={'scenario_id':sid,'dataset':dataset,'audit_window':window,'status':'AUDITED',
         'perception_frames':len(items),'span_s':(items[-1].state.robot_timestamp_us-origin)/1e6,
         'ready_frames':len(ready),'readiness_fraction':len(ready)/len(items),
         'first_ready_offset_s':(ready[0].state.robot_timestamp_us-origin)/1e6 if ready else None,
         'end_hold_reason':rule_readiness(items[-1].state),'hold_counts':dict(holds),
         'uids':sorted(uids),'uid_count':len(uids),'track_epochs':len(epochs),
         'uid_gap_intervals':gaps,'no_detection_intervals':missing_intervals,
         'path_conflict_status':'UNKNOWN' if not any(f['path_conflict_measured'] for f in frames) else 'MEASURED',
         'pass_gesture_status':'UNKNOWN' if not any(f['pass_gesture_measured'] for f in frames) else 'MEASURED'}
    for key in frames[0]:
        row[key+'_frames']=sum(f[key] for f in frames)
        row[key+'_fraction']=row[key+'_frames']/len(items)
    return row


def audit(root,manifest):
    temporal=TemporalConfig.model_validate(manifest['effective_temporal'])
    tracking=TrackConfig(**manifest['effective_tracking'])
    contract=json.loads((root/'contract.json').read_text())
    original=json.loads(Path(manifest['settings']['evaluation_contract']).read_text())
    rows=[]; raw={}; diagnostics=[]
    with (root/'causal-states.jsonl').open('w') as trace:
        for dataset,scenes in (('user_selected_trimmed',contract['scenarios']),('original_nine',original['scenarios'])):
            for scene in scenes:
                sid=scene['scenario_id']
                items=list(iter_replay_states(scene['recording'],'sdk',track_config=tracking,temporal_config=temporal))
                for item in items:
                    trace.write(json.dumps({'scenario_id':sid,'dataset':dataset,'source_line':item.source['line_number'],
                        'state':item.state.model_dump(),'processing':item.processing})+'\n')
                rows.append(coverage(sid,dataset,items,'whole_recording'))
                if dataset=='user_selected_trimmed':
                    endpoint=scene['endpoints']['assumed_survey_video_end']
                    prefix=[i for i in items if i.state.robot_timestamp_us<=endpoint]
                    rows.append(coverage(sid,dataset,prefix,'assumed_endpoint_prefix'))
                else:
                    rows.append({'scenario_id':sid,'dataset':dataset,'audit_window':'verified_survey_endpoint_prefix',
                                 'status':'ALIGNMENT_UNVERIFIED','perception_frames':None,'ready_frames':None})
                    raw[sid]=audit_recording(scene['recording'])
                    for condition,t in scene['endpoints'].items():
                        prior=select_prior(items,t); state=prior.state
                        age=(t-state.robot_timestamp_us)/1e6
                        hold=rule_readiness(state,stale=age>.25)
                        diagnostics.append({'scenario_id':sid,'dataset':dataset,'condition':condition,
                            'endpoint_timestamp_us':t,'source_timestamp_us':state.robot_timestamp_us,
                            'state_age_s':age,'hold_reason':hold,'rule_action':None if hold else decide(state).decision,
                            'human_score':None,'score_eligibility_reason':'PROXY_CONDITION_EXCLUDED'})
    table(root/'sensor_coverage.csv',rows)
    table(root/'proxy_diagnostics.csv',diagnostics)
    write(root/'original-source-audit.json',raw)
    return rows


def survey(root, workbook):
    if workbook:
        reference=verify_workbook(workbook)
        write(root/'anonymous-survey-rows.json',reference.pop('anonymous_rows'))
    else:
        reference={'status':'WORKBOOK_MISSING','respondents':None,'scenarios':{},
          'source_expected_respondents':42,'historical_reference_respondents':37,
          'expected_sha256':'0f9ee6940c8c1687d38c00565e1f0e46b44e4b3fcafea960be1e4aa3640be0f2',
          'verification':'No workbook in supplied attachments, repository or accessible Downloads search. Counts, ratings and preparation checks NOT_VERIFIED; historical counts not substituted.',
          'protocol_verification':'NOT_PERFORMED_NO_PROTOCOL'}
    write(root/'human-reference.json',reference)
    prefs=[]; ratings=[]
    for n in range(1,10):
        sid=f'S{n}'; ref=reference['scenarios'].get(sid)
        for a in (*ACTIONS,UNABLE):
            prefs.append({'scenario_id':sid,'response_category':a,'status':reference['status'],
               'count':ref['preferred_counts'][a] if ref else None,
               'valid_preference_denominator':ref['valid_preference_n'] if ref else None,
               'proportion':ref['preferred_counts'][a]/ref['valid_preference_n'] if ref and ref['valid_preference_n'] else None,
               'action_only_denominator':ref['action_only_n'] if ref else None,
               'action_only_proportion':ref['preferred_counts'][a]/ref['action_only_n'] if ref and ref['action_only_n'] and a in ACTIONS else None,
               'missing_preference_n':ref['missing_preference_n'] if ref else None,
               'unexpected_preference_n':ref['unexpected_preference_n'] if ref else None})
        for a in ACTIONS:
            r=ref['ratings'][a] if ref else {}
            ratings.append({'scenario_id':sid,'action':a,'status':reference['status'],
                'rating_n':r.get('n'),'missing_or_nonrating':r.get('missing_or_nonrating'),
                'mean':r.get('mean'),'median':r.get('median'),
                **{f'rating_{i}_n':r.get('distribution',{}).get(str(i)) for i in range(1,6)}})
    table(root/'human_preferences.csv',prefs); table(root/'human_ratings.csv',ratings)
    return reference,prefs,ratings


def rejection(result):
    if result.get('action') is None: return 'NO_VALID_OUTPUT'
    if result.get('accepted_by_live_client'): return None
    d=result.get('acceptance_details') or {}; output=result.get('resolved_output') or {}
    reasons=[]
    if output.get('delivery_error'): reasons.append('DELIVERY_'+output['delivery_error'].get('category','error').upper())
    if d.get('failure_reason'): reasons.append(d['failure_reason'])
    if d.get('model_source_age_s',0)>10: reasons.append('MODEL_SOURCE_EXPIRED')
    if d.get('latest_observation_age_s',0)>1: reasons.append('LATEST_OBSERVATION_STALE')
    if d.get('current_readiness') is False: reasons.append('CURRENT_STATE_NOT_READY')
    return '|'.join(reasons) or 'CLIENT_REJECTED_OTHER_CHECK'


def operational(root):
    rows=[]; pair_checks=[]
    scenes=json.loads((root/'contract.json').read_text())['scenarios']
    source_perceptions={}
    for scene in scenes:
        records=readlines(scene['recording'])
        source_perceptions[scene['scenario_id']]=[(line+1,r['received_monotonic_us']) for line,r in enumerate(records) if r['stream']=='perception']
    for repeat in range(1,4):
        for n in range(1,10):
            sid=f'S{n}'; pair=[]
            for policy in ('rules','llm'):
                folder=root/f'operational-repeat-{repeat}'/policy/sid
                result=json.loads((folder/'result.json').read_text())
                events=readlines(folder/'events.jsonl'); model=readlines(folder/'models.jsonl')
                observations=[e for e in events if e['event']=='observation']
                if result['status']!='NOT_RUN':
                    assert len(observations)==result['frames_ingested']
                    assert [(e['line_number'],e['source_timestamp_us']) for e in observations]==source_perceptions[sid][:len(observations)]
                first=next((e for e in observations if e['response']['policy_readiness']['status']=='READY'),None)
                h=digest_state(first['response']['social_state']) if first else None
                inference=[m['llm_inference'] for m in model]
                output=result.get('resolved_output') or {}
                raw_source=output.get('source_robot_timestamp_us')
                accepted=result.get('accepted_by_live_client')
                output_event=next((e for e in observations if e['source_timestamp_us']==raw_source),None)
                request_duration=output.get('request_duration_s') if policy=='llm' else None
                receiver_duration=output_event.get('receiver_request_duration_s') if output_event else None
                for m in model:
                    assert digest_state(m['social_state'])==hashlib.sha256(m['social_state_json'].encode()).hexdigest()
                initial_model_hash=hashlib.sha256(model[0]['social_state_json'].encode()).hexdigest() if model else None
                if initial_model_hash is not None: assert initial_model_hash==h
                row={'scenario_id':sid,'policy':policy,'repeat':repeat,'dataset':'user_selected_trimmed',
                    'policy_order':'rules,llm' if repeat!=2 else 'llm,rules',
                    'status':result['status'],'action':result.get('action'),
                    'accepted':accepted,'rejection_reason':rejection(result),
                    'rejection_reason_provenance':'delivery error plus recorded failed acceptance predicates; SingleTrial returns a boolean',
                    'first_ready_offset_s':(first['source_timestamp_us']-result['perception_origin_monotonic_us'])/1e6 if first else None,
                    'first_ready_replay_elapsed_s':first['elapsed_s'] if first else None,
                    'first_ready_state_sha256':h,'initial_model_state_sha256':initial_model_hash,
                    'output_elapsed_s':result.get('resolved_elapsed_s'),'model_request_duration_s':request_duration,
                    'receiver_request_duration_s':receiver_duration,
                    'rule_inference_duration_s':None,'rule_inference_timing_status':'NOT_SEPARATELY_INSTRUMENTED',
                    'source_age_at_delivery_s':(result.get('acceptance_details') or {}).get('model_source_age_s'),
                    'latest_observation_age_s':(result.get('acceptance_details') or {}).get('latest_observation_age_s'),
                    'source_age_at_model_completion_s':request_duration,
                    'source_age_at_model_completion_note':'Unavailable exact queue-start offset; request duration is a lower bound, not exact source age' if request_duration is not None else None,
                    'delivery_status':output.get('delivery_status'),
                    'stop_elapsed_s':result.get('stop_elapsed_s'),'frames_ingested':result.get('frames_ingested'),
                    'max_dispatch_lag_s':result.get('max_dispatch_lag_s'),'model_calls':len([i for i in inference if i.get('request_duration_s') is not None]),
                    'failed_model_calls':sum(i['status']!='succeeded' for i in inference),
                    'late_audit_outputs_after_termination':sum(i['status']=='succeeded' for i in inference) if result.get('action') is None else 0,
                    'failure_events':result.get('model_failures'),'physical_execution':False,
                    'human_score':None,'score_eligibility_reason':'FIRST_ACTION_NOT_SURVEY_ENDPOINT'}
                # Exact model completion source age is not available from independent UTC audit clock.
                row['source_age_at_model_completion_s']=None
                rows.append(row); pair.append(row)
            pair_checks.append({'scenario_id':sid,'repeat':repeat,
                'first_ready_state_match':pair[0]['first_ready_state_sha256']==pair[1]['first_ready_state_sha256'] if all(p['first_ready_state_sha256'] for p in pair) else None,
                'status':'PAIRED' if all(p['first_ready_state_sha256'] for p in pair) else 'NO_PAIRED_ELIGIBLE_STATE'})
    table(root/'operational_replay.csv',rows); table(root/'operational_state_pairs.csv',pair_checks)
    return rows,pair_checks


def summary_stats(rows,field):
    data=[r[field] for r in rows if r.get(field) is not None]
    return {'n':len(data),'median':median(data) if data else None,'min':min(data) if data else None,'max':max(data) if data else None}


def paired_difference(rule_value, repeat_values):
    """Failures/missing ratings have no score, and repeats are averaged first."""
    valid=[v for v in repeat_values if v is not None]
    return mean(valid)-rule_value if rule_value is not None and valid else None


def report(root,workbook=None):
    manifest=validate_execution_inputs(root)
    assert len(list(root.glob('operational-repeat-*/*/*/result.json')))==54, 'Wait for all measured runs before reporting'
    assert len(readlines(root/'endpoint-inputs-outputs.jsonl'))==54
    write(root/'execution-completion.json',{'all_endpoint_rows_present':True,'all_54_operational_results_present':True,
        'runner_completion_marker_present':(root/'run-complete.json').exists(),
        'post_measurement_integrity_check':'Runner reported a changed reporting-only workbook helper after all measurements. No policy, reconstruction, acceptance or configuration input changed.' if manifest['_reporting_only_changes'] else 'PASS',
        'reporting_only_changes':manifest['_reporting_only_changes']})
    reference,prefs,ratings=survey(root,workbook)
    sensor=audit(root,manifest)
    endpoints=readlines(root/'endpoint-inputs-outputs.jsonl')
    compact=[]; pairs=[]
    for r in endpoints:
        assert hashlib.sha256(r['input_state_json'].encode()).hexdigest()==r['state_sha256']
        frozen=json.loads(r['input_state_json'])
        assert frozen['robot_timestamp_us']==r['source_timestamp_us']<=r['endpoint_timestamp_us']
        ref=reference['scenarios'].get(r['scenario_id'])
        if ref and r['status']=='DECIDED': r.update(score(r['action'],ref,condition=r['condition'],mapping_status=r['mapping_status']) or {})
        compact.append({k:v for k,v in r.items() if k not in ('input_state_json','output')})
    for n in range(1,10):
        sid=f'S{n}'; rr=[r for r in compact if r['scenario_id']==sid]; rule=rr[0]; llm=rr[1:]
        assert len({r['state_sha256'] for r in rr})==1
        valid=[r for r in llm if r['status']=='DECIDED']
        paired={'scenario_id':sid,'mapping_status':'ASSUMED_BY_USER','readiness':rule['readiness'],
            'rule_action':rule['action'],'llm_action_distribution':dict(Counter(r['action'] for r in valid)),
            'llm_valid_repeats':len(valid),'llm_failed_repeats':sum(r['status']=='ERROR' for r in llm),
            'llm_not_run_repeats':sum(r['status']=='NOT_RUN' for r in llm),
            'llm_ineligible_repeats':sum(r['status']=='NOT_READY' for r in llm),
            'state_hash_match':True,'human_score_status':reference['status'],
            'paired_preference_difference':None,'paired_appropriateness_difference':None}
        for metric,field in (('preference','preference_support'),('appropriateness','appropriateness')):
            scored=[r[field] for r in valid if r.get(field) is not None]
            paired['paired_'+metric+'_difference']=paired_difference(rule.get(field),scored)
            paired['rules_'+field]=rule.get(field)
            paired['llm_mean_'+field]=mean(scored) if scored else None
        pairs.append(paired)
    table(root/'endpoint_decisions.csv',compact); table(root/'paired_comparison.csv',pairs)
    operations,checks=operational(root)
    operational_summary=[]
    for repeat in range(1,4):
        for policy in ('rules','llm'):
            rows=[r for r in operations if r['repeat']==repeat and r['policy']==policy]
            operational_summary.append({'repeat':repeat,'policy':policy,'scenarios':9,
                'outputs':sum(r['action'] is not None for r in rows),'accepted':sum(r['accepted'] is True for r in rows),
                'statuses':dict(Counter(r['status'] for r in rows)),
                'rejections':dict(Counter(r['rejection_reason'] for r in rows if r['action'] and not r['accepted'])),
                **{field:summary_stats(rows,field) for field in ('output_elapsed_s','model_request_duration_s','receiver_request_duration_s','source_age_at_delivery_s')}})
    whole=[r for r in sensor if r['dataset']=='user_selected_trimmed' and r['audit_window']=='whole_recording']
    old=[r for r in sensor if r['dataset']=='original_nine' and r['audit_window']=='whole_recording']
    counts=lambda rows:{'perception_frames':sum(r['perception_frames'] for r in rows),'detected_frames':sum(r['detected_frames'] for r in rows),
        'ready_frames':sum(r['ready_frames'] for r in rows),'ready_scenarios':sum(r['ready_frames']>0 for r in rows)}
    summary={'independent_scenarios':9,'verified_endpoints':0,'assumed_endpoints':9,
        'assumed_endpoint_ready':sum(r['readiness']=='READY' for r in pairs),
        'verified_endpoint_readiness_denominator':0,'human_reference_status':reference['status'],
        'RQ2_status':'BLOCKED_WORKBOOK_MAPPING_ASSUMED' if not reference['scenarios'] else 'CONDITIONAL_ON_USER_ASSUMPTION_ONLY',
        'user_selected_trimmed':counts(whole),'original_nine_separate_audit':counts(old),
        'endpoint_rules_valid':sum(r['policy']=='rules' and r['status']=='DECIDED' for r in compact),
        'endpoint_llm_valid_calls':sum(r['policy']=='llm' and r['status']=='DECIDED' for r in compact),
        'endpoint_llm_failed_calls':sum(r['policy']=='llm' and r['status']=='ERROR' for r in compact),
        'endpoint_llm_requested_calls':sum(r['policy']=='llm' and r['duration_s'] is not None for r in compact),
        'endpoint_llm_repeat_timing':summary_stats([r for r in compact if r['policy']=='llm'],'duration_s'),
        'paired_human_scored_scenarios':sum(r['paired_preference_difference'] is not None for r in pairs),
        'paired_preference_difference_equal_scenario_mean':mean([r['paired_preference_difference'] for r in pairs if r['paired_preference_difference'] is not None]) if any(r['paired_preference_difference'] is not None for r in pairs) else None,
        'paired_appropriateness_difference_equal_scenario_mean':mean([r['paired_appropriateness_difference'] for r in pairs if r['paired_appropriateness_difference'] is not None]) if any(r['paired_appropriateness_difference'] is not None for r in pairs) else None,
        'operational':operational_summary,'operational_hash_pairs':{'matched':sum(r['first_ready_state_match'] is True for r in checks),
            'mismatched':sum(r['first_ready_state_match'] is False for r in checks),'unavailable':sum(r['first_ready_state_match'] is None for r in checks)},
        'operational_consistency':[{ 'scenario_id':f'S{n}','policy':p,
            'action_distribution':dict(Counter(r['action'] or 'NO_OUTPUT' for r in operations if r['scenario_id']==f'S{n}' and r['policy']==p))}
            for n in range(1,10) for p in ('rules','llm')]}
    write(root/'summary.json',summary)
    table(root/'alignment.csv',json.loads((root/'alignment.json').read_text()))
    graphs=root/'graph_data'; graphs.mkdir(exist_ok=True)
    table(graphs/'human_preferences.csv',prefs);table(graphs/'human_ratings.csv',ratings)
    table(graphs/'endpoint_availability.csv',pairs);table(graphs/'evidence_readiness.csv',sensor)
    table(graphs/'operational_coverage.csv',operations)
    validation={'frozen_inputs_unchanged':not bool(manifest['_reporting_only_changes']),
        'frozen_execution_inputs_unchanged':True,'reporting_only_changes':manifest['_reporting_only_changes'],
        'source_causal_replay_validated':True,
        'endpoint_rows':len(compact),'expected_endpoint_rows':54,'operational_rows':len(operations),'expected_operational_rows':54,
        'all_endpoint_hashes_paired':all(r['state_hash_match'] for r in pairs),
        'operational_first_states':summary['operational_hash_pairs'],
        'proxy_human_scores_absent':all(r['human_score'] is None for r in read_csv_json_proxy(root)),
        'operational_human_scores_absent':all(r['human_score'] is None for r in operations),
        'workbook_checks':reference.get('preparation_checks','NOT_RUN_SOURCE_MISSING'),
        'source_workbook_hash':reference.get('workbook_sha256'),
        'endpoint_causality_and_freshness':all(0<=r['state_age_s']<=.25 for r in compact),
        'endpoint_inputs_no_human_metadata':all(not any(k in json.loads(r['input_state_json']) for k in ('expected_actions','preferred_counts','ratings','scenario_description')) for r in endpoints)}
    assert validation['endpoint_rows']==54 and validation['operational_rows']==54
    assert summary['operational_hash_pairs']['mismatched']==0
    for r in sensor:
        if r['status']=='AUDITED': assert sum(r['hold_counts'].values())==r['perception_frames']
    # Re-read exported data, so verification covers serialized artifacts too.
    with (root/'operational_replay.csv').open() as f: exported_ops=list(csv.DictReader(f))
    with (root/'endpoint_decisions.csv').open() as f: exported_endpoints=list(csv.DictReader(f))
    assert len(exported_ops)==54 and len(exported_endpoints)==54
    assert len({(r['scenario_id'],r['policy'],r['repeat']) for r in exported_ops})==54
    assert len({(r['scenario_id'],r['policy'],r['repeat']) for r in exported_endpoints})==54
    for record in operational_summary:
        selected=[r for r in exported_ops if r['policy']==record['policy'] and int(r['repeat'])==record['repeat']]
        assert len(selected)==9
        assert sum(bool(r['action']) for r in selected)==record['outputs']
        assert sum(r['accepted']=='True' for r in selected)==record['accepted']
    assert all(r['human_score']=='' for r in exported_ops)
    if not reference['scenarios']:
        assert all(r['preference_support']=='' and r['appropriateness']=='' for r in exported_endpoints)
    validation['csv_to_summary_reconciled']=True
    write(root/'verification.json',validation)
    write(root/'reporting-manifest.json',{'code_sha256':{str(p):sha256(p) for p in (Path(__file__),Path('evaluation/poster_survey.py'),Path('tests/test_poster_evaluation.py'))},
        'note':'Reporting code added after execution design freeze; production and frozen runner hashes unchanged. Workbook-only helper edits are explicitly recorded in execution-completion.json.'})
    generate_text(root,summary,whole,pairs,operations,manifest,reference)
    return summary


def read_csv_json_proxy(root):
    # Human score is always blank; retain null semantics in the verifier.
    with (root/'proxy_diagnostics.csv').open() as f:
        return [{**r,'human_score':None if r['human_score']=='' else r['human_score']} for r in csv.DictReader(f)]


def generate_text(root,s,whole,pairs,ops,manifest,reference):
    c=s['user_selected_trimmed']; old=s['original_nine_separate_audit']
    lines=['# Poster evaluation results','',
      f"Candidate: {manifest['actual_rule_version']}, {manifest['actual_prompt_version']}, {manifest['settings']['model']}. The comparison JSON still declares v5/v10; effective temporal defaults and code hashes are frozen in manifest.json. No production tuning was performed.",'',
      '## Source selection and alignment','',
      'The user explicitly replaced the original nine captures with final-scenarios-audit/trimmed as assumed survey clips. Their last perception is the assumed endpoint. These are different capture sessions from scenario-01..09; motion-onset trims are verified against final_scenario originals by packet/session/timestamp equality. The original nine remain a separately labelled source audit. No camera/video file establishes exposure timing or survey-video end: **0/9 independently verified, 9/9 assumed endpoints**. Timing uncertainty is unknown, so numerical alignment sensitivity cannot be bounded.',
      '', '## RQ1 — representation and evidence availability','',
      'The adapter, UID tracker and temporal estimator produce shared state with visibility, gaze history/current gaze, distance zone and robot-relative trend, evidence validity, descriptive labels, robot motion and readiness. Canonical state bytes match across endpoint policies. Retained missing tracks are distinct from observed people. In this configuration, sustained no-person CONTINUE is disabled by its effective null default.',
      '', f"User-selected clips contain **{c['perception_frames']} perception frames**, with detections in **{c['detected_frames']}/{c['perception_frames']}** and readiness in **{c['ready_frames']}/{c['perception_frames']}** ({100*c['ready_frames']/c['perception_frames']:.1f}%, frame-weighted). At least one ready state occurs in **{c['ready_scenarios']}/9** clips. These frames and repeated calls are not independent encounters.",'',
      '| Scenario | Frames | Detected frames | Ready frames | First ready (s) | Endpoint hold |',
      '|---|---:|---:|---:|---:|---|']
    for r in whole:
        lines.append(f"| {r['scenario_id']} | {r['perception_frames']} | {r['detected_frames']} | {r['ready_frames']} | {r['first_ready_offset_s'] if r['first_ready_offset_s'] is not None else 'unavailable'} | {r['end_hold_reason'] or 'READY'} |")
    lines+=['',f"Separately, the originally requested scenario-01..09 captures contain {old['perception_frames']} frames, {old['detected_frames']} detected-person frames and {old['ready_frames']} ready frames across {old['ready_scenarios']}/9 captures. Their camera/SDK receipt-tail offsets and source audit are retained; their proxy decisions are never human-scored.",'',
      'These are availability diagnostics, not perception accuracy. UID gaps/epochs do not independently establish identity errors. Conflict and pass-gesture measurements remain UNKNOWN throughout these inputs; corridor position is not collision prediction or verified clearance. Relative separation cannot identify human motion. Missing detection does not prove an empty route.',
      '', '## RQ2 — endpoint policies and human reference','',
      f"At the assumed endpoints, **{s['assumed_endpoint_ready']}/9** states are ready; rules return **{s['endpoint_rules_valid']}/9** valid outputs. The LLM returns **{s['endpoint_llm_valid_calls']}/{s['endpoint_llm_requested_calls']}** valid requested outputs, with {s['endpoint_llm_failed_calls']} failures. Five fixed-seed calls per eligible state describe repeatability, not five encounters. See endpoint_decisions.csv for every repeat and duration.",'',
      '| Scenario | Rules | LLM valid-repeat distribution | Valid / failed / ineligible LLM repeats |',
      '|---|---|---|---|']
    for r in pairs:
        lines.append(f"| {r['scenario_id']} | {r['rule_action'] or 'unavailable'} | {json.dumps(r['llm_action_distribution'])} | {r['llm_valid_repeats']} / {r['llm_failed_repeats']} / {r['llm_ineligible_repeats']} |")
    lines+=['','The authoritative workbook is unavailable in this environment. Its claimed 42 respondents, hash and preparation counts have not been independently reproduced. The historical 37-response reference is deliberately unused. Human preference bars, appropriateness, modal agreement and paired LLM-minus-rule human differences are unavailable. Blank CSV fields mean unavailable, never zero. **Direct RQ2 comparison is blocked**; the assumed endpoint outputs cannot by themselves establish alignment or LLM superiority.',
      '', '## Operational first-output replay','',
      '| Repeat | Policy | Outputs / 9 | Accepted / 9 | Rejections |', '|---|---|---:|---:|---|']
    for r in s['operational']:
        lines.append(f"| {r['repeat']} | {r['policy']} | {r['outputs']} | {r['accepted']} | {json.dumps(r['rejections'])} |")
    timed=[r for r in ops if r['model_request_duration_s'] is not None]
    timing=summary_stats(timed,'model_request_duration_s')
    if timing['n']:
        lines+=['',f"Across {timing['n']} operational model requests from repeated scenarios, median request duration was {timing['median']:.3f} s (range {timing['min']:.3f}–{timing['max']:.3f} s). These are request timings, not independent encounter outcomes."]
    lines+=['',f"First eligible states match in {s['operational_hash_pairs']['matched']} pairs; {s['operational_hash_pairs']['unavailable']} pairs have no eligible state for both policies. No mismatched pair was observed. All 54 scenario/policy/repetition rows are present. Failed/no-output scenarios remain in every nine-scenario denominator.",'',
      'Warmup responses and elapsed times are stored separately. Inference remains asynchronous while newer observations update client readiness. Model request duration is the actual measured request time; encounter elapsed output time includes evidence accumulation. Rule receiver-request time includes pipeline processing and is not isolated rule inference. Source age at client delivery is measured; exact model-completion age is unavailable because audit UTC and reconstructed source clocks are independent. Request duration provides a lower bound. Pending/late audits, delivery errors and acceptance evidence are retained in per-run result/events/models files.',
      '', '## Limits and remaining prerequisites','',
      'This is reuse of development captures, including prior policy/prompt tuning, with a user-assumed survey mapping. The blocked sandbox attempt is preserved in sibling poster-20261011-assumed-clips-01 and excluded. This run uses local Ollama with measured timing on this host; results may vary with load. Replay reproduces saved observations, not original collection/network delays, perception correctness or physical motion.',
      '', 'Needed: the actual 42-response workbook (sheet Form responses 1); video assets plus trim/exposure-clock evidence for independent synchronisation if a verified RQ2 claim is desired; new live encounters and physical measurements for claims about comfort, safety or navigation. No robot hardware was contacted and no physical action was executed.']
    if manifest['_reporting_only_changes']:
        lines+=['','The runner completed all measurements, then exited with an integrity-check error because the workbook-only reporting helper was improved during replay. That helper was not imported or used by the execution runner. The original manifest remains unchanged; execution-completion.json records the before/after helper hashes. All recording, policy, reconstruction, configuration and execution-runner hashes still match. Reporting verification checks every completed row and paired state hash.']
    if reference['scenarios']:
        paragraph=next(x for x in lines if x.startswith('The authoritative workbook is unavailable'))
        lines[lines.index(paragraph)]=(f"The workbook contains {reference['respondents']} respondents, compared with 37 in the historical reference. Workbook totals, missingness and preparation checks are in human-reference.json; no historical scores are substituted. "
            f"Paired mean LLM-minus-rule differences are {s['paired_preference_difference_equal_scenario_mean']} in preference support and {s['paired_appropriateness_difference_equal_scenario_mean']} appropriateness points, across {s['paired_human_scored_scenarios']} scored scenarios. "
            'These descriptive scores are conditional on the user-assumed clip mapping. Repeats are averaged within each scenario before scenarios are equally weighted; failures have no fabricated score. Independent synchronisation remains unverified.')
    (root/'results.md').write_text('\n'.join(lines)+'\n')
    # Results/Conclusion word bounds are checked by tests/verification below.
    rule=[r for r in s['operational'] if r['policy']=='rules']; llm=[r for r in s['operational'] if r['policy']=='llm']
    r_counts='/'.join(str(r['accepted']) for r in rule); l_counts='/'.join(str(r['accepted']) for r in llm)
    operational_copy=(f"Across three operational repetitions, accepted decisions were {r_counts} for rules and {l_counts} for the LLM, each out of nine. ")
    if len({r['accepted'] for r in rule})==len({r['accepted'] for r in llm})==1:
        operational_copy=(f"In each of three operational repetitions, rules delivered {rule[0]['accepted']}/9 accepted decisions and the LLM delivered {llm[0]['accepted']}/9. ")
    results=(f"Across nine user-selected clips, the shared temporal representation was ready in {c['ready_frames']} of {c['perception_frames']} perception frames ({100*c['ready_frames']/c['perception_frames']:.1f}%) and at {s['assumed_endpoint_ready']} assumed clip endpoints. "
      f"Rules produced {s['endpoint_rules_valid']} endpoint decisions; the LLM produced {s['endpoint_llm_valid_calls']} valid outputs from {s['endpoint_llm_requested_calls']} repeated requests. "
      +operational_copy+
      "Survey alignment remains unverified, and the response workbook was unavailable; human preference and appropriateness comparisons therefore remain unresolved. No physical behaviour was evaluated.")
    conclusion=("The pipeline provides a shared, inspectable representation for both policies, but usable evidence and timely client acceptance constrain decision availability. "
      "This development replay cannot establish which policy better matches human preferences. Future evaluation needs the survey workbook, verified video synchronisation, improved cue coverage, timely delivery and new live encounters, including measurements of physically executed behaviour.")
    assert 70<=len(results.split())<=100, len(results.split())
    assert 45<=len(conclusion.split())<=65, len(conclusion.split())
    (root/'poster-copy.md').write_text(f'# Results ({len(results.split())} words)\n\n{results}\n\n# Conclusion ({len(conclusion.split())} words)\n\n{conclusion}\n\n# Figure captions\n\n'
      f'1. Evidence availability across nine assumed survey clips; readiness covers {c["ready_frames"]}/{c["perception_frames"]} perception frames.\n'
      '2. Operational outputs and client-accepted decisions, each out of nine scenarios in each repetition; no physical execution.\n'
      '3. Endpoint availability: nine assumed mappings, zero independently verified mappings. Human-alignment graph deferred pending the workbook.\n')
    (root/'graph-plan.md').write_text('''# Graph plan

1. Human preferences: graph_data/human_preferences.csv, nine stacked bars, action plus unable-to-judge, percent of valid preference responses including unable-to-judge. Deferred: all measurements blank until workbook verification. Never draw blank values as zero or use historical counts.
2. Endpoint policies: endpoint_availability.csv gives readiness and per-repeat action distribution. Show missing cases explicitly. Paired selected-action preference (%) and appropriateness (1–5) graphs are deferred until workbook supplied; any future scores remain conditional on the user assumption unless alignment is independently verified. No survey scores for first actions or proxies.
3. Evidence readiness: evidence_readiness.csv, select dataset=user_selected_trimmed and audit_window=whole_recording; ready/total perception frames per scenario. A second panel may use assumed_endpoint_prefix; the assumed endpoint is SDK end so prefixes coincide with whole clips. Original_nine is a separate panel, never pooled. These measure availability, not accuracy.
4. Operational coverage: operational_coverage.csv, scenario × policy × repeat cells showing no output / rejected output / accepted decision. Denominator nine for each policy/repeat; retain no-output cases. Optional dots: output_elapsed_s (seconds since first perception), model_request_duration_s (seconds per request) in separate panels. Blank timings remain unavailable, not zero. Repeated runs are not independent scenarios.
''')
    (root/'verification.md').write_text('''# Verification

See verification.json for machine checks and tests.log for targeted test output. Source recordings, configuration, production code and execution runner retain their frozen hashes. The workbook-only reporting helper changed during replay without participating in execution; the runner caught this after all measurements and exited with an integrity error. execution-completion.json explicitly records this exception; the original manifest is not rewritten. reporting-manifest.json identifies final reporting code. All 54 endpoint rows (one rules plus five LLM slots per scenario) and 54 operational rows (nine × two × three) are accounted for. Hash equality checks identical canonical endpoint state bytes and first eligible production states. Operational events match exact causal perception prefixes and reported ingestion counts. No future observations are selected; exact endpoint age is zero under the SDK-end assumption. Whole-frame readiness holds reconcile to frame counts. Exported CSVs are re-read and reconcile to summaries. Proxy/operational human-score columns are blank. Missing workbook totals/denominators are NOT_VERIFIED, never inferred from the prompt.

The last retained perception is a user-assumed video endpoint, not verified video timing. No endpoint uncertainty interval is known. No verified-endpoint human estimate exists. Rules request timing includes the production receiver; isolated rule inference and exact model-completion source age were not separately measured.

Reproduce from repository root, choosing a new directory, with the configured local Ollama model available:

```sh
.venv/bin/python -m unittest tests.test_poster_evaluation tests.test_first_action_replay -v
.venv/bin/python -m evaluation.poster_run --output var/evaluation/poster-NEW --prepare
.venv/bin/python -m evaluation.poster_run --output var/evaluation/poster-NEW --run
.venv/bin/python -m evaluation.poster_report --output var/evaluation/poster-NEW
# If available, append: --workbook '/absolute/path/to/responses.xlsx'
```

Python requires localhost network permission for Ollama. Preserve missing-source attempts; do not replace their recorded status. No model substitution or threshold changes are made.
''')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--workbook',type=Path)
    a=p.parse_args(); print(json.dumps(report(a.output,a.workbook),indent=2))
if __name__=='__main__': main()
