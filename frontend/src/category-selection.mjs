import {categoryLabel, docMatchesCategory, reconcileCategorySelection} from './category-tree-model.mjs';
import {categoryCascadeState, categoryTreeSelectionDocuments, flatCategorySelectionLabel, isCategoryTreeSelection, toggleCategoryCascadeSelection} from './category-cascade.mjs';
export {categoryTreeSelectionId} from './category-cascade.mjs';

const usesTreeCascade = tree => !!tree && tree.mode !== 'tag';

/** Empty selection means the whole library; category IDs and legacy tags stay distinct. */
export function normalizeCategorySelections(values = []) {
  const selections = Array.isArray(values) ? values : [values];
  return [...new Set(selections.filter(value => typeof value === 'string' && value && value !== '全部'))];
}

export function toggleCategorySelection(current, selection, multiple = false, tree) {
  const selections = normalizeCategorySelections(current);
  if (!selection || selection === '全部') return [];
  if (!multiple) return [selection];
  if (usesTreeCascade(tree)) return toggleCategoryCascadeSelection(selections, selection, tree);
  return selections.includes(selection)
    ? selections.filter(value => value !== selection)
    : [...selections, selection];
}

/** Leaving multi-select retains one visible filter, rather than hidden extra scopes. */
export function categoriesForSelectionMode(current, multiple, tree) {
  const selections = usesTreeCascade(tree) ? categorySelectionState(current, tree).selections : normalizeCategorySelections(current);
  return multiple ? selections : selections.slice(0, 1);
}

export function reconcileCategorySelections(current, tree) {
  const selections = normalizeCategorySelections(current)
    .filter(selection => isCategoryTreeSelection(selection) || (reconcileCategorySelection(selection, tree) !== '全部' &&
      (!/^(source|date|tag):/.test(selection) || !!tree?.selectionIndex?.[selection])));
  return Array.isArray(current) && selections.length === current.length && selections.every((value, index) => value === current[index])
    ? current : selections;
}

/** A nonempty selection with no members deliberately returns [], never unscoped null. */
export function categorySelectionScope(current, documents = [], tree) {
  const selections = normalizeCategorySelections(current);
  if (!selections.length) return null;
  const pathMembers = new Set(selections.filter(isCategoryTreeSelection).flatMap(selection => categoryTreeSelectionDocuments(selection, tree)));
  return [...new Set(documents.filter(doc => selections.some(selection => {
    if (isCategoryTreeSelection(selection)) return pathMembers.has(String(doc.id));
    // Group IDs are never flat tags, including after their group disappears.
    if (/^(source|date|tag):/.test(selection)) return tree?.selectionIndex?.[selection]?.documentIds.includes(String(doc.id)) || false;
    return docMatchesCategory(doc, selection, tree);
  }))
    .map(doc => String(doc.id)))];
}

export function categorySelectionLabel(current, tree) {
  const state = categorySelectionState(current, tree), selections = state.selections;
  if (selections.length > 1) return `已选 ${state.count} ${state.containsDocuments || tree?.mode && tree.mode !== 'topic' ? '项' : '类'}`;
  if (isCategoryTreeSelection(selections[0])) return state.labels[0];
  if (state.labels[0] === '已失效的筛选') return state.labels[0];
  return ['date', 'tag'].includes(tree?.mode) && tree.selectionIndex?.[selections[0]]
    ? tree.selectionIndex[selections[0]].pathLabel : categoryLabel(selections[0], tree);
}

/** Checked descendants are derived; only the compressed frontier is counted. */
export function categorySelectionState(current, tree) {
  const selections = normalizeCategorySelections(current);
  if (usesTreeCascade(tree)) return categoryCascadeState(selections, tree);
  return {checked: new Set(selections), indeterminate: new Set(), selections,
    count: selections.length, labels: selections.map(selection => flatCategorySelectionLabel(selection, tree)), containsDocuments: false};
}
