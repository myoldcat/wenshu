import test from 'node:test';
import assert from 'node:assert/strict';
import { writingEditableFingerprint, writingProjectFingerprint, writingMaterialScopeFingerprint,
  writingBriefPayload, writingProjectPayload, writingProjectPatch, writingOutlineFingerprint } from '../src/writing-semantic.js';
import { DEFAULT_STRUCTURE_TEMPLATE, structureTemplates, structureTemplateOptions, structureTemplatePatch,
  writingStylePatch, restoreLegacyWritingPreset } from '../src/writing-presets.js';

const fixture = () => ({ id: 'saved', version: 5, title: '已有标题', articleTitle: '已有标题', topic: '主题', purpose: '',
  targetWords: 6000, automation: 'manual', mode: 'extended', sourceIds: ['a', 'b'],
  brief: { topic: '主题', articleTitle: '已有标题', retainedLegacy: { first: 1, second: 2 } },
  materialSelection: { confirmed: true, userSourceIds: ['a'], recommendationId: null, passages: [
    { sourceId: 'a', revisionId: 'ra', blockId: 'a1' }, { sourceId: 'b', revisionId: 'rb', blockId: 'b1' }] },
  outlineConfirmed: true, sections: [{ id: 'section_a', title: '第一章', goal: '', mode: 'inherit', words: 600, content: '原正文。', locked: false },
    { id: 'section_b', title: '第二章', goal: '', mode: 'extended', words: 600, content: '后续正文。', locked: true }] });

test('navigation, candidate viewing and completion metadata never become editable intent', () => {
  const saved = fixture(), viewed = { ...saved, activeSectionId: 'section_b', bodyView: 'edit', stage: 'outline',
    outlineConfirmed: false, candidate: { sectionId: 'section_b' }, review: { version: 5 }, runs: [{ status: 'succeeded' }] };
  assert.equal(writingEditableFingerprint(viewed), writingEditableFingerprint(saved));
  assert.deepEqual(writingProjectPatch(viewed, saved), {});
});

test('key order, numeric form values, mode aliases and material sets are canonical', () => {
  const saved = fixture(), draft = structuredClone(saved);
  draft.mode = 'expanded'; draft.targetWords = '6000'; draft.sections[1].mode = 'expanded'; draft.sections[0].words = '600';
  draft.sourceIds.reverse(); draft.materialSelection.passages.reverse();
  draft.brief.retainedLegacy = { second: 2, first: 1 };
  assert.equal(writingEditableFingerprint(draft), writingEditableFingerprint(saved));
  assert.deepEqual(writingProjectPatch(draft, saved), {});
});

test('unknown historical brief fields survive a genuine brief update', () => {
  const saved = fixture(), draft = { ...saved, purpose: '新的写作要求' };
  assert.deepEqual(writingBriefPayload(draft).retainedLegacy, { first: 1, second: 2 });
  const patch = writingProjectPatch(draft, saved);
  assert.deepEqual(Object.keys(patch), ['brief']);
  assert.equal(patch.brief.purpose, '新的写作要求');
  assert.deepEqual(patch.brief.retainedLegacy, saved.brief.retainedLegacy);
});

test('recommendation metadata can save without re-sending brief or changing material scope', () => {
  const saved = fixture(), draft = structuredClone(saved);
  draft.materialSelection.queries = ['新条件']; draft.materialSelection.items = [{ sourceId: 'b', summary: '新的显示摘要' }];
  assert.equal(writingMaterialScopeFingerprint(draft), writingMaterialScopeFingerprint(saved));
  assert.notEqual(writingProjectFingerprint(draft), writingProjectFingerprint(saved));
  assert.deepEqual(Object.keys(writingProjectPatch(draft, saved)), ['materialSelection']);
});

test('an omitted user-source list differs from an explicitly cleared list', () => {
  const saved = fixture(), inherited = structuredClone(saved), cleared = structuredClone(saved);
  delete inherited.materialSelection.userSourceIds; cleared.materialSelection.userSourceIds = [];
  assert.notEqual(writingMaterialScopeFingerprint(inherited), writingMaterialScopeFingerprint(cleared));
  assert.notEqual(writingEditableFingerprint(inherited), writingEditableFingerprint(cleared));
});

