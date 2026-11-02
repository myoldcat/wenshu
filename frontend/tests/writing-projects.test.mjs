import test from 'node:test';
import assert from 'node:assert/strict';
import { compactWritingTag, formatWritingProjectTime, isWritingProjectActive, sortWritingProjects, writingProjectCardImage, writingProjectCardProgress, writingProjectCardStatus, writingProjectPhase } from '../src/writing-projects.js';
import { exportedAsDraft, reviewRequiresDraft } from '../src/writing-export-policy.js';

test('only actively running writing work enables card motion', () => {
  for (const kind of ['outline', 'section', 'document', 'edit']) {
    assert.equal(isWritingProjectActive({ runs: [{ kind, status: 'running' }] }), true);
    for (const status of ['queued', 'succeeded', 'failed', 'cancelled', 'paused']) assert.equal(isWritingProjectActive({ runs: [{ kind, status }] }), false);
  }
  for (const kind of ['images', 'image_batch', 'citations', 'citation_match']) assert.equal(isWritingProjectActive({ runs: [{ kind, status: 'running' }] }), false);
  assert.equal(isWritingProjectActive({}), false);
});

test('picture batch cards show picture work while preserving the body activity distinction', () => {
  assert.equal(writingProjectCardStatus({ runs: [{ kind: 'image_batch', status: 'running' }] }), '查找配图');
  assert.equal(writingProjectCardStatus({ runs: [{ kind: 'image_batch', status: 'queued' }] }), '排队中');
});

const fixture = () => [
  { id: 'b', title: '文章10', createdAt: '2026-10-20T02:00:00Z', updatedAt: '2026-10-23T01:00:00Z' },
  { id: 'a', title: '文章2', createdAt: '2026-10-22T02:00:00Z', updatedAt: '2026-10-22T01:00:00Z' },
  { id: 'c', title: '文章1', createdAt: '', updatedAt: 'invalid-time' },
];
test('defaults to latest modification and sorts creation independently without mutating input', () => {
  const items = fixture(), original = structuredClone(items);
  assert.deepEqual(sortWritingProjects(items).map(item => item.id), ['b', 'a', 'c']);
  assert.deepEqual(sortWritingProjects(items, 'createdAt').map(item => item.id), ['a', 'b', 'c']);
  assert.deepEqual(items, original);
});
test('title uses Chinese natural numeric sorting', () => {
  assert.deepEqual(sortWritingProjects(fixture(), 'title').map(item => item.title), ['文章1', '文章2', '文章10']);
  const items = [{ id: '3', title: '中国观察' }, { id: '2', title: '北京观察' }, { id: '1', title: '上海观察' }];
  assert.deepEqual(sortWritingProjects(items, 'title').map(item => item.title), ['北京观察', '上海观察', '中国观察']);
});
test('equal and absent dates have a stable title then identity tie-break', () => {
  const items = [{ id: 'z', title: '文章2' }, { id: 'b', title: '文章1' }, { id: 'a', title: '文章1' }];
  assert.deepEqual(sortWritingProjects(items).map(item => item.id), ['a', 'b', 'z']);
  items.forEach(item => { item.createdAt = '2026-10-20T02:00:00Z'; });
  assert.deepEqual(sortWritingProjects(items.reverse(), 'createdAt').map(item => item.id), ['a', 'b', 'z']);
});
test('missing creation timestamps never borrow modification or run times', () => {
  const items = [{ id: 'missing', title: 'a', createdAt: null, updatedAt: '2030-01-01T01:00:00Z', runs: [{ createdAt: '2031-01-01T01:00:00Z' }] }, { id: 'dated', title: 'b', createdAt: '2020-01-01T01:00:00Z' }];
  assert.deepEqual(sortWritingProjects(items, 'createdAt').map(item => item.id), ['dated', 'missing']);
  assert.equal(formatWritingProjectTime(items[0].createdAt), '未记录');
  assert.equal(formatWritingProjectTime('invalid-time'), '未记录');
});
test('display explicitly uses Beijing time with year, month, day, hour and minute', () => {
  assert.equal(formatWritingProjectTime('2026-10-23T06:20:00Z'), '2026年10月23日 14:20');
  assert.equal(formatWritingProjectTime('2026-10-22T23:05:00Z'), '2026年10月23日 07:05');
});

