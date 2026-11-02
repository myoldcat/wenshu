import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, ArrowUpRight, CaretLeft, CaretRight, ChatsCircle, Clock, Files, FileText, ListChecks, MagnifyingGlass, NotePencil, Pause, Play, Plus, Stack, Star, UploadSimple } from '@phosphor-icons/react';
import HomeOrnament from './HomeOrnament';
import HomeMetricEmblem from './HomeMetricEmblem';
import { HomeRecentCard } from './HomeRecentCard';
import HomeWritingProgress from './HomeWritingProgress';
import { formatWritingProjectTime, writingProjectCardImage, writingProjectCardStatus, writingProjectPhase } from './writing-projects';
import { continuingProjects, favoriteDocuments, intakeSeries, recentDocuments, topicDistribution } from './homepage-model.mjs';
import './homepage.css';
import './home-metric-palette.css';

const number = value => Number(value).toLocaleString('zh-CN');
const shortNumber = value => value >= 10000 ? `${(value / 10000).toFixed(value < 100000 ? 1 : 0)}万` : number(value);
const phases = [
  { id: 'brief', label: '写作准备', color: '#ca8585' },
  { id: 'outline', label: '写作纲要', color: '#d7a66b' },
  { id: 'body', label: '正文写作', color: '#7fae94' },
  { id: 'images', label: '文章配图', color: '#81a5bd' },
  { id: 'review', label: '全文校验', color: '#a496bc' },
  { id: 'ready', label: '校验已完成', color: '#548c78' },
];
const docTitle = doc => doc.title || doc.originalTitle || '未命名资料';
const projectTitle = project => project.title || project.topic || '未命名文稿';
const timestampLabel = value => value ? formatWritingProjectTime(value, { compact: true }) : '未记录时间';

function useReducedMotion() {
  const [reduced, setReduced] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const preference = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(preference.matches);
    preference.addEventListener('change', update);
    return () => preference.removeEventListener('change', update);
  }, []);
  return reduced;
}

function Message({ children, error = false, onRetry, action = '重新载入' }) {
  return <div className={`home-message${error ? ' is-error' : ''}`} role={error ? 'alert' : 'status'}>
    <p>{children}</p>{onRetry && <button className="btn quiet home-text-button" onClick={onRetry}>{action}<ArrowRight size={14}/></button>}
  </div>;
}

function TextButton({ children, onClick }) {
  return <button className="btn quiet home-text-button" onClick={onClick}>{children}<ArrowUpRight size={14}/></button>;
}

function ProjectLead({ project, dirty, busy, onContinue }) {
  const image = useMemo(() => writingProjectCardImage(project), [project]);
  const [failedImage, setFailedImage] = useState(null);
  const phase = writingProjectPhase(project);
  const summary = project.purpose || project.brief?.purpose || ({
    brief: '整理写作目标，让接下来的表达更有方向。',
    outline: '沿着已有思路，继续打磨文章的结构。',
    body: '回到正文，接着写下你的思考。',
    images: '回到文稿，继续核对文章的配图。',
    review: '继续检查全文的表达与引用。',
    ready: '回到这篇文稿，继续阅读、修改或导出。',
  })[phase.id];
  return <button className="home-lead-manuscript home-lead-project" onClick={() => onContinue(project)} disabled={busy} aria-label={`继续编辑：${projectTitle(project)}${dirty ? '，包含未保存修改' : ''}`}>
    <span className="home-manuscript-art" aria-hidden="true">
      {image && failedImage !== image.url ? <img src={image.url} alt="" loading="lazy" decoding="async" draggable={false} onError={() => setFailedImage(image.url)}/> : <><NotePencil size={34} weight="light"/><i/><i/><i/></>}
    </span>
    <span className="home-lead-copy">
      <h3 title={projectTitle(project)}>{projectTitle(project)}</h3>
      <span className="home-phase" data-phase={phase.id}>{dirty ? '有未保存修改' : writingProjectCardStatus(project)}</span>
      <p>{summary}</p>
      <span className="home-time"><Clock size={12}/>{dirty ? '继续当前编辑' : `${timestampLabel(project.updatedAt)} 更新`}</span>
      <HomeWritingProgress project={project}/>
    </span>
  </button>;
}

