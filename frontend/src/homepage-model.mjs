import { documentClassificationPaths } from './category-tree-model.mjs';
import { sortWritingProjects } from './writing-projects.js';

const DAY = 86400000;
const BEIJING_OFFSET = 8 * 3600000;
const timestamp = value => typeof value === 'string' && value.trim() && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
const unique = items => [...new Map(items.filter(item => item?.id).map(item => [String(item.id), item])).values()];
const newestFirst = (items, field) => [...items].sort((a, b) => (timestamp(b[field]) ?? -Infinity) - (timestamp(a[field]) ?? -Infinity)
  || String(a.id).localeCompare(String(b.id), 'en'));

export function recentDocuments(documents, limit = 12) {
  return newestFirst(unique(documents).filter(doc => !doc.deletedAt && timestamp(doc.createdAt) !== null), 'createdAt').slice(0, limit);
}

export function favoriteDocuments(documents, limit = 3) {
  return newestFirst(unique(documents).filter(doc => doc.favorite && !doc.deletedAt), 'createdAt').slice(0, limit);
}

export function continuingProjects(projects, currentProject, dirty = false, limit = 3) {
  const current = currentProject && !currentProject.deletedAt && (currentProject.id || dirty) ? currentProject : null;
  const saved = sortWritingProjects(unique(projects).filter(project => !project.deletedAt));
  return (current ? [current, ...saved.filter(project => project.id !== current.id)] : saved).slice(0, limit);
}

export function beijingDay(value) {
  const time = typeof value === 'number' ? value : timestamp(value);
  return time === null || !Number.isFinite(time) ? null : Math.floor((time + BEIJING_OFFSET) / DAY);
}

export function intakeSeries(documents, days = 14, now = Date.now()) {
  const end = beijingDay(now);
  const start = end - days + 1;
  const counts = Array(days).fill(0);
  for (const doc of unique(documents).filter(doc => !doc.deletedAt)) {
    // Publication dates, read dates and updates are not ingestion dates.
    const day = beijingDay(doc.createdAt);
    if (day !== null && day >= start && day <= end) counts[day - start] += 1;
  }
  return counts.map((count, index) => {
    const date = new Date((start + index) * DAY).toISOString().slice(0, 10);
    return { date, label: `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`, count };
  });
}

export function topicDistribution(documents) {
  const groups = new Map();
  let unclassified = 0;
  for (const doc of unique(documents).filter(doc => !doc.deletedAt)) {
    const topics = new Map(documentClassificationPaths(doc).map(path => [path.nodes[0].id, path.nodes[0]]));
    if (!topics.size) unclassified += 1;
    for (const [id, node] of topics) {
      const group = groups.get(id) || { id, name: node.name, documentIds: [] };
      group.documentIds.push(String(doc.id));
      groups.set(id, group);
    }
  }
  return {
    topics: [...groups.values()].map(group => ({ ...group, count: group.documentIds.length }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-CN')),
    unclassified,
  };
}

