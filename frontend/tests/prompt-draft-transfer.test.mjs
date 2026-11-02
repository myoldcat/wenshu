import test from 'node:test';
import assert from 'node:assert/strict';
import { createPromptDraftTransferBridge } from '../src/prompt-draft-transfer.js';
import { planPromptDraftAdoption } from '../src/prompt-library-state.js';

const draft = (content = '结构人工修改。') => ({ id: 'writing_structure_report', version: 3, content, originalContent: '原结构要求。', contentView: 'source' });
const payload = (value = draft(), overrides = {}) => ({ id: value.id, draft: value, conflict: null, error: '', ...overrides });
const state = (overrides = {}) => ({ drafts: {}, conflicts: {}, errors: {}, savingId: null, ...overrides });
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test('a hidden structure draft waits in memory until a template receiver accepts it', async () => {
  const bridge = createPromptDraftTransferBridge(), transfer = payload();
  let source = transfer.draft, received = null;
  bridge.publish(transfer, () => { source = null; });
  await bridge.flush();
  assert.equal(source, transfer.draft); assert.equal(bridge.pending().length, 1);
  const unregister = bridge.register(value => { received = value; return true; });
  await bridge.flush();
  assert.equal(received, transfer); assert.equal(source, null); assert.deepEqual(bridge.pending(), []);
  unregister();
});

test('rejected or failing receivers retain the source draft and can retry on registration', async () => {
  const bridge = createPromptDraftTransferBridge(), transfer = payload();
  let acknowledgements = 0;
  bridge.publish(transfer, () => { acknowledgements += 1; });
  const reject = bridge.register(() => false);
  await bridge.flush();
  assert.equal(acknowledgements, 0); assert.equal(bridge.pending().length, 1);
  reject();
  const throwing = bridge.register(() => { throw new Error('Receiver temporarily unavailable'); });
  await bridge.flush();
  assert.equal(acknowledgements, 0); throwing();
  const accept = bridge.register(() => true);
  await bridge.flush();
  assert.equal(acknowledgements, 1); assert.deepEqual(bridge.pending(), []); accept();
});

test('source cleanup happens only after asynchronous destination confirmation', async () => {
  const bridge = createPromptDraftTransferBridge(), transfer = payload();
  let resolveAdoption, acknowledgements = 0;
  bridge.register(() => new Promise(resolve => { resolveAdoption = resolve; }));
  bridge.publish(transfer, () => { acknowledgements += 1; });
  await tick();
  assert.equal(acknowledgements, 0); assert.equal(bridge.pending().length, 1);
  resolveAdoption(true); await bridge.flush();
  assert.equal(acknowledgements, 1); assert.deepEqual(bridge.pending(), []);
});

test('a receiver registered during a pending attempt gets a retry after it completes', async () => {
  const bridge = createPromptDraftTransferBridge(), transfer = payload();
  let rejectFirst, acknowledgements = 0;
  const unregister = bridge.register(() => new Promise(resolve => { rejectFirst = resolve; }));
  bridge.publish(transfer, () => { acknowledgements += 1; }); await tick();
  unregister(); bridge.register(() => true);
  rejectFirst(false); await tick(); await bridge.flush();
  assert.equal(acknowledgements, 1); assert.deepEqual(bridge.pending(), []);
});

test('a saving source can refuse cleanup without losing the pending handoff', async () => {
  const bridge = createPromptDraftTransferBridge(), transfer = payload();
  let saving = true, cleared = false;
  bridge.register(() => true);
  bridge.publish(transfer, () => { if (saving) return false; cleared = true; return true; });
  await bridge.flush(); assert.equal(cleared, false); assert.equal(bridge.pending().length, 1);
  saving = false; await bridge.flush();
  assert.equal(cleared, true); assert.deepEqual(bridge.pending(), []);
});

test('an older in-flight adoption cannot delete a newer source transfer', async () => {
  const bridge = createPromptDraftTransferBridge(), first = payload(), second = payload(draft('后来的作者修改。'));
  let resolveFirst, source = first.draft;
  bridge.register(value => value === first ? new Promise(resolve => { resolveFirst = resolve; }) : false);
  bridge.publish(first, () => { if (source !== first.draft) return false; source = null; return true; });
  await tick(); source = second.draft;
  bridge.publish(second, () => { source = null; });
  resolveFirst(true); await tick(); await bridge.flush();
  assert.equal(source, second.draft); assert.equal(bridge.pending()[0], second);
});

test('adoption keeps the exact source draft, conflict, error and frozen save view', () => {
  const conflict = { latest: { id: 'writing_structure_report', version: 4, content: '最新要求。' } };
  const transfer = payload(draft(), { conflict, error: '尚未处理的版本冲突。' });
  const before = state();
  const result = planPromptDraftAdoption(before, transfer);
  assert.equal(result.accepted, true);
  assert.equal(result.state.drafts[transfer.id], transfer.draft);
  assert.equal(result.state.conflicts[transfer.id], conflict);
  assert.equal(result.state.errors[transfer.id], transfer.error);
  assert.equal(result.state.drafts[transfer.id].contentView, 'source');
  assert.deepEqual(before, state());
});

test('destination authored work, conflicts and saving state cannot be overwritten', () => {
  const transfer = payload();
  const authored = draft('目标页已有人工修改。');
  assert.equal(planPromptDraftAdoption(state({ drafts: { [authored.id]: authored } }), transfer).accepted, false);
  assert.equal(planPromptDraftAdoption(state({ conflicts: { [transfer.id]: { latest: null } } }), transfer).accepted, false);
  assert.equal(planPromptDraftAdoption(state({ savingId: transfer.id }), transfer).accepted, false);
  const clean = { ...authored, content: authored.originalContent };
  assert.equal(planPromptDraftAdoption(state({ drafts: { [clean.id]: clean }, savingId: 'unrelated' }), transfer).accepted, true);
  const own = state({ drafts: { [transfer.id]: transfer.draft } });
  assert.equal(planPromptDraftAdoption(own, transfer).accepted, true, 'retrying an already adopted object is idempotent');
});
