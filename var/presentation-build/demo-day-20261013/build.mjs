import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {Presentation,PresentationFile} from '@oai/artifact-tool';

const ROOT='/Users/eddiekim/Documents/Projects/P4P';
const BUILD=ROOT+'/var/presentation-build/demo-day-20261013';
const SKILL='/Users/eddiekim/.codex/plugins/cache/openai-primary-runtime/presentations/26.909.12148/skills/presentations';
const PY='/Users/eddiekim/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3';
const FINAL=ROOT+'/output/presentations/Navel_Demo_Day_Final.pptx';
process.env.RUNTIME_NODE_MODULES='/Users/eddiekim/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const {makeNativeBulletParagraphs,applyPresentationChartFont,finalizePresentation}=await import(pathToFileURL(SKILL+'/container_tools/artifact_tool_utils.mjs').href);
const D=JSON.parse(await fs.readFile(BUILD+'/data.json','utf8'));
const P=Presentation.create({slideSize:{width:1280,height:720}});
const C={blue:'#22577A',ink:'#26333C',pale:'#DDECF2',cream:'#F3F0E7',shadow:'#AFC4CC',cyan:'#78B9D1',mid:'#437E9D',white:'#FFFFFF'};
const slides=[], tableOwners=[],chartOwners=[];
const sources={base:'var/evaluation/llm-refinement-stage-1-2/REPORT.md',s3:'var/evaluation/llm-refinement-stage-3/REPORT.md',s4:'var/evaluation/llm-refinement-stage-4/REPORT.md',s5:'var/evaluation/llm-refinement-stage-5/REPORT.md',s6:'var/evaluation/llm-refinement-stage-6/REPORT.md',final:'var/evaluation/llm-refinement-stage-7-8/REPORT.md',fit:'app/state/features.py',est:'app/state/estimator.py',track:'app/state/tracks.py',lock:'app/policy/target_lock.py',rules:'app/policy/rules.py',state:'app/state/decision_models.py',prompt:'app/policy/llm.py',trial:'robot/navel_client/single_trial.py',tc:'config/temporal-final-scenarios.json',pc:'config/person-tracking-final-scenarios.json',lc:'config/target-lock.json',human:'config/human-reference.json'};

function shape(s,x,y,w,h,fill=C.pale,line='none',radius=0,name=''){
 return s.shapes.add({geometry:radius?'roundRect':'rect',name,position:{left:x,top:y,width:w,height:h},fill,line:{fill:line,width:line==='none'?0:1.3},...(radius?{borderRadius:radius}:{})});
}
function text(s,content,x,y,w,h,size=27,bold=false,color=C.ink,font='Noto Sans',align='left'){
 const b=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 b.text=content;b.text.style={typeface:font,fontSize:size,bold,color,alignment:align,verticalAlignment:'top',autoFit:'none',wrap:'square',insets:{top:0,bottom:0,left:0,right:0}};return b;
}
function bullets(s,items,x,y,w,h,size=27){
 const b=text(s,'',x,y,w,h,size);b.text=makeNativeBulletParagraphs(items,{marginLeftPoints:17,hangingPoints:9,spaceAfterPoints:7});return b;
}
function box(s,label,x,y,w,h,{fill=C.pale,size=25,bold=false,color=C.blue}={}){
 const b=shape(s,x,y,w,h,fill,'none',10,label);b.text=label;b.text.style={typeface:'Noto Sans',fontSize:size,bold,color,alignment:'center',verticalAlignment:'middle',autoFit:'none',insets:{top:8,bottom:8,left:10,right:10}};return b;
}
function arrow(s,a,b,from='right',to='left'){
 return s.shapes.connect(a,b,{kind:'elbow',fromSide:from,toSide:to,line:{fill:C.blue,width:2},tail:{type:'arrow',width:'med',length:'med'}});
}
function note(s,t){text(s,t,60,615,1160,45,18,false,C.mid);}
function ph(s,label,x,y,w,h,sub='Replace with your photograph'){
 const b=shape(s,x,y,w,h,C.cream,C.shadow,12,'Placeholder: '+label);b.line={fill:C.shadow,width:1.5,style:'dashed'};
 if(h<115){text(s,'PHOTO PLACEHOLDER: '+label,x+18,y+15,w-36,h-20,21,true,C.blue,'Rubik','center');return b;}
 text(s,label,x+20,y+h/2-42,w-40,48,27,true,C.blue,'Rubik','center');text(s,sub,x+22,y+h/2+12,w-44,60,20,false,C.mid,'Noto Sans','center');return b;
}
function add(title,{appendix=false,seconds=0,notes='',src=[]}={}){
 const s=P.slides.add();s.background.fill=C.white;slides.push(s);
 const n=slides.length;
 shape(s,65,45,1155,72,C.shadow,'none',10);shape(s,60,40,1155,72,C.blue,'none',10);
 text(s,title,82,51,1110,57,appendix?40:44,true,C.white,'Rubik');
 text(s,appendix?'Appendix '+String(n-7).padStart(2,'0'):'Project 59',60,679,400,22,15,false,C.blue);
 text(s,String(n),1160,675,55,26,17,false,C.blue,'Noto Sans','right');
 const refs=src.map(p=>p.startsWith('/')?p:ROOT+'/'+p);
 s.speakerNotes.textFrame.setText((seconds?`Presentation time: ${seconds} seconds.\n\n`:'Appendix for questions.\n\n')+notes+'\n\nSources:\n'+refs.join('\n'));
 return s;
}
function table(s,headers,rows,{x=60,y=170,w=1155,h=400,widths,font=23,headerFont=23}={}){
 const vals=[headers,...rows].map(row=>row.map(String));
 const t=s.tables.add({rows:vals.length,columns:headers.length,left:x,top:y,width:w,height:h,values:vals,...(widths?{columnWidths:widths}:{})});
 t.borders.assign({fill:C.white,width:1.7,style:'solid'});
 for(let r=0;r<vals.length;r++)for(let c=0;c<headers.length;c++){
  t.cells.block({row:r,column:c,rowCount:1,columnCount:1}).assign({fill:r===0?C.blue:(r%2?C.pale:C.cream),textStyle:{typeface:'Noto Sans',fontSize:r===0?headerFont:font,color:r===0?C.white:C.ink,bold:r===0},margins:{top:3,bottom:3,left:10,right:10}});
 }
 tableOwners.push(slides.length);return t;
}
function chart(s,type,config){
 const ch=s.charts.add(type,{chartFill:C.white,plotAreaFill:C.white,chartLine:{fill:'none',width:0},...config});
 applyPresentationChartFont(ch,{fontFamily:'Noto Sans'});chartOwners.push(slides.length);return ch;
}
const ax={textStyle:{typeface:'Noto Sans',fontSize:20,fill:C.ink},line:{fill:C.shadow,width:1},majorGridlines:{fill:C.pale,width:1}};
function bars(s,cats,vals,{x=60,y=190,w=700,h=365,max=9,unit='',color=C.blue}={}){
 return chart(s,'bar',{position:{left:x,top:y,width:w,height:h},categories:cats,series:[{name:unit||'Value',values:vals,fill:color}],hasLegend:false,
  barOptions:{direction:'column',grouping:'clustered',gapWidth:90},xAxis:{...ax,majorGridlines:null},yAxis:{...ax,min:0,max,majorUnit:max===9?3:undefined,numberFormatCode:unit==='seconds'?'0.0"s"':'0'},
  dataLabels:{showValue:true,position:'outEnd',textStyle:{typeface:'Noto Sans',fontSize:25,bold:true,fill:C.blue}}});
}

