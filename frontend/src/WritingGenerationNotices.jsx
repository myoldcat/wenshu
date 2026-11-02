import React from 'react';
import { generationNotices } from './writing-notices';

export default function WritingGenerationNotices({ notices, additionalNotices, compact = false, collapsible = false }) {
  const messages = generationNotices(notices, additionalNotices);
  if (!messages.length) return null;
  if (collapsible) return <details className="writing-generation-notices writing-notices-disclosure" aria-label="生成提示"><summary>生成提示 · {messages.length} 项待留意</summary><ul>{messages.map(message => <li key={message}>{message}</li>)}</ul></details>;
  return <section className={`writing-generation-notices${compact ? ' compact' : ' writing-generation-banner'}`} aria-label="生成提示" role="status">
    <strong>生成提示</strong>
    <ul>{messages.map(message => <li key={message}>{message}</li>)}</ul>
  </section>;
}
