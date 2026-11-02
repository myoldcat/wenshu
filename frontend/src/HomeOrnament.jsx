import React, { useCallback, useEffect, useRef, useState } from 'react';
import bamboo from './assets/home/home-bamboo-v1.webp';
import banner from './assets/home/home-banner-v1.webp';
import { createHomeOrnamentMotion } from './home-ornament-motion';
import HomeWaterRipples from './HomeWaterRipples';
import './home-ornament.css';

const ARTWORK = { jade: bamboo, glass: null, civic: banner };
const currentTheme = () => Object.hasOwn(ARTWORK, document.documentElement.dataset.theme)
  ? document.documentElement.dataset.theme : 'jade';

export default function HomeOrnament() {
  const host = useRef(null);
  const canvas = useRef(null);
  const [theme, setTheme] = useState(currentTheme);
  const [reduced, setReduced] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [readyTheme, setReadyTheme] = useState(null);
  const updateRippleMotion = useCallback(paused => {
    if (host.current) host.current.dataset.motion = paused ? 'paused' : 'running';
  }, []);

  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(currentTheme()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    setTheme(currentTheme());
    const preference = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(preference.matches);
    preference.addEventListener('change', update);
    return () => { observer.disconnect(); preference.removeEventListener('change', update); };
  }, []);

  useEffect(() => {
    const node = host.current;
    const workspace = node.closest('.home-workspace');
    const hero = node.closest('.home-hero');
    const heading = workspace?.querySelector('.home-rail-heading h2');
    if (!heading) return undefined;
    const reserveHeading = () => {
      const heroBounds = hero.getBoundingClientRect();
      const styles = getComputedStyle(node);
      // The two flush themes paint beyond the inset content grid. Keep their
      // viewport anchor moving with the original header when its page scrolls.
      node.style.setProperty('--ornament-edge-top', `${heroBounds.top - parseFloat(styles.getPropertyValue('--workspace-content-top') || 29)}px`);
      node.style.setProperty('--ornament-edge-width', `${heroBounds.width * parseFloat(styles.getPropertyValue('--ornament-share'))}px`);
      const artwork = node.getBoundingClientRect();
      const text = heading.getBoundingClientRect();
      node.style.setProperty('--ornament-label-x', `${text.left + text.width / 2 - artwork.left}px`);
      node.style.setProperty('--ornament-label-y', `${text.top + text.height / 2 - artwork.top}px`);
      node.style.setProperty('--ornament-label-rx', `${text.width / 2 + 26}px`);
      node.style.setProperty('--ornament-label-ry', `${text.height / 2 + 20}px`);
    };
    const observer = new ResizeObserver(reserveHeading);
    observer.observe(node);
    observer.observe(heading);
    observer.observe(hero);
    workspace.addEventListener('scroll', reserveHeading, { passive: true });
    window.addEventListener('resize', reserveHeading);
    reserveHeading();
    return () => {
      observer.disconnect();
      workspace.removeEventListener('scroll', reserveHeading);
      window.removeEventListener('resize', reserveHeading);
    };
  }, [theme]);

  useEffect(() => {
    const node = host.current;
    setReadyTheme(null);
    if (theme === 'glass') return undefined;
    node.dataset.motion = 'paused';
    if (reduced) return undefined;

    let renderer = null;
    let stopped = false;
    let failed = false;
    let visible = true;
    let frame = 0;
    let previous = 0;
    let lastPaint = 0;
    let seconds = 0;
    const canRun = () => renderer && !stopped && !failed && visible && !document.hidden;
    const pause = () => {
      cancelAnimationFrame(frame);
      frame = 0;
      previous = 0;
      node.dataset.motion = 'paused';
    };
    const fallback = () => {
      if (stopped || failed) return;
      failed = true;
      pause();
      renderer?.dispose();
      renderer = null;
      setReadyTheme(null);
    };
    const tick = now => {
      frame = 0;
      if (!canRun()) return;
      if (previous) seconds += Math.min(80, now - previous) / 1000;
      previous = now;
      if (now - lastPaint >= 1000 / 30) {
        renderer.draw(seconds);
        lastPaint = now;
      }
      if (canRun()) frame = requestAnimationFrame(tick);
    };
    const resume = () => {
      if (!canRun()) { pause(); return; }
      node.dataset.motion = 'running';
      if (!frame) frame = requestAnimationFrame(tick);
    };
    const resize = () => {
      if (!renderer || stopped || failed) return;
      const { width, height } = node.getBoundingClientRect();
      if (width <= 0 || height <= 0) { pause(); return; }
      try {
        renderer.resize(width, height, window.devicePixelRatio);
        renderer.draw(seconds);
      } catch { fallback(); }
    };
    const observer = new ResizeObserver(resize);
    observer.observe(node);
    const intersection = new IntersectionObserver(entries => {
      visible = entries[0]?.isIntersecting ?? true;
      resume();
    });
    intersection.observe(node);
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('resize', resize);

    const image = new Image();
    image.onload = () => {
      if (stopped) return;
      try {
        renderer = createHomeOrnamentMotion(canvas.current, { image, subject: theme, onError: fallback });
        resize();
        if (!failed) { setReadyTheme(theme); resume(); }
      } catch { fallback(); }
    };
    image.onerror = fallback;
    image.src = ARTWORK[theme];

    return () => {
      stopped = true;
      pause();
      image.onload = null;
      image.onerror = null;
      observer.disconnect();
      intersection.disconnect();
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('resize', resize);
      renderer?.dispose();
    };
  }, [theme, reduced]);

  const ready = readyTheme === theme && !reduced;
  return <div className="home-ornament" ref={host} data-theme={theme}
    data-renderer={theme === 'glass' ? 'css' : ready ? 'webgl' : 'static'} aria-hidden="true">
    {theme === 'glass' ? <HomeWaterRipples reduced={reduced} onMotionChange={updateRippleMotion}/> : <>
      <img key={`image-${theme}`} src={ARTWORK[theme]} alt="" draggable={false}/>
      <canvas key={`canvas-${theme}`} ref={canvas}/>
    </>}
  </div>;
}
