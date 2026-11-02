import test from 'node:test';
import assert from 'node:assert/strict';
import { deriveWritingProgress } from '../src/writing-progress.js';
import { deriveWritingProgressNotices } from '../src/writing-progress-notices.js';

const fixture = changes => ({ id: 'writing-notices', version: 3, title: '已保存文稿', topic: '写作主题',
  automation: 'manual', sourceIds: ['source-one'], mode: 'strict', materialSelection: { confirmed: true },
  workflowPendingSteps: [], outlineConfirmed: true,
  sections: [{ id: 'first', title: '第一章', content: '第一章已保存正文。', words: 500, mode: 'inherit' },
    { id: 'second', title: '第二章', content: '第二章已保存正文。', words: 500, mode: 'inherit' }],
  illustrationReview: { status: 'skipped' }, review: { version: 3, citationReviewCompleted: true, issues: [] },
  runs: [], ...changes });
const notices = (project, options = {}) => {
  const savedProject = options.savedProject || structuredClone(project);
  return deriveWritingProgressNotices({ project, savedProject,
    progress: deriveWritingProgress({ project, savedProject, ...options }), ...options });
};
const find = (result, id) => result.find(value => value.id === id);

test('new unsaved drafts and unmatched dirty snapshots do not invent saved reminders', () => {
  assert.deepEqual(deriveWritingProgressNotices({ project: { topic: '未保存主题' } }), []);
  const project = fixture({ workflowPendingSteps: ['brief'] });
  assert.deepEqual(deriveWritingProgressNotices({ project, savedProject: { id: 'other' }, dirty: true }), []);
  assert(find(deriveWritingProgressNotices({ project }), 'brief-confirm'));
});

test('unsaved changes do not revoke saved milestones or invent pending chapter/review decisions', () => {
  const saved = fixture(), draft = structuredClone(saved);
  draft.topic = '未保存主题'; draft.outlineConfirmed = false; draft.workflowPendingSteps = ['brief', 'body'];
  draft.sections[0].content = ''; draft.review.issues = [{ id: 'unsaved-issue' }];
  assert.deepEqual(notices(draft, { savedProject: saved, dirty: true }), []);
  assert.equal(deriveWritingProgress({ project: draft, savedProject: saved, dirty: true }).completed, 6);
});

test('a saved topic needs explicit confirmation, without marking unstarted downstream steps', () => {
  const project = fixture({ sections: [], review: null, outlineConfirmed: false,
    illustrationReview: null, materialSelection: { confirmed: false }, workflowPendingSteps: ['brief'] });
  const result = notices(project);
  assert.deepEqual(result.map(item => item.id), ['brief-confirm']);
  assert.equal(result[0].stepId, 'brief');
  assert.equal(result[0].actionLabel, '前往确认');
  assert.equal(Object.hasOwn(result[0], 'count'), false);
});

test('materials await confirmation only after the earlier saved topic is ready', () => {
  const project = fixture({ sections: [], review: null, outlineConfirmed: false,
    materialSelection: { confirmed: false }, workflowPendingSteps: [] });
  assert.deepEqual(notices(project).map(item => item.id), ['materials-confirm']);
  assert.equal(find(notices({ ...project, workflowPendingSteps: ['brief', 'materials'] }), 'materials-confirm'), undefined);
});

test('existing unconfirmed outline has an actionable reminder without changing saved data', () => {
  const project = fixture({ outlineConfirmed: false, sections: fixture().sections.map(section => ({ ...section, content: '' })) });
  const before = structuredClone(project), result = notices(project);
  assert(find(result, 'outline-confirm'));
  assert.equal(find(result, 'body-confirm'), undefined);
  assert.deepEqual(project, before);
});

test('new chapter tracking identifies the first saved pending chapter and avoids duplicate whole-body reminders', () => {
  const project = fixture({ chapterConfirmationPolicy: 'chapter-confirmation-v1', workflowPendingSteps: ['body'] });
  const result = notices(project), reminder = find(result, 'body-chapters');
  assert.equal(reminder.count, 2); assert.equal(reminder.sectionId, 'first');
  assert.equal(reminder.label, '2 章待确认');
  assert.equal(find(result, 'body-confirm'), undefined);
  assert.equal(find(result, 'images-confirm'), undefined);
});

test('saved chapter acceptance survives browsing and edits to a sibling until an actual save changes it', () => {
  const saved = fixture({ chapterConfirmationPolicy: 'chapter-confirmation-v1', workflowPendingSteps: ['body'] });
  saved.sections[0].chapterConfirmed = true;
  const draft = structuredClone(saved); draft.sections[0].content += '尚未保存的修改'; draft.activeSectionId = 'first';
  const reminder = find(notices(draft, { savedProject: saved, dirty: true }), 'body-chapters');
  assert.equal(reminder.count, 1); assert.equal(reminder.sectionId, 'second');
  const committed = structuredClone(draft); committed.sections[0].chapterConfirmed = false; committed.version += 1;
  assert.equal(find(notices(committed), 'body-chapters').count, 2);
});

