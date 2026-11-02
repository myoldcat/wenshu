import React,{useEffect,useMemo,useRef,useState} from 'react';
import {BookOpen,PencilLine,Graph,MagnifyingGlass,UploadSimple,FunnelSimple,ArrowRight,FileText,X,Check,Star,CaretRight,Plus,LinkSimple,Globe,ImageSquare,Quotes,CheckCircle} from '@phosphor-icons/react';
import '@fontsource-variable/noto-serif-sc';
import '@fontsource-variable/noto-sans-sc';
import {WritingWorkspace} from './Writing';
import {useWriting} from './useWriting';
import {structureTemplatePatch} from './writing-presets';
import {useWritingActivity} from './useWritingActivity';
import {TemplateWorkspace} from './Knowledge';
import {GraphWorkspace} from './GraphWorkspace';
import {StatusPopover,PromptWorkspace} from './WorkspacePanels';
import {CategoryTree} from './CategoryTree';
import {buildDirectoryTree,directoryGrouping,directoryTagSelection} from './directory-grouping.mjs';
import {categoriesForSelectionMode,categorySelectionLabel,categorySelectionScope,normalizeCategorySelections,reconcileCategorySelections,toggleCategorySelection} from './category-selection.mjs';
import {SettingsPanel} from './SettingsPanel';
import {DocumentCardGrid} from './DocumentCardGrid';
import {WorkspaceSidebar} from './WorkspaceSidebar';
import {useWritingPageStage} from './useWritingPageStage';
import './writing-page-transition.css';
import {HomeWorkspace} from './HomeWorkspace';
import {BackToHome} from './BackToHome';
import {isDemoMode,demoDestination,updateDemoTitle} from './demo-mode';
import {MAX_WRITING_SOURCES} from './writing-materials';
import {api,connectionMessage,normalizeDocument} from './local-api';
import {useDocumentLibrary,useDocumentSearch} from './useDocumentLibrary';
import {useModelSettings,useDocumentAI} from './useAI';
import {DocumentReader} from './DocumentReader';
import {DocumentDeleteDialog} from './DocumentDeleteDialog';
import {ImageLibrary} from './ImageLibrary';
import PresetLibrary from './PresetLibrary';
import {currentReaderDerivedMetadata} from './translation-view.mjs';
import './local-library.css';

