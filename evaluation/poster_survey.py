"""Workbook-only survey extraction using stdlib OOXML; no personal cells exported."""
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from statistics import mean, median
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ACTIONS=('CONTINUE','APPROACH','ENGAGE','YIELD')
UNABLE='Unable to judge from the video'
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def workbook_rows(path, sheet_name='Form responses 1'):
    with ZipFile(path) as z:
        book=ET.fromstring(z.read('xl/workbook.xml'))
        sheet=next(s for s in book.find('s:sheets',NS) if s.attrib['name']==sheet_name)
        rel_id=sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target=next(r.attrib['Target'] for r in rels if r.attrib['Id']==rel_id)
        target=target.lstrip('/') if target.startswith('/') else str(PurePosixPath('xl')/target)
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            for si in ET.fromstring(z.read('xl/sharedStrings.xml')):
                strings.append(''.join(t.text or '' for t in si.iter() if t.tag.endswith('}t')))
        rows=[]
        for row in ET.fromstring(z.read(target)).findall('.//s:sheetData/s:row',NS):
            values={}
            for cell in row:
                letters=re.match(r'[A-Z]+',cell.attrib['r'])[0]
                col=0
                for c in letters: col=26*col+ord(c)-ord('A')+1
                v=cell.find('s:v',NS)
                value=None if v is None else v.text
                typ=cell.attrib.get('t')
                if typ=='s': value=strings[int(value)] if value is not None else None
                elif typ=='inlineStr': value=''.join(t.text or '' for t in cell.iter() if t.tag.endswith('}t'))
                elif value is not None and typ not in ('str','e','b'):
                    value=float(value)
                values[col-1]=value
            rows.append(values)
        width=max((max(r,default=-1) for r in rows),default=-1)+1
        return [[r.get(c) for c in range(width)] for r in rows]


def parse_rows(rows):
    headers=rows[0]
    columns={}
    for n in range(1,10):
        pref=[i for i,h in enumerate(headers) if isinstance(h,str) and
              re.match(rf'^Scenario\s+{n}:\s*Which action\b',h)]
        if len(pref)!=1: raise ValueError(f'S{n}: expected one full preferred-action question')
        ratings={}
        for action in ACTIONS:
            found=[i for i,h in enumerate(headers) if isinstance(h,str) and
                re.match(rf'^Scenario\s+{n}:\s*How appropriate\b',h) and h.rstrip().endswith('['+action+']')]
            if len(found)!=1: raise ValueError(f'S{n}/{action}: expected one full appropriateness question')
            ratings[action]=found[0]
        columns[f'S{n}']={'preference':pref[0],'ratings':ratings}
    # Count submitted rows even when every scored question is missing. Personal
    # cells can establish a nonempty row but are never copied into the output.
    responses=[r for r in rows[1:] if any(v not in (None,'') for v in r)]
    result={'status':'VERIFIED_WORKBOOK_ONLY','respondents':len(responses),'scenarios':{},
            'protocol_verification':'NOT_PERFORMED','anonymous_rows':[],
            'denominator':'valid action preferences plus unable-to-judge; missing/unexpected excluded'}
    for index,r in enumerate(responses,1):
        anon={'row_id':index,'scenarios':{}}
        for sid,cols in columns.items():
            p=r[cols['preference']] if cols['preference']<len(r) else None
            if isinstance(p,str): p=p.strip()
            ratings={}
            for a,c in cols['ratings'].items():
                v=r[c] if c<len(r) else None
                ratings[a]=int(v) if type(v) in (int,float) and 1<=v<=5 and int(v)==v else None
            anon['scenarios'][sid]={'preference':p if p in (*ACTIONS,UNABLE) else None,
                'preference_status':'VALID' if p in (*ACTIONS,UNABLE) else 'MISSING' if p in (None,'') else 'UNEXPECTED',
                'ratings':ratings}
        result['anonymous_rows'].append(anon)
    for sid,cols in columns.items():
        data=[r['scenarios'][sid] for r in result['anonymous_rows']]
        counts=Counter(r['preference'] for r in data if r['preference_status']=='VALID')
        valid=sum(counts.values()); only=sum(counts[a] for a in ACTIONS)
        stats={}
        for a in ACTIONS:
            ratings=[r['ratings'][a] for r in data if r['ratings'][a] is not None]
            stats[a]={'n':len(ratings),'missing_or_nonrating':len(data)-len(ratings),
                      'mean':mean(ratings) if ratings else None,'median':median(ratings) if ratings else None,
                      'distribution':{str(i):ratings.count(i) for i in range(1,6)}}
        maximum=max((counts[a] for a in ACTIONS),default=0)
        best=max((v['mean'] for v in stats.values() if v['mean'] is not None),default=None)
        result['scenarios'][sid]={'respondents':len(data),'preferred_counts':{a:counts[a] for a in (*ACTIONS,UNABLE)},
            'valid_preference_n':valid,'action_only_n':only,
            'missing_preference_n':sum(r['preference_status']=='MISSING' for r in data),
            'unexpected_preference_n':sum(r['preference_status']=='UNEXPECTED' for r in data),
            'modal_actions':[a for a in ACTIONS if counts[a]==maximum] if maximum else [],
            'highest_mean_actions':[a for a,v in stats.items() if v['mean']==best] if best is not None else [],
            'ratings':stats,'question_columns':{'preference':headers[cols['preference']],
                 'ratings':{a:headers[c] for a,c in cols['ratings'].items()}}}
    return result


