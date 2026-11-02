import { edgeIdentity, relationshipNature } from './graph-state.js';
import { graphMaterialIcon } from './graph-material-icons.js';
import { graphMaterialStyle } from './graph-materials.js';
import { DEFAULT_GRAPH_MATERIAL } from './graph-material-preferences.js';

export const GRAPH_ZOOM_RANGE = [.12, 2.4];
export const GRAPH_LAYOUTS = ['force', 'radial', 'concentric', 'dagre'];
export const GRAPH_TYPE_NAMES = { concept: '概念', entity: '实体', claim: '观点', fact: '事实', document: '文档' };

export function graphNodeAnimation(enabled) {
  return enabled ? {
    enter: 'fade', exit: 'fade',
    update: [
      { shape: 'key', fields: ['r', 'width', 'height'], duration: 320, easing: 'ease-in-out' },
      { fields: ['opacity'], duration: 160 }, { shape: 'key', fields: ['fill', 'stroke', 'lineWidth'], duration: 160 },
      // G6 5.1's executor awaits the last animation without a shape selector.
      // Keep the full movement last so draw() cannot finish at the fade's 160ms.
      { fields: ['x', 'y', 'size'], duration: 420, easing: 'ease-in-out' },
    ],
  } : false;
}

export function graphEdgeAnimation(enabled) {
  return enabled ? {
    enter: 'path-in', exit: 'fade',
    update: [
      { fields: ['opacity'], duration: 160 },
      // These reserved fields retain a timeline even when endpoint IDs do not
      // change. BaseEdge.onframe then recomputes geometry from the live nodes
      // for the whole 420ms movement, rather than stopping when the fade ends.
      { fields: ['sourceNode', 'targetNode'], duration: 420, easing: 'ease-in-out' },
    ],
  } : false;
}

export function syncGraphEdgeFlow(edge) {
  const path = edge.getShape('key'), flow = edge.getShape('flow');
  if (path && flow) flow.attr({ d: path.attributes.d });
}

// Independent node/edge animation callbacks can run in different orders within
// one timeline tick. Finish geometry at the Canvas paint boundary, after all
// node values for that frame are available. Listen only while a draw is active;
// a persistent dashed-flow animation must not keep recalculating the whole graph.
export function createGraphEdgeFrameSync({ graph, canvas, element }) {
  const layer = canvas.getLayer('main');
  let attached = false, disposed = false;
  const sync = () => {
    if (disposed) return;
    for (const datum of graph.getEdgeData()) {
      const edge = element.getElement(datum.id);
      if (!edge || edge.destroyed || !edge.sourceNode || !edge.targetNode || edge.sourceNode.destroyed || edge.targetNode.destroyed) continue;
      edge.onframe();
    }
  };
  const detach = () => {
    if (attached) layer.removeEventListener('beforerender', sync);
    attached = false;
  };
  return {
    begin() {
      if (disposed || attached) return;
      layer.addEventListener('beforerender', sync);
      attached = true;
    },
    end() { sync(); detach(); },
    destroy() { disposed = true; detach(); },
  };
}

export function graphLayoutKey(layout, metric, metrics) {
  const hasMetrics = metrics instanceof Map ? metrics.size > 0 : Object.keys(metrics || {}).length > 0;
  return `${layout}:${layout === 'concentric' ? `${metric}:${hasMetrics}` : ''}`;
}

// G6 5.1 translateTo(T) means camera world position P = C - T / zoom.
// getPosition() instead negates the world coordinate of the screen origin, so
// it cannot be round-tripped into translateTo when zoom differs from one.
export function graphCameraTranslation(center, zoom, canvasCenter) {
  return [(canvasCenter[0] - center[0]) * zoom, (canvasCenter[1] - center[1]) * zoom];
}

