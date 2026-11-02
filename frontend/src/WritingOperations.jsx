import React, { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowsClockwise, CaretDown, Check, Copy, DownloadSimple, MagnifyingGlass, SpinnerGap, Trash, Warning } from '@phosphor-icons/react';
import { api, connectionMessage } from './local-api.js';
import { OPS_PAGE_SIZE, pagedEndpoint, projectState, listPage, mergePage, readableValue, serializedRecord, operationBodyResult, operationKind, operationStatus, timeLabel, durationLabel } from './writing-operations.js';

const ROOT = '/operations/writing';
const THEMES = [{ id: 'jade', label: '青玉' }, { id: 'glass', label: '流动玻璃' }, { id: 'civic', label: '丹朱鎏金' }];
function initialTheme() {
  const requested = new URLSearchParams(location.search).get('theme');
  if (THEMES.some(theme => theme.id === requested)) return requested;
  try { const stored = localStorage.getItem('koohub-appearance'); return THEMES.some(theme => theme.id === stored) ? stored : 'civic'; } catch { return 'civic'; }
}

function usePagedRecords(endpoint, revision) {
  const [state, setState] = useState({ key: endpoint, items: [], total: 0, busy: Boolean(endpoint), error: '', hasMore: false, nextOffset: 0 });
  const [pageRequest, setPageRequest] = useState(null);
  const sequence = useRef(0);
  const removedIds = useRef(new Set());
  useEffect(() => {
    const version = ++sequence.current;
    const controller = new AbortController();
    setPageRequest(null);
    if (!endpoint) { setState({ key: endpoint, items: [], total: 0, busy: false, error: '', hasMore: false, nextOffset: 0 }); return () => controller.abort(); }
    setState(previous => ({ key: endpoint, items: previous.key === endpoint ? previous.items : [], total: previous.key === endpoint ? previous.total : 0, busy: true, error: '', hasMore: false, nextOffset: 0 }));
    const retainedCount = state.key === endpoint ? Math.max(OPS_PAGE_SIZE, state.items.length) : OPS_PAGE_SIZE;
    const fetchRefresh = async () => {
      let items = [], offset = 0, latest;
      do {
        const limit = Math.min(100, Math.max(OPS_PAGE_SIZE, retainedCount - items.length));
        const data = await api(pagedEndpoint(endpoint, limit, offset), { signal: controller.signal });
        if (controller.signal.aborted || version !== sequence.current) return null;
        latest = listPage(data, offset);
        items = mergePage(items, latest.items.filter(item => !removedIds.current.has(item.id)));
        offset = latest.offset + latest.items.length;
      } while (latest.hasMore && latest.items.length > 0 && items.length < retainedCount);
      return { ...latest, items, offset: 0, nextOffset: offset };
    };
    fetchRefresh().then(page => {
      if (controller.signal.aborted || version !== sequence.current) return;
      if (page) setState({ key: endpoint, ...page, busy: false, error: '' });
    }).catch(error => {
      if (controller.signal.aborted || version !== sequence.current) return;
      setState(previous => ({ ...previous, busy: false, error: connectionMessage(error) }));
    });
    return () => controller.abort();
  }, [endpoint, revision]);
  useEffect(() => {
    if (!pageRequest || pageRequest.key !== endpoint) return;
    const version = sequence.current;
    const controller = new AbortController();
    setState(previous => ({ ...previous, busy: true, error: '' }));
    api(pagedEndpoint(endpoint, OPS_PAGE_SIZE, pageRequest.offset), { signal: controller.signal }).then(data => {
      if (controller.signal.aborted || version !== sequence.current) return;
      const page = listPage(data, pageRequest.offset);
      setState(previous => ({ ...previous, items: mergePage(previous.items, page.items.filter(item => !removedIds.current.has(item.id))), total: page.total, hasMore: page.hasMore, nextOffset: page.offset + page.items.length, busy: false, error: '' }));
    }).catch(error => {
      if (controller.signal.aborted || version !== sequence.current) return;
      setState(previous => ({ ...previous, busy: false, error: connectionMessage(error) }));
    });
    return () => controller.abort();
  }, [endpoint, pageRequest]);
  const current = state.key === endpoint ? state : { items: [], total: 0, busy: Boolean(endpoint), error: '', hasMore: false, nextOffset: 0 };
  return { ...current, remove: id => {
    // Fence both pending refresh and pagination before clearing the selection.
    // Even a mock or intermediary ignoring abort cannot reintroduce a purged row.
    removedIds.current.add(id);
    sequence.current += 1;
    setPageRequest(null);
    setState(previous => ({ ...previous, items: previous.items.filter(item => item.id !== id), total: Math.max(0, previous.total - 1), busy: false }));
  }, loadMore: () => { if (!current.busy && current.hasMore) setPageRequest({ key: endpoint, offset: current.nextOffset, nonce: Date.now() }); } };
}

