"""Generate endpoint comparison tables without treating frame counts as trials."""
import argparse
import csv
import json
from pathlib import Path
from statistics import mean


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def report(root):
    root = Path(root)
    versions = {name: read_rows(root / name / 'encounters.jsonl') for name in ('before', 'after')}
    after = versions['after']
    # The baseline predates explicit missing-trim rows. Declare the same missing
    # decision point, without changing or assigning any historical model action.
    for row in after:
        if row['condition'] == 'survey_video_end':
            versions['before'].append(dict(row))
    columns = ['version', 'scenario', 'condition', 'policy', 'endpoint_us', 'source_us', 'state_age_s',
               'status', 'action', 'reason', 'gaze', 'distance_zone', 'distance_m', 'motion',
               'path', 'gesture', 'preferred_count', 'support_of_37', 'appropriateness',
               'modal_agreement', 'original_agreement', 'latency_s', 'error', 'execution']
    table = []
    for version, rows in versions.items():
        for row in rows:
            observed = [p for p in (row.get('social_state') or {}).get('people', []) if p['visibility'] == 'OBSERVED']
            person = observed[0] if len(observed) == 1 else {}
            h = row['human']
            table.append([version, row['scenario_id'], row['condition'], row['policy'],
                          row.get('endpoint_timestamp_us'), row.get('source_timestamp_us'), row.get('state_age_s'),
                          row['status'], row['action'], row.get('reason'), person.get('gaze_state'),
                          person.get('distance_zone'), person.get('latest_distance_m'), person.get('human_radial_motion'),
                          person.get('path_relation', 'UNKNOWN'), person.get('pass_gesture', 'UNKNOWN'),
                          h['preferred_count'], h['preference_support'], h['mean_appropriateness'],
                          h['modal_agreement'], h['original_agreement'], row.get('duration_s'), row.get('error'),
                          row['execution_status']])
    with (root / 'before-after.csv').open('w', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(columns); writer.writerows(table)
    lines = ['# Development recording endpoint comparison', '',
             'Survey videos were trimmed; end offsets are missing. Actual survey-end actions and human scores are unavailable for all nine scenarios and both policies. `NOT_READY` is not an action. No favourable scores are assigned to missing observations, errors or unrun inference.', '',
             '## Survey video end (primary evaluation)', '',
             '| Scenario | Rule before | Rule after | LLM before | LLM after | Human support/rating |',
             '|---|---|---|---|---|---|']
    for n in range(1, 10):
        lines.append(f'| S{n} | NOT_READY | NOT_READY | NOT_READY | NOT_READY | unavailable: trim offsets missing |')
    lines += ['', 'Primary coverage is **0/9** for each policy/version. This is unresolved synchronisation, not measured social accuracy.', '',
              '## SDK end (separate diagnostic, not the survey endpoint)', '',
              '| Scenario | Rule before | Rule after | LLM before | LLM after | After cue availability | After support / rating | Modal / original agreement |',
              '|---|---|---|---|---|---|---|---|']
    get = lambda v, n, p: next(r for r in versions[v] if r['scenario_id'] == f'S{n}' and r['policy'] == p and r['condition'] == 'sdk_end_diagnostic')
    for n in range(1, 10):
        entries = [get(v, n, p) for v, p in (('before', 'rule'), ('after', 'rule'), ('before', 'llm'), ('after', 'llm'))]
        outcomes = [r['action'] or r['status'] for r in entries]
        r = entries[1]; obs = [p for p in r['social_state']['people'] if p['visibility'] == 'OBSERVED']
        cues = (f"{obs[0]['gaze_state']}, {obs[0]['distance_zone']}; path/gesture UNKNOWN" if len(obs) == 1 else 'no current person; path/gesture unavailable')
        h = r['human']
        score = 'unavailable' if h['preferred_count'] is None else f"{h['preferred_count']}/37 ({h['preference_support']:.1%}) / {h['mean_appropriateness']:.2f}"
        agree = 'unavailable' if h['modal_agreement'] is None else f"{h['modal_agreement']} / {h['original_agreement']}"
        lines.append('| ' + ' | '.join([f'S{n}', *outcomes, cues, score, agree]) + ' |')
    lines += ['', 'SDK-end coverage: **0/9 before, 1/9 after** for both policies. S2 scores are conditional comparisons at a different endpoint. Its chosen ENGAGE has support 11/37 and rating 3.78 (36 valid ratings); APPROACH has support 18/37 and rating 3.65. Neither modal nor original-label agreement is achieved at this diagnostic point.', '',
              'All nine camera-end proxies are NOT_READY/STATE_STALE for both policies/versions, because SDK tails end 7.2–19.6 seconds earlier. No action is extrapolated across that gap.', '',
              '## Availability, source time and latency', '',
              '| Scenario | Person frames / perceptions | SDK end (receipt µs) | Camera end (receipt µs) | SDK age at camera end (s) | SDK-end hold after | Rule / LLM latency after (s) |',
              '|---|---:|---:|---:|---:|---|---|']
    audits = json.loads((root / 'after' / 'sensor-audit.json').read_text())
    contract = json.loads((root / 'after' / 'manifest.json').read_text())['contract']
    for n, c in enumerate(contract['scenarios'], 1):
        a, r, m = audits[f'S{n}'], get('after', n, 'rule'), get('after', n, 'llm')
        lat = 'not run' if r['duration_s'] is None else f"{r['duration_s']:.6f} / {m['duration_s']:.3f}"
        lines.append(f"| S{n} | {a['person_frames']}/{a['perception_frames']} | {c['endpoints']['sdk_end_diagnostic']} | {c['endpoints']['camera_end_proxy']} | {c['camera_tail_without_sdk_s']:.3f} | {r['readiness_reason'] or 'READY'} | {lat} |")
    lines += ['', 'No endpoint inference errors occurred. Ineligible LLM cases made no calls. All actions have NOT_EXECUTED_OFFLINE execution status. Source time is robot-host monotonic receipt time; it is not Unix time or verified exposure time.', '',
              '## Fixed-state policy ablation', '',
              'Original v3 and refined v6 prompts receive identical canonical bytes on 11 eligible frozen states (eight S3, two S9, one S2). These are diagnostic states, not 11 independent encounters.', '',
              '| Prompt | Calls | Errors | APPROACH in interaction range / ENGAGE in approachable range | Mean latency (s) |',
              '|---|---:|---:|---:|---:|']
    rows = read_rows(root / 'policy-ablation' / 'results.jsonl')
    for condition in ('original-v3', 'refined-v6'):
        cases = [r for r in rows if r['prompt_condition'] == condition]
        violations = 0
        for r in cases:
            people = [p for p in json.loads(r['social_state_json'])['people'] if p['visibility'] == 'OBSERVED']
            zone = people[0]['distance_zone']
            violations += (r['action'], zone) in (('APPROACH', 'INTERACTION_RANGE'), ('ENGAGE', 'APPROACHABLE'))
        lines.append(f"| {condition} | {len(cases)} | {sum(r['error'] is not None for r in cases)} | {violations} | {mean(r['duration_s'] for r in cases):.3f} |")
    lines += ['', 'Rule actions are identical before/after on these frozen states. Recorded rule action/coverage changes come from temporal preprocessing. The prompt reduces distance-semantics errors in this sample. The LLM still chooses CONTINUE for the two attentive APPROACHABLE S9 diagnostic states and gives weak distance-based explanations. It did not observe the pass gesture. This is not evidence of gesture understanding or policy superiority.', '',
              'Full per-scenario/per-policy scores, cue availability, latency and execution/error status are in `before-after.csv`; exact inputs and outputs are in each run folder. Frame traces and action counts are separate in `states.jsonl`, `diagnostics.jsonl` and `sensor-audit.json`.']
    (root / 'comparison.md').write_text('\n'.join(lines) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('directory', type=Path)
    report(p.parse_args().directory)


if __name__ == '__main__':
    main()