// Layout completion and viewport completion are independent: a hover or theme
// update during draw must not consume the first fit/restore request.
export function planGraphViewport(previous, { scopeKey, layoutKey, hasNodes, camera, forceFit = false }) {
  const sameScope = previous?.scopeKey === scopeKey, sameLayout = previous?.layoutKey === layoutKey;
  if (!forceFit && sameScope && sameLayout && (previous.complete || previous.pending)) return previous;
  // A request-key switch first exposes an empty loading graph. Carry the saved
  // restore intent across that empty draw, or the subsequent data response
  // would be mistaken for a same-scope first fit and overwrite the user's zoom.
  const deferredRestore = sameScope && sameLayout && previous?.deferred === 'restore';
  const saved = camera || (deferredRestore ? previous.camera : null);
  const restorable = !forceFit && (!sameScope || deferredRestore) && saved?.renderer === 'g6'
    && Number.isFinite(saved.zoom) && saved.center?.length >= 2 && saved.center.every(Number.isFinite);
  const intent = restorable ? 'restore' : 'fit';
  return { scopeKey, layoutKey, complete: false, pending: hasNodes ? intent : null, deferred: hasNodes ? null : intent, camera: restorable ? saved : null };
}

export function graphNodeName(node) {
  return node.type === 'concept' && node.subtype === 'method' ? '方法'
    : node.type === 'entity' && node.subtype === 'person' ? '人物' : GRAPH_TYPE_NAMES[node.type] || '概念';
}

export function truncateGraphLabel(value = '', length = 14) {
  const chars = Array.from(String(value));
  return chars.length > length ? `${chars.slice(0, length).join('')}…` : chars.join('');
}

export function metricValue(metrics, id, metric) {
  const value = (metrics instanceof Map ? metrics.get(id) : metrics?.[id])?.[metric];
  return Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : 0;
}

export function graphMetricScale(nodes, metrics, metric) {
  if (!metric || metric === 'none') return () => 0;
  const max = Math.max(0, ...nodes.filter(node => node.type !== 'document').map(node => metricValue(metrics, node.id, metric)));
  return id => max ? Math.sqrt(metricValue(metrics, id, metric) / max) : 0;
}

export function canFixGraphLabel(datum) {
  return !!datum.data?.node && !datum.data.root && datum.data.node.type !== 'document';
}

export function fixedGraphLabelStyle(style, zoom) {
  if (!style || !Number.isFinite(zoom) || zoom <= 0) return style;
  const result = { ...style };
  for (const field of ['fontSize', 'lineHeight', 'wordWrapWidth', 'backgroundRadius']) {
    if (Number.isFinite(Number(style[field]))) result[field] = Number(style[field]) / zoom;
  }
  if (Array.isArray(style.padding)) result.padding = style.padding.map(value => Number(value) / zoom);
  else if (Number.isFinite(Number(style.padding))) result.padding = Number(style.padding) / zoom;
  return result;
}

// Update the actual text style, rather than a composite Label transform. G6's
// BaseNode.onframe() redraws that label during size/paint animations; the custom
// node's getLabelStyle() applies the same compensation on every such frame.
export function fixGraphLabelSizes({ graph, element }) {
  const zoom = graph.getZoom();
  if (!Number.isFinite(zoom) || zoom <= 0) return [];
  const measurements = [];
  for (const datum of graph.getNodeData()) {
    if (!canFixGraphLabel(datum)) continue;
    const node = element.getElement(datum.id), label = node?.getShape('label');
    if (!label) continue;
    label.setLocalScale(1);
    label.update(node.getLabelStyle(node.parsedAttributes));
    const text = label.getShape('text'), fontSize = Number(text?.attributes.fontSize);
    measurements.push({ id: datum.id, scale: label.getLocalScale()[0], screenPx: fontSize * text.getScale()[1] * zoom });
  }
  return measurements;
}

// Reuse service metrics and interaction state. Label visibility must not launch
// another graph calculation, and identical priorities must stay deterministic.
export function compareGraphLabels(a, b, { hover, selection, pathNodeIds = new Set(), rootId, metrics, metric }) {
  const selectedId = typeof selection === 'string' ? selection : selection?.id;
  const field = !metric || metric === 'none' ? 'degree' : metric;
  const priority = datum => [
    Number(datum.id === hover), Number(datum.id === selectedId),
    Number(pathNodeIds.has(datum.id)), Number(datum.id === rootId), metricValue(metrics, datum.id, field),
  ];
  const pa = priority(a), pb = priority(b);
  for (let index = 0; index < pa.length; index += 1) if (pa[index] !== pb[index]) return pa[index] > pb[index] ? -1 : 1;
  return a.id === b.id ? 0 : a.id.localeCompare(b.id) < 0 ? -1 : 1;
}

