import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { WritingWorkspace } from '../src/Writing';
import { useWriting } from '../src/useWriting';
import { imageParagraphs } from '../src/writing-images';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';

const options = new URLSearchParams(location.search), resultNode = document.getElementById('writing-images-result');
const qualityPolicy = 'writing-image-quality-1';
const root = createRoot(document.getElementById('writing-images-root'));
const clone = value => structuredClone(value), wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (value, message) => { if (!value) throw new Error(message); };
const results = [], calls = [], opened = [];
let hook, delayed = false;
const models = [{ id: 'text-model-fixture', name: '正文写作', model: 'text-model' }, { id: 'visual-model-fixture', name: '图片核对', model: 'vision-model' }];
const img = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="700" height="390" viewBox="0 0 700 390"><rect width="700" height="390" fill="#f0f4ed"/><text x="50" y="55" font-size="24" fill="#244d3a">Sample chart</text><path d="M70 90V320H650" fill="none" stroke="#8faaa0" stroke-width="2"/><rect x="125" y="190" width="80" height="130" rx="4" fill="#8db49a"/><rect x="285" y="140" width="80" height="180" rx="4" fill="#579171"/><rect x="445" y="100" width="80" height="220" rx="4" fill="#306a49"/><text x="150" y="350" font-size="18" fill="#244d3a">A</text><text x="310" y="350" font-size="18" fill="#244d3a">B</text><text x="470" y="350" font-size="18" fill="#244d3a">C</text></svg>');
const content = '正文首先介绍三类设备的基本情况，并列整理资料中已明确给出的信息。[E1]\n\n接下来说明图表展示的范围，提醒读者注意统计时间、单位和适用条件。';
let remote = { id: 'images-fixture', version: 1, title: '让图片与文章相互说明', mode: 'strict', brief: { topic: '让图片与文章相互说明', purpose: '核对图片与段落', targetWords: 600, workflow: 'chapter' }, sourceIds: ['source-fixture'], sources: [{ sourceId: 'source-fixture', revisionId: 'revision-fixture', title: '设备观察报告' }], materialSelection: { recommendationId: null, passages: [] }, outlineConfirmed: true, sections: [{ id: 'section-fixture', title: '设备与图表', goal: '介绍设备及数据范围', mode: 'inherit', targetWords: 600, version: 1, content, contentHash: 'hash-initial', locked: false, evidence: [{ id: 'E1', sourceId: 'source-fixture', revisionId: 'revision-fixture', blockId: 'block-fixture', title: '设备观察报告', quote: '三类设备的基本情况。', start: 0, end: 11 }] }], runs: [], illustrations: [], illustrationReview: null, createdAt: '2026-10-23T03:00:00Z', updatedAt: '2026-10-23T04:00:00Z' };
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const revise = () => { remote.version += 1; remote.review = null; remote.illustrationReview = { status: 'stale' }; return clone(remote); };
function makeImageRun(body, id = `images-run-${calls.length}`) {
  const section = remote.sections[0], paragraphs = imageParagraphs(section.content), paragraphIndex = body.paragraphIndex ?? 0;
  const searchScope = body.searchScope || 'references', placementMode = body.placementMode || 'paragraph';
  const suggestion = (index, status = 'recommended') => ({ paragraphIndex: index, paragraphExact: paragraphs[index], contentHash: section.contentHash, status, reason: index === 1 ? '本段说明图表的统计范围，与图片内容直接对应。' : '本段介绍对照对象，可结合后文补充图表的口径说明。' });
  const candidate = { url: img, sourceType: 'reference', sourceTitle: '设备观察报告', sourceId: 'source-fixture', revisionId: 'revision-fixture', originalCaption: '三类样本对照图（隔离测试数据）', caption: '三类样本对照', altText: '三根不同高度的绿色柱形', scopeEligible: true, rankReasons: ['来自本稿引用的参考资料。'], issues: [], placementSuggestions: [suggestion(1), suggestion(0, 'needs_review')] };
  const candidates = [{ ...candidate, assetId: 'asset-review', status: 'needs_review', placementSuggestions: [suggestion(1, 'needs_review'), suggestion(0)], issues: ['部分说明需要人工核对。'] }, { ...candidate, assetId: 'asset-ok', status: 'recommended' }, { ...candidate, assetId: 'asset-outside', status: 'recommended', scopeEligible: false, scopeReason: '来自本稿参考资料，但超出当前确认的材料范围。' }, ...(searchScope === 'library' ? [{ ...candidate, assetId: 'asset-library', sourceType: 'library', sourceTitle: '图库补充观察', sourceId: 'library-source-fixture', revisionId: 'library-revision-fixture', status: 'recommended', rankReasons: ['图库中的同类对照图，可辅助读者理解。'], placementSuggestions: [suggestion(1, 'needs_review'), suggestion(0)] }] : []), { ...candidate, assetId: 'asset-no-placement', status: 'recommended', placementSuggestions: [] }];
  candidates[0].originalCaption = '(Image credit: Intel)';
  candidates[0].rankReasons = ['画面直接呈现俄亥俄州工厂。'];
  candidates[0].issues = ['两名工人握手，庆祝首个里程碑。'];
  candidates[0].placementSuggestions.forEach(item => { item.reason = '这是首座工厂首个桁架的吊装。'; });
  if (options.has('quality')) candidates.forEach((item, index) => {
    const passed = options.get('quality') !== 'blocked' && body.query !== '无可靠配图' && [1, 3, 4].includes(index);
    item.qualityGate = { policy: qualityPolicy, passed, reason: passed ? '图片直接对应正文内容。' : '图片仅提供相关背景，不能直接说明正文。' };
    item.placementSuggestions = item.placementSuggestions.map((placement, position) => ({ ...placement, qualityPassed: passed && position === 0 }));
  });
  const target = { sectionId: section.id, paragraphIndex, searchScope, placementMode, ...(options.has('quality') ? { qualityPolicy } : {}) };
  return { id, jobId: 'images-job', kind: 'images', status: 'succeeded', baseVersion: remote.version, imageTarget: target, candidate: { imageSearch: { ...target, paragraphExact: paragraphs[paragraphIndex], contentHash: section.contentHash, coverage: { availableSourceCount: 1, libraryScannedSourceCount: searchScope === 'library' ? 1 : 0, savedImageCount: 4, libraryFrozenAssetCount: searchScope === 'library' ? 1 : 0, examinedImageCount: candidates.length, placementMode, visualReviewedCount: placementMode === 'paragraph' ? 3 : 0, ...(placementMode === 'chapter' ? { placement: { visualReviewedCandidateCount: 3 } } : {}) }, warnings: ['请核对图片中的统计口径。'], candidates } } };
}
const legacyRun = makeImageRun({ paragraphIndex: 0 }, 'legacy-paragraph-run');
delete legacyRun.imageTarget;
delete legacyRun.candidate.imageSearch.searchScope;
delete legacyRun.candidate.imageSearch.placementMode;
delete legacyRun.candidate.imageSearch.qualityPolicy;
legacyRun.candidate.imageSearch.candidates.forEach(candidate => { delete candidate.sourceType; delete candidate.placementSuggestions; delete candidate.qualityGate; });
remote.runs.push(legacyRun);
window.fetch = async (input, init = {}) => {
  const url = typeof input === 'string' ? input : input.url, method = init.method || 'GET', body = init.body ? JSON.parse(init.body) : {};
  calls.push({ url, method, body });
  if (method === 'GET' && url === '/api/writing/projects') return response({ projects: [remote] });
  if (method === 'GET' && url === '/api/writing/projects/images-fixture') return response({ project: remote });
  assert(url.startsWith('/api/writing/projects/images-fixture/'), `Unexpected isolated fixture request: ${method} ${url}`);
  if (body.expectedVersion !== remote.version) return response({ detail: '文稿有更新，请重新载入并核对。' }, 409);
  if (method === 'POST' && url.endsWith('/image-search')) {
    const run = makeImageRun(body);
    remote.runs.unshift(run); return response({ run, project: remote }, 202);
  }
  if (method === 'POST' && url.endsWith('/illustrations/complete')) { revise(); remote.illustrationReview = { status: body.skip ? 'skipped' : 'complete' }; return response({ project: remote }); }
  if (method === 'POST' && url.endsWith('/illustrations')) {
    const search = remote.runs.find(run => run.id === body.runId).candidate.imageSearch, candidate = search.candidates.find(item => item.assetId === body.assetId);
    assert(candidate.scopeEligible && candidate.status !== 'unsuitable' && (candidate.status !== 'needs_review' || body.confirmUnverified), 'Unsafe adoption reached fake service');
    const placement = search.placementMode === 'chapter' ? candidate.placementSuggestions.find(item => item.paragraphIndex === body.paragraphIndex) : search;
    assert(placement && (placement.status !== 'needs_review' || body.confirmUnverified), 'Unverified insertion position reached fake service');
    if (search.qualityPolicy === qualityPolicy) assert(candidate.qualityGate?.policy === qualityPolicy && candidate.qualityGate.passed === true && (search.placementMode !== 'chapter' || placement.qualityPassed === true), 'Quality gate was bypassed in the fixture request');
    assert(candidate.sourceType !== 'library' || (body.confirmLibrarySource && body.confirmUnverified), 'Library image lacks independent source and relationship confirmation');
    const figure = { ...candidate, id: `figure-${calls.length}`, librarySourceConfirmed: body.confirmLibrarySource === true, sectionId: search.sectionId, anchor: { paragraphIndex: placement.paragraphIndex, paragraphExact: placement.paragraphExact, contentHash: placement.contentHash }, caption: body.caption, altText: body.altText, status: 'accepted', reviewed: true, locked: false };
    remote.illustrations.push(figure); revise(); if (delayed) await wait(250); return response({ project: remote });
  }
  if (url.includes('/illustrations/')) {
    const id = url.split('/').at(-1), figure = remote.illustrations.find(item => item.id === id);
    assert(figure, 'Unknown fixture figure');
    if (method === 'DELETE') remote.illustrations = remote.illustrations.filter(item => item.id !== id);
    else if (method === 'PATCH') { Object.assign(figure, body); if (Number.isInteger(body.paragraphIndex)) figure.anchor = { paragraphIndex: body.paragraphIndex, paragraphExact: imageParagraphs(remote.sections[0].content)[body.paragraphIndex], contentHash: remote.sections[0].contentHash }; figure.status = 'accepted'; }
    revise(); return response({ project: remote });
  }
  if (method === 'PATCH' && url.includes('/sections/')) { Object.assign(remote.sections[0], body); remote.sections[0].version += 1; remote.sections[0].contentHash = `hash-${remote.version + 1}`; remote.illustrations.forEach(figure => { figure.status = 'stale'; }); revise(); return response({ project: remote }); }
  throw new Error(`Unexpected fixture request: ${method} ${url}`);
};

