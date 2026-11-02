import React, { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingWorkspace from '../src/Writing';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';

const options = new URLSearchParams(window.location.search);
const themes = ['jade', 'glass', 'civic'];
const initialTheme = themes.includes(options.get('theme')) ? options.get('theme') : 'jade';
document.documentElement.dataset.theme = initialTheme;
const output = document.getElementById('writing-progress-test-result');
const root = createRoot(document.getElementById('writing-progress-test-root'));
const results = [], requests = [], calls = [], downloads = [], downloadHistory = [], modelActions = [], opened = [];
const clone = value => structuredClone(value);
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
async function until(predicate, label) {
  for (let index = 0; index < 250; index += 1) { if (predicate()) return; await wait(20); }
  throw new Error(`Timed out: ${label}`);
}
const scenarioLabels = {
  blank: '空白准备', prepared: '要求已保存', outline: '纲要待确认', partial: '部分正文', complete: '正文齐备',
  review: '当前校验', issues: '校验有问题', stale: '校验过期', pending: '任务进行中', dirty: '未保存修改', remote: '已保存文稿更新',
};
const baseContent = '把复杂问题拆成清楚的步骤，有助于读者理解文章的论证顺序。[E1]';
const referenceQuote = baseContent.slice(0, baseContent.indexOf('[E1]'));
const reference = { id: 'E1', sourceId: 'progress-source', revisionId: 'progress-revision', blockId: 'progress-block', title: '写作参考样本',
  quote: referenceQuote, start: 0, end: Array.from(referenceQuote).length, locator: { paragraph: 1 } };
const baseContentHash = 'bd61776f03e66dd7d4fe82d441aa4a736c0a3efd59dc1499258606841fe5dce5';
const baseBindings = { version: 1, content: baseContent, contentHash: baseContentHash, mode: 'strict', items: [{ id: 'progress-checked-first',
  evidenceId: 'E1', markerStart: baseContent.indexOf('[E1]'), start: 0, end: referenceQuote.length, text: referenceQuote, status: 'supported', reason: '原文明确支持本句。' }] };
function makeReport(project, issues = []) {
  const rows = project.sections.map(section => ({ sectionId: section.id, title: section.title, actual: 36, target: 100, min: 80, max: 120, status: 'within_range' }));
  return { version: project.version, checkedAt: '2026-10-23T04:00:00Z', status: issues.length ? 'needs_attention' : 'rules_passed', semanticVerified: false,
    issues, counts: { errors: issues.filter(item => item.severity === 'error').length, warnings: issues.filter(item => item.severity !== 'error').length },
    wordCounts: { total: rows.length * 36, target: 200, min: 160, max: 240, status: 'within_range', sections: rows },
    coverage: { paragraphs: rows.length, validlyCitedParagraphs: rows.length, pendingParagraphs: 0 } };
}
function baseProject() {
  return { id: 'progress-project', version: 3, title: '把写作过程理清楚', topic: '把写作过程理清楚', purpose: '帮助读者逐步理解问题', targetWords: 200,
    style: '清晰克制', template: '观点文章', mode: 'strict', workflow: 'chapter', modelId: '', activeSectionId: 'progress-first',
    sourceIds: ['progress-source'], sources: [{ sourceId: 'progress-source', revisionId: 'progress-revision', title: '写作参考样本' }],
    materialSelection: { recommendationId: null, passages: [] }, outlineConfirmed: true, runs: [], review: null,
    sections: [
      { id: 'progress-first', title: '先理清问题', goal: '明确文章要回答的问题', mode: 'inherit', words: 100, version: 1, content: baseContent, contentHash: baseContentHash, citationBindings: baseBindings, evidence: [reference], locked: false, manual: true },
      { id: 'progress-second', title: '再展开回答', goal: '给出有根据的回答', mode: 'inherit', words: 100, version: 1, content: '依据材料组织论述，再核对事实与结论之间的支持关系。[E1]', evidence: [reference], locked: true, manual: false },
    ], createdAt: '2026-10-23T03:00:00Z', updatedAt: '2026-10-23T04:00:00Z' };
}
function makeScenario(name) {
  const project = baseProject();
  if (name === 'blank') Object.assign(project, { id: null, version: 0, title: '', topic: '', purpose: '', sections: [], sourceIds: [], sources: [], outlineConfirmed: false });
  if (name === 'prepared') Object.assign(project, { sections: [], outlineConfirmed: false });
  if (name === 'outline') { project.outlineConfirmed = false; project.sections.forEach(section => { section.content = ''; }); }
  if (['partial', 'pending'].includes(name)) project.sections[1].content = '';
  if (['review', 'issues', 'stale'].includes(name)) project.review = makeReport(project, name === 'issues' ? [{ code: 'manual_edit_recheck', message: '请核对人工修改后的引用是否支持相邻论述。', severity: 'warning', sectionId: project.sections[0].id }] : []);
  if (name === 'stale') project.review.version -= 1;
  if (name === 'pending') project.runs = [{ id: 'progress-run', jobId: 'progress-job', kind: 'document', status: 'running', stage: 'generating', createdAt: '2026-10-23T04:00:00Z', applied: false,
    plan: { strategy: 'chapter', targetWords: 200, sections: [] }, candidate: { document: { steps: [{ sectionId: 'progress-first', status: 'applied' }, { sectionId: 'progress-second', status: 'generating' }] } } }];
  const savedProject = project.id ? clone(project) : null;
  if (name === 'dirty') project.sections[0].content += '\n\n这里是尚未保存的人工补充。';
  return { project, savedProject, remoteChanged: name === 'remote', busy: '', error: '' };
}
const requirementFingerprint = project => JSON.stringify({ title: project.title, topic: project.topic, purpose: project.purpose, targetWords: project.targetWords,
  style: project.style, template: project.template, mode: project.mode, workflow: project.workflow, modelId: project.modelId,
  sourceIds: project.sourceIds, materialSelection: project.materialSelection, outline: project.sections.map(section => [section.id, section.title, section.goal, section.mode, section.words]) });
const fingerprint = project => JSON.stringify([requirementFingerprint(project), project.sections.map(section => [section.id, section.content, section.locked])]);
const initialScenario = Object.hasOwn(scenarioLabels, options.get('scenario')) ? options.get('scenario') : 'complete';
const initialStage = ['projects', 'brief', 'outline', 'editor'].includes(options.get('stage')) ? options.get('stage') : initialScenario === 'blank' || initialScenario === 'prepared' ? 'brief' : initialScenario === 'outline' ? 'outline' : 'editor';
const workspaceWidths = ['auto', '760', '1040', '1360'];
const initialWorkspaceWidth = workspaceWidths.includes(options.get('width')) ? options.get('width') : 'auto';
let harness;

// This isolated page never falls through to the real service. Unknown calls
// fail rather than creating projects, model tasks, exports or retrieval work.
window.fetch = async (input, init = {}) => {
  const request = { url: typeof input === 'string' ? input : input.url, method: init.method || 'GET' };
  requests.push(request); throw new Error(`Unexpected isolated progress fixture request: ${request.method} ${request.url}`);
};

function Harness() {
  const [state, setState] = useState(() => makeScenario(initialScenario));
  const current = useRef(state); current.current = state;
  const [stage, setStage] = useState(initialStage), [scenario, setScenario] = useState(initialScenario), [theme, setTheme] = useState(initialTheme);
  const [workspaceWidth, setWorkspaceWidth] = useState(initialWorkspaceWidth);
  const [generation, setGeneration] = useState(0), [toast, setToast] = useState('');
  const toastTimer = useRef(null);
  const replace = next => { current.current = next; setState(next); };
  const dirty = state.project.id ? !state.savedProject || fingerprint(state.project) !== fingerprint(state.savedProject) : !!(state.project.topic || state.project.title || state.project.sections.length);
  const load = (name, targetStage) => {
    replace(makeScenario(name)); setScenario(name); setStage(targetStage || (name === 'blank' || name === 'prepared' ? 'brief' : name === 'outline' ? 'outline' : 'editor'));
    setGeneration(value => value + 1); setToast(''); calls.length = 0; downloads.length = 0;
  };
  const saveMemory = () => {
    const before = current.current, changed = !before.project.id || !before.savedProject || fingerprint(before.project) !== fingerprint(before.savedProject);
    const project = clone(before.project);
    if (changed) {
      project.id ||= 'progress-new-project'; project.version = before.project.id ? before.project.version + 1 : 1;
      project.updatedAt = '2026-10-23T04:30:00Z'; project.review = null;
      if (before.savedProject && requirementFingerprint(project) !== requirementFingerprint(before.savedProject)) project.outlineConfirmed = false;
    }
    replace({ ...before, project, savedProject: clone(project), remoteChanged: false, error: '' });
    return clone(project);
  };
  const blocked = name => async () => { calls.push({ action: name }); modelActions.push(name); throw new Error(`Model actions are disabled in the isolated progress fixture: ${name}`); };
  const writing = {
    project: state.project, savedProject: state.savedProject, setProject(change) { const before = current.current; replace({ ...before, project: typeof change === 'function' ? change(before.project) : change }); },
    projects: state.savedProject ? [state.savedProject] : [], busy: state.busy, loading: false, dirty, pending: state.project.runs.some(run => ['queued', 'running'].includes(run.status)),
    remoteChanged: state.remoteChanged, error: state.error, generationPlan: null, planLoading: false, planError: '', capabilities: { wordExport: true, pdfExport: true },
    clearError() { if (current.current.error) replace({ ...current.current, error: '' }); },
    async save() { calls.push({ action: 'save' }); return saveMemory(); },
    async review() { calls.push({ action: 'review' }); const project = saveMemory(); project.review = makeReport(project, scenario === 'issues' ? [{ code: 'manual_edit_recheck', severity: 'warning', message: '请核对人工修改后的引用。', sectionId: project.sections[0]?.id }] : []); replace({ ...current.current, project, savedProject: clone(project) }); return { project: clone(project), review: clone(project.review) }; },
    async exportDocument({ format = 'docx', allowIssues = false } = {}) {
      calls.push({ action: 'export', format, allowIssues });
      const project = current.current.project;
      assert(project.review?.version === project.version && project.review?.wordCounts, 'Mock export requires the current reviewed version');
      assert(!project.review.issues.length || allowIssues, 'Mock draft export requires explicit consent');
      const download = { projectId: project.id, version: project.version, format, reviewStatus: project.review.status };
      downloads.push(download); downloadHistory.push(download); return clone(download);
    },
    async reload() { calls.push({ action: 'reload' }); const before = current.current; replace({ ...before, project: clone(before.savedProject || before.project), remoteChanged: false }); return { project: clone(current.current.project) }; },
    async selectProject() { return clone(current.current.project); },
    newProject() { if (dirty) return false; load('blank'); return true; }, refreshList: async () => ({}),
    generateOutline: blocked('generate-outline'), confirmOutline: blocked('confirm-and-generate'), confirmOutlineOnly: blocked('confirm-outline'),
    generateSection: blocked('generate-section'), generateDocument: blocked('generate-document'), generateEdit: blocked('generate-edit'),
    matchCitations: blocked('match-citations'), applyRun: blocked('apply-run'), applyEdit: blocked('apply-edit'), applyDocumentSection: blocked('apply-document'), cancelRun: blocked('cancel-run'),
  };
  const showToast = message => { setToast(message); clearTimeout(toastTimer.current); toastTimer.current = setTimeout(() => setToast(''), 2400); };
  harness = { state, dirty, stage, setStage, load, calls, downloads, downloadHistory, modelActions, requests, replace, workspaceWidth,
    replaceVersion() { const before = current.current, project = clone(before.project); project.version += 1; if (project.review) project.review = makeReport(project); replace({ ...before, project, savedProject: clone(project) }); },
    replaceProject() { const before = current.current, project = clone(before.project); project.id = 'progress-another-project'; replace({ ...before, project, savedProject: clone(project) }); } };
  window.writingProgressFixture = harness;
  return <div className="progress-fixture-shell">
    <div className="progress-fixture-controls" aria-label="隔离样本控制">
      <strong>写作进度带 · 隔离样本</strong>
      <label>状态<select aria-label="样本状态" value={scenario} onChange={event => load(event.target.value)}>{Object.entries(scenarioLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <label>页面<select aria-label="写作页面" value={stage} onChange={event => setStage(event.target.value)}>{[['brief', '准备'], ['outline', '纲要'], ['editor', '正文'], ['projects', '文稿列表']].map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <label>主题<select aria-label="样本主题" value={theme} onChange={event => { setTheme(event.target.value); document.documentElement.dataset.theme = event.target.value; }}>{themes.map(value => <option value={value} key={value}>{value}</option>)}</select></label>
      <label>工作区宽度<select aria-label="工作区宽度" value={workspaceWidth} onChange={event => setWorkspaceWidth(event.target.value)}>{workspaceWidths.map(value => <option value={value} key={value}>{value === 'auto' ? '随窗口' : `${value} px`}</option>)}</select></label>
      <button type="button" onClick={() => load(scenario)}>重置样本</button>
      <small>内存模拟保存、校验与导出 · 不调用模型</small>
    </div>
    <div className="progress-fixture-workspace" style={{ width: workspaceWidth === 'auto' ? '100%' : `${workspaceWidth}px`, maxWidth: '100%', marginInline: 'auto' }}><WritingWorkspace key={generation} stage={stage} onStageChange={setStage} writing={writing}
      documents={[{ id: 'progress-source', title: '写作参考样本' }]} models={[{ id: 'fixture-model', name: '隔离样本模型', model: 'fixture-only' }]}
      onOpenDoc={target => opened.push(target)} onOpenSettings={() => calls.push({ action: 'open-settings' })} onToast={showToast}/></div>
    {toast && <div className="progress-fixture-toast" role="status">{toast}</div>}
  </div>;
}

const band = () => document.querySelector('[data-writing-progress]');
const step = id => document.querySelector(`[data-writing-step="${id}"]`);
const dialog = () => document.querySelector('[role="dialog"]');
const button = (text, container = document) => [...(container?.querySelectorAll('button') || [])].find(item => item.textContent.trim() === text);
const editor = () => document.querySelector('textarea[aria-label="章节正文"]');
const writingRoot = () => document.querySelector('.writing-root');
const outlinePanel = () => document.getElementById('writing-chapter-navigation');
const evidencePanel = () => document.getElementById('writing-evidence-panel');
const outlineToggle = () => document.querySelector('button[aria-label="收起文章目录"],button[aria-label="展开文章目录"]');
const evidenceToggle = () => document.querySelector('.writing-editor-main .writing-reading-panel-controls button[aria-controls="writing-evidence-panel"]');
const chapter = id => [...document.querySelectorAll('.writing-chapter-list button')].find(item => item.querySelector('span')?.textContent === (id === 'progress-first' ? '01' : '02'));
const mainWidth = () => document.querySelector('.writing-editor-main').getBoundingClientRect().width;
const count = () => Number(band()?.dataset.completed);
const readingParagraph = () => document.querySelector('.writing-body-reading .writing-cited-paragraph');
const previewParagraph = () => document.querySelector('.writing-preview-chapter .writing-cited-paragraph');
function paragraphGeometry(paragraph) {
  const range = document.createRange(); range.selectNodeContents(paragraph);
  const lines = new Set([...range.getClientRects()].map(rect => Math.round(rect.top * 10) / 10)).size;
  return { width: paragraph.getBoundingClientRect().width, lines, font: getComputedStyle(paragraph).fontSize, text: paragraph.textContent };
}
async function resizeWorkspace(width) {
  const select = document.querySelector('select[aria-label="工作区宽度"]');
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, width); select.dispatchEvent(new Event('change', { bubbles: true }));
  await until(() => harness.workspaceWidth === width, `workspace width ${width}`);
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); await wait(40);
  return document.querySelector('.progress-fixture-workspace').getBoundingClientRect().width;
}
function assertNoBodyOverflow(label, container) {
  assert(container && container.scrollWidth <= container.clientWidth + 1, `${label} has horizontal text overflow`);
}
function click(element, label) { assert(element && !element.disabled, `Missing or disabled control: ${label}`); element.click(); }
function input(element, value) { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(element, value); element.dispatchEvent(new Event('input', { bubbles: true })); }
function passed(name) { results.push({ name, status: 'passed' }); output.textContent = JSON.stringify({ status: 'running', results }, null, 2); }
async function load(name, stage) { const previous = harness; harness.load(name, stage); await until(() => harness !== previous && harness.state.project.id === (name === 'blank' ? null : 'progress-project') && band(), `load ${name}`); await wait(30); assert(!document.querySelector('.writing-progress-next,[data-writing-next-action]'), `${name} retained the removed next-action row`); }
async function closeDialog() { if (dialog()) { click(dialog().querySelector('button[aria-label="关闭对话框"]'), 'close dialog'); await until(() => !dialog(), 'dialog closes'); } }
function reachable(element) {
  if (!element?.getClientRects().length) return false;
  const rect = element.getBoundingClientRect(), x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
  if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return false;
  const hit = document.elementFromPoint(x, y); return hit === element || element.contains(hit);
}
function assertBandGeometry(label, contentSelector) {
  const writingRoot = document.querySelector('.writing-root'), content = document.querySelector(contentSelector);
  assert(writingRoot && content?.getClientRects().length, `${label} is missing its visible writing content`);
  const rootRect = writingRoot.getBoundingClientRect(), bandRect = band().getBoundingClientRect(), contentRect = content.getBoundingClientRect();
  for (const edge of ['left', 'right', 'bottom']) assert(Math.abs(bandRect[edge] - rootRect[edge]) <= 1, `${label} progress ${edge} edge is not flush with the writing page`);
  assert(contentRect.bottom <= bandRect.top + 1, `${label} writing content overlaps the fixed progress band`);
  assert(bandRect.height > 0 && bandRect.top >= rootRect.top && reachable(step('brief')), `${label} progress band is outside its writing page or cannot receive pointer input`);
  assert(!document.querySelector('.writing-progress-next,[data-writing-next-action]'), `${label} retained the removed next-action row`);
}

async function run() {
  root.render(<Harness/>);
  await until(() => band() && harness, 'progress band mounted');
  if (options.get('manual') === '1') {
    output.dataset.status = 'manual'; output.textContent = 'MANUAL · 隔离样本可切换状态、页面与三个主题；所有保存、校验和导出均为内存模拟。'; return;
  }
  await load('partial');
  assert(count() === 2 && band().dataset.total === '5', 'Partial document did not render actual 2/5 completion');
  assert(step('body').getAttribute('aria-current') === 'step', 'Current progress did not identify body writing');
  for (const id of ['brief', 'outline', 'body']) { click(step(id), `step ${id}`); await until(() => harness.stage === (id === 'body' ? 'editor' : id), `stage ${id}`); assert(count() === 2, 'Viewing a different page changed completed milestones'); }
  assert(calls.length === 0, 'Band page navigation mutated the document or generated text');
  passed('five real milestones remain 2/5 when navigating preparation, outline and body without saves or generation');

  await load('review');
  const beforeNavigation = calls.length;
  click(step('review'), 'review step'); await until(dialog, 'review report dialog');
  assert(dialog().textContent.includes('全文校验') && calls.length === beforeNavigation, 'Review step started a review instead of opening its report');
  await closeDialog();
  click(step('export'), 'export step'); await until(() => dialog()?.textContent.includes('下载 Word'), 'export choices');
  assert(calls.length === beforeNavigation && downloads.length === 0 && count() === 4, 'Export navigation downloaded or falsely completed a document');
  await closeDialog();
  passed('review and export steps open reviewable dialogs without calling save, model generation or export');

  await load('dirty');
  const unsaved = harness.state.project.sections[0].content;
  click(button('保存', document.querySelector('.writing-flow-actions')), 'explicit header save'); await until(() => !harness.dirty && calls.some(item => item.action === 'save'), 'simulated save');
  assert(harness.state.project.sections[0].content === unsaved && calls.every(item => item.action === 'save'), 'Saving lost draft text or started generation');
  passed('explicit header save preserves the current dirty text in memory without starting generation');

  await load('complete');
  assert(count() === 3 && !step('review').disabled, 'Complete body did not expose review with actual 3/5 completion');
  click(step('review'), 'review step'); await until(dialog, 'open review');
  assert(calls.length === 0, 'Review navigation skipped the review dialog');
  click(button('保存并校验全文', dialog()), 'run explicit review');
  await until(() => calls.some(item => item.action === 'review') && count() === 4, 'simulated current review');
  await closeDialog();
  assert(!step('export').disabled, 'Current report did not expose export navigation');
  click(step('export'), 'export step'); await until(() => button('下载 Word', dialog()), 'download options');
  assert(count() === 4 && downloads.length === 0, 'Opening export counted as a completed download');
  click(button('下载 Word', dialog()), 'explicit simulated download');
  await until(() => !dialog() && downloads.length === 1 && count() === 5, 'session export receipt');
  assert(calls.filter(item => item.action === 'export').length === 1, 'One explicit export did not produce exactly one simulated download');
  passed('explicit review then successful simulated download alone completes 5/5 in this session');

  harness.replaceVersion(); await until(() => harness.state.project.version === 4 && count() === 4, 'version invalidates receipt');
  click(step('export'), 'export newer version'); await until(() => button('下载 Word', dialog()), 'new version export');
  click(button('下载 Word', dialog()), 'download newer version'); await until(() => !dialog() && count() === 5, 'new version receipt');
  harness.replaceProject(); await until(() => harness.state.project.id === 'progress-another-project' && count() === 4, 'project invalidates receipt');
  passed('changing the saved version or selected project invalidates the previous export receipt');

  await load('complete');
  click(button('编辑', document.querySelector('.writing-body-view-tabs')), 'switch body to edit');
  await until(() => editor() && !editor().hidden, 'editable body');
  const retained = `${editor().value}\n\n保留这段未保存的人工文字。`;
  input(editor(), retained); await until(() => harness.dirty, 'body becomes dirty');
  click(step('outline'), 'go outline with draft'); await until(() => harness.stage === 'outline', 'outline navigation');
  click(step('body'), 'return body with draft'); await until(() => harness.stage === 'editor' && editor()?.value === retained, 'retained editor text');
  assert(!editor().hidden && calls.length === 0, 'Band navigation lost edit mode, draft text or triggered a mutation');
  click(button('正文', document.querySelector('.writing-body-view-tabs')), 'switch to reading');
  await until(() => document.querySelector('.writing-body-reading'), 'reading body');
  click(step('brief'), 'reading to preparation'); await until(() => harness.stage === 'brief', 'preparation stage');
  click(step('body'), 'return reading body'); await until(() => harness.stage === 'editor' && document.querySelector('.writing-body-reading'), 'retained reading mode');
  assert(editor().hidden && editor().value === retained && calls.length === 0, 'Band navigation lost reading mode or the underlying unsaved editor text');
  passed('band navigation preserves body reading/edit mode and unsaved text without mutating the project');

  await load('review');
  click(document.querySelector('button[aria-label="进入专注模式"]'), 'focus mode');
  await until(() => document.querySelector('.writing-is-focused'), 'focus mode active');
  assert(reachable(step('outline')), 'Focus mode covered or hid the band navigation');
  click(step('outline'), 'focus band outline'); await until(() => harness.stage === 'outline', 'focus navigation outline');
  click(step('body'), 'focus band body'); await until(() => harness.stage === 'editor', 'focus navigation body');
  click(button('全文预览'), 'open full preview'); await until(() => document.querySelector('.writing-preview'), 'full preview open');
  assert(reachable(step('outline')), 'Full preview covered or hid the progress band');
  click(step('outline'), 'preview band navigation'); await until(() => harness.stage === 'outline' && !document.querySelector('.writing-preview'), 'leave preview through band');
  assert(calls.length === 0, 'Focus or preview navigation triggered a model or document mutation');
  passed('focus mode and full preview keep pointer-reachable band navigation visible');

  await load('complete');
  assert(outlinePanel() && evidencePanel() && evidencePanel().hidden, 'Editor panels lack stable controls or the reference pane does not start collapsed');
  assert(outlineToggle()?.getAttribute('aria-controls') === 'writing-chapter-navigation' && outlineToggle().getAttribute('aria-expanded') === 'true', 'Outline collapse control does not describe its visible panel');
  assert(evidenceToggle()?.getAttribute('aria-expanded') === 'false' && reachable(evidenceToggle()), 'Corner evidence toggle is missing, inaccessible or has an inaccurate expanded state');
  click(evidenceToggle(), 'open reference evidence for independent panel checks');
  await until(() => !evidencePanel().hidden && evidencePanel().getClientRects().length, 'reference evidence opens');
  const fullMainWidth = mainWidth();
  const savedPanelProject = clone(harness.state.project), savedPanelVersion = harness.state.project.version;
  click(outlineToggle(), 'collapse outline');
  await until(() => writingRoot().classList.contains('writing-outline-collapsed') && outlinePanel().hidden, 'outline completely collapses');
  assert(!outlinePanel().getClientRects().length, 'Collapsed outline still occupies reading space');
  assert(mainWidth() > fullMainWidth && outlineToggle().getAttribute('aria-expanded') === 'false', 'Collapsed outline did not release reading space or update its expanded state');
  click(outlineToggle(), 'reopen outline for chapter navigation');
  await until(() => !outlinePanel().hidden && outlinePanel().getClientRects().length, 'outline reopens for chapter navigation');
  assert(reachable(chapter('progress-first')) && reachable(chapter('progress-second')), 'Chapter navigation is not reachable in the reopened outline');
  click(chapter('progress-second'), 'second numbered chapter');
  await until(() => harness.state.project.activeSectionId === 'progress-second' && document.querySelector('.writing-chapter-title-input')?.textContent === '再展开回答', 'outline selects second chapter');
  assert(chapter('progress-second').classList.contains('active') && editor().readOnly, 'Outline did not identify the selected locked chapter');
  click(chapter('progress-first'), 'first numbered chapter');
  await until(() => harness.state.project.activeSectionId === 'progress-first', 'outline returns to first chapter');
  click(outlineToggle(), 'collapse outline after chapter navigation');
  await until(() => outlinePanel().hidden, 'outline remains collapsed for independent reference checks');
  const collapsedMainWidth = mainWidth();
  click(evidenceToggle(), 'collapse reference evidence');
  await until(() => evidencePanel().hidden && writingRoot().classList.contains('writing-evidence-collapsed'), 'evidence completely collapses');
  assert(!evidencePanel().getClientRects().length && mainWidth() > collapsedMainWidth, 'Hidden evidence pane still occupies space or the manuscript did not expand');
  assert(outlineToggle().getAttribute('aria-expanded') === 'false' && evidenceToggle().getAttribute('aria-expanded') === 'false', 'Collapsing evidence changed outline preference or left an inaccurate expanded state');
  click(evidenceToggle(), 'reopen evidence from corner');
  await until(() => !evidencePanel().hidden && evidencePanel().getClientRects().length, 'corner reopens evidence');
  assert(writingRoot().classList.contains('writing-outline-collapsed') && evidenceToggle().getAttribute('aria-expanded') === 'true', 'Reopening evidence changed the independent outline collapse state');
  click(evidenceToggle(), 'hide evidence again');
  await until(() => evidencePanel().hidden, 'evidence hides again');
  click(outlineToggle(), 'expand outline independently');
  await until(() => !writingRoot().classList.contains('writing-outline-collapsed'), 'outline expands');
  assert(evidencePanel().hidden && evidenceToggle().getAttribute('aria-expanded') === 'false', 'Expanding outline unexpectedly reopened the evidence pane');
  assert(fingerprint(harness.state.project) === fingerprint(savedPanelProject) && harness.state.project.version === savedPanelVersion && !harness.dirty && calls.length === 0, 'Independent panel toggles or numbered navigation changed saved content, locks, version or dirty state');
  passed('outline and evidence completely hide and release space; reopening the outline preserves chapter navigation and locks; independent toggles expose accurate accessible controls without saving');

  await load('complete');
  click(evidenceToggle(), 'open evidence before draft panel checks');
  await until(() => !evidencePanel().hidden, 'evidence fixture visible before draft panel checks');
  click(button('编辑', document.querySelector('.writing-body-view-tabs')), 'edit before collapsing panels');
  await until(() => editor() && !editor().hidden, 'native editor before panel collapse');
  const panelDraft = '保留😀这段未保存正文。[E1]\n\n人工补充。', nativeEditor = editor();
  input(nativeEditor, panelDraft); await until(() => harness.dirty && harness.state.project.sections[0].content === panelDraft, 'dirty draft before collapse');
  nativeEditor.focus(); nativeEditor.setSelectionRange(2, 4); document.dispatchEvent(new Event('selectionchange', { bubbles: true })); nativeEditor.dispatchEvent(new KeyboardEvent('keyup', { key: 'Shift', bubbles: true }));
  await until(() => !button('润色').disabled, 'UTF-16 selection before collapse');
  const panelDraftState = fingerprint(harness.state.project), draftVersion = harness.state.project.version;
  click(outlineToggle(), 'collapse outline with selected draft');
  click(evidenceToggle(), 'collapse evidence with selected draft');
  await until(() => writingRoot().classList.contains('writing-outline-collapsed') && evidencePanel().hidden, 'both manual collapse preferences');
  assert(editor() === nativeEditor && editor().value === panelDraft && !editor().hidden && harness.dirty, 'Collapsing panels remounted, hid, saved or lost the native draft editor');
  assert(editor().selectionStart === 2 && editor().selectionEnd === 4 && !button('润色').disabled, 'Collapsing panels lost the exact UTF-16 selection or its edit action');
  assert(harness.state.project.sections[1].locked && !harness.state.project.sections[0].locked && fingerprint(harness.state.project) === panelDraftState && harness.state.project.version === draftVersion && calls.length === 0, 'Collapsing panels changed locks, text, project version or triggered a mutation');
  click(button('润色'), 'inspect preserved selection');
  await until(() => dialog()?.getAttribute('aria-label') === '润色选中的文字', 'preserved selection dialog');
  assert(dialog().querySelector('.writing-selection-preview p')?.textContent === '😀', 'Collapsed layout changed the selected UTF-16 text');
  click(button('返回原稿', dialog()), 'cancel preserved selection dialog'); await until(() => !dialog(), 'selection dialog returns to draft');
  passed('panel toggles preserve the same native editor, unsaved draft, UTF-16 emoji selection, chapter locks and project version without calling save or models');

  click(document.querySelector('button[aria-label="进入专注模式"]'), 'focus with manual collapse preferences');
  await until(() => writingRoot().classList.contains('writing-is-focused'), 'focused editor hides side panels');
  assert(!outlinePanel().getClientRects().length && !evidencePanel().getClientRects().length, 'Focus mode left the outline or evidence pane visible');
  assert(editor() === nativeEditor && editor().value === panelDraft && harness.dirty, 'Focus mode remounted or saved the dirty editor');
  click(document.querySelector('button[aria-label="退出专注模式"]'), 'leave focus preserving manual panel preferences');
  await until(() => !writingRoot().classList.contains('writing-is-focused') && outlinePanel().hidden && !outlinePanel().getClientRects().length, 'focus restores manual outline collapse');
  assert(writingRoot().classList.contains('writing-outline-collapsed') && evidencePanel().hidden && evidenceToggle().getAttribute('aria-expanded') === 'false', 'Leaving focus lost manual outline/evidence collapse preferences');
  for (const theme of themes) {
    const select = document.querySelector('select[aria-label="样本主题"]');
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, theme); select.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => document.documentElement.dataset.theme === theme, `collapsed panels in ${theme}`); await wait(30);
    assert(writingRoot().classList.contains('writing-outline-collapsed') && outlinePanel().hidden && !outlinePanel().getClientRects().length && evidencePanel().hidden && !evidencePanel().getClientRects().length, `${theme} theme lost independent panel collapse states`);
    assert(reachable(outlineToggle()) && reachable(evidenceToggle()), `${theme} theme made the outline or evidence restore button unreachable`);
    assert(editor() === nativeEditor && editor().value === panelDraft && harness.dirty && fingerprint(harness.state.project) === panelDraftState, `${theme} theme changed the draft or locked chapters`);
  }
  const restoredTheme = document.querySelector('select[aria-label="样本主题"]');
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(restoredTheme, initialTheme); restoredTheme.dispatchEvent(new Event('change', { bubbles: true }));
  await until(() => document.documentElement.dataset.theme === initialTheme, 'restore initial theme');
  assert(calls.length === 0, 'Focus mode or theme changes triggered a model or document mutation');
  passed('focus temporarily hides both panels and restores manual choices; jade, glass and civic retain fully hidden side panels and the dirty editor');

  await load('complete');
  click(evidenceToggle(), 'open evidence before citation reveal checks');
  await until(() => !evidencePanel().hidden, 'evidence fixture visible before citation reveal checks');
  const validCitation = () => document.querySelector('.writing-body-reading button[data-evidence-id="E1"]');
  await until(validCitation, 'registered inline citation');
  assert(validCitation().getAttribute('aria-label').includes('已关联原文') && validCitation().dataset.bindingStatus === 'supported', 'Panel reveal fixture does not have a valid current registered citation');
  const citationState = fingerprint(harness.state.project);
  click(outlineToggle(), 'retain outline collapse during citation reveal');
  click(evidenceToggle(), 'hide evidence before citation reveal');
  await until(() => evidencePanel().hidden, 'hidden citation evidence');
  click(validCitation(), 'inline citation reveals hidden evidence');
  await until(() => !evidencePanel().hidden && document.querySelector('.writing-chapter-selected-citation [data-evidence-id="E1"]'), 'citation reopens exact evidence');
  assert(evidenceToggle().getAttribute('aria-expanded') === 'true' && writingRoot().classList.contains('writing-outline-collapsed'), 'Citation reveal failed to update evidence state or expanded the independent outline');
  assert(document.querySelector('.writing-chapter-selected-citation blockquote')?.textContent === referenceQuote, 'Citation reveal selected a different source excerpt');
  click(document.querySelector('button[aria-label="进入专注模式"]'), 'focus before citation reveal');
  await until(() => writingRoot().classList.contains('writing-is-focused') && !evidencePanel().getClientRects().length, 'focused citation reading');
  click(validCitation(), 'inline citation leaves focus and reveals evidence');
  await until(() => !writingRoot().classList.contains('writing-is-focused') && !evidencePanel().hidden && evidencePanel().getClientRects().length, 'citation exits focus with visible evidence');
  assert(writingRoot().classList.contains('writing-outline-collapsed') && fingerprint(harness.state.project) === citationState && !harness.dirty && calls.length === 0, 'Citation reveal changed the outline preference, draft or saved project');
  passed('a valid inline citation reopens the exact hidden evidence excerpt and exits focus while preserving the independent outline collapse and manuscript');

  for (const [name, completed, available] of [
    ['blank', 0, ['brief']], ['stale', 3, ['brief', 'outline', 'body', 'review', 'export']],
    ['pending', 2, ['brief', 'outline', 'body']], ['remote', 3, ['brief', 'outline', 'body']],
  ]) {
    await load(name);
    assert(count() === completed, `${name} scenario has an inaccurate completion count`);
    for (const id of ['brief', 'outline', 'body', 'review', 'export']) assert(step(id).disabled !== available.includes(id), `${name} scenario has incorrect ${id} availability`);
    assert(calls.length === 0, `${name} state invoked a document action without explicit interaction`);
  }
  passed('blank, stale, running and remote-changed states keep actual completion counts and appropriate step availability with no next-action row');

  for (const [stage, contentSelector, scrollerSelector] of [
    ['brief', '.writing-flow-content', '.writing-flow-content'],
    ['outline', '.writing-flow-content', '.writing-flow-content'],
    ['editor', '.writing-editor-view', '.writing-editor-scroll'],
  ]) {
    await load('complete', stage);
    assertBandGeometry(stage, contentSelector);
    const scroller = document.querySelector(scrollerSelector), before = band().getBoundingClientRect();
    scroller.scrollTop = scroller.scrollHeight; await wait(30);
    assertBandGeometry(`${stage} after scrolling`, contentSelector);
    assert(Math.abs(band().getBoundingClientRect().top - before.top) <= 1, `${stage} scrolling moved the fixed progress band`);
  }
  click(button('全文预览', document.querySelector('.writing-flow-actions')), 'preview geometry');
  await until(() => document.querySelector('.writing-preview'), 'preview geometry content'); await wait(30);
  assertBandGeometry('preview', '.writing-preview');
  document.querySelector('.writing-preview-article').scrollTop = document.querySelector('.writing-preview-article').scrollHeight; await wait(30);
  assertBandGeometry('preview after scrolling', '.writing-preview');
  assert(calls.length === 0 && count() === 3, 'Geometry navigation changed the project or its completion count');
  passed('preparation, outline, body and preview place the band flush left, right and bottom without content overlap, including after scrolling');

  await load('complete');
  click(evidenceToggle(), 'open evidence before fluid width checks');
  await until(() => !evidencePanel().hidden, 'evidence fixture visible before fluid width checks');
  const fluidContent = '保留😀这段人工文字，窗口变宽时每行容纳更多正文，窗口变窄时文字自然换行，阅读字号保持稳定。'.repeat(28);
  const fluidProject = clone(harness.state.project); fluidProject.sections[0].content = fluidContent;
  harness.replace({ ...harness.state, project: fluidProject });
  await until(() => harness.dirty && readingParagraph()?.textContent === fluidContent, 'long dirty paragraph for fluid width');
  click(outlineToggle(), 'collapse outline for fluid body'); click(evidenceToggle(), 'collapse evidence for fluid body');
  await until(() => writingRoot().classList.contains('writing-outline-collapsed') && evidencePanel().hidden, 'wide reading area without side panels');
  const fluidState = fingerprint(harness.state.project), fluidEditor = editor();
  for (const theme of themes) {
    const select = document.querySelector('select[aria-label="样本主题"]');
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, theme); select.dispatchEvent(new Event('change', { bubbles: true }));
    await until(() => document.documentElement.dataset.theme === theme, `fluid reading theme ${theme}`);
    const narrowWorkspace = await resizeWorkspace('760'), narrow = paragraphGeometry(readingParagraph());
    assertNoBodyOverflow(`${theme} narrow reading`, document.querySelector('.writing-editor-scroll'));
    const wideWorkspace = await resizeWorkspace('1360'), wide = paragraphGeometry(readingParagraph());
    assert(wideWorkspace > narrowWorkspace + 150, 'The fixture viewport is too narrow to exercise distinct desktop workspace widths');
    assert(wide.width > narrow.width + 120 && wide.lines < narrow.lines, `${theme} reading retained a fixed line width instead of fitting the available manuscript space`);
    assert(wide.font === narrow.font && wide.text === fluidContent && narrow.text === fluidContent, `${theme} width change altered the reading font size or text`);
    assertNoBodyOverflow(`${theme} wide reading`, document.querySelector('.writing-editor-scroll'));
    assert(editor() === fluidEditor && editor().hidden && editor().value === fluidContent && harness.dirty && fingerprint(harness.state.project) === fluidState && calls.length === 0, `${theme} reading resize remounted, saved or changed the draft`);
  }
  passed('jade, glass and civic reading paragraphs gain characters per line as the real workspace widens, without changing font size, draft content or horizontal overflow');

  const restoreFluidTheme = document.querySelector('select[aria-label="样本主题"]');
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(restoreFluidTheme, initialTheme); restoreFluidTheme.dispatchEvent(new Event('change', { bubbles: true }));
  await until(() => document.documentElement.dataset.theme === initialTheme, 'restore fluid test theme');
  click(button('编辑', document.querySelector('.writing-body-view-tabs')), 'edit long responsive draft');
  await until(() => editor() === fluidEditor && !editor().hidden, 'same native editor for responsive draft');
  fluidEditor.focus(); fluidEditor.setSelectionRange(2, 4); document.dispatchEvent(new Event('selectionchange', { bubbles: true })); fluidEditor.dispatchEvent(new KeyboardEvent('keyup', { key: 'Shift', bubbles: true }));
  await until(() => !button('润色').disabled, 'responsive draft selection');
  await resizeWorkspace('760');
  const narrowEditorWidth = fluidEditor.getBoundingClientRect().width, narrowEditorHeight = fluidEditor.getBoundingClientRect().height, editorFont = getComputedStyle(fluidEditor).fontSize;
  assertNoBodyOverflow('narrow native editor', fluidEditor);
  await resizeWorkspace('1360');
  await until(() => fluidEditor.getBoundingClientRect().height < narrowEditorHeight - 30, 'ResizeObserver reflows editor after widening');
  assert(fluidEditor.getBoundingClientRect().width > narrowEditorWidth + 120 && getComputedStyle(fluidEditor).fontSize === editorFont, 'Native editor did not gain usable line width at the same font size');
  assert(fluidEditor.scrollHeight <= fluidEditor.clientHeight + 2, 'Widened native editor retained a clipped content height');
  assertNoBodyOverflow('wide native editor', fluidEditor);
  const wideEditorHeight = fluidEditor.getBoundingClientRect().height;
  await resizeWorkspace('760');
  await until(() => fluidEditor.getBoundingClientRect().height > wideEditorHeight + 30, 'ResizeObserver reflows editor after narrowing');
  assert(fluidEditor.scrollHeight <= fluidEditor.clientHeight + 2 && fluidEditor.getBoundingClientRect().height >= narrowEditorHeight - 2, 'Narrowed native editor clipped text or kept its wide-layout height');
  assert(editor() === fluidEditor && fluidEditor.value === fluidContent && fluidEditor.selectionStart === 2 && fluidEditor.selectionEnd === 4 && !button('润色').disabled, 'Workspace resize lost the mounted native editor, exact UTF-16 emoji selection or edit action');
  assert(harness.dirty && fingerprint(harness.state.project) === fluidState && harness.state.project.sections[1].locked && calls.length === 0, 'Workspace resize changed the unsaved draft, locked chapter or document actions');
  passed('the same native editor expands and shrinks its height when its container changes width, preserving text, UTF-16 selection, fixed font size and unsaved/locked state');

  click(button('全文预览', document.querySelector('.writing-flow-actions')), 'preview responsive draft');
  await until(() => previewParagraph()?.textContent === fluidContent, 'responsive full preview');
  await resizeWorkspace('760'); const narrowPreview = paragraphGeometry(previewParagraph());
  assertNoBodyOverflow('narrow full preview', document.querySelector('.writing-preview-article'));
  await resizeWorkspace('1360'); const widePreview = paragraphGeometry(previewParagraph());
  assert(widePreview.width > narrowPreview.width + 100 && widePreview.lines < narrowPreview.lines && widePreview.font === narrowPreview.font, 'Full preview did not fit more text per line at the same font size in a wider workspace');
  assert(widePreview.text === fluidContent && editor() === fluidEditor && fluidEditor.value === fluidContent && fingerprint(harness.state.project) === fluidState && harness.dirty && calls.length === 0, 'Preview resize altered, saved or remounted the underlying unsaved draft');
  assertNoBodyOverflow('wide full preview', document.querySelector('.writing-preview-article'));
  await resizeWorkspace(initialWorkspaceWidth);
  passed('full preview line width follows the available workspace and keeps the same font, exact draft text and mounted editor without horizontal text overflow');

  assert(modelActions.length === 0, 'Progress band triggered a model action');
  assert(requests.length === 0, `Isolated progress test made ${requests.length} API requests`);
  passed('the complete isolated regression run made zero API or model calls');

  await load('complete');
  output.dataset.status = 'passed'; output.textContent = JSON.stringify({ status: 'passed', results, apiRequests: requests.length, modelCalls: modelActions.length, simulatedDownloads: downloadHistory.length }, null, 2);
}

run().catch(error => { output.dataset.status = 'failed'; output.textContent = JSON.stringify({ status: 'failed', message: error.message, stack: error.stack, results, requests, calls }, null, 2); console.error(error); });
