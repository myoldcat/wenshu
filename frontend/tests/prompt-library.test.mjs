import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/local-api.js';
import { applyPromptSave, createPromptDraft, currentPromptRules, displayPromptContent, editablePromptContent, isStructurePrompt, mergePromptCatalog, promptDraftChanged, rebasePromptDraft, refreshCleanPromptDrafts } from '../src/prompt-library-state.js';

const prompt = (id, version, content) => ({ id, version, content });
const draft = (id, version, originalContent, content = originalContent) => ({ id, version, originalContent, content });

test('entering a paragraph-formatted edit is clean and preserves the server item', () => {
  const item = prompt('summary', 4, '你是助手。保持事实。\n\n任务：保留数字与 {{budget}}。输出 {"items":[{"text":"原文。引句。"}]}。');
  const original = item.content;
  const edit = createPromptDraft(item);
  assert.match(edit.content, /\n/);
  assert.ok(edit.content.includes('原文。引句。'));
  assert.ok(edit.content.includes('{{budget}}'));
  assert.equal(edit.content.replace(/\s/g, ''), original.replace(/\s/g, ''));
  assert.equal(promptDraftChanged(edit), false);
  assert.equal(item.content, original);
  assert.equal(edit.version, 4);
});

test('conflict reconciliation compares the same readable baseline without changing the local draft', () => {
  const latest = prompt('summary', 5, '保留原文与 {{budget}}。输出 {"items":[{"text":"原文。不要拆分。"}]}。');
  const same = createPromptDraft(latest);
  const local = { ...same, version: 2, originalContent: '旧基线' };
  const reconciled = rebasePromptDraft(local, latest);
  assert.equal(reconciled.content, local.content);
  assert.equal(reconciled.version, 5);
  assert.equal(promptDraftChanged(reconciled), false);
  const rawMatch = rebasePromptDraft({ ...local, content: latest.content }, latest);
  assert.equal(promptDraftChanged(rawMatch), false);
  assert.equal(rawMatch.content, latest.content);
  const changed = rebasePromptDraft({ ...local, content: local.content + '\n人工要求' }, latest);
  assert.equal(promptDraftChanged(changed), true);
  assert.ok(changed.content.endsWith('人工要求'));
});

test('readability formatting cannot make an existing instruction exceed the editor limit', () => {
  const raw = '输出 {"items":[' + Array(1400).fill('{"text":"要求"}').join(',') + ']}';
  assert.ok(raw.length <= 24000);
  const edit = createPromptDraft(prompt('summary', 1, raw));
  assert.equal(edit.content, raw);
  assert.equal(promptDraftChanged(edit), false);
});

test('per-entry capacity keeps the raw edit baseline when readable spacing would exceed it', () => {
  const raw = '\n上次结果未通过程序校验：{{validationError}}。请依据同一份原文重新生成完整JSON，严格遵守所有字段、数量、长度及引用要求；不要编造要点或引用。';
  const item = { id: 'knowledge_repair', version: 4, content: raw, maxContentLength: raw.length };
  const edit = createPromptDraft(item);
  assert.equal(edit.content, raw);
  assert.equal(promptDraftChanged(edit), false);
  const local = { ...edit, content: raw + '人工修改' };
  const rebased = rebasePromptDraft(local, { ...item, version: 5 });
  assert.equal(rebased.originalContent, raw);
  assert.equal(rebased.content, local.content);
  assert.equal(rebased.version, 5);
});

test('current display and editor fields take priority without mutating historical stored content', () => {
  const item = { id: 'writing_section_strict', version: 8, content: '作者保存的旧版原指令。', effectiveContent: '旧接口的当前基础指令。', displayContent: '当前展示指令。', editableContent: '当前可编辑指令。', contentView: 'effective', supplementalRules: [{ id: 'fixed', title: '固定规则', content: '不可编辑。' }] };
  const before = structuredClone(item);
  const edit = createPromptDraft(item);
  assert.equal(displayPromptContent(item), item.displayContent);
  assert.equal(editablePromptContent(item), item.editableContent);
  assert.equal(edit.content, item.editableContent);
  assert.equal(edit.originalContent, item.editableContent);
  assert.equal(edit.contentView, 'effective');
  assert.equal(edit.version, 8);
  assert.equal(promptDraftChanged(edit), false);
  assert.deepEqual(item, before);
});

