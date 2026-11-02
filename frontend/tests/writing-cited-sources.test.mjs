import test from 'node:test';
import assert from 'node:assert/strict';
import { buildWritingPreview } from '../src/writing-preview.js';
import { writingCitedSources } from '../src/writing-cited-sources.js';

const source = (id, revisionId = `revision-${id}`) => ({ sourceId: id, revisionId, title: `资料${id}` });
const evidence = (id, sourceId, revisionId = `revision-${sourceId}`) => ({
  id, sourceId, revisionId, blockId: `block-${sourceId}`, title: `资料${sourceId}`,
  quote: '可定位的原文。', start: 0, end: 7,
});
function fixture() {
  return {
    title: '引用列表隔离样本', mode: 'strict', sourceIds: ['A', 'B', 'C', 'unused'],
    sources: ['A', 'B', 'C', 'unused'].map(id => source(id)),
    sections: [
      { id: 'first', title: '第一章', mode: 'inherit', content: '先引用B。[E2]再引用A。[E1]\n\n重复B。[E2]', evidence: [evidence('E1', 'A'), evidence('E2', 'B'), evidence('E9', 'unused')] },
      { id: 'second', title: '第二章', mode: 'inherit', content: '先引用C。[E1]再引用A。[E7]', evidence: [evidence('E1', 'C'), evidence('E7', 'A')] },
    ],
  };
}

test('chapter sources come only from actual markers and retain whole-manuscript numbers', () => {
  const preview = buildWritingPreview(fixture());
  assert.deepEqual(writingCitedSources(preview, 'first').map(item => [item.sourceId, item.number]), [['B', 1], ['A', 2]]);
  assert.deepEqual(writingCitedSources(preview, 'second').map(item => [item.sourceId, item.number]), [['A', 2], ['C', 3]]);
  assert.deepEqual(writingCitedSources(preview).map(item => [item.sourceId, item.number]), [['B', 1], ['A', 2], ['C', 3]]);
  assert.equal(writingCitedSources(preview, 'first')[0].citations.length, 1, 'Repeated markers do not duplicate article or excerpt entries');
  assert.equal(writingCitedSources(preview)[1].citations.length, 2, 'One article can retain chapter-specific evidence');
  assert.deepEqual(writingCitedSources(preview, 'missing'), []);
});

test('unused evidence, uncited selected articles and broken marker registrations never enter the list', () => {
  const input = fixture();
  input.sections = [{ id: 'empty', content: '没有引用的段落。', evidence: [evidence('E1', 'A')] }, {
    id: 'broken', content: '未知。[E99]重复标识。[E1]越界。[E3]定位无效。[E4]',
    evidence: [evidence('E1', 'A'), evidence('E1', 'B'), evidence('E3', 'outside'), { ...evidence('E4', 'C'), quote: '' }],
  }];
  assert.deepEqual(writingCitedSources(buildWritingPreview(input)), []);
});

test('unreviewed and unsupported semantic assessments keep actually registered source articles visible', () => {
  const input = fixture();
  input.sections[0].citationBindings = { version: 1, content: input.sections[0].content, mode: 'strict', items: [] };
  const preview = buildWritingPreview(input), first = preview.sections[0];
  assert.notEqual(first.paragraphs[0].citations[0].bindingStatus, 'supported');
  const entries = writingCitedSources(preview, 'first');
  assert.equal(entries.length, 2);
  assert.equal(entries[0].evidence.revisionId, 'revision-B');
  assert.equal(entries[0].semanticVerified, undefined);
});

test('valid archived chapter sources remain available and different source revisions remain distinct', () => {
  const input = fixture();
  input.sourceIds = ['A'];
  input.sources = [source('A', 'new-revision')];
  input.sections = [
    { id: 'archived', content: '旧章引用。[E1]', evidence: [evidence('E1', 'A', 'old-revision')], evidenceHash: 'saved-evidence', citationSourceScopeValid: true,
      citationSourceScope: { policy: 'preserved-chapter-evidence-scope-v1', sectionId: 'archived', evidenceHash: 'saved-evidence', sources: [source('A', 'old-revision')] } },
    { id: 'current', content: '新章引用。[E1]', evidence: [evidence('E1', 'A', 'new-revision')] },
  ];
  let preview = buildWritingPreview(input);
  assert.deepEqual(writingCitedSources(preview).map(item => [item.sourceId, item.revisionId, item.number]), [['A', 'old-revision', 1], ['A', 'new-revision', 2]]);
  assert.equal(writingCitedSources(preview, 'archived')[0].evidence.blockId, 'block-A');
  input.sections[0].citationSourceScopeValid = false;
  preview = buildWritingPreview(input);
  assert.deepEqual(writingCitedSources(preview, 'archived'), []);
});

test('collecting citation articles does not alter prose, evidence, selected materials or preview records', () => {
  const input = fixture(), preview = buildWritingPreview(input);
  const before = JSON.stringify({ input, preview });
  writingCitedSources(preview);
  writingCitedSources(preview, 'second');
  assert.equal(JSON.stringify({ input, preview }), before);
  assert.deepEqual(writingCitedSources(), []);
});
