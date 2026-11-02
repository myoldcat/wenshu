import assert from 'node:assert/strict';
import test from 'node:test';
import {buildDateTree, buildTagTree} from '../src/directory-grouping.mjs';
import {filterCategoryTree} from '../src/category-tree-model.mjs';
import {categoriesForSelectionMode, categorySelectionLabel, categorySelectionScope, categorySelectionState,
  categoryTreeSelectionId, reconcileCategorySelections, toggleCategorySelection} from '../src/category-selection.mjs';

const id = (...keys) => categoryTreeSelectionId(keys);
const leaf = (value, title = value) => ({kind: 'document', id: value, key: `doc:${value}`, name: title,
  document: {id: value, title, tags: []}, children: []});
const folder = (value, name, children) => ({kind: 'category', key: `folder:${value}`, selection: `cat:${value}`, name, children});
const uniqueDocuments = values => [...new Map(values.map(value => [String(value.id), value])).values()];
function treeOf(children, mode = 'topic') {
  const index = Object.create(null);
  const visit = (node, parents = []) => {
    const pathLabel = [...parents, node.name].join(' / ');
    const nested = (node.children || []).map(child => visit(child, [...parents, node.name]));
    const documents = node.kind === 'document' ? [node.document] : uniqueDocuments(nested.flatMap(child => child.documents));
    const value = {...node, pathLabel, children: nested, documents, documentIds: documents.map(doc => String(doc.id)), count: documents.length};
    if (value.selection) {
      const previous = index[value.selection];
      const combined = uniqueDocuments([...(previous?.documents || []), ...documents]);
      index[value.selection] = {...value, documents: combined, documentIds: combined.map(doc => String(doc.id)), count: combined.length};
    }
    return value;
  };
  const nested = children.map(node => visit(node)), documents = uniqueDocuments(nested.flatMap(node => node.documents));
  return {kind: 'root', name: '全部文档', mode, children: nested, documents, documentIds: documents.map(doc => String(doc.id)), count: documents.length, selectionIndex: index};
}
function fixture() {
  return treeOf([
    folder('parent', '总类', [folder('left', '左类', [leaf('a'), leaf('b')]), folder('right', '右类', [leaf('c'), leaf('d')]), leaf('direct', '直属文章')]),
    folder('other', '另一类', [leaf('other')]),
  ]);
}
const parent = id('folder:parent'), left = id('folder:parent', 'folder:left'), right = id('folder:parent', 'folder:right');
const direct = id('folder:parent', 'doc:direct');
const a = id('folder:parent', 'folder:left', 'doc:a'), b = id('folder:parent', 'folder:left', 'doc:b');
const c = id('folder:parent', 'folder:right', 'doc:c'), d = id('folder:parent', 'folder:right', 'doc:d');
const other = id('folder:other');

test('parent selection checks every descendant occurrence but counts only its canonical parent', () => {
  const tree = fixture(), before = JSON.stringify(tree);
  const selected = toggleCategorySelection([], parent, true, tree), state = categorySelectionState(selected, tree);
  assert.deepEqual(selected, [parent]);
  for (const selection of [parent, left, right, direct, a, b, c, d]) assert(state.checked.has(selection));
  assert(!state.checked.has(other));
  assert.equal(state.count, 1);
  assert.equal(state.containsDocuments, false);
  assert.deepEqual(state.labels, ['总类']);
  assert.deepEqual(categorySelectionScope(selected, tree.documents, tree), ['a', 'b', 'c', 'd', 'direct']);
  assert.equal(JSON.stringify(tree), before);
});

test('unchecking a selected child removes its ancestors while retaining siblings and direct articles', () => {
  const tree = fixture();
  const selected = toggleCategorySelection([parent], left, true, tree), state = categorySelectionState(selected, tree);
  assert.deepEqual(selected, [right, direct]);
  assert(!state.checked.has(parent));assert(!state.checked.has(left));
  assert(state.indeterminate.has(parent));assert(state.checked.has(right));assert(state.checked.has(direct));
  assert.equal(state.count, 2);assert.equal(state.containsDocuments, true);
  assert.equal(categorySelectionLabel(selected, tree), '已选 2 项');
  assert.deepEqual(categorySelectionScope(selected, tree.documents, tree), ['c', 'd', 'direct']);
});

test('unchecking one deep article expands only its selected ancestors and preserves the rest', () => {
  const tree = fixture();
  const selected = toggleCategorySelection([parent], a, true, tree), state = categorySelectionState(selected, tree);
  assert.deepEqual(selected, [b, right, direct]);
  assert(state.indeterminate.has(parent));assert(state.indeterminate.has(left));assert(!state.checked.has(a));
  assert.deepEqual(categorySelectionScope(selected, tree.documents, tree), ['b', 'c', 'd', 'direct']);
  const restored = toggleCategorySelection(selected, a, true, tree);
  assert.deepEqual(restored, [parent]);assert.equal(categorySelectionState(restored, tree).count, 1);
});

