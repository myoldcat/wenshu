import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Graph, NodeEvent } from '@antv/g6';
import { G6GraphCanvas } from '../src/G6GraphCanvas.jsx';
import { useGraphMaterial } from '../src/useGraphMaterial.js';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/graph-workspace.css';

// This fixture mounts the production renderer directly. No knowledge API,
// database, graph query, model request or persistent user preference is used.
const params = new URLSearchParams(location.search);
const themeMaterialMode = params.get('test') === 'theme-materials';
const themeStorageData = new Map(), themeStorageOperations = [];
const isolatedThemeStorage = {
  get length() { return themeStorageData.size; },
  key(index) { return [...themeStorageData.keys()][Number(index)] ?? null; },
  getItem(key) { key = String(key); themeStorageOperations.push({ action: 'get', key }); return themeStorageData.get(key) ?? null; },
  setItem(key, value) { key = String(key); value = String(value); themeStorageOperations.push({ action: 'set', key, value }); themeStorageData.set(key, value); },
  removeItem(key) { key = String(key); themeStorageOperations.push({ action: 'remove', key }); themeStorageData.delete(key); },
  clear() { themeStorageOperations.push({ action: 'clear' }); themeStorageData.clear(); },
};
if (themeMaterialMode) {
  // Replace this fixture window's storage before mounting the real hook. Do
  // not read, copy, clear, write or restore any real user's stored preferences.
  Object.defineProperty(window, 'localStorage', { configurable: true, value: isolatedThemeStorage });
  themeStorageData.set('koohub-graph-material', 'metal');
  themeStorageData.set('fixture-isolation-sentinel', 'preserve');
}
let themeHookSerial = 0;
function ThemeMaterialBridge({ onChange }) {
  const [material, chooseMaterial] = useGraphMaterial();
  const [mountId] = useState(() => ++themeHookSerial);
  useLayoutEffect(() => { onChange(material, chooseMaterial, mountId); }, [material, chooseMaterial, mountId, onChange]);
  return null;
}
// Explicit fixture-only override: the default page keeps the real system
// preference. ?reduce=off supplies controllable MediaQueryLists solely for the
// reduced-motion query so both runtime branches can be exercised on any host.
const nativeMatchMedia = window.matchMedia.bind(window);
const controlledMotion = params.get('reduce') === 'off' ? { reduced: false, queries: new Map() } : null;
if (controlledMotion) window.matchMedia = query => {
  const match = String(query).match(/^\(\s*prefers-reduced-motion\s*:\s*(reduce|no-preference)\s*\)$/u);
  if (!match) return nativeMatchMedia(query);
  const preference = match[1], media = `(prefers-reduced-motion: ${preference})`;
  if (!controlledMotion.queries.has(preference)) {
    const list = new EventTarget();
    Object.defineProperties(list, {
      media: { value: media, enumerable: true },
      matches: { get: () => preference === 'reduce' ? controlledMotion.reduced : !controlledMotion.reduced, enumerable: true },
    });
    list.onchange = null;
    list.addListener = callback => list.addEventListener('change', callback);
    list.removeListener = callback => list.removeEventListener('change', callback);
    list.addEventListener('change', event => list.onchange?.call(list, event));
    controlledMotion.queries.set(preference, list);
  }
  return controlledMotion.queries.get(preference);
};
const motionEnvironment = () => ({ mode: controlledMotion ? 'simulated-media-query' : 'native-system',
  reduced: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  nativeReduced: nativeMatchMedia('(prefers-reduced-motion: reduce)').matches });
function setReducedMotion(value) {
  if (!controlledMotion) throw new Error('只有显式 ?reduce=off 的隔离测试页可以模拟系统动效偏好。');
  if (controlledMotion.reduced === !!value) return;
  controlledMotion.reduced = !!value;
  for (const list of controlledMotion.queries.values()) {
    const event = typeof MediaQueryListEvent === 'function'
      ? new MediaQueryListEvent('change', { matches: list.matches, media: list.media }) : new Event('change');
    if (!('matches' in event)) Object.defineProperties(event, { matches: { value: list.matches }, media: { value: list.media } });
    list.dispatchEvent(event);
  }
  controls?.setSimulatedReduced(controlledMotion.reduced);
}
const MATERIALS = [
  { id: 'warm', name: '温润实体', detail: '陶瓷、磨砂与温润色彩' },
  { id: 'liquid', name: '液态透明', detail: '透光、折射与轻缓流动' },
  { id: 'metal', name: '柔光金属', detail: '细腻光泽与圆润金属' },
];
const THEMES = [{ id: 'jade', name: '青玉' }, { id: 'glass', name: '琉璃' }, { id: 'civic', name: '丹朱' }];
const scope = 'isolated-material-preview';
const rootId = 'material-root';
const initialMaterial = MATERIALS.some(item => item.id === params.get('material')) ? params.get('material') : 'warm';
const initialTheme = THEMES.some(item => item.id === params.get('theme')) ? params.get('theme') : 'jade';
const initialMotion = params.get('motion') !== 'off';
const nodes = [
  { id: rootId, type: 'concept', label: '知识与治理' },
  { id: 'material-ai', type: 'concept', label: '人工智能' },
  { id: 'material-lab', type: 'entity', label: '研究机构' },
  { id: 'material-method', type: 'concept', subtype: 'method', label: '知识组织' },
  { id: 'material-person', type: 'entity', subtype: 'person', label: '研究人员' },
  { id: 'material-data', type: 'fact', label: '数据质量' },
  { id: 'material-claim', type: 'claim', label: '开放与协作' },
  { id: 'material-privacy', type: 'concept', label: '隐私保护' },
  { id: 'material-evidence', type: 'fact', label: '证据溯源' },
  { id: 'material-evaluation', type: 'concept', subtype: 'method', label: '模型评估' },
  { id: 'material-source-1', type: 'document', label: '治理研究与实践', document: { source: '研究报告' } },
  { id: 'material-source-2', type: 'document', label: '公共服务观察', document: { source: '专题资料' } },
];
const longDocumentNodes = {
  'material-source-1': { label: '城市公共服务数字化转型与治理机制研究报告', source: '公共治理与数字化转型联合研究中心专题资料' },
  'material-source-2': { label: 'InternationalGovernanceAndPublicServiceResearchReport', source: 'International Public Service Research Institute' },
};
const fixedPositions = new Map([
  [rootId, { x: 560, y: 275 }],
  ['material-ai', { x: 355, y: 135 }], ['material-lab', { x: 765, y: 135 }],
  ['material-method', { x: 200, y: 285 }], ['material-person', { x: 920, y: 285 }],
  ['material-data', { x: 365, y: 380 }], ['material-claim', { x: 755, y: 380 }],
  ['material-privacy', { x: 150, y: 105 }], ['material-evidence', { x: 970, y: 105 }],
  ['material-evaluation', { x: 560, y: 485 }],
  ['material-source-1', { x: 145, y: 475 }], ['material-source-2', { x: 975, y: 475 }],
]);
const pairs = [
  [rootId, 'material-ai'], [rootId, 'material-lab'], [rootId, 'material-method'], [rootId, 'material-person'],
  [rootId, 'material-data'], [rootId, 'material-claim'], ['material-ai', 'material-privacy'],
  ['material-lab', 'material-evidence'], ['material-data', 'material-evaluation'], ['material-claim', 'material-evaluation'],
  ['material-method', 'material-source-1'], ['material-person', 'material-source-2'],
];
const edges = pairs.map(([from, to], index) => ({ id: `material-edge-${index}`, from, to,
  type: to.startsWith('material-source') ? 'source' : 'association',
  layer: to.startsWith('material-source') ? 'provenance' : 'semantic',
  evidence: [{ blockId: `synthetic-block-${index}` }], label: '' }));
