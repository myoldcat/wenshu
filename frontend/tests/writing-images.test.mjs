import test from 'node:test';
import assert from 'node:assert/strict';
import { IMAGE_QUALITY_POLICY, imageParagraphs, imageQualityBlock, imageSearchCurrent, imageSearchHasNoSuitableImages, imageCandidateBlock, imageCandidateExplanation, imageCandidatePlacements, imageRunForView, imagePlacementMode, imageSearchScope, illustrationsAt, orderedIllustrations, imageBusinessText, imageCoverageText } from '../src/writing-images.js';
import { deriveWritingProgress } from '../src/writing-progress.js';

const content = '  第一段原文。[E1]\n\n第二段内容。\n\n  ';
const section = { id: 's1', title: '原文', content, contentHash: 'current-hash' };
const anchor = { paragraphIndex: 0, paragraphExact: '  第一段原文。[E1]', contentHash: 'current-hash' };
const figure = { id: 'f1', sectionId: 's1', status: 'accepted', anchor };
const project = { id: 'p1', version: 3, title: '标题', topic: '标题', outlineConfirmed: true, sourceIds: [], sections: [section], illustrations: [figure], runs: [] };

test('image anchors preserve full paragraph text, indentation and citations', () => {
  assert.deepEqual(imageParagraphs(content), ['  第一段原文。[E1]', '第二段内容。']);
  assert.deepEqual(imageParagraphs('a\r\n\r\nb'), ['a\r', 'b']);
  assert.deepEqual(imageParagraphs(' \n\n\t '), []);
});

test('search adoption requires original paragraph index, exact text and current complete-body hash', () => {
  const search = { sectionId: 's1', ...anchor };
  assert.equal(imageSearchCurrent(project, search), true);
  for (const change of [{ stale: true }, { paragraphIndex: 1 }, { paragraphIndex: -1 }, { paragraphExact: anchor.paragraphExact.trim() }, { contentHash: 'old' }, { contentHash: '' }, { sectionId: 'other' }]) assert.equal(imageSearchCurrent(project, { ...search, ...change }), false);
});

test('scope eligibility cannot be overridden by human confirmation', () => {
  for (const candidate of [{ status: 'recommended' }, { status: 'recommended', scopeEligible: false }, { status: 'unsuitable', scopeEligible: true }, { status: 'unknown', scopeEligible: true }]) assert.ok(imageCandidateBlock(candidate, true));
  assert.equal(imageCandidateBlock({ status: 'recommended', scopeEligible: true }), '');
  assert.ok(imageCandidateBlock({ status: 'needs_review', scopeEligible: true }));
  assert.equal(imageCandidateBlock({ status: 'needs_review', scopeEligible: true }, true), '');
});

test('quality gate failure, missing new-policy gate and unknown policies cannot be overridden manually', () => {
  const candidate = { status: 'recommended', scopeEligible: true, sourceType: 'library' };
  const options = { placement: { valid: true, status: 'recommended' }, requirePlacement: true, requireQualityGate: true, confirmLibrarySource: true };
  for (const qualityGate of [null, {}, { policy: IMAGE_QUALITY_POLICY, passed: false, reason: '图片只是相关背景，不能直接说明正文。' }, { policy: 'unknown-policy', passed: true }, { policy: IMAGE_QUALITY_POLICY, passed: 'true' }, { policy: IMAGE_QUALITY_POLICY }]) {
    const value = { ...candidate, qualityGate };
    assert.ok(imageCandidateBlock(value, true, options));
    assert.ok(imageCandidateBlock(value, true, { ...options, requireQualityGate: false }));
  }
  assert.ok(imageCandidateBlock(candidate, true, options));
  assert.equal(imageCandidateBlock(candidate, true, { ...options, requireQualityGate: false }), '');
  assert.equal(imageCandidateBlock({ ...candidate, qualityGate: { policy: IMAGE_QUALITY_POLICY, passed: true } }, true, options), '');
  assert.match(imageQualityBlock({ qualityGate: { policy: IMAGE_QUALITY_POLICY, passed: false, reason: '只能提供背景。' } }), /只能提供背景.*可以不配图/);
});

