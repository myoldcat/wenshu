import test from 'node:test';
import assert from 'node:assert/strict';
import { ForceLayout, RadialLayout, ConcentricLayout, DagreLayout } from '@antv/layout';
import { Circle, Quadratic } from '@antv/g6';
import { appendAnchoredPositions, canFixGraphLabel, compareGraphLabels, computeGraphLayout, concentricScoreBands, createGraphEdgeFrameSync, dagreSemanticEdges, fixGraphLabelSizes, fixedGraphLabelStyle, graphCameraTranslation, graphEdgeAnimation, graphMetricScale, graphNodeAnimation, makeG6Data, planGraphViewport, syncGraphEdgeFlow } from './graph-g6.js';
import { edgeIdentity } from './graph-state.js';

test('node movement and edge endpoint tracking share the full animation lifetime', () => {
  const node = graphNodeAnimation(true), edge = graphEdgeAnimation(true);
  const awaitedNode = node.update.filter(animation => !animation.shape).at(-1);
  const awaitedEdge = edge.update.filter(animation => !animation.shape).at(-1);
  assert.deepEqual(awaitedNode.fields, ['x', 'y', 'size']);
  assert.deepEqual(awaitedEdge.fields, ['sourceNode', 'targetNode']);
  assert.equal(awaitedNode.duration, 420);
  assert.equal(awaitedEdge.duration, awaitedNode.duration);
  assert.equal(awaitedEdge.easing, awaitedNode.easing);
  assert.equal(graphNodeAnimation(false), false);
  assert.equal(graphEdgeAnimation(false), false);
});

test('Canvas paint boundary synchronizes actual G6 edges after both nodes move and removes idle listeners', () => {
  const source = new Circle({ style: { x: 0, y: 0, size: 56, label: false } });
  const target = new Circle({ style: { x: 200, y: 100, size: 56, label: false } });
  class FollowingEdge extends Quadratic {
    onframe() { super.onframe(); syncGraphEdgeFlow(this); }
  }
  const edge = new FollowingEdge({ context: { element: { getElement: id => id === 'source' ? source : target } }, style: { sourceNode: 'source', targetNode: 'target', label: false, curveOffset: 0 } });
  edge.upsert('flow', edge.getShape('key').constructor, { d: edge.getShape('key').attributes.d }, edge);
  const listeners = new Set();
  const frameSync = createGraphEdgeFrameSync({
    graph: { getEdgeData: () => [{ id: 'edge' }] }, element: { getElement: () => edge },
    canvas: { getLayer: () => ({ addEventListener: (name, callback) => { assert.equal(name, 'beforerender'); listeners.add(callback); }, removeEventListener: (name, callback) => listeners.delete(callback) }) },
  });
  try {
    frameSync.begin(); frameSync.begin();
    assert.equal(listeners.size, 1);
    for (const progress of [0, .25, .5, .75, .95, 1]) {
      // Deliberately run the edge's own animation before the node callbacks,
      // reproducing G6's one-frame lag. Paint-boundary sync must repair it.
      edge.onframe();
      source.update({ x: 360 * progress, y: -150 * progress });
      target.update({ x: 200 + 450 * progress, y: 100 + 300 * progress });
      listeners.forEach(callback => callback());
      const path = edge.getShape('key').attributes.d;
      const start = path[0].slice(1), end = path.at(-1).slice(-2);
      for (const [point, node] of [[start, source], [end, target]]) {
        const center = node.getCenter();
        assert.ok(Math.abs(Math.hypot(point[0] - center[0], point[1] - center[1]) - 28) < .0001, `${progress}: endpoint leaves the live node`);
      }
      assert.deepEqual(edge.getShape('flow').attributes.d, path, `${progress}: flowing overlay keeps the live key path`);
    }
    frameSync.end();
    assert.equal(listeners.size, 0, 'finished draws leave persistent flow frames idle');
    frameSync.begin(); assert.equal(listeners.size, 1);
    frameSync.destroy(); assert.equal(listeners.size, 0);
    frameSync.begin(); assert.equal(listeners.size, 0, 'destroyed behavior cannot reattach');
  } finally { frameSync.destroy(); edge.destroy(); source.destroy(); target.destroy(); }
});

