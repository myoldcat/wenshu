import assert from 'node:assert/strict';
import test from 'node:test';
import {buildDateTree, buildDirectoryTree, buildSourceTree, buildTagTree, directoryTagSelection, documentDirectoryDate, DIRECTORY_GROUPS} from '../src/directory-grouping.mjs';
import {filterCategoryTree} from '../src/category-tree-model.mjs';
import {categorySelectionLabel, categorySelectionScope, reconcileCategorySelections, toggleCategorySelection} from '../src/category-selection.mjs';

const doc = (id, source, date, extra = {}) => ({id, title: `文章 ${id}`, source, date, tags: [], ...extra});
const members = (tree, selection) => tree.selectionIndex[selection].documentIds;

test('source groups use existing site names, deduplicate articles and retain input document order', () => {
  const first = doc('one', '格隆汇', '2026-10-22');
  const documents = [first, doc('two', '另一来源', '2026-10-21'), first, doc('three', '格隆汇', '2026-10-20'), {title: '无 ID'}];
  const tree = buildSourceTree(documents);
  assert.equal(tree.count, 3);
  assert.deepEqual(tree.documentIds, ['one', 'two', 'three']);
  assert.deepEqual(tree.documents, [first, documents[1], documents[3]]);
  assert.deepEqual(members(tree, 'source:site:%E6%A0%BC%E9%9A%86%E6%B1%87'), ['one', 'three']);
  assert.equal(tree.children.find(node => node.name === '格隆汇').count, 2);
  assert.equal(tree.children.every(node => node.children.every(child => child.kind === 'document')), true);
});

test('missing sources stay reachable, while named sentinel values and special keys cannot collide', () => {
  const documents = [doc('blank', '', ''), doc('missing', undefined, ''), doc('named', '未知来源', ''), doc('proto', '__proto__', ''), doc('colon', 'cat:人工智能 / date:unknown', '')];
  const tree = buildSourceTree(documents);
  assert.deepEqual(members(tree, 'source:unknown'), ['blank', 'missing']);
  assert.deepEqual(members(tree, `source:site:${encodeURIComponent('未知来源')}`), ['named']);
  assert.deepEqual(members(tree, 'source:site:__proto__'), ['proto']);
  assert.equal(tree.children.at(-1).selection, 'source:unknown');
  assert.equal(new Set(tree.children.map(node => node.selection)).size, tree.children.length);
  const reordered = buildSourceTree([...documents].reverse());
  assert.deepEqual(tree.children.map(node => node.selection), reordered.children.map(node => node.selection));
  assert.deepEqual(buildSourceTree([doc('local', '本地导入', '')]).children.map(node => node.name), ['本地导入']);
});

test('date groups retain complete year-month-day hierarchy even for one article and sort newest first', () => {
  const documents = [doc('old', '站点', '2025-12-31'), doc('first', '站点', '2026-01-01'), doc('october', '站点', '2026-10-01'), doc('latest', '站点', '2026-10-22')];
  const tree = buildDateTree(documents);
  assert.deepEqual(tree.children.map(node => node.name), ['2026年', '2025年']);
  assert.deepEqual(tree.children[0].children.map(node => node.name), ['10月', '1月']);
  assert.deepEqual(tree.children[0].children[0].children.map(node => node.name), ['22日', '1日']);
  assert.equal(tree.children[1].children[0].children[0].children[0].id, 'old');
  assert.equal(tree.selectionIndex['date:year:2026'].count, 3);
  assert.equal(tree.selectionIndex['date:month:2026-10'].count, 2);
  assert.deepEqual(members(tree, 'date:day:2026-10-22'), ['latest']);
  assert.deepEqual(tree.documentIds, ['old', 'first', 'october', 'latest']);
});

test('date validation rejects rollover and guesses, accepts leap years, and matches createdAt fallback without timezone shifts', () => {
  for (const date of ['2026-02-29', '2026-02-30', '2026-13-01', '2026-00-01', '2026-01-00', '2026-04-31', '0000-01-01', '26-01-01', '2026-1-1', 'tomorrow', '2026-10-22T00:00:00Z']) {
    assert.equal(documentDirectoryDate({date}), null, date);
  }
  assert.equal(documentDirectoryDate({date: '2024-02-29'}), '2024-02-29');
  assert.equal(documentDirectoryDate({date: '2000-02-29'}), '2000-02-29');
  assert.equal(documentDirectoryDate({date: '1900-02-29'}), null);
  assert.equal(documentDirectoryDate({date: '', createdAt: '2026-10-21T23:59:00Z'}), '2026-10-21');
  assert.equal(documentDirectoryDate({date: '2026-10-20', createdAt: '2026-10-22T00:00:00Z'}), '2026-10-20');
  assert.equal(documentDirectoryDate({date: 'invalid', createdAt: '2026-10-22T00:00:00Z'}), '2026-10-22');
});

