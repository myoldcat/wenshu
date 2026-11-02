import test from 'node:test';
import assert from 'node:assert/strict';
import { writingMessage, materialNotice } from '../src/writing-messages.js';

test('writing failures do not expose model protocol or internal source identifiers', () => {
  const failures = [
    '模型返回不是有效 JSON：Unexpected token at line 3',
    '选区必须明确使用 UTF-16 偏移',
    '模型引文无法在冻结原文中逐字定位，第 2 章（ID：private-section）：blockId=private-block',
    '章节版本或正文校验值已变化，这份修改建议已过期。',
  ];
  for (const failure of failures) {
    const message = writingMessage(failure);
    assert.doesNotMatch(message, /JSON|UTF-16|blockId|private-|校验值|冻结/);
    assert.match(message, /重试|重新|核对/);
  }
  assert.equal(writingMessage({ message: { debug: true } }, '请重试。'), '请重试。');
});

test('actionable writing protection messages remain visible', () => {
  for (const message of ['当前有未保存修改，请先保存。', '章节已锁定，不能采纳修改。', '原选区文字已变化，请重新选择并生成修改建议。']) {
    assert.equal(writingMessage(new Error(message)), message);
  }
  assert.match(writingMessage('“研究结论”目标 5000 字超出单章输出预算，请提高模型最大输出 Token'), /研究结论.*减少目标字数/);
});

test('recommendation limits explain incomplete coverage without execution details', () => {
  for (const notice of ['向量模型的输出维度发生变化，已排除并清理不兼容缓存', 'Wiki 检索已按本次预算限制为最新 500 个词条。', '关键词索引读取失败，请检查资料索引后重试。']) {
    const message = materialNotice(notice);
    assert.doesNotMatch(message, /向量|维度|缓存|Wiki|预算|索引/);
    assert.match(message, /部分|补充|重新/);
  }
});
