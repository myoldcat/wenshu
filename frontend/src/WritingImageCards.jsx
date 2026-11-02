import React, { useEffect, useRef, useState } from 'react';
import { CheckCircle, Image, LockSimple, LockSimpleOpen, Trash } from '@phosphor-icons/react';
import { writingFigureForView } from './writing-library-availability';
import { IMAGE_QUALITY_POLICY, imageBusinessText, imageCandidateBlock, imageCandidateExplanation, imageCandidatePlacements, imageParagraphs, imagePlacementMode, imageQualityBlock, illustrationsAt, isLibraryImage } from './writing-images';
import './writing-images.css';

export function Picture({ value, eager = false }) {
  const [failed, setFailed] = useState(false);
  const dialog = useRef(null);
  useEffect(() => { setFailed(false); }, [value.url, value.unavailableMessage]);
  return value.unavailableMessage || failed || !value.url ? <div className="writing-image-unavailable"><Image size={24}/><span>{value.unavailableMessage || '图片暂时无法显示，请查看来源后重试。'}</span></div>
    : <><button type="button" className="writing-image-enlarge" aria-label="放大查看图片" onClick={() => dialog.current?.showModal()}><img src={value.url} alt={value.altText || value.originalCaption || value.caption || '参考资料图片'} loading={eager ? 'eager' : 'lazy'} decoding="async" onError={() => setFailed(true)}/></button><dialog ref={dialog} className="writing-image-lightbox" aria-label="放大查看参考图片" onClick={event => { if (event.target === event.currentTarget) dialog.current?.close(); }}><header><strong>{value.caption || value.originalCaption || '参考图片'}</strong><button type="button" className="btn" onClick={() => dialog.current?.close()}>关闭图片</button></header><div><img src={value.url} alt={value.altText || value.originalCaption || '参考资料原图'}/></div><p>{value.sourceTitle ? `来源：${value.sourceTitle}` : '请结合原文核对图片'} · 大图可横向、纵向滚动查看</p></dialog></>;
}

export function WritingInlineFigures({ project, sectionId, paragraph, dirty, onOpenDoc }) {
  return illustrationsAt(project, sectionId, paragraph.index, paragraph.text, dirty).map(figure => writingFigureForView(project, figure)).map(figure => <figure key={figure.id} className="writing-inline-figure">
    <Picture value={figure}/><figcaption><strong>图 {figure.number}{figure.caption ? `　${figure.caption}` : ''}</strong><span>{figure.credit || (figure.sourceTitle ? `来源：${figure.sourceTitle}` : '来源待核对')}</span>{isLibraryImage(figure) && <span className="writing-image-library-note">图库补充图片 · 仅作配图，不作为正文依据</span>}{onOpenDoc && figure.sourceId && <button type="button" disabled={figure.sourceStatus !== 'available'} onClick={() => onOpenDoc({ id: figure.sourceId, revisionId: figure.revisionId })}>查看图片来源</button>}</figcaption>
  </figure>);
}

function ImageSource({ value, onOpenDoc }) {
  return <div className="writing-image-source"><button type="button" disabled={!!value.unavailableMessage || !value.sourceId || !onOpenDoc} onClick={() => onOpenDoc?.({ id: value.sourceId, revisionId: value.revisionId })}>{value.sourceTitle || '查看参考资料'}</button>{value.originalCaption && <p><span>原图注</span>{value.originalCaption}</p>}</div>;
}

