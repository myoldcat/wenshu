import React, { createContext, useContext, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { BookOpen, CaretRight, Quotes, WarningCircle, X } from '@phosphor-icons/react';
import { writingSourceNotice } from './writing-library-availability';
import './writing-citations.css';

export const citationLabels = { registered: '已关联原文', unknown: '未找到对应来源', out_of_scope: '不在当前资料范围', invalid_anchor: '来源定位信息不完整', ambiguous: '引用编号重复，无法确定来源' };
export const citationBindingLabels = { supported: '已定位支持句', unreviewed: '原文已定位，自动核对未完成', partial: '材料仅支持部分论述', unsupported: '尚未找到支持材料', uncertain: '引用对应句待核实', unmatched: '仅关联段落，尚未逐句核对', stale: '正文已修改，请重新核对引用', invalid: '引用与正文的对应关系需核对' };
const CitationContext = createContext(null);

export function WritingCitationBindingIssues({ paragraph }) {
  const statuses = [...new Set(paragraph.tokens.filter(token => token.type === 'citation' && token.bindingStatus !== 'supported').map(token => token.bindingStatus))];
  return <>{statuses.map(status => <p key={status} className="writing-citation-binding-notice"><WarningCircle size={13}/>{citationBindingLabels[status]}；悬停引用图标可查看所引原文。</p>)}{!!paragraph.diagnostics.length && <details className="writing-citation-binding-issues"><summary>{paragraph.diagnostics.length} 句论述的引用需核对</summary><ul>{paragraph.diagnostics.map((item, index) => <li key={item.id || index}><q>{item.text}</q><span>{item.status === 'unreviewed' && !item.evidenceIds?.length ? '自动核对未完成' : citationBindingLabels[item.status]}</span>{item.reason && <p>{item.reason.replace(/\bE\d+(?:[、/]\s*E\d+)*\b/g, '相关摘录')}</p>}</li>)}</ul></details>}</>;
}

export function citationLocator(evidence = {}) {
  const locator = evidence.locator;
  if (typeof locator === 'string' && !locator.startsWith('/') && !/[<>\[\]{}]/.test(locator)) return locator;
  
  if (Number.isFinite(locator?.page)) return `第 ${locator.page} 页${locator.paragraph ? ` · 第 ${locator.paragraph} 段` : ''}`;
  if (Number.isFinite(locator?.paragraph)) return `第 ${locator.paragraph} 段`;
  if (Number.isFinite(locator?.ordinal)) return `第 ${locator.ordinal + 1} 段`;
  return evidence.blockId ? '打开原文查看对应段落' : '缺少原文位置';
}

// One popover per writing surface. Its identity contains the project, chapter,
// paragraph and token occurrence; E1 alone is never a cross-chapter lookup key.
export function WritingCitationScope({ children, resetKey, onOpenDoc, clean = false }) {
  const [active, setActive] = useState(null), [position, setPosition] = useState(null), [selected, setSelected] = useState(null);
  const activeRef = useRef(null), panel = useRef(null), closeTimer = useRef(null), suppressFocus = useRef(null), suppressPreview = useRef(null);
  const popupId = useId();
  activeRef.current = active;
  function cancelClose() { clearTimeout(closeTimer.current); }
  function close(restoreFocus = false) {
    cancelClose(); const trigger = activeRef.current?.trigger;
    activeRef.current = null; setActive(null); setPosition(null);
    if (restoreFocus && trigger?.isConnected) {
      suppressFocus.current = trigger;
      trigger.focus({ preventScroll: true });
    }
  }
  function open(entry, intent) {
    cancelClose();
    if ((intent === 'focus' || intent === 'hover') && suppressPreview.current === entry.trigger) return;
    if (intent === 'focus' && suppressFocus.current === entry.trigger) return;
    if (intent === 'hover') suppressFocus.current = null;
    setActive(previous => {
      if (intent !== 'click' && intent !== 'keyboard' && previous?.pinned) return previous;
      if (intent === 'click' && previous?.key === entry.key && previous.pinned) return null;
      return { ...entry, pinned: intent === 'click' || intent === 'keyboard' || (previous?.key === entry.key && previous.pinned) };
    });
  }
  function select(entry, keepPreview = false) {
    suppressPreview.current = entry.trigger;
    setSelected(entry);
    // ArrowDown promotes an existing focus preview to an interactive dialog.
    // Closing it first would clear placement while retaining the same key, so
    // the layout effect would not rerun and the dialog could remain hidden.
    if (!keepPreview) close();
  }
  function scheduleClose() {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      const current = activeRef.current;
      if (current && !current.pinned && document.activeElement !== current.trigger && !panel.current?.contains(document.activeElement)) close();
    }, 180);
  }
  useEffect(() => { close(); setSelected(null); suppressPreview.current = null; }, [resetKey]);
  useEffect(() => {
    const observer = new MutationObserver(() => close());
    const libraryChanged = () => close();
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    window.addEventListener('wenshu-library-changed', libraryChanged);
    return () => { observer.disconnect(); cancelClose(); window.removeEventListener('wenshu-library-changed', libraryChanged); };
  }, []);
  useEffect(() => {
    if (!active) return undefined;
    const outside = event => { if (!panel.current?.contains(event.target) && !activeRef.current?.trigger?.contains(event.target)) close(); };
    const escape = event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); } };
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', escape, true);
    return () => { document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', escape, true); };
  }, [!!active]);
  useLayoutEffect(() => {
    if (!active) return undefined;
    function place(event) {
      if (event?.type === 'scroll' && panel.current?.contains(event.target)) return;
      const trigger = activeRef.current?.trigger;
      if (!trigger?.isConnected) { close(); return; }
      const anchor = trigger.getBoundingClientRect();
      if (anchor.bottom < 0 || anchor.top > window.innerHeight) { close(); return; }
      const width = Math.min(420, window.innerWidth - 24), viewportCap = Math.min(580, window.innerHeight - 24), gap = 10;
      const measuredHeight = Math.min(panel.current?.offsetHeight || 340, viewportCap);
      const below = window.innerHeight - anchor.bottom - gap - 12;
      const above = anchor.top - gap - 12;
      const showBelow = below >= measuredHeight || below >= above;
      const maxHeight = Math.min(viewportCap, Math.max(100, showBelow ? below : above));
      const height = Math.min(measuredHeight, maxHeight);
      const top = showBelow ? anchor.bottom + gap : anchor.top - height - gap;
      setPosition({ left: Math.max(12, Math.min(anchor.left - 20, window.innerWidth - width - 12)), top: Math.max(12, top), width, maxHeight });
    }
    place(); window.addEventListener('resize', place); document.addEventListener('scroll', place, true);
    const observer = new ResizeObserver(() => place()); if (panel.current) observer.observe(panel.current);
    return () => { observer.disconnect(); window.removeEventListener('resize', place); document.removeEventListener('scroll', place, true); };
  }, [active?.key]);
  const citation = active?.citation, evidence = citation?.evidence;
  const sourceNotice = writingSourceNotice(citation?.sourceStatus);
  const sourceReady = !sourceNotice && evidence?.sourceId && evidence?.revisionId && evidence?.blockId && citation.status !== 'ambiguous';
  return <CitationContext.Provider value={{ active, selected, popupId, open, select, cancelClose, scheduleClose, suppressFocus, suppressPreview, panel }}>
    {children}
    {active && createPortal(<aside ref={panel} id={popupId} role="dialog" aria-label={`引用 ${citation.number ? `[${citation.number}]` : citation.id} 的来源`} className="writing-citation-popover" data-citation-status={citation.status} style={{ ...position, visibility: position ? 'visible' : 'hidden' }} onMouseEnter={cancelClose} onMouseLeave={scheduleClose} onFocus={cancelClose} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) scheduleClose(); }}>
      <header><div><Quotes size={17} weight="fill"/><strong>引用 {citation.number ? `[${citation.number}]` : '未编号'}</strong><small>{active.pinned ? '已固定' : '来源预览'}</small></div><button type="button" className="icon-btn" aria-label="关闭引用来源" onClick={() => close(true)}><X size={17}/></button></header>
      <div className="writing-citation-popover-content">
        {sourceNotice && <p className="writing-citation-status attention"><WarningCircle size={14}/>{sourceNotice}</p>}
        {!clean && <p className={`writing-citation-status ${citation.status !== 'registered' ? 'attention' : ''}`}>{citation.status !== 'registered' && <WarningCircle size={14}/>} {citationLabels[citation.status]}</p>}
        {!clean && citation.bindingStatus && <p className={`writing-citation-status ${citation.bindingStatus !== 'supported' ? 'attention' : ''}`}>{citationBindingLabels[citation.bindingStatus]}</p>}
        {evidence ? <><h3>{active.sourceTitle || evidence.title || '来源资料'}</h3><blockquote>{evidence.quote || '没有保存摘录'}</blockquote>{!clean && citation.status === 'out_of_scope' && <p className="writing-citation-explanation">这条资料或版本不在当前项目确认的范围内，不能作为当前有效依据。</p>}{!clean && citation.status === 'invalid_anchor' && <p className="writing-citation-explanation">来源记录缺少完整的原文位置，请核对后再使用。</p>}<dl><div><dt>引用原文</dt><dd>{evidence.revisionId ? '写作时选定的原文' : '缺少所用原文记录'}</dd></div><div><dt>原文位置</dt><dd>{citationLocator(evidence)}</dd></div></dl></> : <p className="writing-citation-explanation">{clean ? '暂无可显示的来源原文。' : citation.status === 'ambiguous' ? '本章有多条同名引用记录，无法唯一确定对应材料。' : '本章没有找到这个引用编号对应的来源，请重新插入引用。'}</p>}
        {!clean && <p className="writing-citation-notice">{citation.bindingStatus === 'supported' ? '正文高亮的是本次核对所匹配的句子，仍请结合上下文核实。' : citation.bindingStatus === 'unreviewed' ? '已定位到所引原文；本次自动核对未完成，尚未确认它是否支持对应论述。' : '当前引用尚未定位到经核对的具体句子，不代表最后一句或整段已获材料支持。'}</p>}
      </div>
      {evidence && <footer><button type="button" className="btn" disabled={!sourceReady || !onOpenDoc} onClick={() => { const target = { id: evidence.sourceId, revisionId: evidence.revisionId, matchBlockId: evidence.blockId }; close(); onOpenDoc?.(target); }}><BookOpen size={15}/>查看写作时选定的原文<CaretRight size={14}/></button></footer>}
    </aside>, document.body)}
  </CitationContext.Provider>;
}

