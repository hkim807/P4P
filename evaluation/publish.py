"""Publish compact auditable results and update the generated baseline table."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from evaluation.summarize import summarize


def publish(source, destination, report=None):
    source,destination=Path(source),Path(destination)
    summarize(source)
    destination.mkdir(parents=True,exist_ok=True)
    for name in ('manifest.json','summary.json','summary.csv','results.md','sensor-audit.json','decisions.jsonl','transitions.jsonl'):
        shutil.copyfile(source/name,destination/name)
    decisions=[json.loads(l) for l in (source/'decisions.jsonl').read_text().splitlines()]
    selected={r['state_sha256'] for r in decisions}
    states=[json.loads(l) for l in (source/'states.jsonl').read_text().splitlines()]
    with (destination/'selected-inputs.jsonl').open('w') as stream:
        for row in states:
            if row['state_sha256'] in selected:
                stream.write(json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n')
    metadata={'source_run':str(source),'files_sha256':{name:hashlib.sha256((source/name).read_bytes()).hexdigest()
              for name in ('states.jsonl','decisions.jsonl','manifest.json')},
              'report_generator_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in (Path(__file__),Path('evaluation/summarize.py'),Path('evaluation/audit_recordings.py'))},
              'final_source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                                     for folder in ('app','evaluation','robot/navel_client')
                                     for p in sorted(Path(folder).rglob('*.py'))}}
    (destination/'publication.json').write_text(json.dumps(metadata,indent=2)+'\n')
    if report:
        report=Path(report); text=report.read_text(); start='<!-- measured-results:start -->'; end='<!-- measured-results:end -->'
        summary=json.loads((source/'summary.json').read_text()); manifest=json.loads((source/'manifest.json').read_text())
        attempts=sum(s['llm']['attempts'] for s in summary.values()); valid=sum(s['llm']['valid'] for s in summary.values())
        pairs=sum(s['agreement_pairs'] for s in summary.values()); agreements=sum((s['agreement'] or 0)*s['agreement_pairs'] for s in summary.values())
        latency=sum(s['llm']['attempts']*(s['llm']['mean_latency_s'] or 0) for s in summary.values())
        observed=[r for r in decisions if r['readiness']=='READY' and any(
            p['visibility']=='OBSERVED' for p in next(s for s in states if s['state_id']==r['state_id'])['social_state']['people'])]
        person_pairs=[v['action']==r['rule']['action'] for r in observed for v in r['llm'] if v['status']=='DECIDED']
        pct=lambda n,d:f'{n/d:.1%}' if d else 'unavailable'
        block=f"\nMeasured run: `{source}`, prompt `{manifest['prompt_version']}`. {len(states)} frames; {len(decisions)} sampled states; {attempts} model calls, {valid} valid structured outputs. Matched action agreement {pct(agreements,pairs)} ({agreements:g}/{pairs}); observed-person agreement {pct(sum(person_pairs),len(person_pairs))} ({sum(person_pairs)}/{len(person_pairs)}). Mean model request latency {latency/attempts:.3f} s.\n\n" if attempts else '\nRule-only run.\n\n'
        table=(source/'results.md').read_text(); block+=table[table.index('| Scenario'):]
        block+='\n| Scenario | Rule first decision / interaction delay (s) | LLM first decision / interaction delay (s) | Last valid rule / LLM actions |\n|---|---|---|---|\n'
        fmt=lambda v:'—' if v is None else f'{v:.3f}'
        for name,s in summary.items():
            r,l=s['rule'],s['llm']; block+=f"| {name} | {fmt(r['first_decision_delay_s'])} / {fmt(r['first_interaction_delay_s'])} | {fmt(l['first_decision_delay_s'])} / {fmt(l['first_interaction_delay_s'])} | {r['last_valid_action']} / {l['last_valid_action_per_repeat']} |\n"
        if start not in text:
            text=text.replace('## J. Research interpretation',start+'\n'+end+'\n\n## J. Research interpretation')
        a,b=text.index(start)+len(start),text.index(end)
        report.write_text(text[:a]+block+text[b:])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path); p.add_argument('--destination',type=Path,default=Path('docs/results/policy-baseline'))
    p.add_argument('--report',type=Path,default=Path('docs/policy_comparison_baseline.md'))
    a=p.parse_args(); publish(a.source,a.destination,a.report)