test('visible setting tags include the ellipsis within eight Unicode characters', () => {
  assert.equal(compactWritingTag('系统默认'), '系统默认');
  assert.equal(compactWritingTag('一二三四五六七八'), '一二三四五六七八');
  assert.equal(compactWritingTag('一二三四五六七八九'), '一二三四五六七…');
  assert.equal(compactWritingTag('😀😃😄😁😆😅😂🤣😊'), '😀😃😄😁😆😅😂…');
  assert.equal(Array.from(compactWritingTag('😀😃😄😁😆😅😂🤣😊')).length, 8);
  assert.equal(compactWritingTag('  清晰克制  '), '清晰克制');
  assert.equal(compactWritingTag(null), '');
  assert.equal(compactWritingTag('模型名称较长', 4), '模型名…');
});

const imageProject = () => ({
  sections: [{ id: 'first', content: '第一段原文。\n\n第二段原文。' }, { id: 'second', content: '下一章原文。', contentHash: 'chapter-hash' }],
  illustrations: [],
});
const adoptedFigure = (id, sectionId = 'first', paragraphIndex = 0, paragraphExact = '第一段原文。') => ({
  id, sectionId, url: `/api/assets/${id}`, status: 'accepted', reviewed: true,
  anchor: { paragraphIndex, paragraphExact, contentHash: sectionId === 'second' ? 'chapter-hash' : 'persisted-list-hash' },
});
test('manuscript pictures follow section then paragraph order rather than adoption order', () => {
  const project = imageProject();
  project.illustrations = [adoptedFigure('late-chapter', 'second', 0, '下一章原文。'), adoptedFigure('late-paragraph', 'first', 1, '第二段原文。'), adoptedFigure('opening')];
  const before = structuredClone(project);
  assert.equal(writingProjectCardImage(project).id, 'opening');
  assert.deepEqual(project, before);
});
test('pictures require accepted reviewed material anchored to current section text', () => {
  const invalid = [
    { status: 'stale' }, { status: 'recommended' }, { reviewed: false }, { url: '' },
    { sectionId: 'removed' }, { anchor: null },
    { anchor: { paragraphIndex: -1, paragraphExact: '第一段原文。' } },
    { anchor: { paragraphIndex: 0.5, paragraphExact: '第一段原文。' } },
    { anchor: { paragraphIndex: 0, paragraphExact: '已被改写的旧段落。' } },
  ];
  for (const patch of invalid) {
    const project = imageProject();
    project.illustrations = [{ ...adoptedFigure('invalid'), ...patch }, adoptedFigure('valid')];
    assert.equal(writingProjectCardImage(project).id, 'valid', JSON.stringify(patch));
  }
});
test('list snapshots can omit computed chapter hashes while details enforce any supplied hash', () => {
  const project = imageProject();
  project.illustrations = [adoptedFigure('list-image')];
  assert.equal(writingProjectCardImage(project).id, 'list-image');
  project.sections[0].contentHash = 'new-chapter-hash';
  assert.equal(writingProjectCardImage(project), null);
  project.illustrations[0].anchor.contentHash = 'new-chapter-hash';
  assert.equal(writingProjectCardImage(project).id, 'list-image');
});
test('empty manuscripts and search candidates alone never invent a card picture', () => {
  assert.equal(writingProjectCardImage({}), null);
  const project = imageProject();
  project.runs = [{ kind: 'images', candidate: { imageSearch: { candidates: [adoptedFigure('candidate-only')] } } }];
  assert.equal(writingProjectCardImage(project), null);
});

