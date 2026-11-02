import { useLayoutEffect, useRef, useState } from 'react';
import './gallery-header.css';
import { isDemoMode } from './demo-mode';

/** Keep a stable scroll range while the article and image galleries contract. */
export function useCompactGalleryHeader(viewportRef, contentKey, { progressive = false } = {}) {
  const [compact, setCompact] = useState(false);
  const compactRef = useRef(false);
  const expandedHeightRef = useRef(0);
  const scrollPositionRef = useRef(0);

  useLayoutEffect(() => {
    if (progressive || isDemoMode()) return;
    const viewport = viewportRef.current;
    if (!viewport) return;

    const update = () => {
      const next = compactRef.current ? viewport.scrollTop > 2 : viewport.scrollTop > 24;
      if (next === compactRef.current) return;
      if (next) {
        expandedHeightRef.current = viewport.clientHeight;
        scrollPositionRef.current = viewport.scrollTop;
      }
      compactRef.current = next;
      setCompact(next);
    };
    update();
    viewport.addEventListener('scroll', update, { passive: true });
    return () => viewport.removeEventListener('scroll', update);
  }, [viewportRef, contentKey, progressive]);

  useLayoutEffect(() => {
    if (progressive || isDemoMode()) return;
    const viewport = viewportRef.current;
    if (!viewport) return;
    // Shrinking a fixed header grows the viewport. Add the released height at
    // the content's end so short lists never clamp to zero and expand it again.
    const releasedHeight = compact ? Math.max(0, viewport.clientHeight - expandedHeightRef.current) : 0;
    viewport.style.setProperty('--gallery-header-scroll-space', `${releasedHeight}px`);
    if (compact) viewport.scrollTop = scrollPositionRef.current;
  }, [compact, viewportRef, progressive]);

  useLayoutEffect(() => {
    if (!progressive || isDemoMode()) return;
    const viewport = viewportRef.current;
    const frame = viewport?.closest('.library-main, .writing-projects, .image-library');
    if (!viewport || !frame) return;
    const subtitle = frame.querySelector('.library-heading>div>p, .writing-page-heading>div>p');
    const heading = frame.querySelector('.library-heading, .writing-page-heading');
    const scrollSpace = viewport.querySelector('.document-card-grid, .writing-project-scroll-space, .image-library-scroll-space');
    const setScrollSpace = height => {
      if (!scrollSpace) return;
      // Ordinary properties avoid invalidating every card's inherited variables.
      scrollSpace.style[scrollSpace.matches('.document-card-grid') ? 'paddingBottom' : 'height'] = `${height}px`;
    };
    viewport.style.removeProperty('--gallery-header-scroll-space');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let animationFrame = 0, needsMeasurement = true, expandedHeight = 0;
    let previousProgress = -1, previousViewportHeight = 0;
    let displayedProgress = Number.parseFloat(frame.style.getPropertyValue('--gallery-header-progress')) || 0;
    let previousTime = performance.now(), initial = true, maximumRelease = 0;
    let frameWidth = frame.clientWidth, frameHeight = frame.clientHeight;

    const paint = (now = performance.now()) => {
      animationFrame = 0;
      if (!viewport.clientWidth || !viewport.clientHeight) return;
      const scrollTop = Math.max(0, viewport.scrollTop);
      const target = Math.min(1, scrollTop / 144);
      const elapsed = Math.max(1, Math.min(32, now - previousTime));
      previousTime = now;
      const delta = target - displayedProgress;
      const step = Math.sign(delta) * Math.min(.12, Math.abs(delta) * (1 - Math.exp(-elapsed / 42)));
      const progress = initial || reducedMotion.matches || Math.abs(delta) < .001 ? target : displayedProgress + step;
      initial = false;
      const measured = needsMeasurement;
      if (measured) {
        // Read the full header before applying this scroll position. This runs
        // before paint, so resize, font loading and hidden-page reentry do not
        // briefly show an expanded header or use a stale viewport baseline.
        frame.style.setProperty('--gallery-header-progress', '0');
        heading?.style.setProperty('--gallery-heading-progress', '0');
        setScrollSpace(0);
        if (subtitle) {
          const originalHeight = subtitle.style.height;
          subtitle.style.height = 'auto';
          const height = subtitle.getBoundingClientRect().height;
          subtitle.style.height = originalHeight;
          frame.style.setProperty('--gallery-header-subtitle-height', `${height}px`);
        }
        expandedHeight = viewport.clientHeight;
        const frameStyle = getComputedStyle(frame), headingStyle = heading ? getComputedStyle(heading) : null;
        maximumRelease = (heading?.getBoundingClientRect().height || 0)
          + (Number.parseFloat(headingStyle?.marginBottom) || 0)
          + (Number.parseFloat(frameStyle.paddingTop) || 0);
        needsMeasurement = false;
      }
      if (!measured && progress === previousProgress && viewport.clientHeight === previousViewportHeight) return;
      // Reserve tail room before the viewport grows, then reduce it to the
      // measured release within the same paint. Native smooth scrolling keeps
      // its destination even for a short list near its scroll boundary.
      setScrollSpace(maximumRelease);
      frame.style.setProperty('--gallery-header-progress', progress.toFixed(6));
      heading?.style.setProperty('--gallery-heading-progress', progress.toFixed(6));
      const height = viewport.clientHeight;
      const releasedHeight = Math.max(0, height - expandedHeight);
      setScrollSpace(releasedHeight);
      // Restoring every frame would cancel native smooth scrolling from a dot.
      // Only repair a browser clamp caused by this frame's viewport expansion.
      if (Math.abs(viewport.scrollTop - scrollTop) > .5) viewport.scrollTop = scrollTop;
      previousViewportHeight = height;
      previousProgress = progress;
      displayedProgress = progress;
      const nextCompact = progress >= 1;
      if (compactRef.current !== nextCompact) {
        compactRef.current = nextCompact;
        setCompact(nextCompact);
      }
      if (progress !== target) schedule();
    };
    const schedule = () => { if (!animationFrame) animationFrame = requestAnimationFrame(paint); };
    const remeasure = () => { needsMeasurement = true; schedule(); };
    const observer = new ResizeObserver(() => {
      const width = frame.clientWidth, height = frame.clientHeight;
      if (width !== frameWidth || height !== frameHeight) {
        frameWidth = width; frameHeight = height; needsMeasurement = true;
      }
      // Ignore the height change that our own preceding paint already measured.
      if (viewport.clientHeight && viewport.clientHeight !== previousViewportHeight) needsMeasurement = true;
      if (needsMeasurement) schedule();
    });
    observer.observe(frame); observer.observe(viewport);
    const mutations = new MutationObserver(remeasure);
    mutations.observe(frame, { childList: true });
    const appearance = new MutationObserver(remeasure);
    appearance.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    viewport.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', remeasure);
    window.addEventListener('pageshow', remeasure);
    document.fonts?.addEventListener('loadingdone', remeasure);
    reducedMotion.addEventListener('change', schedule);
    paint();
    return () => {
      cancelAnimationFrame(animationFrame);
      observer.disconnect(); mutations.disconnect(); appearance.disconnect();
      viewport.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', remeasure);
      window.removeEventListener('pageshow', remeasure);
      document.fonts?.removeEventListener('loadingdone', remeasure);
      reducedMotion.removeEventListener('change', schedule);
    };
  }, [viewportRef, contentKey, progressive]);

  return compact;
}
