import React from 'react';
import { createRoot } from 'react-dom/client';
import { PromptWorkspace } from '../src/PromptWorkspace';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/workspace-panels.css';
import '../src/theme-layout.css';

const theme = new URLSearchParams(location.search).get('theme');
if (['jade', 'glass', 'civic'].includes(theme)) document.documentElement.dataset.theme = theme;
const resultNode = document.getElementById('prompt-library-result');
const root = createRoot(document.getElementById('prompt-library-root'));
const results = [], requests = [];
const clone = value => structuredClone(value);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
let epoch = 1, visible = false, failure = null, releaseSave = null;
const categories = [{ id: 'knowledge', name: '知识整理' }, { id: 'writing', name: '文稿写作' }, { id: 'styles', name: '文风' }];
const base = { description: '受控验收样本', stage: '导入后加工', usage: '验证智能指令编辑', inputDescription: '正文块与来源版本', outputDescription: '摘要结构', readOnlyContract: '由程序核验来源与输出格式。', updatedAt: null };
const officialBody = '政府公文正文要求（仅用于正文生成或选区修改，不用于拟题或纲要规划）：\n' + Array.from({ length: 8 }, (_, index) => `${index + 1}. 隔离模拟公文规则：保持大纲标题、层级及顺序，判断有据，材料来源、口径和时点完整；具体措施不能补造责任主体或期限，资料缺口记入作者可见issues，正文通常100—250字一段，超过300字拆段。`).join('\n');
const styleItems = [
  { ...base, id: 'writing_style_official', title: '政府公文·通用', category: 'styles', stage: '拟题、纲要、正文与选区修改', content: '公文表达庄重、准确、简练、务实，层次清晰、判断有据。不得虚构机关、文号或政策依据。', version: 1 },
  { ...base, id: 'writing_style_official_body', title: '政府公文·正文', category: 'styles', stage: '正文生成与选区修改', usage: '只在政府公文正文和选区修改中生效，不用于拟题或纲要规划。', readOnlyContract: '文风只规范表达，服从实际模式、固定资料、引用与事实边界；保存后仅用于新任务，旧任务与检查点沿用原版本。', content: officialBody, version: 1 },
  { ...base, id: 'writing_style_technical', title: '技术论述', category: 'styles', stage: '拟题、纲要、正文与选区修改', content: '术语准确，说明适用条件、技术边界和权衡，不编造实验或性能结论。', version: 1 },
  { ...base, id: 'writing_style_news', title: '新闻报道', category: 'styles', stage: '拟题、纲要、正文与选区修改', content: '采用客观、准确、简洁的新闻报道语言，清楚归属消息来源，不虚构采访或现场。', version: 1 },
];
let remote = [
  { ...base, id: 'summary', title: '分段摘要', category: 'knowledge', content: '原始摘要提示词 {{summaryBudget}}。保留原文事实。\n\n输出 {"items":[{"text":"句号。留在字符串内。"}]}。', version: 1 },
  { ...base, id: 'translation', title: '正文翻译', category: 'knowledge', content: '原始翻译提示词', version: 1 },
  { ...base, id: 'writing_outline_strict', title: '写作纲要（严格模式）', category: 'writing', content: '原始严格纲要提示词', version: 1 },
  { ...base, id: 'writing_outline_extended', title: '写作纲要（扩展模式）', category: 'writing', content: '原始扩展纲要提示词', version: 1 },
  { ...base, id: 'writing_section_strict', title: '章节正文（严格模式）', category: 'writing', content: '原始严格正文提示词', version: 1 },
  { ...base, id: 'writing_section_extended', title: '章节正文（扩展模式）', category: 'writing', content: '原始扩展正文提示词', version: 1 },
  { ...base, id: 'writing_document_strict', title: '一次成文（严格模式）', category: 'writing', content: '原始严格整稿提示词', version: 1 },
  { ...base, id: 'writing_document_extended', title: '一次成文（扩展模式）', category: 'writing', content: '原始扩展整稿提示词', version: 1 },
  { ...base, id: 'writing_citation_match', title: '逐句引用复核', category: 'writing', content: '原始引用复核提示词', version: 1 },
  ...styleItems,
];