const phaseProject = () => ({
  id: 'phase-fixture', version: 7, outlineConfirmed: true,
  sections: [{ id: 'opening', content: '已保存的开篇正文。' }, { id: 'following', content: '已保存的下一章正文。' }],
  illustrations: [], illustrationReview: null, review: null,
});
const phaseOf = project => {
  const phase = writingProjectPhase(project);
  return { id: phase.id, label: phase.label };
};
test('a manuscript without sections stays in preparation even with historical completion records', () => {
  const project = phaseProject();
  project.sections = [];
  project.illustrationReview = { status: 'complete' };
  project.review = { version: project.version, wordCounts: { total: 1000 } };
  assert.deepEqual(phaseOf(project), { id: 'brief', label: '写作准备' });
  assert.deepEqual(phaseOf({}), { id: 'brief', label: '写作准备' });
});
test('an unconfirmed outline takes priority over existing body text and later review records', () => {
  const project = phaseProject();
  project.outlineConfirmed = false;
  project.illustrationReview = { status: 'complete' };
  project.review = { version: project.version, wordCounts: { total: 1000 } };
  assert.deepEqual(phaseOf(project), { id: 'outline', label: '纲要待确认' });
});
test('a confirmed outline with any empty chapter remains in body writing', () => {
  for (const content of ['', ' \n\t ', undefined]) {
    const project = phaseProject();
    project.sections[1].content = content;
    project.illustrationReview = { status: 'complete' };
    project.review = { version: project.version, wordCounts: { total: 1000 } };
    assert.deepEqual(phaseOf(project), { id: 'body', label: '正文写作' });
  }
});
test('complete body text needs a current illustration decision and stale pictures reopen that stage', () => {
  for (const status of [undefined, 'pending', 'stale']) {
    const project = phaseProject();
    project.illustrationReview = status ? { status } : null;
    assert.deepEqual(phaseOf(project), { id: 'images', label: '文章配图' });
  }
  for (const status of ['complete', 'skipped']) {
    const project = phaseProject();
    project.illustrationReview = { status };
    project.illustrations = [{ id: 'stale-figure', status: 'stale' }];
    project.review = { version: project.version, wordCounts: { total: 1000 } };
    assert.deepEqual(phaseOf(project), { id: 'images', label: '文章配图' });
  }
});
test('confirmed or skipped illustrations lead to full review until a current report has word counts', () => {
  for (const status of ['complete', 'skipped']) {
    for (const review of [null, { version: 7 }, { version: 6, wordCounts: { total: 1000 } }, { version: 7, wordCounts: null }]) {
      const project = phaseProject();
      project.illustrationReview = { status };
      project.review = review;
      assert.deepEqual(phaseOf(project), { id: 'review', label: '全文校验' });
    }
  }
});
test('only the current counted report finishes review and deriving a phase leaves the saved project intact', () => {
  for (const status of ['complete', 'skipped']) {
    const project = phaseProject();
    project.illustrationReview = { status };
    project.review = { version: project.version, wordCounts: { total: 1000 }, issues: [{ message: '仍需作者核对的说明' }] };
    const before = structuredClone(project);
    assert.deepEqual(phaseOf(project), { id: 'ready', label: '校验已完成' });
    assert.deepEqual(project, before);
  }
});

