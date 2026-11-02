import React from 'react';
import { createRoot } from 'react-dom/client';
import { useWriting } from '../src/useWriting';

const resultNode = document.getElementById('writing-hook-result');
const root = createRoot(document.getElementById('writing-hook-root'));
const originalFetch = window.fetch;
const originalAnchorClick = HTMLAnchorElement.prototype.click;
const results = [];
let currentHook, remote, getCount = 0, mutationHandler = null, downloads = [];
const clone = value => structuredClone(value);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };

function fixture() {
  return {
    id: 'writing-hook-fixture', title: '轮询回归样本', version: 1, mode: 'strict', modelId: '',
    brief: { topic: '轮询回归样本', purpose: '', targetWords: 100, workflow: 'chapter', style: '清晰克制', template: '观点文章' },
    sourceIds: [], sources: [], outlineConfirmed: true,
    sections: [{ id: 'section-hook-fixture', title: '第一章', goal: '', mode: 'inherit', targetWords: 100, version: 1, content: '', locked: false, manual: false, evidence: [] }],
    runs: [{ id: 'run-hook-fixture', jobId: 'job-hook-fixture', kind: 'document', status: 'running', applied: false, createdAt: '2026-10-22T00:00:00Z' }],
    createdAt: '2026-10-22T00:00:00Z', updatedAt: '2026-10-22T00:00:00Z',
  };
}

// The only API visible to this dedicated page is an in-memory fixture. Unknown
// requests fail rather than reaching the actual writing service.
window.fetch = async (input, options = {}) => {
  const url = typeof input === 'string' ? input : input.url;
  const method = options.method || 'GET';
  if (method !== 'GET') {
    if (mutationHandler) return mutationHandler(url, options);
    throw new Error(`Unexpected fixture mutation: ${method} ${url}`);
  }
  if (url === '/api/writing/projects') return new Response(JSON.stringify({ projects: [clone(remote)] }), { status: 200 });
  if (url === '/api/writing/projects/writing-hook-fixture') {
    getCount += 1;
    return new Response(JSON.stringify({ project: clone(remote) }), { status: 200 });
  }
  throw new Error(`Unexpected fixture request: ${url}`);
};
// The harness records the intent to download; it never writes a test attachment.
HTMLAnchorElement.prototype.click = function () { downloads.push({ href: this.href, filename: this.download }); };

function Harness() {
  currentHook = useWriting();
  return <p id="writing-hook-observed">{currentHook.loading ? 'loading' : `${currentHook.project.version}:${currentHook.pending ? 'running' : 'finished'}:${currentHook.dirty ? 'dirty' : 'saved'}`}</p>;
}

async function until(predicate, description, timeout = 5000) {
  const deadline = performance.now() + timeout;
  while (!predicate()) {
    if (performance.now() > deadline) throw new Error(`Timeout: ${description}`);
    await delay(20);
  }
}

async function runCase(name, action, dirtyDraft = false) {
  currentHook = null;
  remote = fixture();
  getCount = 0;
  root.render(<Harness key={name}/>);
  await until(() => currentHook?.project.id === remote.id && !currentHook.loading && currentHook.pending, 'initial pending project');
  await delay(60); // Let the initial polling effect mount before invalidating its session.
  await action();
  await until(() => !currentHook.busy && currentHook.pending, 'same-project action finished');
  if (dirtyDraft) {
    currentHook.setProject(project => ({ ...project, sections: project.sections.map(section => ({ ...section, content: '必须保留的未保存人工修改', locked: true })) }));
    await until(() => currentHook.dirty && currentHook.project.sections[0].locked, 'manual draft and local lock');
  }
  const beforeCompletionReads = getCount;
  remote.version = 2;
  remote.sections[0].content = '模型生成的正文';
  remote.sections[0].version = 2;
  remote.runs[0].status = 'succeeded';
  remote.runs[0].applied = true;
  await until(() => !currentHook.pending && currentHook.project.runs[0]?.status === 'succeeded', 'poll observes task completion');
  assert(getCount > beforeCompletionReads, 'No new project GET after reopening/reloading the same project');
  if (dirtyDraft) {
    assert(currentHook.project.sections[0].content === '必须保留的未保存人工修改', 'Late completion overwrote the manual draft');
    assert(currentHook.project.sections[0].locked, 'Late completion removed the local lock');
    assert(currentHook.dirty && currentHook.remoteChanged, 'The preserved local draft or remote-version notice was lost');
  } else {
    assert(currentHook.project.version === 2 && currentHook.project.sections[0].content === '模型生成的正文', 'Clean current project did not receive the finished draft');
  }
  results.push({ name, status: 'passed', projectReads: getCount, preservedDirtyDraft: dirtyDraft });
  resultNode.textContent = JSON.stringify({ status: 'running', results }, null, 2);
}