export function CitedParagraph({ project, section, paragraph, sectionIndex, onSelect, className = '', clean = false }) {
  const context = useContext(CitationContext);
  const displayText = value => clean ? value.replace(/【模型补充[^】]*】/g, '') : value;
  const activeRange = context?.selected?.paragraphId === paragraph.id && context.selected.sectionId === section.id && context.selected.content === section.content ? context.selected.range : null;
  return <p className={`writing-cited-paragraph ${className}`}>{paragraph.tokens.map((token, index) => {
    if (token.type === 'text') {
      const start = activeRange && Math.max(token.start, activeRange.start), end = activeRange && Math.min(token.end, activeRange.end);
      return activeRange && end > start ? <React.Fragment key={index}>{displayText(token.text.slice(0, start - token.start))}<mark className="writing-citation-body-highlight">{displayText(token.text.slice(start - token.start, end - token.start))}</mark>{displayText(token.text.slice(end - token.start))}</React.Fragment> : <React.Fragment key={index}>{displayText(token.text)}</React.Fragment>;
    }
    const citation = { ...(paragraph.citations.find(item => item.id === token.id) || { id: token.id, status: 'unknown' }), bindingStatus: token.bindingStatus, bindingText: token.bindingText };
    const key = JSON.stringify([project.id, section.id, paragraph.id, index]);
    const expanded = context?.active?.key === key, selected = context?.selected?.key === key;
    const title = citation.evidence?.title || project.sources?.find(source => source.sourceId === citation.evidence?.sourceId && source.revisionId === citation.evidence?.revisionId)?.title;
    const entry = trigger => ({ key, citation, sourceTitle: title, trigger, sectionId: section.id, paragraphId: paragraph.id, content: section.content, range: token.range });
    return <button type="button" key={index} className={`writing-citation-icon ${clean || citation.status === 'registered' && token.bindingStatus === 'supported' ? '' : 'attention'} ${selected ? 'selected' : ''}`} data-evidence-id={token.id} data-citation-number={citation.number} data-citation-occurrence={token.start} data-binding-status={token.bindingStatus} aria-label={clean ? `引用 ${citation.number || '未编号'}：${title || '查看来源原文'}` : `第 ${sectionIndex + 1} 章第 ${paragraph.index + 1} 段引用 ${citation.number ? `[${citation.number}]` : '未编号'}：${citationLabels[citation.status]}，${citationBindingLabels[token.bindingStatus]}`} aria-haspopup="dialog" aria-expanded={expanded} aria-controls={expanded ? context.popupId : undefined}
      onMouseEnter={event => context?.open(entry(event.currentTarget), 'hover')} onMouseLeave={() => { if (context) context.suppressPreview.current = null; context?.scheduleClose(); }}
      onFocus={event => context?.open(entry(event.currentTarget), 'focus')} onBlur={() => { if (context) { context.suppressFocus.current = null; context.suppressPreview.current = null; } context?.scheduleClose(); }}
      onMouseDown={event => event.preventDefault()}
      onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); onSelect?.(token.id, token); context?.select(entry(event.currentTarget), true); context?.open(entry(event.currentTarget), 'keyboard'); requestAnimationFrame(() => context?.panel.current?.querySelector('button')?.focus()); } }}>
      {clean || citation.status === 'registered' && token.bindingStatus === 'supported' ? <Quotes size={12} weight="fill" aria-hidden="true"/> : <WarningCircle size={13} weight="bold" aria-hidden="true"/>}
      <span aria-hidden="true">{citation.number || '?'}</span>
    </button>;
  })}</p>;
}

