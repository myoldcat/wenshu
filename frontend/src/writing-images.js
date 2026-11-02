// Match the service's paragraph anchors without rewriting source text or citations.
export const imageParagraphs = content => String(content || '').split(/\n\s*\n/).filter(text => text.trim());

export const imageSearchScope = search => search?.searchScope === 'library' ? 'library' : 'references';
export const imagePlacementMode = search => search?.placementMode === 'chapter' ? 'chapter' : 'paragraph';
export const isLibraryImage = candidate => candidate?.sourceType === 'library';
export const IMAGE_QUALITY_POLICY = 'writing-image-quality-1';

export function imageQualityBlock(candidate, requireGate = false) {
  const present = !!candidate && Object.hasOwn(candidate, 'qualityGate');
  if (!present && !requireGate) return '';
  const gate = candidate?.qualityGate;
  if (!gate || gate.policy !== IMAGE_QUALITY_POLICY || typeof gate.passed !== 'boolean') return '这张图片尚未完成当前配图要求的检查，不能加入文稿。没有合适图片可以不配图。';
  if (!gate.passed) return `${imageBusinessText(gate.reason, '这张图片未能直接对应正文，暂不采用。')} 没有合适图片可以不配图。`;
  return '';
}

export function imageSearchHasNoSuitableImages(search) {
  return search?.qualityPolicy === IMAGE_QUALITY_POLICY && !(search.candidates || []).some(candidate => !imageQualityBlock(candidate, true));
}

export function imageRunTarget(run) {
  return run?.candidate?.imageSearch || run?.imageTarget || null;
}

export function imageRunForView(project, view) {
  return (project.runs || []).find(run => {
    if (run.kind !== 'images') return false;
    const target = imageRunTarget(run);
    return target?.sectionId === view.sectionId && imageSearchScope(target) === view.searchScope
      && imagePlacementMode(target) === view.placementMode
      && (view.placementMode === 'chapter' || target.paragraphIndex === view.paragraphIndex);
  });
}

export function imageSearchCurrent(project, search) {
  const section = project.sections?.find(item => item.id === search?.sectionId);
  if (!section || search?.stale || !search?.contentHash || section.contentHash !== search.contentHash) return false;
  return imagePlacementMode(search) === 'chapter' || (Number.isInteger(search.paragraphIndex) && search.paragraphIndex >= 0
    && imageParagraphs(section.content)[search.paragraphIndex] === search.paragraphExact);
}

export function imageCandidatePlacements(project, search, candidate) {
  if (!imageSearchCurrent(project, search)) return [];
  const section = project.sections.find(item => item.id === search.sectionId), paragraphs = imageParagraphs(section.content);
  const chapter = imagePlacementMode(search) === 'chapter', qualityRequired = search.qualityPolicy === IMAGE_QUALITY_POLICY || candidate?.qualityGate?.policy === IMAGE_QUALITY_POLICY;
  const qualityAllowed = !imageQualityBlock(candidate, search.qualityPolicy === IMAGE_QUALITY_POLICY);
  const suggestions = chapter ? (Array.isArray(candidate?.placementSuggestions) ? candidate.placementSuggestions : [])
    : [{ paragraphIndex: search.paragraphIndex, paragraphExact: search.paragraphExact, contentHash: search.contentHash,
      status: candidate?.status, reason: '放在本次指定的段落之后。' }];
  return suggestions.map(suggestion => ({ ...suggestion, valid: Number.isInteger(suggestion.paragraphIndex) && suggestion.paragraphIndex >= 0
    && paragraphs[suggestion.paragraphIndex] === suggestion.paragraphExact && suggestion.contentHash === section.contentHash
    && ['recommended', 'needs_review'].includes(suggestion.status) && qualityAllowed
    && (!chapter || !qualityRequired || suggestion.qualityPassed === true) }));
}