function SmallProject({ project, busy, onContinue }) {
  const phase = writingProjectPhase(project);
  return <button className="home-small-manuscript home-small-project" disabled={busy} onClick={() => onContinue(project)} aria-label={`打开文稿：${projectTitle(project)}`}>
    <FileText size={23} weight="light"/>
    <span><h3 title={projectTitle(project)}>{projectTitle(project)}</h3><span className="home-phase" data-phase={phase.id}>{writingProjectCardStatus(project)}</span><small>{timestampLabel(project.updatedAt)} 更新</small></span>
    <CaretRight size={14}/>
  </button>;
}

function DocumentFlow({ documents, onOpenDoc, onViewAll, modalOpen, reduced, searchControl }) {
  const viewport = useRef(null);
  const pause = useRef({ hover: false, focus: false, manualUntil: 0 });
  const drag = useRef(null);
  const suppressClickUntil = useRef(0);
  const loop = useRef({ enabled: false, period: 0 });
  const flowPosition = useRef(0);
  const manualMotion = useRef(null);
  const [looping, setLooping] = useState(false);
  const [preference, setPreference] = useState(null);
  const playing = looping && !reduced && (preference ?? true);
  const manualPause = () => { pause.current.manualUntil = performance.now() + 3500; };
  const writePosition = position => {
    const node = viewport.current;
    if (!node) return 0;
    const { enabled, period } = loop.current;
    // Every copy has the same geometry: changing copies leaves the picture still.
    const next = enabled && period > 0 ? period + ((position - period) % period + period) % period : position;
    flowPosition.current = next;
    node.scrollLeft = next;
    return next;
  };

  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const measure = () => {
      const originals = node.querySelectorAll('[data-flow-original]');
      if (!originals.length) return;
      const first = originals[0].getBoundingClientRect();
      const last = originals[originals.length - 1].getBoundingClientRect();
      const style = getComputedStyle(node);
      const available = node.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const needed = documents.length > 1 && last.right - first.left > available + 1;
      if (!needed) {
        loop.current = { enabled: false, period: 0 };
        manualMotion.current = null;
        writePosition(0);
      } else if (looping) {
        const after = node.querySelector('[data-flow-copy="after"]');
        const period = after.getBoundingClientRect().left - first.left;
        const previous = loop.current;
        const progress = previous.enabled && previous.period > 0
          ? ((node.scrollLeft - previous.period) % previous.period + previous.period) % previous.period / previous.period
          : node.scrollLeft / period;
        loop.current = { enabled: true, period };
        manualMotion.current = null;
        writePosition(period + progress * period);
      }
      setLooping(needed);
    };
    measure();
    const resize = new ResizeObserver(measure);
    resize.observe(node);
    return () => resize.disconnect();
  }, [documents.length, looping]);

  useEffect(() => {
    const node = viewport.current;
    if (!node || !looping) return;
    let frame, previous = 0;
    flowPosition.current = node.scrollLeft;
    let visible = !document.hidden;
    const visibility = () => { visible = !document.hidden; previous = 0; };
    document.addEventListener('visibilitychange', visibility);
    const tick = now => {
      const elapsed = previous ? Math.min(50, now - previous) : 0;
      previous = now;
      const paused = modalOpen || !visible;
      const motion = manualMotion.current;
      if (!paused && motion) {
        motion.elapsed = reduced ? 380 : Math.min(380, motion.elapsed + elapsed);
        const progress = 1 - Math.pow(1 - motion.elapsed / 380, 3);
        writePosition(motion.from + motion.distance * progress);
        if (motion.elapsed === 380) { manualMotion.current = null; manualPause(); }
      } else if (paused || !playing || pause.current.hover || pause.current.focus || drag.current || now < pause.current.manualUntil) flowPosition.current = node.scrollLeft;
      else if (loop.current.enabled) {
        // Keep the fraction in memory: scrollLeft may round subpixel increments.
        writePosition(flowPosition.current + 21 * elapsed / 1000);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(frame); document.removeEventListener('visibilitychange', visibility); };
  }, [playing, looping, modalOpen, reduced]);

  const move = direction => {
    manualPause();
    const node = viewport.current;
    if (!node || !loop.current.enabled) return;
    const distance = direction * Math.max(240, node.clientWidth * .75);
    if (reduced) writePosition(node.scrollLeft + distance);
    else manualMotion.current = { from: node.scrollLeft, distance, elapsed: 0 };
  };
  const releaseDrag = event => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    manualPause();
    if (drag.current.moved) suppressClickUntil.current = performance.now() + 250;
    if (viewport.current?.hasPointerCapture(event.pointerId)) viewport.current.releasePointerCapture(event.pointerId);
    drag.current = null;
    viewport.current?.classList.remove('is-dragging');
  };

  return <>
    <div className="home-section-heading home-flow-heading"><div className="home-flow-title"><h2>最近加入</h2><span className="home-subtle">沿着新线索，继续探索</span></div>
      {searchControl}
      <div className="home-flow-controls">
        <button className="icon-btn home-icon-button" onClick={() => move(-1)} aria-label="上一组资料" disabled={!looping}><CaretLeft size={16}/></button>
        <button className="btn quiet home-motion-toggle" aria-pressed={playing} onClick={() => setPreference(!playing)} disabled={reduced || !looping} aria-label={playing ? '暂停自动浏览资料' : '开始自动浏览资料'} title={reduced ? '系统已减少动态效果，可手动翻阅资料' : '悬停、拖动或阅读时暂停自动浏览'}>{playing ? <Pause size={13}/> : <Play size={13}/>}<span>{playing ? '暂停' : '播放'}</span></button>
        <button className="icon-btn home-icon-button" onClick={() => move(1)} aria-label="下一组资料" disabled={!looping}><CaretRight size={16}/></button>
        <TextButton onClick={onViewAll}>查看全部</TextButton>
      </div>
    </div>
    <div ref={viewport} className="home-card-flow home-flow" data-flow-loop={looping ? 'true' : undefined} tabIndex={0} aria-label="最近加入资料，可拖动或用左右方向键翻阅"
      onPointerEnter={() => { pause.current.hover = true; }} onPointerLeave={() => { pause.current.hover = false; }}
      onFocusCapture={() => { pause.current.focus = true; }} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) pause.current.focus = false; }}
      onScroll={event => { const node = event.currentTarget; if (loop.current.enabled && (node.scrollLeft < loop.current.period || node.scrollLeft >= loop.current.period * 2)) writePosition(node.scrollLeft); }}
      onWheel={() => { manualPause(); manualMotion.current = null; }} onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); move(event.key === 'ArrowLeft' ? -1 : 1); } }}
      onPointerDown={event => { if (event.button === 0 && event.isPrimary !== false) { manualPause(); manualMotion.current = null; drag.current = { pointerId: event.pointerId, x: event.clientX, scroll: event.currentTarget.scrollLeft, moved: false }; } }}
      onPointerMove={event => {
        const initial = drag.current;
        if (!initial || initial.pointerId !== event.pointerId) return;
        const distance = event.clientX - initial.x;
        if (Math.abs(distance) > 6) initial.moved = true;
        if (initial.moved) { manualPause(); event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); event.currentTarget.classList.add('is-dragging'); writePosition(initial.scroll - distance); }
      }}
      onPointerUp={releaseDrag} onPointerCancel={releaseDrag} onLostPointerCapture={() => { drag.current = null; viewport.current?.classList.remove('is-dragging'); }}
      onClickCapture={event => { if (performance.now() < suppressClickUntil.current) { event.preventDefault(); event.stopPropagation(); } }}>
      {(looping ? ['before', 'original', 'after'] : ['original']).flatMap(copy => documents.map(doc => <HomeRecentCard key={`${copy}:${doc.id}`}
        doc={doc} title={docTitle(doc)} date={timestampLabel(doc.createdAt).split(' ')[0]} copy={copy} onOpen={() => onOpenDoc(doc)}/>))}
    </div>
  </>;
}

