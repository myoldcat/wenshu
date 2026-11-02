import React from 'react';
import { createRoot } from 'react-dom/client';
import { GraphWorkspace } from '../src/GraphWorkspace';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/workspace-panels.css';
import '../src/theme-layout.css';

const root = createRoot(document.getElementById('wiki-build-scope-root'));
const resultNode = document.getElementById('wiki-build-scope-result');
const results = [], requests = [], toasts = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const corpus = Array.from({ length: 70 }, (_, index) => ({ id: `s${index + 1}`, title: `隔离资料 ${String(index + 1).padStart(2, '0')}`, revisionId: `r${index + 1}`, status: 'ready', selected: false, summary: '仅用于 Wiki 构建范围模拟验收。' }));
// The React library prop deliberately has fewer items than the full API corpus.
// No-selection scope must be resolved by the server, rather than local slicing.
let documents = corpus.slice(0, 60), active = false, scopeErrorOnce = false, scopeHold = null, buildHold = null, buildSplitOnce = false;
let statuses = new Map(), snapshot = 1, emptyJob = false;
function resetStatuses() { statuses = new Map(corpus.map((doc, index) => [doc.id, index < 20 ? 'built' : index === 20 ? 'queued' : index === 21 ? 'running' : 'pending'])); }
resetStatuses();
function preview(sourceIds) {
  const pool = sourceIds == null ? corpus : [...new Set(sourceIds)].map(id => corpus.find(doc => doc.id === id)).filter(Boolean);
  const excluded = { built: 0, active: 0, empty: 0 }, available = [];
  for (const doc of pool) {
    const status = statuses.get(doc.id);
    if (status === 'built') excluded.built += 1;
    else if (status === 'queued' || status === 'running') excluded.active += 1;
    else if (status === 'empty') excluded.empty += 1;
    else if (status === 'unclear') excluded.unclear = (excluded.unclear || 0) + 1;
    else available.push({ id: doc.id, title: doc.title, revisionId: doc.revisionId });
  }
  return { documents: available.slice(0, 20), total: pool.length, eligible: available.length, excluded, limited: available.length > 20, limit: 20 };
}
function graph() { return { nodes: [], edges: [], topics: [], jobs: emptyJob ? [{ id: 'job-empty', source_id: 's53', label: 'Wiki 构建 · 隔离资料 53', status: 'succeeded', stage: 'empty', created_at: '2026-10-28T00:00:00Z' }] : [], stats: { pending: 48, stale: 0 }, snapshotId: `isolated-${snapshot}` }; }
// This override stays installed after completion. Unknown calls fail closed;
// even a late request cannot reach the real service or any model provider.
window.fetch = async (input, options = {}) => {
  const rawUrl = typeof input === 'string' ? input : input.url;
  const url = new URL(rawUrl, location.origin), method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({ url: rawUrl, method, body });
  if (method === 'GET' && url.pathname === '/api/wiki/build-all') return response({ runs: [] });
  if (method === 'GET' && ['/api/wiki/knowledge', '/api/wiki'].includes(url.pathname)) return response(graph());
  if (method === 'POST' && url.pathname === '/api/wiki/build-scope') {
    if (scopeErrorOnce) { scopeErrorOnce = false; return response({ detail: '模拟构建范围读取失败' }, 503); }
    const data = preview(body?.sourceIds);
    if (scopeHold && same(body?.sourceIds, scopeHold.ids)) {
      const hold = scopeHold; scopeHold = null; hold.started = true;
      // Intentionally ignore AbortSignal, exercising the hook's identity guard.
      return new Promise(resolve => { hold.resolve = () => resolve(response(data)); });
    }
    return response(data);
  }
  if (method === 'POST' && url.pathname === '/api/wiki/build') {
    assert(body.sourceIds.length > 0 && body.sourceIds.length <= 20, '真正构建必须是明确的最多20篇范围');
    assert(body.sourceIds.every(id => !['built', 'queued', 'running'].includes(statuses.get(id))), '构建请求不得包含已排除资料');
    const accepted = buildSplitOnce ? body.sourceIds.slice(0, 15) : body.sourceIds;
    const skipped = buildSplitOnce ? body.sourceIds.slice(15).map(sourceId => ({ sourceId, reason: 'already_built' })) : [];
    buildSplitOnce = false;
    const jobs = accepted.map(id => ({ id: `job-${id}-${snapshot}`, sourceId: id, source_id: id, status: 'queued', label: `Wiki 构建 · ${id}` }));
    accepted.forEach(id => statuses.set(id, 'queued')); skipped.forEach(item => statuses.set(item.sourceId, 'built')); snapshot += 1;
    if (buildHold) { const hold = buildHold; buildHold = null; hold.started = true; return new Promise(resolve => { hold.resolve = () => resolve(hold.error ? response({ detail: hold.error }, 503) : response({ jobs, skipped: [] }, 202)); }); }
    return response({ jobs, skipped }, 202);
  }
  throw new Error(`禁止访问真实服务：${method} ${rawUrl}`);
};

