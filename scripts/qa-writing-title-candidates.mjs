import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../frontend/package.json', import.meta.url)),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.argv[2]||'http://127.0.0.1:4173',out=path.resolve(process.argv[3]||'output/writing-navigation/titles-staged');
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
const result={status:'running',cases:[],realApiCalls:0,realModelCalls:0};
const titles=['从资料出发理解网络安全','筑牢网络安全的协作基础','把风险治理落到实际行动'];
async function run(theme,name,fn,{scenario='blank',draftSave=false}={}){
 const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce'}),leaks=[],errors=[];
 await context.route('**/api/**',route=>{leaks.push(route.request().url());return route.abort();});
 const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
 try{
  await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=${scenario}`,{waitUntil:'networkidle'});
  await page.waitForFunction(()=>window.__preparation&&!window.__preparation.state.loading);
  const state=()=>page.evaluate(()=>window.__preparation.state),assistance=()=>page.evaluate(()=>window.__preparation.hook.titleAssistance);
  const panel=page.locator('section[aria-label="辅助拟题结果"]'),candidates=panel.getByRole('group',{name:'候选题目',exact:true});
  const submit=page.locator('.writing-preparation-footer button[type="submit"]'),title=page.locator('#writing-title');
  const request=async()=>{
   await page.locator('.writing-title-assist>button').click();await panel.waitFor();
   await page.waitForFunction(()=>window.__preparation.pendingTitles.length===1&&window.__preparation.hook.titleAssistance.status==='pending');
   assert.equal(await candidates.getByRole('button').count(),3);for(const button of await candidates.getByRole('button').all())assert(await button.isDisabled());
   assert(await submit.isDisabled());return(await panel.boundingBox()).height;
  };
  const release=async data=>{
   await page.evaluate(data=>window.__preparation.releaseTitle(data),data);
   await page.waitForFunction(()=>!window.__preparation.state.busy&&!['pending','applying'].includes(window.__preparation.hook.titleAssistance.status));
  };
  const select=async text=>{
   await candidates.getByRole('button',{name:text,exact:true}).click();
   await page.waitForFunction(text=>window.__preparation.hook.titleAssistance.status==='selected'&&window.__preparation.state.project.articleTitle===text,text);
   assert.equal(await title.inputValue(),text);assert.equal(await candidates.getByRole('button',{name:text,exact:true}).getAttribute('aria-pressed'),'true');assert(await submit.isEnabled());
  };
  const keep=async()=>{await panel.getByRole('button',{name:'使用当前题目',exact:true}).click();await page.waitForFunction(()=>window.__preparation.hook.titleAssistance.status==='kept'&&!window.__preparation.hook.titleChoiceRequired);assert(await submit.isEnabled());};
  const guard=async()=>{
   const blocked=await page.evaluate(async()=>[await window.__preparation.hook.confirmStep('brief'),await window.__preparation.hook.startAutomatic()]);
   assert.deepEqual(blocked,[null,null]);assert(await submit.isDisabled());
   assert.equal(await page.evaluate(()=>window.__preparation.requests.filter(request=>request.method!=='GET'&&request.path!=='/api/writing/assist/title').length),0,'Unresolved title cannot confirm or start automatic writing');
  };
  await fn({page,state,assistance,panel,candidates,submit,title,request,release,select,keep,guard});
  assert.deepEqual(leaks,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>window.__preparation.unexpected),[]);
  const writes=await page.evaluate(()=>window.__preparation.requests.filter(request=>request.method!=='GET'));
  assert(writes.filter(request=>request.path==='/api/writing/assist/title').every(request=>request.body.candidateCount===3));
  const projectWrites=writes.filter(request=>request.path!=='/api/writing/assist/title');
  if(draftSave){assert.equal(projectWrites.length,1);assert.equal(projectWrites[0].method,'POST');assert.equal(projectWrites[0].path,'/api/writing/projects');}
  else assert.deepEqual(projectWrites,[],'Requesting, choosing and browsing titles never persists a project');
  assert.equal((await state()).error,'');result.cases.push({theme,name,passed:true});
 }catch(error){await page.screenshot({path:path.join(out,`${theme}-${name}-failed.png`),animations:'disabled'});throw new Error(`${theme}/${name}: ${error.message}`,{cause:error});}
 finally{await context.close();}
}
try{
 for(const theme of ['jade','glass','civic','demo']){
  await run(theme,'optional-request-select-browse',async({page,state,assistance,panel,candidates,submit,title,request,release,select,guard})=>{
   await page.locator('#writing-topic').fill('隔离主题：网络安全治理');assert.equal((await assistance()).status,'idle');assert.equal(await panel.isVisible(),false);assert(await submit.isEnabled());
   const original=(await state()).savedProject,height=await request();await guard();await release({titles});
   assert.equal((await assistance()).status,'choosing');assert.equal(await title.inputValue(),'');assert(await submit.isDisabled());
   assert.deepEqual(await candidates.getByRole('button').allTextContents(),titles.map((text,index)=>`${index+1}${text}`));
   assert(Math.abs((await panel.boundingBox()).height-height)<1,'Pending and returned candidates reserve the same area');
   for(const button of await candidates.getByRole('button').all())assert.equal(await button.getAttribute('aria-pressed'),'false');
   await guard();await select(titles[1]);await select(titles[0]);await select(titles[1]);
   assert.deepEqual((await state()).savedProject,original);assert.equal(await page.evaluate(()=>window.__preparation.requests.filter(request=>request.path==='/api/writing/assist/title').length),1);
   await page.getByRole('button',{name:'返回文稿列表',exact:true}).click();await page.locator('.writing-local-draft').waitFor();
   await page.locator('.writing-local-draft').getByRole('button',{name:'继续编辑',exact:true}).click();await title.waitFor();assert.equal(await title.inputValue(),titles[1]);assert.equal((await assistance()).status,'selected');
   await panel.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,`${theme}-title-candidates.png`),animations:'disabled'});
  });
  await run(theme,'existing-title-explicit-keep',async({page,state,assistance,title,submit,request,release,keep})=>{
   await page.waitForFunction(()=>window.__preparation.state.project.id==='writing-preparation-other');
   await page.locator('[data-writing-modify-step="brief"]').click();const original=await title.inputValue(),saved=(await state()).savedProject;
   await request();await release({titles});assert.equal(await title.inputValue(),original);assert(await submit.isDisabled());
   await keep();assert.equal(await title.inputValue(),original);assert.equal((await assistance()).status,'kept');assert.deepEqual((await state()).savedProject,saved);
  },{scenario:'other'});
  await run(theme,'late-edits-fence-title-response',async({page,state,assistance,title,request,release,keep,guard})=>{
   await page.locator('#writing-topic').fill('原始拟题主题');await title.fill('原有人工题目');await request();
   await page.evaluate(()=>window.__preparation.hook.setProject(project=>({...project,articleTitle:'等待时新增的人工题目',title:'等待时新增的人工题目'})));
   await release({titles});assert.equal((await assistance()).status,'error');assert.equal(await title.inputValue(),'等待时新增的人工题目');await guard();await keep();
   await request();await page.evaluate(()=>window.__preparation.hook.setProject(project=>({...project,topic:'等待时改变的写作主题'})));
   await release({titles});assert.equal((await assistance()).status,'error');assert.equal((await state()).project.topic,'等待时改变的写作主题');assert.equal(await title.inputValue(),'等待时新增的人工题目');await guard();await keep();
  });
  await run(theme,'invalid-legacy-and-failed-response',async({page,assistance,title,submit,request,release,keep,guard})=>{
   await page.locator('#writing-topic').fill('返回格式隔离主题');await title.fill('需要保留的人工题目');
   for(const response of [{titles:[titles[0],titles[0],titles[2]]},{title:'旧协议单个题目'},{status:503,message:'拟题服务暂不可用，请稍后重试。'}]){
    const height=await request();await release(response);assert.equal((await assistance()).status,'error');assert.equal(await title.inputValue(),'需要保留的人工题目');assert(await submit.isDisabled());
    assert(Math.abs((await page.locator('.writing-title-candidates').boundingBox()).height-height)<1);await guard();
   }
   await keep();
  });
  await run(theme,'edited-selection-requires-reconfirmation',async({page,assistance,title,submit,panel,request,release,select,keep,guard})=>{
   await page.locator('#writing-topic').fill('选题确认隔离主题');await request();await release({titles});await select(titles[0]);
   await title.fill('');await page.waitForFunction(()=>window.__preparation.hook.titleChoiceRequired);assert(await submit.isDisabled());assert(await panel.getByRole('button',{name:'使用当前题目',exact:true}).isDisabled());await guard();
   await select(titles[2]);await title.fill('作者再次调整的题目');assert.equal(await title.inputValue(),'作者再次调整的题目');assert.equal(await page.evaluate(()=>window.__preparation.hook.titleChoiceRequired),false,'A nonempty manual edit after selection remains an explicit valid title');assert(await submit.isEnabled());
   await page.locator('#writing-topic').fill('已经改变的文章主题');await page.waitForFunction(()=>window.__preparation.hook.titleChoiceRequired);await guard();
   await panel.getByRole('group',{name:'候选题目',exact:true}).getByRole('button',{name:titles[1],exact:true}).click();
   await page.waitForFunction(()=>window.__preparation.hook.titleAssistance.status==='error');assert.equal(await title.inputValue(),'作者再次调整的题目');await keep();assert.equal((await assistance()).status,'kept');
  });
  await run(theme,'explicit-draft-save-preserves-choice-scope',async({page,state,assistance,title,submit,request,release,select})=>{
   await page.locator('#writing-topic').fill('显式保存草稿的隔离主题');await request();await release({titles});assert.equal((await state()).project.id,null);
   await page.locator('.writing-flow-actions').getByRole('button',{name:'保存',exact:true}).click();
   await page.waitForFunction(()=>!!window.__preparation.state.project.id&&!window.__preparation.state.busy);
   assert.equal((await assistance()).status,'choosing');assert.deepEqual((await assistance()).titles,titles);assert(await submit.isDisabled());assert.equal(await title.inputValue(),'');
   const saved=(await state()).savedProject;await select(titles[1]);assert.deepEqual((await state()).savedProject,saved);assert.equal(await title.inputValue(),titles[1]);
  },{draftSave:true});
 }
 result.status='passed';
}catch(error){result.status='failed';result.error=String(error);result.stack=error.stack+'\n'+(error.cause?.stack||'');process.exitCode=1;}
finally{await browser.close();await fs.writeFile(path.join(out,'acceptance.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:result.status,cases:result.cases.length,error:result.error,stack:result.stack,realApiCalls:0,realModelCalls:0}));}
