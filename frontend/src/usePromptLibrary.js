import { useCallback, useEffect, useRef, useState } from 'react';
import { api, connectionMessage } from './local-api';
import { applyPromptSave, createPromptDraft, mergePromptCatalog, planPromptDraftAdoption, promptDraftChanged, rebasePromptDraft, refreshCleanPromptDrafts } from './prompt-library-state';

export function usePromptLibrary(visible = true) {
  const [items, setItems] = useState([]);
  const [categories, setCategories] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [errors, setErrors] = useState({});
  const [conflicts, setConflicts] = useState({});
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [savingId, setSavingId] = useState(null);
  const [reloadingId, setReloadingId] = useState(null);
  const mounted = useRef(false);
  const loaded = useRef(false);
  const saving = useRef(false);
  const loadSequence = useRef(0);
  const conflictSequence = useRef(0);
  // Reuse this existing lifetime ref so a hot update does not change this
  // hook's order and discard the very drafts being handed off.
  if (!mounted.promptTransfers) mounted.promptTransfers = new Map();
  mounted.promptTransferState = { drafts, conflicts, errors, savingId };

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; loadSequence.current += 1; conflictSequence.current += 1; for (const request of mounted.promptTransfers.values()) request.resolve(false); mounted.promptTransfers.clear(); }; }, []);

  const refresh = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoading(true); setLoadError('');
    try {
      const result = await api('/prompts');
      if (!mounted.current || sequence !== loadSequence.current) return;
      if (!Array.isArray(result.items) || !Array.isArray(result.categories)) throw new Error('智能指令目录格式无效，请重试。');
      setItems(current => mergePromptCatalog(current, result.items));
      setCategories(result.categories);
      loaded.current = true;
    } catch (error) {
      if (mounted.current && sequence === loadSequence.current) setLoadError(connectionMessage(error));
    } finally {
      if (mounted.current && sequence === loadSequence.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible && !loaded.current && !loading && !loadError) refresh();
    const transferBlocks = Object.fromEntries([...mounted.promptTransfers.keys()].map(id => [id, true]));
    setDrafts(current => refreshCleanPromptDrafts(current, items, { ...conflicts, ...transferBlocks }));
    for (const [id, request] of mounted.promptTransfers) {
      if (drafts[id] !== request.payload.draft) { request.resolve(false); mounted.promptTransfers.delete(id); continue; }
      const incomingConflict = request.payload.conflict || null;
      const incomingError = request.payload.error || '';
      if (conflicts[id] && conflicts[id] !== incomingConflict) { request.resolve(false); mounted.promptTransfers.delete(id); continue; }
      if ((conflicts[id] || null) !== incomingConflict) setConflicts(current => current[id] && current[id] !== incomingConflict ? current : { ...current, [id]: incomingConflict });
      if ((errors[id] || '') !== incomingError) setErrors(current => ({ ...current, [id]: incomingError }));
      if ((conflicts[id] || null) === incomingConflict && (errors[id] || '') === incomingError) { request.resolve(true); mounted.promptTransfers.delete(id); }
    }
  }, [visible, refresh, items, conflicts, drafts, errors]);
  const dirtyCount = Object.values(drafts).filter(promptDraftChanged).length;
  useEffect(() => {
    if (!dirtyCount && !savingId) return;
    const beforeUnload = event => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirtyCount, savingId]);

  const clearItemError = id => setErrors(current => ({ ...current, [id]: '' }));
  const adoptDraftState = payload => {
    if (!mounted.current) return false;
    const state = mounted.promptTransferState;
    if (!planPromptDraftAdoption({ ...state, savingId: saving.current ? saving.promptId : state.savingId }, payload).accepted) return false;
    if (state.drafts[payload.id] === payload.draft && (state.conflicts[payload.id] || null) === (payload.conflict || null) && (state.errors[payload.id] || '') === (payload.error || '')) return true;
    const pending = mounted.promptTransfers.get(payload.id);
    if (pending) return pending.payload.draft === payload.draft ? pending.promise : false;
    const request = { payload };
    request.promise = new Promise(resolve => { request.resolve = resolve; });
    mounted.promptTransfers.set(payload.id, request);
    setDrafts(current => {
      const plan = planPromptDraftAdoption({ ...mounted.promptTransferState, drafts: current }, payload);
      if (!plan.accepted) {
        Promise.resolve().then(() => { request.resolve(false); if (mounted.promptTransfers.get(payload.id) === request) mounted.promptTransfers.delete(payload.id); });
        return current;
      }
      return plan.state.drafts;
    });
    return request.promise;
  };
  const releaseDraftState = payload => {
    const state = mounted.promptTransferState;
    if ((saving.current && saving.promptId === payload.id) || state.savingId === payload.id || state.drafts[payload.id] !== payload.draft || (state.conflicts[payload.id] || null) !== (payload.conflict || null) || (state.errors[payload.id] || '') !== (payload.error || '')) return false;
    setDrafts(current => { if (current[payload.id] !== payload.draft) return current; const next = { ...current }; delete next[payload.id]; return next; });
    setConflicts(current => { if ((current[payload.id] || null) !== (payload.conflict || null)) return current; const next = { ...current }; delete next[payload.id]; return next; });
    setErrors(current => { if ((current[payload.id] || '') !== (payload.error || '')) return current; const next = { ...current }; delete next[payload.id]; return next; });
    return true;
  };
  const startEdit = item => {
    setDrafts(current => current[item.id] ? current : { ...current, [item.id]: createPromptDraft(item) });
    clearItemError(item.id);
  };
  const edit = (id, content) => {
    if (savingId === id) return;
    setDrafts(current => current[id] ? { ...current, [id]: { ...current[id], content } } : current);
    clearItemError(id);
  };
  const discard = id => {
    if (savingId === id) return;
    setDrafts(current => { const next = { ...current }; delete next[id]; return next; });
    setConflicts(current => { const next = { ...current }; delete next[id]; return next; });
    clearItemError(id);
  };
  const save = async id => {
    const submitted = drafts[id];
    if (!submitted || saving.current || conflicts[id]) return null;
    if (!submitted.content.trim()) { setErrors(current => ({ ...current, [id]: '指令正文不能为空。' })); return null; }
    const item = items.find(value => value.id === id);
    const contentLimit = Number.isInteger(item?.maxContentLength) && item.maxContentLength > 0 ? item.maxContentLength : 24000;
    if (submitted.content.length > contentLimit) { setErrors(current => ({ ...current, [id]: `指令正文不能超过 ${contentLimit.toLocaleString()} 字符。` })); return null; }
    if (!promptDraftChanged(submitted)) { discard(id); return null; }
    saving.current = true; saving.promptId = id; setSavingId(id); clearItemError(id);
    try {
      const saved = await api(`/prompts/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ content: submitted.content, expectedVersion: submitted.version, contentView: submitted.contentView || 'source' }) });
      if (!mounted.current) return null;
      setItems(current => current.map(item => item.id === saved.id && saved.version >= item.version ? saved : item));
      setDrafts(current => applyPromptSave(current, submitted, saved));
      return saved;
    } catch (error) {
      if (mounted.current) {
        if (error.status === 409) setConflicts(current => ({ ...current, [id]: { latest: null } }));
        setErrors(current => ({ ...current, [id]: error.status === 409 ? '此指令已在其他页面修改。你的内容已保留，请加载最新版本后对照。' : connectionMessage(error) }));
      }
      return null;
    } finally {
      saving.current = false; saving.promptId = null;
      if (mounted.current) setSavingId(null);
    }
  };
  const reloadConflict = async id => {
    const sequence = ++conflictSequence.current;
    setReloadingId(id); clearItemError(id);
    try {
      const result = await api('/prompts');
      if (!mounted.current || sequence !== conflictSequence.current) return;
      const latest = result.items?.find(item => item.id === id);
      if (!latest) throw new Error('服务端未找到此指令，请刷新目录。你的修改仍保留在本页。');
      setItems(current => mergePromptCatalog(current, result.items));
      setConflicts(current => ({ ...current, [id]: { latest } }));
    } catch (error) {
      if (mounted.current && sequence === conflictSequence.current) setErrors(current => ({ ...current, [id]: connectionMessage(error) }));
    } finally {
      if (mounted.current && sequence === conflictSequence.current) setReloadingId(null);
    }
  };
  const continueFromLatest = id => {
    const latest = conflicts[id]?.latest;
    if (!latest) return;
    setDrafts(current => ({ ...current, [id]: rebasePromptDraft(current[id], latest) }));
    setConflicts(current => { const next = { ...current }; delete next[id]; return next; });
    clearItemError(id);
  };
  return { items, categories, drafts, errors, conflicts, loading, loadError, savingId, reloadingId, dirtyCount, refresh, startEdit, edit, discard, save, reloadConflict, continueFromLatest, adoptDraftState, releaseDraftState };
}
