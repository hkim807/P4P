"""Summarise stages 7/8 without replacing first scores with later decisions."""
import argparse
from pathlib import Path
import statistics

from scripts.evaluate_inference_variants import read
from scripts.profile_llm_baseline import write_json


def actions(rows, scenario):
    return ' / '.join((r['first_decision'] or {}).get('action','INVALID')
        for r in sorted(rows,key=lambda r:r['repetition']) if r['scenario_id']==scenario)


def report(root):
    protocol=read(root/'common/protocol.json')
    seven=read(root/'stage7/comparison.json')
    eight=read(root/'stage8/comparison.json')
    labels={'qwen25-7b':'Qwen 2.5 7B','qwen25-3b':'Qwen 2.5 3B',
            'qwen3-4b-instruct':'Qwen 3 4B Instruct','gemma3-4b':'Gemma 3 4B'}
    lines=['# Stages 7 and 8: inference speed and alternative models','',
        '**Recommendation: retain Qwen 2.5 7B, context 8192 and output ceiling 192.** None of the tested changes meets the predeclared quality/speed criteria. Smaller context gives a small timing change; smaller/newer models produce more first-action errors.','',
        'DecisionState v3, v16 instructions and all tuned rules remain fixed. The original first eligible decision is scored in three operational repetitions per arm; the nine later observations are diagnostic only. Both policies receive identical state, and all 574 frames retain original rule decisions/readiness (402 eligible). No physical robot actions are executed.','',
        '## Stage 7: inference settings','',
        'The setting screens are direct first-snapshot calls, not operational scores. Fresh controls are measured, full messages and prompt token counts are preserved, and at least 256 tokens of context headroom are required. Operational arm order alternates by repetition. A setting must preserve every scenario’s alignment and valid untruncated outputs, improve median time by at least 10%, and avoid a worse 95th percentile.','',
        '| Arm | Alignment | Valid | Median complete output | 95th percentile | Accepted |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for name,value in seven['results'].items():
        lines.append(f"| {name} | {value['aligned']}/27 | {value['valid']}/27 | {value['median_s']:.2f}s | {value['p95_s']:.2f}s | {value['accepted']}/27 |")
    lines += ['',f"Selected inference setting: **{seven['selected']}**. These figures include receiver scheduling and output observation, not just native model generation.",'',
        'Context 2048 lowers the operational median by 1.7%, while the 95th percentile is 1.2% slower. Ollama reports loaded memory of approximately 4.62 GB versus 4.98 GB at context 8192, both entirely on GPU. This is a memory saving, not evidence of a useful speed improvement under the selection criterion.','',
        '### Direct settings screens — not operational scores','',
        '| Setting | Valid first-snapshot responses | Median direct client time |',
        '| --- | ---: | ---: |']
    screens=read(root/'stage7/screen-summary.json')
    for name,items in screens.items():
        lines.append(f"| {name} | {sum(r['valid'] for r in items)}/9 | {statistics.median(r['client_duration_s'] for r in items):.2f}s |")
    lines += ['',
        'Every screen retains the same eight aligned actions and the same prompt-token counts. Context 2048 has the fastest screen median and is the candidate chosen for operational confirmation. Reducing the output ceiling gives no useful benefit: the control already ends its answers well below 64 tokens. The combined change is also not faster than context reduction alone in this screen.','',
        '## Stage 8: model comparison','',
        f"All measured models use **{eight['settings_variant']}** settings. Model order rotates between repetitions, with one model loaded at a time and its weights warmed before each block. The Qwen 2.5 7B control is measured freshly again. The same application messages, structured-output schema, seed and temperature are used; model-specific templates, weights, tokenizers and quantization are recorded.",'',
        '| Model | First alignment by repetition | Valid | Median complete output | 95th percentile | Accepted | Aligned and accepted |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for name,value in eight['results'].items():
        counts=' / '.join(f'{n}/9' for n in value['per_repeat_aligned'])
        lines.append(f"| {name} | {counts} | {value['valid']}/27 | {value['median_s']:.2f}s | {value['p95_s']:.2f}s | {value['accepted']}/27 | {value['aligned_and_accepted']}/27 |")
    excluded=read(root/'stage8/excluded-models.json')
    lines += ['',f"Selected model under the predeclared quality/speed criterion: **{eight['selected']}**."]
    for name,value in excluded.items():
        lines += ['',f"**{labels[name]} unavailable:** {value['reason']} No score is assigned."]
    lines += ['',
        '## First actions','',
        '| Scenario | Allowed actions | '+' | '.join(labels[name] for name in eight['results'])+' |',
        '| --- | --- | '+' | '.join('---' for name in eight['results'])+' |']
    rows={name:read(root/f'stage8/{name}/measurements/operational-summary.json') for name in eight['results']}
    for scene in protocol['scenarios']:
        lines.append('| '+' | '.join([scene['scenario_id'],', '.join(scene['expected_actions'])]+
            [actions(rows[name],scene['scenario_id']) for name in rows])+' |')
    lines += ['', '## Timing components','',
        'Medians below are native telemetry for the first requests. Components have independent medians and should not be added to reconstruct a median total. Prompt cache counts and token counts are retained per call.','',
        '| Model | Prompt evaluation | Generation | Model load | Prompt tokens | Generated tokens |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for name,items in rows.items():
        med=lambda key: statistics.median(r['timing'][key] for r in items if r['timing'] and r['timing'][key] is not None)
        lines.append(f"| {name} | {med('prompt_eval_duration_s'):.2f}s | {med('eval_duration_s'):.2f}s | {med('load_duration_s'):.3f}s | {med('prompt_tokens'):g} | {med('output_tokens'):g} |")
    lines += ['', '## First explanations — distinct answers','']
    for name,items in rows.items():
        lines += [f'### {name}','']
        for i in range(1,10):
            for action,reason in sorted(set((r['first_decision']['action'],r['first_decision']['reason'])
                    for r in items if r['scenario_id']==f'S{i}' and r['first_decision'])):
                lines.append(f'- S{i}: **{action}** — {reason}')
    lines += ['', '## Later observations — excluded from primary score','',
        '| Scenario | '+' | '.join(labels[name] for name in rows)+' |',
        '| --- | '+' | '.join('---' for name in rows)+' |']
    later={name:read(root/f'stage8/{name}/measurements/later-summary.json') for name in rows}
    for index in range(9):
        lines.append('| '+' | '.join([f'S{index+1}']+
            [(items[index]['llm'].get('decision') or {}).get('action','INVALID') for items in later.values()])+' |')
    lines += ['', '## Controls and limits','',
        'There are 252 new decision calls: 36 direct screens, 54 stage 7 first trials plus 18 later probes, and 108 stage 8 first trials plus 36 later probes. Warmup requests are separate and never scored. All 252 decisions pass the existing strict parser; schema validity does not establish action alignment or faithful explanations.','',
        'Every actual request is checked against frozen messages, settings, structured-output schema and transport hashes. First production rule/LLM state hashes match. Model digests and Ollama version are checked before every block; other models are unloaded. Production source hashes and historical artifact seals are rechecked. Invalid first outputs remain failures even if a later retry succeeds.','',
        'The initial Gemma download failed at a storage-host DNS lookup; its retry succeeded. Download activity overlapped the screens and early stage 7 blocks. Later stage 7 blocks and the entire stage 8 comparison ran after completion. The already small setting-time difference should not be treated as a causal speed gain.','',
        'The common protocol retains the original freeze’s unused settings.prompt_version metadata. Actual messages, top-level prompt_version and model audits use decision-state-llm-v16; every request is verified against the selected stage 5 instructions. The recommended settings record uses the current v16 metadata.','',
        'Trials run on an Apple M3 with 16 GB memory. This is a small local comparison with rotation/alternation at block level, not a randomized independent statistical experiment. Deterministic repeats and these nine tuning recordings do not establish held-out accuracy. Different model templates and tokenizers are part of the model comparison.','',
        'Acceptance is separate from action alignment. A finite recording can end before an otherwise valid action is delivered, or current readiness can change. These rates do not measure physical robot execution. S8 retains its broad allowed-action set and its first decision occurs in the empty scene. Later observations cannot repair a first-action mistake.','',
        'Warm measurements assume the model has already been loaded. Preload before the encounter using the same context setting, and keep only the intended model resident. The experiment does not alter Ollama app settings or run models in the cloud.','',
        f'- [Setting screens]({root.resolve()}/stage7/screen-summary.json)',
        f'- [Stage 7 comparison]({root.resolve()}/stage7/comparison.json)',
        f'- [Stage 8 comparison]({root.resolve()}/stage8/comparison.json)',
        f'- [Frozen protocol]({root.resolve()}/common/protocol.json)']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n')
    write_json(root/'comparison.json',{'stage7':seven,'stage8':eight,'physical_execution':False})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    report(parser.parse_args().output)