function Harness() {
  hook = useWriting();
  const [stage, setStage] = useState('editor'), [theme, setTheme] = useState(['jade', 'glass', 'civic'].includes(options.get('theme')) ? options.get('theme') : 'jade');
  document.documentElement.dataset.theme = theme;
  return <div className="image-fixture"><div className="image-fixture-tools"><strong>配图隔离验收</strong><label>主题 <select aria-label="测试主题" value={theme} onChange={event => setTheme(event.target.value)}>{['jade', 'glass', 'civic'].map(value => <option key={value}>{value}</option>)}</select></label><span>仅内存数据 · 不调用真实模型或写入真实文稿</span></div><div className="image-fixture-main"><WritingWorkspace writing={hook} models={models} stage={stage} onStageChange={setStage} onOpenDoc={value => opened.push(value)}/></div></div>;
}
root.render(<Harness/>);
async function until(predicate, label) { for (let i = 0; i < 250; i += 1) { if (predicate()) return; await wait(20); } throw new Error(`Timeout: ${label}`); }
const click = async selector => { const node = document.querySelector(selector); assert(node && !node.disabled, `Unavailable control: ${selector}`); node.click(); await wait(45); };
const clickText = async (selector, text) => { const node = [...document.querySelectorAll(selector)].find(item => item.textContent.trim() === text); assert(node && !node.disabled, `Unavailable text control: ${text}`); node.click(); await wait(45); };
function setValue(node, value) { const proto = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, value); node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); }
function passed(name) { results.push(name); resultNode.textContent = JSON.stringify({ status: 'running', results }, null, 2); }

