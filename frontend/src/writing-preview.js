// Citation tokens identify excerpts. Exact sentence highlights require a saved
// evidence assessment for this exact chapter content and marker occurrence.
// Body offsets are UTF-16 positions, just like the native chapter textarea.
// Source quote offsets remain codepoint offsets supplied by the service.
import { writingSourceStatus } from './writing-library-availability.js';
function paragraphParts(content) {
  const result = [];
  let start = 0;
  for (const match of content.matchAll(/\n\s*\n/g)) {
    const text = content.slice(start, match.index);
    if (text.trim()) result.push({ text, start, end: match.index });
    start = match.index + match[0].length;
  }
  if (content.slice(start).trim()) result.push({ text: content.slice(start), start, end: content.length });
  return result;
}

function validBodyRange(content, item) {
  if (!Number.isInteger(item?.start) || !Number.isInteger(item?.end) || item.start < 0 || item.end <= item.start || item.end > content.length || typeof item.text !== 'string' || !item.text.trim()) return false;
  const splitsSurrogate = offset => offset > 0 && offset < content.length && /[\uD800-\uDBFF]/.test(content[offset - 1]) && /[\uDC00-\uDFFF]/.test(content[offset]);
  return !splitsSurrogate(item.start) && !splitsSurrogate(item.end) && content.slice(item.start, item.end) === item.text && !/\[E\d+\]/.test(item.text);
}

function bindingState(section, content, mode) {
  const saved = section.citationBindings;
  if (!saved) return { status: section.citationBindingState === 'stale' ? 'stale' : 'unmatched', items: [] };
  if (saved.version !== 1 || !Array.isArray(saved.items) || typeof saved.content !== 'string') return { status: 'invalid', items: [] };
  // Exact full text also guards unsaved drafts whose server hash is unchanged.
  if (saved.content !== content || (section.contentHash && saved.contentHash !== section.contentHash) || (saved.mode && saved.mode !== mode)) return { status: 'stale', items: [] };
  return { status: 'current', items: saved.items };
}

function exactCitationBindingRange(content, markerStart, saved, evidenceId, status) {
  if (saved?.version !== 1 || saved.content !== content || !Array.isArray(saved.items)) return null;
  const items = saved.items.filter(item => item?.markerStart === markerStart);
  if (items.length !== 1) return null;
  const item = items[0];
  if (item.status !== status || item.evidenceId !== evidenceId || typeof evidenceId !== 'string' || !validBodyRange(content, item) || content.slice(markerStart, markerStart + evidenceId.length + 2) !== `[${evidenceId}]` || markerStart < item.end || content.slice(item.end, markerStart).replace(/\[E\d+\]/g, '') !== '') return null;
  if (status === 'unreviewed' && item.supportBasis !== 'none') return null;
  return { start: item.start, end: item.end };
}

export function citationSentenceRange(content, markerStart, saved, evidenceId) {
  return exactCitationBindingRange(content, markerStart, saved, evidenceId, 'supported');
}

// A sidebar card can show a different assessment for each use of one excerpt.
export function citationForOccurrence(paragraph, id, occurrence) {
  const citation = paragraph?.citations.find(item => item.id === id);
  const token = paragraph?.tokens.find(item => item.type === 'citation' && item.id === id && item.start === occurrence);
  return citation && token ? { ...citation, bindingStatus: token.bindingStatus, bindingText: token.bindingText } : citation;
}

// The server validates the program-owned archive against the saved chapter's
// evidence and prose. It is a chapter scope, never a new generation source pool.
function preservedChapterSources(section) {
  const saved = section.citationSourceScope;
  if (section.citationSourceScopeValid !== true || saved?.policy !== 'preserved-chapter-evidence-scope-v1' ||
    typeof section.evidenceHash !== 'string' || !section.evidenceHash.trim() || saved.evidenceHash !== section.evidenceHash) return null;
  const sources = saved.sources;
  if (!Array.isArray(sources) || sources.length > 100 || sources.some(source => !source || typeof source !== 'object' ||
    !['sourceId', 'revisionId'].every(key => typeof source[key] === 'string' && source[key].trim()) ||
    Object.hasOwn(source, 'blockIds') && (!Array.isArray(source.blockIds) || source.blockIds.some(id => typeof id !== 'string' || !id.trim())))) return null;
  if (new Set(sources.map(source => JSON.stringify([source.sourceId, source.revisionId]))).size !== sources.length) return null;
  return sources;
}

