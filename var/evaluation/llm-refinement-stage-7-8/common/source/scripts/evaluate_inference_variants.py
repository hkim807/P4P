"""Stages 7/8: fixed evidence and prompt, real inference settings/model trials.

Run prepare once, screen settings, then run operational arms. Screens never
replace the first-eligible production replay score. Existing freezes are read-only.
"""
import argparse
import json
from pathlib import Path
import shutil
import statistics
from unittest.mock import patch

from app.domain.model_decision import model_decision_schema
from app.policy.llm import PROMPT_VERSION, classify_llm
from app.state import decision_models
from app.state.social_models import SocialState
from scripts import evaluate_compact_state as paired
from scripts.evaluate_semantic_state import verify_stage3
from scripts.profile_llm_baseline import (ProfiledClient, ReplayClock, operational_row,
    profile_config, sha, timing_breakdown, verify_freeze, write_json)
from scripts.replay_first_action import replay, request_json


SETTING_VARIANTS = {
    'control': {},
    'context-2048': {'num_ctx': 2048},
    'output-64': {'num_predict': 64},
    'context-2048-output-64': {'num_ctx': 2048, 'num_predict': 64},
}
MODEL_VARIANTS = {
    'qwen25-7b': 'qwen2.5:7b',
    'qwen25-3b': 'qwen2.5:3b',
    'qwen3-4b-instruct': 'qwen3:4b-instruct',
    'gemma3-4b': 'gemma3:4b',
}


def read(path):
    return json.loads(Path(path).read_text())


def check_source(root):
    for name, digest in read(root/'common/manifest.json')['implementation_sha256'].items():
        if sha(Path(name).read_bytes()) != digest:
            raise ValueError(f'Implementation changed: {name}')
    if decision_models.TEMPORAL_SUMMARY_MODE != 'NONE':
        raise ValueError('Temporal summaries must remain disabled')
    verify_stage3(Path('var/evaluation/llm-refinement-stage-5'))
    verify_stage3(Path('var/evaluation/llm-refinement-stage-6'))
    verify_freeze(Path('var/evaluation/llm-refinement-stage-1-2'), worktree=False)


def prepare(root):
    root.mkdir(parents=True, exist_ok=False)
    common = root/'common'
    common.mkdir()
    protocol = paired.prepare(Path('var/evaluation/llm-refinement-stage-1-2'), common,
                              require_original_instructions=False)
    previous = Path('var/evaluation/llm-refinement-stage-5/refined')
    for file in (common/'snapshots').rglob('*.json'):
        if read(file) != read(previous/file.relative_to(common)):
            raise ValueError(f'Stage 5 input changed: {file}')
    manifest = read(common/'manifest.json')
    for name in ['scripts/evaluate_inference_variants.py', 'tests/test_inference_variants.py']:
        target = common/'source'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(name, target)
        manifest['implementation_sha256'][name] = sha(Path(name).read_bytes())
    write_json(common/'manifest.json', manifest)
    protocol.update(version='fixed-evidence-inference-variants-v1', prompt_version=PROMPT_VERSION,
        settings_variants=SETTING_VARIANTS, model_variants=MODEL_VARIANTS,
        screen='One direct first-snapshot call per scenario per settings arm, not an operational score.',
        settings_selection='No per-scenario alignment loss or invalid/truncated first output; at least 10% lower operational median and no worse p95; otherwise retain control.',
        model_selection='Maximise first-action alignment without per-scenario losses or invalid outputs; break equal-quality ties by at least 10% faster median and no worse p95; otherwise retain Qwen 2.5 7B.',
        context_safety='Minimum candidate context 2048; retain full-token counts and at least 256 tokens of headroom for every frozen input. No prompt/state truncation is allowed.',
        primary='First eligible production request only, including failures. Later/retry success cannot improve this score.',
        timing_design='Fresh controls. Stage 7 alternates arm order by repetition. Stage 8 rotates model order by repetition; unload other models before each block. Warm weights, no priming with later snapshots.',
        device={'chip':'Apple M3', 'memory_gb':16, 'cpu_cores':8})
    write_json(common/'protocol.json', protocol)
    runtime = {'version':request_json(protocol['settings']['base_url'], '/api/version'),
               'tags':request_json(protocol['settings']['base_url'], '/api/tags')}
    historical = read(Path('var/evaluation/llm-refinement-stage-1-2/freeze/runtime.json'))
    baseline_model = protocol['settings']['model']
    old_digest = next(m['digest'] for m in historical['ollama_tags']['models'] if m['name']==baseline_model)
    current_digest = next(m['digest'] for m in runtime['tags']['models'] if m['name']==baseline_model)
    if runtime['version'] != historical['ollama_version'] or current_digest != old_digest:
        raise ValueError('Original control model/runtime changed')
    write_json(root/'locked-runtime.json', runtime)
    for model in MODEL_VARIANTS.values():
        if not any(m['name'] == model for m in runtime['tags']['models']):
            continue
        write_json(root/'models'/f'{model.replace(":", "-")}.json',
                   request_json(protocol['settings']['base_url'], '/api/show', {'model':model}))
    check_source(root)


