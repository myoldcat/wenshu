// Persisted writing intent, independent of navigation and derived view state.
const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const own = (value, key) => Object.hasOwn(value, key);
const list = value => Array.isArray(value) ? value : [];
const number = (value, fallback) => value == null ? fallback
  : typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value)) ? Number(value) : value;
export const writingMode = value => value === 'expanded' ? 'extended' : value || 'inherit';
const ids = value => [...new Set(list(value))].sort();
const canonical = value => Array.isArray(value) ? value.map(canonical)
  : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort()
    .filter(key => value[key] !== undefined).map(key => [key, canonical(value[key])])) : value;
const fingerprint = value => JSON.stringify(canonical(value));
export const writingSemanticEqual = (a, b) => fingerprint(a) === fingerprint(b);
const fields = project => ({ ...object(project.brief), ...project });

function automaticOptions(value) {
  if (value == null) return null;
  if (typeof value !== 'object' || Array.isArray(value)) return value;
  const saved = object(value);
  const modern = ['useSelectedSources', 'useRecommendedSources', 'mode'].some(key => own(saved, key));
  return modern ? { ...saved, useSelectedSources: saved.useSelectedSources ?? true,
    useRecommendedSources: saved.useRecommendedSources ?? true, mode: writingMode(saved.mode || 'strict'),
    includeImages: saved.includeImages ?? true, skipReview: saved.skipReview ?? false }
    : { ...saved, restrictToSelectedSources: saved.restrictToSelectedSources ?? false, includeImages: saved.includeImages ?? true,
      skipReview: saved.skipReview ?? false };
}

export function writingBriefPayload(project = {}) {
  const p = fields(project), original = object(project.brief);
  const brief = { ...original, topic: p.topic ?? '', articleTitle: p.articleTitle ?? original.articleTitle ?? p.title ?? '',
    assistanceIds: p.assistanceIds || [], purpose: p.purpose ?? '', targetWords: number(p.targetWords, 6000),
    style: p.style ?? '清晰克制', template: p.template ?? '观点文章',
    workflow: p.workflow === 'oneshot' ? 'oneshot' : 'chapter', automation: p.automation === 'automatic' ? 'automatic' : 'manual',
    templateSections: p.templateSections || [], sampleReferenceText: p.sampleReferenceText ?? '',
    sampleStructureApplied: !!p.sampleStructureApplied };
  if (p.automaticOptions != null) brief.automaticOptions = p.automaticOptions;
  else delete brief.automaticOptions;
  if (own(p, 'interactiveGeneration')) brief.interactiveGeneration = p.interactiveGeneration;
  else delete brief.interactiveGeneration;
  for (const key of ['structureTemplate', 'writingStyle']) {
    if (own(p, key)) brief[key] = p[key];
    else delete brief[key];
  }
  return brief;
}

function passages(value) {
  const byKey = new Map(list(value).map(passage => [fingerprint(passage), passage]));
  return [...byKey].sort(([a], [b]) => a.localeCompare(b)).map(([, value]) => value);
}
function userSources(project) {
  const selection = object(project.materialSelection), current = new Set(list(project.sourceIds));
  return ids(own(selection, 'userSourceIds') ? selection.userSourceIds : project.sourceIds).filter(id => current.has(id));
}
function materialSelection(project) {
  const value = object(project.materialSelection);
  return { ...value, recommendationId: value.recommendationId ?? null, passages: passages(value.passages),
    queries: value.queries || [], items: value.items || [], userSourceIds: userSources(project),
    confirmed: !!value.confirmed, assistanceIds: ids(value.assistanceIds) };
}
function briefIntent(project, includeAudit = true) {
  const value = writingBriefPayload(project);
  value.automaticOptions = automaticOptions(value.automaticOptions);
  // Missing historical choices retain the short-draft single-request default.
  // Compare its meaning without adding a field to an unchanged saved payload.
  if (!own(value, 'interactiveGeneration')) value.interactiveGeneration = 'single';
  if (includeAudit) value.assistanceIds = ids(value.assistanceIds);
  else delete value.assistanceIds;
  return value;
}

export function writingProjectPayload(project = {}) {
  const p = fields(project), brief = writingBriefPayload(project);
  return { title: (brief.articleTitle || brief.topic || '').trim().slice(0, 200), brief,
    mode: writingMode(p.mode), sourceIds: project.sourceIds || [],
    materialSelection: project.materialSelection || { recommendationId: null, passages: [] }, modelId: p.modelId || null };
}
export function writingOutlinePayload(project = {}) {
  return list(project.sections).map(section => ({ ...(section.id?.startsWith('section_') ? { id: section.id } : {}),
    title: section.title, goal: section.goal || '', mode: writingMode(section.mode), targetWords: number(section.words ?? section.targetWords, 600) }));
}
export function writingProjectFingerprint(project = {}) {
  return fingerprint({ ...writingProjectPayload(project), brief: briefIntent(project),
    sourceIds: ids(project.sourceIds), materialSelection: materialSelection(project) });
}
export function writingProjectPatch(project = {}, saved = {}) {
  const next = writingProjectPayload(project), before = writingProjectPayload(saved), patch = {};
  for (const key of ['title', 'mode', 'modelId']) if (!writingSemanticEqual(next[key], before[key])) patch[key] = next[key];
  if (!writingSemanticEqual(briefIntent(project), briefIntent(saved))) patch.brief = next.brief;
  if (!writingSemanticEqual(ids(project.sourceIds), ids(saved.sourceIds))) patch.sourceIds = next.sourceIds;
  if (!writingSemanticEqual(materialSelection(project), materialSelection(saved))) patch.materialSelection = next.materialSelection;
  return patch;
}
export function writingBriefFingerprint(project = {}) { return fingerprint(briefIntent(project, false)); }
export function writingMaterialScopeFingerprint(project = {}) {
  return fingerprint({ mode: writingMode(project.mode), sourceIds: ids(project.sourceIds),
    passages: passages(project.materialSelection?.passages), userSourceIds: userSources(project) });
}
export function writingOutlineFingerprint(project = {}) {
  return fingerprint(writingOutlinePayload(project).map(section => ({ ...section,
    title: typeof section.title === 'string' ? section.title.trim() : section.title,
    goal: section.goal.trim() })));
}
export function writingBodyFingerprint(project = {}) {
  return fingerprint(list(project.sections).map(section => [section.id, section.content ?? '', !!section.locked]));
}
export function writingEditableFingerprint(project = {}) {
  return fingerprint([writingProjectFingerprint(project), writingOutlineFingerprint(project), writingBodyFingerprint(project)]);
}
