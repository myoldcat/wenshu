import React from 'react';
import {createRoot} from 'react-dom/client';
import {GraphWorkspace} from '../src/GraphWorkspace';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/workspace-panels.css';
import '../src/theme-layout.css';

const root = createRoot(document.getElementById('wiki-build-all-root'));
const resultNode = document.getElementById('wiki-build-all-result');
const results = [], requests = [], toasts = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (value, message) => { if (!value) throw new Error(message); };
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const response = (data, status = 200) => new Response(JSON.stringify(data), {status, headers: {'Content-Type': 'application/json'}});
const corpus = Array.from({length: 70}, (_, i) => ({id: `s${i + 1}`, title: `隔离批量资料 ${String(i + 1).padStart(2, '0')}`, revisionId: `r${i + 1}`, selected: false, status: 'ready', summary: '仅用于构建全部待构建资料模拟验收。'}));
let documents = corpus.slice(0, 60), active = false, statuses = new Map(), runs = [], sequence = 0, heldCreate = null;
const sourceJobs = [], frozen = new Map();
function resetStatuses() { statuses = new Map(corpus.map((doc, i) => [doc.id, i < 4 ? 'built' : i === 4 ? 'queued' : i === 5 ? 'running' : 'pending'])); }
resetStatuses();
function pool(sourceIds) { return sourceIds == null ? corpus : [...new Set(sourceIds)].map(id => corpus.find(doc => doc.id === id)).filter(Boolean); }
function candidates(sourceIds) { return pool(sourceIds).filter(doc => !['built', 'queued', 'running', 'empty'].includes(statuses.get(doc.id))); }
function preview(sourceIds) {
  const scope = pool(sourceIds), available = candidates(sourceIds), excluded = {built: 0, active: 0, empty: 0};
  for (const doc of scope) {
    const status = statuses.get(doc.id);
    if (status === 'built') excluded.built += 1;
    else if (['queued', 'running'].includes(status)) excluded.active += 1;
    else if (status === 'empty') excluded.empty += 1;
  }
  return {documents: available.slice(0, 20).map(({id, title, revisionId}) => ({id, title, revisionId})), total: scope.length, eligible: available.length, excluded, limited: available.length > 20, limit: 20};
}
function makeRun(sourceIds) {
  const scope = preview(sourceIds), ids = candidates(sourceIds).map(doc => doc.id);
  if (!ids.length) return null;
  const id = `wiki-all-${++sequence}`;
  const run = {id, job: {id, kind: 'wiki_build_all', label: '构建全部待构建资料', status: 'running', stage: 'waiting', created_at: `2026-10-28T00:00:${String(sequence).padStart(2, '0')}Z`, updated_at: '2026-10-28T00:01:00Z', retryable: false}, scope: sourceIds == null ? 'library' : 'selected', total: ids.length, batchSize: 20, batches: 1, counts: {pending: Math.max(0, ids.length - 20), queued: Math.min(20, ids.length), running: 0, succeeded: 0, failed: 0, cancelled: 0, skipped: 0}, excluded: scope.excluded};
  frozen.set(id, ids);
  ids.slice(0, 20).forEach(sourceId => statuses.set(sourceId, 'queued'));
  runs.unshift(run);
  return run;
}
function advance(run, counts, batches = run.batches, status = 'running') {
  run.counts = {...run.counts, ...counts}; run.batches = batches; run.job.status = status; run.job.updated_at = `2026-10-28T00:${String(batches + 2).padStart(2, '0')}:00Z`;
  const ids = frozen.get(run.id);
  ids.forEach(sourceId => statuses.set(sourceId, 'pending'));
  let offset = 0;
  for (const [kind, count] of [['built', run.counts.succeeded], ['running', run.counts.running], ['queued', run.counts.queued]]) {
    ids.slice(offset, offset + count).forEach(sourceId => statuses.set(sourceId, kind)); offset += count;
  }
}

