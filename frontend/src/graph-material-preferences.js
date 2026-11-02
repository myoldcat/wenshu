export const GRAPH_MATERIAL_STORAGE_KEY = 'koohub-graph-material';
export const DEFAULT_GRAPH_MATERIAL = 'liquid';
export const GRAPH_THEME_MATERIALS = Object.freeze({ jade: DEFAULT_GRAPH_MATERIAL, glass: DEFAULT_GRAPH_MATERIAL, civic: DEFAULT_GRAPH_MATERIAL });
export const GRAPH_MATERIALS = [
  { id: 'warm', label: '温润', description: '陶、石与纸的柔和质感' },
  { id: 'liquid', label: '透明', description: '通透、轻盈的流动质感' },
  { id: 'metal', label: '金属', description: '圆润、细腻的金属光泽' },
];

export function normalizeGraphMaterial(value, fallback = DEFAULT_GRAPH_MATERIAL) {
  return GRAPH_MATERIALS.some(material => material.id === value) ? value : fallback;
}

export function normalizeGraphTheme(theme) {
  return Object.hasOwn(GRAPH_THEME_MATERIALS, theme) ? theme : 'jade';
}

export function defaultGraphMaterial(theme) {
  return GRAPH_THEME_MATERIALS[normalizeGraphTheme(theme)];
}

export function readGraphMaterial(theme = 'jade', storage) {
  const fallback = defaultGraphMaterial(theme);
  try {
    // The former global preference has no theme association. Leave it intact,
    // but do not let it override the new defaults for all three themes.
    return normalizeGraphMaterial((storage ?? globalThis.localStorage)?.getItem(`${GRAPH_MATERIAL_STORAGE_KEY}:${normalizeGraphTheme(theme)}`), fallback);
  } catch {
    return fallback;
  }
}

export function writeGraphMaterial(value, theme = 'jade', storage) {
  const material = normalizeGraphMaterial(value, defaultGraphMaterial(theme));
  try {
    (storage ?? globalThis.localStorage)?.setItem(`${GRAPH_MATERIAL_STORAGE_KEY}:${normalizeGraphTheme(theme)}`, material);
  } catch {
    // Keep the current session usable when the browser blocks preference storage.
  }
  return material;
}
