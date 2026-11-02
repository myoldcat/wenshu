import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingWorkspace from '../src/Writing';
import { useWriting } from '../src/useWriting';
import { initializeDemoMode, isDemoMode } from '../src/demo-mode';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';
import '../src/demo-theme.css';

// This harness replaces only the HTTP/download boundaries. The Workspace and
// every writing callback are the real production components and hook.
const params = new URLSearchParams(location.search);
if (isDemoMode()) initializeDemoMode();
else document.documentElement.dataset.theme = ['jade', 'glass', 'civic'].includes(params.get('theme')) ? params.get('theme') : 'jade';
const clone = value => structuredClone(value);
const requests = [], downloads = [], unexpected = [], pendingTitles = new Map();
let writing, currentStage, switchFixture, setPresentationGuards, serial = 0, requestSerial = 0;
let remote = new Map();
let interruptConflictOnce = false;

function reviewReport(project) {
  const issue = { id: 'check-preparation-one', code: 'citation_review_pending', severity: 'warning',
    sectionId: project.sections[0]?.id, paragraphIndex: 1, message: '本段事实与明确引用尚未核对。', confirmed: false, acknowledged: false };
  return { reportId: 'report-preparation-' + project.version, version: project.version, checkedAt: '2026-10-25T09:00:00Z',
    status: 'needs_attention', semanticVerified: false, citationReviewCompleted: false, authorConfirmed: false,
    disposition: null, requiresDraftConsent: true, issues: [issue], counts: { errors: 0, warnings: 1 },
    wordCounts: { total: 24, target: 6000, min: 4800, max: 7200, status: 'below_target', sections: [], method: '隔离样例规则检查。' },
    coverage: { paragraphs: 1, validlyCitedParagraphs: 0, pendingParagraphs: 1 }, notice: '隔离样例；不调用真实模型。' };
}

function savedProject(id = 'writing-preparation-other', withBody = false) {
  const title = withBody ? '已保存的核对回归文章' : '另一个写作项目';
  const project = { id, version: 3, workflowVersion: 'writing-six-step-v1', title, mode: 'extended', modelId: null,
    brief: { topic: withBody ? '核对与报告真实接线' : '另一个项目的主题', articleTitle: title, assistanceIds: [],
      automaticOptions: { restrictToSelectedSources: false, includeImages: true },
      purpose: '保留用户输入和正文。', targetWords: 6000, style: '清晰克制', template: '观点文章',
      workflow: 'chapter', automation: 'manual', templateSections: [], sampleReferenceText: '', sampleStructureApplied: false },
    sourceIds: [], sources: [], materialSelection: { recommendationId: null, passages: [], queries: [], items: [], userSourceIds: [], confirmed: true },
    outlineConfirmed: withBody, runs: [], illustrations: [], illustrationReview: withBody ? { status: 'skipped' } : null,
    sections: withBody ? [{ id: 'section_preparation_review', version: 1, title: '供作者核对的正文', goal: '', mode: 'inherit',
      targetWords: 6000, content: '这一段文字用于验证作者确认与报告导出的实际接线。', evidence: [], locked: false, manual: true }] : [],
    review: null, createdAt: '2026-10-25T08:00:00Z', updatedAt: '2026-10-25T09:00:00Z' };
  if (withBody) project.review = reviewReport(project);
  return project;
}

function interactiveProject(name) {
  const project = savedProject('writing-interactive-short');
  const generated = ['interactive-generated', 'interactive-legacy'].includes(name);
  project.title = '交互式整稿生成隔离验收';
  project.brief = { ...project.brief, topic: '先生成整稿，再逐章编辑确认', articleTitle: project.title,
    targetWords: name === 'interactive-boundary' ? 10000 : 9999 };
  project.outlineConfirmed = generated;
  project.workflowPendingSteps = ['body'];
  project.sections = ['first', 'second'].map((key, index) => ({ id: 'section_interactive_' + key, version: 1,
    title: index ? '第二章：逐章确认' : '第一章：整稿生成', goal: '', mode: 'inherit', targetWords: index ? 4999 : 5000,
    content: generated ? `这是一次整稿生成后保存的第${index + 1}章正文。` : '', evidence: [], manual: false, locked: false }));
  if (name === 'interactive-generated') {
    project.chapterConfirmationPolicy = 'chapter-confirmation-v1';
    project.sections.forEach(section => { section.chapterConfirmed = false; });
  }
  if (name === 'interactive-capacity') project.fixtureCapacityBlocked = true;
  return project;
}

function chapterFlowProject(name) {
  const project = interactiveProject('interactive-legacy');
  project.id = 'writing-chapter-flow';
  project.title = '逐章确认与全文预览隔离验收';
  project.brief.articleTitle = project.title;
  project.brief.interactiveGeneration = 'chapter';
  project.brief.targetWords = name === 'chapter-flow-short' ? 9999 : 10000;
  if (name === 'chapter-flow-empty-next') project.sections[1].content = '';
  if (name === 'chapter-flow-single-last') project.sections = project.sections.slice(0, 1);
  if (name === 'chapter-flow-locked') project.sections[0].locked = true;
  if (name === 'chapter-flow-running') project.runs = [{ id: 'run-chapter-flow', jobId: 'job-chapter-flow',
    kind: 'section', sectionId: project.sections[0].id, status: 'running', applied: false }];
  return project;
}

function remainingFlowProject(name) {
  const project = chapterFlowProject(name === 'remaining-flow-short' ? 'chapter-flow-short' : 'chapter-flow-long');
  project.id = 'writing-remaining-flow'; project.title = '剩余章节依次生成隔离验收'; project.brief.articleTitle = project.title;
  project.sections = Array.from({ length: name === 'remaining-flow-protected' ? 5 : 4 }, (_, index) => ({
    id: 'section_remaining_' + (index + 1), version: 1, title: '第' + (index + 1) + '章：顺序写作', goal: '',
    mode: 'inherit', targetWords: 2500, content: index === 0 ? '第一章已经生成并保存的正文。' : '', evidence: [], manual: false, locked: false }));
  project.activeSectionId = project.sections[0].id;
  if (name === 'remaining-flow-start-empty') project.sections[0].content = '';
  if (name === 'remaining-flow-middle') {
    project.sections[0].content = ''; project.sections[1].content = '第二章已经保存的正文。'; project.activeSectionId = project.sections[1].id;
  }
  if (name === 'remaining-flow-protected') {
    project.sections[1].manual = true; project.sections[2].locked = true; project.sections[3].content = '第四章已有正文，应予保留。';
  }
  if (name === 'remaining-flow-last') {
    project.sections.forEach((section, index) => { section.content = `已经保存的第${index + 1}章正文。`; });
    project.activeSectionId = project.sections.at(-1).id;
  }
  return project;
}

