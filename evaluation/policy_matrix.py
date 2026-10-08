"""Enumerate category combinations; diagnostic fixtures are never scenario labels."""
from copy import deepcopy
import csv
from itertools import product
from pathlib import Path
from app.pipeline import TrackingPipeline
from app.state.estimator import SocialStateEstimator
from app.state.social_models import SocialState
from app.policy.rules import decide


def reference_state():
    pipeline, estimator = TrackingPipeline('matrix-fixture'), SocialStateEstimator()
    for i in range(21):
        state = estimator.update(pipeline.process({
            'timestamp':i*100000,'people':[{'uid':1,'distance_m':2.0,'gaze_overlap':0.95}],
            'robot':{'linear_velocity':0.0,'angular_velocity':0.0},'safety':{'lidar':None,'sonar':None}}))
    return state.model_dump()


def matrix_rows():
    base = reference_state()
    motions = [('TOWARD','DECREASING'),('STATIONARY','STABLE'),('AWAY','INCREASING')]
    motions += [('UNKNOWN',r) for r in ('UNKNOWN','STABLE','DECREASING','INCREASING')]
    distances = {'TOO_CLOSE':0.4,'INTERACTION_RANGE':1.0,'APPROACHABLE':2.0,'FAR':4.0,'UNKNOWN':None}
    for gaze, (motion,trend), zone, path, gesture in product(
            ('NONE','INTERMITTENT','SUSTAINED','UNKNOWN'),motions,distances,
            ('UNKNOWN','CLEAR','CONFLICT'),('UNKNOWN','PASS')):
        payload=deepcopy(base); p=payload['people'][0]
        p.update(gaze_state=gaze,human_radial_motion=motion,relative_distance_trend=trend,
                 distance_zone=zone,latest_distance_m=distances[zone],path_relation=path,pass_gesture=gesture)
        p['evidence'].update(gaze_valid=gaze!='UNKNOWN',latest_distance_valid=zone!='UNKNOWN',
                             stationary_window_confirmed=motion!='UNKNOWN',distance_trend_valid=trend!='UNKNOWN')
        decision=decide(SocialState.model_validate(payload))
        yield {'gaze':gaze,'human_radial_motion':motion,'relative_trend':trend,'zone':zone,
               'path_relation':path,'pass_gesture':gesture,'action':decision.action or 'NOT_READY',
               'reason':decision.reason_code}


def write_matrix(path):
    rows=list(matrix_rows()); path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,required=True)
    write_matrix(p.parse_args().output)
