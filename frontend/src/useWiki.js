import { useCallback, useEffect, useRef, useState } from 'react';
import { api, connectionMessage } from './local-api';
import { mergeGraphPages } from './graph-state';
import { wikiRoot } from './wiki-unified-state';

function useLibraryRevision() {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const changed = () => setRevision(value => value + 1);
    window.addEventListener('wenshu-library-changed', changed);
    return () => window.removeEventListener('wenshu-library-changed', changed);
  }, []);
  return revision;
}

// A result is only exposed for the request identity that produced it. Aborting
// alone is insufficient when a response has already completed during a switch.
function useWikiResource(path, active, { poll = false, delay = 0, refreshKey = '', requestBody = null } = {}) {
  const libraryRevision = useLibraryRevision();
  const [state, setState] = useState({ key: '', data: null, loading: false, error: '' });
  const sequence = useRef(0);
  const key = path ? `${path}|${requestBody ?? ''}|${refreshKey}|${libraryRevision}` : '';
  const currentKey = useRef(key);
  currentKey.current = key;
  useEffect(() => {
    const current = ++sequence.current;
    if (!key || !active) return;
    const controller = new AbortController();
    let timer;
    const valid = () => !controller.signal.aborted && sequence.current === current && currentKey.current === key;
    setState(previous => ({ key, data: previous.key === key ? previous.data : null, loading: true, error: '' }));
    const tick = async () => {
      try {
        const data = await api(path, { signal: controller.signal, ...(requestBody === null ? {} : { method: 'POST', body: requestBody }) });
        if (valid()) setState({ key, data, loading: false, error: '' });
      } catch (error) {
        if (valid() && error.name !== 'AbortError') setState(previous => ({ key, data: previous.key === key ? previous.data : null, loading: false, error: connectionMessage(error) }));
      }
      if (valid() && poll) timer = setTimeout(tick, document.hidden ? 10000 : 3500);
    };
    if (delay) timer = setTimeout(tick, delay);
    else tick();
    return () => { sequence.current += 1; controller.abort(); clearTimeout(timer); };
  }, [key, path, active, poll, delay, requestBody]);
  return state.key === key && key ? { ...state, loading: active && state.loading } : { data: null, loading: active && !!key, error: '' };
}

// The service checks the entire requested scope before applying the batch cap.
// Previewing is read-only and never resolves a model or queues a build.
export function useWikiBuildScope(sourceIds, active, refreshKey = '') {
  const [revision, setRevision] = useState(0);
  const session = useRef({ active: false, revision: 0 });
  if (session.current.active !== active) session.current = { active, revision: session.current.revision + 1 };
  const resource = useWikiResource('/wiki/build-scope', active, {
    poll: true, requestBody: JSON.stringify({ sourceIds }), refreshKey: `${refreshKey}|${revision}|${session.current.revision}`,
  });
  return { ...resource, refresh: () => setRevision(value => value + 1) };
}

export function useWikiBuildAll(active) {
  const [revision, setRevision] = useState(0);
  const session = useRef({ active: false, revision: 0 });
  if (session.current.active !== active) session.current = { active, revision: session.current.revision + 1 };
  const resource = useWikiResource('/wiki/build-all', active, {
    poll: true, refreshKey: `${revision}|${session.current.revision}`,
  });
  return { ...resource, refresh: () => setRevision(value => value + 1) };
}

