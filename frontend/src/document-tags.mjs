import { documentLanguageTag } from './translation-view.mjs';

const UNCATEGORIZED_LABELS = new Set(['未分类', '未归类']);
const labelOf = value => typeof value === 'string' ? value.trim() : '';

export const ARTICLE_TYPES = Object.freeze([
  {value: 'fact', label: '事实类'},
  {value: 'opinion', label: '观点类'},
  {value: 'technical', label: '技术类'},
]);
export const documentArticleType = doc => ARTICLE_TYPES.find(type => type.value === doc?.articleType) || null;

/**
 * Present persisted classification paths separately from free-form tags.
 * Levels always come from the complete stored path, never the display tree.
 */
export function getDocumentTags(doc) {
  const paths = Array.isArray(doc?.classificationPaths) ? doc.classificationPaths : [];
  const classificationLabels = new Set();
  const languageTag = documentLanguageTag(doc || {});
  const chips = languageTag ? [{label: languageTag, kind: 'language', level: null}] : [];
  const articleType = documentArticleType(doc);
  if (articleType) chips.push({label: articleType.label, kind: 'articleType', level: null, value: articleType.value});
  const byKey = new Map();

  for (const path of paths) {
    const nodes = path?.nodes;
    if (!Array.isArray(nodes) || nodes.length !== 3) continue;
    if (!Array.from(nodes).every(node => labelOf(node?.id) && labelOf(node?.name))) continue;
    if (new Set(nodes.map(node => labelOf(node.id))).size !== 3) continue;

    const names = nodes.map(node => labelOf(node.name));
    const pathLabel = names.join(' / ');
    names.forEach((label, index) => {
      classificationLabels.add(label);
      const uncategorized = UNCATEGORIZED_LABELS.has(label);
      const kind = uncategorized ? 'uncategorized' : 'classification';
      const level = uncategorized ? null : index + 1;
      const key = JSON.stringify([kind, level, label]);
      const existing = byKey.get(key);
      if (existing) {
        if (!existing.paths.includes(pathLabel)) existing.paths.push(pathLabel);
      } else {
        const chip = {label, kind, level, paths: [pathLabel]};
        byKey.set(key, chip);
        chips.push(chip);
      }
    });
  }

  const flatTags = Array.isArray(doc?.tags) ? doc.tags : [];
  const seenFlatTags = new Set();
  for (const value of flatTags) {
    const label = labelOf(value);
    if (!label || label === languageTag || classificationLabels.has(label) || seenFlatTags.has(label)) continue;
    seenFlatTags.add(label);
    chips.push({label, kind: UNCATEGORIZED_LABELS.has(label) ? 'uncategorized' : 'custom', level: null});
  }
  return chips;
}
