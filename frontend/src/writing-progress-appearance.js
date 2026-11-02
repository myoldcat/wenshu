// Paint the saved workflow independently of whichever page the author opens.
import { imageParagraphs } from './writing-images.js';
import { writingAssetStatus } from './writing-library-availability.js';

const list = value => Array.isArray(value) ? value : [];
const active = value => ['queued', 'running'].includes(value?.status);
const terminal = value => ['succeeded', 'completed', 'completed_with_issues', 'failed', 'cancelled'].includes(value?.status);
const phase = run => ({ outline: 'outline', images: 'images', image_batch: 'images', citations: 'review', citation_match: 'review',
  section: 'body', document: 'body', edit: 'body' })[run?.kind];

function stepMetrics(saved, project, automaticActive) {
  const sections = list(saved?.sections), byId = new Map(sections.map(section => [section.id, section]));
  const sourceCount = new Set(list(saved?.sourceIds).filter(id => typeof id === 'string' && id)).size;
  const bodyCount = sections.filter(section => typeof section.content === 'string' && section.content.trim()).length;
  const imageCount = list(saved?.illustrations).filter(figure => {
    const section = byId.get(figure.sectionId), anchor = figure.anchor;
    return figure.status === 'accepted' && writingAssetStatus(saved, figure) === 'available'
      && section && anchor && Number.isInteger(anchor.paragraphIndex) && anchor.paragraphIndex >= 0
      && typeof anchor.paragraphExact === 'string' && !!anchor.paragraphExact.trim()
      && !!anchor.contentHash && anchor.contentHash === section.contentHash
      && imageParagraphs(section.content)[anchor.paragraphIndex] === anchor.paragraphExact;
  }).length;
  const modeSource = saved || (!project.id ? project : null);
  const mode = modeSource?.automation ?? modeSource?.brief?.automation;
  const automatic = mode === 'automatic' || mode == null && automaticActive;
  return {
    brief: { metricLabel: automatic ? '自动模式' : '交互模式', metricValue: automatic ? 'automatic' : 'manual',
      metricTitle: saved ? '已保存的写作方式' : '当前写作方式' },
    materials: { metricLabel: `${sourceCount} 篇资料`, metricValue: sourceCount, metricTitle: `已保存选用 ${sourceCount} 篇资料` },
    outline: { metricLabel: `共 ${sections.length} 章`, metricValue: sections.length, metricTitle: `已保存纲要共 ${sections.length} 章` },
    body: { metricLabel: `已写 ${bodyCount}/${sections.length} 章`, metricValue: bodyCount,
      metricTitle: `已保存正文覆盖 ${bodyCount} 章，纲要共 ${sections.length} 章` },
    images: { metricLabel: `${imageCount} 张配图`, metricValue: imageCount, metricTitle: `已采用且位置有效的配图 ${imageCount} 张` },
  };
}

function duration(record) {
  const milliseconds = record?.durationMs;
  if (!Number.isSafeInteger(milliseconds) || milliseconds < 0) {
    return { durationLabel: '耗时未记录', durationTitle: '没有可用的步骤耗时记录' };
  }
  let label = '不足1秒';
  if (milliseconds >= 1000) {
    const seconds = Math.round(milliseconds / 1000), hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds % 3600 / 60), remainder = seconds % 60;
    label = [hours ? `${hours}小时` : '', minutes ? `${minutes}分` : '', remainder ? `${remainder}秒` : ''].join('');
  }
  return { durationLabel: `用时 ${label}`, durationTitle: record.kind === 'generation'
    ? '生成任务耗时（含排队和保存）' : '步骤记录用时（按已保存记录计算）' };
}

export function decorateWritingProgress({ progress = {}, project = {}, savedProject,
  pending = false, dirty = false } = {}) {
  const saved = project.id && savedProject?.id === project.id ? savedProject
    : project.id && !dirty ? project : null;
  const automatic = Object.hasOwn(project, 'automatic') ? project.automatic : saved?.automatic;
  const runs = Array.isArray(project.runs) ? project.runs : list(saved?.runs);
  const activeRuns = runs.filter(active);
  const automaticRun = activeRuns.find(run => run.kind === 'automatic');
  const automaticActive = progress.automatic === true || active(automatic) || !!automaticRun;
  const automaticStatus = active(automatic) ? automatic.status : automaticRun?.status;
  const report = saved?.automatic?.report;
  const currentReport = !automaticActive && terminal(saved?.automatic) && report
    && report.projectVersion === saved?.version ? report : null;
  const currentReview = saved?.review?.version === saved?.version ? saved?.review : null;
  const emptyDuration = { durationLabel: '', durationTitle: '' };
  const metrics = stepMetrics(saved, project, automaticActive);

  return { ...progress, steps: list(progress.steps).map(step => {
    step = { ...step, ...metrics[step.id] };
    const current = step.id === progress.currentId;
    const job = current ? activeRuns.find(run => phase(run) === step.id) : null;
    const working = current && (automaticActive || !!job || pending);
    const queued = working && (automaticActive ? automaticStatus === 'queued' : job?.status === 'queued');
    const savedSkip = step.id === 'images' && saved?.illustrationReview?.status === 'skipped'
      || step.id === 'review' && currentReview?.disposition?.status === 'skipped';
    const authorConfirmedReview = step.id === 'review' && currentReview?.authorConfirmed === true
      && currentReview.disposition?.status === 'acknowledged';
    // A current saved author decision supersedes the automatic run's history.
    const reportSkip = !authorConfirmedReview && list(currentReport?.stages).some(stage => stage.stage === step.id && stage.status === 'skipped');
    // Future phases in a new automatic run cannot borrow an old skipped state.
    const skipped = reportSkip || savedSkip && (!automaticActive || step.complete === true);
    if (working) return { ...step, visualState: queued ? 'waiting' : 'running',
      stateLabel: queued ? '等待排队' : '进行中', ...emptyDuration };
    if (skipped) return { ...step, visualState: 'skipped', stateLabel: '已跳过',
      durationLabel: '已跳过', durationTitle: '本环节已跳过' };
    if (step.complete === true) return { ...step, visualState: 'complete', stateLabel: '已完成',
      ...duration(saved?.workflowTimings?.[step.id]) };
    if (!automaticActive && current && step.available) {
      return { ...step, visualState: 'running', stateLabel: '进行中', ...emptyDuration };
    }
    return { ...step, visualState: 'waiting', stateLabel: '等待', ...emptyDuration };
  }) };
}
