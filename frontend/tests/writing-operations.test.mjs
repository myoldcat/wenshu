import test from 'node:test';
import assert from 'node:assert/strict';
import { pagedEndpoint, projectState, listPage, mergePage, readableValue, serializedRecord, operationBodyResult, operationKind, operationStatus, timeLabel, durationLabel } from '../src/writing-operations.js';

test('state-filtered pagination appends parameters without discarding the filter', () => {
  assert.equal(pagedEndpoint('/operations/writing/projects?state=deleted', 30, 60), '/operations/writing/projects?state=deleted&limit=30&offset=60');
  assert.equal(pagedEndpoint('/operations/writing/runs/a/calls', 30, 0), '/operations/writing/runs/a/calls?limit=30&offset=0');
  assert.equal(projectState('deleted'), 'deleted');
  assert.equal(projectState('unexpected'), 'all');
});

test('pagination preserves server total, offset and continuation flag', () => {
  assert.deepEqual(listPage({ items: [{ id: 'x' }], offset: 30, total: 67, hasMore: true }), { items: [{ id: 'x' }], offset: 30, total: 67, hasMore: true });
  assert.deepEqual(listPage(null, 30), { items: [], offset: 30, total: 30, hasMore: false });
});

test('overlapping pages replace matching rows without duplicating or reordering', () => {
  const before = [{ id: 'first', title: 'old' }, { id: 'second' }];
  assert.deepEqual(mergePage(before, [{ id: 'first', title: 'new' }, { id: 'third' }, { id: 'third', title: 'latest' }]), [{ id: 'first', title: 'new' }, { id: 'second' }, { id: 'third', title: 'latest' }]);
  assert.equal(before[0].title, 'old');
});

test('structured reading keeps exact original whitespace, escaping and long content', () => {
  const longText = `开头\r\n${'正文 A🙂<script>\\n'.repeat(10000)}\r\n结束_SENTINEL`;
  const prose = readableValue(longText);
  assert.equal(prose.raw, longText);
  assert.equal(prose.structured, null);
  const raw = '  { "内容": ["一", {"quote":"引文\\n第二行"}] }\n';
  const parsed = readableValue(raw);
  assert.equal(parsed.raw, raw);
  assert.deepEqual(parsed.structured, { 内容: ['一', { quote: '引文\n第二行' }] });
  assert.equal(readableValue('123').structured, null);
});

test('full record serialization round trips every prompt and response character', () => {
  const record = { request: { messages: [{ role: 'user', content: 'x'.repeat(200000) + '\nEND' }] }, response: { raw: ' {"result":"原始"}\n', text: '<strong>保持文本</strong>' } };
  assert.deepEqual(JSON.parse(serializedRecord(record)), record);
});

test('writing kinds and missing timing values remain truthful', () => {
  assert.equal(operationKind('materials'), '材料推荐');
  assert.equal(timeLabel(undefined), '未记录');
  assert.equal(durationLabel(null), '未记录');
  assert.equal(durationLabel(312), '0.312 秒');
  assert.match(timeLabel('2026-10-23T01:02:03Z'), /2026\/10\/23 09:02:03/);
});

test('execution, HTTP return, response validation and manuscript outcome stay independent', () => {
  const run = { kind: 'automatic', status: 'succeeded', automatic: { status: 'completed_with_issues', report: { outputAvailable: false, summary: { generatedSections: 0, preservedSections: 0, skippedSections: 0 } } } };
  assert.equal(operationStatus(run.status, 'execution'), '执行已结束');
  assert.equal(operationStatus('succeeded', 'transport'), '已收到返回');
  assert.equal(operationStatus('invalid'), '校验未通过');
  assert.equal(operationBodyResult(run), '未生成正文');
  run.automatic.report.outputAvailable = true;
  run.automatic.report.summary.generatedSections = 1;
  run.automatic.report.summary.skippedSections = 1;
  assert.equal(operationBodyResult(run), '正文部分完成');
  run.automatic.status = 'running';
  assert.equal(operationBodyResult(run), '');
  assert.equal(operationBodyResult({ kind: 'section', status: 'succeeded' }), '');
});
