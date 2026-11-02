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

const resultNode = document.getElementById('wiki-unified-result');
const root = createRoot(document.getElementById('wiki-unified-root'));
const results = [], requests = [], opened = [];
const clone = value => structuredClone(value);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const documents = Array.from({ length: 12 }, (_, index) => ({ id: `s${index + 1}`, title: `隔离样本 ${index + 1}`, revisionId: `r${index + 1}`, summary: '仅用于受控交互测试', selected: false }));
const evidence = { sourceId: 's1', revisionId: 'r1', blockId: 'b1', start: 0, end: 8, quote: '模型理解上下文。', title: '隔离样本 1' };
const sourceMembers = [
  { id: 'entry-a', sourceEntryId: 'entry-a', label: '大语言模型', type: 'concept', sourceId: 's1', sourceTitle: '隔离样本 1', revisionId: 'r1', version: 1 },
  { id: 'entry-b', sourceEntryId: 'entry-b', label: 'LLM', type: 'concept', sourceId: 's2', sourceTitle: '隔离样本 2', revisionId: 'r2', version: 1 },
];
const unifiedTopics = [{ id: 'topic-ai', label: '人工智能', depth: 0, pathLabel: '人工智能', viewId: 'members-a', root: 'can_a', count: 2, sourceCount: 2 }, { id: 'topic-llm', label: '语言模型', depth: 1, pathLabel: '人工智能 / 语言模型', viewId: 'members-a', root: 'can_a', count: 2, sourceCount: 2 }];
let visible = false, snapshot = 1, merged = false, conflictOnce = true, proposalSerial = 0, proposals = [], decisions = [];
const snapshotId = () => `snapshot-${snapshot}`;
const page = (id, source, version) => ({ id, label: source ? '大语言模型' : '统一大语言模型', type: 'concept', summary: source ? '独立来源页' : '跨来源综合定义；分歧保留归属。', version: version || 1, history: [{ version: 1, createdAt: '2026-10-23T00:00:00Z' }], aliases: ['LLM'], evidence: [evidence], sections: [{ title: '定义', claims: [{ text: '模型理解上下文。', evidence: [evidence] }] }], relations: [], members: source ? undefined : clone(sourceMembers.slice(0, merged ? 2 : 1)), snapshotId: snapshotId() });
function graph(source, topic) {
  const nodes = source ? sourceMembers.map(item => ({ ...item, id: item.sourceEntryId })) : [{ id: 'can_a', label: '统一大语言模型', type: 'concept', sourceIds: ['s1', ...(merged ? ['s2'] : [])], memberCount: merged ? 2 : 1 }, ...(!merged ? [{ id: 'can_b', label: 'LLM', type: 'concept', sourceIds: ['s2'], memberCount: 1 }] : [])];
  return { nodes, edges: [], topics: source ? [{ id: 'source-topic', label: '来源图谱', root: 'entry-a', count: 2 }] : unifiedTopics, topicId: topic || (source ? 'source-topic' : 'topic-ai'), rootId: source ? 'entry-a' : 'can_a', stats: { stale: 0, pending: 0 }, jobs: [], snapshotId: snapshotId(), hasMore: false, totalCount: nodes.length };
}

