import test from 'node:test';
import assert from 'node:assert/strict';
import { isWritingProjectActive, writingProjectCardStatus } from '../src/writing-projects.js';
import { AUTOMATIC_STAGES, automaticBodyResult, automaticCurrentRetry, automaticFinished, automaticReportItems, automaticReportMessage, automaticReportRetries, automaticReportSummary, automaticReportText, automaticResultLabel, automaticRunning, automaticStageLabel, deriveAutomaticProgress, automaticWritingOptions, automaticWritingOptionPatch, newAutomaticWritingOptions, effectiveAutomaticMode, automaticUserSourceIds } from '../src/writing-automatic.js';
import { writingMessage } from '../src/writing-messages.js';
import { writingEditableFingerprint, writingProjectPatch } from '../src/writing-semantic.js';

test('only terminal automation exposes a downloadable report', () => {
  const report = { summary: { generatedSections: 2, preservedSections: 1, skippedSections: 0, illustrations: 0 }, issues: [{ stage: 'images', message: '没有符合图文关系要求的图片，保留纯文字。' }] };
  for (const status of ['queued', 'running']) {
    const automatic = { status, report };
    assert.equal(automaticRunning(automatic), true);
    assert.equal(automaticFinished(automatic), false);
    assert.equal(automaticReportText({ automatic }), '');
  }
  for (const status of ['completed', 'completed_with_issues', 'cancelled', 'failed']) {
    const automatic = { status, report };
    assert.equal(automaticFinished(automatic), true);
    assert.equal(automaticRunning(automatic), false);
    const result = automaticReportText({ title: '隔离样本', automatic });
    assert.match(result, /全自动写作报告/);
    assert.match(result, /生成章节 2 · 保留章节 1 · 未生成章节 0 · 采用配图 0/);
    assert.match(result, /没有符合图文关系要求的图片/);
    assert.doesNotMatch(result, /\[object Object\]/);
  }
});

test('report keeps material gaps and redacts implementation errors', () => {
  const report = { issues: ['参考资料不足以支持目标章节。', { stage: 'outline', message: 'JSON schema TypeError sourceId=secret' }] };
  const items = automaticReportItems(report);
  assert.equal(items[0].message, report.issues[0]);
  assert.equal(items[1].stage, '写作纲要');
  assert.doesNotMatch(items[1].message, /JSON|TypeError|sourceId|secret/);
  assert.equal(automaticReportSummary({}), '本次处理情况见报告，请结合文稿核对。');
  assert.equal(automaticReportSummary({ summary: { generatedSections: -1, illustrations: '4' } }), '本次处理情况见报告，请结合文稿核对。');
  assert.equal(automaticReportSummary({ outputAvailable: false }), '本次未生成正文，详情见处理报告。');
});

test('citation report items preserve public diagnostics without asking to regenerate', () => {
  const preserved = '已移除正文中未登记的引用编号，相关段落的引用信息未采用。';
  const technical = '第1章规划引文编号无效：O-B999，不在冻结原文块白名单内';
  const report = {
    outputAvailable: true,
    summary: { generatedSections: 2, preservedSections: 0, skippedSections: 0, illustrations: 0 },
    stages: [{ stage: 'citations', message: technical }],
    issues: [{ stage: 'chapters', sectionTitle: '原始证据', message: preserved }, { stage: 'outline', message: technical }],
  };
  const items = automaticReportItems(report);
  assert.equal(items[0].message, preserved);
  assert.equal(items[1].message, '引用对应问题已记录，请结合参考材料核对。');
  assert.equal(automaticReportMessage(technical, '本步骤已处理。'), items[1].message);
  assert.equal(automaticReportMessage(null), '这部分处理未能完成，请结合文稿核对。');
  const text = automaticReportText({ title: '隔离引用样本', automatic: { status: 'completed_with_issues', report } });
  for (const item of items) assert.ok(text.includes(item.message));
  assert.match(text, /正文已生成 · 附报告/);
  assert.doesNotMatch(text, /重新生成|O-B999|冻结|白名单|未能完成/);
  assert.match(writingMessage(technical), /请重新生成/); // Ordinary task errors retain their own handling.
});

