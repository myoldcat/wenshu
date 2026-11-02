import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { BaseBehavior, Circle, ConcentricLayout, DagreLayout, EdgeEvent, ExtensionCategory, ForceLayout, Graph, GraphEvent, NodeEvent, Quadratic, RadialLayout, Rect, getExtension, register } from '@antv/g6';
import { GRAPH_ZOOM_RANGE, appendAnchoredPositions, compareGraphLabels, computeGraphLayout, createGraphEdgeFrameSync, fixGraphLabelSizes, fixedGraphLabelStyle, graphCameraTranslation, graphEdgeAnimation, graphLayoutKey, graphNodeAnimation, graphNodeName, makeG6Data, planGraphViewport, syncGraphEdgeFlow } from './graph-g6.js';
import { edgeIdentity, relationshipNature } from './graph-state.js';
import { cancelMaterialMotion, drawMaterialSkin, loadGraphMaterial, resizeMaterialSkin } from './graph-materials.js';
import { DEFAULT_GRAPH_MATERIAL, normalizeGraphMaterial } from './graph-material-preferences.js';

const ENGINES = { force: ForceLayout, radial: RadialLayout, concentric: ConcentricLayout, dagre: DagreLayout };
const EMPTY_SET = new Set();
const EMPTY_MAP = new Map();
const SCOPE_LAYOUTS = new WeakMap();

class WenshuCircle extends Circle {
  getLabelStyle(attributes) {
    const style = super.getLabelStyle(attributes);
    return attributes.fixedLabel ? fixedGraphLabelStyle(style, this.context?.graph.getZoom() ?? 1) : style;
  }
  render(attributes = this.parsedAttributes, container = this) {
    super.render(attributes, container);
    drawMaterialSkin(this, attributes, container);
    this.upsert('root-overline', 'text', attributes.subtitleText ? {
      text: attributes.subtitleText, x: 0, y: -25, textAlign: 'center', textBaseline: 'middle',
      fontSize: 9, letterSpacing: 1, fill: attributes.themeAccent, opacity: .65, fontFamily: attributes.themeFont,
    } : false, container);
  }
  onframe() { super.onframe(); resizeMaterialSkin(this); }
  destroy() { cancelMaterialMotion(this); super.destroy(); }
}

class WenshuDocument extends Rect {
  getDocumentContentBounds(attributes) {
    const [width, height] = this.getSize(attributes), body = attributes.materialSprite?.body;
    const scale = body ? Math.min(width / body.width, height / body.height) : 1;
    // The generated capsule keeps its aspect ratio inside the key shape. Its
    // visible body is narrower than the 180px hit area, and varies by material.
    const bodyWidth = body ? body.width * scale : width, bodyHeight = body ? body.height * scale : height;
    const padding = Math.min(12, bodyHeight * .2), iconWidth = Math.min(Number(attributes.iconWidth) || 23, bodyHeight * .5);
    const left = -bodyWidth / 2 + padding, right = bodyWidth / 2 - padding;
    const textX = left + iconWidth + 8;
    return { bodyWidth, bodyHeight, iconX: left + iconWidth / 2, iconWidth, textX, textWidth: Math.max(1, Math.floor(right - textX)) };
  }
  getIconStyle(attributes) {
    const style = super.getIconStyle(attributes);
    if (!style) return style;
    const content = this.getDocumentContentBounds(attributes);
    return { ...style, x: content.iconX, y: 0, width: content.iconWidth, height: content.iconWidth,
      shadowColor: attributes.documentContrast ? 'rgba(38,29,18,.9)' : 'transparent',
      shadowBlur: attributes.documentContrast ? 1.5 : 0, shadowOffsetX: 0, shadowOffsetY: attributes.documentContrast ? .75 : 0 };
  }
  render(attributes = this.parsedAttributes, container = this) {
    super.render(attributes, container);
    drawMaterialSkin(this, attributes, container);
    const content = this.getDocumentContentBounds(attributes);
    this.upsert('document-contrast', 'rect', false, container);
    const textStyle = { x: content.textX, textAlign: 'left', textBaseline: 'middle',
      wordWrap: true, wordWrapWidth: content.textWidth, maxLines: 1, textOverflow: 'ellipsis',
      fontFamily: attributes.themeFont, zIndex: 3, pointerEvents: 'none', opacity: attributes.opacity ?? 1,
      shadowColor: attributes.documentContrast ? 'rgba(38,29,18,.9)' : 'transparent',
      shadowBlur: attributes.documentContrast ? 1.5 : 0, shadowOffsetX: 0, shadowOffsetY: attributes.documentContrast ? .75 : 0 };
    this.upsert('document-title', 'text', {
      ...textStyle, text: String(attributes.documentTitle || '').replace(/\s+/g, ' ').trim(), y: -6,
      fontSize: 12, fontWeight: 600, fill: attributes.documentInk || attributes.themeInk,
    }, container);
    this.upsert('document-source', 'text', {
      ...textStyle, text: String(attributes.subtitleText || '').replace(/\s+/g, ' ').trim(), y: 12,
      fontSize: 10, fontWeight: attributes.documentContrast ? 500 : 400, fill: attributes.documentMuted || attributes.themeMuted,
    }, container);
  }
  onframe() { super.onframe(); resizeMaterialSkin(this); }
  destroy() { cancelMaterialMotion(this); super.destroy(); }
}