const report = () => ({ version: 1, checkedAt: '2026-10-22T01:00:00Z', issues: [], counts: { errors: 0, warnings: 0 }, wordCounts: { total: 100, target: 100, min: 80, max: 120, status: 'within_range', sections: [] }, coverage: { paragraphs: 1, validlyCitedParagraphs: 1, pendingParagraphs: 0 } });
async function loadCompletedCase(name, withReview = true) {
  currentHook = null; mutationHandler = null; downloads = []; remote = fixture(); remote.runs = [];
  remote.sections[0].content = '保存的人工正文'; remote.sections[0].manual = true;
  if (withReview) remote.review = report();
  root.render(<Harness key={name}/>);
  await until(() => currentHook?.project.id === remote.id && !currentHook.loading && !currentHook.pending, name);
  await delay(40);
}
function passed(name) { results.push({ name, status: 'passed' }); resultNode.textContent = JSON.stringify({ status: 'running', results }, null, 2); }
async function runSemanticSaveCases() {
  await loadCompletedCase('read-only navigation and reverted draft save');
  remote.mode = 'extended';
  remote.sections.push({ ...clone(remote.sections[0]), id: 'section-hook-second', title: '第二章', locked: true });
  await currentHook.reload(); await until(() => currentHook.project.sections.length === 2 && !currentHook.busy, 'second chapter loaded');
  const original = clone(remote), writes = [];
  mutationHandler = async (url, options) => { writes.push({ url, options }); throw new Error('A semantic no-op must not send a mutation'); };
  currentHook.setProject(project => ({ ...project, activeSectionId: 'section-hook-second', bodyView: 'edit',
    candidateView: { sectionId: 'section-hook-second' }, mode: 'extended', targetWords: '100',
    sections: project.sections.map(section => ({ ...section, words: '100' })) }));
  await until(() => currentHook.project.activeSectionId === 'section-hook-second', 'navigation applied');
  assert(!currentHook.dirty, 'Navigation, aliases or numeric form values made a saved manuscript dirty');
  currentHook.setProject(project => ({ ...project, sections: project.sections.map((section, index) => index ? section : { ...section, title: '临时修改' }) }));
  await until(() => currentHook.dirty, 'genuine unsaved outline change');
  currentHook.setProject(project => ({ ...project, outlineConfirmed: false,
    sections: project.sections.map((section, index) => index ? section : { ...section, title: original.sections[0].title }) }));
  await until(() => !currentHook.dirty, 'reverting all business fields clears dirty');
  await currentHook.save();
  // The save promise resolves before React necessarily commits adopt(saved).
  // Keep the report/confirmation assertions, but observe the committed hook.
  await until(() => !currentHook.busy && currentHook.project.outlineConfirmed
    && currentHook.project.review?.version === original.review.version, 'no-op save restores saved confirmation and report');
  assert(!writes.length && currentHook.project.version === original.version, 'No-op save submitted a PATCH or changed the version');
  assert(currentHook.project.outlineConfirmed && currentHook.project.review?.version === original.review.version,
    'No-op save lost saved confirmation or report metadata');
  assert(currentHook.project.activeSectionId === 'section-hook-second' && currentHook.project.sections[1].locked,
    'No-op save lost the viewed chapter or lock');
  assert(currentHook.project.sections[0].content === original.sections[0].content, 'No-op save changed prose');
  passed('navigation and reverted semantic draft save preserve version, report, content and locks');

  await loadCompletedCase('real save checkpoint preserves pending flags with concurrent input');
  mutationHandler = async (url, options) => {
    assert(options.method === 'PATCH' && url === '/api/writing/projects/' + remote.id, 'Unexpected checkpoint mutation');
    const body = JSON.parse(options.body);
    assert(body.expectedVersion === remote.version && body.brief.purpose === '真正保存的新要求', 'Checkpoint did not save the captured brief');
    remote.brief = clone(body.brief); remote.version += 1; remote.outlineConfirmed = false;
    remote.workflowPendingSteps = ['brief', 'body']; remote.review = null;
    currentHook.setProject(project => ({ ...project, sections: project.sections.map(section => ({ ...section, content: '保存期间另行输入的正文' })) }));
    return new Response(JSON.stringify({ project: clone(remote) }), { status: 200 });
  };
  currentHook.setProject(project => ({ ...project, purpose: '真正保存的新要求' }));
  await until(() => currentHook.dirty, 'real brief change');
  await currentHook.save();
  await until(() => !currentHook.busy && currentHook.project.version === 2, 'partial save checkpoint adopted');
  assert(JSON.stringify(currentHook.project.workflowPendingSteps) === JSON.stringify(['brief', 'body'])
    && JSON.stringify(currentHook.project.workflowPendingSteps) === JSON.stringify(currentHook.savedProject.workflowPendingSteps),
    'Current project and saved baseline disagree on server-owned pending steps');
  assert(currentHook.dirty && currentHook.project.sections[0].content === '保存期间另行输入的正文',
    'Adopting checkpoint flags overwrote concurrent unsaved prose');
  passed('changed save checkpoint copies pending steps into current project while preserving concurrent input');

  await loadCompletedCase('explicit actions after a clean save');
  const actions = [];
  mutationHandler = async (url, options) => {
    assert(options.method === 'POST' && (url.endsWith('/generate') || url.endsWith('/match-citations')), 'Unexpected semantic no-op action');
    actions.push(url);
    return new Response(JSON.stringify({ project: clone(remote), runId: 'synthetic-action', job: { id: 'synthetic-action-job' } }), { status: 202 });
  };
  await currentHook.generateSection(remote.sections[0].id); await currentHook.matchCitations();
  assert(actions.length === 2 && actions[0].endsWith('/generate') && actions[1].endsWith('/match-citations'),
    'A clean save incorrectly suppressed an explicit generation or citation request');
  passed('semantic no-op save still forwards explicit generation and citation-check actions');

  await loadCompletedCase('explicit step confirmation');
  const confirmations = [];
  mutationHandler = async (url, options) => {
    assert(options.method === 'POST' && url.endsWith('/steps/body/confirm'), 'Unexpected step confirmation request');
    const body = JSON.parse(options.body); confirmations.push(body.expectedVersion);
    assert(body.expectedVersion === remote.version, 'Step confirmation used an outdated project version');
    if (remote.workflowPendingSteps?.includes('body')) {
      remote.workflowPendingSteps = []; remote.version += 1; remote.review = null;
    }
    return new Response(JSON.stringify({ project: clone(remote) }), { status: 200 });
  };
  const beforeNoopConfirmation = currentHook;
  await currentHook.confirmStep('body');
  await until(() => currentHook !== beforeNoopConfirmation && !currentHook.busy,
    'no-op step confirmation committed before checking report preservation');
  assert(currentHook.project.version === 1 && currentHook.project.review?.version === 1, 'An already confirmed step lost version/report state');
  remote.workflowPendingSteps = ['body']; await currentHook.reload();
  await until(() => currentHook.project.workflowPendingSteps?.includes('body') && !currentHook.busy, 'saved pending state loaded');
  await currentHook.confirmStep('body'); await currentHook.confirmStep('body');
  await until(() => !currentHook.busy && currentHook.project.version === 2
    && currentHook.project.workflowPendingSteps?.length === 0, 'step confirmation committed into the visible hook');
  assert(confirmations.join(',') === '1,1,2' && currentHook.project.version === 2 && !currentHook.project.workflowPendingSteps.length,
    'Explicit confirmation did not clear only the saved pending state or repeated confirmation changed its version');
  assert(currentHook.project.sections[0].content === remote.sections[0].content, 'Step confirmation rewrote the saved prose');
  passed('explicit step confirmation preserves no-op reports and adopts version-bound pending completion');
}
async function runReviewAndExportCases() {
  await loadCompletedCase('review preserves inflight manual draft and lock', false);
  let releaseReview, reviewStarted = false;
  mutationHandler = async (url, options) => {
    assert(url.endsWith('/review'), 'Review used an unexpected mutation endpoint');
    assert(JSON.parse(options.body).expectedVersion === 1, 'Review did not fence the saved version');
    reviewStarted = true;
    await new Promise(resolve => { releaseReview = resolve; });
    return new Response(JSON.stringify({ project: { ...clone(remote), review: report() }, review: report() }), { status: 200 });
  };
  const reviewing = currentHook.reviewRules();
  await until(() => reviewStarted, 'review in flight');
  currentHook.setProject(project => ({ ...project, sections: project.sections.map(section => ({ ...section, content: '校验期间的人工文字', locked: true })) }));
  await until(() => currentHook.dirty, 'manual text during review');
  releaseReview(); await reviewing; await delay(40);
  assert(currentHook.project.sections[0].content === '校验期间的人工文字' && currentHook.project.sections[0].locked, 'Review replaced draft text or lock');
  assert(currentHook.dirty && currentHook.project.review?.version === 1, 'Preserved draft did not retain the versioned report');
  passed('review preserves inflight manual draft and lock');

  await loadCompletedCase('dirty draft blocks export');
  currentHook.setProject(project => ({ ...project, sections: project.sections.map(section => ({ ...section, content: '未保存正文' })) }));
  await until(() => currentHook.dirty, 'dirty export draft');
  assert(await currentHook.exportDocument() === null, 'Dirty draft was exportable');
  assert(downloads.length === 0, 'Dirty export downloaded a file');
  passed('dirty draft blocks export without saving or downloading');

  await loadCompletedCase('pending task blocks export');
  currentHook.setProject(project => ({ ...project, runs: fixture().runs }));
  await until(() => currentHook.pending, 'pending export task');
  assert(await currentHook.exportDocument() === null, 'Pending task was exportable');
  assert(downloads.length === 0, 'Pending export downloaded a file');
  passed('pending generation blocks export');

  await loadCompletedCase('stale report blocks export');
  currentHook.setProject(project => ({ ...project, review: { ...project.review, version: 0 } }));
  await delay(40);
  assert(await currentHook.exportDocument() === null, 'Stale report was exportable');
  passed('stale report blocks export');

  await loadCompletedCase('issues need explicit draft consent');
  currentHook.setProject(project => ({ ...project, review: { ...project.review, issues: [{ code: 'pending', message: '待核实内容' }] } }));
  await delay(40);
  assert(await currentHook.exportDocument() === null, 'Review issues were silently bypassed');
  let requestBody;
  mutationHandler = async (url, options) => {
    assert(url.endsWith('/export'), 'Export used an unexpected endpoint'); requestBody = JSON.parse(options.body);
    return new Response('test-docx-fixture', { headers: { 'X-Wenshu-Project-Version': '1', 'X-Wenshu-Review-Status': 'issues' } });
  };
  await delay(40);
  const exported = await currentHook.exportDocument({ format: 'docx', allowIssues: true });
  assert(exported?.version === 1 && requestBody.expectedVersion === 1 && requestBody.allowIssues === true && requestBody.format === 'docx', 'Explicit draft export did not bind format, consent and version');
  assert(downloads.length === 1 && downloads[0].filename.endsWith('.docx') && !downloads[0].filename.includes('待核实'), 'Draft download did not preserve its status');
  assert(currentHook.project.sections[0].content === remote.sections[0].content && currentHook.project.sections[0].manual, 'Export changed manual content');
  passed('explicit draft consent exports the reviewed version without changing manual content');

  for (const format of ['docx', 'html', 'pdf']) {
    await loadCompletedCase('skipped review exports without issue consent ' + format);
    remote.review = { ...report(), citationReviewCompleted: false, authorConfirmed: false,
      disposition: { status: 'skipped', version: 1, reportId: report().reportId },
      issues: [{ id: 'skipped-finding', code: 'citation_review_pending', message: '原检查事项', acknowledged: false }] };
    await currentHook.reload(); await delay(40);
    const before = clone(currentHook.project);
    mutationHandler = async (url, options) => {
      assert(url.endsWith('/export'), 'Skipped review export invoked a different action');
      requestBody = JSON.parse(options.body);
      return new Response(format === 'pdf' ? '%PDF-1.7 isolated fixture' : 'isolated export fixture', { headers: {
        'Content-Type': format === 'pdf' ? 'application/pdf' : 'application/octet-stream',
        'X-Wenshu-Project-Version': '1', 'X-Wenshu-Review-Status': 'needs_attention', 'X-Wenshu-Draft-Required': 'false',
      } });
    };
    const skippedExport = await currentHook.exportDocument({ format });
    assert(skippedExport?.format === format && !skippedExport.requiresDraftConsent && requestBody.allowIssues === false,
      'Skipped review required issue consent or was classified as a draft');
    assert(downloads.length === 1 && downloads[0].filename.endsWith('.' + format), 'Skipped review did not download selected format');
    assert(JSON.stringify(currentHook.project) === JSON.stringify(before), 'Skipped export changed findings, decisions or saved prose');
    currentHook.setProject(project => ({ ...project, review: { ...project.review, version: 0 } }));
    await delay(40);
    assert(await currentHook.exportDocument({ format }) === null && downloads.length === 1, 'An old skipped report bypassed current-version export protection');
    passed('skipped review exports ' + format + ' without issue consent and retains stale-version protection');
  }

  for (const change of ['text-and-lock', 'project', 'wrong-header']) {
    await loadCompletedCase(`late export protection ${change}`);
    let releaseBlob, exportStarted = false;
    mutationHandler = async () => ({ ok: true, headers: new Headers({ 'X-Wenshu-Project-Version': change === 'wrong-header' ? '99' : '1' }), blob: async () => { exportStarted = true; await new Promise(resolve => { releaseBlob = resolve; }); return new Blob(['test-docx-fixture']); } });
    const exporting = currentHook.exportDocument();
    if (change !== 'wrong-header') {
      await until(() => exportStarted, 'export blob in flight');
      currentHook.setProject(project => change === 'project' ? { ...project, id: 'another-project' } : { ...project, sections: project.sections.map(section => ({ ...section, content: '导出期间的人工修改', locked: true })) });
      await delay(40); releaseBlob();
    }
    assert(await exporting === null && downloads.length === 0, `Export downloaded after ${change}`);
    if (change === 'text-and-lock') assert(currentHook.project.sections[0].content === '导出期间的人工修改' && currentHook.project.sections[0].locked, 'Export overwrote the concurrent manual draft');
    passed(`late export cancellation: ${change}`);
  }
}