export function imageCandidateBlock(candidate, reviewed = false, { placement, requirePlacement = false, confirmLibrarySource = false, requireQualityGate = false } = {}) {
  const qualityBlock = imageQualityBlock(candidate, requireQualityGate);
  if (qualityBlock) return qualityBlock;
  if (candidate?.scopeEligible !== true) return '这张图片超出本次可用的配图范围。';
  if (candidate.status === 'unsuitable') return '这张图片不适合当前段落，请选择其他图片。';
  if (!['recommended', 'needs_review'].includes(candidate.status)) return '这张图片尚未完成适用性检查。';
  if (requirePlacement && !placement?.valid) return '尚未找到可靠且直接对应正文的插入位置。没有合适图片可以不配图。';
  if ((candidate.status === 'needs_review' || placement?.status === 'needs_review' || isLibraryImage(candidate)) && !reviewed) return '请查看原图和来源，核对图片与所选段落的关系后确认。';
  if (isLibraryImage(candidate) && !confirmLibrarySource) return '请确认图库补充图片仅作配图，不作为正文依据。';
  return '';
}

export function orderedIllustrations(project) {
  const order = new Map((project.sections || []).map((section, index) => [section.id, index]));
  return [...(project.illustrations || [])].sort((a, b) => (order.get(a.sectionId) ?? Infinity) - (order.get(b.sectionId) ?? Infinity)
    || (a.anchor?.paragraphIndex ?? Infinity) - (b.anchor?.paragraphIndex ?? Infinity));
}

export function illustrationsAt(project, sectionId, paragraphIndex, paragraphExact, dirty = false) {
  if (dirty) return [];
  const section = project.sections?.find(item => item.id === sectionId);
  if (!section) return [];
  return orderedIllustrations(project).map((figure, index) => ({ ...figure, number: index + 1 })).filter(figure =>
    figure.sectionId === sectionId && figure.status === 'accepted'
    && figure.anchor?.paragraphIndex === paragraphIndex && figure.anchor.paragraphExact === paragraphExact
    && !!figure.anchor.contentHash && figure.anchor.contentHash === section.contentHash);
}

export function imageBusinessText(value, fallback = '') {
  const text = typeof value === 'string' ? value : value?.message || value?.reason || value?.description || '';
  if (!text || /(?:source|revision|section|asset|run)_[a-z0-9]+|scopeEligible|paragraphIndex|paragraphExact|placementSuggestions|contentHash|traceback/i.test(text)) return fallback;
  return text.replace(/\bp(\d+)\b/gi, (_, index) => `第 ${Number(index) + 1} 段`).replace(/\buncertain\b/gi, '待核对');
}

// Unconfirmed model descriptions are retained in the frozen run, not presented as evidence of a match.
export function imageCandidateExplanation(candidate, placement, { requireQualityGate = false } = {}) {
  const qualityBlock = imageQualityBlock(candidate, requireQualityGate);
  if (qualityBlock) return { reasons: [], issues: [qualityBlock], placementReason: '没有可靠且直接对应正文的插入位置，可以不配图。' };
  if (candidate?.status !== 'needs_review' && placement?.status !== 'needs_review') return {
    reasons: (candidate?.rankReasons || []).map(value => imageBusinessText(value)).filter(Boolean),
    issues: (candidate?.issues || []).map(value => imageBusinessText(value, '这张图片仍有内容需要结合来源人工核对。')).filter(Boolean),
    placementReason: imageBusinessText(placement?.reason, '请结合上下文核对这个位置。'),
  };
  const caption = String(candidate?.originalCaption || '').trim();
  const lines = caption.split(/\n/).map(line => line.trim().replace(/^[()（）\[\]\s]+|[()（）\[\]\s]+$/g, '')).filter(Boolean);
  const creditLine = /^(?:(?:(?:image|photo(?:graph)?|picture|illustration)\s+)?(?:credit|source)s?\s*[:：]|(?:image|photo(?:graph)?|picture|illustration)\s*[:：]|(?:[图圖]片[来來]源|照片[来來]源|[图圖]像[来來]源|[图圖]源|[图圖]片署名|[摄攝]影|[来來]源)\s*[:：]|courtesy\s+of\b|copyright\b|©)/i;
  const issues = [];
  if (!caption) issues.push('缺少原图注，不能据此确认画面中的对象、地点、时间或事件。');
  else if (lines.length && lines.every(line => creditLine.test(line))) issues.push('原图注仅有署名或来源，未描述画面；对象、地点、时间及事件仍需人工核对。');
  else if ((candidate.issues || []).includes('原图注缺少画面描述，图片身份与位置须人工确认')) issues.push('原图注缺少足够的画面描述，不能仅据此确认图片身份或事件。');
  if (isLibraryImage(candidate)) issues.push('图库补充图片需要独立确认，仅作配图，不作为正文依据。');
  if (candidate.visualStatus === 'not_reviewed') issues.push('尚未完成有效的看图核对，请查看原图和来源后人工确认。');
  return {
    reasons: ['图片与所选段落的对应关系尚未确认，需人工核对。'],
    issues,
    placementReason: '此位置仅供人工核对，尚未确认图片对应本段；请确认对象、地点、时间与事件一致后再选择。',
  };
}

