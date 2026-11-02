import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingWorkspace from '../src/Writing';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';

const options = new URLSearchParams(window.location.search);
document.documentElement.dataset.theme = ['jade', 'glass', 'civic'].includes(options.get('theme')) ? options.get('theme') : 'jade';
const output = document.getElementById('citation-test-result'), root = createRoot(document.getElementById('citation-test-root'));
const results = [], opened = [], requests = [], modelActions = [];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
async function until(predicate, label) { for (let count = 0; count < 200; count += 1) { if (predicate()) return; await wait(20); } throw new Error(`Timed out: ${label}`); }
const evidence = (id, sourceId = 'source-a', extra = {}) => {
  const quote = extra.quote ?? `${sourceId} 中精确登记的原文摘录。`;
  return { id, sourceId, revisionId: `rev-${sourceId}`, blockId: `block-${sourceId}`, title: `${sourceId} 的冻结材料`, quote, start: 0, end: Array.from(quote).length, locator: { paragraph: 2 }, ...extra };
};
const pendingText = '已关联原文但本次自动核对未完成的论述。';
const pendingQuote = '待核对来源的精确原文摘录，保留事实与语境。';
const originalContent = '现有😀人工草稿引用第一份材料。[E1] 同段第二处取自同篇另一片段。[E6] 相同句子。[E1] 相同句子。[E1] 本段另有尚不充分的推断。\n\n长文原文材料。[E2]\n\n' + pendingText + '[E7]\n\n未知引用。[E404] 重名引用。[E3] 范围外引用。[E4] 不完整引用。[E5]';
const sentenceChecks = [
  ['现有😀人工草稿引用第一份材料。', 'E1', 0],
  ['同段第二处取自同篇另一片段。', 'E6', 0],
  ['相同句子。', 'E1', 1],
  ['相同句子。', 'E1', 2],
  ['长文原文材料。', 'E2', 0],
];
const markerOccurrences = id => [...originalContent.matchAll(/\[E\d+\]/g)].filter(match => match[0] === `[${id}]`);
const assessedBindings = { version: 1, content: originalContent, items: sentenceChecks.map(([text, evidenceId, occurrence], index) => {
  const markerStart = markerOccurrences(evidenceId)[occurrence].index, start = originalContent.lastIndexOf(text, markerStart);
  return { id: `checked-${index}`, evidenceId, markerStart, start, end: start + text.length, text, status: 'supported', reason: '材料支持本句。' };
}) };
const partialText = '本段另有尚不充分的推断。';
assessedBindings.items.push({ id: 'partial-note', evidenceId: 'E6', status: 'partial', text: partialText, start: originalContent.indexOf(partialText), end: originalContent.indexOf(partialText) + partialText.length });
assessedBindings.items.push({ id: 'unreviewed-note', evidenceId: 'E7', status: 'unreviewed', supportBasis: 'none', text: pendingText, start: originalContent.indexOf(pendingText), end: originalContent.indexOf(pendingText) + pendingText.length, markerStart: originalContent.indexOf('[E7]'), reason: '本次自动核对未完成，尚未确认原文支持。' });
const initialProject = {
  id: 'citation-project', title: '材料引用隔离样本', topic: '材料引用', version: 7, mode: 'strict', workflow: 'chapter', activeSectionId: 'chapter-a',
  sourceIds: ['source-a', 'source-b'], sources: [{ sourceId: 'source-a', revisionId: 'rev-source-a', title: '冻结材料 A' }, { sourceId: 'source-b', revisionId: 'rev-source-b', title: '冻结材料 B' }],
  brief: {}, materialSelection: {}, runs: [], outlineConfirmed: true,
  sections: [
    { id: 'chapter-a', title: '第一章', goal: '核对原文引用', mode: 'inherit', words: 500, version: 2, content: originalContent, citationBindings: assessedBindings, locked: false, manual: true,
      evidence: [evidence('E1', 'source-a', { quote: '<img src=x onerror="window.citationXss=true">原文作为文字显示。' }), evidence('E2', 'source-b', { quote: '这是保留完整上下文的长摘录，不能被截掉。\n'.repeat(160) }), evidence('E3'), evidence('E3', 'source-b'), evidence('E4', 'excluded'), evidence('E5', 'source-a', { blockId: '' }), evidence('E6', 'source-a', { blockId: 'block-source-a-second', quote: '同篇资料另一个精确片段，保留独立定位。' }), evidence('E7', 'source-a', { blockId: 'block-source-a-pending', quote: pendingQuote })] },
    { id: 'chapter-b', title: '第二章', goal: '相同编号对应不同原文', mode: 'extended', words: 500, version: 3, content: '第二章独立材料。[E1]', locked: true, manual: true, evidence: [evidence('E1', 'source-b', { quote: '第二章独有的来源摘录。' })] },
  ],
};
let state, mountedReady = false;
// Fail closed: this test only mounts controlled components. No real service,
// model request, export download or local/remote document mutation is possible.
window.fetch = async (url, init = {}) => { requests.push({ url: String(url), method: init.method || 'GET' }); throw new Error(`Unexpected fixture request: ${url}`); };
function Harness() {
  const [project, setProject] = useState(() => structuredClone(initialProject));
  // Warm Vite navigation can expose DOM before StrictMode replays the child
  // workspace's initial effects, which deliberately clear old citation state.
  // Start interaction only after this parent effect observes that mount phase.
  useEffect(() => { mountedReady = true; return () => { mountedReady = false; }; }, []);
  state = { project, setProject };
  const blocked = name => () => { modelActions.push(name); throw new Error(`Unexpected fixture action: ${name}`); };
  const writing = { project, setProject, projects: [], busy: '', loading: false, dirty: project.sections[0].content !== originalContent || project.sections[0].locked, error: null, pending: false, remoteChanged: false,
    generationPlan: null, planLoading: false, planError: null, clearError() {}, matchCitations: blocked('match-citations'), save: blocked('save'), review: blocked('review'), generateOutline: blocked('outline'), generateSection: blocked('section'), generateEdit: blocked('edit'), generateDocument: blocked('document'), exportDocument: blocked('export') };
  return <div style={{ height: 'calc(100vh - 70px)' }}><WritingWorkspace stage="editor" onStageChange={() => {}} writing={writing} documents={initialProject.sources.map(source => ({ id: source.sourceId, title: source.title }))} onOpenDoc={target => opened.push(target)}/></div>;
}
const popup = () => document.querySelector('.writing-citation-popover');
const reading = () => document.querySelector('.writing-body-reading');
const editor = () => document.querySelector('textarea[aria-label="章节正文"]');
const icon = (id, container = reading()) => container?.querySelector(`button[data-evidence-id="${id}"]`);
const button = (text, container = document) => [...container.querySelectorAll('button')].find(item => item.textContent.trim() === text);
const chapterButton = title => [...document.querySelectorAll('.writing-chapter-list button')].find(item => item.querySelector('strong')?.textContent === title);
function clickCitation(id) { const element = icon(id); element.scrollIntoView({ block: 'center' }); element.click(); }
function openCitationPanel(id) { const element = icon(id); element.scrollIntoView({ block: 'center' }); element.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })); }
const hover = element => element.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
const leave = (element, next = null) => element.dispatchEvent(new MouseEvent('mouseout', { bubbles: true, relatedTarget: next }));
const outside = () => document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
function input(element, value) { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(element, value); element.dispatchEvent(new Event('input', { bubbles: true })); }
function passed(name) { results.push({ name, status: 'passed' }); output.textContent = JSON.stringify({ status: 'running', results }, null, 2); }

