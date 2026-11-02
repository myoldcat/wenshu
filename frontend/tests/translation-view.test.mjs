import assert from 'node:assert/strict';
import test from 'node:test';
import { currentReaderDerivedMetadata, documentExcerptLabel, documentLanguageName, documentLanguageTag, documentNeedsTranslation, hasChineseTranslation, isChineseDocument, originalLanguageBadge, originalLanguageLabel, readingTitles, readingView, translationNotice } from '../src/translation-view.mjs';

const foreign = { language: 'en', needsTranslation: true, title: 'Source title', originalTitle: 'Source title' };
const translated = { status: 'succeeded', result: { translatedTitle: '中文标题', segments: [{ blockId: 'block-1', text: '已保存的中文译文。' }] } };
const configured = { translationModelId: 'model-1', autoTranslate: true };

test('initial reading uses Chinese when available and preserves native Chinese without an AI output', () => {
  assert.equal(readingView('auto', foreign, null), 'original');
  assert.equal(readingView('auto', foreign, translated), 'translation');
  for (const language of ['zh', 'zh-CN', 'zh-Hant', '中文']) {
    assert.equal(isChineseDocument({ language }), true);
    assert.equal(readingView('auto', { language }, null), 'original');
    assert.equal(translationNotice({ language }, {}, configured), '');
  }
  assert.equal(isChineseDocument({ language: 'ja', needsTranslation: true }), false);
  assert.equal(isChineseDocument({ language: 'zh', needsTranslation: true }), false);
  assert.equal(isChineseDocument({ language: 'mixed' }), false);
  assert.equal(hasChineseTranslation({ result: { segments: [] } }), false);
  assert.equal(hasChineseTranslation({ result: { segments: [{ text: ' ' }] } }), false);
});

test('explicit original and compare choices survive translation polling and regeneration', () => {
  for (const run of [translated, { ...translated, status: 'running', previousSuccess: true }, { ...translated, status: 'failed', previousSuccess: true }]) {
    assert.equal(readingView('original', foreign, run), 'original');
    assert.equal(readingView('compare', foreign, run), 'compare');
    assert.equal(translationNotice(foreign, { ai: { translation: run } }, configured), '');
  }
});

test('missing translations expose actual processing state instead of claiming Chinese output', () => {
  const notice = status => translationNotice(foreign, { ai: { translation: { status } } }, configured);
  assert.match(notice('queued'), /已排队/);
  assert.match(notice('running'), /正在翻译/);
  assert.match(notice('failed'), /失败.*重试/);
  assert.match(notice('cancelled'), /取消.*重试/);
  assert.match(translationNotice(foreign, {}, { ...configured, autoTranslate: false }), /自动翻译已关闭/);
  assert.match(translationNotice(foreign, {}, { autoTranslate: true }), /配置默认模型/);
  assert.match(translationNotice(foreign, { error: '无法连接服务' }, configured), /读取失败/);
});

test('reading titles retain the original and accept the newly polled Chinese title', () => {
  assert.deepEqual(readingTitles(foreign, translated, 'translation'), { title: '中文标题', original: '' });
  assert.deepEqual(readingTitles(foreign, translated, 'original'), { title: 'Source title', original: '' });
  assert.deepEqual(readingTitles(foreign, translated, 'compare'), { title: '中文标题', original: 'Source title' });
  assert.deepEqual(readingTitles({ title: '中文原文' }, null, 'compare'), { title: '中文原文', original: '' });
});

test('source and excerpt labels describe non-English originals and Chinese search hits accurately', () => {
  assert.equal(originalLanguageLabel({ language: 'fr' }), '原文：法语');
  assert.equal(originalLanguageLabel({ language: 'ja' }), '原文：日语');
  assert.equal(originalLanguageLabel({ language: 'mixed' }), '原文：多语言');
  assert.equal(originalLanguageLabel({ language: 'unknown' }), '原文：外语（待识别）');
  assert.equal(documentExcerptLabel({ summarySource: 'translation' }), '中文译文摘录');
  assert.equal(documentExcerptLabel({ summarySource: 'ai_summary' }), 'AI 中文摘要');
  assert.equal(documentExcerptLabel({ snippet: '关键词', matchLanguage: 'zh-CN' }), '全文检索命中的中文内容');
  assert.equal(documentExcerptLabel({ snippet: 'keyword', matchLanguage: 'en' }), '全文检索命中的原文片段');
});

