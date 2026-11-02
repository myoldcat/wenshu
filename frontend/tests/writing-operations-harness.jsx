import React from 'react';
import { createRoot } from 'react-dom/client';
import WritingOperations from '../src/WritingOperations.jsx';
import '../src/writing-operations.css';

const output = document.getElementById('operations-test-result');
const requests = [], results = [];
const longText = `${'这是一段需要完整保留的资料正文。<script>仅作为文本</script>\n'.repeat(2200)}长文结尾_SENTINEL`;
const messages = [{ role: 'system', content: '请按冻结写作资料与指令生成中文正文。\n\n保留全部证据定位。' }, { role: 'user', content: JSON.stringify({ title: '完整资料', sources: Array.from({ length: 75 }, (_, index) => ({ id: index, content: index === 74 ? longText : `资料内容 ${index}` })), last: '输入末尾_SENTINEL' }, null, 2) }];
const project = id => ({ id, version: 17, title: id === 'p1' ? '人工智能治理的制度与实践' : id === 'p2' ? '切换任务与迟到记录' : id === 'deleted-1' ? '已删除文章一_保持检查上下文' : id === 'deleted-2' ? '已删除文章二_剩余文章' : `写作任务 ${id}`, mode: 'strict', runCount: 3, callCount: 2, updatedAt: '2026-10-23T03:30:00Z', ...(id.startsWith('deleted-') ? { deletedAt: '2026-10-23T05:00:00Z' } : {}) });
const run = (id, projectId = 'p1', extra = {}) => ({ id, projectId, kind: 'section', model: { id: 'model-fixture', name: '隔离模型' }, jobId: `job-${id}`, status: 'succeeded', createdAt: '2026-10-23T02:00:00Z', callCount: 1, attempts: 1, logCoverage: 'recorded', ...extra });
const automaticRun = id => run(id, 'p1', { kind: 'automatic', callCount: 0, automatic: { status: 'completed_with_issues', report: { outputAvailable: id === 'automatic-partial', summary: { generatedSections: id === 'automatic-partial' ? 1 : 0, preservedSections: 0, skippedSections: id === 'automatic-partial' ? 1 : 0 } } } });
const call = id => ({ id, runId: id === 'c2' ? 'r2' : 'r1', projectId: 'p1', kind: 'section', attempt: 1, httpAttempt: 1, status: 'succeeded', validationStatus: 'valid', startedAt: '2026-10-23T02:00:00Z', finishedAt: '2026-10-23T02:00:02Z', durationMs: 2000, httpStatus: 200, transport: 'injected' });
const page = (items, total = items.length, offset = 0) => ({ items, total, offset, limit: 30, hasMore: offset + items.length < total });
const json = value => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
let releaseLate, blockSecondCall = false, failProjects = false;
const manual = new URLSearchParams(location.search).get('manual') === '1';
let enableTrash = manual, purgeResult = 'conflict', releasePurge, releaseProjectRefresh, releaseDeletedCall;
let holdNextProjects = false, holdNextDeletedCall = false;
const purged = new Set();
let copiedText = '';
Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async value => { copiedText = value; } } });
window.fetch = async (url, options = {}) => {
  const method = options.method || 'GET';
  requests.push({ url, method, body: options.body });
  if (!String(url).startsWith('/api/operations/writing/')) throw new Error(`Blocked non-fixture request ${method} ${url}`);
  const parsed = new URL(url, location.origin), path = parsed.pathname, offset = Number(parsed.searchParams.get('offset') || 0);
  if (method === 'DELETE' && path === '/api/operations/writing/projects/deleted-1') {
    if (purgeResult === 'conflict') return new Response(JSON.stringify({ detail: '文章状态已改变，请刷新后重试。' }), { status: 409 });
    if (purgeResult === 'failure') return new Response(JSON.stringify({ detail: '隔离彻底删除失败' }), { status: 503 });
    if (purgeResult === 'pending') return new Promise(resolve => { releasePurge = () => { purged.add('deleted-1'); resolve(json({ purged: true, projectId: 'deleted-1' })); }; });
    throw new Error('Unexpected fixture purge state');
  }
  if (method !== 'GET') throw new Error(`Blocked non-fixture mutation ${method} ${url}`);
  if (path.endsWith('/projects')) {
    if (failProjects) return new Response(JSON.stringify({ detail: '隔离连接错误' }), { status: 503 });
    assert(['all', 'active', 'deleted'].includes(parsed.searchParams.get('state')), 'Project requests must retain the state filter');
    assert(Number(parsed.searchParams.get('limit')) > 0, 'Filtered pagination lost its limit parameter');
    const state = parsed.searchParams.get('state');
    const active = [...Array.from({ length: 30 }, (_, index) => project(index < 2 ? `p${index + 1}` : `task-${index}`)), project('last-page')];
    const deleted = enableTrash ? [project('deleted-1'), project('deleted-2')].filter(item => !purged.has(item.id)) : [];
    const items = state === 'deleted' ? deleted : state === 'active' ? active : [...active, ...deleted];
    const result = page(items.slice(offset, offset + Number(parsed.searchParams.get('limit'))), items.length, offset);
    if (holdNextProjects) { holdNextProjects = false; return new Promise(resolve => { releaseProjectRefresh = () => resolve(json(result)); }); }
    return json(result);
  }
  if (path.endsWith('/projects/p1/runs')) return json(page([run('r1'), run('r2'), run('legacy', 'p1', { kind: 'outline', callCount: 0, logCoverage: 'legacy_unavailable' }), automaticRun('automatic-empty'), automaticRun('automatic-partial')]));
  if (path.endsWith('/projects/p2/runs')) return json(page([run('other', 'p2', { callCount: 0, logCoverage: 'not_called', status: 'queued' })]));
  if (path.endsWith('/projects/deleted-1/runs')) return json(page([run('deleted-run', 'deleted-1')]));
  if (/\/projects\/[^/]+\/runs$/.test(path)) return json(page([]));
  if (path.endsWith('/runs/r1/calls')) return json(page([call('c1')]));
  if (path.endsWith('/runs/r2/calls')) return json(page([call('c2')]));
  if (path.endsWith('/runs/deleted-run/calls')) return json(page([{ ...call('deleted-call'), projectId: 'deleted-1', runId: 'deleted-run' }]));
  if (/\/runs\/[^/]+\/calls$/.test(path)) return json(page([]));
  if (path.endsWith('/calls/c1')) return json({ call: { ...call('c1'), request: { messages, temperature: 0.2 }, messages, response: { text: JSON.stringify({ title: '校验前模型返回', body: longText, end: '输出末尾_SENTINEL' }), raw: ` {"choices":[{"message":{"content":"返回原文_HTTP_SENTINEL"}}]}\n` } } });
  if (path.endsWith('/calls/c2')) {
    if (blockSecondCall) return new Promise(resolve => { releaseLate = () => resolve(json({ call: { ...call('c2'), messages: [{ role: 'user', content: '迟到内容_STALE_SENTINEL' }], response: null } })); });
    return json({ call: { ...call('c2'), messages: [{ role: 'user', content: '第二次交互' }], response: null } });
  }
  if (path.endsWith('/calls/deleted-call')) {
    const result = { call: { ...call('deleted-call'), projectId: 'deleted-1', runId: 'deleted-run', messages: [{ role: 'user', content: holdNextDeletedCall ? '彻底删除后迟到调用_PURGED_STALE_SENTINEL' : '已删除文章历史调用_DELETED_HISTORY_SENTINEL' }], response: null } };
    if (holdNextDeletedCall) { holdNextDeletedCall = false; return new Promise(resolve => { releaseDeletedCall = () => resolve(json(result)); }); }
    return json(result);
  }
  const match = path.match(/\/runs\/([^/]+)$/);
  if (match) return json({ run: match[1].startsWith('automatic-') ? automaticRun(match[1]) : run(match[1]), history: match[1] === 'legacy' ? { requests: [{ label: '历史纲要输入', sectionIds: [], messages: [{ role: 'user', content: '历史冻结输入_HISTORICAL_SENTINEL' }] }], candidate: { title: '历史校验后结果_CANDIDATE_SENTINEL' }, rawResponseAvailable: false, inputKind: 'frozen_inputs', note: '历史输入仅为冻结快照；原始返回未记录。' } : { requests: [], candidate: null } });
  throw new Error(`Blocked unknown fixture endpoint ${url}`);
};
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
async function until(predicate, label) { for (let attempt = 0; attempt < 200; attempt += 1) { if (predicate()) return; await wait(20); } throw new Error(`Timed out: ${label}`); }
const text = () => document.getElementById('operations-test-root').textContent;
const findButton = label => [...document.querySelectorAll('button')].find(button => button.textContent.includes(label));
const passed = name => { results.push({ name, status: 'passed' }); output.textContent = JSON.stringify({ status: 'running', results }, null, 2); };
const root = createRoot(document.getElementById('operations-test-root'));
root.render(<React.StrictMode><WritingOperations /></React.StrictMode>);

