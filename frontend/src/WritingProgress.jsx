import React, { useEffect, useId, useRef } from 'react';
import { Check, Clock, Minus, SpinnerGap, WarningCircle } from '@phosphor-icons/react';
import './writing-progress.css';

export default function WritingProgress({ progress, viewedStep, busy, onStep, onNotice }) {
  const automatic = progress.automatic === true;
  const currentView = viewedStep || progress.currentId;
  const list = useRef(null), band = useRef(null), haloGrid = useRef(null);
  const haloId = `writing-ribbon-halo-${useId().replaceAll(':', '')}`;
  const notices = progress.notices || [];
  const stepNotices = new Map();
  for (const notice of notices) if (!stepNotices.has(notice.stepId)) stepNotices.set(notice.stepId, notice);
  function syncHalo() {
    if (haloGrid.current && list.current) haloGrid.current.style.transform = `translateX(${-list.current.scrollLeft}px)`;
  }
  useEffect(() => {
    const container = list.current, button = container?.querySelector('[aria-current="step"]');
    if (button && container.scrollWidth > container.clientWidth) {
      const frame = container.getBoundingClientRect(), target = button.getBoundingClientRect();
      if (target.left < frame.left) container.scrollLeft += target.left - frame.left;
      else if (target.right > frame.right) container.scrollLeft += target.right - frame.right;
    }
    syncHalo();
  }, [currentView]);
  useEffect(() => {
    const rootStyle = document.documentElement.style, property = '--writing-toast-clearance';
    const previous = rootStyle.getPropertyValue(property), priority = rootStyle.getPropertyPriority(property);
    let frame = 0, written = '';
    const measure = () => {
      frame = 0;
      syncHalo();
      if (!band.current) return;
      const value = `${Math.max(0, Math.round((window.innerHeight - band.current.getBoundingClientRect().top) * 100) / 100)}px`;
      if (value !== written) { rootStyle.setProperty(property, value); written = value; }
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(schedule) : null;
    for (const element of new Set([band.current, list.current, band.current?.closest('.app-main')])) if (element) observer?.observe(element);
    const themeObserver = new MutationObserver(schedule);
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    window.addEventListener('resize', schedule);
    measure();
    return () => {
      if (frame) cancelAnimationFrame(frame);
      observer?.disconnect(); themeObserver.disconnect(); window.removeEventListener('resize', schedule);
      if (previous) rootStyle.setProperty(property, previous, priority); else rootStyle.removeProperty(property);
    };
  }, []);
  function moveFocus(event) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const buttons = [...list.current.querySelectorAll('button:not(:disabled)')], index = buttons.indexOf(event.target);
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : Math.max(0, Math.min(buttons.length - 1, index + (event.key === 'ArrowRight' ? 1 : -1)));
    buttons[next]?.focus();
  }
  const noticeBadge = notice => notice.count ? `${notice.count}${notice.stepId === 'body' ? '章' : '项'}待确认`
    : notice.id === 'review-stale' ? '已过期' : notice.id === 'review-skipped' ? '已跳过'
      : ['review-start', 'review-incomplete'].includes(notice.id) ? '待校验' : '待确认';
  return <section ref={band} className={automatic ? 'writing-progress-band is-automatic' : 'writing-progress-band'} aria-label="写作流程进度" data-writing-progress data-completed={progress.completed} data-total={progress.total}>
    {/* Only the separate color layer is blurred; real text and controls never enter the filter. */}
    <svg className="writing-progress-filter" width="0" height="0" aria-hidden="true" focusable="false"><defs>
      <filter id={haloId} x="-5%" y="-120%" width="110%" height="280%" colorInterpolationFilters="sRGB">
        <feGaussianBlur in="SourceAlpha" stdDeviation="8 20" result="ribbon-white-blur"/>
        <feOffset in="ribbon-white-blur" dy="-10" result="ribbon-white-lift"/>
        <feComponentTransfer in="ribbon-white-lift" result="ribbon-white-mask"><feFuncA type="linear" slope="4" intercept="0"/></feComponentTransfer>
        <feFlood floodColor="var(--bg)" floodOpacity="1" result="ribbon-white-tint"/>
        <feComposite in="ribbon-white-tint" in2="ribbon-white-mask" operator="in" result="ribbon-white-halo"/>
        <feGaussianBlur in="SourceGraphic" stdDeviation="12 10" result="ribbon-halo-blur"/>
        <feOffset in="ribbon-halo-blur" dy="-2" result="ribbon-halo-lift"/>
        <feComponentTransfer in="ribbon-halo-lift" result="ribbon-color-shape"><feFuncA type="linear" slope="2" intercept="-.2"/></feComponentTransfer>
        <feComponentTransfer in="ribbon-color-shape" result="ribbon-halo"><feFuncA type="linear" slope=".5" intercept="0"/></feComponentTransfer>
        <feMerge><feMergeNode in="ribbon-white-halo"/><feMergeNode in="ribbon-halo"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
    </defs></svg>
    <div className="writing-progress-halo" data-writing-halo aria-hidden="true" style={{ filter: `url("#${haloId}")` }}><div className="writing-progress-halo-viewport"><div ref={haloGrid} className="writing-progress-halo-grid" data-writing-halo-grid>{progress.steps.map(step => <span key={step.id} className="writing-progress-halo-color" data-stage={step.id} data-state={step.visualState || (step.complete ? 'complete' : 'waiting')} style={{ '--writing-halo-color': `var(--writing-spectrum-${step.id})`, '--writing-halo-deep': `var(--writing-spectrum-${step.id}-deep)` }}/>)}</div></div></div>
    <div className="writing-progress-track">
      <ol ref={list} onKeyDown={moveFocus} onScroll={syncHalo}>{progress.steps.map((step, index) => {
        const notice = stepNotices.get(step.id);
        const state = step.visualState || (step.complete ? 'complete' : 'waiting');
        const action = notice?.actionLabel || (automatic || step.complete ? '查看' : state === 'running' ? '继续' : '前往');
        const durationText = step.durationLabel?.replace(/^(?:用时|耗时)\s*/, '');
        return <li key={step.id} data-stage={step.id} data-state={state} data-writing-notice={notice?.id} data-notice-tone={notice?.tone} className={`${step.complete ? 'is-complete' : ''} ${step.id === currentView ? 'is-viewed' : ''} ${step.id === progress.currentId ? 'is-next' : ''} ${notice ? 'has-notice' : ''}`}>
        <button type="button" data-writing-step={step.id} aria-current={step.id === currentView ? 'step' : undefined} aria-label={`${index + 1}. ${step.label}，${step.stateLabel}${step.metricLabel ? `，${step.metricLabel}` : ''}，${step.detail}${step.durationLabel ? `，${step.durationLabel}` : ''}${notice ? `，${noticeBadge(notice)}，${notice.label}，${notice.description}` : ''}，${action}`} title={[step.label, step.stateLabel, step.metricLabel, step.metricTitle, step.detail, step.durationTitle || step.durationLabel, notice?.label, notice?.description, action].filter(Boolean).join(' · ')} disabled={busy || !step.available} onClick={() => notice && onNotice ? onNotice(notice) : onStep(step.id)}>
          <span className="writing-progress-number" title={step.stateLabel} aria-hidden="true">{state === 'skipped' ? <Minus size={13} weight="bold"/> : state === 'complete' ? <Check size={13} weight="bold"/> : state === 'running' ? <SpinnerGap size={15} weight="bold"/> : <Clock size={14} weight="bold"/>}</span>
          <span className="writing-progress-step-text"><span className="writing-progress-step-heading"><strong title={step.label}>{step.label}</strong>{step.durationLabel && <small className="writing-progress-duration" title={[step.durationLabel, step.durationTitle].filter(Boolean).join(' · ')}>{state !== 'skipped' && <Clock size={12} aria-hidden="true"/>}<span>{durationText}</span></small>}</span><small className="writing-progress-state-line">{step.metricLabel && <span className="writing-progress-metric" title={step.metricTitle || step.metricLabel} data-writing-metric={step.id} data-metric-value={step.metricValue}>{step.metricLabel}</span>}</small>{notice?.count > 0 && <span className="writing-progress-notice-badge" title={[noticeBadge(notice), notice.description].filter(Boolean).join(' · ')}><WarningCircle size={11} weight="fill" aria-hidden="true"/><span className="writing-progress-notice-text">{noticeBadge(notice)}</span></span>}{notice && !notice.count && notice.id !== 'review-skipped' && <span className="writing-progress-inline-notice" title={notice.label} role="status" aria-live="polite">{notice.label}</span>}</span>
        </button>
      </li>; })}</ol>
    </div>
  </section>;
}
