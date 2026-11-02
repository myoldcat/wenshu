import React, { useEffect, useRef, useState } from 'react';
import { ArrowRight, BookOpen, Check, CheckCircle, Clock, FileText, PencilSimple, UploadSimple, X } from '@phosphor-icons/react';
import { ThemeSwitcher } from './ThemeSwitcher';
import { writingMessage } from './writing-messages';
import { OriginalLanguageBadge } from './OriginalLanguageBadge';
import './workspace-panels.css';

const statusTitles = {
  ingest: '入库进度',
  writing: '写作进度',
  selection: '已选资料',
  appearance: '外观主题',
  settings: '系统设置',
};

function ProgressJobs({ jobs, jobCounts, jobsError, onRefreshJobs, onRetryJob, onCancelJob, documents = [], onOpenDoc, action, writing = false }) {
  const [busyJob, setBusyJob] = useState(null);
  const jobAction = async (id, callback) => { setBusyJob(id); try { await callback?.(id); } finally { setBusyJob(null); } };
  const stageNames = { idle_paused: '闲时处理已暂停 · 等待空闲', queued: '等待处理', fetching: '抓取网页', parsing: '解析正文', indexing: '建立全文索引', ready: '已入库', generating: '模型正在生成', validating: '校验结果与来源', ai_summary: '生成 AI 摘要', ai_translation: '生成中文译文', ai_classification: '生成自动分类', wiki_build: '编译 Wiki 图谱', compiling: '编译 Wiki 词条', publishing: '保存 Wiki 与图谱', writing_outline: '生成写作纲要', writing_section: '生成写作章节', writing_document: '自动生成全文', writing_edit: '生成选区修改建议', writing_citation_match: '逐句核对引用', writing_images: '查找文章配图', image_retrieval: '查找参考资料图片', image_ranking: '筛选适合的配图', image_review: '核对图片与段落', candidate: '候选稿待采纳' };
  return <>
      {jobsError && <div className="local-library-message is-error" role="alert"><span>{jobsError}</span><button className="btn quiet" onClick={onRefreshJobs}>重试</button></div>}
      <div className="workspace-section-caption"><h3>{writing ? '写作任务' : '入库任务'}</h3><span>{jobCounts.total > jobs.length ? `显示 ${jobs.length} / 共 ${jobCounts.total} 个 · 未完成优先` : `${jobs.length} 个任务`}</span></div>
      <div className="workspace-status-docs local-job-list">{jobs.map(job => <div className={`local-ingest-job ${job.status}`} key={job.id}>
        <div className="local-job-heading"><span className={`workspace-doc-status ${['queued','running'].includes(job.status) ? 'pending' : ''}`}>{job.status === 'succeeded' ? <CheckCircle size={17}/> : <Clock size={17}/>}</span><strong title={job.label}>{job.label || (writing ? '未命名写作任务' : '未命名文档')}</strong></div>
        <p className="local-job-status">{job.status === 'failed' ? job.kind === 'writing_images' ? '图片检索未完成 · 请到写作空间重试' : job.kind === 'writing_citation_match' ? '引用核对未完成 · 请到写作空间重新核对' : job.kind === 'writing_document' ? '全文任务未全部完成 · 请到写作空间核对各章' : '处理失败' : job.status === 'cancelled' ? '已取消' : job.status === 'succeeded' ? String(job.kind).startsWith('writing_') ? job.kind === 'writing_images' ? '图片检索已完成 · 请到配图步骤核对并选择' : job.stage === 'candidate' ? '已生成候选稿 · 请到写作空间比较' : job.kind === 'writing_citation_match' ? '引用核对已完成 · 请检查仍需核实的论述' : job.kind === 'writing_outline' ? '写作纲要已保存 · 待确认' : job.kind === 'writing_edit' ? '选区修改建议已保存 · 请比较后采纳' : job.kind === 'writing_document' ? '全文任务已结束 · 请预览并核对各章' : '写作章节已保存' : job.kind === 'wiki_build' ? 'Wiki 编译完成 · 图谱已保存' : String(job.kind).startsWith('ai_') ? 'AI 处理完成 · 结果已保存' : job.duplicate ? '已识别重复内容 · 保留已有文档' : '已入库 · 可阅读和检索' : writing && job.status === 'queued' ? '排队中 · 有空位时自动开始' : job.kind === 'writing_images' && job.status === 'running' ? '正在查找图片并核对与段落的联系' : job.kind === 'writing_citation_match' && job.status === 'running' ? '正在逐句核对引用' : stageNames[job.stage] || (/^(summary|translation|classification|wiki):\d+\/\d+$/.test(job.stage || '') ? `${stageNames[job.kind]} · 分段 ${job.stage.split(':')[1]}` : stageNames[job.kind]) || '正在处理'}</p>
        {job.error && <p className={job.stage === 'idle_paused' ? 'local-job-status' : 'local-job-error'}>{String(job.kind).startsWith('writing_') ? writingMessage(job.error, '本次写作任务未完成，请回到写作空间核对后重试。') : typeof job.error === 'string' ? job.error : JSON.stringify(job.error)}</p>}
        <div className="local-job-actions">{job.source_id && documents.some(doc => doc.id === job.source_id) && <button type="button" onClick={() => action(() => onOpenDoc?.(job.source_id))}>打开文档<ArrowRight size={13}/></button>}{['failed','cancelled'].includes(job.status) && (job.retryable === false ? <span className="local-job-reupload">{job.stage === 'superseded' ? '任务已被替代' : job.kind === 'writing_images' ? '请在配图步骤重新检索' : job.kind === 'writing_citation_match' ? '请在写作空间重新核对引用' : String(job.kind).startsWith('writing_') ? '请在写作空间重新生成' : String(job.kind).startsWith('ai_') ? '请新建处理任务' : '需重新上传'}</span> : <button type="button" disabled={busyJob === job.id} onClick={() => jobAction(job.id,onRetryJob)}>{busyJob === job.id ? '正在重试…' : '重新处理'}</button>)}{['queued','running'].includes(job.status) && <button type="button" disabled={busyJob === job.id} onClick={() => jobAction(job.id,onCancelJob)}>取消任务</button>}</div>
      </div>)}</div>
      {!jobs.length && !jobsError && <div className="workspace-small-empty"><BookOpen size={28}/><p>{writing ? '暂无写作任务' : '暂无入库任务'}</p></div>}
  </>;
}

