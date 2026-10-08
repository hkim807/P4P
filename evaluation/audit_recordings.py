"""Audit source coverage and camera/SDK overlap before interpreting a scenario."""
import argparse
import json
from pathlib import Path
from evaluation.source_audit import raw_audit


def audit_recording(path):
    path=Path(path); result=raw_audit(path)
    sdk=[json.loads(l) for l in path.read_text().splitlines()]
    camera_path=path.with_name(path.name.replace('.sdk.jsonl','.cameras'))/'frames.jsonl'
    cameras=[json.loads(l) for l in camera_path.read_text().splitlines()] if camera_path.exists() else []
    perception=[r for r in sdk if r['stream']=='perception']
    first,last=perception[0]['received_monotonic_us'],perception[-1]['received_monotonic_us']
    result['sdk_perception_span_s']=(last-first)/1e6
    result['camera_coverage']={}
    for camera in sorted({r['camera'] for r in cameras}):
        frames=[r for r in cameras if r['camera']==camera and r['event']=='frame']
        same_session=bool(frames and all(r['session_id']==perception[0]['session_id'] for r in frames)
                          and len({r['session_id'] for r in perception})==1)
        overlaps=[r for r in frames if first<=r['received_monotonic_us']<=last] if same_session else []
        result['camera_coverage'][camera]={
            'frames':len(frames),'same_session':same_session,
            'span_s':(frames[-1]['received_monotonic_us']-frames[0]['received_monotonic_us'])/1e6 if frames else None,
            'start_relative_to_sdk_s':(frames[0]['received_monotonic_us']-first)/1e6 if same_session else None,
            'end_after_sdk_s':(frames[-1]['received_monotonic_us']-last)/1e6 if same_session else None,
            'within_sdk_window_sequences':[r['sequence'] for r in overlaps],
            'unavailable_reasons':[r.get('reason') for r in cameras if r['camera']==camera and r['event']=='unavailable']}
    result['coverage_warning']='Compare only overlapping receipt-time windows; same filenames do not establish equal capture duration. A missing tail cannot be diagnosed from contiguous saved sequence numbers alone.'
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--recordings',type=Path,default=Path('var/recordings')); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps({r.name.removesuffix('.sdk.jsonl'):audit_recording(r) for r in sorted(a.recordings.glob('scenario-*.sdk.jsonl'))},indent=2)+'\n')
