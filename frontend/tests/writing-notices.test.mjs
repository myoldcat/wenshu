import test from 'node:test';
import assert from 'node:assert/strict';
import { generationNotices } from '../src/writing-notices.js';

test('ordinary generation notes remain separate readable messages without changing input', () => {
  const notes = ['部分表述可进一步精简。', ' 部分表述可进一步精简。 ', '', null, { message: 'unexpected object' }, '目标篇幅略有偏离。'];
  const snapshot = structuredClone(notes);
  assert.deepEqual(generationNotices(notes), ['部分表述可进一步精简。', '目标篇幅略有偏离。']);
  assert.deepEqual(notes, snapshot);
  for (const value of [undefined, null, '一段正文', {}]) assert.deepEqual(generationNotices(value), []);
  assert.deepEqual(generationNotices(['共同说明。'], ['纲要说明。', '共同说明。']), ['共同说明。', '纲要说明。']);
});

test('generation notes hide execution details and do not turn citation notes into refusal', () => {
  const messages = generationNotices([
    '模型引文无法在冻结原文中逐字定位：blockId=private-block',
    '模型返回不是有效 JSON：Unexpected token at line 3',
    'API key token=private-secret',
  ]);
  assert.equal(messages.length, 2);
  assert.match(messages[0], /引用.*核对/);
  for (const message of messages) {
    assert.doesNotMatch(message, /JSON|blockId|private-|token|冻结|重新生成|拒绝|待核实/);
  }
});

test('generation note lists stay bounded and retain Unicode characters', () => {
  const messages = generationNotices(['待调整\n的表述\u0000。', '😀'.repeat(500), ...Array.from({ length: 20 }, (_, i) => `提示 ${i}`)]);
  assert.equal(messages.length, 8);
  assert.equal(messages[0], '待调整 的表述 。');
  assert.equal(Array.from(messages[1]).length, 400);
  assert.ok(messages[1].endsWith('…'));
  assert.doesNotMatch(messages[1], /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/);
});