test('paragraph-only scope is not equivalent to a whole-document selection', () => {
  const saved = fixture(), draft = structuredClone(saved); draft.materialSelection.passages = [];
  assert.notEqual(writingMaterialScopeFingerprint(draft), writingMaterialScopeFingerprint(saved));
});

test('chapter order, query order and exact body whitespace retain meaning', () => {
  const saved = fixture();
  for (const change of [
    draft => draft.sections.reverse(),
    draft => { draft.sections[0].content += '\n'; },
    draft => { draft.sections[0].content = '原 正文。'; },
    draft => { draft.sections[0].locked = true; },
  ]) { const draft = structuredClone(saved); change(draft); assert.notEqual(writingEditableFingerprint(draft), writingEditableFingerprint(saved)); }
  const a = structuredClone(saved), b = structuredClone(saved);
  a.materialSelection.queries = ['第一条', '第二条']; b.materialSelection.queries = ['第二条', '第一条'];
  assert.notEqual(writingEditableFingerprint(a), writingEditableFingerprint(b));
});

test('outline outer whitespace follows the backend text normalization while body does not', () => {
  const saved = fixture(), draft = structuredClone(saved);
  draft.sections[0].title = ' 第一章 '; draft.sections[0].goal = '  ';
  assert.equal(writingOutlineFingerprint(draft), writingOutlineFingerprint(saved));
  assert.equal(writingEditableFingerprint(draft), writingEditableFingerprint(saved));
});

test('new automatic source protocol is not silently collapsed into old options', () => {
  const saved = fixture(), draft = structuredClone(saved);
  saved.automation = draft.automation = 'automatic';
  saved.automaticOptions = { restrictToSelectedSources: true, includeImages: true };
  draft.automaticOptions = { useSelectedSources: true, useRecommendedSources: false, mode: 'strict', includeImages: true };
  assert.notEqual(writingEditableFingerprint(draft), writingEditableFingerprint(saved));
  assert.ok(writingProjectPatch(draft, saved).brief.automaticOptions.useSelectedSources);
});

test('empty or boolean lengths and malformed historical option values never collapse into valid intent', () => {
  const zero = { ...fixture(), targetWords: 0 };
  for (const targetWords of ['', false]) {
    const draft = { ...zero, targetWords };
    assert.notEqual(writingEditableFingerprint(draft), writingEditableFingerprint(zero));
    assert.equal(writingBriefPayload(draft).targetWords, targetWords);
  }
  const emptyOptions = { ...fixture(), automation: 'automatic', automaticOptions: {} };
  for (const automaticOptions of ['', false, 0, 'legacy', []]) {
    const draft = { ...emptyOptions, automaticOptions };
    assert.notEqual(writingEditableFingerprint(draft), writingEditableFingerprint(emptyOptions));
    assert.deepEqual(writingBriefPayload(draft).automaticOptions, automaticOptions);
  }
});

test('legacy missing review choice is equivalent to explicit checking without changing saved payload', () => {
  for (const automaticOptions of [
    { restrictToSelectedSources: true, includeImages: true },
    { useSelectedSources: true, useRecommendedSources: true, mode: 'strict', includeImages: true },
  ]) {
    const saved = { ...fixture(), automaticOptions }, draft = structuredClone(saved), before = JSON.stringify(saved);
    draft.automaticOptions.skipReview = false;
    assert.equal(writingEditableFingerprint(draft), writingEditableFingerprint(saved));
    assert.deepEqual(writingProjectPatch(draft, saved), {});
    assert.equal(Object.hasOwn(writingBriefPayload(saved).automaticOptions, 'skipReview'), false);
    assert.equal(JSON.stringify(saved), before);
    draft.automaticOptions.skipReview = true;
    assert.notEqual(writingEditableFingerprint(draft), writingEditableFingerprint(saved));
    assert.equal(writingProjectPatch(draft, saved).brief.automaticOptions.skipReview, true);
  }
});

test('explicit new skip and picture decisions serialize and revert without an unnecessary update', () => {
  const saved = { ...fixture(), automation: 'automatic', automaticOptions: {
    useSelectedSources: true, useRecommendedSources: true, mode: 'strict', includeImages: false, skipReview: true,
  } };
  for (const key of ['includeImages', 'skipReview']) {
    const draft = structuredClone(saved);
    draft.automaticOptions[key] = !saved.automaticOptions[key];
    assert.equal(writingProjectPayload(draft).brief.automaticOptions[key], !saved.automaticOptions[key]);
    assert.equal(writingProjectPatch(draft, saved).brief.automaticOptions[key], !saved.automaticOptions[key]);
    draft.automaticOptions[key] = saved.automaticOptions[key];
    assert.deepEqual(writingProjectPatch(draft, saved), {});
  }
});

