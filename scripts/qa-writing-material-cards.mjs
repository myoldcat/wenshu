import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../frontend/package.json', import.meta.url)),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.argv[2]||'http://127.0.0.1:4173',out=path.resolve(process.argv[3]||'output/writing-material-cards/browser-staged');
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
const result={status:'running',cases:[],realApiCalls:0,realModelCalls:0};
const own=['own-b','own','own-a','own-c'];
async function run(theme,name,fn,{scenario='materials-cards',width=1440,hook=false}={}){
 const context=await browser.newContext({viewport:{width,height:1050}}),leaks=[],errors=[];
 await context.route('**/api/**',route=>{leaks.push(route.request().url());return route.abort();});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));
 try{
  await page.goto(`${base}/tests/${hook?'writing-preparation-hook':'writing-sixstep'}.html?theme=${theme}&scenario=${scenario}`,{waitUntil:'networkidle'});
  await page.waitForFunction(hook=>hook?window.__preparation&&!window.__preparation.state.loading:!!window.__sixstep,hook);
  const catalog=page.locator('[aria-label="写作参考资料"]'),cards=catalog.locator('[data-material-id]');
  const ids=()=>cards.evaluateAll(nodes=>nodes.map(n=>n.dataset.materialId));
  const card=id=>catalog.locator(`[data-material-id="${id}"]`);
  const snapshot=()=>page.evaluate(hook=>hook?window.__preparation.state:({project:window.__sixstep.project,calls:window.__sixstep.calls,requests:window.__sixstep.requests}),hook);
  const columns=()=>catalog.locator('.materials-card-grid').count().then(async nested=>{
   const grid=nested?catalog.locator('.materials-card-grid'):catalog;
   return grid.evaluate(n=>getComputedStyle(n).gridTemplateColumns.trim().split(/\s+/).length);
  });
  await fn({page,catalog,cards,ids,card,snapshot,columns});
  assert.deepEqual(leaks,[]);assert.deepEqual(errors,[]);
  if(hook){assert.deepEqual(await page.evaluate(()=>window.__preparation.unexpected),[]);assert.equal((await snapshot()).error,'');}
  else assert((await snapshot()).requests.every(r=>r.url.startsWith('/api/documents/')||r.url==='/api/retrieval/recommendations'));
  result.cases.push({theme,name,width,passed:true});
 }catch(error){await page.screenshot({path:path.join(out,`${theme}-${name}-failed.png`),animations:'disabled'});throw new Error(`${theme}/${name}: ${error.message}`,{cause:error});}
 finally{await context.close();}
}
try{
 for(const theme of ['jade','glass','civic','demo']){
  await run(theme,'catalog-order-detail-selection',async({page,cards,card,ids,columns,snapshot})=>{
   assert.equal(await cards.count(),11);const before=await ids();assert.deepEqual(before.slice(0,4),own);assert.equal(new Set(before).size,before.length);
   assert.equal(await columns(),3);
   assert.equal(await card('own').locator('[data-material-origin="manual"]').count(),1);assert.equal(await card('own').locator('[data-material-origin="recommended"]').count(),1);
   const project=(await snapshot()).project;
   await card('card-rec-3').getByRole('checkbox').check();assert.equal(await card('card-rec-3').locator('[data-material-origin="manual"]').count(),0);
   assert.deepEqual(await ids(),before);await card('card-rec-0').getByRole('checkbox').uncheck();assert.deepEqual(await ids(),before);
   await card('card-rec-0').getByRole('checkbox').check();assert.deepEqual(await ids(),before);
   await card('own').locator('.material-title').click();await page.locator('.material-detail .material-detail-paragraph').first().waitFor();
   await page.waitForFunction(()=>window.__sixstep.requests.some(r=>r.url==='/api/documents/own?revision_id=rev-own'));
   await page.getByRole('button',{name:'打开资料原文',exact:true}).click();
   assert.deepEqual((await snapshot()).calls,[{name:'open-doc',body:{id:'own',revisionId:'rev-own'}}]);
   assert.deepEqual((await snapshot()).project,project,'Browsing and local checkboxes do not save/reorder project sources');
   await page.screenshot({path:path.join(out,`${theme}-catalog.png`),animations:'disabled'});
  });
  await run(theme,'rerun-retained-and-confirm',async({page,card,ids,snapshot})=>{
   await card('card-rec-3').getByRole('checkbox').check();await page.getByRole('button',{name:'重新检索推荐',exact:true}).click();
   await card('rec').waitFor();assert.deepEqual((await ids()).slice(0,4),own);
   const requests=(await snapshot()).requests.filter(r=>r.url==='/api/retrieval/recommendations');assert.equal(requests.length,1);assert(requests[0].body.sourceIds.includes('card-rec-3'));
   await card('card-rec-3').getByRole('checkbox').uncheck();assert.equal(await card('card-rec-3').count(),1);await card('card-rec-3').getByRole('checkbox').check();
   await card('card-retained').getByRole('checkbox').uncheck();assert.equal(await card('card-retained').count(),1);await card('card-retained').getByRole('checkbox').check();
   assert.equal((await snapshot()).calls.length,0);
   await page.getByRole('button',{name:'确认资料并生成纲要',exact:true}).click();await page.waitForFunction(()=>window.__sixstep.stage==='outline');
   const after=await snapshot();assert.deepEqual(after.project.materialSelection.userSourceIds,own);
   assert(after.project.sourceIds.includes('card-retained')&&after.project.sourceIds.includes('card-rec-3'));
   assert.deepEqual(after.project.materialSelection.passages,[]);assert.deepEqual(after.calls.map(c=>c.name),['save','confirm-step','outline']);
  });
  await run(theme,'readonly-and-running',async({page,cards,ids,columns,snapshot})=>{
   assert.equal(await cards.count(),6);assert.equal(await columns(),3);
   const list=await ids(),lastOwn=Math.max(...list.map((id,index)=>own.includes(id)?index:-1)),firstRecommended=list.findIndex(id=>!own.includes(id));assert(lastOwn<firstRecommended);
   const before=await snapshot();assert.equal(await page.locator('.writing-materials input[type="checkbox"]').count(),0);
   await cards.first().getByRole('button').click();assert.deepEqual((await snapshot()).project,before.project);assert((await snapshot()).calls.every(c=>c.name==='open-doc'));
   await page.screenshot({path:path.join(out,`${theme}-readonly.png`),animations:'disabled'});
   await page.evaluate(()=>window.__sixstep.loadScenario('materials-cards-running'));await page.getByText('智能化写作正在进行，当前资料仅供查看。',{exact:true}).waitFor();
   assert.equal(await cards.count(),6);assert.equal(await page.locator('.writing-materials input[type="checkbox"]').count(),0);
   assert.equal(await page.locator('[data-writing-modify-step="materials"]').count(),0);
  },{scenario:'materials-cards-readonly'});
  for(const width of [768,390])await run(theme,'responsive-'+width,async({page,cards,columns})=>{
   assert.equal(await cards.count(),11);assert.equal(await columns(),width===768?2:1);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No horizontal page overflow');
   const layout=page.locator('.materials-card-layout'),catalog=page.locator('.materials-card-catalog'),detail=page.locator('.material-detail');
   const a=await catalog.boundingBox(),b=await detail.boundingBox();assert(b.y>=a.y+a.height-1);
   await page.getByRole('button',{name:'清除用户选择',exact:true}).click();
   const footer=page.locator('.materials-footer');await footer.scrollIntoViewIfNeeded();
   assert(await footer.getByRole('button',{name:'确认资料并生成纲要',exact:true}).isEnabled());assert(await layout.isVisible());
   await page.screenshot({path:path.join(out,`${theme}-${width}.png`),animations:'disabled'});
  },{width});
  await run(theme,'empty-catalog',async({page,cards,snapshot})=>{
   assert.equal(await cards.count(),0);await page.locator('.materials-card-grid .materials-empty').waitFor();
   assert.equal((await snapshot()).calls.length,0);assert.equal((await snapshot()).requests.length,0);
  },{scenario:'materials-cards-empty'});
  await run(theme,'real-hook-completed-noop',async({page,cards,snapshot})=>{
   await page.waitForFunction(()=>window.__preparation.state.project.id==='writing-completed-readonly');
   await page.locator('[data-writing-step="materials"]').click();await cards.first().waitFor();
   const before=await snapshot();assert.equal(await cards.count(),2);
   await page.locator('[data-writing-modify-step="materials"]').click();
   const checkbox=page.getByRole('checkbox',{name:'纳入资料 隔离选择的原始资料',exact:true});await checkbox.uncheck();await checkbox.check();
   await page.getByRole('button',{name:'保存修改',exact:true}).click();await page.waitForFunction(()=>!window.__preparation.state.busy);
   assert.deepEqual((await snapshot()).savedProject,before.savedProject);
   assert.equal(await page.evaluate(()=>window.__preparation.requests.filter(r=>r.method!=='GET').length),0);
  },{scenario:'completed',hook:true});
 }
 result.status='passed';
}catch(error){result.status='failed';result.error=String(error);result.stack=error.stack+'\n'+(error.cause?.stack||'');process.exitCode=1;}
finally{await browser.close();await fs.writeFile(path.join(out,'acceptance.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:result.status,cases:result.cases.length,error:result.error,stack:result.stack,realApiCalls:0,realModelCalls:0}));}
