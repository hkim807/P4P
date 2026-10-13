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

const src='/Users/eddiekim/Downloads/Poster_59.pptx';const srcHash=crypto.createHash('sha256').update(await fs.readFile(src)).digest('hex');
const candidate=BUILD+'/candidate.pptx'; const result=await finalizePresentation({workspaceDir:ROOT,candidatePath:candidate,finalPath:FINAL,pythonExecutable:PY,
 integrityValidatorPath:SKILL+'/container_tools/inspect_presentation_package_integrity.py',layoutValidatorPath:SKILL+'/container_tools/inspect_presentation_layout_geometry.py',
 layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit',...JSON.parse(await fs.readFile(BUILD+'/slide-manifest.json','utf8')).tableOwners.flatMap(n=>['--require-native-table-slide',String(n)])],
 explicitTotalSlideCount:36,requiredNativeTableOwnerSlides:JSON.parse(await fs.readFile(BUILD+'/slide-manifest.json','utf8')).tableOwners,requiredNativeChartOwnerSlides:JSON.parse(await fs.readFile(BUILD+'/slide-manifest.json','utf8')).chartOwners,
 materializeLiteralChartWorkbooks:true,nativeChartTargetApplication:'powerpoint',
 fontPolicy:{basis:'reference',families:['Rubik','Noto Sans'],referencePath:src,referenceSha256:srcHash},verifyArtifactToolImport:true,
 receiptPath:BUILD+'/validation-final.json'});
console.log('Finalized',FINAL,JSON.stringify(result));