test('recorded citation errors do not claim cancelled or changed work continued', () => {
  const stopped = '写作过程中设置、资料或文章版本发生变化，已保留最新内容并结束本次任务。';
  const report = { outputAvailable: true, issues: [
    { code: 'project_changed', message: stopped },
    { message: '生成内容中的引用无法与所选原文对应，请重新生成，并核对参考材料。当前文稿已保留。' },
  ] };
  const items = automaticReportItems(report);
  assert.equal(items[0].message, stopped);
  assert.equal(items[1].message, '引用对应问题已记录，请结合参考材料核对。');
  const text = automaticReportText({ automatic: { status: 'cancelled', report } });
  assert.match(text, /全自动写作已取消/);
  assert.match(text, /结束本次任务/);
  assert.doesNotMatch(text, /重新生成|已继续|流程继续|正文已生成/);
});

test('automatic stage names remain business labels for server aliases', () => {
  assert.equal(automaticStageLabel('document'), '正文编写');
  assert.equal(automaticStageLabel('chapters'), '正文编写');
  assert.equal(automaticStageLabel('preparing'), '资料筛选');
  assert.equal(automaticStageLabel('citation_match'), '检查校验');
  assert.equal(automaticStageLabel('unknown-internal-state'), '正在写作');
  assert.equal(automaticReportText({ automatic: { status: 'completed' } }), '');
});

test('active automatic progress describes handled phases without document completion or navigation', () => {
  for (const [stageIndex, [stage]] of AUTOMATIC_STAGES.entries()) {
    const automatic = Object.freeze({ status: 'running', stage });
    const progress = deriveAutomaticProgress(automatic);
    assert.equal(progress.automatic, true);
    assert.equal(progress.currentId, stage);
    assert.equal(progress.completed, stageIndex);
    assert.equal(progress.total, 6);
    assert.deepEqual(progress.steps.map(step => [step.id, step.label]), AUTOMATIC_STAGES);
    assert.equal(progress.steps.filter(step => step.complete).length, stageIndex);
    for (const [index, step] of progress.steps.entries()) {
      assert.equal(step.complete, index < stageIndex);
      assert.equal(step.available, false);
      assert.equal(step.stateLabel, index < stageIndex ? '已处理' : index === stageIndex ? '进行中' : '未开始');
      assert.equal(step.detail, index < stageIndex ? `${step.label}已处理` : index === stageIndex ? `${step.label}正在进行` : '待处理');
    }
    assert.doesNotMatch(JSON.stringify(progress), /已完成|已通过|正文已生成|导出|人工/);
  }
});

test('automatic progress shares all server stage aliases with the existing stage labels', () => {
  for (const [alias, id] of [
    ['preparation', 'materials'], ['preparing', 'materials'], ['document', 'body'],
    ['chapters', 'body'], ['section', 'body'], ['citation_match', 'review'],
    ['complete', 'review'], ['completed', 'review'],
  ]) {
    const progress = deriveAutomaticProgress({ status: 'running', stage: alias });
    assert.equal(progress.currentId, id);
    assert.equal(progress.steps.find(step => step.id === id).label, automaticStageLabel(alias));
    assert.equal(progress.completed, AUTOMATIC_STAGES.findIndex(([key]) => key === id));
    assert.equal(progress.steps.find(step => step.id === id).complete, false);
  }
});

test('queued automatic progress counts only confirmed initial topic', () => {
  for (const stage of [undefined, 'preparing', 'chapters', 'complete', 'unknown-phase']) {
    const progress = deriveAutomaticProgress({ status: 'queued', stage });
    assert.equal(progress.completed, 1);
    assert.equal(progress.currentId, 'materials');
    assert.equal(progress.steps[0].complete, true);
    assert.ok(progress.steps.every(step => !step.available));
    assert.match(progress.steps[1].detail, /排队/);
    assert.ok(progress.steps.slice(2).every(step => step.detail === '待处理'));
  }
});

test('unknown active phases do not guess or count processed steps', () => {
  for (const stage of [undefined, null, '', 'unknown-phase']) {
    const progress = deriveAutomaticProgress({ status: 'running', stage });
    assert.equal(progress.currentId, null);
    assert.equal(progress.completed, 0);
    assert.ok(progress.steps.every(step => !step.complete && !step.available && step.stateLabel === '未开始' && step.detail === '待处理'));
    assert.equal(automaticStageLabel(stage), '正在写作');
  }
});

