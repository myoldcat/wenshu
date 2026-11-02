import { formatPromptText } from './prompt-format.js';

export function displayPromptContent(item) {
  return typeof item?.displayContent === 'string' ? item.displayContent
    : typeof item?.effectiveContent === 'string' ? item.effectiveContent
      : typeof item?.content === 'string' ? item.content : '';
}

export function editablePromptContent(item) {
  return typeof item?.editableContent === 'string' ? item.editableContent : displayPromptContent(item);
}

export function isStructurePrompt(item) {
  return item?.category === 'structures' || item?.id?.startsWith('writing_structure_');
}

function comparisonText(text) {
  let result = '';
  const quotes = new Map([['"', '"'], ["'", "'"], ['“', '”'], ['‘', '’'], ['「', '」'], ['『', '』']]);
  for (let index = 0; index < text.length;) {
    const start = index;
    if (text.startsWith('{{', index)) {
      const close = text.indexOf('}}', index + 2);
      index = close < 0 ? text.length : close + 2;
    } else if (text[index] === '`' || text.startsWith('~~~', index)) {
      const char = text[index];
      while (text[index] === char) index += 1;
      const delimiter = text.slice(start, index);
      const close = text.indexOf(delimiter, index);
      index = close < 0 ? text.length : close + delimiter.length;
    } else if (quotes.has(text[index])) {
      const opening = text[index], closing = quotes.get(opening);
      let depth = 1; index += 1;
      for (; index < text.length; index += 1) {
        if (text[index] === '\\') { index += 1; continue; }
        if (opening !== closing && text[index] === opening) depth += 1;
        if (text[index] === closing && --depth === 0) { index += 1; break; }
      }
    } else {
      if (!/\s/u.test(text[index])) result += text[index];
      index += 1; continue;
    }
    result += text.slice(start, index);
  }
  return result;
}

export function currentPromptRules(item) {
  const main = comparisonText(displayPromptContent(item));
  const seen = new Set();
  return (Array.isArray(item?.supplementalRules) ? item.supplementalRules : []).flatMap(rule => {
    if (!rule || typeof rule.content !== 'string' || /^legacy(?:-|$)/.test(rule.scope || '') || /^legacy_/.test(rule.id || '') || /历史.*兼容/.test(rule.title || '')) return [];
    const formatted = formatPromptText(rule.content, rule.id);
    const paragraphs = formatted.split(/(?:\r?\n|\r)[\t ]*(?:\r?\n|\r)/u).filter(part => part.trim());
    const remaining = paragraphs.filter(part => {
      const signature = comparisonText(part.trim());
      if (!signature || main.includes(signature) || seen.has(signature)) return false;
      seen.add(signature); return true;
    });
    return remaining.length ? [{ ...rule, formatted: remaining.join('\n\n') }] : [];
  });
}

function editingContent(content, id, maxContentLength = 24000) {
  const formatted = formatPromptText(content, id);
  return formatted.length <= maxContentLength ? formatted : content;
}

export function createPromptDraft(item) {
  const content = editingContent(editablePromptContent(item), item.id, item.maxContentLength);
  return { id: item.id, content, originalContent: content, version: item.version, contentView: item.contentView || 'source' };
}

export function refreshCleanPromptDrafts(drafts, items, conflicts = {}) {
  const incoming = new Map(items.map(item => [item.id, item]));
  let next = drafts;
  for (const [id, draft] of Object.entries(drafts)) {
    const item = incoming.get(id);
    if (!item || item.version < draft.version || conflicts[id] || promptDraftChanged(draft)) continue;
    const fresh = createPromptDraft(item);
    if (fresh.content === draft.content && fresh.version === draft.version && fresh.contentView === draft.contentView) continue;
    if (next === drafts) next = { ...drafts };
    next[id] = fresh;
  }
  return next;
}

export function planPromptDraftAdoption(state, payload) {
  const { id, draft, conflict = null, error = '' } = payload || {};
  if (!id || !draft || draft.id !== id || state.savingId === id) return { accepted: false };
  const existing = state.drafts[id];
  const existingConflict = state.conflicts[id];
  if ((existing && existing !== draft && promptDraftChanged(existing)) || (existingConflict && existingConflict !== conflict)) return { accepted: false };
  return {
    accepted: true,
    state: {
      drafts: { ...state.drafts, [id]: draft },
      conflicts: { ...state.conflicts, [id]: conflict },
      errors: { ...state.errors, [id]: error },
    },
  };
}

// Never let a delayed catalog response undo an acknowledged prompt save.
export function mergePromptCatalog(current, incoming) {
  const known = new Map(current.map(item => [item.id, item]));
  return incoming.map(item => {
    const previous = known.get(item.id);
    return previous && previous.version > item.version ? previous : item;
  });
}

export function promptDraftChanged(draft) {
  return Boolean(draft && draft.content !== draft.originalContent);
}

export function applyPromptSave(drafts, submitted, saved) {
  const current = drafts[submitted.id];
  if (!current) return drafts;
  const next = { ...drafts };
  if (current === submitted) delete next[submitted.id];
  else next[submitted.id] = { ...current, originalContent: editingContent(editablePromptContent(saved), saved.id, saved.maxContentLength), version: saved.version, contentView: saved.contentView || 'source' };
  return next;
}

export function rebasePromptDraft(draft, latest) {
  if (!draft || draft.id !== latest.id) return draft;
  const latestContent = editablePromptContent(latest);
  const originalContent = draft.content === latestContent ? latestContent : editingContent(latestContent, latest.id, latest.maxContentLength);
  return { ...draft, originalContent, version: latest.version, contentView: latest.contentView || 'source' };
}
