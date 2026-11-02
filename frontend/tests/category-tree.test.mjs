import assert from 'node:assert/strict';
import test from 'node:test';
import {buildCategoryTree, categoryLabel, docMatchesCategory, filterCategoryTree, findCategoryNode, reconcileCategorySelection} from '../src/category-tree-model.mjs';

const path = (...names) => ({primary: true, nodes: names.map((name, index) => ({id: names.slice(0, index + 1).join(':'), name}))});
const doc = (id, paths, title = `文章 ${id}`) => ({id, title, classificationPaths: paths, tags: []});
const shape = node => node.kind === 'document' ? node.id : [node.name, node.children.map(shape)];
const siblings = () => [
  doc('one', [path('人工智能', '模型方法', '语言模型')]),
  doc('two', [path('人工智能', '模型方法', '语言模型')]),
  doc('three', [path('人工智能', '模型方法', '视觉模型')]),
];
const assertNoMixedSiblings = (node, requirePopulatedPeer = true) => {
  const children = node.children || [];
  assert.ok(!children.some(child => child.kind === 'document') || children.every(child => child.kind === 'document'),
    `${node.pathLabel || node.name} must not mix documents and directories`);
  for (const child of children) {
    if (requirePopulatedPeer && child.kind === 'category' && child.path.length > 1 && child.count === 1) {
      assert.ok(children.some(peer => peer !== child && peer.kind !== 'document' && peer.count > 1),
        `${child.pathLabel} can only be restored beside a directory with multiple distinct articles`);
    }
    assertNoMixedSiblings(child, requirePopulatedPeer);
  }
};

test('empty catalog hides unused categories and an unclassified document remains reachable', () => {
  assert.deepEqual(buildCategoryTree([]).children, []);
  const article = {id: 'one', title: '未加工资料', tags: ['旧自由标签']};
  const tree = buildCategoryTree([article]);
  assert.deepEqual(shape(tree), ['全部文档', ['one']]);
  assert.equal(docMatchesCategory(article, 'uncat:root', tree), true);
  assert.equal(docMatchesCategory(article, '旧自由标签', tree), true);
  assert.equal(docMatchesCategory(article, '未分类', tree), false);
  assert.equal(docMatchesCategory({...article, tags: []}, '未分类', tree), true);
  assert.equal(docMatchesCategory({...article, tags: ['未分类']}, '未分类', tree), true);
  assert.equal(docMatchesCategory({id: 'untagged'}, '未分类', tree), true);
  assert.equal(findCategoryNode(tree, 'uncat:root'), null);
});

test('lower category chains compact while original first-level anchors and stored paths stay intact', () => {
  const articles = siblings().slice(0, 2);
  const before = JSON.stringify(articles);
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', [['语言模型', ['one', 'two']]]]]]);
  assert.equal(tree.children[0].children[0].pathLabel, '人工智能 / 模型方法 / 语言模型');
  assert.equal(JSON.stringify(articles), before);
  assert.equal(findCategoryNode(tree, 'cat:人工智能').hidden, undefined);
  assert.equal(findCategoryNode(tree, 'cat:人工智能:模型方法').hidden, true);
  assert.equal(categoryLabel('cat:人工智能', tree), '人工智能');
});

test('a single article recursively dissolves every lower category and retains its original first-level topic', () => {
  const tree = buildCategoryTree(siblings().slice(0, 1));
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', ['one']]]]);
  assert.equal(tree.children[0].pathLabel, '人工智能');
  assert.equal(findCategoryNode(tree, 'cat:人工智能:模型方法').hidden, true);
  assert.equal(findCategoryNode(tree, 'cat:人工智能:模型方法:语言模型').hidden, true);
  assert.equal(tree.selectionIndex['uncat:人工智能:模型方法'], undefined);
  assertNoMixedSiblings(tree);
});

test('singleton article categories merge into one bucket before the sole-bucket exception runs', () => {
  const tree = buildCategoryTree([siblings()[0], siblings()[2]]);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', [['模型方法', ['one', 'three']]]]]]);
  assert.equal(tree.children[0].children[0].unclassifiedExpanded, true);
  assertNoMixedSiblings(tree);
});

