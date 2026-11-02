import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { buildWritingPreview, citationSentenceRange, citationForOccurrence } from '../src/writing-preview.js';

const reference = (overrides = {}) => ({
  id: 'E1', sourceId: 'source-one', revisionId: 'revision-one',
  blockId: 'block-one', title: '冻结材料一', quote: '来自第一篇材料的原文。',
  start: 0, end: Array.from(overrides.quote ?? '来自第一篇材料的原文。').length, locator: { paragraph: 1 }, ...overrides,
});

const section = (overrides = {}) => ({
  id: 'chapter-one', title: '第一章', mode: 'inherit', locked: false, manual: false,
  content: '第一段论述。[E1]', evidence: [reference()], ...overrides,
});

const project = (overrides = {}) => ({
  id: 'project-one', title: '预览中的人工草稿', version: 7, mode: 'strict',
  sourceIds: ['source-one', 'source-two'],
  sources: [
    { sourceId: 'source-one', revisionId: 'revision-one', title: '冻结材料一' },
    { sourceId: 'source-two', revisionId: 'revision-two', title: '冻结材料二' },
  ], sections: [section()], ...overrides,
});

function deepFreeze(value) {
  if (value && typeof value === 'object') {
    Object.freeze(value);
    Object.values(value).forEach(deepFreeze);
  }
  return value;
}

test('the same E1 in different chapters resolves only inside that chapter', () => {
  const input = project({ sections: [section(), section({
    id: 'chapter-two', title: '第二章', content: '另一章论述。[E1]',
    evidence: [reference({ sourceId: 'source-two', revisionId: 'revision-two', blockId: 'block-two', quote: '第二篇原文。' })],
  })] });
  const result = buildWritingPreview(input);
  const [first, second] = result.sections.map(item => item.paragraphs[0]);
  assert.equal(first.citations[0].evidence.sourceId, 'source-one');
  assert.equal(second.citations[0].evidence.sourceId, 'source-two');
  assert.notEqual(first.id, second.id, 'paragraph navigation must include chapter identity');
  assert.equal(first.citations[0].status, 'registered');
  assert.equal(second.citations[0].status, 'registered');
});

test('background paragraphs remain in the first evidence scope without becoming an extra chapter', () => {
  const input = deepFreeze(project({ sections: [
    section({ title: '当前态势', content: '开篇背景。[E1]\n\n当前态势正文。', introductionParagraphCount: 1 }),
    section({ id: 'chapter-two', title: '存在问题隐患', content: '问题正文。' }),
    section({ id: 'chapter-three', title: '对策建议', content: '建议正文。' }),
  ] }));
  const before = JSON.stringify(input), result = buildWritingPreview(input);
  assert.equal(result.sections.length, 3);
  assert.equal(result.sections[0].introductionParagraphCount, 1);
  assert.equal(result.sections[0].paragraphs[0].index, 0);
  assert.equal(result.sections[0].paragraphs[0].citations[0].status, 'registered');
  assert.equal(result.sections[0].paragraphs[0].id, 'chapter-one:paragraph-0');
  assert.equal(result.sections[0].paragraphs[1].text, '当前态势正文。');
  assert.equal(JSON.stringify(input), before);
});

test('invalid introduction metadata and counts on later chapters never move chapter headings', () => {
  for (const count of [0, -1, true, '1', 1.5, 2, 100]) {
    const result = buildWritingPreview(project({ sections: [
      section({ content: '背景。\n\n正文。', introductionParagraphCount: count }),
      section({ id: 'chapter-two', content: '另一章。\n\n正文。', introductionParagraphCount: 1 }),
    ] }));
    assert.equal(result.sections[0].introductionParagraphCount, 0);
    assert.equal(result.sections[1].introductionParagraphCount, 0);
  }
});

test('multiple citations preserve inline order while repeated material is listed once', () => {
  const content = '综合论述。[E2][E1] 同段再次提及 [E2]。';
  const result = buildWritingPreview(project({ sections: [section({ content, evidence: [reference(), reference({
    id: 'E2', sourceId: 'source-two', revisionId: 'revision-two', blockId: 'block-two', quote: '第二篇原文。',
  })] })] }));
  const paragraph = result.sections[0].paragraphs[0];
  assert.deepEqual(paragraph.citations.map(item => item.id), ['E2', 'E1']);
  assert.deepEqual(paragraph.tokens.filter(item => item.type === 'citation').map(item => item.id), ['E2', 'E1', 'E2']);
  assert.equal(paragraph.tokens.map(item => item.text).join(''), content);
  assert.equal(paragraph.missingEvidence, false);
});

test('a nearby cited paragraph cannot supply evidence to uncited or unknown citations', () => {
  const result = buildWritingPreview(project({ sections: [section({
    content: '第一段。[E1]\n\n人工新增的另一段。\n\n未知标识。[E999]',
  })] }));
  const [cited, uncited, unknown] = result.sections[0].paragraphs;
  assert.equal(cited.missingEvidence, false);
  assert.equal(uncited.missingEvidence, true);
  assert.deepEqual(uncited.citations, []);
  assert.equal(unknown.citations[0].status, 'unknown');
  assert.equal(unknown.citations[0].evidence, undefined);
  assert.equal(unknown.missingEvidence, true);
  assert.equal(unknown.needsAttention, true);
});