def validate_request(request, settings, messages):
    expected = {'model':settings['model'], 'messages':messages, 'stream':False,
                'format':model_decision_schema(), 'options':profile_config(settings).generation_options()}
    if request != expected:
        raise ValueError('Actual request differs from frozen messages/settings/schema')


def verify_calls(folder, settings, expected_messages=None):
    calls = read(folder/'calls.json')
    for i, call in enumerate(calls, 1):
        path = folder/f'call-{i:03d}.request.json'
        request = read(path)
        messages = expected_messages if i == 1 and expected_messages is not None else request['messages']
        validate_request(request, settings, messages)
        body = json.dumps(request, allow_nan=False, separators=(',', ':')).encode()
        if sha(body) != call['request_sha256']:
            raise ValueError('Request bytes do not match actual transport')
        path.with_name(f'call-{i:03d}.request.raw.json').write_bytes(body)
    return calls


def activate(root, settings, folder):
    stage8_lock = root/'stage8/locked-runtime.json'
    locked = read(stage8_lock if 'stage8' in folder.relative_to(root).parts and stage8_lock.exists() else root/'locked-runtime.json')
    base = settings['base_url']
    if request_json(base, '/api/version') != locked['version']:
        raise ValueError('Ollama version changed')
    tags = request_json(base, '/api/tags')
    digest = lambda value: next(m['digest'] for m in value['models'] if m['name'] == settings['model'])
    if digest(tags) != digest(locked['tags']):
        raise ValueError('Locked model digest changed')
    for model in request_json(base, '/api/ps')['models']:
        request_json(base, '/api/generate', {'model':model['name'], 'stream':False, 'keep_alive':0})
    warm = request_json(base, '/api/generate', {'model':settings['model'], 'prompt':'',
        'stream':False, 'keep_alive':'5m', 'options':{'num_ctx':settings['num_ctx']}})
    write_json(folder/'warmup.json', warm)
    loaded = request_json(base, '/api/ps')
    write_json(folder/'loaded-model.json', loaded)
    if len(loaded['models']) != 1 or loaded['models'][0]['name'] != settings['model']:
        raise ValueError('Exactly one selected model must be loaded')


def screen(root):
    check_source(root)
    protocol = read(root/'common/protocol.json')
    all_rows = {}
    for name, changes in SETTING_VARIANTS.items():
        settings = {**protocol['settings'], **changes}
        folder = root/'stage7/screen'/name
        if folder.exists():
            raise ValueError('Screen output already exists')
        activate(root, settings, folder)
        rows = []
        for scene in protocol['scenarios']:
            state = SocialState.model_validate_json((root/f'common/snapshots/{scene["scenario_id"]}/first.social-state.json').read_text())
            client = ProfiledClient(profile_config(settings), folder/scene['scenario_id'], ReplayClock())
            result = classify_llm(state, client)
            messages = read(root/f'common/snapshots/{scene["scenario_id"]}/first.messages.json')
            call = verify_calls(client.output, settings, messages)[0]
            if call['state_sha256'] != scene['snapshots']['first']['state_sha256']:
                raise ValueError('Screen evidence differs')
            response = read(client.output/'call-001.response.json') if (client.output/'call-001.response.json').exists() else {}
            rows.append({'scenario_id':scene['scenario_id'], 'decision':call['decision'],
                'valid':call['ok'], 'aligned':bool(call['decision'] and call['decision']['action'] in scene['expected_actions']),
                'client_duration_s':call['client_call_duration_s'], 'timing':timing_breakdown(call),
                'done_reason':response.get('done_reason'), 'primary_score':False})
            write_json(folder/'rows.json', rows)
            print('Screen',name,scene['scenario_id'],call['decision'],round(call['client_call_duration_s'],3),flush=True)
        all_rows[name] = rows
    write_json(root/'stage7/screen-summary.json', all_rows)
    control = all_rows['control']
    for name, rows in all_rows.items():
        for old, row in zip(control, rows):
            if row['timing']['prompt_tokens'] != old['timing']['prompt_tokens']:
                raise ValueError(f'Prompt token count changed under settings: {name}')
            check_capacity(row['timing']['prompt_tokens'],
                           {**protocol['settings'], **SETTING_VARIANTS[name]})
    check_source(root)


