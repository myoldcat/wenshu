import { useCallback, useEffect, useRef, useState } from 'react';
import { api, connectionMessage } from './local-api';

const emptyPreferences = { defaultModelId: null, summaryModelId: null, translationModelId: null, classificationModelId: null, autoTranslate: true, autoProcess: false, idleProcess: false, idleStart: '00:00', idleEnd: '08:00' };
const blankModel = () => ({ id: `draft-${Date.now()}`, name: '', baseUrl: 'http://127.0.0.1:11434/v1', model: '', apiKey: '', contextChars: 24000, maxOutputTokens: 2048, timeoutSeconds: 90, reasoningEffort: '', jsonMode: false, draft: true });
export function useModelSettings() {
  const [models, setModels] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [preferences, setPreferences] = useState(emptyPreferences);
  const [preferenceDraft, setPreferenceDraft] = useState(emptyPreferences);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api('/models');
      setModels(result.models || []);
      setDrafts((result.models || []).map(item => ({ ...item, apiKey: '' })));
      setSelectedId(current => result.models?.some(item => item.id === current) ? current : result.models?.[0]?.id || '');
      setPreferences({ ...emptyPreferences, ...result.preferences });
      setPreferenceDraft({ ...emptyPreferences, ...result.preferences });
      setError('');
    } catch (problem) { setError(connectionMessage(problem)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const clearFeedback = () => { setError(''); setFeedback(''); };
  const addModel = () => { const item = blankModel(); setDrafts(items => [...items.filter(model => !model.draft), item]); setSelectedId(item.id); clearFeedback(); };
  const discardModel = () => {
    const saved = models.find(item => item.id === selectedId);
    setDrafts(items => items.filter(item => !item.draft).map(item => item.id === selectedId && saved ? { ...saved, apiKey: '' } : item));
    if (!saved) setSelectedId(models[0]?.id || '');
    clearFeedback();
  };
  const changeModel = (id, changes) => setDrafts(items => items.map(item => item.id === id ? { ...item, ...changes } : item));
  const saveModel = async () => {
    const draft = drafts.find(item => item.id === selectedId);
    if (!draft) return;
    setBusy('save'); setError(''); setFeedback('');
    try {
      const body = { name: draft.name.trim(), baseUrl: draft.baseUrl.trim(), model: draft.model.trim(), contextChars: Number(draft.contextChars), maxOutputTokens: Number(draft.maxOutputTokens), timeoutSeconds: Number(draft.timeoutSeconds), reasoningEffort: draft.reasoningEffort || '', jsonMode: !!draft.jsonMode };
      if (draft.apiKey) body.apiKey = draft.apiKey;
      if (draft.clearApiKey) body.clearApiKey = true;
      const result = await api(draft.draft ? '/models' : `/models/${encodeURIComponent(draft.id)}`, { method: draft.draft ? 'POST' : 'PUT', body: JSON.stringify(body) });
      setModels(items => draft.draft ? [...items, result.model] : items.map(item => item.id === draft.id ? result.model : item));
      setDrafts(items => items.map(item => item.id === draft.id ? { ...result.model, apiKey: '' } : item));
      setSelectedId(result.model.id);
      setFeedback('模型配置已保存。');
      return result.model;
    } catch (problem) { setError(connectionMessage(problem)); }
    finally { setBusy(''); }
  };
  const testModel = async () => {
    if (!selectedId) return;
    setBusy('test'); setError(''); setFeedback('');
    try {
      const response = await api(`/models/${encodeURIComponent(selectedId)}/test`, { method: 'POST', body: '{}' });
      const result = response.result;
      setModels(items => items.map(item => item.id === selectedId ? { ...item, lastTest: result } : item));
      setDrafts(items => items.map(item => item.id === selectedId ? { ...item, lastTest: result } : item));
      if (result.embeddings) setFeedback(`向量连接成功 · ${result.dimensions} 维 · ${Math.round(result.durationMs || 0)} ms`);
      else if (result.textGeneration) setFeedback(`连接成功 · ${Math.round(result.durationMs || 0)} ms${result.jsonParseable ? ' · JSON 解析通过' : ' · 未返回有效 JSON'}`);
      else setError(`连接失败：${result.error || '模型未返回有效内容'}`);
    } catch (problem) { setError(connectionMessage(problem)); }
    finally { setBusy(''); }
  };
  const savePreferences = async (changes = preferenceDraft) => {
    setBusy('preferences'); setError(''); setFeedback('');
    try {
      const fields = ['defaultModelId', 'autoTranslate', 'autoProcess', 'idleProcess', 'idleStart', 'idleEnd'];
      const result = await api('/ai/preferences', { method: 'PUT', body: JSON.stringify(Object.fromEntries(fields.filter(key => Object.hasOwn(changes, key)).map(key => [key, changes[key]]))) });
      setPreferences({ ...emptyPreferences, ...result.preferences });
      setPreferenceDraft({ ...emptyPreferences, ...result.preferences });
      setFeedback('设置已保存。');
      return true;
    } catch (problem) { setError(connectionMessage(problem)); }
    finally { setBusy(''); }
  };
  const deleteModel = async () => {
    if (!selectedId) return false;
    setBusy('delete'); clearFeedback();
    try {
      const result = await api(`/models/${encodeURIComponent(selectedId)}`, { method: 'DELETE' });
      setModels(result.models);
      setDrafts(result.models.map(item => ({ ...item, apiKey: '' })));
      setSelectedId(result.models[0]?.id || '');
      setPreferences({ ...emptyPreferences, ...result.preferences });
      setPreferenceDraft({ ...emptyPreferences, ...result.preferences });
      setFeedback('模型已删除。');
      return true;
    } catch (problem) { setError(connectionMessage(problem)); return false; }
    finally { setBusy(''); }
  };
  return { models, drafts, selectedId, setSelectedId, preferences, preferenceDraft, setPreferenceDraft, loading, busy, error, feedback, refresh, addModel, discardModel, clearFeedback, changeModel, saveModel, testModel, savePreferences, deleteModel };
}

export function useDocumentAI(sourceId, revisionId, onUpdate) {
  const [ai, setAI] = useState(null);
  const [aiKey, setAIKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const generation = useRef(0);
  const identity = `${sourceId || ''}:${revisionId || ''}`;
  const activeIdentity = useRef(identity);
  activeIdentity.current = identity;
  const updateRef = useRef(onUpdate);
  updateRef.current = onUpdate;
  const fingerprint = useRef('');
  const refresh = useCallback(async ({ signal, quiet = false } = {}) => {
    if (!sourceId || activeIdentity.current !== identity) return;
    const current = generation.current;
    if (!quiet) setLoading(true);
    try {
      const suffix = revisionId ? `?revision_id=${encodeURIComponent(revisionId)}` : '';
      const response = await api(`/documents/${encodeURIComponent(sourceId)}/ai${suffix}`, { signal });
      if (current !== generation.current || activeIdentity.current !== identity || signal?.aborted) return;
      const result = response.ai;
      setAI(result); setAIKey(identity); setError('');
      const next = JSON.stringify(['summary','translation','classification'].map(kind => [result?.[kind]?.runId, result?.[kind]?.status, result?.[kind]?.updatedAt]));
      if (fingerprint.current && fingerprint.current !== next) updateRef.current?.();
      fingerprint.current = next;
    } catch (problem) { if (current === generation.current && activeIdentity.current === identity && problem.name !== 'AbortError') setError(connectionMessage(problem)); }
    finally { if (current === generation.current && activeIdentity.current === identity && !signal?.aborted) setLoading(false); }
  }, [sourceId, revisionId, identity]);
  useEffect(() => {
    generation.current += 1; setAI(null); setError(''); setBusy(''); fingerprint.current = '';
    if (!sourceId) { setLoading(false); return; }
    const controller = new AbortController(); let timer;
    const tick = async (initial = false) => { await refresh({ signal: controller.signal, quiet: !initial }); if (!controller.signal.aborted) timer = setTimeout(tick, document.hidden ? 8000 : 2500); };
    tick(true);
    return () => { generation.current += 1; controller.abort(); clearTimeout(timer); };
  }, [refresh, sourceId]);
  const submit = async (kind, force = false) => {
    const current = generation.current; setBusy(kind); setError('');
    try {
      await api(`/documents/${encodeURIComponent(sourceId)}/ai`, { method: 'POST', body: JSON.stringify({ kinds: [kind], force, ...(revisionId ? { revisionId } : {}) }) });
      if (current === generation.current) { await refresh({ quiet: true }); updateRef.current?.(); }
    } catch (problem) { if (current === generation.current) setError(connectionMessage(problem)); }
    finally { if (current === generation.current) setBusy(''); }
  };
  const jobAction = async (jobId, action) => {
    const current = generation.current; setBusy(jobId); setError('');
    try { await api(`/jobs/${encodeURIComponent(jobId)}/${action}`, { method: 'POST', body: '{}' }); if (current === generation.current) { await refresh({ quiet: true }); updateRef.current?.(); } }
    catch (problem) { if (current === generation.current) setError(connectionMessage(problem)); }
    finally { if (current === generation.current) setBusy(''); }
  };
  const editTranslation = async (blockId, text, locked = true) => {
    const current = generation.current; setBusy(blockId); setError('');
    try {
      await api(`/documents/${encodeURIComponent(sourceId)}/ai/translation`, { method: 'PATCH', body: JSON.stringify({ blockId, text, locked, ...(revisionId ? { revisionId } : {}) }) });
      if (current !== generation.current) return false;
      await refresh({ quiet: true }); updateRef.current?.(); return true;
    }
    catch (problem) { if (current === generation.current) setError(connectionMessage(problem)); return false; }
    finally { if (current === generation.current) setBusy(''); }
  };
  return { ai: aiKey === identity ? ai : null, loading, error, busy, refresh, submit, jobAction, editTranslation };
}
