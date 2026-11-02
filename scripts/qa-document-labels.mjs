// Full App acceptance with synthetic documents and intercepted API boundaries.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../frontend/package.json', import.meta.url));
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const base=(process.argv[2]||'http://127.0.0.1:4173').replace(/\/$/,'');
const out=path.resolve(process.argv[3]||path.join(root,'output/document-labels/browser-staged'));
await fs.mkdir(out,{recursive:true});
const manualHtml=await fs.readFile(path.join(root,'resources/presets/manual/user-guide.html'),'utf8');
const clone=value=>structuredClone(value),sorted=value=>[...value].sort();
const labels={fact:'事实类',opinion:'观点类',technical:'技术类'};
const sourceIds=['qa-news','qa-comment','qa-technical','qa-unknown'];
const paths=[{primary:true,nodes:[{id:'cybersecurity',name:'网络安全'},{id:'cybersecurity-identity',name:'身份权限'},{id:'cybersecurity-access',name:'访问控制'}]}];
function fixtures(){
 const definitions=[['qa-news','隔离新闻报道：安全措施发布','fact',['共同标签']],['qa-comment','隔离新闻评述：安全治理建议','opinion',['事实类','共同标签']],['qa-technical','隔离技术文章：访问控制配置','technical',['共同标签']],['qa-unknown','隔离未标注文档：文章整理',null,['待整理']]];
 return definitions.map(([id,title,type,tags],index)=>({id,title,revisionId:'rev-'+id,status:'ready',kind:'资料',read:true,favorite:false,selected:false,
  source:'隔离资料库',date:`2026-10-${27-index}`,createdAt:`2026-10-${27-index}T00:00:00Z`,updatedAt:'2026-10-27T00:00:00Z',
  summary:'本资料仅用于标签、文章类型和帮助界面的隔离验收。',tags,classificationPaths:index===3?[]:clone(paths),classificationManual:false,
  ...(type?{articleType:type,articleTypeLabel:labels[type]}:{}),articleTypeManual:false,
  bodyWordCount:42,language:'zh',languageCode:'zh',languageLabel:'中文',languageTag:null,needsTranslation:false,
  blocks:[{id:'block-'+id,kind:'paragraph',text:'这是隔离验收正文，用于检查文章标签、固定版本和阅读操作。'}],assets:[],imageAssets:[],warnings:[],cardCover:{status:'completed'}}));
}
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
const result={status:'running',synthetic:true,cases:[],realApiCalls:0,realModelCalls:0,installCalls:0};
async function open(theme,options={}){
 const context=await browser.newContext({viewport:{width:1600,height:1100},reducedMotion:'reduce',acceptDownloads:true});
 const state={documents:fixtures(),requests:[],patches:[],unexpectedWrites:[],unexpectedPresetReads:[],errors:[],catalogMode:options.catalogMode||'normal',rejectPatches:!!options.rejectPatches,staleAI:!!options.staleAI};
 const autoTypes=Object.fromEntries(state.documents.map(doc=>[doc.id,doc.articleType||null]));
 await context.addInitScript(theme=>localStorage.setItem('koohub-appearance',theme==='demo'?'civic':theme),theme);
 await context.route('**/api/**',async route=>{
  const request=route.request(),url=new URL(request.url()),endpoint=url.pathname,method=request.method();
  let body=null;try{body=request.postDataJSON();}catch{}
  state.requests.push({method,path:endpoint,query:url.search,body});
  const json=(value,status=200)=>route.fulfill({status,json:clone(value)});
  if(endpoint==='/api/search'){
   if(!['GET','POST'].includes(method)){state.unexpectedWrites.push({method,path:endpoint,body});return json({detail:'Blocked unexpected search mutation'},409);}
   const scope=method==='POST'?body?.sourceIds:null;
   const docs=state.documents.filter(doc=>scope==null||scope.includes(doc.id));
   return json({results:docs.map(doc=>({...doc,snippet:'隔离全文检索命中。',matchBlockId:'block-'+doc.id,matchLanguage:'zh'})),total:docs.length});
  }
  if(endpoint==='/api/ai/writing-activity')return json({active:false});
  const documentMatch=endpoint.match(/^\/api\/documents\/([^/]+)(.*)$/);
  if(documentMatch){
   const id=decodeURIComponent(documentMatch[1]),suffix=documentMatch[2],doc=state.documents.find(item=>item.id===id);
   if(doc&&suffix===''&&method==='PATCH'){
    state.patches.push({id,body:clone(body)});
    if(state.rejectPatches||body?.revisionId!==doc.revisionId)return json({detail:'资料版本已变化，请重新打开当前版本后重试。'},409);
    if(!Object.keys(body||{}).every(key=>['articleType','revisionId'].includes(key))||!['fact','opinion','technical',null].includes(body.articleType)){
     state.unexpectedWrites.push({method,path:endpoint,body});return json({detail:'Blocked unexpected document mutation'},409);
    }
    doc.articleType=body.articleType===null?autoTypes[id]:body.articleType;
    doc.articleTypeLabel=labels[doc.articleType]||null;doc.articleTypeManual=body.articleType!==null;
    return json({document:doc});
   }
   if(doc&&method==='GET'&&suffix==='')return json({document:doc});
   if(doc&&method==='GET'&&suffix==='/ai')return json({ai:{revisionId:doc.revisionId,stale:state.staleAI,classificationAvailable:true,
    summary:{status:'succeeded',result:{summary:doc.summary,keyPoints:[],keywords:['隔离验收']}},
    classification:{status:'succeeded',result:{classificationPaths:clone(doc.classificationPaths),applied:true,articleType:autoTypes[id]}}},jobs:[]});
   if(doc&&method==='GET'&&suffix==='/card-cover')return json({cardCover:{status:'completed',revisionId:doc.revisionId}});
  }
  if(endpoint==='/api/presets'&&method==='GET'){
   if(state.catalogMode==='failed')return json({detail:'隔离目录暂不可用，请重新载入。'},503);
   const items=state.catalogMode==='missing'?[{id:'qa-other-example',title:'其他示例不应显示在帮助中',kind:'ai-agent',language:'zh',viewUrl:'/api/presets/qa-other-example/view'}]:[];
   if(state.catalogMode!=='missing')items.push({id:'wenshu-user-guide',title:'文枢使用指南',kind:'manual',language:'zh',sourceName:'文枢配套文档',viewUrl:'/api/presets/wenshu-user-guide/view',downloadUrl:'/api/presets/wenshu-user-guide/download'});
   return json({version:'qa-only',items,example:null});
  }
  if(method==='GET'&&endpoint==='/api/presets/wenshu-user-guide/view')return route.fulfill({status:200,contentType:'text/html; charset=utf-8',body:manualHtml,
   headers:{'Content-Security-Policy':"sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"}});
  if(method==='GET'&&endpoint==='/api/presets/wenshu-user-guide/download')return route.fulfill({status:200,contentType:'text/html; charset=utf-8',body:manualHtml,headers:{'Content-Disposition':"attachment; filename*=UTF-8''"+encodeURIComponent('文枢使用指南.html')}});
  if(endpoint.startsWith('/api/presets/')){
   if(method==='GET')state.unexpectedPresetReads.push(endpoint);
   else state.unexpectedWrites.push({method,path:endpoint,body});
   return json({detail:'No other catalog item may be opened or changed by this acceptance test'},409);
  }
  if(!['GET','HEAD'].includes(method)){state.unexpectedWrites.push({method,path:endpoint,body});return json({detail:'Acceptance blocked an unexpected mutation'},409);}
  if(endpoint==='/api/documents')return json({documents:state.documents,total:state.documents.length});
  if(endpoint==='/api/models')return json({models:[],preferences:{autoProcess:false,autoTranslate:false,idleProcess:false,classificationModelId:null}});
  if(endpoint==='/api/jobs')return json({jobs:[],counts:{queued:0,running:0,failed:0,succeeded:0,total:0}});
  if(endpoint==='/api/writing/projects')return json({projects:[],capabilities:{}});
  if(endpoint==='/api/home')return json({topics:[],writing:{total:0,totalWords:0}});
  if(endpoint==='/api/ai/taxonomy')return json({categories:[],paths:[]});
  return json({items:[],entries:[],topics:[],images:[],nodes:[],edges:[],results:[],prompts:[],total:0,ai:{},jobs:[]});
 });
 const page=await context.newPage();page.on('pageerror',error=>state.errors.push(String(error)));
 await page.goto(`${base}/?theme=${theme}`,{waitUntil:'networkidle'});
 await page.getByRole('navigation',{name:'主导航',exact:true}).getByRole('button',{name:theme==='demo'?'文档阅读':'知源文库',exact:true}).click();
 await expectIds(page,sourceIds);
 return {context,page,state,tree:page.locator('.category-tree')};
}
async function expectIds(page,ids){
 await page.waitForFunction(expected=>{
  const current=[...document.querySelectorAll('.library-layout:not([hidden]) .document-card')].map(node=>node.dataset.documentId).sort();
  return JSON.stringify(current)===JSON.stringify([...expected].sort());
 },ids,{timeout:10000});
 assert.deepEqual(sorted(await page.locator('.library-layout:not([hidden]) .document-card').evaluateAll(nodes=>nodes.map(n=>n.dataset.documentId))),sorted(ids));
}
const card=(page,id)=>page.locator(`.document-card[data-document-id="${id}"]`);
async function expand(tree,label){const button=tree.getByRole('button',{name:'展开 '+label,exact:true});if(await button.count())await button.click();}
async function choose(tree,title){await tree.locator(`.category-tree-tag-filter[title="${title}"]`).click();}
async function openKnowledge(page,id){
 await card(page,id).locator('.document-card-title').click();await page.locator('.reader-panel').waitFor();
 await page.locator('.reader-sidebar-tabs').getByRole('button',{name:'知识',exact:true}).click();
 const section=page.locator('section[aria-label="文章类型"]');await section.waitFor();return section;
}
async function openHelp(page){
 const direct=page.locator('.sidebar-bottom').getByRole('button',{name:'使用指南',exact:true});
 if(await direct.count()&&await direct.isVisible())await direct.click();
 else{await page.getByRole('button',{name:'展开更多功能',exact:true}).click();await page.getByRole('menuitem',{name:'使用指南',exact:true}).click();}
 const dialog=page.getByRole('dialog',{name:'使用指南',exact:true});await dialog.waitFor();return dialog;
}
async function run(theme,name,fn,options){
 const env=await open(theme,options);
 try{await fn(env);assert.deepEqual(env.state.errors,[]);assert.deepEqual(env.state.unexpectedWrites,[]);assert.deepEqual(env.state.unexpectedPresetReads,[]);
  assert.equal(env.state.requests.filter(request=>request.path.startsWith('/api/presets')&&!['GET','HEAD'].includes(request.method)).length,0);result.cases.push({theme,name,passed:true});
 }catch(error){await env.page.screenshot({path:path.join(out,`${theme}-${name}-failed.png`),animations:'disabled'});throw new Error(`${theme}/${name}: ${error.message}`,{cause:error});}
 finally{await env.context.close();}
}
try{
 for(const theme of ['jade','glass','civic','demo']){
  await run(theme,'tag-directory-scopes',async({page,tree,state})=>{
   assert.equal(await tree.getByRole('tab',{name:'来源',exact:true}).count(),0);await tree.getByRole('tab',{name:'标签',exact:true}).click();
   await tree.getByRole('heading',{name:'标签目录',exact:true}).waitFor();await expand(tree,'文章类型');await expand(tree,'自定义标签');
   for(const label of ['事实类','观点类','技术类','未标注'])assert.equal(await tree.locator(`.category-tree-tag-filter[title="文章类型 / ${label}"]`).count(),1);
   await choose(tree,'文章类型 / 事实类');await expectIds(page,['qa-news']);
   await choose(tree,'自定义标签 / 事实类');await expectIds(page,['qa-comment']);
   await tree.getByRole('button',{name:/全部文档/}).click();await tree.getByRole('checkbox',{name:'多选',exact:true}).check();
   await tree.getByRole('checkbox',{name:'选择标签 文章类型 / 事实类',exact:true}).check();
   await tree.getByRole('checkbox',{name:'选择标签 文章类型 / 观点类',exact:true}).check();
   await tree.getByRole('checkbox',{name:'选择标签 自定义标签 / 共同标签',exact:true}).check();await expectIds(page,['qa-news','qa-comment','qa-technical']);
   const search=page.getByRole('textbox',{name:'搜索资料、观点或概念',exact:true});
   const searchResponse=page.waitForResponse(response=>response.url().includes('/api/search')&&response.request().method()==='POST');await search.fill('隔离');await searchResponse;
   await expectIds(page,['qa-news','qa-comment','qa-technical']);
   const scoped=state.requests.filter(request=>request.path==='/api/search'&&request.method==='POST').at(-1);assert(scoped);assert.deepEqual(sorted(scoped.body.sourceIds),sorted(['qa-news','qa-comment','qa-technical']));assert.equal(new Set(scoped.body.sourceIds).size,3);
   await tree.getByRole('textbox',{name:'查找标签或文档',exact:true}).fill('事实类');await tree.getByRole('status').filter({hasText:'找到 2 份相关文档'}).waitFor();
   await expectIds(page,['qa-news','qa-comment','qa-technical']);await tree.getByRole('button',{name:'清空目录搜索',exact:true}).click();
   await tree.getByRole('button',{name:/全部文档/}).click();await search.fill('');await expectIds(page,sourceIds);
   await tree.getByRole('checkbox',{name:'选择标签 文章类型 / 未标注',exact:true}).check();await expectIds(page,['qa-unknown']);
   await tree.getByRole('tab',{name:'日期',exact:true}).click();await expectIds(page,sourceIds);await tree.getByRole('tab',{name:'标签',exact:true}).click();await expand(tree,'文章类型');
   assert.equal(state.patches.length,0);await page.screenshot({path:path.join(out,`${theme}-tags.png`),animations:'disabled'});
  });
  await run(theme,'typed-badge-collision',async({page,tree,state})=>{
   await card(page,'qa-news').getByRole('button',{name:'文章类型 · 事实类',exact:true}).click();await expectIds(page,['qa-news']);
   assert.equal(await tree.getByRole('tab',{name:'标签',exact:true}).getAttribute('aria-selected'),'true');
   await tree.getByRole('button',{name:/全部文档/}).click();await expectIds(page,sourceIds);
   await card(page,'qa-comment').getByRole('button',{name:'自定义标签 · 事实类',exact:true}).click();await expectIds(page,['qa-comment']);
   assert.equal(state.patches.length,0);if(theme==='demo')assert.equal(await page.getByRole('button',{name:'使用指南',exact:true}).count(),0);
  });
  await run(theme,'article-type-edit-reset',async({page,state})=>{
   let section=await openKnowledge(page,'qa-unknown');assert.equal(await section.getByRole('combobox',{name:'文章类型',exact:true}).inputValue(),'');
   assert(await section.getByRole('button',{name:'保存类型',exact:true}).isDisabled());assert.equal(state.patches.length,0);
   await page.getByRole('button',{name:'关闭资料阅读',exact:true}).click();section=await openKnowledge(page,'qa-news');
   await section.getByRole('combobox',{name:'文章类型',exact:true}).selectOption('opinion');await section.getByRole('button',{name:'保存类型',exact:true}).click();
   await section.locator('[data-tag-kind="articleType"]').filter({hasText:'观点类'}).waitFor();assert.equal(state.documents.find(doc=>doc.id==='qa-news').articleTypeManual,true);
   await section.getByRole('button',{name:'恢复自动',exact:true}).click();await section.locator('[data-tag-kind="articleType"]').filter({hasText:'事实类'}).waitFor();
   await page.waitForFunction(()=>document.querySelector('section[aria-label="文章类型"] select')?.value==='fact');
   assert.equal(await section.getByRole('combobox',{name:'文章类型',exact:true}).inputValue(),'fact');assert.equal(state.documents.find(doc=>doc.id==='qa-news').articleTypeManual,false);
   assert.deepEqual(state.patches,[{id:'qa-news',body:{articleType:'opinion',revisionId:'rev-qa-news'}},{id:'qa-news',body:{articleType:null,revisionId:'rev-qa-news'}}]);
   await page.screenshot({path:path.join(out,`${theme}-article-type.png`),animations:'disabled'});
  });
  await run(theme,'article-type-rejected-and-stale',async({page,state})=>{
   let section=await openKnowledge(page,'qa-news');const before=clone(state.documents);state.rejectPatches=true;
   await section.getByRole('combobox',{name:'文章类型',exact:true}).selectOption('technical');await section.getByRole('button',{name:'保存类型',exact:true}).click();
   await page.getByRole('alert').filter({hasText:'资料版本已变化'}).waitFor();assert.deepEqual(state.documents,before);assert.equal(await section.locator('[data-tag-kind="articleType"]').innerText(),'事实类');
   assert.deepEqual(state.patches,[{id:'qa-news',body:{articleType:'technical',revisionId:'rev-qa-news'}}]);
   await page.getByRole('button',{name:'关闭资料阅读',exact:true}).click();state.staleAI=true;section=await openKnowledge(page,'qa-news');
   await page.getByText('当前阅读的是历史版本，显示这个版本的处理结果。重新打开最新文档后可生成新结果。',{exact:true}).waitFor();
   assert(await section.getByRole('combobox',{name:'文章类型',exact:true}).isDisabled());assert(await section.getByRole('button',{name:'保存类型',exact:true}).isDisabled());assert.equal(state.patches.length,1);
  });
  if(theme!=='demo'){
   await run(theme,'single-help',async({page,state})=>{
    const dialog=await openHelp(page);await dialog.frameLocator('.preset-document-frame').getByRole('heading',{name:'文枢使用指南',exact:true}).waitFor();
    assert.equal(await dialog.locator('iframe').count(),1);assert.equal(await dialog.locator('.preset-catalog,.preset-search,.preset-groups').count(),0);assert.equal(await dialog.getByRole('button',{name:'打开写作示例',exact:true}).count(),0);
    assert.equal(await dialog.locator('.preset-document-frame').getAttribute('src'),'/api/presets/wenshu-user-guide/view');assert.equal(await dialog.getByRole('button',{name:'添加预置示例',exact:true}).count(),0);
    assert.equal(await dialog.getByRole('heading').count(),0,'The guide dialog has no duplicate outer heading');
    assert.equal(await dialog.frameLocator('.preset-document-frame').locator('h1').count(),1,'The document keeps its unique guide heading');
    assert.equal(await dialog.getByRole('link',{name:/下载/}).count(),0);assert.equal(await dialog.locator('a[download],a[href*="/download"]').count(),0);
    assert(!state.requests.some(request=>request.path.endsWith('/download')));assert.equal(state.patches.length,0);
    await page.screenshot({path:path.join(out,`${theme}-help.png`),animations:'disabled'});
   });
   await run(theme,'missing-help-no-fallback',async({page,state})=>{
    const dialog=await openHelp(page);await dialog.getByText('使用指南文件暂不可用，请更新完整软件资料包。',{exact:true}).waitFor();assert.equal(await dialog.locator('iframe').count(),0);
    assert(!state.requests.some(request=>request.path.includes('/qa-other-example/view')));assert.equal(state.patches.length,0);
   },{catalogMode:'missing'});
  }
 }
 await run('civic','help-retry',async({page,state})=>{
  const dialog=await openHelp(page);await dialog.getByRole('alert').filter({hasText:'隔离目录暂不可用'}).waitFor();state.catalogMode='normal';
  await dialog.getByRole('button',{name:'重新载入',exact:true}).click();await dialog.frameLocator('.preset-document-frame').getByRole('heading',{name:'文枢使用指南',exact:true}).waitFor();assert.equal(await dialog.locator('iframe').count(),1);assert.equal(state.patches.length,0);
 },{catalogMode:'failed'});
 result.status='passed';
}catch(error){result.status='failed';result.error=String(error);result.stack=error.stack+'\n'+(error.cause?.stack||'');process.exitCode=1;}
finally{await browser.close();await fs.writeFile(path.join(out,'acceptance.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:result.status,cases:result.cases.length,error:result.error,stack:result.stack,realApiCalls:0,realModelCalls:0,installCalls:0}));}
