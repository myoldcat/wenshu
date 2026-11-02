import React, { useState } from 'react';
import { Check, DownloadSimple, FileText, Sparkle } from '@phosphor-icons/react';
import { AUTOMATIC_STAGES, automaticBodyResult, automaticCurrentRetry, automaticFinished, automaticReportItems, automaticReportMessage, automaticReportRetries, automaticReportSummary, automaticReportText, automaticResultLabel, automaticRunning, automaticStageLabel } from './writing-automatic';
import './writing-automatic.css';

export function downloadAutomaticReport(project) {
  const text = automaticReportText(project);
  if (!text) return;
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url; link.download = `${(project.automatic.report.title || project.title || project.topic || '文枢文稿').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')}（全自动写作报告）.txt`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function WritingAutomatic({ project, busy, compact = false, showStages = true, reportOnly = false, onReport, onInterrupt, onSettings }) {
  const [reportOpen, setReportOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const automatic = project.automatic;
  if (!automatic) return null;
  const active = automaticRunning(automatic), finished = automaticFinished(automatic), report = finished ? automatic.report : null;
  const compactView = active || compact, folded = compactView && (active || !detailsOpen);
  const historical = !!report && Number.isInteger(report.projectVersion) && report.projectVersion !== project.version;
  const issues = automaticReportItems(report);
  const retries = automaticReportRetries(report), currentRetry = automaticCurrentRetry(automatic);
  const stageIndex = AUTOMATIC_STAGES.findIndex(([key]) => automaticStageLabel(key) === automaticStageLabel(automatic.stage));
  const result = automaticBodyResult(automatic, historical ? undefined : project.sections);
  const retryStatus = currentRetry ? [currentRetry.sectionTitle, currentRetry.message].filter(Boolean).join(' · ') : '';
  const title = active ? automatic.status === 'queued' ? '排队中' : [automaticStageLabel(automatic.stage), retryStatus].filter(Boolean).join(' · ') : historical ? '此前全自动写作记录' : automaticResultLabel(automatic, project.sections);
  const activeDescription = automatic.status === 'queued' ? '已加入写作队列，有空位时会自动开始。可以返回文稿列表新建其他文稿，也可以离开此页。' : '文枢会连续完成材料、纲要、正文、配图与校验，结束后汇总报告。可以离开此页，稍后回来查看。';
  const description = result === 'none' ? '本次未生成正文。未完成的步骤和原因保留在单独报告中。' : result === 'partial' ? '已保存部分正文，其余章节尚未完成。处理情况与待核对事项保留在单独报告中。' : result === 'unknown' ? '本次处理已结束，正文结果请结合文稿与报告核对。' : '已生成的正文已保存。处理情况与待核对事项保留在单独报告中。';
  return <section className={`writing-automatic ${compactView ? 'is-compact' : ''} ${folded ? 'is-folded' : ''} ${reportOnly ? 'is-report' : ''}`} aria-label="全自动写作" data-automatic-status={automatic.status}>
    {!reportOnly && <div className="writing-automatic-heading"><span className="writing-automatic-symbol"><Sparkle size={23}/></span><div role="status" aria-live="polite"><h3>{compactView ? `全自动 · ${title}` : title}</h3><p>{active ? activeDescription : historical ? '文稿在本次自动写作结束后已有修改。下方报告保留当次处理情况，当前内容请以文稿为准。' : description}</p></div>{compactView && !active && <button type="button" className="btn quiet" aria-expanded={report && onReport ? undefined : detailsOpen} aria-haspopup={report && onReport ? 'dialog' : undefined} onClick={() => { if (report && onReport) { onReport(); return; } setDetailsOpen(!detailsOpen); if (report) setReportOpen(true); }}>{detailsOpen ? '收起详情' : report ? `写作报告${issues.length ? ` · ${issues.length} 项` : ''}` : '查看详情'}</button>}{active && <><span className="writing-automatic-readonly-label">只读查看</span><button type="button" className="btn quiet" data-writing-interrupt disabled={!!busy || !onInterrupt} onClick={() => onInterrupt?.()}>{String(busy || '').includes('interrupt') ? '正在中断…' : '中断并切换为交互式写作'}</button></>}</div>}
    {active && !compactView && showStages && <ol className="writing-automatic-stages" aria-label="全自动处理进度">{AUTOMATIC_STAGES.map(([key, label], index) => <li key={key} className={index < stageIndex ? 'is-complete' : index === stageIndex ? 'is-current' : ''} aria-current={index === stageIndex ? 'step' : undefined}><span>{index < stageIndex ? <Check size={13}/> : index + 1}</span>{label}</li>)}</ol>}
    {finished && <div className="writing-automatic-result">
      {reportOnly && historical && <p>文稿在本次自动写作结束后已有修改。以下报告保留当次处理情况，当前内容请以文稿为准。</p>}
      {onSettings && <div className="writing-automatic-result-actions"><button type="button" className="btn quiet" onClick={onSettings}>查看初始设置</button></div>}
      {report ? <div className="writing-automatic-report">
        {!reportOnly && <div className="writing-automatic-report-heading"><FileText size={21}/><div><h4>全自动写作报告</h4><p>{issues.length ? `${issues.length} 项处理情况与待核对事项` : '本次没有待核对事项'}{retries.length > 0 && ` · 重试记录 ${retries.length} 项`}</p></div><button type="button" className="btn" aria-expanded={reportOpen} onClick={() => setReportOpen(!reportOpen)}>{reportOpen ? '收起报告' : '阅读报告'}</button><button type="button" className="btn quiet" onClick={() => downloadAutomaticReport(project)}><DownloadSimple size={16}/>下载报告</button></div>}
        {(reportOnly || reportOpen) && <div className="writing-automatic-report-body">
          <p className="writing-report-summary">{automaticReportSummary(report)}{reportOnly && <span>{issues.length ? `${issues.length} 项处理情况与待核对事项` : '本次没有待核对事项'}{retries.length > 0 && ` · 重试记录 ${retries.length} 项`}</span>}</p>
          {Array.isArray(report.stages) && <ul className="writing-automatic-stage-report">{report.stages.map((stage, index) => <li key={index}><strong>{automaticStageLabel(stage.stage)}：</strong>{automaticReportMessage(stage.message, '本步骤已处理。')}</li>)}</ul>}
          {retries.length > 0 && <section aria-label="自动重试记录"><h4>重试记录</h4><ul className="writing-automatic-stage-report">{retries.map((retry, index) => <li key={index}>{retry.stage && <span>{retry.stage} · </span>}{retry.sectionTitle && <strong>{retry.sectionTitle}：</strong>}{retry.message}</li>)}</ul></section>}
          {issues.length > 0 && <ol>{issues.map((issue, index) => <li key={index}>{issue.stage && <span>{issue.stage} · </span>}{issue.sectionTitle && <strong>{issue.sectionTitle}：</strong>}{issue.message}</li>)}</ol>}
        </div>}
      </div> : <p className="writing-automatic-report-pending">本次报告暂未生成，已保存的正文可以继续阅读。</p>}
    </div>}
  </section>;
}
