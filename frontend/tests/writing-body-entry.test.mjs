import test from 'node:test';
import assert from 'node:assert/strict';
import { writingBodyEntry } from '../src/writing-body-entry.js';

const complete = () => ({ id: 'saved', version: 2, brief: { topic: '已保存主题' }, outlineConfirmed: true,
  workflowPendingSteps: [], materialSelection: { confirmed: true }, sections: [{ id: 'one', content: '保存正文' }, { id: 'two', content: '第二章正文' }] });
test('saved completed body opens a full preview, including legacy records', () => {
  const project = complete();
  assert.equal(writingBodyEntry({ project, savedProject: project }), 'preview');
  delete project.workflowPendingSteps;
  assert.equal(writingBodyEntry({ project }), 'preview');
});
test('empty or pending-confirmation chapters remain in the editor', () => {
  const project = complete(); project.workflowPendingSteps = ['body'];
  assert.equal(writingBodyEntry({ project }), 'editor');
  project.workflowPendingSteps = []; project.sections[1].content = '';
  assert.equal(writingBodyEntry({ project }), 'editor');
});
test('unsaved text cannot turn a saved incomplete body into preview completion', () => {
  const saved = complete(); saved.sections[1].content = '';
  const project = complete();
  assert.equal(writingBodyEntry({ project, savedProject: saved, dirty: true }), 'editor');
  assert.equal(writingBodyEntry({ project, dirty: true }), 'editor');
});
test('an active automatic body phase stays in editing; later phases can preview its saved full body', () => {
  const project = complete(); project.automatic = { status: 'running', stage: 'body' };
  assert.equal(writingBodyEntry({ project }), 'editor');
  project.automatic.stage = 'images';
  assert.equal(writingBodyEntry({ project }), 'preview');
  project.sections[1].content = '';
  assert.equal(writingBodyEntry({ project }), 'editor');
});
