// Full App acceptance. All API requests are fulfilled with synthetic fixtures.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const require=createRequire(new URL('../frontend/package.json', import.meta.url));
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const base=(process.argv[2]||'http://127.0.0.1:4173').replace(/\/$/,'');
const out=path.resolve(process.argv[3]||path.join(root,'output/model-reasoning/browser-staged'));
const themes=(process.env.QA_MODEL_REASONING_THEMES||'jade,glass,civic').split(',').filter(Boolean);
const widths=(process.env.QA_MODEL_REASONING_WIDTHS||'1600,390').split(',').map(Number);
const stamp='2026-10-28T00:00:00+08:00';
const fixtureModels=[
 {id:'qa-deepseek',version:1,name:'隔离 DeepSeek',model:'deepseek-flash',baseUrl:'https://api.deepseek.com/v1',contextChars:160000,maxOutputTokens:65536,timeoutSeconds:300,reasoningEffort:'medium',jsonMode:true,hasApiKey:true,createdAt:stamp},
 {id:'qa-ollama',version:1,name:'隔离 Ollama',model:'deepseek-r1:8b',baseUrl:'http://127.0.0.1:11434/v1',contextChars:24000,maxOutputTokens:8192,timeoutSeconds:90,reasoningEffort:'medium',jsonMode:false,hasApiKey:false,createdAt:stamp},
 {id:'qa-spoof',version:1,name:'隔离相似域名',model:'generic-model',baseUrl:'https://api.deepseek.com.evil.invalid/v1',contextChars:24000,maxOutputTokens:8192,timeoutSeconds:90,reasoningEffort:'medium',jsonMode:false,hasApiKey:false,createdAt:stamp},
];
const fixturePreferences={defaultModelId:'qa-deepseek',summaryModelId:null,translationModelId:null,classificationModelId:null,autoTranslate:false,autoProcess:false,idleProcess:false,idleStart:'00:00',idleEnd:'08:00'};
const genericValues=['','none','minimal','low','medium','high','xhigh','max'];
const frames=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const report={status:'running',synthetic:true,cases:[],realApiCalls:0,realModelCalls:0,modelSaveCalls:0,unexpectedWrites:0};
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});

async function open(theme,width){
 const viewport={width,height:width<600?844:1100};
 // The established <=480px app hides its sidebar. Open settings at desktop
 // width first, then resize only the dialog being accepted here.
 const context=await browser.newContext({viewport:{width:1600,height:viewport.height},reducedMotion:'reduce'});
 const state={models:structuredClone(fixtureModels),requests:[],writes:[],unexpectedWrites:[],testCalls:[],errors:[],unmockedApiResponses:[]};
 await context.addInitScript(theme=>localStorage.setItem('koohub-appearance',theme),theme);
 await context.routeWebSocket('**',socket=>socket.close());
 await context.route('**/api/**',async route=>{
  const request=route.request(),url=new URL(request.url()),endpoint=url.pathname,method=request.method();
  let body=null;try{body=request.postDataJSON();}catch{}
  state.requests.push({method,path:endpoint,body});
  const json=(value,status=200)=>route.fulfill({status,json:value,headers:{'X-QA-Synthetic':'model-reasoning','Cache-Control':'no-store'}});
  if(/\/models\/[^/]+\/test$/.test(endpoint)){
   state.testCalls.push({method,path:endpoint});
   return json({detail:'Acceptance must never test a real model connection.'},409);
  }
  if(method==='PUT'&&/^\/api\/models\/[^/]+$/.test(endpoint)){
   const id=decodeURIComponent(endpoint.split('/').at(-1));
   const index=state.models.findIndex(model=>model.id===id);
   assert(index>=0,'Only a synthetic existing model may be saved');
   state.writes.push({method,path:endpoint,body:structuredClone(body)});
   state.models[index]={...state.models[index],...body,version:state.models[index].version+1,createdAt:stamp};
   delete state.models[index].apiKey;
   return json({model:state.models[index]});
  }
  if(!['GET','HEAD'].includes(method)){
   state.unexpectedWrites.push({method,path:endpoint});
   return json({detail:'This isolated acceptance permits only explicit synthetic model saves.'},409);
  }
  if(endpoint==='/api/models')return json({models:state.models,preferences:fixturePreferences});
  if(endpoint==='/api/documents')return json({documents:[],total:0});
  if(endpoint==='/api/jobs')return json({jobs:[],counts:{queued:0,running:0,failed:0,succeeded:0,total:0}});
  if(endpoint==='/api/ai/writing-activity')return json({active:false});
  if(endpoint==='/api/writing/projects')return json({projects:[],capabilities:{}});
  if(endpoint==='/api/home')return json({topics:[],writing:{total:0,totalWords:0}});
  if(endpoint==='/api/ai/taxonomy')return json({categories:[],paths:[]});
  return json({items:[],entries:[],topics:[],images:[],nodes:[],edges:[],results:[],prompts:[],total:0,ai:{},jobs:[]});
 });
 const page=await context.newPage();page.setDefaultTimeout(12000);
 page.on('pageerror',error=>state.errors.push(String(error)));
 page.on('response',response=>{
  if(new URL(response.url()).pathname.startsWith('/api/')&&response.headers()['x-qa-synthetic']!=='model-reasoning')state.unmockedApiResponses.push(response.url());
 });
 await page.goto(`${base}/?theme=${theme}`,{waitUntil:'networkidle'});
 const direct=page.locator('.sidebar-bottom').getByRole('button',{name:'系统设置',exact:true});
 if(await direct.count()&&await direct.isVisible())await direct.click();
 else{
  await page.getByRole('button',{name:'展开更多功能',exact:true}).click();
  await page.getByRole('menuitem',{name:'系统设置',exact:true}).click();
 }
 await page.getByRole('dialog',{name:'系统设置',exact:true}).waitFor();
 return {context,page,state,viewport};
}