test('publication dates take precedence and retain their own calendar day across timezones', () => {
  for (const publishedAt of ['2024-02-29', '2024-02-29T00:05:00+14:00', '2024-02-29T23:59:59.123456-12:00', '2024-02-29 12:30', ' 2024-02-29T12:30Z ']) {
    const article = {publishedAt, date: '2026-10-28', createdAt: '2026-10-28T09:00:00Z'};
    const before = JSON.stringify(article);
    assert.equal(documentDirectoryDate(article), '2024-02-29', publishedAt);
    assert.equal(JSON.stringify(article), before);
  }
  assert.equal(documentDirectoryDate({publishedAt: '2030-01-01'}), '2030-01-01');
});

test('unclear publication metadata falls back to valid import dates without guessing', () => {
  for (const publishedAt of [undefined, null, '', ' ', 20261022, {}, '昨天', '2026', '2026-10', '10/11/2026', '2026-10-22 trailing text', '0000-01-01', '2026-02-29', '2026-04-31', '2026-13-01', '2026-10-22T24:00:00Z', '2026-10-22T12:60:00Z', '2026-10-22T12:00:60Z', '2026-10-22T12:00.123Z', '2026-10-22T12:00:00+99:00', '2026-10-22T12:00:00+08:60', '2026-10-22T00:00:00Zjunk']) {
    assert.equal(documentDirectoryDate({publishedAt, date: '2026-10-28'}), '2026-10-28', String(publishedAt));
    assert.equal(documentDirectoryDate({publishedAt, date: 'invalid', createdAt: '2026-10-27T23:59:59-05:00'}), '2026-10-27', String(publishedAt));
  }
  assert.equal(documentDirectoryDate({publishedAt: 'invalid', date: 'invalid', createdAt: '2026-02-30T00:00:00Z', fetchedAt: '2026-10-28'}), null);
  assert.equal(documentDirectoryDate(), null);
});

test('publication-first trees preserve complete scopes, hierarchy, deduplication and source metadata', () => {
  const documents = [
    doc('published', 'A', '2026-10-28', {publishedAt: '2024-02-29T23:59:59-12:00'}),
    doc('same-day', 'B', '2026-10-27', {publishedAt: '2024-02-29'}),
    doc('imported', 'A', '2026-10-28', {publishedAt: '2026-02-30'}),
    doc('created', 'B', '', {createdAt: '2025-12-31T23:59:59Z'}),
    doc('unknown', 'A', '', {publishedAt: '2026-10', fetchedAt: '2026-10-28'}),
  ];
  const before = JSON.stringify(documents);
  const tree = buildDateTree([...documents, documents[0]]);
  assert.deepEqual(tree.children.map(node => node.name), ['2026年', '2025年', '2024年', '未知日期']);
  assert.equal(tree.count, 5);
  assert.deepEqual(members(tree, 'date:day:2024-02-29'), ['published', 'same-day']);
  assert.deepEqual(members(tree, 'date:day:2026-10-28'), ['imported']);
  assert.deepEqual(members(tree, 'date:unknown'), ['unknown']);
  const scope = ['date:year:2024', 'date:day:2026-10-28'];
  assert.deepEqual(categorySelectionScope(scope, documents, tree), ['published', 'same-day', 'imported']);
  const trimmed = filterCategoryTree(tree, '文章 published');
  assert.equal(trimmed.count, 1);
  assert.deepEqual(categorySelectionScope(scope, documents, trimmed), ['published', 'same-day', 'imported']);
  assert.equal(JSON.stringify(documents), before);
  assert.equal(DIRECTORY_GROUPS.find(group => group.id === 'date').footer[0], '优先按发表日期，未知时按导入日期归档');
});