function interactiveOptionsProject(name) {
  const project = interactiveProject('interactive-short');
  const outline = name.startsWith('options-interactive-outline-');
  const boundary = name === 'options-interactive-boundary';
  project.brief.targetWords = outline ? 9999 : boundary ? 10000 : 6000;
  project.brief.interactiveGeneration = name.endsWith('-chapter') ? 'chapter' : 'single';
  project.sections.forEach((section, index) => {
    section.targetWords = outline ? index ? 4999 : 5000 : project.brief.targetWords / 2;
  });
  project.workflowPendingSteps = outline ? ['body'] : ['brief', 'body'];
  return project;
}

const documentStrategy = project => project.brief.targetWords < 10000
  && (project.brief.automation === 'automatic' || project.brief.interactiveGeneration !== 'chapter') ? 'single' : 'sequential';

// Saved A is a user selection; B is a previously accepted recommendation. Both
// remain in the historical fixed scope so changing next-run options can be
// checked without fabricating a server-side rewrite of the existing article.
function optionsProject(name) {
  const project = savedProject('writing-options-v2', name === 'v2-existing' || name === 'v2-readonly');
  project.brief.automation = 'automatic';
  project.brief.automaticOptions = { useSelectedSources: true, useRecommendedSources: true, mode: 'strict', includeImages: true };
  project.mode = 'strict';
  project.sourceIds = ['source-preparation', 'source-old-recommendation'];
  project.sources = [
    { sourceId: 'source-preparation', revisionId: 'revision-preparation', title: '隔离选择的原始资料' },
    { sourceId: 'source-old-recommendation', revisionId: 'revision-old-recommendation', title: '上轮采用的推荐资料' },
  ];
  project.materialSelection = { recommendationId: 'recommendation-previous-run', queries: ['上轮检索条件'], passages: [],
    items: [{ sourceId: 'source-old-recommendation', reasonTags: ['上轮已采用'] }], userSourceIds: ['source-preparation'], confirmed: true };
  if (name === 'v2-default') delete project.brief.automaticOptions;
  if (name === 'v2-legacy-restricted' || name === 'v2-legacy-unrestricted') {
    project.brief.automaticOptions = { restrictToSelectedSources: name === 'v2-legacy-restricted', includeImages: false };
  }
  if (name === 'v2-no-fixed') { project.sourceIds = []; project.sources = []; project.materialSelection.userSourceIds = []; }
  if (name === 'v2-empty-user') project.materialSelection.userSourceIds = [];
  if (name === 'v2-legacy-source-list') delete project.materialSelection.userSourceIds;
  if (name === 'v2-zero') {
    project.brief.automaticOptions = { useSelectedSources: false, useRecommendedSources: false, mode: 'strict', includeImages: false };
    project.mode = 'extended';
  }
  if (project.sections.length) {
    project.sections[0].content = '旧稿引用已经采用的推荐资料，新的资料选择不得改写此段。[E1]';
    project.sections[0].locked = true;
    project.sections[0].evidence = [{ id: 'E1', sourceId: 'source-old-recommendation', revisionId: 'revision-old-recommendation',
      blockId: 'block-old-recommendation', title: '上轮采用的推荐资料', quote: '旧稿引用已经采用的推荐资料，新的资料选择不得改写此段。',
      start: 0, end: '旧稿引用已经采用的推荐资料，新的资料选择不得改写此段。'.length, locator: { paragraph: 1 } }];
    project.review = null;
  }
  if (name === 'v2-readonly') {
    const run = { id: 'run-options-v2', jobId: 'job-options-v2', kind: 'automatic', status: 'running', stage: 'chapters', applied: false };
    project.runs = [run];
    project.automatic = { runId: run.id, jobId: run.jobId, status: 'running', stage: 'chapters' };
  }
  return project;
}

