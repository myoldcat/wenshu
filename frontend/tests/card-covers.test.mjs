import assert from 'node:assert/strict';
import test from 'node:test';
import { createCardCoverQueue } from '../src/useCardCovers.js';

const settle = () => new Promise(resolve => setImmediate(resolve));
const coverFor = (id, changes = {}) => ({ status: 'not_processed', revisionId: `revision-${id}`, modelId: 'model-1', modelVersion: 1, promptVersion: 'cover-1', candidatesHash: `candidates-${id}`, ...changes });
const documentFor = (id, changes = {}) => ({ id, revisionId: `revision-${id}`, coverCandidates: [{ assetId: `asset-${id}`, url: `/api/assets/asset-${id}`, width: 800, height: 600, ordinal: 1 }], cardCover: coverFor(id), ...changes });
function harness(options = {}) {
  let clock = 1000;
  let pageHidden = false;
  let sequence = 0;
  const timers = new Map();
  const requests = [];
  const queue = createCardCoverQueue({
    now: () => clock,
    hidden: () => pageHidden,
    setTimer: (callback, delay) => { const id = ++sequence; timers.set(id, { callback, at: clock + delay }); return id; },
    clearTimer: id => timers.delete(id),
    request: (path, options) => new Promise((resolve, reject) => requests.push({ path, options, resolve, reject })),
    ...options,
  });
  const update = (documents, visibleIds = documents.map(doc => doc.id), changes = {}) => queue.update({ documents, visibleIds, enabled: true, modelId: 'model-1', modelVersion: 1, ...changes });
  const advance = async ms => {
    clock += ms;
    const due = [...timers].filter(([, value]) => value.at <= clock);
    for (const [id, value] of due) { timers.delete(id); value.callback(); }
    await settle();
  };
  return { queue, requests, update, advance, hide: () => { pageHidden = true; queue.visibilityChanged(); }, show: () => { pageHidden = false; queue.visibilityChanged(); }, timers };
}

test('only visible candidates start work, with at most three requests in flight', async () => {
  const state = harness();
  const documents = [1, 2, 3, 4, 5, 6].map(documentFor);
  state.update(documents, [1, 2, 3, 4, 5]);
  assert.equal(state.requests.length, 3);
  assert.deepEqual(state.requests.map(request => JSON.parse(request.options.body).revisionId), ['revision-1', 'revision-2', 'revision-3']);
  state.requests[0].resolve({ cardCover: coverFor(1, { status: 'queued' }), job: { status: 'queued' } });
  await settle();
  assert.equal(state.requests.length, 4);
  assert.equal(JSON.parse(state.requests[3].options.body).revisionId, 'revision-4');
  state.update(documents, [1, 2, 3, 4, 5]);
  assert.equal(state.requests.length, 4, 'rerender must not duplicate POSTs');
  state.queue.dispose();
});

test('StrictMode setup/cleanup/setup cannot consume a POST that never reached the service', async () => {
  const state = harness({ deferInitialPosts: true });
  const doc = documentFor(1);
  // First effect setup, followed synchronously by StrictMode's probe cleanup.
  state.queue.resume();
  state.update([doc]);
  assert.equal(state.requests.length, 0, 'the first setup must not submit a model job');
  state.queue.dispose();
  await state.advance(0);
  assert.equal(state.requests.length, 0, 'cleanup must cancel its deferred startup');
  // The second effect setup must still issue exactly one real POST, rather
  // than a GET returning not_processed after an aborted, unsent first POST.
  state.queue.resume();
  state.update([doc]);
  assert.equal(state.requests.length, 0);
  await state.advance(0);
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].options.method, 'POST');
  assert.equal(state.requests[0].options.signal.aborted, false);
  state.requests[0].resolve({ cardCover: coverFor(1, { status: 'completed', url: '/chosen-image', assetId: 'asset-1' }) });
  await settle();
  assert.equal(state.queue.get(doc, 'model-1', 1).cardCover.url, '/chosen-image');
  state.update([doc]);
  await state.advance(60000);
  assert.equal(state.requests.length, 1);
  state.queue.dispose();
});

test('StrictMode deferred startup still respects disabled processing', async () => {
  const state = harness({ deferInitialPosts: true });
  const doc = documentFor(1);
  state.queue.resume();
  state.update([doc]);
  state.queue.dispose();
  state.queue.resume();
  state.update([doc], [1], { enabled: false });
  await state.advance(0);
  assert.equal(state.requests.length, 0);
  state.queue.dispose();
});

