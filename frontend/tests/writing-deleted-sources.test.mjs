import test from 'node:test';
import assert from 'node:assert/strict';
import { buildWritingPreview } from '../src/writing-preview.js';
import { writingCitedSources } from '../src/writing-cited-sources.js';
import { writingFigureForView, writingSourceNotice } from '../src/writing-library-availability.js';
import { writingEditableFingerprint } from '../src/writing-semantic.js';

const source = { sourceId: 'deleted-doc', revisionId: 'pinned-version' };
const input = () => ({ id: 'manuscript', version: 3, title: '保留的文稿', mode: 'strict',
  sources: [{ ...source, title: '原资料' }], sourceIds: [source.sourceId],
  sections: [{ id: 'chapter', title: '章节', mode: 'inherit', content: '保存的正文。[E1]',
    evidence: [{ ...source, id: 'E1', blockId: 'old-block', title: '原资料', quote: '已保存摘录', start: 0, end: 5 }] }],
});

test('deleted sources retain body, excerpt, citation number and reference entry', () => {
  const project = input(), before = JSON.stringify(project);
  const original = buildWritingPreview(project).sections[0].paragraphs[0].citations[0];
  project.libraryAvailability = { sources: [{ ...source, status: 'deleted' }] };
  const preview = buildWritingPreview(project), citation = preview.sections[0].paragraphs[0].citations[0];
  assert.equal(citation.number, original.number);
  assert.equal(citation.status, 'registered');
  assert.equal(citation.sourceStatus, 'deleted');
  assert.equal(citation.evidence.quote, '已保存摘录');
  assert.equal(preview.sections[0].content, '保存的正文。[E1]');
  assert.equal(writingCitedSources(preview)[0].sourceStatus, 'deleted');
  const { libraryAvailability, ...unchanged } = project;
  assert.equal(JSON.stringify(unchanged), before);
});

test('availability cannot mark a different fixed version as deleted or make the draft dirty', () => {
  const project = input(), before = writingEditableFingerprint(project);
  project.libraryAvailability = { sources: [{ ...source, revisionId: 'other-version', status: 'deleted' }] };
  assert.equal(buildWritingPreview(project).sections[0].paragraphs[0].citations[0].sourceStatus, 'available');
  assert.equal(writingEditableFingerprint(project), before);
  assert.equal(writingSourceNotice('available'), '');
});

test('deleted image source is an explicit unavailable picture without modifying its anchor or approval', () => {
  const figure = { ...source, id: 'figure', assetId: 'old-asset', url: '/api/assets/old-asset',
    caption: '原有图注', anchor: { paragraphIndex: 0 }, scopeHash: 'saved-approval' };
  const before = JSON.stringify(figure);
  const project = { libraryAvailability: { sources: [{ ...source, status: 'deleted' }],
    assets: [{ ...source, assetId: 'old-asset', status: 'deleted' }] } };
  const view = writingFigureForView(project, figure);
  assert.equal(view.assetStatus, 'deleted');
  assert.match(view.unavailableMessage, /来源文档已删除/);
  assert.equal(view.caption, figure.caption);
  assert.deepEqual(view.anchor, figure.anchor);
  assert.equal(view.scopeHash, figure.scopeHash);
  assert.equal(JSON.stringify(figure), before);
});
