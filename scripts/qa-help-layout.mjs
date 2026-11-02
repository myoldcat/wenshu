// Static artifact layout checks. All requests are served locally from generated HTML.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../frontend/package.json', import.meta.url));
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.resolve(process.argv[2]||path.join(root,'output/help-manual/layout'));
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
const report={status:'running',cases:[],realApiCalls:0,realModelCalls:0};
try{
 for(const edition of ['server','windows']){
  const html=await fs.readFile(path.join(root,`resources/presets/manual/user-guide-${edition}.html`),'utf8');
  for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:1000},deviceScaleFactor:1,reducedMotion:'reduce'});
   const unexpected=[],errors=[];
   await context.route('**/*',route=>{if(route.request().url()===`http://manual.test/${edition}`)return route.fulfill({status:200,contentType:'text/html; charset=utf-8',body:html});unexpected.push(route.request().url());return route.abort();});
   const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
   await page.goto(`http://manual.test/${edition}`,{waitUntil:'networkidle'});
   await page.evaluate(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(async img=>{img.loading='eager';await img.decode();}));});
   const detail=await page.evaluate(()=>({
    client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
    h1:document.querySelectorAll('h1').length,edition:document.querySelector('meta[name="wenshu-help-edition"]')?.content,
    placeholders:document.body.innerText.includes('{{'),
    images:[...document.images].map(img=>({alt:img.alt,loaded:img.complete&&img.naturalWidth>0,naturalWidth:img.naturalWidth,naturalHeight:img.naturalHeight,width:img.getBoundingClientRect().width,height:img.getBoundingClientRect().height,inline:img.style.maxWidth,caption:img.closest('figure')?.querySelector('figcaption')?.innerText||''})),
    anchors:[...document.querySelectorAll('a[href^="#"]')].map(a=>({href:a.getAttribute('href'),exists:!!document.getElementById(a.getAttribute('href').slice(1))})),
    overflow:[...document.querySelectorAll('body *')].filter(n=>{const r=n.getBoundingClientRect();return r.width>0&&(r.right>document.documentElement.clientWidth+1||r.left< -1)&&!n.closest('.table-wrap')&&!n.classList.contains('skip');}).map(n=>({tag:n.tagName,cls:n.className,left:n.getBoundingClientRect().left,right:n.getBoundingClientRect().right})).slice(0,12)
   }));
   assert.equal(detail.edition,edition);assert.equal(detail.h1,1);assert.equal(detail.placeholders,false);
   assert(detail.scroll<=detail.client+1,`Page overflow ${edition} ${width}: ${detail.scroll}`);
   assert.equal(detail.images.length,10);assert(detail.images.every(img=>img.loaded&&img.alt&&img.caption),'All ten illustrations have loaded, alternative text and captions');
   for(const img of detail.images){assert(img.width<=img.naturalWidth+1,'Small screenshots retain natural size');assert(Math.abs(img.width/img.height-img.naturalWidth/img.naturalHeight)<0.01,'No image distortion');}
   assert(detail.anchors.every(a=>a.exists));assert.deepEqual(detail.overflow,[]);assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
   for(const section of ['top','models','body','review','wiki','instructions']){
    await page.locator(`#${section}`).evaluate(node=>window.scrollTo({top:node.id==='top'?0:node.getBoundingClientRect().top+window.scrollY-18,behavior:'instant'}));
    await page.screenshot({path:path.join(out,`${edition}-${width}-${section}.png`),animations:'disabled'});
   }
   report.cases.push({edition,width,passed:true,...detail});await context.close();
  }
 }
 report.status='passed';
}catch(error){report.status='failed';report.error=String(error);process.exitCode=1;console.error(error);}
finally{await fs.writeFile(path.join(out,'layout-report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({status:report.status,cases:report.cases.length}));}