const progressProject = () => ({
  ...phaseProject(), title: '已保存的写作主题',
  sections: Array.from({ length: 6 }, (_, index) => ({ id: `section-${index + 1}`, content: '' })),
});
const assertProgress = (project, percent, stageValues) => {
  const value = writingProjectCardProgress(project);
  assert.ok(Math.abs(value.ratio - percent / 100) < 1e-10, `Expected ${percent}% but received ${value.ratio * 100}%`);
  assert.ok(Math.abs(value.percent - percent) < 1e-10, `Expected ${percent}% but received ${value.percent}%`);
  assert.deepEqual(value.stageValues, stageValues);
  return value;
};
test('five-stage card progress shares forty-percent body and twenty-percent image and review weights', () => {
  const project = progressProject();
  const initial = assertProgress(project, 20, [1, 1, 0, 0, 0]);
  assert.deepEqual(initial.stageIds, ['brief', 'outline', 'body', 'images', 'review']);
  assert.deepEqual(initial.stageWeights, [10, 10, 40, 20, 20]);
  for (let index = 0; index < project.sections.length; index += 1) {
    project.sections[index].content = `已保存的第 ${index + 1} 章。`;
    const ratio = (index + 1) / project.sections.length;
    const value = assertProgress(project, 20 + 40 * ratio, [1, 1, ratio, 0, 0]);
    assert.equal(value.completed, index + 1);
    assert.equal(value.total, 6);
  }
});
test('saved preparation takes ten percent while missing identity, title and confirmation gate later progress', () => {
  assertProgress({}, 0, [0, 0, 0, 0, 0]);
  assertProgress({ id: 'saved', title: '  已保存主题  ', sections: [] }, 10, [1, 0, 0, 0, 0]);
  assertProgress({ id: 'saved', topic: '  以主题继续写作  ', sections: [] }, 10, [1, 0, 0, 0, 0]);
  const project = progressProject();
  project.sections.forEach(section => { section.content = '已有正文。'; });
  project.illustrationReview = { status: 'complete' };
  project.review = { version: project.version, wordCounts: { total: 1000 } };
  project.outlineConfirmed = false;
  const value = assertProgress(project, 10, [1, 0, 0, 0, 0]);
  assert.equal(value.completed, 6, 'Existing body chapter counts remain available when the outline needs reconfirmation');
  project.outlineConfirmed = true;
  project.id = null;
  assertProgress(project, 0, [0, 0, 0, 0, 0]);
  project.id = 'saved'; project.title = ' \n\t ';
  assertProgress(project, 0, [0, 0, 0, 0, 0]);
});
test('empty chapters and unadopted candidates cannot advance body or unlock historical later-stage records', () => {
  const project = progressProject();
  project.sections[0].content = ' \n\t ';
  project.sections[1].content = undefined;
  project.runs = [{ kind: 'document', status: 'running', candidate: { document: { steps: [{ sectionId: project.sections[0].id, status: 'candidate', section: { content: '尚未采纳的正文。' } }] } } }];
  project.illustrationReview = { status: 'complete' };
  project.review = { version: project.version, wordCounts: { total: 1000 } };
  const value = assertProgress(project, 20, [1, 1, 0, 0, 0]);
  assert.equal(value.completed, 0);
  assert.equal(value.total, 6);
  project.sections[0].content = '已有正文。';
  assertProgress(project, 20 + 40 / 6, [1, 1, 1 / 6, 0, 0]);
});
test('only completed or explicitly skipped current illustrations unlock the last twenty percent for review', () => {
  const project = progressProject();
  project.sections.forEach(section => { section.content = '已保存正文。'; });
  for (const status of [undefined, 'pending', 'stale']) {
    project.illustrationReview = status ? { status } : null;
    project.review = { version: project.version, wordCounts: { total: 1000 } };
    assertProgress(project, 60, [1, 1, 1, 0, 0]);
  }
  for (const status of ['complete', 'skipped']) {
    project.illustrationReview = { status }; project.illustrations = [];
    for (const review of [null, { version: project.version }, { version: project.version - 1, wordCounts: { total: 1000 } }, { version: project.version, wordCounts: null }]) {
      project.review = review;
      const progress = assertProgress(project, 80, [1, 1, 1, 1, 0]);
      assert.equal(progress.stageStates[3], status === 'skipped' ? 'skipped' : 'complete');
    }
    project.review = { version: project.version, wordCounts: { total: 1000 }, issues: [{ message: '待作者核实' }] };
    const before = structuredClone(project);
    assertProgress(project, 100, [1, 1, 1, 1, 1]);
    assert.deepEqual(project, before, 'Card progress must remain a read-only view of the saved manuscript');
    project.illustrations = [{ status: 'stale' }];
    assertProgress(project, 60, [1, 1, 1, 0, 0]);
  }
});

