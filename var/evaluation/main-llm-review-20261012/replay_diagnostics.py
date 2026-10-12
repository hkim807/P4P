"""Review-only full-recording diagnostics; no physical execution or policy edits."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from app.inference.ollama import OllamaClient, OllamaConfig
from app.policy.llm import PROMPT_VERSION, SYSTEM_PROMPT, classify_llm, build_llm_prompt
from app.policy.rules import decide, normalise_rule_decision
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import TemporalConfig, observation_hold_reason
from app.state.tracks import TrackConfig

ROOT = Path('var/evaluation/main-llm-review-20261012')


def dump(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def main():
    settings = json.loads(Path('config/policy-comparison-final.json').read_text())
    scenes = json.loads(Path(settings['evaluation_contract']).read_text())['scenarios']
    temporal = TemporalConfig.from_file(settings['temporal_config'])
    tracking = TrackConfig.from_file(settings['track_config'])
    output = ROOT / 'full-recording'
    output.mkdir(exist_ok=False)
    config = OllamaConfig(base_url='http://127.0.0.1:11434', **{
        key: settings[key] for key in ('model', 'temperature', 'seed', 'num_ctx',
                                      'num_predict', 'timeout_seconds')})
    client = OllamaClient(config)
    dump(output / 'manifest.json', {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'prompt_version': PROMPT_VERSION, 'system_prompt': SYSTEM_PROMPT,
        'ollama_configuration': config.model_dump(), 'temporal_config': temporal.model_dump(),
        'tracking_config': tracking.__dict__,
        'method': 'All frames estimated causally; first READY frame then READY frames at least 1 source second apart; synchronous offline classify_llm; no execution/latency simulation',
        'expected_actions_are_model_inputs': False,
    })
    summary = []
    for scene in scenes:
        sid = scene['scenario_id']
        items = list(iter_replay_states(scene['recording'], 'sdk', track_config=tracking,
                                       temporal_config=temporal))
        assert items[0].source['file_sha256'] == scene['source_sha256']
        folder = output / sid
        folder.mkdir()
        origin = items[0].state.robot_timestamp_us
        holds, cues, events = Counter(), {}, Counter()
        selected, last_selected = [], None
        with (folder / 'states.jsonl').open('x') as trace:
            for item in items:
                state = item.state
                reason = observation_hold_reason(state)
                holds[reason or 'READY'] += 1
                offset = (state.robot_timestamp_us - scene['motion_start_monotonic_us']) / 1e6
                rule = normalise_rule_decision(decide(state.model_dump())).model_dump() if not reason else None
                trace.write(json.dumps({'source_offset_s': (state.robot_timestamp_us-origin)/1e6,
                    'since_motion_s': offset, 'source': item.source,
                    'social_state': state.model_dump(), 'rule_decision': rule}) + '\n')
                events.update(event['type'] for event in state.track_events)
                for person in state.people:
                    if person.visibility == 'OBSERVED':
                        for key in ('gaze_state', 'distance_zone', 'relative_distance_trend', 'path_position'):
                            cues.setdefault(key, Counter())[getattr(person, key)] += 1
                if not reason and (last_selected is None or state.robot_timestamp_us-last_selected >= 1_000_000):
                    selected.append(item)
                    last_selected = state.robot_timestamp_us
        row = {'scenario_id': sid, 'observations': len(items), 'readiness_counts': dict(holds),
               'cue_counts_observed_person_frames': {k: dict(v) for k, v in cues.items()},
               'track_event_counts': dict(events), 'selected_llm_calls': len(selected),
               'decisions': [], 'expected_actions': scene['expected_actions']}
        summary.append(row)
        dump(output / 'summary.json', summary)
        with (folder / 'llm.jsonl').open('x') as decisions:
            for item in selected:
                result = classify_llm(item.state, client)
                result['since_motion_s'] = (item.state.robot_timestamp_us-scene['motion_start_monotonic_us'])/1e6
                result['social_state'] = json.loads(build_llm_prompt(item.state).social_state_json)
                result['source'] = item.source
                decisions.write(json.dumps(result) + '\n')
                decisions.flush()
                row['decisions'].append({key: result.get(key) for key in
                    ('since_motion_s', 'status', 'action', 'decision', 'error', 'request_duration_s')})
                dump(output / 'summary.json', summary)
                print(json.dumps({'scenario': sid, **row['decisions'][-1]}), flush=True)
        if selected:
            item = selected[0]
            dump(folder / 'first-ready-state.json', item.state.model_dump())
            prompt = build_llm_prompt(item.state)
            (folder / 'first-ready-prompt.txt').write_text(prompt.instructions + '\n\n' + prompt.messages[1].content)
    return 0


if __name__ == '__main__':
    sys.exit(main())
