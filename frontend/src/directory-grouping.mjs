import {buildCategoryTree} from './category-tree-model.mjs';
import {getDocumentTags, ARTICLE_TYPES, documentArticleType} from './document-tags.mjs';

export const DIRECTORY_GROUPS = [
  {id: 'topic', label: '主题', title: '分类目录', noun: '分类', search: '查找分类或文档', footer: ['按一级、二级、三级主题归档', '目录自动精简，数量按文档去重']},
  {id: 'tag', label: '标签', title: '标签筛选', noun: '标签', search: '查找标签或文档', footer: ['按文章类型、语言筛选', '一篇文章可有多个标签，数量按文档去重']},
  {id: 'date', label: '日期', title: '日期目录', noun: '日期', search: '查找日期或文档', footer: ['优先按发表日期，未知时按导入日期归档', '年 / 月 / 日，较新的日期在前']},
];

export const directoryGrouping = mode => DIRECTORY_GROUPS.find(group => group.id === mode) || DIRECTORY_GROUPS[0];

const uniqueDocuments = documents => [...new Map(documents.filter(doc => doc?.id != null).map(doc => [String(doc.id), doc])).values()];
const documentNode = doc => ({
  kind: 'document', id: String(doc.id), key: `doc:${doc.id}`, name: String(doc.title || '无标题文档'), document: doc,
  documents: [doc], documentIds: [String(doc.id)], count: 1,
});
const withCounts = (node, children) => {
  const documents = uniqueDocuments(children.flatMap(child => child.documents));
  return {...node, children, documents, documentIds: documents.map(doc => String(doc.id)), count: documents.length};
};
const groupNode = (kind, selection, name, pathLabel, extra = {}) => ({kind, id: selection, key: selection, selection, name, pathLabel, ...extra});

function directoryRoot(mode, documents, children) {
  const root = {kind: 'root', id: `directory:${mode}`, key: `directory:${mode}`, name: '全部文档', mode,
    children, documents, documentIds: documents.map(doc => String(doc.id)), count: documents.length,
    selectionIndex: Object.create(null)};
  const index = node => {
    if (node.selection) root.selectionIndex[node.selection] = node;
    for (const child of node.children || []) index(child);
  };
  children.forEach(index);
  return root;
}

export function buildSourceTree(input = []) {
  const documents = uniqueDocuments(input);
  const groups = new Map();
  for (const doc of documents) {
    const source = typeof doc.source === 'string' ? doc.source.trim() : '';
    const selection = source ? `source:site:${encodeURIComponent(source)}` : 'source:unknown';
    if (!groups.has(selection)) groups.set(selection, {selection, source, documents: []});
    groups.get(selection).documents.push(doc);
  }
  const children = [...groups.values()].sort((left, right) =>
    Number(!left.source) - Number(!right.source) || left.source.localeCompare(right.source, 'zh-CN') || left.selection.localeCompare(right.selection),
  ).map(group => {
    const name = group.source || '未知来源';
    return withCounts(groupNode('source', group.selection, name, name, {sourceValue: group.source}), group.documents.map(documentNode));
  });
  return directoryRoot('source', documents, children);
}

/** Typed IDs prevent a custom tag named “事实类” from claiming a factual article. */
export function directoryTagSelection(tag) {
  if (tag?.kind === 'articleType') return `tag:articleType:${tag.value || 'unknown'}`;
  return `tag:${tag?.kind || 'custom'}:${tag?.kind === 'classification' ? `${tag.level}:` : ''}${encodeURIComponent(tag?.label || '')}`;
}