async function edit(page,name='隔离 DeepSeek'){
 const overview=page.getByRole('dialog',{name:'系统设置',exact:true});
 await overview.locator('.settings-model-row').filter({has:page.getByText(name,{exact:true})}).click();
 await overview.getByRole('button',{name:'修改',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'修改模型',exact:true});await dialog.waitFor();
 await dialog.locator('details').getByText('请求参数',{exact:true}).click();
 const select=dialog.locator('label.field').filter({hasText:'思考力度'}).locator('select');await select.waitFor();
 return {dialog,select};
}
async function assertDeepSeek(select,legacy=true){
 const options=await select.locator('option').evaluateAll(nodes=>nodes.map(node=>({value:node.value,text:node.textContent})));
 assert.deepEqual(options.map(option=>option.value).sort(),[...['','none','low','high','max'],...(legacy?['medium']:[])].sort());
 assert.match(options.find(option=>option.value==='').text,/high/,'The service default is explicitly high');
 assert.match(options.find(option=>option.value==='none').text,/关闭思考/);
 if(legacy){
  assert.equal(await select.inputValue(),'medium','Opening keeps the saved compatibility value');
  assert.match(options.find(option=>option.value==='medium').text,/high.*medium|medium.*high/,'The saved alias shows its actual high strength');
 }
}
async function assertHit(page,locator,label){
 await locator.scrollIntoViewIfNeeded();await frames(page);
 const metric=await locator.evaluate(node=>{
  const rect=node.getBoundingClientRect(),hit=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
  return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,width:rect.width,height:rect.height,hit:hit===node||node.contains(hit)};
 });
 const viewport=page.viewportSize();
 assert(metric.left>=-1&&metric.right<=viewport.width+1,`${label} fits horizontally`);
 assert(metric.top>=-1&&metric.bottom<=viewport.height+1,`${label} fits vertically`);
 assert(metric.width>30&&metric.height>25&&metric.hit,`${label} is visible and receives pointer input`);
}
async function closeEditor(page,dialog){
 await dialog.getByRole('button',{name:'关闭修改模型',exact:true}).click();
 await dialog.waitFor({state:'detached'});await frames(page);
}
async function save(page,dialog,state,expectedValue){
 const before=state.writes.length;
 await Promise.all([
  page.waitForResponse(response=>new URL(response.url()).pathname.startsWith('/api/models/')&&response.request().method()==='PUT'),
  dialog.getByRole('button',{name:'保存模型',exact:true}).click(),
 ]);
 await dialog.getByText('模型配置已保存。',{exact:true}).waitFor();await frames(page);
 assert.equal(state.writes.length,before+1,'Each explicit save makes exactly one synthetic write');
 const body=state.writes.at(-1).body;
 assert.equal(body.reasoningEffort,expectedValue,'The submitted strength is the exact selected/saved value');
 assert.equal(body.maxOutputTokens,65536,'The reasoning selector must not lower the configured output capacity');
 assert.equal(body.contextChars,160000);assert.equal(body.timeoutSeconds,300);assert.equal(body.jsonMode,true);
 assert(!Object.hasOwn(body,'apiKey'),'An untouched saved credential is never copied into the update body');
}
async function run(theme,width,name,callback){
 console.log(JSON.stringify({theme,width,name,status:'started'}));
 const env=await open(theme,width);
 try{
  await callback(env);
  assert.deepEqual(env.state.errors,[]);assert.deepEqual(env.state.testCalls,[]);
  assert.deepEqual(env.state.unexpectedWrites,[]);assert.deepEqual(env.state.unmockedApiResponses,[]);
  report.modelSaveCalls+=env.state.writes.length;
  report.cases.push({theme,width,name,status:'passed',apiRequests:env.state.requests.length,modelSaveCalls:env.state.writes.length,realApiCalls:0,realModelCalls:0,
   responsiveSetup:width<480?'Open model settings at desktop width, then resize to the target width; baseline sidebar is hidden on narrow screens.':'Open through the existing settings navigation.'});
  console.log(JSON.stringify({theme,width,name,status:'passed'}));
 }catch(error){
  await env.page.screenshot({path:path.join(out,`${theme}-${width}-${name}-failure.png`),animations:'disabled',timeout:3000}).catch(()=>{});
  throw new Error(`${theme}/${width}/${name}: ${error.message}`,{cause:error});
 }finally{await env.context.close();}
}