test('unknown and invalid dates share a reachable final group; duplicate IDs count only once', () => {
  const article = doc('good', '站点', '2026-10-22');
  const tree = buildDateTree([article, doc('bad', '站点', '2026-02-30'), doc('empty', '站点', ''), article]);
  assert.equal(tree.count, 3);
  assert.equal(tree.children.at(-1).selection, 'date:unknown');
  assert.deepEqual(members(tree, 'date:unknown'), ['bad', 'empty']);
  assert.equal(tree.selectionIndex['date:year:2026'].count, 1);
  assert.deepEqual(buildDateTree([]).children, []);
});

test('source and date multiselect scopes use ID unions and never collide with same-named flat tags', () => {
  const documents = [doc('one', 'A', '2026-10-22'), doc('two', 'B', '2026-10-21'), doc('tag-only', 'C', '2025-01-01', {tags: ['source:site:A', 'date:day:2026-10-22']})];
  const sources = buildSourceTree(documents);
  const dates = buildDateTree(documents);
  const selected = toggleCategorySelection(['source:site:A'], 'source:site:B', true);
  assert.deepEqual(categorySelectionScope(selected, documents, sources), ['one', 'two']);
  assert.deepEqual(categorySelectionScope(['date:month:2026-10', 'date:day:2026-10-22'], documents, dates), ['one', 'two']);
  assert.deepEqual(categorySelectionScope(['date:year:2025', 'date:day:2026-10-22'], documents, dates), ['one', 'tag-only']);
  assert.deepEqual(categorySelectionScope(['source:site:A'], documents, dates), []);
  assert.deepEqual(categorySelectionScope(['date:day:2026-10-22'], documents, sources), []);
  assert.deepEqual(reconcileCategorySelections(['source:site:A', 'source:site:removed'], sources), ['source:site:A']);
  assert.deepEqual(reconcileCategorySelections(['date:day:2026-10-22', 'date:unknown'], dates), ['date:day:2026-10-22']);
  assert.equal(categorySelectionLabel(['date:day:2026-10-22'], dates), '2026年 / 10月 / 22日');
});

test('directory search trims presentation while source and date scopes retain complete membership', () => {
  const documents = [doc('one', 'A', '2026-10-22'), doc('two', 'A', '2026-10-22'), doc('three', 'B', '2026-10-21')];
  for (const [tree, selection] of [[buildSourceTree(documents), 'source:site:A'], [buildDateTree(documents), 'date:day:2026-10-22']]) {
    const trimmed = filterCategoryTree(tree, '文章 one');
    assert.equal(trimmed.count, 1);
    assert.deepEqual(categorySelectionScope([selection], documents, trimmed), ['one', 'two']);
    assert.equal(tree.selectionIndex[selection].count, 2);
  }
});

test('directory dispatcher preserves existing topic compaction and mode namespaces', () => {
  const article = doc('one', 'A', '2026-10-22', {classificationPaths: [{nodes: [{id: 'topic', name: '人工智能'}, {id: 'second', name: '模型'}, {id: 'third', name: '语言模型'}]}]});
  const topic = buildDirectoryTree([article]);
  assert.equal(topic.mode, 'topic');
  assert.equal(topic.children[0].selection, 'cat:topic');
  assert.equal(topic.children[0].children[0].kind, 'document');
  assert.equal(buildDirectoryTree([article], 'source').children[0].kind, 'source');
  assert.equal(buildDirectoryTree([article], 'date').children[0].kind, 'date');
});