const container = () => document.querySelector('.graph-workspace');
const panel = () => container()?.querySelector('.gw-build-panel');
const scopeItems = () => [...(panel()?.querySelectorAll('ul li') || [])].map(node => node.textContent);
const scopeIds = () => scopeItems().map(title => corpus.find(doc => doc.title === title)?.id);
const button = label => [...(container()?.querySelectorAll('button') || [])].find(node => node.textContent.trim() === label || node.getAttribute('aria-label') === label);
const buildButton = () => [...(panel()?.querySelectorAll('button') || [])].find(node => /^增量构建 \d+ 篇$/.test(node.textContent.trim()));
const rebuildButton = () => [...(panel()?.querySelectorAll('button') || [])].find(node => /^重新构建这 \d+ 篇$/.test(node.textContent.trim()));
const builds = () => requests.filter(item => item.method === 'POST' && item.url === '/api/wiki/build');
const scopes = () => requests.filter(item => item.method === 'POST' && item.url === '/api/wiki/build-scope');
const until = async (predicate, label) => { const deadline = performance.now() + 7000; while (!predicate()) { if (performance.now() > deadline) throw new Error(`超时：${label}`); await delay(20); } };
const click = async label => { const node = button(label); assert(node && !node.disabled, `找不到可用按钮：${label}`); node.click(); await delay(35); };
function mount() { root.render(<div className="workspace-shell" style={{ display: 'block', height: '100%' }}><GraphWorkspace documents={documents} active={active} onOpenDoc={() => {}} onToast={text => toasts.push(text)}/></div>); }
async function select(ids) { documents = corpus.slice(0, 60).map(doc => ({ ...doc, selected: ids.includes(doc.id) })); mount(); await delay(35); }
async function open() { if (!panel()) await click('构建 Wiki'); await until(() => panel()?.querySelector('.gw-build-actions button') && !panel().textContent.includes('正在读取构建范围'), '构建范围读完'); }
function passed(name) { results.push({ name, status: 'passed' }); resultNode.textContent = JSON.stringify({ status: 'running', results }, null, 2); }

