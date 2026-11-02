import React, { useEffect, useRef, useState } from 'react';
import { compactWritingTag, isWritingProjectActive, writingProjectCardImage, writingProjectCardProgress, writingProjectCardStatus, writingProjectPhase } from './writing-projects';

export function WritingProjectHeading({ project }) {
  const [failedUrl, setFailedUrl] = useState(null);
  const image = writingProjectCardImage(project);
  const showImage = image && image.url !== failedUrl;
  const title = project.title || project.topic || '未命名文稿';
  return <div className={`writing-project-heading${showImage ? ' has-image' : ''}`}>
    <h2 title={title}>{title}</h2>
    {showImage && <div className="writing-project-thumbnail"><img src={image.url} alt={image.altText || image.caption || '文稿配图'} loading="lazy" decoding="async" onError={() => setFailedUrl(image.url)}/></div>}
  </div>;
}

export function WritingProjectProgress({ project, exportReceipt }) {
  const { ratio, percent, completed, total, stageValues, stageIds, stageWeights, stageStates } = writingProjectCardProgress(project);
  const flowing = isWritingProjectActive(project) && ratio > 0;
  const phase = writingProjectPhase(project);
  const status = writingProjectCardStatus(project, exportReceipt);
  return <div className="writing-project-body-progress" data-phase={phase.id} title={phase.label}>
    <div className="writing-project-progress-line" data-motion={flowing ? 'flow' : 'point'} style={{ '--writing-progress-position': `${ratio * 100}%` }}>
      <progress value={percent} max={100} aria-label="写作进度" aria-valuetext={`整体 ${Math.round(percent)}%，${phase.label}；${total ? `正文 ${completed} / ${total} 章已有正文` : '尚未拟定纲要'}`}/>
      <span className="writing-project-progress-spectrum" aria-hidden="true" style={{ gridTemplateColumns: stageWeights.map(weight => `${weight}fr`).join(' ') }}>{stageValues.map((value, index) => <span key={stageIds[index]} className="writing-project-progress-segment" data-stage={stageIds[index]} data-state={stageStates[index]}><i style={{ width: `${value * 100}%` }}/></span>)}</span>
      {flowing && <span className="writing-project-progress-light" aria-hidden="true"><i/></span>}
      {ratio < 1 && <span className="writing-project-progress-point" aria-hidden="true"/>}
    </div>
    <span className="writing-project-progress-status">{status}</span>
  </div>;
}

export function WritingProjectChapterCount({ project }) {
  const sections = project.sections || [];
  const completed = sections.filter(section => section.content?.trim()).length;
  const previous = useRef(completed);
  const [change, setChange] = useState(0);
  useEffect(() => {
    if (previous.current !== completed) {
      previous.current = completed;
      setChange(value => value + 1);
    }
  }, [completed]);
  return <><strong aria-label={`已完成 ${completed} 章，共 ${sections.length} 章`}><span key={change} className={`writing-project-chapters-completed${change ? ' is-updated' : ''}`}>{completed}</span>/{sections.length}</strong><small>个章节</small></>;
}

export function WritingProjectTags({ project, modelLabel }) {
  const automatic = (project.automation || project.brief?.automation) === 'automatic';
  const tags = [
    { type: 'mode', category: '资料模式', value: ['expanded', 'extended'].includes(project.mode) ? '拓展模式' : '严格模式' },
    { type: 'automation', category: '写作方式', value: automatic ? '智能化写作' : '交互式写作' },
    { type: 'length', category: '文章长度', value: '约 ' + Number(project.targetWords || project.brief?.targetWords || 6000).toLocaleString() + ' 字' },
  ];
  return <div className="writing-project-tags" role="list" aria-label="写作设置">
    {tags.map(tag => <span key={tag.type} className={`writing-project-${tag.type}`} role="listitem" title={`${tag.category}：${tag.value}`} aria-label={`${tag.category}：${tag.value}`}>{compactWritingTag(tag.value)}</span>)}
  </div>;
}
