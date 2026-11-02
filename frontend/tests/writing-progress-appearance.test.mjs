import test from 'node:test';
import assert from 'node:assert/strict';
import { decorateWritingProgress } from '../src/writing-progress-appearance.js';

const steps = () => ['brief', 'materials', 'outline', 'body', 'images', 'review'].map(id => ({
  id, label: id, complete: ['brief', 'materials'].includes(id), available: !['images', 'review'].includes(id) }));
const fixture = changes => ({ id: 'saved-manuscript', version: 4, runs: [], ...changes });
const progress = changes => ({ steps: steps(), currentId: 'outline', completed: 2, total: 6,
  notices: [{ id: 'outline-confirm', stepId: 'outline', label: '纲要待确认' }], ...changes });
const step = (result, id) => result.steps.find(value => value.id === id);

test('selected chapter picture batches stay in the image phase through queueing and execution', () => {
  for (const status of ['queued', 'running']) {
    const project = fixture({ runs: [{ kind: 'image_batch', status }] });
    const result = decorateWritingProgress({ progress: progress({ currentId: 'images' }), project, savedProject: project });
    assert.equal(step(result, 'images').visualState, status === 'queued' ? 'waiting' : 'running');
    assert.equal(step(result, 'images').stateLabel, status === 'queued' ? '等待排队' : '进行中');
    assert.equal(step(result, 'body').visualState, 'waiting');
    assert.equal(step(result, 'images').durationLabel, '');
  }
});

test('saved complete, available current, and future steps receive separate states without changing milestones', () => {
  const project = fixture(), source = progress(), before = structuredClone(source);
  const result = decorateWritingProgress({ progress: source, project, savedProject: project });
  assert.equal(step(result, 'brief').visualState, 'complete');
  assert.equal(step(result, 'outline').visualState, 'running');
  assert.equal(step(result, 'outline').stateLabel, '进行中');
  assert.equal(step(result, 'body').visualState, 'waiting');
  assert.equal(step(result, 'body').stateLabel, '等待');
  assert.equal(result.currentId, source.currentId); assert.equal(result.completed, 2);
  assert.equal(result.notices, source.notices);
  assert.deepEqual(source, before);
});

test('viewing a completed page does not repaint the real current phase', () => {
  const project = fixture(), source = progress();
  const bodyView = decorateWritingProgress({ progress: source, project, savedProject: project, viewedStep: 'body' });
  const themeView = decorateWritingProgress({ progress: source, project, savedProject: project, viewedStep: 'brief' });
  assert.deepEqual(bodyView, themeView);
  assert.equal(step(themeView, 'brief').visualState, 'complete');
  assert.equal(step(themeView, 'outline').visualState, 'running');
});

test('an explicitly saved skip replaces elapsed time with a skipped label without fabricating time', () => {
  const project = fixture({ illustrationReview: { status: 'skipped' },
    review: { version: 4, disposition: { status: 'skipped' }, issues: [{ confirmed: false }] },
    workflowTimings: { images: { durationMs: 0, kind: 'automatic-stage' }, review: { durationMs: 0, kind: 'confirmation' } } });
  const source = progress({ currentId: 'review', completed: 6,
    steps: steps().map(value => ({ ...value, complete: true, available: true })) });
  const result = decorateWritingProgress({ progress: source, project });
  for (const id of ['images', 'review']) {
    assert.equal(step(result, id).visualState, 'skipped');
    assert.equal(step(result, id).stateLabel, '已跳过');
    assert.equal(step(result, id).durationLabel, '已跳过');
    assert.equal(step(result, id).durationTitle, '本环节已跳过');
    assert.equal(step(result, id).complete, true);
  }
  assert.equal(result.notices, source.notices);
  assert.equal(result.completed, 6);
});

test('only a terminal report for the current saved version can supply a recorded skipped phase', () => {
  const project = fixture({ automatic: { status: 'completed_with_issues', report: { projectVersion: 4,
    stages: [{ stage: 'images', status: 'skipped' }] } } });
  const result = decorateWritingProgress({ progress: progress(), project });
  assert.equal(step(result, 'images').visualState, 'skipped');
  assert.equal(step(result, 'images').durationLabel, '已跳过');
  assert.equal(step(result, 'images').complete, false);
  project.automatic.report.projectVersion = 3;
  assert.equal(step(decorateWritingProgress({ progress: progress(), project }), 'images').visualState, 'waiting');
  project.automatic.report.projectVersion = 4; project.automatic.status = 'running';
  assert.equal(step(decorateWritingProgress({ progress: progress({ automatic: true }), project }), 'images').visualState, 'waiting');
});

