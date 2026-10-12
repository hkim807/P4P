"""Selection must prioritise valid first actions and preserve every scenario."""
from copy import deepcopy
import unittest

from scripts.evaluate_inference_variants import no_quality_loss, select_settings, percentile, validate_request, check_capacity
from scripts.profile_llm_baseline import profile_config
from app.domain.model_decision import model_decision_schema


class InferenceVariantsTests(unittest.TestCase):
    def setUp(self):
        self.control = {'trials':27,'aligned':24,'valid':27,'truncated':0,'median_s':3.,'p95_s':6.,
            'per_scenario_aligned':{f'S{i}':0 if i==4 else 3 for i in range(1,10)}}

    def candidate(self, **changes):
        return {**deepcopy(self.control), 'median_s':2., 'p95_s':5., **changes}

    def test_faster_valid_unchanged_actions_can_be_selected(self):
        self.assertEqual(select_settings({'control':self.control,'fast':self.candidate()}),'fast')

    def test_small_gain_bad_tail_invalid_and_truncation_are_rejected(self):
        for changes in [{'median_s':2.8},{'p95_s':6.1},{'valid':26},{'truncated':1},{'aligned':23}]:
            with self.subTest(changes=changes):
                self.assertEqual(select_settings({'control':self.control,'fast':self.candidate(**changes)}),'control')

    def test_equal_score_cannot_exchange_a_new_mistake_for_s4_fix(self):
        candidate=self.candidate()
        candidate['per_scenario_aligned'].update(S1=0,S4=3)
        self.assertFalse(no_quality_loss(self.control,candidate))

    def test_percentile_uses_all_trials(self):
        self.assertAlmostEqual(percentile([1,2,3,4,10],.95),8.8)

    def test_context_must_fit_whole_prompt_and_headroom(self):
        check_capacity(1000,{'num_ctx':2048,'num_predict':64})
        for tokens in [None,1800]:
            with self.subTest(tokens=tokens),self.assertRaises(ValueError):
                check_capacity(tokens,{'num_ctx':2048,'num_predict':64})

    def test_request_verification_requires_same_evidence_and_only_declared_settings(self):
        settings={'model':'example','base_url':'http://localhost:11434','num_ctx':2048,
            'num_predict':64,'temperature':0.,'seed':42,'timeout_seconds':120.}
        messages=[{'role':'system','content':'fixed'},{'role':'user','content':'same evidence'}]
        request={'model':'example','messages':messages,'stream':False,'format':model_decision_schema(),
            'options':profile_config(settings).generation_options()}
        validate_request(request,settings,messages)
        for key,value in [('messages',[{'role':'user','content':'different'}]),('options',{'temperature':.2}),('stream',True)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_request({**request,key:value},settings,messages)
