import React from 'react';
import { createRoot } from 'react-dom/client';
import { TemplateWorkspace } from '../src/Knowledge';
import { PromptWorkspace } from '../src/PromptWorkspace';
import { structureTemplates } from '../src/writing-presets';
import { publishPromptDraftTransfer } from '../src/prompt-draft-transfer';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/workspace-panels.css';
import '../src/theme-layout.css';

const clone = value => JSON.parse(JSON.stringify(value));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (value, message) => { if (!value) throw new Error(message); };
const response = (value, status = 200) => Promise.resolve({ ok: status < 400, status, json: async () => clone(value) });
const requests = [], results = [];
const base = { stage: '模板写作', description: '模板实际论述方向', usage: '保存后用于新任务', inputDescription: '当前结构及写作要求', outputDescription: '合入实际任务', readOnlyContract: '固定程序协议无需在本页显示。', version: 1, updatedAt: null };
const remote = structureTemplates.map(template => ({ ...base, id: `writing_structure_${template.id}`, title: template.name, category: 'structures', content: `${template.name}的实际模板提示词。\n\n保留作者明确结构，按实际内容展开。`, displayContent: `${template.name}的实际模板提示词。\n\n保留作者明确结构，按实际内容展开。`, editableContent: `${template.name}的实际模板提示词。\n\n保留作者明确结构，按实际内容展开。`, contentView: 'source', supplementalRules: [{ id: 'history', title: '历史任务兼容', content: '不应显示的旧提示词说明。', scope: 'legacy' }] }));
remote.push({ ...base, id: 'writing_section_strict', title: '章节正文', category: 'writing', content: '不应优先显示的旧原文。', displayContent: '当前实际章节指令。', editableContent: '当前实际章节指令。', effectiveContent: '当前实际章节指令。', contentView: 'effective' });
let nav = 'templates', epoch = 0, failGet = false;
const root = createRoot(document.getElementById('template-prompt-root'));
const report = document.getElementById('template-prompt-result');
const selected = id => remote.find(item => item.id === `writing_structure_${id}`);
const putRequests = () => requests.filter(item => item.method === 'PUT');
let templateUses = 0;

// Fail closed throughout this page: no request reaches a real API or model.
window.fetch = async (input, options = {}) => {
  const url = typeof input === 'string' ? input : input.url;
  const method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({ url, method, body });
  if (url === '/api/prompts' && method === 'GET') {
    if (failGet) { failGet = false; return response({ detail: '隔离读取失败' }, 503); }
    return response({ items: remote, categories: [{ id: 'structures', name: '结构模板' }, { id: 'writing', name: '文稿写作' }] });
  }
  if (url.startsWith('/api/prompts/') && method === 'PUT') {
    const item = remote.find(entry => entry.id === decodeURIComponent(url.split('/').at(-1)));
    if (!item) return response({ detail: '不存在的提示词' }, 404);
    if (body.expectedVersion !== item.version) return response({ detail: '隔离版本冲突' }, 409);
    Object.assign(item, { content: body.content, displayContent: body.content, editableContent: body.content, version: item.version + 1, updatedAt: '2026-10-28T00:00:00Z' });
    return response(item);
  }
  throw new Error(`禁止访问实际服务：${method} ${url}`);
};

function mount() {
  root.render(<div className="workspace-shell" style={{ display: 'block', height: '100vh' }}><main className={`app-main view-${nav}`} style={{ height: '100%' }}>
    <div className="workspace-view" hidden={nav !== 'templates'}><TemplateWorkspace key={epoch} active={nav === 'templates'} onUseTemplate={() => { templateUses += 1; }}/></div>
    <div className="workspace-view" hidden={nav !== 'prompts'}><PromptWorkspace active={nav === 'prompts'}/></div>
  </main></div>);
}
const workspace = () => document.querySelector('.template-workspace');
const editor = () => workspace()?.querySelector('textarea[aria-label="模板提示词正文"]');
const panel = () => workspace()?.querySelector('.template-prompt-editor');
function button(label, scope = workspace()) { return [...(scope?.querySelectorAll('button') || [])].find(node => node.textContent.trim() === label); }
async function click(label, scope) { const node = button(label, scope); assert(node && !node.disabled, `找不到可用按钮：${label}`); node.click(); await delay(45); }
async function choose(id) {
  const name = structureTemplates.find(item => item.id === id).name;
  const node = [...workspace().querySelectorAll('.template-item')].find(item => item.querySelector('strong')?.textContent === name);
  assert(node, `结构模板入口缺失：${id}`); node.click(); await delay(45);
}
async function type(value) { const node = editor(); assert(node && !node.disabled, '模板编辑器应可输入'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); await delay(45); }
async function until(predicate, name) { const end = performance.now() + 5000; while (!predicate()) { if (performance.now() > end) throw new Error(`超时：${name}`); await delay(20); } }
function passed(name) { results.push({ name, status: 'passed' }); }

