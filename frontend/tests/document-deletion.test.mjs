import assert from 'node:assert/strict';
import test from 'node:test';
import { deletionConfirmation, referenceDescription } from '../src/document-deletion.mjs';

const impact = { sourceId: 'source-1', revisionId: 'revision-2', impactToken: 'current-only', canDelete: true, hasReferences: false };
test('deletion needs a current complete reviewed impact and no active work', () => {
  assert.equal(deletionConfirmation(null, true), null);
  for (const key of ['sourceId', 'revisionId', 'impactToken']) assert.equal(deletionConfirmation({ ...impact, [key]: '' }, true), null);
  for (const canDelete of [false, null, undefined]) assert.equal(deletionConfirmation({ ...impact, canDelete }, true), null);
  assert.deepEqual(deletionConfirmation(impact, false), { expectedRevisionId: 'revision-2', impactToken: 'current-only', confirmReferences: false });
});
test('all reference types require explicit consent and submission uses reviewed revision/token', () => {
  const cited = { ...impact, hasReferences: true };
  for (const consent of [false, undefined, 'true', 1]) assert.equal(deletionConfirmation(cited, consent), null);
  assert.deepEqual(deletionConfirmation(cited, true), { expectedRevisionId: 'revision-2', impactToken: 'current-only', confirmReferences: true });
  assert.deepEqual(deletionConfirmation({ ...cited, revisionId: 'new', impactToken: 'new-token' }, true), { expectedRevisionId: 'new', impactToken: 'new-token', confirmReferences: true });
});
test('text/image citations and selection are separately visible without inventing counts', () => {
  assert.equal(referenceDescription({ textReferences: 2, imageReferences: 3, selectedSource: true }), '文字引用 2 处 · 图片引用 3 处 · 已选为写作资料');
  assert.equal(referenceDescription({ imageReferences: 1 }), '图片引用 1 处');
  assert.equal(referenceDescription({ textReferences: 0, imageReferences: 0, selectedSource: true }), '已选为写作资料');
  assert.equal(referenceDescription({}), '已关联此资料');
});
