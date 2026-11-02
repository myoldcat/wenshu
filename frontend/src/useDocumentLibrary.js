import { useCallback, useEffect, useRef, useState } from 'react';
import { api, connectionMessage, normalizeDocument } from './local-api';

export function useDocumentLibrary() {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [jobs, setJobs] = useState([]);
  const [jobCounts, setJobCounts] = useState({});
  const [jobsError, setJobsError] = useState('');
  const [jobsLoading, setJobsLoading] = useState(true);
  const [writingJobs, setWritingJobs] = useState([]);
  const [writingJobCounts, setWritingJobCounts] = useState({});
  const [writingJobsError, setWritingJobsError] = useState('');
  const [writingJobsLoading, setWritingJobsLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const documentsRef = useRef(documents);
  const refreshId = useRef(0);
  const jobsRefreshId = useRef(0);
  const jobFingerprint = useRef(null);
  const patchQueues = useRef(new Map());
  const deletedIds = useRef(new Set());
  const deleteRequests = useRef(new Map());
  documentsRef.current = documents;

  const refresh = useCallback(async ({ quiet = false } = {}) => {
    const request = ++refreshId.current;
    if (!quiet) setLoading(true);
    try {
      const collected = [];
      let total = Infinity;
      while (collected.length < total) {
        const page = await api(`/documents?limit=500&offset=${collected.length}`);
        const items = page.documents || [];
        collected.push(...items.map(normalizeDocument));
        total = page.total ?? collected.length;
        if (!items.length) break;
      }
      if (request !== refreshId.current) return;
      // Preserve mutations that are currently being saved while a poll completes.
      const next = collected.filter(doc => !deletedIds.current.has(doc.id)).map(doc => patchQueues.current.has(doc.id)
        ? { ...doc, ...documentsRef.current.find(item => item.id === doc.id) } : doc);
      documentsRef.current = next;
      setDocuments(next);
      setError('');
      setRevision(value => value + 1);
    } catch (problem) {
      if (request === refreshId.current) setError(connectionMessage(problem));
    } finally {
      if (request === refreshId.current) setLoading(false);
    }
  }, []);

  const isDocumentDeleted = useCallback(id => deletedIds.current.has(id), []);
  const deleteDocument = useCallback((id, confirmation) => {
    if (deleteRequests.current.has(id)) return deleteRequests.current.get(id);
    const pending = (async () => {
      // A metadata write already submitted from the reader must settle first.
      await patchQueues.current.get(id)?.catch(() => {});
      const result = await api(`/documents/${encodeURIComponent(id)}`, { method: 'DELETE', body: JSON.stringify(confirmation) });
      deletedIds.current.add(id);
      // Ignore a catalog response that began before this deletion completed.
      refreshId.current += 1;
      const next = documentsRef.current.filter(doc => doc.id !== id);
      documentsRef.current = next;
      setDocuments(next);
      setLoading(false);
      setRevision(value => value + 1);
      window.dispatchEvent(new CustomEvent('wenshu-library-changed', { detail: { sourceId: id } }));
      return result;
    })().finally(() => { if (deleteRequests.current.get(id) === pending) deleteRequests.current.delete(id); });
    deleteRequests.current.set(id, pending);
    return pending;
  }, []);

  const refreshJobs = useCallback(async () => {
    const request = ++jobsRefreshId.current;
    await Promise.all([
    (async () => { try {
      const result = await api('/jobs?limit=100&scope=ingest');
      if (request !== jobsRefreshId.current) return;
      const items = result.jobs || [];
      setJobs(items);
      setJobCounts(result.counts || {});
      setJobsError('');
      const nextFingerprint = JSON.stringify([result.counts, items.map(job => [job.id, job.status, job.updated_at])]);
      if (jobFingerprint.current !== null && jobFingerprint.current !== nextFingerprint) await refresh({ quiet: true });
      jobFingerprint.current = nextFingerprint;
    } catch (problem) { if (request === jobsRefreshId.current) setJobsError(connectionMessage(problem)); }
    finally { if (request === jobsRefreshId.current) setJobsLoading(false); } })(),
    (async () => { try {
      const result = await api('/jobs?limit=100&scope=writing');
      if (request !== jobsRefreshId.current) return;
      setWritingJobs(result.jobs || []);
      setWritingJobCounts(result.counts || {});
      setWritingJobsError('');
    } catch (problem) { if (request === jobsRefreshId.current) setWritingJobsError(connectionMessage(problem)); }
    finally { if (request === jobsRefreshId.current) setWritingJobsLoading(false); } })(),
    ]);
  }, [refresh]);

  useEffect(() => {
    refresh();
    let stopped = false;
    let timer;
    const tick = async () => {
      await refreshJobs();
      if (!stopped) timer = setTimeout(tick, document.hidden ? 8000 : 2000);
    };
    tick();
    return () => { stopped = true; clearTimeout(timer); refreshId.current += 1; jobsRefreshId.current += 1; };
  }, [refresh, refreshJobs]);

  const patchDocument = useCallback((id, changes) => {
    const before = documentsRef.current.find(doc => doc.id === id);
    if (!before) return Promise.reject(new Error('当前文档已不在知源文库中，请刷新后重试。'));
    // Structured paths carry program-generated IDs; wait for their canonical
    // response instead of briefly replacing nodes with the editor's raw fields.
    const optimisticChanges = { ...changes };
    delete optimisticChanges.classificationPaths;
    delete optimisticChanges.articleType;
    delete optimisticChanges.revisionId;
    const optimistic = documentsRef.current.map(doc => doc.id === id ? { ...doc, ...optimisticChanges } : doc);
    documentsRef.current = optimistic;
    setDocuments(optimistic);
    const previous = patchQueues.current.get(id) || Promise.resolve();
    const pending = previous.catch(() => {}).then(async () => {
      try {
        const result = await api(`/documents/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(changes) });
        if (patchQueues.current.get(id) === pending) {
          const saved = normalizeDocument(result.document);
          const next = documentsRef.current.map(doc => doc.id === id ? { ...doc, ...saved } : doc);
          documentsRef.current = next;
          setDocuments(next);
          setRevision(value => value + 1);
        }
        return result.document;
      } catch (problem) {
        const next = documentsRef.current.map(doc => {
          if (doc.id !== id) return doc;
          const rollback = {};
          for (const key of Object.keys(changes)) {
            if (JSON.stringify(doc[key]) === JSON.stringify(changes[key])) rollback[key] = before[key];
          }
          return { ...doc, ...rollback };
        });
        documentsRef.current = next;
        setDocuments(next);
        throw problem;
      } finally {
        if (patchQueues.current.get(id) === pending) patchQueues.current.delete(id);
      }
    });
    patchQueues.current.set(id, pending);
    return pending;
  }, []);

  const submitImport = useCallback(async (kind, payload) => {
    const options = { method: 'POST' };
    if (kind === 'files') {
      const body = new FormData();
      Array.from(payload).forEach(file => body.append('files', file));
      options.body = body;
    } else options.body = JSON.stringify(kind === 'urls' ? { urls: payload } : {});
    const result = await api(`/imports/${kind}`, options);
    await refreshJobs();
    await refresh({ quiet: true });
    return result;
  }, [refresh, refreshJobs]);

  const jobAction = useCallback(async (id, action) => {
    await api(`/jobs/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: '{}' });
    await refreshJobs();
  }, [refreshJobs]);

  return { documents, loading, error, refresh, jobs, jobCounts, jobsError, jobsLoading, writingJobs, writingJobCounts, writingJobsError, writingJobsLoading, refreshJobs, patchDocument, deleteDocument, isDocumentDeleted, submitImport, jobAction, revision };
}

export function useDocumentSearch({ query, category, sourceIds = null, source, tab, since, sort, revision }) {
  const [results, setResults] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [retryKey, setRetryKey] = useState(0);
  const controller = useRef(null);
  const requestId = useRef(0);
  const previousCriteria = useRef(null);
  const criteria = JSON.stringify([query.trim(), category, sourceIds, source, tab, since, sort]);
  const latestCriteria = useRef(criteria);
  latestCriteria.current = criteria;
  const criteriaCurrent = previousCriteria.current === criteria;
  const key = JSON.stringify([criteria, revision, retryKey]);

  const request = useCallback(async (offset, signal, generation) => {
    const params = new URLSearchParams({ q: query.trim(), limit: '100', offset: String(offset), filter: tab === 'saved' ? 'favorite' : tab === 'unread' ? 'unread' : 'all', sort });
    if (category !== '全部') params.set('category', category);
    if (source !== '全部') params.set('source', source);
    if (since) params.set('since', since);
    try {
      const result = sourceIds === null
        ? await api(`/search?${params}`, { signal })
        : await api('/search', { signal, method: 'POST', body: JSON.stringify({ ...Object.fromEntries(params), limit: 100, offset, sourceIds }) });
      if (generation !== requestId.current || criteria !== latestCriteria.current) return;
      setResults(previous => offset ? [...previous, ...result.results.map(normalizeDocument)] : result.results.map(normalizeDocument));
      setTotal(result.total);
      setError('');
    } catch (problem) {
      if (problem.name !== 'AbortError' && generation === requestId.current && criteria === latestCriteria.current) setError(connectionMessage(problem));
    } finally { if (generation === requestId.current && criteria === latestCriteria.current) setLoading(false); }
  }, [key]);

  useEffect(() => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    const generation = ++requestId.current;
    const changed = previousCriteria.current !== criteria;
    previousCriteria.current = criteria;
    // Saving a favorite/selection refreshes search without removing the card
    // under the pointer. A changed query or scope still clears obsolete hits.
    if (changed) { setResults([]); setTotal(0); }
    setError('');
    if (!query.trim()) { setLoading(false); return () => current.abort(); }
    if (changed) setLoading(true);
    const timer = setTimeout(() => request(0, current.signal, generation), 250);
    return () => { clearTimeout(timer); current.abort(); };
  }, [key, request]);

  const loadMore = () => {
    if (!criteriaCurrent || !query.trim() || loading || controller.current?.signal.aborted || results.length >= total) return;
    setLoading(true);
    request(results.length, controller.current.signal, requestId.current);
  };
  return { results: criteriaCurrent ? results : [], total: criteriaCurrent ? total : 0,
    loading: Boolean(query.trim()) && (!criteriaCurrent || loading), error: criteriaCurrent ? error : '',
    loadMore, retry: () => setRetryKey(value => value + 1) };
}
