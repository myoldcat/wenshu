import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { OriginalLanguageBadge } from './OriginalLanguageBadge';
import { originalLanguageBadge } from './translation-view.mjs';
import './document-dot-nav.css';

const PREVIEW_WIDTH = 208;
const PREVIEW_GAP = 6;
const VIEWPORT_MARGIN = 12;
const NEARBY_DOT_RADIUS = 3;

/** A spatial index of the document cards. Layout follows the card grid order. */
export function DocumentDotNav({ documents = [], visibleIds = [], highlightedId, columns = 2, onHighlight, onLocate, ariaLabel = '文章卡片定位', itemLabel = '文章' }) {
  const columnCount = Math.max(1, Math.min(3, Math.floor(columns) || 1));
  const visible = useMemo(() => new Set(Array.from(visibleIds, String)), [visibleIds]);
  const visibleIndices = documents.flatMap((doc, index) => visible.has(String(doc.id)) ? [index] : []);
  const firstVisibleRow = visibleIndices.length ? Math.floor(visibleIndices[0] / columnCount) : -1;
  const lastVisibleRow = visibleIndices.length ? Math.floor(visibleIndices.at(-1) / columnCount) : -1;
  const documentOrder = documents.map(doc => String(doc.id)).join('\u0001');
  const railRef = useRef(null);
  const gridRef = useRef(null);
  const dotRefs = useRef([]);
  const tooltipRef = useRef(null);
  const pointerFocusRef = useRef(false);
  const onHighlightRef = useRef(onHighlight);
  onHighlightRef.current = onHighlight;
  const [hoverIndex, setHoverIndex] = useState(-1);
  const [focusIndex, setFocusIndex] = useState(-1);
  const [tabIndex, setTabIndex] = useState(0);
  const [tooltipPosition, setTooltipPosition] = useState(null);
  const [railPosition, setRailPosition] = useState(null);
  const [portalContainer, setPortalContainer] = useState(null);
  const tooltipId = useId();
  const previewIndex = hoverIndex >= 0 ? hoverIndex : focusIndex;
  const previewDoc = documents[previewIndex];
  const hasPointerHover = hoverIndex >= 0 && hoverIndex < documents.length;
  const hoverRow = Math.floor(hoverIndex / columnCount);
  const hoverColumn = hoverIndex % columnCount;

  useLayoutEffect(() => {
    const rail = railRef.current;
    if (!rail) return;
    // Stay within the shell's overlay order while escaping the content pane's
    // backdrop-filter fixed-position containing block (notably the glass theme).
    setPortalContainer(rail.closest('.workspace-shell') || document.body);
    const frame = rail.closest('.library-main, .writing-projects') || rail.parentElement;
    const position = () => {
      const railRect = rail.getBoundingClientRect();
      const frameRect = frame.getBoundingClientRect();
      const visible = railRect.width > 0 && railRect.height > 0 && frameRect.height > 0;
      const top = Math.max(VIEWPORT_MARGIN, frameRect.top);
      const bottom = Math.min(window.innerHeight - VIEWPORT_MARGIN, frameRect.bottom);
      // The gallery moves as its header contracts. Only its horizontal rail is
      // measured; the visual navigation stays on the viewport's fixed midline.
      const center = window.innerHeight / 2;
      const availableHeight = Math.max(0, 2 * Math.min(center - top, bottom - center));
      setRailPosition(previous => previous?.left === railRect.left && previous?.width === railRect.width
        && previous?.availableHeight === availableHeight && previous?.visible === visible
        ? previous : { left: railRect.left, width: railRect.width, availableHeight, visible });
    };
    position();
    const observer = new ResizeObserver(position);
    observer.observe(rail);
    observer.observe(frame);
    window.addEventListener('resize', position);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', position);
    };
  }, [Boolean(documents.length)]);

  const stopPointer = () => {
    setHoverIndex(-1);
    onHighlightRef.current?.(focusIndex >= 0 ? documents[focusIndex]?.id ?? null : null);
  };

  useEffect(() => {
    dotRefs.current.length = documents.length;
    setHoverIndex(-1);
    setFocusIndex(-1);
    onHighlightRef.current?.(null);
    setTabIndex(previous => Math.min(previous, Math.max(0, documents.length - 1)));
  }, [documentOrder]);

  useLayoutEffect(() => {
    if (!previewDoc || !railPosition?.visible) {
      setTooltipPosition(null);
      return;
    }
    const position = () => {
      const dot = dotRefs.current[previewIndex];
      const tooltip = tooltipRef.current;
      const grid = gridRef.current;
      if (!dot || !tooltip || !grid) return;
      const anchor = dot.getBoundingClientRect();
      // Anchor outside the whole rail, clear of the active dot and other columns.
      // Clamping a preview beside an individual dot can cover earlier columns.
      const rail = grid.getBoundingClientRect();
      const viewportWidth = document.documentElement.clientWidth;
      const viewportHeight = document.documentElement.clientHeight;
      const leftRoom = Math.max(0, rail.left - PREVIEW_GAP - VIEWPORT_MARGIN);
      const rightRoom = Math.max(0, viewportWidth - rail.right - PREVIEW_GAP - VIEWPORT_MARGIN);
      const useLeft = leftRoom >= PREVIEW_WIDTH || leftRoom >= rightRoom;
      const maxWidth = Math.min(PREVIEW_WIDTH, useLeft ? leftRoom : rightRoom);
      // Shrink on the roomier side rather than clamp back over the dot grid.
      tooltip.style.maxWidth = `${maxWidth}px`;
      const width = tooltip.offsetWidth;
      const height = tooltip.offsetHeight;
      const left = useLeft ? rail.left - width - PREVIEW_GAP : rail.right + PREVIEW_GAP;
      const top = Math.max(VIEWPORT_MARGIN, Math.min(anchor.top + anchor.height / 2 - height / 2,
        viewportHeight - height - VIEWPORT_MARGIN));
      setTooltipPosition(previous => previous?.left === left && previous?.top === top && previous?.maxWidth === maxWidth
        ? previous : { left, top, maxWidth });
    };
    position();
    const observer = new ResizeObserver(position);
    observer.observe(gridRef.current);
    observer.observe(gridRef.current.parentElement.parentElement);
    observer.observe(tooltipRef.current);
    window.addEventListener('resize', position);
    // Capture scroll from either the content pane or the compact dot rail.
    window.addEventListener('scroll', position, true);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    };
  }, [previewIndex, previewDoc, columnCount, railPosition]);

  const navigate = (event, index) => {
    pointerFocusRef.current = false;
    let next = index;
    if (event.key === 'ArrowUp') next = Math.max(0, index - columnCount);
    else if (event.key === 'ArrowDown') next = Math.min(documents.length - 1, index + columnCount);
    else if (event.key === 'ArrowLeft') next = index % columnCount ? index - 1 : index;
    else if (event.key === 'ArrowRight') next = index % columnCount < columnCount - 1 ? Math.min(documents.length - 1, index + 1) : index;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = documents.length - 1;
    else if (event.key === 'Escape') {
      setHoverIndex(-1);
      setFocusIndex(-1);
      onHighlightRef.current?.(null);
      return;
    } else {
      if (event.key === 'Enter' || event.key === ' ') {
        setFocusIndex(index);
        onHighlightRef.current?.(documents[index]?.id ?? null);
      }
      return;
    }
    event.preventDefault();
    setTabIndex(next);
    setFocusIndex(next);
    onHighlightRef.current?.(documents[next]?.id ?? null);
    dotRefs.current[next]?.focus({ preventScroll: true });
    dotRefs.current[next]?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  };

  if (!documents.length) return null;

  return <>
    <div className="doc-dot-nav doc-dot-nav-placeholder" ref={railRef} aria-hidden="true" style={{ '--doc-dot-columns': columnCount }} />
    {createPortal(<nav className="doc-dot-nav doc-dot-nav-overlay" aria-label={ariaLabel} hidden={!railPosition?.visible}
      style={{ '--doc-dot-columns': columnCount, '--doc-dot-available-height': `${railPosition?.availableHeight || 0}px`,
        left: railPosition?.left || 0, width: railPosition?.width || 0 }}>
    <div className="doc-dot-nav-grid" ref={gridRef} onPointerLeave={stopPointer} onPointerCancel={stopPointer}>
      {firstVisibleRow >= 0 && <span className="doc-dot-viewport-glow" aria-hidden="true"
        style={{ '--doc-view-row': firstVisibleRow, '--doc-view-rows': lastVisibleRow - firstVisibleRow + 1 }} />}
      {documents.map((doc, index) => {
        const isHighlighted = highlightedId != null && String(highlightedId) === String(doc.id);
        const isVisible = visible.has(String(doc.id));
        // Follow the grid's two-dimensional spacing without resizing hit areas.
        const distance = hasPointerHover ? Math.hypot(Math.floor(index / columnCount) - hoverRow, index % columnCount - hoverColumn) : NEARBY_DOT_RADIUS;
        const nearbyScale = 1 + .65 * Math.max(0, 1 - distance / NEARBY_DOT_RADIUS);
        return <button key={doc.id} type="button" ref={element => { dotRefs.current[index] = element; }}
          className={`doc-dot-nav-button${doc.selected ? ' is-selected' : ''}${isHighlighted ? ' is-highlighted' : ''}${isVisible ? ' is-in-view' : ''}`}
          style={{ '--doc-dot-nearby-scale': nearbyScale }}
          aria-label={`定位${itemLabel}：${doc.title}`}
          aria-description={[originalLanguageBadge(doc) && `原文语言：${originalLanguageBadge(doc)}`, doc.selected && '已选为写作资料', isVisible && '当前可见'].filter(Boolean).join('，') || undefined}
          aria-describedby={previewIndex === index ? tooltipId : undefined}
          tabIndex={tabIndex === index ? 0 : -1}
          onPointerEnter={event => {
            if (event.pointerType === 'touch') { stopPointer(); return; }
            setHoverIndex(index);
            onHighlightRef.current?.(doc.id);
          }}
          onPointerLeave={() => {
            setHoverIndex(-1);
            onHighlightRef.current?.(focusIndex >= 0 ? documents[focusIndex]?.id ?? null : null);
          }}
          onPointerDown={() => {
            // Pointer focus must not become a persistent keyboard preview after a click.
            pointerFocusRef.current = true;
            setFocusIndex(-1);
          }}
          onFocus={event => {
            setTabIndex(index);
            if (pointerFocusRef.current || !event.currentTarget.matches(':focus-visible')) return;
            setFocusIndex(index);
            onHighlightRef.current?.(doc.id);
          }}
          onBlur={() => {
            pointerFocusRef.current = false;
            setFocusIndex(-1);
            onHighlightRef.current?.(hoverIndex >= 0 ? documents[hoverIndex]?.id ?? null : null);
          }}
          onKeyDown={event => navigate(event, index)}
          onClick={event => onLocate?.(doc.id, { keyboard: event.detail === 0 })}>
          <span className="doc-dot-nav-dot" aria-hidden="true" />
        </button>;
      })}
    </div>
    </nav>, portalContainer || document.body)}
    {previewDoc && railPosition?.visible && createPortal(<div className="doc-dot-preview" id={tooltipId} role="tooltip" ref={tooltipRef}
      style={tooltipPosition || { left: -9999, top: 0 }}>
      <p className="doc-dot-preview-title"><OriginalLanguageBadge doc={previewDoc}/>{previewDoc.title}</p>
    </div>, portalContainer || document.body)}
  </>;
}