async function runTests() {
  await until(() => document.querySelector('.ops-call-details'), 'first call details');
  assert(!requests.some(request => request.url.endsWith('/calls/c2')), 'Unselected call details were eagerly loaded');
  assert(!requests.some(request => request.url.endsWith('/runs/legacy')), 'Unselected historical payload was eagerly loaded');
  assert(document.querySelector('.ops-call-details').textContent.includes('已收到返回'), 'A model HTTP return was presented as content success');
  document.querySelector('.ops-project-row.active').click();
  await wait(30);
  assert(document.querySelector('.ops-call-details'), 'Re-selecting the same project cleared its current process');
  passed('Call and historical snapshots are loaded only when selected');
  const userMessage = document.querySelectorAll('.ops-message')[1];
  assert(userMessage.textContent.includes('75 项'), 'Large JSON array count was not shown');
  assert(!userMessage.textContent.includes('长文结尾_SENTINEL'), 'Collapsed evidence array eagerly mounted every large item');
  const rawButton = [...userMessage.querySelectorAll('button')].find(button => button.textContent === '原始文本'); rawButton.click();
  await until(() => userMessage.textContent.includes('输入末尾_SENTINEL'), 'full raw prompt');
  assert(userMessage.querySelector('pre').textContent === messages[1].content, 'Raw prompt changed whitespace or characters');
  assert(!userMessage.querySelector('script'), 'Prompt text was interpreted as HTML');
  passed('Long prompts stay verbatim and escaped; JSON branches mount on demand');
  findButton('复制完整记录').click();
  await until(() => copiedText, 'full record clipboard');
  const copied = JSON.parse(copiedText);
  assert(copied.messages[1].content === messages[1].content && copied.response.raw.endsWith(']}\n'), 'Full record copy lost prompt or original response characters');
  let exportedBlob;
  const originalCreateObjectURL = URL.createObjectURL, originalAnchorClick = HTMLAnchorElement.prototype.click;
  URL.createObjectURL = blob => { exportedBlob = blob; return 'blob:isolated-writing-export'; };
  HTMLAnchorElement.prototype.click = function click() { if (!this.download) originalAnchorClick.call(this); };
  try { findButton('下载 JSON').click(); await until(() => exportedBlob, 'full record export'); const exported = JSON.parse(await exportedBlob.text()); assert(exported.messages[1].content === messages[1].content && exported.response.raw === copied.response.raw, 'Download lost full prompt or raw HTTP response'); }
  finally { URL.createObjectURL = originalCreateObjectURL; HTMLAnchorElement.prototype.click = originalAnchorClick; }
  passed('Copy and download preserve complete records without changing clipboard or files');
  findButton('返回结果').click();
  await until(() => text().includes('原始 HTTP 返回'), 'response panel');
  assert(text().includes('输出末尾_SENTINEL'), 'Structured model output lost last field');
  const bodyBranch = [...document.querySelectorAll('.ops-json-branch')].find(branch => branch.querySelector(':scope > summary strong')?.textContent === 'body');
  bodyBranch.querySelector('summary').click();
  await until(() => bodyBranch.textContent.includes('长文结尾_SENTINEL'), 'long response body');
  assert(bodyBranch.querySelector('pre').textContent === longText, 'Long response body was truncated');
  const httpResponse = [...document.querySelectorAll('.ops-extra')].find(details => details.querySelector(':scope > summary')?.textContent === '原始 HTTP 返回');
  [...httpResponse.querySelectorAll('button')].find(button => button.textContent === '原始文本').click();
  await until(() => httpResponse.textContent.includes('返回原文_HTTP_SENTINEL'), 'raw HTTP body');
  assert(httpResponse.querySelector('pre').textContent === ' {"choices":[{"message":{"content":"返回原文_HTTP_SENTINEL"}}]}\n', 'Raw HTTP whitespace changed');
  passed('Full structured response and original HTTP body are independently readable');
  document.querySelector('[aria-label="外观主题"]').value = 'civic';
  document.querySelector('[aria-label="外观主题"]').dispatchEvent(new Event('change', { bubbles: true }));
  await until(() => document.documentElement.dataset.theme === 'civic', 'theme switch');
  assert(document.getElementById('ops-response-tab').getAttribute('aria-selected') === 'true', 'Theme change reset selected response');
  passed('Theme changes retain task, process and response selection');
  findButton('继续载入（30 / 31）').click();
  await until(() => text().includes('写作任务 last-page'), 'project pagination');
  assert(document.querySelectorAll('.ops-project-row').length === 31, 'Pagination removed or duplicated task rows');
  passed('Project pagination keeps all loaded rows');
  [...document.querySelectorAll('.ops-run-row')].find(button => button.textContent.includes('生成纲要')).click();
  await until(() => text().includes('历史冻结输入_HISTORICAL_SENTINEL'), 'historical snapshot');
  assert(text().includes('历史校验后结果_CANDIDATE_SENTINEL'), 'Historical validated result unavailable');
  assert(text().includes('原始模型返回未记录'), 'Historical output provenance was hidden');
  passed('Historical frozen inputs and validated results remain distinct from original returns');
  for (const label of ['未生成正文', '正文部分完成']) {
    const row = [...document.querySelectorAll('.ops-run-row')].find(button => button.textContent.includes(label));
    assert(row?.textContent.includes('执行已结束'), `${label} lost the separate execution status`);
    row.click();
    await until(() => document.querySelector('.ops-inspector .ops-facts')?.textContent.includes(`正文结果${label}`), `${label} inspector outcome`);
    assert(!document.querySelector('.ops-inspector .ops-run-heading')?.textContent.includes('成功'), `${label} execution implied manuscript acceptance`);
  }
  passed('Empty and partial manuscripts keep outcome separate from successful execution');
  blockSecondCall = true;
  document.querySelectorAll('.ops-run-row')[1].click();
  await until(() => releaseLate, 'pending second call');
  document.querySelectorAll('.ops-project-row')[1].click();
  await until(() => text().includes('此过程尚未产生已记录的模型交互'), 'other project');
  releaseLate(); await wait(80);
  assert(!text().includes('迟到内容_STALE_SENTINEL'), 'Late response crossed selected project boundary');
  passed('Late call responses cannot overwrite another task');
  failProjects = true; findButton('刷新记录').click();
  await until(() => text().includes('隔离连接错误'), 'connection error');
  assert(document.querySelector('[role="alert"]'), 'Connection error is not announced');
  failProjects = false; [...document.querySelectorAll('button')].find(button => button.textContent === '重试').click();
  await until(() => !text().includes('隔离连接错误'), 'retry');
  passed('Connection failures have a recoverable retry state');
  assert(requests.every(request => request.method === 'GET' && request.url.startsWith('/api/operations/writing/')), 'Read-only checks escaped mocked endpoints');

  enableTrash = true;
  const stateButton = label => [...document.querySelectorAll('[aria-label="文章状态"] button')].find(button => button.textContent === label);
  stateButton('在用').click();
  await until(() => stateButton('在用').getAttribute('aria-pressed') === 'true' && document.querySelectorAll('.ops-project-row').length === 30, 'active project filter');
  assert(!findButton('彻底删除文章'), 'An active article exposed permanent deletion');
  stateButton('已删除').click();
  await until(() => text().includes('已删除文章历史调用_DELETED_HISTORY_SENTINEL'), 'deleted article history');
  assert(document.querySelectorAll('.ops-project-row').length === 2, 'Deleted filter mixed in active projects');
  assert(document.querySelector('.ops-deleted-label').textContent.includes('2026/10/23 13:00:00'), 'Deleted article omitted the deletion time');
  assert(new URL(location.href).searchParams.get('state') === 'deleted', 'Article filter was not retained in the URL');
  passed('Server state filters keep active articles safe and deleted history readable');

  const deleteRequests = () => requests.filter(request => request.method === 'DELETE');
  findButton('彻底删除文章').click();
  await until(() => document.querySelector('[role="dialog"]'), 'purge confirmation');
  assert(document.querySelector('[role="dialog"]').textContent.includes('正文、修改历史、生成任务及专属模型调用记录将永久删除，无法恢复'), 'Purge confirmation omitted its permanent scope');
  assert(document.querySelector('[role="dialog"]').textContent.includes('共享材料推荐记录会保留'), 'Purge confirmation misrepresented shared material logs');
  assert(deleteRequests().length === 0, 'Opening confirmation sent a destructive request');
  findButton('取消').click();
  await until(() => !document.querySelector('[role="dialog"]'), 'cancel purge');
  assert(deleteRequests().length === 0 && text().includes('已删除文章历史调用_DELETED_HISTORY_SENTINEL'), 'Cancel purged or lost the inspection context');
  passed('Permanent deletion requires explicit confirmation and cancel leaves history intact');

  findButton('彻底删除文章').click();
  await until(() => document.querySelector('[role="dialog"]'), 'conflict dialog');
  findButton('确认彻底删除').click();
  await until(() => document.querySelector('[role="dialog"] [role="alert"]'), 'purge version conflict');
  assert(JSON.parse(deleteRequests()[0].body).expectedVersion === 17, 'Purge did not submit the reviewed project version');
  assert(findButton('确认彻底删除').disabled, 'A conflict allowed blind retry with an old version');
  assert(document.querySelector('.ops-project-row.active').textContent.includes('已删除文章一') && text().includes('已删除文章历史调用_DELETED_HISTORY_SENTINEL'), 'Conflict discarded the article or selected history');
  findButton('关闭并刷新').click();
  await until(() => !document.querySelector('[role="dialog"]') && text().includes('已删除文章历史调用_DELETED_HISTORY_SENTINEL'), 'refresh after conflict');
  passed('A 409 retains the selected article and requires refreshed confirmation');

  purgeResult = 'failure';
  findButton('彻底删除文章').click();
  await until(() => document.querySelector('[role="dialog"]'), 'failure dialog');
  findButton('确认彻底删除').click();
  await until(() => text().includes('隔离彻底删除失败'), 'purge connection failure');
  assert(document.querySelector('.ops-project-row.active').textContent.includes('已删除文章一') && text().includes('已删除文章历史调用_DELETED_HISTORY_SENTINEL'), 'Failure lost the article or selected history');
  findButton('取消').click();
  await until(() => !document.querySelector('[role="dialog"]'), 'close failure dialog');
  root.render(<React.StrictMode><WritingOperations key="reload-preserves-state" /></React.StrictMode>);
  await until(() => text().includes('已删除文章历史调用_DELETED_HISTORY_SENTINEL'), 'restored state after remount');
  assert(stateButton('已删除').getAttribute('aria-pressed') === 'true' && document.querySelector('.ops-project-row.active').textContent.includes('已删除文章一'), 'Page remount lost the state filter or reasonable selection');
  passed('Failed deletion preserves inspection context and page reload restores filter and selection');

  holdNextProjects = true; holdNextDeletedCall = true;
  findButton('刷新记录').click();
  await until(() => releaseProjectRefresh && releaseDeletedCall, 'pending project and call responses');
  purgeResult = 'pending';
  findButton('彻底删除文章').click();
  await until(() => document.querySelector('[role="dialog"]'), 'pending purge dialog');
  const confirmation = findButton('确认彻底删除');
  confirmation.click(); confirmation.click();
  await until(() => releasePurge && document.querySelector('[role="dialog"]').getAttribute('aria-busy') === 'true', 'pending purge');
  assert(deleteRequests().length === 3, 'Double confirmation sent duplicate delete requests');
  assert([...document.querySelectorAll('[role="dialog"] button')].every(button => button.disabled), 'Pending deletion allowed repeated actions');
  document.querySelector('[role="dialog"]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert(document.querySelector('[role="dialog"]'), 'Escape dismissed an unresolved deletion');
  releasePurge();
  await until(() => !document.querySelector('[role="dialog"]') && document.querySelectorAll('.ops-project-row').length === 1 && text().includes('已删除文章二_剩余文章'), 'purge success refresh');
  releaseProjectRefresh(); releaseDeletedCall(); await wait(100);
  assert(!text().includes('已删除文章一_保持检查上下文') && !text().includes('PURGED_STALE_SENTINEL') && !text().includes('DELETED_HISTORY_SENTINEL'), 'Late responses reintroduced a permanently deleted article or call');
  assert(new URL(location.href).searchParams.get('project') === 'deleted-2', 'Success did not move selection to a remaining article');
  passed('Successful purge prevents duplicate requests, clears related selections and fences late responses');

  assert(requests.every(request => request.url.startsWith('/api/operations/writing/') && (request.method === 'GET' || (request.method === 'DELETE' && request.url === '/api/operations/writing/projects/deleted-1'))), 'Fixture escaped isolated endpoints');
  // Finish with a populated screen for three-theme visual QA.
  stateButton('全部').click();
  await until(() => document.querySelector('.ops-project-row')?.textContent.includes('人工智能治理'), 'restore all projects');
  document.querySelectorAll('.ops-project-row')[0].click();
  await until(() => document.querySelector('.ops-call-details'), 'final populated screen');
  output.dataset.status = 'passed'; output.textContent = JSON.stringify({ status: 'passed', results, requests: requests.length }, null, 2);
}
if (manual) { output.dataset.status = 'manual'; output.textContent = '隔离手动检查：所有 API 均由内存模拟处理，不会删除真实文章或调用模型。'; }
else runTests().catch(error => { output.dataset.status = 'failed'; output.textContent = JSON.stringify({ status: 'failed', error: error.stack, results, requests }, null, 2); });
