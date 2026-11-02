// Full App guide acceptance. Every API boundary is fulfilled with isolated fixtures.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../frontend/package.json', import.meta.url));
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const base=(process.argv[2]||'http://127.0.0.1:4173').replace(/\/$/,'');
const out=path.resolve(process.argv[3]||path.join(root,'output/guide-title-cleanup/browser-staged'));
const themes=(process.env.QA_GUIDE_THEMES||'jade,glass,civic').split(',').filter(Boolean);
const widths=(process.env.QA_GUIDE_WIDTHS||'1600,390').split(',').map(Number);
const only=new Set((process.env.QA_GUIDE_CASES||'').split(',').filter(Boolean));
const edition=process.env.QA_GUIDE_EDITION||'server';
assert(['server','windows'].includes(edition));
const manualHtml=await fs.readFile(path.join(root,`resources/presets/manual/user-guide-${edition}.html`),'utf8');
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
const report={status:'running',edition,synthetic:true,cases:[],realApiCalls:0,realModelCalls:0,writeCalls:0,downloadRequests:0};
const frames=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const guideEndpoint='/api/presets/wenshu-user-guide/view';
const isDownload=endpoint=>/\/download(?:\/|$)/.test(endpoint);
const targetViewports=new WeakMap();
async function waitFrameScroll(frame,scrolled){
 // The manual's no-scripts sandbox can suppress document animation callbacks.
 // Poll from Node so a sandboxed iframe never depends on its own RAF timer.
 let offset;
 for(let attempt=0;attempt<40;attempt++){
  offset=await frame.evaluate(()=>window.scrollY);
  if(scrolled?offset>0:offset===0)return;
  await new Promise(resolve=>setTimeout(resolve,50));
 }
 assert.fail(`Guide scroll offset did not reach ${scrolled?'a positive value':'the top'}; got ${offset}`);
}