export function buildTagTree(input = []) {
  const documents = uniqueDocuments(input);
  const kinds = [['articleType', '文章类型'], ['language', '语言']];
  const groups = new Map(kinds.map(([kind, name]) => [kind, {kind, name, tags: new Map()}]));
  for (const doc of documents) {
    const tags = getDocumentTags(doc).filter(tag => ['articleType', 'language'].includes(tag.kind));
    if (!documentArticleType(doc)) tags.unshift({kind: 'articleType', value: 'unknown', label: '未标注', level: null});
    for (const tag of tags) {
      const group = groups.get(tag.kind), selection = directoryTagSelection(tag);
      if (!group) continue;
      if (!group.tags.has(selection)) group.tags.set(selection, {selection, tag, documents: []});
      group.tags.get(selection).documents.push(doc);
    }
  }
  const typeOrder = [...ARTICLE_TYPES.map(type => type.value), 'unknown'];
  const children = [...groups.values()].filter(group => group.tags.size).map(group => {
    const tagNodes = [...group.tags.values()].sort((a, b) => group.kind === 'articleType'
      ? typeOrder.indexOf(a.tag.value) - typeOrder.indexOf(b.tag.value)
      : a.tag.label.localeCompare(b.tag.label, 'zh-CN') || (a.tag.level || 0) - (b.tag.level || 0)).map(entry => {
      const label = group.kind === 'language' ? entry.tag.label.replace(/^语言[：:]\s*/, '') : entry.tag.label;
      const pathLabel = `${group.name} / ${label}`;
      return withCounts(groupNode('tag', entry.selection, entry.tag.label, pathLabel,
        {tagKind: group.kind, tagLevel: entry.tag.level}), uniqueDocuments(entry.documents).map(documentNode));
    });
    return withCounts(groupNode('tagGroup', `tag:group:${group.kind}`, group.name, group.name, {tagKind: group.kind}), tagNodes);
  });
  return directoryRoot('tag', documents, children);
}

function calendarDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [year, month, day] = value.split('-').map(Number);
  if (!year || month < 1 || month > 12 || day < 1) return null;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= days[month - 1] ? value : null;
}

/** Read an explicit ISO calendar date, retaining the publisher's day and timezone. */
function metadataDate(value) {
  if (typeof value !== 'string') return null;
  const match = /^(\d{4}-\d{2}-\d{2})(?:[Tt ](?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)?)?$/.exec(value.trim());
  return match ? calendarDate(match[1]) : null;
}

/** Publication first; incomplete/invalid metadata falls back to the import day. */
export function documentDirectoryDate(doc) {
  return metadataDate(doc?.publishedAt) || calendarDate(doc?.date) || metadataDate(doc?.createdAt);
}

export function buildDateTree(input = []) {
  const documents = uniqueDocuments(input);
  const years = new Map();
  const unknown = [];
  for (const doc of documents) {
    const date = documentDirectoryDate(doc);
    if (!date) {unknown.push(doc); continue;}
    const [year, month] = date.split('-');
    if (!years.has(year)) years.set(year, new Map());
    const months = years.get(year);
    if (!months.has(month)) months.set(month, new Map());
    const days = months.get(month);
    if (!days.has(date)) days.set(date, []);
    days.get(date).push(doc);
  }
  const descending = map => [...map.keys()].sort((left, right) => right.localeCompare(left));
  const children = descending(years).map(year => {
    const months = years.get(year);
    const monthNodes = descending(months).map(month => {
      const monthLabel = `${year}年 / ${Number(month)}月`;
      const days = months.get(month);
      const dayNodes = descending(days).map(date => {
        const day = Number(date.slice(8));
        return withCounts(groupNode('date', `date:day:${date}`, `${day}日`, `${monthLabel} / ${day}日`, {dateGranularity: 'day', dateValue: date}), days.get(date).map(documentNode));
      });
      return withCounts(groupNode('date', `date:month:${year}-${month}`, `${Number(month)}月`, monthLabel, {dateGranularity: 'month', dateValue: `${year}-${month}`}), dayNodes);
    });
    return withCounts(groupNode('date', `date:year:${year}`, `${year}年`, `${year}年`, {dateGranularity: 'year', dateValue: year}), monthNodes);
  });
  if (unknown.length) children.push(withCounts(groupNode('date', 'date:unknown', '未知日期', '未知日期', {dateGranularity: 'unknown'}), unknown.map(documentNode)));
  return directoryRoot('date', documents, children);
}

export function buildDirectoryTree(documents = [], mode = 'topic') {
  if (mode === 'tag') return buildTagTree(documents);
  if (mode === 'source') return buildSourceTree(documents);
  if (mode === 'date') return buildDateTree(documents);
  return {...buildCategoryTree(documents), mode: 'topic'};
}