function useRecord(endpoint, revision) {
  const [state, setState] = useState({ key: '', data: null, busy: false, error: '' });
  const sequence = useRef(0);
  useEffect(() => {
    const version = ++sequence.current;
    const controller = new AbortController();
    if (!endpoint) { setState({ key: '', data: null, busy: false, error: '' }); return () => controller.abort(); }
    setState(previous => ({ key: endpoint, data: previous.key === endpoint ? previous.data : null, busy: true, error: '' }));
    api(endpoint, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted && version === sequence.current) setState({ key: endpoint, data, busy: false, error: '' });
    }).catch(error => {
      if (!controller.signal.aborted && version === sequence.current) setState(previous => ({ ...previous, busy: false, error: connectionMessage(error) }));
    });
    return () => controller.abort();
  }, [endpoint, revision]);
  return state.key === endpoint ? state : { data: null, busy: Boolean(endpoint), error: '' };
}

async function copyText(text) {
  try { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; } } catch { /* LAN HTTP pages may not have clipboard permissions. */ }
  const input = document.createElement('textarea');
  input.value = text; input.style.cssText = 'position:fixed;left:-9999px;top:0';
  const previous = document.activeElement;
  document.body.appendChild(input); input.select();
  try { if (!document.execCommand('copy')) throw new Error('复制未完成，请使用原始文本选择复制。'); } finally { input.remove(); previous?.focus(); }
}

function CopyButton({ text, label = '复制', onNotice }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  return <button className="ops-button ops-small" onClick={async () => {
    try { await copyText(text); setCopied(true); clearTimeout(timer.current); timer.current = setTimeout(() => setCopied(false), 1800); }
    catch (error) { onNotice?.(error.message); }
  }}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? '已复制' : label}</button>;
}

function Status({ value, context = 'execution' }) { return <span className={`ops-status ops-status-${value || 'unknown'}`}>{operationStatus(value, context)}</span>; }
function Notice({ children, busy = false, onRetry }) {
  return <div className={`ops-notice ${onRetry ? 'ops-notice-error' : ''}`} role={onRetry ? 'alert' : 'status'}>{busy && <SpinnerGap className="ops-spinning" size={18} />}<span>{children}</span>{onRetry && <button className="ops-button ops-small" onClick={onRetry}>重试</button>}</div>;
}
function ListFooter({ list }) {
  return <div className="ops-list-footer">{list.hasMore && <button className="ops-button" onClick={list.loadMore} disabled={list.busy}>{list.busy ? '载入中…' : `继续载入（${list.items.length} / ${list.total}）`}</button>}{!list.hasMore && list.items.length > 0 && <span>已载入 {list.items.length} / {list.total}</span>}</div>;
}
function Facts({ items }) {
  return <dl className="ops-facts">{items.filter(item => item[1] !== undefined && item[1] !== null && item[1] !== '').map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{typeof value === 'object' ? serializedRecord(value) : String(value)}</dd></div>)}</dl>;
}