async function exerciseQuality() {
  await click('[data-writing-step="images"]');
  assert(document.querySelector('.writing-images-heading').textContent.includes('宁缺毋滥'), 'Quality priority missing from the UI');
  await click('.writing-images-search button[type="submit"]');
  await until(() => document.querySelectorAll('.writing-image-candidate').length === 5, 'quality candidates');
  let cards = [...document.querySelectorAll('.writing-image-candidate')];
  assert(cards[0].querySelector('.btn.primary').disabled && cards[0].querySelector('input[type="checkbox"]').disabled, 'Unqualified image can be manually confirmed');
  assert(!cards[1].querySelector('.btn.primary').disabled && cards[1].querySelector('option[value="1"]').disabled, 'Quality position gate not reflected in selectable positions');
  assert(cards[4].querySelector('.btn.primary').disabled, 'Missing recommended position can be bypassed');
  await click('.writing-image-candidate .writing-image-enlarge'); assert(document.querySelector('dialog[open]'), 'Rejected image cannot be viewed'); await click('dialog[open] button');
  const run = hook.project.runs[0], requestCount = calls.length;
  await hook.acceptIllustration({ runId: run.id, assetId: 'asset-review', paragraphIndex: 1, caption: '人工确认不能绕过', confirmUnverified: true, confirmLibrarySource: true });
  assert(calls.length === requestCount && !hook.project.illustrations.length, 'Manual flags bypassed the hook quality guard');
  passed('quality rejection blocks controls and direct manual confirmation while original images remain viewable');
  const library = cards[3], sourcesBefore = JSON.stringify(hook.project.sources);
  library.querySelector('[aria-label="已核对图片与所选段落"]').click(); await wait(45);
  assert(library.querySelector('.btn.primary').disabled, 'Qualified library image bypassed independent source confirmation');
  library.querySelector('[aria-label="确认图库图片仅作配图"]').click(); await wait(45);
  assert(!library.querySelector('.btn.primary').disabled, 'Qualified library image not available after both confirmations');
  library.querySelector('.btn.primary').click(); await until(() => hook.project.illustrations.length === 1, 'quality-passed library adoption');
  assert(hook.project.illustrations[0].anchor.paragraphIndex === 1 && JSON.stringify(hook.project.sources) === sourcesBefore, 'Qualified image changed placement or writing evidence');
  await clickText('.writing-images-tabs button', '文稿配图 · 1'); await clickText('.writing-image-saved button', '移除'); await clickText('.writing-image-remove-confirm button', '确认移除');
  await until(() => !hook.project.illustrations.length, 'quality fixture removal'); await clickText('.writing-images-tabs button', '查找图片');
  passed('quality-passed library image still requires independent source consent and preserves writing evidence');
  setValue(document.getElementById('writing-image-query'), '无可靠配图'); await wait(45); await click('.writing-images-search button[type="submit"]');
  await until(() => document.querySelector('[aria-label="暂无合适配图"]'), 'explicit no-suitable-image state');
  cards = [...document.querySelectorAll('.writing-image-candidate')];
  assert(cards.every(card => card.querySelector('.btn.primary').disabled), 'All-failed run offers an adoption');
  assert([...document.querySelectorAll('.writing-image-candidate input[type="checkbox"]')].every(input => input.disabled), 'All-failed run invites manual override');
  assert(!document.querySelector('.writing-images-workspace').textContent.includes('切换为“指定段落”后重新检索'), 'Quality gate UI invites a paragraph workaround');
  await clickText('.writing-images-footer button', '本稿不配图，完成此步'); await until(() => hook.project.illustrationReview?.status === 'skipped', 'quality no-image completion');
  passed('all failed quality gates show no suitable image and permit explicit no-image completion');
  resultNode.textContent = JSON.stringify({ status: 'passed', results, mutationCount: calls.filter(item => item.method !== 'GET').length, externalCalls: 0 }, null, 2); resultNode.dataset.status = 'passed';
}

