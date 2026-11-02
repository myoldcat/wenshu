import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, CheckCircle, Image, MagnifyingGlass, Plus, WarningCircle, X } from '@phosphor-icons/react';
import { api, connectionMessage } from './local-api';
import { CitedParagraph } from './WritingCitations';
import { buildWritingPreview } from './writing-preview';
import { CandidateCard, FigureEditor, Picture } from './WritingImageCards';
import WritingImagesEmpty from './WritingImagesEmpty';
import { imageBusinessText, imageParagraphs, imageRunTarget, imageSearchCurrent, orderedIllustrations, isLibraryImage } from './writing-images';

const hasBody = section => imageParagraphs(section?.content).length > 0;
const canIllustrate = section => hasBody(section) && !section.locked;

function CatalogCard({ item, writing, target, scope, blocked, selected, onInserted }) {
  const [caption, setCaption] = useState(item.caption || item.originalCaption || item.alt || '');
  const [libraryConfirmed, setLibraryConfirmed] = useState(false);
  const library = isLibraryImage(item);
  return <article className={`writing-image-catalog-card ${selected ? 'is-selected' : ''}`}>
    <Picture value={{ ...item, altText: item.altText || item.alt }}/>
    <div className="writing-image-card-body">
      <strong>{item.sourceTitle || item.documentTitle || '参考资料图片'}</strong>
      <span className={`writing-image-source-kind ${library ? 'is-library' : ''}`}>{library ? '图库补充图片 · 仅作配图' : '本稿参考资料'}</span>
      {(item.originalCaption || item.alt) && <p className="writing-image-hint">{item.originalCaption || item.alt}</p>}
      <label>文章图注<textarea aria-label="文章图注" value={caption} maxLength={2000} rows={2} disabled={!!writing.busy || selected} onChange={event => setCaption(event.target.value)}/></label>
      {library && <label className="writing-image-confirm"><input type="checkbox" aria-label="确认图库图片仅作配图" checked={libraryConfirmed} disabled={!!writing.busy || selected} onChange={event => setLibraryConfirmed(event.target.checked)}/>这张图片仅作配图，不作为正文依据。</label>}
      <button type="button" className="btn primary" disabled={!!blocked || !!writing.busy || selected || item.scopeEligible === false || library && !libraryConfirmed} onClick={async () => {
        const result = await writing.insertManualIllustration({ sectionId: target.sectionId, paragraphIndex: target.paragraphIndex,
          assetId: item.assetId || item.id, searchScope: scope, paragraphExact: target.paragraphExact, contentHash: target.contentHash,
          ...(caption.trim() ? { caption } : {}), ...(item.altText || item.alt ? { altText: item.altText || item.alt } : {}),
          ...(library ? { confirmLibrarySource: libraryConfirmed } : {}) });
        if (result) onInserted?.(result, { ...target, assetId: item.assetId || item.id });
      }}><Plus size={16}/>{selected ? '已加入文稿' : writing.busy === 'manual-image' ? '正在插入…' : '插入图片'}</button>
    </div>
  </article>;
}