test('terminal or absent automatic state leaves progress to real manuscript milestones', () => {
  for (const status of ['succeeded', 'completed', 'completed_with_issues', 'failed', 'cancelled', undefined, 'unknown-status']) {
    assert.equal(deriveAutomaticProgress({ status, stage: 'complete' }), null);
  }
  assert.equal(deriveAutomaticProgress(null), null);
  assert.equal(deriveAutomaticProgress(), null);
});

test('edited manuscripts retain frozen report title and identify historical processing', () => {
  const result = automaticReportText({ title: '后来改的标题', version: 9, automatic: { status: 'completed', report: { projectVersion: 8, title: '原有标题', summary: {}, stages: [{ stage: 'images', message: '本次保留纯文字。' }], issues: [] } } });
  assert.match(result, /^原有标题 · 全自动写作报告/);
  assert.match(result, /已有修改/);
  assert.match(result, /本次保留纯文字/);
  assert.doesNotMatch(result, /后来改的标题/);
});

test('cards follow active automation and only the matching completed manuscript version', () => {
  const project = { id: 'automatic-card', title: '自动文稿', version: 8, sections: [], runs: [{ kind: 'automatic', status: 'running' }] };
  assert.equal(isWritingProjectActive(project), true);
  assert.equal(writingProjectCardStatus(project), '正在写作');
  project.runs = [];
  project.automatic = { status: 'completed_with_issues', report: { projectVersion: 8 } };
  assert.equal(writingProjectCardStatus(project), '未生成正文');
  project.version = 9;
  assert.equal(writingProjectCardStatus(project), '待拟纲要');
});

test('empty and partial automation results never imply a completed manuscript', () => {
  for (const status of ['completed', 'completed_with_issues', 'failed', 'cancelled']) {
    const automatic = { status, report: { projectVersion: 8, outputAvailable: false, summary: { generatedSections: 0, preservedSections: 0, skippedSections: 0 } } };
    assert.equal(automaticBodyResult(automatic), 'none');
    assert.equal(writingProjectCardStatus({ version: 8, automatic, sections: [] }), status === 'cancelled' ? '已取消 · 未生成正文' : '未生成正文');
    assert.match(automaticReportText({ automatic }), /未生成正文/);
    assert.doesNotMatch(automaticReportText({ automatic }), /写作已完成|内容已保存/);
  }
  const automatic = { status: 'completed_with_issues', report: { projectVersion: 8, outputAvailable: true, summary: { generatedSections: 1, preservedSections: 0, skippedSections: 1 } } };
  assert.equal(automaticResultLabel(automatic), '正文部分完成');
  assert.equal(writingProjectCardStatus({ version: 8, automatic, sections: [{ content: '已生成。' }, { content: '' }] }), '正文部分完成');
  automatic.report.summary.skippedSections = 0;
  assert.equal(automaticResultLabel(automatic, [{ content: '已生成。' }, { content: '' }]), '正文部分完成');
  assert.equal(automaticResultLabel(automatic, [{ content: '已生成。' }]), '正文已生成 · 附报告');
  assert.equal(automaticResultLabel({ status: 'completed_with_issues' }), '正文结果待核对');
});


test('automatic processing exposes only actual readable material, outline and body pages', () => {
  const automatic = { status: 'running', stage: 'chapters' };
  const project = { topic: '主题', sourceIds: ['source-one'], sections: [] };
  let steps = deriveAutomaticProgress(automatic, project).steps;
  assert.equal(steps.find(step => step.id === 'brief').available, true);
  assert.equal(steps.find(step => step.id === 'materials').available, true);
  assert.equal(steps.find(step => step.id === 'outline').available, false);
  assert.equal(steps.find(step => step.id === 'body').available, false);
  project.sections = [{ id: 'one', content: '已生成的第一章。[E1]', locked: false }, { id: 'two', content: '', locked: true }];
  const snapshot = JSON.stringify(project);
  steps = deriveAutomaticProgress(automatic, project).steps;
  assert.equal(steps.find(step => step.id === 'outline').available, true);
  assert.equal(steps.find(step => step.id === 'body').available, true);
  assert.equal(steps.find(step => step.id === 'images').available, false);
  assert.equal(steps.find(step => step.id === 'review').available, false);
  assert.equal(JSON.stringify(project), snapshot);
});