// Every call, including a late request after completion, stays inside this mock.
window.fetch = async (input, options = {}) => {
  const rawUrl = typeof input === 'string' ? input : input.url;
  const url = new URL(rawUrl, location.origin), method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({url: rawUrl, method, body});
  if (method === 'GET' && ['/api/wiki', '/api/wiki/knowledge'].includes(url.pathname)) return response({nodes: [], edges: [], topics: [], jobs: sourceJobs, stats: {pending: 64, stale: 0}, snapshotId: 'unchanged-graph'});
  if (method === 'POST' && url.pathname === '/api/wiki/build-scope') return response(preview(body?.sourceIds));
  if (method === 'GET' && url.pathname === '/api/wiki/build-all') return response({runs});
  if (method === 'GET' && url.pathname.startsWith('/api/wiki/build-all/')) return response({run: runs.find(run => run.id === decodeURIComponent(url.pathname.split('/').at(-1))) || null});
  if (method === 'POST' && url.pathname === '/api/wiki/build-all') {
    const hold = heldCreate; heldCreate = null;
    const run = hold?.error ? null : makeRun(body?.sourceIds);
    if (hold) {
      hold.started = true;
      // Ignore AbortSignal intentionally, so correctness requires context guards.
      return new Promise(resolve => { hold.resolve = () => resolve(hold.error ? response({detail: hold.error}, 503) : response({run, reused: false}, 202)); });
    }
    return response({run, reused: false}, 202);
  }
  if (method === 'POST' && /^\/api\/jobs\/wiki-all-\d+\/cancel$/.test(url.pathname)) {
    const id = decodeURIComponent(url.pathname.split('/').at(-2)), run = runs.find(item => item.id === id);
    assert(run, '取消必须指定已有父任务');
    run.job.status = 'cancelled'; run.job.updated_at = '2026-10-28T00:09:00Z';
    run.counts.cancelled += run.counts.pending + run.counts.queued + run.counts.running;
    run.counts.pending = run.counts.queued = run.counts.running = 0;
    frozen.get(id).forEach(sourceId => { if (statuses.get(sourceId) !== 'built') statuses.set(sourceId, 'pending'); });
    return response({job: run.job});
  }
  if (method === 'POST' && url.pathname === '/api/wiki/build') {
    assert(body.sourceIds.length > 0 && body.sourceIds.length <= 20, '单批请求必须在20篇边界内');
    const jobs = body.sourceIds.map(sourceId => ({id: `source-job-${sourceId}`, kind: 'wiki_build', source_id: sourceId, status: 'queued', label: `Wiki 构建 · ${sourceId}`, created_at: '2026-10-28T00:20:00Z'}));
    sourceJobs.unshift(...jobs); body.sourceIds.forEach(sourceId => statuses.set(sourceId, 'queued'));
    return response({jobs, skipped: []}, 202);
  }
  throw new Error(`禁止访问真实接口：${method} ${rawUrl}`);
};

const container = () => document.querySelector('.graph-workspace');
const panel = () => container()?.querySelector('.gw-build-panel');
const cards = () => [...(container()?.querySelectorAll('[aria-label="全部待构建资料任务"]') || [])];
const button = label => [...(container()?.querySelectorAll('button') || [])].find(node => node.textContent.trim() === label || node.getAttribute('aria-label') === label);
const allButton = () => button('构建全部待构建资料');
const incremental = () => [...(panel()?.querySelectorAll('button') || [])].find(node => /^增量构建 \d+ 篇$/.test(node.textContent.trim()));
const previewIds = () => [...(panel()?.querySelectorAll('ul li') || [])].map(node => corpus.find(doc => doc.title === node.textContent)?.id);
const creates = () => requests.filter(item => item.method === 'POST' && item.url === '/api/wiki/build-all');
const singles = () => requests.filter(item => item.method === 'POST' && item.url === '/api/wiki/build');
const polls = () => requests.filter(item => item.method === 'GET' && item.url.startsWith('/api/wiki/build-all'));
const cancels = () => requests.filter(item => item.method === 'POST' && item.url.endsWith('/cancel'));
const until = async (predicate, label) => { const end = performance.now() + 8000; while (!predicate()) { if (performance.now() > end) throw new Error(`超时：${label}`); await delay(20); } };
async function click(label) { const node = button(label); assert(node && !node.disabled, `缺少可操作按钮：${label}`); node.click(); await delay(40); }
function mount() { root.render(<div className="workspace-shell" style={{display: 'block', height: '100%'}}><GraphWorkspace documents={documents} active={active} onOpenDoc={() => {}} onToast={text => toasts.push(text)}/></div>); }
async function select(ids) { documents = corpus.slice(0, 66).map(doc => ({...doc, selected: ids.includes(doc.id)})); mount(); await delay(40); }
async function open() { if (!panel()) await click('构建 Wiki'); await until(() => panel()?.querySelector('.gw-build-actions button') && !panel().textContent.includes('正在读取构建范围'), '范围面板读完'); }
function passed(name) { results.push({name, status: 'passed'}); resultNode.textContent = JSON.stringify({status: 'running', results}, null, 2); }