function StageDistribution({ writing, loading, error, onNavigate }) {
  const counts = phases.map(phase => ({ ...phase, count: Number(writing?.phaseCounts?.[phase.id] || 0) }));
  const total = Number(writing?.total || 0);
  const valid = counts.every(phase => Number.isInteger(phase.count) && phase.count >= 0) && counts.reduce((sum, phase) => sum + phase.count, 0) === total;
  const circumference = 2 * Math.PI * 47;
  let offset = 0;
  return <section className="home-panel home-stages"><div className="home-chart-heading"><h2>写作阶段</h2><TextButton onClick={() => onNavigate('writing')}>全部</TextButton></div>
    {loading && !writing ? <Message>正在读取写作进展…</Message> : !writing || !valid ? <Message error={!!error}>暂时未能读取写作进展。</Message> : <div className="home-stage-layout">
      <div className="home-stage-ring" role="img" aria-label={`共 ${total} 篇文稿；${counts.map(phase => `${phase.label} ${phase.count} 篇`).join('，')}`}>
        <svg viewBox="0 0 124 124" aria-hidden="true"><circle className="home-ring-track" cx="62" cy="62" r="47" fill="none" strokeWidth="13"/>
          {counts.filter(phase => phase.count > 0).map(phase => {
            const length = phase.count / total * circumference;
            const start = offset; offset += length;
            return <circle className="home-ring-segment" key={phase.id} cx="62" cy="62" r="47" fill="none" stroke={phase.color} strokeWidth="13" strokeDasharray={`${Math.max(0, length - (length < circumference ? 2 : 0))} ${circumference}`} strokeDashoffset={-start} transform="rotate(-90 62 62)"/>;
          })}
        </svg><div><strong>{shortNumber(total)}</strong><span>篇文稿</span></div>
      </div>
      <div className="home-stage-legend">{counts.map(phase => <div key={phase.id} style={{ '--stage-color': phase.color }}><i/><span>{phase.label}</span><strong>{number(phase.count)}</strong></div>)}</div>
    </div>}
  </section>;
}