// Existing and temporarily hidden positions are retained. New nodes use nearby
// free slots around a connected anchor, rather than moving the current graph.
export function appendAnchoredPositions(nodes, edges, previous = new Map(), rootId) {
  const positions = new Map(previous), entrances = new Map();
  const existingIds = new Set(previous.keys());
  const ordered = [...nodes].sort((a, b) => Number(b.id === rootId) - Number(a.id === rootId) || a.id.localeCompare(b.id));
  const near = (x, y, minimum = 145) => [...positions.values()].some(point => Math.hypot(point.x - x, point.y - y) < minimum);
  for (const node of ordered) {
    if (positions.has(node.id)) continue;
    const adjacent = edges.flatMap(edge => edge.from === node.id ? [edge.to] : edge.to === node.id ? [edge.from] : []);
    const anchor = adjacent.find(id => existingIds.has(id)) || adjacent.find(id => positions.has(id)) || (positions.has(rootId) ? rootId : null);
    const origin = positions.get(anchor) || { x: 450, y: 280 };
    let point = { ...origin };
    if (positions.size) {
      let hash = 0;
      for (const char of node.id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
      for (let slot = 0; slot < 160; slot += 1) {
        const angle = (hash % 628) / 100 + slot * 2.399963;
        const radius = 175 + Math.floor(slot / 10) * 105;
        point = { x: origin.x + Math.cos(angle) * radius, y: origin.y + Math.sin(angle) * radius };
        if (!near(point.x, point.y, node.type === 'document' ? 180 : 145)) break;
      }
    }
    positions.set(node.id, point);
    entrances.set(node.id, anchor ? { ...origin } : { ...point });
  }
  return { positions, entrances };
}

export function forceSeedPositions(nodes, rootId) {
  const ordered = [...nodes].sort((a, b) => Number(b.id === rootId) - Number(a.id === rootId) || a.id.localeCompare(b.id));
  return new Map(ordered.map((node, index) => {
    const radius = 74 * Math.sqrt(index), angle = index * 2.399963;
    return [node.id, { x: 450 + radius * Math.cos(angle), y: 280 + radius * Math.sin(angle) }];
  }));
}

export function concentricScoreBands(nodes, metrics, metric, rootId) {
  const field = !metric || metric === 'none' ? 'degree' : metric;
  const scale = graphMetricScale(nodes, metrics, field);
  return new Map(nodes.map(node => [node.id, node.id === rootId ? 5 : node.type === 'document' ? 0 : Math.min(4, Math.floor(scale(node.id) * 5))]));
}

export function dagreSemanticEdges(nodes, edges) {
  const ids = new Set(nodes.filter(node => node.type !== 'document').map(node => node.id));
  const semantic = edges.filter(edge => ids.has(edge.from) && ids.has(edge.to) && relationshipNature(edge).key !== 'navigation' && edge.type !== 'source' && edge.layer !== 'provenance');
  const hierarchy = semantic.filter(edge => edge.type === 'hierarchy');
  return hierarchy.length ? hierarchy : semantic;
}

async function executeLayoutEngine(type, layoutNodes, edges, options, engines) {
  const engine = new engines[type](options);
  try {
    await engine.execute({ nodes: layoutNodes, edges: edges.map(edge => ({ id: edgeIdentity(edge), source: edge.from, target: edge.to })) });
    const positions = new Map();
    engine.forEachNode(node => {
      if (Number.isFinite(node.x) && Number.isFinite(node.y)) positions.set(String(node.id), { x: node.x, y: node.y });
    });
    return positions;
  } finally { engine.stop?.(); engine.destroy?.(); }
}

async function computeDagrePositions(nodes, edges, positions, rootId, common, engines) {
  const words = nodes.filter(node => node.type !== 'document'), documents = nodes.filter(node => node.type === 'document');
  const semantic = dagreSemanticEdges(nodes, edges), byId = new Map(words.map(node => [node.id, node]));
  const adjacent = new Map(words.map(node => [node.id, []]));
  semantic.forEach(edge => { adjacent.get(edge.from).push(edge.to); adjacent.get(edge.to).push(edge.from); });
  const visited = new Set(), components = [];
  for (const node of [...words].sort((a, b) => a.id.localeCompare(b.id))) {
    if (visited.has(node.id)) continue;
    const ids = [], queue = [node.id]; visited.add(node.id);
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const id = queue[cursor]; ids.push(id);
      for (const neighbor of adjacent.get(id)) if (!visited.has(neighbor)) { visited.add(neighbor); queue.push(neighbor); }
    }
    const idSet = new Set(ids), componentNodes = ids.map(id => byId.get(id));
    const componentEdges = semantic.filter(edge => idSet.has(edge.from) && idSet.has(edge.to));
    const result = ids.length === 1 ? new Map([[ids[0], { x: 0, y: 0 }]])
      : await executeLayoutEngine('dagre', componentNodes, componentEdges, { ...common, rankdir: 'LR', nodesep: 45, ranksep: 100 }, engines);
    const minX = Math.min(...componentNodes.map(item => result.get(item.id).x - item.size[0] / 2));
    const maxX = Math.max(...componentNodes.map(item => result.get(item.id).x + item.size[0] / 2));
    const minY = Math.min(...componentNodes.map(item => result.get(item.id).y - item.size[1] / 2));
    const maxY = Math.max(...componentNodes.map(item => result.get(item.id).y + item.size[1] / 2));
    components.push({ ids, positions: result, minX, minY, width: maxX - minX, height: maxY - minY });
  }
  // Pack independent hierarchies as small diagrams. Treating all isolated roots
  // as one Dagre rank produces a very tall column even for a modest topic.
  components.sort((a, b) => Number(b.ids.includes(rootId)) - Number(a.ids.includes(rootId)) || b.width * b.height - a.width * a.height || a.ids[0].localeCompare(b.ids[0]));
  const gap = 55, area = components.reduce((sum, component) => sum + (component.width + gap) * (component.height + gap), 0);
  const frameWidth = Math.max(400, ...components.map(component => component.width), Math.sqrt(area * 1.25));
  const packed = new Map(); let x = 0, y = 0, rowHeight = 0;
  for (const component of components) {
    if (x && x + component.width > frameWidth) { x = 0; y += rowHeight + gap; rowHeight = 0; }
    component.positions.forEach((point, id) => packed.set(id, { x: point.x - component.minX + x, y: point.y - component.minY + y }));
    x += component.width + gap; rowHeight = Math.max(rowHeight, component.height);
  }
  if (packed.size) {
    const xs = [...packed.values()].map(point => point.x), ys = [...packed.values()].map(point => point.y);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    packed.forEach((point, id) => packed.set(id, { x: point.x - cx + 450, y: point.y - cy + 280 }));
  }
  // Source documents are leaves attached to their real source links. They do
  // not turn otherwise separate semantic hierarchies into one global rank.
  const attached = appendAnchoredPositions(documents, edges, packed, rootId).positions;
  return new Map([...positions, ...attached]);
}

