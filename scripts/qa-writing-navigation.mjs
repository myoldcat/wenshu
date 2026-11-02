import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../frontend/package.json', import.meta.url)),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.argv[2]||'http://127.0.0.1:4173',out=path.resolve(process.argv[3]||'output/writing-navigation/browser-staged');
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
const result={status:'running',cases:[],realApiCalls:0,realModelCalls:0};
const articleId='qa-navigation-article';
const article={id:articleId,version:3,workflowVersion:'writing-six-step-v1',title:'隔离验收已保存文稿',mode:'strict',targetWords:10000,
 brief:{topic:'隔离验收文章导航',purpose:'只读验证列表与文稿入口',articleTitle:'隔离验收已保存文稿',targetWords:10000,automation:'manual',workflow:'chapter',style:'清晰克制',template:'观点文章'},
 sourceIds:['qa-navigation-source'],sources:[{sourceId:'qa-navigation-source',revisionId:'rev-navigation-source',title:'隔离资料'}],
 materialSelection:{userSourceIds:['qa-navigation-source'],recommendationId:null,items:[],passages:[],queries:[],confirmed:true},
 outlineConfirmed:true,workflowPendingSteps:['body'],sections:[{id:'qa-navigation-section',version:1,title:'已保存章节',goal:'查看既有正文',mode:'inherit',targetWords:5000,content:'这段正文已经保存，浏览和切换入口不会重新生成。',manual:true,locked:false,evidence:[]}],
 runs:[],illustrations:[],illustrationReview:null,review:null,createdAt:'2026-10-27T00:00:00Z',updatedAt:'2026-10-27T00:00:00Z'};