test('tag filters expose only article type and language without deriving filters from other metadata', () => {
  const documents = [
    doc('report', 'A', '', {articleType: 'fact', tags: ['事实类'], languageCode: 'zh', keywords: ['事实类', '共享词']}),
    doc('commentary', 'B', '', {articleType: 'opinion', tags: ['事实类'], languageCode: 'en', keywords: ['事实类']}),
    doc('implementation', 'C', '', {articleType: 'technical'}),
    doc('legacy', 'D', '', {kind: '观点', tags: ['事实类', 'tag:articleType:fact']}),
  ];
  const snapshot = JSON.stringify(documents), tree = buildTagTree([...documents, documents[0]]);
  assert.deepEqual(DIRECTORY_GROUPS.map(group => group.id), ['topic', 'tag', 'date']);
  assert.equal(tree.count, 4);
  assert.deepEqual(members(tree, 'tag:articleType:fact'), ['report']);
  assert.deepEqual(members(tree, 'tag:articleType:opinion'), ['commentary']);
  assert.deepEqual(members(tree, 'tag:articleType:technical'), ['implementation']);
  assert.deepEqual(members(tree, 'tag:articleType:unknown'), ['legacy']);
  assert.deepEqual(tree.children.map(group => [group.tagKind, group.name]), [['articleType', '文章类型'], ['language', '语言']]);
  const keyword = directoryTagSelection({kind: 'keyword', label: '事实类'});
  assert.equal(tree.selectionIndex[keyword], undefined);
  assert.equal(tree.selectionIndex[directoryTagSelection({kind: 'custom', label: '事实类'})], undefined);
  assert.equal(tree.selectionIndex['tag:group:articleType'].count, 4);
  assert.equal(tree.selectionIndex['tag:group:keyword'], undefined);
  assert.deepEqual(categorySelectionScope(['tag:articleType:fact', 'tag:articleType:opinion'], documents, tree), ['report', 'commentary']);
  assert.deepEqual(categorySelectionScope(['tag:group:articleType'], documents, tree), documents.map(item => item.id));
  assert.equal(categorySelectionLabel(['tag:articleType:fact'], tree), '文章类型 / 事实类');
  const language = directoryTagSelection({kind: 'language', label: '语言：中文'});
  assert.equal(language, `tag:language:${encodeURIComponent('语言：中文')}`);
  assert.deepEqual(members(tree, language), ['report']);
  assert.equal(categorySelectionLabel([language], tree), '语言 / 中文');
  assert.equal(JSON.stringify(documents), snapshot);
  assert.equal(buildDirectoryTree(documents, 'tag').mode, 'tag');
});

test('tag scopes retain complete membership after directory search and cannot become unscoped after removal', () => {
  const chinese = directoryTagSelection({kind: 'language', label: '语言：中文'});
  const english = directoryTagSelection({kind: 'language', label: '语言：英语'});
  const documents = [
    doc('one', 'A', '', {articleType: 'fact', languageCode: 'zh', keywords: ['共同关键词']}),
    doc('two', 'B', '', {articleType: 'fact', languageCode: 'en', keywords: ['共同关键词']}),
    doc('three', 'D', '', {articleType: 'opinion', languageCode: 'en'}),
    doc('fake', 'C', '', {tags: ['tag:articleType:fact', '共同关键词']}),
  ];
  const tree = buildTagTree(documents), trimmed = filterCategoryTree(tree, '文章 one');
  assert.equal(trimmed.count, 1);
  assert.deepEqual(categorySelectionScope(['tag:articleType:fact'], documents, trimmed), ['one', 'two']);
  assert.deepEqual(categorySelectionScope([english], documents, trimmed), ['two', 'three']);
  assert.deepEqual(categorySelectionScope([chinese, english], documents, trimmed), ['one', 'two', 'three']);
  assert.deepEqual(categorySelectionScope(['tag:articleType:fact', 'tag:articleType:opinion'], documents, trimmed), ['one', 'two', 'three']);
  assert.deepEqual(categorySelectionScope(['tag:articleType:fact', english], documents, trimmed), ['one', 'two', 'three']);
  assert.equal(filterCategoryTree(tree, '共同关键词').count, 0);
  assert.deepEqual(categorySelectionScope(['tag:articleType:removed'], documents, tree), []);
  assert.deepEqual(categorySelectionScope(['tag:keyword:removed'], documents, tree), []);
  assert.deepEqual(reconcileCategorySelections(['tag:articleType:fact', 'tag:articleType:removed'], tree), ['tag:articleType:fact']);
  assert.deepEqual(reconcileCategorySelections([english, 'tag:keyword:removed'], tree), [english]);
  assert.deepEqual(buildTagTree([]).children, []);
});

