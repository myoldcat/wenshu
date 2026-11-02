import { useEffect, useRef, useState } from 'react';
import { api, connectionMessage } from './local-api.js';

const pendingStatuses = new Set(['queued', 'running', 'pending']);
const terminalStatuses = new Set(['completed', 'failed', 'cancelled', 'no_candidates']);
const pollInterval = hidden => hidden ? 12000 : 2500;
const emptyCover = Object.freeze({ status: 'not_processed' });

function contextFor(doc, modelId, modelVersion) {
  const candidates = Array.isArray(doc.coverCandidates) ? doc.coverCandidates : [];
  const revisionId = doc.revisionId || '';
  const candidatesHash = doc.coverCandidatesHash || doc.candidatesHash || doc.cardCover?.candidatesHash || '';
  // The local candidate fingerprint fences a changed list even before the
  // server has attached its cryptographic candidatesHash to a decision.
  const promptVersion = doc.cardCover?.promptVersion || '';
  const key = JSON.stringify([doc.id, revisionId, modelId || '', modelVersion ?? '', promptVersion, candidatesHash, candidates]);
  return { key, id: doc.id, revisionId, modelId, modelVersion, promptVersion, candidatesHash, candidates, doc };
}

function belongsTo(cover, context) {
  if (!cover) return false;
  if (cover.revisionId && cover.revisionId !== context.revisionId) return false;
  if (cover.modelId && context.modelId && cover.modelId !== context.modelId) return false;
  if (cover.modelVersion != null && context.modelVersion != null && String(cover.modelVersion) !== String(context.modelVersion)) return false;
  if (cover.promptVersion && context.promptVersion && cover.promptVersion !== context.promptVersion) return false;
  if (cover.candidatesHash && context.candidatesHash && cover.candidatesHash !== context.candidatesHash) return false;
  return true;
}

