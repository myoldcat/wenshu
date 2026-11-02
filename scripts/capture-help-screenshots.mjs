// Reproducible manual illustrations. Every API call stays within these synthetic fixtures.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire(new URL('../frontend/package.json', import.meta.url));
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const positional=[];let sourceDir=process.env.WENSHU_MANUAL_SOURCE_DIR;
for(let index=2;index<process.argv.length;index++){
 const value=process.argv[index];
 if(value==='--source-dir'){
  sourceDir=process.argv[++index];assert(sourceDir,'--source-dir requires a directory');
 }else if(value==='--help'){
  console.log('Usage: node scripts/capture-help-screenshots.mjs [BASE_URL] [OUTPUT_DIR] [--source-dir DIRECTORY]');
  console.log('Manual sources default to ignored docs-dev/manual; WENSHU_MANUAL_SOURCE_DIR overrides that default.');
  process.exit(0);
 }else{assert(!value.startsWith('--'),`Unknown option: ${value}`);positional.push(value);}
}
assert(positional.length<=2,'Provide at most BASE_URL and OUTPUT_DIR');
const source=path.resolve(sourceDir||path.join(root,'docs-dev/manual'));
const base=(positional[0]||'http://127.0.0.1:4173').replace(/\/$/,'');
const out=path.resolve(positional[1]||path.join(source,'screenshots'));
const only=new Set((process.env.CAPTURE_HELP_ONLY||'').split(',').filter(Boolean));
const titles=['怎样把服务需求转化为改进建议','公共服务窗口的流程观察方法','用证据组织一篇调研报告','社区数字服务的使用指引','访谈记录与事实核对清单','让服务说明更容易理解'];
const summaries=['围绕明确问题组织材料，区分观察、解释与行动建议。用可回看的原始记录支持判断，并交代建议适用的条件。','沿着到达、咨询、办理与反馈四个环节记录服务体验；观察口径一致，才能形成可比较的资料。','从原始记录出发建立证据链，以问题组织结构，避免把材料逐条堆砌成正文。'];
const blocks=[
 {id:'b1',kind:'heading',level:1,text:'从一个清楚的问题开始'},
 {id:'b2',kind:'paragraph',text:'一篇可使用的调研报告，先要说明准备回答什么问题。围绕明确对象和具体环节整理资料，比罗列宽泛主题更容易形成清晰的判断。'},
 {id:'b3',kind:'paragraph',text:'观察记录、受访者观点和作者解释应当区分保存。记录要交代时间、对象和条件；引用保留原意，不能把个别意见写成所有人的结论。'},
 {id:'b4',kind:'heading',level:2,text:'把发现推进为行动建议'},
 {id:'b5',kind:'paragraph',text:'建议需要回答由谁采取什么行动、具备哪些条件，以及如何检查效果。没有可靠数据时保留定性判断和待确认事项，不猜测精确数值。'},
 {id:'b6',kind:'paragraph',text:'交付前核对人名、日期、数字、引语与图片，并回到原始记录检查关键论断。流畅的文字和工具提示都不能代替作者判断。'}];
const documents=titles.map((title,index)=>({id:`demo-source-${index+1}`,revisionId:`demo-revision-${index+1}`,title,originalTitle:title,status:'ready',kind:'资料',read:true,favorite:index===0||index===2,selected:index<3,source:'原创练习资料',date:`2026-10-${28-index}`,publishedAt:`2026-10-${28-index}`,createdAt:`2026-10-${28-index}T02:00:00Z`,updatedAt:'2026-10-28T02:00:00Z',summary:summaries[index%3],tags:['公共服务','调研方法'],classificationPaths:[{primary:true,nodes:[{id:'politics_governance',name:'政治与治理'},{id:'public-service',name:'公共服务'},{id:'research-methods',name:index<3?'调研方法':'服务优化'}]}],articleType:index===2?'technical':'opinion',articleTypeLabel:index===2?'技术类':'观点类',language:'zh',languageCode:'zh',languageLabel:'中文',needsTranslation:false,bodyWordCount:286+index*92,blocks:structuredClone(blocks),assets:[],imageAssets:[],warnings:[],cardCover:{status:'completed'}}));
const content=[
 '公共服务的改进，应从办事人的实际经历和明确的问题出发。围绕咨询、准备材料和办理等环节整理观察记录，有助于发现值得进一步核对的具体情形。\n\n调研应保留记录的对象、时间与条件，区分可直接核对的事实、受访者的观点和作者的解释。对尚未得到支持的判断，需要补充资料或收窄表述。',
 '分析服务体验时，可以分别检查流程衔接、信息说明和特殊情形处置。将不同来源的记录按问题组织，能够减少逐条复述材料造成的重复。\n\n记录中出现相似现象，并不自动证明原因相同。解释因果之前，还需要检查时间顺序、共同条件以及其他可能的解释。',
 '近期可先梳理常见咨询和材料说明，明确负责更新的岗位，并在小范围内试行新的信息表达。建议应与已说明的问题对应，避免泛泛要求加强管理。\n\n中期可根据试行反馈完善流程衔接，建立定期复核机制。评价时同时关注服务对象的经历和工作人员负担，说明指标口径与比较时期。'];
