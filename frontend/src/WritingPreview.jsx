import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { CaretRight, ListBullets, LockSimple, Quotes } from '@phosphor-icons/react';
import { buildWritingPreview } from './writing-preview';
import { writingCitedSources } from './writing-cited-sources';
import { writingSourceNotice } from './writing-library-availability';
import { CitedParagraph, WritingCitationScope } from './WritingCitations';
import { WritingInlineFigures } from './WritingImages';
import { WritingReadingPanelHeader, WritingReadingPanelControls } from './WritingReadingPanels';
import './writing-preview-panels.css';

export default function WritingPreview({ project, dirty, initialSectionId, initialParagraphIndex, initialCitationId, onSectionChange, onOpenDoc }) {
  const preview = useMemo(() => buildWritingPreview(project), [project]);
  const citedSources = useMemo(() => writingCitedSources(preview), [preview]);
  const initial = preview.sections.find(section => section.id === initialSectionId) || preview.sections[0];
  const initialParagraph = initial?.paragraphs[Math.max(0, (initialParagraphIndex || 1) - 1)] || initial?.paragraphs[0];
  const [location, setLocation] = useState(() => ({ sectionId: initial?.id, paragraphId: initialParagraph?.id, citationId: initialCitationId || null }));
  const [citationEpoch, setCitationEpoch] = useState(0);
  const [panels, setPanels] = useState({ outline: true, references: true, revision: 0 });
  const panelId = useId(), outlineId = `writing-preview-outline-${panelId}`, referencesId = `writing-preview-references-${panelId}`;
  const sectionNodes = useRef(new Map()), paragraphNodes = useRef(new Map()), articleScroller = useRef(null);
  const outlinePanel = useRef(null), referencesPanel = useRef(null), outlineScroller = useRef(null), referenceScroller = useRef(null);
  const outlineToggle = useRef(null), referencesToggle = useRef(null), panelPositions = useRef({});
  const readingAnchor = useRef(null), pendingReadingAnchor = useRef(null), restoringLayout = useRef(false), restoreFrame = useRef(null);
  const notifySection = useRef(onSectionChange), reportedSection = useRef(null), explicitScrollTop = useRef(null);
  notifySection.current = onSectionChange;
  const selectedSection = preview.sections.find(section => section.id === location.sectionId) || preview.sections[0];
  const selectedParagraph = selectedSection?.paragraphs.find(paragraph => paragraph.id === location.paragraphId) || null;

  useEffect(() => { articleScroller.current?.focus({ preventScroll: true }); }, []);
  useEffect(() => {
    reportSection(location.sectionId);
  }, [location.sectionId]);
  useEffect(() => {
    // Scroll only this pane; do not move the editor or a source-reader overlay.
    const node = initialParagraphIndex ? paragraphNodes.current.get(initialParagraph?.id) : sectionNodes.current.get(initialSectionId);
    if (node && articleScroller.current && (initialParagraphIndex || initialSectionId !== preview.sections[0]?.id)) {
      articleScroller.current.scrollTop = node.offsetTop - 12;
      explicitScrollTop.current = articleScroller.current.scrollTop;
    }
  }, []);
  useLayoutEffect(() => {
    const anchor = pendingReadingAnchor.current, scroller = articleScroller.current;
    if (!anchor || !scroller) return;
    // Keep the same reading point as text reflows into the recovered space.
    // Retain this anchor through consecutive toggles, including a temporary
    // clamp when the wider article no longer needs its full scroll range.
    if (anchor.node?.isConnected && scroller.contains(anchor.node)) {
      scroller.scrollTop += anchor.node.getBoundingClientRect().top - scroller.getBoundingClientRect().top - anchor.offset;
    } else scroller.scrollTop = anchor.scrollTop;
    explicitScrollTop.current = scroller.scrollTop;
    for (const [key, node] of [['outline', outlineScroller.current], ['references', referenceScroller.current]]) {
      const position = panelPositions.current[key];
      if (panels[key] && node && position) {
        node.scrollTop = position.top;
        node.scrollLeft = position.left;
      }
    }
    pendingReadingAnchor.current = null;
    restoreFrame.current = requestAnimationFrame(() => { restoringLayout.current = false; });
    return () => cancelAnimationFrame(restoreFrame.current);
  }, [panels]);
  function togglePanel(key) {
    const scroller = articleScroller.current;
    if (scroller) {
      if (!readingAnchor.current || !readingAnchor.current.node?.isConnected) {
        const bounds = scroller.getBoundingClientRect(), readingLine = bounds.top + Math.min(80, bounds.height * 0.15);
        const node = [...paragraphNodes.current.values()].find(item => item.getBoundingClientRect().bottom > readingLine) || sectionNodes.current.get(location.sectionId);
        readingAnchor.current = { node, offset: node ? node.getBoundingClientRect().top - bounds.top : 0, scrollTop: scroller.scrollTop };
      }
      pendingReadingAnchor.current = readingAnchor.current;
    }
    const panel = key === 'outline' ? outlinePanel.current : referencesPanel.current;
    for (const [name, node] of [['outline', outlineScroller.current], ['references', referenceScroller.current]]) {
      if (panels[name] && node) panelPositions.current[name] = { top: node.scrollTop, left: node.scrollLeft };
    }
    if (panels[key]) {
      if (panel?.contains(document.activeElement)) (key === 'outline' ? outlineToggle : referencesToggle).current?.focus({ preventScroll: true });
    }
    cancelAnimationFrame(restoreFrame.current);
    restoringLayout.current = true;
    setPanels(previous => ({ ...previous, [key]: !previous[key], revision: previous.revision + 1 }));
  }
  function reportSection(sectionId) {
    if (!sectionId || reportedSection.current === sectionId) return;
    reportedSection.current = sectionId;
    notifySection.current?.(sectionId);
  }
  function selectSection(section) {
    readingAnchor.current = null;
    setCitationEpoch(value => value + 1);
    setLocation({ sectionId: section.id, paragraphId: section.paragraphs[0]?.id, citationId: null });
    reportSection(section.id);
    const node = sectionNodes.current.get(section.id);
    if (node && articleScroller.current) {
      articleScroller.current.scrollTo({ top: node.offsetTop, behavior: 'auto' });
      explicitScrollTop.current = articleScroller.current.scrollTop;
    }
  }
  function selectParagraph(section, paragraph, citationId = null, token = null, toggleHighlight = false) {
    readingAnchor.current = null;
    setLocation(previous => {
      const deselect = toggleHighlight && previous.sectionId === section.id && previous.paragraphId === paragraph.id;
      return { sectionId: section.id, paragraphId: deselect ? null : paragraph.id,
        citationId: deselect ? null : citationId, occurrence: deselect ? undefined : token?.start };
    });
    explicitScrollTop.current = articleScroller.current?.scrollTop ?? null;
    reportSection(section.id);
  }
  function onParagraphClick(event, section, paragraph) {
    if (event.defaultPrevented || event.button !== 0 || event.target.closest('button, a, summary, input, select, textarea, [role="button"], [contenteditable="true"]')) return;
    // Only an explicit click on the paragraph toggles its highlight. Citation
    // hover, keyboard source navigation and links keep their own interactions.
    selectParagraph(section, paragraph, null, null, true);
  }
  function followVisibleSection() {
    const scroller = articleScroller.current;
    if (!scroller || !preview.sections.length || restoringLayout.current) return;
    // An explicit chapter near the end may be clamped to the same scroll
    // position as later chapters. Keep that selection until the view moves.
    if (explicitScrollTop.current !== null && Math.abs(scroller.scrollTop - explicitScrollTop.current) < 1) return;
    explicitScrollTop.current = null;
    readingAnchor.current = null;
    const bounds = scroller.getBoundingClientRect(), readingLine = bounds.top + Math.min(80, bounds.height * 0.15);
    const visible = preview.sections.map(section => ({ section, bounds: sectionNodes.current.get(section.id)?.getBoundingClientRect() })).filter(item => item.bounds && item.bounds.bottom > bounds.top && item.bounds.top < bounds.bottom);
    if (!visible.length) return;
    const atEnd = scroller.scrollTop > 0 && scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop <= 2;
    const section = (atEnd ? visible.at(-1) : visible.filter(item => item.bounds.top <= readingLine).at(-1) || visible[0]).section;
    reportSection(section.id);
    // Scrolling within the same chapter retains the chosen paragraph and
    // citation occurrence; changing panel visibility never resets either.
    setLocation(previous => previous.sectionId === section.id ? previous : { sectionId: section.id, paragraphId: section.paragraphs[0]?.id, citationId: null });
  }
  const controls = <WritingReadingPanelControls outlineExpanded={panels.outline} referencesExpanded={panels.references} outlineId={outlineId} referencesId={referencesId} onToggleOutline={() => togglePanel('outline')} onToggleReferences={() => togglePanel('references')} outlineRef={outlineToggle} referencesRef={referencesToggle} label="全文预览显示设置"/>;
  return <WritingCitationScope clean resetKey={JSON.stringify([project.id, project.version, citationEpoch])} onOpenDoc={onOpenDoc}><section className="writing-preview writing-preview-panels" aria-label="全文预览" data-outline-collapsed={!panels.outline} data-references-collapsed={!panels.references}>
    <div className="writing-preview-grid">
      <nav id={outlineId} ref={outlinePanel} className="writing-preview-nav" aria-label="预览章节" hidden={!panels.outline}><WritingReadingPanelHeader icon={ListBullets}>文章目录</WritingReadingPanelHeader><div ref={outlineScroller} className="writing-preview-nav-scroll">{preview.sections.map((section, index) => <button key={section.id} className={selectedSection?.id === section.id ? 'active' : ''} aria-current={selectedSection?.id === section.id ? 'location' : undefined} onClick={() => selectSection(section)}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{section.title}</strong><small>{section.empty ? '尚无正文' : `${section.paragraphs.length} 段`}{section.locked ? ' · 已锁定' : ''}</small></div>{section.locked && <LockSimple size={13}/>}</button>)}</div></nav>
      <div className="writing-preview-content">
        {controls}
        <main ref={articleScroller} className="writing-preview-article" tabIndex={0} aria-label="全文内容" onScroll={followVisibleSection}><div className="writing-preview-title"><span>文章预览</span><h1>{preview.title}</h1></div>{preview.sections.map((section, sectionIndex) => <section key={section.id} ref={node => { if (node) sectionNodes.current.set(section.id, node); else sectionNodes.current.delete(section.id); }} className="writing-preview-chapter" aria-label={section.title}>{!section.introductionParagraphCount && <header><span>第 {sectionIndex + 1} 章</span>{section.locked && <span><LockSimple size={12}/>已锁定</span>}<h2>{section.title}</h2></header>}{section.empty ? <div className="writing-preview-empty">本章尚无正文。</div> : section.paragraphs.map(paragraph => <React.Fragment key={paragraph.id}>{section.introductionParagraphCount > 0 && paragraph.index === section.introductionParagraphCount && <header><span>第 {sectionIndex + 1} 章</span>{section.locked && <span><LockSimple size={12}/>已锁定</span>}<h2>{section.title}</h2></header>}<div ref={node => { if (node) paragraphNodes.current.set(paragraph.id, node); else paragraphNodes.current.delete(paragraph.id); }} className={`writing-preview-paragraph ${selectedParagraph?.id === paragraph.id && selectedSection?.id === section.id ? 'selected' : ''}`} onClick={event => onParagraphClick(event, section, paragraph)}><CitedParagraph clean project={project} section={section} paragraph={paragraph} sectionIndex={sectionIndex} onSelect={(citationId, token) => selectParagraph(section, paragraph, citationId, token)}/><WritingInlineFigures project={project} sectionId={section.id} paragraph={paragraph} dirty={dirty} onOpenDoc={onOpenDoc}/></div></React.Fragment>)}</section>)}{!preview.sections.length && <div className="writing-preview-empty">尚无章节，可返回编辑完善纲要。</div>}</main>
      </div>
      <aside id={referencesId} ref={referencesPanel} className="writing-preview-materials" aria-label="全文引用文献" hidden={!panels.references}>
        <WritingReadingPanelHeader icon={Quotes}>全文参考文献</WritingReadingPanelHeader>
        <div ref={referenceScroller} className="writing-preview-material-scroll">
          <section className="writing-reference-list" aria-label="全文实际引用文献">
            {citedSources.map(source => <article key={source.key} data-cited-reference={source.number}><div className="writing-reference-label"><span>[{source.number}]</span><span>全文引用文献</span></div><button type="button" className="writing-reference-title" disabled={!onOpenDoc || !!writingSourceNotice(source.sourceStatus)} onClick={() => onOpenDoc?.({ id: source.sourceId, revisionId: source.revisionId, matchBlockId: source.evidence.blockId })}>{source.title}<CaretRight size={14}/></button>{writingSourceNotice(source.sourceStatus) && <p className="writing-preview-warning">{writingSourceNotice(source.sourceStatus)}</p>}</article>)}
            {!citedSources.length && <p className="writing-preview-empty">全文尚未引用文献。</p>}
          </section>
        </div>
      </aside>
    </div>
  </section></WritingCitationScope>;
}
