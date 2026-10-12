import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.inference.ollama import OllamaResult
from app.domain.model_decision import ModelDecision
from app.replay.llm_inputs import iter_replay_states
from app.state.social_models import TemporalConfig
from app.state.tracks import TrackConfig
from evaluation.encounters import select_prior
from evaluation.poster_run import endpoint_rows


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items=list(iter_replay_states('var/evaluation/final-scenarios-audit/trimmed/final_scenario_2-sdk.jsonl',
            'sdk',temporal_config=TemporalConfig.from_file('config/temporal-development-frozen.json'),
            track_config=TrackConfig.from_file('config/person-tracking.json')))

    def scene(self, t):
        return {'scenario_id':'S2','endpoints':{'assumed_survey_video_end':t}}

    def test_selection_last_prior_not_last_ready_or_future(self):
        items=[SimpleNamespace(state=SimpleNamespace(robot_timestamp_us=t)) for t in (10,20,30)]
        self.assertIs(select_prior(items,25),items[1])
        self.assertIsNone(select_prior(items,9))
        self.assertIs(select_prior(items,20),items[1])

    def test_exact_state_five_serial_calls_and_no_human_scores(self):
        seen=[]
        class Client:
            config=SimpleNamespace(model='test')
            def chat(self,messages):
                seen.append(messages[1].content)
                if len(seen)==3:
                    return OllamaResult('test',None,None,.1,None,None)
                return OllamaResult('test','test','raw',.1,ModelDecision(action='CONTINUE',reason='Test evidence.'),None)
        rows=endpoint_rows(self.scene(self.items[-1].state.robot_timestamp_us),self.items,Client())
        self.assertEqual(len(rows),6)
        self.assertEqual(len(seen),5)
        self.assertEqual(len(set(seen)),1)
        self.assertEqual(len({r['state_sha256'] for r in rows}),1)
        self.assertEqual(sum(r['status']=='ERROR' for r in rows),1)
        self.assertTrue(all(r['preference_support'] is None for r in rows))

    def test_freshness_and_unready_avoid_model_calls(self):
        class Client:
            def chat(self,messages):
                raise AssertionError('ineligible model call')
        t=self.items[-1].state.robot_timestamp_us
        rows=endpoint_rows(self.scene(t+250001),self.items,Client())
        self.assertTrue(all(r['status']=='NOT_READY' for r in rows))
        rows=endpoint_rows(self.scene(self.items[0].state.robot_timestamp_us),self.items,Client())
        self.assertTrue(all(r['status']=='NOT_READY' for r in rows))
        rows=endpoint_rows(self.scene(t+250000),self.items,None)
        self.assertEqual(rows[0]['status'],'DECIDED')

if __name__=='__main__': unittest.main()

class WorkbookTests(unittest.TestCase):
    def rows(self):
        from evaluation.poster_survey import ACTIONS
        header=['Timestamp','Private comment']
        for n in range(1,10):
            header += [f'Scenario {n}: Which action should the robot take immediately?']
            header += [f'Scenario {n}: How appropriate is each action? [{a}]' for a in ACTIONS]
        def response(pref,ratings):
            return ['private timestamp','secret free text']+[v for _ in range(9) for v in [pref,*ratings]]
        return [header,response('CONTINUE',[5,None,3,2]),response('ENGAGE',[1,'unrated',4,2]),
                response('Unable to judge from the video',[3,True,5,2]),response('unexpected',[None,None,None,None]),
                response(None,[None,None,None,None])]

    def test_denominators_missingness_modal_ties_and_privacy(self):
        from evaluation.poster_survey import parse_rows
        result=parse_rows(self.rows()); s=result['scenarios']['S1']
        self.assertEqual(result['respondents'],5)
        self.assertEqual(s['valid_preference_n'],3)
        self.assertEqual(s['action_only_n'],2)
        self.assertEqual(s['unexpected_preference_n'],1)
        self.assertEqual(s['missing_preference_n'],1)
        self.assertEqual(set(s['modal_actions']),{'CONTINUE','ENGAGE'})
        self.assertEqual(s['ratings']['APPROACH']['n'],0)
        self.assertIsNone(s['ratings']['APPROACH']['mean'])
        self.assertEqual(s['ratings']['CONTINUE']['mean'],3)
        self.assertEqual(s['highest_mean_actions'],['ENGAGE'])
        self.assertNotIn('private timestamp',str(result))
        self.assertNotIn('secret free text',str(result))
        self.assertNotIn('unexpected',str(result['anonymous_rows']))

    def test_proxy_and_first_action_cannot_get_survey_scores(self):
        from evaluation.poster_survey import parse_rows,score
        s=parse_rows(self.rows())['scenarios']['S1']
        for condition in ('camera_end_proxy','sdk_end_diagnostic','first_action'):
            self.assertIsNone(score('CONTINUE',s,condition=condition,mapping_status='ASSUMED_BY_USER'))
        result=score('CONTINUE',s,condition='assumed_survey_video_end',mapping_status='ASSUMED_BY_USER')
        self.assertAlmostEqual(result['preference_support'],1/3)
        self.assertEqual(result['rating_n'],3)
        self.assertIsNone(score(None,s,condition='assumed_survey_video_end',mapping_status='ASSUMED_BY_USER'))

