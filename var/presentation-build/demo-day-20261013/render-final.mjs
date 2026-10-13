import fs from 'node:fs/promises';
import {FileBlob,PresentationFile} from '@oai/artifact-tool';
const base='/Users/eddiekim/Documents/Projects/P4P';
const P=await PresentationFile.importPptx(await FileBlob.load(base+'/output/presentations/Navel_Demo_Day_Final.pptx'));
const out=base+'/var/presentation-build/demo-day-20261013/final-previews';
await fs.mkdir(out,{recursive:true});
for(let i=0;i<P.slides.items.length;i++){
 const png=await P.export({slide:P.slides.getItem(i),format:'png',scale:1});
 await fs.writeFile(out+`/slide-${String(i+1).padStart(2,'0')}.png`,new Uint8Array(await png.arrayBuffer()));
 console.log('Final file rendered',i+1);
}
