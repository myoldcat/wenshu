import { isDemoMode } from './demo-mode';
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { BookOpen, Check, FileText, Quotes, Star, Trash } from '@phosphor-icons/react';
import { DocumentDotNav } from './DocumentDotNav';
import { useCardCovers } from './useCardCovers';
import { DocumentTags } from './DocumentTags';
import { documentExcerptLabel } from './translation-view.mjs';
import { OriginalLanguageBadge } from './OriginalLanguageBadge';
import { useCompactGalleryHeader } from './useCompactGalleryHeader';
import { BODY_WORD_COUNT_DESCRIPTION, bodyWordCountLabel } from './document-metrics.mjs';

function CardCover({ cover, title, onOpen }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return <button type="button" className="document-card-cover" aria-label={`查看 ${title} 的资料配图`} onClick={onOpen}>
    <img src={cover.url} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)}/>
  </button>;
}

export function DocumentCardGrid({ documents, onOpenDoc, onFavorite, onToggleSelected, onCategoryChange, onOpenImage, onDelete, autoSelectCovers = false, coverModelId, coverModelVersion }) {
  const galleryRef = useRef(null);
  const viewportRef = useRef(null);
  const cards = useRef(new Map());
  const [columns, setColumns] = useState(2);
  const [cardHoverId, setCardHoverId] = useState(null);
  const [keyboardCardId, setKeyboardCardId] = useState(null);
  const [railHighlightId, setRailHighlightId] = useState(null);
  const [visibleIds, setVisibleIds] = useState([]);
  const { getCardCover } = useCardCovers({ documents, visibleIds, enabled: autoSelectCovers, modelId: coverModelId, modelVersion: coverModelVersion });
  const documentKey = documents.map(doc => doc.id).join('|');
  const compactHeader = useCompactGalleryHeader(viewportRef, documentKey, { progressive: true });
  const highlightedId = railHighlightId ?? cardHoverId ?? keyboardCardId;

  useLayoutEffect(() => {
    const gallery = galleryRef.current;
    if (!gallery) return;
    const resize = () => {
      const width = gallery.clientWidth;
      if (!width) return;
      const galleryStyle = getComputedStyle(gallery);
      const viewportStyle = getComputedStyle(viewportRef.current);
      const navigation = gallery.querySelector('.doc-dot-nav');
      const navigationStyle = getComputedStyle(navigation);
      const pixels = value => Number.parseFloat(value) || 0;
      const cardMinWidth = pixels(galleryStyle.getPropertyValue('--document-card-min-width'));
      const cardGap = pixels(galleryStyle.getPropertyValue('--document-card-gap'));
      const galleryGap = pixels(galleryStyle.columnGap);
      const viewportInset = pixels(viewportStyle.paddingLeft) + pixels(viewportStyle.paddingRight)
        + pixels(viewportStyle.marginLeft) + pixels(viewportStyle.marginRight);
      const dotCell = pixels(navigationStyle.getPropertyValue('--doc-dot-cell'));
      const currentDotColumns = pixels(navigationStyle.getPropertyValue('--doc-dot-columns'));
      const navigationWidth = navigation.getBoundingClientRect().width;

      // Predict the mini-map width for each candidate, rather than measuring a
      // card viewport whose width itself changes when the column count changes.
      let nextColumns = 1;
      for (let count = 3; count >= 2; count -= 1) {
        const candidateNavigationWidth = navigationWidth + (count - currentDotColumns) * dotCell;
        const contentWidth = width - galleryGap - candidateNavigationWidth - viewportInset;
        if (contentWidth >= cardMinWidth * count + cardGap * (count - 1)) {
          nextColumns = count;
          break;
        }
      }
      setColumns(nextColumns);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(gallery);
    return () => observer.disconnect();
  }, [Boolean(documents.length)]);

  useEffect(() => {
    // Pointer/focus feedback never carries over to a different filtered order.
    setCardHoverId(null);
    setKeyboardCardId(null);
    setRailHighlightId(null);
  }, [documentKey]);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) {
      setVisibleIds(previous => previous.length ? [] : previous);
      return;
    }

    let animationFrame = 0;
    const updateVisible = () => {
      animationFrame = 0;
      const frame = viewport.getBoundingClientRect();
      const top = frame.top + viewport.clientTop;
      const left = frame.left + viewport.clientLeft;
      const bottom = top + viewport.clientHeight;
      const right = left + viewport.clientWidth;
      const nextVisible = [];
      let closestVisible = null;
      let closestDistance = Infinity;

      for (const doc of documents) {
        const card = cards.current.get(doc.id);
        if (!card) continue;
        const rect = card.getBoundingClientRect();
        const visibleHeight = Math.max(0, Math.min(rect.bottom, bottom) - Math.max(rect.top, top));
        const visibleWidth = Math.max(0, Math.min(rect.right, right) - Math.max(rect.left, left));
        if (!visibleHeight || !visibleWidth) continue;

        // Ignore a narrow sliver at either edge, while allowing short desktop
        // viewports to describe their current position in the document map.
        if (visibleHeight / Math.min(rect.height, viewport.clientHeight) >= 0.18) {
          nextVisible.push(doc.id);
        }
        const distance = Math.abs((rect.top + rect.bottom - top - bottom) / 2);
        if (distance < closestDistance) {
          closestDistance = distance;
          closestVisible = doc.id;
        }
      }

      if (!nextVisible.length && closestVisible !== null) nextVisible.push(closestVisible);
      setVisibleIds(previous => previous.length === nextVisible.length
        && previous.every((id, index) => id === nextVisible[index]) ? previous : nextVisible);
    };
    const scheduleUpdate = () => {
      if (!animationFrame) animationFrame = requestAnimationFrame(updateVisible);
    };

    updateVisible();
    scheduleUpdate();
    viewport.addEventListener('scroll', scheduleUpdate, { passive: true });
    window.addEventListener('pageshow', scheduleUpdate);
    document.addEventListener('visibilitychange', scheduleUpdate);
    const observer = new ResizeObserver(scheduleUpdate);
    observer.observe(viewport);
    if (viewport.firstElementChild) observer.observe(viewport.firstElementChild);
    return () => {
      cancelAnimationFrame(animationFrame);
      observer.disconnect();
      viewport.removeEventListener('scroll', scheduleUpdate);
      window.removeEventListener('pageshow', scheduleUpdate);
      document.removeEventListener('visibilitychange', scheduleUpdate);
    };
  }, [documentKey, columns]);

  const locate = useCallback((id, { keyboard = false } = {}) => {
    const viewport = viewportRef.current;
    const card = cards.current.get(id);
    if (!viewport || !card) return;
    const frame = viewport.getBoundingClientRect();
    const rect = card.getBoundingClientRect();
    const top = viewport.scrollTop + rect.top - frame.top - Math.max(12, (viewport.clientHeight - rect.height) / 2);
    viewport.scrollTo({ top: Math.max(0, top), behavior: isDemoMode() || matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    if (keyboard) card.focus({ preventScroll: true });
  }, []);

  const open = id => onOpenDoc(id);
  if (!documents.length) return null;

  return <section className={`document-gallery${compactHeader ? ' is-header-compact' : ''}`} ref={galleryRef} style={{ '--document-columns': columns }} aria-label="文档卡片列表">
    <div className="document-card-viewport" ref={viewportRef} tabIndex={0} aria-label="浏览文档卡片">
      <div className="document-card-grid" onPointerDownCapture={() => setKeyboardCardId(null)}>
        {documents.map(doc => {
          const cover = getCardCover(doc);
          const showCover = cover?.status === 'completed' && !!cover.url;
          const Icon = doc.kind === '观点' ? Quotes : doc.kind === '概念' ? BookOpen : FileText;
          return <article key={doc.id} ref={element => { if (element) cards.current.set(doc.id, element); else cards.current.delete(doc.id); }}
            className={`document-card${doc.selected ? ' is-selected' : ''}${highlightedId === doc.id ? ' is-highlighted' : ''}`}
            tabIndex={-1} aria-labelledby={`card-title-${doc.id}`} data-document-id={doc.id}
            onPointerEnter={event => { if (event.pointerType !== 'touch') setCardHoverId(doc.id); }}
            onPointerLeave={() => setCardHoverId(current => current === doc.id ? null : current)}
            onFocusCapture={event => { if (event.target.matches(':focus-visible')) setKeyboardCardId(doc.id); }}
            onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) setKeyboardCardId(current => current === doc.id ? null : current); }}
            onClick={event => { if (!event.target.closest('button,input,label,a')) open(doc.id); }}
            onKeyDown={event => { if (event.target === event.currentTarget && event.key === 'Enter') { event.preventDefault(); open(doc.id); } }}>
            <div className="document-card-top">
              <button className={`document-card-type kind-${doc.kind}`} aria-label={`阅读 ${doc.title}`} onClick={() => open(doc.id)}><Icon size={23}/></button>
              <span className="document-card-kind">{doc.kind || '文档'}</span>
              {doc.pending && <span className="document-card-pending">待抓取</span>}
              <div className="document-card-actions">
                {onDelete && <button type="button" className="icon-btn document-card-delete" aria-label={`删除文档 ${doc.title}`} title="删除文档" onClick={() => onDelete(doc)}><Trash size={17}/></button>}
                <button className={`icon-btn document-card-favorite ${doc.favorite ? 'is-favorite' : ''}`} aria-label={`${doc.favorite ? '取消收藏' : '收藏'} ${doc.title}`} onClick={() => onFavorite(doc.id)}><Star size={17} weight={doc.favorite ? 'fill' : 'regular'}/></button>
                <label className="document-card-selection" title={doc.status !== 'ready' ? '入库完成后可选择资料' : doc.selected ? '移出已选资料' : '选为写作资料'}>
                  <input type="checkbox" checked={!!doc.selected} disabled={doc.status !== 'ready'} aria-label={`将 ${doc.title} 选为写作资料`} onChange={() => onToggleSelected(doc.id)}/>
                  <Check className="document-card-selection-check" size={11} weight="regular" aria-hidden="true" focusable="false"/>
                </label>
              </div>
            </div>
            <h2 id={`card-title-${doc.id}`}><button className="document-card-title" onClick={() => open(doc.id)}><OriginalLanguageBadge doc={doc}/>{doc.title}</button></h2>
            <div className="document-card-preview"><p className="document-card-summary" title={documentExcerptLabel(doc)}>{doc.snippet || doc.summary}</p>{showCover && <CardCover key={`${doc.revisionId}:${cover.assetId}:${cover.url}`} cover={cover} title={doc.title} onOpen={() => onOpenImage(doc.id)}/>}</div>
            <div className="document-card-bottom">
              <div className="document-card-meta"><span className="document-card-source" title={doc.source}>{doc.source}</span><span className="document-card-word-count" title={BODY_WORD_COUNT_DESCRIPTION}>{bodyWordCountLabel(doc, '正文 ')}</span><time>{doc.date}</time></div>
              <div className="document-card-tags"><DocumentTags doc={doc} onTagClick={onCategoryChange}/>
              </div>
            </div>
          </article>;
        })}
      </div>
    </div>
    <DocumentDotNav documents={documents} visibleIds={visibleIds} columns={columns} highlightedId={highlightedId} onHighlight={setRailHighlightId} onLocate={locate}/>
  </section>;
}