try{
 for(const theme of themes)for(const width of widths)await run(theme,width,'official-options-and-hit-targets',async({page,state,viewport})=>{
  const {dialog,select}=await edit(page);await page.setViewportSize(viewport);await frames(page);
  await assertDeepSeek(select);
  const hint=dialog.locator('#model-reasoning-hint');await hint.waitFor();
  assert.match(await hint.innerText(),/medium.*high|high.*medium/);
  assert.equal(state.writes.length,0,'Browsing and resizing never save model settings');
  await assertHit(page,select,'Reasoning selector');
  await select.focus();await page.keyboard.press('Tab');
  assert(await dialog.evaluate(node=>node.contains(document.activeElement)),'Keyboard focus remains inside the settings dialog');
  await assertHit(page,dialog.getByRole('button',{name:'保存模型',exact:true}),'Save control');
  await page.screenshot({path:path.join(out,`${theme}-${width}-official-options.png`),animations:'disabled'});
  await closeEditor(page,dialog);assert.equal(state.writes.length,0,'Closing the editor does not save');
 });
 await run('civic',1600,'legacy-preservation-and-explicit-values',async({page,state})=>{
  let {dialog,select}=await edit(page);await assertDeepSeek(select);
  assert.equal(state.writes.length,0);await closeEditor(page,dialog);
  ({dialog,select}=await edit(page));await assertDeepSeek(select);
  await dialog.getByLabel('配置名称',{exact:true}).fill('隔离 DeepSeek 改名');
  await save(page,dialog,state,'medium');
  assert.equal(state.models.find(model=>model.id==='qa-deepseek').reasoningEffort,'medium','An unrelated explicit save retains medium');
  assert.equal(await select.inputValue(),'medium');
  for(const value of ['low','high','max','none','']){
   await select.selectOption(value);assert.equal(await select.inputValue(),value);
   await save(page,dialog,state,value);
   assert.equal(state.models.find(model=>model.id==='qa-deepseek').reasoningEffort,value);
   await assertDeepSeek(select,false);
  }
  await closeEditor(page,dialog);
  const saves=state.writes.length;
  ({dialog,select}=await edit(page,'隔离 DeepSeek 改名'));
  assert.equal(await select.inputValue(),'');await assertDeepSeek(select,false);
  await closeEditor(page,dialog);assert.equal(state.writes.length,saves,'Reopening a saved model adds no write');
 });
 await run('civic',1600,'generic-provider-and-hostname-boundaries',async({page,state})=>{
  for(const name of ['隔离 Ollama','隔离相似域名']){
   const {dialog,select}=await edit(page,name);
   assert.deepEqual(await select.locator('option').evaluateAll(nodes=>nodes.map(node=>node.value)),genericValues,`${name} retains generic provider options`);
   assert.equal(await select.inputValue(),'medium');
   await closeEditor(page,dialog);
  }
  const {dialog,select}=await edit(page);
  const address=dialog.getByLabel('接口地址',{exact:true});
  await address.fill('https://API.DEEPSEEK.COM:443/v1');await assertDeepSeek(select);
  await address.fill('http://127.0.0.1:11434/v1');
  await dialog.getByLabel('模型名称',{exact:true}).fill('deepseek-r1:8b');
  assert.deepEqual(await select.locator('option').evaluateAll(nodes=>nodes.map(node=>node.value)),genericValues,'Changing to local DeepSeek R1 restores generic options');
  assert.equal(await select.inputValue(),'medium','Provider changes retain the unsaved selected value');
  await closeEditor(page,dialog);assert.equal(state.writes.length,0);
 });
 report.status='passed';
}catch(error){report.status='failed';report.error=String(error);report.stack=error.stack+'\n'+(error.cause?.stack||'');process.exitCode=1;}
finally{
 await browser.close();await fs.writeFile(path.join(out,'acceptance.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({status:report.status,cases:report.cases.length,error:report.error,stack:report.stack,realApiCalls:0,realModelCalls:0,modelSaveCalls:report.modelSaveCalls}));
}