export function useWikiGraph(topicId, active, mode = 'unified', actionContext = '') {
  const [refreshKey, setRefreshKey] = useState(0);
  const [busy, setBusy] = useState('');
  const [actionError, setActionError] = useState(null);
  const currentActionContext = useRef(actionContext);
  currentActionContext.current = actionContext;
  const mounted = useRef(false);
  const busyRef = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const graph = useWikiResource(`${wikiRoot(mode)}?limit=100${topicId ? `&topic_id=${encodeURIComponent(topicId)}` : ''}`, active, { poll: true, refreshKey });
  const refresh = useCallback(() => { setActionError(null); setRefreshKey(value => value + 1); }, []);
  const mutate = async (name, path, body) => {
    if (busyRef.current) return null;
    const context = currentActionContext.current;
    busyRef.current = true;
    setBusy(name); setActionError(null);
    try {
      const result = await api(path, { method: 'POST', body: JSON.stringify(body) });
      if (mounted.current) refresh();
      return result;
    } catch (error) {
      if (mounted.current && currentActionContext.current === context) setActionError({ context, message: connectionMessage(error) });
      return null;
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy('');
    }
  };
  return {
    ...graph, busy, actionError: actionError?.context === actionContext ? actionError.message : '', refresh,
    build: (sourceIds, force = false) => mutate('build', '/wiki/build', { sourceIds, force }),
    buildAll: sourceIds => mutate('build-all', '/wiki/build-all', { sourceIds }),
    jobAction: (jobId, action) => mutate(jobId, `/jobs/${encodeURIComponent(jobId)}/${action}`, {}),
  };
}

export function useWikiPage(entryId, version, active, refreshKey = '', mode = 'unified', topicId = '') {
  const params = new URLSearchParams();
  if (version) params.set('version', version);
  if (topicId && mode !== 'source') params.set('topic_id', topicId);
  return useWikiResource(entryId ? `${wikiRoot(mode)}/pages/${encodeURIComponent(entryId)}${params.size ? `?${params}` : ''}` : '', active, { refreshKey });
}

export function useWikiSearch(query, active, refreshKey = '', hops = 1, mode = 'unified', topicId = '') {
  const trimmed = query.trim();
  return useWikiResource(trimmed ? `${wikiRoot(mode)}/search?q=${encodeURIComponent(trimmed)}&limit=20&hops=${hops}${mode !== 'source' && topicId ? `&topic_id=${encodeURIComponent(topicId)}` : ''}` : '', active, { delay: 300, refreshKey });
}

function mergeGroupPages(previous, incoming) {
  return { ...previous, ...incoming, members: [...new Map([
    ...(previous?.members || []), ...(incoming.members || []),
  ].map(member => [member.id, member])).values()] };
}

// Pagination has its own request identity. A late next-page response must never
// append to a newly selected focus, group or hop count.
function useWikiPagedResource(path, active, mergePages, externalKey = '') {
  const libraryRevision = useLibraryRevision();
  const resourceKey = path ? `${path}|${externalKey}|${libraryRevision}` : '';
  const [state, setState] = useState({ key: '', data: null, loading: false, error: '', notice: '', unavailable: false });
  const stateRef = useRef(state), keyRef = useRef(resourceKey), activeRef = useRef(active);
  stateRef.current = state; keyRef.current = resourceKey; activeRef.current = active;
  const requestRef = useRef(null), sequence = useRef(0);
  const [refreshKey, setRefreshKey] = useState(0);

  const request = useCallback(async (offset, reset) => {
    if (!path || !activeRef.current || requestRef.current) return;
    const controller = new AbortController(), requestId = ++sequence.current;
    requestRef.current = controller;
    const valid = () => !controller.signal.aborted && keyRef.current === resourceKey && activeRef.current && sequence.current === requestId;
    const previous = stateRef.current.key === resourceKey ? stateRef.current.data : null;
    setState(current => ({ key: resourceKey, data: reset ? null : (current.key === resourceKey ? current.data : null), loading: true, error: '', notice: reset ? '' : current.notice, unavailable: false }));
    try {
      const separator = path.includes('?') ? '&' : '?';
      const snapshot = !reset && previous?.snapshotId ? `&snapshot_id=${encodeURIComponent(previous.snapshotId)}` : '';
      let data = await api(`${path}${separator}offset=${offset}${snapshot}`, { signal: controller.signal });
      let restarted = false;
      if (data.reloadRequired && data.reason !== 'focus_unavailable' && valid()) {
        // Do not mix revisions. Preserve camera/positions in the caller but
        // replace the accumulated semantic graph with the new first page.
        restarted = true;
        data = await api(`${path}${separator}offset=0`, { signal: controller.signal });
      }
      if (valid()) {
        const next = data.reloadRequired
          ? { key: resourceKey, data: null, loading: false, error: '当前词条已不在最新图谱中，请返回主题图查看最新结果。', notice: '', unavailable: data.reason === 'focus_unavailable' }
          : { key: resourceKey, data: mergePages(reset || restarted ? null : previous, data), loading: false, error: '', notice: restarted ? '图谱已更新，已重新载入第一页；视角保持不变。' : '', unavailable: false };
        stateRef.current = next;
        setState(next);
      }
    } catch (error) {
      if (valid() && error.name !== 'AbortError') setState(current => ({ ...current, loading: false, error: connectionMessage(error) }));
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
    }
  }, [path, resourceKey, mergePages]);

  useEffect(() => {
    sequence.current += 1;
    requestRef.current?.abort(); requestRef.current = null;
    if (path && active) request(0, true);
    return () => { sequence.current += 1; requestRef.current?.abort(); requestRef.current = null; };
  }, [path, active, refreshKey, request]);

  const loadMore = useCallback(() => {
    const current = stateRef.current;
    if (current.key !== resourceKey || !current.data?.hasMore || current.data.nextOffset == null) return;
    request(current.data.nextOffset, false);
  }, [resourceKey, request]);
  return {
    ...(state.key === resourceKey && path ? state : { data: null, loading: active && !!path, error: '', notice: '', unavailable: false }),
    loadMore, refresh: () => setRefreshKey(value => value + 1),
  };
}