def make_arm(root, stage, name, settings):
    output = root/stage/name
    output.mkdir(parents=True, exist_ok=False)
    for file in ('rule-parity.json','payload-sizes.json'):
        shutil.copyfile(root/'common'/file, output/file)
    protocol = read(root/'common/protocol.json')
    protocol.update(arm=name, settings=settings)
    write_json(output/'protocol.json', protocol)
    return output


def run_block(root, output, settings, repetition):
    protocol = read(root/'common/protocol.json')
    block = output/f'measurements/repeat-{repetition}'
    activate(root, settings, block)
    rows = read(output/'measurements/operational-summary.json') if (output/'measurements/operational-summary.json').exists() else []
    for scene in protocol['scenarios']:
        print('Operational',output.name,repetition,scene['scenario_id'],flush=True)
        folder = block/scene['scenario_id']
        clock = ReplayClock()
        clients = []
        def factory(config):
            if config != profile_config(settings):
                raise ValueError('Production configuration differs from arm')
            client = ProfiledClient(config, folder/f'profile-{len(clients)+1}', clock)
            clients.append(client)
            return client
        with patch('app.inference.live.OllamaClient', factory):
            result = replay(scene, 'llm', folder/'live', settings, now=clock.now)
        calls = paired.link_profiles(scene, folder, [c for client in clients for c in client.calls])
        if not calls:
            raise ValueError('No eligible first request')
        messages = read(root/f'common/snapshots/{scene["scenario_id"]}/first.messages.json')
        for index, client in enumerate(clients):
            verify_calls(client.output, settings, messages if index == 0 else None)
        row = operational_row(scene, result, calls, folder/'live')
        offset = (scene['snapshots']['first']['timestamp_us']-scene['first_perception_us'])/1e6
        row.update(repetition=repetition, eligible_to_first_completion_s=calls[0]['chat_end_elapsed_s']-offset)
        first_response = clients[0].output/'call-001.response.json'
        row['first_done_reason'] = read(first_response).get('done_reason') if first_response.exists() else None
        row['prompt_tokens'] = calls[0]['ollama_metrics']['prompt_eval_count']
        row['output_tokens'] = calls[0]['ollama_metrics']['eval_count']
        check_capacity(row['prompt_tokens'],settings)
        rows.append(row)
        write_json(folder/'operational.json', row)
        write_json(output/'measurements/operational-summary.json', rows)
        print(json.dumps({k:row[k] for k in ('scenario_id','first_decision','action_aligned','eligible_to_first_completion_s','accepted_by_live_client')}),flush=True)


def later(root, output, settings):
    activate(root, settings, output/'measurements/later')
    rows = []
    for scene in read(root/'common/protocol.json')['scenarios']:
        folder = root/f'common/snapshots/{scene["scenario_id"]}'
        state = SocialState.model_validate_json((folder/'later.social-state.json').read_text())
        client = ProfiledClient(profile_config(settings), output/f'measurements/later/{scene["scenario_id"]}', ReplayClock())
        result = classify_llm(state, client)
        call = verify_calls(client.output, settings, read(folder/'later.messages.json'))[0]
        if call['state_sha256'] != scene['snapshots']['later']['state_sha256']:
            raise ValueError('Later evidence differs')
        check_capacity(call['ollama_metrics']['prompt_eval_count'],settings)
        rows.append({'scenario_id':scene['scenario_id'], 'snapshot':scene['snapshots']['later'],
            'llm':result, 'timing':timing_breakdown(call), 'primary_score':False})
        write_json(output/'measurements/later-summary.json', rows)
    primary = read(output/'measurements/operational-summary.json')
    if len(primary) != 27 or {(r['repetition'],r['scenario_id']) for r in primary} != {(n,f'S{i}') for n in range(1,4) for i in range(1,10)}:
        raise ValueError('Missing/duplicate primary trials')
    write_json(output/'verification.json', {'primary_trials':27, 'later_probes':9,
        'first_messages_settings_schema_and_transport_bytes_verified':True,
        'rule_llm_shared_input_verified':True, 'unchanged_source':True})


def percentile(values, p):
    values = sorted(values)
    position = (len(values)-1)*p
    low = int(position)
    return values[low]+(values[min(low+1,len(values)-1)]-values[low])*(position-low)


def check_capacity(prompt_tokens, settings):
    if prompt_tokens is None or prompt_tokens+max(256,settings['num_predict']) > settings['num_ctx']:
        raise ValueError('Insufficient context headroom; input must not be truncated')