test('a removed source or changed frozen revision cannot be reported as registered', () => {
  for (const overrides of [
    { sourceIds: ['source-two'] },
    { sources: [{ sourceId: 'source-two', revisionId: 'revision-two' }] },
    { sources: [{ sourceId: 'source-one', revisionId: 'revision-new' }] },
    { sources: [{ sourceId: 'another-source', revisionId: 'revision-one' }] },
  ]) {
    const paragraph = buildWritingPreview(project(overrides)).sections[0].paragraphs[0];
    assert.equal(paragraph.citations[0].status, 'out_of_scope');
    assert.equal(paragraph.missingEvidence, true);
    assert.equal(paragraph.needsAttention, true);
  }
});

test('registered references retain their old frozen revision, quote and locator', () => {
  const input = project();
  const ref = buildWritingPreview(input).sections[0].paragraphs[0].citations[0];
  assert.equal(ref.status, 'registered');
  assert.equal(ref.evidence.revisionId, 'revision-one');
  assert.equal(ref.evidence.blockId, 'block-one');
  assert.equal(ref.evidence.quote, input.sections[0].evidence[0].quote);
  assert.deepEqual(ref.evidence.locator, { paragraph: 1 });
  assert.equal(ref.semanticVerified, undefined, 'displayed registration is not semantic verification');
});

test('incomplete anchors and duplicate IDs are never resolved by guessing', () => {
  for (const field of ['revisionId', 'blockId', 'quote']) {
    const value = project({ sections: [section({ evidence: [reference({ [field]: '' })] })] });
    const paragraph = buildWritingPreview(value).sections[0].paragraphs[0];
    assert.equal(paragraph.citations[0].status, 'invalid_anchor', field);
    assert.equal(paragraph.missingEvidence, true);
  }
  const duplicate = buildWritingPreview(project({ sections: [section({ evidence: [
    reference(), reference({ blockId: 'different-block', quote: '不同的材料内容。' }),
  ] })] })).sections[0].paragraphs[0];
  assert.equal(duplicate.citations[0].status, 'ambiguous');
  assert.equal(duplicate.missingEvidence, true);
  assert.equal(duplicate.needsAttention, true);
});

test('model supplements remain visible and need attention even beside a registered citation', () => {
  for (const content of ['【模型补充·待核实】这是补充论断。', '已有论述。[E1] 【模型补充·待核实】这一部分尚无依据。']) {
    const paragraph = buildWritingPreview(project({ mode: 'extended', sections: [section({ content })] })).sections[0].paragraphs[0];
    assert.equal(paragraph.hasModelSupplement, true);
    assert.equal(paragraph.needsAttention, true);
    assert.equal(paragraph.text, content);
  }
});

test('manual chapters preserve citations but remain marked for human review', () => {
  const chapter = buildWritingPreview(project({ sections: [section({ manual: true, locked: true })] })).sections[0];
  assert.equal(chapter.manual, true);
  assert.equal(chapter.locked, true);
  assert.equal(chapter.paragraphs[0].citations[0].status, 'registered');
  assert.equal(chapter.paragraphs[0].needsAttention, true);
});

test('preview uses current draft text without trimming, saving or mutating any inputs', () => {
  const input = deepFreeze(project({ sections: [section({
    content: '  尚未保存的😀人工修改。[E1]\n同段第二行\n\n\n另一段未引用。  ', manual: true,
  })] }));
  const before = JSON.stringify(input);
  const result = buildWritingPreview(input);
  const paragraphs = result.sections[0].paragraphs;
  assert.equal(result.title, input.title);
  assert.equal(result.paragraphCount, 2);
  assert.equal(paragraphs[0].text, '  尚未保存的😀人工修改。[E1]\n同段第二行');
  assert.equal(paragraphs[1].text, '另一段未引用。  ');
  assert.equal(paragraphs[0].tokens.map(item => item.text).join(''), paragraphs[0].text);
  assert.equal(JSON.stringify(input), before);
  assert.equal(input.version, 7);
});

test('CRLF, blank chapters and per-chapter mode overrides remain distinct', () => {
  const result = buildWritingPreview(project({ sections: [
    section({ content: '第一段。[E1]\r\n\r\n第二段。', mode: 'extended' }),
    section({ id: 'empty-chapter', content: ' \n\n  ', mode: 'strict', evidence: [] }),
  ] }));
  assert.equal(result.sections[0].mode, 'extended');
  assert.equal(result.sections[0].paragraphs.length, 2);
  assert.equal(result.sections[1].mode, 'strict');
  assert.equal(result.sections[1].empty, true);
  assert.deepEqual(result.sections[1].paragraphs, []);
  assert.equal(result.paragraphCount, 2);
});

test('citation spelling must exactly match the registered ID and must not be normalized', () => {
  const paragraph = buildWritingPreview(project({ sections: [section({
    content: '不能把 [E01] 或 [E0] 猜成 [E1]；[e1] 与［E1］保持普通文字。',
  })] })).sections[0].paragraphs[0];
  assert.deepEqual(paragraph.citations.map(item => [item.id, item.status]), [
    ['E01', 'unknown'], ['E0', 'unknown'], ['E1', 'registered'],
  ]);
  assert.equal(paragraph.needsAttention, true);
  assert.equal(paragraph.tokens.map(item => item.text).join(''), paragraph.text);
});

