import assert from 'node:assert/strict';
import test from 'node:test';
import {buildCategoryTree, filterCategoryTree} from '../src/category-tree-model.mjs';
import {categoriesForSelectionMode, categorySelectionLabel, categorySelectionScope, normalizeCategorySelections, reconcileCategorySelections, toggleCategorySelection} from '../src/category-selection.mjs';

const path = (...names) => ({nodes: names.map((name, index) => ({id: names.slice(0, index + 1).join(':'), name}))});
const doc = (id, paths, tags = []) => ({id, title: `文章 ${id}`, classificationPaths: paths, tags});
const articles = () => [
  doc('shared', [path('人工智能', '模型', '语言'), path('经济与金融', '产业', '算力')], ['人工智能', '经济与金融', '观察']),
  doc('ai', [path('人工智能', '模型', '语言')], ['人工智能']),
  doc('finance', [path('经济与金融', '产业', '算力')], ['经济与金融']),
  doc('legacy', [], ['观察']),
];

test('single-select replaces its scope; multi-select toggles scopes and all clears both modes', () => {
  assert.deepEqual(toggleCategorySelection(['cat:人工智能'], 'cat:经济与金融'), ['cat:经济与金融']);
  assert.deepEqual(toggleCategorySelection(['cat:人工智能'], 'cat:经济与金融', true), ['cat:人工智能', 'cat:经济与金融']);
  assert.deepEqual(toggleCategorySelection(['cat:人工智能', 'cat:经济与金融'], 'cat:人工智能', true), ['cat:经济与金融']);
  assert.deepEqual(toggleCategorySelection(['cat:人工智能'], '全部', true), []);
  assert.deepEqual(toggleCategorySelection(['cat:人工智能'], '全部'), []);
  assert.deepEqual(normalizeCategorySelections(['全部', '观察', '观察', null]), ['观察']);
});

test('multi-category scope is a union, deduplicating shared paths and duplicate documents', () => {
  const documents = articles();
  const tree = buildCategoryTree(documents);
  assert.deepEqual(categorySelectionScope(['cat:人工智能', 'cat:经济与金融'], [...documents, documents[0]], tree), ['shared', 'ai', 'finance']);
  assert.deepEqual(categorySelectionScope(['cat:人工智能', 'cat:人工智能:模型:语言'], documents, tree), ['shared', 'ai']);
});

test('legacy tag filters and categories combine without inventing hierarchical membership', () => {
  const documents = articles();
  const tree = buildCategoryTree(documents);
  assert.deepEqual(categorySelectionScope(['观察'], documents, tree), ['shared', 'legacy']);
  assert.deepEqual(categorySelectionScope(['观察', 'cat:人工智能'], documents, tree), ['shared', 'ai', 'legacy']);
  assert.equal(categorySelectionLabel(['cat:人工智能'], tree), '人工智能');
  assert.equal(categorySelectionLabel(['cat:人工智能', '观察'], tree), '已选 2 类');
});

test('no selection is unscoped, but an explicit category with no members remains an empty scope', () => {
  const documents = articles();
  const tree = buildCategoryTree(documents);
  assert.equal(categorySelectionScope([], documents, tree), null);
  assert.deepEqual(categorySelectionScope(['cat:removed'], documents, tree), []);
  assert.deepEqual(categorySelectionScope(['无匹配标签'], documents, tree), []);
});

test('closing multi-select retains the first selected scope with no hidden selections', () => {
  const selected = ['cat:经济与金融', 'cat:人工智能'];
  assert.deepEqual(categoriesForSelectionMode(selected, false), ['cat:经济与金融']);
  assert.deepEqual(categoriesForSelectionMode(selected, true), selected);
  assert.deepEqual(categoriesForSelectionMode([], false), []);
  assert.deepEqual(selected, ['cat:经济与金融', 'cat:人工智能']);
});

test('disappearing virtual unclassified buckets clear independently of surviving real categories', () => {
  const documents = articles().filter(article => article.id !== 'legacy');
  const tree = buildCategoryTree(documents);
  const current = ['uncat:root', 'cat:人工智能'];
  assert.deepEqual(reconcileCategorySelections(current, tree), ['cat:人工智能']);
  assert.deepEqual(reconcileCategorySelections(['uncat:root'], tree), []);
  assert.deepEqual(reconcileCategorySelections(['cat:removed'], tree), ['cat:removed']);
  const valid = ['cat:人工智能'];
  assert.equal(reconcileCategorySelections(valid, tree), valid);
});

test('a singleton root unclassified filter survives alongside real topics with exact scope', () => {
  const documents = articles();
  const before = JSON.stringify(documents);
  const tree = buildCategoryTree(documents);
  assert.deepEqual(reconcileCategorySelections(['uncat:root'], tree), ['uncat:root']);
  assert.deepEqual(categorySelectionScope(['uncat:root'], documents, tree), ['legacy']);
  assert.deepEqual(categorySelectionScope(['uncat:root', 'cat:人工智能'], documents, tree), ['shared', 'ai', 'legacy']);
  assert.equal(JSON.stringify(documents), before);
});

test('restored singleton categories preserve exact multi-path union scope after directory search', () => {
  const documents = [
    doc('one', [path('人工智能', '模型', '语言')]),
    doc('two', [path('人工智能', '模型', '语言')]),
    doc('shared', [path('人工智能', '模型', '视觉'), path('人工智能', '模型', '语音')]),
  ];
  const tree = buildCategoryTree(documents);
  const categories = ['cat:人工智能:模型:视觉', 'cat:人工智能:模型:语音'];
  assert.deepEqual(categorySelectionScope(categories, documents, tree), ['shared']);
  const trimmed = filterCategoryTree(tree, '文章 one');
  assert.deepEqual(categorySelectionScope(categories, documents, trimmed), ['shared']);
  assert.deepEqual(reconcileCategorySelections(categories, trimmed), categories);
});

test('directory search only trims presentation and never narrows selected scope', () => {
  const documents = articles();
  const tree = buildCategoryTree(documents);
  const trimmed = filterCategoryTree(tree, '文章 ai');
  assert.equal(trimmed.count, 1);
  assert.deepEqual(categorySelectionScope(['cat:人工智能'], documents, trimmed), ['shared', 'ai']);
  assert.deepEqual(categorySelectionScope(['cat:人工智能', 'cat:经济与金融'], documents, tree), ['shared', 'ai', 'finance']);
});

test('typed tag group IDs never fall through to same-named free-form tags', () => {
  const documents = [{id: 'one', tags: ['tag:articleType:fact', 'tag:custom:missing']}];
  const tree = buildCategoryTree(documents);
  assert.deepEqual(categorySelectionScope(['tag:articleType:fact'], documents, tree), []);
  assert.deepEqual(categorySelectionScope(['tag:custom:missing'], documents, tree), []);
  assert.deepEqual(reconcileCategorySelections(['tag:articleType:fact'], tree), []);
});
