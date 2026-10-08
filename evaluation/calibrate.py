"""Label-free evidence sensitivity audit; never selects settings by scenario answers."""
import argparse
from collections import Counter
import json
from pathlib import Path
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import TemporalConfig
from app.policy.rules import decide


def audit(recordings):
    base=TemporalConfig.from_file('config/temporal-state.json')
    candidates={'baseline':base,
                'shorter_evidence':TemporalConfig.model_validate(base.model_dump() | {'min_span_s':.5,'min_gaze_coverage_s':.4,'category_dwell_s':.2}),
                'wider_gap':TemporalConfig.model_validate(base.model_dump() | {'max_gap_s':.5})}
    output={}
    for name,config in candidates.items():
        scenarios={}
        for path in sorted(Path(recordings).glob('scenario-*.sdk.jsonl')):
            states=[r.state for r in iter_replay_states(path,'sdk',temporal_config=config)]
            observed=[p for s in states for p in s.people if p.visibility=='OBSERVED']
            scenarios[path.name.removesuffix('.sdk.jsonl')]={
                'actions':dict(Counter(decide(s).action or 'NOT_READY' for s in states)),
                'gaze_states':dict(Counter(p.gaze_state for p in observed)),
                'max_gaze_coverage_s':max((p.evidence.gaze_valid_coverage_s for p in observed),default=0),
                'max_distance_contiguous_span_s':max((p.evidence.distance_valid_span_s for p in observed),default=0),
                'track_epochs':len({(p.uid,p.track_epoch) for p in observed}),
                'relative_trends':dict(Counter(p.relative_distance_trend for p in observed))}
        output[name]={'config':config.model_dump(),'scenarios':scenarios}
    return output

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--recordings',default='var/recordings'); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(audit(a.recordings),indent=2)+'\n')