async function runPdfExportCases() {
  const pdfHeaders = { 'Content-Type': 'application/pdf', 'X-Wenshu-Project-Version': '1', 'X-Wenshu-Review-Status': 'passed' };
  await loadCompletedCase('PDF exports reviewed locked manual content');
  // Fixture and baseline both carry the saved lock; export must not unlock it.
  remote.sections[0].locked = true;
  await currentHook.reload(); await delay(40);
  let requestBody;
  mutationHandler = async (url, options) => {
    assert(url.endsWith('/export') && options.method === 'POST', 'PDF did not use the guarded export endpoint');
    requestBody = JSON.parse(options.body);
    return new Response('%PDF-1.7 controlled fixture', { headers: pdfHeaders });
  };
  const exported = await currentHook.exportDocument({ format: 'pdf' });
  assert(exported?.format === 'pdf' && exported.version === 1 && requestBody.format === 'pdf' && requestBody.expectedVersion === 1 && requestBody.allowIssues === false, 'PDF request lost its format or reviewed version');
  assert(downloads.length === 1 && downloads[0].filename.endsWith('.pdf'), 'PDF download used the wrong extension');
  assert(currentHook.project.sections[0].locked && currentHook.project.sections[0].manual && currentHook.project.sections[0].content === remote.sections[0].content, 'PDF export changed locked manual content');
  passed('PDF export binds reviewed version and preserves locked manual content');

  await loadCompletedCase('PDF draft requires explicit consent');
  currentHook.setProject(project => ({ ...project, review: { ...project.review, issues: [{ code: 'pending', message: '待核实内容' }] } }));
  await delay(40);
  assert(await currentHook.exportDocument({ format: 'pdf' }) === null && downloads.length === 0, 'PDF silently bypassed review issues');
  mutationHandler = async (url, options) => {
    requestBody = JSON.parse(options.body);
    return new Response('%PDF-1.7 controlled draft fixture', { headers: { ...pdfHeaders, 'X-Wenshu-Review-Status': 'issues' } });
  };
  await delay(40);
  const draft = await currentHook.exportDocument({ format: 'pdf', allowIssues: true });
  assert(draft?.format === 'pdf' && requestBody.allowIssues === true && downloads.length === 1 && downloads[0].filename.endsWith('.pdf') && !downloads[0].filename.includes('待核实'), 'Acknowledged PDF draft lost its status');
  passed('PDF findings require explicit current-report draft consent');

  await loadCompletedCase('PDF late blob preserves manual text and lock');
  let releaseBlob, blobStarted = false;
  mutationHandler = async () => ({ ok: true, headers: new Headers(pdfHeaders), blob: async () => { blobStarted = true; await new Promise(resolve => { releaseBlob = resolve; }); return new Blob(['%PDF-1.7 controlled fixture'], { type: 'application/pdf' }); } });
  const exporting = currentHook.exportDocument({ format: 'pdf' });
  await until(() => blobStarted, 'PDF blob in flight');
  currentHook.setProject(project => ({ ...project, sections: project.sections.map(section => ({ ...section, content: 'PDF 导出期间的人工修改', locked: true })) }));
  await delay(40); releaseBlob();
  assert(await exporting === null && downloads.length === 0, 'PDF downloaded despite a changed draft');
  assert(currentHook.project.sections[0].content === 'PDF 导出期间的人工修改' && currentHook.project.sections[0].locked && currentHook.dirty, 'Late PDF replaced the manual draft or lock');
  passed('late PDF blob cancels download and preserves the changed manual draft and lock');

  await loadCompletedCase('PDF rejects non-PDF responses');
  mutationHandler = async () => new Response('<html>upstream failure</html>', { headers: { ...pdfHeaders, 'Content-Type': 'text/html' } });
  assert(await currentHook.exportDocument({ format: 'pdf' }) === null && downloads.length === 0, 'Non-PDF response was downloaded as PDF');
  passed('PDF rejects a successful HTTP response with the wrong media type');
}