export function imageCoverageText(coverage, scope = 'references') {
  const library = scope === 'library', range = library ? '图库' : '本稿参考资料';
  if (typeof coverage === 'string') return imageBusinessText(coverage, `已检索${range}中的图片。`);
  if (!coverage || typeof coverage !== 'object') return `检索${range}中的图片，图片仍需结合来源核对。`;
  const readCount = (value, ...keys) => keys.map(key => value?.[key]).find(number => Number.isInteger(number) && number >= 0);
  const count = (...keys) => readCount(coverage, ...keys);
  const referenceSources = count('availableSourceCount'), librarySources = count('libraryScannedSourceCount');
  const aggregateSources = count('sourcesSearched', 'searchedSources', 'sourceCount', 'sources');
  const sources = library ? (referenceSources !== undefined && librarySources !== undefined ? referenceSources + librarySources : aggregateSources) : referenceSources ?? aggregateSources;
  const referenceImages = count('savedImageCount'), libraryImages = count('libraryFrozenAssetCount');
  const savedImages = library ? (referenceImages !== undefined && libraryImages !== undefined ? referenceImages + libraryImages : undefined) : referenceImages;
  const images = count('examinedImageCount', 'imagesConsidered', 'searchedImages', 'imageCount', 'images', 'totalImages');
  const chapter = coverage.placementMode === 'chapter' || (coverage.placementMode !== 'paragraph' && !!coverage.placement);
  const visual = chapter ? readCount(coverage.placement, 'visualReviewedCandidateCount') : count('visualReviewedCount');
  const parts = [];
  if (sources !== undefined) parts.push(`已检索 ${sources} 篇${library ? '图库来源资料' : '参考资料'}的图片信息`);
  else if (library) {
    if (referenceSources !== undefined) parts.push(`本稿参考资料已检索 ${referenceSources} 篇`);
    if (librarySources !== undefined) parts.push(`图库补充资料已检索 ${librarySources} 篇`);
  }
  if (savedImages !== undefined) parts.push(`检索范围共 ${savedImages} 张已保存图片`);
  else if (library) {
    if (referenceImages !== undefined) parts.push(`本稿参考资料含 ${referenceImages} 张已保存图片`);
    if (libraryImages !== undefined) parts.push(`图库补充范围含 ${libraryImages} 张已保存图片`);
  }
  if (images !== undefined) parts.push(`文字检索与初筛覆盖 ${images} 张图片`);
  if (visual !== undefined) parts.push(`实际看图核对 ${visual} 张`);
  else parts.push('看图核对数量未记录，请逐张人工核对');
  if (coverage.unsavedImageReferences > 0) parts.push(`${coverage.unsavedImageReferences} 处图片尚未保存在图库中`);
  if (coverage.contextUnavailableSourceCount > 0) parts.push(`部分资料缺少图片附近的原文，需人工核对`);
  if (coverage.truncated) parts.push('本次只检查了部分图片，可以调整关键词继续查找');
  return parts.length ? `${parts.join('，')}。` : `已检索${range}中的图片；未发现合适图片时，可以保留纯文字。`;
}
