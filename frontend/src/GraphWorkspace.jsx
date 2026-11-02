import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Graph, BookOpen, FileText, Buildings, UserCircle, Path, MagnifyingGlass, SlidersHorizontal, X, Plus, Minus, ArrowsOutSimple, ArrowsOut, ArrowsIn, ArrowUpRight, CaretRight, LinkSimple, Info, Check, ArrowsClockwise, WarningCircle, Quotes, ClockCounterClockwise } from '@phosphor-icons/react';
import { useWikiGraph, useWikiPage, useWikiSearch, useWikiNeighborhood, useWikiGroup, useWikiResolution, useWikiBuildScope, useWikiBuildAll } from './useWiki';
import { appendGraphPositions, relationshipNature, edgeIdentity } from './graph-state';
import { afterKnowledgeIdentityChange, boundedSourceSelection, graphScopeKey, inheritTopicScopeCaches, resolutionBody, resolutionEntryIds, resolutionPresentation } from './wiki-unified-state';
import { GraphTopicPicker } from './GraphTopicPicker';
import { useGraphInspectorResize } from './useGraphInspectorResize';
import { G6GraphCanvas } from './G6GraphCanvas';
import { useGraphAnalytics } from './useGraphAnalytics';
import { GraphPresentationControls, GRAPH_METRICS, metricValue } from './GraphPresentationControls';
import { useGraphMaterial } from './useGraphMaterial';
import './graph-workspace.css';
import './graph-material-controls.css';

const TYPES = { concept: { label: '概念', Icon: BookOpen }, entity: { label: '实体', Icon: Buildings }, claim: { label: '观点', Icon: Quotes }, fact: { label: '事实', Icon: Check }, document: { label: '文档', Icon: FileText } };
const RELATIONS = { hierarchy: '层级关系', association: '关联关系', source: '文档关联' };
const JOB_STATUS = { queued: '等待构建', running: '正在构建', succeeded: '构建完成', failed: '构建失败', cancelled: '已取消' };
const jobStageLabel = job => {
  if (/^wiki:\d+\/\d+$/.test(job.stage || '')) return `分段 ${job.stage.slice(5)}`;
  return ({ superseded: '已被新构建替代', preparing: '准备证据', publishing: '保存词条', validating: '核对证据', empty: '无可收录内容' })[job.stage] || '';
};
const short = (value = '', length = 12) => { const chars = Array.from(value || ''); return chars.length > length ? `${chars.slice(0, length).join('')}…` : value; };
const evidenceKey = item => `${item.sourceId}:${item.revisionId}:${item.blockId}:${item.start ?? ''}:${item.end ?? ''}`;
const uniqueEvidence = items => [...new Map((items || []).map(item => [evidenceKey(item), item])).values()];
const nodeSubtype = node => node.type === 'concept' && node.subtype === 'method' ? 'method' : node.type === 'entity' && node.subtype === 'person' ? 'person' : '';
const nodeKind = node => nodeSubtype(node) === 'method' ? { label: '方法', Icon: Path } : nodeSubtype(node) === 'person' ? { label: '人物', Icon: UserCircle } : TYPES[node.type] || TYPES.concept;
const dateText = value => { const date = new Date(value); return Number.isNaN(date.valueOf()) ? '' : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); };

function RelationBadge({ edge }) {
  const nature = relationshipNature(edge);
  return <span className={`gw-relation-badge nature-${nature.key}`} title={nature.description}>{nature.label}{edge.stale ? ' · 来源已更新' : ''}</span>;
}

function WikiBuildAllProgress({ resource, busy, onCancel }) {
  const runs = resource.data?.runs || [];
  return <div className="gw-build-all-progress">
    {resource.error && <p className="gw-error-text" role="alert">全部构建任务暂时无法读取：{resource.error}<button onClick={resource.refresh} disabled={resource.loading}>刷新任务</button></p>}
    {runs.map(run => {
      const counts = run.counts || {}, status = run.job?.status;
      return <section className="gw-build-all-run" key={run.id} aria-label="全部待构建资料任务" data-run-id={run.id}>
        <div><strong>全部待构建资料</strong><span>{JOB_STATUS[status] || status}</span></div>
        <p>{run.scope === 'selected' ? '固定已选范围' : '固定文库范围'} · 共 {run.total} 篇 · 每批最多 20 篇 · 已启动 {run.batches || 0} 批</p>
        <p role="status" aria-live="polite">成功 {counts.succeeded || 0} · 失败 {counts.failed || 0} · 跳过 {counts.skipped || 0} · 已取消 {counts.cancelled || 0}<br/>待开始 {counts.pending || 0} · 排队 {counts.queued || 0} · 正在构建 {counts.running || 0}</p>
        {run.job?.error && <p className="gw-error-text">{run.job.error}</p>}
        {['queued', 'running'].includes(status) && <button disabled={!!busy} onClick={() => onCancel(run.id)}>{busy === run.id ? '正在取消…' : '取消全部构建'}</button>}
      </section>;
    })}
  </div>;
}

function EvidenceList({ evidence = [], onOpen, compact = false }) {
  return <div className={`gw-evidence-list ${compact ? 'is-compact' : ''}`}>{uniqueEvidence(evidence).map((item, index) => <button key={evidenceKey(item)} onClick={() => onOpen(item)} title={`打开原文版本并定位来源段落：${item.title || item.sourceId}`}><FileText size={13}/><span><strong>{item.title || `来源 ${index + 1}`}</strong>{!compact && item.quote && <q>{item.quote}</q>}<small>{item.stale ? '历史来源 · ' : ''}定位原文段落</small></span><ArrowUpRight size={12}/></button>)}</div>;
}

function NavigationGroup({ group, currentId, active, onNavigate }) {
  const resource = useWikiGroup(group.id, active), data = resource.data || group;
  return <div className="gw-navigation-group">
    <div className="gw-navigation-head"><strong>{group.label}</strong><span>{data.memberCount ?? 0} 个来源词条</span></div>
    <p>名称与定义相同，保留各自来源与版本。此处用于导航，不作为已证实的实体合并或原文关系。</p>
    <div className="gw-group-members">{(data.members || []).map(member => <button key={member.id} disabled={member.id === currentId} onClick={() => onNavigate(member)}><span><strong>{member.label}</strong><small>{member.sourceTitle || member.documentTitle || member.sourceId || '独立来源'}{member.stale ? ' · 来源已更新' : ''}</small></span>{member.id === currentId ? <small>当前</small> : <CaretRight size={13}/>}</button>)}</div>
    {resource.error && <p className="gw-error-text" role="alert">{resource.error}<button onClick={resource.refresh}>重试</button></p>}
    {data.hasMore && <button className="gw-load-more" disabled={resource.loading} onClick={resource.loadMore}>{resource.loading ? '正在读取…' : '继续查看组成员'}</button>}
  </div>;
}

function PathDetails({ path, knownNodes, onNavigate, onOpen, onClose }) {
  const ids = path.pathNodeIds || [], pathEdges = path.pathEdges || [];
  const labels = { ...knownNodes };
  (path.pathNodes || []).forEach(node => { labels[node.id] = node; });
  pathEdges.forEach(edge => {
    if (!labels[edge.from] && edge.fromLabel) labels[edge.from] = { id: edge.from, label: edge.fromLabel, type: edge.fromType || 'concept' };
    if (!labels[edge.to] && edge.toLabel) labels[edge.to] = { id: edge.to, label: edge.toLabel, type: edge.toType || 'concept' };
  });
  if (path.label) labels[path.id] = path;
  const target = id => labels[id] || { id, label: id, type: 'concept' };
  return <section className="gw-detail-section gw-path-detail" aria-label="发现路径">
    <h3><Path size={16}/>发现路径<span>{path.hopCount || pathEdges.length} 跳</span><button onClick={onClose} aria-label="关闭发现路径"><X size={14}/></button></h3>
    <p className="gw-path-notice">路径说明如何发现词条；每条边单独核对，不把整条路径当作事实结论。</p>
    {ids.length > 0 && <button className="gw-path-node" onClick={() => onNavigate(target(ids[0]), { preservePath: true })}>{target(ids[0]).label}</button>}
    {pathEdges.map((edge, index) => <div className="gw-path-step" key={`${edgeIdentity(edge)}:${index}`}>
      <div className="gw-path-edge"><span>{index + 1}</span><div><RelationBadge edge={edge}/><strong>{edge.label || RELATIONS[edge.type] || '关系'}</strong><small>{edge.fromLabel || target(edge.from).label} → {edge.toLabel || target(edge.to).label}{['reverse', 'incoming'].includes(edge.traversalDirection) ? ' · 本次沿反向查找' : ''}</small></div></div>
      {edge.evidence?.length ? <EvidenceList evidence={edge.evidence} onOpen={onOpen} compact/> : <p className="gw-path-notice">{relationshipNature(edge).key === 'navigation' ? relationshipNature(edge).description : '暂无可显示的逐边原文证据。'}</p>}
      {ids[index + 1] && <button className="gw-path-node" onClick={() => onNavigate(target(ids[index + 1]), { preservePath: true })}>{target(ids[index + 1]).label}</button>}
    </div>)}
  </section>;
}