// Collapsed JSON branches are mounted only when opened. Even very large
// evidence arrays remain individually reachable without rendering them all.
function StructuredValue({ value, label = '内容', level = 0, startOpen = false }) {
  const [open, setOpen] = useState(startOpen);
  const compound = value !== null && typeof value === 'object';
  const entries = compound ? Object.entries(value) : [];
  const description = Array.isArray(value) ? `${value.length} 项` : compound ? `${entries.length} 个字段` : typeof value === 'string' ? `${value.length.toLocaleString()} 字符` : String(value);
  if (!compound && typeof value !== 'string') return <div className="ops-scalar"><strong>{label}</strong><code>{String(value)}</code></div>;
  return <details className="ops-json-branch" open={open} onToggle={event => { if (event.target === event.currentTarget) setOpen(event.currentTarget.open); }}>
    <summary><CaretDown size={13} /><strong>{label}</strong><span>{description}</span></summary>
    {open && (compound ? <div className="ops-json-children">{entries.length ? entries.map(([key, item]) => <StructuredValue key={key} value={item} label={Array.isArray(value) ? `第 ${Number(key) + 1} 项` : key} level={level + 1} startOpen={level < 1 && !Array.isArray(value) && (typeof item !== 'object' || item === null) && (typeof item !== 'string' || item.length < 1200)} />) : <span className="ops-empty-value">{Array.isArray(value) ? '空数组 []' : '空对象 {}'}</span>}</div> : <pre className="ops-verbatim">{value}</pre>)}
  </details>;
}
function ContentView({ value, label = '内容', onNotice }) {
  const { structured, raw } = readableValue(value);
  const [mode, setMode] = useState('structured');
  return <section className="ops-content-view"><div className="ops-content-tools"><div className="ops-tabs" role="group" aria-label={`${label}显示方式`}><button className={mode === 'structured' ? 'active' : ''} onClick={() => setMode('structured')} aria-pressed={mode === 'structured'}>分块阅读</button><button className={mode === 'raw' ? 'active' : ''} onClick={() => setMode('raw')} aria-pressed={mode === 'raw'}>原始文本</button></div><CopyButton text={raw} onNotice={onNotice} /></div>{mode === 'raw' || structured === null ? <pre className="ops-verbatim">{raw || (raw === '' ? '（空文本）' : String(raw))}</pre> : <StructuredValue value={structured} label={label} startOpen />}</section>;
}

function Messages({ messages, onNotice }) {
  const roles = { system: '系统指令', developer: '开发者指令', user: '用户输入', assistant: '模型消息', tool: '工具返回' };
  return <div className="ops-messages">{messages.map((message, index) => <details className="ops-message" open key={index}><summary><CaretDown size={14} /><span className="ops-message-index">{String(index + 1).padStart(2, '0')}</span><strong>{roles[message.role] || message.role || '消息'}</strong><span className="ops-mono">{message.role}</span></summary><ContentView value={message.content ?? message} label={`第 ${index + 1} 条消息`} onNotice={onNotice} />{Object.keys(message).some(key => !['role', 'content'].includes(key)) && <details className="ops-extra"><summary>消息附加字段</summary><ContentView value={Object.fromEntries(Object.entries(message).filter(([key]) => !['role', 'content'].includes(key)))} label="附加字段" onNotice={onNotice} /></details>}</details>)}</div>;
}

function isRerankCall(call) { return call?.model?.phase === 'rerank' || call?.transport === 'rerank_http'; }

