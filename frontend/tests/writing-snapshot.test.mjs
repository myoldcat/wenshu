import test from 'node:test';
import assert from 'node:assert/strict';
import { canAcceptWritingSnapshot } from '../src/writing-snapshot.js';

const snapshot = (version, changes = {}) => ({
  id: 'writing-one', version,
  sections: [{ id: 'chapter-one', content: '原正文', locked: false }],
  runs: [{ id: 'run-one', status: 'running' }], ...changes,
});

test('a delayed poll cannot undo a completed manual save and lock', async () => {
  let returnPoll;
  const delayedPoll = new Promise(resolve => { returnPoll = resolve; });
  const older = snapshot(7);
  let current = snapshot(8, { sections: [{ id: 'chapter-one', content: '必须保留的人工修改', locked: true }] });
  const baseline = structuredClone(current); // Save and lock have completed, so the draft is clean.
  returnPoll(older);
  const incoming = await delayedPoll;
  if (canAcceptWritingSnapshot(incoming, current, baseline)) current = incoming;
  assert.equal(current.version, 8);
  assert.equal(current.sections[0].content, '必须保留的人工修改');
  assert.equal(current.sections[0].locked, true);
});

test('the saved baseline also fences responses while the editable draft lags', () => {
  assert.equal(canAcceptWritingSnapshot(snapshot(7), snapshot(7), snapshot(8)), false);
});

test('status-only merges remember the newest observed server version', () => {
  const current = snapshot(7), baseline = snapshot(7);
  const newest = snapshot(9, { runs: [{ id: 'run-one', status: 'succeeded' }] });
  assert.equal(canAcceptWritingSnapshot(newest, current, baseline), true);
  const observed = { id: newest.id, version: newest.version };
  const delayed = snapshot(8, { runs: [{ id: 'run-one', status: 'running' }] });
  assert.equal(canAcceptWritingSnapshot(delayed, current, baseline, observed), false);
  assert.equal(canAcceptWritingSnapshot(snapshot(10), current, baseline, observed), true);
});

test('same-version task progress remains acceptable without a document mutation', () => {
  assert.equal(canAcceptWritingSnapshot(snapshot(8), snapshot(8), snapshot(8), snapshot(8)), true);
});

test('another project and a response without a valid version cannot replace current state', () => {
  assert.equal(canAcceptWritingSnapshot(snapshot(9, { id: 'writing-other' }), snapshot(8), snapshot(8)), false);
  assert.equal(canAcceptWritingSnapshot(snapshot(undefined), snapshot(8), snapshot(8)), false);
  assert.equal(canAcceptWritingSnapshot(snapshot('9'), snapshot(8), snapshot(8)), false);
});

test('the previous project baseline does not block a legitimate newer selected project', () => {
  assert.equal(canAcceptWritingSnapshot(snapshot(2), snapshot(1), snapshot(99, { id: 'writing-other' }), snapshot(100, { id: 'writing-other' })), true);
});