/** A small viewport queue, exported so request races can be tested without a DOM. */
export function createCardCoverQueue({ request = api, onChange = () => {}, hidden = () => typeof document !== 'undefined' && document.hidden, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout, deferInitialPosts = false } = {}) {
  const records = new Map();
  const active = new Map();
  let contexts = new Map();
  let visibleKeys = [];
  let enabled = false;
  let stopped = false;
  let timer;
  let startupTimer;
  let postsReady = !deferInitialPosts;
  let configuration = '';

  const notify = () => { if (!stopped) onChange(); };
  const abort = key => {
    const running = active.get(key);
    if (!running) return;
    active.delete(key);
    if (running.method === 'POST') records.get(key).needsRead = true;
    running.controller.abort();
  };
  const clearScheduled = () => { if (timer !== undefined) clearTimer(timer); timer = undefined; };
  const schedule = delay => {
    clearScheduled();
    if (!stopped) timer = setTimer(() => { timer = undefined; pump(); }, Math.max(0, delay));
  };
  const currentRecord = context => {
    let record = records.get(context.key);
    if (!record) {
      const seed = belongsTo(context.doc.cardCover, context) ? context.doc.cardCover : emptyCover;
      record = { cover: seed, job: null, postAttempted: false, needsRead: false, failures: 0, dueAt: 0, error: '', seed: JSON.stringify(seed) };
      records.set(context.key, record);
    } else {
      const seed = context.doc.cardCover;
      const fingerprint = JSON.stringify(seed);
      if (fingerprint !== record.seed && belongsTo(seed, context)) {
        record.seed = fingerprint;
        // Library polling may still contain an older not_processed snapshot.
        // It must not erase a queued task or a completed null-image decision.
        if (terminalStatuses.has(seed.status) || (!terminalStatuses.has(record.cover.status) && record.cover.status === 'not_processed' && seed.status !== 'not_processed')) {
          record.cover = seed;
          record.error = '';
          record.failures = 0;
          if (terminalStatuses.has(seed.status)) record.needsRead = false;
        }
      }
    }
    return record;
  };
  const isPending = record => pendingStatuses.has(record.cover.status) || pendingStatuses.has(record.job?.status);

  async function run(context, record, method) {
    const running = { controller: new AbortController(), method };
    active.set(context.key, running);
    if (method === 'POST') record.postAttempted = true;
    try {
      const path = `/documents/${encodeURIComponent(context.id)}/card-cover`;
      const params = new URLSearchParams();
      if (context.revisionId) params.set('revision_id', context.revisionId);
      if (context.modelId) params.set('model_id', context.modelId);
      const suffix = params.size ? `?${params}` : '';
      const result = await request(`${path}${method === 'GET' ? suffix : ''}`, {
        signal: running.controller.signal,
        ...(method === 'POST' ? { method: 'POST', body: JSON.stringify({ revisionId: context.revisionId, ...(context.modelId ? { modelId: context.modelId } : {}), force: false }) } : {}),
      });
      if (stopped || running.controller.signal.aborted || active.get(context.key) !== running || !contexts.has(context.key)) return;
      if (!result.cardCover || !belongsTo(result.cardCover, context)) {
        record.error = '封面结果的来源或模型版本已变化，请刷新文档列表。';
        record.needsRead = false;
        record.failures = 3;
        return;
      }
      record.cover = result.cardCover;
      record.job = result.job || null;
      record.error = '';
      record.failures = 0;
      record.needsRead = false;
      record.dueAt = now() + pollInterval(hidden());
    } catch (problem) {
      if (running.controller.signal.aborted || active.get(context.key) !== running || stopped) return;
      record.error = connectionMessage(problem);
      record.failures += 1;
      // A POST may have reached the server before the connection broke. Only
      // read its status from here; never submit a second automatic model job.
      record.needsRead = record.postAttempted || isPending(record);
      record.dueAt = now() + pollInterval(hidden()) * Math.min(4, record.failures);
    } finally {
      if (active.get(context.key) === running) {
        active.delete(context.key);
        notify();
        pump();
      }
    }
  }

  function pump() {
    if (stopped) return;
    clearScheduled();
    let nextDelay = Infinity;
    for (const key of visibleKeys) {
      const context = contexts.get(key);
      if (!context || active.has(key)) continue;
      const record = currentRecord(context);
      if (terminalStatuses.has(record.cover.status) || record.failures >= 3) continue;
      const read = record.needsRead || isPending(record);
      const submit = postsReady && enabled && !hidden() && context.modelId && context.revisionId && context.candidates.length > 0 && !record.postAttempted && record.cover.status === 'not_processed';
      if (!read && !submit) continue;
      const delay = record.dueAt - now();
      if (delay > 0) { nextDelay = Math.min(nextDelay, delay); continue; }
      if (active.size >= 3) continue;
      run(context, record, read ? 'GET' : 'POST');
    }
    if (Number.isFinite(nextDelay)) schedule(nextDelay);
  }

  return {
    update({ documents = [], visibleIds = [], enabled: nextEnabled = false, modelId, modelVersion }) {
      const nextConfiguration = JSON.stringify([modelId || '', modelVersion ?? '', !!nextEnabled]);
      if (configuration !== nextConfiguration) {
        for (const key of active.keys()) abort(key);
        configuration = nextConfiguration;
      }
      enabled = !!nextEnabled;
      contexts = new Map(documents.map(doc => { const context = contextFor(doc, modelId, modelVersion); return [context.key, context]; }));
      const visible = new Set(visibleIds);
      visibleKeys = [...contexts.values()].filter(context => visible.has(context.id)).map(context => context.key);
      const nextVisible = new Set(visibleKeys);
      for (const key of active.keys()) if (!nextVisible.has(key)) abort(key);
      let changed = false;
      for (const context of contexts.values()) {
        const previousCover = records.get(context.key)?.cover;
        const record = currentRecord(context);
        if (previousCover && previousCover !== record.cover) changed = true;
      }
      pump();
      if (changed) notify();
    },
    get(doc, modelId, modelVersion) {
      const context = contextFor(doc, modelId, modelVersion);
      const record = records.get(context.key);
      return { cardCover: record?.cover || (belongsTo(doc.cardCover, context) ? doc.cardCover : emptyCover), error: record?.error || '' };
    },
    resume() {
      stopped = false;
      // React StrictMode immediately repeats setup/cleanup/setup on mount.
      // Wait one task before starting a new model request so that its probe
      // cleanup cannot abort an unsent POST and leave only an ambiguous GET.
      if (deferInitialPosts && !postsReady && startupTimer === undefined) {
        startupTimer = setTimer(() => {
          startupTimer = undefined;
          if (stopped) return;
          postsReady = true;
          pump();
        }, 0);
      }
      pump();
    },
    visibilityChanged() {
      if (!hidden()) {
        for (const key of visibleKeys) { const record = records.get(key); if (record && isPending(record)) record.dueAt = Math.min(record.dueAt, now()); }
      }
      pump();
    },
    dispose() {
      stopped = true;
      clearScheduled();
      if (startupTimer !== undefined) clearTimer(startupTimer);
      startupTimer = undefined;
      if (deferInitialPosts) postsReady = false;
      for (const key of active.keys()) abort(key);
    },
  };
}

/** Only visible candidates can start work; completed null decisions stay empty. */
export function useCardCovers({ documents = [], visibleIds = [], enabled = false, modelId, modelVersion }) {
  const [, redraw] = useState(0);
  const queue = useRef(null);
  if (!queue.current) queue.current = createCardCoverQueue({ onChange: () => redraw(value => value + 1), deferInitialPosts: true });
  useEffect(() => {
    const current = queue.current;
    current.resume();
    const visibilityChanged = () => current.visibilityChanged();
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => { document.removeEventListener('visibilitychange', visibilityChanged); current.dispose(); };
  }, []);
  const visibleKey = JSON.stringify(visibleIds);
  useEffect(() => { queue.current.update({ documents, visibleIds, enabled, modelId, modelVersion }); }, [documents, visibleKey, enabled, modelId, modelVersion]);

  const byId = new Map(documents.map(doc => [doc.id, doc]));
  const getCardCover = docOrId => {
    const doc = typeof docOrId === 'object' && docOrId ? docOrId : byId.get(docOrId);
    return doc ? queue.current.get(doc, modelId, modelVersion).cardCover : emptyCover;
  };
  const coversById = Object.fromEntries(documents.map(doc => [doc.id, getCardCover(doc)]));
  const errorsById = Object.fromEntries(documents.map(doc => [doc.id, queue.current.get(doc, modelId, modelVersion).error]).filter(([, error]) => error));
  return { coversById, getCardCover, errorsById };
}