export function StatusPopover({ kind, onClose, documents = [], selected = [], onToggleSelected, onClearSelected, onOpenDoc, onStartWriting, onContinueWriting, onImport, project, writingDirty = false, stage, settingsContent, jobs = [], jobCounts = {}, jobsError, writingJobs = [], writingJobCounts = {}, writingJobsError, onRefreshJobs, onRetryJob, onCancelJob }) {
  const panelRef = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const open = Boolean(statusTitles[kind]);
  const selectedDocs = documents.filter(doc => selected.includes(doc.id));
  const activeCount = (jobCounts.queued || 0) + (jobCounts.running || 0);
  const sections = project?.sections || [];
  const drafted = sections.filter(section => (section.content || '').trim());
  const chars = sections.reduce((sum, section) => sum + (section.content || '').replace(/\s/g, '').length, 0);
  const planned = sections.reduce((sum, section) => sum + Number(section.words || 0), 0);
  const progress = planned ? Math.min(100, Math.round(chars / planned * 100)) : 0;
  const title = statusTitles[kind] || '';

  useEffect(() => {
    if (!open) return;
    let previous = document.activeElement;
    panelRef.current?.querySelector('[data-popover-close]')?.focus({ preventScroll: true });
    const outside = event => {
      if (document.querySelector('[data-settings-detail-layer]')) return;
      const trigger = event.target.closest?.('[data-status-trigger]');
      if (trigger) { previous = trigger; return; }
      if (panelRef.current?.contains(event.target)) return;
      if (document.documentElement.dataset.themeTransition === 'crossfade') {
        // Native capture can retarget an inside pointer to <html>/<body>.
        // Treat its actual coordinates as inside instead of closing the chooser.
        const rect = panelRef.current?.getBoundingClientRect();
        if (rect && event.clientX >= rect.left && event.clientX <= rect.right &&
          event.clientY >= rect.top && event.clientY <= rect.bottom) return;
      }
      closeRef.current?.();
    };
    const keyboard = event => {
      if (event.key !== 'Escape' || event.defaultPrevented || document.querySelector('[data-settings-detail-layer]')) return;
      event.preventDefault();
      event.stopPropagation();
      closeRef.current?.();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', keyboard);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', keyboard);
      if (previous?.isConnected) previous.focus?.({ preventScroll: true });
    };
  }, [open]);

  const action = callback => { onClose?.(); callback?.(); };
  return <aside className="workspace-popover" id="workspace-status-popover" ref={panelRef} tabIndex={-1} hidden={!open} role="dialog" aria-modal="false" aria-labelledby="workspace-status-title">
    <header className="workspace-popover-head">
      <h2 id="workspace-status-title">{title}</h2>
      <button type="button" className="icon-btn" data-popover-close aria-label="关闭状态窗口" onClick={onClose}><X size={20}/></button>
    </header>

    <section className="workspace-panel-section" hidden={kind !== 'ingest'}>
      <div className="workspace-status-metrics"><div><strong>{documents.length}</strong><span>可阅读文档</span></div><div><strong>{activeCount}</strong><span>正在处理 / 排队</span></div><div><strong>{jobCounts.failed || 0}</strong><span>失败任务</span></div></div>
      <ProgressJobs jobs={jobs} jobCounts={jobCounts} jobsError={jobsError} onRefreshJobs={onRefreshJobs} onRetryJob={onRetryJob} onCancelJob={onCancelJob} documents={documents} onOpenDoc={onOpenDoc} action={action}/>
      {onImport && <button type="button" className="btn primary workspace-popover-cta" onClick={() => action(onImport)}><UploadSimple size={17}/>导入资料<ArrowRight size={17}/></button>}
    </section>

    <section className="workspace-panel-section" hidden={kind !== 'writing'}>
      <div className="workspace-status-metrics"><div><strong>{(writingJobCounts.queued || 0) + (writingJobCounts.running || 0)}</strong><span>正在写作 / 排队</span></div><div><strong>{writingJobCounts.failed || 0}</strong><span>失败任务</span></div><div><strong>{writingJobCounts.total || 0}</strong><span>写作任务</span></div></div>
      <ProgressJobs writing jobs={writingJobs} jobCounts={writingJobCounts} jobsError={writingJobsError} onRefreshJobs={onRefreshJobs} onRetryJob={onRetryJob} onCancelJob={onCancelJob}/>
      {project ? <><div className="workspace-current-project"><span><PencilSimple size={22}/></span><div><small>当前写作项目</small><h3>{project.title || '尚未命名的文章'}</h3></div></div>
        <div className="workspace-writing-status"><span>{({ projects: '项目概览', brief: '写作准备', outline: '整理大纲', editor: '正文写作' })[stage] || '写作准备'}</span><span>{project.outlineConfirmed ? '大纲已确认' : '大纲待确认'}</span></div>
        <div className="workspace-progress-label"><span>正文 / 计划字数</span><strong>{chars.toLocaleString()} <small>/ {planned.toLocaleString()} 字</small></strong></div><div className="workspace-progress" role="progressbar" aria-label="正文达到计划字数的比例" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><span style={{ width: `${progress}%` }}/></div>
        <div className="workspace-status-metrics two"><div><strong>{drafted.length}<small> / {sections.length}</small></strong><span>已含正文的章节</span></div><div><strong>{project.sourceIds?.length || 0}</strong><span>当前参考资料</span></div></div>
        <div className="workspace-writing-sections">{sections.map(section => <div key={section.id}><span className={(section.content || '').trim() ? 'has-content' : ''}>{(section.content || '').trim() ? <Check size={13}/> : <span/>}</span><strong>{section.title}</strong><small>{(section.content || '').replace(/\s/g, '').length} 字</small></div>)}</div>
        <button type="button" className="btn primary workspace-popover-cta" onClick={() => action(onContinueWriting || onStartWriting)}>继续写作<ArrowRight size={17}/></button>
      </> : <div className="workspace-small-empty"><PencilSimple size={28}/><p>暂无写作项目</p><button type="button" className="btn primary" onClick={() => action(onStartWriting)}>开始写作<ArrowRight size={17}/></button></div>}
      {writingDirty && <p className="workspace-session-note">有未保存修改，请在写作空间保存。</p>}
    </section>

    <section className="workspace-panel-section" hidden={kind !== 'selection'}>
      <div className="workspace-selection-count"><div><strong>{selectedDocs.length}</strong><span>份已选文档</span></div>{selectedDocs.length > 0 && <button type="button" onClick={() => onClearSelected ? onClearSelected() : selectedDocs.forEach(doc => onToggleSelected?.(doc.id))}>清空选择</button>}</div>
      <div className="workspace-selected-list">{selectedDocs.map(doc => <div className="workspace-selected-doc" key={doc.id}><FileText size={20}/><button type="button" onClick={() => action(() => onOpenDoc?.(doc.id))}><strong><OriginalLanguageBadge doc={doc}/>{doc.title}</strong><small>{doc.source} · {doc.pending ? '待抓取' : doc.date}</small></button><button type="button" className="icon-btn" aria-label={`从选择中移除 ${doc.title}`} onClick={() => onToggleSelected?.(doc.id)}><X size={16}/></button></div>)}</div>
      {!selectedDocs.length && <div className="workspace-small-empty"><BookOpen size={30}/><p>在知源文库勾选资料</p></div>}
      <button type="button" className="btn primary workspace-popover-cta" onClick={() => action(onStartWriting)}>{selectedDocs.length ? '用这些资料写作' : '进入写作准备'}<ArrowRight size={17}/></button>
    </section>

    <section className="workspace-panel-section workspace-appearance-section" hidden={kind !== 'appearance'}>
      <ThemeSwitcher/>
    </section>

    <section className="workspace-panel-section workspace-settings-section" hidden={kind !== 'settings'}>
      {kind === 'settings' && settingsContent}
    </section>
  </aside>;
}

export { PromptWorkspace } from './PromptWorkspace';