// 1: integrated cover, problem and objectives.
{
 const s=P.slides.add();s.background.fill=C.white;slides.push(s);
 text(s,'Project 59',60,42,690,40,29,false,C.blue,'Rubik');
 text(s,'Interactive Social\nGuide Robot',60,103,735,190,66,true,C.ink,'Rubik');
 text(s,'Decision-making with shared social evidence',62,298,690,72,31,false,C.blue,'Rubik');
 text(s,'A robot on a laboratory route must decide\nwhen to interact and when to make space.',62,392,690,92,29);
 const actions=['CONTINUE','APPROACH','ENGAGE','YIELD'];
 actions.forEach((a,i)=>box(s,a,62+i*176,510,163,59,{size:21,bold:true,fill:i%2?C.cream:C.pale}));
 const img=await fs.readFile(BUILD+'/image1.jpeg');s.images.add({blob:img,contentType:'image/jpeg',fit:'contain',alt:'Navel robot supplied in the poster',position:{left:820,top:86,width:355,height:520}});
 ph(s,'LAB PHOTO',62,589,685,61,'Replace with a wide laboratory photograph');
 text(s,'Eddie Kim and Jeruh Kim',60,669,660,25,18,false,C.blue);
 s.images.add({blob:await fs.readFile(BUILD+'/image2.png'),contentType:'image/png',alt:'University of Auckland logo from the poster',fit:'contain',position:{left:1000,top:646,width:215,height:59}});
 s.speakerNotes.textFrame.setText('Presentation time: 35 seconds.\nIntroduce the team. Navel follows a fixed route in a laboratory and must select one of four social-navigation actions. Changing gaze, distance and missing detections make single observations insufficient. Our objective is an integrated temporal representation and a fair comparison of rule-based and LLM decisions.\n\nSources:\n/Users/eddiekim/Downloads/Poster_59.pptx\n'+ROOT+'/docs/llm-refinement-summary.md');
}
// 2: questions and study boundaries.
{
 const s=add('Research Questions and Evaluation',{seconds:40,src:['/Users/eddiekim/Downloads/Poster_59.pptx',sources.human,'docs/pipeline-refinement.md','config/final-scenarios.json'],notes:'RQ1 asks how gaze and distance observations over time can be structured into a shared social state. RQ2 asks about alignment with human preferences and appropriateness ratings. The survey judges the immediate action at a video endpoint. The latest tuning study instead scores the first eligible action. Do not equate its 9/9 and 8/9 benchmark scores with survey-endpoint human accuracy. The available verified historical reference has 37 responses, while a 42-response update is unverified. Main slides therefore omit a participant count.'});
 text(s,'RQ1',60,154,110,50,38,true,C.blue,'Rubik');text(s,'How can gaze and distance over time become\nshared evidence for both decision policies?',185,154,1020,89,30);
 text(s,'RQ2',60,278,110,50,38,true,C.blue,'Rubik');text(s,'How do their decisions compare with human\npreferences and appropriateness ratings?',185,278,1020,89,30);
 const a=box(s,'Human video judgments',60,418,345,93,{fill:C.cream,size:26,bold:true});const b=box(s,'Laboratory sensor recordings',456,418,345,93,{size:25,bold:true});const c=box(s,'Paired policy evaluation',855,418,360,93,{fill:C.cream,size:26,bold:true});arrow(s,a,b);arrow(s,b,c);
 text(s,'Preferred action and\nappropriateness ratings',65,532,335,63,22);text(s,'Nine controlled scenarios\nusing real Navel observations',460,532,338,63,22);text(s,'First eligible action\nand time to complete output',862,532,340,63,22);
 note(s,'Current scores describe expected first actions in tuning recordings. Survey-endpoint alignment remains separate.');
}
// 3: editable architecture.
{
 const s=add('Shared Temporal Social-State Pipeline',{seconds:60,src:['docs/architecture.md',sources.state,sources.trial,'docs/target-lock.md'],notes:'Sensors stream person detections, gaze, distance, nose geometry and robot motion through HTTP. The server maintains histories and derives temporal cues and validity. Full SocialState retains diagnostics. Both policies receive the same compact DecisionState. A selected policy produces action and reason. Trial correlation, readiness and freshness govern delivery. Native execution is opt-in. Continuous rule commands use a separate target-lock flow, detailed in the appendix. The reported replay trials instantiate no robot executor.'});
 const a=box(s,'Navel sensors\nGaze, distance, motion',60,170,250,105,{size:23,bold:true});
 const b=box(s,'HTTP receiver\nValidate and record',360,170,250,105,{fill:C.cream,size:23,bold:true});
 const c=box(s,'Track history\nTime and evidence quality',660,170,280,105,{size:23,bold:true});
 const d=box(s,'Full SocialState\nDiagnostic record',990,170,225,105,{fill:C.cream,size:24,bold:true});arrow(s,a,b);arrow(s,b,c);arrow(s,c,d);
 const compact=box(s,'DecisionState\nShared compact input',930,363,285,106,{size:23,bold:true});arrow(s,d,compact,'bottom','top');
 const rule=box(s,'Rule policy',620,330,245,76,{fill:C.cream,size:27,bold:true});const llm=box(s,'LLM policy',620,456,245,76,{fill:C.cream,size:27,bold:true});arrow(s,compact,rule,'left','right');arrow(s,compact,llm,'left','right');
 const checks=box(s,'Selected action\nOutput and freshness checks',300,380,255,106,{size:24,bold:true});arrow(s,rule,checks,'left','right');arrow(s,llm,checks,'left','right');
 const exec=box(s,'Robot action\nOpt-in handler',60,380,185,106,{fill:C.cream,size:22,bold:true});arrow(s,checks,exec,'left','right');
 text(s,'Current gaze',65,563,225,34,23,true,C.blue);text(s,'Looking history',300,563,225,34,23,true,C.blue);text(s,'Relative separation',550,563,285,34,23,true,C.blue);text(s,'Validity and position',890,563,325,34,23,true,C.blue);
 note(s,'Both policies share measured evidence. Unknown cues remain explicit. Physical execution is opt-in.');
}
// 4: rule refinement.
{
 const s=add('Rule-Based Decision Policy',{seconds:40,src:[sources.rules,sources.tc,'docs/social-policy.md'],notes:'The initial design used gaze and proximity conditions. Refinements calibrated distance-dependent gaze thresholds, kept time and missing-data boundaries, and added forward-corridor plus closing evidence to the low-gaze YIELD condition. The final preset disables recurring intermittent attention as an interaction trigger. These are simplified examples, not the full precedence. The final rule matches all nine expected first actions on this tuning set.'});
 table(s,['Observed evidence','Action'],[['Sustained looking, farther away','APPROACH'],['Sustained looking, already nearby','ENGAGE'],['Closing inside corridor, low looking','YIELD'],['Other eligible cases','CONTINUE']],{y:160,h:302,widths:[865,290],font:27,headerFont:26});
 text(s,'Refinements',65,489,660,44,31,true,C.blue,'Rubik');bullets(s,['Temporal gaze and gap handling','Distance-aware thresholds','Corridor and closing evidence'],65,540,725,115,24);
 text(s,'9/9',936,500,250,80,66,true,C.blue,'Rubik','center');text(s,'expected first actions',864,585,340,36,23,false,C.ink,'Noto Sans','center');
}
// 5: main LLM findings.
{
 const s=add('Compact Evidence Improved LLM Performance',{seconds:45,src:[sources.base,sources.s3,sources.s4,sources.s5,sources.final],notes:'The initial LLM received the full SocialState including configuration, raw geometry and unused diagnostics. Compact projection was the largest improvement: 6/9 to 8/9 and 6.21 seconds to 2.76 seconds in stage 3. Current semantic descriptions and the v16 instructions retain 8/9, with the latest fresh model control median 3.20 seconds. Stage 3 is the faster historical reference, and richer labels have no demonstrated accuracy gain. Timings are warm local runs, not randomized causal estimates. The final selection is Qwen 2.5 7B, DecisionState v3 and v16.'});
 table(s,['First decision','Initial','Final selection'],[['Expected-action agreement','6/9','8/9'],['Median output delay','6.21s','3.20s'],['Median input tokens','1,766','917']],{y:169,h:264,widths:[595,280,280],font:28,headerFont:25});
 text(s,'What we retained',65,468,520,44,31,true,C.blue,'Rubik');bullets(s,['Compact shared evidence','Descriptive labels and clear instructions'],65,524,700,93,26);
 text(s,'Qwen 2.5 7B',862,498,350,53,34,true,C.blue,'Rubik','center');text(s,'DecisionState v3\nInstructions v16',862,557,350,67,25,false,C.ink,'Noto Sans','center');
 note(s,'Delay starts at the first eligible observation. Richer labels did not improve the 8/9 score.');
}
// 6: benchmark and unresolved case.
{
 const s=add('First-Action Accuracy and Decision Delay',{seconds:50,src:[sources.final,'var/evaluation/llm-refinement-stage-7-8/JUDGEMENT-REVIEW.md'],notes:'Both policies use identical evidence. Rules match 9/9 expected first actions; Qwen 2.5 7B matches 8/9 in all three repetitions. The LLM median is 3.20 seconds after eligibility, with 95th percentile 6.34 seconds. Median motion-to-output is 4.31 seconds in these recordings. S4 remains wrong: the same in-corridor, closing, low-gaze evidence leads rules to YIELD and the LLM to CONTINUE. Current scores use nine tuning recordings, not held-out human alignment. Readiness and freshness checks can reject a syntactically valid output.'});
 bars(s,['Rules','Selected LLM'],[9,8],{x:60,y:154,w:540,h:370,max:9});
 text(s,'Expected first actions matched',60,528,540,36,23,true,C.blue,'Noto Sans','center');
 ph(s,'S4 SCENARIO PHOTO',675,155,540,200,'Insert the closing encounter inside the forward corridor');
 table(s,['Same S4 evidence','First action'],[['Rules','YIELD'],['Selected LLM','CONTINUE']],{x:675,y:382,w:540,h:157,widths:[340,200],font:26});
 text(s,'LLM median 3.20s',678,565,540,36,29,true,C.blue,'Rubik');
 note(s,'Three repetitions on nine tuning recordings. LLM 95th percentile: 6.34s after eligibility.');
}
// 7: timed demonstration and close.
{
 const s=add('Robot Behaviour Demonstration',{seconds:30,src:['docs/robot-decision-dry-run.md','docs/physical-executor.md'],notes:'Play approximately 15 seconds of the prepared montage, then deliver the closing contribution. Use captions that distinguish scripted action execution from autonomous policy selection. Four short shots can show route progress, approach, greeting and giving space. These action demonstrations do not turn recorded inference results into measured physical execution rates. Closing: We developed a shared temporal social-state pipeline and a repeatable way to compare decision quality and delivery time. Compact input substantially improved the LLM, while the tuned rules remained more reliable on tested first decisions.'});
 ph(s,'VIDEO PLACEHOLDER',60,157,1155,328,'15-second montage: route following, approach, greeting and giving space');
 text(s,'Shared evidence made comparison possible',65,522,1120,45,34,true,C.blue,'Rubik');
 text(s,'Compact input improved LLM decisions and delay.\nThe tuned rule policy remained more reliable on tested first decisions.',65,576,1130,72,26);
}