test('a single loose article restores its original category beside retained directories', () => {
  const articles = [...siblings(), doc('four', [path('经济与金融', '金融市场', '股票投资')])];
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [
    ['人工智能', [['模型方法', [['语言模型', ['one', 'two']], ['视觉模型', ['three']]]]]],
    ['经济与金融', ['four']],
  ]]);
  assert.equal(findCategoryNode(tree, 'uncat:人工智能:模型方法'), null);
  const restored = findCategoryNode(tree, 'cat:人工智能:模型方法:视觉模型');
  assert.equal(restored.hidden, undefined);
  assert.equal(restored.pathLabel, '人工智能 / 模型方法 / 视觉模型');
  assert.deepEqual(restored.documentIds, ['three']);
  for (const article of articles) assert.equal(docMatchesCategory(article, restored.selection, tree), article.id === 'three');
  assert.equal(docMatchesCategory(articles[3], 'cat:人工智能', tree), false);
  assertNoMixedSiblings(tree);
});

test('multiple populated branches stay under their shared parent and same names in different branches stay separate', () => {
  const articles = [
    doc('a1', [path('科学与技术', '工程', '应用')]), doc('a2', [path('科学与技术', '工程', '应用')]),
    doc('b1', [path('科学与技术', '物理', '应用')]), doc('b2', [path('科学与技术', '物理', '应用')]),
  ];
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [['科学与技术', [['应用', ['a1', 'a2']], ['应用', ['b1', 'b2']]]]]]);
  assert.notEqual(tree.children[0].children[0].selection, tree.children[0].children[1].selection);
  assert.deepEqual(findCategoryNode(tree, 'cat:科学与技术:工程:应用').documentIds, ['a1', 'a2']);
});

test('multi-path membership and duplicate input documents count once at every common ancestor', () => {
  const article = doc('one', [path('人工智能', '模型方法', '语言模型'), path('人工智能', '模型方法', '视觉模型')]);
  const articles = [article, article, doc('two', [path('人工智能', '模型方法', '语言模型')]), doc('three', [path('人工智能', '模型方法', '视觉模型')])];
  const tree = buildCategoryTree(articles);
  assert.equal(tree.count, 3);
  assert.equal(tree.children[0].count, 3);
  assert.deepEqual(tree.children[0].children[0].children.map(child => child.count), [2, 2]);
  assert.deepEqual(findCategoryNode(tree, 'cat:人工智能').documentIds.sort(), ['one', 'three', 'two']);
});

test('duplicate paths and singleton multi-path leaves never duplicate an article in merged unclassified scope', () => {
  const first = path('人工智能', '模型方法', '语言模型');
  const article = doc('one', [first, first, path('人工智能', '模型方法', '视觉模型')]);
  const tree = buildCategoryTree([article]);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', ['one']]]]);
  assert.equal(tree.count, 1);
  assertNoMixedSiblings(tree);
});

test('directory search trims the already-compacted tree and does not change classification or membership', () => {
  const tree = buildCategoryTree(siblings());
  const before = JSON.stringify(tree);
  const filtered = filterCategoryTree(tree, '文章 one');
  assert.deepEqual(shape(filtered), ['全部文档', [['人工智能', [['模型方法', [['语言模型', ['one']]]]]]]]);
  assert.equal(filtered.count, 1);
  assert.equal(findCategoryNode(filtered, 'cat:人工智能:模型方法:语言模型').count, 2);
  assert.equal(JSON.stringify(tree), before);
  assert.equal(filterCategoryTree(tree, ''), tree);
  assert.equal(filterCategoryTree(tree, '人工智能').count, 3);
  assert.equal(filterCategoryTree(tree, '不存在').count, 0);
  const restored = filterCategoryTree(tree, '文章 three');
  assert.deepEqual(shape(restored), ['全部文档', [['人工智能', [['模型方法', [['视觉模型', ['three']]]]]]]]);
  assert.equal(findCategoryNode(restored, 'cat:人工智能:模型方法').count, 3);
  assertNoMixedSiblings(restored, false);
});

