import React from 'react';
import { getDocumentTags } from './document-tags.mjs';

const levels = ['一级分类', '二级分类', '三级分类'];

export function DocumentTag({ tag, onClick, disabled, ariaLabel, children }) {
  const label = tag.kind === 'language' ? String(tag.label || '').replace(/^语言[：:]\s*/, '') : tag.label;
  const kindLabel = tag.kind === 'classification' ? levels[tag.level - 1] : tag.kind === 'articleType' ? '文章类型' : tag.kind === 'language' ? '原文语言' : tag.kind === 'custom' ? '自定义标签' : '未归类';
  const description = `${kindLabel} · ${label}`;
  const props = {
    className: 'pill document-tag',
    'data-tag-kind': tag.kind,
    'data-tag-level': tag.level || undefined,
    title: [description, ...(tag.paths || [])].join('\n'),
    'aria-label': ariaLabel || description,
  };
  const content = <>{children}<span className="document-tag-label">{label}</span></>;
  return onClick ? <button type="button" {...props} disabled={disabled} onClick={onClick}>{content}</button> : <span {...props}>{content}</span>;
}

export function DocumentTags({ doc, onTagClick, kinds }) {
  return getDocumentTags(doc).filter(tag => !kinds || kinds.includes(tag.kind)).map(tag => <DocumentTag
    key={`${tag.kind}:${tag.level}:${tag.label}`} tag={tag}
    onClick={onTagClick && tag.kind !== 'language' ? () => onTagClick(tag.label, tag) : undefined}/>);
}

export function DocumentTagLegend() {
  return <div className="document-tag-legend" aria-label="标签颜色说明">
    {[1, 2, 3].map(level => <DocumentTag key={level} tag={{ kind: 'classification', level, label: ['一级', '二级', '三级'][level - 1] }}/>) }
    <DocumentTag tag={{ kind: 'custom', level: null, label: '自定义' }}/>
    <DocumentTag tag={{ kind: 'language', level: null, label: '语言' }}/>
    <DocumentTag tag={{ kind: 'articleType', level: null, label: '文章类型' }}/>
  </div>;
}