test('all children coalesce bottom-up including direct document leaves, never the synthetic root', () => {
  const tree = fixture();let selected = [];
  for (const selection of [a, b]) selected = toggleCategorySelection(selected, selection, true, tree);
  assert.deepEqual(selected, [left]);
  for (const selection of [c, d]) selected = toggleCategorySelection(selected, selection, true, tree);
  assert.deepEqual(selected, [left, right]);
  assert(!categorySelectionState(selected, tree).checked.has(parent), 'An unselected direct article prevents accidental widening');
  selected = toggleCategorySelection(selected, direct, true, tree);assert.deepEqual(selected, [parent]);
  selected = toggleCategorySelection(selected, other, true, tree);
  assert.deepEqual(selected, [parent, other]);assert.equal(categorySelectionState(selected, tree).count, 2);
});

test('partial parents select all on click and single selection still replaces the exact clicked scope', () => {
  const tree = fixture();
  assert.deepEqual(toggleCategorySelection([a], left, true, tree), [left]);
  assert.deepEqual(toggleCategorySelection([parent], a, false, tree), [a]);
  assert.deepEqual(categorySelectionScope([a], tree.documents, tree), ['a']);
  assert.deepEqual(toggleCategorySelection([parent], '全部', true, tree), []);
});

test('canonical counts derive without mutating redundant current selections', () => {
  const tree = fixture(), selected = [left, a, right, direct], before = [...selected];
  const state = categorySelectionState(selected, tree);
  assert.deepEqual(state.selections, [parent]);assert.equal(state.count, 1);assert.deepEqual(selected, before);
});

test('click order survives tree-order differences, upward merges and leaving multiselect', () => {
  const tree = fixture();let selected = [other];
  for (const selection of [left, right, direct]) selected = toggleCategorySelection(selected, selection, true, tree);
  assert.deepEqual(selected, [other, parent]);
  assert.deepEqual(categoriesForSelectionMode(selected, false, tree), [other]);
  assert.deepEqual(categoriesForSelectionMode(selected, true, tree), [other, parent]);
  assert.deepEqual(toggleCategorySelection(selected, other, true, tree), [parent]);
});

test('direct article leaves use directory scopes without changing saved writing-material selection', () => {
  const tree = fixture();tree.documents[0].selected = true;
  const before = JSON.stringify(tree.documents), selected = toggleCategorySelection([], a, true, tree);
  assert.deepEqual(categorySelectionScope(selected, tree.documents, tree), ['a']);
  assert.equal(categorySelectionState(selected, tree).containsDocuments, true);
  assert.equal(categorySelectionLabel(selected, tree), '总类 / 左类 / a');
  assert.equal(JSON.stringify(tree.documents), before);
});

test('same category IDs and same document IDs in different paths keep independent occurrence state', () => {
  const tree = treeOf([
    folder('first', '第一类', [folder('shared', '重复分类', [leaf('same'), leaf('first-only')]), leaf('first-direct')]),
    folder('second', '第二类', [folder('shared', '重复分类', [leaf('same'), leaf('second-only')]), leaf('second-direct')]),
  ]);
  const first = id('folder:first', 'folder:shared'), second = id('folder:second', 'folder:shared');
  const state = categorySelectionState([first], tree);
  assert(state.checked.has(first));assert(!state.checked.has(second));
  assert.deepEqual(categorySelectionScope([first], tree.documents, tree), ['same', 'first-only']);
  const both = toggleCategorySelection([first], second, true, tree);
  assert.equal(categorySelectionState(both, tree).count, 2);
  assert.deepEqual(categorySelectionScope(both, tree.documents, tree), ['same', 'first-only', 'second-only']);
});

test('legacy shared category IDs retain their exact union and split correctly after a child is unchecked', () => {
  const tree = treeOf([
    folder('first', '第一类', [folder('shared', '重复分类', [leaf('same'), leaf('first-only')]), leaf('first-direct')]),
    folder('second', '第二类', [folder('shared', '重复分类', [leaf('same'), leaf('second-only')]), leaf('second-direct')]),
  ]);
  const state = categorySelectionState(['cat:shared'], tree);
  assert.deepEqual(state.selections, ['cat:shared']);assert.equal(state.count, 1);
  assert.deepEqual(categorySelectionScope(['cat:shared'], tree.documents, tree), ['same', 'first-only', 'second-only']);
  const selected = toggleCategorySelection(['cat:shared'], id('folder:first', 'folder:shared'), true, tree);
  assert.deepEqual(selected, [id('folder:second', 'folder:shared')]);
  assert.deepEqual(categorySelectionScope(selected, tree.documents, tree), ['same', 'second-only']);
});