test('legacy chapters are not migrated; full-body confirmation requires saved prose in every chapter', () => {
  const project = fixture({ workflowPendingSteps: ['body'] });
  assert(find(notices(project), 'body-confirm'));
  assert.equal(find(notices(project), 'body-chapters'), undefined);
  project.sections[1].content = '';
  assert.equal(find(notices(project), 'body-confirm'), undefined);
});

test('skipped review suppresses issue reminders without acknowledging saved findings', () => {
  const project = fixture({ review: { version: 3, citationReviewCompleted: false,
    disposition: { status: 'skipped' }, issues: [{ id: 'pending' }, { id: 'accepted', acknowledged: true }, { id: 'checked', confirmed: true }] } });
  const before = structuredClone(project), result = notices(project), reminder = find(result, 'review-skipped');
  assert.equal(reminder.tone, 'info'); assert.equal(Object.hasOwn(reminder, 'count'), false);
  assert.match(reminder.description, /手动发起校验/);
  assert.equal(result[0].id, 'review-skipped');
  assert.equal(deriveWritingProgress({ project, savedProject: project }).completed, 6);
  assert.equal(find(result, 'review-issues'), undefined);
  assert.deepEqual(project, before);
  assert.doesNotMatch(deriveWritingProgress({ project, savedProject: project }).next.description, /待核实|忽略/);
});

test('skipped review without saved issues stays informational and never fabricates a count', () => {
  const project = fixture({ review: { version: 3, citationReviewCompleted: false,
    disposition: { status: 'skipped' }, issues: [] } });
  const reminder = find(notices(project), 'review-skipped');
  assert.equal(reminder.tone, 'info'); assert.equal(reminder.actionLabel, '查看校验报告');
  assert.equal(Object.hasOwn(reminder, 'count'), false);
});

test('current explicit author acknowledgement is respected without fabricating item-level acceptance', () => {
  const project = fixture({ review: { version: 3, citationReviewCompleted: false, authorConfirmed: true,
    issues: [{ id: 'one', confirmed: true }, { id: 'two', acknowledged: true }] } });
  assert.deepEqual(notices(project), []);
  project.review.issues.push({ id: 'still-pending' });
  assert.equal(find(notices(project), 'review-issues').count, 1);
});

test('stale or remotely outdated reports show no old issue count as current findings', () => {
  const project = fixture({ review: { version: 2, issues: [{ id: 'old' }, { id: 'older' }] } });
  const reminder = find(notices(project), 'review-stale');
  assert.equal(reminder.label, '校验报告已过期'); assert.equal(Object.hasOwn(reminder, 'count'), false);
  project.review.version = 3;
  const result = notices(project, { remoteChanged: true });
  assert.deepEqual(result.map(item => item.id), ['review-stale']);
});

test('a skipped decision cannot conceal a stale review or a newer remote manuscript', () => {
  const project = fixture({ review: { version: 2, citationReviewCompleted: false,
    disposition: { status: 'skipped' }, issues: [{ id: 'old' }] } });
  assert.deepEqual(notices(project).map(item => item.id), ['review-stale']);
  assert.equal(deriveWritingProgress({ project, savedProject: project }).reviewComplete, false);
  project.review.version = 3;
  assert.deepEqual(notices(project, { remoteChanged: true }).map(item => item.id), ['review-stale']);
});

test('automatic running or queued work has no premature author-confirmation alerts', () => {
  const project = fixture({ workflowPendingSteps: ['brief', 'body'], review: { version: 2, issues: [{ id: 'old' }] } });
  for (const status of ['queued', 'running']) {
    assert.deepEqual(notices({ ...project, automatic: { status } }), []);
    assert.deepEqual(notices({ ...project, runs: [{ kind: 'automatic', status }] }), []);
  }
  assert.deepEqual(notices(project, { progress: { automatic: true, steps: [] } }), []);
  assert(find(notices({ ...project, automatic: { status: 'completed_with_issues' } }), 'review-stale'));
});

test('nonautomatic tasks suppress confirmation prompts while truly stale reports remain readable', () => {
  const project = fixture({ workflowPendingSteps: ['body'], runs: [{ kind: 'document', status: 'running' }] });
  assert.deepEqual(notices(project), []);
  project.review.version = 2;
  assert.deepEqual(notices(project).map(item => item.id), ['review-stale']);
});

test('illustrations and first review reminders appear only after their saved prerequisites', () => {
  const project = fixture({ review: null, illustrationReview: null });
  assert.deepEqual(notices(project).map(item => item.id), ['images-confirm']);
  project.illustrationReview = { status: 'skipped' };
  assert.deepEqual(notices(project).map(item => item.id), ['review-start']);
  project.workflowPendingSteps = ['body'];
  assert.deepEqual(notices(project).map(item => item.id), ['body-confirm']);
});

test('step availability is respected while an already saved report remains directly readable', () => {
  const project = fixture({ workflowPendingSteps: ['brief'], review: { version: 3, issues: [{ id: 'pending' }] } });
  const progress = deriveWritingProgress({ project, savedProject: project });
  const result = notices(project, { progress: { ...progress,
    steps: progress.steps.map(step => ({ ...step, available: false })) } });
  assert.deepEqual(result.map(item => item.id), ['review-issues']);
  assert(result.every(item => !Object.hasOwn(item, 'count') || Number.isInteger(item.count) && item.count > 0));
});