// Appendix 1: question navigation.
{
 const s=add('Appendix: Technical Details and Evidence',{appendix:true,notes:'The five-minute presentation ends at slide 7. Use the following slides selectively for questions. All numerical results refer to their frozen experiment and timing origin.'});
 table(s,['Topic','Slides'],[['Evaluation and human reference','9 to 11'],['Sensors, history and cue calculations','12 to 18'],['Rule policy and target locking','19 to 23'],['Shared input and LLM instructions','24 to 26 and 36'],['Refinement results and model comparison','27 to 32 and 35'],['Final configuration and research limits','33 to 34']],{y:166,h:425,widths:[840,315],font:27});
 note(s,'Tables, figures and diagrams are editable. Photo and video frames are labelled placeholders.');
}
// 9: causal scoring contract and all scenarios.
{
 const s=add('Frozen First-Decision Evaluation',{appendix:true,src:['config/final-scenarios.json',sources.base,sources.final],notes:'Exactly nine motion-trimmed SDK recordings are the tuning set. Freeze the first eligible source snapshot and unchanged allowed-action lists. Invalid/missing first outputs count as failure. Later calls use separate frozen last-eligible points and cannot replace the first result. Three deterministic repeats are repeated measurements of the same nine scenarios. S8 resolves while the observed scene is empty; it does not demonstrate later gaze or departure recognition. S3 and S8 allow several actions.'});
 const rows=D.rows['qwen25-7b'].filter(r=>r.repetition===1);
 table(s,['Case','Allowed first actions','Rules','LLM','Eligible after motion'],rows.map(r=>[r.scenario_id,r.expected_actions.join(' / '),r.scenario_id==='S4'?'YIELD':r.first_decision.action,r.first_decision.action,r.first_eligible_since_motion_s.toFixed(2)+'s']),{y:152,h:440,widths:[105,395,175,175,305],font:22,headerFont:22});
 note(s,'S8 first uses an empty scene. These expected-action labels are evaluator metadata and never enter policy input.');
}
// 10: survey reference scope without fabricated current scores.
{
 const s=add('Human Reference: Available Evidence',{appendix:true,src:[sources.human,'docs/results/pipeline-refinement/reference-verification.json','var/evaluation/poster-20261011-final-first-output-01/human-reference.json','docs/pipeline-refinement.md'],notes:'The historical source reference verifies 37 responses per scenario, with independent action-rating denominators 34 to 37. A later 42-response workbook is expected but unavailable/unverified in the stored run. The historical example below deliberately demonstrates preference/rating disagreement rather than claiming current human accuracy. S3 has YIELD 18 votes and ENGAGE 12 votes, while mean appropriateness is higher for ENGAGE. Survey clip trim-to-SDK endpoint mappings are unverified, so direct RQ2 alignment remains unresolved. Replace/update the survey figure after verifying the final dataset and endpoint mapping.'});
 table(s,['Reference','Status'],[['Historical source','37 responses per scenario, verified'],['Later workbook update','42 expected, source unavailable/unverified'],['Survey decision point','Immediate next action at clip end'],['Operational decision point','First eligible observation'],['Direct survey-end scoring','Clip-to-sensor mapping unverified']],{y:155,h:292,widths:[410,745],font:24});
 table(s,['Historical S3 example','YIELD','ENGAGE'],[['Preferred-action votes','18','12'],['Mean appropriateness','3.57','3.78']],{y:480,h:115,widths:[595,280,280],font:24});
 note(s,'Human preference and mean appropriateness can disagree. Current 9/9 and 8/9 scores use expected first actions.');
}
// 11: metrics and time origins.
{
 const s=add('Metric Definitions and Timing Origins',{appendix:true,src:[sources.base,sources.final,'scripts/profile_llm_baseline.py'],notes:'Action agreement means the first action belongs to the frozen allowed set. Strict validity means the parser accepts the enum and nonblank reason, not semantic correctness. Client acceptance additionally checks correlation, readiness and freshness. Time to complete observed output starts at first eligibility; motion-to-output includes the evidence wait. Components have independent medians. The latest 7B run has 3.20-second output median, 6.34-second 95th percentile, 1.17-second median eligibility after motion and 4.31-second measured motion-to-output median. These are local replay timings, excluding physical LAN and action execution.'});
 table(s,['Measure','Definition'],[['First-action agreement','Action belongs to the unchanged expected set'],['Output validity','Strict action enum and nonblank reason'],['Live-client acceptance','Correlation, current readiness and freshness'],['Eligibility to output','First eligible observation to complete observed output'],['Motion to output','Recorded motion onset to complete observed output'],['Later diagnosis','Separate frozen observation, excluded from primary score']],{y:154,h:402,widths:[380,775],font:24});
 text(s,'3.20s median',65,575,345,38,30,true,C.blue,'Rubik');text(s,'6.34s 95th percentile',455,575,430,38,30,true,C.blue,'Rubik');
 note(s,'Timing origin: after eligibility. Recorded motion-to-output median: 4.31s.');
}
// 12: source mapping.
{
 const s=add('Navel Sensor Inputs and Mapping',{appendix:true,src:['docs/raw-observation-frame.md','docs/navel-supported-cues.md','robot/navel_client/adapter.py'],notes:'Use actual SDK perception and latest prior locomotion packets. dist_mm is converted to metres. Raw score is gaze overlap, not interest probability. A finite positive-size face bounding box supplies face detection. Nose geometry selects finite nonzero CAM_HEAD points; never relabel another frame. Latest locomotion supplies measured forward/yaw velocities. Missing/invalid/stale values remain unavailable. Unsupported path relation/pass gesture measurements have been removed. Raw safety range units remain unverified and do not enter the compact LLM social evidence. HTTP keeps the newest pending frame, with at most ten POST starts per second by default.'});
 table(s,['SDK observation','Shared evidence','Interpretation'],[['Person UID','Track identity and epoch','Bookkeeping outside LLM text'],['dist_mm','distance_m = dist_mm / 1000','Measured robot-person range'],['gaze_overlap','Latest gaze score','Thresholded measurement'],['Face bounding box','face_detected','Face detection only'],['g_nose in CAM_HEAD','Corridor position','Fixed-head position estimate'],['Forward and yaw velocity','Robot motion state','Translation or rotation'],['Missing or invalid values','null / UNKNOWN','Unavailable evidence']],{y:157,h:420,widths:[330,410,415],font:23});
 note(s,'HTTP POST /api/v1/observations. No measured pass gesture or path-relation input is available from Navel.');
}
// 13: track lifecycle and limits.
{
 const s=add('Person History and Detection Continuity',{appendix:true,src:[sources.track,sources.pc,'docs/person-tracking.md'],notes:'Identify histories by session, SDK UID and track epoch. Missing grace 0.75 seconds is distinct from the target lock hold of two seconds. A return after expiry starts a new epoch. The final preset enables a limited nose-based cross-UID continuity heuristic only for one retained/current person in adjacent frames with finite nonzero CAM_HEAD positions, distance at most 0.45 metres and elapsed time at most 0.25 seconds. This can preserve history/epoch through SDK churn but does not prove physical identity. The separate target-lock handoff keeps a logical lock while a new raw track builds fresh history.'});
 const a=box(s,'ACQUIRED\nNew epoch',60,166,240,86,{size:25,bold:true});const b=box(s,'OBSERVED\nCollect samples',355,166,245,86,{fill:C.cream,size:25,bold:true});const c=box(s,'MISSING\nRetain briefly',655,166,245,86,{size:25,bold:true});const d=box(s,'LOST\nRemove history',955,166,260,86,{fill:C.cream,size:25,bold:true});arrow(s,a,b);arrow(s,b,c);arrow(s,c,d);
 table(s,['Final tracking setting','Value'],[['History window','3.0s'],['Missing grace','0.75s'],['Capacity','64 samples per track, 32 tracks'],['Nose continuity distance','At most 0.45m'],['Nose continuity time','At most 0.25s, adjacent single-person frames']],{y:302,h:290,widths:[545,610],font:24});
 note(s,'Same UID within grace preserves its epoch. Geometric continuity is a heuristic, not verified identity.');
}
// 14: sample hysteresis.
{
 const s=add('Current Gaze: Score to Classification',{appendix:true,src:[sources.fit,sources.tc],notes:'gaze_thresholds uses near thresholds at distance <=2.75m and stronger thresholds at distance >2.75m. gaze >= enter sets looking true; gaze <= exit sets false; values in the band keep the previous state. An uninitialised band remains unknown. A null score, nonadjacent source frame or gap >0.25s resets sample memory. Score 0.5 is valid low gaze in this preset. The face label does not establish gaze or facing. Labels recompute from authoritative evidence.'});
 table(s,['Measured distance','Looking entry','Looking exit'],[['At or below 2.75m','0.93','0.90'],['Beyond 2.75m','0.97','0.95']],{y:162,h:170,widths:[575,290,290],font:29});
 const a=box(s,'Score at or above entry\nLOOKING',60,382,350,124,{size:25,bold:true});box(s,'Score in hysteresis band\nKeep previous classification',460,382,350,124,{size:24,bold:true,fill:C.cream});box(s,'Score at or below exit\nNOT LOOKING',865,382,350,124,{size:25,bold:true});
 text(s,'Null or a source gap resets memory to UNKNOWN',65,548,1120,42,29,true,C.blue,'Rubik');
 note(s,'Gaze overlap is a measured score. It does not express a probability of interest or willingness.');
}
// 15: time-weighted gaze and evidence criteria.
{
 const s=add('Temporal Looking: Coverage and Categories',{appendix:true,src:[sources.fit,sources.est,sources.tc],notes:'Integrate only adjacent intervals where both sample classifications are known. The interval uses the earlier classification for looking duration. coverage sums valid durations, looking_time sums durations classified true, gaze_fraction is looking_time divided by coverage. sustained_gaze_s is the trailing contiguous true run. Count bouts only from observed false-to-true transitions; detector gaps cannot create bouts. Sufficiency requires current known gaze, at least five known samples, span >=1s, coverage >=0.6s and coverage fraction >=0.5. Sustained category needs current looking true plus fraction >=0.8 entering or >=0.65 retaining. NONE enters <=0.2 or retains <=0.35. Current preset dwell/run minimum are zero. Example S7 is measured, not illustrative.'});
 text(s,'Looking fraction = measured looking time / covered gaze time',60,154,1155,48,30,true,C.blue,'Rubik');
 table(s,['Evidence gate','Final value'],[['Recent window / minimum span','3.0s / 1.0s'],['Known samples / coverage','5+ samples / 0.6s+'],['Coverage fraction','At least 50%'],['SUSTAINED entry / retention','80% / 65% + looking'],['NONE entry / retention','20% / 35%'],['Dwell / trailing-run minimum','0s / 0s']],{x:60,y:236,w:700,h:353,widths:[430,270],font:22});
 const e=D.first_states.S7.people[0].evidence;
 text(s,'S7 first observation',807,239,405,42,28,true,C.blue,'Rubik');
 text(s,`${(e.gaze_fraction*100).toFixed(1)}%`,805,302,405,85,64,true,C.blue,'Rubik','center');
 text(s,'Measured looking fraction',805,394,405,42,23,false,C.ink,'Noto Sans','center');
 text(s,'Current gaze: not looking\nCategory: INTERMITTENT\nTrailing looking run: 0s',805,454,405,113,24);
 note(s,'High recent looking fraction can coexist with a current non-looking sample. Missing time stays uncovered.');
}
// 16: real slope fitting, editable scatter.
{
 const f=D.fit;
 const s=add('Distance Change Uses a Robust Fitted Slope',{appendix:true,src:[sources.fit,sources.tc,'var/evaluation/llm-refinement-stage-7-8/common/snapshots/S4/first.social-state.json'],notes:`Fit only the newest contiguous valid-distance segment. The implementation uses a Theil-Sen-style median of all pairwise slopes, not ordinary least squares. Intercept is median(d_i - m*t_i). RMS residual measures error about this line. This figure uses actual S4 samples from the frozen first decision; reconstructed values exactly reproduce stored slope. n=${f.fit_samples}, span=${f.span_s}s, slope=${f.slope}m/s, intercept=${f.intercept}m, RMS=${f.residual}m. Either participant can cause changing separation.`});
 chart(s,'scatter',{position:{left:60,top:160,width:760,height:425},series:[{name:'Measured distance',xValues:f.points.map(p=>p[0]),values:f.points.map(p=>p[1]),fill:C.mid,line:{fill:'none',width:0},marker:{symbol:'circle',size:6}},{name:'Median-slope fit',xValues:[0,f.span_s],values:[f.intercept,f.intercept+f.slope*f.span_s],line:{fill:C.blue,width:3},marker:{symbol:'none',size:3}}],scatterOptions:{style:'lineWithMarkers'},hasLegend:true,legend:{position:'bottom',textStyle:{typeface:'Noto Sans',fontSize:20}},xAxis:{...ax,min:0,max:1.2,majorUnit:0.3,title:'Time within segment (s)',numberFormatCode:'0.0'},yAxis:{...ax,min:3.0,max:3.8,majorUnit:0.2,title:'Distance (m)',numberFormatCode:'0.0'}});
 text(s,'m = median of pairwise slopes',850,173,365,74,27,true,C.blue,'Rubik');text(s,'b = median(d - m t)',850,281,365,48,27,true,C.blue,'Rubik');
 table(s,['S4 fit','Value'],[['Slope',f.slope.toFixed(3)+'m/s'],['RMS residual',f.residual.toFixed(3)+'m'],['Samples / span',f.fit_samples+' / '+f.span_s.toFixed(2)+'s'],['Classification','DECREASING']],{x:850,y:362,w:365,h:214,widths:[185,180],font:21,headerFont:22});
 note(s,'Actual S4 first-decision samples. Negative slope describes decreasing separation, not verified human motion.');
}
// 17: invalid segments and decision bins.
{
 const s=add('Distance Fit Quality and Trend Labels',{appendix:true,src:[sources.fit,sources.est,sources.tc],notes:'Sample adjacency requires contiguous frame_sequence and dt>0 through max_gap_s. Nulls/gaps/jumps break the fitting segment. A jump exceeds 3.0m/s * dt +0.05m and invalidates that latest distance at this update. Subsequent clean samples accumulate a new fit. Downsample evenly to at most 32 fit samples if necessary. Five samples and span at least one second are required. Fit is valid only with current observed distance and RMS <=0.1m. Trends use strict inequalities below -0.1 and above +0.1m/s; equality is STABLE.'});
 table(s,['Fit-quality condition','Final value'],[['Minimum sample count','5'],['Minimum segment span','1.0s'],['Maximum fit samples','32, evenly sampled when needed'],['Maximum source gap','0.25s'],['Jump allowance','3.0m/s times elapsed time + 0.05m'],['Maximum RMS residual','0.10m']],{x:60,y:156,w:670,h:350,widths:[390,280],font:22});
 table(s,['Valid fitted slope','Trend'],[['m < -0.10m/s','DECREASING'],['-0.10 to +0.10m/s','STABLE'],['m > +0.10m/s','INCREASING'],['Invalid evidence','UNKNOWN']],{x:785,y:156,w:430,h:350,widths:[235,195],font:22});
 text(s,'Null, gap or jump starts a new fitting segment',65,552,1140,47,31,true,C.blue,'Rubik');
 note(s,'The model receives the category and validity. Full diagnostics retain slope, span, residual and jump counts.');
}
// 18: distance bands and corridor.
{
 const s=add('Proximity Bands and Forward-Corridor Position',{appendix:true,src:['app/state/path_position.py',sources.tc,'docs/path-position.md'],notes:'Final bands use bisect_left without zone hysteresis: <=0.6m TOO_CLOSE, (0.6,2] interaction, (2,6] approachable, >6 far. Fixed-head CAM_HEAD nose transforms into forward/lateral using configured camera offset and yaw. Current offsets and yaw are zero. Corridor half width 0.4m, hysteresis 0.05m: initial <=0.4m, retain IN_WAY to0.45m, reenter from TO_SIDE at <=0.35m. Forward <=0 is BEHIND. Missing/invalid geometry or disabled fixed-head interpretation is UNKNOWN. Positions below are actual first snapshots, not predicted trajectories. No body envelope or dynamic head compensation is modelled.'});
 table(s,['Distance band','Category'],[['At most 0.6m','TOO_CLOSE'],['Above 0.6m through 2.0m','INTERACTION_RANGE'],['Above 2.0m through 6.0m','APPROACHABLE'],['Beyond 6.0m','FAR']],{x:60,y:161,w:575,h:265,widths:[305,270],font:20.5});
 text(s,'Corridor half width 0.40m\nHysteresis 0.05m\nFixed head, offsets and yaw = 0',65,468,565,120,25,true,C.blue);
 const pts=['S1','S4','S7'].map((id,i)=>({name:id+' first',xValues:[D.first_states[id].people[0].nose_forward_m],values:[D.first_states[id].people[0].nose_lateral_m],fill:[C.mid,C.blue,C.cyan][i],line:{fill:'none',width:0},marker:{symbol:['circle','diamond','square'][i],size:8},dataLabelOverrides:[{idx:0,showSeriesName:true,position:'right',textStyle:{typeface:'Noto Sans',fontSize:17,fill:C.ink}}]}));
 chart(s,'scatter',{position:{left:685,top:153,width:530,height:443},series:[...pts,{name:'Corridor boundary',xValues:[0,6],values:[0.4,0.4],line:{fill:C.shadow,width:2},marker:{symbol:'none'}},{name:'Opposite boundary',xValues:[0,6],values:[-0.4,-0.4],line:{fill:C.shadow,width:2},marker:{symbol:'none'}}],scatterOptions:{style:'lineWithMarkers'},hasLegend:true,legend:{position:'bottom',textStyle:{typeface:'Noto Sans',fontSize:16}},xAxis:{...ax,min:0,max:6,majorUnit:2,title:'Forward (m)'},yAxis:{...ax,min:-1.5,max:1.5,majorUnit:0.5,title:'Lateral (m)',numberFormatCode:'0.0'}});
 note(s,'Nose position estimates current corridor occupancy. It does not establish collision, future clearance or intent.');
}
// 19: complete supported precedence.
{
 const s=add('Supported Rule Precedence',{appendix:true,src:[sources.rules,sources.tc],notes:'These are ordered supported v7 conditions. Readiness gates invocation. Current preset disables recurring attention; the general rule supports it only when enabled. YIELD additionally requires detected face, observed person, distance <=3.5m, latest looking false, valid NONE gaze and current gaze overlap <=effective exit threshold. Sustained category held with current looking false returns CONTINUE before interaction branches. IN_WAY/TO_SIDE paired closing conditions differ only by current position. No unsupported measured path or gesture branch remains.'});
 table(s,['Order','Eligible evidence','Action'],[['1','Configured continuous empty interval complete','CONTINUE'],['2','IN_WAY, valid closing, low gaze, face, within 3.5m','YIELD'],['3','Same closing and low-gaze evidence, TO_SIDE','CONTINUE'],['4','Held SUSTAINED category, latest gaze not looking','CONTINUE'],['5','Attentive and already within conversation range','ENGAGE'],['6','Reliable increasing separation outside conversation range','CONTINUE'],['7','Attentive within approach distance','APPROACH'],['8','Other eligible evidence','CONTINUE']],{y:156,h:439,widths:[95,870,190],font:21.5,headerFont:23});
 note(s,'Readiness precedes this table. No policy output is supplied for an unready observation.');
}
// 20: calibrated parameters and rationale.
{
 const s=add('Rule Refinements and Final Parameter Choices',{appendix:true,src:[sources.tc,sources.pc,'docs/final-scenario-tuning.md'],notes:'These are engineering choices tuned on the same nine recordings, not population-calibrated thresholds. They address compressed gaze scores, unreliable far gaze, intermittent detections, passing beside the robot and empty intervals. No independent sensor accuracy or held-out policy generalisation follows from fitting the preset. Native physical approach stopping distance is separate from a descriptive proximity band.'});
 table(s,['Setting','Final value','Design purpose'],[['Conversation / social range','2m / 6m','Approach versus speaking nearby'],['Near gaze entry / exit','0.93 / 0.90','Classify compressed SDK scores'],['Far gaze entry / exit','0.97 / 0.95','Stronger gaze beyond 2.75m'],['History / minimum span','3s / 1s','Build evidence within short encounters'],['Minimum gaze coverage','0.6s and 50%','Preserve missing-data boundaries'],['Extra dwell / trailing run','0s / 0s','Avoid additional decision delay'],['Closing YIELD range','At most 3.5m','Detect closing in the corridor'],['Recurring-looking trigger','Disabled','Avoid interaction after incidental looks']],{y:153,h:446,widths:[390,285,480],font:21.5});
 note(s,'The preset fits these nine tuning recordings. Thresholds do not establish human interest or universal social rules.');
}
// 21: lock lifecycle.
{
 const s=add('Person Locking: Logical Target Lifecycle',{appendix:true,src:[sources.lock,sources.lc,'docs/target-lock.md'],notes:'The continuous rule command/head-focus flow maintains one logical lock ID bound to a session, UID and epoch. It can acquire a sole visible track even before a ready proposal, unless no-attention/receding policy dismisses it. LOCKED permits matching target actions or YIELD; other proposals can hold. A missing target preserves the logical lock for less than two seconds. Same UID/epoch returning within hold restores it. Multiple candidates cause AMBIGUOUS. New UID is TENTATIVE_RETURN and needs guarded evidence. Policy release, timeout, long stream gap, engagement completion or terminal execution failure releases to cooldown. A hold returns effective_decision null, not a fifth action. SingleTrial first-output scoring is separate from this effective command layer.'});
 const u=box(s,'UNLOCKED',60,170,245,72,{size:26,bold:true,fill:C.cream});const l=box(s,'LOCKED',410,170,245,72,{size:26,bold:true});const m=box(s,'MISSING',870,170,300,72,{size:26,bold:true,fill:C.cream});arrow(s,u,l);arrow(s,l,m);
 text(s,'Sole visible track',310,136,200,30,18,false,C.mid);text(s,'Target absent',700,136,170,30,18,false,C.mid);
 const t=box(s,'TENTATIVE_RETURN\nOne new UID',870,342,300,90,{size:24,bold:true});arrow(s,m,t,'bottom','top');
 const am=box(s,'AMBIGUOUS\nSeveral candidates',410,342,285,90,{size:24,bold:true,fill:C.cream});arrow(s,m,am,'bottom','top');
 const co=box(s,'COOLDOWN\nRelease and wait',60,342,245,90,{size:24,bold:true});arrow(s,l,co,'bottom','top');arrow(s,t,l,'left','bottom');
 text(s,'Stable lock ID binds to a UID and track epoch',65,506,1145,43,31,true,C.blue,'Rubik');
 text(s,'Missing, tentative or ambiguous states return HOLD with no effective action.',65,560,1140,63,25);
 note(s,'Tracking grace is 0.75s. The separate logical target-lock hold is 2.0s.');
}
// 22: lock handoff exact gates.
{
 const s=add('Guarded UID Handoff and Release Values',{appendix:true,src:[sources.lock,sources.lc],notes:'All gates are required. Candidate must be newly acquired within 0.8s of old last sighting, exclusive throughout the gap, neither UID may be zero. Scalar distance allowance 0.25 +1.5*dt metres applies from old track and between candidate frames. At least three consistent frames span0.15s and have maximum gap0.35s. At most three rebinds per logical lock. Handoff retains lock ID, but the new raw track keeps its own epoch/fresh history. The handoff frame holds UID_REBOUND_OBSERVE. This is not the tracker nose continuity heuristic and not proof of personal identity.'});
 table(s,['Lock or handoff condition','Final value'],[['Target missing hold','2.0s'],['Release cooldown','1.0s'],['New UID return window','0.8s'],['Candidate evidence','At least 3 frames over at least 0.15s'],['Candidate maximum frame gap','0.35s'],['Allowed distance change','0.25m + 1.5m/s times elapsed time'],['Maximum handoffs per lock','3'],['Identity guard','One exclusive candidate, both UIDs nonzero']],{y:154,h:444,widths:[545,610],font:23});
 note(s,'A new UID builds fresh gaze and distance history. Similar range alone cannot verify physical identity.');
}
// 23: scope of command locking vs SingleTrial.
{
 const s=add('Policy Outputs, Locking and Execution',{appendix:true,src:['docs/command-feedback.md','docs/robot-decision-dry-run.md','docs/live-model-inference.md',sources.trial],notes:'Do not depict the LLM as passing through rule correction or rule target locking. Continuous rule command flow uses pure proposal, target lock, command planner and opt-in execution feedback. Selected-policy SingleTrial uses the selected rules/LLM/VLM source directly, validates correlation/readiness/freshness and latches an accepted action. It does not reconsider a latched CONTINUE. Route settling precedes executing other actions when --single-trial-execute is enabled. Model audits outside selected trials are audit-only. This evaluation stops at first output and separately records acceptance; it instantiates no physical executor.'});
 text(s,'Continuous rule command flow',60,154,1120,44,31,true,C.blue,'Rubik');
 const a=box(s,'Rule proposal',60,223,235,82,{size:24,bold:true});const b=box(s,'Target lock',355,223,235,82,{size:24,bold:true,fill:C.cream});const c=box(s,'Command planner',650,223,255,82,{size:24,bold:true});const d=box(s,'Opt-in execution',965,223,250,82,{size:24,bold:true,fill:C.cream});arrow(s,a,b);arrow(s,b,c);arrow(s,c,d);
 text(s,'Selected-policy trial',60,357,1120,44,31,true,C.blue,'Rubik');
 const e=box(s,'Rules or LLM',60,426,235,82,{size:24,bold:true});const f=box(s,'Trial checks',355,426,235,82,{size:24,bold:true,fill:C.cream});const g=box(s,'First accepted action',650,426,255,82,{size:23,bold:true});const h=box(s,'Native action handler',965,426,250,82,{size:23,bold:true,fill:C.cream});arrow(s,e,f);arrow(s,f,g);arrow(s,g,h);
 note(s,'The recorded evaluation ends at policy output. Acceptance is separate. Physical handlers require explicit execution.');
}
// 24: exact shared payload scopes.
{
 const s=add('DecisionState v3: Included and Excluded Evidence',{appendix:true,src:[sources.state,'docs/navel-supported-cues.md'],notes:'DecisionState contains readiness, robot motion state/description, observed people in preserved order, distance/gaze/trend/face/corridor fields, semantic labels and ten exact numeric/validity rule operands. IDs and source timestamps are correlation context, not LLM text. Seven rule constants are cached separately. Full SocialState retains geometry, fit diagnostics, tracking history and configuration. Unsupported upstream measured path relation and pass gesture are removed throughout. Temporal summaries default NONE, preserving exact v3 JSON. Both production policy paths use the same canonical observation content.'});
 table(s,['Compact shared input','Full diagnostics or separate context'],[['Readiness and empty-scene duration','Session/state IDs and timestamps'],['Robot motion category and description','Raw velocities and configuration'],['Current observed people','Missing-track lifecycle and events'],['Distance, gaze, trend and corridor categories','Raw head/nose geometry and fit statistics'],['Face evidence and descriptive labels','Detailed quality flags and provenance'],['Ten exact evidence operands','Cached rule settings outside observations']],{y:159,h:400,widths:[575,580],font:24});
 text(s,'One canonical observation object for both policies',65,585,1140,47,32,true,C.blue,'Rubik');
}
// 25: labels clearly separated from intent.
{
 const s=add('Readable Labels and Their Measurement Meaning',{appendix:true,src:[sources.state,sources.prompt],notes:'Labels recompute from authoritative categories and validity. Current sample may be known before temporal gaze becomes usable. Invalid trend must be described as unavailable, not stationary. Distance categories are configured references, not willingness. Face detection does not establish facing. Labels contain no recommended action and do not establish intent, permission, predicted collision or clearance. These rows condense the exact English descriptions for readability; authoritative strings are in the state model.'});
 table(s,['Label','Meaning supplied to the LLM'],[['Current gaze','Latest sample classified looking, not looking or unavailable'],['Attention over time','Recent little/no, intermittent or high-fraction looking'],['Proximity','Configured conversation/social-distance band'],['Relative separation','Robot-person distance getting closer, steady or farther'],['Corridor position','Current estimate inside, beside or behind the forward corridor'],['Face detection','Face detected, absent or unavailable']],{y:158,h:404,widths:[365,790],font:24});
 note(s,'Either participant may change relative separation. Labels describe evidence without encoding an action or intention.');
}
// 26: instructions and validation contract.
{
 const s=add('LLM Instructions and Output Validation',{appendix:true,src:[sources.prompt,'app/domain/model_decision.py','app/inference/ollama.py'],notes:'v16 asks for an independent judgement using compact measurements and the four action definitions. It distinguishes current gaze from history and high coverage fraction from uninterrupted looking, known absence from unavailable data, relative separation from individual motion and face detection from facing. No rule-based correction/fallback selects actions. Parser requires exactly action/reason, one of four enums, nonblank string and no extras. The <=25-word explanation is prompt guidance, not a semantic or length validator. All252 new stage7/8 calls are syntactically valid despite varying behavioural agreement.'});
 table(s,['Instruction guidance','Strict output checks'],[['Use only supplied observed evidence','Exactly action and reason'],['Respect UNKNOWN and validity','CONTINUE / APPROACH / ENGAGE / YIELD'],['Separate history and current gaze','Nonblank reason string'],['Do not invent intent or permission','No extra fields'],['One concise sentence, at most 25 words','No semantic correctness or word-count check']],{y:163,h:334,widths:[595,560],font:23});
 box(s,'{"action": "CONTINUE", "reason": "Observed evidence does not support interaction."}',60,530,1155,72,{fill:C.cream,size:24,color:C.ink});
 note(s,'Example output structure. Strict JSON validity and social appropriateness are separate measures.');
}
// 27: stages one through six, retain cost caveat.
{
 const s=add('Input and Instruction Refinement Results',{appendix:true,src:[sources.base,sources.s3,sources.s4,sources.s5,sources.s6],notes:'Three warm operational repetitions per scored arm, same frozen first observations and allowed-action sets. Stages are separate sequential runs; timing is not a randomized causal estimate. Stage3 is fastest historical8/9 at2.76s. Stage4 adds meaning but has no action gain and costs time. Cue removal makes currentv3 but had no isolated model run. Stage5 uses a fresh v14/v3 control3.31s versus selectedv163.04s. Stage6 reuses that selected control; active first summaries do not improve8/9. No repeated-looking primary run occurs because its first inputs are unchanged.'});
 table(s,['Stage / arm','First alignment','Median delay','Input tokens','Decision'],[['1-2 Full-state baseline','6/9','6.21s','1,766','Reference'],['3 Compact state','8/9','2.76s','794','Retain compact core'],['4 Semantic labels','8/9','3.72s','880','Clearer meaning, no action gain'],['5 v14 on supported v3','8/9','3.31s','842','Fresh prompt control'],['5 Selected v16','8/9','3.04s','917','Retain instructions'],['6 Looking-change summary','8/9','3.05s','918','Disabled'],['6 Detection-resumed summary','8/9','3.06s','917','Disabled']],{y:157,h:424,widths:[375,170,160,150,300],font:21.5,headerFont:22});
 note(s,'Measured timings vary with runtime and cache. Stage 3 remains the faster historical 8/9 reference.');
}
// 28: extra temporal cue conditions and coverage.
{
 const s=add('Temporal Summary Ablations: No First-Action Gain',{appendix:true,src:[sources.s6,'docs/temporal-summary-experiments.md'],notes:'Variants are independent. Looking change is justified by valid history, known latest classificationfalse and positive recent looking time. Repeated looking requires at least two measured bouts and does not assert episode durations. Detection resumed requires exact current REACQUIRED event matchingUID,epoch,timestamp. Reassociation/new acquisition/past events do not qualify. Events do not establish gaze during gap or a person physically returning. Positive summaries alone add fields and version4; inactive payload stays exactv3. DefaultNONE.'});
 table(s,['Summary','Active first cases','Primary score','Measured delay'],[['Earlier looking, latest not looking','S4, S6, S7','8/9','3.05s'],['Repeated measured looking','None','No new primary trials','Not measured'],['Detection resumed after a gap','S7','8/9','3.06s']],{y:161,h:273,widths:[420,260,270,205],font:22});
 text(s,'Why the additions stay disabled',65,474,1110,43,31,true,C.blue,'Rubik');bullets(s,['No improvement in the frozen first actions','Extra text costs 36 to 37 tokens in affected inputs','Later diagnostic changes cannot repair the primary score'],65,532,1120,117,25);
}
// 29: settings screens versus confirmed operational score.
{
 const s=add('Inference Settings: Screen and Operational Check',{appendix:true,src:[sources.final,'var/evaluation/llm-refinement-stage-7-8/stage7/screen-selection.json'],notes:'Direct screens use one first snapshot call per scenario and are not primary operational scores. All retain8/9 actions and full prompttoken counts. Context2048 is fastest screen, hence selected for three operational repetitions against fresh control. The output64 ceiling cannot improve answers already terminating below64tokens. Confirmation gives3.13to3.07s (-1.7%) and p955.84to5.91 (+1.2%). Criteria require no scenario loss, all valid/untruncated output,>=10% median gain and no worsep95. Keep8192/192. Loaded memory falls4.98to4.62GB. Downloads overlap early settings blocks, limiting inference about a small difference.'});
 table(s,['Direct screen','Context','Output cap','Median client call'],[['Control','8192','192','3.14s'],['Small context','2048','192','2.99s'],['Small output ceiling','8192','64','3.06s'],['Combined','2048','64','3.00s']],{y:157,h:245,widths:[470,190,195,300],font:24});
 table(s,['Operational arm','Alignment','Median','95th percentile'],[['Fresh control','24/27','3.13s','5.84s'],['Context 2048','24/27','3.07s','5.91s']],{y:444,h:147,widths:[470,190,195,300],font:24});
 note(s,'Only a 1.7% median gain with a slower tail. Retain context 8192 and output ceiling 192.');
}
// 30: full model performance, transparent acceptance.
{
 const s=add('Alternative Models: Accuracy and Timing',{appendix:true,src:[sources.final,'var/evaluation/llm-refinement-stage-7-8/stage8/comparison.json'],notes:'All models use sharedv3/v16 text messages,temperature0 seed42 context8192 output192. Quantization/templates/tokenizers remainmodel-specific and /api/show metadata is saved. Three repetitions rotate model order and one model remains loaded at a time with warmweights. All27first outputs valid permodel. Qwen2.5 3B fixesS4butwronglyYieldsS1/S2/S6/S7/S9. Qwen3instructwronglyApproachesS1/S4 and claimsindividual motion/interest. Gemma3continuesallfirstcases. SelectQwen7B because no candidate preserves scenario quality. The experiment is M3,16GB,Ollama0.34.0;nohardwareactions.'});
 const names=[['Qwen 2.5 7B','qwen25-7b'],['Qwen 3 4B Instruct','qwen3-4b-instruct'],['Gemma 3 4B','gemma3-4b'],['Qwen 2.5 3B','qwen25-3b']];
 table(s,['Model','Each pass','Median','95th pct.','Accepted','Aligned and accepted'],names.map(([name,key])=>{const v=D.stage8.results[key];return[name,v.per_repeat_aligned[0]+'/9',v.median_s.toFixed(2)+'s',v.p95_s.toFixed(2)+'s',v.accepted+'/27',v.aligned_and_accepted+'/27'];}),{y:161,h:355,widths:[350,150,150,150,165,190],font:23,headerFont:21});
 text(s,'Faster alternatives introduce more first-action mistakes',65,556,1130,76,33,true,C.blue,'Rubik');
 note(s,'All first outputs are valid JSON. Acceptance and action agreement measure different things.');
}
// 31: complete stable actions.
{
 const s=add('All First Actions Across Models',{appendix:true,src:[sources.final,'config/final-scenarios.json'],notes:'Every shown first action is stable across the three repetitions. C CONTINUE,A APPROACH,E ENGAGE,Y YIELD. S3 permitsE/A; S8permitsA/E/Cbecauseprimaryfirstdecisioncanoccurinempty scene. No model receives expected labels or scenario IDs as social evidence. Table showsnineunique cases,not27 independent encounters.'});
 const keys=['qwen25-7b','qwen25-3b','qwen3-4b-instruct','gemma3-4b'];const ab={CONTINUE:'C',APPROACH:'A',ENGAGE:'E',YIELD:'Y'};
 table(s,['Case','Allowed','Rules','Qwen 7B','Qwen 3B','Qwen 3 4B','Gemma 4B'],D.rows['qwen25-7b'].filter(r=>r.repetition===1).map(r=>[r.scenario_id,r.expected_actions.map(a=>ab[a]).join(' / '),r.scenario_id==='S4'?'Y':ab[r.first_decision.action],...keys.map(k=>ab[D.rows[k].find(v=>v.repetition===1&&v.scenario_id===r.scenario_id).first_decision.action])]),{y:156,h:442,widths:[95,200,125,175,175,190,195],font:24,headerFont:22});
 note(s,'C = CONTINUE, A = APPROACH, E = ENGAGE, Y = YIELD. Every action repeats across all three passes.');
}
// 32: telemetry and delivery.
{
 const s=add('Latency Components and Live Delivery',{appendix:true,src:[sources.base,sources.final,sources.trial,'scripts/profile_llm_baseline.py'],notes:'Baseline promptprocessing is77.35%of summed nativewarmtime,gen22.12,load0.09,other0.44. CurrentQwen7 first-request medians:prompt1.88s,gen1.27s,load0.003s. Do not add independently calculated medians to derive medianwalltime. latestcompleteobservedmedian3.20,p956.34. Firsteligibleaftermotionmedian1.166849,motiontooutputmedian4.307320,computed directly. Trialdeadline30s;currentobs<=1s,modelsource<=10s,ready/correlated. A continuinglive streamcanstayfreshafterclipend,whilecurrentreadinesscanstillchange. 20/27accepted;17/27bothalignedaccepted. NoLAN/hardwareexecution measurement.'});
 table(s,['Latest Qwen 7B measure','Value'],[['Native prompt evaluation median','1.88s'],['Native answer generation median','1.27s'],['Native model-loading median','0.003s'],['Eligibility to complete observed output','3.20s median, 6.34s 95th percentile'],['Recorded motion to complete output','4.31s median'],['Accepted / aligned and accepted','20/27 / 17/27']],{x:60,y:160,w:710,h:392,widths:[435,275],font:22});
 table(s,['Delivery check','Limit'],[['Current observation age','1s'],['Model source age','10s'],['Trial deadline','30s'],['Current readiness','Required'],['Correlation identity','Required']],{x:825,y:160,w:390,h:392,widths:[245,145],font:22});
 note(s,'Medians of timing components do not add. Valid output can fail delivery as observations age or readiness changes.');
}
// 33: selected configuration.
{
 const s=add('Final Pipeline Configuration for Live Trials',{appendix:true,src:['var/evaluation/llm-refinement-stage-7-8/recommended-settings.json','docs/inference-settings-and-models.md',sources.state],notes:'RetaincurrenttestedbaselinewithQwen2.5 7B,Statev3,Promptv16 andadditionaltemporalmodeNONE. Context8192,maxprediction192,temp0,seed42. Loadweightswithsamecontextbeforeencounterandkeepintendedmodelresident. Measurementsalreadyassumewarmweights;preloadisreproductionconditionnotclaimedextra gain. Effective temporal/tracking finalpresets remainexplicit. HTTPtimeout120s is transportlimit,separatefrom30sSingleTrialdeadline. Actualv16messagesandstatehashesverified. Originalcopiedprotocolsettingspromptversionmetadataisunusedhistoricalv14;recommended-settings.jsoncorrectsv16metadata.'});
 table(s,['Component','Retained selection'],[['Shared observation state','DecisionState v3'],['Instructions','decision-state-llm-v16'],['Model','qwen2.5:7b'],['Generation','Temperature 0, seed 42'],['Context / output ceiling','8192 / 192 tokens'],['Additional temporal summaries','NONE'],['Estimator / tracker','Final-scenario presets'],['Model condition','Preload with the same context before the encounter']],{y:155,h:439,widths:[410,745],font:24});
 note(s,'Exact values: recommended-settings.json. Setup commands: docs/inference-settings-and-models.md.');
}
// 34: scope of research answers.
{
 const s=add('Research Findings and Remaining Limits',{appendix:true,src:[sources.final,'docs/pipeline-refinement.md','docs/llm-refinement-summary.md'],notes:'RQ1: implementedcausalsharedevidencewithhistory,validityandcompactinput;all574framespreservetunedruledecisions/readiness,402eligible. RQ2:currentfirst-actioncomparisonfavoursrules,whileformalhumanpreferencealignmentneedssurveyendpointmappingandfinalverifiedworkbook. Do notclaim9/9humanagreement. Nine recordings are tuning cases,notheldoutaccuracy. Deterministicrepeatsmeasurestabilitynotindependent sample size. SourceS8firstemptyandS6firstbeforelaterglance;do notinferlatercueunderstanding. Corridorfixedheadproxyandreassociationidentityheuristicsneedindependentvalidation. Replayendsatoutput,notphysicalexecution. Historicalstage3at2.76sremainfastest8/9observedrun;finalpipeline3.0to3.2snotprovenbetterbecauseofricherlabels.'});
 table(s,['Research finding','Evidence boundary'],[['Shared state supports policy comparison','Same inputs; rules unchanged across 574 frames'],['Compact input improves action match and delay','6/9 to 8/9; roughly half the initial delay'],['Rules lead on these tuning cases','9/9 expected first actions versus 8/9 LLM'],['Extra temporal descriptions add no gain','No improvement in expected first actions'],['Direct human alignment remains incomplete','Verify survey reference and endpoint mapping'],['Physical execution needs separate evidence','Recorded replay and scripted demos differ']],{y:159,h:413,widths:[575,580],font:23});
 note(s,'Sources and exact values appear in each slide’s speaker notes. Main presentation ends at slide 7.');
}