// A fully completed manuscript retains a narrow historical source scope, a
// locked second chapter, an accepted figure, and a current author-confirmed
// report. Browsing this fixture must never need a generated condition or model.
function completedProject(name = 'completed') {
  const project = savedProject('writing-completed-readonly', true);
  project.title = '六步已完成的只读验收文稿';
  project.brief = { ...project.brief, topic: '固定资料与写作步骤的只读查看', articleTitle: project.title,
    purpose: '保留原文、引用、章节锁与已确认事项。', automaticOptions: null };
  project.mode = 'strict'; project.workflowPendingSteps = [];
  project.sourceIds = ['source-preparation', 'source-old-recommendation'];
  project.sources = [
    { sourceId: 'source-preparation', revisionId: 'revision-preparation', title: '隔离选择的原始资料', blockIds: ['block-preparation'] },
    { sourceId: 'source-old-recommendation', revisionId: 'revision-old-recommendation', title: '上轮采用的推荐资料' },
  ];
  project.materialSelection = { recommendationId: null,
    passages: [{ sourceId: 'source-preparation', revisionId: 'revision-preparation', blockId: 'block-preparation' }],
    queries: [], items: [{ sourceId: 'source-old-recommendation', revisionId: 'revision-old-recommendation', title: '上轮采用的推荐资料', summary: '', reasons: [], channels: [] }],
    userSourceIds: ['source-preparation'], confirmed: true, assistanceIds: [] };
  const chapters = [
    ['section_completed_first', '已经完成的第一章', '说明固定原文与文章的关系', '这一章保留已经保存的事实、原文与引文。', 'source-preparation', 'revision-preparation', 'block-preparation', false],
    ['section_completed_locked', '已经锁定的第二章', '保留人工确认与章节锁定', '本例保存了固定版本的原文与章节锁定状态。', 'source-old-recommendation', 'revision-old-recommendation', 'block-old-recommendation', true],
  ];
  project.sections = chapters.map(([id, title, goal, quote, sourceId, revisionId, blockId, locked], index) => ({
    id, version: 2, title, goal, mode: 'inherit', targetWords: 1000, content: quote + '[E1]',
    contentHash: 'fixture-completed-content-' + index, citationBindingState: 'unreviewed', locked, manual: true,
    evidence: [{ id: 'E1', sourceId, revisionId, blockId, title: project.sources[index].title, quote, start: 0, end: quote.length, locator: { paragraph: 1 } }],
  }));
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="180" viewBox="0 0 600 180"><rect width="600" height="180" fill="#e6eee7"/><path d="M80 125V60h100v65m60 0V40h100v85m60 0V80h100v45" fill="none" stroke="#52745f" stroke-width="9"/></svg>';
  project.illustrations = [{ id: 'figure-completed', sectionId: project.sections[0].id, status: 'accepted', locked: true,
    sourceType: 'references', sourceId: 'source-preparation', revisionId: 'revision-preparation', sourceTitle: project.sources[0].title,
    caption: '隔离验收示意图', altText: '三个不同高度的示意框', credit: '隔离测试图，不代表真实数据', url: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg),
    anchor: { paragraphIndex: 0, paragraphExact: project.sections[0].content, contentHash: project.sections[0].contentHash } }];
  project.illustrationReview = { status: 'complete', checkedAt: '2026-10-25T09:00:00Z' };
  project.review = reviewReport(project);
  project.review.authorConfirmed = true; project.review.requiresDraftConsent = false;
  project.review.issues = project.review.issues.map(issue => ({ ...issue, confirmed: true, acknowledged: true }));
  project.review.disposition = { status: 'acknowledged', version: project.version, reportId: project.review.reportId };
  if (name === 'completed-legacy-materials') delete project.materialSelection.confirmed;
  return project;
}
function manualGenerationProject(name) {
  const kind = name.split('-')[1], project = completedProject('completed');
  project.id = 'writing-manual-' + kind;
  project.title = '手动正文任务只读验收 · ' + kind;
  project.brief = { ...project.brief, articleTitle: project.title, automation: 'manual', workflow: kind === 'document' ? 'oneshot' : 'chapter' };
  project.workflowPendingSteps = ['body'];
  project.review = null;
  if (kind !== 'edit') project.sections[1] = { ...project.sections[1], title: '等待生成的第二章', content: '',
    contentHash: null, evidence: [], manual: false, locked: false };
  const sectionId = kind === 'section' ? project.sections[1].id : kind === 'edit' ? project.sections[0].id : null;
  project.runs = [{ id: 'run-manual-' + kind, jobId: 'job-manual-' + kind, kind, sectionId,
    status: kind === 'edit' ? 'queued' : 'running', applied: false,
    ...(kind === 'edit' ? { operation: 'polish' } : {}), createdAt: '2026-10-27T10:00:00Z' }];
  return project;
}

const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().filter(key => value[key] !== undefined).map(key => [key, canonical(value[key])])) : value;
const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const sortedIds = value => [...new Set(value || [])].sort();
const modeOf = value => value === 'expanded' ? 'extended' : value;
function sourceScope(project) {
  const selection = project.materialSelection || {}, chosen = new Set(project.sourceIds);
  return { mode: modeOf(project.mode), sourceIds: sortedIds(project.sourceIds), passages: selection.passages || [],
    userSourceIds: sortedIds(Object.hasOwn(selection, 'userSourceIds') ? selection.userSourceIds : project.sourceIds).filter(id => chosen.has(id)) };
}
function markPending(project, ...steps) { project.workflowPendingSteps = sortedIds([...(project.workflowPendingSteps || []), ...steps]); }
function saveVersion(project, request) {
  project.version += 1; project.updatedAt = '2026-10-25T10:' + String(project.version).padStart(2, '0') + ':00Z'; project.review = null;
  request.persisted = true; request.finishedAt = performance.now();
}

function workflowProject(name) {
  const timings = { brief: { durationMs: 1200, kind: 'confirmation' }, materials: { durationMs: 80000, kind: 'automatic-stage' },
    outline: { durationMs: 40000, kind: 'generation' }, body: { durationMs: 155876, kind: 'generation' },
    images: { durationMs: 17000, kind: 'generation' }, review: { durationMs: null, kind: 'generation' } };
  if (name === 'workflow-body') {
    const project = interactiveProject('interactive-generated');
    project.sections[0].chapterConfirmed = true;
    project.workflowTimings = timings;
    return project;
  }
  if (['workflow-materials', 'workflow-brief'].includes(name)) {
    const project = savedProject('writing-workflow-pending');
    project.materialSelection.confirmed = false;
    project.workflowPendingSteps = [name === 'workflow-brief' ? 'brief' : 'materials'];
    project.workflowTimings = timings;
    return project;
  }
  const project = completedProject('completed');
  project.workflowTimings = timings;
  project.id = 'writing-workflow-review';
  project.title = '底部流程带提醒隔离验收';
  if (name === 'workflow-reading-long') {
    project.sections.forEach((section, index) => {
      const first = section.content;
      section.content = [first, ...Array.from({ length: 18 }, (_, paragraph) => `第${paragraph + 2}段阅读定位。` + section.evidence[0].quote.repeat(8) + '[E1]')].join('\n\n');
      section.contentHash = 'fixture-long-content-' + index;
    });
    project.illustrations[0].anchor.contentHash = project.sections[0].contentHash;
    return project;
  }
  if (name === 'workflow-confirmed' || name === 'workflow-untimed') {
    if (name === 'workflow-untimed') project.workflowTimings = {};
    return project;
  }
  if (name === 'workflow-states') {
    project.workflowPendingSteps = ['body']; project.illustrationReview = { status: 'skipped' }; project.review = null;
    return project;
  }
  if (name === 'workflow-unchecked') { project.review = null; return project; }
  project.review.authorConfirmed = false;
  project.review.requiresDraftConsent = true;
  project.review.issues = [
    { ...project.review.issues[0], id: 'workflow-check-1', confirmed: false, acknowledged: false },
    { ...project.review.issues[0], id: 'workflow-check-2', confirmed: false, acknowledged: false, message: '请核对这一段的引用位置。' },
    { ...project.review.issues[0], id: 'workflow-check-3', confirmed: true, acknowledged: true },
  ];
  project.review.disposition = { status: 'skipped', version: project.version, reportId: project.review.reportId };
  if (name === 'workflow-review-auto-skipped') {
    project.brief.automation = 'automatic';
    project.automatic = { runId: 'run-review-history', jobId: 'job-review-history', status: 'completed_with_issues', stage: 'complete',
      report: { projectVersion: project.version, status: 'completed_with_issues',
        stages: [{ stage: 'review', status: 'skipped', message: '本次自动写作按设置跳过校验。' }], issues: [] } };
    project.runs = [{ id: 'run-review-history', jobId: 'job-review-history', kind: 'automatic', status: 'succeeded', stage: 'complete', applied: true }];
  }
  if (name === 'workflow-stale') project.review.version -= 1;
  if (name === 'workflow-skipped') project.review.issues = [];
  if (name === 'workflow-images') { project.review = null; project.illustrations = []; project.illustrationReview = null; }
  return project;
}