async function traceTransitions(context){await context.addInitScript(()=>{
 window.__navigationTransitions={supported:typeof document.startViewTransition==='function',records:[]};
 if(!window.__navigationTransitions.supported)return;
 const original=document.startViewTransition.bind(document);
 document.startViewTransition=function(update){
  const record={finished:false,ready:false},callback=typeof update==='function'?update:update?.update;
  window.__navigationTransitions.records.push(record);
  const wrapped=async()=>{await callback?.();record.updated=true;};
  const transition=original(typeof update==='function'?wrapped:{...update,update:wrapped});
  transition.ready.then(()=>{
   record.ready=true;const header=document.querySelector('.writing-flow-header'),footer=document.querySelector('[data-writing-progress]');
   record.headerName=header?getComputedStyle(header).viewTransitionName:'none';record.footerName=footer?getComputedStyle(footer).viewTransitionName:'none';
   record.headerDuration=getComputedStyle(document.documentElement,'::view-transition-group(writing-page-header)').animationDuration;
   record.footerAnimation=getComputedStyle(document.documentElement,'::view-transition-new(writing-workflow-footer)').animationName;
   record.layout={pageHeight:document.documentElement.scrollHeight,viewport:innerHeight,rootHeight:document.querySelector('.writing-root')?.getBoundingClientRect().height,footerHeight:footer?.getBoundingClientRect().height};
  }).catch(error=>{record.readyError=String(error);});
  transition.finished.then(()=>{record.finished=true;},error=>{record.finished=true;record.finishError=String(error);});return transition;
 };
});}
async function open(theme,reducedMotion='no-preference'){
 const context=await browser.newContext({viewport:{width:1440,height:1050},reducedMotion});await traceTransitions(context);
 const state={requests:[],unsafe:[],errors:[],deferDetails:false,deferred:[],onDeferred:null};
 await context.addInitScript(theme=>localStorage.setItem('koohub-appearance',theme==='demo'?'civic':theme),theme);
 await context.route('**/api/**',async route=>{
  const request=route.request(),endpoint=new URL(request.url()).pathname,method=request.method();let body=null;try{body=request.postDataJSON();}catch{}
  state.requests.push({method,path:endpoint,body});const json=value=>route.fulfill({status:200,json:structuredClone(value)});
  if(endpoint==='/api/ai/writing-activity')return json({active:body?.active||false});
  if(!['GET','HEAD'].includes(method)){state.unsafe.push({method,path:endpoint,body});return route.fulfill({status:409,json:{detail:'Navigation acceptance blocked a mutation'}});}
  if(endpoint==='/api/writing/projects')return json({projects:[article],capabilities:{}});
  if(endpoint==='/api/writing/projects/'+articleId){if(state.deferDetails)return new Promise(resolve=>{state.deferred.push(async()=>{await json({project:article});resolve();});state.onDeferred?.();});return json({project:article});}
  if(endpoint==='/api/documents')return json({documents:[],total:0});
  if(endpoint==='/api/models')return json({models:[],preferences:{autoProcess:false,autoTranslate:false,idleProcess:false}});
  if(endpoint==='/api/jobs')return json({jobs:[],counts:{queued:0,running:0,failed:0,total:0}});
  if(endpoint==='/api/home')return json({topics:[],writing:{total:1,totalWords:24}});
  return json({items:[],entries:[],topics:[],images:[],nodes:[],edges:[],results:[],total:0,ai:{},jobs:[],prompts:[]});
 });
 const page=await context.newPage();page.on('pageerror',error=>state.errors.push(String(error)));
 const startupDetail=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/writing/projects/'+articleId&&response.request().method()==='GET');
 await page.goto(`${base}/?theme=${theme}`,{waitUntil:'networkidle'});
 await startupDetail;
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const nav=page.getByRole('navigation',{name:'主导航',exact:true}),writingNav=nav.getByRole('button',{name:theme==='demo'?'文稿写作':'写作空间',exact:true}),libraryNav=nav.getByRole('button',{name:theme==='demo'?'文档阅读':'知源文库',exact:true});
 return{context,page,state,writingNav,libraryNav,detailReads:()=>state.requests.filter(request=>request.method==='GET'&&request.path==='/api/writing/projects/'+articleId).length};
}
async function gallery(page){await page.locator('.writing-stage-projects .writing-page-heading').waitFor();assert.equal(await page.locator('[data-writing-progress]').count(),0);}
async function settled(page){await page.waitForFunction(()=>window.__navigationTransitions.records.every(record=>record.finished));}
async function clean(env){assert.deepEqual(env.state.unsafe,[]);assert.deepEqual(env.state.errors,[]);}
async function entryPaint(page,expected){
 await settled(page);const trace=await page.evaluate(()=>window.__navigationTransitions);const records=trace.records;
 if(expected&&trace.supported){
  assert(records.length>0);const last=records.at(-1);assert.equal(last.ready,true);assert.equal(last.headerName,'writing-page-header');assert.equal(last.footerName,'writing-workflow-footer');
  assert(parseFloat(last.headerDuration)>0&&parseFloat(last.headerDuration)<=1);assert.match(last.footerAnimation,/writing-workflow-footer-in/);
  const after=await page.evaluate(()=>({pageHeight:document.documentElement.scrollHeight,viewport:innerHeight,rootHeight:document.querySelector('.writing-root')?.getBoundingClientRect().height,footerHeight:document.querySelector('[data-writing-progress]')?.getBoundingClientRect().height}));
  assert(Math.abs(last.layout.pageHeight-after.pageHeight)<1&&after.pageHeight<=after.viewport+1,'Entry snapshots do not add page height');
  assert(Math.abs(last.layout.rootHeight-after.rootHeight)<1&&Math.abs(last.layout.footerHeight-after.footerHeight)<1,'Entry motion keeps the content/footer layout stable');
 }else assert.equal(records.length,0,'Demo/reduced-motion entries remain static');
 return records.length;
}
async function navCase(theme,reduced=false){
 const env=await open(theme,reduced?'reduce':'no-preference'),{context,page,writingNav,libraryNav,detailReads}=env;
 try{
  const startup=detailReads();await writingNav.click();await gallery(page);assert.equal(detailReads(),startup,'Main navigation does not select a manuscript');
  await writingNav.click();await gallery(page);assert.equal(detailReads(),startup);assert.equal(await page.evaluate(()=>window.__navigationTransitions.records.length),0,'Entering the list has no article transition');
  await page.locator(`[data-writing-project-id="${articleId}"]`).click();await page.locator('.writing-stage-editor .writing-flow-header').waitFor();
  assert.equal(detailReads(),startup+1,'An explicit card opens the requested manuscript');
  const beforeList=await entryPaint(page,theme!=='demo'&&!reduced);
  await writingNav.click();await gallery(page);assert.equal(await page.evaluate(()=>window.__navigationTransitions.records.length),beforeList);
  if(!reduced){
   await page.screenshot({path:path.join(out,`${theme}-list.png`),animations:'disabled'});
   await page.getByRole('button',{name:'开始新的写作',exact:true}).click();await page.locator('#writing-topic').waitFor();await settled(page);
   await page.locator('#writing-topic').fill('隔离未保存主题草稿');const readsBeforeLeaving=detailReads();
   await libraryNav.click();await writingNav.click();await gallery(page);
   await page.locator('.writing-local-draft').filter({hasText:'隔离未保存主题草稿'}).waitFor();assert.equal(detailReads(),readsBeforeLeaving);
   await page.locator('.writing-local-draft').getByRole('button',{name:'继续编辑',exact:true}).click();await page.locator('#writing-topic').waitFor();await settled(page);
   assert.equal(await page.locator('#writing-topic').inputValue(),'隔离未保存主题草稿');
   await writingNav.click();await gallery(page);await page.locator('.writing-local-draft').getByRole('button',{name:'继续编辑',exact:true}).click();await page.locator('#writing-topic').waitFor();await settled(page);
   assert.equal(await page.locator('#writing-topic').inputValue(),'隔离未保存主题草稿');assert.equal(detailReads(),readsBeforeLeaving);
   await page.screenshot({path:path.join(out,`${theme}-draft.png`),animations:'disabled'});
  }
  await clean(env);result.cases.push({theme,name:reduced?'reduced-entry':'gallery-navigation-draft',passed:true});
 }catch(error){await page.screenshot({path:path.join(out,`${theme}-${reduced?'reduced':'navigation'}-failed.png`),animations:'disabled'});throw new Error(`${theme}/navigation: ${error.message}`,{cause:error});}
 finally{await context.close();}
}
async function lateNavigation(){
 const env=await open('civic'),{context,page,state,writingNav,libraryNav,detailReads}=env;
 try{
  await writingNav.click();await gallery(page);const before=detailReads();state.deferDetails=true;
  let timer;const requestDeferred=new Promise((resolve,reject)=>{timer=setTimeout(()=>reject(new Error('The explicit manuscript request was not deferred')),10000);state.onDeferred=()=>{clearTimeout(timer);resolve();};});
  await page.locator(`[data-writing-project-id="${articleId}"]`).click();await requestDeferred;await page.waitForFunction(()=>document.querySelector('.writing-page-heading .primary')?.disabled===true);
  assert.equal(state.deferred.length,1);await libraryNav.click();await writingNav.click();await gallery(page);
  state.deferDetails=false;for(const release of state.deferred.splice(0))await release();
  await page.waitForFunction(()=>document.querySelector('.writing-page-heading .primary')?.disabled===false);await gallery(page);
  assert.equal(detailReads(),before+1);assert.equal(await page.evaluate(()=>window.__navigationTransitions.records.length),0,'A late article result cannot override the chosen list view');
  await clean(env);result.cases.push({theme:'civic',name:'late-card-result-keeps-gallery',passed:true});
 }finally{await context.close();}
}
async function runningLight(theme){
 const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'no-preference'}),leaks=[],errors=[];
 await context.route('**/api/**',route=>{leaks.push(route.request().url());return route.abort();});
 const page=await context.newPage();page.on('pageerror',error=>errors.push(String(error)));
 try{
  await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=workflow-states`,{waitUntil:'networkidle'});
  await page.waitForFunction(()=>window.__preparation?.state.project.id&&!window.__preparation.state.loading);
  const band=page.locator('[data-writing-progress]'),before=await band.boundingBox(),saved=await page.evaluate(()=>window.__preparation.state.savedProject);
  const paint=()=>band.locator('li').evaluateAll(nodes=>nodes.map(node=>({stage:node.dataset.stage,state:node.dataset.state,before:getComputedStyle(node,'::before').backgroundImage,sweep:getComputedStyle(node,'::after').animationName})));
  const original=await paint();assert.equal(original.filter(item=>item.state==='running').length,1);
  for(const item of original){if(item.state==='running'){assert.match(item.before,/radial-gradient/);assert.equal(item.sweep,theme==='demo'?'none':'writing-running-light');}else assert.equal(item.sweep,'none');}
  await band.locator('[data-writing-step="brief"]').click();await page.waitForFunction(()=>window.__preparation.state.stage==='brief');
  assert.deepEqual((await paint()).map(({stage,state})=>({stage,state})),original.map(({stage,state})=>({stage,state})));
  assert.equal(await band.locator('li.is-viewed').getAttribute('data-stage'),'brief');assert.deepEqual(await page.evaluate(()=>window.__preparation.state.savedProject),saved);
  assert(Math.abs((await band.boundingBox()).height-before.height)<1);
  await page.emulateMedia({reducedMotion:'reduce'});assert((await paint()).every(item=>item.sweep==='none'));
  assert.equal(await page.evaluate(()=>document.getAnimations().filter(animation=>animation.animationName==='writing-running-light'&&animation.playState==='running').length),0);
  assert.deepEqual(leaks,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>window.__preparation.unexpected),[]);
  assert.equal(await page.evaluate(()=>window.__preparation.requests.filter(request=>request.method!=='GET').length),0);
  await band.screenshot({path:path.join(out,`${theme}-running.png`),animations:'disabled'});result.cases.push({theme,name:'running-light-state-and-reduced-motion',passed:true});
 }finally{await context.close();}
}
try{
 for(const theme of ['jade','glass','civic','demo'])await navCase(theme);
 for(const theme of ['jade','glass','civic'])await navCase(theme,true);
 await lateNavigation();for(const theme of ['jade','glass','civic','demo'])await runningLight(theme);
 result.status='passed';
}catch(error){result.status='failed';result.error=String(error);result.stack=error.stack+'\n'+(error.cause?.stack||'');process.exitCode=1;}
finally{await browser.close();await fs.writeFile(path.join(out,'acceptance.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:result.status,cases:result.cases.length,error:result.error,stack:result.stack,realApiCalls:0,realModelCalls:0}));}
