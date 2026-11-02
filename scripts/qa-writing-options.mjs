import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const require=createRequire(new URL('../frontend/package.json', import.meta.url));
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.argv[2]||'http://127.0.0.1:4173',out=path.resolve(process.argv[3]||'output/writing-options/browser-staged');
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
const result={status:'running',cases:[],realApiCalls:0,realModelCalls:0};
async function open(theme,scenario='blank',width=1440){
  const context=await browser.newContext({viewport:{width,height:1000}}),leaks=[],errors=[];
  await context.route('**/api/**',route=>{leaks.push(route.request().url());return route.abort();});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
  await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=${scenario}`,{waitUntil:'networkidle'});
  await page.waitForFunction(()=>window.__preparation&&!window.__preparation.state.loading);
  const group=name=>page.getByRole('group',{name,exact:true});
  const state=()=>page.evaluate(()=>window.__preparation.state);
  const writes=()=>page.evaluate(()=>window.__preparation.requests.filter(r=>r.method!=='GET'));
  const idle=()=>page.waitForFunction(()=>!window.__preparation.state.busy);
  const clean=async()=>{assert.deepEqual(leaks,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>window.__preparation.unexpected),[]);assert.equal((await state()).error,'');};
  return{context,page,group,state,writes,idle,clean};
}
try{
 for(const theme of ['jade','glass','civic','demo']){
  {
   const {context,page,group,state,writes,clean}=await open(theme);
   try{
    const aside=page.getByRole('complementary',{name:'智能化写作配置',exact:true});
    await aside.waitFor();const a=await aside.boundingBox(),topic=await page.locator('#writing-topic').boundingBox();
    assert(a.x>=topic.x+topic.width,'Automatic configuration sits on the right');
    for(const name of ['自动配图','检查校验'])assert.equal(await aside.getByRole('group',{name,exact:true}).count(),1);
    for(const id of ['writing-automatic-images-label','writing-automatic-review-label'])assert(await aside.locator(`#${id}`).getByText('耗时较长',{exact:true}).isVisible());
    assert.equal(await aside.getByText('耗时较长',{exact:true}).count(),2);
    await page.screenshot({path:path.join(out,`${theme}-default.png`),animations:'disabled'});
    assert.equal(await group('自动配图').getByRole('button',{name:'否',exact:true}).getAttribute('aria-pressed'),'true');
    const review=group('检查校验');
    assert.equal(await review.getByRole('button',{name:'否',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await review.getByRole('button',{name:'是',exact:true}).getAttribute('aria-pressed'),'false');
    assert.equal((await state()).project.automaticOptions.skipReview,true,'Default No skips the review');
    await review.getByRole('button',{name:'是',exact:true}).click();
    assert.equal((await state()).project.automaticOptions.skipReview,false,'Yes enables the review');
    assert.equal(await review.getByRole('button',{name:'是',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await review.getByRole('button',{name:'否',exact:true}).getAttribute('aria-pressed'),'false');
    await review.getByRole('button',{name:'否',exact:true}).click();
    assert.equal((await state()).project.automaticOptions.skipReview,true,'No skips the review');
    assert.equal(await review.getByRole('button',{name:'否',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await review.getByRole('button',{name:'是',exact:true}).getAttribute('aria-pressed'),'false');
    assert.equal((await writes()).length,0,'Changing review choices does not save or start writing');
    const lengths=group('文章长度');assert.equal(await lengths.getByRole('combobox').count(),0);
    for(const [label,value] of [['短篇',1500],['中篇',3000],['长文',6000],['专题',15000]]){
      await lengths.getByRole('button',{name:label,exact:true}).click();assert.equal((await state()).project.targetWords,value);
      assert.equal(await lengths.getByRole('button',{name:label,exact:true}).getAttribute('aria-pressed'),'true');
    }
    await lengths.getByRole('button',{name:'自定义',exact:true}).click();
    await page.locator('#writing-custom-length').fill('9999');assert.equal((await state()).project.targetWords,9999);
    assert(await page.locator('#writing-custom-length').evaluate(node=>node.checkValidity()));
    await page.locator('#writing-topic').fill('隔离验收：右侧配置与直接长度选项');
    await group('写作方式').getByRole('button',{name:/交互式写作/}).click();
    assert.equal(await aside.count(),0);
    const manualAside=page.getByRole('complementary',{name:'交互式写作配置',exact:true});await manualAside.waitFor();
    const generation=manualAside.getByRole('group',{name:'正文生成方式',exact:true});assert.equal(await generation.count(),1);
    assert.equal(await generation.getByRole('button',{name:'一次成文',exact:true}).getAttribute('aria-pressed'),'true');
    await generation.getByRole('button',{name:'逐章生成',exact:true}).click();
    assert.equal((await state()).project.interactiveGeneration,'chapter');
    await page.locator('#writing-custom-length').fill('10000');assert.equal(await generation.count(),1);
    assert.deepEqual(await generation.getByRole('button').evaluateAll(buttons=>buttons.map(button=>({label:button.getAttribute('aria-label'),disabled:button.disabled,pressed:button.getAttribute('aria-pressed')}))),[
      {label:'一次成文',disabled:true,pressed:'false'},
      {label:'逐章生成',disabled:true,pressed:'true'},
    ],'The threshold keeps both choices visible and disabled while showing the effective chapter workflow');
    assert.equal((await state()).project.interactiveGeneration,'chapter','Disabling the choices preserves the preference');
    await page.locator('#writing-custom-length').fill('9999');
    for(const button of await generation.getByRole('button').all())assert(await button.isEnabled());
    assert.equal(await generation.getByRole('button',{name:'逐章生成',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal((await writes()).length,0,'Choosing options does not save or start writing');
    assert(await page.evaluate(()=>window.__preparation.hook.save()));
    await page.evaluate(()=>window.__preparation.hook.reload());
    assert.equal((await state()).project.targetWords,9999);assert.equal((await state()).project.interactiveGeneration,'chapter');
    await page.screenshot({path:path.join(out,`${theme}-manual.png`),animations:'disabled'});
    await group('写作方式').getByRole('button',{name:/智能化写作/}).click();
    await aside.waitFor();await page.screenshot({path:path.join(out,`${theme}-automatic.png`),animations:'disabled'});
    await clean();result.cases.push({theme,scenario:'layout-length-choice-persistence',passed:true});
   }finally{await context.close();}
  }
  for(const choice of ['chapter','single']){
   const {context,page,writes,state,idle,clean}=await open(theme,`options-interactive-outline-${choice}`);
   try{
    await page.waitForFunction(()=>window.__preparation.state.project.id==='writing-interactive-short');
    const button=page.getByRole('button',{name:choice==='single'?'确认纲要并生成全文':'保存并确认纲要',exact:true});
    await button.click();await page.waitForFunction(()=>window.__preparation.state.stage==='editor');await idle();
    const requests=await writes(),body=(await state()).savedProject.sections;
    assert.equal(requests.filter(r=>r.path.endsWith('/generate-document')).length,choice==='single'?1:0);
    if(choice==='single')assert(body.every(section=>section.content.trim()));
    else{
      assert(body.every(section=>!section.content.trim()));
      await page.getByRole('button',{name:'生成当前章节',exact:true}).click();
      await page.getByRole('button',{name:'开始生成本章',exact:true}).click();await idle();
      assert.equal((await state()).savedProject.sections.filter(section=>section.content.trim()).length,1,'Chapter selection generates only the selected chapter');
    }
    await clean();result.cases.push({theme,scenario:'generation-route',choice,passed:true});
   }finally{await context.close();}
  }
  {
   const {context,page,group,writes,clean}=await open(theme,'v2-readonly');
   try{
    await page.waitForFunction(()=>window.__preparation.state.project.id==='writing-options-v2');
    for(const name of ['文章长度','自动配图','检查校验'])for(const button of await group(name).getByRole('button').all())assert(await button.isDisabled());
    assert.equal((await writes()).length,0);await clean();result.cases.push({theme,scenario:'running-readonly',passed:true});
   }finally{await context.close();}
  }
  {
   const {context,page,group,writes,state,clean}=await open(theme,'other');
   try{
    await page.waitForFunction(()=>window.__preparation.state.project.id==='writing-preparation-other');
    const modify=page.locator('[data-writing-modify-step="brief"]');if(await modify.count())await modify.click();
    await group('正文生成方式').getByRole('button',{name:'逐章生成',exact:true}).click();
    await group('正文生成方式').getByRole('button',{name:'一次成文',exact:true}).click();
    assert(await page.evaluate(()=>window.__preparation.hook.save()));
    assert.equal((await writes()).length,0,'Restoring an implicit choice is a no-op');
    assert.equal((await state()).savedProject.brief.interactiveGeneration,undefined);
    await clean();result.cases.push({theme,scenario:'legacy-choice-revert-noop',passed:true});
   }finally{await context.close();}
  }
 }
 for(const theme of ['jade','glass','civic','demo']){
  const {context,page,clean}=await open(theme,'blank',768);
  try{
   const aside=page.getByRole('complementary',{name:'智能化写作配置',exact:true}),box=await aside.boundingBox(),topic=await page.locator('#writing-topic').boundingBox();
   assert(box.y>topic.y,'Narrow layout stacks configuration below the writing form');assert(box.x>=0&&box.x+box.width<=768);
   const footer=page.locator('.writing-preparation-footer'),f=await footer.boundingBox();
   assert(f.y>=box.y+box.height-1,'Submit comes after configuration on narrow screens');
   await footer.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,`${theme}-768.png`),animations:'disabled'});
   await clean();result.cases.push({theme,scenario:'responsive',width:768,passed:true});
  }finally{await context.close();}
 }
 result.status='passed';
}catch(error){result.status='failed';result.error=String(error);result.stack=error.stack;process.exitCode=1;}
finally{await browser.close();await fs.writeFile(path.join(out,'acceptance.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:result.status,cases:result.cases.length,error:result.error,stack:result.stack,realApiCalls:0,realModelCalls:0}));}