test('narrowed article scope retains only citations to chosen original blocks', () => {
  const original = project();
  const input = deepFreeze(project({
    sources: original.sources.map(source => source.sourceId === 'source-one' ? { ...source, blockIds: ['chosen-block'] } : source),
    sections: [section({ content: '先前引用。[E1]\n\n当前所选段落。[E2]', evidence: [reference(), reference({ id: 'E2', blockId: 'chosen-block', quote: '目前确认纳入的原文。' })] })],
  }));
  const paragraphs = buildWritingPreview(input).sections[0].paragraphs;
  assert.equal(paragraphs[0].citations[0].status, 'out_of_scope');
  assert.equal(paragraphs[0].citations[0].evidence.quote, input.sections[0].evidence[0].quote, 'Keep old reference readable without claiming it is in scope');
  assert.equal(paragraphs[0].missingEvidence, true);
  assert.equal(paragraphs[1].citations[0].status, 'registered');
  assert.equal(paragraphs[1].missingEvidence, false);
});

test('explicit empty or malformed block scope cannot revert to whole-article permission', () => {
  for (const blockIds of [[], null, 'block-one']) {
    const input = project();
    input.sources[0].blockIds = blockIds;
    const paragraph = buildWritingPreview(input).sections[0].paragraphs[0];
    assert.equal(paragraph.citations[0].status, 'out_of_scope');
    assert.equal(paragraph.missingEvidence, true);
  }
  assert.equal(buildWritingPreview(project()).sections[0].paragraphs[0].citations[0].status, 'registered', 'Legacy whole-article projects retain their scope');
});

test('inline icons share first-appearance source-version numbers while retaining every exact excerpt and occurrence', () => {
  const input = project({ sections: [section({
    content: '先引第二篇。[E2] 再引第一篇的两处。[E1][E3] 重复同一处。[E1]',
    evidence: [reference(), reference({ id: 'E3', blockId: 'another-block', quote: '同一来源另一处原文。' }), reference({ id: 'E2', sourceId: 'source-two', revisionId: 'revision-two', blockId: 'block-two' })],
  }), section({ id: 'chapter-two', content: '下一章沿用第二篇。[E1]', evidence: [reference({ sourceId: 'source-two', revisionId: 'revision-two' })] })] });
  const result = buildWritingPreview(deepFreeze(input));
  const first = result.sections[0].paragraphs[0];
  assert.deepEqual(first.citations.map(item => [item.id, item.number]), [['E2', 1], ['E1', 2], ['E3', 2]]);
  assert.deepEqual(first.tokens.filter(item => item.type === 'citation').map(item => item.number), [1, 2, 2, 2]);
  assert.equal(first.citations[2].evidence.blockId, 'another-block');
  assert.equal(result.sections[1].paragraphs[0].citations[0].number, 1);
  assert.equal(first.tokens.map(item => item.text).join(''), input.sections[0].content);
});

test('same article at distinct frozen revisions gets separate numbers; invalid records never consume a number', () => {
  const input = project({ sources: [...project().sources, { sourceId: 'source-one', revisionId: 'revision-new' }], sections: [section({
    content: '未知。[E404] 不完整。[E9] 原版。[E1] 新版。[E2]',
    evidence: [reference(), reference({ id: 'E9', end: 99 }), reference({ id: 'E2', revisionId: 'revision-new' })],
  })] });
  assert.deepEqual(buildWritingPreview(input).sections[0].paragraphs[0].citations.map(item => [item.id, item.number]), [['E404', undefined], ['E9', undefined], ['E1', 1], ['E2', 2]]);
});

const hash = content => createHash('sha256').update(content).digest('hex');
function bind(content, items) {
  return { version: 1, content, contentHash: hash(content), textHash: hash(content.replace(/\[E\d+\]/g, '')), items };
}
function supported(content, text, markerStart, start = content.lastIndexOf(text, markerStart)) {
  return { id: `binding-${markerStart}`, evidenceId: content.slice(markerStart + 1, content.indexOf(']', markerStart)), status: 'supported', markerStart, start, end: start + text.length, text, reason: '材料支持本句论述。' };
}

test('legacy paragraph-end and mid-sentence markers never imply a supported last sentence', () => {
  for (const content of ['第一句有来源。末句是作者判断。[E1]', '第一句。[E1]另一句。', '带引文[E1]的完整句子。']) {
    const paragraph = buildWritingPreview(project({ sections: [section({ content })] })).sections[0].paragraphs[0];
    const token = paragraph.tokens.find(item => item.type === 'citation');
    assert.equal(token.range, null);
    assert.equal(token.bindingStatus, 'unmatched');
    assert.equal(paragraph.unplacedCitations, 1);
    assert.equal(paragraph.citations[0].status, 'registered', 'Exact original excerpt remains available without claiming sentence support');
    assert.equal(citationSentenceRange(content, content.indexOf('[E1]')), null);
  }
});

