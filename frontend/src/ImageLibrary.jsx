import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowClockwise, ArrowSquareOut, CaretLeft, CaretRight, Image as ImageIcon, MagnifyingGlass, X } from '@phosphor-icons/react';
import { api, connectionMessage } from './local-api';
import { useCompactGalleryHeader } from './useCompactGalleryHeader';
import './image-library.css';

const PAGE_SIZE = 60;
const imageKey = item => JSON.stringify([item.sourceId, item.revisionId, item.id]);
const titleOf = item => item.documentTitle || item.originalTitle || '未命名文章';
const captionOf = item => item.alt?.trim() || '暂无图片说明';

function AssetImage({ item, large = false }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <span className="image-library-broken"><ImageIcon size={30} /><span>图片暂时无法显示</span></span>;
  return <img src={item.url} alt={item.alt || ''} loading={large ? 'eager' : 'lazy'} decoding="async" onError={() => setFailed(true)} />;
}

function ImagePreview({ item, items, onClose, onChoose, onOpenDoc }) {
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  const index = items.findIndex(entry => imageKey(entry) === imageKey(item));
  useEffect(() => {
    const dialog = dialogRef.current;
    dialog.showModal();
    closeRef.current?.focus();
    return () => { if (dialog.open) dialog.close(); };
  }, []);
  const move = step => {
    const next = items[index + step];
    if (next) onChoose(imageKey(next));
  };
  const dismiss = () => { dialogRef.current?.close(); onClose(); };
  return createPortal(<dialog ref={dialogRef} className="image-library-preview" aria-labelledby="image-library-preview-title" aria-describedby="image-library-preview-caption"
    onCancel={event => { event.preventDefault(); dismiss(); }}
    onClick={event => { if (event.target === event.currentTarget) dismiss(); }}
    onKeyDown={event => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === 'ArrowLeft') { event.preventDefault(); move(-1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); move(1); }
    }}>
    <div className="image-library-preview-inner">
      <header><div><h2 id="image-library-preview-title">{titleOf(item)}</h2><p>{index + 1} / {items.length} 张已加载图片</p></div><button ref={closeRef} type="button" className="icon-btn" aria-label="关闭图片预览" onClick={dismiss}><X size={22} /></button></header>
      <div className="image-library-preview-stage">
        <button type="button" className="image-library-preview-direction" aria-label="上一张图片" disabled={index <= 0} onClick={() => move(-1)}><CaretLeft size={26} /></button>
        <div className="image-library-preview-picture"><AssetImage key={imageKey(item)} item={item} large /></div>
        <button type="button" className="image-library-preview-direction" aria-label="下一张图片" disabled={index >= items.length - 1} onClick={() => move(1)}><CaretRight size={26} /></button>
      </div>
      <footer><div><p id="image-library-preview-caption">{captionOf(item)}</p><small>{[item.source, item.date].filter(Boolean).join(' · ')}</small></div><button type="button" className="btn" onClick={() => { dismiss(); onOpenDoc?.({ id: item.sourceId, revisionId: item.revisionId }); }}><ArrowSquareOut size={17} />查看文章</button></footer>
    </div>
  </dialog>, document.body);
}