export function CandidateCard({ candidate, runId, search, blocked, writing, onOpenDoc, onToast, onInserted, selected }) {
  const [reviewed, setReviewed] = useState(false);
  const [libraryConfirmed, setLibraryConfirmed] = useState(false);
  const [caption, setCaption] = useState(candidate.caption || candidate.originalCaption || '');
  const [altText, setAltText] = useState(candidate.altText || '');
  const placements = imageCandidatePlacements(writing.project, search, candidate);
  const [positionIndex, setPositionIndex] = useState(() => placements.findIndex(placement => placement.valid));
  const placement = placements[positionIndex], library = isLibraryImage(candidate), chapter = imagePlacementMode(search) === 'chapter';
  const requireQualityGate = search?.qualityPolicy === IMAGE_QUALITY_POLICY;
  const qualityBlocked = imageQualityBlock(candidate, requireQualityGate);
  const reviewDisabled = !!blocked || !!writing.busy || !!qualityBlocked || ((requireQualityGate || candidate.qualityGate?.policy === IMAGE_QUALITY_POLICY) && !placement?.valid);
  const reviewRequired = library || candidate.status === 'needs_review' || placement?.status === 'needs_review';
  const reason = blocked || imageCandidateBlock(candidate, reviewed, { placement, requirePlacement: true, confirmLibrarySource: libraryConfirmed, requireQualityGate });
  const { reasons, issues, placementReason } = imageCandidateExplanation(candidate, placement, { requireQualityGate });
  return <article className={`writing-image-candidate ${selected ? 'is-selected' : ''}`}>
    <Picture value={candidate}/>
    <div className="writing-image-card-body">
      <div className="writing-image-card-status"><strong>{selected ? '已加入文稿' : qualityBlocked ? '暂不采用' : candidate.status === 'unsuitable' ? '不适合当前内容' : reviewRequired ? '需要人工核对' : candidate.status === 'recommended' ? '可供选择' : '尚待检查'}</strong>{candidate.scopeEligible !== true && <span>超出可用范围</span>}</div>
      <span className={`writing-image-source-kind ${library ? 'is-library' : ''}`}>{library ? '图库补充图片' : '本稿参考资料'}</span>
      <ImageSource value={candidate} onOpenDoc={onOpenDoc}/>
      {reasons.length > 0 && <ul>{reasons.map((text, index) => <li key={index}>{text}</li>)}</ul>}
      {issues.length > 0 && <div className="writing-image-warning">{issues.map((text, index) => <p key={index}>{text}</p>)}</div>}
      {candidate.scopeEligible === false && <p className="writing-image-warning">{imageBusinessText(candidate.scopeReason, '这张图片超出本次可用的配图范围。')}</p>}
      {candidate.status !== 'unsuitable' && candidate.scopeEligible === true && <div className="writing-image-placement">
        {chapter && placements.length > 0 && <label>建议插入位置<select aria-label="建议插入位置" value={positionIndex} disabled={!!blocked || !!writing.busy || selected} onChange={event => { setPositionIndex(Number(event.target.value)); setReviewed(false); }}>{positionIndex < 0 && <option value={-1}>暂无可用位置</option>}{placements.map((item, index) => <option key={`${item.paragraphIndex}-${index}`} value={index} disabled={!item.valid}>第 {item.paragraphIndex + 1} 段之后{index === 0 ? ' · 首选' : ''}{item.status === 'needs_review' ? ' · 需核对' : ''}</option>)}</select></label>}
        {placement?.valid ? <><strong>{chapter ? '所选段落' : '插入位置'} · 第 {placement.paragraphIndex + 1} 段之后</strong><p className="writing-image-placement-excerpt">{placement.paragraphExact}</p><p className="writing-image-placement-reason">{placementReason}</p></> : <p className="writing-image-hint">尚未找到可靠且直接对应正文的插入位置。没有合适图片可以不配图。</p>}
      </div>}
      <label>文章图注<textarea aria-label="文章图注" rows={2} maxLength={2000} value={caption} onChange={event => setCaption(event.target.value)} disabled={!!writing.busy || selected}/></label>
      <label>图片文字说明<input aria-label="图片文字说明" value={altText} maxLength={2000} onChange={event => setAltText(event.target.value)} disabled={!!writing.busy || selected} placeholder="供无法查看图片的读者了解内容"/></label>
      {reviewRequired && candidate.status !== 'unsuitable' && candidate.scopeEligible === true && !selected && <label className="writing-image-confirm"><input type="checkbox" aria-label="已核对图片与所选段落" checked={reviewed} onChange={event => setReviewed(event.target.checked)} disabled={reviewDisabled}/>我已查看原图与来源，核对了对象、时间和图表口径，确认适合所选段落。</label>}
      {library && candidate.status !== 'unsuitable' && candidate.scopeEligible === true && !selected && <label className="writing-image-confirm writing-image-library-confirm"><input type="checkbox" aria-label="确认图库图片仅作配图" checked={libraryConfirmed} onChange={event => setLibraryConfirmed(event.target.checked)} disabled={reviewDisabled}/>这张图库补充图片仅作配图，不作为正文依据。</label>}
      {reason && !selected && <p className="writing-image-hint">{reason}</p>}
      <button type="button" className="btn primary" disabled={!!reason || !!writing.busy || selected} onClick={async () => {
        const result = await writing.acceptIllustration({ runId, assetId: candidate.assetId, paragraphIndex: placement.paragraphIndex, caption, altText,
          confirmUnverified: reviewed || caption !== (candidate.caption || candidate.originalCaption || '') || altText !== (candidate.altText || ''), ...(library ? { confirmLibrarySource: libraryConfirmed } : {}) });
        if (result) { onToast?.('图片已加入文稿，可以继续检查位置和图注。'); onInserted?.(result, { projectId: writing.project.id, sectionId: search.sectionId, assetId: candidate.assetId, paragraphIndex: placement.paragraphIndex, paragraphExact: placement.paragraphExact, contentHash: placement.contentHash }); }
      }}>{selected ? <CheckCircle size={16}/> : <Image size={16}/>} {selected ? '已加入' : writing.busy === 'accept-image' ? '正在加入…' : chapter ? '插入所选位置' : '插入本段之后'}</button>
    </div>
  </article>;
}

