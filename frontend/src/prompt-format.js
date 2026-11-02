import { getPromptParagraphs } from './prompt-paragraphs.js';

const SPACE = /\s/u;
const QUOTES = new Map([["\"", "\""], ["'", "'"], ['“', '”'], ['‘', '’'], ['「', '」'], ['『', '』']]);

function skipSpace(text, from) {
  let end = from;
  while (end < text.length && SPACE.test(text[end])) end += 1;
  return end;
}

function quotedEnd(text, start, strict = false) {
  const opening = text[start];
  const closing = QUOTES.get(opening);
  let depth = 1;
  for (let index = start + 1; index < text.length; index += 1) {
    const char = text[index];
    if (char === '\\') {
      index += 1;
      continue;
    }
    if (strict && char.charCodeAt(0) < 32) return null;
    if (opening !== closing && char === opening) depth += 1;
    if (char === closing) {
      depth -= 1;
      if (depth === 0) return index + 1;
    }
  }
  return strict ? null : text.length;
}

function variableEnd(text, start) {
  const closing = text.indexOf('}}', start + 2);
  return closing < 0 ? text.length : closing + 2;
}

// Recognize JSON lexically: never parse/stringify values or lose duplicate keys.
function jsonStructure(text, start) {
  const punctuation = [];
  let maxDepth = 0;
  function value(from, depth) {
    if (depth > 100) return null;
    const index = skipSpace(text, from);
    const char = text[index];
    if (char === '"') return quotedEnd(text, index, true);
    if (text.startsWith('{{', index)) {
      const end = variableEnd(text, index);
      return text.slice(index, end).endsWith('}}') ? end : null;
    }
    if (char === '{' || char === '[') return container(index, depth + 1);
    const token = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/u.exec(text.slice(index));
    return token ? index + token[0].length : null;
  }
  function container(from, depth) {
    maxDepth = Math.max(maxDepth, depth);
    const object = text[from] === '{';
    const close = object ? '}' : ']';
    punctuation.push({ index: from, char: text[from], depth });
    let index = skipSpace(text, from + 1);
    if (text[index] === close) {
      punctuation.push({ index, char: close, depth, empty: true });
      return index + 1;
    }
    while (index < text.length) {
      if (object) {
        if (text[index] !== '"') return null;
        index = quotedEnd(text, index, true);
        if (index === null) return null;
        index = skipSpace(text, index);
        if (text[index] !== ':') return null;
        punctuation.push({ index, char: ':', depth });
        index += 1;
      }
      index = value(index, depth);
      if (index === null) return null;
      index = skipSpace(text, index);
      if (text[index] === close) {
        punctuation.push({ index, char: close, depth });
        return index + 1;
      }
      if (text[index] !== ',') return null;
      punctuation.push({ index, char: ',', depth });
      index = skipSpace(text, index + 1);
    }
    return null;
  }
  const end = container(start, 1);
  return end === null ? null : { end, punctuation, maxDepth };
}

function balancedEnd(text, start) {
  const stack = [];
  for (let index = start; index < text.length; index += 1) {
    const char = text[index];
    if (text.startsWith('{{', index)) {
      index = variableEnd(text, index) - 1;
    } else if (QUOTES.has(char)) {
      index = quotedEnd(text, index) - 1;
    } else if (char === '{' || char === '[') {
      stack.push(char === '{' ? '}' : ']');
    } else if (char === '}' || char === ']') {
      if (stack.pop() !== char) return null;
      if (!stack.length) return index + 1;
    }
  }
  return null;
}

/**
 * Add curated semantic paragraphs without changing existing characters or whitespace.
 * Unknown/custom instructions retain their prose layout. Quotes, variables and code
 * are opaque; JSON is indented lexically without touching its quoted values.
 */