test('readable label fixing excludes root and source cards and collision priority follows current interaction', () => {
  const datum = (id, type = 'concept', root = false) => ({ id, data: { root, node: { id, type } } });
  assert.equal(canFixGraphLabel(datum('word')), true);
  assert.equal(canFixGraphLabel(datum('root', 'concept', true)), false);
  assert.equal(canFixGraphLabel(datum('source', 'document')), false);
  const nodes = ['metric', 'root', 'path', 'selected', 'hover', 'a', 'b'].map(id => datum(id));
  const context = { hover: 'hover', selection: { id: 'selected' }, pathNodeIds: new Set(['path']), rootId: 'root', metrics: { metric: { pagerank: 100 } }, metric: 'pagerank' };
  assert.deepEqual([...nodes].sort((a, b) => compareGraphLabels(a, b, context)).map(node => node.id), ['hover', 'selected', 'path', 'root', 'metric', 'a', 'b']);
  const selectedBefore = compareGraphLabels(datum('selected'), datum('metric'), context);
  context.selection = { id: 'metric' };
  const selectedAfter = compareGraphLabels(datum('selected'), datum('metric'), context);
  assert.equal(selectedBefore, -1);
  assert.equal(selectedAfter, 1);
});

test('actual G6 label shapes retain 13 screen pixels after redraws and relative-fit zooms', () => {
  let zoom = .37;
  class ReadableCircle extends Circle {
    getLabelStyle(attributes) { return fixedGraphLabelStyle(super.getLabelStyle(attributes), zoom); }
  }
  const regular = new ReadableCircle({ style: { size: 56, label: true, labelText: '字号核验', labelFontSize: 13, labelLineHeight: 19, labelBackground: false } });
  const root = new Circle({ style: { size: 94, label: true, labelText: '主题', labelFontSize: 14, labelBackground: false } });
  const document = new Circle({ style: { size: 56, label: true, labelText: '来源', labelFontSize: 12, labelBackground: false } });
  const shapes = new Map([['word', regular], ['root', root], ['source', document]]);
  const context = {
    graph: { getZoom: () => zoom, getNodeData: () => [
      { id: 'word', data: { node: { type: 'concept' } } }, { id: 'root', data: { root: true, node: { type: 'concept' } } }, { id: 'source', data: { node: { type: 'document' } } },
    ] }, element: { getElement: id => shapes.get(id) },
  };
  try {
    for (zoom of [.37, .52, .675, .4, 1.3]) {
      // Node/style redraws really reset G6 Label's local transform; the camera
      // stays unchanged. The next behavior pass must still restore 13 pixels.
      regular.update({ labelFill: '#44765d', labelOffsetY: 10 });
      const result = fixGraphLabelSizes(context);
      assert.deepEqual(result.map(item => item.id), ['word']);
      assert.ok(Math.abs(result[0].screenPx - 13) < .00001, `${zoom}: ${result[0].screenPx}px`);
      // BaseNode.onframe really redraws Label from node attributes. Check the
      // final Text, including parsed font and transform, after that late write.
      regular.onframe();
      const text = regular.getShape('label').getShape('text');
      assert.ok(Math.abs(text.parsedStyle.fontSize * text.getScale()[1] * zoom - 13) < .00001);
      assert.equal(regular.getShape('label').getLocalScale()[0], 1);
      assert.equal(root.getShape('label').getLocalScale()[0], 1);
      assert.equal(document.getShape('label').getLocalScale()[0], 1);
    }
  } finally { shapes.forEach(shape => shape.destroy()); }
});