function GraphResolutionPanel({ open, active, documents, onChanged, id }) {
  const review = useWikiResolution(active && open);
  const [sourceIds, setSourceIds] = useState([]), [sourceQuery, setSourceQuery] = useState('');
  const [drafts, setDrafts] = useState({}), [feedback, setFeedback] = useState('');
  const [reviewedSnapshot, setReviewedSnapshot] = useState(''), reviewedRequest = useRef('');
  const availableIds = new Set(documents.map(doc => doc.id));
  const selectedIds = sourceIds.filter(sourceId => availableIds.has(sourceId));
  const proposals = (review.data?.proposals || []).filter(proposal => !proposal.status || proposal.status === 'pending');
  const staleCount = (review.data?.proposals || []).filter(proposal => proposal.status === 'stale').length;
  const snapshotId = reviewedSnapshot;
  const snapshotChanged = !!reviewedSnapshot && review.data?.snapshotId !== reviewedSnapshot;
  const blocked = !!review.busy || review.loading || review.conflict || snapshotChanged;
  useEffect(() => {
    // Polls may update task progress, but must not silently replace the snapshot
    // the user reviewed. Only an explicit reload or completed action accepts it.
    if (!review.loading && review.data && review.key && reviewedRequest.current !== review.key) {
      reviewedRequest.current = review.key; setReviewedSnapshot(review.data.snapshotId);
    }
  }, [review.key, review.loading, review.data]);
  const patchDraft = (proposal, patch) => setDrafts(current => ({ ...current, [proposal.id]: { label: proposal.label || '', reason: '', ...current[proposal.id], ...patch } }));
  const complete = async (action, body) => {
    setFeedback('');
    const result = await review.decide(action, body);
    if (result) {
      const message = action === 'undo' ? '已撤销该决定，已保存的来源版本仍可核对。' : action === 'merge' ? '已确认归并，正在刷新统一图谱。' : '已保存关联判断。';
      setFeedback(`${message}${result.synthesisNotice ? ` ${result.synthesisNotice}` : ''}`); onChanged(result.synthesisNotice, action);
    }
  };
  if (!open) return null;
  return <div className="gw-resolution-panel" id={id} aria-label="整理关联">
    <div className="gw-jobs-head"><strong>整理关联</strong><button onClick={review.refresh} disabled={!!review.busy || review.loading} aria-label="刷新归并候选"><ArrowsClockwise size={15}/></button></div>
    <p>先明确选择少量资料，再检测同义词条。每次最多 10 篇；检测仅生成候选，归并需要逐组确认。</p>
    <label className="gw-resolution-search">筛选资料<input value={sourceQuery} onChange={event => setSourceQuery(event.target.value)} placeholder="按资料标题筛选" aria-label="筛选待整理资料"/></label>
    <div className="gw-resolution-sources" aria-label="选择待整理资料">{documents.filter(doc => !sourceQuery.trim() || doc.title?.toLocaleLowerCase().includes(sourceQuery.trim().toLocaleLowerCase())).map(doc => <label key={doc.id}><input type="checkbox" aria-label={`整理资料：${doc.title}`} checked={selectedIds.includes(doc.id)} disabled={!!review.busy || (!selectedIds.includes(doc.id) && selectedIds.length >= 10)} onChange={() => setSourceIds(current => boundedSourceSelection(current.filter(sourceId => availableIds.has(sourceId)), doc.id))}/><span title={doc.title}>{doc.title}</span></label>)}{!documents.length && <p>暂无资料。</p>}</div>
    <div className="gw-resolution-actions"><span>已选 {selectedIds.length} / 10 篇</span><button disabled={!selectedIds.length || !!review.busy} onClick={async () => {
      setFeedback(''); const result = await review.resolve(selectedIds);
      if (result) setFeedback(result.jobs?.length ? '检测任务已提交，完成后点击刷新候选。' : '已完成本次检测，请逐组核对候选。');
    }}>{review.busy === 'resolve' ? '正在检测…' : '检测所选资料'}</button></div>
    {(review.error || review.actionError) && <p className="gw-error-text" role="alert">{review.actionError || review.error}</p>}
    {review.conflict && <button className="gw-load-more" disabled={!!review.busy || review.loading} onClick={review.refresh}>刷新候选后重新核对</button>}
    {!review.conflict && snapshotChanged && <p className="gw-error-text" role="alert">知识范围已更新，已保留人工填写内容。<button className="gw-load-more" disabled={!!review.busy || review.loading} onClick={review.refresh}>刷新候选后重新核对</button></p>}
    {feedback && <p role="status">{feedback}</p>}
    {(review.data?.jobs || []).length > 0 && <><h3>整理任务</h3>{review.data.jobs.map(job => <div className={`gw-job ${job.status}`} key={job.id}><div><strong>{job.label || (job.kind?.includes('synth') ? '综合知识页' : '关联候选检测')}</strong><small>{({ queued: '等待处理', running: '正在处理', succeeded: '处理完成', failed: '处理失败', cancelled: '已取消' })[job.status] || job.status}</small>{job.error && <p>{job.error}</p>}</div>{['queued', 'running'].includes(job.status) && <button disabled={!!review.busy} onClick={() => review.jobAction(job.id, 'cancel')}>取消</button>}{['failed', 'cancelled'].includes(job.status) && job.retryable !== false && <button disabled={!!review.busy} onClick={() => review.jobAction(job.id, 'retry')}>重试</button>}</div>)}</>}
    <h3>已保存候选 <span>{proposals.length}</span></h3>
    {review.loading && <p role="status">正在读取候选…</p>}
    {!review.loading && !review.error && !proposals.length && <p>暂无待确认候选。</p>}
    {staleCount > 0 && <p>{staleCount} 组旧候选的来源或归并范围已变化，请重新选择资料检测。</p>}
    {proposals.map(proposal => {
      const draft = { label: proposal.label || '', reason: '', ...drafts[proposal.id] };
      const presentation = resolutionPresentation(proposal);
      const body = extra => resolutionBody(proposal, snapshotId, { ...draft, reason: draft.reason || proposal.reason || '', ...extra });
      return <section key={proposal.id} className="gw-resolution-proposal" aria-label={`归并候选：${proposal.label || proposal.id}`}>
        <strong>{proposal.label || '候选词条'}</strong><small>{presentation.label}</small>
        <p>{proposal.reason || '请结合各来源判断概念边界。'}</p>
        {presentation.explanation && <p>{presentation.explanation}</p>}
        <ul>{(proposal.entries || []).map(item => <li key={item.sourceEntryId || item.id}><strong>{item.label}</strong><span>{item.sourceTitle || item.sourceId || '独立来源'}</span></li>)}</ul>
        {!proposal.entries?.length && <p>{resolutionEntryIds(proposal).length} 个来源词条待核对。</p>}
        <label>统一名称<input aria-label={`统一名称：${proposal.id}`} maxLength={200} value={draft.label} onChange={event => patchDraft(proposal, { label: event.target.value })} disabled={!!review.busy}/></label>
        <label>判断备注<textarea aria-label={`判断备注：${proposal.id}`} maxLength={1200} value={draft.reason} onChange={event => patchDraft(proposal, { reason: event.target.value })} rows={2} disabled={!!review.busy}/></label>
        <div className="gw-resolution-actions"><button disabled={blocked || !presentation.mergeAllowed || !snapshotId || resolutionEntryIds(proposal).length < 2 || !draft.label.trim()} title={presentation.explanation || '确认这些来源词条指向同一概念'} onClick={() => { if (presentation.mergeAllowed) complete('merge', body()); }}>确认归并</button><button disabled={blocked || !snapshotId} onClick={() => complete('reject', body({ judgment: 'related' }))}>仅关联</button><button disabled={blocked || !snapshotId} onClick={() => complete('reject', body({ judgment: 'distinct' }))}>保持独立</button></div>
      </section>;
    })}
    {(review.data?.decisions || []).length > 0 && <><h3>近期决定</h3>{review.data.decisions.map(decision => <div className="gw-resolution-decision" key={decision.id}><div><strong>{decision.label || decision.canonicalLabel || ({ merge: '确认归并', reject: '关联判断', undo: '已撤销' })[decision.kind] || '归并决定'}</strong><small>{decision.undone ? '已撤销 · ' : ''}{decision.reason || ''}</small></div>{decision.undoable && decision.kind !== 'undo' && <button disabled={blocked || !snapshotId} onClick={() => complete('undo', { decisionId: decision.id, expectedSnapshotId: snapshotId })}>撤销</button>}</div>)}</>}
  </div>;
}

