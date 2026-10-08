"""Behavioral, missing-information and recorded end-to-end comparison regressions."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.domain.actions import ACTIONS
from app.domain.model_decision import ModelDecision, parse_model_decision, ModelDecisionValidationError
from app.inference.ollama import OllamaConfig, OllamaResult, OllamaClient, HttpResponse
from app.policy.llm import classify_llm, SOCIAL_STATE_PREFIX
from app.policy.rules import decide
from app.state.social_models import SocialState, TemporalConfig
from app.replay.llm_inputs import iter_replay_states
from evaluation.policy_matrix import reference_state, matrix_rows
from evaluation.run_scenarios import evaluate, load_humans, digest
from tests.test_social_state import sample
from app.pipeline import TrackingPipeline
from app.state.estimator import SocialStateEstimator

ROOT=Path(__file__).resolve().parents[1]

class FakeClient:
    config=OllamaConfig(base_url='http://localhost:11434',model='explicit-test-double')
    def __init__(self): self.inputs=[]
    def chat(self,messages):
        self.inputs.append(messages[1].content.removeprefix(SOCIAL_STATE_PREFIX))
        decision=ModelDecision(action='CONTINUE',reason='Test double, not measured model behavior.')
        return OllamaResult('explicit-test-double','explicit-test-double',decision.model_dump_json(),0.01,decision,None)

class ComparisonTests(unittest.TestCase):
    def test_canonical_four_actions_and_legacy_stop_rejected(self):
        self.assertEqual(set(ACTIONS),{'CONTINUE','YIELD','APPROACH','ENGAGE'})
        with self.assertRaises(ModelDecisionValidationError):
            parse_model_decision('{"action":"STOP","reason":"Old contract"}')

    def test_complete_matrix_priority_and_no_fifth_action(self):
        rows=list(matrix_rows()); self.assertEqual(len(rows),840)
        for r in rows:
            self.assertIn(r['action'],(*ACTIONS,'NOT_READY'))
            if r['path_relation']=='CONFLICT': self.assertEqual(r['action'],'YIELD')
            elif r['zone'] in ('UNKNOWN','TOO_CLOSE') or r['gaze']=='UNKNOWN':
                self.assertEqual(r['action'],'NOT_READY')
            elif r['pass_gesture']=='PASS' or r['gaze'] in ('NONE','INTERMITTENT'):
                self.assertEqual(r['action'],'CONTINUE')

    def test_missing_track_is_not_empty_scene(self):
        payload=reference_state(); payload['people'][0]['visibility']='TEMPORARILY_MISSING'
        state=SocialState.model_validate(payload); client=FakeClient()
        self.assertIsNone(decide(state).action)
        self.assertEqual(classify_llm(state,client)['status'],'NOT_READY'); self.assertFalse(client.inputs)
        payload['people']=[]; state=SocialState.model_validate(payload)
        self.assertEqual(decide(state).action,'CONTINUE')
        self.assertEqual(classify_llm(state,client)['action'],'CONTINUE'); self.assertEqual(len(client.inputs),1)

    def test_moving_base_closing_range_never_becomes_human_approach(self):
        pipeline=TrackingPipeline('moving'); estimator=SocialStateEstimator()
        for i in range(21):
            state=estimator.update(pipeline.process(sample(i,distance=2.5-i*.02,velocity=.2)))
        p=state.people[0]
        self.assertEqual(p.relative_distance_trend,'DECREASING')
        self.assertEqual(p.human_radial_motion,'UNKNOWN')
        self.assertIn('EGO_MOTION_UNCOMPENSATED',p.validity_flags)
        self.assertEqual(decide(state).action,'APPROACH')
        self.assertEqual(p.path_relation,'UNKNOWN'); self.assertEqual(p.pass_gesture,'UNKNOWN')

    def test_gaze_missingness_mean_and_thresholds(self):
        pipeline=TrackingPipeline('gaze'); estimator=SocialStateEstimator()
        for i in range(21): state=estimator.update(pipeline.process(sample(i,gaze=.95)))
        self.assertAlmostEqual(state.people[0].evidence.mean_gaze_overlap,.95)
        self.assertEqual(state.observation_readiness,'READY')
        state=estimator.update(pipeline.process(sample(21,gaze=None)))
        self.assertEqual(state.people[0].gaze_state,'UNKNOWN')
        self.assertEqual(state.observation_readiness,'NOT_READY')
        self.assertIsNone(decide(state).action)

    def test_real_recordings_have_known_coverage_and_no_gesture(self):
        counts=[0,27,110,0,0,4,0,32,56]
        for index,count in enumerate(counts,1):
            states=list(iter_replay_states(ROOT/f'var/recordings/scenario-{index:02}.sdk.jsonl','sdk'))
            self.assertEqual(sum(any(p.visibility=='OBSERVED' for p in x.state.people) for x in states),count)
            for x in states:
                for p in x.state.people:
                    self.assertEqual(p.pass_gesture,'UNKNOWN'); self.assertEqual(p.path_relation,'UNKNOWN')
                    if not p.evidence.stationary_window_confirmed: self.assertEqual(p.human_radial_motion,'UNKNOWN')
            if index==3: self.assertTrue(any(decide(x.state).action=='ENGAGE' for x in states))
            if index==9: self.assertTrue(any(decide(x.state).action=='APPROACH' for x in states))

    def test_end_to_end_hash_parity_repeats_and_regeneration(self):
        client=FakeClient()
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'results'
            summary=evaluate([ROOT/'var/recordings/scenario-03.sdk.jsonl'],output,client=client,repeats=3)
            states=[json.loads(l) for l in (output/'states.jsonl').read_text().splitlines()]
            hashes={r['state_sha256'] for r in states}
            self.assertTrue(client.inputs); self.assertTrue(all(digest(s) in hashes for s in client.inputs))
            self.assertTrue(all(client.inputs[i]==client.inputs[i+1]==client.inputs[i+2] for i in range(0,len(client.inputs),3)))
            self.assertEqual(summary['scenario-03']['frames'],115)
            from evaluation.summarize import summarize
            self.assertEqual(summary,summarize(output))
            with self.assertRaises(FileExistsError): evaluate([],output)

    def test_reconstructed_frames_match_live_production_stack(self):
        from app.social_pipeline import SocialPipeline
        states=list(iter_replay_states(ROOT/'var/recordings/scenario-03.sdk.jsonl','sdk'))
        live=SocialPipeline(states[0].state.session_id)
        for item in states:
            snapshot=live.process(item.source['reconstructed_raw_observation'])
            self.assertEqual(snapshot['social_state'],item.state.model_dump(mode='json'))

    def test_publisher_keeps_exact_sampled_inputs(self):
        from evaluation.publish import publish
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'run'; dest=Path(d)/'published'; report=Path(d)/'report.md'
            report.write_text('# Report\n\n## J. Research interpretation\n')
            evaluate([ROOT/'var/recordings/scenario-01.sdk.jsonl'],output,client=FakeClient())
            publish(output,dest,report)
            self.assertEqual((output/'decisions.jsonl').read_bytes(),(dest/'decisions.jsonl').read_bytes())
            self.assertIn('Measured run:',report.read_text())
            self.assertEqual(len((dest/'selected-inputs.jsonl').read_text().splitlines()),9)

    def test_camera_tail_is_not_treated_as_sdk_evidence(self):
        from evaluation.audit_recordings import audit_recording
        audit=audit_recording(ROOT/'var/recordings/scenario-04.sdk.jsonl')
        head=audit['camera_coverage']['head']
        self.assertGreater(head['end_after_sdk_s'],7.0)
        self.assertNotIn(11,head['within_sdk_window_sequences'])
        self.assertEqual(audit['person_frames'],0)

    def test_parse_failure_is_error_not_continue(self):
        class Transport:
            def post(self,*a,**k):
                return HttpResponse(200,b'{"model":"bad","done":true,"message":{"role":"assistant","content":"not json"}}')
        result=classify_llm(SocialState.model_validate(reference_state()),OllamaClient(FakeClient.config,transport=Transport()))
        self.assertEqual(result['status'],'ERROR'); self.assertIsNone(result['action'])
        self.assertEqual(result['error']['category'],'invalid_decision')

    def test_survey_validation_without_invented_labels(self):
        self.assertEqual(load_humans(None),{})
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'survey.json'
            p.write_text(json.dumps({'scenario-01':{'preferred':{'CONTINUE':.75,'YIELD':.25},'least_appropriate':{'APPROACH':1}}}))
            self.assertEqual(load_humans(p)['scenario-01']['preferred']['CONTINUE'],.75)
            p.write_text('{"scenario-01":{"preferred":{"STOP":1},"least_appropriate":{"ENGAGE":1}}}')
            with self.assertRaises(ValueError): load_humans(p)

if __name__=='__main__': unittest.main()