async function run() {
  mount(); await until(container, '隐藏组件挂载'); await delay(80); assert(requests.length === 0, '隐藏图谱不得读取或创建任务');
  active = true; mount(); await until(() => polls().length, '持久任务只读恢复');
  await open();
  assert(creates().length === 0 && singles().length === 0, '打开图谱/弹窗不得隐式创建任务');
  assert(previewIds().length === 20 && panel().textContent.includes('64 篇待构建资料'), '总待构建数应为64，首批预览仅20，不能把20当总数');
  assert(allButton() && !allButton().disabled, '应有独立的构建全部待构建资料入口');
  passed('入口明确区分64篇全部待构建与20篇首批预览，浏览只读不启动');

  const firstHold = {started: false}; heldCreate = firstHold;
  const firstButton = allButton(); firstButton.click(); firstButton.click(); await until(() => firstHold.started, '首次批量提交');
  assert(creates().length === 1 && creates()[0].body.sourceIds == null, '无选范围由后端固定整个文库，双击不得重复创建');
  const libraryRun = runs[0]; assert(libraryRun.total === 64 && frozen.get(libraryRun.id).includes('s70'), '全库不得受前端已加载60篇限制');
  firstHold.resolve(); await delay(80); await open(); await until(() => cards().length > 0, '批量父任务显示');
  assert(singles().length === 0, '前端只能创建父任务，不能自己循环提交子批次');
  passed('显式一次创建固定全库父任务，双击去重，前端不循环排源任务');

  const beforeProgress = cards().map(node => node.textContent).join(''), pollCount = polls().length;
  advance(libraryRun, {pending: 44, queued: 0, running: 3, succeeded: 17});
  await until(() => cards().some(node => node.textContent.includes('17')) && cards().map(node => node.textContent).join('') !== beforeProgress, '进度独立轮询更新17篇完成');
  assert(polls().length > pollCount && creates().length === 1 && singles().length === 0, '只读进度轮询不得创建下一批请求');
  advance(libraryRun, {pending: 4, queued: 20, running: 0, succeeded: 40}, 2);
  await until(() => cards().some(node => node.textContent.includes('40')), '第二批进度');
  advance(libraryRun, {pending: 0, queued: 0, running: 0, succeeded: 64}, 4, 'succeeded');
  await until(() => !button('取消全部构建'), '末批完成收起取消入口');
  assert(creates().length === 1 && singles().length === 0, '自动续批由服务端推进，完成期间前端不补交请求');
  passed('图谱快照不变时批量进度持续刷新，跨多批至尾批完成，前端始终仅只读');

  await click('构建 Wiki'); assert(!panel(), '关闭范围弹窗');
  active = false; mount(); await delay(100);
  const hiddenCount = requests.length; await delay(3800); assert(requests.length === hiddenCount, '隐藏页面必须停止只读轮询');
  active = true; mount(); await open(); await until(() => cards().length, '重开恢复已有批量任务');
  assert(creates().length === 1, '隐藏恢复及关闭重开不能重新创建父任务');
  passed('关闭和隐藏停止相关读取，重开从持久任务恢复，不重复创建');

  resetStatuses(); const selected = Array.from({length: 45}, (_, i) => `s${i + 1}`);
  await select(selected); await until(() => previewIds().length === 20 && panel().textContent.includes('39 篇待构建资料'), '已选45篇过滤后39篇');
  await until(() => allButton() && !allButton().disabled, '已选全部入口可用');
  allButton().click(); await until(() => creates().length === 2, '已选范围创建');
  assert(same(creates().at(-1).body.sourceIds, selected), '必须发送全部45篇已选范围，不能截成20或回退全库');
  const selectedRun = runs[0]; assert(selectedRun.total === 39 && selectedRun.scope === 'selected' && frozen.get(selectedRun.id).every(id => selected.includes(id)), '已选父任务固定范围，排除后不补未选来源');
  await delay(60); await select(['s60']); await open(); await until(() => same(previewIds(), ['s60']), '运行期间新选择只更新预览');
  assert(selectedRun.total === 39 && frozen.get(selectedRun.id).length === 39 && !frozen.get(selectedRun.id).includes('s60'), '后续勾选不得扩大已创建父任务');
  passed('已选完整范围先过滤后批处理，切换勾选不能改变原父任务范围');

  await until(() => button('取消全部构建') && !button('取消全部构建').disabled, '取消入口可用');
  await click('取消全部构建'); await until(() => selectedRun.job.status === 'cancelled' && !button('取消全部构建'), '取消父任务');
  assert(cancels().length === 1 && cancels()[0].url === `/api/jobs/${selectedRun.id}/cancel`, '取消必须定向父任务，不取消其他来源或历史任务');
  assert(libraryRun.job.status === 'succeeded' && creates().length === 2, '取消不重开、不改写其他已完成父任务');
  passed('取消操作定向当前父任务，停止后续状态，不影响历史成功任务');

  await select(['s60']); await open(); await until(() => allButton() && !allButton().disabled && same(previewIds(), ['s60']), '迟到成功初始范围');
  const successHold = {started: false}; heldCreate = successHold; allButton().click(); await until(() => successHold.started, '旧选择创建响应挂起');
  await select(['s61']); await until(() => same(previewIds(), ['s61']), '挂起时切换新范围');
  const toastCount = toasts.length; successHold.resolve(); await delay(100);
  assert(panel() && same(previewIds(), ['s61']) && toasts.length === toastCount, '旧创建成功不能关闭新面板或弹出错误范围回执');
  await until(() => button('取消全部构建') && !button('取消全部构建').disabled, '迟到任务的可见取消入口'); await click('取消全部构建');
  passed('批量创建成功迟到时不关闭新选择面板，原固定任务仍可显式取消');

  await select(['s62']); await until(() => allButton() && !allButton().disabled && same(previewIds(), ['s62']), '迟到错误初始范围');
  const failureHold = {started: false, error: '隔离旧范围批量创建失败'}; heldCreate = failureHold; allButton().click(); await until(() => failureHold.started, '旧创建错误挂起');
  await select(['s63']); await until(() => same(previewIds(), ['s63']), '错误前切换选择');
  failureHold.resolve(); await until(() => allButton() && !allButton().disabled, '旧错误后新范围解锁');
  assert(panel() && !container().textContent.includes(failureHold.error), '旧范围创建错误不得污染新范围');
  passed('切换选择后的迟到503被隔离，新范围保留并可继续提交');

  await select(['s64']); await until(() => allButton() && !allButton().disabled, '关闭重开迟到错误初始范围');
  const reopenedHold = {started: false, error: '隔离旧面板批量创建失败'}; heldCreate = reopenedHold; allButton().click(); await until(() => reopenedHold.started, '旧面板错误挂起');
  await click('构建 Wiki'); await open(); reopenedHold.resolve(); await delay(100);
  assert(panel() && same(previewIds(), ['s64']) && !container().textContent.includes(reopenedHold.error), '同范围关闭重开也不能显示旧创建错误');
  passed('同一选择关闭重开形成独立面板上下文，旧错误不污染新会话');

  await select(['s65']); await until(() => incremental() && !incremental().disabled && same(previewIds(), ['s65']), '保留单批入口');
  const beforeAll = creates().length; incremental().click(); await until(() => singles().length === 1, '显式单批提交');
  assert(same(singles()[0].body.sourceIds, ['s65']) && singles()[0].body.force === false && creates().length === beforeAll, '单批功能仍只提交显式范围，不转成全部构建');
  await select(['s66']); await open(); await until(() => allButton() && !allButton().disabled, '主题截图范围');
  for (const theme of ['jade', 'glass', 'civic']) { document.documentElement.dataset.theme = theme; await delay(25); assert(allButton() && !allButton().disabled && same(previewIds(), ['s66']), `${theme}主题范围与入口必须一致`); }
  passed('原单批增量入口和三主题行为保持，全部构建为独立显式操作');

  const final = {status: 'passed', cases: results.length, results, creates: creates().length, singleBuilds: singles().length, cancels: cancels().length, progressReads: polls().length, requests, toasts, realApiRequests: 0, modelCalls: 0};
  resultNode.textContent = JSON.stringify({...final, requests: undefined, toasts: undefined}, null, 2); window.__wikiBuildAllHarness = final;
}
run().catch(error => { const result = {status: 'failed', results, error: error.stack || String(error), requests, toasts, realApiRequests: 0, modelCalls: 0}; resultNode.textContent = JSON.stringify({...result, requests: undefined}, null, 2); window.__wikiBuildAllHarness = result; });
