// View identity includes the representation. A shared topic viewId is supplied
// from the full server-side member set, never inferred from a truncated canvas.
export function wikiRoot(mode = 'unified') {
  return mode === 'source' ? '/wiki' : '/wiki/knowledge';
}

export function graphScopeKey(mode, topic, exploration, fallback = '') {
  return `${mode}:${exploration ? `neighborhood:${exploration.entryId}` : `topic:${topic?.viewId || topic?.id || fallback}`}`;
}

export function inheritTopicScopeCaches(mode, topicId, scopeKey, aliases, cameras, layouts) {
  if (!topicId) return;
  const alias = `${mode}:${topicId}`, previous = aliases.get(alias);
  if (previous && previous !== scopeKey) {
    if (!cameras.has(scopeKey) && cameras.has(previous)) cameras.set(scopeKey, { ...cameras.get(previous) });
    if (!layouts.has(scopeKey) && layouts.has(previous)) layouts.set(scopeKey, new Map(layouts.get(previous)));
  }
  aliases.set(alias, scopeKey);
}

export function boundedSourceSelection(current, sourceId, max = 10) {
  if (current.includes(sourceId)) return current.filter(id => id !== sourceId);
  return current.length < max ? [...current, sourceId] : current;
}

export function resolutionEntryIds(proposal) {
  return [...new Set(proposal.entryIds || (proposal.entries || []).map(entry => entry.sourceEntryId || entry.id))].filter(Boolean);
}

export function resolutionJudgment(proposal) {
  return proposal.judgment === 'same_as' ? 'same' : proposal.judgment;
}

export function resolutionPresentation(proposal) {
  const judgment = resolutionJudgment(proposal);
  const label = ({ same: '可能是同一概念', broader_than: '上位关系（范围更广）', narrower_than: '下位关系（范围更窄）',
    related: '相关但不同', distinct: '保持独立', uncertain: '尚不能确定' })[judgment] || '待核对';
  return { label, mergeAllowed: ['same', 'uncertain'].includes(judgment),
    explanation: ['broader_than', 'narrower_than'].includes(judgment) ? '上下位概念保留独立词条，可选择仅关联或保持独立。'
      : ['related', 'distinct'].includes(judgment) ? '此候选判断为不同概念，可选择仅关联或保持独立。' : '' };
}

export function afterKnowledgeIdentityChange(saved = {}) {
  // A changed canonical membership may remove the selected node. Retain the
  // topic, query and separate canvas caches while discarding that stale focus.
  return { ...saved, selection: null, version: '', exploration: null, selectedPath: null };
}

export function resolutionBody(proposal, snapshotId, { label = proposal.label || '', reason = '', judgment } = {}) {
  const entryIds = resolutionEntryIds(proposal);
  if (!snapshotId || entryIds.length < 2) return null;
  return { ...(proposal.id && proposal.id.length <= 150 ? { proposalId: proposal.id } : {}), entryIds, expectedSnapshotId: snapshotId, label: label.trim(), reason: reason.trim(), ...(judgment ? { judgment } : {}) };
}
