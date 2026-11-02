import test from 'node:test';
import assert from 'node:assert/strict';
import { deriveWritingProgress } from '../src/writing-progress.js';
const fixture = changes => ({ id: 'p', version: 3, title: '文章题目', topic: '写作主题', purpose: '', targetWords: 6000, automation: 'manual', mode: 'strict', sourceIds: ['a'], materialSelection: { confirmed: true, passages: [] }, outlineConfirmed: true, sections: [{ id: 's1', title: '第一章', goal: '', mode: 'inherit', words: 500, content: '第一段正文。' }, { id: 's2', title: '第二章', goal: '', mode: 'inherit', words: 500, content: '' }], runs: [], ...changes });
const progress = (project, options = {}) => deriveWritingProgress({ project, savedProject: structuredClone(project), ...options });
const step = (result, id) => result.steps.find(item => item.id === id);
const full = changes => fixture({ sections: [{ id: 's', title: '全文', goal: '', mode: 'inherit', words: 500, content: '全文正文。' }], illustrationReview: { status: 'skipped' }, ...changes });

test('the six visible steps include materials and keep export as a final action', () => {
  const result = deriveWritingProgress({ project: { topic: '', sections: [] } });
  assert.deepEqual(result.steps.map(item => [item.id, item.label]), [['brief', '主题设定'], ['materials', '资料筛选'], ['outline', '写作纲要'], ['body', '正文编写'], ['images', '文章配图'], ['review', '检查校验']]);
  assert.equal(result.total, 6); assert.equal(result.completed, 0); assert.equal(result.currentId, 'brief'); assert.equal(result.next.action, 'focus-topic');
});
test('saving a topic does not silently confirm material selection', () => {
  const result = progress(fixture({ sections: [], outlineConfirmed: false, sourceIds: [], mode: 'expanded', materialSelection: { confirmed: false } }));
  assert.equal(step(result, 'brief').complete, true); assert.equal(step(result, 'materials').complete, false); assert.equal(result.next.action, 'materials');
});
test('confirmed empty-source expanded selection is a valid material step', () => {
  const result = progress(fixture({ sourceIds: [], mode: 'expanded', materialSelection: { confirmed: true }, sections: [], outlineConfirmed: false }));
  assert.equal(step(result, 'materials').complete, true); assert.equal(step(result, 'outline').complete, false);
});
test('unsaved body edits preserve confirmed earlier steps and require save', () => {
  const saved = fixture(), draft = structuredClone(saved); draft.sections[0].content = '尚未保存的新正文。';
  const result = progress(draft, { savedProject: saved, dirty: true });
  assert.equal(step(result, 'brief').complete, true); assert.equal(step(result, 'materials').complete, true); assert.equal(step(result, 'outline').complete, true); assert.equal(result.next.action, 'save');
});
test('topic and selected-source drafts preserve milestones until a changed save is adopted', () => {
  const saved = fixture();
  const topic = progress({ ...saved, topic: '新主题' }, { savedProject: saved, dirty: true });
  assert.equal(step(topic, 'brief').complete, true); assert.equal(step(topic, 'outline').complete, true); assert.equal(topic.currentId, 'brief');
  const sources = progress({ ...saved, sourceIds: ['b'] }, { savedProject: saved, dirty: true });
  assert.equal(step(sources, 'brief').complete, true); assert.equal(step(sources, 'materials').complete, true); assert.equal(step(sources, 'outline').complete, true); assert.equal(sources.currentId, 'materials');
  const committed = { ...saved, topic: '新主题', outlineConfirmed: false, workflowPendingSteps: ['brief', 'body'] };
  assert.equal(step(progress(committed), 'brief').complete, false); assert.equal(step(progress(committed), 'outline').complete, false);
});
test('outline drafts preserve completion and changed saves require renewed confirmation', () => {
  const saved = fixture(), draft = structuredClone(saved); draft.sections[0].title = '新章名';
  const result = progress(draft, { savedProject: saved, dirty: true });
  assert.equal(step(result, 'materials').complete, true); assert.equal(step(result, 'outline').complete, true);
  const committed = { ...draft, outlineConfirmed: false, workflowPendingSteps: ['body'] };
  assert.equal(step(progress(committed), 'outline').complete, false);
});
test('partial prose directs the author to the next empty chapter', () => {
  const result = progress(fixture()); assert.equal(result.currentId, 'body'); assert.equal(result.next.sectionId, 's2'); assert.equal(result.body.completed, 1); assert.equal(result.body.empty, 1);
});
test('images and review remain separate steps and completed prose may export without model validation', () => {
  const result = progress(full({ illustrationReview: null }));
  assert.equal(result.currentId, 'images'); assert.equal(step(result, 'review').complete, false); assert.equal(result.exportReady, true);
});
test('a rules-only export snapshot never pretends citation verification completed', () => {
  const result = progress(full({ review: { version: 3, wordCounts: { total: 10 }, citationReviewCompleted: false, issues: [] } }));
  assert.equal(result.reviewComplete, false); assert.equal(result.currentId, 'review'); assert.equal(result.next.action, 'review');
});
test('verified, explicitly skipped and author-confirmed reviews each finish the review step', () => {
  for (const report of [{ citationReviewCompleted: true }, { disposition: { status: 'skipped' } }, { authorConfirmed: true }]) {
    const result = progress(full({ review: { version: 3, wordCounts: { total: 10 }, issues: [], ...report } }));
    assert.equal(result.reviewComplete, true); assert.equal(result.total, 6); assert.equal(result.completed, 6); assert.equal(result.currentId, 'review'); assert.equal(result.next.action, 'export'); assert.equal(result.steps.some(item => item.id === 'export'), false);
  }
});
test('stale reports, remote updates and active tasks prevent claiming current completion', () => {
  const report = { version: 2, citationReviewCompleted: true, issues: [] };
  assert.equal(progress(full({ review: report })).reviewComplete, false);
  assert.equal(progress(full(), { remoteChanged: true }).next.action, 'reload');
  const waiting = progress(full({ runs: [{ kind: 'citations', status: 'running' }] })); assert.equal(waiting.currentId, 'review'); assert.equal(waiting.exportReady, false);
});
test('an export receipt belongs only to the exact current project and version', () => {
  const project = full();
  assert.equal(progress(project, { exportReceipt: { projectId: 'p', version: 3 } }).exportComplete, true);
  assert.equal(progress(project, { exportReceipt: { projectId: 'other', version: 3 } }).exportComplete, false);
  assert.equal(progress(project, { exportReceipt: { projectId: 'p', version: 2 } }).exportComplete, false);
});