const reviewedCardProject = () => ({
  ...phaseProject(), title: '当前已保存的文稿',
  illustrationReview: { status: 'skipped' },
  review: { version: 7, wordCounts: { total: 1000 }, issues: [], status: 'rules_passed' },
});
const historicalCardRuns = () => [
  { id: 'old-images', kind: 'images', status: 'succeeded', stage: 'candidate', applied: false,
    candidate: { imageSearch: { stale: true, candidates: [{ assetId: 'old-image' }] } } },
  { id: 'old-edit', kind: 'edit', status: 'succeeded', stage: 'candidate', applied: false, baseVersion: 1,
    candidate: { edit: { replacementText: '旧修改建议。' } } },
  { id: 'old-outline', kind: 'outline', status: 'succeeded', stage: 'candidate', applied: false, baseVersion: 1,
    candidate: { sections: [{ title: '旧纲要' }] } },
  { id: 'optional-section', kind: 'section', sectionId: 'opening', status: 'succeeded', stage: 'candidate', applied: false, canApply: true,
    candidate: { section: { content: '可比较的另一份正文。' } } },
  { id: 'optional-document', kind: 'document', status: 'succeeded', stage: 'candidate', applied: false,
    candidate: { document: { steps: [{ sectionId: 'opening', status: 'candidate', canApply: true, section: { content: '另一份全文候选。' } }] } } },
];

test('historical and optional proposals never replace the saved manuscript phase with a generic adoption status', () => {
  for (const run of historicalCardRuns()) {
    const ready = reviewedCardProject();
    ready.runs = [run];
    assert.equal(writingProjectCardStatus(ready), '已校验', run.kind);

    const preparing = { ...ready, sections: [] };
    assert.equal(writingProjectCardStatus(preparing), '待拟纲要', run.kind);
    assert.equal(writingProjectCardStatus({ ...ready, outlineConfirmed: false }), '待确认纲要', run.kind);

    const writing = structuredClone(ready);
    writing.sections[1].content = '';
    assert.equal(writingProjectCardStatus(writing), '待续写', run.kind);
    assert.equal(writingProjectCardStatus({ ...ready, illustrationReview: null }), '待确认配图', run.kind);
    assert.equal(writingProjectCardStatus({ ...ready, review: null }), '待全文校验', run.kind);
  }
});

test('an export receipt describes only the same manuscript version with a current counted review', () => {
  const project = reviewedCardProject();
  const receipt = { projectId: project.id, version: project.version, reviewStatus: 'rules_passed' };
  assert.equal(writingProjectCardStatus(project, receipt), '已导出');
  for (const invalid of [null, {}, { ...receipt, projectId: 'another-manuscript' }, { ...receipt, version: project.version - 1 }]) {
    assert.equal(writingProjectCardStatus(project, invalid), '已校验', JSON.stringify(invalid));
  }
  for (const review of [null, { version: project.version }, { version: project.version - 1, wordCounts: { total: 1000 } }, { version: project.version, wordCounts: null }]) {
    assert.equal(writingProjectCardStatus({ ...project, review }, receipt), '待全文校验', JSON.stringify(review));
  }
});

test('review issues keep both saved and exported manuscripts visibly in the draft boundary', () => {
  const project = reviewedCardProject();
  project.review.issues = [{ message: '正文仍有一项需要核对' }];
  assert.equal(writingProjectCardStatus(project), '待核对');
  const receipt = { projectId: project.id, version: project.version, reviewStatus: 'rules_passed' };
  assert.equal(writingProjectCardStatus(project, receipt), '草稿已导出');
  project.review.issues = [];
  assert.equal(writingProjectCardStatus(project, { ...receipt, reviewStatus: 'needs_attention' }), '草稿已导出');
  assert.equal(writingProjectCardStatus(project, receipt), '已导出');
});

