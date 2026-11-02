import { writingMessage } from './writing-messages.js';

const FALLBACK = '本次生成有部分内容建议核对，可继续阅读和使用正文。';
const CITATION_NOTICE = '部分引用未能对应所选原文，可在参考证据中核对。';

// These are author-facing notes. They never change prose or adoption eligibility.
export function generationNotices(...values) {
  const value = values.flatMap(value => Array.isArray(value) ? value.slice(0, 100) : []);
  const notices = new Set();
  for (const item of value.slice(0, 100)) {
    if (typeof item !== 'string') continue;
    const text = item.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const message = writingMessage(text, FALLBACK);
    const safe = message.startsWith('生成内容中的引用无法与所选原文对应') ? CITATION_NOTICE : message;
    const points = Array.from(safe);
    notices.add(points.length > 400 ? `${points.slice(0, 399).join('')}…` : safe);
    if (notices.size >= 8) break;
  }
  return [...notices];
}