async function runCitationMatchingCases() {
  await loadCompletedCase('unsaved text blocks citation matching', false);
  let mutations = 0;
  mutationHandler = async () => { mutations += 1; throw new Error('Matching must not save the draft'); };
  currentHook.setProject(project => ({ ...project, sections: project.sections.map(section => ({ ...section, content: '未保存的人工正文', locked: true })) }));
  await until(() => currentHook.dirty, 'citation matching dirty fence');
  assert(await currentHook.matchCitations() === null && mutations === 0, 'Matching saved or submitted an unsaved draft');
  assert(currentHook.project.sections[0].content === '未保存的人工正文' && currentHook.project.sections[0].locked, 'Blocked matching changed the draft or lock');
  passed('citation matching requires an explicitly saved draft without auto-saving or submitting');

  await loadCompletedCase('pending task blocks citation matching', false);
  currentHook.setProject(project => ({ ...project, runs: fixture().runs }));
  await until(() => currentHook.pending, 'citation matching pending fence');
  mutations = 0;
  mutationHandler = async () => { mutations += 1; throw new Error('Matching must not overlap another task'); };
  assert(await currentHook.matchCitations() === null && mutations === 0, 'Matching overlapped an active writing task');
  passed('citation matching cannot overlap an active writing task');

  await loadCompletedCase('saved locked prose allows citation-only matching', false);
  remote.sections[0].locked = true;
  await currentHook.reload(); await delay(40);
  const original = currentHook.project.sections[0].content;
  let releaseMatch, matchingStarted = false;
  mutations = 0;
  mutationHandler = async (url, options) => {
    mutations += 1;
    assert(url === '/api/writing/projects/writing-hook-fixture/match-citations' && options.method === 'POST', 'Matching used a save or another endpoint');
    assert(JSON.stringify(JSON.parse(options.body)) === JSON.stringify({ expectedVersion: 1 }), 'Matching lost its saved-version boundary');
    matchingStarted = true;
    await new Promise(resolve => { releaseMatch = resolve; });
    remote.runs = [{ id: 'match-run', jobId: 'match-job', kind: 'citation_match', status: 'running', applied: false }];
    return new Response(JSON.stringify({ project: clone(remote) }), { status: 202 });
  };
  const matching = currentHook.matchCitations();
  await until(() => matchingStarted, 'saved locked matching submitted');
  releaseMatch(); await matching;
  await until(() => currentHook.pending, 'matching enters existing task polling');
  assert(mutations === 1 && currentHook.project.sections[0].locked && currentHook.project.sections[0].content === original, 'Matching changed locked prose or sent another mutation');
  remote.version = 2; remote.sections[0].content = original + '[E1]'; remote.sections[0].version = 2; remote.runs[0].status = 'succeeded'; remote.runs[0].applied = true;
  await until(() => !currentHook.pending && currentHook.project.version === 2, 'matching completion updates clean saved draft');
  assert(currentHook.project.sections[0].content.replace(/\[E\d+\]/g, '') === original && currentHook.project.sections[0].locked, 'Matching completion changed prose or lock');
  passed('saved locked prose permits one version-bound matching request and adopts citation-only task completion');

  await loadCompletedCase('late citation matching preserves manual draft', false);
  let releaseLate;
  matchingStarted = false;
  mutationHandler = async () => {
    matchingStarted = true;
    await new Promise(resolve => { releaseLate = resolve; });
    remote.runs = [{ id: 'late-match-run', jobId: 'late-match-job', kind: 'citation_match', status: 'running', applied: false }];
    return new Response(JSON.stringify({ project: clone(remote) }), { status: 202 });
  };
  const lateMatching = currentHook.matchCitations();
  await until(() => matchingStarted, 'late matching submitted');
  currentHook.setProject(project => ({ ...project, sections: project.sections.map(section => ({ ...section, content: '核对期间的未保存人工修改', locked: true })) }));
  await until(() => currentHook.dirty, 'draft modified during matching submission');
  releaseLate(); await lateMatching;
  await until(() => currentHook.pending, 'late matching status merged');
  remote.version = 2; remote.sections[0].content += '[E1]'; remote.runs[0].status = 'succeeded'; remote.runs[0].applied = true;
  await until(() => !currentHook.pending, 'late matching completes');
  assert(currentHook.project.sections[0].content === '核对期间的未保存人工修改' && currentHook.project.sections[0].locked && currentHook.dirty && currentHook.remoteChanged, 'Late matching overwrote the manual draft or lock');
  passed('citation matching submission and completion preserve concurrent manual text and lock');
}

