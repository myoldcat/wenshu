import React, { useEffect, useState } from 'react';
import { api, connectionMessage } from './local-api';
import './presets.css';

export default function PresetLibrary() {
  const [catalog, setCatalog] = useState(null), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    api('/presets').then(value => {
      if (!current) return;
      setCatalog(value); setError('');
    }).catch(problem => { if (current) setError(connectionMessage(problem)); });
    return () => { current = false; };
  }, [retry]);
  const items = Array.isArray(catalog?.items) ? catalog.items : [];
  const selected = items.find(item => item.id === 'wenshu-user-guide');
  return <div className="preset-library">
    {error && <div className="local-library-message is-error" role="alert"><span>{error}</span><button type="button" className="btn quiet" onClick={() => setRetry(value => value + 1)}>重新载入</button></div>}
    <section className="preset-document" aria-label="使用指南阅读">
      {selected ? <>
        <iframe className="preset-document-frame" title={selected.title} src={selected.viewUrl || '/api/presets/wenshu-user-guide/view'} sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"/>
      </> : !catalog && !error ? <p className="preset-empty" role="status">正在读取使用指南…</p> : catalog ? <p className="preset-empty" role="status">使用指南暂不可用，请检查帮助资源。</p> : null}
    </section>
  </div>;
}