export async function computeGraphLayout({ nodes, edges, positions, rootId, layout, metrics, metric }, engines) {
  if (!nodes.length) return new Map(positions);
  const type = GRAPH_LAYOUTS.includes(layout) ? layout : 'force';
  const count = nodes.length, width = Math.max(700, Math.sqrt(count) * 195), height = Math.max(430, Math.sqrt(count) * 145);
  const scores = concentricScoreBands(nodes, metrics, metric, rootId);
  // Compute from an algorithm-owned bounded seed, never from a previous Dagre
  // extent. The renderer still animates from its actual previous positions.
  const seeds = type === 'force' ? forceSeedPositions(nodes, rootId) : positions;
  const layoutNodes = [...nodes].sort((a, b) => a.id.localeCompare(b.id)).map(node => ({ id: node.id, type: node.type, ...seeds.get(node.id), size: node.type === 'document' ? [190, 85] : [156, 116], score: scores.get(node.id) }));
  const layoutEdges = [...edges].sort((a, b) => edgeIdentity(a).localeCompare(edgeIdentity(b)));
  const common = { width, height, center: [450, 280], node: datum => datum, edge: datum => datum, nodeSize: node => node.size, nodeSpacing: 35, preventOverlap: true, animate: false };
  const options = type === 'radial' ? { focusNode: nodes.some(node => node.id === rootId) ? rootId : nodes[0].id, unitRadius: 210, linkDistance: 190, maxIteration: 200, maxPreventOverlapIteration: 100 }
    : type === 'concentric' ? { sortBy: node => node.score, maxLevelDiff: 1, nodeSpacing: 30, equidistant: false }
      : type === 'dagre' ? { rankdir: 'LR', nodesep: 65, ranksep: 135, nodeSize: node => node.size }
        : { maxIteration: 220, gravity: 8, linkDistance: 220, nodeStrength: 1100, edgeStrength: 35, damping: .8, minMovement: .5 };
  if (type === 'dagre') return computeDagrePositions(layoutNodes, layoutEdges, positions, rootId, common, engines);
  return new Map([...positions, ...await executeLayoutEngine(type, layoutNodes, layoutEdges, { ...common, ...options }, engines)]);
}