test('singleton category siblings cannot support one another without a populated directory', () => {
  const articles = [
    doc('one', [path('人工智能', '模型方法', '语言模型')]),
    doc('two', [path('人工智能', 'AI治理', '数据治理')]),
  ];
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', ['one', 'two']]]]);
  for (const selection of ['cat:人工智能:模型方法:语言模型', 'cat:人工智能:AI治理:数据治理']) {
    assert.equal(findCategoryNode(tree, selection).hidden, true);
  }
  assertNoMixedSiblings(tree);
});

test('duplicate documents and same-article paths never qualify as a populated directory peer', () => {
  const firstPath = path('人工智能', '模型方法', '语言模型');
  const repeated = doc('one', [firstPath, firstPath, path('人工智能', '模型方法', '视觉模型')]);
  const articles = [repeated, repeated, doc('two', [path('人工智能', 'AI治理', '数据治理')])];
  const before = JSON.stringify(articles);
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', ['one', 'two']]]]);
  assert.equal(tree.count, 2);
  for (const selection of ['cat:人工智能:模型方法:语言模型', 'cat:人工智能:模型方法:视觉模型', 'cat:人工智能:AI治理:数据治理']) {
    assert.equal(findCategoryNode(tree, selection).hidden, true);
  }
  assert.equal(JSON.stringify(articles), before);
  assertNoMixedSiblings(tree);
});

test('same-article singleton branches remain compact when no multiple-article peer exists', () => {
  const article = doc('one', [path('人工智能', '模型方法', '语言模型'), path('人工智能', 'AI治理', '数据治理')]);
  const tree = buildCategoryTree([article]);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', ['one']]]]);
  assert.equal(tree.count, 1);
  assertNoMixedSiblings(tree);
});

test('a restored singleton category dissolves again when its populated peer shrinks to one article', () => {
  const articles = siblings();
  const populated = buildCategoryTree(articles);
  assert.equal(findCategoryNode(populated, 'cat:人工智能:模型方法:视觉模型').hidden, undefined);
  assertNoMixedSiblings(populated);
  const shrunk = buildCategoryTree(articles.filter(article => article.id !== 'two'));
  assert.deepEqual(shape(shrunk), ['全部文档', [['人工智能', [['模型方法', ['one', 'three']]]]]]);
  assert.equal(findCategoryNode(shrunk, 'cat:人工智能:模型方法:视觉模型').hidden, true);
  assert.equal(findCategoryNode(shrunk, 'cat:人工智能:模型方法:语言模型').hidden, true);
  assertNoMixedSiblings(shrunk);
});

test('a singleton branch restores its deepest original category after a chain compacts', () => {
  const articles = [...siblings().slice(0, 2), doc('governance', [path('人工智能', 'AI治理', '数据治理')])];
  const before = JSON.stringify(articles);
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', [
    ['语言模型', ['one', 'two']], ['数据治理', ['governance']],
  ]]]]);
  const restored = tree.children[0].children[1];
  assert.equal(restored.key, `cat:${articles[2].classificationPaths[0].nodes.map(node => encodeURIComponent(node.id)).join('/')}`);
  assert.equal(restored.selection, 'cat:人工智能:AI治理:数据治理');
  assert.equal(restored.pathLabel, '人工智能 / AI治理 / 数据治理');
  assert.deepEqual(restored.path.map(node => node.name), ['人工智能', 'AI治理', '数据治理']);
  assert.deepEqual(findCategoryNode(tree, 'cat:人工智能:AI治理').documentIds, ['governance']);
  assert.equal(JSON.stringify(articles), before);
  assertNoMixedSiblings(tree);
});

test('one loose article with multiple original paths restores every occurrence and counts once', () => {
  const visual = path('人工智能', '模型方法', '视觉模型');
  const articles = [...siblings().slice(0, 2), doc('shared', [visual, visual, path('人工智能', '模型方法', '语音模型')])];
  const before = JSON.stringify(articles);
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', [['模型方法', [
    ['语言模型', ['one', 'two']], ['视觉模型', ['shared']], ['语音模型', ['shared']],
  ]]]]]]);
  assert.equal(tree.count, 3);
  assert.equal(tree.children[0].count, 3);
  assert.equal(tree.children[0].children[0].count, 3);
  for (const name of ['视觉模型', '语音模型']) {
    const restored = findCategoryNode(tree, `cat:人工智能:模型方法:${name}`);
    assert.equal(restored.hidden, undefined);
    assert.deepEqual(restored.documentIds, ['shared']);
  }
  assert.equal(JSON.stringify(articles), before);
  assertNoMixedSiblings(tree);
});