test('automatic navigation can inspect completed no-image decisions and saved reports without claiming support', () => {
  const automatic = { status: 'running', stage: 'review' };
  const project = { materialSelection: { confirmed: true }, sections: [{ content: '已完成的正文。' }], illustrationReview: { status: 'skipped' }, review: { version: 2, citationReviewCompleted: false } };
  const progress = deriveAutomaticProgress(automatic, project);
  assert.equal(progress.steps.find(step => step.id === 'materials').available, true);
  assert.equal(progress.steps.find(step => step.id === 'images').available, true);
  assert.equal(progress.steps.find(step => step.id === 'review').available, true);
  assert.equal(progress.steps.find(step => step.id === 'review').complete, false);
  assert.equal(progress.currentId, 'review');
});


test('new automatic form defaults both source pools and strict preference independently of available documents', () => {
  const project = { sourceIds: [], mode: 'expanded', automaticOptions: null };
  const options = automaticWritingOptions(project);
  assert.deepEqual(options, { useSelectedSources: true, useRecommendedSources: true, mode: 'strict', includeImages: false, skipReview: true });
  assert.equal(effectiveAutomaticMode(options), 'strict');
  assert.equal(project.automaticOptions, null);
});
test('all four automatic source combinations preserve mode preference while no-source work expands', () => {
  for (const useSelectedSources of [false, true]) for (const useRecommendedSources of [false, true]) for (const mode of ['strict', 'extended']) {
    const saved = { useSelectedSources, useRecommendedSources, mode, includeImages: false };
    const options = automaticWritingOptions({ automaticOptions: saved });
    assert.deepEqual(options, { ...saved, skipReview: false });
    assert.equal(effectiveAutomaticMode(options), useSelectedSources || useRecommendedSources ? mode : 'extended');
    assert.equal(options.mode, mode);
    assert.equal(options.includeImages, false);
  }
});
test('legacy saved automatic choices map source and mode intent without mutating frozen records', () => {
  const old = { sourceIds: ['a'], mode: 'strict', automaticOptions: { restrictToSelectedSources: true, includeImages: false }, automatic: { runId: 'old-frozen', report: { title: '历史记录' } } };
  const snapshot = JSON.stringify(old);
  assert.deepEqual(automaticWritingOptions(old), { useSelectedSources: true, useRecommendedSources: false, mode: 'strict', includeImages: false, skipReview: false });
  assert.equal(JSON.stringify(old), snapshot);
  assert.deepEqual(automaticWritingOptions({ mode: 'expanded', brief: { automaticOptions: { restrictToSelectedSources: false } } }), { useSelectedSources: true, useRecommendedSources: true, mode: 'extended', includeImages: true, skipReview: false });
});


test('legacy unrestricted choice stays expanded even when old project mode is temporarily strict', () => {
  assert.deepEqual(automaticWritingOptions({ mode: 'strict', automaticOptions: { restrictToSelectedSources: false, includeImages: true } }), { useSelectedSources: true, useRecommendedSources: true, mode: 'extended', includeImages: true, skipReview: false });
});

test('new options are fresh values while saved manuscripts without options keep their legacy defaults', () => {
  const fresh = newAutomaticWritingOptions();
  fresh.includeImages = true; fresh.skipReview = false;
  assert.deepEqual(newAutomaticWritingOptions(), { useSelectedSources: true, useRecommendedSources: true, mode: 'strict', includeImages: false, skipReview: true });
  const saved = { id: 'saved-without-options', brief: { automation: 'automatic' } }, before = JSON.stringify(saved);
  assert.deepEqual(automaticWritingOptions(saved), { useSelectedSources: true, useRecommendedSources: true, mode: 'strict', includeImages: true, skipReview: false });
  assert.equal(JSON.stringify(saved), before);
});

