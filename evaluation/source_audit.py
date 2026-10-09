"""Source audit imported from c6b988f; independent of encounter scoring."""
from collections import Counter
import json
import math

def raw_audit(path):
    records = [json.loads(line) for line in path.read_text().splitlines()]
    frames = [r for r in records if r['stream'] == 'perception']
    people = [p for r in frames for p in r['packet'].get('persons', [])]
    loco = [r['packet']['odometry'] for r in records if r['stream'] == 'locomotion']
    gazes = [p['gaze_overlap'] for p in people if p.get('gaze_overlap') is not None]
    cameras = path.with_name(path.name.replace('.sdk.jsonl', '.cameras')) / 'frames.jsonl'
    camera_rows = [json.loads(l) for l in cameras.read_text().splitlines()] if cameras.exists() else []
    return {'perception_frames': len(frames), 'locomotion_frames': len(loco),
            'person_frames': sum(bool(r['packet'].get('persons')) for r in frames),
            'person_instances': len(people), 'uids': sorted({p['uid'] for p in people}),
            'nonempty_person_fields': dict(Counter(k for p in people for k,v in p.items() if v not in (None, [], {}))),
            'gaze_min': min(gazes, default=None), 'gaze_max': max(gazes, default=None),
            'distance_min_m': min((p['dist_mm']/1000 for p in people if p.get('dist_mm') is not None), default=None),
            'distance_max_m': max((p['dist_mm']/1000 for p in people if p.get('dist_mm') is not None), default=None),
            'linear_velocity_range_mps': [min(o['velocity']['linear_x'] for o in loco), max(o['velocity']['linear_x'] for o in loco)] if loco else None,
            'odometry_displacement_m': math.dist(list(loco[0]['position'].values()), list(loco[-1]['position'].values())) if loco else None,
            'camera_records': len(camera_rows),
            'camera_events': dict(Counter(str(r.get('event')) for r in camera_rows)),
            'camera_streams': dict(Counter(str(r.get('camera')) for r in camera_rows))}
