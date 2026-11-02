import React from 'react';
import { Image } from '@phosphor-icons/react';
import { buildWritingPreview } from './writing-preview';
import { WritingInlineFigures } from './WritingImageCards';
import WritingImagesEmpty from './WritingImagesEmpty';

export { default } from './WritingImageModes';
export { WritingInlineFigures } from './WritingImageCards';

export function WritingImagesOverview({ writing, readOnly = false, onStart, onOpenDoc }) {
  const { project, dirty, remoteChanged, busy, pending, loading } = writing;
  const figures = project.illustrations || [], skipped = project.illustrationReview?.status === 'skipped';
  const blocked = loading ? '正在读取文稿，请稍后。' : readOnly ? '智能化写作正在进行，请结束后操作。'
    : busy || pending || project.runs?.some(run => ['queued', 'running'].includes(run.status)) ? '写作任务正在进行，请结束后配图。'
      : dirty ? '有未保存修改，请先保存正文。' : remoteChanged ? '已保存文稿有更新，请重新载入后配图。'
        : !project.id ? '请先保存文稿。' : !project.sections?.some(section => section.content?.trim() && !section.locked) ? '请先保存未锁定章节的正文。' : '';
  const action = <button type="button" className="btn primary" data-writing-start-images disabled={!!blocked} title={blocked || undefined} onClick={() => { if (!blocked) onStart?.(); }}><Image size={17}/>{figures.length ? '继续配图' : '开始配图'}</button>;
  return <section className="writing-automatic-materials-readonly writing-images-overview" aria-label="当前文章配图">
    <header><div><h1>文章配图</h1><p>{skipped ? '本次不配图，文稿保留纯文字。' : readOnly ? '智能化写作正在进行，已完成的配图可在此查看。' : '文章配图已完成，可在这里查看已确认的图片。'}</p></div>{figures.length > 0 && action}</header>
    {!figures.length ? <WritingImagesEmpty title={skipped ? '纯文字也能完整表达' : '本文尚未配图'} description="需要时可选择章节与图片范围，再开始配图。没有合适图片时继续保留纯文字。">{action}{blocked && <p className="writing-image-hint" role="status">{blocked}</p>}</WritingImagesEmpty>
      : buildWritingPreview(project).sections.map(section => <section key={section.id}>{section.paragraphs.map(paragraph => <WritingInlineFigures key={paragraph.id} project={project} sectionId={section.id} paragraph={paragraph} dirty={dirty} onOpenDoc={onOpenDoc}/>)}</section>)}
  </section>;
}
