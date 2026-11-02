import { writingBriefFingerprint, writingMaterialScopeFingerprint, writingOutlineFingerprint,
  writingSemanticEqual } from './writing-semantic.js';

const fields = project => ({ ...(typeof project?.brief === 'object' ? project.brief : {}), ...project });

// This is an action-time decision, not a migration of a saved workflow choice.
// Historical explicit whole-document workflows keep their existing meaning.
export function usesDocumentWriting(project = {}) {
  const value = fields(project), target = Number(value.targetWords ?? 6000);
  if (value.automation !== 'automatic' && Number.isFinite(target) && target > 0 && target < 10000) {
    return value.interactiveGeneration !== 'chapter';
  }
  return value.workflow === 'oneshot';
}

export const hasChapterConfirmations = project => project?.chapterConfirmationPolicy === 'chapter-confirmation-v1';

// Only saved server confirmation counts. A local edit temporarily hides it;
// editing another chapter or browsing never changes a saved confirmation.
export function chapterWritingConfirmed(project, savedProject, sectionId) {
  if (!hasChapterConfirmations(project) || !hasChapterConfirmations(savedProject)
    || !project.id || project.id !== savedProject.id) return false;
  const section = project.sections?.find(value => value.id === sectionId);
  const saved = savedProject.sections?.find(value => value.id === sectionId);
  if (!section?.content?.trim() || saved?.chapterConfirmed !== true) return false;
  return section.content === saved.content && writingSemanticEqual(section.evidence, saved.evidence)
    && writingBriefFingerprint(project) === writingBriefFingerprint(savedProject)
    && writingMaterialScopeFingerprint(project) === writingMaterialScopeFingerprint(savedProject)
    && writingOutlineFingerprint({ sections: [section] }) === writingOutlineFingerprint({ sections: [saved] });
}