test('Chinese originals ignore obsolete translation output and need no language badge', () => {
  const doc = { languageCode: 'zh', languageLabel: '中文', needsTranslation: false, originalTitle: '中文原题', title: '旧译标题' };
  for (const choice of ['auto', 'translation', 'original', 'compare']) assert.equal(readingView(choice, doc, translated), 'original');
  assert.deepEqual(readingTitles(doc, translated, 'translation'), { title: '中文原题', original: '' });
  assert.equal(originalLanguageBadge(doc), '');
  assert.equal(documentLanguageTag(doc), '语言：中文');
  assert.equal(readingView('auto', { ...doc, needsTranslation: true, languageCode: 'mixed', languageLabel: '多语言' }, translated), 'translation');
});

test('language badges prioritize identified source language and never infer English from unknown foreign text', () => {
  assert.equal(originalLanguageBadge({ language: 'en', languageCode: 'ja', languageLabel: '日语', needsTranslation: true, title: '已翻译的标题' }), '日语');
  assert.equal(documentLanguageName({ language: 'en', languageCode: 'und', needsTranslation: true }), '外语（待识别）');
  assert.equal(originalLanguageBadge({ languageCode: 'other', needsTranslation: true }), '外语（待识别）');
  assert.equal(originalLanguageBadge({ languageCode: 'mixed', needsTranslation: true }), '多语言');
  assert.equal(originalLanguageBadge({ languageTag: '语言：韩语', needsTranslation: true }), '韩语');
  assert.equal(documentLanguageTag({}), '');
  assert.equal(originalLanguageBadge({ title: 'An English-looking title without registered language' }), '');
});

test('classification metadata refreshes a current reader but cannot change a historical source version', () => {
  const oldDetail = { id: 'doc-1', revisionId: 'revision-1', languageCode: 'und', languageLabel: '外语（待识别）' };
  const metadata = { id: 'doc-1', revisionId: 'revision-1', languageCode: 'fr', languageLabel: '法语', languageTag: '语言：法语', needsTranslation: true, title: '中文标题' };
  assert.equal(currentReaderDerivedMetadata(metadata, oldDetail).languageLabel, '法语');
  assert.equal(currentReaderDerivedMetadata(metadata, oldDetail).title, '中文标题');
  assert.deepEqual(currentReaderDerivedMetadata({ ...metadata, revisionId: 'revision-2' }, oldDetail), {});
  assert.deepEqual(currentReaderDerivedMetadata(metadata, null), {});
});

test('actual service language labels normalize unknown and mixed source metadata', () => {
  for (const [languageCode, languageLabel] of [['und', '未确定'], ['other', '其他外语'], ['foreign', '其他外语']]) {
    const doc = { language: 'en', languageCode, languageLabel, languageTag: `语言：${languageLabel}`, needsTranslation: true };
    assert.equal(documentLanguageName(doc), '外语（待识别）');
    assert.equal(originalLanguageBadge(doc), '外语（待识别）');
    assert.equal(documentLanguageTag(doc), '语言：外语（待识别）');
  }
  const mixed = { language: 'zh', languageCode: 'mixed', languageLabel: '多语混合', languageTag: '语言：多语混合', needsTranslation: true };
  assert.equal(documentLanguageName(mixed), '多语言');
  assert.equal(documentNeedsTranslation(mixed), true);
  assert.equal(originalLanguageBadge(mixed), '多语言');
});

test('no translation required does not misidentify numeric content as Chinese', () => {
  const numeric = { language: 'en', languageCode: 'und', languageLabel: '未确定', languageTag: '语言：未确定', needsTranslation: false };
  assert.equal(isChineseDocument(numeric), false);
  assert.equal(documentNeedsTranslation(numeric), false);
  assert.equal(documentLanguageName(numeric), '未确定');
  assert.equal(documentLanguageTag(numeric), '语言：未确定');
  assert.equal(originalLanguageBadge(numeric), '');
  assert.equal(readingView('translation', numeric, translated), 'original');
  assert.equal(translationNotice(numeric, {}, configured), '');
  const chinese = { ...numeric, languageCode: 'zh', languageLabel: '中文', languageTag: '语言：中文' };
  assert.equal(isChineseDocument(chinese), true);
  assert.equal(documentNeedsTranslation(chinese), false);
  assert.equal(originalLanguageBadge(chinese), '');
  assert.equal(documentLanguageName(chinese), '中文');
  const foreignTitle = { ...chinese, needsTranslation: true };
  assert.equal(documentNeedsTranslation(foreignTitle), true);
  assert.equal(originalLanguageBadge(foreignTitle), '多语言');
});

test('article type metadata refreshes only the same original revision', () => {
  const metadata = {revisionId: 'current', articleType: 'opinion', articleTypeLabel: '观点类', articleTypeManual: true};
  assert.deepEqual(currentReaderDerivedMetadata(metadata, {revisionId: 'current'}), {
    articleType: 'opinion', articleTypeLabel: '观点类', articleTypeManual: true,
  });
  assert.deepEqual(currentReaderDerivedMetadata(metadata, {revisionId: 'historical', articleType: 'fact'}), {});
});