test('current tasks take priority over completion receipts and optional historical candidates', () => {
  const project = reviewedCardProject();
  const receipt = { projectId: project.id, version: project.version, reviewStatus: 'rules_passed' };
  for (const kind of ['outline', 'section', 'document', 'edit']) {
    assert.equal(writingProjectCardStatus({ ...project, runs: [{ kind, status: 'running' }, ...historicalCardRuns()] }, receipt), '正在写作', kind);
  }
  assert.equal(writingProjectCardStatus({ ...project, runs: [{ kind: 'images', status: 'running' }] }, receipt), '查找配图');
  for (const kind of ['citations', 'citation_match']) {
    assert.equal(writingProjectCardStatus({ ...project, runs: [{ kind, status: 'running' }] }, receipt), '核对中', kind);
  }
  assert.equal(writingProjectCardStatus({ ...project, runs: [{ kind: 'section', status: 'queued' }] }, receipt), '排队中');
  assert.equal(writingProjectCardStatus({ ...project, runs: [{ kind: 'section', status: 'paused' }] }, receipt), '已暂停');
  assert.equal(writingProjectCardStatus({ ...project, runs: [{ kind: 'images', status: 'running' }, { kind: 'section', status: 'running' }] }, receipt), '正在写作');
  assert.equal(writingProjectCardStatus({ ...project, runs: [{ kind: 'section', status: 'paused' }, { kind: 'section', status: 'queued' }] }, receipt), '排队中');
});

test('card status derivation preserves saved text, history, candidates, issues and export receipts', () => {
  const project = reviewedCardProject();
  project.runs = historicalCardRuns();
  project.sections[0].locked = true;
  project.review.issues = [{ message: '保留校验说明' }];
  const receipt = { projectId: project.id, version: project.version, reviewStatus: 'needs_attention' };
  const beforeProject = structuredClone(project), beforeReceipt = structuredClone(receipt);
  assert.equal(writingProjectCardStatus(project), '待核对');
  assert.equal(writingProjectCardStatus(project, receipt), '草稿已导出');
  assert.deepEqual(project, beforeProject);
  assert.deepEqual(receipt, beforeReceipt);
});

const reminderCodes = ['section_word_count', 'total_word_count', 'source_new_revision', 'image_caption_missing', 'images_unconfirmed'];
const reminderIssues = () => reminderCodes.map(code => ({ code, severity: 'warning', message: '一般提醒' }));

test('only explicitly identified ordinary warnings allow export without draft consent', () => {
  assert.equal(reviewRequiresDraft({ issues: [] }), false);
  for (const code of reminderCodes) {
    assert.equal(reviewRequiresDraft({ issues: [{ code, severity: 'warning' }] }), false, code);
    for (const severity of [undefined, 'error', 'info', 'Warning']) {
      assert.equal(reviewRequiresDraft({ issues: [{ code, ...(severity === undefined ? {} : { severity }) }] }), true, `${code}:${severity}`);
    }
  }
  for (const issue of [
    { code: 'citation_sentence_unsupported', severity: 'warning' },
    { code: 'citation_anchor_invalid', severity: 'warning' },
    { code: 'unknown_future_issue', severity: 'warning' },
    { severity: 'warning' },
  ]) assert.equal(reviewRequiresDraft({ issues: [issue] }), true, JSON.stringify(issue));
  assert.equal(reviewRequiresDraft({ issues: [...reminderIssues(), { code: 'citation_binding_stale', severity: 'warning' }] }), true);
});

test('new export receipts carry the draft-consent decision while legacy receipts retain conservative fallback', () => {
  const reminders = { issues: reminderIssues() };
  const noIssues = { issues: [] };
  assert.equal(exportedAsDraft(reminders, { requiresDraftConsent: false, reviewStatus: 'needs_attention' }), false);
  assert.equal(exportedAsDraft(noIssues, { requiresDraftConsent: true, reviewStatus: 'rules_passed' }), true);
  assert.equal(exportedAsDraft(reminders, { reviewStatus: 'needs_attention' }), true);
  assert.equal(exportedAsDraft(noIssues, { reviewStatus: 'rules_passed' }), false);
  assert.equal(exportedAsDraft(reminders, { requiresDraftConsent: 'false', reviewStatus: 'needs_attention' }), true);
  assert.equal(exportedAsDraft({ issues: [{ code: 'citation_sentence_unsupported', severity: 'warning' }] }, { requiresDraftConsent: false, reviewStatus: 'rules_passed' }), true);
});

