export const RETRIEVAL_CHANNELS = { keyword: '关键词', vector: '内容相近', category: '分类', summary: '摘要', claims: '观点', facts: '事实', graph: '图谱' };
export const MAX_WRITING_SOURCES = 100;

// A recommendation belongs to this exact writing context, never a later project.
export const materialContext = project => JSON.stringify({
  id: project.id, version: project.version, topic: project.topic || project.title || '',
  purpose: project.purpose || '', articleTitle: project.articleTitle || '', targetWords: project.targetWords, style: project.style,
  template: project.template, templateSections: project.templateSections || [], mode: project.mode, workflow: project.workflow,
  sampleReferenceText: project.sampleReferenceText || '', modelId: project.modelId || null,
  sourceIds: project.sourceIds || [], materialSelection: project.materialSelection || null,
});

export const passageKey = passage => `${passage.sourceId}:${passage.revisionId}:${passage.blockId}`;
export const parseMaterialQueries = text => [...new Set(text.split('\n').map(value => value.trim()).filter(Boolean))];

export function addMaterialSelections(selectedIds, candidateIds, excludedIds = []) {
  const selected = new Set(selectedIds), excluded = new Set(excludedIds);
  for (const id of candidateIds) {
    if (selected.size >= MAX_WRITING_SOURCES) break;
    if (!excluded.has(id)) selected.add(id);
  }
  return [...selected];
}

export function initialMaterialChoices(project) {
  const passages = project.materialSelection?.passages || [];
  return Object.fromEntries((project.sourceIds || []).map(id => {
    const selected = passages.filter(passage => passage.sourceId === id);
    return [id, { selected: true, mode: selected.length ? 'passages' : 'whole', passages: selected }];
  }));
}

export function mergeMaterialRecommendations(choices, items) {
  const next = { ...choices };
  for (const item of items) {
    if (next[item.sourceId]) continue;
    const passages = (item.passages || []).filter(passage => passage.blockId).map(passage => ({ sourceId: item.sourceId, revisionId: item.revisionId, blockId: passage.blockId }));
    next[item.sourceId] = { selected: Object.values(next).filter(value => value.selected).length < MAX_WRITING_SOURCES, mode: passages.length ? 'passages' : 'whole', passages };
  }
  return next;
}

export function materialPayload(choices, recommendationId = null) {
  const selected = Object.entries(choices).filter(([, choice]) => choice.selected);
  if (selected.length > MAX_WRITING_SOURCES) throw new Error(`一次写作最多选 ${MAX_WRITING_SOURCES} 篇资料。`);
  if (selected.some(([, choice]) => choice.mode === 'passages' && !choice.passages.length)) throw new Error('使用指定段落的资料至少需要勾选一段；也可切换为整篇资料。');
  return { sourceIds: selected.map(([id]) => id), materialSelection: { recommendationId, passages: selected.flatMap(([id, choice]) => choice.mode === 'passages' ? choice.passages.filter(passage => passage.sourceId === id) : []) } };
}