class WenshuRelation extends Quadratic {
  render(attributes = this.parsedAttributes, container = this) {
    super.render(attributes, container);
    this.upsert('flow', 'path', attributes.flowActive ? {
      d: this.getKeyPath(attributes), stroke: attributes.flowColor, lineWidth: 1.1,
      lineDash: [2, 16], opacity: .85, pointerEvents: 'none',
    } : false, container);
    this.syncFlow();
  }
  onframe() {
    super.onframe();
    syncGraphEdgeFlow(this);
  }
  syncFlow() {
    const shape = this.shapeMap.flow;
    if (this.flowShape === shape && this.flowAnimation) return;
    this.flowAnimation?.cancel();
    this.flowAnimation = null;
    this.flowShape = shape;
    if (shape) this.flowAnimation = shape.animate([{ lineDashOffset: 18 }, { lineDashOffset: 0 }], { duration: 1300, iterations: Infinity });
  }
  onDestroy() { this.flowAnimation?.cancel(); }
}

class WenshuReadableLabels extends BaseBehavior {
  constructor(context, options) {
    super(context, options);
    this.fixLabels = () => {
      if (!this.destroyed) this.options.onUpdate?.(fixGraphLabelSizes(this.context));
    };
    this.afterDraw = () => {
      this.fixLabels();
      cancelAnimationFrame(this.frame);
      this.frame = requestAnimationFrame(() => { this.frame = null; this.fixLabels(); });
    };
    context.graph.on(GraphEvent.AFTER_DRAW, this.afterDraw);
    context.graph.on(GraphEvent.AFTER_TRANSFORM, this.fixLabels);
  }
  destroy() {
    cancelAnimationFrame(this.frame);
    this.context.graph.off(GraphEvent.AFTER_DRAW, this.afterDraw);
    this.context.graph.off(GraphEvent.AFTER_TRANSFORM, this.fixLabels);
    super.destroy();
  }
}

class WenshuSynchronizedEdges extends BaseBehavior {
  constructor(context, options) {
    super(context, options);
    this.frameSync = createGraphEdgeFrameSync(context);
    this.begin = () => this.frameSync.begin();
    this.end = () => this.frameSync.end();
    context.graph.on(GraphEvent.BEFORE_DRAW, this.begin);
    context.graph.on(GraphEvent.AFTER_DRAW, this.end);
  }
  destroy() {
    this.context.graph.off(GraphEvent.BEFORE_DRAW, this.begin);
    this.context.graph.off(GraphEvent.AFTER_DRAW, this.end);
    this.frameSync.destroy();
    super.destroy();
  }
}

