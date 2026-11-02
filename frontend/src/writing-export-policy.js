// Keep this list aligned with server/writing_review.py. Evidence and content
// issues still need explicit draft consent, even when their severity is warning.
const generalReminderCodes = new Set([
  'section_word_count', 'total_word_count', 'source_new_revision',
  'image_caption_missing', 'images_unconfirmed',
]);

export function reviewRequiresDraft(review) {
  if (!review || typeof review !== 'object') return true;
  // A saved skip is an explicit workflow choice, not issue acknowledgement or
  // model verification. Callers still fence it to the current manuscript.
  if (review.disposition?.status === 'skipped') return false;
  if (!review.authorConfirmed && review.citationReviewCompleted === false) return true;
  const issues = review.issues === undefined ? [] : review.issues;
  if (!Array.isArray(issues)) return true;
  return issues.some(issue => !issue || !issue.confirmed && !issue.acknowledged && (issue.severity !== 'warning' || !generalReminderCodes.has(issue.code)));
}

export function exportedAsDraft(review, receipt) {
  // Older receipts may record the previous policy for this same saved skip.
  if (review?.disposition?.status === 'skipped') return false;
  return reviewRequiresDraft(review) || (typeof receipt?.requiresDraftConsent === 'boolean'
    ? receipt.requiresDraftConsent : receipt?.reviewStatus === 'needs_attention');
}