def summary(rows):
    # Complete first output observed by the pipeline is primary. A failed first
    # output retains its own completion time, never the time of a later retry.
    times = [(r['timing'] or {}).get('eligible_to_observed_output_s')
             if r['first_output_valid'] else None for r in rows]
    times = [value if value is not None else r['eligible_to_first_completion_s'] for value,r in zip(times,rows)]
    return {'trials':len(rows), 'aligned':sum(r['action_aligned'] for r in rows),
        'valid':sum(r['first_output_valid'] for r in rows),
        'accepted':sum(r['accepted_by_live_client'] is True for r in rows),
        'aligned_and_accepted':sum(r['action_aligned'] and r['accepted_by_live_client'] is True for r in rows),
        'median_s':statistics.median(times), 'p95_s':percentile(times,.95),
        'median_first_client_completion_s':statistics.median(r['eligible_to_first_completion_s'] for r in rows),
        'truncated':sum(r['first_done_reason']=='length' for r in rows),
        'per_scenario_aligned':{f'S{i}':sum(r['action_aligned'] for r in rows if r['scenario_id']==f'S{i}') for i in range(1,10)},
        'per_repeat_aligned':[sum(r['action_aligned'] for r in rows if r['repetition']==n) for n in range(1,4)]}


def no_quality_loss(before, after):
    return (after['valid'] == after['trials'] and after['truncated'] == 0 and
        after['aligned'] >= before['aligned'] and
        all(after['per_scenario_aligned'][s] >= value for s,value in before['per_scenario_aligned'].items()))


def select_settings(results):
    control = results['control']
    candidates = [(v['median_s'],name) for name,v in results.items() if name != 'control' and
        no_quality_loss(control,v) and v['median_s'] <= control['median_s']*.9 and v['p95_s'] <= control['p95_s']]
    return min(candidates)[1] if candidates else 'control'


def run(root, stage, settings_name):
    check_source(root)
    base = read(root/'common/protocol.json')['settings']
    if stage == 'stage7':
        arms = {name:{**base, **SETTING_VARIANTS[name]} for name in ['control',settings_name]}
    else:
        locked = read(root/'locked-runtime.json')
        current = {'version':request_json(base['base_url'],'/api/version'),
                   'tags':request_json(base['base_url'],'/api/tags')}
        if current['version'] != locked['version']:
            raise ValueError('Runtime changed before model comparison')
        for old in locked['tags']['models']:
            now = next(m for m in current['tags']['models'] if m['name']==old['name'])
            if now['digest'] != old['digest']:
                raise ValueError('Previously locked model changed')
        write_json(root/'stage8/locked-runtime.json',current)
        available = {m['name'] for m in current['tags']['models']}
        excluded = {name:{'model':model,'reason':'Not installed; download/storage-host lookup failed before comparison.'}
                    for name,model in MODEL_VARIANTS.items() if model not in available}
        write_json(root/'stage8/excluded-models.json',excluded)
        arms = {name:{**base, **SETTING_VARIANTS[settings_name], 'model':model}
                for name,model in MODEL_VARIANTS.items() if model in available}
        for settings in arms.values():
            write_json(root/'models'/f'{settings["model"].replace(":", "-")}.json',
                       request_json(base['base_url'],'/api/show',{'model':settings['model']}))
    outputs = {name:make_arm(root,stage,name,settings) for name,settings in arms.items()}
    order = list(arms)
    schedule = []
    for repetition in range(1,4):
        current = (order if repetition % 2 else order[::-1]) if stage == 'stage7' else order[repetition-1:]+order[:repetition-1]
        schedule.append({'repetition':repetition,'order':current})
    write_json(root/stage/'schedule.json', schedule)
    for block in schedule:
        for name in block['order']:
            run_block(root, outputs[name], arms[name], block['repetition'])
            check_source(root)
    for name in order:
        later(root, outputs[name], arms[name])
    results = {name:summary(read(output/'measurements/operational-summary.json')) for name,output in outputs.items()}
    selected = select_settings(results) if stage == 'stage7' else 'qwen25-7b'
    if stage == 'stage8':
        control = results['qwen25-7b']
        eligible = [( -v['aligned'],v['median_s'],name) for name,v in results.items() if name != 'qwen25-7b' and no_quality_loss(control,v) and
            (v['aligned'] > control['aligned'] or (v['median_s'] <= control['median_s']*.9 and v['p95_s'] <= control['p95_s']))]
        if eligible:
            selected = min(eligible)[2]
    write_json(root/stage/'comparison.json', {'results':results,'selected':selected,
        'settings_variant':settings_name,'first_score_only':True})
    check_source(root)
    print(json.dumps({'stage':stage,'results':results,'selected':selected},indent=2),flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','screen','stage7','stage8'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--settings',choices=list(SETTING_VARIANTS),default='context-2048-output-64')
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        prepare(args.output)
    elif args.command == 'screen':
        screen(args.output)
    else:
        run(args.output,args.command,args.settings)


if __name__ == '__main__':
    main()