export function useWikiNeighborhood(entryId, hops, active, mode = 'unified', topicId = '', graphSnapshot = '') {
  return useWikiPagedResource(entryId ? `${wikiRoot(mode)}/neighborhood?entry_id=${encodeURIComponent(entryId)}&hops=${hops}&limit=100${mode !== 'source' && topicId ? `&topic_id=${encodeURIComponent(topicId)}` : ''}` : '', active, mergeGraphPages, graphSnapshot);
}

export function useWikiGroup(groupId, active) {
  return useWikiPagedResource(groupId ? `/wiki/groups/${encodeURIComponent(groupId)}?limit=20` : '', active, mergeGroupPages);
}

// Opening the review panel only reads proposals. Detection and all decisions
// require a separate user action; no model task is queued by an effect.
export function useWikiResolution(active) {
  const [refreshKey, setRefreshKey] = useState(0);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [conflict, setConflict] = useState(false);
  const busyRef = useRef(false), mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const resource = useWikiResource('/wiki/resolution/proposals', active, { refreshKey, poll: true });
  const refresh = () => { setConflict(false); setError(''); setRefreshKey(value => value + 1); };
  const mutate = async (name, path, body) => {
    if (busyRef.current || (conflict && ['merge', 'reject', 'undo'].includes(name))) return null;
    busyRef.current = true; setBusy(name); setError('');
    try {
      const result = await api(path, { method: 'POST', body: JSON.stringify(body) });
      if (mounted.current) refresh();
      return result;
    } catch (failure) {
      if (mounted.current) {
        setConflict(failure.status === 409);
        setError(failure.status === 409 ? '知识快照已更新。已保留本地选择和填写内容，请刷新候选并重新核对后再提交。' : connectionMessage(failure));
      }
      return null;
    } finally { busyRef.current = false; if (mounted.current) setBusy(''); }
  };
  return { ...resource, busy, actionError: error, conflict, refresh,
    resolve: sourceIds => sourceIds.length > 0 && sourceIds.length <= 10 ? mutate('resolve', '/wiki/resolve', { sourceIds }) : null,
    decide: (action, body) => body ? mutate(action, `/wiki/knowledge/${action}`, body) : null,
    jobAction: (jobId, action) => mutate(jobId, `/jobs/${encodeURIComponent(jobId)}/${action}`, {}),
  };
}
