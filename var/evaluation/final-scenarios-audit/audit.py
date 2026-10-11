"""Audit and trim all nine final SDK captures at robot motion onset.

Run from the repository root: python3 var/evaluation/final-scenarios-audit/audit.py
All scenarios are trimmed regardless of alignment verdict; sources are preserved.
"""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.sdk_capture import validate_sdk_capture
from app.replay.llm_inputs import iter_replay_states
from evaluation.gaze_features import FeatureConfig, person_features

OUT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'config/temporal-development-frozen.json').read_text())
VERDICTS = {
    1: ('NOT_ALIGNED', 'Closing and disappearance are compatible with passing, but gaze is mostly high until late. Human passing is not established by relative distance alone.'),
    2: ('ALIGNED', 'Sustained high gaze at 2.11–2.56 m, within approach distance. Two perception packets contain duplicate entries for the same UID; one gaze vector is unavailable.'),
    3: ('PARTIAL', 'Sustained high gaze, but starts around 1.7 m, outside the configured 1.5 m interaction range; reaches that range later.'),
    4: ('NOT_ALIGNED', 'Closing and final disappearance, but frequent high gaze and many unavailable estimates do not support a clean no-look pass.'),
    5: ('UNOBSERVABLE', 'No detected person in any post-motion perception frame; walking away and gaze cannot be assessed.'),
    6: ('NOT_ALIGNED', 'High gaze occurs across most of the recorded pass, with almost no usable low gaze. The file ends without a reliable look-ahead-again phase or completion of the pass.'),
    7: ('PARTIAL', 'Alternating high and low usable gaze is compatible with intermittent attention, but detection/default-gaze gaps and UID changes break continuity. Standing to the side is unverified.'),
    8: ('PARTIAL', 'Relative separation grows from about 1.12 to 2.10 m, consistent with moving away. Continuous looking is not supported: many low or unavailable gaze estimates, and an initial no-detection period.'),
    9: ('PARTIAL', 'Looking is supported at roughly 2.0–2.4 m. No gesture or path-relation measurements, and no matching video, establish the pass gesture or placement in front of the route.'),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stats(records):
    result = {}
    for stream in ('perception', 'locomotion'):
        rr = [(ln, r) for ln, r in records if r['stream'] == stream]
        gaps = [(b[1]['received_monotonic_us'] - a[1]['received_monotonic_us']) / 1e6
                for a, b in zip(rr, rr[1:])]
        source_times = [r['packet'].get('time') if stream == 'perception'
                        else r['packet'].get('odometry', {}).get('time') for _, r in rr]
        pairs = [(a, b) for a, b in zip(source_times, source_times[1:])
                 if isinstance(a, (float, int)) and isinstance(b, (float, int))]
        result[stream] = {
            'packets': len(rr), 'first_sequence': rr[0][1]['sequence'],
            'last_sequence': rr[-1][1]['sequence'],
            'sequence_discontinuities': [{'line': b[0], 'previous': a[1]['sequence'], 'current': b[1]['sequence']}
                                         for a, b in zip(rr, rr[1:]) if b[1]['sequence'] != a[1]['sequence'] + 1],
            'nonincreasing_receipt_times': sum(g <= 0 for g in gaps),
            'median_receipt_gap_s': median(gaps) if gaps else None,
            'max_receipt_gap_s': max(gaps) if gaps else None,
            'gaps_over_0_25_s': sum(g > .25 for g in gaps),
            'repeated_source_timestamps': sum(a == b for a, b in pairs),
            'backwards_source_timestamps': sum(b < a for a, b in pairs),
        }
    return result


def speed(r):
    v = r['packet']['odometry']['velocity']
    return math.hypot(v['linear_x'], v['linear_y']), abs(v['angular_z'])


def onset(records):
    ll = [(ln, r) for ln, r in records if r['stream'] == 'locomotion']
    # Confirm movement across three consecutive packets, then include its low-speed ramp.
    strong = next(i for i in range(len(ll) - 2)
                  if all(speed(r)[0] > .02 or speed(r)[1] > .03 for _, r in ll[i:i+3]))
    i = strong
    while i > 0 and any(x > 1e-6 for x in speed(ll[i-1][1])):
        i -= 1
    return ll[i], ll[strong]


def audit(n):
    path = ROOT / f'var/recordings/final_scenario_{n}-sdk.jsonl'
    data = path.read_bytes()
    records = [(ln, json.loads(line)) for ln, line in enumerate(data.splitlines(), 1) if line.strip()]
    for _, r in records:
        validate_sdk_capture(r)
    assert len({r['session_id'] for _, r in records}) == 1
    assert all(b['received_monotonic_us'] >= a['received_monotonic_us']
               for (_, a), (_, b) in zip(records, records[1:]))
    (start_line, first), (_, strong) = onset(records)
    start = first['received_monotonic_us']
    post = [(ln, r) for ln, r in records if r['received_monotonic_us'] >= start]
    frames = [(ln, r) for ln, r in post if r['stream'] == 'perception']
    people = [(ln, r, p, person_features(p, FeatureConfig()))
              for ln, r in frames for p in r['packet'].get('persons') or []]
    valid = [f for *_, f in people if f['feature_status'] == 'AVAILABLE' and f['gaze_overlap_raw_finite'] is not None]
    gs = [f['gaze_overlap_raw_finite'] for f in valid]
    ds = [f['distance_m'] for *_, f in people if f['distance_m'] is not None]
    buckets = []
    for b in range(math.ceil((records[-1][1]['received_monotonic_us'] - start) / 1e6)):
        ff = [r for _, r in frames if b <= (r['received_monotonic_us'] - start) / 1e6 < b+1]
        pp = [(p, f) for _, r, p, f in people if b <= (r['received_monotonic_us'] - start) / 1e6 < b+1]
        gg = [f['gaze_overlap_raw_finite'] for _, f in pp if f['feature_status'] == 'AVAILABLE' and f['gaze_overlap_raw_finite'] is not None]
        dd = [f['distance_m'] for _, f in pp if f['distance_m'] is not None]
        buckets.append({'start_s': b, 'perception_packets': len(ff), 'person_instances': len(pp),
                        'distance_median_m': median(dd) if dd else None,
                        'gaze_median': median(gg) if gg else None,
                        'gaze_high': sum(g >= CONFIG['looking_enter'] for g in gg),
                        'gaze_low': sum(g <= CONFIG['looking_exit'] for g in gg),
                        'gaze_unknown': len(pp)-len(gg)})
    intervals = []
    missing_start = None
    for _, r in frames:
        t = (r['received_monotonic_us'] - start) / 1e6
        if not r['packet'].get('persons') and missing_start is None:
            missing_start = t
        elif r['packet'].get('persons') and missing_start is not None:
            intervals.append({'start_s': missing_start, 'end_s': t, 'ends_at_capture_end': False})
            missing_start = None
    if missing_start is not None:
        intervals.append({'start_s': missing_start, 'end_s': (records[-1][1]['received_monotonic_us']-start)/1e6, 'ends_at_capture_end': True})
    result = {
        'scenario': n, 'source_path': str(path.relative_to(ROOT)), 'source_sha256': sha(data),
        'session_id': first['session_id'], 'verdict': VERDICTS[n][0], 'assessment': VERDICTS[n][1],
        'total_duration_s': (records[-1][1]['received_monotonic_us']-records[0][1]['received_monotonic_us'])/1e6,
        'motion_start_offset_s': (start-records[0][1]['received_monotonic_us'])/1e6,
        'motion_start_source_line': start_line, 'motion_start_monotonic_us': start,
        'confirmed_motion_offset_s': (strong['received_monotonic_us']-records[0][1]['received_monotonic_us'])/1e6,
        'post_motion_duration_s': (records[-1][1]['received_monotonic_us']-start)/1e6,
        'full_streams': stats(records), 'post_motion_streams': stats(post),
        'post_motion_person_count_histogram': dict(Counter(len(r['packet'].get('persons') or []) for _, r in frames)),
        'post_motion_duplicate_uid_frames': sum(len({p.get('uid') for p in r['packet'].get('persons') or []}) < len(r['packet'].get('persons') or []) for _, r in frames),
        'post_motion_person_instances': len(people), 'post_motion_distinct_uids': len({p.get('uid') for _, _, p, _ in people}),
        'gaze_usable': len(gs), 'gaze_unknown_reasons': dict(Counter(f['invalid_reason'] or 'missing_overlap' for *_, f in people if f['feature_status'] != 'AVAILABLE' or f['gaze_overlap_raw_finite'] is None)),
        'gaze_high_count': sum(g >= CONFIG['looking_enter'] for g in gs), 'gaze_low_count': sum(g <= CONFIG['looking_exit'] for g in gs),
        'gaze_median': median(gs) if gs else None, 'distance_min_m': min(ds) if ds else None,
        'distance_max_m': max(ds) if ds else None, 'distance_interaction_samples': sum(d <= CONFIG['interaction_max_m'] for d in ds),
        'no_detection_intervals_relative_to_motion': intervals, 'one_second_bins': buckets,
        'trimmed_path': None,
    }
    target = OUT / 'trimmed' / path.name
    target.parent.mkdir(exist_ok=True)
    counts = Counter()
    mapping = {}
    trimmed = []
    for ln, r in post:
        counts[r['stream']] += 1
        mapping.setdefault(r['stream'], {'first_original_sequence': r['sequence'], 'first_source_line': ln})
        trimmed.append(dict(r, sequence=counts[r['stream']]))
    output = ''.join(json.dumps(r, separators=(',', ':'), allow_nan=False)+'\n' for r in trimmed).encode()
    if target.exists() and target.read_bytes() != output:
        raise FileExistsError(f'Refusing to replace a different trim: {target}')
    target.write_bytes(output)
    states = list(iter_replay_states(target, 'sdk'))
    assert len(states) == counts['perception']
    for (_, original), cropped in zip(post, trimmed):
        assert {k:v for k,v in original.items() if k != 'sequence'} == {k:v for k,v in cropped.items() if k != 'sequence'}
    result.update(trimmed_path=str(target.relative_to(ROOT)), trimmed_sha256=sha(output),
                  trim_sequence_mapping=mapping, replay_validated_perception_states=len(states))
    assert path.read_bytes() == data, f'Source changed during audit: {path}'
    return result


if __name__ == '__main__':
    report = {'analysis_config': CONFIG, 'motion_start_method': 'First nonzero planar/yaw velocity in the contiguous ramp leading to three packets above 0.02 m/s or 0.03 rad/s.',
              'trim_method': 'Remove earlier packets; preserve session, timestamps and payloads; restart each stream sequence at 1 for SDK replay compatibility.',
              'limitations': ['No camera frames with matching final-capture sessions were found.', 'Contiguous saved sequences do not prove every upstream sensor frame arrived or that the intended trial finished.', 'Usable gaze means a finite nonzero vector and finite overlap, not validated eye contact. Suspected default bundles are UNKNOWN for this audit; this does not modify live policy.', 'Gaze high/low counts are instantaneous diagnostics, not temporal gaze categories or time fractions.', 'Intent labels require independent observation; sensor agreement is not behavioral ground truth.'],
              'scenarios': [audit(n) for n in range(1, 10)]}
    (OUT / 'audit.json').write_text(json.dumps(report, indent=2)+'\n')
    for r in report['scenarios']:
        print(f"{r['scenario']}: {r['verdict']} onset={r['motion_start_offset_s']:.3f}s duration={r['post_motion_duration_s']:.3f}s high={r['gaze_high_count']}/{r['gaze_usable']} low={r['gaze_low_count']} unknown={sum(r['gaze_unknown_reasons'].values())} empty={r['post_motion_person_count_histogram'].get(0,0)} trim={r['trimmed_path']}")