test('initial viewport fit stays pending through draw and visual invalidations, and empty loading cannot consume it', () => {
  const scope = { scopeKey: 'topic:a', layoutKey: 'force:', hasNodes: false };
  const empty = planGraphViewport(null, scope);
  assert.equal(empty.complete, false);
  assert.equal(empty.pending, null);
  const initial = planGraphViewport(empty, { ...scope, hasNodes: true });
  assert.equal(initial.pending, 'fit');
  const hoverRefresh = planGraphViewport(initial, { ...scope, hasNodes: true });
  assert.equal(hoverRefresh, initial);
  assert.equal(hoverRefresh.pending, 'fit');
  initial.complete = true; initial.pending = null;
  assert.equal(planGraphViewport(initial, { ...scope, hasNodes: true }), initial);
  assert.equal(planGraphViewport(initial, { ...scope, layoutKey: 'radial:', hasNodes: true }).pending, 'fit');
  assert.equal(planGraphViewport(initial, { ...scope, hasNodes: true, forceFit: true }).pending, 'fit');
});

test('returning to a completed scope restores only an actual G6 camera', () => {
  const camera = { renderer: 'g6', zoom: .5, center: [120, 95] };
  const restored = planGraphViewport({ scopeKey: 'topic:b', layoutKey: 'force:', complete: true }, { scopeKey: 'topic:a', layoutKey: 'force:', hasNodes: true, camera });
  assert.equal(restored.pending, 'restore');
  assert.equal(restored.camera, camera);
  assert.equal(restored.complete, false);
  const legacy = planGraphViewport(null, { scopeKey: 'a', layoutKey: 'force:', hasNodes: true, camera: { zoom: 1, x: 0, y: 0 } });
  assert.equal(legacy.pending, 'fit');
  const loading = planGraphViewport({ scopeKey: 'topic:b', layoutKey: 'force:', complete: true }, { scopeKey: 'topic:a', layoutKey: 'force:', hasNodes: false, camera });
  assert.equal(loading.pending, null);
  assert.equal(loading.deferred, 'restore');
  const loaded = planGraphViewport(loading, { scopeKey: 'topic:a', layoutKey: 'force:', hasNodes: true, camera });
  assert.equal(loaded.pending, 'restore');
  assert.equal(loaded.camera.zoom, .5);
  const changedLayout = planGraphViewport(loading, { scopeKey: 'topic:a', layoutKey: 'radial:', hasNodes: true, camera });
  assert.equal(changedLayout.pending, 'fit');
});

test('camera world-center restores screen position across non-unit zoom and resized canvases', () => {
  const center = [441, 274], zoom = .60907;
  for (const canvasCenter of [[606, 345], [430, 470]]) {
    const translation = graphCameraTranslation(center, zoom, canvasCenter);
    const restoredCenter = canvasCenter.map((value, axis) => value - translation[axis] / zoom);
    assert.ok(Math.abs(restoredCenter[0] - center[0]) < .000001);
    assert.ok(Math.abs(restoredCenter[1] - center[1]) < .000001);
    const root = [441, 274], viewport = root.map((value, axis) => canvasCenter[axis] + (value - restoredCenter[axis]) * zoom);
    assert.deepEqual(viewport, canvasCenter);
  }
  const legacyOffset = planGraphViewport(null, { scopeKey: 'legacy', layoutKey: 'force:', hasNodes: true, camera: { renderer: 'g6', zoom, position: [-233, -186] } });
  assert.equal(legacyOffset.pending, 'fit');
});

test('incremental neighbors keep existing and hidden positions and enter from their anchor', () => {
  const original = new Map([['root', { x: 90, y: 110 }], ['hidden', { x: 280, y: 100 }]]);
  const result = appendAnchoredPositions([{ id: 'root' }, { id: 'new' }], [{ from: 'root', to: 'new' }], original, 'root');
  assert.deepEqual(result.positions.get('root'), original.get('root'));
  assert.deepEqual(result.positions.get('hidden'), original.get('hidden'));
  assert.deepEqual(result.entrances.get('new'), original.get('root'));
  assert.ok(Math.hypot(result.positions.get('new').x - 90, result.positions.get('new').y - 110) >= 145);
  assert.equal(original.has('new'), false);
  const repeated = appendAnchoredPositions([{ id: 'root' }, { id: 'new' }], [], result.positions, 'root');
  assert.deepEqual(repeated.positions, result.positions);
  assert.equal(repeated.entrances.size, 0);
});