async function run() {
  root.render(<React.StrictMode><Harness/></React.StrictMode>);
  await until(() => mountedReady && icon('E1'), 'mounted citation workspace and initial effects');
  assert(editor().hidden && !editor().getClientRects().length && editor().value === originalContent, 'Reading surface changed or removed the native draft editor or left it visible');
  assert(!reading().textContent.includes('[E1]'), 'Raw citation protocol leaked into the reading text');
  assert(button('润色').disabled && button('改写').disabled, 'Reading view allowed an old hidden selection to start AI editing');
  assert(reading().querySelector('.writing-citation-binding-issues')?.textContent.includes('材料仅支持部分论述'), 'Partial support diagnosis was hidden');
  passed('existing text opens as icon reading while native draft editor, edit fences and partial-support notice remain intact');

  assert([...reading().querySelectorAll('[data-evidence-id="E1"], [data-evidence-id="E6"]')].every(item => item.textContent.trim() === '1'), 'Same source excerpts do not share visible bibliography number 1');
  assert(icon('E2').textContent.trim() === '2', 'Second source did not receive first-appearance number 2');
  const chapterScroller = document.querySelector('.writing-editor-scroll'), materialPane = document.querySelector('.writing-evidence-panel');
  icon('E6').scrollIntoView({ block: 'center' }); const chapterPosition = chapterScroller.scrollTop;
  icon('E6').click(); await until(() => document.querySelector('.writing-chapter-selected-citation [data-evidence-id="E6"]'), 'chapter exact source card');
  assert([...reading().querySelectorAll('mark')].map(item => item.textContent).join('') === '同段第二处取自同篇另一片段。', 'Inline marker did not highlight its own sentence');
  assert(document.querySelector('.writing-chapter-selected-citation blockquote')?.textContent === '同篇资料另一个精确片段，保留独立定位。', 'Chapter sidebar resolved the article instead of exact excerpt');
  assert(document.querySelector('.writing-chapter-selected-citation .writing-preview-source-heading')?.textContent.includes('[1]'), 'Chapter sidebar number does not match icon');
  assert(!popup(), 'Click left an overlay covering the sentence or sidebar');
  hover(icon('E6')); icon('E6').focus(); await wait(50); assert(!popup(), 'Pointer/focus reopened the dismissed popup after selecting a citation');
  assert(chapterScroller.scrollTop === chapterPosition, 'Material selection moved the manuscript pane');
  const citedAgain = [...reading().querySelectorAll('[data-evidence-id="E1"]')].at(-1); citedAgain.click(); await wait(40);
  const highlighted = reading().querySelector('mark');
  assert(reading().querySelectorAll('mark').length === 1 && highlighted?.textContent === '相同句子。' && highlighted.nextElementSibling === citedAgain, 'Repeated text highlighted the first matching sentence instead of clicked occurrence');
  outside(); await until(() => !popup(), 'numbered citation closes');
  passed('numbered icons retain multiple excerpts, highlight the clicked sentence occurrence and select its exact chapter-side excerpt without moving the manuscript');

  hover(icon('E1')); await until(popup, 'hover preview');
  assert(popup().textContent.includes('原文作为文字显示') && !popup().querySelector('img') && !window.citationXss, 'Source quote was interpreted as HTML');
  assert(popup().textContent.includes('写作时选定的原文') && popup().textContent.includes('第 2 段') && !popup().textContent.includes('rev-source-a'), 'Quote lost its pinned version or locator');
  const hoverPanel = popup(); leave(icon('E1'), hoverPanel); hover(hoverPanel); await wait(240);
  assert(popup() === hoverPanel, 'Hover preview disappeared while moving into its content');
  leave(hoverPanel); await until(() => !popup(), 'hover exit closes preview');
  passed('hover preview has exact escaped quote, version and locator and accepts pointer movement into the panel');

  clickCitation('E1'); await until(() => document.querySelector('.writing-chapter-selected-citation [data-evidence-id="E1"]'), 'click selects source');
  assert(!popup(), 'Click source selection opened an obstructing popup');
  button('查看写作时选定的原文', document.querySelector('.writing-chapter-selected-citation')).click();
  assert(JSON.stringify(opened[0]) === JSON.stringify({ id: 'source-a', revisionId: 'rev-source-a', matchBlockId: 'block-source-a' }), 'Original action guessed or lost the pinned block');
  openCitationPanel('E1'); await until(() => popup()?.textContent.includes('已固定'), 'keyboard pins');
  leave(icon('E1')); await wait(240); assert(popup(), 'Pinned source closed on pointer leave');
  outside(); await until(() => !popup(), 'outside click closes');
  passed('click shows an unobstructed chapter excerpt, original navigation uses exact frozen source and keyboard panels pin and dismiss');

  const pendingIcon = icon('E7');
  assert(pendingIcon?.textContent.trim() === '1' && pendingIcon.dataset.bindingStatus === 'unreviewed', 'Located but unreviewed citation lost its shared visible source number or distinct review state');
  assert(pendingIcon.getAttribute('aria-label').includes('原文已定位，自动核对未完成'), 'Pending citation accessible text incorrectly claims support or loses the incomplete-review explanation');
  clickCitation('E7'); await until(() => document.querySelector('.writing-chapter-selected-citation [data-evidence-id="E7"]'), 'pending chapter source card');
  const pendingCard = document.querySelector('.writing-chapter-selected-citation');
  assert(pendingCard.querySelector('blockquote')?.textContent === pendingQuote && pendingCard.textContent.includes('自动核对未完成'), 'Pending citation did not select its exact original excerpt and incomplete-review state');
  assert(!reading().querySelector('mark'), 'Selecting an unreviewed citation created a confirmed-support highlight');
  openCitationPanel('E7'); await until(() => popup()?.querySelector('blockquote')?.textContent === pendingQuote, 'pending original popover');
  assert(popup().dataset.citationStatus === 'registered' && popup().textContent.includes('原文已定位，自动核对未完成') && popup().textContent.includes('尚未确认它是否支持对应论述'), 'Pending original popover confused source registration with confirmed support');
  assert(!reading().querySelector('mark'), 'Opening a pending original popover created a confirmed-support highlight');
  button('查看写作时选定的原文', popup()).click();
  assert(JSON.stringify(opened.at(-1)) === JSON.stringify({ id: 'source-a', revisionId: 'rev-source-a', matchBlockId: 'block-source-a-pending' }), 'Pending original action lost its frozen source and exact block');
  await until(() => !popup(), 'pending original navigation closes popover');
  clickCitation('E6'); await until(() => reading().querySelector('mark')?.textContent === '同段第二处取自同篇另一片段。', 'confirmed citation remains precisely highlighted');
  passed('unreviewed citations stay numbered and clickable with exact source navigation and incomplete-review wording, never support highlights; confirmed citations remain unaffected');

  icon('E1').focus(); await until(popup, 'keyboard focus preview');
  icon('E1').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
  await until(() => popup()?.contains(document.activeElement), 'keyboard enters popover');
  assert(getComputedStyle(popup()).visibility === 'visible', 'Promoting the same focus preview cleared its dialog placement');
  icon('E1').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })); await wait(40);
  assert(popup()?.contains(document.activeElement) && getComputedStyle(popup()).visibility === 'visible', 'Repeated keyboard entry hid the already pinned dialog');
  document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await until(() => !popup(), 'escape closes'); await wait(50);
  assert(document.activeElement === icon('E1') && !popup(), 'Escape did not restore trigger focus or reopened the popup');
  document.activeElement.blur();
  passed('keyboard focus and ArrowDown enter source panel; Escape closes and restores focus without reopening');

  for (const [id, status, text] of [['E404', 'unknown', '本章没有找到这个引用编号对应的来源'], ['E3', 'ambiguous', '无法唯一确定'], ['E4', 'out_of_scope', '不在当前项目确认'], ['E5', 'invalid_anchor', '缺少完整的原文位置']]) {
    openCitationPanel(id); await until(() => popup()?.dataset.citationStatus === status, id);
    assert(popup().textContent.includes(text), `Missing truthful ${status} explanation`);
    if (['unknown', 'ambiguous'].includes(status)) assert(!popup().querySelector('blockquote') && !button('查看写作时选定的原文', popup()), 'Unresolved citation guessed a source');
    outside(); await until(() => !popup(), `${id} closes`);
  }
  passed('unknown, duplicate, out-of-scope and incomplete citations retain distinct truthful states without guessed sources');

  openCitationPanel('E2'); await until(() => popup()?.querySelector('blockquote')?.textContent.length > 2000, 'long source');
  const bounds = popup().getBoundingClientRect(), scrollbox = popup().querySelector('.writing-citation-popover-content');
  assert(bounds.left >= 0 && bounds.right <= innerWidth && bounds.top >= 0 && bounds.bottom <= innerHeight, 'Long popup overflowed the viewport');
  assert(scrollbox.scrollHeight > scrollbox.clientHeight, 'Long quote was truncated instead of scrollable');
  scrollbox.scrollTop = scrollbox.scrollHeight; assert(scrollbox.scrollTop > 0, 'Long source was not scrollable');
  chapterButton('第二章').click(); await until(() => reading()?.textContent.includes('第二章独立材料'), 'switch chapter');
  assert(!popup(), 'Old chapter popup survived navigation'); openCitationPanel('E1'); await until(() => popup()?.textContent.includes('第二章独有'), 'chapter-local E1');
  assert(popup().textContent.includes('写作时选定的原文') && !popup().textContent.includes('原文作为文字显示'), 'E1 resolved from another chapter');
  assert(state.project.sections[1].locked, 'Reading changed chapter lock');
  assert(!reading().querySelector('mark') && icon('E1').dataset.bindingStatus === 'unmatched' && reading().textContent.includes('尚未逐句核对'), 'Legacy paragraph-end citation was falsely treated as a supported last sentence');
  document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'civic' ? 'jade' : 'civic'; await until(() => !popup(), 'theme dismisses source');
  passed('long sources scroll within viewport; chapter and theme changes fence popovers and chapter-local E1');

  chapterButton('第一章').click(); await until(() => reading()?.textContent.includes('现有'), 'return chapter for full inline preview');
  button('全文预览').click(); await until(() => document.querySelector('.writing-preview'), 'full inline preview');
  const inlinePreviewIcon = document.querySelector('.writing-preview-chapter [data-evidence-id="E6"]'); inlinePreviewIcon.scrollIntoView({ block: 'center' }); inlinePreviewIcon.click();
  await until(() => document.querySelector('.writing-preview-source-card.active[data-evidence-id="E6"]'), 'full preview exact excerpt');
  assert([...document.querySelectorAll('.writing-preview-chapter mark')].map(item => item.textContent).join('') === '同段第二处取自同篇另一片段。', 'Whole preview highlighted the paragraph instead of cited sentence');
  const fullMaterialCard = document.querySelector('.writing-preview-source-card.active[data-evidence-id="E6"]'), fullMaterialPane = fullMaterialCard.closest('.writing-preview-material-scroll');
  assert(fullMaterialCard.querySelector('blockquote')?.textContent === '同篇资料另一个精确片段，保留独立定位。', 'Whole preview lost exact excerpt');
  assert(fullMaterialPane.scrollTop > 0 && fullMaterialCard.getBoundingClientRect().top >= fullMaterialPane.getBoundingClientRect().top - 2, 'Whole preview failed to scroll only its material pane to the selected excerpt');
  const pendingPreviewIcon = document.querySelector('.writing-preview-chapter [data-evidence-id="E7"]');
  pendingPreviewIcon.scrollIntoView({ block: 'center' }); pendingPreviewIcon.click();
  await until(() => document.querySelector('.writing-preview-source-card.active[data-evidence-id="E7"]'), 'full preview pending original');
  const pendingPreviewCard = document.querySelector('.writing-preview-source-card.active[data-evidence-id="E7"]');
  assert(pendingPreviewIcon.textContent.trim() === '1' && pendingPreviewIcon.dataset.bindingStatus === 'unreviewed', 'Whole preview lost the pending citation number or state');
  assert(pendingPreviewCard.querySelector('blockquote')?.textContent === pendingQuote && pendingPreviewCard.textContent.includes('自动核对未完成'), 'Whole preview failed to show the exact pending source excerpt');
  assert(!document.querySelector('.writing-preview-chapter mark'), 'Whole preview highlighted an unreviewed citation as supported');
  passed('whole preview keeps pending original excerpts accessible without borrowing a confirmed citation highlight');
  outside(); button('返回编辑').click(); await until(() => !document.querySelector('.writing-preview'), 'return from full inline preview');
  passed('whole preview preserves mid-paragraph numbered icons and scrolls its own sidebar to the precise excerpt while highlighting one sentence');

  chapterButton('第一章').click(); await until(() => reading()?.textContent.includes('现有'), 'return chapter');
  const sameEditor = editor(); button('编辑', document.querySelector('.writing-body-view-tabs')).click(); await until(() => !editor().hidden && editor().getClientRects().length && !reading(), 'edit view');
  const draft = '未保存😀修改，保留引文。[E1]\n\n第二段人工文字。'; input(editor(), draft); await until(() => state.project.sections[0].content === draft, 'manual draft');
  editor().focus(); editor().setSelectionRange(3, 5); document.dispatchEvent(new Event('selectionchange', { bubbles: true })); editor().dispatchEvent(new KeyboardEvent('keyup', { key: 'Shift', bubbles: true })); await wait(40);
  assert(!button('润色').disabled, 'Native selection did not enable editing');
  button('润色').click(); await until(() => document.querySelector('[role="dialog"][aria-label="润色选中的文字"]'), 'selection dialog');
  const selectionDialog = document.querySelector('[role="dialog"][aria-label="润色选中的文字"]');
  assert(selectionDialog.querySelector('.writing-selection-preview p')?.textContent === '😀', 'Native UTF-16 emoji selection changed');
  button('返回原稿', selectionDialog).click(); await until(() => !document.querySelector('[role="dialog"][aria-label="润色选中的文字"]'), 'cancel AI selection');
  button('正文', document.querySelector('.writing-body-view-tabs')).click(); await until(() => reading()?.textContent.includes('未保存😀修改'), 'read unsaved draft');
  assert(editor() === sameEditor && editor().value === draft && button('润色').disabled, 'View switch lost native editor/draft or reused hidden selection');
  clickCitation('E1'); await wait(40);
  assert(!reading().querySelector('mark') && icon('E1').dataset.bindingStatus === 'stale' && reading().textContent.includes('正文已修改'), 'Unsaved edit reused an old precise sentence assessment');
  button('编辑', document.querySelector('.writing-body-view-tabs')).click(); await until(() => !editor().hidden, 'back to editor');
  assert(editor().value === draft && !state.project.sections[0].locked && state.project.sections[1].locked, 'Edit/read roundtrip changed draft or locks');
  passed('read/edit and cancelled AI dialog preserve mounted native editor, unsaved draft, UTF-16 selection and locks without writes');

  button('全文预览').click(); await until(() => document.querySelector('.writing-preview'), 'full preview');
  const previewIcon = document.querySelector('.writing-preview-chapter .writing-citation-icon'); previewIcon.click(); await until(() => document.querySelector('.writing-preview-source-card.active'), 'preview source icon');
  assert(!popup() && document.querySelector('.writing-preview-source-card.active')?.textContent.includes('原文作为文字显示'), 'Full preview did not show unobstructed current chapter source');
  assert(!document.querySelector('.writing-preview-chapter mark') && previewIcon.dataset.bindingStatus === 'stale', 'Whole preview highlighted an unsupported old range after manual edit');
  assert(document.querySelector('.writing-preview-source-card.active')?.dataset.evidenceId === 'E1' && document.querySelector('.writing-preview-source-card.active')?.textContent.includes('[1]'), 'Inline click stopped selecting matching numbered right-hand material');
  outside(); button('返回编辑').click(); await until(() => !document.querySelector('.writing-preview'), 'return editor');
  assert(editor().value === draft && state.project.sections[0].content === draft, 'Full preview lost unsaved draft');
  passed('full preview shares source icons, selects corresponding material and returns to the same unsaved draft');

  assert(requests.length === 0 && modelActions.length === 0, 'Controlled citation QA attempted an API/model/save/export action');
  output.dataset.status = 'passed'; output.textContent = JSON.stringify({ status: 'passed', groups: results.length, results, realApiRequests: requests.length, modelCalls: modelActions.length, originalNavigations: opened }, null, 2);
  if (options.get('preview') === '1') { button('正文', document.querySelector('.writing-body-view-tabs')).click(); await until(() => icon('E1'), 'preview reading'); clickCitation('E1'); }
}
if (options.get('manual') === '1') {
  root.render(<React.StrictMode><Harness/></React.StrictMode>);
  output.dataset.status = 'ready'; output.textContent = JSON.stringify({ status: 'ready', mode: 'manual', realApiRequests: requests.length, modelCalls: modelActions.length });
} else run().catch(error => { output.dataset.status = 'failed'; output.textContent = JSON.stringify({ status: 'failed', error: error.stack, results, requests, modelActions }, null, 2); });
