import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { GRAPH_THEME_MATERIALS, normalizeGraphTheme, readGraphMaterial, writeGraphMaterial } from './graph-material-preferences.js';

function currentGraphTheme() {
  const theme = globalThis.document?.documentElement.dataset.theme;
  if (Object.hasOwn(GRAPH_THEME_MATERIALS, theme)) return theme;
  const requested = new URLSearchParams(globalThis.location?.search || '').get('theme');
  if (Object.hasOwn(GRAPH_THEME_MATERIALS, requested)) return requested;
  try { return normalizeGraphTheme(globalThis.localStorage?.getItem('koohub-appearance')); }
  catch { return 'jade'; }
}

export function useGraphMaterial() {
  const session = useRef(new Map());
  const [appearance, setAppearance] = useState(() => {
    const theme = currentGraphTheme();
    return { theme, material: readGraphMaterial(theme) };
  });
  useLayoutEffect(() => {
    const root = document.documentElement;
    const update = () => {
      const theme = currentGraphTheme();
      setAppearance(current => current.theme === theme ? current : {
        theme, material: session.current.get(theme) ?? readGraphMaterial(theme),
      });
    };
    // ThemeSwitcher commits in a layout effect; also read once after mounting
    // so an already-committed change cannot be missed by the observer.
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  const chooseMaterial = useCallback(value => {
    const theme = currentGraphTheme(), material = writeGraphMaterial(value, theme);
    // Preserve manual choices within this session even if storage is blocked.
    session.current.set(theme, material);
    setAppearance({ theme, material });
  }, []);
  return [appearance.material, chooseMaterial];
}