function ensureExtensions() {
  for (const [category, name, implementation] of [
    [ExtensionCategory.NODE, 'wenshu-circle', WenshuCircle],
    [ExtensionCategory.NODE, 'wenshu-document', WenshuDocument],
    [ExtensionCategory.EDGE, 'wenshu-relation', WenshuRelation],
    [ExtensionCategory.BEHAVIOR, 'wenshu-readable-labels', WenshuReadableLabels],
    [ExtensionCategory.BEHAVIOR, 'wenshu-synchronized-edges', WenshuSynchronizedEdges],
  ]) if (!getExtension(category, name)) register(category, name, implementation);
}

function readPalette(host) {
  const probe = document.createElement('span');
  probe.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none;';
  host.appendChild(probe);
  const color = (variable, fallback) => {
    probe.style.color = `var(${variable},${fallback})`;
    return getComputedStyle(probe).color || fallback;
  };
  const palette = {
    accent: color('--green', '#44765d'), ink: color('--ink', '#293b32'), muted: color('--muted', '#7c8981'),
    surface: color('--surface', '#fffdf5'), line: color('--line', '#d6dfd2'), canvas: color('--gw-canvas', '#f9faf3'),
    concept: color('--gw-concept', '#44765d'), entity: color('--gw-entity', '#a18449'), method: color('--gw-method', '#589084'),
    person: color('--gw-person', '#9380a2'), document: color('--gw-document', '#7d8d77'),
  };
  palette.claim = palette.person; palette.fact = palette.method;
  const computed = getComputedStyle(host);
  palette.sans = computed.getPropertyValue('--sans').trim() || 'sans-serif';
  palette.serif = computed.getPropertyValue('--serif').trim() || 'serif';
  probe.remove();
  return palette;
}

/**
 * Owns only rendering, layout and scoped camera/position state. Evidence, graph
 * queries, selection and document navigation stay in GraphWorkspace.
 */
