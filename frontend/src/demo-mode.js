// A deployment may default to demo; explicit valid URL themes still take priority.
// Demo is never a saved appearance or a menu option.
export function isDemoMode(search = globalThis.location?.search || '', defaultTheme = import.meta.env?.VITE_WENSHU_DEFAULT_THEME) {
  const requested = new URLSearchParams(search).get('theme');
  if (['demo', 'jade', 'glass', 'civic'].includes(requested)) return requested === 'demo';
  return defaultTheme === 'demo';
}

export function demoDestination(destination, demo = isDemoMode()) {
  return demo && !['library', 'writing'].includes(destination) ? 'library' : destination;
}

export function updateDemoTitle(destination = 'library') {
  if (isDemoMode()) document.title = destination === 'writing' ? '文稿写作' : '文档阅读';
}

export function initializeDemoMode() {
  if (!isDemoMode()) return;
  document.documentElement.dataset.theme = 'demo';
  document.querySelector('link[rel="icon"]')?.setAttribute('href', 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E');
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', '#f4f5f7');
  updateDemoTitle();
}
