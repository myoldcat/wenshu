import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../frontend/package.json', import.meta.url)),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.argv[2]||'http://127.0.0.1:4173',out=path.resolve(process.argv[3]||'output/writing-layout/browser-staged');
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
const result={status:'running',cases:[],realApiCalls:0,realModelCalls:0};
async function open(theme,scenario='blank',width=1440){
 const context=await browser.newContext({viewport:{width,height:1100}}),leaks=[],errors=[];
 await context.route('**/api/**',route=>{leaks.push(route.request().url());return route.abort();});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=${scenario}`,{waitUntil:'networkidle'});
 await page.waitForFunction(()=>window.__preparation&&!window.__preparation.state.loading);
 const group=name=>page.getByRole('group',{name,exact:true}),state=()=>page.evaluate(()=>window.__preparation.state);
 const writes=()=>page.evaluate(()=>window.__preparation.requests.filter(r=>r.method!=='GET'));
 const clean=async()=>{assert.deepEqual(leaks,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>window.__preparation.unexpected),[]);assert.equal((await state()).error,'');};
 async function anchors(){return page.evaluate(()=>{
  const flow=document.querySelector('.writing-flow-content'),scroll=flow.scrollTop;
  const nodes=['#writing-topic','#writing-title','.writing-length-options','[aria-label="写作方式"]','.writing-preparation-footer'];
  return{scroll,boxes:nodes.map(selector=>{const b=document.querySelector(selector).getBoundingClientRect();return{x:b.x,y:b.y+scroll,width:b.width,height:b.height};})};
 });}
 function stable(before,after,scroll=true){for(let i=0;i<before.boxes.length;i++)for(const key of ['x','y','width','height'])assert(Math.abs(before.boxes[i][key]-after.boxes[i][key])<1.1,`Layout anchor ${i}.${key} shifted`);if(scroll)assert(Math.abs(before.scroll-after.scroll)<1.1,'Interaction must not jump scroll');}
 return{context,page,group,state,writes,clean,anchors,stable};
}
try{
 for(const theme of ['jade','glass','civic','demo']){
  {
   const {context,page,group,state,writes,clean,anchors,stable}=await open(theme);
   try{
    const aligned=async()=>{const boxes=await page.locator('.writing-preparation-grid').evaluate(n=>{const main=n.querySelector('.writing-preparation-main').getBoundingClientRect(),aside=n.querySelector('.writing-preparation-options').getBoundingClientRect(),heading=n.querySelector('.writing-form-heading').getBoundingClientRect();return{main:{y:main.y,height:main.height},aside:{y:aside.y,height:aside.height},headingBottom:heading.bottom};});assert(Math.abs(boxes.main.y-boxes.aside.y)<1,'Both columns start at the same height');assert(Math.abs(boxes.main.height-boxes.aside.height)<1,'Both columns have equal height');assert(boxes.headingBottom<=boxes.main.y,'Page heading spans above both columns');};
    await aligned();
    const review=group('检查校验');assert.equal(await review.getByRole('button',{name:'否',exact:true}).getAttribute('aria-pressed'),'true');assert.equal((await state()).project.automaticOptions.skipReview,true);
    await review.getByRole('button',{name:'是',exact:true}).click();assert.equal((await state()).project.automaticOptions.skipReview,false);
    await review.getByRole('button',{name:'否',exact:true}).click();assert.equal((await state()).project.automaticOptions.skipReview,true);
    for(const id of ['writing-automatic-images-label','writing-automatic-review-label'])assert.match(await page.locator('#'+id).innerText(),/耗时较长/);
    const optionPositions=()=>page.evaluate(()=>{const scroll=document.querySelector('.writing-flow-content').scrollTop;return ['自动配图','检查校验'].map(name=>{const b=document.querySelector(`[role="group"][aria-label="${name}"]`).getBoundingClientRect();return b.y+scroll;});});
    const originalOptions=await optionPositions();
    await page.getByRole('button',{name:'已选资料',exact:true}).click();await page.getByRole('button',{name:'系统推荐',exact:true}).click();
    assert.equal(await group('资料写作模式').count(),0);assert.deepEqual(await optionPositions(),originalOptions,'Turning off sources keeps later settings in place');
    await page.getByRole('button',{name:'已选资料',exact:true}).click();await page.getByRole('button',{name:'系统推荐',exact:true}).click();assert.deepEqual(await optionPositions(),originalOptions);
    await group('写作方式').scrollIntoViewIfNeeded();const original=await anchors();
    await group('写作方式').getByRole('button',{name:/交互式写作/}).click();
    const aside=page.getByRole('complementary',{name:'交互式写作配置',exact:true});await aside.waitFor();
    const panel=await aside.boundingBox(),topic=await page.locator('#writing-topic').boundingBox();assert(panel.x>=topic.x+topic.width,'Manual controls are on the right');
    stable(original,await anchors());await aligned();
    const generation=group('正文生成方式'),choices=await generation.getByRole('button').all();
    assert.equal(await choices[0].getAttribute('aria-label'),'一次成文');assert.equal(await choices[1].getAttribute('aria-label'),'逐章生成');
    await generation.getByRole('button',{name:'逐章生成',exact:true}).click();assert.equal((await state()).project.interactiveGeneration,'chapter');
    await group('写作方式').getByRole('button',{name:/智能化写作/}).click();stable(original,await anchors());
    await group('写作方式').getByRole('button',{name:/交互式写作/}).click();stable(original,await anchors());
    const beforeCustom=await anchors();await group('文章长度').getByRole('button',{name:'自定义',exact:true}).click();
    const input=page.locator('#writing-custom-length');await input.waitFor({state:'visible'});await page.waitForFunction(()=>document.activeElement?.id==='writing-custom-length');
    stable(beforeCustom,await anchors());
    assert.equal(await group('文章长度').locator('button input').count(),0,'Input must not be nested inside a button');
    const custom=group('文章长度').locator('.writing-length-custom'),card=await custom.boundingBox(),number=await input.boundingBox();
    assert(number.x>=card.x&&number.y>=card.y&&number.x+number.width<=card.x+card.width&&number.y+number.height<=card.y+card.height,'Number is inside the custom card');
    const preset=group('文章长度').getByRole('button',{name:'短篇',exact:true});assert(Math.abs((await preset.boundingBox()).height-card.height)<1,'Choice cards keep equal height');
    const radius=await preset.evaluate(n=>getComputedStyle(n).borderRadius);assert.equal(await custom.evaluate(n=>getComputedStyle(n).borderRadius),radius);
    await input.fill('9999');stable(beforeCustom,await anchors());
    await input.fill('10000');stable(beforeCustom,await anchors());
    assert.equal(await generation.getByRole('button').count(),2);for(const b of await generation.getByRole('button').all())assert(await b.isDisabled());
    assert.equal((await state()).project.interactiveGeneration,'chapter');
    await input.fill('9999');assert(await generation.getByRole('button',{name:'逐章生成',exact:true}).isEnabled());
    await group('文章长度').getByRole('button',{name:'长文',exact:true}).click();stable(beforeCustom,await anchors());
    assert.equal((await writes()).length,0,'Layout/choices must not save or generate');
    await page.screenshot({path:path.join(out,`${theme}-manual.png`),animations:'disabled'});
    await group('写作方式').getByRole('button',{name:/智能化写作/}).click();stable(original,await anchors());
    await page.screenshot({path:path.join(out,`${theme}-automatic.png`),animations:'disabled'});
    await clean();result.cases.push({theme,scenario:'stable-layout-inline-custom-right-manual',passed:true});
   }finally{await context.close();}
  }
  {
   const {context,page,group,state,writes,clean}=await open(theme,'options-interactive-chapter');
   try{
    await page.waitForFunction(()=>window.__preparation.state.project.id==='writing-interactive-short');
    const saved=(await state()).savedProject;
    await group('正文生成方式').getByRole('button',{name:'一次成文',exact:true}).click();
    await group('正文生成方式').getByRole('button',{name:'逐章生成',exact:true}).click();
    assert(await page.evaluate(()=>window.__preparation.hook.save()));assert.equal((await writes()).length,0);assert.deepEqual((await state()).savedProject,saved);
    await group('文章长度').getByRole('button',{name:'自定义',exact:true}).click();await page.locator('#writing-custom-length').fill('8888');
    assert(await page.evaluate(()=>window.__preparation.hook.save()));await page.evaluate(()=>window.__preparation.hook.reload());
    assert.equal((await state()).project.targetWords,8888);assert.equal((await state()).project.interactiveGeneration,'chapter');
    assert.equal(await group('正文生成方式').getByRole('button',{name:'逐章生成',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('#writing-custom-length').inputValue(),'8888');await clean();result.cases.push({theme,scenario:'saved-custom-choice-noop-roundtrip',passed:true});
   }finally{await context.close();}
  }
  {
   const {context,page,group,writes,clean}=await open(theme,'v2-readonly');
   try{
    await page.waitForFunction(()=>window.__preparation.state.project.id==='writing-options-v2');
    for(const b of await group('文章长度').getByRole('button').all())assert(await b.isDisabled());
    assert.equal(await group('检查校验').getByRole('button',{name:'是',exact:true}).getAttribute('aria-pressed'),'true','Saved legacy review behavior remains enabled');
    await page.getByRole('complementary',{name:'智能化写作配置',exact:true}).waitFor();assert.equal((await writes()).length,0);await clean();result.cases.push({theme,scenario:'readonly',passed:true});
   }finally{await context.close();}
  }
  {
   const {context,page,group,clean,anchors,stable}=await open(theme,'options-interactive-single',768);
   try{
    const aside=page.getByRole('complementary',{name:'交互式写作配置',exact:true}),panel=await aside.boundingBox(),topic=await page.locator('#writing-topic').boundingBox();
    assert(panel.y>topic.y&&panel.x>=0&&panel.x+panel.width<=768);
    await group('文章长度').scrollIntoViewIfNeeded();const before=await anchors();
    await group('文章长度').getByRole('button',{name:'自定义',exact:true}).click();await page.locator('#writing-custom-length').fill('7777');stable(before,await anchors());
    const footer=page.locator('.writing-preparation-footer'),f=await footer.boundingBox(),a=await aside.boundingBox();assert(f.y>=a.y+a.height-1);
    await aside.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,`${theme}-768.png`),animations:'disabled'});await clean();result.cases.push({theme,scenario:'responsive-inline-stability',passed:true});
   }finally{await context.close();}
  }
 }
 result.status='passed';
}catch(error){result.status='failed';result.error=String(error);result.stack=error.stack;process.exitCode=1;}
finally{await browser.close();await fs.writeFile(path.join(out,'acceptance.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:result.status,cases:result.cases.length,error:result.error,stack:result.stack,realApiCalls:0,realModelCalls:0}));}
