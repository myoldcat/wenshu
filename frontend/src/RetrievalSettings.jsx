import React, { useEffect, useRef, useState } from 'react';
import { Check, ArrowLeft } from '@phosphor-icons/react';
import { api } from './local-api';
import { writingMessage } from './writing-messages';
import { RETRIEVAL_CHANNELS } from './writing-materials';
import './writing-materials.css';

export default function RetrievalSettings({ models = [], onClose, onSaved, onBusyChange }) {
  const [draft, setDraft] = useState(null), [busy, setBusy] = useState('loading'), [error, setError] = useState(''), [feedback, setFeedback] = useState('');
  const alive = useRef(false), request = useRef(0);
  const load = async () => {
    const token = ++request.current; setBusy('loading'); setError('');
    try { const result = await api('/retrieval/settings'); if (alive.current && token === request.current) setDraft(result.settings || result); }
    catch (problem) { if (alive.current && token === request.current) setError(writingMessage(problem)); }
    finally { if (alive.current && token === request.current) setBusy(''); }
  };
  useEffect(() => { alive.current = true; load(); return () => { alive.current = false; request.current += 1; }; }, []);
  useEffect(() => { onBusyChange?.(!!busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  const change = changes => { setDraft(previous => ({ ...previous, ...changes })); setFeedback(''); };
  async function save(event) {
    event.preventDefault(); if (!draft || busy) return;
    if (Number(draft.candidateLimit) < Number(draft.resultLimit)) { setError('候选文章上限不能少于推荐文章数。'); return; }
    if (!Object.values(draft.channels || {}).some(Boolean)) { setError('至少启用一种检索方式。'); return; }
    if (draft.channels.vector && !draft.embeddingModelId) { setError('请为语义检索选择合适的模型，或先关闭此查找方式。'); return; }
    setBusy('saving'); setError(''); setFeedback(''); const token = ++request.current;
    const body = { ...draft, rerankEnabled: draft.rerankEnabled !== false, resultLimit: Number(draft.resultLimit), passagesPerSource: Number(draft.passagesPerSource), candidateLimit: Number(draft.candidateLimit), graphHops: Number(draft.graphHops), minimumScore: Number(draft.minimumScore) };
    try {
      const result = await api('/retrieval/settings', { method: 'PUT', body: JSON.stringify(body) });
      if (!alive.current || token !== request.current) return;
      setDraft(result.settings || result); setFeedback('推荐设置已保存，下次推荐时生效。'); onSaved?.(result.settings || result);
    } catch (problem) { if (alive.current && token === request.current) setError(writingMessage(problem)); }
    finally { if (alive.current && token === request.current) setBusy(''); }
  }
  return <form className="retrieval-settings" onSubmit={save}>
    <div className="settings-detail-body retrieval-settings-body">
      {onClose && <button type="button" className="writing-inline-link" disabled={!!busy} onClick={onClose}><ArrowLeft size={15}/>返回材料确认</button>}
      <p className="retrieval-description">按写作主题在知识库内综合检索。推荐结果由你调整后确认。</p>
      {!draft && !error && <p role="status">正在读取推荐设置…</p>}
      {draft && <fieldset disabled={!!busy}>
        <div className="retrieval-number-fields">
          <label>推荐文章数<input aria-label="推荐文章数" type="number" required min="1" max="100" value={draft.resultLimit} onChange={event => change({ resultLimit: event.target.value })}/></label>
          <label>每篇推荐段落<input aria-label="每篇推荐段落" type="number" required min="1" max="5" value={draft.passagesPerSource} onChange={event => change({ passagesPerSource: event.target.value })}/></label>
          <label>候选文章上限<input aria-label="候选文章上限" type="number" required min="20" max="1000" value={draft.candidateLimit} onChange={event => change({ candidateLimit: event.target.value })}/></label>
          <label>最低相关分<input aria-label="最低相关分" type="number" required min="0" max="1" step="0.01" value={draft.minimumScore} onChange={event => change({ minimumScore: event.target.value })}/></label>
        </div>
        <label className="retrieval-toggle"><input type="checkbox" checked={!!draft.queryExpansion} onChange={event => change({ queryExpansion: event.target.checked })}/><span>自动补充查找角度<small>根据主题与写作要求生成多个检索角度，可在推荐后修改。</small></span></label>
        <label className="retrieval-toggle"><input type="checkbox" aria-label="增强排序" aria-describedby="retrieval-rerank-description" checked={draft.rerankEnabled !== false} onChange={event => change({ rerankEnabled: event.target.checked })}/><span>增强排序<small id="retrieval-rerank-description">进一步按主题相关性排序，等待时间会增加。</small></span></label>
        <div className="retrieval-channel-settings" role="group" aria-label="检索方式">{Object.entries(RETRIEVAL_CHANNELS).map(([key, label]) => <label key={key}><input type="checkbox" checked={!!draft.channels?.[key]} onChange={event => change({ channels: { ...draft.channels, [key]: event.target.checked } })}/>{label}</label>)}</div>
        <label className="retrieval-field">语义检索模型<select aria-label="语义检索模型" value={draft.embeddingModelId || ''} onChange={event => change({ embeddingModelId: event.target.value || null })}><option value="">未配置</option>{models.map(model => <option value={model.id} key={model.id}>{model.name} · {model.model}</option>)}</select><small>用于查找表达不同但含义相近的材料。需要在系统设置中配置支持此功能的模型。</small></label>
        <label className="retrieval-field">图谱关联层数<select aria-label="图谱关联层数" value={draft.graphHops} onChange={event => change({ graphHops: Number(event.target.value) })}><option value={0}>仅直接关联</option><option value={1}>扩展一层</option><option value={2}>扩展两层</option></select></label>
      </fieldset>}
      {error && <p className="writing-candidate-error" role="alert">{error}</p>}{!draft && error && <button type="button" className="btn" onClick={load}>重新读取</button>}{feedback && <p className="retrieval-description" role="status">{feedback}</p>}
    </div>
    <footer className="settings-detail-footer"><button type="submit" className="btn primary" disabled={!!busy || !draft}><Check size={15}/>{busy === 'saving' ? '正在保存…' : '保存推荐设置'}</button></footer>
  </form>;
}