test('an explicit skipped review exports without issue consent and preserves its findings', () => {
  const review = { version: 3, citationReviewCompleted: false, authorConfirmed: false,
    disposition: { status: 'skipped' },
    issues: [{ code: 'citation_sentence_unsupported', severity: 'error' }] };
  const before = structuredClone(review);
  assert.equal(reviewRequiresDraft(review), false);
  for (const receipt of [undefined, { reviewStatus: 'needs_attention' },
    { reviewStatus: 'needs_attention', requiresDraftConsent: true },
    { reviewStatus: 'needs_attention', requiresDraftConsent: false }]) {
    assert.equal(exportedAsDraft(review, receipt), false);
  }
  assert.deepEqual(review, before);
  assert.equal(reviewRequiresDraft({ ...review, disposition: null }), true);
});

test('current skipped cards show exported even with old draft receipts while stale reviews remain gated', () => {
  const project = reviewedCardProject(); project.workflowVersion = 'writing-six-step-v1';
  Object.assign(project.review, { disposition: { status: 'skipped' }, citationReviewCompleted: false,
    issues: [{ code: 'citation_sentence_unsupported', severity: 'error' }] });
  const before = structuredClone(project);
  const receipt = { projectId: project.id, version: project.version,
    reviewStatus: 'needs_attention', requiresDraftConsent: true };
  assert.equal(writingProjectCardStatus(project), '已跳过校验');
  assert.equal(writingProjectCardStatus(project, receipt), '已导出');
  assert.deepEqual(project, before);
  project.review.version -= 1;
  assert.equal(writingProjectCardStatus(project, receipt), '待全文校验');
  assert.equal(writingProjectCardProgress(project).percent, 80);
});

test('ordinary reminders remain visible on cards but a new non-draft export receipt says exported', () => {
  const project = reviewedCardProject();
  project.review.issues = reminderIssues();
  project.review.status = 'needs_attention';
  project.runs = historicalCardRuns();
  const receipt = { projectId: project.id, version: project.version, reviewStatus: 'needs_attention', requiresDraftConsent: false };
  const beforeProject = structuredClone(project), beforeReceipt = structuredClone(receipt);
  assert.equal(writingProjectCardStatus(project), '待核对');
  assert.equal(writingProjectCardStatus(project, receipt), '已导出');
  assert.equal(writingProjectCardStatus(project, { ...receipt, requiresDraftConsent: true }), '草稿已导出');
  assert.deepEqual(project, beforeProject);
  assert.deepEqual(receipt, beforeReceipt);
});

test('a non-draft receipt cannot conceal evidence issues or unclassified findings on a card', () => {
  const project = reviewedCardProject();
  const receipt = { projectId: project.id, version: project.version, reviewStatus: 'rules_passed', requiresDraftConsent: false };
  for (const issue of [{ code: 'citation_sentence_unsupported', severity: 'warning' }, { code: 'section_word_count' }, { code: 'unknown_future_issue', severity: 'warning' }]) {
    project.review.issues = [...reminderIssues(), issue];
    assert.equal(writingProjectCardStatus(project, receipt), '草稿已导出', JSON.stringify(issue));
  }
});


