import { deriveWritingProgress } from './writing-progress.js';

// Read-only reminders about saved writing state. Opening one is navigation,
// never a confirmation, a save, or a request to a model.
const list = value => Array.isArray(value) ? value : [];
const text = value => typeof value === 'string' && !!value.trim();
const running = value => ['queued', 'running'].includes(value?.status);
const fields = project => ({ ...(project.brief || {}), ...project });

export function deriveWritingProgressNotices({ project = {}, savedProject, dirty = false,
  remoteChanged = false, pending = false, progress } = {}) {
  const saved = project.id && savedProject?.id === project.id ? savedProject
    : project.id && !dirty ? project : null;
  if (!saved) return [];
  const active = [...list(project.runs), ...list(saved.runs)].filter(running);
  if (progress?.automatic || running(project.automatic) || running(saved.automatic)
    || active.some(run => run.kind === 'automatic')) return [];

  const steps = list((progress || deriveWritingProgress({ project: saved, savedProject: saved,
    remoteChanged, pending })).steps);
  const step = id => steps.find(value => value.id === id);
  const available = id => step(id)?.available !== false;
  const complete = id => step(id)?.complete === true;
  const sections = list(saved.sections), written = sections.filter(section => text(section.content));
  const allWritten = !!sections.length && written.length === sections.length;
  const pendingSteps = new Set(list(saved.workflowPendingSteps));
  const bodyComplete = allWritten && !pendingSteps.has('body') && (step('body') ? complete('body') : true);
  const notices = [], review = saved.review;
  const busy = pending || active.length > 0;
  const add = (id, stepId, tone, label, description, actionLabel, extra = {}) => {
    if (available(stepId) || stepId === 'review' && review) {
      notices.push({ id, stepId, tone, label, description, actionLabel, ...extra });
    }
  };

  // Saved reports remain readable, including old ones, but their old issue
  // counts must not be presented as findings about the current manuscript.
  if (review && (remoteChanged || review.version !== saved.version)) {
    add('review-stale', 'review', 'warning', '校验报告已过期',
      '文稿已有变化，请查看此前报告；其中的结论和确认记录不代表当前文稿。', '查看校验报告');
  } else if (review && !busy) {
    const skipped = review.disposition?.status === 'skipped';
    const count = list(review.issues).filter(issue => issue && typeof issue === 'object'
      && !issue.confirmed && !issue.acknowledged).length;
    if (skipped) {
      add('review-skipped', 'review', 'info', '已跳过校验',
        '本次未完成事实与引用的语义核对，可在检查校验页面手动发起校验。', '查看校验报告');
    } else if (count) {
      add('review-issues', 'review', 'warning', `${count} 项待确认`,
        `校验报告中有 ${count} 项尚未确认，请查看后逐项处理。`, '查看待确认事项', { count });
    } else if (review.citationReviewCompleted !== true && review.authorConfirmed !== true) {
      add('review-incomplete', 'review', 'warning', '校验待完成',
        '事实与引用的支持关系尚未完成核对，可查看报告后决定继续校验或跳过。', '查看校验报告');
    }
  }
  // A running model or a newer remote version cannot be author-confirmed from
  // this snapshot. Keep only the genuinely stale report reminder, if present.
  if (busy || remoteChanged) return notices;

  if (text(fields(saved).topic) && pendingSteps.has('brief') && !complete('brief')) {
    add('brief-confirm', 'brief', 'warning', '主题待确认',
      '主题与写作要求已经保存，确认后可继续。', '前往确认');
  }
  if (complete('brief') && !complete('materials')
    && (pendingSteps.has('materials') || saved.materialSelection?.confirmed === false)) {
    add('materials-confirm', 'materials', 'warning', '资料待确认',
      '请确认资料范围及使用方式；不使用资料时也可明确确认。', '前往确认');
  }
  if (sections.length && !saved.outlineConfirmed) {
    add('outline-confirm', 'outline', 'warning', '纲要待确认',
      '已有写作纲要，请审阅章节结构与目标后确认。', '前往确认');
  }

  const chapters = saved.chapterConfirmationPolicy === 'chapter-confirmation-v1'
    ? written.filter(section => section.chapterConfirmed !== true) : [];
  if (chapters.length) {
    add('body-chapters', 'body', 'warning', `${chapters.length} 章待确认`,
      '已有保存的章节正文，可逐章编辑并确认。', '前往确认章节',
      { count: chapters.length, sectionId: chapters[0].id });
  } else if (allWritten && pendingSteps.has('body')) {
    add('body-confirm', 'body', 'warning', '正文待确认',
      '各章正文已经保存，请确认整篇正文后继续。', '前往确认');
  }
  if (bodyComplete && !complete('images')) {
    add('images-confirm', 'images', 'warning', '配图待确认',
      '请确认文章配图，也可以明确选择不配图。', '前往确认');
  }
  if (!review && bodyComplete && complete('images')) {
    add('review-start', 'review', 'info', '待检查校验',
      '正文与配图已确认，可以开始检查校验，也可以跳过。', '前往检查校验');
  }
  return notices;
}