test('interactive generation remains optional while an explicit chapter preference is serialized', () => {
  const saved = fixture(), before = JSON.stringify(saved);
  assert.equal(Object.hasOwn(writingBriefPayload(saved), 'interactiveGeneration'), false);
  const changed = { ...saved, interactiveGeneration: 'chapter' };
  assert.equal(writingProjectPayload(changed).brief.interactiveGeneration, 'chapter');
  assert.deepEqual(Object.keys(writingProjectPatch(changed, saved)), ['brief']);
  assert.equal(writingProjectPatch(changed, saved).brief.interactiveGeneration, 'chapter');
  assert.equal(JSON.stringify(saved), before, 'Rendering or deriving intent must not migrate the saved brief');
});

test('reverting to the implicit single default is a no-op without adding it to a legacy brief', () => {
  const saved = fixture(), draft = { ...saved, interactiveGeneration: 'chapter' };
  assert.notEqual(writingEditableFingerprint(draft), writingEditableFingerprint(saved));
  draft.interactiveGeneration = 'single';
  assert.equal(writingEditableFingerprint(draft), writingEditableFingerprint(saved));
  assert.deepEqual(writingProjectPatch(draft, saved), {});
  assert.equal(Object.hasOwn(writingBriefPayload(saved), 'interactiveGeneration'), false);
});

test('saved interactive choices survive another brief edit, the length boundary and raw payload reload', () => {
  const saved = { ...fixture(), interactiveGeneration: 'chapter' };
  const raw = { ...writingProjectPayload(saved), id: saved.id, version: saved.version, sections: saved.sections };
  assert.equal(writingBriefPayload(raw).interactiveGeneration, 'chapter');
  assert.deepEqual(writingProjectPatch(raw, saved), {});
  const long = { ...saved, targetWords: 10000 };
  assert.equal(writingProjectPatch(long, saved).brief.interactiveGeneration, 'chapter');
  assert.equal(writingBriefPayload({ ...raw, purpose: '调整行文要求' }).interactiveGeneration, 'chapter');
  const draft = { ...saved, interactiveGeneration: 'single' };
  assert.equal(writingProjectPatch(draft, saved).brief.interactiveGeneration, 'single');
  draft.interactiveGeneration = 'chapter';
  assert.deepEqual(writingProjectPatch(draft, saved), {});
});

test('malformed interactive preferences stay distinct instead of becoming a valid default', () => {
  const saved = fixture();
  for (const interactiveGeneration of [null, false, 0, '', 'invalid', []]) {
    const draft = { ...saved, interactiveGeneration };
    assert.notEqual(writingEditableFingerprint(draft), writingEditableFingerprint(saved));
    assert.deepEqual(writingProjectPatch(draft, saved).brief.interactiveGeneration, interactiveGeneration);
  }
});

test('new template and style choices persist and revert without migrating historical briefs', () => {
  const saved = fixture(), before = JSON.stringify(saved);
  const historical = writingBriefPayload(saved);
  assert.equal(Object.hasOwn(historical, 'structureTemplate'), false);
  assert.equal(Object.hasOwn(historical, 'writingStyle'), false);
  const draft = { ...saved, ...structureTemplatePatch('research-report'), ...writingStylePatch('technical') };
  const payload = writingProjectPatch(draft, saved).brief;
  assert.equal(payload.structureTemplate, 'research-report');
  assert.equal(payload.template, '调研报告');
  assert.deepEqual(payload.templateSections, ['当前态势', '存在问题隐患', '对策建议']);
  assert.equal(payload.writingStyle, 'technical');
  assert.equal(payload.style, '技术论述');
  const restored = restoreLegacyWritingPreset(restoreLegacyWritingPreset(draft, saved, 'structure'), saved, 'style');
  assert.deepEqual(writingProjectPatch(restored, saved), {});
  assert.equal(Object.hasOwn(writingBriefPayload(restored), 'structureTemplate'), false);
  assert.equal(Object.hasOwn(writingBriefPayload(restored), 'writingStyle'), false);

  const raw = { ...writingProjectPayload(draft), id: saved.id, version: saved.version, sections: saved.sections };
  assert.deepEqual(writingProjectPatch(raw, draft), {});
  assert.notEqual(writingEditableFingerprint(draft), writingEditableFingerprint(saved));
  assert.deepEqual(writingProjectPatch(saved, saved), {});
  assert.equal(JSON.stringify(saved), before);
});

