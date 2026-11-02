import { deriveAutomaticProgress } from './writing-automatic.js';
import { deriveWritingProgress } from './writing-progress.js';

// Browsing follows saved milestones; having text alone does not confirm a step.
export function writingBodyEntry(options = {}) {
  const { project = {}, savedProject, dirty = false } = options;
  const saved = savedProject?.id === project.id && project.id ? savedProject : project.id && !dirty ? project : null;
  const sections = saved?.sections || [];
  if (!sections.length || sections.some(section => !section.content?.trim())) return 'editor';
  const progress = deriveAutomaticProgress(project.automatic, project) || deriveWritingProgress(options);
  return progress.steps.some(step => step.id === 'body' && step.complete) ? 'preview' : 'editor';
}