const positionCache = new Map([[scope, new Map(fixedPositions)]]);
const cameraCache = new Map([[scope, { renderer: 'g6', zoom: 1, center: [560, 295] }]]);
const runtimeErrors = [], apiRequests = [], instances = [];
let lastGraph, controls, suiteRunning = false, resultValue = { status: 'loading', checks: [] };
const originalSetSize = Graph.prototype.setSize;
Graph.prototype.setSize = function (...args) {
  if (this.options?.container?.closest?.('#graph-materials-root')) {
    lastGraph = this;
    if (!instances.includes(this)) instances.push(this);
  }
  return originalSetSize.apply(this, args);
};
const nativeFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
  if (/\/api\//.test(url.pathname)) {
    apiRequests.push({ url: url.pathname, method: init?.method || 'GET' });
    return Promise.reject(new Error(`材质预览禁止访问业务 API：${url.pathname}`));
  }
  return nativeFetch(input, init);
};
window.addEventListener('error', event => runtimeErrors.push(event.message));
window.addEventListener('unhandledrejection', event => runtimeErrors.push(event.reason?.message || String(event.reason)));

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const nextFrame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
const assert = (value, message) => { if (!value) throw new Error(message); };
const waitFor = async (read, message, timeout = 20000) => {
  const deadline = performance.now() + timeout;
  while (performance.now() < deadline) {
    const value = read();
    if (value) return value;
    await sleep(25);
  }
  throw new Error(`等待超时：${message}`);
};
const ready = async (material = controls?.material) => {
  const shell = await waitFor(() => {
    const element = document.querySelector('.gw-g6-shell');
    return element?.dataset.renderState === 'ready' && element.dataset.renderedCount === String(nodes.length)
      && element.dataset.material === material && element.dataset.renderedMaterial === material
      && ['true', 'ready', '1'].includes(element.dataset.materialReady) && element;
  }, `实际 ${material} 材质、图片与 G6 绘制完成`);
  await nextFrame();
  assert(lastGraph && !lastGraph.destroyed, '实际 G6 实例必须可用');
  return shell;
};
const elementFor = id => lastGraph.context.element.getElement(id);
const camera = () => ({ zoom: lastGraph.getZoom(), center: [...lastGraph.getViewportCenter()] });
// G6 returns [x, y, z] even for this two-dimensional canvas. Compare the same
// two coordinates as the fixture/cache, rather than comparing z to undefined.
const positions = () => nodes.map(node => [node.id, ...lastGraph.getElementPosition(node.id).slice(0, 2)]);
const samePositions = (a, b, tolerance = .05) => a.length === b.length
  && a.every((point, index) => point[0] === b[index][0] && point.slice(1).every((value, axis) => Math.abs(value - b[index][axis + 1]) <= tolerance));
const sameCamera = (a, b) => Math.abs(a.zoom - b.zoom) < .0001 && a.center.every((value, index) => Math.abs(value - b.center[index]) < .75);
const runningAnimations = shape => (shape?.getAnimations?.() || []).filter(animation => ['running', 'pending'].includes(animation.playState));
const animationTime = animation => Number(animation.currentTime || 0);
const srcOf = shape => typeof shape?.attributes.src === 'string' ? shape.attributes.src : shape?.attributes.src?.src;
const graphicSnapshot = shape => JSON.stringify({ transform: shape?.attributes.transform, opacity: shape?.attributes.opacity,
  matrix: shape?.getLocalTransform?.() ? [...shape.getLocalTransform()] : [] });
const keyGeometry = () => JSON.stringify(nodes.map(node => {
  const key = elementFor(node.id).getShape('key');
  return { id: node.id, center: [...key.getBounds().center], r: key.attributes.r,
    width: key.attributes.width, height: key.attributes.height, transform: key.attributes.transform };
}));
const skinOpacity = id => Number(elementFor(id).getShape('material-skin').attributes.opacity ?? 1);
const assertOpacity = (id, expected) => assert(Math.abs(skinOpacity(id) - expected) < .001, `${id} 材质透明度应为 ${expected}，实际为 ${skinOpacity(id)}`);