function outlineRegenerationProject(name) {
  const project = savedProject('writing-outline-regeneration');
  project.brief = { ...project.brief, topic: '基层数据治理调研', articleTitle: '基层数据治理调研',
    structureTemplate: 'research-report-decision', template: '调研报告（决策参考型）',
    templateSections: ['现状与趋势', '风险研判', '意见建议（分近期、中期）'] };
  project.title = project.brief.articleTitle;
  project.outlineConfirmed = name === 'outline-regen-confirmed';
  project.workflowPendingSteps = name === 'outline-regen-existing' ? ['brief', 'materials', 'body']
    : name === 'outline-regen-pending-brief' ? ['brief', 'body']
    : name === 'outline-regen-pending-materials' ? ['materials', 'body'] : ['body'];
  const override = name === 'outline-regen-override';
  if (override) project.brief.purpose = '全文仅分为“治理现状”“改进路径”两章，不要章外引言；按作者指定顺序展开。';
  project.sections = (override ? ['治理现状', '改进路径'] : ['作者保留的现状判断', '作者保留的风险研判', '作者保留的意见建议'])
    .map((title, index) => ({ id: 'section_outline_original_' + index, version: 1, title,
      goal: '保留已有章节，只有显式重新生成才替换。', mode: 'inherit', targetWords: 1500,
      content: name === 'outline-regen-body' && index === 0 ? '已有保存正文。' : '', evidence: [],
      manual: name === 'outline-regen-manual' && index === 0,
      locked: name === 'outline-regen-locked' && index === 0 }));
  if (name === 'outline-regen-running') {
    project.brief.automation = 'automatic';
    project.automatic = { runId: 'run-outline-automatic', jobId: 'job-outline-automatic', status: 'running', stage: 'outline' };
    project.runs = [{ id: 'run-outline-automatic', jobId: 'job-outline-automatic', kind: 'automatic', status: 'running', stage: 'outline', applied: false }];
  }
  if (name === 'outline-regen-legacy') delete project.workflowPendingSteps;
  return project;
}

