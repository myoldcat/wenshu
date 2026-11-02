import { imageParagraphs, orderedIllustrations } from './writing-images.js';
import { exportedAsDraft } from './writing-export-policy.js';
import { automaticFinished, automaticResultLabel } from './writing-automatic.js';

const titleOrder = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' });
const beijingTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const timestamp = value => typeof value === 'string' && value.trim() && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
const title = project => project.title || project.topic || '';
const tieOrder = (left, right) => titleOrder.compare(title(left), title(right)) || String(left.id || '').localeCompare(String(right.id || ''), 'en');

export function sortWritingProjects(projects, order = 'updatedAt') {
  return [...projects].sort((left, right) => {
    if (order === 'title') return tieOrder(left, right);
    const field = order === 'createdAt' ? 'createdAt' : 'updatedAt';
    const a = timestamp(left[field]), b = timestamp(right[field]);
    // Missing historical dates stay at the end rather than borrowing another
    // timestamp; equal dates have a deterministic title/identity tie-break.
    if (a === null || b === null) return a === b ? tieOrder(left, right) : a === null ? 1 : -1;
    return b - a || tieOrder(left, right);
  });
}

export function formatWritingProjectTime(value, { compact = false } = {}) {
  const time = timestamp(value);
  if (time === null) return '未记录';
  const parts = Object.fromEntries(beijingTime.formatToParts(new Date(time)).map(part => [part.type, part.value]));
  if (compact) return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  return `${parts.year}年${parts.month}月${parts.day}日 ${parts.hour}:${parts.minute}`;
}

// Only an adopted, still-anchored illustration represents this manuscript.
export function writingProjectCardImage(project) {
  return orderedIllustrations(project).find(figure => {
    if (figure.status !== 'accepted' || !figure.reviewed || !figure.url) return false;
    const section = project.sections?.find(item => item.id === figure.sectionId);
    const anchor = figure.anchor;
    return !!section && !!anchor && Number.isInteger(anchor.paragraphIndex) && anchor.paragraphIndex >= 0
      && !!anchor.paragraphExact && imageParagraphs(section.content)[anchor.paragraphIndex] === anchor.paragraphExact
      && (!section.contentHash || section.contentHash === anchor.contentHash);
  }) || null;
}

export function compactWritingTag(value, limit = 8) {
  const characters = Array.from(String(value || '').trim());
  return characters.length > limit ? `${characters.slice(0, limit - 1).join('')}…` : characters.join('');
}

const sixStep = project => (project.workflowVersion || project.brief?.workflowVersion) === 'writing-six-step-v1';
const reviewDone = project => { const report = project.review; return !!(report && report.version === project.version && (sixStep(project) ? report.citationReviewCompleted === true || report.disposition?.status === 'skipped' || report.authorConfirmed === true : report.wordCounts && report.citationReviewCompleted !== false)); };
const cardStageIds = Object.freeze(['brief', 'outline', 'body', 'images', 'review']);
const sixStepCardStageIds = Object.freeze(['brief', 'materials', 'outline', 'body', 'images', 'review']);
const cardStageWeights = Object.freeze([10, 10, 40, 20, 20]);
const sixStepCardStageWeights = Object.freeze([5, 5, 10, 40, 20, 20]);

export function writingProjectPhase(project) {
  const sections = project.sections || [];
  if (!sections.length) {
    if (sixStep(project) && project.id && (project.topic || project.brief?.topic)?.trim()) return project.materialSelection?.confirmed ? { id: 'outline', label: '纲要待生成' } : { id: 'materials', label: '资料待筛选' };
    return { id: 'brief', label: sixStep(project) ? '主题设定' : '写作准备' };
  }
  if (!project.outlineConfirmed) return { id: 'outline', label: '纲要待确认' };
  if (sections.some(section => !section.content?.trim())) return { id: 'body', label: '正文写作' };
  if (!['complete', 'skipped'].includes(project.illustrationReview?.status) || project.illustrations?.some(image => image.status === 'stale')) return { id: 'images', label: '文章配图' };
  if (!reviewDone(project)) return { id: 'review', label: sixStep(project) ? '检查校验' : '全文校验' };
  return { id: 'ready', label: project.review.disposition?.status === 'skipped' ? '已跳过校验' : project.review.authorConfirmed ? '已人工确认' : '校验已完成' };
}