function GrowthChart({ series }) {
  const chart = useRef(null);
  const [height, setHeight] = useState(136);
  useEffect(() => {
    const node = chart.current;
    const resize = () => {
      const bounds = node.getBoundingClientRect();
      if (bounds.width > 0) setHeight(Math.max(64, Math.round(bounds.height * 320 / bounds.width)));
    };
    const observer = new ResizeObserver(resize);
    observer.observe(node);
    resize();
    return () => observer.disconnect();
  }, []);
  const width = 320, left = 24, top = 10, bottom = height - 26;
  const plotWidth = width - left - 5;
  const maximum = Math.max(1, ...series.map(day => day.count));
  const step = plotWidth / series.length;
  const labels = [...new Set([0, Math.floor((series.length - 1) / 2), series.length - 1])];
  return <svg ref={chart} className="home-growth-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={series.map(day => `${day.date} 加入 ${day.count} 份`).join('；')}>
    {[0, ...(maximum > 1 ? [Math.ceil(maximum / 2)] : []), maximum].filter((value, index, values) => values.indexOf(value) === index).map(value => {
      const y = bottom - value / maximum * (bottom - top);
      return <g key={value}><line className="home-chart-grid" x1={left} y1={y} x2={width - 3} y2={y}/><text className="home-chart-label" x={left - 6} y={y + 3} textAnchor="end">{value}</text></g>;
    })}
    {series.map((day, index) => <rect className="home-growth-bar" key={day.date} x={left + index * step + step * .2} y={bottom - day.count / maximum * (bottom - top)} width={Math.max(3, step * .6)} height={day.count / maximum * (bottom - top)} rx={Math.min(3, step * .2)}><title>{day.date} · 加入 {day.count} 份资料</title></rect>)}
    {labels.map(index => <text className="home-chart-label" key={series[index].date} x={left + index * step + step / 2} y={height - 7} textAnchor="middle">{series[index].label}</text>)}
  </svg>;
}

