import assert from 'node:assert/strict';
import test from 'node:test';
import {getDocumentTags, documentArticleType} from '../src/document-tags.mjs';

const path = (...names) => ({nodes: names.map((name, index) => ({id: `${index}:${names.slice(0, index + 1).join(':')}`, name}))});

test('stored complete paths determine levels and matching flat tags are not repeated as custom', () => {
  assert.deepEqual(getDocumentTags({
    classificationPaths: [path('人工智能', '模型方法', '语言模型')],
    tags: ['人工智能', '模型方法', '语言模型', '重点资料'],
  }), [
    {label: '人工智能', kind: 'classification', level: 1, paths: ['人工智能 / 模型方法 / 语言模型']},
    {label: '模型方法', kind: 'classification', level: 2, paths: ['人工智能 / 模型方法 / 语言模型']},
    {label: '语言模型', kind: 'classification', level: 3, paths: ['人工智能 / 模型方法 / 语言模型']},
    {label: '重点资料', kind: 'custom', level: null},
  ]);
});

test('repeated same-level labels aggregate distinct paths in path order', () => {
  const first = path('人工智能', '治理', '风险评估');
  const second = path('公共治理', '治理', '风险评估');
  const chips = getDocumentTags({classificationPaths: [first, second, first]});
  assert.deepEqual(chips.map(({label, level}) => [label, level]), [
    ['人工智能', 1], ['治理', 2], ['风险评估', 3], ['公共治理', 1],
  ]);
  assert.deepEqual(chips[1].paths, ['人工智能 / 治理 / 风险评估', '公共治理 / 治理 / 风险评估']);
});

test('same label at different levels keeps both classifications for accurate titles and shading', () => {
  const chips = getDocumentTags({classificationPaths: [path('治理', '政策', '治理')], tags: ['治理']});
  assert.deepEqual(chips.filter(chip => chip.label === '治理').map(chip => chip.level), [1, 3]);
  assert.equal(chips.some(chip => chip.kind === 'custom'), false);
});

test('manual classifications use their stored levels without turning into user tags', () => {
  const chips = getDocumentTags({classificationManual: true, classificationPaths: [path('经济与金融', '资本市场', '企业融资')], tags: ['观察']});
  assert.deepEqual(chips.map(chip => chip.kind), ['classification', 'classification', 'classification', 'custom']);
});

test('legacy flat tags never imply a hierarchy, even when they resemble category names', () => {
  assert.deepEqual(getDocumentTags({classificationManual: true, tags: ['人工智能', '模型方法', '人工智能', ' 自定义 ', '', 7, null]}), [
    {label: '人工智能', kind: 'custom', level: null},
    {label: '模型方法', kind: 'custom', level: null},
    {label: '自定义', kind: 'custom', level: null},
  ]);
});

test('uncategorized placeholders remain neutral and deduplicate across stored levels', () => {
  assert.deepEqual(getDocumentTags({classificationPaths: [path('人工智能', '未归类', '未归类')], tags: ['未归类', '未分类']}), [
    {label: '人工智能', kind: 'classification', level: 1, paths: ['人工智能 / 未归类 / 未归类']},
    {label: '未归类', kind: 'uncategorized', level: null, paths: ['人工智能 / 未归类 / 未归类']},
    {label: '未分类', kind: 'uncategorized', level: null},
  ]);
});

test('incomplete or malformed paths cannot invent classification levels or consume flat tags', () => {
  const duplicateId = path('重复ID一', '重复ID二', '重复ID三');
  duplicateId.nodes[2].id = duplicateId.nodes[0].id;
  const sparsePath = path('人工智能', '模型方法', '语言模型');
  delete sparsePath.nodes[1];
  assert.deepEqual(getDocumentTags({classificationPaths: [
    null,
    {nodes: ['人工智能', '模型方法', '语言模型']},
    path('人工智能', '模型方法'),
    {nodes: [{id: 'one', name: '人工智能'}, {id: 'two', name: ' '}, {id: 'three', name: '语言模型'}]},
    duplicateId,
    sparsePath,
  ], tags: ['人工智能']}), [{label: '人工智能', kind: 'custom', level: null}]);
});

test('invalid document containers return an empty collection and input data is not mutated', () => {
  for (const value of [undefined, null, 9, '', {}, {classificationPaths: {}, tags: {}}]) {
    assert.deepEqual(getDocumentTags(value), []);
  }
  const doc = {classificationPaths: [path(' 人工智能 ', '模型方法', '语言模型')], tags: [' 自定义 ']};
  const snapshot = JSON.stringify(doc);
  const chips = getDocumentTags(doc);
  assert.equal(chips[0].label, '人工智能');
  assert.equal(JSON.stringify(doc), snapshot);
  chips[0].paths.push('外部修改');
  assert.equal(JSON.stringify(doc), snapshot);
});

test('source language is a separate read-only tag and never acquires a topic level', () => {
  const doc = { languageCode: 'ja', languageLabel: '日语', languageTag: '语言：日语', needsTranslation: true, classificationPaths: [path('人工智能', '模型方法', '语言模型')], tags: ['语言：日语', '重点资料'] };
  const snapshot = JSON.stringify(doc);
  const chips = getDocumentTags(doc);
  assert.deepEqual(chips[0], {label: '语言：日语', kind: 'language', level: null});
  assert.equal(chips.filter(tag => tag.label === '语言：日语').length, 1);
  assert.deepEqual(chips.filter(tag => tag.kind === 'classification').map(tag => tag.level), [1, 2, 3]);
  assert.equal(chips.at(-1).kind, 'custom');
  assert.equal(JSON.stringify(doc), snapshot);
});

test('Chinese and unidentified foreign articles both receive accurate language tags', () => {
  assert.deepEqual(getDocumentTags({ languageCode: 'zh', needsTranslation: false }), [{label: '语言：中文', kind: 'language', level: null}]);
  assert.deepEqual(getDocumentTags({ language: 'en', languageCode: 'und', needsTranslation: true }), [{label: '语言：外语（待识别）', kind: 'language', level: null}]);
});

test('article nature is a distinct typed label and never inferred from old kind or free-form tags', () => {
  for (const [value, label] of [['fact', '事实类'], ['opinion', '观点类'], ['technical', '技术类']]) {
    const doc = {articleType: value, articleTypeManual: true, tags: [label]};
    const snapshot = JSON.stringify(doc);
    assert.deepEqual(getDocumentTags(doc), [
      {label, kind: 'articleType', level: null, value},
      {label, kind: 'custom', level: null},
    ]);
    assert.equal(documentArticleType(doc).label, label);
    assert.equal(JSON.stringify(doc), snapshot);
  }
  for (const doc of [{articleType: null}, {articleType: '事实类'}, {kind: '观点'}, {tags: ['事实类']}]) {
    assert.equal(documentArticleType(doc), null);
    assert.equal(getDocumentTags(doc).some(tag => tag.kind === 'articleType'), false);
  }
});