test('missing, disabled and zero metrics never fabricate an importance score', () => {
  const nodes = [{ id: 'a', type: 'concept' }, { id: 'b', type: 'entity' }, { id: 'doc', type: 'document' }];
  assert.equal(graphMetricScale(nodes, {}, 'pagerank')('a'), 0);
  assert.equal(graphMetricScale(nodes, { a: { pagerank: 1 } }, 'none')('a'), 0);
  const scaled = graphMetricScale(nodes, { a: { pagerank: .04 }, b: { pagerank: .01 }, doc: { pagerank: 100 } }, 'pagerank');
  assert.equal(scaled('a'), 1);
  assert.equal(scaled('b'), .5);
});

test('all four installed layout engines handle cycles and disconnected nodes with finite positions', async () => {
  const nodes = [{ id: 'a', type: 'concept' }, { id: 'b', type: 'claim' }, { id: 'c', type: 'entity' }, { id: 'd', type: 'document' }];
  const edges = [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }];
  const positions = new Map(nodes.map((node, index) => [node.id, { x: 450 + index * 160, y: 250 }]));
  const engines = { force: ForceLayout, radial: RadialLayout, concentric: ConcentricLayout, dagre: DagreLayout };
  const original = JSON.stringify(nodes);
  for (const layout of Object.keys(engines)) {
    const result = await computeGraphLayout({ nodes, edges, positions, rootId: 'a', layout, metrics: { a: { degree: 2 }, b: { degree: 1 } }, metric: 'degree' }, engines);
    assert.equal(result.size, nodes.length, layout);
    assert.ok([...result.values()].every(point => Number.isFinite(point.x) && Number.isFinite(point.y)), layout);
  }
  assert.equal(JSON.stringify(nodes), original);
});

test('render conversion retains evidence nature, hierarchy arrows, stale state and bounded flow', () => {
  const palette = { accent: '#44765d', concept: '#44765d', entity: '#a18449', person: '#9380a2', method: '#589084', document: '#7d8d77', claim: '#9380a2', fact: '#589084', surface: '#fffdf5', canvas: '#f9faf3', ink: '#293b32', muted: '#7c8981', line: '#d6dfd2', sans: 'sans-serif', serif: 'serif' };
  const nodes = [{ id: 'a', type: 'concept', label: '概念', memberCount: 2 }, { id: 'b', type: 'claim', label: '观点', stale: true }, { id: 'doc', type: 'document', label: '原文', document: { source: '来源' } }];
  const edges = [
    { id: 'supported', from: 'a', to: 'b', type: 'hierarchy', evidence: [{ blockId: 'p1' }] },
    { id: 'navigation', from: 'b', to: 'a', layer: 'navigation', type: 'association' },
    { id: 'source', from: 'b', to: 'doc', type: 'source', stale: true },
    { id: 'dangling', from: 'b', to: 'missing', type: 'association' },
  ];
  const props = { nodes, edges, positions: new Map(nodes.map((node, i) => [node.id, { x: i * 160, y: 100 }])), rootId: 'a', selection: nodes[1], metric: 'none', metrics: {}, pathEdgeIds: new Set(edges.map(edgeIdentity)), motion: true, palette };
  const data = makeG6Data(props);
  assert.equal(data.edges.length, 3);
  assert.equal(data.edges[0].style.endArrow, true);
  assert.equal(data.edges[0].style.flowActive, true);
  assert.deepEqual(data.edges[1].style.lineDash, [8, 4, 2, 4]);
  assert.equal(data.edges[1].style.flowActive, false);
  assert.deepEqual(data.edges[2].style.lineDash, [2, 4]);
  assert.equal(data.edges[2].style.flowActive, false);
  assert.deepEqual(data.nodes[1].style.lineDash, [3, 3]);
  assert.equal(data.nodes[0].style.badges[0].text, '2');
  assert.equal(makeG6Data({ ...props, motion: false }).edges[0].style.flowActive, false);
});