// This override lives for the entire page. Unknown calls fail closed and can
// never reach the actual service, including after the assertions have finished.
window.fetch = async (input, options = {}) => {
  const rawUrl = typeof input === 'string' ? input : input.url;
  const url = new URL(rawUrl, location.origin), method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({ url: rawUrl, method, body });
  if (method === 'GET' && url.pathname === '/api/wiki/build-all') return response({ runs: [] });
  if (method === 'GET' && ['/api/wiki/knowledge', '/api/wiki'].includes(url.pathname)) return response(graph(url.pathname === '/api/wiki', url.searchParams.get('topic_id')));
  if (method === 'GET' && /\/pages\//.test(url.pathname)) return response(page(decodeURIComponent(url.pathname.split('/').at(-1)), !url.pathname.includes('/knowledge/'), url.searchParams.get('version')));
  if (method === 'GET' && url.pathname.endsWith('/neighborhood')) return response(graph(!url.pathname.includes('/knowledge/'), url.searchParams.get('topic_id')));
  if (method === 'GET' && url.pathname.endsWith('/search')) return response({ results: [{ id: 'can_a', label: '统一大语言模型', type: 'concept', summary: '同义名称命中', topicId: 'topic-ai' }], expanded: [], evidence: [evidence], snapshotId: snapshotId() });
  if (method === 'GET' && url.pathname === '/api/wiki/resolution/proposals') return response({ proposals: clone(proposals), decisions: clone(decisions), snapshotId: snapshotId() });
  if (method === 'POST' && url.pathname === '/api/wiki/resolve') {
    assert(body.sourceIds.length > 0 && body.sourceIds.length <= 10, '检测只能处理明确选定的最多十篇资料');
    proposals = [{ id: `proposal-${++proposalSerial}`, entryIds: ['entry-a', 'entry-b'], entries: clone(sourceMembers), label: '大语言模型', judgment: 'same_as', reason: '别名与定义相符，仍需人工核对。' }];
    return response({ proposals: clone(proposals), snapshotId: snapshotId() });
  }
  if (method === 'POST' && ['/api/wiki/knowledge/merge', '/api/wiki/knowledge/reject', '/api/wiki/knowledge/undo'].includes(url.pathname)) {
    if (url.pathname.endsWith('/merge') && conflictOnce) { conflictOnce = false; snapshot += 1; return response({ detail: '模拟并发快照更新' }, 409); }
    assert(body.expectedSnapshotId === snapshotId(), '决定必须绑定当前服务端快照');
    snapshot += 1;
    if (url.pathname.endsWith('/merge')) { merged = true; decisions.unshift({ id: 'decision-merge', kind: 'merge', canonicalLabel: body.label, reason: body.reason, undoable: true }); proposals = proposals.map(item => ({ ...item, status: 'confirmed' })); }
    if (url.pathname.endsWith('/reject')) { decisions.unshift({ id: 'decision-reject', kind: 'reject', label: '关联判断', reason: body.judgment, undoable: true }); proposals = proposals.map(item => ({ ...item, status: body.judgment === 'related' ? 'related' : 'rejected' })); }
    if (url.pathname.endsWith('/undo')) { const decision = decisions.find(item => item.id === body.decisionId); assert(decision, '撤销必须指向已有决定'); decision.undoable = false; decision.undone = true; decisions.unshift({ id: 'decision-undo', kind: 'undo', undoable: true }); if (decision.kind === 'merge') merged = false; }
    return response({ snapshotId: snapshotId() });
  }
  throw new Error(`禁止访问真实服务：${method} ${rawUrl}`);
};

const container = () => document.querySelector('.graph-workspace');
const text = () => container()?.textContent || '';
const button = label => [...container().querySelectorAll('button')].find(node => node.textContent.trim() === label || node.getAttribute('aria-label') === label);
const click = async label => { const node = button(label); assert(node && !node.disabled, `找不到可用按钮：${label}`); node.click(); await delay(45); };
const input = async (label, value) => {
  const node = [...container().querySelectorAll('input,textarea')].find(item => item.getAttribute('aria-label') === label);
  assert(node && !node.disabled, `找不到可编辑输入：${label}`);
  Object.getOwnPropertyDescriptor(node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set.call(node, value);
  node.dispatchEvent(new Event('input', { bubbles: true })); await delay(35);
};
const until = async (predicate, label) => { const deadline = performance.now() + 5000; while (!predicate()) { if (performance.now() > deadline) throw new Error(`超时：${label}`); await delay(20); } };
const passed = name => { results.push({ name, status: 'passed' }); resultNode.textContent = JSON.stringify({ status: 'running', results }, null, 2); };
const writes = () => requests.filter(item => item.method === 'POST');
function mount() { root.render(<div className="workspace-shell" style={{ display: 'block', height: '100%' }}><GraphWorkspace documents={documents} active={visible} onOpenDoc={doc => opened.push(doc)}/></div>); }

async function run() {
  mount(); await until(container, '隐藏组件挂载'); await delay(70); assert(requests.length === 0, '隐藏图谱不能预先调用API');
  visible = true; mount(); await until(() => text().includes('跨来源综合定义'), '统一图谱加载');
  assert(requests.some(item => item.url.startsWith('/api/wiki/knowledge?')), '默认应使用统一词条API'); assert(writes().length === 0, '进入图谱不能提交任务');
  assert(Number(container().querySelector('[role="separator"]').getAttribute('aria-valuenow')) === 360, '默认详情宽度必须为360像素');
  passed('隐藏页不请求；默认统一词条；保持360像素详情与无自动加工');

  await click('切换图谱：人工智能');
  const topicOption = container().querySelector('[role="menuitemradio"][title="人工智能 / 语言模型"]'); assert(topicOption, '缺少完整主题路径'); topicOption.click();
  await until(() => requests.some(item => item.url.includes('topic_id=topic-llm')), '分层主题切换');
  assert(text().includes('语言模型'), '层级主题应可选择');
  passed('层级主题列表保留完整路径、键盘控件与服务端成员视图');

  await click('搜索图谱'); await input('检索全部 Wiki 词条与原文证据', 'LLM');
  await until(() => requests.some(item => item.url.includes('/knowledge/search') && item.url.includes('topic_id=topic-llm')), '主题内同义检索');
  assert(text().includes('同义名称命中'), '同义命中应显示统一词条');
  await click('清空图谱搜索'); await click('搜索图谱');
  passed('统一检索绑定当前主题真实资料范围，同义命中统一词条');

  const member = container().querySelector('.gw-unified-members .gw-group-members button'); assert(member, '统一页缺少来源贡献'); member.click();
  await until(() => text().includes('独立来源页'), '来源页回查');
  assert(requests.some(item => item.url === '/api/wiki/pages/entry-a?version=1'), '来源贡献必须打开固定词条版本');
}

// Continue the evidence check using the exact evidence button, whose title is
// intentionally descriptive while its visible text remains the source title.
async function finish() {
  const evidenceButton = container().querySelector('.gw-source-section .gw-evidence-list button'); assert(evidenceButton, '缺少原文证据按钮'); evidenceButton.click();
  assert(opened.at(-1)?.revisionId === 'r1' && opened.at(-1)?.matchBlockId === 'b1', '原文回查必须固定修订与段落');
  await click('返回统一词条视图'); await until(() => text().includes('跨来源综合定义'), '回到统一视图');
  passed('来源贡献回查固定词条版本，原文固定修订与段落，返回保留主题');

  await click('整理关联'); await until(() => text().includes('已选 0 / 10 篇'), '候选面板');
  assert(button('检测所选资料').disabled && writes().length === 0, '打开候选面板不能默认勾选或提交检测');
  const checkboxes = () => [...container().querySelectorAll('.gw-resolution-sources input')];
  for (const node of checkboxes().slice(0, 10)) { node.click(); await delay(10); }
  assert(checkboxes()[10].disabled && text().includes('已选 10 / 10 篇'), '必须禁止选超过十篇');
  for (const node of checkboxes().slice(2, 10)) { node.click(); await delay(10); }
  await click('检测所选资料'); await until(() => text().includes('可能是同一概念'), '检测候选');
  assert(writes()[0].url === '/api/wiki/resolve' && writes()[0].body.sourceIds.join(',') === 's1,s2', '仅检测显式选中的两篇资料');
  passed('打开仅只读；最多选择十篇；显式检测仅提交所选资料');

  await input('统一名称：proposal-1', '人工统一名称'); await input('判断备注：proposal-1', '保留两份贡献的人工确认');
  snapshot += 1;
  await until(() => text().includes('知识范围已更新'), '后台轮询发现新快照');
  assert(button('确认归并').disabled, '轮询不能悄悄替换人工核对过的快照');
  await click('刷新候选后重新核对'); await until(() => button('确认归并') && !button('确认归并').disabled, '手工接受轮询后的快照');
  assert(container().querySelector('[aria-label="判断备注：proposal-1"]').value.includes('人工确认'), '后台快照变化丢失备注');
  passed('后台轮询更新任务进度，快照变化后保留人工草稿并要求明确复核');
  await click('确认归并'); await until(() => text().includes('知识快照已更新'), '409反馈');
  assert(container().querySelector('[aria-label="统一名称：proposal-1"]').value === '人工统一名称', '409丢失人工名称');
  assert(container().querySelector('[aria-label="判断备注：proposal-1"]').value.includes('人工确认'), '409丢失判断备注');
  const count = writes().length; await delay(100); assert(writes().length === count && button('确认归并').disabled, '409后不得自动重试或用旧快照再提交');
  await click('刷新候选后重新核对'); await until(() => button('确认归并') && !button('确认归并').disabled, '刷新候选');
  await click('确认归并'); await until(() => text().includes('近期决定'), '归并成功');
  assert(writes().filter(item => item.url.endsWith('/merge')).at(-1).body.expectedSnapshotId === 'snapshot-3', '重核后必须发送新快照');
  assert(!container().querySelector('.gw-resolution-proposal'), '已确认候选不得继续显示为可提交候选');
  passed('并发冲突保留选择和人工草稿，刷新复核后才允许再次手动提交');

  const decision = container().querySelector('.gw-resolution-decision'); assert(decision?.textContent.includes('人工统一名称'), '缺少已保存决定');
  await click('撤销'); await until(() => !container().querySelector('.gw-resolution-decision button'), '撤销归并');
  assert(writes().at(-1).url.endsWith('/undo') && writes().at(-1).body.decisionId === 'decision-merge', '撤销必须固定决定与快照');
  await click('检测所选资料'); await until(() => container().querySelector('.gw-resolution-proposal'), '第二轮候选');
  await click('仅关联'); await until(() => text().includes('related'), '仅关联保存');
  assert(writes().at(-1).body.judgment === 'related', '相关概念不能误发实体归并');
  passed('归并可撤销；仅关联保存独立判断，未自动执行其他候选');

  await click('整理关联');
  const separator = container().querySelector('[role="separator"]'); separator.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true })); await delay(30);
  const width = separator.getAttribute('aria-valuenow'); assert(Number(width) > 360, '键盘调整详情宽度失效');
  const camera = container().querySelector('.gw-graph-svg>g').getAttribute('transform');
  for (const theme of ['glass', 'civic', 'jade']) { document.documentElement.dataset.theme = theme; await delay(35); assert(separator.getAttribute('aria-valuenow') === width, `${theme}主题丢失详情宽度`); assert(container().querySelector('.gw-graph-svg>g').getAttribute('transform') === camera, `${theme}主题改变视角`); }
  passed('三主题保留详情宽度和图谱视角；原有键盘尺寸调整继续工作');

  const final = { status: 'passed', cases: results.length, results, reads: requests.filter(item => item.method === 'GET').length, writes: writes().length, realApiRequests: 0, modelCalls: 0 };
  resultNode.textContent = JSON.stringify(final, null, 2); window.__wikiUnifiedHarness = { ...final, requests, opened };
}

// run() deliberately stops immediately after reaching the source page; keeping
// all actual network traffic behind the override makes this safe for previews.
run().then(finish).catch(error => { const result = { status: 'failed', results, error: error.stack || String(error), realApiRequests: 0, modelCalls: 0 }; resultNode.textContent = JSON.stringify(result, null, 2); window.__wikiUnifiedHarness = { ...result, requests, opened }; });