test('new chapter suggestions require qualityPassed while legacy anchors and paragraph mode stay compatible', () => {
  const search = { sectionId: 's1', ...anchor, placementMode: 'chapter', qualityPolicy: IMAGE_QUALITY_POLICY };
  const suggestion = { ...anchor, status: 'recommended', qualityPassed: true };
  const candidate = { status: 'recommended', qualityGate: { policy: IMAGE_QUALITY_POLICY, passed: true }, placementSuggestions: [suggestion] };
  assert.equal(imageCandidatePlacements(project, search, candidate)[0].valid, true);
  for (const qualityPassed of [false, undefined, 'true']) assert.equal(imageCandidatePlacements(project, search, { ...candidate, placementSuggestions: [{ ...suggestion, qualityPassed }] })[0].valid, false);
  assert.deepEqual(imageCandidatePlacements(project, search, { ...candidate, placementSuggestions: [] }), []);
  const { qualityGate, ...missingGate } = candidate;
  assert.equal(imageCandidatePlacements(project, search, missingGate)[0].valid, false);
  assert.equal(imageCandidatePlacements(project, search, { ...candidate, qualityGate: { ...qualityGate, passed: false } })[0].valid, false);
  const legacySearch = { ...search }; delete legacySearch.qualityPolicy;
  assert.equal(imageCandidatePlacements(project, legacySearch, { ...missingGate, placementSuggestions: [{ ...suggestion, qualityPassed: undefined }] })[0].valid, true);
  assert.equal(imageCandidatePlacements(project, { ...search, placementMode: 'paragraph' }, candidate)[0].valid, true);
  assert.equal(imageCandidatePlacements(project, { ...search, placementMode: 'paragraph' }, missingGate)[0].valid, false);
});

test('new quality policy exposes explicit no-suitable-image state and never invites manual bypass', () => {
  const failed = { status: 'needs_review', scopeEligible: true, rankReasons: ['人工确认即可采用。'], issues: ['请尝试指定其他段落。'], qualityGate: { policy: IMAGE_QUALITY_POLICY, passed: false, reason: '与正文不直接对应。' } };
  const search = { qualityPolicy: IMAGE_QUALITY_POLICY, candidates: [failed, { status: 'recommended' }] };
  assert.equal(imageSearchHasNoSuitableImages(search), true);
  assert.equal(imageSearchHasNoSuitableImages({ ...search, candidates: [] }), true);
  assert.equal(imageSearchHasNoSuitableImages({ candidates: [failed] }), false);
  assert.equal(imageSearchHasNoSuitableImages({ ...search, candidates: [{ qualityGate: { policy: IMAGE_QUALITY_POLICY, passed: true } }] }), false);
  const explanation = imageCandidateExplanation(failed, { reason: '随便放置。' });
  assert.match(JSON.stringify(explanation), /不直接对应.*可以不配图/);
  assert.doesNotMatch(JSON.stringify(explanation), /人工确认即可|指定其他段落|随便放置/);
});

test('chapter results use verified candidate positions rather than the compatibility paragraph', () => {
  const search = { sectionId: 's1', ...anchor, searchScope: 'library', placementMode: 'chapter' };
  const suggested = { paragraphIndex: 1, paragraphExact: '第二段内容。', contentHash: 'current-hash', status: 'recommended', reason: '对应第二段' };
  assert.equal(imageSearchCurrent(project, { ...search, paragraphExact: 'Compatibility anchor is unused' }), true);
  const placements = imageCandidatePlacements(project, search, { placementSuggestions: [suggested] });
  assert.deepEqual(placements, [{ ...suggested, valid: true }]);
  assert.deepEqual(imageCandidatePlacements(project, search, {}), []);
  assert.deepEqual(imageCandidatePlacements(project, search, { placementSuggestions: {} }), []);
  for (const change of [{ paragraphIndex: 0 }, { paragraphIndex: -1 }, { paragraphExact: 'changed' }, { contentHash: 'old' }, { status: 'unsuitable' }]) {
    assert.equal(imageCandidatePlacements(project, search, { placementSuggestions: [{ ...suggested, ...change }] })[0].valid, false);
  }
  assert.deepEqual(imageCandidatePlacements(project, { ...search, stale: true }, { placementSuggestions: [suggested] }), []);
  assert.ok(imageCandidateBlock({ status: 'recommended', scopeEligible: true }, true, { requirePlacement: true }));
});