// 35: native figure of the model quality/speed trade-off.
{
 const s=add('Model Trade-Off: Earlier Answers and Action Agreement',{appendix:true,src:[sources.final],notes:'This editable scatter plot uses the same fresh model comparison. Each point is one model, with median first-output delay and number of aligned unique scenarios. All three repeats yield the same counts. Faster models do not preserve the 7B baseline action agreement. This is a nine-scenario tuning comparison on an Apple M3 with 16GB, not a general benchmark. Model inference times include the local complete-output path.'});
 const names=[['Qwen 2.5 7B','qwen25-7b',C.blue,'diamond'],['Qwen 3 4B Instruct','qwen3-4b-instruct',C.mid,'square'],['Gemma 3 4B','gemma3-4b',C.cyan,'triangle'],['Qwen 2.5 3B','qwen25-3b',C.shadow,'circle']];
 chart(s,'scatter',{position:{left:60,top:153,width:810,height:444},series:names.map(([label,key,color,symbol])=>({name:label,xValues:[D.stage8.results[key].median_s],values:[D.stage8.results[key].per_repeat_aligned[0]],fill:color,line:{fill:'none',width:0},marker:{symbol,size:11}})),scatterOptions:{style:'marker'},dataLabels:{showSeriesName:true,position:'right',textStyle:{typeface:'Noto Sans',fontSize:17,fill:C.ink}},hasLegend:false,legend:{position:'bottom',textStyle:{typeface:'Noto Sans',fontSize:18}},xAxis:{...ax,min:0,max:4,majorUnit:1,title:'Median complete output delay (s)',numberFormatCode:'0.0'},yAxis:{...ax,min:0,max:9,majorUnit:3,title:'Aligned scenarios out of 9',numberFormatCode:'0'}});
 text(s,'Accuracy first',916,198,300,52,35,true,C.blue,'Rubik');
 text(s,'Retain the 7B model:\n8/9 expected first actions\nat a median 3.20s.',916,283,295,153,28);
 text(s,'Fastest tested model:\n4/9 at 1.81s.',916,488,295,93,27);
 note(s,'Every point uses identical shared observation messages and v16 instructions.');
}
// 36: complete evidence operand names, with one measured example.
{
 const s=add('Exact Compact Evidence Operands',{appendix:true,src:[sources.state,'var/evaluation/llm-refinement-stage-7-8/common/snapshots/S4/first.decision-state.json'],notes:'These ten fields retain all numeric/validity operands required by the tuned rules. The compact object also contains current distance/categories, face/corridor observations and semantic descriptions, as shown earlier. Values here are the actual first S4 snapshot and rounded only for display. The policy and model JSON preserve full precision. Neither source IDs nor raw geometry enter LLM text. Looking exit remains because a false sample in the hysteresis band does not necessarily satisfy the rule low-score YIELD condition.'});
 const e=D.first_states.S4.people[0].evidence;
 const defs=[['gaze_valid','Temporal gaze sufficiency'],['distance_trend_valid','Trust fitted separation trend'],['latest_distance_valid','Use current measured distance'],['latest_gaze_looking','Current hysteresis classification'],['latest_gaze_overlap','Current measured gaze score'],['looking_exit_threshold','Effective sample exit threshold'],['looking_bouts','Measured looking episode count'],['looking_time_s','Covered looking duration'],['gaze_fraction','Looking time / covered time'],['sustained_gaze_s','Trailing continuous looking run']];
 table(s,['Exact operand','S4 first value','Meaning'],defs.map(([key,meaning])=>[key,typeof e[key]==='number'?(Number.isInteger(e[key])?String(e[key]):e[key].toFixed(4)):String(e[key]),meaning]),{y:154,h:442,widths:[440,215,500],font:22,headerFont:23});
 note(s,'Displayed values are rounded. Model input preserves exact numeric operands and validity flags.');
}