test('cached decisions, failed tasks, cancelled tasks and absent candidates never create a model job', () => {
  const state = harness();
  const documents = [
    documentFor(1, { cardCover: coverFor(1, { status: 'completed', assetId: null, url: null, reason: '无合适配图' }) }),
    documentFor(2, { cardCover: coverFor(2, { status: 'failed' }) }),
    documentFor(3, { cardCover: coverFor(3, { status: 'cancelled' }) }),
    documentFor(4, { coverCandidates: [] }),
    documentFor(5, { cardCover: coverFor(5, { status: 'no_candidates' }) }),
  ];
  state.update(documents);
  assert.equal(state.requests.length, 0);
  assert.equal(state.queue.get(documents[0], 'model-1', 1).cardCover.url, null);
  assert.equal(state.queue.get(documents[0], 'model-1', 1).cardCover.reason, '无合适配图');
  state.queue.dispose();
});

test('existing jobs are read with revision and model scope; hidden pages use slower polling', async () => {
  const state = harness();
  const doc = documentFor(1, { cardCover: coverFor(1, { status: 'running' }) });
  state.hide();
  state.update([doc]);
  assert.equal(state.requests.length, 1);
  assert.equal(state.requests[0].options.method, undefined);
  assert.match(state.requests[0].path, /revision_id=revision-1&model_id=model-1$/);
  state.requests[0].resolve({ cardCover: coverFor(1, { status: 'running' }), job: { status: 'running' } });
  await settle();
  await state.advance(3000);
  assert.equal(state.requests.length, 1);
  await state.advance(9000);
  assert.equal(state.requests.length, 2);
  state.queue.dispose();
});

test('late responses from a previous article revision cannot populate the new card', async () => {
  const state = harness();
  const oldDoc = documentFor(1);
  const newDoc = documentFor(1, { revisionId: 'revision-1-v2', cardCover: coverFor(1, { revisionId: 'revision-1-v2' }) });
  state.update([oldDoc]);
  state.update([newDoc]);
  assert.equal(state.requests.length, 2);
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.requests[0].resolve({ cardCover: coverFor(1, { status: 'completed', url: '/old-image' }) });
  await settle();
  assert.equal(state.queue.get(newDoc, 'model-1', 1).cardCover.status, 'not_processed');
  state.requests[1].resolve({ cardCover: coverFor(1, { revisionId: 'revision-1-v2', status: 'completed', assetId: 'asset-1', url: '/new-image' }) });
  await settle();
  assert.equal(state.queue.get(newDoc, 'model-1', 1).cardCover.url, '/new-image');
  state.queue.dispose();
});

test('model-version changes abort the previous request and reject old-version responses', async () => {
  const state = harness();
  const doc = documentFor(1);
  state.update([doc]);
  state.update([doc], [1], { modelVersion: 2 });
  assert.equal(state.requests.length, 2);
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.requests[1].resolve({ cardCover: coverFor(1, { status: 'completed', url: '/wrong-model' }) });
  await settle();
  assert.equal(state.queue.get(doc, 'model-1', 2).cardCover.status, 'not_processed');
  assert.match(state.queue.get(doc, 'model-1', 2).error, /模型版本/);
  state.queue.dispose();
});

test('an aborted POST is reconciled through GET when its card becomes visible again', async () => {
  const state = harness();
  const doc = documentFor(1);
  state.update([doc]);
  state.update([doc], []);
  assert.equal(state.requests[0].options.signal.aborted, true);
  state.update([doc]);
  assert.equal(state.requests.length, 2);
  assert.equal(state.requests[1].options.method, undefined);
  state.requests[1].resolve({ cardCover: coverFor(1, { status: 'completed', url: null, assetId: null }) });
  await settle();
  state.update([doc]);
  assert.equal(state.requests.length, 2);
  state.queue.dispose();
});

test('network ambiguity never repeats a POST and status-read retries are bounded', async () => {
  const state = harness();
  const doc = documentFor(1);
  state.update([doc]);
  state.requests[0].reject(new Error('Failed to fetch'));
  await settle();
  await state.advance(2500);
  assert.equal(state.requests.length, 2);
  assert.equal(state.requests[1].options.method, undefined);
  state.requests[1].reject(new Error('Failed to fetch'));
  await settle();
  await state.advance(5000);
  assert.equal(state.requests.length, 3);
  assert.equal(state.requests[2].options.method, undefined);
  state.requests[2].reject(new Error('Failed to fetch'));
  await settle();
  await state.advance(60000);
  state.update([doc]);
  assert.equal(state.requests.length, 3);
  assert.equal(state.requests.filter(request => request.options.method === 'POST').length, 1);
  state.queue.dispose();
});

test('disabling automatic processing starts no new jobs but retains a completed image', async () => {
  const state = harness();
  const doc = documentFor(1);
  const cached = documentFor(2, { cardCover: coverFor(2, { status: 'completed', url: '/saved-image', assetId: 'asset-2' }) });
  state.update([doc, cached], [1, 2], { enabled: false });
  assert.equal(state.requests.length, 0);
  assert.equal(state.queue.get(cached, 'model-1', 1).cardCover.url, '/saved-image');
  state.update([doc, cached]);
  assert.equal(state.requests.length, 1);
  state.queue.dispose();
});
