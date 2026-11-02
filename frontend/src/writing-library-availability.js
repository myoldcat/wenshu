// Availability belongs to the current library, never to saved evidence scope.
export function writingSourceStatus(project, source) {
  return project?.libraryAvailability?.sources?.find(item => item.sourceId === source?.sourceId
    && item.revisionId === source?.revisionId)?.status || 'available';
}

export function writingAssetStatus(project, figure) {
  return project?.libraryAvailability?.assets?.find(item => item.assetId === figure?.assetId
    && item.sourceId === figure?.sourceId && item.revisionId === figure?.revisionId)?.status
    || writingSourceStatus(project, figure);
}

export const writingSourceNotice = status => status === 'deleted'
  ? '引用文档已删除；保留写作时保存的摘录，无法打开原文。'
  : status === 'unavailable' ? '引用文档的固定版本暂不可用；保留写作时保存的摘录。' : '';

export function writingFigureForView(project, figure) {
  const sourceStatus = writingSourceStatus(project, figure), assetStatus = writingAssetStatus(project, figure);
  return { ...figure, sourceStatus, assetStatus,
    unavailableMessage: assetStatus === 'deleted' ? '图片来源文档已删除，原图已不可用。'
      : assetStatus === 'unavailable' ? '原始图片或固定来源版本已不可用。' : '' };
}