async function runExplicitReviewSequence() {
  for (const persisted of [false, true]) for (const concurrent of [false, true]) {
    await loadCompletedCase('explicit review queues then checks ' + persisted + ':' + concurrent, false);
    const sequence = [];
    mutationHandler = async (url, options) => {
      const payload = JSON.parse(options.body);
      if (url.endsWith('/match-citations')) {
        sequence.push('citations'); assert(payload.expectedVersion === 1, 'Review did not freeze current saved version');
        remote.runs = [{ id: 'explicit-review-run', kind: 'citation_match', status: 'running', jobId: 'explicit-review-job', applied: false }];
        return new Response(JSON.stringify({ project: clone(remote), run: clone(remote.runs[0]) }), { status: 202 });
      }
      if (url.endsWith('/review')) {
        sequence.push('rules'); assert(payload.expectedVersion === 2, 'Rule report did not use completed citation version');
        remote.review = { ...report(), version: 2, citationReviewCompleted: true };
        return new Response(JSON.stringify({ project: clone(remote), review: clone(remote.review) }), { status: 200 });
      }
      throw new Error('Unexpected explicit-review endpoint: ' + url);
    };
    await currentHook.review(); await until(() => currentHook.pending, 'explicit review queues citation model task');
    assert(sequence.join(',') === 'citations', 'Rules ran before asynchronous citation completion');
    if (concurrent) {
      currentHook.setProject(p => ({ ...p, sections: p.sections.map(section => ({ ...section, content: '明确核对期间的人工修改', locked: true })) }));
      await until(() => currentHook.dirty, 'edit during explicit review');
    }
    remote.version = 2; remote.runs[0].status = 'succeeded'; remote.runs[0].applied = true;
    if (persisted) remote.review = { ...report(), version: 2, citationReviewCompleted: true };
    await until(() => !currentHook.pending && (persisted || sequence.includes('rules')), 'rule report follows citation completion');
    assert(sequence.join(',') === (persisted ? 'citations' : 'citations,rules'), 'Explicit review duplicated a server report or reordered model/rule requests');
    if (concurrent) assert(currentHook.project.sections[0].content === '明确核对期间的人工修改' && currentHook.project.sections[0].locked && currentHook.dirty && currentHook.remoteChanged, 'Full review replaced a concurrent manual draft or lock');
    else assert(currentHook.project.review?.version === 2 && currentHook.project.review.citationReviewCompleted, 'Completed explicit review lost its exact-version report');
    passed('explicit review server/fallback report preserves version and concurrent draft: ' + persisted + ':' + concurrent);
  }
}

try {
  await runCase('same-project reopen continues polling', () => currentHook.selectProject(remote.id));
  await runCase('same-project reload continues polling', () => currentHook.reload());
  await runCase('reopen completion preserves dirty text and local lock', () => currentHook.selectProject(remote.id), true);
  await runSemanticSaveCases();
  await runReviewAndExportCases();
  await runPdfExportCases();
  await runCitationMatchingCases();
  await runExplicitReviewSequence();
  resultNode.dataset.status = 'passed';
  resultNode.textContent = JSON.stringify({ status: 'passed', tests: results.length, results }, null, 2);
} catch (error) {
  resultNode.dataset.status = 'failed';
  resultNode.textContent = JSON.stringify({ status: 'failed', results, error: error.stack || error.message }, null, 2);
} finally {
  root.unmount();
  window.fetch = originalFetch;
  HTMLAnchorElement.prototype.click = originalAnchorClick;
}