async function open(theme,width,catalogMode){
 const viewport={width,height:width<600?844:1100};
 // Existing <=480px app navigation hides the sidebar. Open it at desktop size,
 // then resize the guide itself; this acceptance does not change app navigation.
 const context=await browser.newContext({viewport:{width:width<480?1600:width,height:viewport.height},reducedMotion:'reduce',acceptDownloads:true});
 const state={catalogMode,requests:[],writes:[],downloadRequests:[],downloads:[],unexpectedPresetReads:[],assetErrors:[],errors:[]};
 await context.addInitScript(theme=>localStorage.setItem('koohub-appearance',theme==='demo'?'civic':theme),theme);
 await context.routeWebSocket('**',socket=>socket.close());
 await context.route('**/api/**',async route=>{
  const request=route.request(),url=new URL(request.url()),endpoint=url.pathname,method=request.method();
  let body=null;try{body=request.postDataJSON();}catch{}
  state.requests.push({method,path:endpoint,query:url.search,body});
  const json=(value,status=200)=>route.fulfill({status,json:value});
  if(isDownload(endpoint)){state.downloadRequests.push({method,path:endpoint});return json({detail:'Guide downloads are blocked by isolated acceptance.'},409);}
  if(!['GET','HEAD'].includes(method)){state.writes.push({method,path:endpoint,body});return json({detail:'Guide browsing must not write or start a task.'},409);}
  if(endpoint==='/api/presets'){
   if(state.catalogMode==='failed')return json({detail:'隔离目录暂不可用，请重新载入。'},503);
   const withExamples=['missing','with-examples'].includes(state.catalogMode);
   const items=withExamples?[{id:'qa-other-example',title:'其他示例不应替代指南',kind:'ai-agent',language:'zh',viewUrl:'/api/presets/qa-other-example/view'}]:[];
   if(state.catalogMode!=='missing')items.push({id:'wenshu-user-guide',title:'文枢使用指南',kind:'manual',language:'zh',sourceName:'隔离配套文档',viewUrl:guideEndpoint,downloadUrl:'/api/presets/wenshu-user-guide/download'});
   return json({version:'guide-dialog-qa',items,example:withExamples?{id:'qa-writing-example',title:'隔离示例文稿',projectId:'qa-unused-project'}:null});
  }
  if(endpoint===guideEndpoint)return route.fulfill({status:200,contentType:'text/html; charset=utf-8',body:manualHtml,headers:{
   'Cache-Control':'no-store','Content-Security-Policy':"sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"}});
  if(endpoint.startsWith('/api/presets/')){state.unexpectedPresetReads.push(endpoint);return json({detail:'No extra catalog document may be opened by this test.'},404);}
  if(endpoint==='/api/documents')return json({documents:[],total:0});
  if(endpoint==='/api/models')return json({models:[],preferences:{autoProcess:false,autoTranslate:false,idleProcess:false,classificationModelId:null}});
  if(endpoint==='/api/jobs')return json({jobs:[],counts:{queued:0,running:0,failed:0,succeeded:0,total:0}});
  if(endpoint==='/api/ai/writing-activity')return json({active:false});
  if(endpoint==='/api/writing/projects')return json({projects:[],capabilities:{}});
  if(endpoint==='/api/home')return json({topics:[],writing:{total:0,totalWords:0}});
  if(endpoint==='/api/ai/taxonomy')return json({categories:[],paths:[]});
  return json({items:[],entries:[],topics:[],images:[],nodes:[],edges:[],results:[],prompts:[],total:0,ai:{},jobs:[]});
 });
 const page=await context.newPage();targetViewports.set(page,viewport);page.setDefaultTimeout(12000);
 page.on('pageerror',error=>state.errors.push(String(error)));page.on('download',download=>state.downloads.push(download.suggestedFilename()));
 page.on('response',response=>{const url=new URL(response.url());if(url.origin===base&&!url.pathname.startsWith('/api/')&&response.status()>=400)state.assetErrors.push(`${response.status()} ${url.pathname}`);});
 await page.goto(`${base}/?theme=${theme}`,{waitUntil:'domcontentloaded',timeout:60000});
 await page.getByRole('navigation',{name:'主导航',exact:true}).waitFor({state:'visible',timeout:60000});
 await page.evaluate(()=>document.fonts.ready);
 await page.getByRole('navigation',{name:'主导航',exact:true}).getByRole('button',{name:theme==='demo'?'文档阅读':'知源文库',exact:true}).press('Enter');
 await page.setViewportSize(viewport);await frames(page);
 return {context,page,state};
}
async function openHelp(page){
 const viewport=targetViewports.get(page);if(viewport.width<480){await page.setViewportSize({width:1600,height:viewport.height});await frames(page);}
 const direct=page.locator('.sidebar-bottom').getByRole('button',{name:'使用指南',exact:true});
 let returnTarget;
 if(await direct.count()&&await direct.isVisible()){returnTarget=direct;await direct.press('Enter');}
 else{const more=page.getByRole('button',{name:'展开更多功能',exact:true});returnTarget=more;await more.press('Enter');await page.getByRole('menuitem',{name:'使用指南',exact:true}).press('Enter');}
 const dialog=page.getByRole('dialog',{name:'使用指南',exact:true});await dialog.waitFor();
 if(viewport.width<480){await page.setViewportSize(viewport);await frames(page);}
 assert.equal(await dialog.count(),1,'The accessible name remains 使用指南');
 assert.equal(await dialog.getByRole('heading').count(),0,'No outer heading duplicates the document heading');
 assert.equal(await dialog.getByRole('link',{name:/下载/}).count(),0);
 assert.equal(await dialog.getByRole('button',{name:/下载/}).count(),0);
 assert.equal(await dialog.locator('a[download],a[href*="/download"]').count(),0);
 return {dialog,returnTarget};
}
async function guideFrame(dialog){
 const locator=dialog.locator('.preset-document-frame');await locator.waitFor();assert.equal(await locator.count(),1);
 assert.equal(await locator.getAttribute('src'),guideEndpoint);
 const frame=await (await locator.elementHandle()).contentFrame();assert(frame);
 await frame.getByRole('heading',{level:1,name:'文枢使用指南',exact:true}).waitFor();
 assert.equal(await frame.locator('h1').count(),1,'The guide document owns the sole h1');
 assert.equal(await frame.locator('meta[name="wenshu-help-edition"]').getAttribute('content'),edition);
 assert.equal(await frame.locator('article section').count(),12);
 assert.equal(await frame.locator('figure.screenshot img').count(),10);
 const artifact=await frame.evaluate(async()=>{
  await Promise.all([...document.images].map(async img=>{img.loading='eager';await img.decode();}));
  return {badAnchors:[...document.querySelectorAll('a[href^="#"]')].filter(a=>!document.getElementById(a.hash.slice(1))).map(a=>a.hash),
   horizontalOverflow:document.scrollingElement.scrollWidth>innerWidth+1,
   badImages:[...document.images].filter(img=>!img.complete||!img.naturalWidth||!img.alt||!img.src.startsWith('data:')).length,
   scripts:document.scripts.length,externalStyles:document.querySelectorAll('link[rel="stylesheet"]').length};
 });
 assert.deepEqual(artifact,{badAnchors:[],horizontalOverflow:false,badImages:0,scripts:0,externalStyles:0});
 assert.equal(await frame.locator('a[download],a[href*="/download"]').count(),0);
 assert.equal(await dialog.locator('.preset-catalog,.preset-search,.preset-groups').count(),0);
 assert.equal(await dialog.getByRole('button',{name:'打开写作示例',exact:true}).count(),0);
 assert.equal(await dialog.getByRole('button',{name:/添加.*示例|预置示例|安装/}).count(),0,'Help remains read-only even when the catalog contains extra items');
 return {frame,locator};
}
async function restored(page,dialog,returnTarget){
 await dialog.waitFor({state:'detached'});await frames(page);
 if(await returnTarget.isVisible())assert(await returnTarget.evaluate(node=>node===document.activeElement),'Closing returns focus to the guide opener or compact-menu trigger');
 else assert(page.viewportSize().width<480,'Desktop closing keeps its return target visible');
}
async function bounds(page,dialog,iframe){
 const outer=await dialog.boundingBox(),inner=await iframe.boundingBox();assert(outer&&inner);
 assert(outer.x>=-1&&outer.x+outer.width<=page.viewportSize().width+1,'Dialog fits the viewport width');
 assert(outer.y>=-1&&outer.y+outer.height<=page.viewportSize().height+1,'Dialog and close control fit the viewport height');
 assert(inner.width>200&&inner.height>250,'The document retains usable reading space');
 assert(inner.x>=outer.x&&inner.x+inner.width<=outer.x+outer.width+1);
}
async function run(theme,width,name,fn,catalogMode='normal'){
 if(only.size&&!only.has(name))return;
 console.log(JSON.stringify({theme,width,name,status:'started'}));const env=await open(theme,width,catalogMode);
 try{
  await fn(env);assert.deepEqual(env.state.errors,[]);assert.deepEqual(env.state.assetErrors,[]);assert.deepEqual(env.state.writes,[]);
  assert.deepEqual(env.state.downloads,[]);assert.deepEqual(env.state.downloadRequests,[]);assert.deepEqual(env.state.unexpectedPresetReads,[]);
  report.cases.push({theme,width,name,status:'passed',guideCatalogReads:env.state.requests.filter(r=>r.path==='/api/presets').length,
   guideViewReads:env.state.requests.filter(r=>r.path===guideEndpoint).length,apiRequests:env.state.requests.length,writeCalls:0,downloadRequests:0,
   responsiveSetup:width<480?'Open through desktop navigation, then resize the dialog to the target width; baseline sidebar is hidden at this width.':'Open directly; closing verifies focus restoration.'});
  console.log(JSON.stringify({theme,width,name,status:'passed'}));
 }catch(error){await env.page.screenshot({path:path.join(out,`${theme}-${width}-${name}-failure.png`),animations:'disabled',timeout:3000}).catch(()=>{});throw new Error(`${theme}/${width}/${name}: ${error.message}`,{cause:error});}
 finally{await env.context.close();}
}
try{
 for(const theme of themes)for(const width of widths){
  await run(theme,width,'single-guide-title-and-close',async({page,state})=>{
   let {dialog,returnTarget}=await openHelp(page);let {frame,locator}=await guideFrame(dialog);await bounds(page,dialog,locator);
   const before=await page.evaluate(()=>({x:scrollX,y:scrollY}));
   const metrics=await frame.evaluate(()=>({height:document.scrollingElement.scrollHeight,viewport:innerHeight}));assert(metrics.height>metrics.viewport+300,'The complete manual remains scrollable inside its iframe');
   const box=await locator.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+Math.min(180,box.height/2));await page.mouse.wheel(0,650);
   await waitFrameScroll(frame,true);assert.deepEqual(await page.evaluate(()=>({x:scrollX,y:scrollY})),before,'Manual scrolling leaves the outer app in place');
   await frame.evaluate(()=>scrollTo({top:0,behavior:'instant'}));await waitFrameScroll(frame,false);
   await page.screenshot({path:path.join(out,`${theme}-${width}-guide.png`),animations:'disabled'});
   const firstCatalogReads=state.requests.filter(r=>r.path==='/api/presets').length;
   await dialog.getByRole('button',{name:'关闭弹窗',exact:true}).focus();await page.keyboard.press('Escape');await restored(page,dialog,returnTarget);
   ({dialog,returnTarget}=await openHelp(page));await guideFrame(dialog);await dialog.getByRole('button',{name:'关闭弹窗',exact:true}).click();await restored(page,dialog,returnTarget);
   assert(state.requests.filter(r=>r.path==='/api/presets').length>firstCatalogReads,'Reopening reads the catalog again, including StrictMode effect replay');assert.equal(state.requests.filter(r=>r.path===guideEndpoint).length,2);
  });
  await run(theme,width,'extra-catalog-items-remain-readonly',async({page})=>{
   const {dialog,returnTarget}=await openHelp(page);const {locator}=await guideFrame(dialog);await bounds(page,dialog,locator);
   await dialog.getByRole('button',{name:'关闭弹窗',exact:true}).click();await restored(page,dialog,returnTarget);
  },'with-examples');
  await run(theme,width,'missing-guide-no-fallback',async({page,state})=>{
   const {dialog,returnTarget}=await openHelp(page);await dialog.getByText('使用指南暂不可用，请检查帮助资源。',{exact:true}).waitFor();
   assert.equal(await dialog.locator('iframe').count(),0);assert.equal(state.requests.filter(r=>r.path===guideEndpoint).length,0);
   assert(!state.requests.some(r=>r.path.includes('qa-other-example')||r.path.includes('qa-writing-example')));
   await dialog.getByRole('button',{name:'关闭弹窗',exact:true}).click();await restored(page,dialog,returnTarget);
  },'missing');
  await run(theme,width,'failed-guide-request-retry',async({page,state})=>{
   const {dialog,returnTarget}=await openHelp(page);await dialog.getByRole('alert').filter({hasText:'隔离目录暂不可用'}).waitFor();assert.equal(await dialog.locator('iframe').count(),0);
   const failedCatalogReads=state.requests.filter(r=>r.path==='/api/presets').length;state.catalogMode='normal';await dialog.getByRole('button',{name:'重新载入',exact:true}).click();const {locator}=await guideFrame(dialog);await bounds(page,dialog,locator);
   assert.equal(await dialog.getByRole('alert').count(),0);assert.equal(state.requests.filter(r=>r.path==='/api/presets').length,failedCatalogReads+1);assert.equal(state.requests.filter(r=>r.path===guideEndpoint).length,1);
   await dialog.getByRole('button',{name:'关闭弹窗',exact:true}).focus();await page.keyboard.press('Escape');await restored(page,dialog,returnTarget);
  },'failed');
 }
 for(const width of widths)await run('demo',width,'demo-has-no-guide-entry',async({page,state})=>{
  assert.equal(await page.getByRole('button',{name:'使用指南',exact:true}).count(),0);assert.equal(await page.getByRole('menuitem',{name:'使用指南',exact:true}).count(),0);
  assert.equal(await page.getByRole('button',{name:'展开更多功能',exact:true}).count(),0);assert.equal(await page.getByRole('dialog',{name:'使用指南',exact:true}).count(),0);
  assert.equal(state.requests.filter(r=>r.path.startsWith('/api/presets')).length,0);
 });
 report.status='passed';
}catch(error){report.status='failed';report.error=String(error);report.stack=error.stack+'\n'+(error.cause?.stack||'');process.exitCode=1;}
finally{await browser.close();await fs.writeFile(path.join(out,'acceptance.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,cases:report.cases.length,error:report.error,stack:report.stack,realApiCalls:0,realModelCalls:0,writeCalls:0,downloadRequests:0}));}