function Dialog({title,subtitle,children,onClose,wide=false,showTitle=true}){
 const panel=useRef(null);
 useEffect(()=>{const previous=document.activeElement;const el=panel.current;el?.querySelector('input,button,select')?.focus();const fn=e=>{if(e.key==='Escape')onClose();if(e.key==='Tab'){const a=[...el.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea,select,[tabindex="0"]')].filter(n=>n.offsetParent!==null);if(e.shiftKey&&document.activeElement===a[0]){e.preventDefault();a.at(-1)?.focus()}else if(!e.shiftKey&&document.activeElement===a.at(-1)){e.preventDefault();a[0]?.focus()}}};document.addEventListener('keydown',fn);return()=>{document.removeEventListener('keydown',fn);previous?.focus?.()}},[]);
 return <div className="dialog-backdrop" onMouseDown={e=>e.target===e.currentTarget&&onClose()}><section className={`dialog ${wide?'wide':''}`} role="dialog" aria-modal="true" aria-label={title} ref={panel}><header className={`dialog-head${showTitle?'':' is-titleless'}`}>{showTitle&&<div><h2>{title}</h2>{subtitle&&<p>{subtitle}</p>}</div>}<button className="icon-btn" aria-label="关闭弹窗" onClick={onClose}><X size={22}/></button></header>{children}</section></div>
}
function SourceIcon({doc,small=false}){const Icon=doc.kind==='观点'?Quotes:doc.kind==='概念'?BookOpen:FileText;return <span className={`source-icon ${small?'small':''} kind-${doc.kind}`}><Icon size={small?21:26}/></span>}
function locatorLabel(locator,index){if(!locator)return `段落 ${index+1}`;if(typeof locator==='string')return locator;return [locator.page?`第 ${locator.page} 页`:null,`段落 ${(locator.ordinal??index)+1}`,locator.heading||locator.section||null].filter(Boolean).join(' · ')}

export function App(){
 const demoMode=isDemoMode();
 const library=useDocumentLibrary();
 const {documents,patchDocument}=library;
 const imageLibraryRevision=useMemo(()=>JSON.stringify(documents.map(doc=>[doc.id,doc.revisionId,doc.title,doc.originalTitle,doc.translatedTitle])),[documents]);
 const selected=documents.filter(doc=>doc.selected&&doc.status==='ready').map(doc=>doc.id);
 const [nav,setNav]=useState(()=>demoDestination('home')),[query,setQuery]=useState(''),[tab,setTab]=useState('all');
 const noFavorites=tab==='saved'&&!documents.some(doc=>doc.favorite);
 useEffect(()=>{updateDemoTitle(nav)},[nav]);
 const [filterOpen,setFilterOpen]=useState(false),[treeMode,setTreeMode]=useState('topic'),[categories,setCategories]=useState([]),[categoryMultiple,setCategoryMultiple]=useState(false),[source,setSource]=useState('全部'),[period,setPeriod]=useState('全部时间'),[sort,setSort]=useState('new');
 const [readerId,setReaderId]=useState(null),[readerTab,setReaderTab]=useState('摘要'),[modal,setModal]=useState(null),[toast,setToast]=useState(null);
 const [deleteTarget,setDeleteTarget]=useState(null);
 const [stage,setStage,cancelStageTransition]=useWritingPageStage({nav,demoMode}),[collapsed,setCollapsed]=useState(false),[statusPanel,setStatusPanel]=useState(null);
 const writingNavigationEpoch=useRef(0),[writingStageEpoch,setWritingStageEpoch]=useState(0);
  const [graphFullscreen,setGraphFullscreen]=useState(false);
 useWritingActivity(nav==='writing' && stage!=='projects');
 const writing=useWriting(),{project,setProject}=writing;
 const [homeOverview,setHomeOverview]=useState(null),[homeOverviewLoading,setHomeOverviewLoading]=useState(true),[homeOverviewError,setHomeOverviewError]=useState(''),[homeOverviewRetry,setHomeOverviewRetry]=useState(0),[homeWritingError,setHomeWritingError]=useState('');
 const overviewLoaded=useRef(false);
 const writingListKey=useMemo(()=>JSON.stringify(writing.projects.map(item=>[item.id,item.version,item.updatedAt])),[writing.projects]);
 useEffect(()=>{
  if(nav!=='home')return;
  let stopped=false,timer,controller;
  const load=async()=>{
   if(document.hidden){timer=setTimeout(load,10000);return}
   controller=new AbortController();
   if(!overviewLoaded.current)setHomeOverviewLoading(true);
   try{const result=await api('/home',{signal:controller.signal});if(!stopped){overviewLoaded.current=true;setHomeOverview(result);setHomeOverviewError('')}}
   catch(error){if(!stopped&&error.name!=='AbortError')setHomeOverviewError(connectionMessage(error))}
   finally{if(!stopped){setHomeOverviewLoading(false);timer=setTimeout(load,10000)}}
  };
  load();return()=>{stopped=true;clearTimeout(timer);controller?.abort()};
 },[nav,homeOverviewRetry,writingListKey]);
 const refreshHomeWriting=()=>writing.refreshList().then(()=>setHomeWritingError('')).catch(error=>setHomeWritingError(connectionMessage(error)));
 useEffect(()=>{if(nav==='home')refreshHomeWriting()},[nav]);
 const modelSettings=useModelSettings();
 const [settingsTab,setSettingsTab]=useState('模型设置'),[internet,setInternet]=useState(false),[verify,setVerify]=useState(false);
 const [importTab,setImportTab]=useState('file'),[urls,setUrls]=useState(''),[importError,setImportError]=useState(''),[busy,setBusy]=useState(false);
 const [readerDetail,setReaderDetail]=useState(null),[readerLoading,setReaderLoading]=useState(false),[readerError,setReaderError]=useState(''),[readerMatch,setReaderMatch]=useState(null),[readerRevision,setReaderRevision]=useState(null),[readerRetry,setReaderRetry]=useState(0);
 const [readerLanguageView,setReaderLanguageView]=useState('auto');
 const processing=useDocumentAI(readerId,readerRevision,()=>{library.refresh({quiet:true});library.refreshJobs()});
 const openModelSettings=()=>{setReaderId(null);setSettingsTab('模型设置');setStatusPanel('settings')};
 const locateEvidence=id=>{setReaderLanguageView('original');setReaderMatch(id);if(readerMatch===id&&readerLanguageView==='original')requestAnimationFrame(()=>document.getElementById(`source-block-${id}`)?.scrollIntoView({block:'center',behavior:demoMode||matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'}))};
 const searchRef=useRef(null),toastTimer=useRef(null),fileRef=useRef(null);
 const readerMetadata=documents.find(d=>d.id===readerId)||(readerDetail?.id===readerId?readerDetail:null)||(readerId?{id:readerId,title:'文章阅览',tags:[],classificationPaths:[]}:null);
 const readDoc=readerMetadata?{...readerMetadata,...(readerDetail?.id===readerId?readerDetail:{}),...currentReaderDerivedMetadata(readerMetadata,readerDetail),favorite:readerMetadata.favorite,read:readerMetadata.read,selected:readerMetadata.selected,tags:readerMetadata.tags,classificationPaths:readerMetadata.classificationPaths,classificationManual:readerMetadata.classificationManual}:null;
 useEffect(()=>{document.querySelector('.app-main')?.scrollTo({top:0})},[nav,stage]);
 useEffect(()=>{
  if(!readerId)return;
  const previous=document.activeElement,panel=document.querySelector('.reader-panel');
  panel?.querySelector('button')?.focus();
  const trap=e=>{if(e.key!=='Tab'||document.querySelector('.document-delete-dialog'))return;const items=[...panel.querySelectorAll('button:not([disabled]),input,select,textarea,a[href]')].filter(n=>n.offsetParent!==null);if(e.shiftKey&&document.activeElement===items[0]){e.preventDefault();items.at(-1)?.focus()}else if(!e.shiftKey&&document.activeElement===items.at(-1)){e.preventDefault();items[0]?.focus()}};
  document.addEventListener('keydown',trap);return()=>{document.removeEventListener('keydown',trap);previous?.focus?.()};
 },[readerId]);
 const notify=message=>{setToast(message);clearTimeout(toastTimer.current);toastTimer.current=setTimeout(()=>setToast(null),3600)};
  const go=next=>{writingNavigationEpoch.current+=1;setWritingStageEpoch(writingNavigationEpoch.current);cancelStageTransition();setNav(demoDestination(next));setGraphFullscreen(false);setReaderId(null);setStatusPanel(null)};
 const sidebarNavigate=next=>{if(next==='writing')setStage('projects');go(next)};
 const changeWritingStage=next=>{if(writingStageEpoch===writingNavigationEpoch.current)setStage(next)};
 const saveDocument=(id,changes)=>patchDocument(id,changes).catch(error=>notify(`保存失败：${connectionMessage(error)}`));
 const openDoc=value=>{const id=typeof value==='object'?value.id:value;const doc=typeof value==='object'?value:search.results.find(item=>item.id===id)||documents.find(item=>item.id===id);if(!doc){notify('未找到这份来源资料，请在知源文库中确认。');return}setStatusPanel(null);setReaderId(id);setReaderTab('摘要');setReaderLanguageView(doc.matchBlockId && doc.matchLanguage !== 'zh-CN' ? 'original' : 'auto');setReaderMatch(doc.matchBlockId||null);setReaderRevision(doc.revisionId||null);const saved=documents.find(item=>item.id===id);if(saved&&!saved.read)saveDocument(id,{read:true})};
 const toggleSelected=id=>{const doc=documents.find(item=>item.id===id);if(doc?.status==='ready')saveDocument(id,{selected:!doc.selected})};
 const favorite=id=>{const doc=documents.find(item=>item.id===id);if(doc)saveDocument(id,{favorite:!doc.favorite})};
 const requestDocumentDeletion=doc=>{if(doc?.id)setDeleteTarget({id:doc.id,title:doc.title})};
 const documentDeleted=(id,result)=>{setDeleteTarget(null);setReaderId(current=>current===id?null:current);setReaderDetail(current=>current?.id===id?null:current);setHomeOverviewRetry(value=>value+1);library.refreshJobs();notify(result?.pendingCleanup ? '文档已删除，暂被占用的原图将在释放后自动清理。' : '文档及相关图片、知识图谱和检索内容已删除。')};
 const clearSelected=async()=>{await Promise.all(selected.map(id=>saveDocument(id,{selected:false})))};
 useEffect(()=>{
  if(!readerId)return;
  const controller=new AbortController();setReaderLoading(true);setReaderError('');setReaderDetail(null);
  const params=readerRevision?`?revision_id=${encodeURIComponent(readerRevision)}`:'';
  api(`/documents/${encodeURIComponent(readerId)}${params}`,{signal:controller.signal}).then(result=>{if(!controller.signal.aborted)setReaderDetail(normalizeDocument(result.document))}).catch(error=>{if(error.name!=='AbortError')setReaderError(connectionMessage(error))}).finally(()=>{if(!controller.signal.aborted)setReaderLoading(false)});
  return()=>controller.abort();
 },[readerId,readerRevision,readerRetry]);
 useEffect(()=>{if(!readerDetail||!readerMatch)return;const frame=requestAnimationFrame(()=>document.getElementById(`source-block-${readerMatch}`)?.scrollIntoView({block:'center',behavior:demoMode||matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'}));return()=>cancelAnimationFrame(frame)},[readerDetail,readerMatch,readerLanguageView]);
 useEffect(()=>{const fn=e=>{if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();go('library');requestAnimationFrame(()=>searchRef.current?.focus())}if(e.key==='Escape'){setReaderId(null);setFilterOpen(false);setStatusPanel(null)}};window.addEventListener('keydown',fn);return()=>window.removeEventListener('keydown',fn)},[]);
 const categoryTree=useMemo(()=>buildDirectoryTree(documents,treeMode),[documents,treeMode]);
 useEffect(()=>{setCategories(current=>reconcileCategorySelections(current,categoryTree))},[categoryTree]);
 const categoryName=categorySelectionLabel(categories,categoryTree);
 const directoryRangeLabel=treeMode==='topic'?categoryName:`${directoryGrouping(treeMode).label} · ${categoryName}`;
 const categoryScope=useMemo(()=>categorySelectionScope(categories,documents,categoryTree),[categories,documents,categoryTree]);
 const categoryScopeIds=useMemo(()=>categoryScope===null?null:new Set(categoryScope),[categoryScope]);
 const replaceCategory=value=>{setTreeMode('topic');setCategories(normalizeCategorySelections(value))};
 const changeDocumentTag=(label,tag)=>{if(tag?.kind==='articleType'){setTreeMode('tag');setCategories([directoryTagSelection(tag)])}else replaceCategory(label)};
 const changeDirectoryMode=mode=>{if(mode!==treeMode){setTreeMode(mode);setCategories([])}};
 const changeTreeCategory=value=>setCategories(current=>toggleCategorySelection(current,value,categoryMultiple,categoryTree));
 const changeCategoryMode=multiple=>{setCategoryMultiple(multiple);setCategories(current=>categoriesForSelectionMode(current,multiple,categoryTree))};
 const categorySelectValue=categories.length&&treeMode!=='topic'?'__directory_scope__':categories.length>1?'__selected_categories__':categories[0]||'全部';
 const filters=[categories.length>0&&{id:'category',label:directoryRangeLabel,clear:()=>setCategories([])},source!=='全部'&&{id:'source',label:source,clear:()=>setSource('全部')},period!=='全部时间'&&{id:'period',label:period,clear:()=>setPeriod('全部时间')}].filter(Boolean);
 const since=period==='全部时间'?'':new Date(Date.now()-7*86400000).toLocaleDateString('en-CA');
 const viewScope=tab==='selected'?selected.filter(id=>categoryScopeIds===null||categoryScopeIds.has(String(id))):categoryScope;
 const search=useDocumentSearch({query,category:'全部',sourceIds:viewScope,source,tab,since,sort,revision:library.revision});
 useEffect(()=>{if(!query.trim()&&sort==='relevance')setSort('new')},[query,sort]);
 const docs=query.trim()?search.results.filter(result=>!library.isDocumentDeleted(result.id)).map(result=>({...result,...documents.find(doc=>doc.id===result.id),snippet:result.snippet,matchBlockId:result.matchBlockId,revisionId:result.revisionId})).filter(d=>tab!=='selected'||(d.selected&&d.status==='ready')):documents.filter(d=>(tab!=='saved'||d.favorite)&&(tab!=='selected'||(d.selected&&d.status==='ready'))&&(categoryScopeIds===null||categoryScopeIds.has(String(d.id)))&&(source==='全部'||d.source===source)&&(!since||d.date>=since)).sort((a,b)=>sort==='title'?a.title.localeCompare(b.title,'zh'):(b.createdAt||b.date).localeCompare(a.createdAt||a.date));
 const tagOptions=[...new Set(documents.flatMap(d=>d.tags.length?d.tags:['未分类']))];
 const resetFilters=()=>{setQuery('');setCategories([]);setSource('全部');setPeriod('全部时间');setTab('all')};
 const startWriting=()=>{go('writing');if(writing.newProject(selected))setStage('brief')};
 const homeSearch=value=>{resetFilters();setQuery(value||'');if(value?.trim())setSort('relevance');go('library');requestAnimationFrame(()=>searchRef.current?.focus())};
 const homeNavigate=destination=>{if(destination==='favorites'){resetFilters();setTab('saved');go('library')}else if(destination==='library'){resetFilters();go('library')}else if(destination==='writing'){go('writing');setStage('projects')}else go(destination)};
 const continueHomeProject=async item=>{
  if(writing.busy){notify('正在处理文稿，请稍后再打开。');return}
  let next=project;
  if(item?.id!==project.id){
   if(writing.dirty){notify('当前文稿有未保存修改，请先继续当前文稿并保存。');return}
   next=await writing.selectProject(item.id);
  }
  if(next){go('writing');setStage(next.outlineConfirmed?'editor':next.sections?.length?'outline':'brief')}
 };
 const newHomeProject=()=>{
  if(writing.busy){notify('正在处理文稿，请稍后再新建。');return}
  if(writing.dirty){notify('当前文稿有未保存修改，请先继续当前文稿并保存。');return}
  if(selected.length>MAX_WRITING_SOURCES){notify(`一次写作最多选 ${MAX_WRITING_SOURCES} 篇资料，请先调整知源文库已选范围。`);return}
  if(writing.newProject(selected)){go('writing');setStage('brief')}
 };
 async function importFiles(files){
  const items=Array.from(files||[]);setImportError('');if(!items.length)return;if(items.some(file=>!/\.html?$/i.test(file.name))){setImportError('本阶段支持 HTML 文件，请选择 .html 或 .htm 文件。');return}if(items.length>50){setImportError('每次最多导入 50 个文件，请分批导入。');return}if(items.some(file=>file.size>64*1024*1024)){setImportError('单个文件最大支持 64 MiB，请分批整理后再导入。');return}if(items.reduce((total,file)=>total+file.size,0)>255*1024*1024){setImportError('单次上传总量需小于 256 MiB，请分批导入。');return}setBusy(true);
  try{const result=await library.submitImport('files',items);finishImport(result.jobs?.length||items.length)}catch(error){setImportError(connectionMessage(error))}finally{setBusy(false);if(fileRef.current)fileRef.current.value=''}
 }
 function finishImport(count){setModal(null);setUrls('');go('library');resetFilters();setStatusPanel('ingest');notify(`已提交 ${count} 个入库任务，可在入库进度查看处理结果`)}
 async function importUrls(){const arr=urls.split(/\n/).map(value=>value.trim()).filter(Boolean);if(!arr.length){setImportError('先粘贴一个或多个网页地址。');return}if(arr.length>50){setImportError('每次最多导入 50 个网址。');return}try{arr.forEach(value=>{const url=new URL(value);if(!['http:','https:'].includes(url.protocol))throw new Error()})}catch{setImportError('请输入以 https:// 或 http:// 开头的完整地址。');return}setBusy(true);setImportError('');try{const result=await library.submitImport('urls',arr);finishImport(result.jobs?.length||arr.length)}catch(error){setImportError(connectionMessage(error))}finally{setBusy(false)}}
 const manageJob=(id,action)=>library.jobAction(id,action).catch(error=>notify(connectionMessage(error)));
 const graphExpanded=nav==='knowledge'&&graphFullscreen, sidebarCompact=collapsed||graphExpanded;
 return <div className={`app-shell workspace-shell ${sidebarCompact?'sidebar-collapsed':''} ${graphExpanded?'graph-fullscreen':''}`} style={{'--sidebar-width':sidebarCompact?'76px':'216px'}}>
  <WorkspaceSidebar collapsed={sidebarCompact} onToggleCollapsed={()=>{if(graphExpanded){setGraphFullscreen(false);setCollapsed(false)}else setCollapsed(v=>!v)}} nav={nav} onNavigate={sidebarNavigate} statusPanel={statusPanel} onToggleStatus={key=>{setModal(null);setStatusPanel(p=>p===key?null:key)}} onOpenHelp={()=>{setStatusPanel(null);setModal('presets')}} onOpenSettings={()=>{setModal(null);setStatusPanel(value=>value==='settings'?null:'settings')}} pendingCount={(library.jobCounts.queued||0)+(library.jobCounts.running||0)} writingPendingCount={(library.writingJobCounts.queued||0)+(library.writingJobCounts.running||0)} selectedCount={selected.length}/>
  <main className={`app-main view-${nav}`}>
  {!demoMode&&nav!=='home'&&(nav!=='writing'||stage==='projects')&&<BackToHome onClick={()=>go('home')}/>}
  {nav==='home'&&<HomeWorkspace documents={documents} documentsLoading={library.loading} documentsError={library.error} projects={writing.projects} currentProject={project} writingDirty={writing.dirty} writingLoading={writing.loading} writingError={homeWritingError||writing.error} writingBusy={writing.busy} jobCounts={library.jobCounts} writingJobCounts={library.writingJobCounts} jobsLoading={library.jobsLoading} writingJobsLoading={library.writingJobsLoading} jobsError={library.jobsError} writingJobsError={library.writingJobsError} overview={homeOverview} overviewLoading={homeOverviewLoading} overviewError={homeOverviewError} onRetryOverview={()=>setHomeOverviewRetry(value=>value+1)} onRetryDocuments={library.refresh} onRetryWriting={refreshHomeWriting} onOpenDoc={openDoc} onContinueProject={continueHomeProject} onNewProject={newHomeProject} onImport={()=>{setImportError('');setModal('import')}} onSearch={homeSearch} onOpenTopic={topic=>{resetFilters();setTreeMode('topic');setCategories([`cat:${topic.id}`]);go('library')}} onNavigate={homeNavigate} onOpenStatus={key=>setStatusPanel(key==='selected'?'selection':key)} modalOpen={!!modal||!!readerId||!!statusPanel}/>}
  <div className="library-layout" hidden={nav!=='library'}><section className="library-main"><header className="library-heading"><div><h1>{demoMode?'文档阅读':'让阅读，留下回响'}</h1>{!demoMode&&<p>收集、理解、连接，让每一份文档成为思考的起点。</p>}</div><button className="btn primary import-button" onClick={()=>{setModal('import');setImportError('')}}><UploadSimple size={21}/>导入文档</button></header>
  <div className="library-tools"><div className="tabs" role="tablist" aria-label="资料视图">{[['all','全部'],['saved','收藏'],['selected','已选']].map(([id,label])=><button key={id} role="tab" aria-selected={tab===id} className={tab===id?'active':''} onClick={()=>setTab(id)}>{label}</button>)}</div>
    <div className="search-field library-search"><MagnifyingGlass size={18}/><input ref={searchRef} value={query} onChange={e=>{if(!query.trim()&&e.target.value.trim())setSort('relevance');setQuery(e.target.value)}} placeholder="搜索资料、观点或概念" aria-label="搜索资料、观点或概念"/>{query?<button className="icon-btn" aria-label="清空搜索" onClick={()=>setQuery('')}><X size={18}/></button>:<kbd>Ctrl K</kbd>}</div>
    <button className={`filter-trigger ${filterOpen||filters.length?'active':''}`} aria-label="筛选资料" aria-expanded={filterOpen} onClick={()=>setFilterOpen(!filterOpen)}><FunnelSimple size={18}/>筛选{filters.length>0&&<span className="filter-count">{filters.length}</span>}</button></div>
  {filterOpen&&<div className="filter-panel"><label>分类与主题<select value={categorySelectValue} onChange={e=>{if(!['__selected_categories__','__directory_scope__'].includes(e.target.value))replaceCategory(e.target.value)}}><option>全部</option>{categories.length>0&&treeMode!=='topic'&&<option value="__directory_scope__" disabled>{directoryRangeLabel}</option>}{categories.length>1&&treeMode==='topic'&&<option value="__selected_categories__" disabled>{categoryName}</option>}{categories.length===1&&treeMode==='topic'&&!tagOptions.includes(categories[0])&&<option value={categories[0]}>{categoryName}</option>}{tagOptions.map(t=><option key={t}>{t}</option>)}</select></label><label>来源<select value={source} onChange={e=>setSource(e.target.value)}><option>全部</option>{[...new Set(documents.map(d=>d.source))].map(s=><option key={s}>{s}</option>)}</select></label><label>入库时间<select value={period} onChange={e=>setPeriod(e.target.value)}><option>全部时间</option><option>最近 7 天</option></select></label><button className="btn quiet" onClick={resetFilters}>重置</button></div>}
  {filters.length>0&&<div className="active-filters">{filters.map(filter=><button className="pill" key={filter.id} onClick={filter.clear}>{filter.label}<X size={12}/></button>)}</div>}
  <div className="list-caption"><span>{tab==='selected'?(query.trim()?'已选资料 · 搜索结果':'已选资料'):query.trim()?'全文搜索结果':tab==='saved'?'最近收藏':'全部资料'}<span className="count">{query.trim()?search.total:docs.length}</span>{query.trim()&&search.total>docs.length&&<small className="search-page-count">已显示 {docs.length} 份</small>}</span><div className="list-caption-actions">{query.trim()&&search.total>docs.length&&<button className="selection-shortcut" disabled={search.loading} onClick={search.loadMore}>{search.loading?'正在检索…':'加载更多'}</button>}<button className="selection-shortcut" onClick={()=>setStatusPanel('selection')}>已选 {selected.length} 份<ArrowRight size={14}/></button><select aria-label="资料排序" value={sort} onChange={e=>setSort(e.target.value)}>{query.trim()&&<option value="relevance">相关程度</option>}<option value="new">最近添加</option><option value="title">标题顺序</option></select></div></div>
  {(library.error||library.jobsError||search.error)&&<div className="local-library-message is-error" role="alert"><span>{query.trim()&&search.error?search.error:library.error||library.jobsError}{documents.length>0&&(library.error||library.jobsError)?' 当前显示上次读取的资料。':''}</span><button className="btn quiet" onClick={()=>{library.refresh();library.refreshJobs();if(query.trim())search.retry()}}>重新连接</button></div>}
  {(library.loading||search.loading)&&<div className="local-library-message" role="status">{query.trim()?'正在检索本地文档全文…':'正在读取本地知源文库…'}</div>}
  <DocumentCardGrid documents={docs} onOpenDoc={openDoc} onFavorite={favorite} onToggleSelected={toggleSelected} onCategoryChange={changeDocumentTag} onOpenImage={id=>{openDoc(id);setReaderTab('图片')}} onDelete={requestDocumentDeletion} autoSelectCovers={modelSettings.preferences.autoProcess && !!modelSettings.preferences.summaryModelId} coverModelId={modelSettings.preferences.summaryModelId} coverModelVersion={modelSettings.models.find(model=>model.id===modelSettings.preferences.summaryModelId)?.version}/>
  {!docs.length&&!library.loading&&!search.loading&&!library.error&&!search.error&&<div className="empty-state">{noFavorites?<Star size={36} weight="light"/>:<MagnifyingGlass size={36} weight="light"/>}<h3>{tab==='selected'?(selected.length?'没有匹配的已选资料':'还没有已选资料'):noFavorites?'还没有收藏资料，请先添加收藏':documents.length?'换个线索试试':'从第一份文档开始'}</h3><p>{tab==='selected'&&!selected.length?'在全部资料中勾选文章，即可在这里集中查看和取消选材。':noFavorites?'在全部资料中点击文章的收藏图标，即可在这里集中查看。':documents.length?'没有找到匹配资料，可以减少筛选条件或换一个关键词。':'导入本地文件，资料会保存在文枢中，随时可以继续阅读与检索。'}</p><button className="btn" onClick={tab!=='all'||documents.length?resetFilters:()=>{setModal('import');setImportError('')}}>{tab!=='all'||documents.length?'查看全部资料':'导入文档'}</button></div>}</section>
  <CategoryTree documents={documents} tree={categoryTree} mode={treeMode} onModeChange={changeDirectoryMode} categories={categories} multiple={categoryMultiple} onMultipleChange={changeCategoryMode} onCategoryChange={changeTreeCategory} onOpenDoc={openDoc}/></div>
  {!demoMode&&<div className="workspace-view" hidden={nav!=='images'}><ImageLibrary active={nav==='images'} revision={imageLibraryRevision} onOpenDoc={value=>{openDoc(value);setReaderTab('图片')}}/></div>}
  {nav==='writing'&&<WritingWorkspace stage={stage} onStageChange={changeWritingStage} writing={writing} documents={documents} selectedIds={selected} models={modelSettings.models} onOpenSettings={openModelSettings} onOpenDoc={openDoc} onToast={notify}/>}{!demoMode&&<><div className="workspace-view graph-view" hidden={nav!=='knowledge'}><GraphWorkspace fullscreen={graphExpanded} onFullscreenChange={setGraphFullscreen} documents={documents} onToast={notify} onOpenDoc={openDoc} active={nav==='knowledge'} modelName={modelSettings.models.find(item=>item.id===modelSettings.preferences.summaryModelId)?.name || ''}/></div><div className="workspace-view" hidden={nav!=='templates'}><TemplateWorkspace active={nav==='templates'} onToast={notify} onUseTemplate={(name,template)=>{if(writing.busy||writing.loading){notify('正在读取或保存文稿，请稍后再应用模板。');return}if(template?.type==='输出模板'){notify('导出采用统一版式，暂不支持切换输出模板。');return}if(project.id&&!writing.newProject(selected)){notify('当前文稿有未保存修改，请先保存后再使用模板。');return}setProject(p=>template?.type==='范文参考'?{...p,structureTemplate:'custom',template:'从范文提取结构',templateSections:[],sampleReferenceText:template.sections.join('\n'),sampleStructureApplied:true}:{...p,...structureTemplatePatch(template.id)});go('writing');setStage('brief');notify(`已选择「${name}」模板`)}}/></div><div className="workspace-view" hidden={nav!=='prompts'}><PromptWorkspace onToast={notify} active={nav==='prompts'}/></div></>}</main>
  <StatusPopover kind={statusPanel} onClose={()=>setStatusPanel(null)} documents={documents} selected={selected} onToggleSelected={toggleSelected} onClearSelected={clearSelected} onOpenDoc={openDoc} onStartWriting={startWriting} onContinueWriting={()=>{go('writing');setStage(project.outlineConfirmed?'editor':project.sections.length?'outline':'brief')}} onImport={()=>{setStatusPanel(null);setImportError('');setModal('import')}} jobs={library.jobs} jobCounts={library.jobCounts} jobsError={library.jobsError} writingJobs={library.writingJobs} writingJobCounts={library.writingJobCounts} writingJobsError={library.writingJobsError} onRefreshJobs={library.refreshJobs} onRetryJob={id=>manageJob(id,'retry')} onCancelJob={id=>manageJob(id,'cancel')} project={project.id||writing.dirty?project:null} writingDirty={writing.dirty} stage={stage} settingsContent={<SettingsPanel settings={modelSettings} settingsTab={settingsTab} setSettingsTab={setSettingsTab} internet={internet} setInternet={setInternet} verify={verify} setVerify={setVerify} onClose={()=>setStatusPanel(null)}/> }/>
  {readDoc&&<DocumentReader key={readDoc.id} doc={readDoc} selected={selected.includes(readDoc.id)} onToggleSelected={()=>toggleSelected(readDoc.id)} onFavorite={()=>favorite(readDoc.id)} onClose={()=>setReaderId(null)} tab={readerTab} onTabChange={setReaderTab} loading={readerLoading} error={readerError} onRetry={()=>setReaderRetry(value=>value+1)} processing={processing} settings={modelSettings} onSettings={openModelSettings} languageView={readerLanguageView} onLanguageChange={setReaderLanguageView} matched={readerMatch} onLocate={locateEvidence} onSaveTags={tags=>patchDocument(readDoc.id,{tags})} onSaveClassification={classificationPaths=>patchDocument(readDoc.id,{classificationPaths})} onSaveArticleType={readDoc.revisionId && readDoc.revisionId===readerMetadata?.revisionId ? articleType=>patchDocument(readDoc.id,{articleType,revisionId:readDoc.revisionId}) : undefined} onDelete={requestDocumentDeletion} revision={readerRevision}/>}
  {deleteTarget&&<DocumentDeleteDialog key={deleteTarget.id} document={deleteTarget} onDelete={library.deleteDocument} onDeleted={documentDeleted} onClose={()=>setDeleteTarget(null)}/>}
  {modal==='presets'&&<Dialog title="使用指南" wide showTitle={false} onClose={()=>setModal(null)}><PresetLibrary/></Dialog>}
  {modal==='import'&&<Dialog title="收集新的线索" onClose={()=>setModal(null)}><div className="import-tabs"><button className={importTab==='url'?'active':''} onClick={()=>setImportTab('url')}><LinkSimple size={18}/>网页链接</button><button className={importTab==='file'?'active':''} onClick={()=>setImportTab('file')}><UploadSimple size={18}/>本地文件</button></div>
   {importTab==='url'?<div className="dialog-body"><label className="field">网页地址<textarea rows={5} disabled={busy} value={urls} onChange={e=>{setUrls(e.target.value);setImportError('')}} placeholder={'https://example.com/article\n可同时粘贴多个链接，每行一个'}/></label><p className="field-hint">每次最多 50 个链接。</p></div>:<div className="dialog-body"><button className="upload-zone" disabled={busy} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(!busy)importFiles(e.dataTransfer.files)}} onClick={()=>fileRef.current?.click()}><UploadSimple size={34} weight="light"/><strong>{busy?'正在提交入库任务…':'拖入文件，或点击选择'}</strong><span>HTML / HTM · 单文件最多 64 MiB</span></button><input ref={fileRef} type="file" hidden accept=".html,.htm,text/html" multiple onChange={e=>importFiles(e.target.files)}/><p className="field-hint">每次最多 50 个文件。</p><p className="field-hint">重复文档自动识别。</p></div>}
   {importError&&<p className="form-error" role="alert">{importError}</p>}<div className="dialog-footer"><button className="btn quiet" onClick={()=>setModal(null)}>关闭</button><button className="btn primary" disabled={busy} onClick={importTab==='url'?importUrls:()=>fileRef.current?.click()}>{busy?'正在提交…':importTab==='url'?'加入知源文库':'选择文件'}<ArrowRight size={17}/></button></div>
  </Dialog>}
  {toast&&<div className="toast" role="status"><CheckCircle size={19} weight="fill"/><span>{toast}</span><button aria-label="关闭提示" onClick={()=>setToast(null)}><X size={15}/></button></div>}
 </div>
}
