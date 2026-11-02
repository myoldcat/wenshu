import { writingMessage, writingText } from './writing-messages.js';

export const AUTOMATIC_STAGES = [
  ['brief', '主题设定'], ['materials', '资料筛选'], ['outline', '写作纲要'],
  ['body', '正文编写'], ['images', '文章配图'], ['review', '检查校验'],
];
export function newAutomaticWritingOptions() {
  return { useSelectedSources: true, useRecommendedSources: true, mode: 'strict', includeImages: false, skipReview: true };
}
// Form preferences stay separate from the effective no-source writing mode.
export function automaticUserSourceIds(project = {}) {
  const current = new Set(Array.isArray(project.sourceIds) ? project.sourceIds : []);
  const stored = project.materialSelection && Object.hasOwn(project.materialSelection, 'userSourceIds')
    ? project.materialSelection.userSourceIds : [...current];
  return [...new Set(Array.isArray(stored) ? stored : [])].filter(id => current.has(id));
}
export function automaticWritingOptions(project = {}) {
  const saved = project.automaticOptions || project.brief?.automaticOptions;
  if (!saved || typeof saved !== 'object') return project.id
    ? { useSelectedSources: true, useRecommendedSources: true, mode: 'strict', includeImages: true, skipReview: false }
    : newAutomaticWritingOptions();
  const legacy = typeof saved.restrictToSelectedSources === 'boolean';
  const savedMode = saved.mode === 'expanded' ? 'extended' : saved.mode;
  const mode = ['strict', 'extended'].includes(savedMode) ? savedMode
    : legacy ? saved.restrictToSelectedSources ? 'strict' : 'extended' : 'strict';
  return {
    useSelectedSources: typeof saved.useSelectedSources === 'boolean' ? saved.useSelectedSources : true,
    useRecommendedSources: typeof saved.useRecommendedSources === 'boolean' ? saved.useRecommendedSources : !saved.restrictToSelectedSources,
    mode,
    includeImages: saved.includeImages !== false,
    skipReview: saved.skipReview === true,
  };
}
export function effectiveAutomaticMode(options) {
  return options.useSelectedSources || options.useRecommendedSources ? options.mode : 'extended';
}
export function automaticWritingOptionPatch(project, key, value) {
  const saved = project.automaticOptions || project.brief?.automaticOptions;
  const legacy = saved && typeof saved === 'object' && typeof saved.restrictToSelectedSources === 'boolean'
    && !['useSelectedSources', 'useRecommendedSources', 'mode'].some(field => Object.hasOwn(saved, field));
  const optionOnly = ['includeImages', 'skipReview'].includes(key);
  // Changing delivery options does not opt a saved manuscript into a different
  // source-selection schema. Source controls make that change explicitly.
  const options = { ...(legacy && optionOnly ? saved : automaticWritingOptions(project)), [key]: value };
  return {
    automaticOptions: options,
    ...(['useSelectedSources', 'useRecommendedSources', 'mode'].includes(key)
      ? { mode: effectiveAutomaticMode(options) === 'strict' ? 'strict' : 'expanded' } : {}),
  };
}
export const automaticRunning = automatic => ['queued', 'running'].includes(automatic?.status);
export const automaticFinished = automatic => ['succeeded', 'completed', 'completed_with_issues', 'failed', 'cancelled'].includes(automatic?.status);
const AUTOMATIC_STAGE_ALIASES = { preparation: 'materials', preparing: 'materials', document: 'body', chapters: 'body', section: 'body', citations: 'review', citation_match: 'review', complete: 'review', completed: 'review' };
const automaticStageId = stage => AUTOMATIC_STAGES.find(([key]) => key === (AUTOMATIC_STAGE_ALIASES[stage] || stage))?.[0] || null;

