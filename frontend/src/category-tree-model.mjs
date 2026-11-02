/** Display-only normalization. The stored, complete three-level paths never change. */
const DOCUMENT = 'document';
const CATEGORY = 'category';
const UNCATEGORIZED = 'uncategorized';

const uniqueDocuments = documents => [...new Map(documents.map(doc => [String(doc.id), doc])).values()];
const titleOf = doc => String(doc.title || '无标题文档');

export function documentClassificationPaths(doc) {
  return (Array.isArray(doc.classificationPaths) ? doc.classificationPaths : []).filter(path =>
    Array.isArray(path?.nodes) && path.nodes.length === 3 &&
    path.nodes.every(node => typeof node?.id === 'string' && node.id.trim() && typeof node.name === 'string' && node.name.trim()) &&
    new Set(path.nodes.map(node => node.id)).size === 3,
  );
}

const documentNode = doc => ({
  kind: DOCUMENT, id: String(doc.id), key: `doc:${doc.id}`, name: titleOf(doc), document: doc,
  documents: [doc], documentIds: [String(doc.id)], count: 1,
});

function withCounts(node, children) {
  const documents = uniqueDocuments(children.flatMap(child => child.documents));
  return {...node, children, documents, documentIds: documents.map(doc => String(doc.id)), count: documents.length};
}

function unclassifiedNode(parent, documents) {
  const id = `uncat:${parent.id}`;
  return withCounts({
    kind: UNCATEGORIZED, id, key: id, selection: id, name: '未归类',
    path: parent.path || [], pathLabel: [...(parent.path || []).map(node => node.name), '未归类'].join(' / '),
  }, uniqueDocuments(documents).map(documentNode));
}

function descendantDocuments(node) {
  return node.kind === DOCUMENT ? [node.document] : node.children.flatMap(descendantDocuments);
}

/**
 * Recursively normalize children before deciding whether their parent survives.
 * Original first-level topics are anchors; every other category needs at least
 * two distinct articles, except when a singleton's original category must be
 * restored to keep documents from sharing a level with folders.
 * Removed folders return articles to the nearest surviving parent. A topic's
 * virtual 未归类 bucket groups loose articles alongside other folders;
 * otherwise expose those articles directly when there are no folders.
 * A lone loose article beside a folder with at least two distinct articles
 * keeps its deepest original categories,
 * or an 未归类 folder if no narrower category is available. Keep each path so
 * promoting a lower category never grants it the first-level exception.
 */
function normalizeCategory(raw, isRoot = false) {
  const children = [];
  const looseDocuments = [];
  const looseCategories = [];
  for (const child of raw.children) {
    if (child.kind === DOCUMENT) children.push(child);
    else if (child.kind === UNCATEGORIZED) looseDocuments.push(...child.documents);
    // Stored paths also use 未归类 when the evidence cannot support a narrower
    // topic. They share the same display bucket as singleton articles; retain
    // the original stored path in the category index, not duplicate wrappers.
    else if (child.name === '未归类') looseDocuments.push(...descendantDocuments(child));
    else {
      const result = normalizeCategory(child);
      if (result.node) children.push(result.node);
      looseDocuments.push(...result.looseDocuments);
      looseCategories.push(...result.looseCategories);
    }
  }
  const directIds = new Set(children.filter(child => child.kind === DOCUMENT).map(child => child.id));
  const loose = uniqueDocuments(looseDocuments).filter(doc => !directIds.has(String(doc.id)));
  if (loose.length === 1) {
    if (children.some(child => child.kind !== DOCUMENT)) {
      // Candidates carry the exact original occurrence, including paths for
      // multi-classified articles. Never choose a category from a flat tag or
      // another branch of the article's classification.
      // A restored singleton must not let another singleton survive in turn.
      // Check already-retained peers, before adding any restored categories.
      const hasPopulatedPeer = children.some(child => child.kind !== DOCUMENT && child.count > 1);
      if (hasPopulatedPeer && looseCategories.length) children.push(...looseCategories);
      else children.push(unclassifiedNode(raw, loose));
    } else children.push(documentNode(loose[0]));
  } else if (loose.length) children.push(unclassifiedNode(raw, loose));
  const node = withCounts(raw, children);
  if (isRoot) return {node, looseDocuments: [], looseCategories: []};
  if (!node.count) return {node: null, looseDocuments: [], looseCategories: []};
  const isFirstLevel = raw.path.length === 1;
  // Test unique membership before the sole-未归类 exception. Otherwise a single
  // article could keep an entire chain alive after all its descendants folded.
  if (!isFirstLevel && node.count === 1) return {
    node: null, looseDocuments: node.documents,
    looseCategories: looseCategories.length ? looseCategories : [node],
  };
  if (children.length === 1) {
    const only = children[0];
    if (only.kind === UNCATEGORIZED) {
      return {node: withCounts({...node, unclassifiedExpanded: true}, only.children), looseDocuments: [], looseCategories: []};
    }
    if (only.kind === CATEGORY && !isFirstLevel) return {node: only, looseDocuments: [], looseCategories: []};
  }
  return {node, looseDocuments: [], looseCategories: []};
}