test('saved evidence assessment highlights an early supported sentence, never the rest of its paragraph', () => {
  const content = '材料支持这句😀判断。[E1]后续是作者自己的分析，不能一并高亮。';
  const item = supported(content, '材料支持这句😀判断。', content.indexOf('[E1]'));
  const input = deepFreeze(project({ sections: [section({ content, contentHash: hash(content), citationBindings: bind(content, [item]) })] }));
  const before = JSON.stringify(input);
  const paragraph = buildWritingPreview(input).sections[0].paragraphs[0];
  const token = paragraph.tokens.find(item => item.type === 'citation');
  assert.deepEqual(token.range, { start: 0, end: item.end });
  assert.equal(paragraph.text.slice(token.range.start, token.range.end), item.text);
  assert.equal(token.bindingStatus, 'supported');
  assert.equal(paragraph.unplacedCitations, 0);
  assert.equal(JSON.stringify(input), before, 'Reading evidence assessment must not move citations or rewrite text');
});

test('unreviewed exact original references remain clickable without a supported sentence highlight', () => {
  const text = '原文可定位的这句😀论述。', content = text + '[E1]后文仍需检查。';
  const item = { ...supported(content, text, content.indexOf('[E1]')), status: 'unreviewed', supportBasis: 'none', reason: '本次自动引用核对未完成。' };
  const input = deepFreeze(project({ sections: [section({ content, contentHash: hash(content), citationBindings: { ...bind(content, [item]), mode: 'strict' } })] }));
  const before = JSON.stringify(input), paragraph = buildWritingPreview(input).sections[0].paragraphs[0];
  const token = paragraph.tokens.find(item => item.type === 'citation');
  assert.equal(token.status, 'registered');
  assert.equal(token.bindingStatus, 'unreviewed');
  assert.equal(token.number, 1);
  assert.equal(token.bindingText, text);
  assert.equal(token.range, null, 'A located excerpt is not confirmed sentence support');
  assert.equal(citationSentenceRange(content, token.sectionStart, input.sections[0].citationBindings, 'E1'), null);
  const clicked = citationForOccurrence(paragraph, 'E1', token.start);
  assert.equal(clicked.bindingStatus, 'unreviewed');
  assert.equal(clicked.bindingText, text);
  assert.equal(clicked.evidence.blockId, 'block-one', 'Pending review retains original-source navigation');
  assert.equal(paragraph.missingEvidence, false);
  assert.equal(paragraph.needsAttention, true);
  assert.equal(paragraph.unplacedCitations, 1);
  assert.equal(JSON.stringify(input), before);
});

test('stale, ambiguous, out-of-scope or malformed pending references never become recognized unreviewed markers', () => {
  const text = '😀原文关联的论述。', content = text + '[E1]另一句。';
  const pending = { ...supported(content, text, content.indexOf('[E1]')), status: 'unreviewed', supportBasis: 'none' };
  const base = { content, contentHash: hash(content), citationBindings: { ...bind(content, [pending]), mode: 'strict' } };
  const invalidItems = [
    [{ ...pending, evidenceId: 'E2' }],
    [pending, { ...pending, id: 'duplicate' }],
    [{ ...pending, text: '另一句。' }],
    [{ ...pending, start: 1, text: content.slice(1, pending.end) }],
    [{ ...pending, markerStart: pending.markerStart + 1 }],
    [{ ...pending, supportBasis: 'paraphrase' }],
  ];
  const variations = [
    { content: content + '人工修改。' }, { contentHash: hash('another revision') },
    { mode: 'extended' }, { evidence: [] },
    { evidence: [reference({ revisionId: 'outside' })] },
    { evidence: [reference({ end: 999 })] }, { evidence: [reference(), reference()] },
    ...invalidItems.map(items => ({ citationBindings: { ...bind(content, items), mode: 'strict' } })),
  ];
  for (const overrides of variations) {
    const paragraph = buildWritingPreview(project({ sections: [section({ ...base, ...overrides })] })).sections[0].paragraphs[0];
    const token = paragraph.tokens.find(item => item.type === 'citation');
    assert.notEqual(token.bindingStatus, 'unreviewed');
    assert.notEqual(token.bindingStatus, 'supported');
    assert.equal(token.range, null);
    assert.equal(paragraph.needsAttention, true);
  }
});

test('unreviewed statements without a marker remain separate pending diagnostics in strict and current extended prose', () => {
  const content = '自动核对尚未完成的论述。';
  for (const mode of ['strict', 'extended', 'expanded']) {
    const pending = { id: 'pending', status: 'unreviewed', evidenceId: null, supportBasis: 'none', start: 0, end: content.length, text: content, reason: '自动核对未完成。' };
    const input = deepFreeze(project({ mode, sections: [section({
      content, evidence: [], extendedContentPolicy: 'retain-as-prose-v2',
      citationBindings: { ...bind(content, [pending]), mode: mode === 'strict' ? 'strict' : 'extended' },
    })] }));
    const paragraph = buildWritingPreview(input).sections[0].paragraphs[0];
    assert.deepEqual(paragraph.diagnostics.map(item => item.status), ['unreviewed']);
    assert.equal(paragraph.diagnostics[0].text, content);
    assert.deepEqual(paragraph.diagnostics[0].evidenceIds, []);
    assert.equal(paragraph.needsAttention, true, 'Incomplete review must not disappear under the extended prose policy');
    assert.equal(paragraph.missingEvidence, mode === 'strict');
    assert.equal(paragraph.citations.length, 0, 'Pending diagnostics must not invent citation markers');
    assert(paragraph.tokens.every(item => item.type === 'text'));
  }
});

