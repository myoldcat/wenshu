import test from 'node:test';
import assert from 'node:assert/strict';
import { usesDocumentWriting, hasChapterConfirmations, chapterWritingConfirmed } from '../src/writing-workflow.js';

const fixture = () => ({ id: 'writing_short', automation: 'manual', workflow: 'chapter', targetWords: 6000,
  topic: '主题', title: '标题', articleTitle: '标题', mode: 'extended', sourceIds: [], sources: [],
  materialSelection: { passages: [] }, chapterConfirmationPolicy: 'chapter-confirmation-v1',
  sections: [{ id: 'section_first', title: '第一章', goal: '', words: 3000, mode: 'inherit', content: '已经保存的第一章。', evidence: [], chapterConfirmed: true },
    { id: 'section_second', title: '第二章', goal: '', words: 3000, mode: 'inherit', content: '已经保存的第二章。', evidence: [], chapterConfirmed: true }] });

test('interactive writing uses one whole-document action strictly below 10000 words', () => {
  for (const targetWords of [100, 6000, 9999, '9999']) assert.equal(usesDocumentWriting({ automation: 'manual', targetWords }), true);
  for (const targetWords of [10000, 10001, 20000, 0, -1, 'invalid']) assert.equal(usesDocumentWriting({ automation: 'manual', targetWords }), false);
  assert.equal(usesDocumentWriting({ brief: { automation: 'manual', targetWords: 9999 } }), true);
  assert.equal(usesDocumentWriting({ brief: { automation: 'manual', targetWords: 9999 }, targetWords: 10000 }), false);
});

test('automatic and historical explicit workflows retain their meaning without mutation', () => {
  const project = { automation: 'automatic', targetWords: 6000, workflow: 'chapter' }, before = structuredClone(project);
  assert.equal(usesDocumentWriting(project), false);
  assert.deepEqual(project, before);
  assert.equal(usesDocumentWriting({ automation: 'manual', targetWords: 20000, workflow: 'oneshot' }), true);
});

test('an explicit short interactive chapter choice wins over both the default and old oneshot intent', () => {
  for (const workflow of ['chapter', 'oneshot']) {
    const project = { automation: 'manual', targetWords: 9999, workflow, interactiveGeneration: 'chapter' };
    const before = structuredClone(project);
    assert.equal(usesDocumentWriting(project), false);
    assert.deepEqual(project, before);
    assert.equal(usesDocumentWriting({ ...project, interactiveGeneration: 'single' }), true);
  }
});

test('short interactive choices round-trip in a raw brief and top-level edits take precedence', () => {
  const project = { brief: { automation: 'manual', targetWords: 6000, workflow: 'chapter', interactiveGeneration: 'chapter' } };
  assert.equal(usesDocumentWriting(project), false);
  assert.equal(usesDocumentWriting({ ...project, interactiveGeneration: 'single' }), true);
  assert.equal(project.brief.interactiveGeneration, 'chapter');
});

test('10000 is outside the choice range and returning below it restores the saved choice', () => {
  const project = { automation: 'manual', workflow: 'chapter', targetWords: 9999, interactiveGeneration: 'single' };
  assert.equal(usesDocumentWriting(project), true);
  project.targetWords = 10000;
  assert.equal(usesDocumentWriting(project), false);
  assert.equal(project.interactiveGeneration, 'single');
  project.targetWords = 9999; project.interactiveGeneration = 'chapter';
  assert.equal(usesDocumentWriting(project), false);
  project.targetWords = 10000; project.workflow = 'oneshot';
  assert.equal(usesDocumentWriting(project), true, 'Historical explicit long whole-document workflows remain valid');
});

test('the interactive preference does not redirect an automatic task through a manual body action', () => {
  for (const interactiveGeneration of ['chapter', 'single']) {
    assert.equal(usesDocumentWriting({ automation: 'automatic', workflow: 'chapter', targetWords: 6000, interactiveGeneration }), false);
  }
});

test('chapter confirmation requires the new server policy and saved confirmation', () => {
  const saved = fixture();
  assert.equal(hasChapterConfirmations(saved), true);
  assert.equal(chapterWritingConfirmed(saved, saved, 'section_first'), true);
  assert.equal(chapterWritingConfirmed(saved, null, 'section_first'), false);
  assert.equal(chapterWritingConfirmed({ ...saved, id: 'other' }, saved, 'section_first'), false);
  assert.equal(chapterWritingConfirmed({ ...saved, chapterConfirmationPolicy: null }, saved, 'section_first'), false);
  const unconfirmed = structuredClone(saved); unconfirmed.sections[0].chapterConfirmed = false;
  assert.equal(chapterWritingConfirmed(unconfirmed, unconfirmed, 'section_first'), false);
});

test('only the changed chapter loses its displayed saved confirmation', () => {
  const saved = fixture(), edited = structuredClone(saved);
  edited.sections[0].content += '正在修改。';
  assert.equal(chapterWritingConfirmed(edited, saved, 'section_first'), false);
  assert.equal(chapterWritingConfirmed(edited, saved, 'section_second'), true);
  edited.sections[0].content = saved.sections[0].content;
  assert.equal(chapterWritingConfirmed(edited, saved, 'section_first'), true);
});

test('chapter scope and writing scope edits hide confirmation until explicitly saved and confirmed', () => {
  const saved = fixture();
  for (const patch of [p => p.sections[0].title += '改', p => p.sections[0].goal = '新增目标',
    p => p.sections[0].words = 2000, p => p.sections[0].evidence.push({ id: 'E1' }),
    p => p.targetWords = 9000, p => p.sourceIds.push('new-source'), p => p.mode = 'strict']) {
    const project = structuredClone(saved); patch(project);
    assert.equal(chapterWritingConfirmed(project, saved, 'section_first'), false);
  }
});

test('navigation, locking and edits to another chapter do not invalidate this confirmation', () => {
  const saved = fixture(), viewed = structuredClone(saved);
  viewed.activeSectionId = 'section_second'; viewed.version = 10; viewed.sections[0].locked = true;
  viewed.sections[1].title = '其他章节的新标题'; viewed.sections[1].content = '其他章节的修改。';
  assert.equal(chapterWritingConfirmed(viewed, saved, 'section_first'), true);
  assert.deepEqual(saved, fixture());
});