test('older catalog responses still prefer actual effective text over obsolete raw defaults', () => {
  const item = { id: 'writing_outline_strict', version: 3, content: '旧版原文。', effectiveContent: '当前真实指令。' };
  assert.equal(displayPromptContent(item), item.effectiveContent);
  assert.equal(createPromptDraft(item).content, item.effectiveContent);
  assert.equal(createPromptDraft({ id: 'summary', version: 1, content: '普通指令。' }).content, '普通指令。');
});

test('current conflict reconciliation never exposes the historical storage baseline', () => {
  const latest = { id: 'writing_section_strict', version: 7, content: '历史原文不得用于对照。', displayContent: '当前实际规则。', editableContent: '当前实际规则。', contentView: 'effective' };
  const local = { id: latest.id, version: 6, originalContent: '原有效基线。', content: '当前人工修改。', contentView: 'effective' };
  const result = rebasePromptDraft(local, latest);
  assert.equal(result.content, local.content);
  assert.equal(result.originalContent, latest.editableContent);
  assert.equal(result.contentView, 'effective');
  assert.equal(result.version, 7);
  const same = rebasePromptDraft({ ...local, content: latest.editableContent }, latest);
  assert.equal(promptDraftChanged(same), false);
});

test('catalog refresh adopts the new current view only for untouched drafts', () => {
  const clean = draft('writing_outline_strict', 2, '旧版原文。');
  const dirty = draft('writing_section_strict', 2, '旧版原文。', '作者保留的修改。');
  const blocked = draft('writing_document_strict', 2, '旧版原文。');
  const items = [clean, dirty, blocked].map(value => ({ id: value.id, version: 3, content: '历史存储。', displayContent: '新有效指令。', editableContent: '新有效指令。', contentView: 'effective' }));
  const result = refreshCleanPromptDrafts({ [clean.id]: clean, [dirty.id]: dirty, [blocked.id]: blocked }, items, { [blocked.id]: { latest: null } });
  assert.equal(result[clean.id].content, '新有效指令。');
  assert.equal(result[clean.id].contentView, 'effective');
  assert.equal(promptDraftChanged(result[clean.id]), false);
  assert.equal(result[dirty.id], dirty);
  assert.equal(result[dirty.id].contentView, undefined, 'An inherited dirty source draft cannot silently acquire the effective save protocol');
  assert.equal(result[blocked.id], blocked, 'A pending conflict requires explicit reconciliation');
  assert.equal(clean.content, '旧版原文。');
});

test('a stale catalog cannot roll back a clean current draft or its frozen save view', () => {
  const edit = createPromptDraft({ id: 'writing_section_strict', version: 8, content: '旧原文。', editableContent: '有效新指令。', contentView: 'effective' });
  const drafts = { [edit.id]: edit };
  assert.equal(refreshCleanPromptDrafts(drafts, [{ id: edit.id, version: 7, content: '迟到旧版。', contentView: 'source' }]), drafts);
});

test('smart instructions hide structure entries by both category and stable ID', () => {
  assert.equal(isStructurePrompt({ id: 'writing_structure_report', category: 'structures' }), true);
  assert.equal(isStructurePrompt({ id: 'writing_structure_report', category: 'writing' }), true);
  assert.equal(isStructurePrompt({ id: 'other', category: 'structures' }), true);
  assert.equal(isStructurePrompt({ id: 'writing_section_strict', category: 'writing' }), false);
});

test('only necessary current supplemental clauses remain without duplicating the displayed body', () => {
  const item = { content: '旧原文。', displayContent: '当前正文。规则甲。\n\n规则乙。', supplementalRules: [
    { id: 'whole', title: '已有规则', scope: 'current', content: '规则甲。规则乙。' },
    { id: 'partial', title: '补充规则', scope: 'current', content: '规则甲。\n\n规则丙。' },
    { id: 'duplicate', title: '重复补充', scope: 'current', content: '规则丙。' },
    { id: 'legacy_scope', title: '旧规则', scope: 'legacy', content: '历史内容。' },
    { id: 'old_template', title: '旧模板', scope: 'legacy-template', content: '历史模板。' },
  ] };
  const before = structuredClone(item);
  const rules = currentPromptRules(item);
  assert.deepEqual(rules.map(rule => [rule.id, rule.formatted]), [['partial', '规则丙。']]);
  assert.deepEqual(item, before);
});