test('new six-step cards reserve a material milestone and never count a rules-only report as verification', () => {
  const project = reviewedCardProject(); project.workflowVersion = 'writing-six-step-v1'; project.materialSelection = { confirmed: true }; project.review.citationReviewCompleted = false;
  const pending = writingProjectCardProgress(project);
  assert.deepEqual(pending.stageIds, ['brief', 'materials', 'outline', 'body', 'images', 'review']);
  assert.equal(pending.stageValues.length, 6); assert.equal(pending.percent, 80); assert.equal(writingProjectPhase(project).id, 'review');
  assert.deepEqual(pending.stageWeights, [5, 5, 10, 40, 20, 20]);
  project.review.citationReviewCompleted = true;
  assert.equal(writingProjectCardProgress(project).percent, 100);
});
test('new cards distinguish skipped checks and manual confirmation from model verification', () => {
  for (const [changes, label] of [[{ disposition: { status: 'skipped' }, citationReviewCompleted: false }, '已跳过校验'], [{ authorConfirmed: true, citationReviewCompleted: false }, '已人工确认']]) {
    const project = reviewedCardProject(); project.workflowVersion = 'writing-six-step-v1'; Object.assign(project.review, changes);
    assert.equal(writingProjectPhase(project).label, label); assert.equal(writingProjectCardStatus(project), label);
    const progress = writingProjectCardProgress(project);
    assert.equal(progress.percent, 100);
    assert.equal(progress.stageStates[4], 'skipped');
    assert.equal(progress.stageStates[5], changes.disposition ? 'skipped' : 'complete');
    assert.equal(project.review.citationReviewCompleted, false);
  }
});

test('six-step weighted progress follows saved chapter fractions without changing the manuscript', () => {
  const project = progressProject(); project.workflowVersion = 'writing-six-step-v1'; project.materialSelection = { confirmed: true };
  project.sections[0].content = '已保存的开篇。'; project.sections[1].content = '已保存的第二章。';
  const before = structuredClone(project), progress = writingProjectCardProgress(project);
  assert.deepEqual(progress.stageWeights, [5, 5, 10, 40, 20, 20]);
  assertProgress(project, 20 + 40 / 3, [1, 1, 1, 1 / 3, 0, 0]);
  assert.deepEqual(progress.stageStates, ['complete', 'complete', 'complete', 'running', 'waiting', 'waiting']);
  assert.equal(progress.percent, progress.stageValues.reduce((sum, value, index) => sum + value * progress.stageWeights[index], 0));
  assert.deepEqual(project, before);
});

test('only valid current saved skips use skipped metadata while stale stages retain their original gates', () => {
  const project = reviewedCardProject(); project.workflowVersion = 'writing-six-step-v1';
  project.review.disposition = { status: 'skipped' }; project.review.citationReviewCompleted = false;
  const before = structuredClone(project), ready = writingProjectCardProgress(project);
  assert.equal(ready.percent, 100);
  assert.deepEqual(ready.stageStates, ['complete', 'complete', 'complete', 'complete', 'skipped', 'skipped']);
  assert.deepEqual(project, before);
  project.review.version -= 1;
  const staleReview = writingProjectCardProgress(project);
  assert.equal(staleReview.percent, 80);
  assert.deepEqual(staleReview.stageStates.slice(-2), ['skipped', 'running']);
  project.illustrations = [{ status: 'stale' }];
  const staleImages = writingProjectCardProgress(project);
  assert.equal(staleImages.percent, 60);
  assert.deepEqual(staleImages.stageStates.slice(-2), ['running', 'waiting']);
  project.sections[0].content = '';
  assert.deepEqual(writingProjectCardProgress(project).stageStates.slice(-2), ['waiting', 'waiting']);
});
test('new topic-only cards lead to material selection and saved selection leads to outline', () => {
  const project = { id: 'new-sixstep-card', workflowVersion: 'writing-six-step-v1', title: '文章', topic: '主题', sections: [] };
  assert.equal(writingProjectPhase(project).id, 'materials'); assert.equal(writingProjectCardProgress(project).percent, 5);
  project.materialSelection = { confirmed: true };
  assert.equal(writingProjectPhase(project).id, 'outline'); assert.equal(writingProjectCardProgress(project).percent, 10);
});