test('changing templates clears a sample-derived structure while retaining the independent writing style', () => {
  const original = { ...fixture(), ...writingStylePatch('news'), sampleReferenceText: '旧范文结构', sampleStructureApplied: true };
  const draft = { ...original, ...structureTemplatePatch('opinion') };
  const payload = writingBriefPayload(draft);
  assert.equal(payload.structureTemplate, 'opinion');
  assert.equal(payload.sampleReferenceText, '');
  assert.equal(payload.sampleStructureApplied, false);
  assert.equal(payload.writingStyle, 'news');
});

test('new research reports offer three reference structures and default to decision support', () => {
  const options = structureTemplateOptions();
  const reports = options.filter(item => item.id.startsWith('research-report'));
  assert.equal(DEFAULT_STRUCTURE_TEMPLATE, 'research-report-decision');
  assert.equal(reports.length, 3);
  assert.deepEqual(new Set(reports.map(item => item.id)), new Set([
    'research-report-problem', 'research-report-experience', 'research-report-decision',
  ]));
  assert.deepEqual(structureTemplatePatch(DEFAULT_STRUCTURE_TEMPLATE), {
    structureTemplate: 'research-report-decision', template: '调研报告（决策参考型）',
    templateSections: ['现状与趋势', '风险研判', '意见建议（分近期、中期）'],
    sampleReferenceText: '', sampleStructureApplied: false,
  });
  assert.deepEqual(reports.find(item => item.id === 'research-report-problem').sections,
    ['调研背景与方法', '基本情况', '存在问题', '原因分析', '对策建议']);
  assert.deepEqual(reports.find(item => item.id === 'research-report-experience').sections,
    ['主要做法', '取得成效', '经验启示', '推广建议']);
  assert(reports.every(item => item.background === '引言（开篇，不算独立章节）'));
  assert.equal(options.some(item => item.id === 'research-report'), false);
  assert.equal(structureTemplates.find(item => item.id === 'research-report').legacy, true);
});

test('all three report variants round-trip independently without changing saved chapters or style', () => {
  const saved = { ...fixture(), ...writingStylePatch('official') }, before = JSON.stringify(saved);
  for (const id of ['research-report-problem', 'research-report-experience', 'research-report-decision']) {
    const draft = { ...saved, ...structureTemplatePatch(id) };
    const payload = writingProjectPayload(draft);
    assert.equal(payload.brief.structureTemplate, id);
    assert.equal(payload.brief.writingStyle, 'official');
    const raw = { ...payload, id: saved.id, version: saved.version, sections: saved.sections };
    assert.deepEqual(writingProjectPatch(raw, draft), {});
    assert.equal(writingProjectPatch(draft, saved).brief.structureTemplate, id);
    assert.deepEqual(draft.sections, saved.sections);
  }
  assert.equal(JSON.stringify(saved), before);
});

test('retired research-report selections remain exact and can be restored after a variant change', () => {
  const saved = { ...fixture(), structureTemplate: 'research-report', template: '调研报告',
    templateSections: ['作者保存的旧条目', '另一条手工安排'], sampleReferenceText: '旧的参考说明', sampleStructureApplied: true };
  const before = JSON.stringify(saved);
  const draft = { ...saved, ...structureTemplatePatch(DEFAULT_STRUCTURE_TEMPLATE) };
  assert(structureTemplateOptions(draft, saved).some(item => item.id === 'research-report'));
  assert.equal(structureTemplateOptions(draft).some(item => item.id === 'research-report'), false);
  const restored = restoreLegacyWritingPreset(draft, saved, 'structure');
  assert.equal(restored.structureTemplate, 'research-report');
  assert.equal(restored.template, '调研报告');
  assert.deepEqual(restored.templateSections, saved.templateSections);
  assert.deepEqual(writingProjectPatch(restored, saved), {});
  const raw = { ...writingProjectPayload(saved), id: saved.id, version: saved.version, sections: saved.sections };
  assert.deepEqual(writingProjectPatch(raw, saved), {});
  assert.equal(JSON.stringify(saved), before);
});