function topicLayoutFixture() {
  const words = Array.from({ length: 28 }, (_, index) => ({ id: `word-${String(index).padStart(2, '0')}`, type: 'concept', label: `中文知识组织与证据一致性核验词条${index}` }));
  const documents = Array.from({ length: 6 }, (_, index) => ({ id: `doc-${index}`, type: 'document', label: `隔离合成来源${index}` }));
  const nodes = [...words, ...documents], edges = [];
  const add = (source, target, index) => edges.push({ id: `semantic-${edges.length}`, from: source.id, to: target.id, type: index % 4 === 0 ? 'hierarchy' : 'association', layer: 'semantic', evidence: [{ blockId: 'p1' }] });
  words.forEach((node, index) => {
    if (words[index + 1]) add(node, words[index + 1], index);
    if (index > 1 && index % 2 === 0) add(words[0], node, index + 1);
    if (words[index + 5] && index % 3 === 0) add(node, words[index + 5], index + 2);
    edges.push({ id: `source-${index}`, from: node.id, to: documents[index % 6].id, type: 'source', layer: 'provenance' });
  });
  const metrics = Object.fromEntries(nodes.map((node, index) => [node.id, { degree: 100 - index, pagerank: 1 / (index + 1) }]));
  return { nodes, edges, metrics, rootId: words[0].id, metric: 'pagerank' };
}

test('34-node mixed topics keep bounded extents and force geometry is independent of prior layout extents', async () => {
  const fixture = topicLayoutFixture(), engines = { force: ForceLayout, radial: RadialLayout, concentric: ConcentricLayout, dagre: DagreLayout };
  let positions = appendAnchoredPositions(fixture.nodes, fixture.edges, new Map(), fixture.rootId).positions;
  let originalForce;
  for (const layout of ['force', 'radial', 'concentric', 'dagre', 'force']) {
    positions = await computeGraphLayout({ ...fixture, positions, layout }, engines);
    const current = fixture.nodes.map(node => positions.get(node.id));
    const width = Math.max(...current.map(point => point.x)) - Math.min(...current.map(point => point.x));
    const height = Math.max(...current.map(point => point.y)) - Math.min(...current.map(point => point.y));
    assert.ok(width < 2200 && height < 2200, `${layout} extent ${width} x ${height}`);
    if (layout === 'force') {
      if (!originalForce) originalForce = positions;
      else fixture.nodes.forEach(node => {
        assert.ok(Math.hypot(positions.get(node.id).x - originalForce.get(node.id).x, positions.get(node.id).y - originalForce.get(node.id).y) < .00001, node.id);
      });
    }
    if (layout === 'concentric') {
      const radii = new Set(current.map(point => Math.hypot(point.x - 450, point.y - 280).toFixed(4)));
      assert.ok(radii.size <= 6, `${radii.size} concentric rings`);
    }
  }
  const exaggerated = new Map([...positions].map(([id, point]) => [id, { x: point.x * 100 + 100000, y: point.y * 100 - 50000 }]));
  const recovered = await computeGraphLayout({ ...fixture, positions: exaggerated, layout: 'force' }, engines);
  fixture.nodes.forEach(node => assert.deepEqual(recovered.get(node.id), originalForce.get(node.id)));
});

test('concentric scores use at most six levels and Dagre only constrains semantic hierarchy when available', () => {
  const fixture = topicLayoutFixture();
  const bands = concentricScoreBands(fixture.nodes, fixture.metrics, fixture.metric, fixture.rootId);
  assert.equal(bands.get(fixture.rootId), 5);
  assert.ok(new Set(bands.values()).size <= 6);
  const constraints = dagreSemanticEdges(fixture.nodes, fixture.edges);
  assert.ok(constraints.length > 0);
  assert.ok(constraints.every(edge => edge.type === 'hierarchy'));
  assert.ok(fixture.edges.some(edge => edge.type === 'association'));
  const associationOnly = fixture.edges.filter(edge => edge.type !== 'hierarchy');
  assert.ok(dagreSemanticEdges(fixture.nodes, associationOnly).every(edge => edge.type === 'association' && edge.layer === 'semantic'));
});