async function run() {
  const initialTheme = new URLSearchParams(location.search).get('theme') || 'jade';
  document.documentElement.dataset.theme = initialTheme;
  mount(); await until(() => panel()?.querySelector('pre'), '读取模板提示词');
  assert(workspace().querySelectorAll('.template-item').length === 9, '九个现行结构必须都能访问');
  assert(![...workspace().querySelectorAll('.template-item strong')].some(node => node.textContent === '调研报告'), '旧调研报告模板不能恢复为新入口');
  assert(panel().textContent.includes('调研报告（决策参考型）的实际模板提示词。'), '模板详情应展示实际提示词');
  assert(!workspace().textContent.includes('历史任务兼容') && !workspace().textContent.includes('不应显示的旧提示词说明'), '模板详情不能显示旧原文或历史说明');
  assert(!workspace().textContent.includes('已排队任务继续使用原版本'), '模板详情不显示旧任务兼容说明');
  assert(putRequests().length === 0 && templateUses === 0, '浏览不应保存或使用模板');
  for (const id of ['explainer', 'custom']) { await choose(id); assert(panel()?.textContent.includes(selected(id).content), `${id}提示词应可达`); }
  passed('九个现行结构提示词可达、旧模板隐藏且浏览无写入');

  await choose('research-report-decision'); await click('编辑提示词');
  assert(button('保存提示词').disabled, '仅进入编辑不能标记为修改');
  const decisionDraft = editor().value + '\n决策模板未保存修改';
  await type(decisionDraft); await choose('research-report-problem');
  assert(!editor(), '其他模板应保留独立的编辑状态');
  await click('编辑提示词'); const problemDraft = editor().value + '\n问题模板未保存修改'; await type(problemDraft);
  nav = 'prompts'; mount(); await until(() => document.querySelector('.runtime-prompt-library .prompt-content'), '智能指令页读取');
  assert(!document.querySelector('.runtime-prompt-library').textContent.includes('结构模板'), '结构模板不在智能指令页展示');
  document.documentElement.dataset.theme = initialTheme === 'glass' ? 'jade' : 'glass';
  nav = 'templates'; mount(); await delay(70);
  assert(editor().value === problemDraft, '跨页面与主题切换应保留模板草稿');
  await choose('research-report-decision'); assert(editor().value === decisionDraft, '模板切换应保留各自草稿');
  assert(putRequests().length === 0, '切换模板、页面和主题不得隐式保存');
  passed('独立草稿跨模板、页面及主题保留');

  await click('取消编辑'); assert(panel().querySelector('[aria-label="放弃模板提示词修改"]'), '放弃脏草稿须明确确认');
  await click('继续编辑'); assert(editor().value === decisionDraft, '继续编辑应保留原草稿');
  await click('取消编辑'); await click('放弃修改'); assert(!editor(), '显式放弃后应退出编辑');
  await choose('research-report-problem'); assert(editor().value === problemDraft, '放弃一个模板不得清除另一个草稿');
  await click('保存提示词'); await until(() => !editor(), '保存模板提示词');
  assert(selected('research-report-problem').version === 2 && selected('research-report-problem').content === problemDraft, '显式保存应写入对应模板');
  const savedRequest = putRequests().at(-1);
  assert(savedRequest.body.expectedVersion === 1 && savedRequest.body.contentView === 'source', '保存应使用原ID及source/CAS协议');
  passed('取消确认、独立保存及原ID版本协议');

  await click('编辑提示词'); const localConflict = editor().value + '\n冲突时保留的本地修改'; await type(localConflict);
  Object.assign(selected('research-report-problem'), { content: '服务器更新的模板提示词', displayContent: '服务器更新的模板提示词', editableContent: '服务器更新的模板提示词', version: 3 });
  await click('保存提示词'); await until(() => panel().querySelector('[aria-label="模板提示词版本冲突"]'), '展示冲突');
  assert(editor().value === localConflict, '409必须保留本地草稿');
  await click('加载最新版本'); await until(() => panel().textContent.includes('保留我的内容，基于 v3 继续编辑'), '加载最新版本');
  assert(editor().value === localConflict && panel().textContent.includes('服务器更新的模板提示词'), '冲突须同时保留本地与最新内容');
  const writesBeforeRebase = putRequests().length;
  await click('保留我的内容，基于 v3 继续编辑');
  assert(putRequests().length === writesBeforeRebase, '继续编辑不能自动保存');
  await click('保存提示词'); await until(() => !editor(), '显式保存冲突后的修改');
  assert(selected('research-report-problem').version === 4 && putRequests().at(-1).body.expectedVersion === 3, '冲突处理应重新显式CAS保存');
  passed('409对照、保留本地内容和显式重新保存');

  await click('输出模板'); assert(!panel(), '输出模板不应显示结构提示词编辑器');
  await click('结构模板'); await choose('research-report-decision');
  document.documentElement.dataset.theme = initialTheme;
  assert(templateUses === 0 && requests.every(item => item.url === '/api/prompts' || item.url.startsWith('/api/prompts/')), '验收仅允许目录GET和显式提示词PUT');
  passed('输出模板及原应用模板行为保持');

  const writesBeforeTransfers = putRequests().length;
  const transferFor = (id, content) => ({ id: `writing_structure_${id}`, draft: {
    id: `writing_structure_${id}`, content, originalContent: selected(id).editableContent,
    version: selected(id).version, contentView: 'source',
  }, conflict: null, error: '' });
  let firstAcknowledged = false;
  const transferredText = '改版前智能页中尚未保存的知识解读提示词。';
  publishPromptDraftTransfer(transferFor('explainer', transferredText), () => { firstAcknowledged = true; });
  await until(() => firstAcknowledged, '成功采用后才确认源草稿可清理');
  await choose('explainer'); await until(() => editor()?.value === transferredText, '移交草稿在对应模板中可达');
  assert(editor().value === transferredText && firstAcknowledged, '移交必须保留同ID的编辑内容与source协议');
  await click('取消编辑'); await click('放弃修改');

  await choose('custom'); await click('编辑提示词');
  const targetDraft = editor().value + '\n模板页已有人工草稿'; await type(targetDraft);
  let secondAcknowledged = false;
  const waitingText = '等待移交的旧智能页自定结构草稿。';
  publishPromptDraftTransfer(transferFor('custom', waitingText), () => { secondAcknowledged = true; });
  await delay(70);
  assert(editor().value === targetDraft && !secondAcknowledged, '不能覆盖目标已有人工草稿或提前确认清理源草稿');
  await click('取消编辑'); await click('放弃修改');
  await until(() => secondAcknowledged && editor()?.value === waitingText, '目标明确处理后采用待移交草稿');
  assert(putRequests().length === writesBeforeTransfers, '草稿移交不得触发保存或创建任务');
  await click('取消编辑'); await click('放弃修改');
  await choose('research-report-decision');
  passed('旧结构草稿移交后可达、成功才确认且不覆盖目标人工草稿');
  window.__templatePromptQA = { requests, results, choose, click, type, mount, setNav: value => { nav = value; mount(); }, failGet: () => { failGet = true; epoch += 1; mount(); } };
  report.textContent = JSON.stringify({ status: 'passed', results, realApiRequests: 0, modelCalls: 0, writes: putRequests().length }, null, 2);
}

run().catch(error => { report.textContent = JSON.stringify({ status: 'failed', error: error.message, results, realApiRequests: 0, modelCalls: 0 }, null, 2); console.error(error); });