test('library illustrations require separate source consent and strictest candidate/placement review', () => {
  const candidate = { status: 'recommended', scopeEligible: true, sourceType: 'library' };
  const placement = { valid: true, status: 'recommended' };
  const options = { placement, requirePlacement: true, confirmLibrarySource: true };
  assert.ok(imageCandidateBlock(candidate, false, options));
  assert.ok(imageCandidateBlock(candidate, true, { ...options, confirmLibrarySource: false }));
  assert.equal(imageCandidateBlock(candidate, true, options), '');
  assert.ok(imageCandidateBlock({ ...candidate, scopeEligible: false }, true, options));
  assert.ok(imageCandidateBlock({ ...candidate, status: 'unsuitable' }, true, options));
  assert.ok(imageCandidateBlock({ ...candidate, sourceType: 'reference' }, false, { ...options, placement: { ...placement, status: 'needs_review' } }));
  assert.ok(imageCandidateBlock({ ...candidate, sourceType: 'reference', status: 'needs_review' }, false, options));
});

test('old paragraph results retain their original anchor and defaults', () => {
  const search = { sectionId: 's1', ...anchor };
  assert.equal(imageSearchScope(search), 'references');
  assert.equal(imagePlacementMode(search), 'paragraph');
  const placements = imageCandidatePlacements(project, search, { status: 'recommended', placementSuggestions: [{ paragraphIndex: 1 }] });
  assert.equal(placements.length, 1);
  assert.equal(placements[0].paragraphIndex, 0);
  assert.equal(placements[0].valid, true);
});

test('result selection isolates scope and paragraph while pending/failed frozen targets replace old results', () => {
  const target = { sectionId: 's1', paragraphIndex: 0, searchScope: 'library', placementMode: 'chapter' };
  const old = { id: 'old-paragraph', kind: 'images', status: 'succeeded', candidate: { imageSearch: { sectionId: 's1', ...anchor } } };
  const chapter = { id: 'chapter', kind: 'images', status: 'succeeded', candidate: { imageSearch: { ...anchor, ...target } } };
  const otherParagraph = { id: 'p1', kind: 'images', status: 'succeeded', candidate: { imageSearch: { sectionId: 's1', ...anchor, paragraphIndex: 1 } } };
  const value = { ...project, runs: [chapter, otherParagraph, old] };
  assert.equal(imageRunForView(value, { ...target, paragraphIndex: 1 }).id, 'chapter');
  assert.equal(imageRunForView(value, { ...target, searchScope: 'references', placementMode: 'paragraph' }).id, 'old-paragraph');
  assert.equal(imageRunForView(value, { ...target, searchScope: 'references', placementMode: 'paragraph', paragraphIndex: 1 }).id, 'p1');
  assert.equal(imageRunForView(value, { ...target, searchScope: 'references' }), undefined);
  for (const status of ['queued', 'running', 'failed']) {
    const newest = { id: status, kind: 'images', status, imageTarget: target };
    assert.equal(imageRunForView({ ...value, runs: [newest, ...value.runs] }, target).id, status);
    assert.equal(imageRunForView({ ...value, runs: [newest, ...value.runs] }, { ...target, searchScope: 'references', placementMode: 'paragraph' }).id, 'old-paragraph');
  }
});

test('reading surfaces never place stale images beside changed or dirty paragraphs', () => {
  assert.equal(illustrationsAt(project, 's1', 0, anchor.paragraphExact).length, 1);
  assert.equal(illustrationsAt(project, 's1', 0, anchor.paragraphExact, true).length, 0);
  assert.equal(illustrationsAt(project, 's1', 0, anchor.paragraphExact.trim()).length, 0);
  assert.equal(illustrationsAt(project, 's1', 1, anchor.paragraphExact).length, 0);
  assert.equal(illustrationsAt({ ...project, illustrations: [{ ...figure, status: 'stale' }] }, 's1', 0, anchor.paragraphExact).length, 0);
  assert.equal(illustrationsAt({ ...project, sections: [{ ...section, contentHash: 'changed' }] }, 's1', 0, anchor.paragraphExact).length, 0);
});

