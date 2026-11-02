import React,{useEffect,useId,useMemo,useState} from 'react';
import {BookOpen,CalendarBlank,CaretRight,FileText,FolderOpen,FolderSimple,Globe,MagnifyingGlass,X} from '@phosphor-icons/react';
import {filterCategoryTree} from './category-tree-model.mjs';
import {categorySelectionLabel,categorySelectionState,categoryTreeSelectionId} from './category-selection.mjs';
import {buildDirectoryTree,DIRECTORY_GROUPS,directoryGrouping} from './directory-grouping.mjs';
import {OriginalLanguageBadge} from './OriginalLanguageBadge';
import './category-tree.css';

function ancestorsFor(node,selection,parents=[],path=[]){
 const nodePath=node.kind==='root'?[]:[...path,node.key];
 if(node.selection===selection||(node.kind!=='root'&&categoryTreeSelectionId(nodePath)===selection))return parents;
 for(const child of node.children||[]){
  const found=ancestorsFor(child,selection,node.kind==='root'?parents:[...parents,node.key],nodePath);
  if(found)return found;
 }
 return null;
}

export function CategoryTree({documents=[],tree,mode='topic',onModeChange,categories=[],multiple=false,onMultipleChange,onCategoryChange,onOpenDoc}){
 const treeId=useId();
 const [query,setQuery]=useState('');
 const [openNodes,setOpenNodes]=useState(new Set());
 const [searchClosedNodes,setSearchClosedNodes]=useState(new Set());
 const grouping=directoryGrouping(mode);
 const catalog=useMemo(()=>tree||buildDirectoryTree(documents,mode),[documents,tree,mode]);
 const search=query.trim();
 const visibleTree=useMemo(()=>filterCategoryTree(catalog,search),[catalog,search]);
 const selectedCategories=useMemo(()=>new Set(categories),[categories]);
 const selectionState=useMemo(()=>categorySelectionState(categories,catalog),[categories,catalog]);
 const selectedLabel=categorySelectionLabel(categories,catalog);

 useEffect(()=>{setQuery('');setOpenNodes(new Set());setSearchClosedNodes(new Set())},[mode]);

 useEffect(()=>{
  const ancestors=categories.flatMap(category=>ancestorsFor(catalog,category)||[]);
  if(ancestors.length)setOpenNodes(previous=>new Set([...previous,...ancestors]));
 },[categories,catalog]);

 const changeQuery=value=>{setQuery(value);setSearchClosedNodes(new Set())};
 const changeMode=value=>{if(value!==mode){setQuery('');setOpenNodes(new Set());setSearchClosedNodes(new Set());onModeChange(value)}};
 const moveMode=event=>{
  const index=DIRECTORY_GROUPS.findIndex(group=>group.id===mode);
  const next=event.key==='ArrowRight'?(index+1)%DIRECTORY_GROUPS.length:event.key==='ArrowLeft'?(index+DIRECTORY_GROUPS.length-1)%DIRECTORY_GROUPS.length:event.key==='Home'?0:event.key==='End'?DIRECTORY_GROUPS.length-1:null;
  if(next===null)return;
  event.preventDefault();
  event.currentTarget.parentElement.querySelectorAll('[role="tab"]')[next]?.focus();
  changeMode(DIRECTORY_GROUPS[next].id);
 };
 const toggle=key=>(search?setSearchClosedNodes:setOpenNodes)(previous=>{
  const next=new Set(previous);
  if(next.has(key))next.delete(key);else next.add(key);
  return next;
 });

 const renderNode=(node,depth=0,path=[])=>{
  const nodePath=[...path,node.key];
  const selection=categoryTreeSelectionId(nodePath);
  const isSelected=selectionState.checked.has(selection);
  const isPartial=selectionState.indeterminate.has(selection);
  if(node.kind==='document')return <li className={`category-tree-document ${isSelected?'is-active':''}`} key={node.key}>
   {multiple&&<input type="checkbox" checked={isSelected} onChange={()=>onCategoryChange(selection)} aria-label={`选择文档 ${node.name}`}/>}
   <button type="button" onClick={()=>onOpenDoc(node.id)} title={node.name} aria-label={`阅读 ${node.name}`}><FileText size={13} aria-hidden="true"/><span><OriginalLanguageBadge doc={node.document}/>{node.name}</span></button>
  </li>;
  const isOpen=search?!searchClosedNodes.has(node.key):openNodes.has(node.key);
  const childId=`${treeId}-${encodeURIComponent(node.key)}`;
  const NodeIcon=node.kind==='source'?Globe:node.kind==='date'?CalendarBlank:isOpen?FolderOpen:FolderSimple;
  return <li className={`category-tree-node ${depth===0?'category-tree-subject':''}`} key={node.key}>
   <div className={`category-tree-tag-row ${isSelected?'is-active':''}`}>
    <button type="button" className="category-tree-expander" aria-label={`${isOpen?'收起':'展开'} ${node.pathLabel}`} aria-expanded={isOpen} aria-controls={childId} onClick={()=>toggle(node.key)}><CaretRight className={isOpen?'is-open':''} size={11} aria-hidden="true"/></button>
    {multiple?<label className="category-tree-tag-filter category-tree-check-filter" title={node.pathLabel}>
     <input type="checkbox" checked={isSelected} ref={input=>{if(input)input.indeterminate=isPartial}} onChange={()=>onCategoryChange(selection)} aria-label={`选择${grouping.noun} ${node.pathLabel}`}/>
     <NodeIcon size={15} aria-hidden="true"/>
     <span className="category-tree-node-name">{node.name}</span><span className="category-tree-count">{node.count}</span>
    </label>:<button type="button" className="category-tree-tag-filter" aria-pressed={isSelected} title={node.pathLabel} onClick={()=>onCategoryChange(selection)}>
     <NodeIcon size={15} aria-hidden="true"/>
     <span className="category-tree-node-name">{node.name}</span><span className="category-tree-count">{node.count}</span>
    </button>}
   </div>
   {isOpen&&<ul className="category-tree-children" id={childId}>{node.children.map(child=>renderNode(child,depth+1,nodePath))}</ul>}
  </li>;
 };

 return <aside className="category-tree" aria-label={`文档${grouping.title}`}>
  <header className="category-tree-heading">
   <div><h2>{grouping.title}</h2></div>
   <div className="category-tree-heading-actions"><label className="category-tree-multiple"><input type="checkbox" checked={multiple} onChange={event=>onMultipleChange(event.target.checked)}/>多选</label>{mode!=='tag'&&<button type="button" className="category-tree-fold-all" onClick={()=>{setQuery('');setOpenNodes(new Set())}} title={`收起全部${grouping.label}目录`}>收起</button>}</div>
  </header>
  <div className="category-tree-modes" role="tablist" aria-label="目录分组方式">{DIRECTORY_GROUPS.map(group=><button type="button" role="tab" key={group.id} id={`${treeId}-tab-${group.id}`} aria-controls={`${treeId}-panel`} aria-selected={mode===group.id} tabIndex={mode===group.id?0:-1} onKeyDown={moveMode} onClick={()=>changeMode(group.id)}>{group.label}</button>)}</div>
  <div className="category-tree-browser" role="tabpanel" id={`${treeId}-panel`} aria-labelledby={`${treeId}-tab-${mode}`}>
  <label className="category-tree-search">
   <MagnifyingGlass size={15} aria-hidden="true"/>
   <input value={query} onChange={event=>changeQuery(event.target.value)} placeholder={grouping.search} aria-label={grouping.search}/>
   {query&&<button type="button" aria-label="清空目录搜索" onClick={()=>changeQuery('')}><X size={13}/></button>}
  </label>
  <button type="button" className={`category-tree-all ${!categories.length?'is-active':''}`} aria-pressed={!categories.length} onClick={()=>onCategoryChange('全部')}>
   <BookOpen size={18} aria-hidden="true"/><span>全部文档</span><span className="category-tree-count">{catalog.count}</span>
  </button>
  <div className={`category-tree-current ${!categories.length?'is-empty':''}`}>
   <span title={categories.length?selectionState.labels.join('；'):undefined} aria-live="polite">{categories.length?(multiple?`已选 ${selectionState.count} ${mode==='topic'&&!selectionState.containsDocuments?'类':'项'}`:`正在查看 · ${selectedLabel}`):mode==='topic'?'一篇文章可以属于多个分类。':''}</span>
   {!!categories.length&&<button type="button" onClick={()=>onCategoryChange('全部')} aria-label={`清除${grouping.noun}筛选`}>清除<X size={12}/></button>}
  </div>
  <div className="category-tree-scroll">
   {search&&<p className="category-tree-results" role="status">找到 {visibleTree.count} 份相关文档</p>}
   {mode==='tag'?<div className="category-tag-groups">{visibleTree.children.map(group=><section className="category-tag-group" aria-label={group.name} key={group.key}>
    <h3>{group.name}</h3>
    <div className="category-tag-options">{group.children.map(tag=>{
     const isSelected=selectedCategories.has(tag.selection);
     return <button type="button" className="category-tag-chip" key={tag.key} data-tag-kind={tag.tagKind} data-tag-selection={tag.selection} aria-label={tag.pathLabel} aria-pressed={isSelected} title={tag.pathLabel} onClick={()=>onCategoryChange(isSelected&&!multiple?'全部':tag.selection)}>
      <span className="category-tag-label">{tag.tagKind==='language'?tag.name.replace(/^语言[：:]\s*/,''):tag.name}</span><span className="category-tree-count">{tag.count}</span>
     </button>;
    })}</div>
   </section>)}</div>:<ul className="category-tree-subjects">{visibleTree.children.map(node=>renderNode(node))}</ul>}
   {!visibleTree.children.length&&<div className="category-tree-empty"><FolderSimple size={27} weight="light" aria-hidden="true"/><strong>{search?`没有匹配的${grouping.noun}或文档`:'目录等待第一份资料'}</strong><p>{search?'试试文档标题或更短的关键词。':`导入资料后，会在这里按${grouping.label}归档。`}</p>{search&&<button type="button" onClick={()=>changeQuery('')}>清空搜索</button>}</div>}
  </div>
  </div>
  <footer className="category-tree-footer"><span className="category-tree-footer-dot" aria-hidden="true"/><p>{grouping.footer[0]}<br/>{grouping.footer[1]}</p></footer>
 </aside>;
}