test('current saved all-issue author confirmation completes review over a historical automatic skip', () => {
  const project = fixture({ automatic: { status: 'completed_with_issues', report: { projectVersion: 4,
    stages: [{ stage: 'images', status: 'skipped' }, { stage: 'review', status: 'skipped' }] } },
  review: { version: 4, disposition: { status: 'acknowledged' }, authorConfirmed: true,
    citationReviewCompleted: false, issues: [{ confirmed: true, acknowledged: true }] } });
  const source = progress({ currentId: 'review', completed: 6,
    steps: steps().map(value => ({ ...value, complete: true, available: true })) });
  const before = structuredClone(project);
  const result = decorateWritingProgress({ progress: source, project, savedProject: project });
  assert.equal(step(result, 'review').visualState, 'complete');
  assert.equal(step(result, 'review').stateLabel, '已完成');
  assert.equal(step(result, 'review').complete, true);
  assert.equal(step(result, 'images').visualState, 'skipped');
  assert.equal(step(result, 'review').durationLabel, '耗时未记录');
  assert.equal(step(decorateWritingProgress({ progress: source, project: structuredClone(project) }), 'review').visualState, 'complete');
  assert.equal(project.review.citationReviewCompleted, false);
  assert.deepEqual(project, before);
});

test('stale or incomplete author decisions do not override an automatic review skip', () => {
  for (const review of [
    { version: 3, disposition: { status: 'acknowledged' }, authorConfirmed: true },
    { version: 4, disposition: { status: 'acknowledged' }, authorConfirmed: false },
    { version: 4, disposition: { status: 'skipped' }, authorConfirmed: true },
    { version: 4, authorConfirmed: true },
  ]) {
    const project = fixture({ automatic: { status: 'completed_with_issues', report: { projectVersion: 4,
      stages: [{ stage: 'review', status: 'skipped' }] } }, review });
    const source = progress({ currentId: 'review', steps: steps().map(value => ({ ...value, complete: true, available: true })) });
    const before = structuredClone(project);
    assert.equal(step(decorateWritingProgress({ progress: source, project }), 'review').visualState, 'skipped');
    assert.deepEqual(project, before);
  }
});

test('queued automatic work stays waiting even when its phase is not yet viewable', () => {
  const project = fixture({ automatic: { status: 'queued' } });
  const source = progress({ automatic: true, currentId: 'materials',
    steps: steps().map(value => ({ ...value, complete: value.id === 'brief', available: value.id === 'brief' })) });
  const result = decorateWritingProgress({ progress: source, project });
  assert.equal(step(result, 'materials').visualState, 'waiting');
  assert.equal(step(result, 'materials').stateLabel, '等待排队');
  assert.equal(step(result, 'brief').visualState, 'complete');
});

test('active automatic phase is running while future saved skips cannot be borrowed from an earlier run', () => {
  const project = fixture({ automatic: { status: 'running' }, illustrationReview: { status: 'skipped' },
    review: { version: 4, disposition: { status: 'skipped' } } });
  const source = progress({ automatic: true, currentId: 'body' });
  const result = decorateWritingProgress({ progress: source, project });
  assert.equal(step(result, 'body').visualState, 'running');
  assert.equal(step(result, 'images').visualState, 'waiting');
  assert.equal(step(result, 'review').visualState, 'waiting');
});

test('active and queued manual tasks override old completion paint without changing saved completion flags', () => {
  for (const status of ['queued', 'running']) {
    const project = fixture({ runs: [{ id: 'current-job', kind: 'document', status }] });
    const source = progress({ currentId: 'body', steps: steps().map(value => ({ ...value, complete: true })) });
    const result = decorateWritingProgress({ progress: source, project });
    assert.equal(step(result, 'body').complete, true);
    assert.equal(step(result, 'body').visualState, status === 'queued' ? 'waiting' : 'running');
    assert.equal(step(result, 'body').stateLabel, status === 'queued' ? '等待排队' : '进行中');
    assert.equal(step(result, 'body').durationLabel, '');
    assert.equal(result.completed, source.completed);
  }
});

test('current activity metadata wins over an old running task in the saved baseline', () => {
  const saved = fixture({ runs: [{ id: 'old-job', kind: 'document', status: 'running' }] });
  const project = fixture({ runs: [{ id: 'old-job', kind: 'document', status: 'succeeded' }] });
  const source = progress({ currentId: 'body', steps: steps().map(value => ({ ...value, complete: true })) });
  const result = decorateWritingProgress({ progress: source, project, savedProject: saved });
  assert.equal(step(result, 'body').visualState, 'complete');
});

test('an unavailable current manual step remains waiting; a pending real task may still be running', () => {
  const project = fixture(), source = progress({ currentId: 'review' });
  assert.equal(step(decorateWritingProgress({ progress: source, project }), 'review').visualState, 'waiting');
  assert.equal(step(decorateWritingProgress({ progress: source, project, pending: true }), 'review').visualState, 'running');
});

