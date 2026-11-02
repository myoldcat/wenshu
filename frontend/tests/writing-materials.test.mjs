import test from 'node:test';
import assert from 'node:assert/strict';
import { addMaterialSelections, initialMaterialChoices, materialContext, materialPayload, mergeMaterialRecommendations, parseMaterialQueries } from '../src/writing-materials.js';

const item = (id, revisionId = `revision-${id}`) => ({ sourceId: id, revisionId, passages: [{ blockId: `block-${id}` }] });

test('new recommendation IDs are selected after existing sources without duplicates or input changes', () => {
  const selected = ['own-b', 'own-a', 'own-b'], candidates = ['new-b', 'own-a', 'new-a', 'new-b'];
  assert.deepEqual(addMaterialSelections(selected, candidates), ['own-b', 'own-a', 'new-b', 'new-a']);
  assert.deepEqual(selected, ['own-b', 'own-a', 'own-b']);
  assert.deepEqual(candidates, ['new-b', 'own-a', 'new-a', 'new-b']);
});

test('explicit user exclusions survive new recommendations without removing existing choices', () => {
  const excluded = new Set(['excluded', 'own']);
  assert.deepEqual(addMaterialSelections(['own'], ['excluded', 'new', 'own'], excluded), ['own', 'new']);
  assert.deepEqual([...excluded], ['excluded', 'own']);
  assert.deepEqual(addMaterialSelections([], ['excluded', 'new'], ['excluded']), ['new']);
});

test('automatic selections fill only remaining capacity in candidate order', () => {
  const selected = Array.from({ length: 99 }, (_, index) => `own-${index}`);
  assert.deepEqual(addMaterialSelections(selected, ['own-0', 'excluded', 'new-a', 'new-b'], new Set(['excluded'])), [...selected, 'new-a']);
  const full = [...selected, 'existing-last'];
  assert.deepEqual(addMaterialSelections(full, ['new-a']), full);
});

test('an already oversized saved selection is preserved rather than silently truncated', () => {
  const selected = Array.from({ length: 101 }, (_, index) => `saved-${index}`);
  assert.deepEqual(addMaterialSelections(selected, ['new-a']), selected);
});

test('recommendation keeps original whole sources and user exclusions across reruns', () => {
  const original = initialMaterialChoices({ sourceIds: ['own'], materialSelection: { passages: [] } });
  let selected = mergeMaterialRecommendations(original, [item('own'), item('a')]);
  assert.equal(selected.own.mode, 'whole');
  assert.equal(selected.a.mode, 'passages');
  selected.a.selected = false;
  const rerun = mergeMaterialRecommendations(selected, [item('a'), item('b')]);
  assert.equal(rerun.a.selected, false);
  assert.equal(rerun.b.selected, true);
  assert.deepEqual(materialPayload(rerun, 'recommendation-2'), {
    sourceIds: ['own', 'b'], materialSelection: { recommendationId: 'recommendation-2', passages: [{ sourceId: 'b', revisionId: 'revision-b', blockId: 'block-b' }] },
  });
});

test('rerun preserves earlier exact revision and block rather than replacing selected evidence', () => {
  const selected = mergeMaterialRecommendations({}, [item('a', 'old')]);
  const rerun = mergeMaterialRecommendations(selected, [item('a', 'new')]);
  assert.equal(materialPayload(rerun).materialSelection.passages[0].revisionId, 'old');
});

test('empty chosen paragraph scope cannot silently become whole-source permission', () => {
  assert.throws(() => materialPayload({ a: { selected: true, mode: 'passages', passages: [] } }), /至少需要勾选一段/);
  assert.deepEqual(materialPayload({ a: { selected: true, mode: 'whole', passages: [{ sourceId: 'a', revisionId: 'r', blockId: 'b' }] } }).materialSelection.passages, []);
});

test('recommendation fills remaining room without dropping existing user choices', () => {
  const choices = initialMaterialChoices({ sourceIds: Array.from({ length: 99 }, (_, index) => `own-${index}`) });
  const result = mergeMaterialRecommendations(choices, [item('new-a'), item('new-b')]);
  assert.equal(materialPayload(result).sourceIds.length, 100);
  assert.equal(result['new-a'].selected, true);
  assert.equal(result['new-b'].selected, false);
  assert.throws(() => materialPayload({ ...result, extra: { selected: true, mode: 'whole', passages: [] } }), /最多选 100/);
});

test('theme-independent context fencing reacts to changed topic, requirement, model, version and material scope', () => {
  const base = { id: 'p', version: 1, topic: '主题', purpose: '目标', sourceIds: ['a'] };
  for (const changes of [{ topic: '新主题' }, { purpose: '新目标' }, { modelId: 'm2' }, { version: 2 }, { sourceIds: ['b'] }]) assert.notEqual(materialContext(base), materialContext({ ...base, ...changes }));
  assert.equal(materialContext(base), materialContext({ ...base, activeSectionId: 's2' }));
});

test('query lines are trimmed and deduplicated without merging independent angles', () => {
  assert.deepEqual(parseMaterialQueries('  人工智能治理\n\n网络安全\n人工智能治理\n'), ['人工智能治理', '网络安全']);
});
