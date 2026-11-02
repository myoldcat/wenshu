// Pure graph state helpers. Layout positions belong to a scope (topic or focus),
// never to a polling response or to a theme.
export function edgeIdentity(edge) {
  return edge.id || edge.edgeId || JSON.stringify([edge.from, edge.to, edge.type, edge.layer, edge.label, edge.origin]);
}

export function mergeGraphPages(previous, incoming) {
  if (previous?.snapshotId && incoming.snapshotId && previous.snapshotId !== incoming.snapshotId) previous = null;
  if (!previous) previous = { nodes: [], edges: [], mergeEdges: [] };
  const nodes = [...new Map([...previous.nodes, ...(incoming.nodes || [])].map(node => [node.id, node])).values()];
  const nodeIds = new Set(nodes.map(node => node.id));
  const allEdges = [...new Map([
    ...(previous.edges || []), ...(previous.mergeEdges || []),
    ...(incoming.edges || []), ...(incoming.mergeEdges || []),
  ].map(edge => [edgeIdentity(edge), edge])).values()];
  return {
    ...previous, ...incoming, nodes,
    edges: allEdges.filter(edge => nodeIds.has(edge.from) && nodeIds.has(edge.to)),
    mergeEdges: allEdges.filter(edge => !nodeIds.has(edge.from) || !nodeIds.has(edge.to)),
  };
}

function ringPosition(slot) {
  if (slot === 0) return { x: 450, y: 250 };
  let remaining = slot - 1;
  for (let ring = 1; ; ring += 1) {
    const radius = ring * 175;
    const count = Math.floor(2 * Math.PI * radius / 185);
    if (remaining < count) {
      const angle = -Math.PI / 2 + 2 * Math.PI * remaining / count + (ring % 2 ? 0 : .18);
      return { x: 450 + radius * Math.cos(angle), y: 250 + radius * Math.sin(angle) };
    }
    remaining -= count;
  }
}

export function appendGraphPositions(rawNodes, edges, rootId, previous = new Map()) {
  const positions = new Map(previous);
  const degree = new Map();
  edges.forEach(edge => {
    degree.set(edge.from, (degree.get(edge.from) || 0) + 1);
    degree.set(edge.to, (degree.get(edge.to) || 0) + 1);
  });
  const added = rawNodes.filter(node => !positions.has(node.id)).sort((a, b) => (
    a.id === rootId ? -1 : b.id === rootId ? 1
      : (a.type === 'document') - (b.type === 'document')
        || (degree.get(b.id) || 0) - (degree.get(a.id) || 0) || a.id.localeCompare(b.id)
  ));
  // Retain vacant slots too: a removed and later reintroduced page resumes its
  // old place, and adding a page never shifts existing labels.
  added.forEach(node => positions.set(node.id, ringPosition(positions.size)));
  return positions;
}

export function fitGraphView(nodes) {
  const extentX = Math.max(330, ...nodes.map(node => Math.abs(node.x - 450) + 100));
  const extentY = Math.max(200, ...nodes.map(node => Math.abs(node.y - 250) + 65));
  return { x: 0, y: 0, zoom: Math.max(.12, Math.min(1, 420 / extentX, 205 / extentY)) };
}

export function relationshipNature(edge = {}) {
  if (edge.supportKind === 'manual_related' || edge.derivedBy === 'manual_related'
      || (edge.layer === 'navigation' && edge.origin === 'manual_decision')) {
    return { key: 'navigation', label: '人工关联', description: '用户确认相关但保留独立词条；此关联用于导航，不代表原文陈述了这一关系。' };
  }
  if (edge.layer === 'navigation' || edge.supportKind === 'exact_concept_definition'
      || ['exact_concept_definition', 'exact_name_and_definition'].includes(edge.derivedBy)
      || edge.origin === 'exact_concept_definition' || edge.supportKind === 'wording_equivalence') {
    return { key: 'navigation', label: '同定义导航', description: '名称与定义相同的独立来源词条；导航连接不代表原文陈述了这一关系。' };
  }
  if (edge.type === 'source' || edge.layer === 'provenance') {
    return { key: 'source', label: '原文关联', description: '词条与其来源文档的溯源连接。' };
  }
  return edge.evidence?.length
    ? { key: 'supported', label: '有据关系', description: '模型整理的关系附有原文证据，仍需核对上下文。' }
    : { key: 'unverified', label: '待核对关系', description: '当前关系没有可显示的原文证据。' };
}