test('legacy raw tags and hidden categories remain independent exact scopes', () => {
  const tree = fixture();tree.documents[0].tags = ['手工'];
  tree.selectionIndex['cat:hidden'] = {name: '隐藏分类', pathLabel: '总类 / 隐藏分类', documentIds: ['other'], documents: [tree.documents.at(-1)], hidden: true};
  assert.deepEqual(categorySelectionScope(['手工', right], tree.documents, tree), ['a', 'c', 'd']);
  assert.deepEqual(categorySelectionScope(['cat:hidden'], tree.documents, tree), ['other']);
  const state = categorySelectionState(['cat:hidden', right], tree);
  assert.equal(state.count, 2);assert.deepEqual(state.labels, ['总类 / 隐藏分类', '总类 / 右类']);
});

test('expired internal paths remain explicit empty scopes with friendly labels instead of falling back to flat tags', () => {
  const tree = fixture(), stale = id('folder:removed', 'doc:removed');
  tree.documents[0].tags = [stale];
  const selected = [stale];assert.equal(reconcileCategorySelections(selected, tree), selected);
  assert.deepEqual(categorySelectionScope(selected, tree.documents, tree), []);
  assert.equal(categorySelectionState(selected, tree).count, 1);
  assert.deepEqual(categorySelectionState(selected, tree).labels, ['已失效的筛选']);
  assert.equal(categorySelectionLabel(selected, tree), '已失效的筛选');
  assert.equal(categorySelectionLabel(['cat:removed'], tree), '已失效的筛选');
  const removedTree = treeOf([folder('other', '另一类', [leaf('other')])]);
  assert.deepEqual(categorySelectionScope([parent], removedTree.documents, removedTree), []);
  assert.deepEqual(reconcileCategorySelections([parent], removedTree), [parent]);
});

test('search presentation never supplies the cascade catalog or widens selected scopes', () => {
  const tree = fixture(), trimmed = filterCategoryTree(tree, '左类');
  assert.equal(trimmed.count, 2);
  const selected = toggleCategorySelection([parent], a, true, tree);
  assert.deepEqual(selected, [b, right, direct]);
  assert.deepEqual(categorySelectionScope(selected, tree.documents, tree), ['b', 'c', 'd', 'direct']);
  // If a caller accidentally supplies a trimmed catalog, absent paths fail closed.
  assert.deepEqual(categorySelectionScope([right], tree.documents, trimmed), []);
});

test('date trees cascade through year/month/day and independent article leaves', () => {
  const documents = [
    {id: 'oct-one', title: '一日', date: '2026-10-01', tags: []},
    {id: 'oct-two', title: '二日', date: '2026-10-02', tags: []},
    {id: 'sep', title: '九月', date: '2026-09-01', tags: []},
    {id: 'older', title: '往年', date: '2025-10-01', tags: []},
  ];
  const tree = buildDateTree(documents), year = id('date:year:2026');
  const october = id('date:year:2026', 'date:month:2026-10');
  const selected = toggleCategorySelection([year], october, true, tree), state = categorySelectionState(selected, tree);
  assert.equal(state.count, 1);assert(!state.checked.has(year));assert(state.indeterminate.has(year));
  assert.deepEqual(categorySelectionScope(selected, documents, tree), ['sep']);
  assert.deepEqual(toggleCategorySelection(selected, october, true, tree), [year]);
});

test('flat tag selections keep their prior OR semantics and do not coalesce group headings', () => {
  const documents = [{id: 'one', articleType: 'fact', languageCode: 'zh', tags: []}, {id: 'two', articleType: 'opinion', languageCode: 'en', tags: []}];
  const tree = buildTagTree(documents);
  const selected = toggleCategorySelection(['tag:articleType:fact'], 'tag:articleType:opinion', true, tree);
  assert.deepEqual(selected, ['tag:articleType:fact', 'tag:articleType:opinion']);
  assert.equal(categorySelectionState(selected, tree).count, 2);
  assert.deepEqual(categorySelectionScope(selected, documents, tree), ['one', 'two']);
});

test('path selection IDs encode complete arrays without delimiter or unicode collisions', () => {
  assert.notEqual(id('a/b', 'c'), id('a', 'b/c'));
  assert.notEqual(id('a', 'doc:same'), id('b', 'doc:same'));
  assert.equal(id('分类：中文', '__proto__'), id('分类：中文', '__proto__'));
});
