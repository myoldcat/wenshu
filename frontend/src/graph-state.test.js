import test from 'node:test';
import assert from 'node:assert/strict';
import { appendGraphPositions, edgeIdentity, mergeGraphPages, relationshipNature } from './graph-state.js';

test('manual related navigation is distinct from exact-definition navigation and is not original evidence', () => {
  const nature = relationshipNature({ layer: 'navigation', origin: 'manual_decision', supportKind: 'manual_related', evidence: [] });
  assert.equal(nature.key, 'navigation');
  assert.equal(nature.label, '人工关联');
  assert.match(nature.description, /不代表原文/);
  assert.equal(relationshipNature({ layer: 'navigation', supportKind: 'exact_concept_definition' }).label, '同定义导航');
});

test('polling, reordered data and changed degrees preserve existing positions; additions append', () => {
  const nodes = [{ id: 'root', type: 'concept' }, { id: 'a', type: 'concept' }, { id: 'b', type: 'document' }];
  const first = appendGraphPositions(nodes, [{ from: 'root', to: 'a' }], 'root');
  const updated = appendGraphPositions([{ id: 'new', type: 'entity' }, ...nodes.slice().reverse()], [{ from: 'b', to: 'a' }, { from: 'new', to: 'root' }], 'root', first);
  for (const node of nodes) assert.deepEqual(updated.get(node.id), first.get(node.id));
  assert.equal(new Set([...updated.values()].map(point => `${point.x}:${point.y}`)).size, 4);
  assert.equal(first.size, 3, 'the prior scope snapshot is not mutated');
});

test('separate scope caches do not overwrite one another and a removed node can resume its slot', () => {
  const a = appendGraphPositions([{ id: 'same', type: 'concept' }], [], 'same');
  const b = appendGraphPositions([{ id: 'other', type: 'concept' }, { id: 'same', type: 'concept' }], [], 'other');
  assert.notDeepEqual(a.get('same'), b.get('same'));
  const removed = appendGraphPositions([], [], '', a);
  const returned = appendGraphPositions([{ id: 'same', type: 'concept' }], [], 'same', removed);
  assert.deepEqual(returned.get('same'), a.get('same'));
});

test('pagination deduplicates focus and preserves cross-page edges until both endpoints are loaded', () => {
  const edge = { id: 'ab', from: 'a', to: 'b', type: 'association' };
  const first = mergeGraphPages(null, { nodes: [{ id: 'root' }, { id: 'a' }], edges: [], mergeEdges: [edge], snapshotId: 'v1', hasMore: true, nextOffset: 1 });
  assert.equal(first.edges.length, 0);
  assert.equal(first.mergeEdges.length, 1);
  const second = mergeGraphPages(first, { nodes: [{ id: 'root', label: 'Root' }, { id: 'b' }], edges: [], mergeEdges: [edge], snapshotId: 'v1', hasMore: false, nextOffset: null });
  assert.equal(second.nodes.length, 3);
  assert.equal(second.edges.length, 1);
  assert.equal(second.mergeEdges.length, 0);
  assert.equal(second.hasMore, false);
  assert.equal(second.nodes.find(node => node.id === 'root').label, 'Root');
});

test('a snapshot restart replaces accumulated semantic data rather than mixing revisions', () => {
  const old = { nodes: [{ id: 'root' }, { id: 'removed' }], edges: [{ id: 'old-edge', from: 'root', to: 'removed' }], snapshotId: 'v1' };
  const replacement = mergeGraphPages(old, { nodes: [{ id: 'root' }, { id: 'current' }], edges: [], snapshotId: 'v2' });
  assert.deepEqual(replacement.nodes.map(node => node.id), ['root', 'current']);
  assert.equal(replacement.edges.length, 0);
  assert.equal(replacement.snapshotId, 'v2');
});

test('path edgeId shares identity with rendered id, preserving canonical direction', () => {
  assert.equal(edgeIdentity({ id: 'relation-1', from: 'a', to: 'b' }), edgeIdentity({ edgeId: 'relation-1', from: 'a', to: 'b', traversalDirection: 'reverse' }));
  assert.notEqual(edgeIdentity({ from: 'a', to: 'b' }), edgeIdentity({ from: 'b', to: 'a' }));
});

test('equivalence navigation cannot be presented as source-supported semantic relation', () => {
  assert.equal(relationshipNature({ layer: 'navigation', evidence: [{ blockId: 'b1' }] }).key, 'navigation');
  assert.equal(relationshipNature({ supportKind: 'wording_equivalence' }).key, 'navigation');
  assert.equal(relationshipNature({ derivedBy: 'exact_name_and_definition' }).key, 'navigation');
  assert.equal(relationshipNature({ layer: 'provenance', evidence: [{ blockId: 'b1' }] }).key, 'source');
  assert.equal(relationshipNature({ layer: 'semantic', evidence: [{ blockId: 'b1' }] }).key, 'supported');
  assert.equal(relationshipNature({ layer: 'semantic', evidence: [] }).key, 'unverified');
});
