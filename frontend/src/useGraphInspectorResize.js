import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

const DEFAULT_WIDTH = 360;
const MIN_WIDTH = 280;
const MAX_WIDTH = 620;
const GRAPH_MIN_WIDTH = 480;

export function graphInspectorResizeBounds(bodyWidth) {
  const available = Number.isFinite(bodyWidth) ? Math.max(0, Math.floor(bodyWidth)) : 1100;
  // Below the usual desktop width, keep most of the space for the graph.
  const minWidth = Math.min(MIN_WIDTH, Math.floor(available * .4));
  const maxWidth = Math.max(minWidth, Math.min(MAX_WIDTH, available - GRAPH_MIN_WIDTH));
  return { minWidth, maxWidth };
}

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export function useGraphInspectorResize({ bodyRef, active = true }) {
  const [requestedWidth, setRequestedWidth] = useState(DEFAULT_WIDTH);
  const [bodyWidth, setBodyWidth] = useState(null);
  const [resizing, setResizing] = useState(false);
  const dragRef = useRef(null);
  const { minWidth, maxWidth } = graphInspectorResizeBounds(bodyWidth);
  const width = clamp(requestedWidth, minWidth, maxWidth);
  const limitsRef = useRef({ width, minWidth, maxWidth });
  limitsRef.current = { width, minWidth, maxWidth };

  const endResize = useCallback((updateState = true) => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag) {
      try {
        if (drag.element.hasPointerCapture?.(drag.pointerId)) drag.element.releasePointerCapture(drag.pointerId);
      } catch { /* The element or pointer may already have been released. */ }
    }
    if (updateState) setResizing(false);
  }, []);

  useLayoutEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    const measure = () => setBodyWidth(body.clientWidth);
    measure();
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(measure);
      observer.observe(body);
      return () => observer.disconnect();
    }
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [bodyRef, active]);

  useLayoutEffect(() => {
    if (!active) endResize();
  }, [active, endResize]);

  useEffect(() => {
    const cancel = () => endResize();
    const visibility = () => { if (document.hidden) endResize(); };
    window.addEventListener('blur', cancel);
    window.addEventListener('resize', cancel);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      window.removeEventListener('blur', cancel);
      window.removeEventListener('resize', cancel);
      document.removeEventListener('visibilitychange', visibility);
      endResize(false);
    };
  }, [endResize]);

  const stopPointer = event => {
    if (!dragRef.current || event.pointerId !== dragRef.current.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    endResize();
  };

  const separatorProps = {
    role: 'separator',
    tabIndex: 0,
    'aria-label': '调整词条详情宽度',
    'aria-orientation': 'vertical',
    'aria-valuenow': width,
    'aria-valuemin': minWidth,
    'aria-valuemax': maxWidth,
    'aria-valuetext': `${width} 像素`,
    onPointerDown: event => {
      if (!active || event.button !== 0 || event.isPrimary === false || dragRef.current) return;
      event.preventDefault();
      event.stopPropagation();
      const element = event.currentTarget;
      element.focus({ preventScroll: true });
      try { element.setPointerCapture(event.pointerId); } catch { return; }
      dragRef.current = { element, pointerId: event.pointerId, x: event.clientX, width: limitsRef.current.width };
      setResizing(true);
    },
    onPointerMove: event => {
      const drag = dragRef.current;
      if (!active || !drag || event.pointerId !== drag.pointerId) return;
      event.preventDefault();
      event.stopPropagation();
      const limits = limitsRef.current;
      const next = clamp(Math.round(drag.width + drag.x - event.clientX), limits.minWidth, limits.maxWidth);
      if (next !== limits.width) setRequestedWidth(next);
    },
    onPointerUp: stopPointer,
    onPointerCancel: stopPointer,
    onLostPointerCapture: stopPointer,
    onKeyDown: event => {
      if (!active || event.altKey || event.ctrlKey || event.metaKey) return;
      const step = event.shiftKey ? 32 : 16;
      const limits = limitsRef.current;
      let next;
      if (event.key === 'ArrowLeft') next = limits.width + step;
      if (event.key === 'ArrowRight') next = limits.width - step;
      if (event.key === 'Home') next = limits.minWidth;
      if (event.key === 'End') next = limits.maxWidth;
      if (next === undefined) return;
      event.preventDefault();
      event.stopPropagation();
      next = clamp(next, limits.minWidth, limits.maxWidth);
      if (next !== limits.width) setRequestedWidth(next);
    },
  };

  return { width, minWidth, maxWidth, resizing, separatorProps };
}