test('automatic preparation drafts retain their saved completion until confirmed changes persist', () => {
  const saved = fixture({ automation: 'automatic', automaticOptions: null });
  const defaults = { useSelectedSources: true, useRecommendedSources: true, mode: 'strict', includeImages: true };
  const equivalent = progress({ ...saved, automaticOptions: defaults }, { savedProject: saved, dirty: true });
  assert.equal(step(equivalent, 'brief').complete, true);
  for (const changed of [{ useSelectedSources: false }, { useRecommendedSources: false }, { mode: 'extended' }, { includeImages: false }]) {
    const result = progress({ ...saved, automaticOptions: { ...defaults, ...changed } }, { savedProject: saved, dirty: true });
    assert.equal(step(result, 'brief').complete, true);
    const committed = { ...saved, automaticOptions: { ...defaults, ...changed }, workflowPendingSteps: ['brief'] };
    assert.equal(step(progress(committed), 'brief').complete, false);
  }
  assert.equal(saved.automaticOptions, null);
});

test('all six saved milestones remain complete during unsaved inputs and navigation', () => {
  const saved = full({ review: { version: 3, citationReviewCompleted: true, issues: [] } });
  const draft = structuredClone(saved); draft.topic = '未保存主题'; draft.sections[0].content = ''; draft.sections[0].title = '未保存章名';
  draft.outlineConfirmed = false; draft.activeSectionId = 'other'; draft.bodyView = 'edit';
  const result = deriveWritingProgress({ project: draft, savedProject: saved, dirty: true });
  assert.equal(result.completed, 6); assert.equal(result.reviewComplete, true); assert.equal(result.exportReady, false);
  assert.equal(result.body.completed, 1);
});

test('a persisted body checkpoint requires confirmation even when every chapter still has prose', () => {
  const changed = full({ workflowPendingSteps: ['body'] });
  const result = progress(changed);
  assert.equal(step(result, 'body').complete, false); assert.equal(result.currentId, 'body');
  assert.equal(result.next.label, '确认正文修改');
  assert.equal(step(progress({ ...changed, workflowPendingSteps: [] }), 'body').complete, true);
});

test('material pending markers persist across reload and do not rewrite the earlier saved topic', () => {
  const changed = full({ workflowPendingSteps: ['materials', 'body'], outlineConfirmed: false });
  const result = deriveWritingProgress({ project: structuredClone(changed), savedProject: changed });
  assert.equal(step(result, 'brief').complete, true); assert.equal(step(result, 'materials').complete, false);
  assert.equal(step(result, 'outline').complete, false); assert.equal(step(result, 'body').complete, false);
});

test('saved material metadata requires only its own confirmation when the scope did not change', () => {
  const changed = full({ workflowPendingSteps: ['materials'] });
  const result = progress(changed);
  assert.equal(step(result, 'brief').complete, true); assert.equal(step(result, 'materials').complete, false);
  assert.equal(step(result, 'outline').complete, true); assert.equal(step(result, 'body').complete, true);
  assert.equal(step(result, 'materials').stateLabel, '进行中');
});
