"""Rebuild aggregate survey chart options without modifying prior replay results."""
from pathlib import Path
import csv
import hashlib
import json
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0,str(Path.cwd()))
from evaluation.poster_survey import verify_workbook,ACTIONS,UNABLE
from evaluation.poster_report import table

out=Path(__file__).resolve().parent
source=Path('/home/anyone/Downloads/Human Evaluation of Robot Behaviour (Responses).xlsx')
r=verify_workbook(source)
r.pop('anonymous_rows')
(out/'human-reference.json').write_text(json.dumps(r,indent=2)+'\n')
preferences=[];ratings=[]
for sid,s in r['scenarios'].items():
 for a in (*ACTIONS,UNABLE):
  preferences.append({'scenario_id':sid,'action':a,'count':s['preferred_counts'][a],
   'valid_preference_n':s['valid_preference_n'],'proportion':s['preferred_counts'][a]/s['valid_preference_n'],
   'missing_n':s['missing_preference_n'],'unexpected_n':s['unexpected_preference_n']})
 for a in ACTIONS:
  x=s['ratings'][a]
  ratings.append({'scenario_id':sid,'action':a,'n':x['n'],'missing_or_nonrating':x['missing_or_nonrating'],
    'mean':x['mean'],'median':x['median'],**{f'rating_{k}_n':v for k,v in x['distribution'].items()}})
table(out/'human_preferences.csv',preferences);table(out/'human_ratings.csv',ratings)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':12,'axes.spines.top':False,'axes.spines.right':False,
 'svg.fonttype':'none','savefig.facecolor':'white'})
colors=['#2A788E','#E69F00','#8064A2','#D55E00','#B9BDC4']
fig,ax=plt.subplots(figsize=(10,6.5),layout='constrained')
left=np.zeros(9);y=np.arange(9)
for a,color in zip((*ACTIONS,UNABLE),colors):
 values=np.array([r['scenarios'][f'S{n}']['preferred_counts'][a]/42*100 for n in range(1,10)])
 ax.barh(y,values,left=left,color=color,height=.67,label='Unable to judge' if a==UNABLE else a)
 for i,v in enumerate(values):
  if v>=9:ax.text(left[i]+v/2,i,f'{v:.0f}%',ha='center',va='center',color='black' if a in ('APPROACH',UNABLE) else 'white',fontsize=10,fontweight='bold')
 left+=values
ax.set_yticks(y,[f'S{i}' for i in range(1,10)]);ax.invert_yaxis();ax.set_xlim(0,100)
ax.set_xlabel('Share of preference responses (%)')
ax.set_title('Which action did people prefer?',loc='left',fontweight='bold',pad=32)
ax.text(0,1.025,'42 respondents per scenario · Immediate next action at video end',transform=ax.transAxes,fontsize=11)
ax.legend(loc='lower center',bbox_to_anchor=(.5,-.22),ncol=5,frameon=False,fontsize=10)
for extension in ('png','svg'):fig.savefig(out/f'option-a-human-preferences.{extension}',dpi=180,bbox_inches='tight')
plt.close(fig)
fig,ax=plt.subplots(figsize=(8.5,6.5),layout='constrained')
values=np.array([[r['scenarios'][f'S{n}']['ratings'][a]['mean'] for a in ACTIONS] for n in range(1,10)])
im=ax.imshow(values,vmin=1,vmax=5,cmap='YlGnBu',aspect='auto')
ax.set_xticks(range(4),ACTIONS);ax.set_yticks(range(9),[f'S{i}' for i in range(1,10)])
for i in range(9):
 for j in range(4):ax.text(j,i,f'{values[i,j]:.2f}',ha='center',va='center',color='white' if values[i,j]>3.5 else '#17233A',fontweight='bold')
ax.set_title('How appropriate was each action?',loc='left',fontweight='bold',pad=32)
ax.text(0,1.03,'Mean survey rating, 1–5 · 39–42 valid ratings per cell',transform=ax.transAxes,fontsize=11)
fig.colorbar(im,ax=ax,label='Mean appropriateness rating',ticks=[1,2,3,4,5],shrink=.82)
for extension in ('png','svg'):fig.savefig(out/f'option-b-appropriateness.{extension}',dpi=180,bbox_inches='tight')
plt.close(fig)
replay=Path('var/evaluation/poster-20261011-final-first-output-01/summary.json')
summary=json.loads(replay.read_text())
ops=summary['operational']
for policy in ('rules','llm'):
 selected=[s for s in ops if s['policy']==policy]
 assert len(selected)==3 and len({(s['outputs'],s['accepted']) for s in selected})==1
fig,ax=plt.subplots(figsize=(7,4),layout='constrained')
x=np.arange(2);w=.32
for shift,key,label,color in ((-w/2,'outputs','Valid first output','#679FB8'),(w/2,'accepted','Client accepted','#153F57')):
 data=[next(s for s in ops if s['policy']==p)[key] for p in ('rules','llm')]
 bars=ax.bar(x+shift,data,w,label=label,color=color)
 for bar,v in zip(bars,data):ax.text(bar.get_x()+w/2,v+.12,f'{v}/9',ha='center',va='bottom',fontweight='bold')
ax.set_xticks(x,['Rules','LLM']);ax.set_ylim(0,10.8);ax.set_yticks([0,3,6,9]);ax.set_ylabel('Scenarios')
ax.set_title('First outputs and client acceptance',loc='left',fontweight='bold')
ax.legend(loc='upper center',bbox_to_anchor=(.5,-.12),ncol=2,frameon=False)
fig.text(.5,-.08,'Same counts in each of 3 repetitions · Recorded-speed replay · No physical execution',ha='center',fontsize=9)
for extension in ('png','svg'):fig.savefig(out/f'operational-coverage.{extension}',dpi=180,bbox_inches='tight')
plt.close(fig)
checks={'respondents':r['respondents'],'valid_preference_responses':sum(s['valid_preference_n'] for s in r['scenarios'].values()),
 'all_preference_denominators_42':all(s['valid_preference_n']==42 for s in r['scenarios'].values()),
 'missing_or_unexpected_preferences':sum(s['missing_preference_n']+s['unexpected_preference_n'] for s in r['scenarios'].values()),
 'missing_or_nonrating_cells':sum(x['missing_or_nonrating'] for s in r['scenarios'].values() for x in s['ratings'].values()),
 'preparation_checks':r['preparation_checks'],'workbook_sha256':r['workbook_sha256'],
 'workbook_matches_preparation_hash':r['matches_preparation_hash'],'workbook_unchanged':hashlib.sha256(source.read_bytes()).hexdigest()==r['workbook_sha256'],
 'replay_summary_sha256':hashlib.sha256(replay.read_bytes()).hexdigest(),
 'policy_actions_not_scored_against_video_end_ratings':True,'identifying_cells_or_free_text_exported':False,
 'build_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
assert all(r['preparation_checks'].values())
assert all(sum(s['preferred_counts'].values())==42 for s in r['scenarios'].values())
(out/'verification.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(checks,indent=2))
