import React from 'react';
import { ListBullets, Quotes } from '@phosphor-icons/react';
import './writing-reading-panels.css';

export function WritingReadingPanelHeader({ icon: Icon, children, actions }) {
  const icon = React.isValidElement(Icon) ? Icon : Icon ? <Icon size={20} aria-hidden="true"/> : null;
  return <header className="writing-reading-panel-header">
    {icon}
    <h3 className="writing-reading-panel-title">{children}</h3>
    {actions && <div className="writing-reading-panel-actions">{actions}</div>}
  </header>;
}

export function WritingReadingPanelControls({ outlineExpanded, referencesExpanded, outlineId, referencesId, onToggleOutline, onToggleReferences, outlineRef, referencesRef, label }) {
  const outlineLabel = outlineExpanded ? '收起文章目录' : '展开文章目录';
  const referencesLabel = referencesExpanded ? '收起参考文献' : '展开参考文献';
  return <div className="writing-reading-panel-controls writing-preview-panel-controls" role="group" aria-label={label || '阅读显示设置'}>
    <button type="button" ref={outlineRef} className="icon-btn writing-reading-panel-toggle writing-preview-panel-toggle" aria-label={outlineLabel} title={outlineLabel} aria-expanded={outlineExpanded} aria-controls={outlineId} onClick={onToggleOutline}><ListBullets size={16} aria-hidden="true"/></button>
    <button type="button" ref={referencesRef} className="icon-btn writing-reading-panel-toggle writing-preview-panel-toggle" aria-label={referencesLabel} title={referencesLabel} aria-expanded={referencesExpanded} aria-controls={referencesId} onClick={onToggleReferences}><Quotes size={16} aria-hidden="true"/></button>
  </div>;
}
