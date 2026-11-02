import React from 'react';
import { createRoot } from 'react-dom/client';
import WritingMaterials from '../src/WritingMaterials';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/writing.css';
import '../src/settings-panel.css';

const previewOptions = new URLSearchParams(window.location.search);
document.documentElement.dataset.theme = ['jade', 'glass', 'civic'].includes(previewOptions.get('theme')) ? previewOptions.get('theme') : 'jade';
const output = document.getElementById('materials-test-result'), root = createRoot(document.getElementById('materials-test-root'));
const results = [], requests = [], settingsWrites = [];
const defaultSettings = { resultLimit: 40, passagesPerSource: 3, candidateLimit: 200, queryExpansion: true, channels: { keyword: true, vector: false, category: true, summary: true, claims: true, facts: true, graph: true }, embeddingModelId: null, graphHops: 1, minimumScore: 0 };
let storedSettings = structuredClone(defaultSettings);
let handler, confirmed = null;
const base = { id: null, version: 0, topic: '人工智能治理与产业实践', purpose: '比较治理框架、产业效益和实践边界', sourceIds: ['own'], sources: [], materialSelection: { recommendationId: null, passages: [] } };
const docs = [{ id: 'own', title: '自己选中的文章' }];
const reply = (items = 3) => ({ id: 'rec-fixture', queryOrigin: 'model', queries: ['人工智能治理', '产业实践 边界'], items: Array.from({ length: items }, (_, index) => ({ sourceId: `rec-${index}`, revisionId: `rev-${index}`, title: `推荐文章 ${index + 1}`, summary: '文章摘要，帮助评估材料的相关性。', channels: ['keyword', 'summary', 'graph'], reasons: ['关键词和图谱关联共同命中'], passages: [{ blockId: `block-${index}`, quote: '这是一段可精确定位的推荐原文，保留原始语境与事实限定。' }] })), channels: [{ id: 'vector', status: 'unavailable', detail: '尚未配置向量模型' }], warnings: [] });
window.fetch = async (url, options = {}) => {
  if (url === '/api/retrieval/settings') {
    const method = options.method || 'GET';
    if (method === 'PUT') { storedSettings = JSON.parse(options.body); settingsWrites.push(structuredClone(storedSettings)); }
    else if (method !== 'GET') throw new Error(`Unexpected fixture settings method ${method}`);
    return new Response(JSON.stringify({ settings: structuredClone(storedSettings) }), { status: 200 });
  }
  if (url !== '/api/retrieval/recommendations') throw new Error(`Unexpected fixture request ${url}`);
  const body = JSON.parse(options.body); requests.push(body);
  // Unknown requests never reach the real service or any model.
  return new Response(JSON.stringify(await handler(body)), { status: 200 });
};
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
async function until(predicate, label) { for (let attempt = 0; attempt < 150; attempt += 1) { if (predicate()) return; await wait(20); } throw new Error(`Timed out: ${label}`); }
function render(project = base, key = 'first') { root.render(<React.StrictMode><section className="writing-dialog writing-materials-dialog" data-materials-test-case={key} style={{ position: 'fixed', inset: '70px 28px 25px', width: 'auto' }}><WritingMaterials key={key} project={project} documents={docs} onConfirm={value => { confirmed = value; }} onClose={() => {}} onOpenDoc={() => {}}/></section></React.StrictMode>); }
const findButton = text => [...document.querySelectorAll('button')].find(button => button.textContent.includes(text));
const checkbox = label => document.querySelector(`input[aria-label="${label}"]`);
function passed(name) { results.push({ name, status: 'passed' }); output.textContent = JSON.stringify({ status: 'running', results }, null, 2); }
async function run() {
  handler = async () => reply(); render();
  await until(() => checkbox('纳入资料 推荐文章 1'), 'recommendations');
  assert(requests.length === 1, 'StrictMode issued duplicate recommendation/model requests');
  passed('StrictMode starts exactly one recommendation request');
  checkbox('纳入资料 推荐文章 1').click(); await wait(30);
  handler = async () => reply(4); findButton('按这些条件检索').click();
  await until(() => checkbox('纳入资料 推荐文章 4'), 'rerun');
  assert(!checkbox('纳入资料 推荐文章 1').checked, 'Rerun restored an excluded source');
  assert(requests[1].sourceIds.includes('own') && requests[1].sourceIds.includes('rec-1') && !requests[1].sourceIds.includes('rec-0'), 'Rerun did not capture current selection');
  passed('Rerun preserves exclusions and sends current chosen sources');
  findButton('确认材料').click(); await until(() => confirmed, 'confirmation');
  assert(confirmed.sourceIds.includes('own') && !confirmed.sourceIds.includes('rec-0'), 'Confirmation source scope mismatch');
  assert(!confirmed.materialSelection.passages.some(passage => passage.sourceId === 'own'), 'Original whole article narrowed automatically');
  assert(confirmed.materialSelection.passages.every(passage => passage.revisionId && passage.blockId), 'Missing frozen block identity');
  passed('Confirmation preserves whole sources and exact paragraph identities');
  let release; handler = () => new Promise(resolve => { release = resolve; });
  render(base, 'late'); await until(() => release, 'request pending');
  render({ ...base, topic: '已经改变的主题' }, 'late'); await wait(30);
  release(reply(8)); await wait(80);
  assert(!checkbox('纳入资料 推荐文章 8'), 'Late result attached to changed topic');
  assert(findButton('确认材料').disabled, 'Changed context was confirmable');
  passed('Late response cannot attach to changed topic or confirm stale inputs');
  handler = async () => reply(0); render({ ...base, mode: 'expanded', sourceIds: [] }, 'empty-expanded');
  await until(() => document.querySelector('[data-materials-test-case="empty-expanded"] .materials-channel-status') && !findButton('重新生成查询并推荐').disabled, 'empty expanded recommendations');
  assert(findButton('确认材料').disabled, 'Empty expanded source scope did not require acknowledgement');
  document.querySelector('.materials-empty-consent input').click(); await wait(30);
  assert(!findButton('确认材料').disabled, 'Acknowledged expanded writing cannot continue without sources');
  passed('Expanded writing requires explicit consent to continue without sources');
  render({ ...base, mode: 'strict', sourceIds: [] }, 'empty-strict');
  // Wait for this distinct React commit AND its completed mocked request, not
  // the previous screen's enabled buttons during an asynchronous root.render.
  await until(() => document.querySelector('[data-materials-test-case="empty-strict"] .materials-channel-status') && !findButton('重新生成查询并推荐').disabled, 'empty strict recommendations');
  assert(!document.querySelector('.materials-empty-consent') && findButton('确认材料').disabled, 'Strict writing accepted an empty source scope');
  passed('Strict writing still requires at least one source');
  handler = async () => reply(40); render(base, 'visual');
  await until(() => checkbox('纳入资料 推荐文章 40'), 'forty article display');
  assert(document.querySelectorAll('.material-card').length === 41, 'Dozens of recommendations truncated');
  passed('Forty recommendations remain available for review');
  assert(!findButton('确认材料').disabled, 'Current material selection unexpectedly blocked before changing config');
  findButton('检索配置').click();
  await until(() => document.querySelector('input[aria-label="推荐文章数"]') && !findButton('保存检索配置').disabled, 'settings loaded from isolated fixture');
  const resultLimitInput = document.querySelector('input[aria-label="推荐文章数"]');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(resultLimitInput, '25');
  resultLimitInput.dispatchEvent(new Event('input', { bubbles: true }));
  document.querySelector('.retrieval-toggle input').click(); await wait(30);
  findButton('保存检索配置').click();
  await until(() => settingsWrites.length === 1 && document.querySelector('.materials-notice'), 'settings saved and recommendation invalidated');
  assert(findButton('确认材料').disabled, 'Changed configuration did not require a new recommendation');
  passed('Saving retrieval settings requires a fresh recommendation before confirmation');
  const expectedSettings = { ...defaultSettings, resultLimit: 25, queryExpansion: false };
  assert(JSON.stringify(settingsWrites[0]) === JSON.stringify(expectedSettings), 'Mock settings persistence changed fields outside the exact edit');
  findButton('检索配置').click();
  await until(() => document.querySelector('input[aria-label="推荐文章数"]') && !findButton('保存检索配置').disabled, 'saved settings reopened');
  assert(document.querySelector('input[aria-label="推荐文章数"]').value === '25' && !document.querySelector('.retrieval-toggle input').checked, 'Reopened settings did not read exact persisted mock values');
  assert(settingsWrites.length === 1, 'Reopening settings performed an unexpected save');
  passed('Settings mock persists exact edits and reopening performs no extra mutation');
  findButton('返回材料确认').click();
  await until(() => findButton('按这些条件检索'), 'returned to material review');
  const beforeRerun = requests.length; findButton('按这些条件检索').click();
  await until(() => requests.length === beforeRerun + 1 && !findButton('确认材料').disabled && !document.querySelector('.materials-notice'), 'fresh recommendation clears configuration invalidation');
  assert(document.querySelectorAll('.material-card').length === 41, 'Final visual view lost forty recommendations');
  output.dataset.status = 'passed'; output.textContent = JSON.stringify({ status: 'passed', results }, null, 2);
}
run().catch(error => { output.dataset.status = 'failed'; output.textContent = JSON.stringify({ status: 'failed', error: error.stack, results }, null, 2); });