async function run() {
  mount(); await until(container, '隐藏组件挂载'); await delay(80); assert(requests.length === 0, '隐藏图谱不得调用 API');
  active = true; mount(); await until(() => requests.some(item => item.method === 'GET'), '图谱加载'); await delay(60);
  assert(scopes().length === 0 && builds().length === 0, '未打开范围前不得预读或创建任务');
  await open();
  assert(scopes().at(-1).body.sourceIds == null, '无勾选必须让服务端按整个文库选取');
  assert(same(scopeIds(), Array.from({ length: 20 }, (_, index) => `s${index + 23}`)), '应排除前20篇成功与21/22排队运行，再取23到42篇');
  assert(panel().textContent.includes('整个文库') && panel().textContent.includes('已有有效结果') && panel().textContent.includes('排队或正在构建'), '范围应解释全库及排除计数');
  assert(builds().length === 0, '查看预览不得开始构建');
  passed('先排除成功/排队/运行，再从整个服务端文库取最多20篇；打开范围仅只读');

  buildSplitOnce = true;
  buildButton().click(); await until(() => builds().length === 1 && !panel(), '显式提交增量构建');
  assert(same(builds()[0].body.sourceIds, Array.from({ length: 20 }, (_, index) => `s${index + 23}`)) && builds()[0].body.force === false, '仅提交用户看见的20篇预览');
  await open();
  assert(same(scopeIds(), Array.from({ length: 20 }, (_, index) => `s${index + 43}`)), '再次打开应推进到43到62，且包含本地列表60条以外的资料');
  assert(builds().length === 1, '后续范围刷新不得自动排队');
  assert(toasts[0]?.includes('本次提交') && toasts[0]?.includes('15 项等待或正在构建') && toasts[0]?.includes('5 篇已有当前结果'), '提交回执应明确标注为本次提交');
  assert(!container().textContent.includes('15 项等待或正在构建；5 篇已有当前结果'), '旧的提交回执不能常驻范围面板或冒充实时状态');
  passed('显式点击才提交准确预览范围；下一批跳过已入队，并能从服务端完整文库补足');

  resetStatuses();
  const selectedRange = Array.from({ length: 26 }, (_, index) => `s${index + 1}`);
  await select(selectedRange); await until(() => same(scopeIds(), ['s23', 's24', 's25', 's26']), '超过20篇的完整已选范围');
  assert(same(scopes().at(-1).body.sourceIds, selectedRange), '已选范围超过20篇时必须先传完整范围筛选，不能先截断');
  assert(builds().length === 1, '扩大已选预览不得自动排队');
  passed('超过20篇已选资料先筛选完整范围，保留第21篇以后符合条件的候选');

  await select(['s1', 's21', 's22', 's24', 's60']);
  await until(() => same(scopeIds(), ['s24', 's60']), '已选范围筛选');
  assert(same(scopes().at(-1).body.sourceIds, ['s1', 's21', 's22', 's24', 's60']), '预览需传完整已选范围而非前20');
  assert(panel().textContent.includes('已选资料'), '已选范围文案缺失');
  assert(builds().length === 1, '切换勾选不能保存或构建');
  passed('有勾选仅筛选已选完整范围，候选不足20也不从未选资料补齐');

  await select(['s1', 's21', 's22']);
  await until(() => panel()?.textContent.includes('当前范围没有待构建资料。'), '已选均已排除');
  assert(scopeItems().length === 0 && buildButton().disabled && rebuildButton().disabled, '无候选必须禁用全部构建操作');
  assert(builds().length === 1, '空的已选候选不得回退到全库构建');
  passed('已选资料全部排除时保持空范围，禁用构建，不回退全库');

  const liveRange = Array.from({ length: 20 }, (_, index) => `s${index + 1}`);
  liveRange.forEach((id, index) => statuses.set(id, index < 15 ? 'queued' : 'built'));
  await select(liveRange);
  await until(() => panel()?.textContent.includes('5 篇已有有效结果，15 篇排队或正在构建'), '实时范围初始15活动5完成');
  const stableSnapshot = snapshot, beforePoll = scopes().length, beforeBuilds = builds().length;
  liveRange.forEach(id => statuses.set(id, 'built'));
  // Neither graph jobs nor its snapshot changes. Scope must poll independently.
  await until(() => panel()?.textContent.includes('20 篇已有有效结果，0 篇排队或正在构建'), '独立范围轮询更新已完成资料');
  assert(scopes().length > beforePoll && snapshot === stableSnapshot && builds().length === beforeBuilds, '范围轮询应独立于图谱快照，且不能创建任务');
  assert(!container().textContent.includes('15 项等待或正在构建；5 篇已有当前结果') && !panel().querySelector('.gw-build-feedback'), '实时更新后旧提交回执不得残留');
  passed('图谱快照/任务不变时范围仍独立轮询，15活动5完成更新为20完成，无旧回执或自动构建');

  const held = { ids: ['s44'], started: false }; scopeHold = held; await select(['s44']);
  await until(() => held.started, '旧范围请求挂起');
  assert(buildButton()?.disabled !== false && rebuildButton()?.disabled !== false, '范围读取期间不得用旧候选提交');
  await select(['s45']); await until(() => same(scopeIds(), ['s45']), '新勾选范围完成');
  held.resolve(); await delay(100);
  assert(same(scopeIds(), ['s45']), '忽略AbortSignal的旧响应不能覆盖新勾选范围');
  passed('读取中立即屏蔽旧候选；切换勾选后迟到响应不会覆盖新范围');

  scopeErrorOnce = true; await select(['s46']);
  await until(() => panel()?.textContent.includes('模拟构建范围读取失败'), '范围失败显示');
  assert(buildButton()?.disabled !== false && rebuildButton()?.disabled !== false && builds().length === 1, '读取失败不能提交旧范围或自动重试构建');
  await click('刷新构建范围'); await until(() => same(scopeIds(), ['s46']) && !buildButton().disabled, '手动刷新恢复');
  passed('读取失败保留错误并禁用构建，显式刷新后恢复准确范围');

  for (const theme of ['jade', 'glass', 'civic']) {
    document.documentElement.dataset.theme = theme; await delay(30);
    assert(same(scopeIds(), ['s46']) && !buildButton().disabled && !rebuildButton().disabled, `${theme}主题不能改变范围或操作`);
  }
  const count = builds().length; rebuildButton().click(); await until(() => builds().length === count + 1 && !panel(), '显式重新构建');
  assert(same(builds().at(-1).body.sourceIds, ['s46']) && builds().at(-1).body.force === true, '重新构建也只提交同一过滤后的预览候选');
  passed('三主题保持相同范围，显式重新构建只提交过滤后的候选');

  await select(['s48']); await open(); await until(() => same(scopeIds(), ['s48']), '迟到提交测试的初始范围');
  const heldBuild = { started: false }; buildHold = heldBuild; buildButton().click();
  await until(() => heldBuild.started, '旧范围构建响应挂起');
  await select(['s49']); await until(() => same(scopeIds(), ['s49']), '提交期间切换新范围');
  heldBuild.resolve(); await delay(100);
  assert(panel() && same(scopeIds(), ['s49']), '旧范围的迟到提交响应不得关闭或替换新范围面板');
  assert(same(builds().at(-1).body.sourceIds, ['s48']), '已发出的构建必须固定原先显式预览范围');
  passed('已提交范围保持固定，切换选择后旧提交的迟到响应不干扰新范围');

  await select(['s50']); await until(() => same(scopeIds(), ['s50']) && buildButton() && !buildButton().disabled, '迟到错误测试的初始范围');
  const switchedFailure = { started: false, error: '模拟旧选取范围构建失败' }; buildHold = switchedFailure; buildButton().click();
  await until(() => switchedFailure.started, '旧范围错误响应挂起');
  await select(['s51']); await until(() => same(scopeIds(), ['s51']), '迟到错误前切换范围');
  switchedFailure.resolve(); await until(() => buildButton() && !buildButton().disabled, '旧错误返回后新范围解锁');
  assert(panel() && same(scopeIds(), ['s51']) && !container().textContent.includes(switchedFailure.error), '旧范围的迟到构建错误不得污染新选择面板');
  passed('切换选择后旧构建返回503，错误不显示在新范围且新候选可继续操作');

  await select(['s52']); await until(() => same(scopeIds(), ['s52']) && buildButton() && !buildButton().disabled, '关闭重开错误测试的初始范围');
  const reopenedFailure = { started: false, error: '模拟旧面板构建失败' }; buildHold = reopenedFailure; buildButton().click();
  await until(() => reopenedFailure.started, '旧面板错误响应挂起');
  await click('构建 Wiki'); await until(() => !panel(), '关闭旧范围面板');
  await open();
  // It is the same selected source, but a new panel session; the source is now
  // queued in our mock so the new preview correctly has no pending candidate.
  await until(() => panel()?.textContent.includes('当前范围没有待构建资料。'), '重开读取新的空范围');
  reopenedFailure.resolve(); await delay(100);
  assert(panel() && !container().textContent.includes(reopenedFailure.error), '关闭再重开后旧构建错误不得污染新面板会话');
  assert(buildButton().disabled && rebuildButton().disabled, '重开后的排队来源不得再次提交');
  passed('同一选择关闭再重开也隔离旧构建503，保留新会话的排队过滤结果');

  const beforeEmpty = builds().length;
  statuses.set('s53', 'unclear'); await select(['s53']);
  await until(() => panel()?.textContent.includes('1 篇已完成筛选、无可收录内容'), '筛选全空范围说明');
  assert(buildButton().disabled && rebuildButton().disabled && scopeIds().length === 0, '全空已处理结果不能反复进入待构建范围');
  emptyJob = true; snapshot += 1;
  if (!container().querySelector('.gw-jobs')) await click('Wiki 构建任务');
  await until(() => container().querySelector('.gw-job')?.textContent.includes('无可收录内容'), '全空成功任务说明');
  assert(!container().querySelector('.gw-job').classList.contains('failed') && builds().length === beforeEmpty, '全空筛选不冒充失败，也不能自动重跑');
  passed('已完成筛选的全空资料明确说明并排除，任务显示无可收录内容，不报错或自动重跑');

  const final = { status: 'passed', cases: results.length, results, previewRequests: scopes().length, buildRequests: builds().length, realApiRequests: 0, modelCalls: 0, requests, toasts };
  resultNode.textContent = JSON.stringify({ ...final, requests: undefined, toasts: undefined }, null, 2); window.__wikiBuildScopeHarness = final;
}
run().catch(error => { const result = { status: 'failed', results, error: error.stack || String(error), requests, realApiRequests: 0, modelCalls: 0 }; resultNode.textContent = JSON.stringify(result, null, 2); window.__wikiBuildScopeHarness = result; });