export function isWritingProjectActive(project) {
  return (project.runs || []).some(run => run.status === 'running' && ['outline', 'section', 'document', 'edit', 'automatic'].includes(run.kind));
}

export function writingProjectCardStatus(project, exportReceipt) {
  const runs = project.runs || [];
  const running = runs.find(run => run.status === 'running');
  if (isWritingProjectActive(project)) return '正在写作';
  if (['images', 'image_batch'].includes(running?.kind)) return '查找配图';
  if (running) return '核对中';
  if (runs.some(run => run.status === 'queued')) return '排队中';
  if (runs.some(run => run.status === 'paused')) return '已暂停';
  const currentAutomatic = project.automatic?.report?.projectVersion === project.version;
  if (currentAutomatic && automaticFinished(project.automatic)) return automaticResultLabel(project.automatic, project.sections);
  const currentReview = project.review?.wordCounts && project.review.version === project.version;
  const hasIssues = !!project.review?.issues?.some(issue => !issue.confirmed && !issue.acknowledged);
  if (currentReview && project.id && exportReceipt?.projectId === project.id && exportReceipt.version === project.version) {
    return exportedAsDraft(project.review, exportReceipt) ? '草稿已导出' : '已导出';
  }
  // Historical suggestions are optional alternatives, not manuscript state.
  // In particular an image search is never a body-adoption requirement.
  const phase = writingProjectPhase(project);
  if (phase.id === 'ready') return project.review?.disposition?.status === 'skipped' ? '已跳过校验' : project.review?.authorConfirmed ? '已人工确认' : hasIssues ? '待核对' : '已校验';
  return ({ brief: sixStep(project) ? '待设定主题' : '待拟纲要', materials: '待筛选资料', outline: '待确认纲要', body: '待续写', images: '待确认配图', review: '待全文校验' })[phase.id];
}

export function writingProjectCardProgress(project) {
  const sections = project.sections || [];
  const completed = sections.filter(section => section.content?.trim()).length;
  const prepared = !!(project.id && (project.title?.trim() || project.topic?.trim()));
  const outlined = prepared && sections.length > 0 && !!project.outlineConfirmed;
  const body = outlined ? completed / sections.length : 0;
  const images = body === 1 && ['complete', 'skipped'].includes(project.illustrationReview?.status)
    && !(project.illustrations || []).some(image => image.status === 'stale');
  const reviewed = images && reviewDone(project);
  const six = sixStep(project);
  const selected = prepared && !!(project.materialSelection?.confirmed || sections.length);
  const stageIds = six ? sixStepCardStageIds : cardStageIds;
  const stageWeights = six ? sixStepCardStageWeights : cardStageWeights;
  const stageValues = six
    ? [Number(prepared), Number(selected), Number(outlined), body, Number(images), Number(reviewed)]
    : [Number(prepared), Number(outlined), body, Number(images), Number(reviewed)];
  const phase = writingProjectPhase(project).id;
  const stageStates = stageIds.map((id, index) => {
    // A saved skip advances the workflow but never represents verification.
    // The existing completion gates also exclude stale and incomplete work.
    if ((id === 'images' && images && project.illustrationReview.status === 'skipped')
      || (id === 'review' && reviewed && project.review.disposition?.status === 'skipped')) return 'skipped';
    if (stageValues[index] === 1) return 'complete';
    return stageValues[index] > 0 || id === phase ? 'running' : 'waiting';
  });
  // Both card renderers use these same weights for segment width and point.
  const percent = stageValues.reduce((sum, value, index) => sum + value * stageWeights[index], 0);
  return { ratio: percent / 100, percent, completed, total: sections.length, stageValues, stageIds, stageWeights, stageStates };
}