function CallDetails({ callId, revision, onRefresh, onNotice }) {
  const result = useRecord(callId ? `${ROOT}/calls/${encodeURIComponent(callId)}` : null, revision);
  const call = result.data?.call || result.data;
  const [tab, setTab] = useState('request');
  useEffect(() => setTab('request'), [callId]);
  if (!callId) return <Notice>选择一次交互，查看完整提示词与返回。</Notice>;
  if (result.error) return <Notice onRetry={onRefresh}>{result.error}</Notice>;
  if (!call) return <Notice busy={result.busy}>载入交互记录…</Notice>;
  const messages = Array.isArray(call.messages) ? call.messages : Array.isArray(call.request?.messages) ? call.request.messages : [];
  const rerank = isRerankCall(call);
  const download = () => {
    const url = URL.createObjectURL(new Blob([serializedRecord(call)], { type: 'application/json;charset=utf-8' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `writing-call-${String(call.id || callId).replace(/[^a-zA-Z0-9_-]/g, '_')}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <article className="ops-call-details" key={callId}><header className="ops-record-heading"><div><h3>交互详情</h3><span className="ops-mono ops-record-id">{call.id}</span></div><div className="ops-actions"><CopyButton text={serializedRecord(call)} label="复制完整记录" onNotice={onNotice} /><button className="ops-button ops-small" onClick={download}><DownloadSimple size={15} />下载 JSON</button></div></header>
    <Facts items={[["请求状态", operationStatus(call.status, 'transport')], ["响应校验", operationStatus(call.validationStatus)], ["任务尝试", call.attempt], ["HTTP 尝试", call.httpAttempt], ["开始时间", timeLabel(call.startedAt)], ["耗时", durationLabel(call.durationMs)], ["HTTP 状态", call.httpStatus], ["结束原因", call.finishReason], ["交互方式", call.transport === 'injected' ? '隔离模拟' : call.transport], ["用量", call.usage]]} />
    {call.error && <div className="ops-error-record"><strong>错误记录</strong><pre className="ops-verbatim">{typeof call.error === 'string' ? call.error : serializedRecord(call.error)}</pre></div>}
    {call.responseTruncated && <Notice>服务记录的返回结果已截断；当前显示已保存的内容。</Notice>}
    <div className="ops-record-tabs" role="tablist" aria-label="交互内容" onKeyDown={event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; const tabs = [...event.currentTarget.querySelectorAll('[role="tab"]')]; const index = tabs.indexOf(document.activeElement); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; event.preventDefault(); tabs[next].click(); tabs[next].focus(); }}><button id="ops-request-tab" role="tab" tabIndex={tab === 'request' ? 0 : -1} aria-selected={tab === 'request'} aria-controls="ops-request-panel" onClick={() => setTab('request')}>{rerank ? '提交的完整输入' : '提交的完整提示词'} <span>{rerank ? `${call.request?.documents?.length || 0} 篇候选资料` : `${messages.length} 条消息`}</span></button><button id="ops-response-tab" role="tab" tabIndex={tab === 'response' ? 0 : -1} aria-selected={tab === 'response'} aria-controls="ops-response-panel" onClick={() => setTab('response')}>返回结果</button><button id="ops-record-tab" role="tab" tabIndex={tab === 'record' ? 0 : -1} aria-selected={tab === 'record'} aria-controls="ops-record-panel" onClick={() => setTab('record')}>完整记录</button></div>
    {tab === 'request' && <section id="ops-request-panel" role="tabpanel" aria-labelledby="ops-request-tab">{rerank && call.request ? <ContentView value={call.request} label="增强排序完整输入" onNotice={onNotice} /> : <>{messages.length ? <Messages messages={messages} onNotice={onNotice} /> : <Notice>此次交互没有已保存的消息。</Notice>}<details className="ops-extra"><summary>请求参数与完整请求</summary><ContentView value={call.request ?? { messages }} label="完整请求" onNotice={onNotice} /></details></>}</section>}
    {tab === 'response' && <section id="ops-response-panel" role="tabpanel" aria-labelledby="ops-response-tab">{call.response?.text !== undefined && call.response?.text !== null && <section className="ops-record-section"><h4>模型返回内容</h4><ContentView value={call.response.text} label="模型返回内容" onNotice={onNotice} /></section>}{call.response?.raw !== undefined && call.response?.raw !== null && <details className="ops-extra" open><summary>原始 HTTP 返回</summary><ContentView value={call.response.raw} label="原始 HTTP 返回" onNotice={onNotice} /></details>}{!call.response?.raw && call.response?.json && <ContentView value={call.response.json} label="HTTP 返回 JSON" onNotice={onNotice} />}{!call.response || (call.response.raw == null && call.response.text == null && call.response.json == null) ? <Notice>{call.status === 'in_progress' ? '请求进行中，尚未收到返回。刷新后可查看最新记录。' : '此次交互没有保存到模型返回。'}</Notice> : null}</section>}
    {tab === 'record' && <section id="ops-record-panel" role="tabpanel" aria-labelledby="ops-record-tab"><ContentView value={call} label="完整交互记录" onNotice={onNotice} /></section>}
  </article>;
}

function RunInspector({ run, revision, onRefresh, onNotice }) {
  const runId = run?.id;
  const calls = usePagedRecords(runId ? `${ROOT}/runs/${encodeURIComponent(runId)}/calls` : null, revision);
  const details = useRecord(runId ? `${ROOT}/runs/${encodeURIComponent(runId)}` : null, revision);
  const [callId, setCallId] = useState(null);
  const [selectedRun, setSelectedRun] = useState(runId);
  if (runId !== selectedRun) { setSelectedRun(runId); setCallId(null); }
  useEffect(() => { if (!callId && calls.items.length) setCallId(calls.items[0].id); }, [callId, calls.items]);
  if (!run) return <div className="ops-inspector-empty"><div className="ops-empty-glyph">文</div><h2>查看写作交互</h2><p>从左侧选择写作任务及某次过程。</p></div>;
  const history = details.data?.history;
  const inspectedRun = details.data?.run?.id === run.id ? details.data.run : run;
  const bodyResult = operationBodyResult({ ...inspectedRun, automatic: inspectedRun.automatic || history?.candidate?.automatic });
  const historicalRequests = Array.isArray(history?.requests) ? history.requests : [];
  const legacyResult = history?.candidate;
  return <div className="ops-inspector-inner"><header className="ops-run-heading"><div><span className="ops-eyebrow">当前写作过程</span><h2>{operationKind(run.kind)}</h2></div><Status value={inspectedRun.status} /></header><Facts items={[["正文结果", bodyResult], ["过程 ID", run.id], ["任务 ID", run.jobId], ["模型", run.model?.name || run.model?.model || run.model?.id], ["创建时间", timeLabel(run.createdAt)], ["指令版本", run.promptVersion], ["基础版本", run.baseVersion], ["执行尝试", run.attempts], ["采用结果", run.applied === true ? '已采用' : run.applied === false ? '未采用' : undefined]]} />
    {run.error && <div className="ops-error-record"><strong>过程错误</strong><pre className="ops-verbatim">{typeof run.error === 'string' ? run.error : serializedRecord(run.error)}</pre></div>}
    <section className="ops-call-picker"><div className="ops-section-heading"><h3>模型交互 <span>{calls.total}</span></h3>{calls.busy && calls.items.length > 0 && <span className="ops-subtle">刷新中…</span>}</div>{calls.error && <Notice onRetry={onRefresh}>{calls.error}</Notice>}{!calls.items.length && calls.busy && <Notice busy>载入交互列表…</Notice>}{calls.items.length > 0 && <div className="ops-call-list" aria-label="模型交互列表">{calls.items.map((call, index) => <button className={`ops-call-row ${callId === call.id ? 'active' : ''}`} aria-pressed={callId === call.id} key={call.id} onClick={() => setCallId(call.id)}><span className="ops-call-number">{index + 1}</span><span><strong>{isRerankCall(call) ? '资料增强排序' : operationKind(call.kind)}{call.httpAttempt != null ? ` · HTTP ${call.httpAttempt}` : ''}</strong><small>{timeLabel(call.startedAt)} · {durationLabel(call.durationMs)}</small></span><Status value={call.status} context="transport" /></button>)}</div>}<ListFooter list={calls} /></section>
    {calls.items.length > 0 ? <CallDetails key={callId || runId} callId={callId} revision={revision} onRefresh={onRefresh} onNotice={onNotice} /> : !calls.busy && <section className="ops-history-record">{run.logCoverage === 'legacy_unavailable' ? <Notice>此过程没有已保存的逐次交互，原始模型返回未记录。</Notice> : <Notice>此过程尚未产生已记录的模型交互。</Notice>}{details.error && <Notice onRetry={onRefresh}>{details.error}</Notice>}{details.busy && !details.data && <Notice busy>载入过程快照…</Notice>}{historicalRequests.length > 0 && <section className="ops-record-section"><h3>历史输入快照</h3><Notice>{history.note || '冻结输入用于还原任务准备内容；原始返回未记录。'}</Notice>{historicalRequests.map((request, index) => <section className="ops-record-section" key={index}><h4>{request.label || `输入 ${index + 1}`}</h4>{Array.isArray(request.messages) && <Messages messages={request.messages} onNotice={onNotice} />}</section>)}</section>}{legacyResult !== undefined && legacyResult !== null && <section className="ops-record-section"><h3>校验后结果</h3><ContentView value={legacyResult} label="历史校验后结果" onNotice={onNotice} /></section>}</section>}
  </div>;
}

function PurgeProjectDialog({ target, busy, error, conflict, onCancel, onConfirm, onReload }) {
  const dialogRef = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    dialogRef.current?.querySelector('button')?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  return <div className="ops-modal-backdrop" onMouseDown={event => { if (!busy && event.target === event.currentTarget) onCancel(); }}>
    <section ref={dialogRef} className="ops-purge-dialog" role="dialog" aria-modal="true" aria-labelledby="ops-purge-title" aria-describedby="ops-purge-description" aria-busy={busy} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); if (!busy) onCancel(); }
      if (event.key !== 'Tab') return;
      const buttons = [...event.currentTarget.querySelectorAll('button:not(:disabled)')];
      if (!buttons.length) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0].focus(); }
    }}>
      <div className="ops-purge-heading"><Warning size={24} /><h2 id="ops-purge-title">彻底删除这篇文章？</h2></div>
      <p className="ops-purge-project">{target.title || '未命名写作任务'}</p>
      <p id="ops-purge-description">这篇文章的正文、修改历史、生成任务及专属模型调用记录将永久删除，无法恢复。关联的知源文库资料和共享材料推荐记录会保留。</p>
      <p className="ops-purge-time">标记删除时间：{timeLabel(target.deletedAt)}</p>
      {error && <div className="ops-purge-error" role="alert">{error}{conflict && <p>请关闭并刷新记录，再重新确认文章状态。</p>}</div>}
      <div className="ops-purge-actions"><button className="ops-button" onClick={conflict ? onReload : onCancel} disabled={busy}>{conflict ? '关闭并刷新' : '取消'}</button><button className="ops-button ops-danger-button" onClick={onConfirm} disabled={busy || conflict}>{busy ? <><SpinnerGap className="ops-spinning" size={16} />正在彻底删除…</> : <><Trash size={16} />确认彻底删除</>}</button></div>
    </section>
  </div>;
}

export default function WritingOperations() {
  const [theme, setTheme] = useState(initialTheme);
  const [revision, setRevision] = useState(0);
  const [stateFilter, setStateFilter] = useState(() => projectState(new URLSearchParams(location.search).get('state')));
  const [projectId, setProjectId] = useState(() => new URLSearchParams(location.search).get('project'));
  const [runId, setRunId] = useState(null);
  const [query, setQuery] = useState('');
  const [notice, setNotice] = useState('');
  const [purge, setPurge] = useState(null);
  const purgeBusy = useRef(false);
  const inspectorRef = useRef(null);
  const projects = usePagedRecords(`${ROOT}/projects?state=${stateFilter}`, revision);
  const selectedProject = projects.items.find(project => project.id === projectId);
  const selectedProjectId = selectedProject?.id;
  const runs = usePagedRecords(selectedProjectId ? `${ROOT}/projects/${encodeURIComponent(selectedProjectId)}/runs` : null, revision);
  const selectedRun = runs.items.find(run => run.id === runId);
  const refresh = () => setRevision(value => value + 1);
  const selectProject = id => { if (id !== projectId) { setProjectId(id); setRunId(null); } };
  const confirmPurge = async () => {
    if (!purge?.target?.deletedAt || purgeBusy.current || purge.conflict) return;
    const target = purge.target;
    purgeBusy.current = true;
    setPurge(previous => ({ ...previous, busy: true, error: '', conflict: false }));
    try {
      const result = await api(`${ROOT}/projects/${encodeURIComponent(target.id)}`, { method: 'DELETE', body: JSON.stringify({ expectedVersion: target.version }) });
      if (result.purged !== true || result.projectId !== target.id) throw new Error('尚未确认彻底删除结果，请刷新记录后检查。');
      projects.remove(target.id);
      selectProject(null);
      setPurge(null);
      setNotice('文章及其写作记录已彻底删除。');
      refresh();
    } catch (error) {
      setPurge(previous => ({ ...previous, busy: false, error: connectionMessage(error), conflict: error.status === 409 }));
    } finally { purgeBusy.current = false; }
  };
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('koohub-appearance', theme); } catch { /* Appearance is optional. */ }
    const url = new URL(location.href); url.searchParams.set('theme', theme); history.replaceState(null, '', url);
  }, [theme]);
  useEffect(() => {
    const url = new URL(location.href);
    url.searchParams.set('state', stateFilter);
    if (projectId) url.searchParams.set('project', projectId); else url.searchParams.delete('project');
    history.replaceState(null, '', url);
  }, [stateFilter, projectId]);
  useEffect(() => { if (!projects.busy && !projects.error && !projects.items.some(project => project.id === projectId)) selectProject(projects.items[0]?.id || null); }, [projectId, projects.items, projects.busy, projects.error]);
  useEffect(() => { if (!runs.busy && !runs.error && !runs.items.some(run => run.id === runId)) setRunId(runs.items[0]?.id || null); }, [runId, runs.items, runs.busy, runs.error]);
  useEffect(() => { if (inspectorRef.current) inspectorRef.current.scrollTop = 0; }, [projectId, runId]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 5000); return () => clearTimeout(timer); }, [notice]);
  const visibleProjects = projects.items.filter(project => `${project.title || ''} ${project.id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <main className="ops-shell"><div className="ops-page-content" inert={purge ? true : undefined}><header className="ops-header"><div className="ops-title-group"><a className="ops-return" href={`/?theme=${theme}`}><ArrowLeft size={16} />返回文枢</a><div className="ops-page-title"><h1>写作运维</h1><span>交互记录</span></div></div><div className="ops-actions"><label className="ops-theme-label">外观<select aria-label="外观主题" value={theme} onChange={event => setTheme(event.target.value)}>{THEMES.map(option => <option value={option.id} key={option.id}>{option.label}</option>)}</select></label><button className="ops-button" onClick={refresh}><ArrowsClockwise size={17} />刷新记录</button></div></header>
    <div className="ops-workspace"><aside className="ops-panel ops-projects"><div className="ops-panel-heading"><h2>写作任务 <span>{projects.total}</span></h2><p>选择任务，沿过程查看每次交互。</p></div><div className="ops-project-filters" role="group" aria-label="文章状态">{[['all', '全部'], ['active', '在用'], ['deleted', '已删除']].map(([value, label]) => <button key={value} className={stateFilter === value ? 'active' : ''} aria-pressed={stateFilter === value} onClick={() => setStateFilter(value)}>{label}</button>)}</div><label className="ops-search"><MagnifyingGlass size={17} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="筛选已载入任务" aria-label="筛选已载入写作任务" />{query && <button aria-label="清空筛选" onClick={() => setQuery('')}>×</button>}</label><div className="ops-scroll">{projects.error && <Notice onRetry={refresh}>{projects.error}</Notice>}{projects.busy && !projects.items.length && <Notice busy>载入写作任务…</Notice>}{!projects.busy && !projects.error && !projects.items.length && <Notice>{stateFilter === 'deleted' ? '没有已删除的文章。' : stateFilter === 'active' ? '没有在用的写作任务。' : '还没有写作任务。'}</Notice>}{query && !visibleProjects.length && <Notice>已载入任务中没有匹配项。</Notice>}<div className="ops-project-list">{visibleProjects.map(project => <button className={`ops-project-row ${projectId === project.id ? 'active' : ''} ${project.deletedAt ? 'ops-project-deleted' : ''}`} aria-pressed={projectId === project.id} key={project.id} onClick={() => selectProject(project.id)}><strong>{project.title || '未命名写作任务'}</strong>{project.deletedAt && <span className="ops-deleted-label">已删除 · {timeLabel(project.deletedAt)}</span>}<span>{project.mode === 'strict' ? '严谨风格' : project.mode === 'extended' ? '拓展风格' : project.id === 'preparations' ? '材料推荐' : '写作任务'}</span><div><small>{project.runCount ?? 0} 次过程 · {project.callCount ?? 0} 次交互</small><time>{timeLabel(project.updatedAt || project.createdAt).slice(0, 10)}</time></div></button>)}</div><ListFooter list={projects} /></div></aside>
    <aside className="ops-panel ops-runs"><div className="ops-panel-heading"><h2>写作过程 <span>{runs.total}</span></h2><p title={selectedProject?.title}>{selectedProject?.title || '选择左侧写作任务'}</p>{selectedProject?.deletedAt && <div className="ops-deleted-actions"><span>已删除于 {timeLabel(selectedProject.deletedAt)}</span><button className="ops-button ops-small ops-danger-button" onClick={() => setPurge({ target: { ...selectedProject }, busy: false, error: '', conflict: false })}><Trash size={14} />彻底删除文章</button></div>}</div><div className="ops-scroll">{runs.error && <Notice onRetry={refresh}>{runs.error}</Notice>}{runs.busy && !runs.items.length && <Notice busy>载入写作过程…</Notice>}{selectedProjectId && !runs.busy && !runs.error && !runs.items.length && <Notice>此任务还没有写作过程。</Notice>}<div className="ops-run-list">{runs.items.map(run => <button className={`ops-run-row ${runId === run.id ? 'active' : ''}`} aria-pressed={runId === run.id} key={run.id} onClick={() => setRunId(run.id)}><div><strong>{operationKind(run.kind)}</strong><Status value={run.status} /></div>{operationBodyResult(run) && <span className="ops-body-result">正文结果：{operationBodyResult(run)}</span>}<span>{run.model?.name || run.model?.model || '模型未记录'}</span><time>{timeLabel(run.createdAt)}</time><small>{run.callCount ?? 0} 次已记录交互{run.logCoverage === 'legacy_unavailable' ? ' · 无原始交互' : ''}</small></button>)}</div><ListFooter list={runs} /></div></aside>
    <section ref={inspectorRef} className="ops-panel ops-inspector" aria-label="写作交互详情"><RunInspector key={selectedProjectId || 'none'} run={selectedRun} revision={revision} onRefresh={refresh} onNotice={setNotice} /></section></div></div>{notice && <div className="ops-toast" role="status">{notice}</div>}{purge && <PurgeProjectDialog target={purge.target} busy={purge.busy} error={purge.error} conflict={purge.conflict} onCancel={() => { if (!purgeBusy.current) setPurge(null); }} onConfirm={confirmPurge} onReload={() => { setPurge(null); refresh(); }} />}
  </main>;
}