test('dirty drafts cannot replace saved skip and timing metadata', () => {
  const saved = fixture({ illustrationReview: { status: 'skipped' },
    workflowTimings: { brief: { durationMs: 155876, kind: 'generation' } } });
  const draft = structuredClone(saved); draft.illustrationReview = null;
  draft.workflowTimings.brief.durationMs = 1; draft.sections = [{ content: '未保存内容' }];
  const result = decorateWritingProgress({ progress: progress(), project: draft, savedProject: saved, dirty: true });
  assert.equal(step(result, 'images').visualState, 'skipped');
  assert.equal(step(result, 'brief').durationLabel, '用时 2分36秒');
  const noBaseline = decorateWritingProgress({ progress: progress(), project: draft,
    savedProject: { id: 'different-manuscript' }, dirty: true });
  assert.equal(step(noBaseline, 'images').visualState, 'waiting');
  assert.equal(step(noBaseline, 'brief').durationLabel, '耗时未记录');
});

test('old completed records without timing data stay completed and explicitly show missing timing', () => {
  const project = fixture();
  const result = decorateWritingProgress({ progress: progress(), project });
  assert.equal(step(result, 'brief').visualState, 'complete');
  assert.equal(step(result, 'brief').durationLabel, '耗时未记录');
  assert.equal(step(result, 'outline').durationLabel, '');
});

test('known timing formats subsecond, second, minute, and hour values without showing negative or fake skip durations', () => {
  for (const [durationMs, expected] of [[0, '不足1秒'], [999, '不足1秒'], [1000, '1秒'],
    [155876, '2分36秒'], [60000, '1分'], [3723000, '1小时2分3秒']]) {
    const project = fixture({ workflowTimings: { brief: { durationMs, kind: 'generation' } } });
    const result = decorateWritingProgress({ progress: progress(), project });
    assert.equal(step(result, 'brief').durationLabel, `用时 ${expected}`);
    assert.equal(step(result, 'brief').durationTitle, '生成任务耗时（含排队和保存）');
  }
});

test('invalid elapsed-time frames remain unknown instead of producing misleading durations', () => {
  for (const durationMs of [null, undefined, -1, NaN, Infinity, 1.5, '155876', Number.MAX_SAFE_INTEGER + 1]) {
    const project = fixture({ workflowTimings: { brief: { durationMs, kind: 'generation' } } });
    assert.equal(step(decorateWritingProgress({ progress: progress(), project }), 'brief').durationLabel, '耗时未记录');
  }
});

test('confirmation and automatic-stage timing describes saved records rather than claiming a model request', () => {
  for (const kind of ['confirmation', 'automatic-stage']) {
    const project = fixture({ workflowTimings: { brief: { durationMs: 1234, kind } } });
    const result = decorateWritingProgress({ progress: progress(), project });
    assert.equal(step(result, 'brief').durationLabel, '用时 1秒');
    assert.equal(step(result, 'brief').durationTitle, '步骤记录用时（按已保存记录计算）');
  }
});

test('step metrics count saved unique sources and prose without treating chapter confirmations as generated chapters', () => {
  const project = fixture({ automation: 'manual', sourceIds: ['source-b', 'source-a', 'source-b'],
    sections: [{ id: 'one', content: '第一章已保存正文。' }, { id: 'two', content: '  \n  ' }, { id: 'three', content: '第三章已保存正文。' }],
    chapterConfirmations: [{ sectionId: 'two' }] });
  const before = structuredClone(project), source = progress();
  const result = decorateWritingProgress({ progress: source, project, savedProject: project });
  assert.equal(step(result, 'brief').metricLabel, '交互模式');
  assert.equal(step(result, 'materials').metricValue, 2);
  assert.equal(step(result, 'outline').metricValue, 3);
  assert.equal(step(result, 'body').metricValue, 2);
  assert.equal(step(result, 'body').metricLabel, '已写 2/3 章');
  assert.equal(step(result, 'images').metricValue, 0);
  assert.equal(result.completed, source.completed);
  assert.deepEqual(project, before);
});

