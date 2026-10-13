import fs from 'node:fs/promises';
import {FileBlob,PresentationFile} from '@oai/artifact-tool';
const source=await PresentationFile.importPptx(await FileBlob.load('/Users/eddiekim/Downloads/Poster_59.pptx'));
const out='/Users/eddiekim/Documents/Projects/P4P/var/presentation-build/demo-day-20261013';
const result=await source.inspect({kind:'layout,slide,textbox,image',maxChars:12000});
await fs.writeFile(out+'/reference-inspection.ndjson',result.ndjson);
const preview=await source.export({slide:source.slides.getItem(0),format:'png',scale:0.42});
await fs.writeFile(out+'/poster-reference.png',new Uint8Array(await preview.arrayBuffer()));
console.log('Reference imported and rendered',source.slides.items.length);