export function FigureEditor({ figure, number, section, blocked, writing, onOpenDoc, onToast }) {
  figure = writingFigureForView(writing.project, figure);
  const paragraphs = imageParagraphs(section?.content);
  const [caption, setCaption] = useState(figure.caption || ''), [altText, setAltText] = useState(figure.altText || '');
  const [paragraphIndex, setParagraphIndex] = useState(figure.anchor?.paragraphIndex ?? 0), [remove, setRemove] = useState(false);
  useEffect(() => { setCaption(figure.caption || ''); setAltText(figure.altText || ''); setParagraphIndex(figure.anchor?.paragraphIndex ?? 0); }, [figure.id, figure.caption, figure.altText, figure.anchor?.paragraphIndex, figure.status]);
  const changed = caption !== (figure.caption || '') || altText !== (figure.altText || '') || paragraphIndex !== figure.anchor?.paragraphIndex;
  const disabled = !!blocked || !!writing.busy || !!section?.locked;
  return <article className="writing-image-saved" data-writing-figure-id={figure.id} tabIndex={-1}><div className="writing-image-saved-picture"><Picture value={figure}/></div><div className="writing-image-card-body"><div className="writing-image-card-status"><strong>图 {number} · {section?.title || '原章节已变化'}</strong><span>{figure.status === 'stale' ? '需要重新核对' : figure.locked ? '已锁定' : '已加入文稿'}</span></div>{isLibraryImage(figure) && <span className="writing-image-source-kind is-library">图库补充图片 · 仅作配图，不作为正文依据</span>}<ImageSource value={figure} onOpenDoc={onOpenDoc}/>{figure.status === 'stale' && <p className="writing-image-warning">正文或材料已变化，请核对图片、图注与位置，确认后重新保存。</p>}<label>放置位置<select aria-label="放置位置" value={paragraphIndex} disabled={disabled || figure.locked} onChange={event => setParagraphIndex(Number(event.target.value))}>{paragraphs.map((text, index) => <option key={index} value={index}>第 {index + 1} 段之后 · {text.replace(/\s+/g, ' ').slice(0, 48)}</option>)}</select></label><label>文章图注<textarea aria-label="文章图注" rows={2} value={caption} maxLength={2000} disabled={disabled || figure.locked} onChange={event => setCaption(event.target.value)}/></label><label>图片文字说明<input aria-label="图片文字说明" value={altText} maxLength={2000} disabled={disabled || figure.locked} onChange={event => setAltText(event.target.value)}/></label><p className="writing-image-hint">{section?.locked ? '本章已锁定，请先解锁并保存正文，再调整配图。' : '请核对图注与原图一致；保存表示已确认图片与所选段落适配。'}</p><div className="writing-image-card-actions"><button type="button" className="btn" disabled={disabled || figure.locked || !paragraphs[paragraphIndex] || (!changed && figure.status !== 'stale')} onClick={async () => { if (await writing.updateIllustration(figure.id, { caption, altText, paragraphIndex, confirmUnverified: true })) onToast?.('配图位置与文字已保存。'); }}>保存配图修改</button><button type="button" className="btn quiet" disabled={disabled} onClick={() => writing.updateIllustration(figure.id, { locked: !figure.locked })}>{figure.locked ? <LockSimpleOpen size={15}/> : <LockSimple size={15}/>} {figure.locked ? '解锁' : '锁定'}</button><button type="button" className="btn quiet" disabled={disabled || figure.locked} onClick={() => setRemove(true)}><Trash size={15}/>移除</button></div>{remove && <div className="writing-image-remove-confirm" role="group" aria-label="确认移除配图"><p>从这篇文稿中移除图片？参考资料中的原图会保留。</p><button className="btn quiet" disabled={disabled} onClick={() => setRemove(false)}>保留</button><button className="btn" disabled={disabled || figure.locked} onClick={() => writing.removeIllustration(figure.id)}>确认移除</button></div>}</div></article>;
}