export const G6GraphCanvas = forwardRef(function G6GraphCanvas({
  nodes = [], edges = [], rootId, selection, hover = '', onSelect, onHover, matchedIds = EMPTY_SET,
  pathNodeIds = EMPTY_SET, pathEdgeIds = EMPTY_SET, showLabels = false, cameraKey = 'default',
  cameraCache, positionCache, layout = 'force', metrics = {}, metric = 'none', motion = true,
  onCameraChange, onNodeDrag, onError, ariaLabel = '知识图谱', theme, material = DEFAULT_GRAPH_MATERIAL,
}, ref) {
  const hostRef = useRef(null), containerRef = useRef(null), controllerRef = useRef(null), callbacksRef = useRef(null);
  const ownCamera = useRef(new Map()), ownPositions = useRef(new Map());
  const [themeVersion, setThemeVersion] = useState(0), [dragVersion, setDragVersion] = useState(0), [retryVersion, setRetryVersion] = useState(0), [viewportVersion, setViewportVersion] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(() => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
  const enabledMotion = !!motion && !reducedMotion;
  const cameras = cameraCache || ownCamera.current, positionsByScope = positionCache || ownPositions.current;
  const props = { nodes, edges, rootId, selection, hover, onSelect, onHover, matchedIds, pathNodeIds, pathEdgeIds, showLabels, cameraKey, cameraCache: cameras, positionCache: positionsByScope, layout, metrics, metric, material: normalizeGraphMaterial(material), motion: enabledMotion, onCameraChange, onNodeDrag, onError, ariaLabel };
  callbacksRef.current = props;

  const report = (controller, error) => {
    if (!controller.disposed) {
      if (hostRef.current) hostRef.current.dataset.renderState = 'error';
      callbacksRef.current.onError?.(`图谱绘制失败：${error?.message || '请重试'}`);
    }
  };
  const enqueue = (controller, operation) => {
    const task = controller.queue.then(async () => { if (!controller.disposed) await operation(); });
    controller.queue = task.catch(error => report(controller, error));
    return controller.queue;
  };
  const syncMaterialVisibility = controller => {
    const enabled = callbacksRef.current.motion && callbacksRef.current.material === 'liquid';
    const visible = controller.visible && document.visibilityState !== 'hidden';
    let running = 0, paused = 0;
    // The workspace remains mounted while another page is open. Pause its
    // optical skin timelines directly; no layout, data or camera reset is needed.
    controller.graph.getCanvas()?.getRoot()?.forEach(element => {
      const animation = element.materialAnimation;
      if (!animation || element.destroyed) return;
      if (!enabled) { cancelMaterialMotion(element); return; }
      if (!visible) {
        if (animation.playState !== 'paused') animation.pause();
        paused += 1;
      } else {
        if (animation.playState === 'paused') animation.play();
        running += 1;
      }
    });
    if (hostRef.current) {
      hostRef.current.dataset.materialMotion = !enabled ? 'off' : visible ? 'running' : 'paused';
      hostRef.current.dataset.materialAnimationsRunning = String(running);
      hostRef.current.dataset.materialAnimationsPaused = String(paused);
    }
  };
  // G6 changes its container to position:relative/display:grid. Measure the
  // independent filling shell, not that container or its canvas children: a
  // hidden mount otherwise fixes the height at the constructor's fallback.
  const syncCanvasSize = controller => {
    const shell = hostRef.current, width = shell?.clientWidth || 0, height = shell?.clientHeight || 0;
    controller.visible = width > 0 && height > 0;
    syncMaterialVisibility(controller);
    if (!controller.visible) return false;
    // Unlike resize(), setSize() also records dimensions before the first
    // asynchronous draw has created G6's canvas.
    controller.graph.setSize(width, height);
    return true;
  };
  const cachePositions = controller => {
    if (!controller.ready || !controller.scopeKey) return;
    const cache = new Map(controller.positions || EMPTY_MAP);
    for (const node of controller.graph.getNodeData()) {
      const point = controller.graph.getElementPosition(node.id);
      if (Number.isFinite(point?.[0]) && Number.isFinite(point?.[1])) cache.set(node.id, { x: point[0], y: point[1] });
    }
    controller.positions = cache;
    controller.positionCache.set(controller.scopeKey, cache);
    const scopeState = SCOPE_LAYOUTS.get(controller.positionCache)?.get(controller.scopeKey);
    if (scopeState) scopeState.positions = cache;
  };
  const cacheCamera = controller => {
    if (!controller.ready || !hostRef.current?.clientWidth || !hostRef.current?.clientHeight || controller.restoring || !controller.scopeKey || controller.disposed
      || !controller.viewportState?.complete || controller.viewportState.scopeKey !== controller.scopeKey) return;
    const graph = controller.graph, zoom = graph.getZoom(), center = [...graph.getViewportCenter()];
    controller.cameraCache.set(controller.scopeKey, { renderer: 'g6', zoom, center });
    if (hostRef.current) {
      hostRef.current.dataset.cameraZoom = String(zoom);
      const renderedIds = new Set(graph.getNodeData().map(node => node.id));
      for (const button of hostRef.current.querySelectorAll('[data-graph-node]')) {
        if (!renderedIds.has(button.dataset.graphNode)) continue;
        const point = graph.getElementPosition(button.dataset.graphNode), viewport = graph.getViewportByCanvas(point);
        // Numeric coordinates make pointer QA possible without exposing a global
        // graph instance; the same buttons provide the accessible node list.
        button.dataset.graphX = String(point[0]); button.dataset.graphY = String(point[1]);
        button.dataset.viewportX = String(viewport[0]); button.dataset.viewportY = String(viewport[1]);
      }
    }
    // A completed old-scope pan/zoom may arrive after React has selected a new
    // topic. Save it under the old key, but never publish it as the new UI zoom.
    if (callbacksRef.current.cameraKey === controller.scopeKey) callbacksRef.current.onCameraChange?.({ zoom, center, cameraKey: controller.scopeKey });
  };

  useImperativeHandle(ref, () => ({
    zoom(factor) {
      const controller = controllerRef.current;
      if (!controller) return;
      return enqueue(controller, async () => {
        if (!controller.ready || !syncCanvasSize(controller)) return;
        const zoom = Math.min(GRAPH_ZOOM_RANGE[1], Math.max(GRAPH_ZOOM_RANGE[0], controller.graph.getZoom() * factor));
        await controller.graph.zoomTo(zoom, callbacksRef.current.motion ? { duration: 180 } : false);
        cacheCamera(controller);
      });
    },
    fit() {
      const controller = controllerRef.current;
      if (!controller) return;
      return enqueue(controller, async () => {
        if (!controller.ready || !syncCanvasSize(controller) || !controller.graph.getNodeData().length) return;
        await controller.graph.fitView({ when: 'always' }, callbacksRef.current.motion ? { duration: 320 } : false);
        if (controller.viewportState) { controller.viewportState.complete = true; controller.viewportState.pending = null; }
        cacheCamera(controller);
      });
    },
    retry() {
      const controller = controllerRef.current;
      if (!controller) return;
      controller.forceLayout = true;
      setRetryVersion(value => value + 1);
    },
    focus(id) {
      const controller = controllerRef.current;
      if (!controller) return;
      controller.pendingFocus = id;
      return enqueue(controller, async () => {
        if (!controller.ready || !syncCanvasSize(controller) || !controller.graph.getNodeData().some(node => node.id === id)) return;
        await controller.graph.focusElement(id, callbacksRef.current.motion ? { duration: 320 } : false);
        controller.pendingFocus = null;
        cacheCamera(controller);
      });
    },
    getZoom: () => controllerRef.current?.ready ? controllerRef.current.graph.getZoom() : 1,
    getPositions: () => new Map(controllerRef.current?.positions || EMPTY_MAP),
  }));

  useEffect(() => {
    const query = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(query.matches);
    query.addEventListener('change', update);
    const observer = new MutationObserver(() => setThemeVersion(value => value + 1));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => { query.removeEventListener('change', update); observer.disconnect(); };
  }, []);

  useEffect(() => {
    ensureExtensions();
    const host = containerRef.current, shell = hostRef.current;
    const graph = new Graph({
      container: host, width: shell.clientWidth || 300, height: shell.clientHeight || 220,
      padding: [115, 90, 85, 90], zoomRange: GRAPH_ZOOM_RANGE, animation: { duration: 320 },
      node: { animation: graphNodeAnimation(callbacksRef.current.motion) },
      edge: { animation: graphEdgeAnimation(callbacksRef.current.motion) },
      behaviors: [
        { type: 'drag-canvas' }, { type: 'zoom-canvas', sensitivity: .8 }, { type: 'drag-element', hideEdge: false },
        { key: 'wenshu-synchronized-edges', type: 'wenshu-synchronized-edges' },
        // Run before collision detection, including after each data/theme draw.
        // Only label shapes are inverse-scaled; node sizes still show metrics.
        { key: 'wenshu-readable-labels', type: 'wenshu-readable-labels', onUpdate: measurements => {
          const buttons = new Map([...shell.querySelectorAll('[data-graph-node]')].map(button => [button.dataset.graphNode, button]));
          buttons.forEach(button => { delete button.dataset.labelScale; delete button.dataset.labelScreenPx; });
          measurements.forEach(({ id, scale, screenPx }) => {
            const button = buttons.get(id);
            if (button) { button.dataset.labelScale = String(scale); button.dataset.labelScreenPx = String(screenPx); }
          });
        } },
        { key: 'wenshu-label-collision', type: 'auto-adapt-label', padding: [3, 5], throttle: 80,
          // The callback reads current props, so hover/selection/metric changes
          // are reflected by AFTER_DRAW without rebuilding either behavior.
          sortNode: (a, b) => compareGraphLabels(a, b, callbacksRef.current) },
      ],
      transforms: [],
    });
    const controller = { graph, queue: Promise.resolve(), disposed: false, ready: false, visible: shell.clientWidth > 0 && shell.clientHeight > 0, generation: 0, scopeKey: '', positions: new Map(), cameraCache: cameras, positionCache: positionsByScope, restoring: false, dragging: false, dragUntil: 0, viewportState: null, forceLayout: false };
    controllerRef.current = controller;
    graph.on(NodeEvent.CLICK, event => {
      if (performance.now() < controller.dragUntil) return;
      const node = callbacksRef.current.nodes.find(item => item.id === event.target.id);
      if (node) callbacksRef.current.onSelect?.(node);
    });
    graph.on(NodeEvent.POINTER_ENTER, event => {
      const node = callbacksRef.current.nodes.find(item => item.id === event.target.id);
      host.title = node ? `${graphNodeName(node)}：${node.label}${node.stale ? ' · 来源已更新' : ''}` : '';
      if (!controller.dragging) callbacksRef.current.onHover?.(event.target.id);
    });
    graph.on(NodeEvent.POINTER_LEAVE, () => { host.removeAttribute('title'); if (!controller.dragging) callbacksRef.current.onHover?.(''); });
    graph.on(EdgeEvent.POINTER_ENTER, event => {
      const current = callbacksRef.current, edge = current.edges.find(item => edgeIdentity(item) === event.target.id);
      if (!edge) return;
      const labels = new Map(current.nodes.map(node => [node.id, node.label]));
      host.title = `${labels.get(edge.from) || edge.from} → ${labels.get(edge.to) || edge.to}：${edge.label || ({ hierarchy: '层级关系', association: '关联关系', source: '文档关联' })[edge.type] || '关系'} · ${relationshipNature(edge).label}${edge.stale ? ' · 来源已更新' : ''}`;
    });
    graph.on(EdgeEvent.POINTER_LEAVE, () => host.removeAttribute('title'));
    graph.on(NodeEvent.DRAG_START, () => { controller.dragging = true; controller.dragUntil = Infinity; });
    graph.on(NodeEvent.DRAG_END, event => {
      controller.dragging = false; controller.dragUntil = performance.now() + 250;
      // DragElement's own handler can be registered after ours during prepare().
      queueMicrotask(() => {
        if (controller.disposed) return;
        cachePositions(controller);
        const point = controller.positions.get(event.target?.id);
        if (point) callbacksRef.current.onNodeDrag?.(event.target.id, point);
        setDragVersion(value => value + 1);
      });
    });
    graph.on(GraphEvent.AFTER_TRANSFORM, () => cacheCamera(controller));
    const syncVisibility = () => syncMaterialVisibility(controller);
    graph.on(GraphEvent.AFTER_DRAW, syncVisibility);
    document.addEventListener('visibilitychange', syncVisibility);
    const resize = new ResizeObserver(() => {
      // Hiding must not wait behind an asset load or layout animation in the
      // render queue. Returning later will redraw/resume with the latest props.
      if (!shell.clientWidth || !shell.clientHeight) {
        controller.visible = false;
        syncMaterialVisibility(controller);
        return;
      }
      enqueue(controller, () => {
        const wasVisible = controller.visible;
        if (!syncCanvasSize(controller)) return;
        // Hidden data/style updates leave rendering pending. Activation runs
        // the latest props and completes any deferred first fit/restore. A
        // normal visible resize keeps the user's current world center/zoom.
        if (!wasVisible || !controller.ready || !controller.viewportState?.complete) setViewportVersion(value => value + 1);
        else cacheCamera(controller);
      });
    });
    resize.observe(shell);
    return () => {
      resize.disconnect();
      document.removeEventListener('visibilitychange', syncVisibility);
      graph.off(GraphEvent.AFTER_DRAW, syncVisibility);
      controller.visible = false;
      syncMaterialVisibility(controller);
      try { cachePositions(controller); cacheCamera(controller); } catch { /* preparation may still be in flight */ }
      controller.disposed = true; controller.generation += 1;
      if (controllerRef.current === controller) controllerRef.current = null;
      // G6 prepares its canvas asynchronously. Wait for that work before destroy,
      // including React StrictMode's immediate setup/cleanup/setup cycle.
      controller.queue.finally(() => graph.destroy()).catch(() => {});
    };
  }, []);

  const semantics = JSON.stringify([
    nodes.map(node => [node.id, node.type, node.subtype, node.label, node.stale, node.memberCount, node.document?.source]),
    edges.map(edge => [edgeIdentity(edge), edge.from, edge.to, edge.type, edge.layer, edge.label, edge.stale, edge.supportKind, edge.derivedBy, edge.origin, edge.evidence?.length]),
    cameraKey, rootId, layout, metric, metrics, typeof selection === 'string' ? selection : selection?.id,
    hover, [...matchedIds], [...pathNodeIds], [...pathEdgeIds], showLabels, enabledMotion, theme, material, themeVersion, dragVersion, retryVersion,
  ]);

  useEffect(() => {
    const controller = controllerRef.current;
    if (!controller) return;
    // Apply motion / reduced-motion changes even while a hidden canvas skips draw.
    syncMaterialVisibility(controller);
    const generation = ++controller.generation;
    if (hostRef.current) hostRef.current.dataset.renderState = 'pending';
    enqueue(controller, async () => {
      if (generation !== controller.generation || controller.dragging || !syncCanvasSize(controller)) return;
      const current = callbacksRef.current, changedScope = controller.scopeKey !== current.cameraKey;
      if (changedScope) { cachePositions(controller); cacheCamera(controller); }
      controller.cameraCache = current.cameraCache; controller.positionCache = current.positionCache;
      let scopeLayouts = SCOPE_LAYOUTS.get(current.positionCache);
      if (!scopeLayouts) { scopeLayouts = new Map(); SCOPE_LAYOUTS.set(current.positionCache, scopeLayouts); }
      const oldState = scopeLayouts.get(current.cameraKey);
      // GraphWorkspace can still seed provisional positions for arriving API
      // nodes. Once G6 owns this scope, its own saved membership is authoritative
      // for deciding which nodes are new and need an anchor entrance.
      const previous = oldState?.positions || current.positionCache.get(current.cameraKey) || new Map();
      const { positions: anchored, entrances } = appendAnchoredPositions(current.nodes, current.edges, previous, current.rootId);
      // A deliberate metric switch can reorder concentric rings. Routine rank
      // refreshes do not rearrange the user's current graph.
      const layoutKey = graphLayoutKey(current.layout, current.metric, current.metrics), forceLayout = controller.forceLayout;
      const relayout = current.nodes.length > 0 && (oldState?.layoutKey !== layoutKey || forceLayout);
      let positions = anchored;
      if (relayout) positions = await computeGraphLayout({ ...current, positions: anchored }, ENGINES);
      if (controller.disposed || generation !== controller.generation || !syncCanvasSize(controller)) return;
      controller.scopeKey = current.cameraKey;
      controller.positions = positions;
      current.positionCache.set(current.cameraKey, positions);
      if (current.nodes.length) scopeLayouts.set(current.cameraKey, { layoutKey, positions });
      controller.forceLayout = false;
      controller.viewportState = planGraphViewport(controller.viewportState, {
        scopeKey: current.cameraKey, layoutKey, hasNodes: current.nodes.length > 0,
        camera: current.cameraCache.get(current.cameraKey), forceFit: forceLayout || (relayout && !changedScope),
      });
      const viewportState = controller.viewportState;
      const targetIsCurrent = () => callbacksRef.current.cameraKey === current.cameraKey
        && graphLayoutKey(callbacksRef.current.layout, callbacksRef.current.metric, callbacksRef.current.metrics) === layoutKey;
      const materialAssets = await loadGraphMaterial(current.material);
      if (controller.disposed || generation !== controller.generation || !syncCanvasSize(controller)) return;
      const palette = readPalette(hostRef.current), graph = controller.graph;
      graph.setOptions({ animation: current.motion ? { duration: 320 } : false });
      graph.setNode({ animation: graphNodeAnimation(current.motion) });
      graph.setEdge({ animation: graphEdgeAnimation(current.motion) });
      const data = makeG6Data({ ...current, positions, palette, materialAssets });
      // Start new neighbors at their anchor, then animate only those new nodes.
      const reveal = !changedScope && !relayout && current.motion && entrances.size > 0;
      if (reveal) data.nodes.forEach(node => { if (entrances.has(node.id)) Object.assign(node.style, entrances.get(node.id)); });
      controller.restoring = true;
      graph.setData(data);
      await graph.draw();
      controller.ready = true;
      if (controller.disposed || !targetIsCurrent() || !syncCanvasSize(controller)) { controller.restoring = false; return; }
      if (reveal) {
        graph.updateNodeData([...entrances.keys()].map(id => ({ id, style: positions.get(id) })));
        await graph.draw();
      }
      if (controller.disposed || !targetIsCurrent() || !syncCanvasSize(controller)) { controller.restoring = false; return; }
      if (viewportState.pending === 'restore') {
        await graph.zoomTo(viewportState.camera.zoom, false);
        await graph.translateTo(graphCameraTranslation(viewportState.camera.center, graph.getZoom(), graph.getCanvasCenter()), false);
      } else if (viewportState.pending === 'fit' && current.nodes.length) {
        await graph.fitView({ when: 'always' }, changedScope ? false : current.motion ? { duration: 320 } : false);
      }
      if (controller.disposed || !targetIsCurrent() || !syncCanvasSize(controller)) { controller.restoring = false; return; }
      if (current.nodes.length && viewportState.pending) { viewportState.complete = true; viewportState.pending = null; }
      if (controller.pendingFocus && data.nodes.some(node => node.id === controller.pendingFocus)) {
        await graph.focusElement(controller.pendingFocus, current.motion ? { duration: 320 } : false);
        controller.pendingFocus = null;
      }
      controller.restoring = false;
      cachePositions(controller); cacheCamera(controller);
      if (!controller.disposed && generation === controller.generation && targetIsCurrent()) {
        if (hostRef.current) {
          hostRef.current.dataset.renderedKey = current.cameraKey;
          hostRef.current.dataset.renderedLayout = current.layout;
          hostRef.current.dataset.renderedMaterial = current.material;
          hostRef.current.dataset.materialReady = 'true';
          hostRef.current.dataset.renderedSelected = typeof current.selection === 'string' ? current.selection : current.selection?.id || '';
          hostRef.current.dataset.renderedCount = String(current.nodes.length);
          hostRef.current.dataset.renderGeneration = String(generation);
          hostRef.current.dataset.renderState = 'ready';
        }
        callbacksRef.current.onError?.('');
      }
    });
  }, [semantics, viewportVersion]);

  const selectedId = typeof selection === 'string' ? selection : selection?.id;
  const keyboardFocus = node => {
    onHover?.(node.id);
    const controller = controllerRef.current;
    if (controller) enqueue(controller, async () => {
      if (controller.ready && syncCanvasSize(controller) && controller.graph.getNodeData().some(item => item.id === node.id)) {
        await controller.graph.focusElement(node.id, enabledMotion ? { duration: 180 } : false);
        cacheCamera(controller);
      }
    });
  };
  return <div ref={hostRef} className="gw-g6-shell" data-material={normalizeGraphMaterial(material)} data-layout={layout} data-selected={selectedId || ''} data-count={nodes.length} data-motion={enabledMotion ? 'on' : 'off'}>
    <div ref={containerRef} className="gw-g6-canvas" style={{ width: '100%', height: '100%' }} role="img" aria-label={`${ariaLabel}。${nodes.length} 个节点，支持拖动和滚轮缩放；可用 Tab 访问节点列表。`}/>
    <div className="gw-g6-node-list" role="group" aria-label="图谱节点键盘列表">
      {nodes.map(node => <button key={node.id} type="button" className="gw-g6-keyboard-node" data-graph-node={node.id} aria-label={`${graphNodeName(node)}：${node.label}${node.stale ? '，来源已更新' : ''}`} aria-pressed={node.id === selectedId} onClick={() => onSelect?.(node)} onFocus={() => keyboardFocus(node)} onBlur={() => onHover?.('')}>
        <span>{graphNodeName(node)}</span> {node.label}{node.stale ? ' · 来源已更新' : ''}
      </button>)}
    </div>
  </div>;
});

export default G6GraphCanvas;