test('figure numbering follows manuscript order and keeps stale placements visible in management', () => {
  const second = { ...figure, id: 'f2', sectionId: 's2', status: 'stale' };
  const value = { ...project, sections: [section, { ...section, id: 's2' }], illustrations: [second, figure] };
  assert.deepEqual(orderedIllustrations(value).map(item => item.id), ['f1', 'f2']);
  assert.equal(illustrationsAt(value, 's1', 0, anchor.paragraphExact)[0].number, 1);
});

test('coverage and issues expose business language without protocol identifiers', () => {
  assert.equal(imageBusinessText({ message: 'paragraphIndex changed' }, '请重新核对。'), '请重新核对。');
  assert.match(imageCoverageText({ sourcesSearched: 2, imagesConsidered: 8 }), /2 篇.*8 张/);
  assert.doesNotMatch(imageCoverageText({ internalBudget: 999 }), /999|internalBudget/);
  assert.match(imageCoverageText({ sourcesSearched: 2, imagesConsidered: 8 }, 'library'), /2 篇图库来源资料.*8 张/);
});

test('image explanations translate historical paragraph aliases and uncertain status into reader-facing text', () => {
  assert.equal(imageBusinessText('与p2施工场景相容，p0/p2仍需核对，状态 uncertain。'), '与第 3 段施工场景相容，第 1 段/第 3 段仍需核对，状态 待核对。');
  assert.equal(imageBusinessText({ reason: 'P1 对应统计范围，UNCERTAIN。' }), '第 2 段 对应统计范围，待核对。');
  assert.equal(imageBusinessText('型号 H100p2 未确认。'), '型号 H100p2 未确认。');
  assert.equal(imageBusinessText('placementSuggestions 缺少 paragraphExact。', '请重新查找插入位置。'), '请重新查找插入位置。');
});

test('unconfirmed image explanations hide free model claims in candidate, issue and placement text without changing frozen data', () => {
  const candidate = { status: 'needs_review', originalCaption: '(Image credit: Intel)', rankReasons: ['画面直接呈现俄亥俄州工厂。'], issues: ['工人握手庆祝首个里程碑。'], visualStatus: 'reviewed' };
  const placement = { status: 'needs_review', reason: '画面是首座工厂首个桁架。' };
  const original = JSON.stringify({ candidate, placement });
  const result = imageCandidateExplanation(candidate, placement), text = JSON.stringify(result);
  assert.doesNotMatch(text, /握手|俄亥俄|首座|首个|直接呈现/);
  assert.match(text, /仅有署名或来源/);
  assert.match(text, /对应关系尚未确认/);
  assert.match(text, /此位置仅供人工核对/);
  assert.equal(JSON.stringify({ candidate, placement }), original);
});

test('the stricter candidate or chosen position status controls explanation visibility, including old paragraph results', () => {
  const candidate = { status: 'recommended', originalCaption: '设备结构图', rankReasons: ['结构与原文明确对应。'], issues: ['请查看图例。'] };
  const placement = { status: 'recommended', reason: '本段介绍图示结构。' };
  assert.deepEqual(imageCandidateExplanation(candidate, placement), { reasons: candidate.rankReasons, issues: candidate.issues, placementReason: placement.reason });
  assert.doesNotMatch(JSON.stringify(imageCandidateExplanation(candidate, { ...placement, status: 'needs_review' })), /结构与原文明确对应|本段介绍图示结构/);
  assert.doesNotMatch(JSON.stringify(imageCandidateExplanation({ ...candidate, status: 'needs_review' }, placement)), /结构与原文明确对应|本段介绍图示结构/);
  assert.match(JSON.stringify(imageCandidateExplanation({ ...candidate, status: 'needs_review' })), /对应关系尚未确认/);
});

test('unconfirmed explanation identifies missing captions, library confirmation and unreviewed images conservatively', () => {
  const result = imageCandidateExplanation({ status: 'needs_review', sourceType: 'library', visualStatus: 'not_reviewed', originalCaption: '', issues: ['自由模型叙述'] });
  assert.match(JSON.stringify(result), /缺少原图注.*图库补充图片需要独立确认.*尚未完成有效的看图核对/);
  assert.doesNotMatch(JSON.stringify(result), /自由模型叙述/);
  const described = imageCandidateExplanation({ status: 'needs_review', originalCaption: '(Image credit: Intel)\n起重机吊装钢结构。' });
  assert.doesNotMatch(JSON.stringify(described), /仅有署名/);
  const short = imageCandidateExplanation({ status: 'needs_review', originalCaption: '图', issues: ['原图注缺少画面描述，图片身份与位置须人工确认'] });
  assert.match(JSON.stringify(short), /缺少足够的画面描述/);
});