function configure(name) {
  remote = new Map();
  if (name.startsWith('remaining-flow-')) {
    const project = remainingFlowProject(name); remote.set(project.id, project);
  } else if (name.startsWith('chapter-flow-')) {
    const project = chapterFlowProject(name); remote.set(project.id, project);
  } else if (name.startsWith('manual-') && name.endsWith('-active')) {
    const project = manualGenerationProject(name); remote.set(project.id, project);
  } else if (name.startsWith('outline-regen-')) {
    const project = outlineRegenerationProject(name); remote.set(project.id, project);
  } else if (name.startsWith('options-interactive-')) {
    const project = interactiveOptionsProject(name); remote.set(project.id, project);
  } else if (name.startsWith('workflow-')) {
    const project = workflowProject(name); remote.set(project.id, project);
  } else if (name.startsWith('interactive-')) {
    const project = interactiveProject(name); remote.set(project.id, project);
  } else if (name.startsWith('completed')) {
    const project = completedProject(name); remote.set(project.id, project);
  } else if (name.startsWith('v2-')) {
    const project = optionsProject(name); remote.set(project.id, project);
  } else if (name === 'review' || name === 'editor') {
    const project = savedProject('writing-preparation-review', true); remote.set(project.id, project);
  } else if (name === 'other') {
    const project = savedProject(); remote.set(project.id, project);
  } else if (name === 'selected' || name === 'materials-empty') {
    const project = savedProject('writing-preparation-selected');
    project.materialSelection.confirmed = false;
    if (name === 'selected') {
      project.mode = 'strict'; project.sourceIds = ['source-preparation'];
      project.sources = [{ sourceId: 'source-preparation', revisionId: 'revision-preparation', title: '隔离选择的原始资料' }];
      project.materialSelection.userSourceIds = ['source-preparation'];
    }
    remote.set(project.id, project);
  } else if (name === 'automatic') {
    const project = savedProject('writing-preparation-automatic', true);
    project.brief.automation = 'automatic';
    project.sourceIds = ['source-preparation'];
    project.sources = [{ sourceId: 'source-preparation', revisionId: 'revision-preparation', title: '隔离选择的原始资料' }];
    project.sections[0].content = '自动写作已经保存了第一章的正文。[E1]';
    project.sections[0].manual = false;
    project.sections[0].evidence = [{ id: 'E1', sourceId: 'source-preparation', revisionId: 'revision-preparation', blockId: 'block-preparation',
      title: '隔离选择的原始资料', quote: '自动写作已经保存了第一章的正文。', start: 0, end: '自动写作已经保存了第一章的正文。'.length, locator: { paragraph: 1 } }];
    project.sections.push({ id: 'section_preparation_pending', version: 1, title: '仍在编写的章节', goal: '等待后续生成', mode: 'inherit',
      targetWords: 1000, content: '', evidence: [], locked: true, manual: false });
    project.review = null;
    project.automatic = { runId: 'run-preparation-automatic', jobId: 'job-preparation-automatic', status: 'running', stage: 'chapters' };
    project.runs = [{ id: 'run-preparation-automatic', jobId: 'job-preparation-automatic', kind: 'automatic', status: 'running', stage: 'chapters', applied: false }];
    remote.set(project.id, project);
  }
}

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
window.fetch = async (input, options = {}) => {
  const path = typeof input === 'string' ? input : input.url;
  const method = options.method || 'GET', body = options.body ? JSON.parse(options.body) : null;
  const request = { id: ++requestSerial, path, method, body: clone(body), startedAt: performance.now() };
  requests.push(request);
  if (method === 'GET' && path === '/api/writing/projects') return json({ projects: [...remote.values()].map(clone) });
  const document = path.match(/^\/api\/documents\/([^/?]+)(?:\?.*)?$/);
  if (method === 'GET' && document) return json({ document: { id: document[1], revisionId: document[1] === 'source-preparation' ? 'revision-preparation' : 'revision-old-recommendation', title: document[1] === 'source-preparation' ? '隔离选择的原始资料' : '上轮采用的推荐资料', blocks: [{ id: document[1] === 'source-preparation' ? 'block-preparation' : 'block-old-recommendation', text: document[1] === 'source-preparation' ? '这一章保留已经保存的事实、原文与引文。' : '本例保存了固定版本的原文与章节锁定状态。' }] } });
  const detail = path.match(/^\/api\/writing\/projects\/([^/]+)$/);
  if (method === 'GET' && detail) {
    const project = remote.get(detail[1]); return project ? json({ project: clone(project) }) : json({ detail: '隔离项目不存在。' }, 404);
  }
  const generationPlan = path.match(/^\/api\/writing\/projects\/([^/]+)\/generation-plan$/);
  if (method === 'GET' && generationPlan) {
    const project = remote.get(generationPlan[1]);
    if (!project) return json({ detail: '隔离项目不存在。' }, 404);
    const eligible = project.sections.filter(section => !section.content?.trim() && !section.locked && !section.manual);
    return json({ plan: { strategy: documentStrategy(project), targetWords: project.brief.targetWords,
      eligibleSections: eligible.map(section => ({ sectionId: section.id, title: section.title })),
      skippedSections: project.sections.filter(section => !eligible.includes(section)).map(section => ({ sectionId: section.id, title: section.title, reason: 'existing_content' })),
      canGenerate: eligible.length > 0 && !project.fixtureCapacityBlocked,
      blockingReasons: project.fixtureCapacityBlocked ? ['当前模型容量不足，无法一次生成全文。请调整模型容量后再试。'] : [] } });
  }
  const generateOutline = path.match(/^\/api\/writing\/projects\/([^/]+)\/generate-outline$/);
  if (method === 'POST' && generateOutline) {
    const project = remote.get(generateOutline[1]);
    if (!project || body.expectedVersion !== project.version) return json({ detail: '纲要基稿版本不一致。' }, 409);
    if (project.sections.some(section => section.content || section.manual || section.locked)) return json({ detail: '已有正文或锁定章节受保护。' }, 409);
    request.savedBrief = clone(project.brief);
    request.replacedSections = clone(project.sections);
    const count = project.brief.purpose === '全文仅分为“治理现状”“改进路径”两章，不要章外引言；按作者指定顺序展开。'
      ? 2 : ({ 'research-report-problem': 5, 'research-report-experience': 4, 'research-report-decision': 3 }[project.brief.structureTemplate] || 3);
    project.sections = Array.from({ length: count }, (_, index) => ({ id: 'section_outline_regenerated_' + index,
      version: 1, title: count === 2 ? ['治理现状', '改进路径'][index] : '基层数据治理的具体论述方向' + (index + 1),
      goal: '模拟模型按已保存写作要求规划。', mode: 'inherit', targetWords: 1200, content: '', evidence: [], manual: false, locked: false }));
    project.outlineConfirmed = false;
    project.runs = [{ id: 'run-outline-regenerated', kind: 'outline', status: 'succeeded', applied: true }];
    markPending(project, 'body'); saveVersion(project, request);
    return json({ project: clone(project) });
  }
  const generateDocument = path.match(/^\/api\/writing\/projects\/([^/]+)\/generate-document$/);
  if (method === 'POST' && generateDocument) {
    const project = remote.get(generateDocument[1]);
    if (!project || body.expectedVersion !== project.version) return json({ detail: '整稿生成基稿版本不一致。' }, 409);
    if (project.fixtureCapacityBlocked) return json({ detail: '当前模型容量不足，无法一次生成全文。请调整模型容量后再试。' }, 422);
    if (body.confirmOutline) project.outlineConfirmed = true;
    const fromIndex = body.remainingFromSectionId ? project.sections.findIndex(section => section.id === body.remainingFromSectionId) : 0;
    if (fromIndex < 0) return json({ detail: '剩余章节起点已改变。' }, 409);
    const strategy = body.remainingFromSectionId ? 'sequential' : documentStrategy(project);
    if (project.brief.automation !== 'automatic' && project.brief.targetWords < 10000 && strategy === 'single') project.chapterConfirmationPolicy = 'chapter-confirmation-v1';
    const steps = project.sections.map((section, index) => {
      if (index < fromIndex) return { sectionId: section.id, title: section.title, status: 'skipped', reason: '不在本次剩余章节范围内' };
      if (section.content?.trim() || section.locked || section.manual) return { sectionId: section.id, title: section.title, status: 'skipped', reason: 'existing_content' };
      section.content = `这是一次整稿生成后保存的第${index + 1}章正文。`; section.version += 1;
      if (project.chapterConfirmationPolicy) section.chapterConfirmed = false;
      return { sectionId: section.id, title: section.title, status: 'applied' };
    });
    markPending(project, 'body');
    const plan = { strategy, targetWords: project.brief.targetWords, ...(body.remainingFromSectionId ? {
      remainingGenerationPolicy: 'remaining-sequential-document-v1', remainingFromSectionId: body.remainingFromSectionId,
      remainingSectionIds: project.sections.slice(fromIndex).map(section => section.id) } : {}) };
    project.runs = [{ id: 'run-interactive-single', kind: 'document', status: 'succeeded', applied: true,
      plan, candidate: { document: { strategy, steps } } }];
    saveVersion(project, request);
    return json({ project: clone(project), runId: 'run-interactive-single', job: { id: 'job-interactive-single', status: 'succeeded' } }, 202);
  }
  const activeProject = [...remote.values()].find(project => project.runs?.some(run => run.kind === 'automatic' && ['queued', 'running'].includes(run.status)));
  const interrupt = path.match(/^\/api\/writing\/projects\/([^/]+)\/automatic\/interrupt$/);
  if (method === 'POST' && interrupt) {
    const project = remote.get(interrupt[1]), run = project?.runs.find(item => item.id === body.runId && item.kind === 'automatic');
    if (!run || !['queued', 'running'].includes(run.status)) return json({ detail: '原自动任务已变化。' }, 409);
    if (interruptConflictOnce) {
      interruptConflictOnce = false; project.version += 1;
      return json({ detail: '任务刚刚保存了新检查点，请读取后重试。' }, 409);
    }
    if (body.expectedVersion !== project.version) return json({ detail: '自动稿版本已更新。' }, 409);
    run.status = 'cancelled'; run.stage = 'interrupted';
    project.brief.automation = 'manual'; project.version += 1;
    project.automatic = { ...project.automatic, status: 'cancelled', stage: 'complete', finishedAt: '2026-10-25T10:00:00Z',
      report: { status: 'cancelled', title: project.title, generatedAt: '2026-10-25T10:00:00Z', projectVersion: project.version,
        outputAvailable: true, summary: { generatedSections: 1, preservedSections: 0, skippedSections: 1, illustrations: 0 },
        stages: [], issues: [{ code: 'manual_takeover', stage: 'complete', message: '已中断智能化写作，后续由您逐步确认。' }], review: null } };
    request.finishedAt = performance.now(); return json({ project: clone(project), interrupted: true });
  }
  if (activeProject && method !== 'GET') {
    unexpected.push({ path, method, reason: 'Mutations are forbidden during automatic read-only browsing' });
    return json({ detail: '自动写作只读期间，仅允许显式中断。' }, 409);
  }
  const generateSection = path.match(/^\/api\/writing\/projects\/([^/]+)\/sections\/([^/]+)\/generate$/);
  if (method === 'POST' && generateSection) {
    const project = remote.get(generateSection[1]), section = project?.sections.find(value => value.id === generateSection[2]);
    if (!section || body.expectedVersion !== project.version) return json({ detail: '章节生成基稿版本不一致。' }, 409);
    if (!project.outlineConfirmed || section.locked || section.manual || section.content?.trim()) return json({ detail: '本隔离验收仅生成已确认纲要中的空白章节。' }, 409);
    section.content = '这是单独生成后保存的当前章节正文。'; section.version += 1;
    if (project.chapterConfirmationPolicy) section.chapterConfirmed = false;
    project.runs.unshift({ id: 'run-options-section-' + request.id, sectionId: section.id, kind: 'section', status: 'succeeded', applied: true,
      createdAt: '2026-10-27T09:00:00Z', candidate: { section: { content: section.content, evidence: [] } } });
    markPending(project, 'body'); saveVersion(project, request);
    return json({ project: clone(project), runId: project.runs[0].id, job: { id: 'job-options-section-' + request.id, status: 'succeeded' } }, 202);
  }
  if (method === 'POST' && path === '/api/writing/assist/title') {
    return new Promise(resolve => pendingTitles.set(request.id, { request, resolve }));
  }
  if (method === 'POST' && path === '/api/writing/assist/material-queries') {
    return json({ queries: ['核对原文范围', '写作要求'] });
  }
  if (method === 'POST' && path === '/api/writing/projects') {
    const project = { ...clone(body), id: 'writing-preparation-created', version: 1, workflowVersion: 'writing-six-step-v1',
      workflowPendingSteps: ['brief'],
      mode: body.sourceIds?.length ? body.mode : 'extended', sources: [], sections: [], outlineConfirmed: false,
      runs: [], illustrations: [], illustrationReview: null, review: null,
      createdAt: '2026-10-25T10:00:00Z', updatedAt: '2026-10-25T10:00:00Z' };
    remote.set(project.id, project); return json({ project: clone(project) }, 201);
  }
  const automatic = path.match(/^\/api\/writing\/projects\/([^/]+)\/automatic$/);
  if (method === 'POST' && automatic) {
    const project = remote.get(automatic[1]);
    if (!project || body.expectedVersion !== project.version) return json({ detail: '自动任务基稿版本不一致。' }, 409);
    const run = { id: 'run-preparation-started', jobId: 'job-preparation-started', kind: 'automatic', status: 'running', stage: 'preparing', applied: false };
    project.runs = [run]; project.automatic = { runId: run.id, jobId: run.jobId, status: 'running', stage: 'preparing' };
    return json({ project: clone(project), runId: run.id, job: { id: run.jobId, status: 'running' } }, 202);
  }
  const action = path.match(/^\/api\/writing\/projects\/([^/]+)\/review(?:\/(skip|confirm|export))?$/);
  if (method === 'POST' && action) {
    const project = remote.get(action[1]);
    if (!project) return json({ detail: '隔离项目不存在。' }, 404);
    if (body.expectedVersion !== project.version) return json({ detail: '文稿已改变，请刷新后重试。' }, 409);
    if (action[2] === 'export') {
      request.finishedAt = performance.now();
      return new Response('<!doctype html><html lang="zh-CN"><title>独立校验报告</title><body><h1>独立校验报告</h1><p>核对结果仅在此报告中提供。</p></body></html>',
        { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8', 'X-Wenshu-Project-Version': String(project.version) } });
    }
    const report = project.review?.version === project.version ? clone(project.review) : reviewReport(project);
    if (action[2] === 'skip') {
      report.disposition = { status: 'skipped', version: project.version, reportId: report.reportId };
    } else if (action[2] === 'confirm') {
      if (project.runs?.some(run => ['queued', 'running'].includes(run.status)) || ['queued', 'running'].includes(project.automatic?.status)) return json({ detail: '请等待当前任务结束后确认。' }, 409);
      if (body.all !== true && !body.issueIds?.length) return json({ detail: '请选择核对事项。' }, 422);
      if (body.all === true && body.issueIds?.length) return json({ detail: '请按事项确认，或选择全局确认。' }, 422);
      const selected = new Set(body.issueIds || []);
      if ([...selected].some(id => !report.issues.some(issue => issue.id === id))) return json({ detail: '核对事项已经变化，请刷新报告后确认。' }, 409);
      report.issues = report.issues.map(issue => ({ ...issue,
        confirmed: issue.confirmed || body.all === true || selected.has(issue.id),
        acknowledged: issue.acknowledged || body.all === true || selected.has(issue.id) }));
      report.authorConfirmed = report.issues.every(issue => issue.confirmed);
      report.requiresDraftConsent = !report.authorConfirmed;
      report.disposition = { status: 'acknowledged', version: project.version, reportId: report.reportId,
        issueIds: report.issues.filter(issue => issue.acknowledged).map(issue => issue.id).sort(), decidedAt: '2026-10-27T12:00:00Z' };
      project.reviewDisposition = clone(report.disposition);
      request.persisted = true;
    }
    project.review = report; request.finishedAt = performance.now();
    return json({ project: clone(project), review: clone(report) });
  }
  const stepConfirmation = path.match(/^\/api\/writing\/projects\/([^/]+)\/steps\/(brief|materials|body)\/confirm$/);
  if (method === 'POST' && stepConfirmation) {
    const project = remote.get(stepConfirmation[1]), step = stepConfirmation[2];
    if (!project || body.expectedVersion !== project.version) return json({ detail: '步骤基稿版本不一致。' }, 409);
    const confirmChapters = step === 'body' && project.chapterConfirmationPolicy === 'chapter-confirmation-v1'
      && project.sections.some(section => section.content?.trim() && !section.chapterConfirmed);
    if ((project.workflowPendingSteps || []).includes(step) || confirmChapters) {
      project.workflowPendingSteps = (project.workflowPendingSteps || []).filter(value => value !== step);
      if (confirmChapters) project.sections.forEach(section => { section.chapterConfirmed = !!section.content?.trim(); });
      if (step === 'body' && project.chapterConfirmationPolicy === 'chapter-confirmation-v1') {
        request.persisted = true; request.finishedAt = performance.now();
      } else saveVersion(project, request);
    }
    return json({ project: clone(project) });
  }
  const sectionConfirmation = path.match(/^\/api\/writing\/projects\/([^/]+)\/sections\/([^/]+)\/confirm$/);
  if (method === 'POST' && sectionConfirmation) {
    const project = remote.get(sectionConfirmation[1]), section = project?.sections.find(value => value.id === sectionConfirmation[2]);
    if (!section || body.expectedVersion !== project.version) return json({ detail: '章节确认基稿版本不一致。' }, 409);
    if (!section.content?.trim() || project.runs.some(run => ['queued', 'running'].includes(run.status))) return json({ detail: '当前章节不能确认。' }, 409);
    if (project.chapterConfirmationPolicy !== 'chapter-confirmation-v1') {
      const target = Number(project.brief.targetWords ?? 6000);
      const sequential = project.brief.automation !== 'automatic' && Number.isFinite(target) && target > 0
        && (target < 10000 ? project.brief.interactiveGeneration === 'chapter' : project.brief.workflow !== 'oneshot');
      if (!sequential) return json({ detail: '当前章节不能确认。' }, 409);
      project.chapterConfirmationPolicy = 'chapter-confirmation-v1';
      project.sections.forEach(chapter => { chapter.chapterConfirmed = false; });
      markPending(project, 'body');
    }
    if (!section.chapterConfirmed) {
      section.chapterConfirmed = true; request.persisted = true; request.finishedAt = performance.now();
    }
    return json({ project: clone(project) });
  }
  const outline = path.match(/^\/api\/writing\/projects\/([^/]+)\/outline$/);
  if (method === 'PUT' && outline) {
    const project = remote.get(outline[1]);
    if (!project || body.expectedVersion !== project.version) return json({ detail: '纲要基稿版本不一致。' }, 409);
    const next = body.sections.map((value, index) => {
      const previous = project.sections.find(section => section.id === value.id) || project.sections[index];
      if (!previous) throw new Error('本验收仅调整已有纲要，不新增模拟生成章节。');
      const changes = { title: value.title.trim(), goal: (value.goal || '').trim(), mode: modeOf(value.mode), targetWords: value.targetWords };
      if (previous.locked && Object.entries(changes).some(([key, value]) => value !== previous[key])) throw new Error('锁定章节不应收到纲要变更。');
      const changed = Object.entries(changes).some(([key, value]) => value !== previous[key]);
      const section = { ...previous, ...changes, version: previous.version + Number(changed) };
      if (changed && project.chapterConfirmationPolicy) section.chapterConfirmed = false;
      return section;
    });
    if (!same(next, project.sections)) { project.sections = next; project.outlineConfirmed = false; markPending(project, 'body'); saveVersion(project, request); }
    return json({ project: clone(project) });
  }
  const sectionPatch = path.match(/^\/api\/writing\/projects\/([^/]+)\/sections\/([^/]+)$/);
  if (method === 'PATCH' && sectionPatch) {
    const project = remote.get(sectionPatch[1]), section = project?.sections.find(value => value.id === sectionPatch[2]);
    if (!section || body.expectedVersion !== project.version) return json({ detail: '正文基稿版本不一致。' }, 409);
    if (section.locked && Object.hasOwn(body, 'content')) return json({ detail: '章节已锁定，请先解锁后编辑。' }, 409);
    const before = clone(section);
    for (const key of ['content', 'locked', 'mode']) if (Object.hasOwn(body, key)) section[key] = key === 'mode' ? modeOf(body[key]) : body[key];
    if (!same(before, section)) {
      if (before.content !== section.content || before.mode !== section.mode) markPending(project, 'body');
      if (before.content !== section.content) {
        if (project.chapterConfirmationPolicy) section.chapterConfirmed = false;
        section.manual = true; section.contentHash = 'fixture-edited-content-' + (project.version + 1);
        for (const figure of project.illustrations || []) if (figure.sectionId === section.id) figure.status = 'stale';
      }
      section.version += 1; saveVersion(project, request);
    }
    return json({ project: clone(project) });
  }
  const confirmOutline = path.match(/^\/api\/writing\/projects\/([^/]+)\/confirm-outline$/);
  if (method === 'POST' && confirmOutline) {
    const project = remote.get(confirmOutline[1]);
    if (!project || body.expectedVersion !== project.version) return json({ detail: '纲要确认版本不一致。' }, 409);
    if (!project.outlineConfirmed) { project.outlineConfirmed = true; saveVersion(project, request); }
    return json({ project: clone(project) });
  }
  if (method === 'PATCH' && detail) {
    const project = remote.get(detail[1]);
    if (!project || body.expectedVersion !== project.version) return json({ detail: '隔离项目版本不一致。' }, 409);
    const before = clone(project);
    Object.assign(project, Object.fromEntries(Object.entries(body).filter(([key]) => key !== 'expectedVersion')));
    if (!project.sourceIds.length) project.mode = 'extended';
    if (!same(before, project)) {
      const briefChanged = ['title', 'brief', 'modelId'].some(key => !same(before[key], project[key]));
      const scopeChanged = !same(sourceScope(before), sourceScope(project));
      const materialsChanged = scopeChanged || !same(before.materialSelection, project.materialSelection);
      if (briefChanged) markPending(project, 'brief', 'body');
      if (materialsChanged) markPending(project, 'materials');
      if (scopeChanged) markPending(project, 'body');
      if (scopeChanged || ['title', 'brief'].some(key => !same(before[key], project[key]))) project.outlineConfirmed = false;
      if (project.chapterConfirmationPolicy && (briefChanged || scopeChanged)) project.sections.forEach(section => { section.chapterConfirmed = false; });
      saveVersion(project, request);
    }
    request.finishedAt = performance.now(); return json({ project: clone(project) });
  }
  unexpected.push({ path, method });
  throw new Error('Unexpected isolated HTTP request: ' + method + ' ' + path);
};
HTMLAnchorElement.prototype.click = function () {
  downloads.push({ href: this.href, filename: this.download, at: performance.now() });
};

function Case({ name }) {
  const hook = useWriting();
  writing = hook;
  const [presentationGuards, setGuards] = useState({});
  setPresentationGuards = setGuards;
  const [stage, setStage] = useState(name === 'materials-empty' ? 'materials'
    : name.startsWith('chapter-flow-') || name.startsWith('remaining-flow-') ? 'editor'
    : name.startsWith('outline-regen-') && name !== 'outline-regen-existing' ? 'outline'
    : name.startsWith('options-interactive-outline-') ? 'outline'
    : ['workflow-body', 'workflow-materials', 'workflow-brief'].includes(name) ? 'brief'
    : name.startsWith('workflow-') || name.startsWith('manual-') || ['review', 'editor', 'interactive-generated', 'interactive-legacy'].includes(name) ? 'editor'
    : name.startsWith('interactive-') ? 'outline' : 'brief');
  const [toast, setToast] = useState('');
  currentStage = stage;
  return <div className="workspace-shell preparation-hook-shell"><main className="app-main view-writing">
    <WritingWorkspace writing={{ ...hook, ...presentationGuards }} stage={stage} onStageChange={setStage}
      documents={[{ id: 'source-preparation', title: '隔离选择的原始资料', revisionId: 'revision-preparation' },
        { id: 'source-old-recommendation', title: '上轮采用的推荐资料', revisionId: 'revision-old-recommendation' }]}
      selectedIds={name === 'selected' ? ['source-preparation'] : []} models={[]}
      onToast={setToast} onOpenSettings={() => {}} onOpenDoc={() => {}}/>
    <output id="preparation-hook-observed" data-ready={!hook.loading} data-busy={hook.busy}>{hook.error}</output>
  </main>{toast && <output className="preparation-hook-toast">{toast}</output>}</div>;
}

function Harness() {
  const initial = params.get('scenario') || 'blank';
  const [fixture, setFixture] = useState(() => { configure(initial); return { name: initial, key: 0 }; });
  switchFixture = name => { configure(name); setFixture({ name, key: ++serial }); };
  return <Case key={fixture.key} name={fixture.name}/>;
}

window.__preparation = {
  get hook() { return writing; },
  get state() { return { project: clone(writing?.project), savedProject: clone(writing?.savedProject), loading: writing?.loading, busy: writing?.busy,
    dirty: writing?.dirty, error: writing?.error, remoteChanged: writing?.remoteChanged,
    titleAssistance: clone(writing?.titleAssistance), titleChoiceRequired: writing?.titleChoiceRequired, stage: currentStage }; },
  get callbacks() { return Object.fromEntries(['generateTitle', 'selectTitle', 'keepCurrentTitle', 'confirmReview', 'skipReview', 'exportReview', 'interruptAutomatic', 'confirmStep'].map(name => [name, typeof writing?.[name]])); },
  get requests() { return clone(requests); }, get downloads() { return clone(downloads); }, get unexpected() { return clone(unexpected); },
  get savedProjects() { return [...remote.values()].map(clone); },
  get pendingTitles() { return [...pendingTitles.keys()]; },
  loadScenario(name) { switchFixture(name); },
  setPresentationGuards(guards) { setPresentationGuards(guards); },
  conflictNextInterrupt() { interruptConflictOnce = true; },
  releaseTitle({ title = '资料如何走向清楚表达', titles, status = 200, message = '拟题服务暂不可用，请稍后重试。', requestId } = {}) {
    const id = requestId || pendingTitles.keys().next().value, pending = pendingTitles.get(id);
    if (!pending) throw new Error('没有待返回的拟题请求。');
    pendingTitles.delete(id); pending.request.finishedAt = performance.now();
    pending.resolve(json(status === 200 ? { title, ...(titles === undefined ? {} : { titles }), assistanceId: 'writing-assistance-' + id } : { detail: message }, status));
  },
  clearRecords() { requests.length = 0; downloads.length = 0; unexpected.length = 0; },
};
createRoot(document.getElementById('preparation-hook-root')).render(<Harness/>);
