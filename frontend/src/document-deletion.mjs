export function deletionConfirmation(impact, confirmed) {
  if (!impact?.sourceId || !impact.revisionId || !impact.impactToken || impact.canDelete !== true) return null;
  if (impact.hasReferences && confirmed !== true) return null;
  return { expectedRevisionId: impact.revisionId, impactToken: impact.impactToken, confirmReferences: confirmed === true };
}

export function referenceDescription(reference) {
  const parts = [];
  if (reference.textReferences > 0) parts.push(`文字引用 ${reference.textReferences} 处`);
  if (reference.imageReferences > 0) parts.push(`图片引用 ${reference.imageReferences} 处`);
  if (reference.selectedSource) parts.push('已选为写作资料');
  return parts.join(' · ') || '已关联此资料';
}