// Fail closed for the entire lifetime of this test page, including after tests
// finish. No unknown request can ever fall through to the actual API.
window.fetch = async (input, options = {}) => {
  const url = typeof input === 'string' ? input : input.url;
  const method = options.method || 'GET';
  requests.push({ url, method, body: options.body ? JSON.parse(options.body) : null });
  if (failure?.method === method) { const pending = failure; failure = null; return response({ detail: pending.message }, pending.status); }
  if (url === '/api/prompts' && method === 'GET') return response({ items: clone(remote), categories });
  if (url.startsWith('/api/prompts/') && method === 'PUT') {
    const id = decodeURIComponent(url.split('/').at(-1));
    const item = remote.find(entry => entry.id === id);
    if (!item) return response({ detail: '找不到指令' }, 404);
    const body = JSON.parse(options.body);
    if (body.expectedVersion !== item.version) return response({ detail: '服务端指令已更新' }, 409);
    if (releaseSave === 'pending') await new Promise(resolve => { releaseSave = resolve; });
    Object.assign(item, { content: body.content, version: item.version + 1, updatedAt: '2026-10-23T00:00:00Z' });
    return response(clone(item));
  }
  throw new Error(`禁止访问真实服务：${method} ${url}`);
};

function mount() {
  root.render(<div className="workspace-shell"><div hidden={!visible}><PromptWorkspace key={epoch} active={visible}/></div></div>);
}
const container = () => document.querySelector('.runtime-prompt-library');
const text = () => container()?.textContent || '';
const textarea = () => container()?.querySelector('textarea[aria-label="指令正文"]');
function button(label, exact = true) {
  return [...(container()?.querySelectorAll('button') || [])].find(node => exact ? node.textContent.trim() === label : node.textContent.includes(label));
}
async function click(label, exact = true) {
  const target = button(label, exact);
  assert(target && !target.disabled, `找不到可用按钮：${label}`);
  target.click(); await delay(40);
}
async function type(value) {
  const element = textarea();
  assert(element && !element.disabled, '编辑器应处于可输入状态');
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(element, value);
  element.dispatchEvent(new Event('input', { bubbles: true }));
  await delay(40);
}
async function until(predicate, label) {
  const deadline = performance.now() + 5000;
  while (!predicate()) { if (performance.now() > deadline) throw new Error(`超时：${label}`); await delay(20); }
}
function passed(name) {
  results.push({ name, status: 'passed' });
  resultNode.textContent = JSON.stringify({ status: 'running', results }, null, 2);
}
const writes = () => requests.filter(request => request.method !== 'GET');
const chooseCategory = async label => { const node = [...container().querySelectorAll('.prompt-category-tabs button')].find(entry => entry.textContent.startsWith(label)); assert(node, `分类不存在：${label}`); node.click(); await delay(40); };