test('full-chapter UTF-16 offsets identify repeated sentences and adjacent excerpts after CRLF separators', () => {
  const content = '前置😀句。相同句子。[E1]\r\n\r\n相同句子。[E1][E2]后文。';
  const markers = [...content.matchAll(/\[E\d+\]/g)];
  const first = supported(content, '相同句子。', markers[0].index);
  const second = supported(content, '相同句子。', markers[1].index);
  const third = { ...second, id: 'other-excerpt', evidenceId: 'E2', markerStart: markers[2].index };
  const input = project({ sections: [section({ content, contentHash: hash(content), citationBindings: bind(content, [first, second, third]), evidence: [reference(), reference({ id: 'E2', blockId: 'other-block' })] })] });
  const [one, two] = buildWritingPreview(input).sections[0].paragraphs;
  const [a, b] = [one, two].map(paragraph => paragraph.tokens.find(item => item.type === 'citation'));
  assert.notEqual(a.sectionStart, b.sectionStart);
  assert.equal(one.text.slice(a.range.start, a.range.end), '相同句子。');
  assert.equal(two.text.slice(b.range.start, b.range.end), '相同句子。');
  assert.equal(two.start + b.range.start, second.start);
  assert.deepEqual(two.tokens.filter(item => item.type === 'citation').map(item => item.range), [b.range, b.range]);
  assert.deepEqual(two.tokens.filter(item => item.type === 'citation').map(item => item.number), [1, 1]);
  assert.equal(citationForOccurrence(two, 'E1', b.start).bindingText, '相同句子。');
});

test('unsaved or saved manual changes invalidate all sentence bindings even if the old server hash remains', () => {
  const original = '有来源的句子。[E1]作者判断。';
  const saved = bind(original, [supported(original, '有来源的句子。', original.indexOf('[E1]'))]);
  for (const override of [
    { content: original + '新的人工文字。', contentHash: hash(original) },
    { content: '改过的句子。[E1]作者判断。', contentHash: hash(original) },
    { content: original, contentHash: hash('another revision') },
  ]) {
    const paragraph = buildWritingPreview(project({ sections: [section({ ...override, manual: true, citationBindings: saved })] })).sections[0].paragraphs[0];
    const token = paragraph.tokens.find(item => item.type === 'citation');
    assert.equal(token.range, null);
    assert.equal(token.bindingStatus, 'stale');
    assert.equal(citationForOccurrence(paragraph, 'E1', token.start).bindingStatus, 'stale');
  }
});

test('changed occurrence, duplicate assessment, mismatched sentence and split emoji never receive precise highlights', () => {
  const content = '😀支持句。[E1]其他句子。';
  const item = supported(content, '😀支持句。', content.indexOf('[E1]'));
  for (const items of [
    [{ ...item, evidenceId: 'E2' }],
    [item, { ...item, id: 'duplicate' }],
    [{ ...item, text: '捏造句子。' }],
    [{ ...item, start: 1, text: content.slice(1, item.end) }],
    [{ ...item, status: 'partial' }],
    [{ ...item, end: content.length, text: content }],
    [{ ...item, start: content.indexOf('其他'), end: content.length, text: '其他句子。' }],
  ]) {
    const paragraph = buildWritingPreview(project({ sections: [section({ content, citationBindings: bind(content, items) })] })).sections[0].paragraphs[0];
    assert.equal(paragraph.tokens.find(item => item.type === 'citation').range, null);
  }
  const unrelated = '支持的是第一句。末句无依据。[E1]';
  const fakePlacement = supported(unrelated, '支持的是第一句。', unrelated.indexOf('[E1]'), 0);
  assert.equal(citationSentenceRange(unrelated, fakePlacement.markerStart, bind(unrelated, [fakePlacement]), 'E1'), null, 'Do not attach an earlier sentence assessment to a legacy paragraph-end marker');
});

test('partial, unsupported and uncertain sentences remain unplaced diagnostics with exact current text', () => {
  const content = '前面😀有材料。[E1]部分论断缺少依据。\n\n尚无支持材料。含义无法确认。';
  const support = supported(content, '前面😀有材料。', content.indexOf('[E1]'));
  const diagnostics = [['partial', '部分论断缺少依据。'], ['unsupported', '尚无支持材料。'], ['uncertain', '含义无法确认。']].map(([status, text], index) => ({ id: `issue-${index}`, evidenceId: index ? undefined : 'E1', status, text, start: content.indexOf(text), end: content.indexOf(text) + text.length, reason: '需核对' }));
  const result = buildWritingPreview(project({ sections: [section({ content, citationBindings: bind(content, [support, ...diagnostics]) })] }));
  assert.deepEqual(result.sections[0].paragraphs.map(paragraph => paragraph.diagnostics.map(item => item.status)), [['partial'], ['unsupported', 'uncertain']]);
  assert.equal(result.sections[0].paragraphs[0].tokens.filter(item => item.type === 'citation').length, 1, 'Diagnostics must never create or relocate a marker');
  assert.equal(result.sections[0].paragraphs[1].tokens.some(item => item.type === 'citation'), false);
  assert(result.sections[0].paragraphs.every(paragraph => paragraph.needsAttention));
});