test('a root singleton with no valid classification remains in an unclassified folder beside topics', () => {
  for (const paths of [[], [{nodes: [{id: 'invalid', name: '不可推测的分类'}]}]]) {
    const articles = [...siblings().slice(0, 2), doc('unclassified', paths)];
    const before = JSON.stringify(articles);
    const tree = buildCategoryTree(articles);
    assert.deepEqual(shape(tree), ['全部文档', [
      ['人工智能', [['语言模型', ['one', 'two']]]], ['未归类', ['unclassified']],
    ]]);
    assert.deepEqual(findCategoryNode(tree, 'uncat:root').documentIds, ['unclassified']);
    assert.equal(JSON.stringify(articles), before);
    assertNoMixedSiblings(tree);
  }
});

test('a stored singleton unclassified leaf stays a folder when its parent has other directories', () => {
  const articles = [...siblings().slice(0, 2), doc('unclassified', [path('人工智能', '模型方法', '未归类')])];
  const before = JSON.stringify(articles);
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', [['模型方法', [
    ['语言模型', ['one', 'two']], ['未归类', ['unclassified']],
  ]]]]]]);
  assert.deepEqual(findCategoryNode(tree, 'uncat:人工智能:模型方法').documentIds, ['unclassified']);
  assert.equal(findCategoryNode(tree, 'cat:人工智能:模型方法:未归类').hidden, true);
  assert.equal(JSON.stringify(articles), before);
  assertNoMixedSiblings(tree);
});

test('incomplete or malformed paths do not create partial empty branches', () => {
  const articles = [
    doc('one', [{nodes: [{id: 'a', name: 'A'}]}]),
    doc('two', [{nodes: [{id: 'a', name: 'A'}, {id: 'a', name: 'B'}, {id: 'c', name: 'C'}]}]),
    doc('three', [{nodes: [{id: 'a', name: 'A'}, {id: 'b', name: ''}, {id: 'c', name: 'C'}]}]),
  ];
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [['未归类', ['one', 'two', 'three']]]]);
  assert.equal(findCategoryNode(tree, 'cat:a'), null);
  assert.equal(docMatchesCategory(articles[0], '全部', tree), true);
});

test('stored unclassified levels use the same exception and keep their nearest real topic', () => {
  const articles = [doc('one', [path('人工智能', '未归类', '未归类')])];
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', ['one']]]]);
  assertNoMixedSiblings(tree);
  assert.deepEqual(articles[0].classificationPaths[0].nodes.map(node => node.name), ['人工智能', '未归类', '未归类']);
});

test('stored unclassified leaves and compacted singleton leaves merge without losing siblings', () => {
  const articles = [...siblings(), doc('four', [path('人工智能', '模型方法', '未归类')])];
  const tree = buildCategoryTree(articles);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', [['模型方法', [
    ['语言模型', ['one', 'two']], ['未归类', ['three', 'four']],
  ]]]]]]);
  assert.deepEqual(findCategoryNode(tree, 'uncat:人工智能:模型方法').documentIds, ['three', 'four']);
  assert.equal(findCategoryNode(tree, 'cat:人工智能:模型方法:未归类').hidden, true);
  assertNoMixedSiblings(tree);
});

test('several paths for one article cannot preserve singleton lower topics across branches', () => {
  const article = doc('one', [path('人工智能', '模型方法', '语言模型'), path('人工智能', 'AI治理', '数据治理'), path('网络安全', '数据安全', '访问控制')]);
  const before = JSON.stringify(article);
  const tree = buildCategoryTree([article]);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', ['one']], ['网络安全', ['one']]]]);
  assert.equal(tree.count, 1);
  assert.equal(findCategoryNode(tree, 'cat:人工智能:AI治理:数据治理').count, 1);
  assert.equal(findCategoryNode(tree, 'cat:人工智能:AI治理:数据治理').hidden, true);
  assert.equal(JSON.stringify(article), before);
  assertNoMixedSiblings(tree);
});