async function run() {
  mount(); await until(() => container(), '隐藏组件挂载'); await delay(80);
  assert(requests.length === 0, '隐藏页不应提前请求提示词');
  visible = true; mount(); await until(() => text().includes('原始摘要提示词'), '首次加载');
  assert(requests.length === 1 && requests[0].method === 'GET', '进入页面只应读取目录');
  assert(!text().includes('新建指令') && !text().includes('用于写作') && !container().querySelector('input'), '不应显示新建、搜索或用于写作');
  assert(text().includes('流程步骤') && text().includes('输入') && text().includes('输出'), '缺少流程说明');
  const groupNav = container().querySelector('aside.prompt-list .prompt-category-tabs');
  assert(groupNav && container().querySelectorAll('.prompt-category-tabs').length === 1, '分类应只显示在右侧指令选择栏内部');
  const groupButtons = [...groupNav.querySelectorAll('button')];
  assert(groupButtons.length === categories.length && !groupButtons.some(node => node.textContent.trim().startsWith('全部')), '分类只能显示真实分组，不能包含全部');
  assert(groupNav.querySelector('button[aria-pressed="true"]')?.textContent.startsWith('知识整理'), '初次进入应选中第一个真实分组');
  const initialTitles = [...container().querySelectorAll('.prompt-list .prompt-card strong')].map(node => node.textContent);
  assert(initialTitles.length === 2 && initialTitles[0] === '分段摘要' && initialTitles[1] === '正文翻译', '默认知识整理分组应仅呈现该组两条指令');
  passed('隐藏页延迟加载；分类位于右栏，无全部，默认仅显示知识整理两条指令');

  const initialSource = remote[0].content;
  const readable = container().querySelector('.prompt-content').textContent;
  assert(readable.includes('\n') && readable.includes('句号。留在字符串内。'), '阅读态应分段并保护JSON字符串');
  assert(readable.includes('原始摘要提示词 {{summaryBudget}}。保留原文事实。\n\n'), '同一语义段的相关句子应连写，不得逐句换行');
  await click('编辑正文');
  assert(textarea().value === readable && button('保存修改').disabled, '初次编辑应使用干净的可读基线');
  assert(!container().querySelector('.prompt-draft-notice'), '自动分段不能变成未保存修改');
  const cleanClose = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(cleanClose);
  assert(!cleanClose.defaultPrevented, '未修改的分段编辑不应阻止离页');
  await click('取消编辑');
  assert(!textarea() && !text().includes('放弃此条指令') && writes().length === 0 && remote[0].content === initialSource, '仅查看或进入编辑不能保存、改版本或弹出放弃确认');
  passed('阅读和编辑自动分段；JSON字符串完整；未修改时不产生草稿提醒、不保存');

  await click('编辑正文'); await type('人工摘要修改 {{summaryBudget}}');
  await chooseCategory('文稿写作');
  assert(container().querySelectorAll('.prompt-list .prompt-card').length === 7 && !container().querySelector('.prompt-list').textContent.includes('分段摘要'), '切到写作分组后只能显示该组七条指令');
  await click('编辑正文'); await type('人工写作修改');
  await chooseCategory('知识整理');
  assert(container().querySelectorAll('.prompt-list .prompt-card').length === 2 && !container().querySelector('.prompt-list').textContent.includes('写作纲要（严格模式）'), '切回知识整理后应恢复该组两条指令');
  assert(textarea().value === '人工摘要修改 {{summaryBudget}}', '切分类丢失摘要草稿');
  assert(text().includes('有 2 条未保存修改'), '跨分类草稿计数错误');
  await click('正文翻译', false); assert(!textarea(), '另一条目不应显示摘要草稿');
  await click('分段摘要', false); assert(textarea().value === '人工摘要修改 {{summaryBudget}}', '切条目丢稿');
  visible = false; mount(); await delay(50); visible = true; mount(); await delay(50);
  assert(textarea().value === '人工摘要修改 {{summaryBudget}}' && requests.length === 1, '切页面丢稿或重复读取');
  assert(writes().length === 0, '编辑或切换不能自动保存');
  const closeEvent = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(closeEvent);
  assert(closeEvent.defaultPrevented, '有未保存修改时缺少关闭保护');
  passed('切分类、条目和页面保留多个草稿；不自动保存；关闭前保护');

  releaseSave = 'pending'; await click('保存修改');
  await until(() => typeof releaseSave === 'function', '保存请求被模拟挂起');
  assert(textarea().disabled, '保存中必须保护当前提交内容');
  const firstWrite = writes()[0];
  assert(firstWrite.url === '/api/prompts/summary' && firstWrite.body.expectedVersion === 1 && firstWrite.body.content === '人工摘要修改 {{summaryBudget}}', '保存请求契约错误');
  releaseSave(); releaseSave = null;
  await until(() => !textarea() && text().includes('v2'), '保存完成');
  await chooseCategory('文稿写作');
  assert(textarea().value === '人工写作修改', '保存其他条目不能丢弃写作草稿');
  await click('取消编辑'); assert(text().includes('放弃此条指令'), '取消修改需明确确认');
  await click('继续编辑'); assert(textarea().value === '人工写作修改', '继续编辑丢稿');
  await click('取消编辑'); await click('放弃修改');
  assert(!textarea() && remote.find(entry => entry.id === 'writing_outline_strict').version === 1, '放弃修改不能保存');
  passed('显式保存发送内容与版本；保存中禁用编辑；其他草稿保留；取消需确认');

  epoch += 1; mount(); await until(() => text().includes('人工摘要修改') && !textarea(), '模拟刷新重新挂载');
  assert(text().includes('v2'), '重新加载没有读取保存内容');
  passed('模拟刷新重新挂载从服务读取已保存提示词与新版本');

  await click('编辑正文'); await type('冲突中的人工修改 {{summaryBudget}}');
  remote[0].version = 3; remote[0].content = '另一页面已经保存的内容 {{summaryBudget}}';
  await click('保存修改'); await until(() => text().includes('请对照最新版本'), '409冲突');
  assert(textarea().value.startsWith('冲突中的人工修改') && button('保存修改').disabled, '409应保留草稿并阻止盲目覆盖');
  const beforeReconcileWrites = writes().length;
  await click('加载最新版本');
  assert(text().includes('另一页面已经保存的内容') && textarea().value.startsWith('冲突中的人工修改'), '加载最新版本覆盖了草稿或缺少对照');
  await click('保留我的内容，基于 v3 继续编辑');
  assert(writes().length === beforeReconcileWrites && remote[0].version === 3, '确认继续编辑不能自动保存');
  await click('保存修改'); await until(() => !textarea() && remote[0].version === 4, '冲突后显式保存');
  assert(writes().at(-1).body.expectedVersion === 3 && remote[0].content.startsWith('冲突中的人工修改'), '冲突后没有使用新基线版本');
  passed('409保留草稿；加载服务版本供对照；显式接受新基线后手动保存');

  await click('编辑正文'); await type('重试前保留的内容 {{summaryBudget}}');
  failure = { method: 'PUT', status: 503, message: '模拟服务暂不可用' };
  await click('保存修改'); await until(() => text().includes('模拟服务暂不可用'), '保存失败反馈');
  assert(textarea().value.startsWith('重试前保留的内容'), '保存失败丢失草稿');
  const writeCount = writes().length; await delay(100);
  assert(writes().length === writeCount, '失败后不应自动重试');
  await click('保存修改'); await until(() => !textarea() && remote[0].version === 5, '手动重试成功');
  passed('保存失败保留草稿，显示原始错误，仅在再次点击时重试');

  failure = { method: 'GET', status: 503, message: '模拟目录不可用' }; epoch += 1; mount();
  await until(() => text().includes('模拟目录不可用'), '目录加载失败');
  assert(!text().includes('原始摘要提示词'), '失败后不得填入示例提示词');
  await click('重新加载'); await until(() => text().includes('重试前保留的内容'), '重新读取目录');
  passed('目录加载失败显示重试，不回退到示例内容');

  await chooseCategory('文稿写作');
  assert(container().querySelectorAll('.prompt-list .prompt-card').length === 7, '写作分类应展示7条独立模式与引用复核提示词');
  assert(text().includes('写作纲要（严格模式）') && text().includes('写作纲要（扩展模式）') && text().includes('一次成文（严格模式）') && text().includes('一次成文（扩展模式）') && !text().includes('一次成文要求'), '写作分类缺少独立整稿模式提示词或仍展示旧通用条目');
  await click('章节正文（严格模式）', false); await click('编辑正文'); await type('严格模式独立人工修改');
  await click('章节正文（扩展模式）', false); await click('编辑正文'); await type('扩展模式独立人工修改');
  await chooseCategory('知识整理'); await chooseCategory('文稿写作');
  await click('章节正文（严格模式）', false);
  assert(textarea().value === '严格模式独立人工修改', '分类切换不能混用或丢失模式草稿');
  await click('保存修改'); await until(() => !textarea(), '严格模式提示词保存');
  const strictPrompt = remote.find(entry => entry.id === 'writing_section_strict');
  const extendedPrompt = remote.find(entry => entry.id === 'writing_section_extended');
  assert(strictPrompt.version === 2 && strictPrompt.content === '严格模式独立人工修改' && extendedPrompt.version === 1 && extendedPrompt.content === '原始扩展正文提示词', '保存严格模式不能改变扩展模式的版本或正文');
  await click('章节正文（扩展模式）', false);
  assert(textarea().value === '扩展模式独立人工修改', '保存严格模式不能丢弃扩展模式草稿');
  await click('保存修改'); await until(() => !textarea(), '扩展模式提示词保存');
  assert(extendedPrompt.version === 2 && extendedPrompt.content === '扩展模式独立人工修改' && strictPrompt.version === 2 && strictPrompt.content === '严格模式独立人工修改', '保存扩展模式不能改变严格模式的版本或正文');
  const modeWrites = writes().slice(-2);
  assert(modeWrites[0].url === '/api/prompts/writing_section_strict' && modeWrites[1].url === '/api/prompts/writing_section_extended' && modeWrites.every(request => request.body.expectedVersion === 1), '两种模式必须按独立条目与版本保存');
  epoch += 1; mount(); await until(() => text().includes('重试前保留的内容'), '模式保存后重新挂载');
  await chooseCategory('文稿写作'); await click('章节正文（严格模式）', false);
  assert(!textarea() && text().includes('严格模式独立人工修改') && text().includes('智能指令 · v2'), '重载后未读取独立的严格模式保存内容');
  await click('章节正文（扩展模式）', false);
  assert(!textarea() && text().includes('扩展模式独立人工修改') && text().includes('智能指令 · v2'), '重载后未读取独立的扩展模式保存内容');
  passed('写作分类7条；严格与扩展正文分别保留草稿、独立保存并在重新加载后恢复');

  await click('一次成文（严格模式）', false); await click('编辑正文'); await type('一次成文严格独立人工修改');
  await click('一次成文（扩展模式）', false); await click('编辑正文'); await type('一次成文扩展独立人工修改');
  await chooseCategory('知识整理'); await chooseCategory('文稿写作');
  await click('一次成文（严格模式）', false);
  assert(textarea().value === '一次成文严格独立人工修改', '一次成文严格草稿被扩展草稿覆盖或分类切换丢失');
  const strictDocument = remote.find(entry => entry.id === 'writing_document_strict');
  const extendedDocument = remote.find(entry => entry.id === 'writing_document_extended');
  await click('保存修改'); await until(() => !textarea(), '一次成文严格保存');
  assert(strictDocument.version === 2 && strictDocument.content === '一次成文严格独立人工修改' && extendedDocument.version === 1 && extendedDocument.content === '原始扩展整稿提示词', '保存一次成文严格指令改变了扩展指令');
  await click('一次成文（扩展模式）', false);
  assert(textarea().value === '一次成文扩展独立人工修改', '保存一次成文严格指令丢弃了扩展草稿');
  await click('保存修改'); await until(() => !textarea(), '一次成文扩展保存');
  assert(extendedDocument.version === 2 && extendedDocument.content === '一次成文扩展独立人工修改' && strictDocument.version === 2 && strictDocument.content === '一次成文严格独立人工修改', '保存一次成文扩展指令改变了严格指令');
  const documentWrites = writes().slice(-2);
  assert(documentWrites[0].url === '/api/prompts/writing_document_strict' && documentWrites[1].url === '/api/prompts/writing_document_extended' && documentWrites.every(request => request.body.expectedVersion === 1), '一次成文两模式未使用各自ID或版本');
  epoch += 1; mount(); await until(() => text().includes('重试前保留的内容'), '整稿提示词保存后重新挂载');
  await chooseCategory('文稿写作'); await click('一次成文（严格模式）', false);
  assert(!textarea() && text().includes('一次成文严格独立人工修改') && text().includes('智能指令 · v2'), '一次成文严格重载未恢复独立保存内容');
  await click('一次成文（扩展模式）', false);
  assert(!textarea() && text().includes('一次成文扩展独立人工修改') && text().includes('智能指令 · v2'), '一次成文扩展重载未恢复独立保存内容');
  passed('一次成文两模式独立草稿、分类切换、独立保存、CAS版本及重载恢复');

  const beforeStyleWrites = writes().length;
  await chooseCategory('文风');
  const styleTitles = [...container().querySelectorAll('.prompt-list .prompt-card strong')].map(node => node.textContent);
  assert(JSON.stringify(styleTitles) === JSON.stringify(styleItems.map(entry => entry.title)), '文风分类应独立展示四条指令，并保持服务端顺序');
  await click('政府公文·正文', false);
  assert(container().querySelector('.prompt-content').textContent === officialBody && text().includes('不用于拟题或纲要规划'), '长公文正文规则必须完整展示，并注明仅用于正文和选区');
  assert(writes().length === beforeStyleWrites && styleItems.every(entry => entry.version === 1), '浏览文风分类不能保存配置或升级版本');
  await click('编辑正文');
  assert(textarea().value === officialBody && button('保存修改').disabled && !text().includes('{{变量}}'), '无变量文风编辑应保留完整正文，不显示必需变量说明或误判修改');
  await click('取消编辑');
  passed('文风独立分类四条；长公文正文与用途完整展示；只读浏览和无修改编辑不写入');

  await click('政府公文·通用', false); await click('编辑正文'); await type('通用公文独立修改：庄重、准确、务实。');
  await click('政府公文·正文', false); await click('编辑正文'); await type(officialBody + '\n新增正文表达要求。');
  await chooseCategory('文稿写作');
  assert(container().querySelectorAll('.prompt-card').length === 7 && !container().querySelector('.prompt-list').textContent.includes('政府公文'), '新增文风不能混入或改变原文稿写作分组');
  await chooseCategory('文风');
  assert(textarea().value === '通用公文独立修改：庄重、准确、务实。' && text().includes('有 2 条未保存修改'), '切换分类必须保留两条独立文风草稿');
  await click('保存修改'); await until(() => !textarea(), '公文通用指令保存');
  assert(writes().at(-1).url === '/api/prompts/writing_style_official' && writes().at(-1).body.expectedVersion === 1, '通用公文必须按独立ID和版本保存');
  assert(styleItems[0].version === 2 && styleItems[1].version === 1 && styleItems[2].version === 1 && styleItems[3].version === 1, '保存通用文风不能改变正文、技术或新闻条目');
  await click('政府公文·正文', false);
  assert(textarea().value === officialBody + '\n新增正文表达要求。', '通用文风保存不能丢弃公文正文草稿');
  passed('通用与正文独立草稿；跨分类保持；独立CAS保存不改其他文风或原写作分组');

  styleItems[1].version = 2; styleItems[1].content = officialBody + '\n其他页面保存的正文要求。';
  await click('保存修改'); await until(() => text().includes('请对照最新版本'), '公文正文版本冲突');
  assert(textarea().value.endsWith('新增正文表达要求。') && button('保存修改').disabled, '文风冲突必须保留本地草稿并阻止覆盖');
  await click('加载最新版本');
  assert(text().includes('其他页面保存的正文要求。') && textarea().value.endsWith('新增正文表达要求。'), '加载最新文风需要展示对照并保留草稿');
  await click('保留我的内容，基于 v2 继续编辑');
  await click('保存修改'); await until(() => !textarea(), '公文正文冲突后保存');
  assert(styleItems[1].version === 3 && writes().at(-1).url === '/api/prompts/writing_style_official_body' && writes().at(-1).body.expectedVersion === 2, '公文正文冲突处理必须使用当前版本且仅更新自身');
  passed('公文正文409保留草稿与最新版本对照；显式采用新基线后保存');

  await click('技术论述', false); await click('编辑正文'); await type('技术论述独立修改：统一术语并说明适用条件。');
  await click('保存修改'); await until(() => !textarea(), '技术文风保存');
  await click('新闻报道', false); await click('编辑正文'); await type('新闻报道独立修改：客观简洁，保留来源归属。');
  failure = { method: 'PUT', status: 503, message: '模拟文风保存失败' };
  await click('保存修改'); await until(() => text().includes('模拟文风保存失败'), '文风保存失败');
  assert(textarea().value === '新闻报道独立修改：客观简洁，保留来源归属。' && styleItems[3].version === 1, '文风保存失败应保留草稿和原保存版本');
  const styleFailureWrites = writes().length; await delay(100);
  assert(writes().length === styleFailureWrites, '文风保存失败不能自动重试');
  await click('保存修改'); await until(() => !textarea(), '新闻文风显式重试');
  epoch += 1; mount(); await until(() => text().includes('重试前保留的内容'), '文风保存后重新挂载');
  await chooseCategory('文风');
  for (const expected of styleItems) {
    await click(expected.title, false);
    assert(!textarea() && container().querySelector('.prompt-content').textContent === expected.content && text().includes(`智能指令 · v${expected.version}`), `重载未恢复文风独立内容和版本：${expected.id}`);
  }
  passed('四条文风独立保存与重载；失败保留草稿且仅显式重试');

  const allowedUrls = new Set(['/api/prompts', '/api/prompts/summary', '/api/prompts/writing_section_strict', '/api/prompts/writing_section_extended', '/api/prompts/writing_document_strict', '/api/prompts/writing_document_extended', ...styleItems.map(entry => `/api/prompts/${entry.id}`)]);
  assert(requests.every(request => allowedUrls.has(request.url)), '测试调用了预期提示词以外接口');
  resultNode.textContent = JSON.stringify({ status: 'passed', cases: results.length, results, reads: requests.filter(request => request.method === 'GET').length, writes: writes().length, realApiRequests: 0, modelCalls: 0 }, null, 2);
}

run().catch(error => { resultNode.textContent = JSON.stringify({ status: 'failed', results, error: error.stack || String(error), realApiRequests: 0, modelCalls: 0 }, null, 2); });