async function exercise() {
  await until(() => hook?.project.id === remote.id && !hook.loading, 'loaded fixture');
  if (options.get('manual') === '1') { resultNode.textContent = 'MANUAL · 所有请求均为内存模拟'; return; }
  if (options.get('quality') === '1') return exerciseQuality();
  assert(document.querySelectorAll('[data-writing-step]').length === 6, 'Six steps missing');
  await clickText('.writing-body-view-tabs button', '编辑');
  const native = document.querySelector('textarea[aria-label="章节正文"]'); native.focus(); native.setSelectionRange(2, 8);
  await click('[data-writing-step="images"]');
  assert(native === document.querySelector('textarea[aria-label="章节正文"]') && native.selectionStart === 2 && native.selectionEnd === 8, 'Images navigation replaced native editor or selection');
  assert(document.querySelector('[aria-label="检索范围"]').value === 'library' && document.querySelector('[aria-label="插入位置方式"]').value === 'chapter', 'New UI defaults do not search the library and recommend chapter positions');
  assert(!document.querySelector('.writing-image-candidate'), 'Legacy paragraph results leaked into new chapter view');
  const beforeHistory = calls.length;
  await clickText('.writing-images-history button', '查看以前的段落推荐');
  assert(document.querySelector('[aria-label="检索范围"]').value === 'references' && document.querySelector('[aria-label="插入位置方式"]').value === 'paragraph' && document.querySelectorAll('.writing-image-candidate').length === 4, 'Legacy results unavailable through their original scope');
  setValue(document.querySelector('[aria-label="指定段落"]'), '1'); await wait(45);
  assert(!document.querySelector('.writing-image-candidate'), 'Paragraph zero results displayed as paragraph one');
  setValue(document.querySelector('[aria-label="检索范围"]'), 'library');
  setValue(document.querySelector('[aria-label="插入位置方式"]'), 'chapter'); await wait(45);
  assert(calls.length === beforeHistory, 'Viewing previous results or changing scope caused an unintended request');
  passed('new library/chapter defaults, old paragraph access and exact view isolation');
  await click('.writing-images-search button[type="submit"]');
  await until(() => document.querySelectorAll('.writing-image-candidate').length === 5, 'image candidates');
  const firstRequest = calls.filter(item => item.url.endsWith('/image-search')).at(-1).body;
  assert(firstRequest.modelId === null && firstRequest.searchScope === 'library' && firstRequest.placementMode === 'chapter' && !Object.hasOwn(firstRequest, 'paragraphIndex'), 'Default request model, scope or chapter compatibility anchor incorrect');
  const coverageText = document.querySelector('.writing-images-coverage').textContent;
  assert(coverageText.includes('2 篇图库来源资料') && coverageText.includes('初筛覆盖 5 张') && coverageText.includes('实际看图核对 3 张'), 'Coverage conflates reference-only counts or textual screening with vision review');
  const writingModel = hook.project.modelId, requestCount = calls.length;
  setValue(document.getElementById('writing-image-model'), 'visual-model-fixture'); await wait(45);
  assert(calls.length === requestCount && !hook.dirty && hook.project.modelId === writingModel, 'Image model selection mutated the draft or issued a request');
  await click('.writing-images-search button[type="submit"]');
  await until(() => !hook.busy && calls.filter(item => item.url.endsWith('/image-search')).at(-1).body.modelId === 'visual-model-fixture', 'explicit visual model request');
  assert(hook.project.modelId === writingModel, 'Visual request replaced writing model');
  setValue(document.getElementById('writing-image-model'), ''); await wait(45);
  passed('request-scoped visual model override and default inheritance preserve writing model');
  let cards = [...document.querySelectorAll('.writing-image-candidate')];
  assert(cards[0].querySelector('.btn.primary').disabled, 'Unreviewed candidate adoptable');
  assert(!/握手|俄亥俄|首座|首个|直接呈现/.test(cards[0].innerText) && cards[0].innerText.includes('仅有署名或来源'), 'Unconfirmed free model claims leaked as matching evidence');
  assert(hook.project.runs[0].candidate.imageSearch.candidates[0].rankReasons[0].includes('直接呈现俄亥俄'), 'UI changed frozen model output');
  assert(cards[2].querySelector('.btn.primary').disabled, 'Out-of-scope candidate adoptable');
  assert(cards[4].querySelector('.btn.primary').disabled && !cards[4].querySelector('select'), 'Chapter result without a placement guessed a paragraph');
  assert(cards[1].querySelector('.writing-image-placement-excerpt').textContent === imageParagraphs(content)[1], 'First recommendation incorrectly used compatibility paragraph zero');
  setValue(cards[1].querySelector('select'), '1'); await wait(45);
  assert(cards[1].querySelector('.btn.primary').disabled && cards[1].querySelector('[aria-label="已核对图片与所选段落"]'), 'Recommended candidate bypassed stricter placement review');
  assert(cards[1].querySelector('.writing-image-placement-reason').textContent.includes('此位置仅供人工核对'), 'Needs-review selected position exposed its free reason');
  setValue(cards[1].querySelector('select'), '0'); await wait(45);
  assert(cards[1].querySelector('.writing-image-placement-reason').textContent.includes('本段说明图表的统计范围'), 'Recommended position lost its original explanation');
  passed('uncertain candidate and position explanations are conservative while frozen reasons and recommended explanations remain');
  await click('.writing-image-candidate .writing-image-enlarge');
  assert(document.querySelector('dialog[open]'), 'Original image could not be enlarged');
  await click('dialog[open] button');
  assert(!document.querySelector('dialog[open]'), 'Image view did not close');
  passed('six steps, editor identity and selection, review and scope guards');
  const sourcesBefore = JSON.stringify(hook.project.sources), libraryCard = cards[3];
  libraryCard.querySelector('[aria-label="确认图库图片仅作配图"]').click(); await wait(45);
  assert(libraryCard.querySelector('.btn.primary').disabled, 'Source consent alone bypassed image review');
  libraryCard.querySelector('[aria-label="确认图库图片仅作配图"]').click();
  libraryCard.querySelector('[aria-label="已核对图片与所选段落"]').click(); await wait(45);
  assert(libraryCard.querySelector('.btn.primary').disabled, 'Image review alone bypassed source consent');
  libraryCard.querySelector('[aria-label="确认图库图片仅作配图"]').click(); await wait(45);
  libraryCard.querySelector('.btn.primary').click();
  await until(() => hook.project.illustrations.length === 1, 'library illustration adoption');
  const libraryFigure = hook.project.illustrations[0], libraryRequest = calls.filter(item => item.url.endsWith('/illustrations')).at(-1).body;
  assert(libraryFigure.anchor.paragraphIndex === 1 && libraryRequest.paragraphIndex === 1 && libraryRequest.confirmLibrarySource && libraryRequest.confirmUnverified, 'Library position/confirmation not transmitted');
  assert(libraryFigure.sourceType === 'library' && libraryFigure.librarySourceConfirmed && JSON.stringify(hook.project.sources) === sourcesBefore && hook.project.sourceIds.join(',') === 'source-fixture', 'Library illustration changed body evidence');
  await click('[data-writing-step="body"]'); await clickText('.writing-body-view-tabs button', '正文');
  assert(document.querySelector('.writing-inline-figure .writing-image-library-note'), 'Library provenance missing in reading view');
  await click('[data-writing-step="images"]'); await clickText('.writing-images-tabs button', '文稿配图 · 1');
  assert(document.querySelector('.writing-image-saved .is-library'), 'Library provenance missing in image management');
  await clickText('.writing-image-saved button', '移除'); await clickText('.writing-image-remove-confirm button', '确认移除');
  await until(() => !hook.project.illustrations.length, 'temporary library figure removal');
  await clickText('.writing-images-tabs button', '查找图片');
  passed('library source and relationship confirmations, suggested position and unchanged writing evidence');
  await click('.writing-image-candidate input[type="checkbox"]');
  setValue(document.querySelector('.writing-image-candidate select'), '1'); await wait(45);
  assert(!document.querySelector('.writing-image-candidate input[type="checkbox"]').checked && document.querySelector('.writing-image-candidate .btn.primary').disabled, 'Changing suggested paragraph reused previous manual confirmation');
  await click('.writing-image-candidate input[type="checkbox"]');
  await click('.writing-image-candidate .btn.primary');
  await until(() => hook.project.illustrations.length === 1, 'figure adoption');
  const adopted = calls.filter(item => item.url.endsWith('/illustrations')).at(-1).body;
  assert(adopted.confirmUnverified === true && adopted.paragraphIndex === 0 && !Object.hasOwn(adopted, 'confirmLibrarySource'), 'Reference review/selected position missing or misclassified as library');
  await click('[data-writing-step="body"]');
  await clickText('.writing-body-view-tabs button', '正文');
  assert(document.querySelectorAll('.writing-inline-figure').length === 1, 'Accepted figure missing in reading');
  assert(document.querySelector('.writing-inline-figure').previousElementSibling?.className !== 'writing-inline-figure', 'Figure placement broken');
  await clickText('.writing-flow-actions button', '全文预览');
  assert(document.querySelectorAll('.writing-preview .writing-inline-figure').length === 1, 'Accepted figure missing in full preview');
  assert(native === document.querySelector('textarea[aria-label="章节正文"]'), 'Preview replaced native editor');
  passed('confirmed adoption and chapter/full-preview figures');
  await click('[data-writing-step="images"]');
  await clickText('.writing-images-tabs button', '文稿配图 · 1');
  setValue(document.querySelector('.writing-image-saved select'), '1');
  setValue(document.querySelector('.writing-image-saved textarea'), '人工核对后的样本对照图'); await wait(50);
  await clickText('.writing-image-saved button', '保存配图修改');
  await until(() => hook.project.illustrations[0].anchor.paragraphIndex === 1, 'figure moved');
  assert(calls.filter(item => item.method === 'PATCH').at(-1).body.confirmUnverified === true, 'Manual caption not explicitly confirmed');
  await clickText('.writing-image-saved button', '锁定');
  assert(document.querySelector('.writing-image-saved textarea').disabled, 'Locked caption editable');
  await clickText('.writing-image-saved button', '解锁');
  passed('move, caption, explicit manual confirmation and figure lock');
  await click('[data-writing-step="body"]'); await clickText('.writing-body-view-tabs button', '编辑');
  setValue(native, native.value + '\n\n未保存的人工补充。'); await until(() => hook.dirty, 'dirty body'); native.setSelectionRange(3, 9);
  const dirtyContent = native.value, callCount = calls.length;
  await click('[data-writing-step="images"]');
  assert(document.querySelector('.writing-images-search button[type="submit"]').disabled, 'Dirty search allowed');
  for (const theme of ['glass', 'civic', 'jade']) { setValue(document.querySelector('[aria-label="测试主题"]'), theme); await wait(40); assert(native === document.querySelector('textarea[aria-label="章节正文"]') && native.value === dirtyContent && native.selectionStart === 3 && native.selectionEnd === 9, 'Theme/image toggle lost draft or native selection'); }
  assert(calls.length === callCount, 'Toggling images or themes performed a request');
  await clickText('.writing-image-notice button', '保存正文');
  await until(() => !hook.dirty, 'saved changed body');
  await clickText('.writing-images-tabs button', '文稿配图 · 1');
  assert(document.querySelector('.writing-image-warning').textContent.includes('正文或材料已变化'), 'Stale figure warning missing');
  await clickText('.writing-image-saved button', '保存配图修改');
  await until(() => hook.project.illustrations[0].status === 'accepted', 'stale recheck');
  passed('dirty action guards, three-theme textarea preservation and stale recheck');
  await clickText('.writing-images-footer button', '完成配图');
  await until(() => hook.project.illustrationReview.status === 'complete', 'images complete');
  assert(document.querySelector('[data-writing-step="images"]').textContent.includes('已完成'), 'Progress not completed');
  await clickText('.writing-image-saved button', '移除');
  await clickText('.writing-image-remove-confirm button', '确认移除');
  await until(() => !hook.project.illustrations.length, 'figure removed');
  await clickText('.writing-images-footer button', '本稿不配图，完成此步');
  await until(() => hook.project.illustrationReview.status === 'skipped', 'no-image completion');
  passed('complete, explicit removal and no-image completion');
  await clickText('.writing-images-tabs button', '查找图片');
  await click('.writing-images-search button[type="submit"]');
  await until(() => !hook.busy && hook.project.runs[0].candidate.imageSearch.contentHash === hook.project.sections[0].contentHash, 'new recommendation');
  delayed = true;
  const run = hook.project.runs[0], candidate = run.candidate.imageSearch.candidates[1];
  const pending = hook.acceptIllustration({ runId: run.id, assetId: candidate.assetId, paragraphIndex: 1, caption: candidate.caption, altText: candidate.altText, confirmUnverified: false });
  await wait(30);
  hook.setProject(value => ({ ...value, sections: value.sections.map(item => ({ ...item, content: '并发请求期间必须保留的人工草稿。' })) }));
  await pending; await wait(60);
  assert(hook.project.sections[0].content === '并发请求期间必须保留的人工草稿。' && hook.dirty && hook.remoteChanged, 'Late image adoption overwrote draft or hid remote change');
  delayed = false;
  passed('late image mutation preserves concurrent local draft and conflict notice');
  resultNode.textContent = JSON.stringify({ status: 'passed', results, mutationCount: calls.filter(item => item.method !== 'GET').length, externalCalls: 0 }, null, 2); resultNode.dataset.status = 'passed';
}
exercise().catch(error => { resultNode.textContent = JSON.stringify({ status: 'failed', message: error.stack, results }, null, 2); resultNode.dataset.status = 'failed'; console.error(error); });