test('unsaved edits never replace the saved mode, source, outline, prose, or adopted-image metric scope', () => {
  const saved = fixture({ automation: 'automatic', sourceIds: ['original'], sections: [{ id: 'one', content: '已保存正文。', contentHash: 'saved-hash' }],
    illustrations: [{ id: 'accepted', sectionId: 'one', status: 'accepted', anchor: { paragraphIndex: 0, paragraphExact: '已保存正文。', contentHash: 'saved-hash' } }] });
  const draft = structuredClone(saved);
  draft.automation = 'manual'; draft.sourceIds = ['new-one', 'new-two'];
  draft.sections[0].content = ''; draft.sections.push({ id: 'draft-only', content: '未保存章节。' }); draft.illustrations = [];
  const result = decorateWritingProgress({ progress: progress(), project: draft, savedProject: saved, dirty: true });
  assert.equal(step(result, 'brief').metricLabel, '自动模式');
  assert.equal(step(result, 'materials').metricValue, 1);
  assert.equal(step(result, 'outline').metricValue, 1);
  assert.equal(step(result, 'body').metricValue, 1);
  assert.equal(step(result, 'images').metricValue, 1);
});

test('image metrics include only adopted placements with current exact paragraph and content-hash anchors', () => {
  const accepted = { id: 'valid', sectionId: 'one', status: 'accepted', anchor: { paragraphIndex: 1, paragraphExact: '第二段。', contentHash: 'current-hash' } };
  const withAnchor = (id, patch) => ({ ...accepted, id, anchor: { ...accepted.anchor, ...patch } });
  const project = fixture({ sections: [{ id: 'one', content: '第一段。\n\n第二段。', contentHash: 'current-hash' }],
    illustrations: [accepted, { ...accepted, id: 'stale', status: 'stale' }, { ...accepted, id: 'candidate', status: 'recommended' },
      { ...accepted, id: 'missing-section', sectionId: 'missing' }, withAnchor('old-hash', { contentHash: 'old-hash' }),
      withAnchor('wrong-paragraph', { paragraphExact: '旧段落。' }), withAnchor('outside', { paragraphIndex: 99 }),
      withAnchor('missing-anchor-text', { paragraphIndex: 99, paragraphExact: undefined }), { ...accepted, id: 'anchorless', anchor: null }],
    runs: [{ kind: 'images', status: 'succeeded', candidate: { imageSearch: { candidates: [accepted, accepted] } } }] });
  const result = decorateWritingProgress({ progress: progress(), project, savedProject: project });
  assert.equal(step(result, 'images').metricValue, 1);
  assert.equal(step(result, 'images').metricLabel, '1 张配图');
});

test('new unsaved briefs expose their chosen mode while numeric metrics remain saved-work counts', () => {
  const project = { id: null, automation: 'automatic', sourceIds: ['not-saved'], sections: [{ id: 'draft', content: '尚未保存。' }] };
  const result = decorateWritingProgress({ progress: progress(), project, dirty: true });
  assert.equal(step(result, 'brief').metricLabel, '自动模式');
  for (const id of ['materials', 'outline', 'body', 'images']) assert.equal(step(result, id).metricValue, 0);
  project.automation = 'manual';
  assert.equal(step(decorateWritingProgress({ progress: progress(), project, dirty: true }), 'brief').metricLabel, '交互模式');
});

test('unavailable library images leave adopted-image counts without changing saved workflow completion', () => {
  const figure = { sectionId: 'one', status: 'accepted', sourceId: 'source', revisionId: 'revision',
    anchor: { paragraphIndex: 0, paragraphExact: '已保存正文。', contentHash: 'saved-hash' } };
  const project = fixture({ sections: [{ id: 'one', content: '已保存正文。', contentHash: 'saved-hash' }],
    illustrations: ['available', 'deleted', 'unavailable'].map(assetId => ({ ...figure, id: assetId, assetId })),
    libraryAvailability: { assets: ['available', 'deleted', 'unavailable'].map(status => ({ assetId: status, sourceId: 'source', revisionId: 'revision', status })) } });
  const source = progress({ completed: 6, currentId: 'review', steps: steps().map(value => ({ ...value, complete: true, available: true })) });
  const before = structuredClone(project), result = decorateWritingProgress({ progress: source, project, savedProject: project });
  assert.equal(step(result, 'images').metricValue, 1);
  assert.equal(step(result, 'images').metricLabel, '1 张配图');
  assert.equal(step(result, 'images').complete, true);
  assert.equal(step(result, 'images').visualState, 'complete');
  assert.equal(result.completed, source.completed);
  assert.equal(result.currentId, source.currentId);
  assert.equal(result.notices, source.notices);
  assert.deepEqual(project, before);
});

test('a mismatched saved record cannot supply another manuscript metrics', () => {
  const project = fixture({ sourceIds: ['draft'], sections: [{ id: 'draft', content: '未保存正文。' }] });
  const other = fixture({ id: 'another-manuscript', automation: 'automatic', sourceIds: ['a', 'b'], sections: [{ content: '另一文稿正文。' }] });
  const result = decorateWritingProgress({ progress: progress(), project, savedProject: other, dirty: true });
  for (const id of ['materials', 'outline', 'body', 'images']) assert.equal(step(result, id).metricValue, 0);
});