const project={id:'demo-writing',title:'公共服务体验改进的思路与建议',topic:'如何把公共服务体验调研转化为可执行建议',version:7,mode:'strict',brief:{topic:'如何把公共服务体验调研转化为可执行建议',articleTitle:'公共服务体验改进的思路与建议',purpose:'面向工作讨论，以问题和证据组织论述，建议写清行动主体与适用条件。',targetWords:1800,workflow:'chapter',automation:'manual',interactiveGeneration:'chapter',structureTemplate:'research-report-decision',writingStyle:'official',style:'政府公文',template:'调研报告（决策参考型）'},modelId:'demo-model',sourceIds:documents.slice(0,3).map(d=>d.id),sources:documents.slice(0,3).map(d=>({sourceId:d.id,revisionId:d.revisionId,title:d.title,blocks:d.blocks})),materialSelection:{confirmed:true,passages:[],recommendationId:null},sections:['从服务经历出发明确研究范围','以具体记录识别改进方向','分阶段推进可复核的优化措施'].map((title,index)=>({id:`demo-section-${index+1}`,title,goal:['说明对象和条件，梳理服务需求。','区分事实、观点与解释，识别具体环节。','近期试行，中期完善，明确复核办法。'][index],version:1,mode:'inherit',targetWords:600,content:content[index],evidence:[],manual:true,locked:false})),runs:[],illustrations:[],outlineConfirmed:true,workflowPendingSteps:[],illustrationReview:{status:'skipped'},review:{version:7,checkedAt:'2026-10-28T03:00:00Z',citationReviewCompleted:false,disposition:{status:'skipped'},issues:[],wordCounts:{total:476,sections:[]}},createdAt:'2026-10-28T02:00:00Z',updatedAt:'2026-10-28T03:00:00Z'};
// Register original-source locations without pretending the demonstration was model verified.
for(let index=0;index<2;index++){
 const source=documents[index],quote=source.blocks[2].text;
 project.sections[index].content=project.sections[index].content.replace('\n\n','[E1]\n\n');
 project.sections[index].evidence=[{id:'E1',sourceId:source.id,revisionId:source.revisionId,blockId:'b3',title:source.title,quote,start:0,end:Array.from(quote).length}];
}
const model={id:'demo-model',name:'常用文字模型',model:'text-model',provider:'openai',baseUrl:'https://model.example.test/v1',enabled:true,isDefault:true,hasApiKey:true,version:1,timeout:120,maxOutputTokens:8192,maxInputTokens:64000};
let catalog;
try{catalog=JSON.parse(await fs.readFile(path.join(source,'fixtures/prompts.json'),'utf8'));}
catch(error){throw new Error('手册开发源未提供或缺少 fixtures/prompts.json；请用 --source-dir DIRECTORY 或 WENSHU_MANUAL_SOURCE_DIR 指定手册源目录。',{cause:error});}
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
await fs.mkdir(out,{recursive:true});
const state={errors:[],requests:[],blocked:[],screenshots:[],realApiCalls:0,realModelCalls:0};
const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1,reducedMotion:'reduce'});
await context.addInitScript(()=>localStorage.setItem('koohub-appearance','civic'));
await context.routeWebSocket('**',socket=>socket.close());
await context.route('**/api/**',async route=>{
 const req=route.request(),url=new URL(req.url()),p=url.pathname,m=req.method();let body={};try{body=req.postDataJSON()||{};}catch{}
 state.requests.push({method:m,path:p});const json=value=>route.fulfill({status:200,json:value});
 if(p==='/api/ai/writing-activity')return json({active:false});
 if(p==='/api/wiki/build-scope')return json({documents:documents.slice(3).map(d=>({id:d.id,title:d.title,revisionId:d.revisionId})),total:6,eligible:3,excluded:{built:2,active:1,empty:0},limited:false,limit:20});
 if(!['GET','HEAD'].includes(m)){state.blocked.push({method:m,path:p});return route.fulfill({status:409,json:{detail:'演示截图不执行保存或生成。'}});}
 if(p==='/api/documents')return json({documents,total:documents.length});
 const match=p.match(/^\/api\/documents\/([^/]+)(.*)$/);if(match){const doc=documents.find(d=>d.id===match[1]);if(match[2]==='/ai')return json({ai:{revisionId:doc.revisionId,summary:{status:'succeeded',result:{summary:doc.summary,keyPoints:[{text:'明确问题，保留对象、时间与条件。',blockIds:['b2']},{text:'区分事实、观点与解释。',blockIds:['b3']},{text:'建议与发现对应，并说明复核办法。',blockIds:['b5']}],keywords:['调研方法','证据','公共服务']}},classification:{status:'succeeded',result:{classificationPaths:doc.classificationPaths,applied:true,articleType:doc.articleType}}},jobs:[]});if(match[2]==='/card-cover')return json({cardCover:{status:'completed',revisionId:doc.revisionId}});return json({document:doc});}
 if(p==='/api/models')return json({models:[model],preferences:{defaultModelId:model.id,autoProcess:false,autoTranslate:true,idleProcess:false,idleStart:'00:00',idleEnd:'08:00'}});
 if(p==='/api/jobs')return json({jobs:[],counts:{queued:0,running:0,failed:0,succeeded:0,total:0}});
 if(p==='/api/writing/projects')return json({projects:[project],capabilities:{maxConcurrentProjects:3}});
 if(p===`/api/writing/projects/${project.id}`)return json({project});
 if(p.endsWith('/generation-plan'))return json({plan:{allowed:true,issues:[],strategy:'chapter'}});
 if(p==='/api/home')return json({topics:[{id:'public-service',name:'公共服务',count:6}],writing:{total:1,totalWords:338,phaseCounts:{brief:0,outline:0,body:0,images:0,review:1,ready:0}}});
 if(p==='/api/prompts')return json(catalog);
 if(['/api/wiki','/api/wiki/knowledge'].includes(p))return json({nodes:[],edges:[],topics:[],jobs:[],stats:{pending:3,stale:0},snapshotId:'demo-graph'});
 if(p==='/api/wiki/build-all')return json({runs:[]});
 if(p==='/api/ai/taxonomy')return json({categories:[],paths:[]});
 return json({items:[],entries:[],topics:[],images:[],nodes:[],edges:[],results:[],prompts:[],total:0,ai:{},jobs:[]});
});
const page=await context.newPage();page.on('pageerror',error=>state.errors.push(String(error)));
const settle=async()=>{await page.waitForTimeout(350);await page.evaluate(()=>document.fonts.ready);};
async function shot(name,selector){if(only.size&&!only.has(name))return;await settle();const locator=selector?page.locator(selector).filter({visible:true}).first():null;const filename=`${name}-civic.png`;if(locator)await locator.screenshot({path:path.join(out,filename),animations:'disabled'});else await page.screenshot({path:path.join(out,filename),animations:'disabled'});state.screenshots.push(filename);console.log(filename);}
const nav=()=>page.getByRole('navigation',{name:'主导航',exact:true});
async function instructions(){
 await nav().getByRole('button',{name:'智能指令',exact:true}).click();await page.locator('.prompt-category-tabs button').filter({hasText:/^文风/}).click();await settle();
 assert.equal(await page.locator('.prompt-effective-instructions,.prompt-effective-note').count(),0);
 assert.equal(await page.locator('.prompt-category-tabs button').filter({hasText:'结构模板'}).count(),0);
 assert(!/历史兼容|旧版原文|固定规则与历史兼容说明/.test(await page.locator('.prompt-workspace').innerText()));
 await shot('instructions','.app-main');
}
try{
 if(only.size===1&&only.has('instructions')){
 await page.goto(`${base}/?theme=civic`,{waitUntil:'networkidle'});await instructions();
 }else{
 await page.goto(`${base}/?theme=civic`,{waitUntil:'networkidle'});await shot('home');
 await nav().getByRole('button',{name:'知源文库',exact:true}).click();await page.locator('.document-card').first().waitFor();await shot('library');
 await page.locator('.document-card-title').first().click();await page.locator('.reader-panel').waitFor();await shot('reader','.reader-panel');await page.getByRole('button',{name:'关闭资料阅读',exact:true}).click();
 const settings=page.getByRole('button',{name:'系统设置',exact:true});if(await settings.isVisible())await settings.click();else{await page.getByRole('button',{name:'展开更多功能',exact:true}).click();await page.getByRole('menuitem',{name:'系统设置',exact:true}).click();}await shot('settings','.workspace-popover');await page.keyboard.press('Escape');
 await instructions();
 await nav().getByRole('button',{name:'写作空间',exact:true}).click();await page.getByRole('button',{name:'开始新的写作',exact:true}).click();await page.locator('#writing-topic').fill('如何把公共服务体验调研转化为可执行建议');await page.locator('#writing-purpose').fill('面向工作讨论，以问题和证据组织论述，建议写清行动主体与适用条件。');await shot('writing','.app-main');
 await page.reload({waitUntil:'networkidle'});await nav().getByRole('button',{name:'写作空间',exact:true}).click();await settle();await page.getByRole('button',{name:/公共服务体验改进的思路与建议/}).first().click();await settle();await shot('body','.app-main');
 await page.locator('[data-writing-step="outline"]').click();await shot('outline','.app-main');
 await page.locator('[data-writing-step="review"]').click();await shot('review','.app-main');
 await nav().getByRole('button',{name:'知识图谱',exact:true}).click();await page.getByRole('button',{name:'构建 Wiki',exact:true}).click();await page.locator('#gw-build-panel').waitFor();await shot('wiki','#gw-build-panel');
 }
 state.status=state.errors.length?'failed':'passed';
}catch(error){state.status='failed';state.failure=String(error);await page.screenshot({path:path.join(out,'capture-failure.png')});console.error(error);process.exitCode=1;}
finally{await fs.writeFile(path.join(out,only.size?`capture-${[...only].join('-')}-report.json`:'capture-report.json'),JSON.stringify(state,null,2));await browser.close();}
