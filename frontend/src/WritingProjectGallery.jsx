import { isDemoMode } from './demo-mode';
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { DocumentDotNav } from './DocumentDotNav';
import { useCompactGalleryHeader } from './useCompactGalleryHeader';

/** Manuscripts use the library's spatial index without opening or saving a draft. */
export function WritingProjectGallery({ projects = [], renderProject, children }) {
  const galleryRef = useRef(null), viewportRef = useRef(null), cards = useRef(new Map());
  const [columns, setColumns] = useState(2), [visibleIds, setVisibleIds] = useState([]);
  const [cardHoverId, setCardHoverId] = useState(null), [keyboardCardId, setKeyboardCardId] = useState(null), [railHighlightId, setRailHighlightId] = useState(null);
  const projectKey = projects.map(project => project.id).join('|');
  const compact = useCompactGalleryHeader(viewportRef, projectKey, { progressive: true });
  const highlightedId = railHighlightId ?? cardHoverId ?? keyboardCardId;
  const entries = projects.map(project => ({ id: project.id, title: project.title || project.topic || '未命名文稿' }));

  useLayoutEffect(() => {
    const gallery = galleryRef.current, viewport = viewportRef.current;
    if (!gallery || !viewport || !projects.length) return;
    const resize = () => {
      const navigation = gallery.querySelector('.doc-dot-nav');
      if (!navigation || !gallery.clientWidth) return;
      const pixels = value => Number.parseFloat(value) || 0;
      const galleryStyle = getComputedStyle(gallery), viewportStyle = getComputedStyle(viewport), navigationStyle = getComputedStyle(navigation);
      const cardWidth = pixels(galleryStyle.getPropertyValue('--writing-card-min-width'));
      const cardGap = pixels(galleryStyle.getPropertyValue('--writing-card-gap'));
      const viewportInset = pixels(viewportStyle.paddingLeft) + pixels(viewportStyle.paddingRight) + pixels(viewportStyle.marginLeft) + pixels(viewportStyle.marginRight);
      const dotCell = pixels(navigationStyle.getPropertyValue('--doc-dot-cell'));
      const currentColumns = pixels(navigationStyle.getPropertyValue('--doc-dot-columns'));
      let nextColumns = 1;
      for (let count = 3; count >= 2; count -= 1) {
        const navigationWidth = navigation.getBoundingClientRect().width + (count - currentColumns) * dotCell;
        const available = gallery.clientWidth - pixels(galleryStyle.columnGap) - navigationWidth - viewportInset;
        if (available >= cardWidth * count + cardGap * (count - 1)) { nextColumns = count; break; }
      }
      setColumns(nextColumns);
    };
    resize();
    const observer = new ResizeObserver(resize); observer.observe(gallery);
    return () => observer.disconnect();
  }, [Boolean(projects.length)]);

  useEffect(() => {
    setCardHoverId(null); setKeyboardCardId(null); setRailHighlightId(null);
  }, [projectKey]);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    let frameId = 0;
    const update = () => {
      frameId = 0;
      const frame = viewport.getBoundingClientRect(), top = frame.top + viewport.clientTop, bottom = top + viewport.clientHeight;
      const left = frame.left + viewport.clientLeft, right = left + viewport.clientWidth;
      const next = []; let closest = null, distance = Infinity;
      for (const project of projects) {
        const card = cards.current.get(project.id); if (!card) continue;
        const rect = card.getBoundingClientRect();
        const height = Math.max(0, Math.min(rect.bottom, bottom) - Math.max(rect.top, top));
        const width = Math.max(0, Math.min(rect.right, right) - Math.max(rect.left, left));
        if (!height || !width) continue;
        if (height / Math.min(rect.height, viewport.clientHeight) >= 0.18) next.push(project.id);
        const offset = Math.abs((rect.top + rect.bottom - top - bottom) / 2);
        if (offset < distance) { distance = offset; closest = project.id; }
      }
      if (!next.length && closest !== null) next.push(closest);
      setVisibleIds(previous => previous.length === next.length && previous.every((id, index) => id === next[index]) ? previous : next);
    };
    const schedule = () => { if (!frameId) frameId = requestAnimationFrame(update); };
    update(); schedule();
    viewport.addEventListener('scroll', schedule, { passive: true }); window.addEventListener('pageshow', schedule); document.addEventListener('visibilitychange', schedule);
    const observer = new ResizeObserver(schedule); observer.observe(viewport);
    if (viewport.firstElementChild) observer.observe(viewport.firstElementChild);
    return () => {
      cancelAnimationFrame(frameId); observer.disconnect(); viewport.removeEventListener('scroll', schedule);
      window.removeEventListener('pageshow', schedule); document.removeEventListener('visibilitychange', schedule);
    };
  }, [projectKey, columns]);

  const locate = useCallback((id, { keyboard = false } = {}) => {
    const viewport = viewportRef.current, card = cards.current.get(id);
    if (!viewport || !card) return;
    const frame = viewport.getBoundingClientRect(), rect = card.getBoundingClientRect();
    const top = viewport.scrollTop + rect.top - frame.top - Math.max(12, (viewport.clientHeight - rect.height) / 2);
    viewport.scrollTo({ top: Math.max(0, top), behavior: isDemoMode() || matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    if (keyboard) card.focus({ preventScroll: true });
  }, []);

  return <section className={`writing-project-gallery${compact ? ' is-header-compact' : ''}`} ref={galleryRef} style={{ '--writing-project-columns': columns }} aria-label="文稿卡片列表">
    <div className="writing-project-viewport" ref={viewportRef} tabIndex={0} aria-label="浏览文稿卡片">
      <div className="writing-project-grid" onPointerDownCapture={() => setKeyboardCardId(null)}>
        {projects.map(project => renderProject(project, {
          ref: element => { if (element) cards.current.set(project.id, element); else cards.current.delete(project.id); },
          className: `writing-project-card${highlightedId === project.id ? ' is-highlighted' : ''}`,
          tabIndex: -1,
          'data-writing-project-id': project.id,
          onPointerEnter: event => { if (event.pointerType !== 'touch') setCardHoverId(project.id); },
          onPointerLeave: () => setCardHoverId(current => current === project.id ? null : current),
          onFocusCapture: event => { if (event.target.matches(':focus-visible')) setKeyboardCardId(project.id); },
          onBlurCapture: event => { if (!event.currentTarget.contains(event.relatedTarget)) setKeyboardCardId(current => current === project.id ? null : current); },
        }))}
      </div>
      {children}
      <div className="writing-project-scroll-space" aria-hidden="true"/>
    </div>
    <DocumentDotNav documents={entries} visibleIds={visibleIds} columns={columns} highlightedId={highlightedId} onHighlight={setRailHighlightId} onLocate={locate} ariaLabel="文稿卡片定位" itemLabel="文稿"/>
  </section>;
}
