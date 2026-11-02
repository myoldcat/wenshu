import React, { useEffect, useId, useRef, useState } from 'react';
import { CaretDown, Check, Graph } from '@phosphor-icons/react';

export function GraphTopicPicker({ topics = [], currentId, label = '知识图谱', loading = false, onSelect, idBase }) {
  const [open, setOpen] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const rootRef = useRef(null), triggerRef = useRef(null), menuRef = useRef(null), optionRefs = useRef([]);
  const generatedId = useId().replace(/:/g, '');
  const menuId = `${idBase || `gw-topic-picker-${generatedId}`}-menu`;
  const currentIndex = Math.max(0, topics.findIndex(topic => topic.id === currentId));
  const disabled = topics.length === 0;

  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  };
  const reveal = () => {
    if (disabled) return;
    setFocusedIndex(currentIndex);
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    optionRefs.current[focusedIndex]?.focus();
  }, [open, focusedIndex]);

  useEffect(() => {
    if (!open) return;
    if (!topics.length) { setOpen(false); return; }
    if (focusedIndex >= topics.length) setFocusedIndex(currentIndex);
  }, [open, topics.length, focusedIndex, currentIndex]);

  useEffect(() => {
    if (!open) return;
    const outside = event => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    const escape = event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  const moveFocus = event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    let next;
    if (event.key === 'ArrowDown') next = (focusedIndex + 1) % topics.length;
    if (event.key === 'ArrowUp') next = (focusedIndex - 1 + topics.length) % topics.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = topics.length - 1;
    if (next === undefined) return;
    event.preventDefault();
    event.stopPropagation();
    setFocusedIndex(next);
  };

  const availability = disabled ? loading ? '，正在加载' : '，暂无可切换图谱' : '';
  return <div className="gw-topic-picker" ref={rootRef}>
    <button ref={triggerRef} type="button" className="gw-topic-trigger" disabled={disabled}
      aria-label={`切换图谱：${label}${availability}`} aria-haspopup="menu" aria-expanded={open} aria-controls={menuId}
      title={label} onClick={() => open ? close() : reveal()}
      onKeyDown={event => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        event.preventDefault();
        if (open) optionRefs.current[focusedIndex]?.focus(); else reveal();
      }}>
      <Graph size={18} aria-hidden="true"/><span className="gw-topic-label">{label}</span><CaretDown size={16} className="gw-topic-caret" aria-hidden="true"/>
    </button>
    {open && <div ref={menuRef} id={menuId} className="gw-topic-menu" role="menu" aria-label="图谱列表"
      onKeyDown={moveFocus} onBlur={event => {
        if (!event.currentTarget.contains(event.relatedTarget)) close();
      }}>
      <div className="gw-topic-menu-heading" role="presentation"><span>图谱列表</span><span>{topics.length}</span></div>
      {topics.map((topic, index) => {
        const current = topic.id === currentId;
        return <button key={topic.id} ref={node => { optionRefs.current[index] = node; }} type="button"
          className={`gw-topic-option${current ? ' is-current' : ''}`} role="menuitemradio" aria-checked={current}
          tabIndex={focusedIndex === index ? 0 : -1} title={topic.pathLabel || topic.label}
          onFocus={() => setFocusedIndex(index)} onClick={() => { onSelect?.(topic.id); close(true); }}>
          <span className="gw-topic-mark" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
          <span className="gw-topic-option-label" style={{ paddingInlineStart: `${Math.min(5, Math.max(0, Number(topic.depth) || 0)) * 12}px` }}>{topic.label}{topic.pathLabel && topic.pathLabel !== topic.label && <small>{topic.pathLabel}</small>}</span><span className="gw-topic-count" title={`${topic.count ?? 0} 个词条${topic.sourceCount != null ? `，${topic.sourceCount} 篇资料` : ''}`}>{topic.count ?? 0}</span>
          {current && <Check size={16} aria-hidden="true"/>}
        </button>;
      })}
    </div>}
  </div>;
}