function ImagePicker({ writing, target, blocked, onClose, onOpenDoc, onToast, onInserted }) {
  const [scope, setScope] = useState('references'), [query, setQuery] = useState(''), [submittedQuery, setSubmittedQuery] = useState('');
  const [page, setPage] = useState(0), [refresh, setRefresh] = useState(0);
  const [catalog, setCatalog] = useState({ items: [], total: 0 }), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const dialogRef = useRef(null);
  const project = writing.project, section = project.sections.find(item => item.id === target.sectionId);
  const paragraph = imageParagraphs(section?.content)[target.paragraphIndex];
  const exact = paragraph === target.paragraphExact && section?.contentHash === target.contentHash;
  const actionBlocked = blocked || (!exact ? '正文已变化，请关闭后重新选择插入位置。' : '') || (section?.locked ? '本章已锁定，请先解锁并保存。' : '');
  useEffect(() => {
    const dialog = dialogRef.current, opener = document.activeElement;
    dialog.showModal();
    return () => { if (dialog.open) dialog.close(); if (opener?.isConnected) opener.focus({ preventScroll: true }); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    setLoading(true); setError(''); setCatalog({ items: [], total: 0 });
    const params = new URLSearchParams({ searchScope: scope, q: submittedQuery, limit: '60', offset: String(page * 60) });
    api(`/writing/projects/${project.id}/image-assets?${params}`, { signal: controller.signal }).then(result => {
      if (alive) setCatalog({ items: result.items || [], total: result.total || 0 });
    }).catch(problem => { if (alive && problem.name !== 'AbortError') setError(connectionMessage(problem)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; controller.abort(); };
  }, [project.id, scope, submittedQuery, page, refresh]);
  const runs = project.runs.filter(run => run.kind === 'images' && run.status === 'succeeded' && imageRunTarget(run)?.sectionId === target.sectionId
    && imageRunTarget(run)?.paragraphIndex === target.paragraphIndex && imageRunTarget(run)?.placementMode !== 'chapter');
  const historical = runs.find(run => imageSearchCurrent(project, run.candidate?.imageSearch));
  return <dialog ref={dialogRef} className="writing-image-picker" aria-label="选择配图" onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <header><div><h3>选择配图</h3><p>{section?.title} · 第 {target.paragraphIndex + 1} 段之后</p></div><button type="button" className="icon-btn" aria-label="关闭图片选择" onClick={onClose}><X size={20}/></button></header>
    <div className="writing-image-picker-toolbar">
      <label>图片范围<select aria-label="图片范围" value={scope} onChange={event => { setScope(event.target.value); setPage(0); }}><option value="references">参考资料</option><option value="library">全部图片</option></select></label>
      <form onSubmit={event => { event.preventDefault(); setSubmittedQuery(query.trim()); setPage(0); setRefresh(value => value + 1); }}><label htmlFor="writing-image-picker-query">检索图片</label><div><input id="writing-image-picker-query" value={query} maxLength={500} placeholder="输入图片说明或来源关键词" onChange={event => setQuery(event.target.value)}/><button type="submit" className="btn"><MagnifyingGlass size={16}/>检索</button></div></form>
    </div>
    <div className="writing-image-picker-scroll">
      <blockquote>{paragraph}</blockquote>
      <p className="writing-image-hint">选择图片后直接插入指定段落，可编辑图注。</p>
      {actionBlocked && <p className="writing-image-warning" role="status">{actionBlocked}</p>}
      {writing.error && <p className="writing-image-warning" role="alert">{imageBusinessText(writing.error, '图片未能插入，请重试。')}</p>}
      {loading ? <p role="status">正在读取图片…</p> : error ? <div role="alert"><p>{error}</p><button type="button" className="btn" onClick={() => setRefresh(value => value + 1)}>重试</button></div> : <>
        <p className="writing-image-catalog-count">共 {catalog.total} 张图片</p>
        <div className="writing-image-catalog">{catalog.items.map(item => <CatalogCard key={`${scope}:${item.sourceId}:${item.revisionId}:${item.assetId || item.id}`} item={item} writing={writing} target={target} scope={scope} blocked={actionBlocked} selected={project.illustrations?.some(figure => figure.assetId === (item.assetId || item.id))} onInserted={onInserted}/>)}</div>
        {!catalog.items.length && <div className="writing-images-empty"><Image size={28}/><strong>没有找到图片</strong><p>可以更换关键词或切换图片范围，也可以保留纯文字。</p></div>}
        {catalog.total > 60 && <nav className="writing-image-catalog-pages" aria-label="图片分页"><button type="button" className="btn quiet" disabled={!page} onClick={() => setPage(value => value - 1)}>上一页</button><span>{page + 1} / {Math.ceil(catalog.total / 60)}</span><button type="button" className="btn quiet" disabled={(page + 1) * 60 >= catalog.total} onClick={() => setPage(value => value + 1)}>下一页</button></nav>}
      </>}
      {historical && <details className="writing-images-previous"><summary>以前的段落推荐</summary><div className="writing-images-candidates">{historical.candidate.imageSearch.candidates.map(candidate => <CandidateCard key={`${historical.id}:${candidate.assetId}`} candidate={candidate} runId={historical.id} search={historical.candidate.imageSearch} blocked={actionBlocked} writing={writing} onOpenDoc={onOpenDoc} onToast={onToast} onInserted={onInserted} selected={project.illustrations?.some(figure => figure.assetId === candidate.assetId)}/>)}</div></details>}
    </div>
    <footer><span>插入后返回对应章节查看效果，已有配图保留。</span><button type="button" className="btn" onClick={onClose}>返回正文段落</button></footer>
  </dialog>;
}

function NoImageConfirmation({ onClose, onConfirm, disabled }) {
  const ref = useRef(null);
  useEffect(() => { const dialog = ref.current; dialog.showModal(); return () => { if (dialog.open) dialog.close(); }; }, []);
  return <dialog ref={ref} className="writing-image-skip-dialog" aria-label="确认本文不配图" onCancel={event => { event.preventDefault(); onClose(); }}>
    <h3>本文不配图</h3><p>确认后移除本文已有配图，保留正文和参考资料中的原图。</p>
    <div><button type="button" className="btn quiet" onClick={onClose}>保留配图</button><button type="button" className="btn primary" disabled={disabled} onClick={onConfirm}>移除配图并跳过</button></div>
  </dialog>;
}

function ImageWorkspace({ writing, models = [], initialSectionId, onClose, onNext, onOpenDoc, onToast }) {
  const { project, dirty, remoteChanged, busy, pending } = writing;
  const sections = project.sections || [];
  const [mode, setMode] = useState('automatic'), [sectionId, setSectionId] = useState(initialSectionId || sections[0]?.id || '');
  const [selectedIds, setSelectedIds] = useState(() => sections.filter(canIllustrate).map(section => section.id));
  const [scope, setScope] = useState('references'), [modelId, setModelId] = useState(''), [query, setQuery] = useState('');
  const [libraryConfirmed, setLibraryConfirmed] = useState(false), [target, setTarget] = useState(null), [skipConfirm, setSkipConfirm] = useState(false);
  const [insertion, setInsertion] = useState(null), [focusFigure, setFocusFigure] = useState(null);
  const contentRef = useRef(null);
  useEffect(() => { if (modelId && !models.some(model => model.id === modelId)) setModelId(''); }, [models, modelId]);
  const section = sections.find(item => item.id === sectionId) || sections[0];
  const paragraphs = useMemo(() => imageParagraphs(section?.content), [section?.content]);
  const previewSection = useMemo(() => buildWritingPreview(project).sections.find(item => item.id === section?.id), [project, section?.id]);
  const figures = orderedIllustrations(project), numberedFigures = figures.map((figure, index) => ({ ...figure, number: index + 1 }));
  const selected = sections.filter(item => selectedIds.includes(item.id) && canIllustrate(item));
  const allEligible = sections.filter(canIllustrate), allChecked = allEligible.length > 0 && selected.length === allEligible.length;
  const blocked = !project.id ? '请先保存文稿，再选择配图。' : dirty ? '有未保存修改，请先保存正文，再检索或调整配图。' : remoteChanged ? '已保存文稿有更新，请重新载入并核对。' : pending ? '写作任务正在进行，请等任务结束后继续。' : '';
  const staleCount = figures.filter(figure => figure.status === 'stale').length;
  const lockedPictures = figures.some(figure => figure.locked || sections.find(item => item.id === figure.sectionId)?.locked);
  const done = ['complete', 'skipped'].includes(project.illustrationReview?.status) && !dirty && !remoteChanged;
  const workflowRun = (project.runs || []).find(run => run.kind === 'image_batch');
  const activeRuns = (project.runs || []).filter(run => ['images', 'image_batch'].includes(run.kind) && ['queued', 'running'].includes(run.status));
  const batch = workflowRun?.candidate?.imageBatch;
  const orphaned = numberedFigures.filter(figure => figure.sectionId === section?.id && (!paragraphs[figure.anchor?.paragraphIndex] || figure.anchor.paragraphExact !== paragraphs[figure.anchor.paragraphIndex]));
  const inserted = (result, location) => {
    if (result.project?.id !== location.projectId) return;
    const figure = result.illustration || result.project.illustrations?.find(item => item.assetId === location.assetId && item.sectionId === location.sectionId);
    if (figure) setInsertion({ ...location, figureId: figure.id });
  };
  useEffect(() => {
    if (!insertion) return;
    const chapter = sections.find(item => item.id === insertion.sectionId);
    if (project.id !== insertion.projectId || dirty || remoteChanged || !chapter || chapter.contentHash !== insertion.contentHash
        || imageParagraphs(chapter.content)[insertion.paragraphIndex] !== insertion.paragraphExact) { setInsertion(null); return; }
    const figure = figures.find(item => item.id === insertion.figureId && item.sectionId === insertion.sectionId && item.status === 'accepted'
      && item.anchor?.contentHash === insertion.contentHash && item.anchor.paragraphIndex === insertion.paragraphIndex && item.anchor.paragraphExact === insertion.paragraphExact);
    if (!figure) { if (!busy) setInsertion(null); return; }
    setSectionId(insertion.sectionId); setMode('manual'); setTarget(null); setFocusFigure(insertion); setInsertion(null);
    onToast?.('图片已插入，已定位到对应章节。');
  }, [insertion, project, dirty, remoteChanged, busy]);
  useEffect(() => {
    if (!focusFigure || section?.id !== focusFigure.sectionId || target || dirty || remoteChanged) return;
    const frame = requestAnimationFrame(() => {
      const node = [...(contentRef.current?.querySelectorAll('[data-writing-figure-id]') || [])].find(item => item.dataset.writingFigureId === focusFigure.figureId);
      if (node) { node.scrollIntoView({ block: 'start', behavior: 'auto' }); node.focus({ preventScroll: true }); }
      setFocusFigure(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [focusFigure, section?.id, target, dirty, remoteChanged]);
  const finish = async () => {
    if (done || await writing.completeIllustrations({ skip: !figures.length })) { onToast?.(figures.length ? '配图已确认。' : '已确认本文不配图。'); onNext?.(); }
  };
  const skip = async clearExisting => {
    if (await writing.completeIllustrations({ skip: true, ...(clearExisting ? { clearExisting: true } : {}) })) { setSkipConfirm(false); onToast?.('已确认本文不配图。'); onNext?.(); }
  };
  const figureEditor = figure => <FigureEditor key={figure.id} figure={figure} number={figure.number} section={sections.find(item => item.id === figure.sectionId)} blocked={blocked} writing={writing} onOpenDoc={onOpenDoc} onToast={onToast}/>;
  return <section className={`writing-images-workspace writing-images-mode-${mode}`} aria-label="文章配图">
    <header className="writing-images-heading"><div><button type="button" className="btn quiet" onClick={onClose}><ArrowLeft size={16}/>返回正文</button><h2>{mode === 'automatic' ? '文章自动配图' : '文章手动配图'}</h2><p>宁缺毋滥，图片须直接对应正文。没有合适图片可以不配图。</p></div><span className="writing-images-total">{figures.length} 张配图</span></header>
    {blocked && <div className="writing-image-notice" role="status"><WarningCircle size={16}/><span>{blocked}</span>{dirty && <button type="button" className="btn" disabled={!!busy || remoteChanged || pending} onClick={writing.save}>保存正文</button>}</div>}
    <div className="writing-images-layout">
      <aside className="writing-images-chapters" aria-label="配图章节目录"><div className="writing-images-chapters-heading"><strong>章节目录</strong>{mode === 'automatic' && <label><input type="checkbox" aria-label="选择全部章节" checked={allChecked} disabled={!!busy || pending || !allEligible.length} onChange={event => setSelectedIds(event.target.checked ? allEligible.map(item => item.id) : [])}/>全选</label>}</div>
        <nav>{sections.map((item, index) => <div key={item.id} className={`writing-image-chapter ${section?.id === item.id ? 'is-active' : ''}`}>
          {mode === 'automatic' && <input type="checkbox" aria-label={`选择章节：${item.title}`} checked={selectedIds.includes(item.id) && canIllustrate(item)} disabled={!!busy || pending || !canIllustrate(item)} onChange={event => setSelectedIds(ids => event.target.checked ? [...ids, item.id] : ids.filter(id => id !== item.id))}/>}
          <button type="button" aria-current={section?.id === item.id ? 'location' : undefined} title={item.title} onClick={() => { setSectionId(item.id); contentRef.current?.scrollTo({ top: 0 }); }}><span>{item.title}</span><small>{item.locked ? '已锁定' : hasBody(item) ? `${figures.filter(figure => figure.sectionId === item.id).length} 张配图` : '尚无正文'}</small></button>
        </div>)}</nav>
      </aside>
      <main className="writing-images-scroll" ref={contentRef}>
        {activeRuns.map(run => <div className="writing-image-notice" role="status" key={run.id}><Image size={18}/><span>{run.status === 'queued' ? '配图任务正在排队。' : '正在核对图片与正文，通过后加入文稿。'}</span>{run.jobId && <button type="button" className="btn quiet" disabled={!!busy} onClick={() => writing.cancelRun(run.jobId)}>取消配图</button>}</div>)}
        {workflowRun?.status === 'failed' && <p className="writing-image-warning" role="alert">本次配图未完成，可以重试。{imageBusinessText(workflowRun.error)}</p>}
        {batch && <div className="writing-image-batch-result" role="status"><p>{imageBusinessText(batch.message, workflowRun.status === 'succeeded' ? '本次配图已处理，请检查已有配图后完成。' : '配图结果已保留。')}</p>{(batch.issues || []).map((issue, index) => <p className="writing-image-warning" key={index}>{imageBusinessText(issue, '部分图片未通过核对，已保留纯文字。')}</p>)}{workflowRun.status === 'succeeded' && mode === 'automatic' && <button type="button" className="btn" disabled={!!blocked || !!busy || !!staleCount} onClick={finish}>完成配图</button>}</div>}
        {mode === 'automatic' ? <>
          <section className="writing-images-config"><h3>配图配置</h3><p className="writing-images-selection-summary">已选择 {selected.length} 个章节，已有配图保留。</p>
            <label>配图范围<select aria-label="配图范围" value={scope} disabled={!!busy || pending} onChange={event => { setScope(event.target.value); setLibraryConfirmed(false); }}><option value="library">全部图片</option><option value="references">参考资料</option></select></label>
            <label>图片核对模型<select id="writing-image-model" aria-label="图片核对模型" value={modelId} disabled={!!busy || pending} onChange={event => setModelId(event.target.value)}><option value="">沿用写作模型</option>{models.map(model => <option key={model.id} value={model.id}>{model.name}{model.model ? ` · ${model.model}` : ''}</option>)}</select></label>
            <label htmlFor="writing-image-query">希望图片展示什么？<span>选填，留空时按所选章节内容推荐</span><input id="writing-image-query" value={query} maxLength={500} disabled={!!busy || pending} onChange={event => setQuery(event.target.value)} placeholder="例如：设备实物、结构示意图或原始数据图表"/></label>
            {scope === 'library' && <label className="writing-image-confirm"><input type="checkbox" aria-label="确认自动图库图片仅作配图" checked={libraryConfirmed} disabled={!!busy || pending} onChange={event => setLibraryConfirmed(event.target.checked)}/>全部图片中的图库补充图片仅作配图，不作为正文依据。</label>}
            <p className="writing-image-hint">自动核对图片内容、清晰度和适合的段落位置。未通过核对的图片不加入文稿，已锁定或尚无正文的章节不参与。</p>
          </section>
          <section className="writing-images-existing"><h3>已有配图 · {figures.length}</h3>{numberedFigures.length ? <div className="writing-images-placed">{numberedFigures.map(figureEditor)}</div> : <WritingImagesEmpty description="选择左侧章节后自动配图，也可以切换手动配图。"/>}</section>
        </> : <article className="writing-images-manual-body"><header><h3>{section?.title || '章节正文'}</h3><p>在段落之间选择插入位置，已有配图可继续调整。</p></header>
          {paragraphs.map((text, index) => <React.Fragment key={`${section.id}:${index}`}><CitedParagraph className="writing-image-paragraph" project={project} section={previewSection} sectionIndex={sections.findIndex(item => item.id === section.id)} paragraph={previewSection.paragraphs[index]} clean/>{numberedFigures.filter(figure => figure.sectionId === section.id && figure.anchor?.paragraphIndex === index && figure.anchor.paragraphExact === text).map(figureEditor)}<div className="writing-image-insert-gap"><button type="button" aria-label={`在第 ${index + 1} 段后插入图片`} disabled={!!blocked || !!busy || section.locked} title={section.locked ? '本章已锁定，请先解锁并保存。' : undefined} onClick={() => setTarget({ projectId: project.id, sectionId: section.id, paragraphIndex: index, paragraphExact: text, contentHash: section.contentHash })}><Plus size={16}/>插入图片</button></div></React.Fragment>)}
          {!paragraphs.length && <div className="writing-images-empty"><strong>本章尚无正文</strong><p>请先编写并保存正文。</p></div>}{orphaned.length > 0 && <section><h3>需要重新核对的配图</h3>{orphaned.map(figureEditor)}</section>}
        </article>}
      </main>
    </div>
    <footer className="writing-images-footer"><span>{staleCount ? `${staleCount} 张图片需要重新核对` : done ? project.illustrationReview.status === 'skipped' ? '已确认本文不配图' : '配图已确认完成' : mode === 'automatic' ? `已选 ${selected.length} 章 · ${figures.length} 张配图` : `${figures.length} 张配图 · 请核对图文关系`}</span><div>
      <button type="button" className="btn quiet" onClick={() => setMode(mode === 'automatic' ? 'manual' : 'automatic')}>{mode === 'automatic' ? '切换手动配图' : '切换自动（已有配图保留）'}</button>
      {mode === 'automatic' ? <button type="button" className="btn primary" disabled={!!blocked || !!busy || !selected.length || scope === 'library' && !libraryConfirmed} onClick={async () => { if (await writing.automaticIllustrations({ sectionIds: selected.map(item => item.id), searchScope: scope, modelId: modelId || null, query: query.trim(), ...(scope === 'library' ? { confirmLibrarySource: libraryConfirmed } : {}) })) onToast?.('所选章节已提交自动配图。'); }}><Image size={17}/>为所选章节自动配图</button> : <button type="button" className="btn primary" disabled={!!blocked || !!busy || !!staleCount || !sections.some(hasBody)} onClick={finish}><CheckCircle size={17}/>完成配图</button>}
      <button type="button" className="btn quiet" disabled={!!blocked || !!busy || !sections.some(hasBody) || lockedPictures} title={lockedPictures ? '本文含已锁定配图或章节，请先解锁后再选择不配图。' : undefined} onClick={() => figures.length ? setSkipConfirm(true) : skip(false)}>{mode === 'automatic' ? '跳过，本文不配图' : '本文不配图'}</button>
    </div></footer>
    {target && <ImagePicker key={`${project.id}:${target.sectionId}:${target.paragraphIndex}`} writing={writing} target={target} blocked={blocked} onInserted={inserted} onClose={() => setTarget(null)} onOpenDoc={onOpenDoc} onToast={onToast}/>}
    {skipConfirm && <NoImageConfirmation onClose={() => setSkipConfirm(false)} onConfirm={() => skip(true)} disabled={!!blocked || !!busy || lockedPictures}/>}
  </section>;
}

export default function WritingImages(props) {
  return <ImageWorkspace key={props.writing.project.id || 'unsaved'} {...props}/>;
}