test('saved picture and review decisions survive reopening independently of new defaults', () => {
  for (const includeImages of [false, true]) for (const skipReview of [false, true]) {
    const choices = { useSelectedSources: false, useRecommendedSources: true, mode: 'extended', includeImages, skipReview };
    const project = { id: 'saved-choice', brief: { automaticOptions: choices }, automatic: { status: 'running', runId: 'frozen-choice' } };
    const before = JSON.stringify(project);
    assert.deepEqual(automaticWritingOptions(project), choices);
    assert.equal(JSON.stringify(project), before);
  }
});

test('picture and review choices do not change a saved no-source manuscript mode', () => {
  const saved = { id: 'no-fixed-sources', sourceIds: [], mode: 'expanded', automaticOptions: {
    useSelectedSources: true, useRecommendedSources: true, mode: 'strict', includeImages: true, skipReview: false,
  } };
  const before = JSON.stringify(saved);
  for (const key of ['includeImages', 'skipReview']) {
    const changed = { ...saved, ...automaticWritingOptionPatch(saved, key, !saved.automaticOptions[key]) };
    assert.equal(changed.mode, 'expanded');
    const reverted = { ...changed, ...automaticWritingOptionPatch(changed, key, saved.automaticOptions[key]) };
    assert.deepEqual(reverted, saved);
  }
  assert.equal(JSON.stringify(saved), before);
  const unrestricted = { ...saved, automaticOptions: { ...saved.automaticOptions, useSelectedSources: false } };
  assert.equal(automaticWritingOptionPatch(unrestricted, 'useRecommendedSources', false).mode, 'expanded');
  assert.equal(automaticWritingOptionPatch(unrestricted, 'useSelectedSources', true).mode, 'strict');
});

test('picture and review edits preserve legacy source choices and revert without a save', () => {
  for (const restrictToSelectedSources of [false, true]) for (const key of ['includeImages', 'skipReview']) {
    const saved = { id: 'legacy-delivery-options', automation: 'automatic', mode: restrictToSelectedSources ? 'strict' : 'expanded',
      sourceIds: ['pinned-source'], materialSelection: { userSourceIds: ['pinned-source'], confirmed: true },
      brief: { topic: '已有主题', automaticOptions: { restrictToSelectedSources, includeImages: true } } };
    const before = JSON.stringify(saved), original = automaticWritingOptions(saved)[key];
    const changed = { ...saved, ...automaticWritingOptionPatch(saved, key, !original) };
    assert.equal(changed.mode, saved.mode);
    assert.deepEqual(changed.sourceIds, saved.sourceIds);
    assert.equal(changed.automaticOptions.restrictToSelectedSources, restrictToSelectedSources);
    assert.equal(Object.hasOwn(changed.automaticOptions, 'useSelectedSources'), false);
    assert.equal(Object.hasOwn(changed.automaticOptions, 'useRecommendedSources'), false);
    assert.notEqual(writingEditableFingerprint(changed), writingEditableFingerprint(saved));
    const reverted = { ...changed, ...automaticWritingOptionPatch(changed, key, original) };
    assert.equal(writingEditableFingerprint(reverted), writingEditableFingerprint(saved));
    assert.deepEqual(writingProjectPatch(reverted, saved), {});
    assert.equal(JSON.stringify(saved), before);
    assert.equal(Object.hasOwn(automaticWritingOptionPatch(saved, 'useSelectedSources', true).automaticOptions, 'restrictToSelectedSources'), false);
  }
});


test('selected-source card counts only recorded user choices still pinned in the current manuscript', () => {
  const project = { sourceIds: ['own-a', 'recommended-b'], materialSelection: { userSourceIds: ['own-a', 'missing-old'] } };
  const snapshot = JSON.stringify(project);
  assert.deepEqual(automaticUserSourceIds(project), ['own-a']);
  assert.equal(JSON.stringify(project), snapshot);
  assert.deepEqual(automaticUserSourceIds({ ...project, materialSelection: { userSourceIds: [] } }), []);
  assert.deepEqual(automaticUserSourceIds({ ...project, materialSelection: {} }), ['own-a', 'recommended-b']);
});