function indexCategories(node, index) {
  if (node.selection) {
    const previous = index[node.selection];
    const documents = previous ? uniqueDocuments([...previous.documents, ...node.documents]) : node.documents;
    index[node.selection] = {
      id: node.id, kind: node.kind, name: node.name, pathLabel: node.pathLabel,
      selection: node.selection, documents, documentIds: documents.map(doc => String(doc.id)), count: documents.length,
    };
  }
  for (const child of node.children || []) indexCategories(child, index);
}

export function buildCategoryTree(documents = []) {
  const root = {kind: 'root', id: 'root', key: 'root', name: '全部文档', path: [], children: []};
  const unclassified = [];
  const childrenById = new WeakMap([[root, new Map()]]);
  const leafDocumentIds = new WeakMap();
  const originalMembership = new Map();
  for (const doc of uniqueDocuments(documents.filter(doc => doc?.id != null))) {
    const paths = documentClassificationPaths(doc);
    if (!paths.length) {
      unclassified.push(doc);
      continue;
    }
    for (const path of paths) {
      let parent = root;
      for (const entry of path.nodes) {
        const children = childrenById.get(parent);
        let child = children.get(entry.id);
        if (!child) {
          const originalPath = [...parent.path, {id: entry.id, name: entry.name}];
          child = {
            kind: CATEGORY, id: entry.id, key: `cat:${originalPath.map(node => encodeURIComponent(node.id)).join('/')}`,
            selection: `cat:${entry.id}`, name: entry.name, path: originalPath,
            pathLabel: originalPath.map(node => node.name).join(' / '), children: [],
          };
          parent.children.push(child);
          children.set(entry.id, child);
          childrenById.set(child, new Map());
        }
        let membership = originalMembership.get(child.selection);
        if (!membership) {
          membership = {id: child.id, kind: CATEGORY, name: child.name, pathLabel: child.pathLabel,
            selection: child.selection, documents: new Map()};
          originalMembership.set(child.selection, membership);
        }
        membership.documents.set(String(doc.id), doc);
        parent = child;
      }
      let leafIds = leafDocumentIds.get(parent);
      if (!leafIds) {leafIds = new Set(); leafDocumentIds.set(parent, leafIds);}
      if (!leafIds.has(String(doc.id))) {
        parent.children.push(documentNode(doc));
        leafIds.add(String(doc.id));
      }
    }
  }
  if (unclassified.length) root.children.push(unclassifiedNode(root, unclassified));
  const tree = normalizeCategory(root, true).node;
  tree.selectionIndex = Object.create(null);
  indexCategories(tree, tree.selectionIndex);
  // Keep names and membership for a category selected before a later edit hides
  // it through compaction. Virtual buckets only exist in the actual display tree.
  for (const [selection, membership] of originalMembership) {
    if (tree.selectionIndex[selection]) continue;
    const matching = [...membership.documents.values()];
    tree.selectionIndex[selection] = {...membership, documents: matching,
      documentIds: matching.map(doc => String(doc.id)), count: matching.length, hidden: true};
  }
  return tree;
}

export function findCategoryNode(tree, selection = '全部') {
  if (!selection || selection === '全部') return tree || null;
  return tree?.selectionIndex?.[selection] || null;
}

/** Virtual buckets cease to be filters when compaction removes their scope. */
export function reconcileCategorySelection(selection = '全部', tree) {
  return selection.startsWith('uncat:') && !findCategoryNode(tree, selection) ? '全部' : selection;
}

export function docMatchesCategory(doc, selection = '全部', tree) {
  if (!selection || selection === '全部') return true;
  const visible = tree?.selectionIndex?.[selection];
  if (visible) return visible.documentIds.includes(String(doc.id));
  // A selected category can disappear after a document edit. Its stable ID still
  // identifies its original subtree; never guess a virtual bucket by tag names.
  if (selection.startsWith('cat:')) {
    const id = selection.slice(4);
    return documentClassificationPaths(doc).some(path => path.nodes.some(node => node.id === id));
  }
  if (selection.startsWith('uncat:')) {
    return selection === 'uncat:root' && !documentClassificationPaths(doc).length;
  }
  const tags = Array.isArray(doc.tags) ? doc.tags : [];
  return (selection === '未分类' && !tags.length) || tags.includes(selection);
}

export function categoryLabel(selection = '全部', tree) {
  if (!selection || selection === '全部') return '全部文档';
  return tree?.selectionIndex?.[selection]?.name || (selection.startsWith('uncat:') ? '未归类' : selection);
}

/** Search the already-normalized tree, retaining its category names and levels. */
export function filterCategoryTree(tree, query = '') {
  const search = String(query).trim().toLocaleLowerCase();
  if (!search) return tree;
  function visit(node, ancestorMatches = false) {
    if (node.kind === DOCUMENT) return ancestorMatches || node.name.toLocaleLowerCase().includes(search) ? node : null;
    const matches = ancestorMatches || (node.kind !== 'root' && `${node.name} ${node.pathLabel}`.toLocaleLowerCase().includes(search));
    const children = node.children.map(child => visit(child, matches)).filter(Boolean);
    return children.length || node.kind === 'root' ? withCounts(node, children) : null;
  }
  return visit(tree);
}