/** A read-only catalog of images from the current persisted article versions. */
export function ImageLibrary({ active = true, revision, onOpenDoc }) {
  const [query, setQuery] = useState('');
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState({ context: null, items: [], total: 0, documentCount: 0, nextOffset: 0, more: false });
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);
  const [pageError, setPageError] = useState('');
  const [previewKey, setPreviewKey] = useState(null);
  const viewportRef = useRef(null);
  const paginationRef = useRef(null);
  const autoLoadState = useRef(null);
  const requests = useRef({ generation: 0, controller: null, busy: false });
  const normalizedQuery = query.trim();
  const context = useMemo(() => ({}), [normalizedQuery, revision, retry]);
  const current = result.context === context;
  const currentError = error?.context === context ? error.message : '';
  const items = current ? result.items : [];
  const previewItem = active ? items.find(item => imageKey(item) === previewKey) : null;
  const compact = useCompactGalleryHeader(viewportRef, context, { progressive: true });
  autoLoadState.current = { context, nextOffset: result.nextOffset,
    allowed: active && current && !loading && !loadingMore && !pageError && result.more };

  useEffect(() => {
    const state = requests.current;
    state.generation += 1;
    state.controller?.abort();
    const generation = state.generation;
    state.busy = false;
    setPreviewKey(null);
    setLoadingMore(false);
    setPageError('');
    setError(null);
    if (!active) { setLoading(false); return; }
    setLoading(true);
    if (viewportRef.current) viewportRef.current.scrollTop = 0;
    const controller = new AbortController();
    state.controller = controller;
    // Coalesce typing while immediately masking results from the previous search.
    const timer = setTimeout(async () => {
      try {
        const data = await api(`/images?${new URLSearchParams({ q: normalizedQuery, limit: String(PAGE_SIZE), offset: '0' })}`, { signal: controller.signal });
        if (controller.signal.aborted || state.generation !== generation) return;
        const page = Array.isArray(data.items) ? data.items : [];
        const nextOffset = Number(data.offset || 0) + page.length;
        setResult({ context, items: page, total: data.total, documentCount: data.documentCount, nextOffset, more: page.length > 0 && nextOffset < data.total });
      } catch (cause) {
        if (!controller.signal.aborted && state.generation === generation) setError({ context, message: connectionMessage(cause) });
      } finally {
        if (!controller.signal.aborted && state.generation === generation) setLoading(false);
      }
    }, normalizedQuery ? 220 : 0);
    return () => { clearTimeout(timer); controller.abort(); if (state.generation === generation) state.controller?.abort(); };
  }, [active, context, normalizedQuery]);

  const loadMore = async () => {
    const state = requests.current;
    if (!active || !current || loading || state.busy || !result.more) return;
    state.busy = true;
    const generation = state.generation;
    const controller = new AbortController();
    state.controller = controller;
    setLoadingMore(true);
    setPageError('');
    try {
      const data = await api(`/images?${new URLSearchParams({ q: normalizedQuery, limit: String(PAGE_SIZE), offset: String(result.nextOffset) })}`, { signal: controller.signal });
      if (controller.signal.aborted || state.generation !== generation) return;
      const page = Array.isArray(data.items) ? data.items : [];
      const nextOffset = Number(data.offset ?? result.nextOffset) + page.length;
      setResult(previous => {
        if (previous.context !== context) return previous;
        const seen = new Set(previous.items.map(imageKey));
        return { context, items: [...previous.items, ...page.filter(item => { const key = imageKey(item); if (seen.has(key)) return false; seen.add(key); return true; })], total: data.total, documentCount: data.documentCount, nextOffset, more: page.length > 0 && nextOffset < data.total };
      });
    } catch (cause) {
      if (!controller.signal.aborted && state.generation === generation) setPageError(connectionMessage(cause));
    } finally {
      if (state.generation === generation) { state.busy = false; setLoadingMore(false); }
    }
  };

  useEffect(() => {
    const viewport = viewportRef.current, pagination = paginationRef.current;
    if (!active || !current || loading || loadingMore || pageError || !result.more ||
        !viewport || !pagination || typeof IntersectionObserver !== 'function') return;
    let disposed = false;
    const observer = new IntersectionObserver(entries => {
      const latest = autoLoadState.current;
      // Reject notifications queued before a search, page change or dismissal.
      if (!disposed && latest.allowed && latest.context === context &&
          latest.nextOffset === result.nextOffset && entries.some(entry => entry.isIntersecting)) loadMore();
    }, { root: viewport, rootMargin: '0px 0px 240px 0px' });
    observer.observe(pagination);
    return () => { disposed = true; observer.disconnect(); };
  }, [active, context, current, loading, loadingMore, pageError, result.more, result.nextOffset]);

  return <section className={`image-library${compact ? ' is-header-compact' : ''}`} aria-label="图片素材">
    <header className="library-heading image-library-heading"><div><h1>让信息，跃然眼前</h1><p>从一幅图像出发，看见文字之外的线索。</p></div><button type="button" className="btn quiet" disabled={loading || !active} onClick={() => setRetry(value => value + 1)}><ArrowClockwise size={17} />刷新</button></header>
    <div className="image-library-tools"><label className="image-library-search"><MagnifyingGlass size={19} /><input type="search" value={query} maxLength={500} placeholder="搜索文章标题或图片说明" aria-label="搜索文章标题或图片说明" onChange={event => setQuery(event.target.value)} />{query && <button type="button" className="icon-btn" aria-label="清空图片搜索" onClick={() => setQuery('')}><X size={16} /></button>}</label><p className="image-library-count" role="status" aria-live="polite">{currentError ? '暂时无法读取' : loading || !current ? '正在读取图片…' : <><strong>{result.total}</strong> 张图片<span>来自 {result.documentCount} 篇文章</span></>}</p></div>
    <div className="image-library-viewport" ref={viewportRef} tabIndex={0} aria-label="文章图片" aria-busy={loading || loadingMore}>
      {currentError ? <div className="image-library-empty" role="alert"><ImageIcon size={32} /><h2>图片暂时无法读取</h2><p>{currentError}</p><button type="button" className="btn" onClick={() => setRetry(value => value + 1)}>重新读取</button></div> : loading || !current ? <div className="image-library-empty"><ImageIcon size={32} /><p>正在读取图片…</p></div> : !items.length ? <div className="image-library-empty"><ImageIcon size={36} /><h2>{normalizedQuery ? '没有找到匹配的图片' : '还没有文章图片'}</h2><p>{normalizedQuery ? '试试其他文章标题或图片说明。' : '导入文章中的图片会显示在这里。'}</p>{normalizedQuery && <button type="button" className="btn quiet" onClick={() => setQuery('')}>清除搜索</button>}</div> : <>
        <div className="image-library-grid">{items.map(item => <article className="image-library-card" key={imageKey(item)}>
          <button type="button" className="image-library-picture" aria-label={`放大图片：${item.alt?.trim() || titleOf(item)}`} onClick={() => setPreviewKey(imageKey(item))}><AssetImage item={item} /></button>
          <div className="image-library-card-copy"><p className={`image-library-caption${!item.alt?.trim() ? ' is-missing' : ''}`}>{captionOf(item)}</p><h2>{titleOf(item)}</h2><div className="image-library-card-bottom"><small>{[item.source, item.date].filter(Boolean).join(' · ') || '文章图片'}</small><button type="button" onClick={() => onOpenDoc?.({ id: item.sourceId, revisionId: item.revisionId })}>查看文章<ArrowSquareOut size={14} /></button></div></div>
        </article>)}</div>
        <div className="image-library-pagination" ref={paginationRef}>{pageError && <p role="alert">{pageError}</p>}{result.more ? <button type="button" className="btn quiet" disabled={loadingMore} onClick={loadMore}>{loadingMore ? '正在读取…' : pageError ? '重试加载更多' : '加载更多图片'}</button> : <p>已显示全部 {result.total} 张图片</p>}</div>
      </>}
      <div className="image-library-scroll-space" aria-hidden="true" />
    </div>
    {previewItem && <ImagePreview item={previewItem} items={items} onClose={() => setPreviewKey(null)} onChoose={setPreviewKey} onOpenDoc={onOpenDoc} />}
  </section>;
}