export function formatPromptText(text, id = '') {
  if (typeof text !== 'string' || !text) return text;
  const newline = text.match(/\r\n|\n|\r/u)?.[0] || '\n';
  const additions = new Map();
  const protectedRanges = [];
  function add(index, whitespace) {
    additions.set(index, (additions.get(index) || '') + whitespace);
  }
  function lineBreak(from, depth) {
    const next = skipSpace(text, from);
    const gap = text.slice(from, next);
    if (!/[\r\n]/u.test(gap)) {
      add(from, newline + ' '.repeat(depth * 2));
    } else {
      const indent = gap.split(/\r\n|\r|\n/u).at(-1);
      if (!indent.includes('\t') && indent.length < depth * 2) {
        add(next, ' '.repeat(depth * 2 - indent.length));
      }
    }
  }
  function formatJson(structure) {
    for (const { index, char, depth, empty } of structure.punctuation) {
      if (char === ':') {
        if (!SPACE.test(text[index + 1] || '')) add(index + 1, ' ');
      } else if (char === ',') {
        lineBreak(index + 1, depth);
      } else if (char === '{' || char === '[') {
        const next = skipSpace(text, index + 1);
        if (text[next] !== (char === '{' ? '}' : ']')) lineBreak(index + 1, depth);
      } else if (!empty) {
        let previous = index - 1;
        while (previous >= 0 && SPACE.test(text[previous])) previous -= 1;
        lineBreak(previous + 1, depth - 1);
      }
    }
  }

  for (let index = 0; index < text.length;) {
    const char = text[index];
    if (text.startsWith('{{', index)) {
      const end = variableEnd(text, index);
      protectedRanges.push([index, end]);
      index = end;
      continue;
    }
    if (char === '`' || text.startsWith('~~~', index)) {
      let end = index + 1;
      while (text[end] === char) end += 1;
      const delimiter = text.slice(index, end);
      const close = text.indexOf(delimiter, end);
      const codeEnd = close < 0 ? text.length : close + delimiter.length;
      protectedRanges.push([index, codeEnd]);
      index = codeEnd;
      continue;
    }
    // Apostrophes within words (don't, user's) are ordinary text.
    if (QUOTES.has(char) && !(char === "'" && /[\p{L}\p{N}]/u.test(text[index - 1] || '') && /[\p{L}\p{N}]/u.test(text[index + 1] || ''))) {
      const end = quotedEnd(text, index);
      protectedRanges.push([index, end]);
      index = end;
      continue;
    }
    const nextContent = text[skipSpace(text, index + 1)];
    const jsonCandidate = (char === '{' && (nextContent === '"' || nextContent === '}'))
      || (char === '[' && /["{\[\d\]tfn-]/u.test(nextContent || ''));
    if (jsonCandidate) {
      const structure = jsonStructure(text, index);
      const end = structure?.end || balancedEnd(text, index);
      if (end) {
        protectedRanges.push([index, end]);
        if (structure && (end - index >= 100 || structure.maxDepth >= 2)) formatJson(structure);
        index = end;
        continue;
      }
    }
    index += 1;
  }

  const paragraphs = getPromptParagraphs(text, id);
  if (Array.isArray(paragraphs) && paragraphs.length > 1 && paragraphs.every(part => typeof part === 'string')) {
    // Map each curated boundary back onto this exact version of the source. This
    // tolerates existing manual whitespace, but never permits reordered content.
    const normalizedParts = paragraphs.map(part => part.replace(/\s/gu, ''));
    const normalizedText = text.replace(/\s/gu, '');
    if (normalizedParts.every(Boolean) && normalizedParts.join('') === normalizedText) {
      const contentOffsets = [];
      for (let index = 0; index < text.length; index += 1) {
        if (!SPACE.test(text[index])) contentOffsets.push(index);
      }
      const boundaries = [];
      let consumed = 0;
      for (const part of normalizedParts.slice(0, -1)) {
        consumed += part.length;
        boundaries.push(contentOffsets[consumed]);
      }
      // A catalog prompt embedded in a quoted/code example must remain opaque,
      // even if the catalog's text recognizer happens to find matching anchors.
      if (boundaries.every(boundary => !protectedRanges.some(([start, end]) => boundary > start && boundary < end))) {
        for (const boundary of boundaries) {
          let gapStart = boundary;
          while (gapStart > 0 && SPACE.test(text[gapStart - 1])) gapStart -= 1;
          const existingLines = text.slice(gapStart, boundary).match(/\r\n|\r|\n/gu)?.length || 0;
          if (existingLines < 2) add(gapStart, newline.repeat(2 - existingLines));
        }
      }
    }
  }
  if (!additions.size) return text;
  let result = '';
  let previous = 0;
  for (const [index, whitespace] of [...additions].sort(([left], [right]) => left - right)) {
    result += text.slice(previous, index) + whitespace;
    previous = index;
  }
  return result + text.slice(previous);
}