class ReportingTests(unittest.TestCase):
    def test_repeat_aggregation_excludes_failures_without_fabrication(self):
        from evaluation.poster_report import paired_difference
        from statistics import mean
        self.assertIsNone(paired_difference(None,[1.,1.]))
        self.assertIsNone(paired_difference(.5,[None,None]))
        first=paired_difference(.5,[1.,None,None,None,None])
        second=paired_difference(.5,[0.,0.,0.,0.,0.])
        self.assertEqual(first,.5)
        self.assertEqual(mean([first,second]),0.)

    def test_workbook_xml_uses_named_sheet_and_full_headers(self):
        from evaluation.poster_survey import workbook_rows,parse_rows
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from zipfile import ZipFile
        from xml.sax.saxutils import escape
        rows=WorkbookTests().rows()
        def col(i):
            s=''
            while i:
                i,r=divmod(i-1,26);s=chr(65+r)+s
            return s
        xml=[]
        for i,row in enumerate(rows,1):
            cells=[]
            for j,v in enumerate(row,1):
                if v is None: continue
                address=col(j)+str(i)
                if isinstance(v,str): cells.append(f'<c r="{address}" t="inlineStr"><is><t>{escape(v)}</t></is></c>')
                elif isinstance(v,bool): cells.append(f'<c r="{address}" t="b"><v>1</v></c>')
                else: cells.append(f'<c r="{address}"><v>{v}</v></c>')
            xml.append(f'<row r="{i}">'+''.join(cells)+'</row>')
        with TemporaryDirectory() as d:
            p=Path(d)/'survey.xlsx'
            with ZipFile(p,'w') as z:
                z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Form responses 1" r:id="r1"/></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
                z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(xml)+'</sheetData></worksheet>')
            result=parse_rows(workbook_rows(p))
            self.assertEqual(result['respondents'],5)
            self.assertEqual(result['scenarios']['S9']['ratings']['APPROACH']['n'],0)

class IntegrityTests(unittest.TestCase):
    def test_only_disclosed_reporting_helper_change_is_permitted(self):
        from evaluation.poster_report import validate_execution_inputs
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import json
        manifest={'frozen_sha256':{'app/policy/rules.py':'fixed','evaluation/poster_survey.py':'old'},'design_sha256':{}}
        with TemporaryDirectory() as d:
            p=Path(d);(p/'manifest.json').write_text(json.dumps(manifest))
            with patch('evaluation.poster_report.sha256',side_effect=lambda p: 'fixed' if p=='app/policy/rules.py' else 'new'):
                result=validate_execution_inputs(p)
                self.assertEqual(set(result['_reporting_only_changes']),{'evaluation/poster_survey.py'})
            with patch('evaluation.poster_report.sha256',return_value='changed'):
                with self.assertRaises(ValueError):validate_execution_inputs(p)