function checkDocumentTextBounds() {
  return nodes.filter(node => node.type === 'document').map(node => {
    const element = elementFor(node.id), sprite = element.attributes.materialSprite;
    const skin = element.getShape('material-skin'), center = element.getShape('key').getBounds().center;
    const width = sprite.body.width * Number(skin.attributes.width) / sprite.width;
    const height = sprite.body.height * Number(skin.attributes.height) / sprite.height;
    const box = { left: center[0] - width / 2, right: center[0] + width / 2, top: center[1] - height / 2, bottom: center[1] + height / 2 };
    const icon = element.getShape('icon').getBounds();
    assert(icon.min[0] >= box.left && icon.max[0] <= box.right, '文档图标完整位于实际 PNG 胶囊内');
    const lines = ['document-title', 'document-source'].map(name => {
      const shape = element.getShape(name), bounds = shape.getBounds(), metrics = shape.parsedStyle.metrics;
      assert(bounds.min[0] >= box.left - .5 && bounds.max[0] <= box.right + .5, `${name} 的真实绘制宽度位于 PNG 胶囊内`);
      assert(bounds.min[1] >= box.top - .5 && bounds.max[1] <= box.bottom + .5, `${name} 的真实绘制高度位于 PNG 胶囊内`);
      assert(bounds.min[0] >= icon.max[0] + 6, `${name} 不与文档图标重叠`);
      assert(metrics?.lines?.length === 1, `${name} 实际只绘制一行`);
      assert(shape.isOverflowing() && /(?:\.\.\.|…)$/u.test(metrics.lines[0]), `${name} 的长中文或英文实际显示省略号`);
      assert(Number(metrics.maxLineWidth) <= Number(shape.attributes.wordWrapWidth) + .5, `${name} 的实测字体宽度遵守可用宽度`);
      return { name, text: metrics.lines[0], measuredWidth: metrics.maxLineWidth, availableWidth: shape.attributes.wordWrapWidth };
    });
    return { id: node.id, actualBodyWidth: width, actualBodyHeight: height, lines };
  });
}

async function stagePreview() {
  const shell = await ready();
  const graph = lastGraph;
  // The production first mount computes its normal layout. Set only the
  // synthetic fixture's displayed spread, then commit through the same cache
  // path as a completed user drag. Tests below never force positions again.
  graph.updateNodeData([...fixedPositions].map(([id, point]) => ({ id, style: point })));
  await graph.draw();
  graph.emit(NodeEvent.DRAG_END, { target: elementFor(rootId), type: NodeEvent.DRAG_END });
  await nextFrame(); await ready();
  assert(samePositions(positions(), nodes.map(node => [node.id, fixedPositions.get(node.id).x, fixedPositions.get(node.id).y]), .1), '合成预览位置已真实提交');
  shell.dataset.fixtureReady = 'true';
  resultValue = { status: 'ready', checks: [] };
  controls.setResult(resultValue);
  controls.setStatus('ready');
  return graph;
}

async function checkImagesAndShapes(material) {
  const sources = new Set();
  for (const node of nodes) {
    const element = elementFor(node.id), key = element?.getShape('key'), skin = element?.getShape('material-skin');
    assert(key && Number(key.attributes.lineWidth || 0) === 0, `${node.label} 没有硬描边`);
    assert(skin && srcOf(skin), `${node.label} 具有真实材质图片`);
    assert(skin.attributes.pointerEvents === 'none', `${node.label} 材质不会遮挡节点交互`);
    sources.add(srcOf(skin));
    const icon = element.getShape('icon');
    if (icon) assert(Number(icon.attributes.zIndex || 0) > Number(skin.attributes.zIndex || 0), `${node.label} 图标高于材质`);
  }
  await Promise.all([...sources].map(src => new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => image.naturalWidth > 0 && image.naturalHeight > 0 ? resolve() : reject(new Error(`无效材质图片：${src}`));
    image.onerror = () => reject(new Error(`材质图片加载失败：${src}`));
    image.src = src;
  })));
  const selected = elementFor(controls.selectedId), halo = selected.getShape('halo');
  assert(halo && Number(halo.attributes.lineWidth || 0) === 0, '所选节点用光晕而非描边');
  assert(Number(halo.attributes.shadowBlur || 0) > 0, '所选节点光晕具有柔和阴影');
  return { material, nodes: nodes.length, uniqueImages: sources.size,
    sourceFiles: [...sources].map(src => src.startsWith('data:') ? `decoded PNG (${src.length} encoded characters)` : src.split('/').at(-1)), selected: controls.selectedId };
}

