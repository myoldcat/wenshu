import React, { useEffect, useRef, useState } from 'react';
import { Check, GearSix, MagnifyingGlass, ArrowSquareOut, Sparkle } from '@phosphor-icons/react';
import { api } from './local-api';
import { writingMessage, materialNotice } from './writing-messages';
import RetrievalSettings from './RetrievalSettings';
import { addMaterialSelections, materialContext, parseMaterialQueries, RETRIEVAL_CHANNELS, MAX_WRITING_SOURCES } from './writing-materials';
import './writing-materials.css';

export default function WritingMaterials({ project, documents = [], models = [], busy, confirmationError, autoRecommend = true, onConfirm, onClose, onOpenDoc, continueLabel = '确认资料并生成纲要' }) {
  const [initial] = useState(() => ({ ...project, sourceIds: [...(project.sourceIds || [])], context: materialContext(project) }));
  const [assistanceIds, setAssistanceIds] = useState(project.materialSelection?.assistanceIds || []);
  const [chosen, setChosen] = useState(() => new Set(project.sourceIds || []));
  const [result, setResult] = useState(() => project.materialSelection?.items?.length ? { id: project.materialSelection.recommendationId, items: project.materialSelection.items } : null);
  const [retainedRecommendations, setRetainedRecommendations] = useState(() => (project.materialSelection?.items || []).filter(item => project.sourceIds?.includes(item.sourceId)));
  const [queries, setQueries] = useState(() => (project.materialSelection?.queries || []).join('\n'));
  const [mode, setMode] = useState(project.sourceIds?.length && ['expanded', 'extended'].includes(project.mode) ? 'expanded' : 'strict');
  const [loading, setLoading] = useState(''), [error, setError] = useState(''), [settingsOpen, setSettingsOpen] = useState(false);
  const [focusedId, setFocusedId] = useState(project.sourceIds?.[0] || project.materialSelection?.items?.[0]?.sourceId || null);
  const [detail, setDetail] = useState(null), [detailLoading, setDetailLoading] = useState(false), [detailError, setDetailError] = useState('');
  const alive = useRef(false), serial = useRef(0), controller = useRef(null), current = useRef(project);
  const exclusions = useRef(new Set([...(initial.materialSelection?.userSourceIds || []), ...(initial.materialSelection?.items || []).map(item => item.sourceId)].filter(id => !initial.sourceIds.includes(id))));
  current.current = project;
  const stale = materialContext(project) !== initial.context;
  const ownIds = initial.materialSelection?.userSourceIds || initial.sourceIds, ownSet = new Set(ownIds);
  const items = result?.items || [], known = Object.fromEntries([...(initial.materialSelection?.items || []), ...retainedRecommendations, ...items].map(item => [item.sourceId, item]));
  const recommendedIds = [...new Set([...items.map(item => item.sourceId), ...initial.sourceIds, ...retainedRecommendations.map(item => item.sourceId)])].filter(id => !ownSet.has(id));
  const catalogIds = [...new Set([...ownIds, ...recommendedIds])];
  const effectiveMode = chosen.size ? mode : 'expanded', disabled = !!loading || !!busy || stale;
  const metadata = id => ({ ...(documents.find(doc => doc.id === id) || {}), ...(initial.sources?.find(source => source.sourceId === id) || {}), ...(known[id] || {}) });
  async function request(kind) {
    if (stale || loading) return;
    const topic = initial.topic?.trim(); if (!topic) { setError('请先填写写作主题。'); return; }
    const conditions = parseMaterialQueries(queries);
    if (kind === 'search' && !conditions.length) { setError('请先生成或填写检索条件。'); return; }
    const token = ++serial.current; controller.current?.abort(); controller.current = new AbortController(); setLoading(kind); setError('');
    try {
      const response = kind === 'queries'
        ? await api('/writing/assist/material-queries', { method: 'POST', signal: controller.current.signal, body: JSON.stringify({ topic, purpose: initial.purpose || '', title: initial.title || '', targetWords: initial.targetWords }) })
        : await api('/retrieval/recommendations', { method: 'POST', signal: controller.current.signal, body: JSON.stringify({ topic, brief: { purpose: initial.purpose || '', targetWords: initial.targetWords }, sourceIds: [...chosen], queries: conditions }) });
      if (!alive.current || token !== serial.current || materialContext(current.current) !== initial.context) return;
      if (kind === 'queries') { setQueries((response.queries || []).join('\n')); if (response.assistanceId) setAssistanceIds(previous => [...new Set([...previous, response.assistanceId])]); }
      else {
        setRetainedRecommendations(previous => [...new Map([...previous, ...items].filter(item => chosen.has(item.sourceId)).map(item => [item.sourceId, item])).values()]);
        setResult(response);
        setChosen(previous => new Set(addMaterialSelections([...previous], (response.items || []).map(item => item.sourceId), exclusions.current)));
        if (!focusedId && response.items?.length) setFocusedId(response.items[0].sourceId);
      }
    } catch (problem) { if (alive.current && token === serial.current && problem.name !== 'AbortError') setError(writingMessage(problem)); }
    finally { if (alive.current && token === serial.current) setLoading(''); }
  }
  useEffect(() => {
    alive.current = true; const token = serial.current;
    if (autoRecommend && !queries.trim() && !initial.materialSelection?.confirmed) queueMicrotask(() => { if (alive.current && serial.current === token) request('queries'); });
    return () => { alive.current = false; serial.current += 1; controller.current?.abort(); };
  }, []);
  useEffect(() => { if (stale) { serial.current += 1; controller.current?.abort(); setLoading(''); } }, [stale]);
  useEffect(() => {
    setDetail(null); setDetailError(''); if (!focusedId) return;
    const abort = new AbortController(), meta = metadata(focusedId); setDetailLoading(true);
    api('/documents/' + encodeURIComponent(focusedId) + (meta.revisionId ? '?revision_id=' + encodeURIComponent(meta.revisionId) : ''), { signal: abort.signal })
      .then(response => { if (!abort.signal.aborted) setDetail(response.document); })
      .catch(problem => { if (problem.name !== 'AbortError') setDetailError(writingMessage(problem, '暂时无法读取全文，可打开原文查看。')); })
      .finally(() => { if (!abort.signal.aborted) setDetailLoading(false); });
    return () => abort.abort();
  }, [focusedId]);
  function toggle(id, selected) {
    if (disabled) return;
    if (selected) exclusions.current.delete(id); else exclusions.current.add(id);
    setChosen(previous => { const next = new Set(previous); if (selected && next.size < MAX_WRITING_SOURCES) next.add(id); else if (!selected) next.delete(id); return next; }); setError('');
  }
  function selectAll() {
    if (disabled) return;
    const next = new Set(addMaterialSelections([...chosen], catalogIds));
    for (const id of next) exclusions.current.delete(id);
    setChosen(next); setError('');
  }
  function clearSelection(onlyOwn = false) {
    if (disabled) return;
    const ids = onlyOwn ? ownIds : catalogIds;
    for (const id of ids) exclusions.current.add(id);
    setChosen(previous => new Set([...previous].filter(id => onlyOwn && !ownSet.has(id)))); setError('');
  }
  async function confirm() {
    if (disabled || chosen.size > MAX_WRITING_SOURCES) return;
    const storedItems = items.map(item => ({ sourceId: item.sourceId, revisionId: item.revisionId, title: item.title || '', summary: item.summary || '', reasons: item.reasons || [], channels: item.channels || [] }));
    try { await onConfirm({ sourceIds: [...chosen], mode: effectiveMode, materialSelection: { recommendationId: result?.id || initial.materialSelection?.recommendationId || null, passages: [], assistanceIds, queries: parseMaterialQueries(queries), items: storedItems, userSourceIds: ownIds, confirmed: true } }, initial.context); }
    catch (problem) { setError(writingMessage(problem)); }
  }
  function row(id, own = false) {
    const item = metadata(id), title = item.title || '已选资料';
    const manual = own && (Array.isArray(initial.materialSelection?.userSourceIds) || !(initial.materialSelection?.recommendationId || initial.materialSelection?.items?.length || initial.materialSelection?.passages?.length));
    const recommended = !own || !!known[id];
    return <article key={id} data-material-id={id} className={'material-card ' + (chosen.has(id) ? 'is-selected ' : '') + (focusedId === id ? 'is-focused' : '')}>
      <div className="material-card-head"><label><input type="checkbox" aria-label={'纳入资料 ' + title} disabled={disabled || !chosen.has(id) && chosen.size >= MAX_WRITING_SOURCES} checked={chosen.has(id)} onChange={event => toggle(id, event.target.checked)}/></label><button type="button" className="material-title" aria-pressed={focusedId === id} onClick={() => setFocusedId(id)}>{title}</button></div>
      <div className="material-badges">{manual && <span className="material-origin-badge" data-material-origin="manual">人工筛选</span>}{recommended && <span className="material-origin-badge" data-material-origin="recommended">系统推荐</span>}{!manual && !recommended && <span className="material-origin-badge">来源未记录</span>}{(item.channels || []).map(channel => <span key={channel}>{RETRIEVAL_CHANNELS[channel] || '主题相关'}</span>)}{(item.reasons || []).map((reason, index) => <span key={'reason-' + index}>{reason}</span>)}</div>
      {item.summary && <p className="material-summary">{item.summary}</p>}<button type="button" className="writing-inline-link" onClick={() => setFocusedId(id)}>查看详情</button>
    </article>;
  }
  const focused = focusedId ? { ...metadata(focusedId), ...detail } : null;
  const blocks = detail?.blocks || detail?.body || [];
  return <div className="writing-materials">
    {settingsOpen ? <RetrievalSettings models={models} onClose={() => setSettingsOpen(false)} onSaved={() => setSettingsOpen(false)}/> : <>
      <div className="materials-intro"><div><h3>资料筛选</h3><p>选择本次写作使用的整篇资料；点击题目可在右侧阅读全文。最多 {MAX_WRITING_SOURCES} 篇。</p></div><button type="button" className="btn" disabled={disabled} onClick={() => setSettingsOpen(true)}><GearSix size={17}/>资料检索设置</button></div>
      <div className="materials-writing-mode" role="group" aria-label="写作模式"><strong>写作模式</strong><button type="button" className="btn" aria-pressed={effectiveMode === 'strict'} disabled={disabled || !chosen.size} onClick={() => setMode('strict')}>严格模式</button><button type="button" className="btn" aria-pressed={effectiveMode === 'expanded'} disabled={disabled} onClick={() => setMode('expanded')}>拓展模式</button><span>{!chosen.size ? '未选资料，使用拓展模式。' : effectiveMode === 'strict' ? '忠实整理资料中的明确内容。' : '允许延伸讨论，新增内容需核实。'}</span></div>
      <div className="materials-query"><label htmlFor="material-queries">检索条件<small>生成后可编辑，点击检索按钮才会开始筛选</small></label><textarea id="material-queries" disabled={disabled} value={queries} rows={2} placeholder="每行一个检索角度" onChange={event => setQueries(event.target.value)}/><div><button type="button" className="btn" disabled={disabled || !queries.trim()} onClick={() => request('search')}><MagnifyingGlass size={16}/>{loading === 'search' ? '正在检索…' : result ? '重新检索推荐' : '按这些条件检索'}</button><button type="button" className="btn quiet" disabled={disabled} onClick={() => request('queries')}><Sparkle size={16}/>{loading === 'queries' ? '正在生成条件…' : queries ? '重新生成检索条件' : '生成检索条件'}</button></div></div>
      {stale && <p className="writing-candidate-error" role="alert">写作要求已改变，请返回主题设定后重新确认资料。</p>}
      {(error || confirmationError) && <p className="writing-candidate-error" role="alert">{error || writingMessage(confirmationError)}</p>}
      {!!result?.warnings?.length && <div className="materials-warnings" role="status">{[...new Set(result.warnings.map(materialNotice))].map((warning, index) => <p key={index}>{warning}</p>)}</div>}
      <div className="materials-columns materials-card-layout" aria-busy={!!loading}>
        <section className="materials-card-catalog" aria-label="写作参考资料"><header><div><h3>写作参考资料 <small>{catalogIds.length} 篇</small></h3><p>用户选择的资料优先展示。</p>{chosen.size >= MAX_WRITING_SOURCES && catalogIds.some(id => !chosen.has(id)) && <p role="status">已达 {MAX_WRITING_SOURCES} 篇上限，取消部分已选资料后可继续勾选。</p>}</div><div className="materials-selection-actions" role="group" aria-label="批量选择资料"><button type="button" className="btn quiet" disabled={disabled || chosen.size >= MAX_WRITING_SOURCES || !catalogIds.some(id => !chosen.has(id))} onClick={selectAll}>全选</button><button type="button" className="btn quiet" disabled={disabled || !chosen.size} onClick={() => clearSelection()}>取消全选</button><button type="button" className="writing-inline-link" disabled={disabled || !ownIds.some(id => chosen.has(id))} onClick={() => clearSelection(true)}>清除用户选择</button></div></header><div className="materials-list materials-card-grid">{catalogIds.map(id => row(id, ownSet.has(id)))}{!catalogIds.length && <p className="materials-empty">{result ? '暂无可用推荐，可调整检索条件后重试。' : '全局选择的资料会带到这里，也可生成条件后检索推荐资料。'}</p>}</div></section>
        <section className="material-detail" aria-label="资料详情"><header><h3>资料详情</h3>{focusedId && <button type="button" className="icon-btn" aria-label="打开资料原文" onClick={() => onOpenDoc?.({ id: focusedId, revisionId: focused?.revisionId })}><ArrowSquareOut size={17}/></button>}</header><div className="materials-list">{focused ? <><h3>{focused.title || '资料内容'}</h3>{focused.summary && <p className="material-detail-summary">{focused.summary}</p>}{detailLoading && <p role="status">正在读取全文…</p>}{detailError && <p role="alert">{detailError}</p>}{Array.isArray(blocks) && blocks.map((block, index) => <p key={block.id || block.blockId || index} className="material-detail-paragraph">{typeof block === 'string' ? block : block.text || block.content || ''}</p>)}{!detailLoading && !blocks.length && !detailError && <p className="materials-empty">暂无可显示的正文，请打开原文查看。</p>}</> : <p className="materials-empty">点击资料卡片的题目，查看资料内容。</p>}</div></section>
      </div>
      <footer className="materials-footer"><div><strong>已纳入 {chosen.size} 篇</strong><span>整篇资料 · {effectiveMode === 'strict' ? '严格模式' : '拓展模式'}</span></div><button type="button" className="btn quiet" disabled={!!busy} onClick={onClose}>返回主题设定</button><button type="button" className="btn primary" disabled={disabled || chosen.size > MAX_WRITING_SOURCES} onClick={confirm}><Check size={17}/>{busy ? '正在保存…' : continueLabel}</button></footer>
    </>}
  </div>;
}
