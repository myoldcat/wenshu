import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { Diamond, Drop, Bank, Check } from '@phosphor-icons/react';
import { AmbientBackground } from './AmbientBackground';
import { isDemoMode } from './demo-mode';
import jadeMark from './assets/brand/symbol-jade-v7.png';
import glassMark from './assets/brand/symbol-glass-v7.png';
import goldMark from './assets/brand/symbol-gold-v7.png';

const brandIcons = { jade: jadeMark, glass: glassMark, civic: goldMark };

const themes = [
  { id: 'jade', name: '青玉', note: '青绿渐染 · 柔和流动', color: '#e4eee5', Icon: Diamond },
  { id: 'glass', name: '流动玻璃', note: '通透边缘 · 水色流动', color: '#dcecff', Icon: Drop },
  { id: 'civic', name: '丹朱鎏金', note: '绯红渐染 · 舒缓流动', color: '#8c1421', Icon: Bank },
];

function initialTheme() {
  if (isDemoMode()) return 'demo';
  const requested = new URLSearchParams(location.search).get('theme');
  if (themes.some(t => t.id === requested)) return requested;
  try {
    const stored = localStorage.getItem('koohub-appearance');
    return themes.some(t => t.id === stored) ? stored : 'civic';
  }
  catch { return 'civic'; }
}

export function ThemeSwitcher() {
  const [theme, setTheme] = useState(initialTheme);
  const requestedTheme = useRef(theme);
  const switchVersion = useRef(0);
  const activeTransition = useRef(null);

  // Commit the appearance before the browser captures the new view. Nothing in
  // the workspace is remounted, so editing, selection and scroll stay in place.
  useLayoutEffect(() => {
    if (theme === 'demo') return;
    document.documentElement.dataset.theme = theme;
    document.querySelector('link[rel="icon"]')?.setAttribute('href', brandIcons[theme]);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', themes.find(t => t.id === theme).color);
  }, [theme]);

  useEffect(() => {
    if (theme === 'demo') return;
    try { localStorage.setItem('koohub-appearance', theme); } catch { /* A private browser may disable storage. */ }
    const url = new URL(location.href);
    url.searchParams.set('theme', theme);
    history.replaceState(null, '', url);
  }, [theme]);

  const changeTheme = id => {
    if (requestedTheme.current === id) return;
    requestedTheme.current = id;
    const version = ++switchVersion.current;
    activeTransition.current?.skipTransition();
    activeTransition.current = null;

    const commit = () => {
      // A newer click may arrive before a queued capture callback executes.
      if (version !== switchVersion.current) return;
      flushSync(() => setTheme(id));
    };
    const root = document.documentElement;
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!document.startViewTransition || reducedMotion) {
      root.dataset.themeTransitionMode = reducedMotion ? 'reduced' : 'colors';
      delete root.dataset.themeTransition;
      commit();
      return;
    }

    try {
      root.dataset.themeTransitionMode = 'crossfade';
      // Cover the capture phase as well as the animation phase. During native
      // capture some browsers temporarily retarget pointer events to the root.
      root.dataset.themeTransition = 'crossfade';
      const transition = document.startViewTransition(() => {
        if (version !== switchVersion.current) return;
        // The crossfade supplies the animation; avoid capturing halfway through
        // a simultaneous color transition on the underlying surfaces.
        commit();
      });
      activeTransition.current = transition;
      const finish = () => {
        if (activeTransition.current !== transition) return;
        activeTransition.current = null;
        delete root.dataset.themeTransition;
      };
      // Skipping a transition (rapid clicks, a hidden tab) is a normal outcome.
      transition.ready.catch(() => {});
      transition.updateCallbackDone.catch(() => {
        if (version === switchVersion.current) commit();
      });
      transition.finished.then(finish, finish);
    } catch {
      root.dataset.themeTransitionMode = 'colors';
      delete root.dataset.themeTransition;
      commit();
    }
  };

  useEffect(() => () => {
    ++switchVersion.current;
    activeTransition.current?.skipTransition();
    activeTransition.current = null;
    delete document.documentElement.dataset.themeTransition;
  }, []);

  useEffect(() => {
    if (theme === 'demo') return;
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    const move = event => {
      if (motion.matches || event.pointerType === 'touch' || frame) return;
      const { clientX, clientY, target } = event;
      frame = requestAnimationFrame(() => {
        document.documentElement.style.setProperty('--light-x', `${(clientX / innerWidth * 100).toFixed(1)}%`);
        document.documentElement.style.setProperty('--light-y', `${(clientY / innerHeight * 100).toFixed(1)}%`);
        const control = target.closest?.('.btn,.nav-item,.theme-option,.icon-btn,.filter-trigger');
        if (control) {
          const rect = control.getBoundingClientRect();
          control.style.setProperty('--shine-x', `${((clientX - rect.left) / rect.width * 100).toFixed(1)}%`);
          control.style.setProperty('--shine-y', `${((clientY - rect.top) / rect.height * 100).toFixed(1)}%`);
        }
        frame = 0;
      });
    };
    window.addEventListener('pointermove', move, { passive: true });
    return () => { window.removeEventListener('pointermove', move); cancelAnimationFrame(frame); };
  }, []);

  if (theme === 'demo') return null;
  return <><AmbientBackground theme={theme} /><section className="theme-switcher" aria-label="工作台外观">
    <div className="theme-options">
      {themes.map(({ id, name, note, Icon }) => <button key={id} type="button" className={`theme-option ${theme === id ? 'active' : ''}`} aria-label={`切换为${name}主题`} aria-pressed={theme === id} title={`${name} · ${note}`} onClick={() => changeTheme(id)}>
        <span className={`theme-symbol ${id}`}><Icon size={19} weight="duotone" /></span>
        <span className="theme-option-copy"><strong>{name}</strong><small>{note}</small></span>
        {theme === id && <Check className="theme-check" size={14} weight="bold" />}
      </button>)}
    </div>
  </section></>;
}