test('supplemental deduplication preserves whitespace inside quoted or coded values', () => {
  const item = { displayContent: '值为"AB"。代码`x y`。变量{{a b}}。', supplementalRules: [
    { id: 'quoted', title: '不同值', content: '值为"A B"。' },
    { id: 'code', title: '不同代码', content: '代码`xy`。' },
    { id: 'variable', title: '不同变量', content: '变量{{ab}}。' },
  ] };
  assert.equal(currentPromptRules(item).length, 3);
});

test('a delayed catalog read cannot roll back a successfully saved prompt', () => {
  const result = mergePromptCatalog([prompt('summary', 3, '已保存的人工修改')], [prompt('summary', 2, '旧内容'), prompt('translation', 1, '译文规则')]);
  assert.equal(result[0].version, 3);
  assert.equal(result[0].content, '已保存的人工修改');
  assert.equal(result[1].id, 'translation');
});

test('saving one prompt preserves unfinished edits in other categories', () => {
  const one = draft('summary', 1, '原摘要', '摘要修改');
  const two = draft('writing_section_strict', 4, '原写作规则', '写作修改');
  const result = applyPromptSave({ summary: one, writing_section_strict: two }, one, prompt('summary', 2, one.content));
  assert.equal(result.summary, undefined);
  assert.equal(result.writing_section_strict, two);
  assert.equal(promptDraftChanged(result.writing_section_strict), true);
});

test('a save response cannot discard editing that happened after submission', () => {
  const submitted = draft('summary', 1, '旧内容', '已提交内容');
  const newer = { ...submitted, content: '提交后的继续修改' };
  const result = applyPromptSave({ summary: newer }, submitted, prompt('summary', 2, submitted.content));
  assert.equal(result.summary.content, '提交后的继续修改');
  assert.equal(result.summary.originalContent, '已提交内容');
  assert.equal(result.summary.version, 2);
  assert.equal(promptDraftChanged(result.summary), true);
});

test('explicit conflict reconciliation retains the local content and adopts only the latest baseline', () => {
  const local = draft('summary', 1, '最初内容', '本地待保存修改');
  const latest = prompt('summary', 3, '另一个页面的修改');
  const result = rebasePromptDraft(local, latest);
  assert.equal(result.content, '本地待保存修改');
  assert.equal(result.originalContent, latest.content);
  assert.equal(result.version, 3);
  assert.equal(promptDraftChanged(result), true);
  assert.deepEqual(local, draft('summary', 1, '最初内容', '本地待保存修改'));
});

test('a response from a different prompt cannot rebase the active edit', () => {
  const local = draft('summary', 1, '原内容', '待保存');
  assert.equal(rebasePromptDraft(local, prompt('writing_section_strict', 9, '其他提示词')), local);
});

test('an unchanged edit and a manual return to the original content are clean', () => {
  assert.equal(promptDraftChanged(undefined), false);
  assert.equal(promptDraftChanged(draft('summary', 1, '原内容')), false);
  assert.equal(promptDraftChanged(draft('summary', 1, '原内容', '原内容\n')), true);
});

test('HTTP 409 remains distinguishable from network and validation failures without dropping server details', async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: false, status: 409, json: async () => ({ detail: '提示词版本已更新' }) });
    await assert.rejects(api('/prompts/summary', { method: 'PUT', body: JSON.stringify({ content: '修改', expectedVersion: 1 }) }), error => error.status === 409 && error.message === '提示词版本已更新');
    globalThis.fetch = async () => ({ ok: false, status: 422, json: async () => ({ detail: '必须保留变量 {{summaryBudget}}' }) });
    await assert.rejects(api('/prompts/summary', { method: 'PUT', body: '{}' }), error => error.status === 422 && error.message.includes('{{summaryBudget}}'));
  } finally { globalThis.fetch = originalFetch; }
});