// These are processing phases, not a statement that the manuscript or its
// citations passed review. Finished runs return to the actual document milestones.
export function deriveAutomaticProgress(automatic, project) {
  if (!automaticRunning(automatic)) return null;
  const queued = automatic.status === 'queued';
  const currentId = queued ? 'materials' : automaticStageId(automatic.stage);
  const stageIndex = AUTOMATIC_STAGES.findIndex(([key]) => key === currentId);
  const sections = project?.sections || [];
  const hasBody = sections.some(section => section.content?.trim());
  const available = project ? { brief: true, materials: !!(project.sourceIds?.length || project.materialSelection?.confirmed || project.materialSelection?.items?.length), outline: sections.length > 0, body: hasBody, images: hasBody && !!(project.illustrations?.length || ['complete', 'skipped'].includes(project.illustrationReview?.status)), review: !!project.review } : {}; 
  return {
    automatic: true, currentId, completed: Math.max(0, stageIndex), total: AUTOMATIC_STAGES.length,
    steps: AUTOMATIC_STAGES.map(([id, label], index) => {
      const complete = index < stageIndex, current = index === stageIndex;
      return { id, label, complete, available: !!available[id],
        stateLabel: complete ? '已处理' : current && !queued ? '进行中' : '未开始',
        detail: complete ? `${label}已处理` : current ? queued ? '正在排队，等待开始。' : `${label}正在进行` : '待处理' };
    }),
  };
}
// A finished worker is separate from an available, complete manuscript. The
// report describes that frozen run; current sections are only a fallback.
export function automaticBodyResult(automatic, sections) {
  const report = automatic?.report, summary = report?.summary;
  const knownSections = Array.isArray(sections);
  const written = knownSections ? sections.filter(section => section.content?.trim()).length : 0;
  const count = key => Number.isInteger(summary?.[key]) && summary[key] >= 0 ? summary[key] : 0;
  const recorded = count('generatedSections') + count('preservedSections');
  if (report?.outputAvailable === false || report?.outputAvailable !== true && !recorded && knownSections && !written) return 'none';
  if (report?.outputAvailable === true || recorded || written) {
    return count('skippedSections') > 0 || knownSections && written > 0 && written < sections.length ? 'partial' : 'available';
  }
  return 'unknown';
}
export function automaticResultLabel(automatic, sections) {
  const result = automaticBodyResult(automatic, sections);
  if (result === 'none') return automatic?.status === 'cancelled' ? '已取消 · 未生成正文' : '未生成正文';
  if (result === 'partial') return automatic?.status === 'cancelled' ? '已取消 · 正文部分完成' : '正文部分完成';
  if (automatic?.status === 'cancelled') return '全自动写作已取消';
  if (automatic?.status === 'failed') return '本次生成未完成';
  if (result === 'unknown') return '正文结果待核对';
  return automatic?.status === 'completed_with_issues' ? '正文已生成 · 附报告' : '正文已生成';
}
export function automaticStageLabel(stage) {
  return AUTOMATIC_STAGES.find(([key]) => key === automaticStageId(stage))?.[1] || '正在写作';
}
export function automaticReportMessage(value, fallback = '这部分处理未能完成，请结合文稿核对。') {
  const original = writingText(value) || writingText(value?.detail);
  const message = writingMessage(value, fallback);
  // A recorded citation fault is a report item, not an instruction to restart
  // the run. Preserve public diagnostics while keeping protocol details out.
  if (!message.startsWith('生成内容中的引用无法与所选原文对应，请重新生成')) return message;
  if (/JSON|schema|traceback|HTTP\s*\d|API\s*key|token|contextChars|stack|TypeError|ValueError|sourceId|revisionId|blockId|原文块|证据块|白名单|冻结|协议|ID[：:]|\b[CO]\d*-B\d+\b|重新生成/i.test(original)) {
    return '引用对应问题已记录，请结合参考材料核对。';
  }
  return original.replaceAll('固定版本原文', '写作时选定的原文');
}
export function automaticReportItems(report) {
  const issues = Array.isArray(report?.issues) ? report.issues : [];
  return issues.map(issue => {
    const fallback = '这部分处理未能完成，请结合文稿核对。';
    const message = automaticReportMessage(typeof issue === 'string' ? issue : issue?.message || issue?.reason || issue?.detail, fallback);
    return { message, stage: issue?.stage ? automaticStageLabel(issue.stage) : '', sectionTitle: typeof issue?.sectionTitle === 'string' ? issue.sectionTitle : '' };
  });
}
const RETRY_REASONS = {
  format_error: '返回格式错误',
  interrupted: '生成结果异常中断',
  network: '模型连接中断或请求超时',
};
function automaticRetryItem(value) {
  if (!value || typeof value !== 'object' || ![1, 2].includes(value.retryCount) || value.maxRetries !== 2
      || !['retrying', 'recovered', 'exhausted'].includes(value.status)) return null;
  // Reasons are business categories. Provider errors and internal identifiers
  // stay in operations records, even if a malformed response includes them here.
  const reason = (typeof value.category === 'string' && Object.hasOwn(RETRY_REASONS, value.category) ? RETRY_REASONS[value.category] : '')
    || (Object.values(RETRY_REASONS).includes(value.reason) ? value.reason : RETRY_REASONS.interrupted);
  const result = value.status === 'retrying' ? `正在第${value.retryCount}次重试（最多${value.maxRetries}次）。`
    : value.status === 'recovered' ? `第${value.retryCount}次重试成功。` : `已重试${value.retryCount}次，仍未完成。`;
  return {
    stage: value.stage ? automaticStageLabel(value.stage) : '',
    sectionTitle: typeof value.sectionTitle === 'string' ? value.sectionTitle : '',
    status: value.status, retryCount: value.retryCount, maxRetries: value.maxRetries,
    message: `${reason}，${result}`,
  };
}
export function automaticReportRetries(report) {
  return (Array.isArray(report?.retries) ? report.retries : []).map(automaticRetryItem)
    .filter(item => item && item.status !== 'retrying');
}
export function automaticCurrentRetry(automatic) {
  if (automatic?.status !== 'running' || automatic.currentRetry?.status !== 'retrying') return null;
  return automaticRetryItem(automatic.currentRetry);
}
export function automaticReportSummary(report) {
  const fallback = report?.outputAvailable === false ? '本次未生成正文，详情见处理报告。' : '本次处理情况见报告，请结合文稿核对。';
  if (typeof report?.summary === 'string') return automaticReportMessage(report.summary, fallback);
  const summary = report?.summary;
  if (!summary || typeof summary !== 'object') return fallback;
  const count = (key, label) => Number.isInteger(summary[key]) && summary[key] >= 0 ? `${label} ${summary[key]}` : '';
  const retryCounts = Number.isInteger(summary.retryRequests) && summary.retryRequests > 0
    ? [count('retryRequests', '发生重试'), count('recoveredRequests', '重试成功'), count('exhaustedRequests', '重试耗尽')] : [];
  return [count('generatedSections', '生成章节'), count('preservedSections', '保留章节'), count('skippedSections', '未生成章节'), count('illustrations', '采用配图'), ...retryCounts].filter(Boolean).join(' · ') || fallback;
}
export function automaticReportText(project) {
  const report = project?.automatic?.report;
  if (!automaticFinished(project?.automatic) || !report) return '';
  const issues = automaticReportItems(report);
  const retries = automaticReportRetries(report);
  const status = automaticResultLabel(project.automatic);
  return [
    `${report.title || project.title || project.topic || '文枢文稿'} · 全自动写作报告`, status,
    Number.isInteger(report.projectVersion) && report.projectVersion !== project.version ? '文稿在本次自动写作结束后已有修改；以下为当次处理记录。' : '',
    automaticReportSummary(report),
    ...(Array.isArray(report.stages) ? report.stages.map(stage => `${automaticStageLabel(stage.stage)}：${automaticReportMessage(stage.message, '本步骤已处理。')}`) : []),
    ...(retries.length ? [`重试记录（${retries.length}）`, ...retries.map((retry, index) => `${index + 1}. ${[retry.stage, retry.sectionTitle, retry.message].filter(Boolean).join(' · ')}`)] : []),
    issues.length ? `待核对事项（${issues.length}）` : '本次报告没有待核对事项。',
    ...issues.map((issue, index) => `${index + 1}. ${[issue.stage, issue.sectionTitle, issue.message].filter(Boolean).join(' · ')}`),
  ].filter(Boolean).join('\n\n');
}