function rgb(value) {
  if (value?.startsWith('#')) {
    let hex = value.slice(1);
    if (hex.length === 3) hex = hex.split('').map(char => char + char).join('');
    return [0, 2, 4].map(offset => parseInt(hex.slice(offset, offset + 2), 16) || 0);
  }
  const values = value?.match(/[\d.]+/g)?.slice(0, 3).map(Number);
  return values?.length === 3 ? values : [70, 110, 90];
}

export function mixGraphColor(foreground, background, amount) {
  const a = rgb(foreground), b = rgb(background);
  return `rgb(${a.map((channel, index) => Math.round(channel * amount + b[index] * (1 - amount))).join(',')})`;
}

export function makeG6Data({ nodes, edges, positions, rootId, selection, hover, matchedIds = new Set(), pathNodeIds = new Set(), pathEdgeIds = new Set(), showLabels, metrics, metric, motion, palette, material = DEFAULT_GRAPH_MATERIAL, materialAssets }) {
  const nodeSet = new Set(nodes.map(node => node.id)), selectedId = typeof selection === 'string' ? selection : selection?.id;
  const neighbors = new Set(hover ? [hover, ...edges.flatMap(edge => edge.from === hover ? [edge.to] : edge.to === hover ? [edge.from] : [])] : []);
  const scale = graphMetricScale(nodes, metrics, metric);
  const fadedNode = id => id !== selectedId && !pathNodeIds.has(id) && (hover ? !neighbors.has(id) : matchedIds.size > 0 && !matchedIds.has(id));
  let flowing = 0;
  return {
    nodes: nodes.map((node, index) => {
      const root = node.id === rootId, doc = node.type === 'document', selected = node.id === selectedId;
      const surface = graphMaterialStyle(material, node, root);
      const color = surface.glow, sprite = materialAssets?.sprites[surface.slot];
      const matched = pathNodeIds.has(node.id) || matchedIds.has(node.id), hovered = node.id === hover;
      const diameter = root ? 100 : 64 + scale(node.id) * 25;
      return { id: node.id, type: doc ? 'wenshu-document' : 'wenshu-circle', data: { node, root }, style: {
        ...positions.get(node.id), size: doc ? [180, 60] : diameter, radius: 30, opacity: fadedNode(node.id) ? .18 : 1,
        fill: sprite ? 'transparent' : surface.fill,
        stroke: 'transparent', lineWidth: 0, lineDash: node.stale ? [3, 3] : [], shadowColor: 'transparent', shadowBlur: 0, shadowOffsetY: 0,
        materialSprite: sprite, materialId: surface.id, materialMotion: !!(motion && surface.id === 'liquid' && !doc), materialSeed: index,
        label: !doc, fixedLabel: !root && !doc, labelText: truncateGraphLabel(node.label, 24) + (node.stale ? ' · 历史' : ''), labelPlacement: 'bottom',
        labelOffsetY: 11, labelFontSize: root ? 16 : 13, labelFontWeight: root || selected ? 600 : 500, labelFill: palette.ink, labelZIndex: 4,
        labelFontFamily: root ? palette.serif : palette.sans, labelWordWrap: true, labelMaxWidth: root ? 180 : 156, labelMaxLines: 2, labelTextOverflow: 'ellipsis', labelLineHeight: root ? 22 : 19,
        labelBackground: !root, labelBackgroundFill: palette.canvas, labelBackgroundRadius: 4, labelPadding: [2, 4],
        icon: true, iconSrc: graphMaterialIcon(node, surface.ink), iconWidth: root ? 38 : doc ? 23 : 29, iconHeight: root ? 38 : doc ? 23 : 29, iconX: doc ? -65 : 0, iconZIndex: 3,
        halo: selected || matched || hovered, haloStroke: 'transparent', haloLineWidth: 0, haloStrokeOpacity: 0, haloOpacity: 1,
        haloFill: color, haloFillOpacity: surface.id === 'liquid' ? .3 : .7, haloShadowColor: color,
        haloR: doc ? undefined : diameter / 2 + 8, haloFilter: 'blur(7px)',
        haloShadowBlur: selected ? 26 : hovered ? 18 : 12, haloShadowOffsetX: 0, haloShadowOffsetY: 0, haloZIndex: -1,
        badge: node.memberCount > 1, badges: node.memberCount > 1 ? [{ text: node.memberCount > 99 ? '99+' : String(node.memberCount), placement: 'right-top', fill: palette.surface, backgroundFill: palette.accent, fontSize: 10, padding: [3, 5], backgroundRadius: 10 }] : [],
        subtitleText: doc ? (node.stale ? '历史来源' : node.document?.source || '来源文档') : '',
        documentTitle: doc ? node.label : '', documentInk: surface.ink, documentMuted: surface.id === 'metal' ? '#f4e7cc' : '#4f5b52',
        documentContrast: doc && surface.id === 'metal',
        themeMuted: palette.muted, themeAccent: palette.accent, themeInk: palette.ink, themeFont: palette.sans,
      } };
    }),
    edges: edges.filter(edge => nodeSet.has(edge.from) && nodeSet.has(edge.to)).map((edge, index) => {
      const nature = relationshipNature(edge).key, onPath = pathEdgeIds.has(edgeIdentity(edge));
      const connected = edge.from === selectedId || edge.to === selectedId;
      const faded = !onPath && (hover ? edge.from !== hover && edge.to !== hover : matchedIds.size && !matchedIds.has(edge.from) && !matchedIds.has(edge.to));
      const color = onPath ? palette.accent : nature === 'navigation' ? palette.method : nature === 'supported' ? mixGraphColor(palette.accent, palette.line, connected ? .7 : .48) : palette.muted;
      // A separate overlay carries flow; the base evidence/navigation dash stays intact.
      const flowActive = !!(motion && onPath && nature === 'supported' && flowing++ < 8);
      return { id: edgeIdentity(edge), source: edge.from, target: edge.to, type: 'wenshu-relation', data: { edge }, style: {
        curveOffset: index % 2 ? 12 : -12, stroke: color, lineWidth: onPath ? 2.3 : connected ? 1.6 : 1.15,
        opacity: faded ? .16 : edge.stale ? .52 : nature === 'unverified' ? .5 : .82,
        lineDash: nature === 'navigation' ? [8, 4, 2, 4] : nature === 'source' ? [2, 4] : nature === 'unverified' ? [1, 5] : [],
        endArrow: edge.type === 'hierarchy', endArrowSize: 6,
        label: !!(showLabels && edge.label), labelText: truncateGraphLabel(edge.label, 16), labelFontSize: 10, labelFill: palette.muted, labelFontFamily: palette.sans,
        labelBackground: true, labelBackgroundFill: palette.canvas, labelBackgroundRadius: 3, labelPadding: [2, 4], labelAutoRotate: false,
        flowActive, flowColor: palette.surface,
      } };
    }),
  };
}