test('library coverage totals both source pools and separates textual screening from actual vision review', () => {
  const coverage = { availableSourceCount: 1, libraryScannedSourceCount: 57, savedImageCount: 5, libraryFrozenAssetCount: 200, examinedImageCount: 190, placementMode: 'chapter', visualReviewedCount: 0, placement: { visualReviewedCandidateCount: 6 } };
  const text = imageCoverageText(coverage, 'library');
  assert.match(text, /已检索 58 篇图库来源资料/);
  assert.match(text, /检索范围共 205 张已保存图片/);
  assert.match(text, /文字检索与初筛覆盖 190 张图片/);
  assert.match(text, /实际看图核对 6 张/);
  assert.doesNotMatch(text, /实际看图核对 (0|190|205) 张/);
  assert.match(imageCoverageText({ ...coverage, placementMode: 'paragraph', visualReviewedCount: 3 }, 'library'), /实际看图核对 3 张/);
  assert.match(imageCoverageText({ ...coverage, placement: undefined }, 'library'), /看图核对数量未记录/);
  assert.match(imageCoverageText({ availableSourceCount: 1, savedImageCount: 5, examinedImageCount: 5, visualReviewedCount: 0 }), /实际看图核对 0 张/);
});

test('coverage missing fields use recorded legacy totals without fabricating library totals or vision completion', () => {
  const legacy = imageCoverageText({ availableSourceCount: 1, sourcesSearched: 58, imagesConsidered: 190 }, 'library');
  assert.match(legacy, /已检索 58 篇图库来源资料/);
  assert.match(legacy, /文字检索与初筛覆盖 190 张图片/);
  assert.match(legacy, /看图核对数量未记录/);
  assert.doesNotMatch(legacy, /检索范围共|实际看图核对/);
  const partial = imageCoverageText({ availableSourceCount: 1, libraryFrozenAssetCount: 200, visualReviewedCount: -1 }, 'library');
  assert.match(partial, /本稿参考资料已检索 1 篇/);
  assert.match(partial, /图库补充范围含 200 张已保存图片/);
  assert.doesNotMatch(partial, /已检索 1 篇图库|检索范围共|实际看图核对/);
  assert.match(imageCoverageText({ placement: { visualReviewedCandidateCount: 2 } }, 'library'), /实际看图核对 2 张/);
  assert.match(imageCoverageText({ libraryScannedSourceCount: 4, savedImageCount: 3 }, 'library'), /图库补充资料已检索 4 篇.*本稿参考资料含 3 张/);
  assert.match(imageCoverageText({ sourcesSearched: 0, imagesConsidered: 0, visualReviewedCount: 0 }), /已检索 0 篇.*覆盖 0 张.*看图核对 0 张/);
});

test('six-step progress requires explicit illustration completion or skip and invalidates stale work', () => {
  const result = deriveWritingProgress({ project, savedProject: project });
  assert.equal(result.total, 6);
  assert.equal(result.currentId, 'images');
  assert.equal(result.steps.find(item => item.id === 'images').complete, false);
  for (const status of ['complete', 'skipped']) {
    const saved = { ...project, illustrations: status === 'skipped' ? [] : project.illustrations, illustrationReview: { status } };
    assert.equal(deriveWritingProgress({ project: saved, savedProject: saved }).steps.find(item => item.id === 'images').complete, true);
    // Draft input does not undo a saved milestone; only an adopted saved
    // change/stale image invalidates the completed illustration step.
    assert.equal(deriveWritingProgress({ project: saved, savedProject: saved, dirty: true }).steps.find(item => item.id === 'images').complete, true);
  }
  const stale = { ...project, illustrationReview: { status: 'complete' }, illustrations: [{ ...figure, status: 'stale' }] };
  assert.equal(deriveWritingProgress({ project: stale, savedProject: stale }).steps.find(item => item.id === 'images').complete, false);
  assert.equal(deriveWritingProgress({ project: { ...project, runs: [{ kind: 'images', status: 'running' }] }, savedProject: project }).currentId, 'images');
});