function Fixture() {
  const [material, setRenderedMaterial] = useState(themeMaterialMode ? 'liquid' : initialMaterial);
  const [theme, setTheme] = useState(initialTheme);
  const [themeHookGeneration, setThemeHookGeneration] = useState(0), [themeHookMountId, setThemeHookMountId] = useState(0);
  const themeChoiceRef = useRef(null);
  const receiveThemeMaterial = useCallback((value, choose, mountId) => {
    themeChoiceRef.current = choose; setRenderedMaterial(value); setThemeHookMountId(mountId);
  }, []);
  const setMaterial = themeMaterialMode ? value => {
    if (!themeChoiceRef.current) throw new Error('主题材质偏好尚未挂载');
    themeChoiceRef.current(value);
  } : setRenderedMaterial;
  const remountThemeHook = () => setThemeHookGeneration(value => value + 1);
  const [motion, setMotion] = useState(initialMotion), [selectedId, setSelectedId] = useState('material-ai');
  const [mounted, setMounted] = useState(true), [error, setError] = useState(''), [status, setStatus] = useState('loading');
  const [hover, setHover] = useState(nodes.some(node => node.id === params.get('hover')) ? params.get('hover') : ''), [matchedIds, setMatchedIds] = useState(new Set());
  const [longDocuments, setLongDocuments] = useState(false), [visible, setVisible] = useState(true);
  const [simulatedReduced, setSimulatedReduced] = useState(controlledMotion?.reduced || false);
  const [resultOpen, setResultOpen] = useState(false), [result, setResult] = useState(resultValue), [zoom, setZoom] = useState(1);
  const canvasRef = useRef(null), staged = useRef(false);
  controls = { material, theme, motion, selectedId, mounted, hover, matchedIds, longDocuments, visible, themeHookMountId,
    setMaterial, setTheme, setMotion, setSelectedId, setMounted, setStatus, setResult, setResultOpen, setHover, setMatchedIds, setLongDocuments, setVisible, setSimulatedReduced, remountThemeHook, canvasRef };
  const previewNodes = longDocuments ? nodes.map(node => longDocumentNodes[node.id]
    ? { ...node, label: longDocumentNodes[node.id].label, document: { source: longDocumentNodes[node.id].source } } : node) : nodes;
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    if (staged.current) return;
    staged.current = true;
    stagePreview().catch(reason => {
      resultValue = { status: 'failed', checks: [], failures: [{ message: reason.message, stack: reason.stack }] };
      setResult(resultValue); setError(reason.message); setStatus('failed');
    });
  }, []);
  const chosen = MATERIALS.find(item => item.id === material);
  return <main className="materials-fixture graph-workspace" data-fixture-status={status}>
    {themeMaterialMode && <ThemeMaterialBridge key={themeHookGeneration} onChange={receiveThemeMaterial}/>}
    <header className="materials-heading"><div><h1>知识图谱</h1><p>让知识有形，让联系自然发生</p></div><div className="materials-context"><strong>节点材质预览</strong>10 个词条 · 2 份来源</div></header>
    <div className="materials-toolbar">
      <div className="materials-options" role="group" aria-label="节点材质">{MATERIALS.map(item => <button key={item.id} type="button" data-material-control={item.id} aria-pressed={material === item.id} onClick={() => setMaterial(item.id)}>{item.name}</button>)}</div>
      <label>界面主题<select aria-label="界面主题" value={theme} onChange={event => setTheme(event.target.value)}>{THEMES.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label><input type="checkbox" aria-label="图谱动效" checked={motion} onChange={event => setMotion(event.target.checked)}/>轻缓动效</label>
      {controlledMotion && <span data-motion-environment="simulated" style={{ fontSize: 11, color: 'var(--muted)' }}>隔离测试动效偏好：{simulatedReduced ? '减少动态效果' : '常规动效'}（模拟）</span>}
      {themeMaterialMode && <span data-preference-environment="memory-only" style={{ fontSize: 11, color: 'var(--muted)' }}>主题材质测试 · 仅使用内存偏好</span>}
    </div>
    <section className="materials-canvas" aria-label="材质图谱预览" style={visible ? undefined : { display: 'none' }}>
      <div className="materials-caption"><strong>{chosen.name}</strong>{chosen.detail}</div>
      {mounted && <G6GraphCanvas ref={canvasRef} nodes={previewNodes} edges={edges} rootId={rootId} selection={selectedId} hover={hover} onHover={setHover} matchedIds={matchedIds}
        onSelect={node => setSelectedId(node.id)} cameraKey={scope} cameraCache={cameraCache} positionCache={positionCache}
        material={material} theme={theme} motion={motion} onCameraChange={value => setZoom(value.zoom)} onError={setError}/>}
      <div className="materials-zoom" role="group" aria-label="预览缩放">
        <button type="button" aria-label="缩小预览" onClick={() => canvasRef.current?.zoom(1 / 1.15)}>−</button>
        <span>{Math.round(zoom * 100)}%</span>
        <button type="button" aria-label="放大预览" onClick={() => canvasRef.current?.zoom(1.15)}>＋</button>
        <button type="button" aria-label="居中所选节点" onClick={() => canvasRef.current?.focus(selectedId)}>◎</button>
      </div>
      {error && <p role="alert" className="materials-error">{error}</p>}
    </section>
    <footer className="materials-footer"><span>圆润造型 · 无硬描边 · 选中光晕</span><span>隔离合成资料，不访问业务 API 或模型</span>
      <button className="materials-run" type="button" onClick={() => setResultOpen(value => !value)} aria-expanded={resultOpen}>查看结果</button>
      <button className="materials-run" type="button" aria-label="运行材质验收" disabled={status === 'running' || status === 'loading'} onClick={() => themeMaterialMode ? runThemeSuite() : runSuite()}>运行验收</button>
    </footer>
    <pre id="graph-materials-result" className="materials-result" role="status" hidden={!resultOpen} data-status={result.status}>{JSON.stringify(result, null, 2)}</pre>
  </main>;
}

