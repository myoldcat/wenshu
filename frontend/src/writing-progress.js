import { reviewRequiresDraft } from './writing-export-policy.js';
import { writingBriefFingerprint, writingMaterialScopeFingerprint, writingOutlineFingerprint,
  writingBodyFingerprint } from './writing-semantic.js';
const text = value => typeof value === 'string' && !!value.trim();
const running = run => ['queued', 'running'].includes(run.status);
const fields = project => ({ ...(typeof project.brief === 'object' ? project.brief : {}), ...project });
export function deriveWritingProgress({ project = {}, savedProject, dirty = false, remoteChanged = false, pending = false, exportReceipt } = {}) {
  const saved = savedProject?.id === project.id && project.id ? savedProject : project.id && !dirty ? project : null;
  const committed = saved || {}, sections = committed.sections || [], body = sections.filter(section => text(section.content)), empty = sections.filter(section => !text(section.content));
  const pendingSteps = new Set(committed.workflowPendingSteps || []);
  const topicPresent = text(fields(project).topic), preparationBase = !!(saved && text(fields(saved).topic));
  const materialsBase = !!(preparationBase && (saved.materialSelection?.confirmed || sections.length || saved.outlineConfirmed));
  const preparationComplete = preparationBase && !pendingSteps.has('brief');
  const materialsComplete = materialsBase && !pendingSteps.has('materials');
  const outlineComplete = !!(materialsBase && sections.length && saved?.outlineConfirmed);
  const bodyComplete = !!sections.length && !empty.length && !pendingSteps.has('body'), active = (project.runs || []).find(running), taskPending = !!(pending || active);
  const figures = committed.illustrations || [], imagesComplete = !!(saved && body.length && ['complete', 'skipped'].includes(saved.illustrationReview?.status) && !figures.some(figure => figure.status === 'stale') && !remoteChanged && !taskPending);
  const review = committed.review, reviewComplete = !!(review && review.version === committed.version && (review.citationReviewCompleted === true || review.disposition?.status === 'skipped' || review.authorConfirmed === true) && !remoteChanged && !taskPending);
  const exportReady = !!project.id && !!body.length && !dirty && !remoteChanged && !taskPending;
  const exportComplete = !!(exportReady && exportReceipt?.projectId === project.id && exportReceipt.version === project.version);
  const steps = [
    { id: 'brief', label: '主题设定', complete: preparationComplete, available: true, detail: preparationComplete ? '主题与写作要求已保存' : '确定主题、题目、篇幅与写作方式' },
    { id: 'materials', label: '资料筛选', complete: materialsComplete, available: topicPresent, detail: materialsComplete ? (committed.sourceIds || []).length + ' 篇资料已确认' : '筛选资料，选择严格模式或拓展模式' },
    { id: 'outline', label: '写作纲要', complete: outlineComplete, available: materialsComplete || !!sections.length, detail: outlineComplete ? sections.length + ' 章纲要已确认' : '调整文章结构与章节目标后确认' },
    { id: 'body', label: '正文编写', complete: bodyComplete, available: !!sections.length, detail: body.length + ' / ' + sections.length + ' 章已有正文' },
    { id: 'images', label: '文章配图', complete: imagesComplete, available: !!project.id && !!body.length, detail: imagesComplete ? committed.illustrationReview.status === 'skipped' ? '已确认不配图' : '配图已确认' : '选择与正文匹配的配图，也可不配图' },
    { id: 'review', label: '检查校验', complete: reviewComplete, available: !!project.id && !!body.length && !remoteChanged && !taskPending, detail: reviewComplete ? review.disposition?.status === 'skipped' ? '已跳过校验' : '已生成校验报告' : '核对事实、观点与引用，也可跳过' },
  ].map(step => ({ ...step, stateLabel: step.complete ? '已完成'
    : pendingSteps.has(step.id) || step.id === 'outline' && sections.length && !committed.outlineConfirmed ? '进行中' : '未完成' }));
  let currentId, next;
  if (remoteChanged) { currentId = 'body'; next = { label: '重新载入并核对', description: '已保存文稿有更新，请先保留未保存修改。', action: 'reload' }; }
  else if (taskPending) { currentId = active?.kind === 'outline' ? 'outline' : ['images', 'image_batch'].includes(active?.kind) ? 'images' : ['citations', 'citation_match'].includes(active?.kind) ? 'review' : 'body'; next = { label: '等待任务完成', description: '当前处理完成后继续下一步。', action: 'wait' }; }
  else if (!topicPresent) { currentId = 'brief'; next = { label: '填写写作主题', description: '先确定文章主题与写作要求。', action: 'focus-topic' }; }
  else if (dirty || !project.id) {
    currentId = !saved || writingBriefFingerprint(project) !== writingBriefFingerprint(saved) ? 'brief'
      : writingMaterialScopeFingerprint(project) !== writingMaterialScopeFingerprint(saved) ? 'materials'
      : writingOutlineFingerprint(project) !== writingOutlineFingerprint(saved) ? 'outline'
      : writingBodyFingerprint(project) !== writingBodyFingerprint(saved) ? 'body' : 'materials';
    next = { label: '保存当前修改', description: '保存后继续下一步。', action: 'save' };
  }
  else if (!preparationComplete) { currentId = 'brief'; next = { label: '确认主题设定', description: '确认已保存的主题与写作要求后继续。', action: 'brief' }; }
  else if (!materialsComplete || !sections.length) { currentId = 'materials'; next = { label: '筛选并确认资料', description: '检索条件可编辑，确认资料后生成纲要。', action: 'materials' }; }
  else if (!outlineComplete) { currentId = 'outline'; next = { label: '审阅并确认纲要', description: '确认章节顺序与各章目标。', action: 'outline' }; }
  else if (!bodyComplete) { currentId = 'body'; next = { label: pendingSteps.has('body') && !empty.length ? '确认正文修改' : '继续编写正文', description: pendingSteps.has('body') && !empty.length ? '正文已有保存的修改，请确认后继续。' : '还有 ' + empty.length + ' 章尚无正文。', action: 'editor', sectionId: empty[0]?.id }; }
  else if (!imagesComplete) { currentId = 'images'; next = { label: '确认文章配图', description: '选择合适图片，或确认不配图。', action: 'images' }; }
  else if (!reviewComplete) { currentId = 'review'; next = { label: '前往检查校验', description: '核对事实与引用、生成报告，也可跳过此步。', action: 'review' }; }
  else { currentId = 'review'; next = { label: '导出文章', description: reviewRequiresDraft(review) ? '可先处理待核实事项，或选择忽略后导出。' : '文章与校验报告分别导出。', action: 'export' }; }
  return { steps, completed: steps.filter(step => step.complete).length, total: 6, currentId, next, body: { completed: body.length, total: sections.length, empty: empty.length, firstEmptySectionId: empty[0]?.id || null }, completedSections: body.length, totalSections: sections.length, reviewComplete, exportReady, exportComplete };
}
