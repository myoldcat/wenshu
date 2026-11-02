import React from 'react';
import { CheckCircle, Sparkle, WarningCircle } from '@phosphor-icons/react';
import { writingMessage } from './writing-messages';
import WritingGenerationNotices from './WritingGenerationNotices';

const strategies = { single: '整体成文', sequential: '依次完成各章' };
const states = { pending: '等待生成', generating: '正在生成', validated: '已生成待处理', applied: '已写入', candidate: '待比较', skipped: '已保留', failed: '生成失败', cancelled: '已取消' };
const taskStates = { queued: '等待处理', running: '进行中', succeeded: '已结束', failed: '未全部完成', cancelled: '已取消' };
const reasons = { locked: '章节已锁定', existing_content: '已有正文', existing: '已有正文', manual: '已有人工作出的修改', manual_content: '已有人工作出的修改', content_changed: '正文已修改', section_changed: '章节内容已变化', project_changed: '写作要求或资料已变化', cancelled: '任务已取消' };
const readable = value => value ? reasons[value] || writingMessage(value, '本次生成未完成，请稍后重试。当前文稿已保留。') : '';

export function WritingGenerationPlan({ plan, loading, error, dirty }) {
  return <div className="writing-generation-plan" aria-label="全文生成方案">
    <Sparkle size={18}/><div><strong>{dirty ? '保存后更新全文生成方案' : loading ? '正在评估全文生成方案…' : plan ? strategies[plan.strategy] || '全文生成方案' : '确认纲要后自动成文'}</strong>
      <p>{dirty ? '请先保存修改，再按最新纲要继续生成。' : error ? writingMessage(error) : '按照已确认纲要编写全文，已有内容会保留。'}</p>
      {plan && <><p>全文约 {Number(plan.targetWords || 0).toLocaleString()} 字 · 本次生成 {plan.eligibleSections?.length || 0} 章 · 保留 {plan.skippedSections?.length || 0} 章</p>{plan.blockingReasons?.map((reason, index) => <p className="writing-plan-warning" key={index}><WarningCircle size={13}/>{readable(reason)}</p>)}</>}
      <small>已有正文、人工修改和锁定章节会保留。全文预览可逐段查看引用材料。</small>
    </div>
  </div>;
}

export default function WritingDocumentTask({ run, busy, onCancel, onSelect, onCompare }) {
  if (!run) return null;
  const document = run.candidate?.document, plan = run.plan || {}, active = ['queued', 'running'].includes(run.status);
  const remaining = !!plan.remainingFromSectionId;
  const steps = document?.steps || [], shown = new Set(steps.map(step => step.sectionId));
  const skipped = (plan.skippedSections || []).filter(step => !shown.has(step.sectionId));
  const skippedCount = steps.filter(step => step.status === 'skipped').length + skipped.length;
  const completed = document?.completedSections ?? steps.filter(step => ['applied', 'candidate', 'failed', 'cancelled'].includes(step.status)).length;
  const total = document?.totalSections ?? plan.eligibleSections?.length ?? steps.length;
  return <section className={`writing-document-task ${run.status}`} aria-label="全文生成进度">
    <div className="writing-document-task-heading"><div><Sparkle size={16}/><strong>{remaining ? '剩余章节生成' : '全文生成'} · {taskStates[run.status] || '处理中'}</strong><span>{strategies[plan.strategy || document?.strategy] || '准备生成'}</span></div>{active && run.jobId && <button disabled={busy} onClick={() => onCancel(run.jobId)}>{remaining ? '取消剩余章节生成' : '取消全文任务'}</button>}</div>
    <details><summary><span>已处理 {completed} / {total} 章{skippedCount ? ` · 已保留 ${skippedCount} 章` : ''}</span><span>查看各章进度</span></summary><div className="writing-document-steps">
      {[...steps, ...skipped.map(step => ({ ...step, status: 'skipped' }))].map(step => <div key={step.sectionId} className={`writing-document-step ${step.status}`}><button className="writing-document-step-title" onClick={() => onSelect(step.sectionId)}>{step.title || '章节'}</button><span>{step.status === 'applied' && <CheckCircle size={13}/>} {!active && step.status === 'pending' ? '未生成' : states[step.status] || '处理中'}</span>{(step.reason || Number.isFinite(step.actualWords)) && <small>{readable(step.reason)}{Number.isFinite(step.actualWords) ? `${step.reason ? ' · ' : ''}正文约 ${step.actualWords} 字 / 目标 ${step.targetWords} 字${step.lengthWarning ? ' · 篇幅偏离较大，请核对。' : ''}` : ''}</small>}{step.status === 'candidate' && step.section?.content && <button disabled={active || busy} onClick={() => onCompare(run.id, step.sectionId)}>比较候选</button>}<WritingGenerationNotices notices={step.section?.generationNotices} compact/></div>)}
    </div></details>
    {run.error && <p className="writing-plan-warning" role="alert">{readable(run.error)}</p>}
    <small>{active ? '生成期间正文只读；已有正文、人工修改和锁定章节会保留，完成后可比较候选。' : '已写入的内容已保存；候选稿经你比较后采纳，已修改或锁定的正文会保留。'}</small>
  </section>;
}
