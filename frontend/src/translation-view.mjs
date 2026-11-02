const chineseLanguages = /^(?:zh(?:[-_].*)?|cmn(?:[-_].*)?|chinese|中文|汉语|簡體中文|简体中文|繁體中文|繁体中文)$/i;

export function isChineseDocument(doc = {}) {
  if (doc.needsTranslation === true) return false;
  return chineseLanguages.test(String(doc.languageCode || doc.language || '').trim());
}

export function documentNeedsTranslation(doc = {}) {
  if (typeof doc.needsTranslation === 'boolean') return doc.needsTranslation;
  return !isChineseDocument(doc);
}

export function documentLanguageName(doc = {}) {
  if (isChineseDocument(doc)) return '中文';
  const explicit = String(doc.languageLabel || (typeof doc.languageTag === 'string' ? doc.languageTag.replace(/^语言[：:]\s*/, '') : '')).trim();
  const language = String(doc.languageCode || doc.language || '').trim().toLowerCase().split(/[-_]/)[0];
  if (language === 'mixed' || (chineseLanguages.test(language) && doc.needsTranslation === true)) return '多语言';
  if (['und', 'unknown', 'other', 'foreign'].includes(language)) return documentNeedsTranslation(doc) ? '外语（待识别）' : '未确定';
  const labels = { en: '英语', eng: '英语', ja: '日语', jpn: '日语', ko: '韩语', kor: '韩语', fr: '法语', fra: '法语', de: '德语', deu: '德语', es: '西班牙语', spa: '西班牙语', ru: '俄语', rus: '俄语', ar: '阿拉伯语', ara: '阿拉伯语', pt: '葡萄牙语', it: '意大利语', hi: '印地语', th: '泰语', vi: '越南语', mixed: '多语言' };
  if (labels[language]) return labels[language];
  if (['多语言', '多种语言', '多语混合', 'mixed'].includes(explicit)) return '多语言';
  if (explicit && !['未知', '未识别', '未确定', '其他外语', 'und', 'unknown', 'other', 'foreign', '外语'].includes(explicit)) return explicit;
  return language || explicit || doc.needsTranslation ? (documentNeedsTranslation(doc) ? '外语（待识别）' : '未确定') : '';
}

export function originalLanguageBadge(doc = {}) {
  return documentNeedsTranslation(doc) ? documentLanguageName(doc) : '';
}

export function originalLanguageLabel(doc = {}) {
  if (isChineseDocument(doc)) return '中文原文';
  return `原文：${documentLanguageName(doc) || '外语（待识别）'}`;
}

export function documentLanguageTag(doc = {}) {
  const name = documentLanguageName(doc);
  return name ? `语言：${name}` : '';
}

export function currentReaderDerivedMetadata(metadata, detail) {
  if (!metadata?.revisionId || metadata.revisionId !== detail?.revisionId) return {};
  const fields = ['title', 'originalTitle', 'summary', 'summarySource', 'summaryLanguage', 'languageCode', 'languageLabel', 'languageTag', 'needsTranslation', 'articleType', 'articleTypeLabel', 'articleTypeManual'];
  return Object.fromEntries(fields.filter(key => Object.hasOwn(metadata, key)).map(key => [key, metadata[key]]));
}

export function hasChineseTranslation(run) {
  return !!run?.result?.segments?.some(segment => typeof segment.text === 'string' && segment.text.trim());
}

// The stored preference is intentionally independent of the polling result.
// Only the initial "auto" choice follows a newly available translation.
export function readingView(preference = 'auto', doc = {}, run) {
  if (!documentNeedsTranslation(doc)) return 'original';
  const available = hasChineseTranslation(run);
  if (preference === 'auto') return available ? 'translation' : 'original';
  if (preference === 'original') return 'original';
  if (preference === 'compare' && !isChineseDocument(doc)) return 'compare';
  return available ? 'translation' : 'original';
}

export function translationNotice(doc, processing = {}, preferences = {}) {
  if (!documentNeedsTranslation(doc)) return '';
  const run = processing.ai?.translation;
  if (hasChineseTranslation(run)) return '';
  if (processing.loading) return '正在读取中文译文，暂显示原文。';
  if (processing.error) return '中文译文读取失败，暂显示原文；可在侧栏重新读取。';
  if (run?.status === 'queued') return '中文翻译已排队，暂显示原文。';
  if (run?.status === 'running') return '正在翻译为中文，暂显示原文。';
  if (run?.status === 'failed') return '中文翻译失败，暂显示原文；可重试。';
  if (run?.status === 'cancelled') return '中文翻译已取消，暂显示原文；可重试。';
  if (!preferences.translationModelId) return '尚无中文译文，暂显示原文；请先在系统设置中配置默认模型。';
  if (preferences.autoTranslate === false) return '自动翻译已关闭，暂显示原文。';
  return '尚无中文译文，暂显示原文。';
}

export function readingTitles(doc = {}, run, view = 'translation') {
  const original = doc.originalTitle || doc.title || '';
  if (!documentNeedsTranslation(doc)) return { title: original, original: '' };
  const chinese = run?.result?.translatedTitle || doc.title || original;
  return { title: view === 'original' ? original : chinese, original: view === 'compare' && original !== chinese ? original : '' };
}

export function documentExcerptLabel(doc = {}) {
  if (doc.snippet) return doc.matchLanguage === 'zh-CN' ? '全文检索命中的中文内容' : '全文检索命中的原文片段';
  if (doc.summarySource === 'ai_summary') return 'AI 中文摘要';
  if (doc.summarySource === 'translation') return '中文译文摘录';
  return '原文摘录';
}