async function runThemeSuite() {
  if (!themeMaterialMode || window.localStorage !== isolatedThemeStorage) throw new Error('主题材质验收只允许在显式内存隔离模式运行');
  if (suiteRunning) return resultValue;
  suiteRunning = true;
  const checks = [], failures = [], expectedDefaults = { jade: 'liquid', glass: 'liquid', civic: 'liquid' };
  const overrides = { jade: 'metal', glass: 'warm', civic: 'liquid' }, initial = { theme: controls.theme, motion: controls.motion };
  const check = (name, evidence = {}) => checks.push({ name, ...evidence });
  const publish = status => {
    resultValue = { status, suite: 'theme-materials', checks, failures,
      preferenceEnvironment: { storage: 'isolated-memory-only', hook: 'useGraphMaterial',
        values: Object.fromEntries(themeStorageData), reads: themeStorageOperations.filter(item => item.action === 'get').length,
        mutations: themeStorageOperations.filter(item => item.action !== 'get') },
      runtimeErrors: [...runtimeErrors], apiRequests: [...apiRequests], modelCalls: 0, createdInstances: instances.length };
    controls.setResult(resultValue); controls.setStatus(status); return resultValue;
  };
  const selectTheme = async (theme, expectedMaterial) => {
    controls.setTheme(theme); await nextFrame();
    await waitFor(() => document.documentElement.dataset.theme === theme && controls.theme === theme, `界面主题 ${theme} 提交`);
    await ready(expectedMaterial);
    assert(controls.material === expectedMaterial, `真实主题偏好 hook 应为 ${theme} 选择 ${expectedMaterial}`);
    assert(lastGraph.getNodeData().every(node => node.style.materialId === expectedMaterial), '真实 G6 全部节点使用当前主题材质');
  };
  const remountHook = async expectedMaterial => {
    const oldMount = controls.themeHookMountId;
    controls.remountThemeHook(); await nextFrame();
    await waitFor(() => controls.themeHookMountId > oldMount, '实际 useGraphMaterial hook 重新挂载');
    await ready(expectedMaterial);
    assert(controls.material === expectedMaterial, '重新挂载的 hook 恢复已保存材质');
    return { previousHookMount: oldMount, currentHookMount: controls.themeHookMountId };
  };
  controls.setStatus('running'); publish('running');
  try {
    // Reset only the in-memory map. Remount the hook to clear its session cache
    // so this suite can be rerun without touching the G6 instance or real storage.
    themeStorageData.clear(); themeStorageOperations.splice(0);
    themeStorageData.set('koohub-graph-material', 'metal');
    themeStorageData.set('fixture-isolation-sentinel', 'preserve');
    controls.setMotion(false); controls.setHover(''); controls.setMatchedIds(new Set()); controls.setTheme('jade');
    await nextFrame();
    await waitFor(() => document.documentElement.dataset.theme === 'jade', '初始青玉主题提交');
    await remountHook('liquid');
    document.querySelector('[data-graph-node="material-person"]').click(); await nextFrame(); await ready('liquid');
    await controls.canvasRef.current.zoom(1.12);
    await controls.canvasRef.current.focus('material-person'); await ready('liquid');
    const graph = lastGraph, beforePositions = positions(), beforeCamera = camera(), selected = controls.selectedId;
    const assertView = () => {
      assert(lastGraph === graph, '主题与材质变化复用同一个真实 G6 实例');
      assert(samePositions(beforePositions, positions()), '主题与材质变化保留节点位置');
      assert(sameCamera(beforeCamera, camera()), '主题与材质变化保留缩放和相机中心');
      assert(controls.selectedId === selected && document.querySelector('.gw-g6-shell').dataset.renderedSelected === selected, '主题与材质变化保留所选节点');
    };
    for (const theme of ['jade', 'glass', 'civic']) {
      await selectTheme(theme, expectedDefaults[theme]); assertView();
      assert(!themeStorageData.has(`koohub-graph-material:${theme}`), '未手动选择时不写入主题覆盖');
      check('未手动选择时采用对应主题默认材质，真实渲染与视图保持', {
        theme, expected: expectedDefaults[theme], ...await checkImagesAndShapes(expectedDefaults[theme]),
      });
    }
    assert(themeStorageData.get('koohub-graph-material') === 'metal', '旧全局材质键被忽略但保留');
    assert(!themeStorageOperations.some(item => item.action !== 'get'), '只切换主题默认材质不写存储');
    check('旧全局材质不覆盖三主题默认值，且原键未删除');

    const saved = {};
    for (const theme of ['jade', 'glass', 'civic']) {
      await selectTheme(theme, expectedDefaults[theme]);
      document.querySelector(`[data-material-control="${overrides[theme]}"]`).click(); await nextFrame(); await ready(overrides[theme]);
      saved[theme] = overrides[theme];
      assert(themeStorageData.get(`koohub-graph-material:${theme}`) === overrides[theme], '真实手动选择仅保存到当前主题键');
      for (const candidate of ['jade', 'glass', 'civic']) assert(themeStorageData.get(`koohub-graph-material:${candidate}`) === saved[candidate], '手动覆盖不会污染其他主题偏好');
      assertView();
      check('手动材质覆盖按当前主题独立保存', { theme, material: overrides[theme] });
    }
    for (const theme of ['jade', 'glass', 'civic', 'jade']) {
      await selectTheme(theme, overrides[theme]); assertView();
      check('切回主题恢复该主题的手动材质', { theme, material: overrides[theme] });
    }
    for (const theme of ['glass', 'jade', 'civic', 'jade', 'glass']) {
      controls.setTheme(theme); await nextFrame();
    }
    await selectTheme('glass', overrides.glass); assertView();
    check('快速切换主题最终材质一致，无旧响应覆盖', { theme: 'glass', material: overrides.glass });
    const mounts = await remountHook(overrides.glass); assertView();
    check('仅重新挂载真实偏好 hook 即从内存存储恢复手动选择，画布实例保持', { ...mounts, theme: 'glass', material: overrides.glass });
    // Verify the fresh hook's subsequent theme reads use persisted per-theme
    // choices, rather than its previous mount's in-memory session Map.
    for (const theme of ['civic', 'jade', 'glass']) { await selectTheme(theme, overrides[theme]); assertView(); }
    check('新 hook 在其他主题恢复各自已保存选择');
    assert(themeStorageData.get('koohub-graph-material') === 'metal' && themeStorageData.get('fixture-isolation-sentinel') === 'preserve', '旧全局键及无关内存条目原样保留');
    assert(themeStorageOperations.filter(item => item.action !== 'get').every(item => item.action === 'set' && /^koohub-graph-material:(jade|glass|civic)$/u.test(item.key)), '只有三个主题偏好键发生写入');
    assert(!runtimeErrors.length && !document.querySelector('[role="alert"]'), `没有运行错误：${runtimeErrors.join('；')}`);
    assert(!apiRequests.length && window.localStorage === isolatedThemeStorage, '全程无业务 API、模型调用或真实 localStorage 访问');
    check('所有偏好读写仅发生在隔离内存中，无业务 API 或模型调用');
    return publish('passed');
  } catch (error) {
    failures.push({ message: error.message, stack: error.stack }); controls.setResultOpen(true); return publish('failed');
  } finally {
    controls.setTheme(initial.theme); controls.setMotion(initial.motion); suiteRunning = false;
  }
}

