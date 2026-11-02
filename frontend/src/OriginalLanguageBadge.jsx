import React from 'react';
import { originalLanguageBadge } from './translation-view.mjs';
import './document-language.css';

export function OriginalLanguageBadge({ doc }) {
  const label = originalLanguageBadge(doc);
  return label ? <span className="document-language-badge" title={`原文语言：${label}`} aria-label={`原文语言：${label}`}>{label}</span> : null;
}