test('out-of-scope, invalid and ambiguous evidence cannot be upgraded by a supported sentence assessment', () => {
  const content = '材料支持此句。[E1]';
  const bindings = bind(content, [supported(content, '材料支持此句。', content.indexOf('[E1]'))]);
  for (const evidence of [[reference({ revisionId: 'outside' })], [reference({ end: 999 })], [reference(), reference()]]) {
    const token = buildWritingPreview(project({ sections: [section({ content, citationBindings: bindings, evidence })] })).sections[0].paragraphs[0].tokens.find(item => item.type === 'citation');
    assert.equal(token.range, null);
    assert.notEqual(token.bindingStatus, 'supported');
  }
});

test('reviewed organizational paragraphs need no citation, while stale neutral decisions do not hide missing evidence', () => {
  const content = '下面讨论清扫条件。';
  const neutral = { id: 'transition', status: 'not_required', text: content, start: 0, end: content.length, reason: '组织承接' };
  const saved = bind(content, [neutral]);
  const current = buildWritingPreview(project({ sections: [section({ content, manual: true, evidence: [], citationBindings: saved })] })).sections[0].paragraphs[0];
  assert.equal(current.missingEvidence, false);
  assert.equal(current.needsAttention, false);
  assert.equal(current.diagnostics.length, 0);
  const changed = buildWritingPreview(project({ sections: [section({ content: content + '新增事实。', evidence: [], citationBindings: saved })] })).sections[0].paragraphs[0];
  assert.equal(changed.missingEvidence, true);
  assert.equal(changed.needsAttention, true);
});

test('multiple excerpts for one partially supported sentence produce one explanation', () => {
  const content = '这句包含证据未能支持的加强判断。';
  const item = { status: 'partial', text: content, start: 0, end: content.length, reason: '原文仅说明可能性，未说明必然性。' };
  const bindings = bind(content, [{ ...item, id: 'one', evidenceId: 'E1' }, { ...item, id: 'two', evidenceId: 'E2' }]);
  const paragraph = buildWritingPreview(project({ sections: [section({ content, citationBindings: bindings })] })).sections[0].paragraphs[0];
  assert.equal(paragraph.diagnostics.length, 1);
  assert.deepEqual(paragraph.diagnostics[0].evidenceIds, ['E1', 'E2']);
  assert.equal(paragraph.diagnostics[0].reason, item.reason);
});

test('saved edits retain the stale warning after the backend removes obsolete coordinates', () => {
  const content = '人工改写后的句子。[E1]';
  const paragraph = buildWritingPreview(project({ sections: [section({ content, manual: true, citationBindingState: 'stale' })] })).sections[0].paragraphs[0];
  const token = paragraph.tokens.find(item => item.type === 'citation');
  assert.equal(token.bindingStatus, 'stale');
  assert.equal(token.range, null);
  assert.equal(paragraph.unplacedCitations, 1);
  assert.equal(paragraph.needsAttention, true);
});

test('switching effective mode invalidates a scoped inference review before saving', () => {
  const content = '这是一条扩展模式下的有据推断。[E1]';
  const saved = { ...bind(content, [supported(content, '这是一条扩展模式下的有据推断。', content.indexOf('[E1]'))]), mode: 'extended' };
  const draft = overrides => buildWritingPreview(project({ sections: [section({ content, citationBindings: saved, ...overrides })] })).sections[0].paragraphs[0].tokens.find(item => item.type === 'citation');
  assert.equal(draft({ mode: 'inherit' }).bindingStatus, 'stale');
  assert.equal(draft({ mode: 'inherit' }).range, null);
  assert.equal(draft({ mode: 'extended' }).bindingStatus, 'supported', 'Explicit chapter override keeps its actual extended scope');
});

test('new extended prose needs no citation or unsupported warning without claiming source support', () => {
  const content = '这里可以展开新的分析。';
  for (const status of [undefined, 'partial', 'unsupported', 'uncertain']) {
    const items = status ? [{ id: 'uncited', status, text: content, start: 0, end: content.length, reason: '原文未覆盖' }] : [];
    const input = deepFreeze(project({ mode: 'extended', sections: [section({
      content, evidence: [], extendedContentPolicy: 'retain-as-prose-v2',
      ...(status ? { citationBindings: bind(content, items) } : {}),
    })] }));
    const before = JSON.stringify(input);
    const paragraph = buildWritingPreview(input).sections[0].paragraphs[0];
    assert.equal(paragraph.text, content);
    assert.equal(paragraph.missingEvidence, false);
    assert.equal(paragraph.needsAttention, false);
    assert.deepEqual(paragraph.diagnostics, []);
    assert.deepEqual(paragraph.citations, []);
    assert(paragraph.tokens.every(token => token.type === 'text'));
    assert.equal(JSON.stringify(input), before, 'Do not change internal unsupported decisions to supported');
  }
});

