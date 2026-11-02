const TREE_SELECTION_PREFIX = 'tree:';

export const isCategoryTreeSelection = value => typeof value === 'string' && value.startsWith(TREE_SELECTION_PREFIX);
export const categoryTreeSelectionId = pathKeys => TREE_SELECTION_PREFIX + encodeURIComponent(JSON.stringify(pathKeys));

const unique = values => [...new Set(values)];
const intersects = (left, right) => [...left].some(value => right.has(value));
const isSubset = (left, right) => [...left].every(value => right.has(value));

/** Occurrence paths, rather than shared category/document IDs, define ancestry. */
export function compileCategoryCascade(tree) {
  const entries = new Map(), bySelection = new Map();
  let order = 0;
  const walk = (node, pathKeys, parent) => {
    const id = categoryTreeSelectionId(pathKeys);
    const pathLabel = node.pathLabel || [parent?.pathLabel, node.name].filter(Boolean).join(' / ') || '未命名目录项';
    const entry = {id, node, parent, pathLabel, children: [], order: order++};
    entries.set(id, entry);
    if (node.selection) {
      if (!bySelection.has(node.selection)) bySelection.set(node.selection, []);
      bySelection.get(node.selection).push(entry);
    }
    entry.children = (node.children || []).map(child => walk(child, [...pathKeys, child.key], entry));
    // Empty directories remain explicit empty filters, not the unscoped library.
    entry.leaves = entry.children.length ? unique(entry.children.flatMap(child => child.leaves)) : [id];
    entry.leafSet = new Set(entry.leaves);
    entry.documentIds = node.kind === 'document'
      ? (node.id == null ? [] : [String(node.id)])
      : unique(entry.children.flatMap(child => child.documentIds));
    return entry;
  };
  const roots = (tree?.children || []).map(node => walk(node, [node.key], null));
  return {tree, entries, bySelection, roots};
}

function friendlyLabel(selection, tree, resolved) {
  if (resolved?.length === 1) return resolved[0].pathLabel;
  const indexed = tree?.selectionIndex?.[selection];
  if (indexed) return indexed.pathLabel || indexed.name;
  return /^(tree|cat|uncat|source|date|tag):/.test(selection) ? '已失效的筛选' : selection;
}

function sourcesFor(selections, compiled) {
  return selections.map((selection, index) => {
    const internal = isCategoryTreeSelection(selection);
    const resolved = internal
      ? (compiled.entries.has(selection) ? [compiled.entries.get(selection)] : [])
      : (compiled.bySelection.get(selection) || []);
    return {selection, index, internal, resolved,
      leaves: new Set(resolved.flatMap(entry => entry.leaves)),
      label: friendlyLabel(selection, compiled.tree, resolved)};
  });
}

function selectionResult(sources, coverage, compiled) {
  const checked = new Set(), indeterminate = new Set();
  for (const entry of compiled.entries.values()) {
    const selected = entry.leaves.filter(leaf => coverage.has(leaf)).length;
    if (selected === entry.leaves.length) checked.add(entry.id);
    else if (selected) indeterminate.add(entry.id);
  }
  const rank = leaves => sources.reduce((first, source) => intersects(leaves, source.leaves) ? Math.min(first, source.index) : first, Infinity);
  let items = [];
  const frontier = entry => {
    if (checked.has(entry.id)) items.push({selection: entry.id, leaves: entry.leafSet,
      label: entry.pathLabel, rank: rank(entry.leafSet), order: entry.order,
      containsDocuments: entry.node.kind === 'document'});
    else entry.children.forEach(frontier);
  };
  // The synthetic 全部文档 root is a clear action, never a counted selection.
  compiled.roots.forEach(frontier);

  // Preserve a legacy category's exact union (including repeated occurrences)
  // when it is still whole. A partial legacy parent is expanded into paths.
  for (const source of sources.filter(source => !source.internal && source.resolved.length)) {
    if (!isSubset(source.leaves, coverage)) continue;
    const contained = items.filter(item => isSubset(item.leaves, source.leaves));
    const union = new Set(contained.flatMap(item => [...item.leaves]));
    if (!contained.length || union.size !== source.leaves.size || !isSubset(source.leaves, union)) continue;
    const removed = new Set(contained);
    items = items.filter(item => !removed.has(item));
    items.push({selection: source.selection, leaves: source.leaves, label: source.label,
      rank: source.index, order: Math.min(...contained.map(item => item.order)), containsDocuments: false});
  }
  for (const source of sources.filter(source => !source.resolved.length)) {
    items.push({selection: source.selection, label: source.label, rank: source.index,
      order: Infinity, containsDocuments: false});
  }
  items.sort((left, right) => left.rank - right.rank || left.order - right.order);
  return {checked, indeterminate, selections: items.map(item => item.selection),
    count: items.length, labels: items.map(item => item.label),
    containsDocuments: items.some(item => item.containsDocuments)};
}

export function categoryCascadeState(selections, tree) {
  const compiled = compileCategoryCascade(tree);
  const sources = sourcesFor(selections, compiled);
  return selectionResult(sources, new Set(sources.flatMap(source => [...source.leaves])), compiled);
}

export function toggleCategoryCascadeSelection(selections, selection, tree) {
  const compiled = compileCategoryCascade(tree);
  let sources = sourcesFor(selections, compiled);
  const target = sourcesFor([selection], compiled)[0];
  const coverage = new Set(sources.flatMap(source => [...source.leaves]));
  if (!target.resolved.length) {
    sources = selections.includes(selection)
      ? sources.filter(source => source.selection !== selection)
      : [...sources, {...target, index: sources.length}];
  } else if (isSubset(target.leaves, coverage)) {
    for (const leaf of target.leaves) coverage.delete(leaf);
  } else {
    for (const leaf of target.leaves) coverage.add(leaf);
    sources.push({...target, index: sources.length});
  }
  return selectionResult(sources, coverage, compiled).selections;
}

export function categoryTreeSelectionDocuments(selection, tree) {
  return compileCategoryCascade(tree).entries.get(selection)?.documentIds || [];
}

export function flatCategorySelectionLabel(selection, tree) {
  return friendlyLabel(selection, tree);
}