export function GraphWorkspace({ documents = [], onOpenDoc, onToast, active = true, modelName = '', fullscreen = false, onFullscreenChange }) {
  const [mode, setMode] = useState('unified'), modeStates = useRef(new Map());
  const [topicId, setTopicId] = useState(''), [selection, setSelection] = useState(null), [version, setVersion] = useState('');
  const [query, setQuery] = useState(''), [showSearch, setShowSearch] = useState(false);
  const [visibleTypes, setVisibleTypes] = useState(Object.keys(TYPES)), [visibleRelations, setVisibleRelations] = useState(Object.keys(RELATIONS));
  const [showFilters, setShowFilters] = useState(false), [showBuild, setShowBuild] = useState(false), [showJobs, setShowJobs] = useState(false);
  const [showExplore, setShowExplore] = useState(false);
  const [showResolution, setShowResolution] = useState(false);
  const [showInspector, setShowInspector] = useState(true), [showLabels, setShowLabels] = useState(false);
  const [layout, setLayout] = useState('force'), [metric, setMetric] = useState('none');
  const [material, chooseMaterial] = useGraphMaterial();
  const [motion, setMotion] = useState(true), [analysisScope, setAnalysisScope] = useState('topic');
  const [renderError, setRenderError] = useState(''), [cameraZoom, setCameraZoom] = useState(1);
  const [exploration, setExploration] = useState(null), [pendingFocus, setPendingFocus] = useState(null);
  const [selectedPath, setSelectedPath] = useState(null), [searchHops, setSearchHops] = useState(1);
  const [visibleNatures, setVisibleNatures] = useState(['supported', 'source', 'navigation', 'unverified']);
  const [hoveredId, setHoveredId] = useState('');
  const [scopeNotice, setScopeNotice] = useState('');
  const layoutCache = useRef(new Map()), cameraCache = useRef(new Map()), topicsCache = useRef(new Map()), topicScopeAliases = useRef(new Map());
  const canvasRef = useRef(null), filterRef = useRef(null), buildRef = useRef(null), searchRef = useRef(null);
  const exploreRef = useRef(null), jobsRef = useRef(null), resolutionRef = useRef(null);
  const bodyRef = useRef(null);
  const inspectorResize = useGraphInspectorResize({ bodyRef, active: active && showInspector });
  const selectedSourceIds = documents.filter(doc => doc.selected).map(doc => doc.id);
  const buildCatalogStamp = JSON.stringify(documents.map(doc => [doc.id, doc.revisionId]));
  const buildContext = useRef({ key: '', revision: 0 });
  const buildContextKey = JSON.stringify([active, showBuild, selectedSourceIds, buildCatalogStamp]);
  if (buildContext.current.key !== buildContextKey) buildContext.current = { key: buildContextKey, revision: buildContext.current.revision + 1 };
  useEffect(() => () => { buildContext.current.revision += 1; }, []);
  const graph = useWikiGraph(topicId, active, mode, buildContext.current.revision), data = graph.data;
  const buildAllResource = useWikiBuildAll(active);
  useEffect(() => {
    const changed = () => {
      // Keep independent layout/camera preferences, discard deleted evidence
      // and cached focus in either source or unified graph mode.
      topicsCache.current.clear(); modeStates.current.clear();
      setExploration(null); setPendingFocus(null); setSelection(null);
      setVersion(''); setSelectedPath(null); setHoveredId('');
      setScopeNotice('资料已删除，图谱已更新。');
    };
    window.addEventListener('wenshu-library-changed', changed);
    return () => window.removeEventListener('wenshu-library-changed', changed);
  }, []);
  if (data?.topics) topicsCache.current.set(mode, data.topics);
  const topics = data?.topics || topicsCache.current.get(mode) || [], topic = topics.find(item => item.id === (topicId || data?.topicId)) || topics[0];
  const neighborhood = useWikiNeighborhood(exploration?.entryId, exploration?.hops || 1, active && !!exploration, mode, data?.topicId || topicId, data?.snapshotId || '');
  const canvasData = exploration ? neighborhood.data : data;
  const canvasLoading = exploration ? neighborhood.loading : graph.loading;
  const canvasError = exploration ? neighborhood.error : graph.error;
  const rootId = exploration ? (canvasData?.rootId || exploration.entryId) : topic?.root;
  const scopeKey = graphScopeKey(mode, topic, exploration, topicId || data?.topicId || '');
  if (!exploration) inheritTopicScopeCaches(mode, topic?.id, scopeKey, topicScopeAliases.current, cameraCache.current, layoutCache.current);
  const onCameraChange = useCallback(view => setCameraZoom(view.zoom), []);
  const analytics = useGraphAnalytics({ mode, topicId: data?.topicId || topicId || '', snapshotId: data?.snapshotId,
    scope: analysisScope, active: active && (showFilters || metric !== 'none'), onConflict: graph.refresh });
  const alignedMetrics = analytics.data && (!exploration || canvasData?.graphSnapshotId === analytics.data.snapshotId) ? analytics.data.metrics : {};
  const rawEdges = canvasData?.edges || [];
  const nodes = useMemo(() => {
    const rawNodes = canvasData?.nodes || [];
    const positions = appendGraphPositions(rawNodes, canvasData?.edges || [], rootId, layoutCache.current.get(scopeKey));
    layoutCache.current.set(scopeKey, positions);
    const docs = new Map(documents.map(doc => [doc.id, doc]));
    return rawNodes.map(node => ({ ...node, ...positions.get(node.id), document: node.type === 'document' ? docs.get(node.documentId) : undefined }));
  }, [canvasData?.nodes, canvasData?.edges, rootId, documents, scopeKey]);
  const nodesById = useMemo(() => Object.fromEntries(nodes.map(node => [node.id, node])), [nodes]);
  const selectedNode = (selection && (nodesById[selection.id] || selection)) || nodesById[rootId] || nodes[0] || null;
  const jobs = data?.jobs || [];
  // The API prioritizes running and failed jobs. Determine each source's latest
  // attempt from creation time, so a historical failure cannot mask a success.
  const recentJobs = [...jobs].sort((a, b) => (b.created_at || b.createdAt || '').localeCompare(a.created_at || a.createdAt || '') || b.id.localeCompare(a.id));
  const latestBySource = new Map();
  recentJobs.forEach(job => { const sourceId = job.source_id || job.sourceId || job.id; if (!latestBySource.has(sourceId)) latestBySource.set(sourceId, job); });
  const latestJobs = [...latestBySource.values()], latestJobIds = new Set(latestJobs.map(job => job.id));
  const activeJobs = latestJobs.filter(job => ['queued', 'running'].includes(job.status)), failedJobs = latestJobs.filter(job => job.status === 'failed');
  const refreshStamp = JSON.stringify([data?.snapshotId, data?.stats, jobs.map(job => [job.id, job.status, job.updated_at || job.updatedAt])]);
  const page = useWikiPage(selectedNode?.type === 'document' ? '' : selectedNode?.id, version, active && showInspector, refreshStamp, mode, topicId);
  const latestPage = useWikiPage(selectedNode?.type === 'document' ? '' : selectedNode?.id, '', active && showInspector && !!version, refreshStamp, mode, topicId);
  const search = useWikiSearch(query, active, refreshStamp, searchHops, mode, topicId), detail = page.data;
  const entry = detail || (selectedNode && version ? { ...selectedNode, summary: '' } : selectedNode);
  const latestDetail = version ? latestPage.data : detail;
  const latestVersion = latestDetail?.version || detail?.history?.[0]?.version;
  const latestVersionLabel = latestDetail && !latestDetail.archived ? '当前版本' : '最近保存';
  const entrySourceTitle = detail?.evidence?.[0]?.title || (entry?.type === 'document' ? entry.label : documents.find(doc => doc.id === entry?.sourceId)?.title) || '';
  const visibleNodes = nodes.filter(node => visibleTypes.includes(node.type)), visibleIds = new Set(visibleNodes.map(node => node.id));
  const edges = rawEdges.filter(edge => visibleRelations.includes(edge.type) && visibleNatures.includes(relationshipNature(edge).key) && visibleIds.has(edge.from) && visibleIds.has(edge.to));
  const matchedIds = new Set([...(search.data?.results || []), ...(search.data?.expanded || [])].map(item => item.id));
  const neighbors = selectedNode ? rawEdges.filter(edge => edge.from === selectedNode.id || edge.to === selectedNode.id).map(edge => ({ ...edge, node: nodesById[edge.from === selectedNode.id ? edge.to : edge.from] })).filter(item => item.node) : [];
  const buildScopeResource = useWikiBuildScope(selectedSourceIds.length ? selectedSourceIds : null, active && showBuild, buildCatalogStamp);
  const buildDocuments = buildScopeResource.data?.documents || [], buildCount = buildDocuments.length;
  const buildScope = selectedSourceIds.length ? '已选资料' : '整个文库';
  const buildExcluded = buildScopeResource.data?.excluded;
  const buildBlocked = !active || !showBuild || buildScopeResource.loading || !!buildScopeResource.error || !buildScopeResource.data || !!graph.busy || !buildCount;
  const rawId = useId().replace(/:/g, '');

  const fit = () => canvasRef.current?.fit();
  const retryRender = () => { setRenderError(''); canvasRef.current?.retry(); };
  useEffect(() => {
    if (!data?.topicId || data.topicId === topicId) return;
    if (!topicId) { setTopicId(data.topicId); return; }
    if (data.topics?.some(item => item.id === topicId)) return;
    // Recategorisation may remove a topic. Follow the server's explicit
    // fallback before requesting rankings or another old-topic neighborhood.
    setTopicId(data.topicId); setExploration(null); setPendingFocus(null);
    setSelection(null); setVersion(''); setSelectedPath(null); setHoveredId('');
    setScopeNotice('原主题范围已更新，已切换到当前可用图谱。');
  }, [topicId, data?.topicId, data?.topics]);
  useEffect(() => {
    if (!pendingFocus || pendingFocus.scopeKey !== scopeKey || !nodesById[pendingFocus.id]) return;
    canvasRef.current?.focus(pendingFocus.id);
    setPendingFocus(null);
  }, [pendingFocus, scopeKey, nodesById]);
  useEffect(() => {
    if (!exploration || !neighborhood.unavailable) return;
    setScopeNotice('该词条已不在最新构建中，已返回主题图；历史内容仍可从已保存版本核对。');
    setExploration(null); setPendingFocus(null); setSelection(null); setVersion(''); setSelectedPath(null);
    graph.refresh();
  }, [exploration, neighborhood.unavailable]);
  useEffect(() => {
    if (!showFilters && !showBuild && !showSearch && !showExplore && !showJobs && !showResolution) return;
    const controls = [[filterRef, setShowFilters], [buildRef, setShowBuild], [searchRef, setShowSearch], [exploreRef, setShowExplore], [jobsRef, setShowJobs], [resolutionRef, setShowResolution]];
    const close = event => {
      controls.forEach(([ref, hide]) => { if (!ref.current?.contains(event.target)) hide(false); });
    };
    const key = event => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      const current = controls.find(([ref]) => ref.current?.contains(event.target));
      controls.forEach(([, hide]) => hide(false));
      if (current) {
        event.preventDefault();
        current[0].current.querySelector('[data-graph-control]')?.focus({ preventScroll: true });
      }
    };
    document.addEventListener('pointerdown', close); document.addEventListener('keydown', key);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', key); };
  }, [showFilters, showBuild, showSearch, showExplore, showJobs, showResolution]);

  const chooseTopic = id => { setTopicId(id); setExploration(null); setPendingFocus(null); setSelectedPath(null); setHoveredId(''); setSelection(null); setVersion(''); setShowSearch(false); setQuery(''); setVisibleTypes(Object.keys(TYPES)); setShowInspector(true); };
  const chooseMode = (nextMode, member = null) => {
    if (nextMode === mode && !member) return;
    modeStates.current.set(mode, { topicId, selection, version, exploration, query, selectedPath });
    const saved = modeStates.current.get(nextMode) || {};
    setMode(nextMode); setTopicId(member ? '' : saved.topicId || '');
    setSelection(member ? { ...member, id: member.sourceEntryId || member.id, type: member.type || 'concept' } : saved.selection || null);
    setVersion(member ? member.version ? String(member.version) : '' : saved.version || ''); setQuery(member ? '' : saved.query || ''); setSelectedPath(member ? null : saved.selectedPath || null);
    setExploration(member ? { entryId: member.sourceEntryId || member.id, label: member.label, hops: 1 } : saved.exploration || null);
    setPendingFocus(null); setHoveredId(''); setScopeNotice(''); setShowSearch(false); setShowInspector(true);
  };
  const selectNode = node => { setSelection(node); setVersion(''); setShowInspector(true); setVisibleTypes(current => [...new Set([...current, node.type || 'concept'])]); };
  const openEvidence = item => {
    if (!item?.sourceId || !item.revisionId) { onToast?.('这条来源缺少文档版本，暂时无法定位。'); return; }
    const doc = documents.find(document => document.id === item.sourceId);
    onOpenDoc?.({ ...(doc || {}), id: item.sourceId, title: item.title || doc?.title || '来源文档', revisionId: item.revisionId, matchBlockId: item.blockId || null });
  };
  const openDocument = node => openEvidence({ sourceId: node.documentId, revisionId: node.revisionId || node.document?.revisionId, title: node.label });
  const focusNeighborhood = (node, hops = 1) => {
    if (!node || node.type === 'document') return;
    selectNode(node); setHoveredId(''); setScopeNotice('');
    setExploration({ entryId: node.id, label: node.label, hops });
    if (exploration?.entryId !== node.id) setPendingFocus({ id: node.id, scopeKey: graphScopeKey(mode, topic, { entryId: node.id }) });
  };
  const navigateNode = (node, { preservePath = false, path = null } = {}) => {
    if (node.type === 'document') { if (node.evidence?.[0]) openEvidence(node.evidence[0]); else openDocument(node); return; }
    selectNode(node); setShowSearch(false); setHoveredId('');
    if (path) setSelectedPath(path); else if (!preservePath) setSelectedPath(null);
    if (nodesById[node.id]) {
      setPendingFocus({ id: node.id, scopeKey });
    } else {
      if (node.topicId) setTopicId(node.topicId);
      focusNeighborhood(node, 1);
    }
  };
  const returnToTopic = () => { setExploration(null); setPendingFocus(null); setSelectedPath(null); setSelection(null); setVersion(''); setHoveredId(''); };
  const openSearchResult = result => {
    if (result.type === 'document') { if (result.evidence?.[0]) openEvidence(result.evidence[0]); return; }
    navigateNode(result, { path: result.pathEdges?.length ? result : null });
    searchRef.current?.querySelector('[data-graph-control]')?.focus({ preventScroll: true });
  };
  const build = async force => {
    if (buildBlocked) return;
    const context = buildContext.current.revision;
    const result = await graph.build(buildDocuments.map(doc => doc.id), force);
    if (!result) return;
    buildScopeResource.refresh();
    if (buildContext.current.revision !== context) return;
    const count = (result.jobs || []).filter(job => ['queued', 'running'].includes(job.status)).length;
    const completed = (result.skipped || []).filter(item => item.reason === 'already_built').length;
    const empty = (result.skipped || []).filter(item => item.reason === 'empty_source').length;
    const unclear = (result.skipped || []).filter(item => item.reason === 'no_clear_entries').length;
    const failed = (result.jobs || []).filter(job => ['failed', 'cancelled'].includes(job.status)).length;
    const message = [`${count} 项等待或正在构建`, completed ? `${completed} 篇已有当前结果` : '', empty ? `${empty} 篇无可提取正文，已跳过` : '', unclear ? `${unclear} 篇无可收录内容，已完成筛选` : '', failed ? `${failed} 项需在任务面板重试或重新构建` : ''].filter(Boolean).join('；') + '。';
    setShowBuild(false); setShowJobs(true); onToast?.(`本次提交：${message}`);
    jobsRef.current?.querySelector('[data-graph-control]')?.focus({ preventScroll: true });
  };
  const buildAll = async () => {
    if (buildBlocked) return;
    const context = buildContext.current.revision;
    const result = await graph.buildAll(selectedSourceIds.length ? selectedSourceIds : null);
    if (!result) return;
    buildScopeResource.refresh(); buildAllResource.refresh();
    if (buildContext.current.revision !== context) return;
    onToast?.(!result.run ? '当前范围没有待构建资料。' : result.reused ? '已复用正在进行的全部构建任务。' : `已固定 ${result.run.total} 篇待构建资料，将按每批最多 20 篇自动继续。`);
  };
  const cancelBuildAll = async id => {
    const result = await graph.jobAction(id, 'cancel');
    if (result) { buildAllResource.refresh(); buildScopeResource.refresh(); }
  };
  const toggleType = type => setVisibleTypes(current => current.includes(type) ? current.filter(item => item !== type) : [...current, type]);
  const zoom = factor => canvasRef.current?.zoom(factor);
  const fullscreenButton = useRef(null);
  useEffect(() => {
    if (!active || !fullscreen || !onFullscreenChange) return;
    const exit = event => {
      if (event.key !== 'Escape' || event.defaultPrevented || showFilters || showBuild || showSearch || showExplore || showJobs || showResolution) return;
      if (event.target.closest?.('[role="dialog"],[role="listbox"],[role="menu"],.gw-topic-picker:has(.gw-topic-trigger[aria-expanded="true"])')) return;
      event.preventDefault();
      onFullscreenChange(false);
      fullscreenButton.current?.focus({ preventScroll: true });
    };
    document.addEventListener('keydown', exit);
    return () => document.removeEventListener('keydown', exit);
  }, [active, fullscreen, onFullscreenChange, showFilters, showBuild, showSearch, showExplore, showJobs, showResolution]);
  const reloadGraph = () => { if (exploration) neighborhood.refresh(); else { if (graph.error && topicId) { setTopicId(''); setSelection(null); setVersion(''); } graph.refresh(); } };
  const restoreDisplay = () => { setVisibleTypes(Object.keys(TYPES)); setVisibleRelations(Object.keys(RELATIONS)); setVisibleNatures(['supported', 'source', 'navigation', 'unverified']); setShowLabels(false); };
  const entryRelations = detail?.relations || (entry?.type === 'document' ? neighbors.filter(item => item.node.type !== 'document').map(item => ({ ...item, targetId: item.node.id, targetLabel: item.node.label, targetType: item.node.type, evidence: item.evidence || [] })) : []);
  const sources = uniqueEvidence(detail?.evidence || []);
  const navigationGroups = Array.isArray(detail?.navigationGroups) ? detail.navigationGroups : detail?.navigationGroups ? [detail.navigationGroups] : [];
  const pathNodeIds = new Set(selectedPath?.pathNodeIds || []);
  const pathEdgeIds = new Set((selectedPath?.pathEdges || []).map(edgeIdentity));
  const canvasTitle = exploration ? `${exploration.label || selectedNode?.label || '词条'} · ${exploration.hops} 跳邻域` : topic?.label || '知识图谱';

  return <section className={`graph-workspace ${fullscreen ? 'is-fullscreen' : ''}`} aria-label="知识图谱工作台">
    <div ref={bodyRef} className={`gw-body ${showInspector ? 'with-inspector' : ''} ${inspectorResize.resizing ? 'is-resizing' : ''}`} style={{ '--gw-inspector-width': `${inspectorResize.width}px` }} id={`gw-panel-${rawId}`} role="region" aria-label={canvasTitle}>
      <div className="gw-visualization">
        <header className="gw-page-heading"><div><h1>让知识，彼此连接</h1><p>以词条为节点，沿着关系探索理解，也随时回到原始文档。</p></div></header>
        <div className="gw-canvas" data-graph-snapshot={exploration ? canvasData?.graphSnapshotId || '' : data?.snapshotId || ''} data-applied-metrics-snapshot={Object.keys(alignedMetrics).length ? analytics.data.snapshotId : ''}>
          <div className="gw-canvas-notices">
        {renderError && <div className="gw-service-message is-error" role="alert"><WarningCircle size={15}/><span>{renderError}</span><button onClick={retryRender}>重新布局</button></div>}
        {!showFilters && metric !== 'none' && analytics.error && <div className="gw-service-message is-error" role="alert"><WarningCircle size={15}/><span>{analytics.error}</span><button onClick={analytics.refresh}>重试计算</button></div>}
        {(canvasError || graph.actionError) && <div className="gw-service-message is-error" role="alert"><WarningCircle size={15}/><span>{canvasError || graph.actionError}</span><button onClick={reloadGraph}>重新加载</button></div>}
        {exploration && neighborhood.notice && <div className="gw-service-message" role="status"><Info size={14}/><span>{neighborhood.notice}</span></div>}
        {scopeNotice && <div className="gw-service-message" role="status"><Info size={14}/><span>{scopeNotice}</span><button onClick={() => setScopeNotice('')} aria-label="关闭图谱更新提示"><X size={13}/></button></div>}
        {!graph.error && (data?.stats?.stale > 0 || data?.stats?.pending > 0) && <div className="gw-service-message"><Info size={14}/><span>{data.stats.pending > 0 ? `${data.stats.pending} 篇文档待构建。` : ''}{data.stats.stale > 0 ? `${data.stats.stale} 项来源已更新，旧结果保留原版本引用。` : ''}</span><button onClick={() => setShowBuild(true)}>查看构建范围</button></div>}
          </div>
          <div className="gw-canvas-heading" id={`gw-title-${rawId}`}><GraphTopicPicker topics={topics} currentId={topic?.id} label={canvasTitle} loading={graph.loading} onSelect={chooseTopic} idBase={rawId}/><span>{mode === 'unified' ? '统一词条' : '来源词条'} <b>·</b> {visibleNodes.filter(node => node.type !== 'document').length} 个词条 <b>·</b> {visibleNodes.filter(node => node.type === 'document').length} 份文档{canvasLoading && nodes.length ? ' · 更新中' : ''}</span></div>
          <div className="gw-canvas-tools" role="toolbar" aria-label="图谱操作">
            <div className="gw-canvas-tool-row">
              <div className="gw-search-wrap" ref={searchRef}>
              <button className={`gw-tool-button ${showSearch || query ? 'active' : ''}`} data-graph-control aria-label="搜索图谱" title="搜索图谱" aria-expanded={showSearch} aria-controls={`gw-search-panel-${rawId}`} onClick={() => setShowSearch(value => !value)}><MagnifyingGlass size={20}/></button>
              {showSearch && <div className="gw-search-panel" id={`gw-search-panel-${rawId}`}><div className="gw-search"><MagnifyingGlass size={18}/><input autoFocus value={query} onChange={event => { setQuery(event.target.value); setShowSearch(true); }} onFocus={() => setShowSearch(true)} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing && event.keyCode !== 229 && search.data?.results?.length) { event.preventDefault(); event.stopPropagation(); openSearchResult(search.data.results[0]); } }} placeholder="检索 Wiki 与原文证据" aria-label="检索全部 Wiki 词条与原文证据" aria-expanded={!!query.trim() && showSearch} aria-controls="gw-search-results"/>{query && <button aria-label="清空图谱搜索" onClick={() => { setQuery(''); setShowSearch(true); }}><X size={15}/></button>}</div>
          {query.trim() && showSearch && <div className="gw-search-results gw-live-search" id="gw-search-results"><div className="gw-search-scope"><span>{mode === 'unified' ? '当前主题资料与统一词条' : '全部来源词条与原文'}</span><label>关系扩展<select value={searchHops} onChange={event => setSearchHops(Number(event.target.value))} aria-label="检索关系扩展跳数"><option value={1}>一跳</option><option value={2}>两跳</option></select></label></div><span aria-live="polite">{search.loading ? '正在检索 Wiki 与原文…' : search.error ? '检索失败' : `${search.data?.results?.length || 0} 个直接命中 · 全文检索 + ${searchHops === 1 ? '一' : '两'}跳关系扩展`}</span>{search.error && <p className="gw-error-text" role="alert">{search.error}</p>}{!search.loading && !search.error && !search.data?.results?.length && !search.data?.evidence?.length && <p className="gw-search-empty">没有匹配结果。可换用词条名称、别名或原文中的关键词。</p>}
            {(search.data?.results || []).map(result => <button key={result.id} onClick={() => openSearchResult(result)}><span><strong>{result.label}</strong><small>{result.reason || `${TYPES[result.type]?.label || '词条'}正文命中`}</small><p>{short(result.snippet || result.summary, 95)}</p></span><CaretRight size={13}/></button>)}
            {(search.data?.evidence || []).length > 0 && <div className="gw-search-evidence"><h4>原文证据</h4><EvidenceList evidence={search.data.evidence} onOpen={openEvidence}/></div>}
            {(search.data?.expanded || []).length > 0 && <div className="gw-search-expanded"><h4>沿关系发现</h4>{search.data.expanded.map(result => <button key={result.id} onClick={() => openSearchResult(result)}><span><strong>{result.label}</strong><small>{result.reason || '与命中词条存在关系路径'}{result.hopCount ? ` · ${result.hopCount} 跳` : ''}</small>{result.pathNodes?.length > 0 && <p>{result.pathNodes.map(node => node.label).join(' → ')}</p>}<small>查看完整路径与逐边原文</small></span><LinkSimple size={13}/></button>)}</div>}
            {search.data?.truncated && <p className="gw-search-empty">结果已截断，请缩小关键词范围。</p>}
          </div>}
        </div>}
            </div>
              <div className="gw-explore-wrap" ref={exploreRef}>
              <button className={`gw-tool-button ${showExplore || exploration ? 'active' : ''}`} data-graph-control aria-label="图谱探索范围" title="图谱探索范围" aria-expanded={showExplore} aria-controls={`gw-explore-${rawId}`} onClick={() => setShowExplore(value => !value)}><Path size={20}/></button>
              {showExplore && <div className="gw-explore-toolbar" id={`gw-explore-${rawId}`} aria-label="图谱探索范围"><span>{exploration ? `${exploration.hops} 跳邻域` : '主题图'}</span><button disabled={!selectedNode || selectedNode.type === 'document'} aria-pressed={!!exploration && exploration.hops === 1 && exploration.entryId === selectedNode?.id} onClick={() => focusNeighborhood(selectedNode, 1)}>只看一跳</button><button disabled={!selectedNode || selectedNode.type === 'document'} aria-pressed={!!exploration && exploration.hops === 2} onClick={() => focusNeighborhood(exploration ? { id: exploration.entryId, label: exploration.label, type: nodesById[exploration.entryId]?.type || 'concept' } : selectedNode, 2)}>展开两跳</button>{exploration && <><button onClick={returnToTopic}>返回主题图</button><button onClick={neighborhood.refresh} disabled={neighborhood.loading} aria-label="刷新当前邻域"><ArrowsClockwise size={13}/></button><small>{nodes.filter(node => node.type !== 'document').length} / {canvasData?.totalCount ?? '…'} 个词条</small>{canvasData?.hasMore && <button className="gw-more-neighbors" disabled={neighborhood.loading} onClick={neighborhood.loadMore}>{neighborhood.loading ? '正在加载…' : '继续加载邻域'}</button>}</>}</div>}
            </div>
              <div className="gw-jobs-wrap" ref={jobsRef}>
              <button className={`gw-tool-button ${showJobs ? 'active' : ''}`} data-graph-control aria-label="Wiki 构建任务" title={activeJobs.length ? `${activeJobs.length} 项构建中` : failedJobs.length ? `${failedJobs.length} 项失败` : 'Wiki 构建任务'} aria-expanded={showJobs} aria-controls={`gw-jobs-${rawId}`} onClick={() => setShowJobs(value => !value)}><ArrowsClockwise size={20}/>{(activeJobs.length > 0 || failedJobs.length > 0) && <small className="gw-tool-count">{activeJobs.length || failedJobs.length}</small>}</button>
              {showJobs && <div className="gw-jobs" id={`gw-jobs-${rawId}`} aria-label="Wiki 构建任务"><div className="gw-jobs-head"><strong>Wiki 构建任务</strong><button onClick={() => setShowJobs(false)} aria-label="关闭构建任务"><X size={15}/></button></div><WikiBuildAllProgress resource={buildAllResource} busy={graph.busy} onCancel={cancelBuildAll}/>{jobs.length === 0 && !buildAllResource.data?.runs?.length && <p>暂无构建任务。提交后可在这里查看进度。</p>}{recentJobs.slice(0, 20).map(job => <div key={job.id} className={`gw-job ${job.status}`}><div><strong>{job.label || 'Wiki 构建'}</strong><small>{latestJobIds.has(job.id) ? '当前任务' : '历史记录'} · {JOB_STATUS[job.status] || job.status}{jobStageLabel(job) ? ` · ${jobStageLabel(job)}` : ''}</small>{job.error && <p>{job.error}</p>}{latestJobIds.has(job.id) && ['failed', 'cancelled'].includes(job.status) && job.retryable === false && <p>此任务不可重试，请核对原因后重新提交构建。</p>}</div>{['queued', 'running'].includes(job.status) && <button disabled={!!graph.busy} onClick={() => graph.jobAction(job.id, 'cancel')}>取消</button>}{latestJobIds.has(job.id) && ['failed', 'cancelled'].includes(job.status) && job.retryable !== false && <button disabled={!!graph.busy} onClick={() => graph.jobAction(job.id, 'retry')}>重试</button>}</div>)}</div>}
            </div>
              <div className="gw-filter-wrap" ref={filterRef}><button className={`gw-tool-button ${showFilters ? 'active' : ''}`} data-graph-control aria-label="图谱显示设置" title="图谱显示设置" onClick={() => setShowFilters(value => !value)} aria-expanded={showFilters} aria-controls="gw-display-settings"><SlidersHorizontal size={20}/></button>{showFilters && <div className="gw-filter-popover" id="gw-display-settings"><GraphPresentationControls material={material} onMaterial={chooseMaterial} layout={layout} onLayout={setLayout} metric={metric} onMetric={setMetric} motion={motion} onMotion={setMotion} scope={analysisScope} onScope={setAnalysisScope} analytics={analytics} onNavigate={navigateNode} topicLabel={topic?.label} visibleIds={visibleIds}/><strong>词条视图</strong><div className="gw-view-switch" role="group" aria-label="词条视图"><button aria-pressed={mode === 'unified'} onClick={() => chooseMode('unified')}>统一词条</button><button aria-pressed={mode === 'source'} onClick={() => chooseMode('source')}>来源词条</button></div><strong>节点类型</strong><div className="gw-type-checks">{Object.entries(TYPES).map(([key, { label, Icon }]) => <button key={key} className={`gw-type-check ${visibleTypes.includes(key) ? 'active' : ''}`} onClick={() => toggleType(key)} aria-pressed={visibleTypes.includes(key)}><span className={`gw-type-dot type-${key}`}><Icon size={15}/></span>{label}<span className="gw-check-box">{visibleTypes.includes(key) && <Check size={11}/>}</span></button>)}</div><strong>关系显示</strong><label className="gw-label-toggle"><input type="checkbox" checked={showLabels} onChange={event => setShowLabels(event.target.checked)}/>显示关系名称</label><button className="gw-filter-reset" onClick={restoreDisplay}>恢复全部节点和关系</button></div>}</div>
              <div className="gw-resolution-wrap" ref={resolutionRef}><button className={`gw-tool-button ${showResolution ? 'active' : ''}`} data-graph-control aria-label="整理关联" title="整理关联" aria-expanded={showResolution} aria-controls={`gw-resolution-${rawId}`} onClick={() => setShowResolution(value => !value)}><LinkSimple size={20}/></button><GraphResolutionPanel open={showResolution} active={active} documents={documents} id={`gw-resolution-${rawId}`} onChanged={(notice, action) => { graph.refresh(); if (['merge', 'undo'].includes(action)) { if (modeStates.current.has('unified')) modeStates.current.set('unified', afterKnowledgeIdentityChange(modeStates.current.get('unified'))); if (mode === 'unified') returnToTopic(); } else if (exploration) neighborhood.refresh(); if (notice) onToast?.(notice); }}/></div>
              <button className={`gw-tool-button gw-inspector-toggle ${showInspector ? 'active' : ''}`} onClick={() => setShowInspector(value => !value)} aria-label={showInspector ? '收起词条详情' : '展开词条详情'} aria-expanded={showInspector}><Info size={20}/></button>
              <div className="gw-build-wrap" ref={buildRef}><button className="gw-build-trigger" data-graph-control aria-label="构建 Wiki" title="构建 Wiki" aria-expanded={showBuild} aria-controls="gw-build-panel" onClick={() => setShowBuild(value => !value)}><Graph size={20}/><span>构建 Wiki</span>{activeJobs.length > 0 && <small>{activeJobs.length}</small>}</button>
        {showBuild && <div className="gw-build-panel" id="gw-build-panel">
          <strong>从文档构建 Wiki</strong>
          <p>本次范围：<b>{buildScope}</b></p>
          {buildScopeResource.data && <p className="gw-build-scope-total" role="status">当前范围共 <b>{buildScopeResource.data.eligible}</b> 篇待构建资料；下方仅预览首批 {buildCount} 篇。</p>}
          <p className="gw-build-model">使用默认模型：{modelName || '按系统设置中的默认模型'}</p>
          {buildScopeResource.loading && <p role="status">正在读取构建范围…</p>}
          {buildScopeResource.error && <p className="gw-error-text" role="alert">{buildScopeResource.error}</p>}
          <ul>{buildDocuments.map(doc => <li key={doc.id} title={doc.title}>{doc.title}</li>)}</ul>
          {buildExcluded && <p role="status" aria-live="polite">已排除：{buildExcluded.built} 篇已有有效结果，{buildExcluded.active} 篇排队或正在构建{buildExcluded.empty ? `，${buildExcluded.empty} 篇无可提取正文` : ''}{buildExcluded.unclear ? `，${buildExcluded.unclear} 篇已完成筛选、无可收录内容` : ''}。{buildScopeResource.data.eligible > buildCount ? `另有 ${buildScopeResource.data.eligible - buildCount} 篇待后续批次构建。` : ''}</p>}
          <p>有勾选时仅处理已选范围；未勾选时处理整个文库。全部构建会固定本次范围及原文版本，每批最多 20 篇，完成一批后自动继续。仅收录明确且可独立理解的词条，不明确内容会丢弃。关闭窗口或改变勾选不会改变已提交范围，可随时取消。</p>
          <div className="gw-build-actions">
            <button disabled={buildBlocked} onClick={buildAll}>{graph.busy === 'build-all' ? '正在提交全部构建…' : '构建全部待构建资料'}</button>
            <button disabled={buildBlocked} onClick={() => build(false)}>{graph.busy === 'build' ? '正在提交…' : `增量构建 ${buildCount} 篇`}</button>
            <button disabled={buildBlocked} onClick={() => build(true)}>重新构建这 {buildCount} 篇</button>
            <button disabled={buildScopeResource.loading || !!graph.busy} onClick={buildScopeResource.refresh}>刷新构建范围</button>
          </div>
          {!buildScopeResource.loading && !buildScopeResource.error && buildScopeResource.data && !buildCount && <p>当前范围没有待构建资料。</p>}
          {graph.actionError && <p className="gw-error-text" role="alert">{graph.actionError}</p>}
          <WikiBuildAllProgress resource={buildAllResource} busy={graph.busy} onCancel={cancelBuildAll}/>
        </div>}
      </div>
            </div>
            <div className="gw-zoom-controls"><button onClick={() => zoom(1 / 1.2)} aria-label="缩小图谱" title="缩小图谱" disabled={cameraZoom <= .12}><Minus size={16}/></button><span aria-live="polite">{Math.round(cameraZoom * 100)}%</span><button onClick={() => zoom(1.2)} aria-label="放大图谱" title="放大图谱" disabled={cameraZoom >= 2.4}><Plus size={16}/></button><i/><button onClick={fit} aria-label="重置缩放并居中图谱" title="居中图谱"><ArrowsOutSimple size={17}/></button><i/><button type="button" ref={fullscreenButton} className="gw-fullscreen-toggle" aria-label={fullscreen ? '退出图谱全屏' : '进入图谱全屏'} title={fullscreen ? '退出图谱全屏' : '进入图谱全屏'} aria-pressed={fullscreen} aria-controls={`gw-panel-${rawId}`} onClick={() => onFullscreenChange?.(!fullscreen)}>{fullscreen ? <ArrowsIn size={20}/> : <ArrowsOut size={20}/>}</button></div>
          </div>
          <G6GraphCanvas ref={canvasRef} nodes={visibleNodes} edges={edges} rootId={rootId}
            selection={selectedNode} hover={hoveredId} onSelect={node => { selectNode(node); setSelectedPath(null); canvasRef.current?.focus(node.id); }}
            onHover={setHoveredId} matchedIds={query.trim() ? matchedIds : new Set()} pathNodeIds={pathNodeIds} pathEdgeIds={pathEdgeIds}
            showLabels={showLabels} cameraKey={scopeKey} cameraCache={cameraCache.current} positionCache={layoutCache.current}
            layout={layout} material={material} metrics={alignedMetrics} metric={metric} motion={motion}
            onCameraChange={onCameraChange} onError={setRenderError} ariaLabel={canvasTitle}/>

          {!visibleNodes.length && <div className="gw-no-nodes"><Graph size={32}/><strong>{canvasLoading ? '正在读取知识图谱…' : canvasError ? '图谱服务暂不可用' : nodes.length ? '当前没有显示的节点' : exploration ? '当前邻域没有可显示节点' : activeJobs.length ? 'Wiki 正在构建' : '从少量文档，开始构建 Wiki'}</strong><p>{canvasLoading ? '正在读取已保存的词条与来源。' : canvasError ? '连接恢复后会显示已保存的真实数据。' : nodes.length ? '在「显示」中开启至少一种节点类型。' : exploration ? '可刷新邻域，或返回主题图继续探索。' : activeJobs.length ? '完成后，真实词条和文档关联会自动显示。' : '提取词条、整理陈述与关系，并保留每条来源证据。'}</p>{!canvasLoading && <button onClick={nodes.length ? restoreDisplay : canvasError || exploration ? reloadGraph : () => setShowBuild(true)}>{nodes.length ? '显示全部节点' : canvasError || exploration ? '重新加载' : '选择构建范围'}</button>}</div>}

        </div>
        <footer className="gw-legend"><div className="gw-node-legend">{Object.entries(TYPES).map(([key, { label }]) => <button key={key} className={!visibleTypes.includes(key) ? 'is-off' : ''} onClick={() => toggleType(key)} aria-pressed={visibleTypes.includes(key)} aria-label={`${visibleTypes.includes(key) ? '隐藏' : '显示'}${label}节点`}><span className={`gw-legend-dot type-${key}`}/>{label}</button>)}</div><div className="gw-relation-legend">{Object.entries(RELATIONS).map(([key, label]) => <button key={key} className={!visibleRelations.includes(key) ? 'is-off' : ''} aria-pressed={visibleRelations.includes(key)} onClick={() => setVisibleRelations(current => current.includes(key) ? current.filter(item => item !== key) : [...current, key])}><span className={`gw-relation-line relation-${key}`}/>{label}</button>)}</div></footer>
        <div className="gw-nature-legend" aria-label="按关系性质显示">{[['supported', '有据关系'], ['source', '原文关联'], ['navigation', '导航关联']].map(([key, label]) => <button key={key} className={`nature-${key} ${visibleNatures.includes(key) ? '' : 'is-off'}`} aria-pressed={visibleNatures.includes(key)} onClick={() => setVisibleNatures(current => current.includes(key) ? current.filter(item => item !== key) : [...current, key])}><i/>{label}</button>)}<span>导航连接不代表原文关系</span></div>
        <p className="gw-prototype-note"><Info size={13}/>{exploration ? canvasData?.hasMore ? '邻域还有未加载词条，可继续加载；已加载节点保持原位。' : '当前为词条邻域；返回主题图将恢复此前视角。' : data?.truncated ? '主题画布显示前 100 个节点；检索任意词条可加载其局部邻域。' : 'Wiki 为模型整理的知识页；请结合引用核对原文。检索采用全文匹配和关系扩展。'}</p>
      </div>
      {showInspector && <aside className="gw-inspector" id={`gw-inspector-${rawId}`} aria-label="当前词条详情"><div className="gw-inspector-resizer" {...inspectorResize.separatorProps} aria-controls={`gw-inspector-${rawId}`} title="拖动调整词条详情宽度"/><div className="gw-inspector-head">{entry && <div className="gw-entry-heading"><div className={`gw-entry-emblem type-${entry.type}`}>{React.createElement(nodeKind(entry).Icon, { size: 22, weight: 'duotone' })}</div><div className="gw-entry-heading-copy"><span className="gw-entry-type">{nodeKind(entry).label}<span/>{entry.type === 'document' ? '原始资料' : mode === 'unified' ? '统一词条' : '来源词条'}{entry.archived ? ' · 历史构建版本' : entry.stale ? ' · 来源已更新' : ''}</span><h2 title={entry.label}>{entry.label}</h2></div></div>}<button onClick={() => setShowInspector(false)} aria-label="关闭词条详情"><X size={17}/></button></div><div className="gw-inspector-scroll">
        {!entry ? <p className="gw-detail-empty">构建完成后，选择词条查看正文、关联和原文证据。</p> : <>
          {entry.aliases?.length > 0 && <p className="gw-aliases">{entry.aliases.join(' / ')}</p>}
          {entry.type !== 'document' && <div className="gw-version-row"><ClockCounterClockwise size={13}/><label>版本<select value={version || ''} aria-label="Wiki 词条版本" onChange={event => setVersion(event.target.value)}><option value="">{latestVersionLabel}{latestVersion ? ` · ${latestVersion}` : ''}</option>{(detail?.history || []).map(item => <option key={item.version} value={item.version}>{item.version} · {dateText(item.createdAt)}</option>)}</select></label></div>}
          {page.loading && entry.type !== 'document' && <p className="gw-detail-empty" role="status">正在读取词条内容…</p>}{page.error && entry.type !== 'document' && <p className="gw-error-text" role="alert">{page.error}</p>}
          {metric !== 'none' && !version && !entry.archived && !entry.stale && alignedMetrics[entry.id] && <div className="gw-entry-importance"><span>{GRAPH_METRICS[metric]}</span><strong>{metricValue(alignedMetrics[entry.id][metric], metric)}</strong><small>{analysisScope === 'all' ? '全部主题' : '当前主题'} · 语义网络</small></div>}
          <p className="gw-entry-summary">{entry.summary || entry.document?.summary || (entry.type === 'document' ? '打开原始资料核对完整上下文。' : '')}</p>
          {mode === 'unified' && detail?.scopeLimited && <p className="gw-stale-note"><Info size={13}/><span>当前按所选主题展示 {detail.memberCount || detail.members?.length || 0} 项来源贡献；该统一词条共有 {detail.allMemberCount || detail.memberCount || detail.members?.length || 0} 项贡献。正文、关系与引文保持当前主题范围。</span></p>}
          {selectedPath?.pathEdges?.length > 0 && <PathDetails path={selectedPath} knownNodes={nodesById} onNavigate={navigateNode} onOpen={openEvidence} onClose={() => setSelectedPath(null)}/>}
          {entry.archived ? <p className="gw-stale-note"><ClockCounterClockwise size={13}/><span>当前展示历史构建结果，引用保持构建时的原文版本。{latestDetail ? latestDetail.archived ? '此词条已不在最新构建中，请选择图谱中的当前词条查看最新结果。' : '可切回当前版本查看最新结果。' : '可通过版本菜单或图谱查看其他已保存结果。'}</span></p> : entry.stale && <p className="gw-stale-note"><WarningCircle size={13}/>来源已有新版本。此处引用仍指向构建时的原文，可增量构建以更新。</p>}
          {entry.type === 'document' && <button className="gw-open-document" onClick={() => openDocument(entry)}>打开来源文档<ArrowUpRight size={16}/></button>}
          {(detail?.issues || []).length > 0 && <section className="gw-detail-section gw-wiki-issues"><h3><WarningCircle size={15}/>核对提示</h3><ul>{detail.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul></section>}
          {(detail?.sections || []).map((section, index) => <section className="gw-detail-section gw-wiki-section" key={`${section.title}-${index}`}><h3>{section.title}</h3>{(section.claims || []).map((claim, claimIndex) => <div className="gw-wiki-claim" key={claimIndex}><p>{claim.text}</p><EvidenceList evidence={claim.evidence} onOpen={openEvidence} compact/></div>)}</section>)}
          {mode === 'unified' && detail?.members?.length > 0 && <section className="gw-detail-section gw-unified-members"><h3><BookOpen size={16}/>来源贡献<span>{detail.members.length}</span></h3><div className="gw-group-members">{detail.members.map(member => <button key={`${member.sourceEntryId || member.id}:${member.version || ''}`} onClick={() => chooseMode('source', member)}><span><strong>{member.label}</strong><small>{member.sourceTitle || member.sourceId} · 词条版本 {member.version || '当前'}{member.stale ? ' · 来源已更新' : ''}</small></span><CaretRight size={13}/></button>)}</div><p className="gw-path-notice">综合内容的每条引文仍指向原文。打开来源词条可核对该资料的独立版本。</p></section>}
          {mode === 'source' && <button className="gw-return-unified" onClick={() => chooseMode('unified')}>返回统一词条视图<CaretRight size={13}/></button>}
          <div className="gw-entry-location"><FileText size={14}/><span title={entrySourceTitle}>{entrySourceTitle ? `来源：${entrySourceTitle}` : '来源读取中'}</span><span>{entryRelations.length} 条连接</span></div>
          {navigationGroups.length > 0 && <section className="gw-detail-section gw-navigation-section"><h3><LinkSimple size={16}/>同定义组<span>{navigationGroups.length}</span></h3>{navigationGroups.map(group => <NavigationGroup key={`${entry.id}:${version}:${group.id}`} group={group} currentId={entry.id} active={active && showInspector} onNavigate={navigateNode}/>)}</section>}
          <section className="gw-detail-section"><h3><LinkSimple size={16}/>关联词条<span>{entryRelations.length}</span></h3><div className="gw-neighbor-list">{entryRelations.map((relation, index) => {
            const target = nodesById[relation.targetId] || { id: relation.targetId, label: relation.targetLabel || '关联词条', type: relation.targetType || 'concept' };
            const reverse = relation.direction === 'incoming';
            const edge = { ...relation, id: relation.edgeId || relation.id, from: relation.from || (reverse ? target.id : entry.id), to: relation.to || (reverse ? entry.id : target.id), fromLabel: reverse ? target.label : entry.label, toLabel: reverse ? entry.label : target.label, traversalDirection: reverse ? 'reverse' : 'forward' };
            const path = { id: target.id, label: target.label, hopCount: 1, pathNodeIds: [entry.id, target.id], pathNodes: [entry, target], pathEdges: [edge] };
            return <div className="gw-wiki-relation" key={`${relation.targetId}-${index}`}><RelationBadge edge={relation}/><button onClick={() => navigateNode(target, { path })}><span className={`gw-legend-dot type-${target.type}`}/><strong>{target.label}</strong><small>{relation.label || RELATIONS[relation.type] || '关联'}</small><CaretRight size={13}/></button>{relation.evidence?.length > 0 && <EvidenceList evidence={relation.evidence} onOpen={openEvidence} compact/>}</div>;
          })}{!entryRelations.length && <p className="gw-detail-empty">这个词条暂无其他词条连接。</p>}</div></section>
          {entry.type !== 'document' && <section className="gw-detail-section gw-source-section"><h3><FileText size={16}/>来源证据<span>{sources.length}</span></h3><EvidenceList evidence={sources} onOpen={openEvidence}/>{!sources.length && !page.loading && <p className="gw-detail-empty">暂无可显示的来源证据，请核对构建任务状态。</p>}</section>}
          {detail?.updatedAt && <p className="gw-entry-updated">更新于 {dateText(detail.updatedAt)}</p>}
        </>}
      </div><div className="gw-inspector-foot"><span className="gw-status-dot"/><span>词条是整理结果，原始文档是核对依据。</span></div></aside>}
    </div>
  </section>;
}