test('extended prose policy follows the effective chapter mode and preserves legacy checks', () => {
  const content = '不带引用的分析。';
  for (const [projectMode, chapterMode, policy, missing] of [
    ['strict', 'extended', 'retain-as-prose-v2', false],
    ['extended', 'strict', 'retain-as-prose-v2', true],
    ['strict', 'inherit', 'retain-as-prose-v2', true],
    ['extended', 'inherit', undefined, true],
    ['extended', 'inherit', 'retain-unverified-v1', true],
  ]) {
    const input = project({ mode: projectMode, sections: [section({ content, mode: chapterMode, extendedContentPolicy: policy, evidence: [] })] });
    const paragraph = buildWritingPreview(input).sections[0].paragraphs[0];
    assert.equal(paragraph.missingEvidence, missing);
    assert.equal(paragraph.needsAttention, missing);
  }
});

test('new extended paragraphs retain valid citation highlights alongside uncited prose', () => {
  const content = '原文明确记载的事实。[E1]这里接着展开分析。';
  const prose = '这里接着展开分析。';
  const items = [supported(content, '原文明确记载的事实。', content.indexOf('[E1]')),
    { status: 'unsupported', text: prose, start: content.indexOf(prose), end: content.length, reason: '原文未覆盖' }];
  const paragraph = buildWritingPreview(project({ mode: 'extended', sections: [section({
    content, extendedContentPolicy: 'retain-as-prose-v2', citationBindings: bind(content, items),
  })] })).sections[0].paragraphs[0];
  assert.equal(paragraph.citations[0].status, 'registered');
  assert.equal(paragraph.tokens.find(token => token.type === 'citation').bindingStatus, 'supported');
  assert.deepEqual(paragraph.diagnostics, []);
  assert.equal(paragraph.missingEvidence, false);
  assert.equal(paragraph.needsAttention, false);
});

test('new extended prose preserves actual citation failures and evidence-bound diagnostics', () => {
  const content = '带有引用的论断。[E1]';
  for (const overrides of [
    { evidence: [] },
    { evidence: [reference({ revisionId: 'outside' })] },
    { citationBindingState: 'stale' },
  ]) {
    const paragraph = buildWritingPreview(project({ mode: 'extended', sections: [section({
      content, extendedContentPolicy: 'retain-as-prose-v2', ...overrides,
    })] })).sections[0].paragraphs[0];
    assert.equal(paragraph.needsAttention, true);
    assert.equal(paragraph.unplacedCitations, 1);
  }
  const uncited = '这句话存在具体引用的问题。';
  const item = { status: 'unsupported', evidenceId: 'E1', text: uncited, start: 0, end: uncited.length, reason: '引用与论断不符' };
  const paragraph = buildWritingPreview(project({ mode: 'extended', sections: [section({
    content: uncited, extendedContentPolicy: 'retain-as-prose-v2', citationBindings: bind(uncited, [item]),
  })] })).sections[0].paragraphs[0];
  assert.deepEqual(paragraph.diagnostics.map(diagnostic => diagnostic.status), ['unsupported']);
  assert.equal(paragraph.needsAttention, true);
});

test('new extended prose keeps explicit pending text and manual or locked chapter state', () => {
  for (const content of ['【模型补充·待核实】保留现有标注。', '【待核实】这是作者保留的说明。']) {
    const paragraph = buildWritingPreview(project({ mode: 'extended', sections: [section({
      content, evidence: [], extendedContentPolicy: 'retain-as-prose-v2',
    })] })).sections[0].paragraphs[0];
    assert.equal(paragraph.text, content);
    assert.equal(paragraph.needsAttention, true);
  }
  const chapter = buildWritingPreview(project({ mode: 'extended', sections: [section({
    content: '人工修改后的正文。', evidence: [], manual: true, locked: true, extendedContentPolicy: 'retain-as-prose-v2',
  })] })).sections[0];
  assert.equal(chapter.manual, true);
  assert.equal(chapter.locked, true);
  assert.equal(chapter.paragraphs[0].needsAttention, true);
});

test('UI expanded aliases retain the same current mode-bound citation review and prose policy as API extended modes', () => {
  const text = '原文明示的事实。', prose = '接下来是扩展讨论。', content = text + '[E1]\n\n' + prose;
  const items = [supported(content, text, content.indexOf('[E1]')),
    { status: 'unsupported', text: prose, start: content.indexOf(prose), end: content.length, reason: '原文未覆盖' }];
  const saved = { ...bind(content, items), mode: 'extended' };
  for (const [projectMode, chapterMode] of [['expanded', 'inherit'], ['strict', 'expanded'], ['expanded', 'extended']]) {
    const input = deepFreeze(project({ mode: projectMode, sections: [section({
      content, mode: chapterMode, citationBindings: saved, extendedContentPolicy: 'retain-as-prose-v2',
    })] }));
    const before = JSON.stringify(input), result = buildWritingPreview(input).sections[0];
    assert.equal(result.mode, 'extended');
    assert.equal(result.bindingStatus, 'current');
    assert.equal(result.paragraphs[0].tokens.find(token => token.type === 'citation').bindingStatus, 'supported');
    assert(result.paragraphs[0].tokens.find(token => token.type === 'citation').range);
    assert.equal(result.paragraphs[1].missingEvidence, false);
    assert.equal(result.paragraphs[1].needsAttention, false);
    assert.equal(JSON.stringify(input), before, 'Preview must not rewrite stored mode or support decisions');
  }
  const strictOverride = buildWritingPreview(project({ mode: 'expanded', sections: [section({
    content, mode: 'strict', citationBindings: saved, extendedContentPolicy: 'retain-as-prose-v2',
  })] })).sections[0];
  assert.equal(strictOverride.bindingStatus, 'stale', 'An actual mode change must still invalidate the review');
  assert.equal(strictOverride.paragraphs[1].missingEvidence, true);
});


