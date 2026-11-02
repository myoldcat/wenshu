import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Graph as G6Graph } from '@antv/g6';
import { GraphWorkspace } from '../src/GraphWorkspace';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/workspace-panels.css';
import '../src/theme-layout.css';

// This page exercises the actual workspace. Its fixture API never delegates to
// native fetch, including unknown routes, writes, aborted and delayed requests.
const clone = value => structuredClone(value);
const params = new URLSearchParams(location.search);
const appMode = params.get('app') === '1';
const focusSuite = !appMode && params.get('test') === 'focus';
const result = document.getElementById('graph-g6-result');
const root = createRoot(document.getElementById('graph-g6-root'));
const requests = [], opened = [], toasts = [], delays = [], holds = [], runtimeErrors = [];
let fixtureFullscreen = false, lastSizedGraph = null, AppComponent = null;
// The opt-in suite observes the actual renderer without adding an application
// debug API. Forward every resize unchanged; no layout/camera code is mocked.
const originalSetSize = G6Graph.prototype.setSize;
if (focusSuite) G6Graph.prototype.setSize = function (...args) {
  lastSizedGraph = this;
  return originalSetSize.apply(this, args);
};
const labels = [
  '可信人工智能与公共治理协同机制', '大语言模型', '检索增强生成', '知识图谱', '证据与版本溯源', '智能体规划与工具调用',
  '模型评估与基准测试', '数据质量治理', '隐私计算', '人工智能风险分类', '跨部门数据共享', '公众参与和政策反馈',
  '网络安全责任边界', '公共服务可解释性', '训练数据授权', '算法透明度', '多模态信息理解', '因果分析方法',
  '人工复核与责任追踪', '面向复杂长文本的中文知识组织与证据一致性核验方法', '开放数据标准', '个人信息保护',
  '供应链安全', '模型鲁棒性', '数字政府协同', '数据分类分级', '企业融资与产业投资', '人工智能政策执行',
  '知识抽取', '主题分类', '智能辅助写作', '信息检索', '技术扩散', '数据资产管理', '公共利益评估', '治理能力建设',
];
const documents = Array.from({ length: 6 }, (_, i) => ({
  id: `g6_source_${i + 1}`, title: `隔离合成来源 ${i + 1}：人工智能与公共治理研究资料`,
  revisionId: `g6_revision_${i + 1}`, summary: '用于图谱交互验收的合成资料，不属于真实语料。',
  source: '隔离验收', selected: false, tags: [],
}));
const topicDefs = [
  { id: 'topic-ai', label: '人工智能', pathLabel: '人工智能', depth: 0, indices: Array.from({ length: 28 }, (_, i) => i) },
  { id: 'topic-safety', label: '网络安全与公共治理', pathLabel: '网络安全 / 公共治理', depth: 1, indices: Array.from({ length: 28 }, (_, i) => (i + 8) % 36) },
  { id: 'topic-data', label: '数据治理', pathLabel: '数据治理', depth: 0, indices: Array.from({ length: 26 }, (_, i) => (i + 16) % 36) },
];
let revision = 1, active = true, requestSerial = 0, responseCount = 0;
let entries = labels.map((label, i) => ({
  id: `can_g6_${String(i + 1).padStart(2, '0')}`, label,
  type: ['concept', 'entity', 'concept', 'claim', 'fact', 'concept'][i % 6],
  subtype: i % 11 === 0 ? 'method' : '', summary: `${label}的合成说明，保留来源、语义关系和人工导航的不同含义。`,
  aliases: i === 1 ? ['LLM', '语言模型'] : [], sourceId: documents[i % 6].id,
  sourceIds: [documents[i % 6].id], revisionId: documents[i % 6].revisionId,
  memberCount: i % 7 === 0 ? 2 : 1, sourceCount: 1, stale: false,
}));
const snapshotId = () => `g6-fixture-snapshot-${revision}`;
const sourceId = id => id.replace(/^can_/, 'entry_');
const canonicalId = id => id.replace(/^entry_/, 'can_');
const modeId = (id, mode) => mode === 'source' ? sourceId(id) : canonicalId(id);
const evidence = entry => [{ sourceId: entry.sourceId, revisionId: entry.revisionId,
  blockId: `g6_block_${entry.id}`, start: 0, end: 10, quote: `${entry.label}具有明确研究边界。`,
  title: documents.find(doc => doc.id === entry.sourceId)?.title || '隔离合成来源', stale: !!entry.stale }];
