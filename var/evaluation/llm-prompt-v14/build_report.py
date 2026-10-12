"""Summarise saved v14 first-action replays; never call the model again."""
import json
from pathlib import Path
import statistics

from app.policy.rules import decide
from app.state.social_models import SocialState


ROOT = Path(__file__).resolve().parent
RUN = ROOT / 'run-01'


def main():
    manifest = json.loads((RUN / 'manifest.json').read_text())
    summaries = json.loads((RUN / 'summary.json').read_text())
    scenes = json.loads(Path(manifest['settings']['evaluation_contract']).read_text())['scenarios']
    assert len(summaries) == len(scenes) == 9, 'Wait for all nine trials to finish'
    rows = []
    for scene, result in zip(scenes, summaries):
        sid = scene['scenario_id']
        assert result['scenario_id'] == sid
        audits = [json.loads(line) for line in (RUN / 'llm' / sid / 'models.jsonl').read_text().splitlines()]
        assert audits, f'{sid}: no model audit'
        audit = audits[0]
        inference = audit['llm_inference']
        state = audit['social_state']
        assert state == json.loads(audit['social_state_json'])
        old_path = ROOT.parent / 'final-scenario-tuning' / 'run-02' / 'llm' / sid / 'models.jsonl'
        old = json.loads(old_path.read_text().splitlines()[0])['social_state']
        original_evidence = json.loads(json.dumps(state))
        for person in original_evidence['people']:
            person.pop('observation_labels')
        original_evidence['estimator_version'] = old['estimator_version']
        assert original_evidence == old, f'{sid}: source evidence changed'
        decision = inference['decision']
        rows.append({
            'scenario_id': sid,
            'expected_actions': scene['expected_actions'],
            'rule_action': decide(SocialState.model_validate(state)).decision,
            'llm_action': decision['action'] if decision else None,
            'reason': decision['reason'] if decision else None,
            'error': inference['error'], 'valid_response': inference['ok'],
            'matches_requested': bool(decision and decision['action'] in scene['expected_actions']),
            'inference_s': inference['request_duration_s'],
            'source_since_motion_s': (audit['source_robot_timestamp_us'] - scene['motion_start_monotonic_us']) / 1e6,
            'replay_first_output_s': result['resolved_elapsed_s'],
            'accepted_by_live_client': result['accepted_by_live_client'],
            'acceptance_details': result['acceptance_details'],
            'trial_status': result['status'],
            'model_calls': len(audits),
            'labels': [p['observation_labels'] for p in state['people']],
        })
    (ROOT / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')
    matches = sum(r['matches_requested'] for r in rows)
    accepted = sum(bool(r['accepted_by_live_client']) for r in rows)
    valid = sum(r['valid_response'] for r in rows)
    median = statistics.median(r['inference_s'] for r in rows)
    lines = [
        '# LLM v14: nine trimmed scenarios', '',
        f'Actual local `qwen2.5:7b` responses: **{matches}/9 match the requested action options**, '
        f'**{valid}/9 are valid responses**, and **{accepted}/9 were accepted by the production trial client**. '
        f'Median model request time: **{median:.2f} s**. No physical actions were executed.', '',
        'The motion-trimmed SDK recordings were replayed at recorded speed through the production '
        'receiver, shared readiness gate and `SingleTrial`. Prompt v14 and estimator v6 include the '
        'shared descriptive labels. The model weights were loaded before trial clocks started; '
        'prompt processing and generation are included in inference time. Each input matches the '
        'original run-02 first eligible state exactly apart from the added labels and estimator version. '
        'Rule actions below are evaluated from those same submitted states.', '',
        '| Scenario | Requested | Rule | LLM | Inference (s) | Accepted? |',
        '| --- | --- | --- | --- | ---: | --- |',
    ]
    for r in rows:
        lines.append(f"| {r['scenario_id']} | {' / '.join(r['expected_actions'])} | {r['rule_action']} | "
                     f"{r['llm_action'] or 'No valid output'} | {r['inference_s']:.2f} | "
                     f"{'Yes' if r['accepted_by_live_client'] else 'No'} |")
    lines += ['', '## Exact model explanations', '']
    for r in rows:
        lines.append(f"- **{r['scenario_id']} — {r['llm_action']}:** {r['reason'] or r['error']}")
    lines += ['',
        'S1 and S4 still choose APPROACH based on closing distance despite low gaze. S4 also '
        'has an occupied forward corridor. S9 claims there is no sustained looking even though '
        'both the original gaze category and the new attention label explicitly report sustained '
        'looking. These failures show that clearer labels do not ensure the model uses the evidence correctly.', '',
        'Compared with the earlier v13 sweep, S3 now selects ENGAGE, and S6/S7 select CONTINUE. '
        'S9 changes from the desired APPROACH to CONTINUE. Requested-option matches increase from '
        '5/9 to 6/9 in these individual runs; this is not a controlled estimate of label effectiveness. '
        'Only S5 passes live acceptance. The seven visible-person responses exceed the 10 s '
        'source-age limit and arrive after recording end. S8 returns in time but its current '
        'observation is no longer eligible.',
    ]
    lines += ['', '## Interpretation limits', '',
        'These nine recordings were used for policy tuning; this is not held-out evaluation. '
        'One sample per scenario does not establish reliability. The S3 requested options allow either '
        'ENGAGE or APPROACH, although its actual first-state distance is within conversation range. '
        'S5 and S8 submit an initially empty state; S8 does not test reasoning about a visible departing person. '
        'S6 submits before the later brief glance. No gesture recognition is available for S9.', '',
        'Valid JSON, matching an expected action and freshness acceptance are separate outcomes. '
        'The client retains a 10 s model-source age limit, 1 s latest-observation age limit, current '
        'readiness checks and 30 s trial timeout. Recording end can leave the latest observation stale '
        'even when inference takes less than 10 s. See per-scenario acceptance details in `results.json`.', '',
        'This is a real-time replay, whereas the v13 prompt comparison was an offline frozen-state '
        'sweep. Cache residency, load and concurrent replay processing affect timings; differences '
        'cannot be attributed solely to labels. The saved placement snapshot reports zero GPU memory '
        'for this model.', '',
        '## Evidence and reproduction', '',
        '[Run manifest](run-01/manifest.json), [raw summary](run-01/summary.json), '
        '[combined results](results.json), [model placement](run-01/placement.json), '
        '[frozen system prompt](run-01/system-prompt.txt). '
        'Each `run-01/llm/S*/models.jsonl` retains the full submitted state, raw model content, '
        'parsed decision, model configuration, prompt version and inference duration. '
        'The manifest freezes source hashes, model identity and effective configurations.', '',
        'Run from the repository root with a fresh output directory:', '',
        '```sh',
        'python3 -m scripts.replay_first_action --settings config/policy-comparison-final.json \\',
        '  --output var/evaluation/llm-prompt-v14/new-run --policies llm --warm-model',
        '```', '',
        'Regenerate this report from the saved run:', '',
        '```sh', 'PYTHONPATH=. python3 var/evaluation/llm-prompt-v14/build_report.py', '```', '',
    ]
    (ROOT / 'results.md').write_text('\n'.join(lines))
    print(json.dumps({'matches_requested': matches, 'valid_responses': valid,
                      'accepted': accepted, 'median_inference_s': median}))


if __name__ == '__main__':
    main()