function archivedChapter(overrides = {}, sources = [{ sourceId: 'source-one', revisionId: 'revision-one', blockIds: ['block-one'] }]) {
  const chapter = section(overrides);
  const evidenceHash = createHash('sha256').update(JSON.stringify(chapter.evidence)).digest('hex');
  return { ...chapter, evidenceHash, citationSourceScopeValid: true, citationSourceScope: { policy: 'preserved-chapter-evidence-scope-v1', evidenceHash, textHash: 'server-verified-prose', sources } };
}
function projectWithNewSources(chapters) {
  return project({ sourceIds: ['source-two'], sources: [{ sourceId: 'source-two', revisionId: 'revision-two' }], sections: chapters });
}

test('a server-validated chapter archive preserves its old citation after the generation source pool changes', () => {
  const input = deepFreeze(projectWithNewSources([archivedChapter({ locked: true, manual: true })]));
  const before = JSON.stringify(input), citation = buildWritingPreview(input).sections[0].paragraphs[0].citations[0];
  assert.equal(citation.status, 'registered');
  assert.equal(citation.number, 1);
  assert.equal(citation.evidence.revisionId, 'revision-one');
  assert.equal(citation.evidence.quote, input.sections[0].evidence[0].quote);
  assert.equal(citation.semanticVerified, undefined);
  assert.notEqual(citation.bindingStatus, 'supported', 'A preserved registry scope is not semantic support');
  assert.equal(JSON.stringify(input), before);
});

test('the preserved scope is chapter-only and never unions old evidence into a new chapter or current source into the archive', () => {
  const fresh = reference({ id: 'E2', sourceId: 'source-two', revisionId: 'revision-two', blockId: 'block-two', quote: '新一轮资料的原文。' });
  const content = '旧资料的论述。[E1]当前资料的论述。[E2]';
  const oldChapter = archivedChapter({ content, evidence: [reference(), fresh] });
  const newChapter = section({ id: 'new-chapter', content, evidence: [reference(), fresh] });
  const result = buildWritingPreview(deepFreeze(projectWithNewSources([oldChapter, newChapter])));
  assert.deepEqual(result.sections[0].paragraphs[0].citations.map(item => item.status), ['registered', 'out_of_scope']);
  assert.deepEqual(result.sections[1].paragraphs[0].citations.map(item => item.status), ['out_of_scope', 'registered']);
  assert.equal(result.sections[1].paragraphs[0].citations[1].number, 2);
});

test('wrong policy, changed evidence hash or missing server approval cannot revive an old source', () => {
  for (const change of [
    chapter => { chapter.evidenceHash = 'different-current-evidence'; },
    chapter => { delete chapter.evidenceHash; },
    chapter => { chapter.evidenceHash = ''; chapter.citationSourceScope.evidenceHash = ''; },
    chapter => { chapter.citationSourceScope.evidenceHash = 'different-archive-evidence'; },
    chapter => { chapter.citationSourceScope.policy = 'unrecognized-scope'; },
    chapter => { chapter.citationSourceScopeValid = false; },
    chapter => { delete chapter.citationSourceScopeValid; },
  ]) {
    const chapter = archivedChapter(); change(chapter);
    const citation = buildWritingPreview(deepFreeze(projectWithNewSources([chapter]))).sections[0].paragraphs[0].citations[0];
    assert.equal(citation.status, 'out_of_scope');
    assert.equal(citation.number, undefined);
  }
});

test('an invalid archive falls back to the actual current scope, with no cross-chapter or malformed-source permission', () => {
  const fresh = reference({ id: 'E2', sourceId: 'source-two', revisionId: 'revision-two', blockId: 'block-two', quote: '当前有效资料。' });
  for (const sources of [null, {}, [{ sourceId: 'source-one' }], [{ sourceId: 'source-one', revisionId: 'revision-one', blockIds: null }], [{ sourceId: 'source-one', revisionId: 'revision-one' }, { sourceId: 'source-one', revisionId: 'revision-one' }]]) {
    const chapter = archivedChapter({ content: '旧引用。[E1]新引用。[E2]', evidence: [reference(), fresh] }, sources);
    const citations = buildWritingPreview(deepFreeze(projectWithNewSources([chapter]))).sections[0].paragraphs[0].citations;
    assert.deepEqual(citations.map(item => item.status), ['out_of_scope', 'registered']);
  }
});

test('a valid archived block whitelist remains authoritative even when the same source is broadly available in the current pool', () => {
  for (const blockIds of [[], ['another-block']]) {
    const chapter = archivedChapter({}, [{ sourceId: 'source-one', revisionId: 'revision-one', blockIds }]);
    const citation = buildWritingPreview(deepFreeze(project({ sections: [chapter] }))).sections[0].paragraphs[0].citations[0];
    assert.equal(citation.status, 'out_of_scope');
  }
  const chapter = archivedChapter({}, []);
  assert.equal(buildWritingPreview(project({ sections: [chapter] })).sections[0].paragraphs[0].citations[0].status, 'out_of_scope', 'An explicitly empty valid archive cannot expand to project-wide permission');
});
