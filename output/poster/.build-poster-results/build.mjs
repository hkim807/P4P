import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const ROOT = "/Users/eddiekim/Documents/Projects/P4P";
const SOURCE = "/Users/eddiekim/Downloads/Poster_59.pptx";
const SKILL_DIR = "/Users/eddiekim/.codex/plugins/cache/openai-primary-runtime/presentations/26.909.12148/skills/presentations";
const BUILD_DIR = path.join(ROOT, "output/poster/.build-poster-results");
const FINAL_PPTX = path.join(ROOT, "output/poster/Poster_59_Evaluation_Results.pptx");
const mm = (value) => value * 96 / 25.4;
const navy = "#22577A";
const ink = "#263642";
const blue = "#215C80";
const pale = "#DDECF2";
const quiet = "#536A77";

await fs.mkdir(BUILD_DIR, { recursive: true });
const pres = await PresentationFile.importPptx(await FileBlob.load(SOURCE));
const slide = pres.slides.getItem(0);
const before = await pres.inspect({ kind: "slide,shape,textbox,table,chart,notes,layout", maxChars: 14000 });
await fs.writeFile(path.join(BUILD_DIR, "source-inspect.ndjson"), before.ndjson);

function textBox(name, value, x, y, w, h, options={}) {
  const shape=slide.shapes.add({
    geometry:"textbox", name,
    position:{left:mm(x),top:mm(y),width:mm(w),height:mm(h)},
    fill:"none", line:{fill:"none",width:0},
  });
  shape.text=value;
  shape.text.style={
    typeface:options.font??"Noto Sans",
    fontSize:options.size??35,
    bold:options.bold??false,
    color:options.color??ink,
    alignment:options.align??"left",
    verticalAlignment:options.valign??"middle",
    wrap:"square", autoFit:"none",
    insets:{left:0,right:0,top:0,bottom:0},
  };
  return shape;
}

textBox("result-policy-heading", "First outputs by scenario", 37, 494, 255, 18,
        {font:"Rubik",size:43,bold:true,color:navy});
textBox("result-timing-heading", "Timing and delivery", 319, 494, 237, 18,
        {font:"Rubik",size:43,bold:true,color:navy});

const rows=[
  ["S1","CONTINUE","APPROACH / YIELD / YIELD"],
  ["S2","APPROACH","APPROACH / APPROACH / APPROACH"],
  ["S3","ENGAGE","ENGAGE / ENGAGE / ENGAGE"],
  ["S4","YIELD","APPROACH / APPROACH / APPROACH"],
  ["S5","CONTINUE","CONTINUE / CONTINUE / CONTINUE"],
  ["S6","CONTINUE","CONTINUE / CONTINUE / CONTINUE"],
  ["S7","CONTINUE","CONTINUE / CONTINUE / CONTINUE"],
  ["S8","CONTINUE","CONTINUE / CONTINUE / CONTINUE"],
  ["S9","APPROACH","CONTINUE / CONTINUE / CONTINUE"],
];
const table=slide.tables.add({
  rows:10,columns:3,
  left:mm(37),top:mm(514),width:mm(266),height:mm(138),
  columnWidths:[mm(27),mm(72),mm(167)],
  values:[["Scenario","Rules","LLM (3 runs)"],...rows],
});
table.styleOptions={headerRow:true,bandedRows:false};
table.borders.assign({style:"solid",fill:"#ABC4D0",width:1});
for(let r=0;r<10;r++) {
  table.rows[r].height=mm(13.8);
  for(let c=0;c<3;c++) {
    const cell=table.getCell(r,c);
    cell.fill=r===0?"#C3DCE6":(r%2===0?"#EAF3F6":pale);
    cell.text.style={
      typeface:r===0?"Rubik":"Noto Sans",
      fontSize:r===0?32:30,
      bold:r===0||c===0,
      color:r===0?navy:ink,
      alignment:"left",verticalAlignment:"middle",wrap:"none",autoFit:"none",
      insets:{left:4,right:2,top:1,bottom:1},
    };
  }
}

textBox("time-label", "Median time to first output", 319, 516, 240, 13,
        {font:"Noto Sans",size:32,bold:true,color:ink});
textBox("rule-time-label", "RULES", 319, 531, 106, 11,
        {font:"Rubik",size:29,bold:true,color:quiet});