// Both chapter reading and whole-document preview use the same exact excerpt.
export function WritingCitationSourceCard({ citation, project, active = false, onOpenDoc, cardRef, clean = false }) {
  const evidence = citation.evidence;
  const sourceNotice = writingSourceNotice(citation.sourceStatus);
  return <article ref={cardRef} data-evidence-id={citation.id} className={`writing-preview-source-card ${active ? 'active' : ''}`}>
    {sourceNotice && <p className="writing-preview-warning">{sourceNotice}</p>}
    <div className="writing-preview-source-heading"><span>{citation.number ? `[${citation.number}]` : '未编号'}</span>{!clean && <small className={citation.status !== 'registered' ? 'writing-preview-attention' : ''}>{citationLabels[citation.status]}</small>}</div>
    {!clean && citation.bindingStatus && <p className={`writing-citation-status ${citation.bindingStatus !== 'supported' ? 'attention' : ''}`}>{citationBindingLabels[citation.bindingStatus]}</p>}
    {citation.bindingText && <div className="writing-citation-matched-sentence"><span>对应正文</span><q>{citation.bindingText}</q></div>}
    {evidence ? <><h4>{evidence.title || project.sources?.find(source => source.sourceId === evidence.sourceId && source.revisionId === evidence.revisionId)?.title || '来源资料'}</h4>{!clean && citation.status === 'out_of_scope' && <p className="writing-preview-warning">这条引用已不在本次选定的资料范围内，请重新核对材料。</p>}{!clean && citation.status === 'invalid_anchor' && <p className="writing-preview-warning">这条记录缺少有效的来源定位信息，无法确认对应原文位置。</p>}<blockquote>{evidence.quote || '没有保存摘录'}</blockquote><details className="writing-preview-source-location"><summary>原文位置</summary><dl><div><dt>引用原文</dt><dd>{evidence.revisionId ? '写作时选定的原文' : '缺少所用原文记录'}</dd></div><div><dt>原文位置</dt><dd>{citationLocator(evidence)}</dd></div></dl></details><button type="button" className="btn" disabled={!!sourceNotice || !evidence.sourceId || !evidence.revisionId || !evidence.blockId || !onOpenDoc} onClick={() => onOpenDoc?.({ id: evidence.sourceId, revisionId: evidence.revisionId, matchBlockId: evidence.blockId })}><BookOpen size={15}/>查看写作时选定的原文<CaretRight size={14}/></button></> : <p>{clean ? '暂无可显示的来源原文。' : citation.status === 'ambiguous' ? '本章有多条同名引用记录，无法唯一确定对应材料。' : '本章没有找到这个引用编号对应的来源，请重新插入引用。'}</p>}
  </article>;
}

// Move only the material pane. scrollIntoView would also move the manuscript.
export function scrollCitationCard(node, container) {
  if (!node || !container) return;
  const top = container.scrollTop + node.getBoundingClientRect().top - container.getBoundingClientRect().top - 12;
  container.scrollTo({ top: Math.max(0, top), behavior: 'auto' });
}