test('removing documents recursively dissolves newly singleton folders and keeps hidden selections exact', () => {
  const articles = [
    ...siblings(),
    doc('four', [path('人工智能', '模型方法', '视觉模型')]),
    doc('five', [path('人工智能', 'AI治理', '行业政策')]),
    doc('six', [path('人工智能', 'AI治理', '行业政策')]),
  ];
  let tree = buildCategoryTree(articles);
  assertNoMixedSiblings(tree);
  assert.deepEqual(tree.children[0].children.map(node => node.name), ['模型方法', '行业政策']);
  // Both lower branches now have only one distinct article; normalization must
  // continue through their parents, including a category previously promoted.
  const remaining = articles.filter(article => ['one', 'five'].includes(article.id));
  tree = buildCategoryTree(remaining);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', ['one', 'five']]]]);
  assert.equal(tree.children[0].unclassifiedExpanded, true);
  for (const id of ['cat:人工智能:模型方法', 'cat:人工智能:模型方法:语言模型']) {
    const selected = findCategoryNode(tree, id);
    assert.equal(selected.hidden, true);
    assert.deepEqual(selected.documentIds, ['one']);
    assert.equal(docMatchesCategory(remaining[0], id, tree), true);
    assert.equal(docMatchesCategory(remaining[1], id, tree), false);
  }
  assert.deepEqual(shape(buildCategoryTree(remaining.slice(0, 1))), ['全部文档', [['人工智能', ['one']]]]);
  assertNoMixedSiblings(tree);
});

test('a stored unclassified branch holding several paths to one article also dissolves recursively', () => {
  const article = doc('one', [path('人工智能', '模型方法', '未归类'), path('人工智能', 'AI治理', '未归类')]);
  const tree = buildCategoryTree([article]);
  assert.deepEqual(shape(tree), ['全部文档', [['人工智能', ['one']]]]);
  assertNoMixedSiblings(tree);
});

test('a root virtual filter is cleared after its two articles shrink to one', () => {
  const articles = [doc('one', []), doc('two', [])];
  const before = buildCategoryTree(articles);
  assert.deepEqual(findCategoryNode(before, 'uncat:root').documentIds, ['one', 'two']);
  assert.equal(reconcileCategorySelection('uncat:root', before), 'uncat:root');
  const remaining = articles.slice(0, 1);
  const after = buildCategoryTree(remaining);
  assert.equal(findCategoryNode(after, 'uncat:root'), null);
  const selection = reconcileCategorySelection('uncat:root', after);
  assert.equal(selection, '全部');
  assert.deepEqual(remaining.filter(article => docMatchesCategory(article, selection, after)).map(article => article.id), ['one']);
  // The reconciled value selects the complete root scope, never an absent
  // virtual node that App would turn into an empty full-text sourceIds array.
  assert.deepEqual(findCategoryNode(after, selection).documentIds, ['one']);
});

test('a nested virtual filter clears when its remaining article restores a real category', () => {
  const articles = [...siblings(), doc('four', [path('人工智能', '模型方法', '未归类')])];
  const virtualSelection = 'uncat:人工智能:模型方法';
  const before = buildCategoryTree(articles);
  assert.deepEqual(findCategoryNode(before, virtualSelection).documentIds, ['three', 'four']);
  assert.equal(reconcileCategorySelection(virtualSelection, before), virtualSelection);
  const after = buildCategoryTree(articles.slice(0, -1));
  assert.equal(reconcileCategorySelection(virtualSelection, after), '全部');
  const topicSelection = 'cat:人工智能:模型方法:视觉模型';
  assert.equal(findCategoryNode(after, topicSelection).hidden, undefined);
  assert.equal(reconcileCategorySelection(topicSelection, after), topicSelection);
  assert.deepEqual(findCategoryNode(after, topicSelection).documentIds, ['three']);
  assert.equal(reconcileCategorySelection('旧自由标签', after), '旧自由标签');
  assert.equal(reconcileCategorySelection('全部', after), '全部');
});