// Export editable draft, each-slide previews and checked PowerPoint package.
await fs.mkdir(BUILD+'/previews',{recursive:true});
const candidate=BUILD+'/candidate.pptx';await (await PresentationFile.exportPptx(P)).save(candidate);
console.log('Exported draft',slides.length,'slides');
await fs.writeFile(BUILD+'/slide-manifest.json',JSON.stringify({count:slides.length,mainSlides:7,tableOwners:[...new Set(tableOwners)],chartOwners:[...new Set(chartOwners)]},null,2));
for(let i=0;i<slides.length;i++){
 const blob=await P.export({slide:slides[i],format:'png',scale:1});
 await fs.writeFile(BUILD+`/previews/slide-${String(i+1).padStart(2,'0')}.png`,new Uint8Array(await blob.arrayBuffer()));
 const lay=await slides[i].export({format:'layout'});await fs.writeFile(BUILD+`/previews/slide-${String(i+1).padStart(2,'0')}.layout.json`,await lay.text());
 console.log('Rendered',i+1);
}
const src='/Users/eddiekim/Downloads/Poster_59.pptx';const srcHash=crypto.createHash('sha256').update(await fs.readFile(src)).digest('hex');
const result=await finalizePresentation({workspaceDir:ROOT,candidatePath:candidate,finalPath:FINAL,pythonExecutable:PY,
 integrityValidatorPath:SKILL+'/container_tools/inspect_presentation_package_integrity.py',layoutValidatorPath:SKILL+'/container_tools/inspect_presentation_layout_geometry.py',
 layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit',...[...new Set(tableOwners)].flatMap(n=>['--require-native-table-slide',String(n)])],
 explicitTotalSlideCount:slides.length,requiredNativeTableOwnerSlides:[...new Set(tableOwners)],requiredNativeChartOwnerSlides:[...new Set(chartOwners)],
 materializeLiteralChartWorkbooks:true,nativeChartTargetApplication:'powerpoint',
 fontPolicy:{basis:'reference',families:['Rubik','Noto Sans'],referencePath:src,referenceSha256:srcHash},verifyArtifactToolImport:true,
 receiptPath:BUILD+'/validation-final.json'});
console.log('Finalized',FINAL,JSON.stringify(result));
