import test from 'node:test';
import assert from 'node:assert/strict';
import { beijingDay, continuingProjects, favoriteDocuments, intakeSeries, recentDocuments, topicDistribution } from '../src/homepage-model.mjs';

const path = (id, name = id, suffix = 'one') => ({ nodes: [{ id, name }, { id: `${id}-${suffix}-2`, name: '二级' }, { id: `${id}-${suffix}-3`, name: '三级' }] });

test('recently added and favorites never borrow publication or read dates', () => {
  const docs = [{ id: 'a', createdAt: '2026-10-20', date: '2026-10-24', favorite: true }, { id: 'b', createdAt: '2026-10-23', favorite: false }, { id: 'c', date: '2026-10-24', favorite: true }];
  assert.deepEqual(recentDocuments(docs).map(doc => doc.id), ['b', 'a']);
  assert.deepEqual(favoriteDocuments(docs).map(doc => doc.id), ['a', 'c']);
});

test('intake calendar uses Beijing day boundaries, deduplicates sources and excludes future/missing dates', () => {
  const docs = [
    { id: 'before', createdAt: '2026-10-22T15:59:59Z' },
    { id: 'edge', createdAt: '2026-10-22T16:00:00Z' },
    { id: 'edge', createdAt: '2026-10-22T16:00:00Z' },
    { id: 'today', createdAt: '2026-10-23T16:00:00Z' },
    { id: 'future', createdAt: '2026-10-24T16:00:00Z' },
    { id: 'publication-only', date: '2026-10-24' },
  ];
  assert.deepEqual(intakeSeries(docs, 2, Date.parse('2026-10-24T01:00:00Z')), [
    { date: '2026-10-23', label: '10/23', count: 1 },
    { date: '2026-10-24', label: '10/24', count: 1 },
  ]);
  assert.equal(beijingDay('n/a'), null);
});

test('topic counts deduplicate repeated first-level paths and never infer a hierarchy from flat tags', () => {
  const { topics, unclassified } = topicDistribution([
    { id: 'a', classificationPaths: [path('ai', '人工智能'), path('ai', '人工智能', 'two'), path('law', '法律')] },
    { id: 'b', classificationPaths: [path('ai', '人工智能')] },
    { id: 'c', tags: ['人工智能'] },
    { id: 'bad', classificationPaths: [{ nodes: [{ id: 'ai', name: '人工智能' }] }] },
  ]);
  assert.deepEqual(topics.map(topic => [topic.id, topic.count]), [['ai', 2], ['law', 1]]);
  assert.equal(unclassified, 2);
});

test('continue current unsaved work without treating it as another saved manuscript or restoring deleted projects', () => {
  const saved = [{ id: 'old', updatedAt: '2026-10-21' }, { id: 'new', updatedAt: '2026-10-24' }, { id: 'deleted', deletedAt: '2026-10-24' }];
  const draft = { id: null, title: '仍在编辑' };
  assert.deepEqual(continuingProjects(saved, draft, true).map(project => project.id), [null, 'new', 'old']);
  assert.deepEqual(continuingProjects(saved, draft, false).map(project => project.id), ['new', 'old']);
  assert.deepEqual(continuingProjects(saved, { id: 'old' }).map(project => project.id), ['old', 'new']);
});

