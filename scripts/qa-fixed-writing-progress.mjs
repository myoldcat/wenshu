import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../frontend/package.json', import.meta.url)),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.argv[2]||'http://127.0.0.1:4173',out=path.resolve(process.argv[3]||'output/document-labels/ribbon-staged');
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
const result={status:'running',cases:[],realApiCalls:0,realModelCalls:0};
async function measure(band){return band.evaluate(node=>{
 const rect=n=>{const b=n.getBoundingClientRect();return{x:b.x,y:b.y,width:b.width,height:b.height};};
 const list=node.querySelector('ol'),buttons=[...node.querySelectorAll('li>button')];
 return{band:rect(node),list:rect(list),buttons:buttons.map(rect),scrollWidth:list.scrollWidth,clientWidth:list.clientWidth,scrollLeft:list.scrollLeft};
});}
function stable(before,after){
 assert(Math.abs(before.band.height-after.band.height)<1,'Band height remains fixed across stages and text');
 assert(Math.abs(before.band.y-after.band.y)<1,'Fixed footer stays at the same vertical position');
 assert.equal(after.buttons.length,6);for(let i=0;i<6;i++)assert(Math.abs(before.buttons[i].height-after.buttons[i].height)<1,`Step ${i+1} height remains fixed`);
}
async function viewed(band){
 assert.equal(await band.locator('li.is-viewed').count(),1);assert.equal(await band.locator('[aria-current="step"]').count(),1);
 const mark=await band.locator('li.is-viewed').evaluate(node=>{
  const button=node.querySelector('button'),number=node.querySelector('.writing-progress-number');
  const transitions=[button.style.transition,number.style.transition];button.style.transition='none';number.style.transition='none';
  const paint=()=>{const pseudo=getComputedStyle(button,'::after'),css=getComputedStyle(button),numberCss=getComputedStyle(number);return{
   content:pseudo.content,height:pseudo.height,pseudoBackground:pseudo.backgroundColor,pseudoShadow:pseudo.boxShadow,
   buttonShadow:css.boxShadow,numberBackground:numberCss.backgroundColor,numberShadow:numberCss.boxShadow,numberOutline:numberCss.outlineWidth};};
  const active=paint(),stage=node.dataset.stage,state=node.dataset.state;
  node.classList.remove('is-viewed');const inactive=paint();node.classList.add('is-viewed');button.style.transition=transitions[0];number.style.transition=transitions[1];
  return{active,inactive,stage,state,current:button.getAttribute('aria-current')};
 });
 assert.equal(mark.current,'step');assert.notDeepEqual(mark.active,mark.inactive,'Viewed step has a dedicated visible highlight');
 assert((!['none','normal'].includes(mark.active.content)&&parseFloat(mark.active.height)>0)||mark.active.buttonShadow!=='none'||mark.active.numberShadow!=='none'||parseFloat(mark.active.numberOutline)>0,'Highlight uses a visible rail or ring');
}
async function frameless(band){
 const css=await band.evaluate(node=>{
  const style=getComputedStyle(node),list=node.querySelector('ol'),listStyle=getComputedStyle(list);
  return{summary:!!node.querySelector('.writing-progress-summary'),background:style.backgroundColor,shadow:style.boxShadow,
   border:['borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth'].map(key=>style[key]),
   gap:[listStyle.columnGap,listStyle.rowGap],buttons:[...node.querySelectorAll('li>button')].map(button=>{const s=getComputedStyle(button);return[s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth,s.borderRadius];}),
   palette:[...node.querySelectorAll('li')].map(li=>({stage:li.dataset.stage,state:li.dataset.state,color:getComputedStyle(li).getPropertyValue('--workflow-stage-color').trim(),shared:getComputedStyle(document.documentElement).getPropertyValue('--writing-spectrum-'+li.dataset.stage).trim()}))};
 });
 assert.equal(css.summary,false);assert.equal(css.background,'rgba(0, 0, 0, 0)');assert.equal(css.shadow,'none');assert(css.border.every(value=>value==='0px'));
 assert(css.gap.every(value=>value==='0px'));assert(css.buttons.every(values=>values.every(value=>value==='0px')),'Segments remain contiguous and frameless');
 assert.equal(new Set(css.palette.map(item=>item.color)).size,6);assert(css.palette.every(item=>item.color===item.shared&&['waiting','running','skipped','complete'].includes(item.state)));
 assert.equal(await band.locator('.writing-progress-reminder').count(),0);assert(!/当前查看/.test(await band.innerText()),'Current-view text is removed from visible segments');
 assert.equal(await band.locator('.writing-progress-step-action').count(),0,'The compact ribbon has no action or arrow row');
 assert(!/查看|已完成|用时/.test(await band.innerText()),'Visible segment text omits repeated browse, completed and duration prefixes');
 const durations=await band.locator('.writing-progress-duration').evaluateAll(nodes=>nodes.map(node=>({icons:node.querySelectorAll('svg').length,title:node.title,size:parseFloat(getComputedStyle(node).fontSize)})));
 assert(durations.every(item=>item.icons===1&&item.title&&item.size===13),'Each duration retains enlarged text, its clock icon and full tooltip');
 const typography=await band.locator('li[data-stage]').evaluateAll(nodes=>nodes.map(node=>{
  const title=node.querySelector('.writing-progress-step-heading strong'),metric=node.querySelector('.writing-progress-metric'),duration=node.querySelector('.writing-progress-duration');
  return{titleSize:parseFloat(getComputedStyle(title).fontSize),gap:parseFloat(getComputedStyle(node.querySelector('.writing-progress-step-text')).rowGap),
   titleClipped:title.scrollWidth>title.clientWidth+1,metricSize:metric?parseFloat(getComputedStyle(metric).fontSize):null,
   overlap:duration?title.getBoundingClientRect().right>duration.getBoundingClientRect().left+1:false};
 }));
 assert(typography.every(item=>item.titleSize===15&&item.gap===4&&(item.metricSize===null||item.metricSize===13)),'Larger title/metric fonts retain a 4px gap between the three rows');
 assert(typography.every(item=>!item.titleClipped&&!item.overlap),'Four-character headings stay complete without colliding with trailing duration in narrow columns');
 assert(await band.locator('.writing-progress-notice-badge,.writing-progress-inline-notice').evaluateAll(nodes=>nodes.every(node=>{
  const bounds=node.getBoundingClientRect(),button=node.closest('button').getBoundingClientRect();
  return parseFloat(getComputedStyle(node).fontSize)===12&&node.scrollHeight<=node.clientHeight+1&&bounds.width>0&&bounds.height>0&&bounds.top>=button.top-1&&bounds.bottom<=button.bottom+1&&bounds.left>=button.left-1&&bounds.right<=button.right+1;
 })),'Pending and stale notices stay readable inside their fixed segment');
}
async function stress(page,band,baseline){
 const semantics=await band.locator('li>button').evaluateAll(nodes=>nodes.map(node=>({title:node.title,label:node.getAttribute('aria-label')})));
 assert(semantics.every(item=>item.title&&item.label),'Complete details stay available through title and accessible labels');
 await band.evaluate(node=>{
  const longText='很长的流程说明、待确认提示和耗时信息，'.repeat(32);
  for(const item of node.querySelectorAll('.writing-progress-step-heading>strong,.writing-progress-automatic-badge,.writing-progress-notice-badge,.writing-progress-inline-notice,.writing-progress-state-line>span,.writing-progress-duration')){
   const label=item.querySelector('span');
   if(label)label.textContent=longText;
   else item.textContent=longText;
  }
 });
 stable(baseline,await measure(band));
 assert.deepEqual(await band.locator('li>button').evaluateAll(nodes=>nodes.map(node=>({title:node.title,label:node.getAttribute('aria-label')}))),semantics,'Clipping long visible text retains full accessible details');
 await viewed(band);
}
try{
 for(const theme of ['jade','glass','civic','demo'])for(const width of [1440,1024,768,390]){
  const context=await browser.newContext({viewport:{width,height:1050},reducedMotion:'reduce'}),leaks=[],errors=[];
  await context.route('**/api/**',route=>{leaks.push(route.request().url());return route.abort();});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
  try{
   await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=workflow-states`,{waitUntil:'networkidle'});
   await page.waitForFunction(()=>window.__preparation?.state.project.id&&!window.__preparation.state.loading);
   const band=page.locator('[data-writing-progress]');await band.waitFor();
   const load=async name=>{
    await page.evaluate(name=>{window.__fixedBandOldHook=window.__preparation.hook;window.__preparation.loadScenario(name);},name);
    await page.waitForFunction(()=>window.__preparation.hook!==window.__fixedBandOldHook&&!!window.__preparation.state.project.id&&!window.__preparation.state.loading);
    await band.waitFor();
   };
   const clean=async()=>{
    assert.deepEqual(leaks,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>window.__preparation.unexpected),[]);
    assert.equal(await page.evaluate(()=>window.__preparation.requests.filter(request=>request.method!=='GET').length),0,'No save, confirmation, query, search or model call');
    assert.equal(await page.evaluate(()=>window.__preparation.state.error),'');
   };
   const baseline=await measure(band);assert.equal(baseline.buttons.length,6);
   assert(baseline.buttons.every(button=>Math.abs(button.height-88)<1),'Each step retains an enlarged 88px fixed hit area');
   const rows=new Set(baseline.buttons.map(button=>Math.round(button.y)));assert.equal(rows.size,width===768?2:1);
   assert.equal(baseline.scrollWidth>baseline.clientWidth,width===390);
   const rowHeight=baseline.buttons[0].height;assert(baseline.buttons.every(button=>Math.abs(button.height-rowHeight)<1));
   assert(Math.abs(baseline.band.height-(width===390?96:width===768?176:88))<1,'Readable ribbon height is 88px desktop, 176px two-row or 96px with the narrow scroll area');
   if(width===390)assert(Math.abs(baseline.band.height-rowHeight-8)<1,'Only the fixed scrollbar space extends the single narrow row');
   else assert(Math.abs(baseline.band.height-rowHeight*(width===768?2:1))<1);
   assert.deepEqual(await band.locator('li').evaluateAll(nodes=>nodes.map(node=>node.dataset.state)),['complete','complete','complete','running','skipped','waiting']);
   await frameless(band);await viewed(band);await stress(page,band,baseline);await clean();
   for(const scenario of ['workflow-review','workflow-stale','workflow-untimed','automatic']){
    await load(scenario);stable(baseline,await measure(band));await frameless(band);await viewed(band);
    if(scenario==='workflow-review'){
     assert.match(await band.locator('[data-writing-step="review"]').innerText(),/2项待确认/);
     const duration=band.locator('[data-writing-step="body"] .writing-progress-duration');assert.equal((await duration.innerText()).trim(),'2分36秒');assert.equal(await duration.locator('svg').count(),1);assert.match(await duration.getAttribute('title'),/用时\s*2分36秒/);
     assert.equal(await band.locator('li[data-stage="review"]').getAttribute('data-state'),'skipped');assert.equal(await band.getAttribute('data-completed'),'6');
    }else if(scenario==='workflow-stale'){
     assert.match(await band.locator('[data-writing-step="review"]').innerText(),/校验报告已过期/);assert(!/2项待确认/.test(await band.innerText()));
    }else if(scenario==='workflow-untimed')assert.equal(await band.locator('.writing-progress-duration').filter({hasText:/^未记录$/}).count(),6);
    else {
     assert.match(await band.locator('[data-writing-metric="brief"]').innerText(),/自动模式/);assert.equal(await band.locator('[data-writing-metric="brief"]').getAttribute('data-metric-value'),'automatic');
     assert.equal(await page.locator('.writing-automatic:not(.is-report)').count(),0,'No duplicate automatic-writing banner is rendered');
     const interrupt=page.locator('.writing-flow-header .writing-flow-actions [data-writing-interrupt]');
     assert.equal(await interrupt.count(),1);assert(await interrupt.isEnabled(),'The running task can still be interrupted from the header');
     assert.equal((await interrupt.innerText()).trim(),'中断写作');assert.equal(await interrupt.getAttribute('aria-label'),'中断并切换为交互式写作');assert.equal(await interrupt.getAttribute('title'),'中断并切换为交互式写作');
     assert(await band.evaluate(node=>node.classList.contains('is-automatic')),'Automatic mode remains visible in the footer');
    }
    await stress(page,band,baseline);await clean();
   }
   await load('workflow-review');const saved=await page.evaluate(()=>window.__preparation.state.savedProject),states=await band.locator('li').evaluateAll(nodes=>nodes.map(node=>node.dataset.state));
   if(width===390){
    await band.locator('[data-writing-step="brief"]').focus();await page.keyboard.press('End');assert.equal(await page.locator(':focus').getAttribute('data-writing-step'),'review');
    const list=await band.locator('ol').boundingBox(),review=await band.locator('[data-writing-step="review"]').boundingBox();
    assert(review.x>=list.x-1&&review.x+review.width<=list.x+list.width+1,'Keyboard End scrolls the last step into view');
    await page.keyboard.press('Enter');
   }else await band.locator('[data-writing-step="review"]').click();
   const report=page.locator('.writing-review-page');await report.waitFor();assert.equal(await page.getByRole('dialog').count(),0,'Footer navigation opens a full review page');
   assert.equal(await report.getByRole('button',{name:'已核对，此项无误',exact:true}).count(),0,'Saved validation report remains read-only');
   assert.equal(await band.locator('[aria-current="step"]').getAttribute('data-writing-step'),'review');
   assert.deepEqual(await band.locator('li').evaluateAll(nodes=>nodes.map(node=>node.dataset.state)),states,'Changing the viewed step keeps actual workflow states');
   assert.deepEqual(await page.evaluate(()=>window.__preparation.state.savedProject),saved);stable(baseline,await measure(band));await viewed(band);
   await page.keyboard.press('Escape');assert(await report.isVisible(),'Escape does not dismiss a full workspace page');
   await report.getByRole('button',{name:'返回正文',exact:true}).click();await report.waitFor({state:'hidden'});await frameless(band);await clean();
   await band.screenshot({path:path.join(out,`${theme}-${width}.png`),animations:'disabled'});
   result.cases.push({theme,width,scenarios:5,statesFixed:true,longTextFixed:true,reportReadOnly:true,passed:true});
  }catch(error){await page.screenshot({path:path.join(out,`${theme}-${width}-failed.png`),animations:'disabled'});throw new Error(`${theme}/${width}: ${error.message}`,{cause:error});}
  finally{await context.close();}
 }
 result.status='passed';
}catch(error){result.status='failed';result.error=String(error);result.stack=error.stack+'\n'+(error.cause?.stack||'');process.exitCode=1;}
finally{await browser.close();await fs.writeFile(path.join(out,'acceptance.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:result.status,cases:result.cases.length,error:result.error,stack:result.stack,realApiCalls:0,realModelCalls:0}));}
