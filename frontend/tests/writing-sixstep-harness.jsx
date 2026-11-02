import React, { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingWorkspace from '../src/Writing';
import { blankWritingProject } from '../src/useWriting';
import { initializeDemoMode, isDemoMode } from '../src/demo-mode';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';
import '../src/demo-theme.css';

const params = new URLSearchParams(location.search), theme = ['jade', 'glass', 'civic'].includes(params.get('theme')) ? params.get('theme') : 'jade';
if (isDemoMode()) initializeDemoMode();
else document.documentElement.dataset.theme = theme;
const clone = value => structuredClone(value), requests = [], calls = [];
const quote = '样本文档明确描述了知识整理的基本步骤。';
const body = quote + '[E1]\n\n这些步骤构成文章的结构。';
const evidence = { id: 'E1', sourceId: 'own', revisionId: 'rev-own', blockId: 'block-own', title: '用户选择的参考资料', quote, start: 0, end: quote.length, locator: { paragraph: 1 } };
const documents = [{ id: 'own', revisionId: 'rev-own', title: '用户选择的参考资料', summary: '介绍知识整理步骤及明确的适用边界。' }];
const items = [{ sourceId: 'own', revisionId: 'rev-own', title: documents[0].title, summary: documents[0].summary, channels: ['keyword'], reasons: ['知识整理步骤'] }, { sourceId: 'rec', revisionId: 'rev-rec', title: '系统推荐的补充资料', summary: '围绕资料筛选和文章写作提供事实性说明。', channels: ['summary', 'graph'], reasons: ['写作流程直接相关'] }];
const sections = (written = false) => [{ id: 'section-sixstep', title: '从资料到文章', goal: '说明资料如何帮助组织文章', mode: 'inherit', words: 6000, targetWords: 6000, version: 1, content: written ? body : '', contentHash: written ? 'fixture-hash' : '', locked: false, manual: false, evidence: written ? [clone(evidence)] : [], generationNotices: written ? [{ message: '材料缺口仅编辑可见，请结合原文核对。' }] : [], ...(written ? { citationBindings: { version: 1, content: body, contentHash: 'fixture-hash', mode: 'strict', items: [{ id: 'binding-one', evidenceId: 'E1', markerStart: quote.length, start: 0, end: quote.length, text: quote, status: 'unreviewed', reason: '尚未进行自动引用核对。' }] }, citationBindingState: 'unreviewed' } : {}) }];
function fixture(scenario) {
  const skippedReview = ['review-skipped', 'review-auto-skipped'].includes(scenario), automaticReview = scenario === 'review-auto-skipped';
  if (skippedReview) scenario = 'review';
  const p = { ...blankWritingProject(), id: 'writing-sixstep-fixture', version: 1, topic: '如何从资料整理形成可读文章', title: '如何从资料整理形成可读文章', sourceIds: ['own'], sources: [{ sourceId: 'own', revisionId: 'rev-own', title: documents[0].title }], materialSelection: { recommendationId: null, passages: [], queries: [], items: [], userSourceIds: ['own'], confirmed: false } };
  if (scenario === 'blank') Object.assign(p, { id: null, topic: '', title: '' });
  if (['restored-materials', 'outline', 'editor', 'review', 'automatic'].includes(scenario)) p.materialSelection = { recommendationId: 'rec-sixstep', passages: [], queries: ['知识整理的步骤', '资料与写作流程'], items: clone(items), userSourceIds: ['own'], confirmed: true };
  if (['outline', 'editor', 'review', 'automatic'].includes(scenario)) p.sections = sections(scenario !== 'outline');
  if (['editor', 'review', 'automatic'].includes(scenario)) { p.outlineConfirmed = true; p.activeSectionId = p.sections[0].id; }
  if (scenario === 'materials-empty') { p.sourceIds = []; p.sources = []; p.mode = 'expanded'; p.materialSelection.userSourceIds = []; }
  if (scenario === 'review') { p.illustrationReview = { status: 'skipped' }; p.review = report(p); }
  if (skippedReview) p.review.disposition = { status: 'skipped', version: p.version, reportId: p.review.reportId };
  if (automaticReview) { p.automation = 'automatic'; p.automatic = { status: 'completed_with_issues', stage: 'complete', report: { projectVersion: p.version, stages: [{ stage: 'review', status: 'skipped' }], issues: [] } }; }
  if (scenario === 'automatic') { p.automation = 'automatic'; p.automatic = { runId: 'run-auto', status: 'running', stage: 'materials' }; p.runs = [{ id: 'run-auto', jobId: 'job-auto', kind: 'automatic', status: 'running' }]; }
  if (scenario.startsWith('materials-cards')) {
    p.automation = 'manual';
    const ownIds = ['own-b', 'own', 'own-a', 'own-c'];
    const candidates = Array.from({ length: 6 }, (_, index) => ({ sourceId: 'card-rec-' + index, revisionId: 'rev-card-rec-' + index,
      title: '系统推荐卡片 ' + (index + 1), summary: '围绕网络安全治理、风险识别与协作机制的隔离模拟资料。', channels: ['keyword'], reasons: ['主题直接相关'] }));
    p.sourceIds = ['card-retained', 'own-a', 'own-c', 'own-b', 'own', 'card-rec-0'];
    p.sources = p.sourceIds.map(id => ({ sourceId: id, revisionId: 'rev-' + id,
      title: id === 'own' ? documents[0].title : id.startsWith('own-') ? '用户选择卡片 ' + id.slice(-1).toUpperCase() : id === 'card-retained' ? '已保存的历史推荐资料' : '系统推荐卡片 1',
      summary: '用于验证多列展示、来源顺序与固定原文版本的隔离资料。' }));
    p.materialSelection = { recommendationId: 'rec-card-grid', queries: ['网络安全 治理建议'], passages: [],
      userSourceIds: ownIds, items: [clone(items[0]), ...candidates, clone(items[0])], confirmed: true };
    p.workflowPendingSteps = scenario === 'materials-cards-readonly' || scenario === 'materials-cards-running' ? [] : ['materials'];
    if (scenario === 'materials-cards-empty') { p.sourceIds = []; p.sources = []; p.materialSelection = { ...p.materialSelection, userSourceIds: [], items: [], confirmed: false }; }
    if (scenario === 'materials-cards-running') { p.automation = 'automatic'; p.automatic = { runId: 'run-card-grid', status: 'running', stage: 'materials' }; p.runs = [{ id: 'run-card-grid', jobId: 'job-card-grid', kind: 'automatic', status: 'running' }]; }
  }
  return p;
}
function report(p) { return { reportId: 'report-' + p.version, version: p.version, checkedAt: '2026-10-25T08:00:00Z', citationReviewCompleted: false, authorConfirmed: false, disposition: null, status: 'needs_attention', wordCounts: { total: 42, target: p.targetWords, status: 'below_target', sections: [{ sectionId: p.sections[0]?.id, title: p.sections[0]?.title, actual: 42, target: p.targetWords, status: 'below_target' }] }, issues: [{ id: 'issue-one', code: 'citation_review_pending', severity: 'warning', sectionId: p.sections[0]?.id, paragraphIndex: 1, message: '此处事实与引用仍需核对。', acknowledged: false, confirmed: false }] }; }
const fingerprint = p => JSON.stringify([p.topic, p.title, p.articleTitle, p.purpose, p.targetWords, p.automation, p.mode, p.sourceIds, p.materialSelection, p.outlineConfirmed, p.sections.map(s => [s.id, s.title, s.content, s.locked, s.goal, s.words, s.mode])]);
window.fetch = async (url, options = {}) => {
  const path = typeof url === 'string' ? url : url.url, payload = options.body ? JSON.parse(options.body) : null;
  requests.push({ url: path, method: options.method || 'GET', body: payload });
  let result;
  if (path === '/api/writing/assist/title') result = { title: '从资料整理到清楚表达' };
  else if (path === '/api/writing/assist/material-queries') result = { queries: ['知识整理的步骤', '资料与写作流程'] };
  else if (path === '/api/retrieval/recommendations') result = { id: 'rec-sixstep', items: clone(items), queries: payload.queries, warnings: [] };
  else if (path.startsWith('/api/documents/')) { const id = path.split('/')[3].split('?')[0]; result = { document: { id, revisionId: 'rev-' + id, title: id === 'own' ? documents[0].title : items[1].title, summary: '资料摘要；本页正文均为隔离模拟内容。', blocks: [{ id: 'block-' + id, text: quote }, { id: 'block-' + id + '-2', text: '资料的明确内容用于写作，具体事实与观点在检查校验步骤进行核对。' }] } }; }
  else throw new Error('Unexpected isolated request: ' + path);
  return new Response(JSON.stringify(result), { status: 200, headers: { 'Content-Type': 'application/json' } });
};
function Harness() {
  const initial = params.get('scenario') || 'blank';
  const [project, updateProject] = useState(() => fixture(initial)), [saved, setSaved] = useState(() => fixture(initial));
  const [stage, setStage] = useState(initial.includes('materials') ? 'materials' : initial === 'outline' ? 'outline' : initial.startsWith('review') ? 'review' : ['editor', 'automatic'].includes(initial) ? 'editor' : 'brief');
  const [toast, setToast] = useState(''); const current = useRef(project); current.current = project;
  const setProject = change => { const next = typeof change === 'function' ? change(current.current) : change; current.current = next; updateProject(next); };
  const commit = (patch = {}, bump = true) => { const next = { ...current.current, ...patch, id: current.current.id || 'writing-sixstep-fixture', version: current.current.version + (bump ? 1 : 0) }; current.current = next; updateProject(next); setSaved(clone(next)); return next; };
  const record = (name, body) => { calls.push({ name, body: clone(body || {}) }); };
  const writing = { project, savedProject: saved, setProject, projects: [saved], loading: false, busy: '', pending: project.runs.some(run => ['queued', 'running'].includes(run.status)), dirty: fingerprint(project) !== fingerprint(saved), remoteChanged: false, error: '', capabilities: {}, generationPlan: null, planLoading: false, planError: '', clearError() {}, refreshList: async () => {},
    save: async () => { record('save'); return commit(); },
    confirmStep: async step => { record('confirm-step', { step }); return { project: commit({ workflowPendingSteps: (current.current.workflowPendingSteps || []).filter(id => id !== step) }, false) }; },
    generateTitle: async () => { record('title'); const response = await fetch('/api/writing/assist/title', { method: 'POST', body: JSON.stringify({ topic: current.current.topic }) }); const value = await response.json(); setProject(p => ({ ...p, articleTitle: value.title, title: value.title })); return value; },
    generateOutline: async () => { record('outline'); return { project: commit({ sections: sections(false), activeSectionId: 'section-sixstep', outlineConfirmed: false }) }; },
    confirmOutline: async () => { record('confirm-outline'); return { project: commit({ outlineConfirmed: true }) }; },
    confirmOutlineOnly: async () => { record('confirm-outline-only'); return { project: commit({ outlineConfirmed: true }) }; },
    generateSection: async id => { record('section', { id }); return { project: commit({ sections: sections(true) }) }; },
    generateDocument: async () => { record('document'); return { project: commit({ sections: sections(true) }) }; },
    review: async () => { record('review'); return { project: commit({ review: report(current.current) }, false) }; },
    matchCitations: async () => { record('citations'); return { project: current.current }; },
    skipReview: async () => { record('skip-review'); const review = { ...report(current.current), disposition: { status: 'skipped' } }; return { project: commit({ review }, false) }; },
    confirmReview: async ids => { record('confirm-review', { ids }); const review = report(current.current); review.authorConfirmed = !ids?.length; review.issues = review.issues.map(issue => ({ ...issue, confirmed: !ids?.length || ids.includes(issue.id), acknowledged: !ids?.length || ids.includes(issue.id) })); return { project: commit({ review }, false) }; },
    exportReview: async () => { record('export-report'); return true; },
    exportDocument: async args => { record('export', args); return { format: args.format, version: current.current.version, reviewStatus: 'needs_attention', requiresDraftConsent: args.allowIssues }; },
    completeIllustrations: async args => { record('images-complete', args); return { project: commit({ illustrationReview: { status: args.skip ? 'skipped' : 'complete' } }) }; },
    startAutomatic: async () => { record('automatic'); return { project: commit({ automatic: { runId: 'run-auto', status: 'running', stage: 'materials' }, runs: [{ id: 'run-auto', jobId: 'job-auto', kind: 'automatic', status: 'running' }] }) }; },
    cancelRun: async () => { record('cancel'); return { project: commit({ runs: [], automatic: { ...current.current.automatic, status: 'cancelled' } }) }; },
    newProject: () => { const next = fixture('blank'); setProject(next); setSaved(clone(next)); return true; }, selectProject: async () => current.current,
  };
  window.__sixstep = { get project() { return clone(current.current); }, get stage() { return stage; }, get requests() { return clone(requests); }, get calls() { return clone(calls); }, clearRequests() { requests.length = 0; calls.length = 0; }, loadScenario(name) { const next = fixture(name); setProject(next); setSaved(clone(next)); setStage(name.includes('materials') ? 'materials' : name === 'outline' ? 'outline' : ['editor', 'review', 'automatic'].includes(name) ? 'editor' : 'brief'); }, setProject, setStage };
  return <div className="workspace-shell sixstep-fixture-shell"><main className="app-main view-writing"><WritingWorkspace stage={stage} onStageChange={setStage} writing={writing} documents={documents} selectedIds={['own']} models={[{ id: 'fixture-model', name: '隔离模拟模型', model: 'fixture' }]} onOpenDoc={value => record('open-doc', value)} onToast={setToast}/></main>{toast && <output className="sixstep-fixture-toast">{toast}</output>}</div>;
}
createRoot(document.getElementById('sixstep-root')).render(<React.StrictMode><Harness/></React.StrictMode>);
