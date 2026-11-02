import React, { useEffect, useState } from 'react';
import { ArrowClockwise } from '@phosphor-icons/react';
import { connectionMessage } from './local-api';

export function OriginalFilePreview({ url, title }) {
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState('checking');
  const [error, setError] = useState('');
  const [html, setHtml] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setStatus('checking');
    setError('');
    setHtml('');
    const timeout = setTimeout(() => { setError('读取原始文件超时，请重新读取。'); setStatus('error'); controller.abort(); }, 30000);
    fetch(url, { signal: controller.signal, credentials: 'same-origin' })
      .then(async response => {
        if (!response.ok) throw new Error(response.status === 404 ? '未找到这份资料的原始文件或版本。' : `原始文件暂时无法读取（${response.status}）。`);
        if (!response.headers.get('content-type')?.includes('text/html')) throw new Error('这份文件暂不支持窗口内预览。');
        const content = await response.text();
        if (!controller.signal.aborted) { setHtml(content); setStatus('loading'); }
      })
      .catch(reason => { if (!controller.signal.aborted) { setError(connectionMessage(reason)); setStatus('error'); } })
      .finally(() => clearTimeout(timeout));
    return () => { clearTimeout(timeout); controller.abort(); };
  }, [url, attempt]);

  useEffect(() => {
    if (status !== 'loading') return;
    const timeout = setTimeout(() => { setError('文件显示较慢，可重新读取后再试。'); setStatus('error'); }, 15000);
    return () => clearTimeout(timeout);
  }, [status]);

  return <div className="reader-preview-frame">
    <div className="reader-preview-note"><span>本地原始快照 · 保留已保存的排版与图片</span><button type="button" className="inline-link" onClick={() => setAttempt(value => value + 1)} aria-label="重新载入原始文件"><ArrowClockwise size={14}/>重新读取</button></div>
    {status === 'error' ? <div className="reader-preview-message" role="alert"><p>{error}</p><button type="button" className="btn quiet" onClick={() => setAttempt(value => value + 1)}><ArrowClockwise size={16}/>重新读取</button></div> : <>
      {status !== 'ready' && <p className="reader-preview-message" role="status">正在打开原始文件…</p>}
      {status !== 'checking' && <iframe key={`${url}:${attempt}`} srcDoc={html} title={`${title} · 原始文件预览`} sandbox="" referrerPolicy="no-referrer" onLoad={() => setStatus('ready')}/>}
    </>}
  </div>;
}