async function runSuite() {
  if (suiteRunning) return resultValue;
  suiteRunning = true;
  const checks = [], failures = [], initial = { material: controls.material, theme: controls.theme, motion: controls.motion,
    hover: controls.hover, matchedIds: controls.matchedIds, longDocuments: controls.longDocuments, visible: controls.visible,
    reduced: controlledMotion?.reduced };
  const check = (name, evidence = {}) => checks.push({ name, ...evidence });
  const publish = status => {
    resultValue = { status, checks, failures, motionEnvironment: motionEnvironment(), runtimeErrors: [...runtimeErrors], apiRequests: [...apiRequests], modelCalls: 0, createdInstances: instances.length };
    controls.setResult(resultValue); controls.setStatus(status); return resultValue;
  };
  const change = async (field, value) => {
    if (field === 'material') document.querySelector(`[data-material-control="${value}"]`).click();
    else controls[field === 'theme' ? 'setTheme' : 'setMotion'](value);
    await nextFrame(); await ready(field === 'material' ? value : controls.material);
  };
  const checkFading = async () => {
    // Feed actual node events through the production handler, then check the
    // rendered image child and document text, not merely the incoming data.
    lastGraph.emit(NodeEvent.POINTER_ENTER, { target: elementFor('material-ai'), type: NodeEvent.POINTER_ENTER });
    await nextFrame(); await ready();
    assertOpacity('material-ai', 1); assertOpacity(rootId, 1); assertOpacity(controls.selectedId, 1);
    for (const id of ['material-claim', 'material-source-1', 'material-source-2']) {
      assertOpacity(id, .18);
      if (id.startsWith('material-source')) for (const name of ['document-title', 'document-source']) {
        assert(Math.abs(Number(elementFor(id).getShape(name).attributes.opacity) - .18) < .001, '淡化时文档文字与材质同步');
      }
    }
    const hoverOpacity = skinOpacity('material-source-1');
    lastGraph.emit(NodeEvent.POINTER_LEAVE, { target: elementFor('material-ai'), type: NodeEvent.POINTER_LEAVE });
    await nextFrame(); await ready();
    nodes.forEach(node => assertOpacity(node.id, 1));
    controls.setMatchedIds(new Set(['material-ai'])); await nextFrame(); await ready();
    assertOpacity('material-ai', 1); assertOpacity(controls.selectedId, 1);
    for (const id of [rootId, 'material-claim', 'material-source-1', 'material-source-2']) assertOpacity(id, .18);
    const matchedOpacity = skinOpacity('material-source-1');
    const originalSelection = controls.selectedId;
    document.querySelector('[data-graph-node="material-source-1"]').click(); await nextFrame(); await ready();
    assertOpacity('material-source-1', 1);
    for (const name of ['document-title', 'document-source']) assert(Number(elementFor('material-source-1').getShape(name).attributes.opacity) === 1, '选中文档时材质和文字保持完整清晰');
    const selectedDocumentOpacity = skinOpacity('material-source-1');
    document.querySelector(`[data-graph-node="${originalSelection}"]`).click(); await nextFrame(); await ready();
    assertOpacity(originalSelection, 1); assertOpacity('material-source-1', .18);
    controls.setMatchedIds(new Set()); await nextFrame(); await ready();
    nodes.forEach(node => assertOpacity(node.id, 1));
    for (const id of ['material-source-1', 'material-source-2']) for (const name of ['document-title', 'document-source']) {
      assert(Number(elementFor(id).getShape(name).attributes.opacity) === 1, '取消淡化后文档文字恢复');
    }
    return { hoverOpacity, matchedOpacity, restoredOpacity: skinOpacity('material-source-1'), selectedOpacity: skinOpacity(controls.selectedId), selectedDocumentOpacity };
  };
  controls.setStatus('running'); publish('running');
  try {
    await ready();
    await change('motion', false);
    controls.setHover(''); controls.setMatchedIds(new Set()); controls.setLongDocuments(true);
    await nextFrame(); await ready();
    const selectable = document.querySelector('[data-graph-node="material-person"]');
    selectable.click(); await nextFrame(); await ready();
    await controls.canvasRef.current.zoom(1.12);
    await controls.canvasRef.current.focus('material-person');
    await ready();
    const graph = lastGraph, originalPositions = positions(), originalCamera = camera();
    assert(originalCamera.zoom > 1, '通过真实画布 ref 完成缩放');
    const selected = controls.selectedId;
    for (const theme of THEMES) {
      await change('theme', theme.id);
      for (const material of MATERIALS) {
        await change('material', material.id);
        assert(lastGraph === graph, '材质与主题变化复用同一个 G6 实例');
        assert(samePositions(originalPositions, positions()), '材质与主题变化保留节点位置');
        assert(sameCamera(originalCamera, camera()), '材质与主题变化保留相机');
        assert(controls.selectedId === selected && document.querySelector('.gw-g6-shell').dataset.renderedSelected === selected, '材质与主题变化保留选择');
        check('材质、主题、图标、选中光晕与视图保留', { theme: theme.id, ...await checkImagesAndShapes(material.id), camera: camera() });
        check('长中文、长英文文档标题与来源按真实字体宽度省略且不溢出', { theme: theme.id, material: material.id, documents: checkDocumentTextBounds() });
        check('悬停和搜索淡化明显、取消后恢复、所选节点始终保留', { theme: theme.id, material: material.id, ...await checkFading() });
        assert(samePositions(originalPositions, positions()) && sameCamera(originalCamera, camera()), '淡化和文档文本不会改变布局或相机');
      }
    }
    for (const material of ['liquid', 'metal', 'warm', 'liquid']) {
      controls.setMaterial(material); await nextFrame();
    }
    await ready('liquid');
    assert(lastGraph === graph && samePositions(originalPositions, positions()) && sameCamera(originalCamera, camera()), '快速材质变化只采纳最后一次且不改变视图');
    check('快速切换材质最终为液态透明', await checkImagesAndShapes('liquid'));

    await change('motion', true);
    await sleep(550); await ready();
    lastGraph.emit(NodeEvent.POINTER_ENTER, { target: elementFor('material-ai'), type: NodeEvent.POINTER_ENTER });
    await nextFrame(); await ready();
    const prefersReduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const skin = elementFor('material-person').getShape('material-skin'), animations = runningAnimations(skin);
    const before = { positions: positions(), camera: camera(), key: keyGeometry(), skin: graphicSnapshot(skin), times: animations.map(animationTime) };
    const frames = [];
    for (let index = 0; index < 10; index += 1) {
      await sleep(65); await nextFrame();
      frames.push({ positions: positions(), camera: camera(), key: keyGeometry(), skin: graphicSnapshot(skin), times: animations.map(animationTime),
        fadedOpacity: skinOpacity('material-claim'), selectedOpacity: skinOpacity(controls.selectedId) });
    }
    assert(frames.every(frame => samePositions(before.positions, frame.positions) && sameCamera(before.camera, frame.camera)), '液态纹理运动不移动节点或相机');
    assert(frames.every(frame => frame.key === before.key), '液态纹理运动不改变节点关键形状与边锚点');
    assert(frames.every(frame => Math.abs(frame.fadedOpacity - .18) < .001 && frame.selectedOpacity === 1), '液态动画逐帧保持非相关节点淡化，所选节点保持清晰');
    if (prefersReduced) {
      assert(!runningAnimations(skin).length, '系统减少动态效果时不运行液态动画');
      check('系统减少动态效果已生效', { frames: frames.length });
    } else {
      assert(animations.length > 0, '液态材质具有实际运行的子形状动画');
      assert(frames.some(frame => frame.skin !== before.skin), '液态纹理在实际帧中发生变化');
      assert(frames.some(frame => frame.times.some((time, index) => time !== before.times[index])), '液态动画时间持续推进');
      check('液态纹理真实流动且节点与相机静止', { frames: frames.length, animations: animations.length });
    }
    check('液态动画逐帧保留淡化与选中透明度', { frames: frames.length, fadedOpacity: frames.at(-1).fadedOpacity, selectedOpacity: frames.at(-1).selectedOpacity });
    lastGraph.emit(NodeEvent.POINTER_LEAVE, { target: elementFor('material-ai'), type: NodeEvent.POINTER_LEAVE });
    await nextFrame(); await ready(); nodes.forEach(node => assertOpacity(node.id, 1));
    const visibilityGraph = lastGraph, visibilityPositions = positions(), visibilityCamera = camera();
    const opticalNodes = nodes.map(node => elementFor(node.id)).filter(element => element.materialAnimation);
    controls.setVisible(false); await nextFrame();
    await waitFor(() => {
      const shell = document.querySelector('.gw-g6-shell');
      return shell && shell.clientWidth === 0 && shell.clientHeight === 0
        && (prefersReduced ? shell.dataset.materialMotion === 'off'
          : shell.dataset.materialMotion === 'paused' && Number(shell.dataset.materialAnimationsPaused) === opticalNodes.length);
    }, '隐藏画布后真实液态动画暂停');
    await nextFrame();
    const pausedTimes = opticalNodes.map(element => animationTime(element.materialAnimation));
    assert(opticalNodes.every(element => element.materialAnimation.playState === 'paused'), '所有液态节点动画进入暂停状态');
    await sleep(240);
    assert(opticalNodes.every((element, index) => Math.abs(animationTime(element.materialAnimation) - pausedTimes[index]) < 1), '隐藏期间动画时间不推进');
    controls.setVisible(true); await nextFrame(); await ready('liquid');
    if (!prefersReduced) {
      await waitFor(() => opticalNodes.every(element => element.materialAnimation.playState === 'running'), '返回可见画布后原有液态动画继续');
      await sleep(140);
      assert(opticalNodes.every((element, index) => animationTime(element.materialAnimation) > pausedTimes[index]), '返回画布后动画时间继续推进');
    }
    assert(lastGraph === visibilityGraph && samePositions(visibilityPositions, positions()) && sameCamera(visibilityCamera, camera()), '隐藏和恢复画布保留实例、节点位置和相机');
    check('隐藏画布暂停液态动画，恢复时继续且保持视图', { animations: opticalNodes.length, reducedMotion: prefersReduced });
    if (controlledMotion) {
      const preferenceGraph = lastGraph, preferencePositions = positions(), preferenceCamera = camera();
      const beforeReduction = nodes.map(node => elementFor(node.id)).filter(element => element.materialAnimation);
      assert(beforeReduction.length > 0, '模拟减少动态效果前已实际运行液态动画');
      const cancelled = beforeReduction.map(element => element.materialAnimation), cancellationStarted = performance.now();
      setReducedMotion(true);
      await waitFor(() => {
        const shell = document.querySelector('.gw-g6-shell');
        return shell?.dataset.motion === 'off' && shell.dataset.materialMotion === 'off'
          && beforeReduction.every(element => !element.materialAnimation && runningAnimations(element.getShape('material-skin')).length === 0);
      }, '模拟系统偏好 change=true 立即取消所有液态动画', 5000);
      const cancellationElapsedMs = Math.round(performance.now() - cancellationStarted);
      await ready('liquid');
      const reducedFrames = beforeReduction.map(element => graphicSnapshot(element.getShape('material-skin')));
      await sleep(180); await nextFrame();
      assert(beforeReduction.every((element, index) => graphicSnapshot(element.getShape('material-skin')) === reducedFrames[index]), '减少动态效果生效后实际材质帧保持静止');
      assert(cancelled.every(animation => !['running', 'pending'].includes(animation.playState)), '原液态动画已取消而非继续在后台运行');
      setReducedMotion(false); await nextFrame(); await ready('liquid');
      await waitFor(() => beforeReduction.every(element => element.materialAnimation?.playState === 'running'), '模拟系统偏好 change=false 恢复液态动画');
      const restarted = beforeReduction.map(element => animationTime(element.materialAnimation));
      await sleep(160); await nextFrame();
      assert(beforeReduction.every((element, index) => element.materialAnimation !== cancelled[index]
        && animationTime(element.materialAnimation) > restarted[index]), '系统偏好恢复后创建新的液态动画且时间实际推进');
      assert(lastGraph === preferenceGraph && samePositions(preferencePositions, positions()) && sameCamera(preferenceCamera, camera()), '系统动态偏好变化保留实例、节点与相机');
      check('模拟系统减少动态效果 change 立即取消，恢复后重新运行动画', {
        environment: 'explicit reduce=off fixture only', animations: beforeReduction.length, cancellationElapsedMs,
        nativeReduced: nativeMatchMedia('(prefers-reduced-motion: reduce)').matches,
      });
    }
    await change('motion', false); await sleep(550);
    const stoppedSkin = elementFor('material-person').getShape('material-skin'), stopped = graphicSnapshot(stoppedSkin);
    assert(!runningAnimations(stoppedSkin).length, '关闭动效取消液态动画');
    await sleep(260); await nextFrame();
    assert(graphicSnapshot(stoppedSkin) === stopped, '关闭动效后材质帧保持静止');
    check('动效开关停止液态动画');

    await change('motion', true); await sleep(500);
    const oldGraph = lastGraph, oldSkins = nodes.map(node => elementFor(node.id).getShape('material-skin'));
    const oldAnimations = oldSkins.flatMap(shape => runningAnimations(shape));
    controls.setMounted(false); await nextFrame();
    await waitFor(() => oldGraph.destroyed, '卸载时真实 G6 实例销毁');
    assert(oldSkins.every(shape => !runningAnimations(shape).length), '卸载取消所有材质动画');
    const frozenTimes = oldAnimations.map(animationTime);
    await sleep(180);
    assert(oldAnimations.every((animation, index) => animationTime(animation) === frozenTimes[index]), '卸载后旧动画不继续推进');
    controls.setMounted(true); controls.setMaterial('warm'); await nextFrame();
    controls.setMaterial('metal'); await nextFrame(); controls.setMaterial('liquid'); await nextFrame();
    await ready('liquid');
    assert(lastGraph !== oldGraph && !lastGraph.destroyed, '重新挂载得到可用的新 G6 实例');
    assert(samePositions(originalPositions, positions()) && sameCamera(originalCamera, camera()), '异步重挂载与快速材质切换保留缓存视图');
    check('卸载取消动画、重挂载与初始快速切换安全', { oldAnimations: oldAnimations.length, ...await checkImagesAndShapes('liquid') });
    assert(!runtimeErrors.length && !document.querySelector('[role="alert"]'), `没有运行错误：${runtimeErrors.join('；')}`);
    assert(!apiRequests.length, '验收没有业务 API 或模型请求');
    check('隔离运行，无业务 API、模型调用或运行错误');
    return publish('passed');
  } catch (error) {
    failures.push({ message: error.message, stack: error.stack });
    controls.setResultOpen(true); return publish('failed');
  } finally {
    // Restore the screenshot choice without replacing the graph or user camera.
    controls.setMounted(true); controls.setMaterial(initial.material); controls.setTheme(initial.theme); controls.setMotion(initial.motion);
    controls.setVisible(initial.visible); controls.setHover(initial.hover); controls.setMatchedIds(initial.matchedIds); controls.setLongDocuments(initial.longDocuments);
    if (controlledMotion) setReducedMotion(initial.reduced);
    suiteRunning = false;
  }
}