textBox("llm-time-label", "LLM", 440, 531, 106, 11,
        {font:"Rubik",size:29,bold:true,color:quiet});
textBox("rule-time-value", "1.09 s", 319, 541, 109, 24,
        {font:"Rubik",size:64,bold:true,color:blue});
textBox("llm-time-value", "11.46 s", 440, 541, 115, 24,
        {font:"Rubik",size:64,bold:true,color:blue});

textBox("accept-label", "Client accepted per repetition", 319, 580, 240, 13,
        {font:"Noto Sans",size:32,bold:true,color:ink});
textBox("rule-accept-label", "RULES", 319, 596, 106, 11,
        {font:"Rubik",size:29,bold:true,color:quiet});
textBox("llm-accept-label", "LLM", 440, 596, 106, 11,
        {font:"Rubik",size:29,bold:true,color:quiet});
textBox("rule-accept-value", "9/9", 319, 606, 109, 24,
        {font:"Rubik",size:64,bold:true,color:blue});
textBox("llm-accept-value", "1/9", 440, 606, 115, 24,
        {font:"Rubik",size:64,bold:true,color:blue});
textBox("state-match", "Initial SocialState matched in 27/27 paired replays.",
        319, 634, 237, 14, {font:"Noto Sans",size:29,color:ink});

textBox("results-note", "First validated output, including CONTINUE. Three replays of nine scenarios. Survey judgments concern video endings, so human alignment is not scored here.",
        37, 656, 521, 20, {font:"Noto Sans",size:26,color:quiet});

textBox("conclusion-copy",
        "Both policies produced valid decisions from identical initial evidence. Rules reached the client sooner and were accepted in 9/9 scenarios, compared with 1/9 for the LLM. Further work should align policy decisions with survey video endings and test delivery on new live encounters.",
        36, 723, 522, 55,
        {font:"Noto Sans",size:36,color:ink});
textBox("conclusion-limitation", "Offline replay; no robot movement or matched-time human rating of policy outputs.",
        36, 778, 522, 10,
        {font:"Noto Sans",size:26,color:quiet});

slide.speakerNotes.append("\nEvaluation sources: var/evaluation/poster-20261011-final-first-output-01/graph_data/scenario_comparison.csv and first_output_coverage.csv; output/poster/Evaluation_Findings_and_Poster_Plan_2026-10-11.md. Numbers describe the final scenario preset, first validated policy output, three paired replays, and offline SingleTrial client acceptance. First-output elapsed time includes evidence accumulation. Survey responses concern video endings and were not scored against these outputs. No physical execution.\n");

const preview=await slide.export({format:"png",scale:0.58});
await fs.writeFile(path.join(BUILD_DIR,"slide-preview.png"),new Uint8Array(await preview.arrayBuffer()));
const layout=await slide.export({format:"layout"});
await fs.writeFile(path.join(BUILD_DIR,"slide-layout.json"),await layout.text());

const {finalizePresentation}=await import(pathToFileURL(path.join(SKILL_DIR,"container_tools/artifact_tool_utils.mjs")).href);
const finalizerDir=path.join(BUILD_DIR,".codex-finalizer");
await fs.mkdir(finalizerDir,{recursive:true});
const candidatePath=path.join(finalizerDir,"candidate.pptx");
await (await PresentationFile.exportPptx(pres)).save(candidatePath);
const result=await finalizePresentation({
  workspaceDir:ROOT,
  candidatePath,
  finalPath:FINAL_PPTX,
  pythonExecutable:"/Users/eddiekim/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3",
  integrityValidatorPath:path.join(SKILL_DIR,"container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath:path.join(SKILL_DIR,"container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs:["--expected-slide-size-emu","21383625,30275213","--require-native-table-slide","1"],
  explicitTotalSlideCount:1,
  requiredNativeTableOwnerSlides:[1],
  fontPolicy:{basis:"reference",families:["Rubik","Noto Sans"],referencePath:SOURCE,
              referenceSha256:"e937ba6f9c3907acf97084a354c2e8c4621bfadf33746d3973738d5fbd3fc5a3"},
  verifyArtifactToolImport:true,
  receiptPath:path.join(finalizerDir,"validation.json"),
});
console.log(JSON.stringify({finalPath:FINAL_PPTX,result}));