test('retry report distinguishes recovered requests from exhausted attempts and exports both', () => {
  const report = {
    title: '隔离重试样本', projectVersion: 8, outputAvailable: true,
    summary: { generatedSections: 1, skippedSections: 1, retryRequests: 2, recoveredRequests: 1, exhaustedRequests: 1 },
    retries: [
      { stage: 'chapters', sectionId: 'private-section-four', sectionTitle: '第四章', retryCount: 1, maxRetries: 2, status: 'recovered', category: 'format_error', reason: '返回格式错误' },
      { stage: 'chapters', sectionId: 'private-section-five', sectionTitle: '第五章', retryCount: 2, maxRetries: 2, status: 'exhausted', category: 'interrupted', reason: '生成结果异常中断' },
    ], issues: [],
  };
  const before = JSON.stringify(report), retries = automaticReportRetries(report);
  assert.equal(retries.length, 2);
  assert.equal(retries[0].stage, '正文编写');
  assert.equal(retries[0].message, '返回格式错误，第1次重试成功。');
  assert.equal(retries[1].message, '生成结果异常中断，已重试2次，仍未完成。');
  const text = automaticReportText({ version: 8, automatic: { status: 'completed_with_issues', report } });
  assert.match(text, /正文部分完成/);
  assert.match(text, /发生重试 2 · 重试成功 1 · 重试耗尽 1/);
  assert.match(text, /重试记录（2）/);
  for (const retry of retries) {
    assert.ok(text.includes(`${retry.stage} · ${retry.sectionTitle} · ${retry.message}`));
  }
  assert.doesNotMatch(text, /private-section|format_error|interrupted|retryCount|maxRetries|\[object Object\]/);
  assert.equal(JSON.stringify(report), before);
});

test('current retry is visible only during running work with a valid bounded retry number', () => {
  const currentRetry = { stage: 'chapters', sectionTitle: '第四章', retryCount: 1, maxRetries: 2, status: 'retrying', category: 'format_error' };
  assert.equal(automaticCurrentRetry({ status: 'running', currentRetry }).message, '返回格式错误，正在第1次重试（最多2次）。');
  assert.equal(automaticCurrentRetry({ status: 'running', currentRetry: { ...currentRetry, retryCount: 2, category: 'network' } }).message, '模型连接中断或请求超时，正在第2次重试（最多2次）。');
  for (const status of ['queued', 'completed', 'completed_with_issues', 'cancelled', 'failed']) {
    assert.equal(automaticCurrentRetry({ status, currentRetry }), null);
  }
  for (const retryCount of [0, 3, '1', null]) assert.equal(automaticCurrentRetry({ status: 'running', currentRetry: { ...currentRetry, retryCount } }), null);
  assert.equal(automaticCurrentRetry({ status: 'running', currentRetry: { ...currentRetry, status: 'recovered' } }), null);
  assert.equal(automaticCurrentRetry({ status: 'running' }), null);
});

test('retry presentation suppresses raw errors and ignores unfinished or malformed report records', () => {
  const record = { stage: 'outline', retryCount: 2, maxRetries: 2, status: 'exhausted', reason: 'JSON Extra data traceback sourceId=private-source runId=private-run' };
  const report = { outputAvailable: false, retries: [record, null, { ...record, retryCount: 4 }, { ...record, status: 'retrying' }, { ...record, status: 'unknown' }] };
  const retries = automaticReportRetries(report);
  assert.equal(retries.length, 1);
  assert.equal(retries[0].message, '生成结果异常中断，已重试2次，仍未完成。');
  const text = automaticReportText({ automatic: { status: 'failed', report } });
  assert.match(text, /未生成正文/);
  assert.doesNotMatch(text, /JSON|Extra data|traceback|private|sourceId|runId|正在.*重试/);
  assert.equal(automaticReportRetries({ retries: [{ ...record, category: 'toString' }] })[0].message, retries[0].message);
});

test('older reports retain their text and zero retries do not add empty retry sections', () => {
  const report = { outputAvailable: false, summary: { generatedSections: 0 }, issues: [] };
  const project = { title: '历史文稿', automatic: { status: 'completed', report } };
  const previous = automaticReportText(project);
  assert.deepEqual(automaticReportRetries(report), []);
  assert.doesNotMatch(previous, /重试/);
  report.retries = [];
  Object.assign(report.summary, { retryRequests: 0, recoveredRequests: 0, exhaustedRequests: 0 });
  assert.equal(automaticReportText(project), previous);
  assert.deepEqual(automaticReportRetries({ retries: {} }), []);
});