const topics = mode => topicDefs.map(topic => ({ ...topic, indices: undefined,
  root: modeId(entries[topic.indices[0]].id, mode), count: topic.indices.length,
  sourceCount: 6, viewId: `${topic.id}-fixture-members`,
}));
const scoped = topicId => {
  const topic = topicDefs.find(item => item.id === topicId);
  return topic ? entries.filter((entry, i) => topic.indices.includes(i) || entry.extraTopic === topicId) : entries;
};
const node = (entry, mode) => ({ ...clone(entry), id: modeId(entry.id, mode), topicId: topicDefs.find(topic => topic.indices.includes(entries.indexOf(entry)))?.id || entry.extraTopic || 'topic-ai' });

function semanticEdges(selected, mode) {
  const valid = new Set(selected.map(entry => entry.id)), edges = [];
  const add = (a, b, index) => {
    if (a === b || !valid.has(a.id) || !valid.has(b.id)) return;
    edges.push({ id: `g6_semantic_${a.id}_${b.id}`, edgeId: `g6_semantic_${a.id}_${b.id}`,
      from: modeId(a.id, mode), to: modeId(b.id, mode), type: index % 4 === 0 ? 'hierarchy' : 'association',
      label: index % 4 === 0 ? '构成研究层级' : '存在实质分析关联', layer: 'semantic', origin: 'model_extraction',
      supportKind: 'cited_original', evidence: evidence(a), stale: false });
  };
  selected.forEach((entry, index) => {
    if (selected[index + 1]) add(entry, selected[index + 1], index);
    if (index > 1 && index % 2 === 0) add(selected[0], entry, index + 1);
    if (selected[index + 5] && index % 3 === 0) add(entry, selected[index + 5], index + 2);
  });
  return edges;
}
function graph(mode, topicId = 'topic-ai', neighborhoodId = '') {
  const selected = scoped(topicId), nodes = selected.map(entry => node(entry, mode));
  const edges = semanticEdges(selected, mode);
  selected.forEach(entry => {
    const doc = documents.find(item => item.id === entry.sourceId), docId = `doc:${doc.id}`;
    if (!nodes.some(item => item.id === docId)) nodes.push({ ...clone(doc), id: docId, label: doc.title, documentId: doc.id, sourceId: doc.id, type: 'document' });
    edges.push({ id: `g6_provenance_${entry.id}`, from: modeId(entry.id, mode), to: docId, type: 'source', label: '原文依据',
      layer: 'provenance', origin: 'program_source', supportKind: 'source_reference', evidence: evidence(entry) });
  });
  for (let i = 2; i < selected.length; i += 6) edges.push({ id: `g6_navigation_${selected[i].id}`,
    from: modeId(selected[i - 1].id, mode), to: modeId(selected[i].id, mode), type: 'association', label: '人工确认相关',
    layer: 'navigation', origin: 'manual_decision', supportKind: 'manual_related', derivedBy: 'manual_related', evidence: [] });
  return { mode, nodes, edges, topics: topics(mode), topicId, rootId: neighborhoodId || modeId(selected[0].id, mode),
    stats: { pages: entries.length, sources: 6, stale: selected.filter(item => item.stale).length, pending: 0 }, jobs: [],
    snapshotId: snapshotId(), graphSnapshotId: snapshotId(), canonicalSnapshotId: snapshotId(), truncated: false, hasMore: false,
    offset: 0, nextOffset: null, totalCount: selected.length, mergeEdges: [], requiredNodeIds: [], reloadRequired: false,
    scope: { kind: neighborhoodId ? 'neighborhood' : 'topic', topicId, entryId: neighborhoodId || undefined },
  };
}
function page(id, mode, topicId) {
  const entry = entries.find(item => item.id === canonicalId(id));
  if (!entry) return null;
  const refs = evidence(entry), edges = semanticEdges(scoped(topicId), mode).filter(edge => [edge.from, edge.to].includes(id));
  return { ...node(entry, mode), version: revision, snapshotId: snapshotId(),
    history: [{ version: revision, createdAt: '2026-10-23T04:00:00Z' }], evidence: refs,
    sections: [{ title: '定义与适用边界', claims: [{ text: entry.summary, evidence: refs }] },
      ...(focusSuite || appMode ? Array.from({ length: 10 }, (_, index) => ({ title: `独立滚动验证 ${index + 1}`,
        claims: [{ text: `${entry.label}的合成说明用于检验较长正文与原文证据的阅读布局。`.repeat(4), evidence: refs }] })) : [])],
    relations: edges.map(edge => { const targetId = edge.from === id ? edge.to : edge.from; return { ...edge, targetId,
      targetLabel: entries.find(item => item.id === canonicalId(targetId))?.label, targetType: 'concept', direction: edge.from === id ? 'outgoing' : 'incoming' }; }),
    members: mode === 'source' ? undefined : [{ id: sourceId(entry.id), sourceEntryId: sourceId(entry.id), label: entry.label,
      type: entry.type, sourceId: entry.sourceId, sourceTitle: refs[0].title, revisionId: entry.revisionId, version: 1 }],
    navigationGroups: [], issues: [], provenance: { identityScope: mode === 'source' ? 'source' : 'knowledge', evidenceType: 'original_blocks' },
  };
}
function analytics(mode, params) {
  const kind = params.get('scope') === 'all' ? 'all' : 'topic', topicId = kind === 'topic' ? params.get('topic_id') || 'topic-ai' : null;
  const selected = kind === 'all' ? entries : scoped(topicId), edges = semanticEdges(selected, mode);
  const incoming = new Map(), outgoing = new Map();
  edges.forEach(edge => { incoming.set(edge.to, (incoming.get(edge.to) || 0) + 1); outgoing.set(edge.from, (outgoing.get(edge.from) || 0) + 1); });
  const metrics = {}, size = selected.length;
  selected.forEach((entry, index) => {
    const id = modeId(entry.id, mode), inDegree = incoming.get(id) || 0, outDegree = outgoing.get(id) || 0, degree = inDegree + outDegree;
    // Deterministic fixture values test rendering, sorting and scope binding;
    // they are deliberately not a second graph-algorithm implementation.
    metrics[id] = { degree, degreeCentrality: degree / Math.max(1, size - 1), inDegree, outDegree,
      pagerank: (1 + inDegree) / (size + edges.length), betweenness: index === 0 ? .43 : Math.max(0, .13 - index * .004),
      coreNumber: index < 5 ? 3 : index < 20 ? 2 : 1 };
  });
  const rankings = Object.fromEntries(Object.keys(metrics[modeId(selected[0].id, mode)]).map(key => [key,
    selected.map(entry => ({ id: modeId(entry.id, mode), label: entry.label, type: entry.type, value: metrics[modeId(entry.id, mode)][key],
      topicId: kind === 'topic' ? topicId : topicDefs.find(topic => topic.indices.includes(entries.indexOf(entry)) || entry.extraTopic === topic.id)?.id || 'topic-ai' }))
      .sort((a, b) => b.value - a.value || a.id.localeCompare(b.id)).map((item, index) => ({ ...item, rank: index + 1 })),
  ]));
  return { mode, snapshotId: snapshotId(), analysisId: `fixture-analysis-${revision}-${mode}-${kind}-${topicId || 'all'}`,
    scope: { kind, topicId }, graph: { nodeCount: size, edgeCount: edges.length }, metrics, rankings,
    algorithms: { fixture: true, directed: true, layers: ['semantic'], excludedLayers: ['navigation', 'provenance'],
      pagerank: { alpha: .85 }, betweenness: { normalized: true, approximate: false } },
    computedAt: '2026-10-23T04:00:00Z', cacheHit: responseCount > 1 };
}
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const matches = (matcher, url) => matcher instanceof RegExp ? (matcher.lastIndex = 0, matcher.test(url)) : url.includes(String(matcher));
const aborted = () => new DOMException('The operation was aborted.', 'AbortError');
function wait(ms, signal, ignoreAbort, held) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted && !ignoreAbort) { reject(aborted()); return; }
    let timer;
    const done = () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); resolve(); };
    const cancel = () => { clearTimeout(timer); reject(aborted()); };
    if (!ignoreAbort) signal?.addEventListener('abort', cancel, { once: true });
    if (held) held.release = done; else timer = setTimeout(done, ms);
  });
}
window.fetch = async (input, options = {}) => {
  const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
  const method = String(options.method || input?.method || 'GET').toUpperCase();
  const signal = options.signal || input?.signal;
  const record = { id: ++requestSerial, url: url.pathname + url.search, method, startedAt: performance.now(), snapshotId: snapshotId(), status: 'pending' };
  requests.push(record);
  if (method !== 'GET') { record.status = 405; record.completedAt = performance.now(); return response({ detail: '隔离验收禁止所有写请求，不会访问真实服务或调用模型。' }, 405); }
  const mode = url.pathname.includes('/knowledge') ? 'unified' : 'source';
  const params = url.searchParams, topicId = params.get('topic_id') || 'topic-ai';
  let data, status = 200;
  if (appMode && url.pathname === '/api/documents') data = { documents: documents.map(doc => ({ ...clone(doc),
    kind: 'article', status: 'ready', read: true, createdAt: '2026-10-23T04:00:00Z', date: '2026-10-23',
    classificationPaths: [], concepts: [], coverCandidates: [], cardCover: { status: 'no_candidates', revisionId: doc.revisionId } })), total: documents.length };
  else if (appMode && url.pathname === '/api/jobs') data = { jobs: [], counts: {}, total: 0 };
  else if (appMode && url.pathname === '/api/models') data = { models: [], preferences: {
    defaultModelId: null, summaryModelId: null, translationModelId: null, classificationModelId: null,
    autoTranslate: false, autoProcess: false, idleProcess: false, idleStart: '00:00', idleEnd: '08:00' } };
  else if (appMode && url.pathname === '/api/writing/projects') data = { projects: [], capabilities: {} };
  else if (['/api/wiki/knowledge', '/api/wiki'].includes(url.pathname)) data = graph(mode, topicId);
  else if (url.pathname.endsWith('/analytics')) {
    const expected = params.get('snapshot_id');
    if (expected && expected !== snapshotId()) { status = 409; data = { detail: '模拟图谱快照已更新，请重新计算。', snapshotId: snapshotId() }; }
    else data = analytics(mode, params);
  } else if (url.pathname.endsWith('/neighborhood')) data = graph(mode, topicId, params.get('entry_id'));
  else if (/\/pages\//.test(url.pathname)) { data = page(decodeURIComponent(url.pathname.split('/').at(-1)), mode, topicId); if (!data) { status = 404; data = { detail: '隔离样本中没有该词条' }; } }
  else if (url.pathname.endsWith('/search')) {
    const query = (params.get('q') || '').toLowerCase(), selected = scoped(topicId).filter(entry => [entry.label, ...entry.aliases].join(' ').toLowerCase().includes(query));
    const seed = selected[0], scopeEntries = scoped(topicId);
    const relation = seed && semanticEdges(scopeEntries, mode).find(edge => edge.from === modeId(seed.id, mode));
    const target = relation && scopeEntries.find(entry => modeId(entry.id, mode) === relation.to);
    const expanded = target ? [{ ...node(target, mode), reason: '沿有据语义关系发现', hopCount: 1,
      pathNodeIds: [modeId(seed.id, mode), modeId(target.id, mode)], pathNodes: [node(seed, mode), node(target, mode)],
      pathEdges: [{ ...relation, fromLabel: seed.label, toLabel: target.label, traversalDirection: 'forward' }], evidence: evidence(target) }] : [];
    data = { query, mode: 'fulltext+graph', results: selected.map(entry => ({ ...node(entry, mode), snippet: entry.summary, reason: '隔离词条全文命中', evidence: evidence(entry), score: 1 })),
      expanded, evidence: [], snapshotId: snapshotId(), truncated: false };
  } else if (url.pathname === '/api/wiki/resolution/proposals') data = { proposals: [], decisions: [], jobs: [], snapshotId: snapshotId() };
  else { status = 404; data = { detail: `隔离验收未定义接口：${url.pathname}；真实网络未访问。` }; }
  // Capture data before delaying, so a late response genuinely carries an old
  // snapshot. ignoreAbort allows exercising stale-response guards explicitly.
  data = clone(data);
  const delayRule = delays.find(rule => matches(rule.matcher, record.url));
  if (delayRule?.once) delays.splice(delays.indexOf(delayRule), 1);
  const held = holds.find(item => !item.started && matches(item.matcher, record.url));
  if (held) { held.started = true; held.requestId = record.id; }
  try {
    if (held || delayRule) await wait(delayRule?.ms || 0, signal, held?.ignoreAbort ?? delayRule?.ignoreAbort, held);
    else if (signal?.aborted) throw aborted();
    record.status = status; record.completedAt = performance.now(); responseCount += 1;
    if (!focusSuite) result.textContent = `快照 ${revision} · ${entries.length} 词条 · ${requests.length} 次受控请求 · 写入 ${requests.filter(item => item.method !== 'GET').length} 次（均拒绝）`;
    return response(data, status);
  } catch (error) { record.status = error.name === 'AbortError' ? 'aborted' : 'failed'; record.completedAt = performance.now(); throw error; }
};
function Fixture() {
  const [fullscreen, setFullscreen] = useState(false);
  fixtureFullscreen = fullscreen;
  return <div className="workspace-shell" style={{ display: 'block', height: '100%' }}><GraphWorkspace documents={documents} active={active}
    fullscreen={fullscreen} onFullscreenChange={setFullscreen}
    onOpenDoc={doc => opened.push(clone(doc))} onToast={message => toasts.push(message)}/></div>;
}
function mount() {
  if (appMode) { if (AppComponent) root.render(<React.StrictMode><AppComponent/></React.StrictMode>); }
  else root.render(<React.StrictMode><Fixture/></React.StrictMode>);
}
const harness = {
  requests, opened, toasts, realApiRequests: 0, modelCalls: 0, appMode,
  getState: () => ({ snapshotId: snapshotId(), revision, active, fullscreen: fixtureFullscreen, entryCount: entries.length, sourceCount: documents.length, topics: topics('unified'), pending: requests.filter(item => item.status === 'pending'), held: holds.map(({ matcher, ...item }) => ({ ...item, matcher: String(matcher), release: undefined })) }),
  setTheme(theme) { if (!['jade', 'glass', 'civic'].includes(theme)) throw new Error('Unknown theme'); document.documentElement.dataset.theme = theme; },
  setActive(value) { active = !!value; mount(); },
  bumpSnapshot() { revision += 1; return snapshotId(); },
  appendNodes(count = 3, topicId = 'topic-ai') {
    if (!Number.isInteger(count) || count < 1 || count > 100) throw new Error('count must be 1..100');
    const added = [];
    for (let i = 0; i < count; i += 1) {
      const serial = entries.length + 1, template = entries[(serial - 1) % 36];
      const entry = { ...clone(template), id: `can_g6_extra_${serial}`, label: `新增节点 ${serial}：长中文标签与增量布局稳定性`, extraTopic: topicId };
      entries.push(entry); added.push(entry.id);
    }
    revision += 1; return { snapshotId: snapshotId(), ids: added };
  },
  setDelay(matcher, ms, { ignoreAbort = false } = {}) { delays.push({ matcher, ms, ignoreAbort, once: false }); },
  delayNext(matcher, ms, { ignoreAbort = false } = {}) { delays.push({ matcher, ms, ignoreAbort, once: true }); },
  holdNext(matcher, { ignoreAbort = false } = {}) { const id = `hold-${holds.length + 1}`; holds.push({ id, matcher, ignoreAbort, started: false, release: null }); return id; },
  release(id) { const hold = holds.find(item => item.id === id); if (!hold?.release) throw new Error('Held request has not started'); hold.release(); hold.released = true; },
  clearDelays() { delays.splice(0); },
  clearRequests() { requests.splice(0); },
  fixtureGraph: (mode = 'unified', topicId = 'topic-ai') => clone(graph(mode, topicId)),
  fixtureAnalytics: (scope = 'topic', topicId = 'topic-ai', mode = 'unified') => clone(analytics(mode, new URLSearchParams({ scope, topic_id: topicId }))),
};
window.__graphG6Harness = harness;
window.addEventListener('error', event => { runtimeErrors.push(event.message); if (!focusSuite) result.textContent = `运行错误：${event.message}`; });
window.addEventListener('unhandledrejection', event => { const message = event.reason?.message || String(event.reason); runtimeErrors.push(message); if (!focusSuite) result.textContent = `异步错误：${message}`; });
const theme = params.get('theme');
if (['jade', 'glass', 'civic'].includes(theme)) harness.setTheme(theme);
mount();

async function mountApp() {
  result.dataset.status = 'loading-app';
  // Import the real shell only in this manual mode. The exact main-entry CSS
  // order follows it as ordinary stylesheets, including already loaded sheets,
  // so the fixture does not manufacture sidebar dimensions or fullscreen paint.
  const { App } = await import('../src/App.jsx');
  const entryStyles = ['styles.css', 'themes.css', 'jade-theme.css', 'glass-theme.css', 'civic-theme.css',
    'workspace-layout.css', 'ambient-background.css', 'glass-refinement.css', 'edge-light.css', 'brand.css',
    'theme-layout.css', 'document-cards.css', 'reader-layout.css', 'document-tags.css',
    'civic-card-selection.css', 'article-card-materials.css'];
  await Promise.all(entryStyles.map(name => new Promise((resolve, reject) => {
    const sheet = document.createElement('link'); sheet.rel = 'stylesheet'; sheet.href = `/src/${name}?direct`;
    sheet.dataset.graphAppFixture = name;
    sheet.onload = resolve; sheet.onerror = () => reject(new Error(`无法读取应用样式：${name}`));
    document.head.appendChild(sheet);
  })));
  const container = document.getElementById('graph-g6-root');
  result.closest('aside').hidden = true;
  Object.assign(container.style, { height: '100vh', minHeight: '0', minWidth: '0' });
  AppComponent = App; mount(); result.dataset.status = 'app-ready';
}
if (appMode) mountApp().catch(error => { runtimeErrors.push(error.message); result.dataset.status = 'failed'; result.textContent = JSON.stringify({ errors: runtimeErrors }); });

async function runFocusSuite() {
  result.dataset.status = 'running';
  const checks = [], errors = [];
  const nextFrame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const waitFor = async (read, message, timeout = 15000) => {
    const deadline = performance.now() + timeout;
    while (performance.now() < deadline) { const value = read(); if (value) return value; await sleep(25); }
    throw new Error(`等待超时：${message}`);
  };
  const byLabel = label => document.querySelector(`button[aria-label="${label}"]`);
  const click = async button => {
    assert(button && !button.disabled, '操作按钮必须可用');
    button.focus({ preventScroll: true }); button.click(); await nextFrame();
  };
  const ready = async () => {
    const shell = await waitFor(() => {
      const item = document.querySelector('.gw-g6-shell');
      return item?.dataset.renderState === 'ready' && item.dataset.renderedCount > 0 && item;
    }, '实际 G6 绘制完成');
    await nextFrame();
    return shell;
  };
  const camera = graph => ({ zoom: graph.getZoom(), center: [...graph.getViewportCenter()] });
  const positions = graph => graph.getNodeData().map(item => [item.id, ...graph.getElementPosition(item.id)]);
  const sameCamera = (before, after) => Math.abs(before.zoom - after.zoom) < .0001
    && before.center.every((value, index) => Math.abs(value - after.center[index]) < .75);
  const samePositions = (before, after) => before.length === after.length
    && before.every((item, index) => item[0] === after[index][0] && item.slice(1).every((value, axis) => Math.abs(value - after[index][axis + 1]) < .05));
  const check = (name, evidence) => { checks.push({ theme: document.documentElement.dataset.theme, name, ...evidence }); };
  const canvasFullscreenControl = expectedFullscreen => {
    const button = byLabel(expectedFullscreen ? '退出图谱全屏' : '进入图谱全屏');
    const canvas = document.querySelector('.gw-canvas'), controls = canvas?.querySelector('.gw-zoom-controls');
    assert(button && canvas && controls, '画布与全屏操作完整可用');
    assert(document.querySelectorAll('.gw-fullscreen-toggle').length === 1 && controls.contains(button), '唯一全屏按钮位于画布内的缩放控件组');
    assert(!document.querySelector('.gw-page-heading .gw-fullscreen-toggle'), '页眉不再保留重复的全屏入口');
    assert(button.innerText.trim() === '', '全屏按钮只显示图标');
    assert(button.title === button.getAttribute('aria-label') && button.getAttribute('aria-pressed') === String(expectedFullscreen), '纯图标按钮保留名称、提示和当前状态');
    const svg = button.querySelector('svg'), paths = [...button.querySelectorAll('svg path')];
    const iconSubpaths = paths.reduce((count, path) => count + (path.getAttribute('d').match(/[Mm]/g) || []).length, 0);
    assert(svg && iconSubpaths === 4, '全屏图标包含四个方向的箭头');
    const rect = button.getBoundingClientRect(), bounds = canvas.getBoundingClientRect();
    assert(rect.width >= 40 && rect.height >= 40, '画布全屏按钮保持可点击的控件尺寸');
    assert(rect.left >= bounds.left - .5 && rect.top >= bounds.top - .5 && rect.right <= bounds.right + .5 && rect.bottom <= bounds.bottom + .5, '全屏按钮整体位于画布边界内');
    const otherControls = [...canvas.querySelectorAll('.gw-tool-button,.gw-build-trigger,.gw-topic-trigger,.gw-zoom-controls button')].filter(item => item !== button);
    otherControls.forEach(item => {
      const other = item.getBoundingClientRect();
      const overlap = Math.max(0, Math.min(rect.right, other.right) - Math.max(rect.left, other.left))
        * Math.max(0, Math.min(rect.bottom, other.bottom) - Math.max(rect.top, other.top));
      assert(overlap < .5, `全屏按钮不覆盖其他画布操作：${item.getAttribute('aria-label') || item.textContent}`);
    });
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    assert(button === hit || button.contains(hit), '全屏按钮中心未被画布或其他控件遮挡');
    return { x: rect.left - bounds.left, y: rect.top - bounds.top, width: rect.width, height: rect.height,
      arrowCount: iconSubpaths, icon: paths.map(path => path.getAttribute('d')).join(' ') };
  };
  const fixedInspectorTitle = async () => {
    const inspector = document.querySelector('.gw-inspector'), scroll = inspector?.querySelector('.gw-inspector-scroll');
    const title = inspector?.querySelector('.gw-entry-heading h2'), close = byLabel('关闭词条详情');
    assert(inspector && scroll && title && close, '标题、正文和关闭入口齐全');
    assert(!scroll.contains(title), '词条标题必须位于滚动正文之外');
    assert(scroll.scrollHeight > scroll.clientHeight + 200, '使用真实长正文形成足够的滚动距离');
    scroll.scrollTop = 0; await nextFrame();
    const before = { title: title.getBoundingClientRect().top, close: close.getBoundingClientRect().top, scrollTop: scroll.getBoundingClientRect().top };
    scroll.scrollTop = Math.min(700, scroll.scrollHeight - scroll.clientHeight);
    scroll.dispatchEvent(new Event('scroll', { bubbles: true })); await nextFrame();
    assert(scroll.scrollTop > 100, '词条正文实际向下滚动');
    assert(Math.abs(title.getBoundingClientRect().top - before.title) < 1, '正文滚动时词条标题位置固定');
    assert(Math.abs(close.getBoundingClientRect().top - before.close) < 1, '正文滚动时关闭入口位置固定');
    assert(title.getBoundingClientRect().bottom <= scroll.getBoundingClientRect().top + 1, '固定标题与滚动正文互不覆盖');
    assert(close.getBoundingClientRect().top >= inspector.getBoundingClientRect().top, '关闭入口留在面板边界内');
    return { scrolled: scroll.scrollTop, titleTop: before.title, closeTop: before.close };
  };
  const escape = async () => {
    (document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    await nextFrame();
  };
  const report = status => {
    result.dataset.status = status;
    const output = { status, checks, errors, runtimeErrors, realApiRequests: harness.realApiRequests, modelCalls: harness.modelCalls,
      writes: requests.filter(item => item.method !== 'GET').length, requests: requests.length };
    harness.focusResult = output; result.textContent = JSON.stringify(output);
  };
  try {
    await ready();
    // Disable animation through the real control so camera/layout assertions
    // describe settled state, independent of OS reduced-motion preferences.
    await click(byLabel('图谱显示设置'));
    const motion = document.querySelector('.gw-motion-toggle input');
    if (motion?.checked) { motion.click(); await nextFrame(); }
    const radial = [...document.querySelectorAll('[aria-label="图谱布局"] button')].find(item => item.textContent.includes('径向'));
    await click(radial);
    await waitFor(() => document.querySelector('.gw-g6-shell')?.dataset.renderedLayout === 'radial', '切换到非默认径向布局');
    await escape();
    for (const currentTheme of ['jade', 'glass', 'civic']) {
      harness.setTheme(currentTheme); await nextFrame();
      const shell = await ready();
      const expandControl = canvasFullscreenControl(false);
      const { icon: expandIcon, ...expandGeometry } = expandControl;
      check('画布内四箭头全屏入口可见且不遮挡其他控件', expandGeometry);
      await waitFor(() => document.querySelector('.gw-inspector .gw-wiki-section'), '读取合成词条正文');
      check('普通视图长正文滚动标题固定', await fixedInspectorTitle());
      // Select a non-root node through the same accessible control as users.
      const selectedId = 'can_g6_02';
      await click(document.querySelector(`[data-graph-node="${selectedId}"]`));
      await waitFor(() => shell.dataset.renderedSelected === selectedId && document.querySelector('.gw-entry-heading h2')?.textContent === labels[1], '保留明确的非根节点选择');
      await click(byLabel('放大图谱'));
      await sleep(70); await ready();
      const renderer = lastSizedGraph, canvas = shell.querySelector('canvas') || shell.querySelector('.gw-g6-canvas')?.firstElementChild;
      assert(renderer && canvas, '已挂载真实 G6 实例和绘制表面');
      const before = { camera: camera(renderer), positions: positions(renderer), layout: shell.dataset.renderedLayout,
        selected: shell.dataset.renderedSelected, canvasHeight: shell.clientHeight,
        headingHeight: document.querySelector('.gw-page-heading').getBoundingClientRect().height };
      const requestCount = requests.length;
      await click(byLabel('进入图谱全屏'));
      await waitFor(() => document.querySelector('.graph-workspace.is-fullscreen'), '图谱进入全屏布局');
      await sleep(100); await ready();
      assert(byLabel('退出图谱全屏')?.getAttribute('aria-pressed') === 'true', '全屏按钮表达当前状态并可退出');
      const collapseControl = canvasFullscreenControl(true);
      assert(collapseControl.icon !== expandIcon, '进入全屏后四箭头图标切换为向内收缩');
      const { icon: collapseIcon, ...collapseGeometry } = collapseControl;
      check('全屏内四箭头收缩入口可见且不遮挡其他控件', collapseGeometry);
      const headingHeight = document.querySelector('.gw-page-heading').getBoundingClientRect().height;
      assert(headingHeight < before.headingHeight - 8, '全屏后上方标题区域实际缩小');
      assert(shell.clientHeight > before.canvasHeight + 8, '全屏后画布实际得到更多高度');
      check('全屏缩小标题并增加画布高度', { beforeHeadingHeight: before.headingHeight, headingHeight, beforeCanvasHeight: before.canvasHeight, canvasHeight: shell.clientHeight });
      assert(lastSizedGraph === renderer && (shell.querySelector('canvas') || shell.querySelector('.gw-g6-canvas')?.firstElementChild) === canvas, '全屏切换复用原 G6 实例与画布');
      assert(shell.dataset.renderedLayout === before.layout && shell.dataset.renderedSelected === before.selected, '布局与所选非根节点保留');
      assert(samePositions(before.positions, positions(renderer)), '节点世界坐标没有重新排布');
      assert(sameCamera(before.camera, camera(renderer)), '全屏尺寸改变保持相机缩放与世界中心');
      check('全屏保留 G6 实例、选择、布局和相机', { selected: before.selected, layout: before.layout, zoom: renderer.getZoom() });
      check('全屏长正文滚动标题固定', await fixedInspectorTitle());

      await click(byLabel('关闭词条详情'));
      assert(!document.querySelector('.gw-inspector') && document.querySelector('.graph-workspace.is-fullscreen'), '详情关闭与全屏相互独立');
      canvasFullscreenControl(true);
      await click(byLabel('退出图谱全屏'));
      assert(!document.querySelector('.graph-workspace.is-fullscreen') && !document.querySelector('.gw-inspector'), '退出全屏保留详情关闭状态');
      canvasFullscreenControl(false);
      await click(byLabel('进入图谱全屏'));
      assert(!document.querySelector('.gw-inspector'), '再次进入全屏仍保留详情关闭状态');
      await click(byLabel('展开词条详情'));
      await waitFor(() => document.querySelector('.gw-entry-heading h2')?.textContent === labels[1], '重新打开详情仍为所选词条');
      assert(document.querySelector('.graph-workspace.is-fullscreen'), '重新打开详情不退出全屏');
      check('右栏独立关闭、跨全屏恢复和重新打开', { selectedTitle: document.querySelector('.gw-entry-heading h2').textContent });

      await click(byLabel('图谱显示设置'));
      assert(document.querySelector('.gw-filter-popover'), '显示设置浮层已打开');
      await escape();
      assert(!document.querySelector('.gw-filter-popover') && document.querySelector('.graph-workspace.is-fullscreen'), '首次 Escape 只关闭显示设置');
      assert(document.activeElement === byLabel('图谱显示设置'), '关闭显示设置后焦点返回触发按钮');
      const picker = document.querySelector('.gw-topic-trigger');
      await click(picker);
      await waitFor(() => document.querySelector('.gw-topic-menu [role="menuitemradio"]') === document.activeElement, '图谱列表键盘焦点进入当前条目');
      await escape();
      assert(!document.querySelector('.gw-topic-menu') && document.querySelector('.graph-workspace.is-fullscreen'), '图谱列表的 Escape 优先关闭列表');
      assert(document.activeElement === picker, '关闭图谱列表后焦点返回其入口');
      await escape();
      assert(!document.querySelector('.graph-workspace.is-fullscreen'), '没有浮层时 Escape 退出全屏');
      assert(document.activeElement === byLabel('进入图谱全屏'), '退出全屏后焦点返回全屏按钮');
      await sleep(100); await ready();
      assert(canvasFullscreenControl(false).icon === expandIcon, '退出全屏后恢复原四箭头扩张图标');
      assert(lastSizedGraph === renderer && shell.dataset.renderedSelected === selectedId && shell.dataset.renderedLayout === 'radial', '多次进入退出不替换实例、所选词条或布局');
      assert(samePositions(before.positions, positions(renderer)) && sameCamera(before.camera, camera(renderer)), '收展右栏与退出全屏后相机和节点位置保留');
      assert(requests.slice(requestCount).every(item => item.method === 'GET'), '收展布局不触发写请求');
      check('Escape 按浮层优先级关闭并恢复焦点', { rendererPreserved: true, selection: selectedId, layout: shell.dataset.renderedLayout });
    }
    assert(!runtimeErrors.length, `页面没有运行错误：${runtimeErrors.join('；')}`);
    assert(requests.every(item => item.method === 'GET') && !opened.length && !harness.realApiRequests && !harness.modelCalls, '验收未保存、打开真实来源、请求真实 API 或调用模型');
    report('passed');
  } catch (error) { errors.push({ message: error.message, stack: error.stack }); report('failed'); }
  finally { G6Graph.prototype.setSize = originalSetSize; }
}
if (focusSuite) runFocusSuite();
