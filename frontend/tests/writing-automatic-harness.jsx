import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingWorkspace from '../src/Writing';
import { useWriting } from '../src/useWriting';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/glass-refinement.css';
import '../src/theme-layout.css';
import '../src/civic-card-selection.css';

const query = new URLSearchParams(location.search), output = document.getElementById('writing-automatic-result');
document.documentElement.dataset.theme = ['jade', 'glass', 'civic'].includes(query.get('theme')) ? query.get('theme') : 'jade';
const root = createRoot(document.getElementById('writing-automatic-root'));
const clone = value => structuredClone(value), pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const requests = [], results = [], downloads = [], confirmations = [], automaticStarts = [];
const originalClick = HTMLAnchorElement.prototype.click;
let hook, setStage, remote, scenario = 'complete', version = 1;
const fixture = () => ({
  id: 'automatic-ui-isolated', title: '城市公共服务资料整理', mode: 'strict', version: 1, modelId: '',
  brief: { topic: '城市公共服务资料整理', purpose: '整理原文明示事实', targetWords: 1500, style: '清晰克制', template: '观点文章', workflow: 'oneshot', automation: 'manual' },
  sourceIds: ['source-fixture'], sources: [{ sourceId: 'source-fixture', title: '城市公共服务原文' }], materialSelection: { recommendationId: null, passages: [] },
  sections: [], runs: [], illustrations: [], illustrationReview: null, outlineConfirmed: false,
  createdAt: '2026-10-23T01:00:00Z', updatedAt: '2026-10-23T01:00:00Z',
});
const section = () => ({ id: 'section-automatic-ui', title: '已知服务进展', goal: '整理明示事实', mode: 'inherit', targetWords: 500, content: '公共服务平台开放了新的办事入口。', contentHash: 'isolated-body', version: 1, locked: false, manual: false, evidence: [] });
const finalReport = () => ({ status: 'completed_with_issues', title: '全自动写作报告', generatedAt: '2026-10-23T02:00:00Z', outputAvailable: true, summary: { generatedSections: 1, preservedSections: 0, skippedSections: 0, illustrations: 0 }, stages: [{ stage: 'images', status: 'skipped', message: '无合适配图，已保留纯文字。' }], issues: [{ code: 'image_unavailable', stage: 'images', message: '没有与目标段落直接相关的清晰图片，已保留纯文字。' }, { code: 'citation_support', stage: 'chapters', sectionTitle: '已知服务进展', message: '模型引文无法在冻结原文中逐字定位，blockId: C01-B999' }] });
function json(value) { return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }); }
window.confirm = message => { confirmations.push(message); throw new Error('Automatic flow called confirm'); };
HTMLAnchorElement.prototype.click = function () { downloads.push({ filename: this.download }); };
// All requests stay in this in-memory fixture. No source, real manuscript or
// actual model is read or changed; unknown endpoints fail immediately.
window.fetch = async (input, options = {}) => {
  const url = typeof input === 'string' ? input : input.url, method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : undefined;
  requests.push({ url, method, body });
  if (url === '/api/writing/projects' && method === 'GET') return json({ projects: [clone(remote)] });
  if (url === '/api/writing/projects/automatic-ui-isolated' && method === 'GET') return json({ project: clone(remote) });
  if (url === '/api/writing/projects/automatic-ui-isolated' && method === 'PATCH') {
    assert(body.expectedVersion === remote.version, 'Settings did not fence the current saved version');
    remote = { ...remote, ...body, version: ++version }; delete remote.expectedVersion;
    return json({ project: clone(remote) });
  }
  if (url === '/api/writing/projects/automatic-ui-isolated/automatic' && method === 'POST') {
    assert(body.expectedVersion === remote.version && Object.keys(body).length === 1, 'Automatic start lost expectedVersion or sent hidden stage approvals');
    const expectedMode = scenario === 'no-sources-expanded' ? 'extended' : 'strict';
    assert(remote.brief.automation === 'automatic' && remote.brief.workflow === 'chapter' && remote.mode === expectedMode, 'Automation replaced or failed to save initial workflow and source mode');
    automaticStarts.push({ scenario, mode: remote.mode, modelId: remote.modelId, sourceIds: clone(remote.sourceIds), materialSelection: clone(remote.materialSelection) });
    remote.version = ++version;
    remote.automatic = { runId: 'run-automatic-ui', jobId: 'job-automatic-ui', status: 'queued', stage: 'materials', report: null };
    remote.runs = [{ id: 'run-automatic-ui', jobId: 'job-automatic-ui', kind: 'automatic', status: 'queued', applied: false }];
    return json({ project: clone(remote), runId: 'run-automatic-ui', job: { id: 'job-automatic-ui' } });
  }
  if (url.endsWith('/generation-plan') && method === 'GET') return json({ plan: { canGenerate: true } });
  throw new Error(`Unexpected isolated request: ${method} ${url}`);
};
function Harness({ initialStage = 'brief' }) {
  hook = useWriting();
  const [stage, setStageState] = useState(initialStage); setStage = setStageState;
  return <div className="workspace-shell" style={{ height: '100%' }}><WritingWorkspace stage={stage} onStageChange={setStageState} writing={hook} documents={[{ id: 'source-fixture', title: '城市公共服务原文' }]} models={[{ id: 'model-fixture', name: '隔离模型', model: 'mock' }]}/></div>;
}
const button = text => [...document.querySelectorAll('button')].find(item => item.textContent.trim() === text);
function assertFinishedProgress(bodyComplete, context) {
  const bands = document.querySelectorAll('[data-writing-progress]');
  assert(bands.length === 1, `${context}: finished manuscript must have one workflow footer`);
  const band = bands[0], bounds = band.getBoundingClientRect();
  assert(bounds.width > 0 && bounds.height > 0 && bounds.top >= 0 && bounds.bottom <= innerHeight + 1, `${context}: workflow footer is hidden or outside the viewport (${JSON.stringify({ top: bounds.top, bottom: bounds.bottom, height: bounds.height, viewportHeight: innerHeight })})`);
  const bodyStep = band.querySelector('[data-writing-step="body"]')?.closest('li');
  assert(bodyStep && bodyStep.classList.contains('is-complete') === bodyComplete, `${context}: body milestone disagrees with actual saved chapter content`);
  assert(!document.querySelector('.writing-automatic-stages'), `${context}: ended automation retained a duplicate running-stage strip`);
}
function assertActiveProgress(stepId, context) {
  const bands = document.querySelectorAll('[data-writing-progress]');
  assert(bands.length === 1, `${context}: running automation must have exactly one workflow footer`);
  const band = bands[0], bounds = band.getBoundingClientRect();
  assert(bounds.width > 0 && bounds.height > 0 && bounds.top >= 0 && bounds.bottom <= innerHeight + 1, `${context}: automatic footer is hidden or outside the viewport (${JSON.stringify({ top: bounds.top, bottom: bounds.bottom, height: bounds.height, viewportHeight: innerHeight })})`);
  assert(band.classList.contains('is-automatic') && band.querySelector('.writing-progress-automatic-badge')?.textContent.trim() === '全自动', `${context}: active footer is not labelled 全自动`);
  const workspaceBottom = band.closest('.writing-root').getBoundingClientRect().bottom;
  assert(Math.abs(bounds.bottom - workspaceBottom) <= 2, `${context}: automatic footer moved away from the workspace bottom (${bounds.bottom} / ${workspaceBottom})`);
  const steps = [...band.querySelectorAll('button[data-writing-step]')];
  assert(steps.length === 6 && steps.map(item => item.dataset.writingStep).join(',') === 'materials,outline,body,images,citations,review', `${context}: active footer has manual workflow steps instead of the automatic phases`);
  assert(steps.every(item => item.disabled), `${context}: active automatic phases expose manual navigation or confirmation`);
  assert(band.querySelector('li.is-next button')?.dataset.writingStep === stepId && band.querySelector('[aria-current="step"]')?.dataset.writingStep === stepId, `${context}: current automatic footer step disagrees with phase ${stepId}`);
  assert(!document.querySelector('.writing-automatic-stages'), `${context}: running automation has duplicate top and bottom stage strips`);
  assert(!document.querySelector('[role="dialog"]') && ![...document.querySelectorAll('.writing-flow-actions button')].some(item => item.getClientRects().length), `${context}: active automatic footer exposed manual confirmation controls`);
}
async function until(predicate, label, timeout = 6000) { const end = performance.now() + timeout; while (!predicate()) { if (performance.now() > end) throw new Error(`Timed out: ${label}`); await pause(20); } }
const passed = name => { results.push({ name, status: 'passed' }); output.textContent = JSON.stringify({ status: 'running', results }, null, 2); };
async function run() {
  remote = fixture();
  remote.modelId = 'model-stale-manual-choice';
  remote.materialSelection = { recommendationId: 'recommendation-fixture', passages: [{ sourceId: 'source-fixture', revisionId: 'revision-fixture', blockId: 'block-fixture' }] };
  const initialSources = clone(remote.sourceIds), initialSelection = clone(remote.materialSelection), initialModel = remote.modelId;
  root.render(<Harness/>);
  await until(() => hook?.project.id === remote.id && !hook.loading, 'initial settings');
  const emptyLibrarySelection = button('采用知源文库已选 · 0');
  assert(emptyLibrarySelection?.disabled, 'Empty library selection can replace already selected writing materials');
  emptyLibrarySelection.click(); await pause(40);
  assert(JSON.stringify(hook.project.sourceIds) === JSON.stringify(initialSources), 'Empty library selection erased manuscript materials');
  assert(JSON.stringify(hook.project.materialSelection) === JSON.stringify(initialSelection), 'Empty library selection erased confirmed passage scope');
  assert(document.querySelector('.writing-aside-sources')?.textContent.includes('城市公共服务原文'), 'Existing source disappeared from the manuscript source list');
  assert(!requests.some(item => item.method !== 'GET'), 'Empty library selection wrote initial settings');
  passed('zero selected library sources cannot replace manuscript materials or confirmed passages');
  assert(document.getElementById('writing-model')?.value === initialModel, 'Unavailable prior manual model choice was not preserved in settings');
  document.querySelector('[aria-label="自动化程度"] button:last-child').click();
  await until(() => hook.project.automation === 'automatic' && button('开始全自动写作'), 'automatic option');
  assert(!document.getElementById('writing-model') && document.body.textContent.includes('自动使用可用模型'), 'Automatic setup still requires a manual writing-model selection');
  assert(hook.project.modelId === initialModel && !document.querySelector('[role="dialog"]') && confirmations.length === 0, 'Automatic setup erased the saved manual choice or asked for model confirmation');
  document.querySelector('[aria-label="自动化程度"] button:first-child').click();
  await until(() => hook.project.automation === 'manual' && document.getElementById('writing-model'), 'manual model control restored');
  assert(document.getElementById('writing-model').value === initialModel && hook.project.modelId === initialModel, 'Switching back to manual did not restore the original model choice');
  document.querySelector('[aria-label="自动化程度"] button:last-child').click();
  await until(() => hook.project.automation === 'automatic' && !document.getElementById('writing-model'), 'automatic model control hidden again');
  passed('automatic setup hides model choice and preserves the prior unavailable manual selection across mode switches');
  document.querySelector('[aria-label="写作流程"] button:last-child').click();
  await until(() => hook.project.workflow === 'chapter', 'independent chapter workflow');
  button('开始全自动写作').click();
  await until(() => hook.pending && hook.project.automatic?.status === 'queued', 'one-click automatic queue');
  await pause(80);
  assert(!document.querySelector('[role="dialog"]'), 'Automatic start opened a confirmation dialog');
  assert(requests.filter(item => item.method === 'POST' && item.url.endsWith('/automatic')).length === 1, 'Automatic start submitted more than once');
  assert(requests.filter(item => item.method === 'PATCH').length === 1, 'Initial settings were not saved exactly once');
  const initialPatch = requests.find(item => item.method === 'PATCH');
  assert(initialPatch.body.modelId === initialModel && automaticStarts[0].modelId === initialModel, 'Frontend guessed or silently replaced the saved manual model before automatic resolution');
  assert(JSON.stringify(initialPatch.body.sourceIds) === JSON.stringify(initialSources) && JSON.stringify(initialPatch.body.materialSelection) === JSON.stringify(initialSelection), 'Initial save lost already selected source or passage scope');
  assert(JSON.stringify(automaticStarts[0].sourceIds) === JSON.stringify(initialSources) && JSON.stringify(automaticStarts[0].materialSelection) === JSON.stringify(initialSelection), 'Automatic POST started with cleared manuscript materials');
  assert(![...document.querySelectorAll('.writing-flow-actions button')].some(item => item.getClientRects().length), 'Manual actions remain available during automation');
  assertActiveProgress('materials', 'queued automatic material preparation');
  passed('one click saves independent initial settings and starts one automatic job without confirmation');

  remote.automatic.status = 'running'; remote.automatic.stage = 'outline';
  remote.automatic.report = finalReport(); // Early draft reports must stay hidden.
  remote.runs[0].status = 'running'; remote.runs.push({ id: 'child-outline-ui', kind: 'outline', status: 'failed', error: '中途缺口绝不能提前显示', applied: false });
  await until(() => hook.project.automatic?.stage === 'outline', 'outline progress');
  assert(document.querySelector('[aria-label="全自动写作"]').textContent.includes('规划纲要'), 'Automatic stage is not visible');
  assert(!button('阅读报告') && !button('下载报告') && !document.body.textContent.includes('中途缺口绝不能提前显示'), 'Interim issue/report leaked before final');
  assertActiveProgress('outline', 'running automatic outline');
  const activeMutations = requests.filter(item => item.method !== 'GET').length, activeStarts = automaticStarts.length, activeSnapshot = JSON.stringify(hook.project);
  document.querySelectorAll('[data-writing-progress] button').forEach(item => item.click());
  await pause(40);
  assertActiveProgress('outline', 'disabled automatic footer steps');
  assert(requests.filter(item => item.method !== 'GET').length === activeMutations && automaticStarts.length === activeStarts, 'Clicking a disabled automatic phase saved, confirmed or queued a task');
  assert(JSON.stringify(hook.project) === activeSnapshot && confirmations.length === 0, 'Clicking a disabled automatic phase altered the draft or requested confirmation');
  for (const [serverStage, currentStep] of [['chapters', 'body'], ['images', 'images'], ['review', 'review']]) {
    remote.automatic.stage = serverStage;
    await until(() => hook.project.automatic?.stage === serverStage && document.querySelector('[data-writing-progress] li.is-next button')?.dataset.writingStep === currentStep, `automatic ${serverStage} footer progress`);
    assertActiveProgress(currentStep, `running automatic ${serverStage}`);
    assert(!button('阅读报告') && !button('下载报告') && !document.body.textContent.includes('中途缺口绝不能提前显示'), `Automatic ${serverStage} phase exposed unfinished issues or report`);
  }
  assert(requests.filter(item => item.method !== 'GET').length === activeMutations && automaticStarts.length === activeStarts && confirmations.length === 0, 'Polling automatic phases submitted or confirmed a duplicate task');
  passed('one visible disabled automatic footer follows outline, body, images and review while keeping interim failures and reports hidden');

  remote.version = ++version; remote.sections = [section()]; remote.outlineConfirmed = true;
  remote.automatic.status = 'completed_with_issues'; remote.automatic.stage = 'completed'; remote.automatic.report = finalReport();
  remote.runs = [{ ...remote.runs[0], status: 'succeeded', applied: true }];
  await until(() => !hook.pending && button('阅读报告'), 'final report');
  assertFinishedProgress(true, 'completed automatic manuscript');
  button('阅读报告').click(); await until(() => document.querySelector('.writing-automatic-report-body'), 'separate report reader');
  assertFinishedProgress(true, 'completed automatic manuscript with report open');
  assert(document.querySelector('.writing-automatic-report-body').textContent.includes('已保留纯文字'), 'Final issue missing in separate report');
  assert(document.querySelector('.writing-automatic-report-body').textContent.includes('引用对应问题已记录'), 'Citation fault missing from final report');
  assert(!document.querySelector('.writing-automatic-report-body').textContent.match(/重新生成|blockId|C01-B999/), 'Citation report asks to restart or exposes internal details');
  assert(!hook.project.sections[0].content.includes('图片') && !hook.project.sections[0].content.includes('待核对'), 'Report commentary contaminated manuscript');
  button('下载报告').click();
  assert(downloads.length === 1 && downloads[0].filename.endsWith('（全自动写作报告）.txt'), 'Final report did not download separately');
  passed('terminal result preserves manuscript and offers an independent readable downloadable report');

  root.render(<Harness key="reopen" initialStage="editor"/>);
  await until(() => hook?.project.automatic?.status === 'completed_with_issues' && button('阅读报告'), 'reopen saved final result');
  assert(requests.filter(item => item.method === 'POST').length === 1 && confirmations.length === 0, 'Reopening restarted or requested confirmation');
  assertFinishedProgress(true, 'reopened completed automatic manuscript');
  passed('reopening reads persisted completion without restarting or asking for confirmation');

  root.render(<Harness key="finished-initial-settings" initialStage="brief"/>);
  await until(() => !hook.loading && hook.project.automatic?.status === 'completed_with_issues' && button('开始全自动写作'), 'completed automatic initial settings');
  assertFinishedProgress(true, 'completed automatic initial settings');
  assert(requests.filter(item => item.method === 'POST').length === 1, 'Viewing completed initial settings restarted automatic writing');
  passed('completed automatic initial settings retain the workflow footer without restarting writing');

  remote.brief.automation = 'manual'; remote.version = ++version;
  root.render(<Harness key="manual-with-automatic-history" initialStage="editor"/>);
  await until(() => !hook.loading && hook.project.automation === 'manual' && hook.project.version === remote.version && button('阅读报告'), 'manual workflow with completed automatic history');
  assertFinishedProgress(true, 'manual manuscript with automatic history');
  const historicalMutations = requests.filter(item => item.method !== 'GET').length, historicalStarts = automaticStarts.length;
  document.querySelector('[data-writing-step="brief"]').click();
  await until(() => document.getElementById('writing-model'), 'historical manual footer opens initial settings');
  assertFinishedProgress(true, 'historical manual initial settings');
  document.querySelector('[data-writing-step="body"]').click();
  await until(() => document.querySelector('.writing-body-reading') && button('阅读报告'), 'historical manual footer returns to manuscript');
  assert(requests.filter(item => item.method !== 'GET').length === historicalMutations && automaticStarts.length === historicalStarts && confirmations.length === 0, 'Historical footer navigation submitted or confirmed a writing task');
  const historicalRemote = JSON.stringify(remote);
  hook.setProject(previous => ({ ...previous, sections: previous.sections.map(item => ({ ...item, content: '' })) }));
  await until(() => hook.dirty && hook.project.sections.every(item => !item.content), 'unsaved changes after historical automatic writing');
  assertFinishedProgress(false, 'historical automatic manuscript with unsaved empty body');
  const dirtyBand = document.querySelector('[data-writing-progress]');
  assert(Number(dirtyBand.dataset.completed) < Number(dirtyBand.dataset.total), 'A historical automatic result falsely completed the current dirty manuscript workflow');
  assert(JSON.stringify(remote) === historicalRemote && requests.filter(item => item.method !== 'GET').length === historicalMutations, 'Dirty historical progress inspection saved or altered the manuscript');
  passed('historical automatic records retain manual navigation and actual dirty-body progress without submitting duplicate tasks');

  for (const status of ['cancelled', 'completed_with_issues']) {
    scenario = status; remote = fixture(); remote.brief.automation = 'automatic'; remote.version = ++version;
    remote.automatic = { runId: `run-${status}`, status, stage: 'materials', report: { ...finalReport(), outputAvailable: false, summary: { generatedSections: 0, preservedSections: 0, skippedSections: 0, illustrations: 0 }, issues: [{ stage: 'materials', message: '本次未选定可用资料，未生成正文。' }] } };
    root.render(<Harness key={status} initialStage="editor"/>);
    await until(() => hook.project.automatic?.runId === `run-${status}` && button('阅读报告'), `${status} terminal report`);
    assertFinishedProgress(false, `${status} without manuscript output`);
    assert(!button('阅读全文') && !document.querySelector('[role="dialog"]'), `${status} presented an empty manuscript or wait`);
    assert(document.querySelector('.writing-automatic-heading h3')?.textContent === (status === 'cancelled' ? '已取消 · 未生成正文' : '未生成正文'), `${status} claimed completion without manuscript output`);
    assert(!document.querySelector('.writing-automatic-heading p')?.textContent.includes('已完成的内容已保存'), `${status} claimed absent manuscript was saved`);
    button('阅读报告').click(); await until(() => document.querySelector('.writing-automatic-report-body')?.textContent.includes('未生成正文'), `${status} gap report`);
    assertFinishedProgress(false, `${status} without output and report open`);
    passed(`${status}: no-output result finishes with report and no confirmation wait`);
  }
  remote = fixture(); remote.brief.automation = 'automatic'; remote.version = ++version;
  remote.sections = [section(), { ...section(), id: 'unwritten-section', title: '尚未生成章节', content: '' }];
  remote.automatic = { runId: 'run-partial', status: 'completed_with_issues', stage: 'complete', report: { ...finalReport(), summary: { generatedSections: 1, preservedSections: 0, skippedSections: 1, illustrations: 0 } } };
  root.render(<Harness key="partial" initialStage="editor"/>);
  await until(() => document.querySelector('.writing-automatic-heading h3')?.textContent === '正文部分完成', 'partial manuscript result');
  assertFinishedProgress(false, 'partial automatic manuscript');
  assert(button('阅读全文') && document.querySelector('.writing-automatic-heading p')?.textContent.includes('其余章节尚未完成'), 'Partial output lost reading access or claimed all chapters complete');
  passed('partial output stays readable and clearly identifies unfinished chapters');

  scenario = 'no-sources-strict'; remote = fixture(); remote.version = ++version;
  remote.brief.automation = 'automatic'; remote.brief.workflow = 'chapter';
  remote.sourceIds = []; remote.sources = []; remote.materialSelection = { recommendationId: null, passages: [] };
  root.render(<Harness key="no-sources-strict"/>);
  await until(() => !hook.loading && hook.project.version === remote.version && hook.project.sourceIds.length === 0 && button('开始全自动写作'), 'standalone strict setup without materials');
  hook.setProject(previous => ({ ...previous, title: '尚未保存的严谨文稿', purpose: '保留未保存的写作要求', targetWords: 1700 }));
  await until(() => hook.project.title === '尚未保存的严谨文稿' && hook.dirty, 'unsaved strict initial requirements');
  const strictDraft = JSON.stringify(hook.project), strictRemote = JSON.stringify(remote);
  const strictMutations = requests.filter(item => item.method !== 'GET').length, strictStarts = automaticStarts.length;
  button('开始全自动写作').click();
  await until(() => hook.error.includes('选定参考资料'), 'strict material guard on initial submit');
  assert(button('开始全自动写作') && !document.querySelector('[role="dialog"]'), 'Missing strict materials navigated away or opened an intermediate confirmation');
  const blocked = await hook.startAutomatic(); await pause(30);
  assert(blocked === null && /参考资料|材料/.test(hook.error), 'Direct strict automatic hook did not return a business error and null');
  assert(!hook.busy && !hook.pending, 'Missing strict materials left an active writing state');
  assert(requests.filter(item => item.method !== 'GET').length === strictMutations && automaticStarts.length === strictStarts, 'Strict material guard saved settings or queued an automatic job before checking materials');
  assert(JSON.stringify(hook.project) === strictDraft && JSON.stringify(remote) === strictRemote && hook.dirty, 'Strict material guard changed saved or unsaved draft state');
  passed('standalone strict setup rejects empty materials before UI and direct-hook saves or queues while preserving unsaved requirements');

  scenario = 'no-sources-expanded'; remote = fixture(); remote.version = ++version; remote.mode = 'extended';
  remote.brief.automation = 'automatic'; remote.brief.workflow = 'chapter';
  remote.sourceIds = []; remote.sources = []; remote.materialSelection = { recommendationId: null, passages: [] };
  root.render(<Harness key="no-sources-expanded"/>);
  await until(() => !hook.loading && hook.project.version === remote.version && hook.project.mode === 'expanded' && button('开始全自动写作'), 'expanded initial setup without sources');
  hook.setProject(previous => ({ ...previous, purpose: '无需原文的延伸讨论，新增事实另行核实' }));
  await until(() => hook.dirty && hook.project.purpose === '无需原文的延伸讨论，新增事实另行核实', 'expanded unsaved initial requirements');
  const expandedRequestIndex = requests.length, expandedStarts = automaticStarts.length;
  button('开始全自动写作').click();
  await until(() => hook.pending && hook.project.automatic?.status === 'queued', 'expanded empty-source automatic start');
  await pause(60);
  const expandedRequests = requests.slice(expandedRequestIndex);
  assert(expandedRequests.filter(item => item.method === 'PATCH').length === 1 && expandedRequests.filter(item => item.method === 'POST' && item.url.endsWith('/automatic')).length === 1, 'Expanded empty-source setup did not save and queue exactly once');
  assert(automaticStarts.length === expandedStarts + 1 && automaticStarts.at(-1).mode === 'extended' && automaticStarts.at(-1).sourceIds.length === 0, 'Expanded automatic start changed requested mode or fabricated source selection');
  assert(remote.mode === 'extended' && hook.project.mode === 'expanded' && hook.project.sourceIds.length === 0 && !hook.error && !document.querySelector('[role="dialog"]'), 'Expanded source-free start inherited the strict material guard or changed its requested scope');
  remote.version = ++version; remote.sections = [section()]; remote.outlineConfirmed = true;
  remote.automatic.status = 'completed_with_issues'; remote.automatic.stage = 'complete'; remote.automatic.report = finalReport();
  remote.runs = [{ ...remote.runs[0], status: 'succeeded', applied: true }];
  await until(() => !hook.pending && button('阅读报告'), 'expanded empty-source terminal result');
  passed('expanded setup with no materials preserves extended mode and queues exactly once');
  if (query.get('manual') === 'running') { remote = fixture(); remote.brief.automation = 'automatic'; remote.automatic = { runId: 'run-manual', status: 'running', stage: 'body' }; remote.runs = [{ id: 'run-manual', jobId: 'job-manual', kind: 'automatic', status: 'running' }]; root.render(<Harness key="manual-running" initialStage="editor"/>); }
  if (query.get('manual') === 'complete') { remote = fixture(); remote.brief.automation = 'automatic'; remote.sections = [section()]; remote.outlineConfirmed = true; remote.automatic = { runId: 'run-manual-complete', status: 'completed_with_issues', stage: 'completed', report: finalReport() }; root.render(<Harness key="manual-complete" initialStage="editor"/>); }
  output.dataset.status = 'passed'; output.textContent = JSON.stringify({ status: 'passed', theme: document.documentElement.dataset.theme, results, mutations: requests.filter(item => item.method !== 'GET'), automaticStarts, downloads, confirmations }, null, 2);
}
run().catch(error => { output.dataset.status = 'failed'; output.textContent = JSON.stringify({ status: 'failed', message: error.message, stack: error.stack, scenario, results, requests }, null, 2); console.error(error); });
