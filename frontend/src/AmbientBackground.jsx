import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { drawAmbientScene } from './ambient-scenes';

// One decorative canvas lives outside the status popover, so closing that
// popover never hides the atmosphere or restarts the current workspace.
export function AmbientBackground({ theme }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let width = 1, height = 1, frame = 0, lastFrame = 0, time = 8;
    let energy = 0, targetEnergy = 0;
    let surfacePointer = '';
    let pointerActive = false;
    const pointer = { x: 0, y: 0 };
    const target = { x: 0, y: 0 };
    const flow = { x: 0, y: 0 };

    const paint = () => {
      drawAmbientScene(ctx, { theme, width, height, time, pointer, energy, flow });
      const nextSurfacePointer = `${pointer.x.toFixed(3)},${pointer.y.toFixed(3)}`;
      if (theme === 'glass' && nextSurfacePointer !== surfacePointer) {
        // The same eased pointer moves surface lighting, never the text or layout.
        const style = document.documentElement.style;
        style.setProperty('--glass-flow-x', `${((pointer.x + 1) * 50).toFixed(1)}%`);
        style.setProperty('--glass-flow-y', `${((pointer.y + 1) * 50).toFixed(1)}%`);
        style.setProperty('--glass-edge-shift-x', `${(pointer.x * 2.8 + .4).toFixed(2)}px`);
        style.setProperty('--glass-edge-shift-y', `${(pointer.y * 2.4 + .4).toFixed(2)}px`);
        surfacePointer = nextSurfacePointer;
      }
    };
    const resize = () => {
      width = window.innerWidth;
      height = window.innerHeight;
      // Limit backing-store size on large/high-DPI desktop displays.
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5, 2560 / width);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      paint();
    };
    const animate = now => {
      frame = requestAnimationFrame(animate);
      if (now - lastFrame < 1000 / 30) return;
      const dt = lastFrame ? Math.min((now - lastFrame) / 1000, .08) : 1 / 30;
      lastFrame = now;
      time += dt;
      const damping = 1 - Math.exp(-dt * (theme === 'glass' ? 6.2 : 4.8));
      const previousX = pointer.x, previousY = pointer.y;
      pointer.x += (target.x - pointer.x) * damping;
      pointer.y += (target.y - pointer.y) * damping;
      const velocityX = pointerActive ? Math.max(-1, Math.min(1, (pointer.x - previousX) / dt * .6)) : 0;
      const velocityY = pointerActive ? Math.max(-1, Math.min(1, (pointer.y - previousY) / dt * .6)) : 0;
      const flowDamping = 1 - Math.exp(-dt * 7);
      flow.x += (velocityX - flow.x) * flowDamping;
      flow.y += (velocityY - flow.y) * flowDamping;
      energy += (targetEnergy - energy) * damping;
      targetEnergy *= Math.exp(-dt * 1.7);
      paint();
    };
    const syncMotion = () => {
      cancelAnimationFrame(frame);
      frame = 0;
      lastFrame = 0;
      canvas.dataset.motion = motion.matches ? 'still' : document.hidden ? 'paused' : 'running';
      if (motion.matches) {
        pointer.x = pointer.y = target.x = target.y = energy = targetEnergy = 0;
        flow.x = flow.y = 0;
        pointerActive = false;
        paint();
      } else if (!document.hidden) {
        frame = requestAnimationFrame(animate);
      }
    };
    const move = event => {
      if (motion.matches || document.hidden || event.pointerType === 'touch') return;
      const x = Math.max(0, Math.min(1, event.clientX / width));
      const y = Math.max(0, Math.min(1, event.clientY / height));
      const nextX = x * 2 - 1, nextY = y * 2 - 1;
      const distance = Math.hypot(nextX - target.x, nextY - target.y);
      targetEnergy = Math.min(.65, targetEnergy + distance * 1.2);
      pointerActive = true;
      target.x = nextX;
      target.y = nextY;
    };
    const release = () => { pointerActive = false; target.x = target.y = targetEnergy = 0; };
    const leave = event => { if (!event.relatedTarget) release(); };

    resize();
    syncMotion();
    window.addEventListener('resize', resize, { passive: true });
    window.addEventListener('pointermove', move, { passive: true });
    window.addEventListener('pointerout', leave, { passive: true });
    window.addEventListener('blur', release);
    document.addEventListener('visibilitychange', syncMotion);
    motion.addEventListener('change', syncMotion);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', resize);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerout', leave);
      window.removeEventListener('blur', release);
      document.removeEventListener('visibilitychange', syncMotion);
      motion.removeEventListener('change', syncMotion);
      if (theme === 'glass') {
        ['--glass-flow-x', '--glass-flow-y', '--glass-edge-shift-x', '--glass-edge-shift-y'].forEach(name => document.documentElement.style.removeProperty(name));
      }
    };
  }, [theme]);

  return createPortal(<canvas ref={canvasRef} className="ambient-background" data-ambient-theme={theme} aria-hidden="true" />, document.body);
}