export function buildWritingPreview(project = {}) {
  const sourceIds = new Set(project.sourceIds || []);
  const frozenSources = new Map((project.sources || []).map(source => [JSON.stringify([source.sourceId, source.revisionId]), source]));
  const sourceNumbers = new Map();
  const sections = (project.sections || []).map((section, sectionIndex) => {
    const preserved = preservedChapterSources(section);
    const chapterSourceIds = preserved ? new Set(preserved.map(source => source.sourceId)) : sourceIds;
    const chapterSources = preserved ? new Map(preserved.map(source => [JSON.stringify([source.sourceId, source.revisionId]), source])) : frozenSources;
    const content = String(section.content || ''), effectiveMode = !section.mode || section.mode === 'inherit' ? project.mode : section.mode;
    // The editor calls extended mode "expanded"; saved assessments use the API
    // value. Compare the same actual mode without altering the saved record.
    const mode = effectiveMode === 'expanded' ? 'extended' : effectiveMode, assessment = bindingState(section, content, mode);
    const retainUncitedProse = mode === 'extended' && section.extendedContentPolicy === 'retain-as-prose-v2';
    const registry = new Map();
    for (const evidence of section.evidence || []) {
      const entries = registry.get(evidence.id) || [];
      registry.set(evidence.id, [...entries, evidence]);
    }
    const resolveCitation = id => {
      const entries = registry.get(id);
      if (!entries?.length) return { id, status: 'unknown' };
      if (entries.length !== 1) return { id, status: 'ambiguous' };
      const evidence = entries[0];
      if (!['sourceId', 'revisionId', 'blockId', 'title', 'quote'].every(key => typeof evidence[key] === 'string' && evidence[key].trim()) ||
        !Number.isInteger(evidence.start) || !Number.isInteger(evidence.end) || evidence.start < 0 || evidence.end <= evidence.start || evidence.end - evidence.start !== Array.from(evidence.quote).length) return { id, status: 'invalid_anchor', evidence };
      const source = chapterSources.get(JSON.stringify([evidence.sourceId, evidence.revisionId]));
      if (!chapterSourceIds.has(evidence.sourceId) || !source) return { id, status: 'out_of_scope', evidence };
      // An explicitly narrowed source authorizes only those original blocks.
      // Empty/malformed explicit scope never becomes permission for the article.
      if (Object.hasOwn(source, 'blockIds') && (!Array.isArray(source.blockIds) || !source.blockIds.includes(evidence.blockId))) return { id, status: 'out_of_scope', evidence };
      const sourceKey = JSON.stringify([evidence.sourceId, evidence.revisionId]);
      if (!sourceNumbers.has(sourceKey)) sourceNumbers.set(sourceKey, sourceNumbers.size + 1);
      return { id, status: 'registered', evidence, number: sourceNumbers.get(sourceKey),
        sourceStatus: writingSourceStatus(project, evidence) };
    };
    const paragraphs = paragraphParts(content).map(({ text, start: paragraphStart, end: paragraphEnd }, index) => {
      const citations = [], tokens = [], seen = new Map();
      let cursor = 0;
      for (const match of text.matchAll(/\[(E\d+)\]/g)) {
        if (match.index > cursor) tokens.push({ type: 'text', text: text.slice(cursor, match.index), start: cursor, end: match.index });
        const id = match[1];
        if (!seen.has(id)) { const citation = resolveCitation(id); seen.set(id, citation); citations.push(citation); }
        const citation = seen.get(id), markerStart = paragraphStart + match.index;
        const currentRegistered = assessment.status === 'current' && citation.status === 'registered';
        const savedRange = currentRegistered ? citationSentenceRange(content, markerStart, section.citationBindings, id) : null;
        const range = savedRange && savedRange.start >= paragraphStart && savedRange.end <= paragraphEnd ? { start: savedRange.start - paragraphStart, end: savedRange.end - paragraphStart } : null;
        const pendingRange = currentRegistered ? exactCitationBindingRange(content, markerStart, section.citationBindings, id, 'unreviewed') : null;
        const pendingText = pendingRange && pendingRange.start >= paragraphStart && pendingRange.end <= paragraphEnd ? content.slice(pendingRange.start, pendingRange.end) : null;
        const bindingStatus = assessment.status === 'current' ? range ? 'supported' : pendingText ? 'unreviewed' : assessment.items.some(item => item?.markerStart === markerStart) ? 'invalid' : 'unmatched' : assessment.status;
        tokens.push({ type: 'citation', id, text: match[0], status: citation.status, number: citation.number,
          start: match.index, end: match.index + match[0].length, sectionStart: markerStart, range, bindingStatus, bindingText: range ? text.slice(range.start, range.end) : pendingText });
        cursor = match.index + match[0].length;
      }
      if (cursor < text.length) tokens.push({ type: 'text', text: text.slice(cursor), start: cursor, end: text.length });
      for (const citation of citations) {
        const occurrences = tokens.filter(token => token.type === 'citation' && token.id === citation.id);
        citation.bindingStatus = occurrences.every(token => token.bindingStatus === 'supported') ? 'supported' : occurrences.find(token => token.bindingStatus !== 'supported')?.bindingStatus;
      }
      const diagnosticGroups = new Map();
      for (const item of assessment.items.filter(item => ['partial', 'unsupported', 'uncertain', 'unreviewed'].includes(item?.status) && !Number.isInteger(item.markerStart) && validBodyRange(content, item) && item.start >= paragraphStart && item.end <= paragraphEnd)) {
        // Extended prose may have no source support. Keep the saved assessment
        // unchanged; only actual citation findings need a preview diagnostic.
        if (retainUncitedProse && !item.evidenceId && item.status !== 'unreviewed') continue;
        const key = JSON.stringify([item.start, item.end, item.status, item.reason]);
        if (!diagnosticGroups.has(key)) diagnosticGroups.set(key, { ...item, evidenceIds: [] });
        const grouped = diagnosticGroups.get(key);
        if (item.evidenceId && !grouped.evidenceIds.includes(item.evidenceId)) grouped.evidenceIds.push(item.evidenceId);
      }
      const diagnostics = [...diagnosticGroups.values()];
      const hasModelSupplement = text.includes('【模型补充');
      const hasPendingVerification = text.includes('待核实');
      const paragraphAssessments = assessment.status === 'current' ? assessment.items.filter(item => validBodyRange(content, item) && item.start >= paragraphStart && item.end <= paragraphEnd) : [];
      const noCitationRequired = paragraphAssessments.length > 0 && paragraphAssessments.every(item => item.status === 'not_required' && !item.evidenceId && !Number.isInteger(item.markerStart));
      const missingEvidence = !(retainUncitedProse && citations.length === 0) && !noCitationRequired && !citations.some(citation => citation.status === 'registered');
      return { id: `${section.id || `section-${sectionIndex}`}:paragraph-${index}`, index, text, start: paragraphStart, end: paragraphEnd, tokens, citations, diagnostics, hasModelSupplement, missingEvidence,
        unplacedCitations: tokens.filter(token => token.type === 'citation' && token.bindingStatus !== 'supported').length,
        needsAttention: (!!section.manual && assessment.status !== 'current') || hasModelSupplement || hasPendingVerification || missingEvidence || diagnostics.length > 0 || tokens.some(token => token.type === 'citation' && (token.status !== 'registered' || token.bindingStatus !== 'supported')) };
    });
    const introductionParagraphCount = sectionIndex === 0 && Number.isInteger(section.introductionParagraphCount)
      && section.introductionParagraphCount > 0 && section.introductionParagraphCount < paragraphs.length
      ? section.introductionParagraphCount : 0;
    return { id: section.id || `section-${sectionIndex}`, title: section.title || `第 ${sectionIndex + 1} 章`, content, bindingStatus: assessment.status,
      introductionParagraphCount,
      mode,
      locked: !!section.locked, manual: !!section.manual, paragraphs, empty: !paragraphs.length };
  });
  return { title: project.title || project.topic || '未命名文稿', sections, paragraphCount: sections.reduce((sum, section) => sum + section.paragraphs.length, 0) };
}
