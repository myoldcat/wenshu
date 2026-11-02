import test from 'node:test';
import assert from 'node:assert/strict';
import { wikiRoot, graphScopeKey, inheritTopicScopeCaches, boundedSourceSelection, resolutionEntryIds, resolutionBody, resolutionJudgment, resolutionPresentation, afterKnowledgeIdentityChange } from '../src/wiki-unified-state.js';

test('canonical and source routes never share an identity; server view IDs share only identical topic canvases', () => {
  assert.equal(wikiRoot(), '/wiki/knowledge');
  assert.equal(wikiRoot('source'), '/wiki');
  const parent = { id: 'parent', viewId: 'full-members-a' }, child = { id: 'child', viewId: 'full-members-a' };
  assert.equal(graphScopeKey('unified', parent), graphScopeKey('unified', child));
  assert.notEqual(graphScopeKey('source', parent), graphScopeKey('unified', parent));
  assert.notEqual(graphScopeKey('unified', { ...child, viewId: 'full-members-b' }), graphScopeKey('unified', parent));
  assert.notEqual(graphScopeKey('source', null, { entryId: 'a' }), graphScopeKey('unified', null, { entryId: 'a' }));
});

test('explicit source selection begins empty, caps at ten and remains reversible', () => {
  let selected = [];
  for (let index = 0; index < 12; index += 1) selected = boundedSourceSelection(selected, `s${index}`);
  assert.equal(selected.length, 10);
  assert(!selected.includes('s10'));
  selected = boundedSourceSelection(selected, 's0');
  assert.equal(selected.length, 9);
  assert.equal(boundedSourceSelection(selected, 's11').length, 10);
});

test('decisions require a frozen snapshot and at least two registered source entries', () => {
  const proposal = { id: 'p', label: '规范概念', entries: [{ sourceEntryId: 'a' }, { id: 'b' }, { id: 'b' }] };
  assert.deepEqual(resolutionEntryIds(proposal), ['a', 'b']);
  assert.equal(resolutionBody(proposal, ''), null);
  assert.equal(resolutionBody({ entryIds: ['a'] }, 'v1'), null);
  assert.deepEqual(resolutionBody(proposal, 'v2', { label: ' 人工名称 ', reason: ' 人工判断 ', judgment: 'distinct' }), {
    proposalId: 'p', entryIds: ['a', 'b'], expectedSnapshotId: 'v2', label: '人工名称', reason: '人工判断', judgment: 'distinct',
  });
  assert.equal(resolutionJudgment({ judgment: 'same_as' }), 'same');
  assert(!('proposalId' in resolutionBody({ ...proposal, id: `lexical:${'a'.repeat(200)}` }, 'v2')), '长词法候选标识不应超出API可选字段限制');
});

test('broader, narrower, related and distinct judgments retain separate concepts instead of offering a direct identity merge', () => {
  for (const judgment of ['broader_than', 'narrower_than', 'related', 'distinct']) {
    const presentation = resolutionPresentation({ judgment });
    assert.equal(presentation.mergeAllowed, false, judgment);
    assert.notEqual(presentation.label, '待核对', judgment);
    assert(presentation.explanation.includes('保持独立'), judgment);
  }
  assert.equal(resolutionPresentation({ judgment: 'same_as' }).mergeAllowed, true);
  assert.equal(resolutionPresentation({ judgment: 'uncertain' }).mergeAllowed, true, '无模型的词法候选仍允许人工核对后归并');
  assert.equal(resolutionPresentation({ judgment: 'unrecognized' }).mergeAllowed, false);
});

test('identity changes invalidate current or cached canonical focus while retaining topic, query and independent canvas identity', () => {
  const saved = { topicId: 'topic-ai', query: 'LLM', selection: { id: 'canonical_removed' }, version: '2', exploration: { entryId: 'canonical_removed' }, selectedPath: { id: 'old-path' } };
  const next = afterKnowledgeIdentityChange(saved);
  assert.deepEqual(next, { topicId: 'topic-ai', query: 'LLM', selection: null, version: '', exploration: null, selectedPath: null });
  assert.equal(saved.selection.id, 'canonical_removed', '保留旧缓存对象以免改变另一表示的状态');
});

test('a changed complete-member view hash inherits the same topic camera and positions without overwriting an existing shared view', () => {
  const aliases = new Map(), cameras = new Map([['old', { x: 8, y: 9, zoom: 1.2 }]]), layouts = new Map([['old', new Map([['a', { x: 1, y: 2 }]])]]);
  inheritTopicScopeCaches('unified', 'topic-a', 'old', aliases, cameras, layouts);
  inheritTopicScopeCaches('unified', 'topic-a', 'new', aliases, cameras, layouts);
  assert.deepEqual(cameras.get('new'), cameras.get('old'));
  assert.deepEqual(layouts.get('new'), layouts.get('old'));
  assert.notEqual(layouts.get('new'), layouts.get('old'));
  cameras.set('shared', { x: 33, y: 0, zoom: .8 });
  inheritTopicScopeCaches('unified', 'topic-a', 'shared', aliases, cameras, layouts);
  assert.equal(cameras.get('shared').x, 33);
  inheritTopicScopeCaches('source', 'topic-a', 'source-new', aliases, cameras, layouts);
  assert(!cameras.has('source-new'), '来源与统一表示仍需隔离相机');
});