window.__graphMaterialsHarness = {
  run: () => themeMaterialMode ? runThemeSuite() : runSuite(), getResult: () => resultValue, getGraph: () => lastGraph,
  getState: () => ({ material: controls?.material, theme: controls?.theme, motion: controls?.motion,
    selected: controls?.selectedId, mounted: controls?.mounted, visible: controls?.visible, hover: controls?.hover,
    matchedIds: [...(controls?.matchedIds || [])], longDocuments: controls?.longDocuments, instances: instances.length,
    motionEnvironment: motionEnvironment(), preferenceEnvironment: themeMaterialMode ? 'isolated-memory-only' : 'none',
    themeHookMountId: controls?.themeHookMountId, runtimeErrors: [...runtimeErrors], apiRequests: [...apiRequests], modelCalls: 0 }),
  setMaterial: value => controls.setMaterial(value), setTheme: value => controls.setTheme(value),
  setMotion: value => controls.setMotion(value), setMounted: value => controls.setMounted(value),
  setVisible: value => controls.setVisible(value), setHover: value => controls.setHover(value),
  setMatchedIds: value => controls.setMatchedIds(new Set(value)), setLongDocuments: value => controls.setLongDocuments(value),
  setReducedMotion,
  remountThemeHook: () => { if (!themeMaterialMode) throw new Error('未启用主题材质隔离测试'); controls.remountThemeHook(); },
};
document.documentElement.dataset.theme = initialTheme;
createRoot(document.getElementById('graph-materials-root')).render(<Fixture/>);
if (params.get('test') === 'materials' || themeMaterialMode) {
  waitFor(() => document.querySelector('[data-fixture-status="ready"],[data-fixture-status="failed"]'), '预览场景就绪', 30000)
    .then(element => element.dataset.fixtureStatus === 'ready' ? (themeMaterialMode ? runThemeSuite() : runSuite()) : resultValue)
    .catch(error => { runtimeErrors.push(error.message); controls?.setResult({ status: 'failed', failures: [{ message: error.message }] }); });
}
