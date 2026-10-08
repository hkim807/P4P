"""Regenerate compact JSON, CSV and Markdown from saved evaluation evidence."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from statistics import mean


def fraction(n, d):
    return n/d if d else None


def summarize(directory):
    directory = Path(directory)
    states = [json.loads(l) for l in (directory/'states.jsonl').read_text().splitlines()]
    decisions = [json.loads(l) for l in (directory/'decisions.jsonl').read_text().splitlines()]
    manifest = json.loads((directory/'manifest.json').read_text())
    audits = json.loads((directory/'sensor-audit.json').read_text())
    summaries = {}
    transitions = []
    for scenario in sorted({r['scenario_id'] for r in states}):
        rows = [r for r in states if r['scenario_id'] == scenario]
        selected = [r for r in decisions if r['scenario_id'] == scenario]
        calls = [v for r in selected for v in r['llm'] if v['status'] != 'NOT_READY']
        valid = [v for v in calls if v['status'] == 'DECIDED']
        matches = [v['action'] == r['rule']['action'] for r in selected for v in r['llm']
                   if v['status'] == 'DECIDED' and r['rule']['action'] is not None]
        detected_matches = [v['action'] == r['rule']['action'] for r in selected for v in r['llm']
                            if v['status'] == 'DECIDED' and r['rule']['action'] is not None
                            and next(s for s in rows if s['state_id'] == r['state_id'])['social_state']['people']]
        repeat_consistency = []
        for r in selected:
            actions = [v['action'] for v in r['llm'] if v['status'] == 'DECIDED']
            if len(actions) >= 2:
                repeat_consistency.append(max(Counter(actions).values()) / len(actions))
        first = next((r['timestamp_us'] for r in rows if any(
            p['visibility'] == 'OBSERVED' and p['evidence']['latest_distance_valid']
            for p in r['social_state']['people'])), None)
        def delay(policy, interaction=False):
            if first is None:
                return None
            candidates = rows if policy == 'rule' else selected
            for r in candidates:
                values = [r['rule']] if policy == 'rule' else r['llm']
                if r['timestamp_us'] >= first and any(v.get('action') is not None and
                        (not interaction or v['action'] in ('APPROACH','ENGAGE')) for v in values):
                    return (r['timestamp_us']-first)/1e6
            return None
        last = [r['rule']['action'] for r in rows if r['rule']['action'] is not None]
        last_per_repeat = {}
        for r in selected:
            for v in r['llm']:
                if v['action'] is not None:
                    last_per_repeat[str(v['repeat'])] = v['action']
        rule_counts = dict(Counter(r['rule']['action'] or 'NOT_READY' for r in rows))
        llm_counts = dict(Counter(v['action'] for v in valid))
        person_states = [p for r in rows for p in r['social_state']['people'] if p['visibility'] == 'OBSERVED']
        errors = dict(Counter(v['error']['category'] for v in calls if v.get('error')))
        human = (manifest.get('human_reference') or {}).get(scenario)
        human_scores = None
        if human:
            def score(dist):
                n = sum(dist.values())
                return {kind: sum(count*human[kind].get(action,0) for action,count in dist.items())/n if n else None
                        for kind in ('preferred','least_appropriate')}
            human_scores = {'rule_all_frame_expected_support': score({k:v for k,v in rule_counts.items() if k != 'NOT_READY'}),
                            'llm_selected_frame_expected_support': score(llm_counts),
                            'rule_final_preferred_support': human['preferred'].get(last[-1],0) if last else None,
                            'rule_final_least_appropriate_support': human['least_appropriate'].get(last[-1],0) if last else None}
        prev = None
        for r in rows:
            signature = (r['readiness'], r['rule']['action'], tuple((p['uid'],p['track_epoch'],p['visibility'],p['gaze_state'],p['distance_zone'],p['human_radial_motion'],p['relative_distance_trend']) for p in r['social_state']['people']))
            if signature != prev:
                transitions.append({'scenario_id':scenario,'timestamp_us':r['timestamp_us'],'state_id':r['state_id'],
                                    'readiness':r['readiness'],'rule':r['rule'],'people':signature[2]})
                prev = signature
        summaries[scenario] = {
            'raw': audits[scenario], 'frames':len(rows), 'sampled_frames':len(selected),
            'not_ready_rate':fraction(sum(r['readiness']!='READY' for r in rows),len(rows)),
            'not_ready_reasons':dict(Counter(r['readiness_reason'] for r in rows if r['readiness']!='READY')),
            'gaze_states':dict(Counter(p['gaze_state'] for p in person_states)),
            'motion_states':dict(Counter(p['human_radial_motion'] for p in person_states)),
            'distance_zones':dict(Counter(p['distance_zone'] for p in person_states)),
            'rule': {'all_frame_actions':rule_counts,'last_valid_action':last[-1] if last else None,
                     'end_status':rows[-1]['rule']['status'], 'end_action':rows[-1]['rule']['action'],
                     'first_decision_delay_s':delay('rule'), 'first_interaction_delay_s':delay('rule',True)},
            'llm': {'attempts':len(calls),'valid':len(valid),'errors':errors,
                    'not_ready':sum(v['status']=='NOT_READY' for r in selected for v in r['llm']),
                    'action_distribution':llm_counts,'last_valid_action_per_repeat':last_per_repeat,
                    'structured_success_rate':fraction(len(valid),len(calls)),
                    'mean_latency_s':mean(v['request_duration_s'] for v in calls) if calls else None,
                    'repeat_modal_consistency':mean(repeat_consistency) if repeat_consistency else None,
                    'first_decision_delay_s':delay('llm'), 'first_interaction_delay_s':delay('llm',True)},
            'agreement':fraction(sum(matches),len(matches)), 'agreement_pairs':len(matches),
            'detected_person_agreement':fraction(sum(detected_matches),len(detected_matches)),
            'human':human, 'human_scores':human_scores}
    (directory/'summary.json').write_text(json.dumps(summaries, indent=2)+'\n')
    (directory/'transitions.jsonl').write_text(''.join(json.dumps(t)+'\n' for t in transitions))
    columns = ['scenario','detected_frames','frames','rule_actions','llm_actions','agreement','not_ready_rate','rule_delay_s','llm_delay_s']
    table = []
    for name,s in summaries.items():
        table.append([name,s['raw']['person_frames'],s['frames'],json.dumps(s['rule']['all_frame_actions']),
                      json.dumps(s['llm']['action_distribution']),s['agreement'],s['not_ready_rate'],
                      s['rule']['first_decision_delay_s'],s['llm']['first_decision_delay_s']])
    with (directory/'summary.csv').open('w') as f:
        writer=csv.writer(f); writer.writerow(columns); writer.writerows(table)
    lines = ['# Development-set baseline results','',
             'Counts describe frame-level outputs, not independent trials or human accuracy. Rule uses every frame; LLM uses the declared sampling interval. Agreement uses matched sampled states only. NOT_READY is excluded from action agreement. Last valid action is not an episode verdict.','',
             '| Scenario | Detected / frames | Rule counts | LLM counts | Agreement | Not ready |',
             '|---|---:|---|---|---:|---:|']
    for name,s in summaries.items():
        pct=lambda v: '—' if v is None else f'{v:.1%}'
        lines.append(f"| {name} | {s['raw']['person_frames']}/{s['frames']} | {s['rule']['all_frame_actions']} | {s['llm']['action_distribution']} | {pct(s['agreement'])} | {pct(s['not_ready_rate'])} |")
    (directory/'results.md').write_text('\n'.join(lines)+'\n')
    return summaries

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('directory',type=Path)
    summarize(p.parse_args().directory)