def verify_workbook(path):
    before=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    result=parse_rows(workbook_rows(path))
    result['workbook_sha256']=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert result['workbook_sha256']==before, 'Workbook changed during verification'
    result['matches_preparation_hash']=before=='0f9ee6940c8c1687d38c00565e1f0e46b44e4b3fcafea960be1e4aa3640be0f2'
    result['sheet']='Form responses 1'
    result['historical_reference_respondents']=37
    result['respondent_discrepancy_vs_historical']=result['respondents']-37
    for s in result['scenarios'].values():
        assert s['valid_preference_n']+s['missing_preference_n']+s['unexpected_preference_n']==result['respondents']
        for r in s['ratings'].values():
            assert sum(r['distribution'].values())==r['n']
            assert r['n']+r['missing_or_nonrating']==result['respondents']
    result['preparation_checks']={
        '42_respondents':result['respondents']==42,
        'S1_CONTINUE_35':result['scenarios']['S1']['preferred_counts']['CONTINUE']==35,
        'S4_YIELD_38':result['scenarios']['S4']['preferred_counts']['YIELD']==38,
        'S3_YIELD_18':result['scenarios']['S3']['preferred_counts']['YIELD']==18,
        'S3_ENGAGE_16':result['scenarios']['S3']['preferred_counts']['ENGAGE']==16,
        'S3_ENGAGE_rating_above_YIELD':(result['scenarios']['S3']['ratings']['ENGAGE']['mean'] or -1)>
                                     (result['scenarios']['S3']['ratings']['YIELD']['mean'] or -1)}
    return result


def score(action, scenario, *, condition, mapping_status):
    if condition!='assumed_survey_video_end' or mapping_status!='ASSUMED_BY_USER' or action not in ACTIONS:
        return None
    n=scenario['valid_preference_n']; rating=scenario['ratings'][action]
    return {'preference_support':scenario['preferred_counts'][action]/n if n else None,
            'appropriateness':rating['mean'],'rating_n':rating['n'],'rating_distribution':rating['distribution'],
            'modal_agreement':action in scenario['modal_actions'] if scenario['modal_actions'] else None,
            'score_eligibility_reason':'CONDITIONAL_ON_USER_ASSUMED_CLIP_MAPPING'}
