import React, { useEffect, useRef, useState } from 'react';
import { Trash, WarningCircle, X } from '@phosphor-icons/react';
import { api, connectionMessage } from './local-api';
import { deletionConfirmation, referenceDescription } from './document-deletion.mjs';
import './document-deletion.css';

export function DocumentDeleteDialog({ document: target, onDelete, onDeleted, onClose }) {
  const [impact, setImpact] = useState(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const panel = useRef(null), live = useRef(true), pendingRef = useRef(false), close = useRef(onClose);
  close.current = onClose;
  const dismiss = () => { if (!pendingRef.current) close.current(); };

  useEffect(() => {
    live.current = true;
    const previous = document.activeElement;
    panel.current?.querySelector('button')?.focus();
    const keyboard = event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dismiss(); }
      if (event.key !== 'Tab') return;
      const items = [...panel.current.querySelectorAll('button:not([disabled]),input:not([disabled]),[tabindex="0"]')].filter(item => item.offsetParent !== null);
      if (!items.length) { event.preventDefault(); panel.current.focus(); return; }
      if (event.shiftKey && (!panel.current.contains(document.activeElement) || document.activeElement === items[0])) { event.preventDefault(); items.at(-1).focus(); }
      else if (!event.shiftKey && (!panel.current.contains(document.activeElement) || document.activeElement === items.at(-1))) { event.preventDefault(); items[0].focus(); }
    };
    document.addEventListener('keydown', keyboard, true);
    return () => { live.current = false; document.removeEventListener('keydown', keyboard, true); if (previous?.isConnected) previous.focus(); };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setImpact(null); setConfirmed(false); setError('');
    api(`/documents/${encodeURIComponent(target.id)}/deletion-impact`, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setImpact(result.impact); })
      .catch(problem => { if (!controller.signal.aborted) setError(connectionMessage(problem)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [target.id, retry]);

  const remove = async () => {
    const confirmation = deletionConfirmation(impact, confirmed);
    if (pendingRef.current || !confirmation) return;
    pendingRef.current = true; setPending(true); setError('');
    try {
      const result = await onDelete(target.id, confirmation);
      if (live.current) onDeleted(target.id, result);
    } catch (problem) {
      if (!live.current) return;
      if (problem.status === 409) {
        // A newer source or newly cited manuscript needs a fresh review/consent.
        setConfirmed(false);
        if (problem.detail?.impact) setImpact(problem.detail.impact);
        else setImpact(null);
      }
      setError(connectionMessage(problem));
    } finally { pendingRef.current = false; if (live.current) setPending(false); }
  };

  return <div className="dialog-backdrop document-delete-backdrop" onMouseDown={event => event.target === event.currentTarget && dismiss()}>
    <section className="dialog document-delete-dialog" role="alertdialog" aria-modal="true" aria-labelledby="document-delete-title" aria-describedby="document-delete-description" ref={panel} tabIndex={-1} aria-busy={loading || pending}>
      <header className="dialog-head"><div><h2 id="document-delete-title">删除文档</h2><p className="document-delete-name" title={impact?.title || target.title}>{impact?.title || target.title}</p></div><button className="icon-btn" aria-label="关闭删除文档" disabled={pending} onClick={dismiss}><X size={22}/></button></header>
      <div className="dialog-body document-delete-body">
        <p id="document-delete-description">将永久删除这份文档的全部版本，以及图片库、知识图谱、检索索引和加工结果中的相关内容。此操作无法撤销。</p>
        {loading && <p role="status">正在检查文字与图片引用…</p>}
        {impact && <>
          <p className="document-delete-counts">{impact.revisionCount} 个资料版本 · {impact.imageCount} 张图片</p>
          {impact.hasReferences && <div className="document-delete-warning"><strong><WarningCircle size={19}/>这份文档已被写作文稿引用或选用</strong><p>删除后文稿正文和已有引用记录会保留，原文及相关图片将无法查看。查看相应引用时会提示来源已删除。</p><ul aria-label="受影响的写作文稿">{impact.references.map(reference => <li key={reference.projectId}><span title={reference.title}>{reference.title || '未命名文稿'}{reference.deleted ? '（已删除文稿）' : ''}</span><small>{referenceDescription(reference)}</small></li>)}</ul></div>}
          {impact.activeJobs?.length > 0 && <div className="document-delete-warning"><strong>相关任务正在排队或运行</strong><p>请先取消相关任务或等待结束，再重新检查。</p><ul>{impact.activeJobs.map(job => <li key={job.id}>{job.label || '资料相关任务'}</li>)}</ul></div>}
          {impact.hasReferences && <label className="document-delete-consent"><input type="checkbox" checked={confirmed} disabled={pending} onChange={event => setConfirmed(event.target.checked)}/><span>我已知晓对上述文稿的文字及图片引用的影响，仍要删除。</span></label>}
        </>}
        {error && <p className="document-delete-error" role="alert">{error}</p>}
      </div>
      <footer className="dialog-footer"><button className="btn quiet" disabled={pending} onClick={dismiss}>取消</button><div>{!loading && (!impact || impact.canDelete !== true || error) && <button className="btn quiet" disabled={pending} onClick={() => setRetry(value => value + 1)}>重新检查</button>}<button className="btn document-delete-confirm" disabled={loading || pending || !deletionConfirmation(impact, confirmed)} onClick={remove}><Trash size={17}/>{pending ? '正在删除…' : '永久删除'}</button></div></footer>
    </section>
  </div>;
}