test('same-named keywords and classification tags never claim type or language filter membership', () => {
  const paths = [{nodes: [{id: 'a', name: '事实类'}, {id: 'b', name: '语言：中文'}, {id: 'c', name: '个人标记'}]}];
  const documents = [
    doc('typed', '原文站点', '', {articleType: 'fact', languageCode: 'zh'}),
    doc('keyword', '另一站点', '', {articleType: 'opinion', languageCode: 'en', keywords: ['事实类', '语言：中文', '未标注']}),
    doc('classification', '第三站点', '', {languageCode: 'en', tags: ['事实类', '语言：中文', '个人标记'], classificationPaths: paths}),
  ];
  const snapshot = JSON.stringify(documents), tree = buildTagTree(documents);
  const type = 'tag:articleType:fact';
  const language = directoryTagSelection({kind: 'language', label: '语言：中文'});
  const sameTypeKeyword = directoryTagSelection({kind: 'keyword', label: '事实类'});
  const sameLanguageKeyword = directoryTagSelection({kind: 'keyword', label: '语言：中文'});
  assert.deepEqual(members(tree, type), ['typed']);
  assert.deepEqual(members(tree, language), ['typed']);
  assert.equal(tree.selectionIndex[sameTypeKeyword], undefined);
  assert.equal(tree.selectionIndex[sameLanguageKeyword], undefined);
  assert.deepEqual(members(tree, 'tag:articleType:unknown'), ['classification']);
  assert.equal(tree.selectionIndex[directoryTagSelection({kind: 'keyword', label: '未标注'})], undefined);
  assert.notEqual(type, sameTypeKeyword);
  assert.notEqual(language, sameLanguageKeyword);
  assert.equal(tree.selectionIndex['tag:group:classification'], undefined);
  assert.equal(tree.selectionIndex['tag:group:custom'], undefined);
  assert.equal(tree.selectionIndex['tag:group:uncategorized'], undefined);
  assert.equal(tree.selectionIndex['tag:group:keyword'], undefined);
  assert.equal(Object.values(tree.selectionIndex).some(node => ['原文站点', '个人标记'].includes(node.name)), false);
  assert.equal(JSON.stringify(documents), snapshot);
});

test('keyword values including malformed metadata do not create filters or change document counts', () => {
  const documents = [
    doc('one', '站点', '', {revisionId: 'revision-current', articleType: 'fact', languageCode: 'zh', keywords: [' 关键词 ', '关键词', '', ' ', null, 7, false, {label: '不能转换'}, '__proto__', 'tag:articleType:fact', '语言：中文']}),
    doc('two', '站点', '', {articleType: 'fact', languageCode: 'en', keywords: ['关键词']}),
    doc('missing', '站点', '', {languageCode: 'zh', tags: ['关键词']}),
    doc('malformed', '站点', '', {languageCode: 'en', keywords: '关键词', tags: ['关键词']}),
  ];
  const snapshot = JSON.stringify(documents), tree = buildTagTree([...documents, documents[0]]);
  assert.deepEqual(tree.children.map(group => group.tagKind), ['articleType', 'language']);
  assert.equal(Object.keys(tree.selectionIndex).some(selection => selection.startsWith('tag:keyword:') || selection === 'tag:group:keyword'), false);
  assert.deepEqual(members(tree, 'tag:articleType:fact'), ['one', 'two']);
  assert.equal(tree.selectionIndex['tag:group:articleType'].count, 4);
  assert.equal(tree.selectionIndex['tag:group:language'].count, 4);
  assert.equal(tree.count, 4);
  const membership = catalog => Object.fromEntries(Object.entries(catalog.selectionIndex).map(([key, node]) => [key, node.documentIds]));
  const withoutKeywords = buildTagTree(documents.map(({keywords, ...metadata}) => metadata));
  assert.deepEqual(membership(tree), membership(withoutKeywords));
  assert.equal(JSON.stringify(documents), snapshot);
});

test('keyword metadata remains intact across revision changes while obsolete keyword selections disappear', () => {
  const before = doc('versioned', '站点', '', {revisionId: 'revision-old', articleType: 'technical', languageCode: 'zh', keywords: ['旧关键词']});
  const after = {...before, revisionId: 'revision-current', keywords: ['新关键词']};
  const oldSelection = directoryTagSelection({kind: 'keyword', label: '旧关键词'});
  const newSelection = directoryTagSelection({kind: 'keyword', label: '新关键词'});
  const oldTree = buildTagTree([before]), currentTree = buildTagTree([after]);
  assert.equal(oldTree.selectionIndex[oldSelection], undefined);
  assert.equal(currentTree.selectionIndex[newSelection], undefined);
  assert.equal(currentTree.selectionIndex[oldSelection], undefined);
  assert.deepEqual(members(currentTree, 'tag:articleType:technical'), ['versioned']);
  assert.deepEqual(categorySelectionScope([oldSelection], [after], currentTree), []);
  assert.deepEqual(reconcileCategorySelections([oldSelection], currentTree), []);
  assert.deepEqual(before.keywords, ['旧关键词']);
  assert.deepEqual(after.keywords, ['新关键词']);
});