function StatCard({ icon: Icon, label, value, unit, caption, featured, tone, active = false, onClick, children }) {
  const displayValue = value === null ? '—' : number(value);
  return <button className={`home-stat${featured ? ' is-featured' : ''}`} data-stat={tone} data-count-size={displayValue.length > 9 ? 'long' : displayValue.length > 6 ? 'compact' : undefined} data-active={active ? 'true' : undefined} onClick={onClick}>
    <span className="home-stat-head"><Icon size={18} weight="duotone" aria-hidden="true"/><span>{label}</span><ArrowUpRight size={13}/></span>
    <span className="home-stat-value"><strong>{displayValue}</strong><small>{unit}</small></span>
    <span className="home-stat-caption">{caption}</span><span className="home-stat-spark" aria-hidden="true"><HomeMetricEmblem kind={tone}>{children}</HomeMetricEmblem></span>
  </button>;
}

export function HomeWorkspace({ documents = [], documentsLoading = false, documentsError = '', projects = [], currentProject, writingDirty = false, writingLoading = false, writingError = '', writingBusy = false, jobCounts = {}, writingJobCounts = {}, jobsLoading, jobsError = '', writingJobsError = '', overview, overviewLoading = false, overviewError = '', onOpenDoc, onContinueProject, onNewProject, onImport, onSearch, onNavigate, onOpenStatus, onOpenTopic, onRetryOverview, onRetryDocuments, onRetryWriting, modalOpen = false }) {
  const [search, setSearch] = useState('');
  const [days, setDays] = useState(14);
  const [today, setToday] = useState(() => Date.now());
  const reduced = useReducedMotion();
  useEffect(() => { const timer = setInterval(() => setToday(Date.now()), 60000); return () => clearInterval(timer); }, []);
  const projectCards = useMemo(() => continuingProjects(projects, currentProject, writingDirty), [projects, currentProject, writingDirty]);
  const added = useMemo(() => recentDocuments(documents), [documents]);
  const favorites = useMemo(() => favoriteDocuments(documents), [documents]);
  const topics = useMemo(() => topicDistribution(documents), [documents]);
  const series = useMemo(() => intakeSeries(documents, days, today), [documents, days, today]);
  const fortnight = useMemo(() => intakeSeries(documents, 14, today).reduce((sum, day) => sum + day.count, 0), [documents, today]);
  const writing = overview?.writing;
  const documentsKnown = !documentsLoading && (!documentsError || documents.length > 0);
  const writingKnown = Number.isInteger(writing?.total) && Number.isInteger(writing?.totalWords);
  const tasksKnown = !(jobsLoading ?? documentsLoading) && !jobsError;
  const taskCount = Number(jobCounts.queued || 0) + Number(jobCounts.running || 0);
  const documentCount = new Set(documents.filter(doc => !doc.deletedAt).map(doc => doc.id)).size;
  const favoriteCount = new Set(documents.filter(doc => doc.favorite && !doc.deletedAt).map(doc => doc.id)).size;
  const maximumTopic = Math.max(1, ...topics.topics.map(topic => topic.count));
  const seriesTotal = series.reduce((sum, day) => sum + day.count, 0);
  const spark = series.map((day, index) => `${index / Math.max(1, series.length - 1) * 100},${38 - day.count / Math.max(1, ...series.map(item => item.count)) * 31}`).join(' ');
  const searchControl = <form className="search-field home-search" role="search" onSubmit={event => { event.preventDefault(); onSearch(search.trim()); }}><MagnifyingGlass size={20}/><input aria-label="搜索资料、观点或概念" placeholder="搜索资料、观点或概念" value={search} onChange={event => setSearch(event.target.value)}/><button type="submit" aria-label="搜索资料"><kbd>Ctrl K</kbd><ArrowRight size={17}/></button></form>;

  return <div className="home-workspace" data-motion={reduced ? 'off' : 'on'}>
    <header className="home-hero"><div className="home-hero-copy"><h1>知识，逐渐成章<span>。</span></h1><p>从上次停下的地方，继续。</p></div><HomeOrnament/></header>
    <div className="home-dashboard">
      <div className="home-content home-feed">
        <section className="home-continue"><div className="home-section-heading"><h2>继续写作</h2><TextButton onClick={() => onNavigate('writing')}>查看全部</TextButton></div>
          {writingError && <Message error onRetry={onRetryWriting}>{writingError}</Message>}
          {projectCards.length ? <div className="home-manuscripts home-projects"><ProjectLead project={projectCards[0]} dirty={writingDirty && projectCards[0] === currentProject} busy={!!writingBusy} onContinue={onContinueProject}/><div className="home-other-manuscripts home-side-projects">{projectCards.slice(1).map(project => <SmallProject key={project.id} project={project} busy={!!writingBusy} onContinue={onContinueProject}/>)}{projectCards.length === 1 && <button className="home-small-manuscript home-new-manuscript" onClick={onNewProject} disabled={!!writingBusy}><Plus size={24} weight="light"/><span><h3>让新的想法，落在这里</h3><small>开始一篇新文稿</small></span><ArrowRight size={15}/></button>}</div></div>
            : writingLoading ? <Message>正在读取最近文稿…</Message> : !writingError && <div className="home-empty-writing"><NotePencil size={32} weight="light"/><div><h3>让第一篇文章，从这里开始</h3><p>把已有资料与新的想法，写成自己的表达。</p></div><button className="btn primary home-button" onClick={onNewProject} disabled={!!writingBusy}>新建文稿<ArrowRight size={15}/></button></div>}
        </section>
        <section className="home-quick-access" aria-label="常用工具"><div className="home-section-heading"><h2>常用工具</h2><span className="home-subtle">把下一步，放在手边</span></div><div className="home-shortcut-row">
          <button className="btn quiet home-shortcut" onClick={onNewProject} disabled={!!writingBusy}><Plus size={22} weight="light"/><span>新建文稿</span><ArrowUpRight size={14}/></button>
          <button className="btn quiet home-shortcut" onClick={onImport}><UploadSimple size={22} weight="light"/><span>导入资料</span><ArrowUpRight size={14}/></button>
          <button className="btn quiet home-shortcut" onClick={() => onNavigate('prompts')}><ChatsCircle size={22} weight="light"/><span>智能指令</span><ArrowUpRight size={14}/></button>
          <button className="btn quiet home-shortcut" onClick={() => onOpenStatus('selected')}><ListChecks size={22} weight="light"/><span>已选资料</span><ArrowUpRight size={14}/></button>
        </div></section>
        <section className="home-materials home-recent">
          {!added.length && searchControl}
          {documentsError && <Message error onRetry={onRetryDocuments}>{documentsError}</Message>}
          {documentsLoading && !documents.length ? <Message>正在读取资料…</Message> : added.length ? <DocumentFlow documents={added} onOpenDoc={onOpenDoc} onViewAll={() => onNavigate('library')} modalOpen={modalOpen} reduced={reduced} searchControl={searchControl}/> : !documentsError && <Message onRetry={documents.length ? () => onNavigate('library') : onImport} action={documents.length ? '打开文库' : '导入资料'}>{documents.length ? '这些资料未记录加入时间，可在文库继续浏览。' : '把第一份资料收进来，让积累慢慢开始。'}</Message>}
        </section>
        <section className="home-favorites"><div className="home-section-heading"><h2>最近收藏</h2><span className="home-subtle">你主动收藏的内容</span><TextButton onClick={() => onNavigate('favorites')}>全部收藏</TextButton></div>
          {favorites.length ? <div className="home-favorite-list">{favorites.map(doc => <button className="home-favorite-item" key={doc.id} onClick={() => onOpenDoc(doc)}><Star size={17} weight="fill"/><span><strong title={docTitle(doc)}>{docTitle(doc)}</strong><small>{doc.source || '本地导入'}</small></span><ArrowUpRight size={15}/></button>)}</div> : <p className="home-empty-reading">{documentsLoading ? '正在读取收藏…' : documentsError && !documents.length ? '暂时未能读取收藏。' : '把值得保留的资料收藏起来，方便再次翻阅。'}</p>}
        </section>
      </div>
      <aside className="home-insights" aria-label="数据概览"><div className="home-rail-heading"><h2>数据概览</h2><span className="home-subtle">积累与进展</span></div>
        {overviewError && <Message error onRetry={onRetryOverview}>{overviewError}</Message>}
        <section className="home-stats" aria-label="关键统计">
          <StatCard tone="writing" icon={NotePencil} label="文稿总量" value={writingKnown ? writing.total : null} unit="篇" featured onClick={() => onNavigate('writing')} caption={<span title="所有未删除文稿的当前已保存正文，不计历史版本及未采纳候选">总字数 <b>{writingKnown ? number(writing.totalWords) : '—'}</b> 字</span>}><NotePencil size={42} weight="light"/></StatCard>
          <StatCard tone="library" icon={Files} label="资料总量" value={documentsKnown ? documentCount : null} unit="份" onClick={() => onNavigate('library')} caption={documentsKnown ? <>近14天新增 <b>{number(fortnight)}</b> 份</> : documentsLoading ? '正在读取资料…' : '暂时无法读取'}><svg viewBox="0 0 100 42" fill="none"><polyline points={spark} stroke="currentColor" strokeWidth="2"/></svg></StatCard>
          <StatCard tone="saved" icon={Star} label="收藏资料" value={documentsKnown ? favoriteCount : null} unit="份" onClick={() => onNavigate('favorites')} caption="值得再次翻开的线索"><Star size={37} weight="light"/></StatCard>
          <StatCard tone="processing" active={tasksKnown && taskCount > 0} icon={Clock} label="资料处理中" value={tasksKnown ? taskCount : null} unit="项" onClick={() => onOpenStatus('ingest')} caption={jobsError ? '暂时无法读取进度' : '排队及运行中的任务'}><Stack size={39} weight="light"/></StatCard>
        </section>
        <StageDistribution writing={writing} loading={overviewLoading} error={overviewError} onNavigate={onNavigate}/>
        <section className="home-panel home-growth"><div className="home-chart-heading"><div><h2>资料加入</h2><span className="home-subtle">近{days}天 · {documentsKnown ? `共${number(seriesTotal)}份` : '正在读取'}</span></div><div className="home-period-switch" role="group" aria-label="资料加入时间范围">{[7, 14, 30].map(period => <button key={period} aria-pressed={days === period} className={days === period ? 'is-active' : ''} onClick={() => setDays(period)}>{period}天</button>)}</div></div>
          {documentsKnown ? <GrowthChart series={series}/> : <Message>{documentsLoading ? '正在读取入库趋势…' : '暂时未能读取入库趋势。'}</Message>}
        </section>
        <section className="home-panel home-topics"><div className="home-chart-heading"><h2>关注主题</h2><span className="home-subtle">点击探索</span></div>
          {topics.topics.length ? <><div className="home-topic-list">{topics.topics.slice(0, 5).map(topic => <button className="home-topic-row" key={topic.id} onClick={() => onOpenTopic ? onOpenTopic(topic) : onNavigate('library')} aria-label={`${topic.name}，${topic.count} 份资料`}><span className="home-topic-name" title={topic.name}><span>{topic.name}</span></span><span className="home-topic-track"><i style={{ width: `${topic.count / maximumTopic * 100}%` }}/></span><strong className="home-topic-count">{number(topic.count)}</strong></button>)}</div><p className="home-chart-note">同一份资料可以归入多个主题{topics.unclassified ? ` · ${number(topics.unclassified)}份未有完整主题分类` : ''}</p></> : <p className="home-empty-reading">{documentsLoading ? '正在读取资料主题…' : documentsError && !documents.length ? '暂时未能读取资料主题。' : '资料完成主题分类后，在这里查看分布。'}</p>}
        </section>
      </aside>
    </div>
    <footer className="home-footer">文枢 · 每一次积累，都在靠近下一篇好文章。</footer>
  </div>;
}
