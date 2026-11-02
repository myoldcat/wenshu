import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { BookOpen, ImageSquare, PencilLine, Graph, SquaresFour, GearSix, SidebarSimple, ChatsCircle, Stack, Palette, Checks, CaretUp, Question } from '@phosphor-icons/react';
import { BrandMark, BrandWordmark } from './Brand';
import { isDemoMode } from './demo-mode';

const destinations = [['library', BookOpen, '知源文库'], ['knowledge', Graph, '知识图谱'], ['writing', PencilLine, '写作空间'], ['images', ImageSquare, '图片素材'], ['templates', SquaresFour, '参考模板'], ['prompts', ChatsCircle, '智能指令']];

export function WorkspaceSidebar({ collapsed, onToggleCollapsed, nav, onNavigate, statusPanel, onToggleStatus, onOpenSettings, onOpenHelp, pendingCount = 0, writingPendingCount = 0, selectedCount = 0 }) {
  const demoMode = isDemoMode();
  const navigation = demoMode ? [['library', BookOpen, '文档阅读'], ['writing', PencilLine, '文稿写作']] : destinations;
  const availableRef = useRef(null);
  const moreTriggerRef = useRef(null);
  const moreMenuRef = useRef(null);
  const [compactActions, setCompactActions] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState(null);
  const moreMenuId = useId();

  useLayoutEffect(() => {
    const space = availableRef.current;
    if (!space) return;
    const fit = () => {
      const style = getComputedStyle(space);
      const row = parseFloat(style.getPropertyValue('--sidebar-status-row-height')) || 40;
      const gap = parseFloat(style.getPropertyValue('--sidebar-status-row-gap')) || 4;
      // Keep status, guide and settings actions available on short windows.
      const compact = space.clientHeight < row * 6 + gap * 5;
      setCompactActions(previous => previous === compact ? previous : compact);
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(space);
    window.addEventListener('resize', fit);
    return () => { observer.disconnect(); window.removeEventListener('resize', fit); };
  }, []);

  useLayoutEffect(() => {
    if (!compactActions) {
      if (moreOpen) availableRef.current?.querySelector('button')?.focus({ preventScroll: true });
      setMoreOpen(false);
      setMenuPosition(null);
    }
  }, [compactActions, moreOpen]);

  useLayoutEffect(() => {
    if (!moreOpen) return;
    const trigger = moreTriggerRef.current;
    const menu = moreMenuRef.current;
    if (!trigger || !menu) return;
    let animationFrame = 0;
    const position = () => {
      animationFrame = 0;
      const anchor = trigger.getBoundingClientRect();
      const width = menu.offsetWidth;
      const height = menu.offsetHeight;
      const left = Math.max(12, Math.min(window.innerWidth - width - 12, anchor.right + 10));
      const top = Math.max(12, Math.min(window.innerHeight - height - 12, anchor.bottom - height));
      setMenuPosition(previous => previous?.left === left && previous?.top === top ? previous : { left, top });
    };
    const schedulePosition = () => {
      if (!animationFrame) animationFrame = requestAnimationFrame(position);
    };
    position();
    const observer = new ResizeObserver(schedulePosition);
    observer.observe(trigger);
    observer.observe(menu);
    window.addEventListener('resize', schedulePosition);
    window.addEventListener('scroll', schedulePosition, true);
    return () => {
      cancelAnimationFrame(animationFrame);
      observer.disconnect();
      window.removeEventListener('resize', schedulePosition);
      window.removeEventListener('scroll', schedulePosition, true);
    };
  }, [moreOpen, collapsed]);

  useEffect(() => {
    if (!moreOpen) return;
    moreMenuRef.current?.querySelector('[role="menuitem"]')?.focus({ preventScroll: true });
    const outside = event => {
      if (moreMenuRef.current?.contains(event.target) || moreTriggerRef.current?.contains(event.target)) return;
      setMoreOpen(false);
      if (!event.target.closest?.('button, input, select, textarea, a, [tabindex]')) {
        moreTriggerRef.current?.focus({ preventScroll: true });
      }
    };
    const escape = event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      setMoreOpen(false);
      moreTriggerRef.current?.focus({ preventScroll: true });
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape, true);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape, true);
    };
  }, [moreOpen]);

  const actions = [['ingest', Stack, '入库进度', pendingCount], ['writing', PencilLine, '写作进度', writingPendingCount], ['selection', Checks, '已选资料', selectedCount], ...(!demoMode ? [['appearance', Palette, '外观主题', 0]] : [])];

  const toggleMore = () => {
    if (!moreOpen && statusPanel) onToggleStatus(statusPanel);
    setMenuPosition(null);
    setMoreOpen(previous => !previous);
  };
  const chooseAction = callback => {
    setMoreOpen(false);
    // Status windows capture their return target after the menu is removed.
    moreTriggerRef.current?.focus({ preventScroll: true });
    callback();
  };
  const menuKeyboard = event => {
    const items = Array.from(moreMenuRef.current?.querySelectorAll('[role="menuitem"]') || []);
    const index = items.indexOf(document.activeElement);
    let next;
    if (event.key === 'ArrowDown') next = (index + 1) % items.length;
    if (event.key === 'ArrowUp') next = (index - 1 + items.length) % items.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = items.length - 1;
    if (next !== undefined) {
      event.preventDefault();
      items[next]?.focus({ preventScroll: true });
    }
  };

  return <><aside className="app-sidebar" aria-label="工作台导航">
    {!demoMode && <div className="sidebar-brand-row"><button className="brand" onClick={() => onNavigate('home')} aria-label="文枢，返回主页" title="文枢 · 返回主页" aria-current={nav === 'home' ? 'page' : undefined}><BrandMark/><BrandWordmark/></button></div>}
    <nav aria-label="主导航">{navigation.map(([key, Icon, label]) => <button key={key} className={`nav-item ${nav === key ? 'active' : ''}`} onClick={() => onNavigate(key)} aria-label={label} title={collapsed ? label : undefined} aria-current={nav === key ? 'page' : undefined}><Icon size={23} weight={nav === key ? 'fill' : 'regular'}/><span>{label}</span>{nav === key && <i className="nav-active-dot"/>}</button>)}</nav>
    {!demoMode && <div className="sidebar-bottom">
      <div className={`sidebar-status-space ${compactActions ? 'is-compact' : ''}`} ref={availableRef}><div className="sidebar-status-actions">
        {compactActions ? <button ref={moreTriggerRef} data-status-trigger className={`nav-item status-trigger sidebar-more-trigger ${moreOpen ? 'status-active' : ''}`} aria-label="展开更多功能" title="展开更多功能" aria-haspopup="menu" aria-expanded={moreOpen} aria-controls={moreMenuId} onClick={toggleMore}><CaretUp size={22}/></button> : <>
          {actions.map(([key, Icon, label, count]) => <button key={key} data-status-trigger className={`nav-item status-trigger ${statusPanel === key ? 'status-active' : ''}`} aria-label={label} title={collapsed ? label : undefined} aria-expanded={statusPanel === key} aria-controls="workspace-status-popover" onClick={() => onToggleStatus(key)}><Icon size={21}/><span>{label}</span>{count > 0 && <b className="sidebar-badge">{count}</b>}</button>)}
          {!demoMode && <button className="nav-item status-trigger" aria-label="使用指南" title={collapsed ? '使用指南' : undefined} aria-haspopup="dialog" onClick={onOpenHelp}><Question size={21}/><span>使用指南</span></button>}
          <button data-status-trigger className={`nav-item status-trigger ${statusPanel === 'settings' ? 'status-active' : ''}`} aria-label="系统设置" title={collapsed ? '系统设置' : undefined} aria-haspopup="dialog" aria-expanded={statusPanel === 'settings'} aria-controls="workspace-status-popover" onClick={onOpenSettings}><GearSix size={21}/><span>系统设置</span></button>
        </>}
      </div></div>
      <button className="sidebar-collapse nav-item status-trigger" aria-label={collapsed ? '展开左边栏' : '收起左边栏'} aria-expanded={!collapsed} title={collapsed ? '展开左边栏' : '收起左边栏'} onClick={onToggleCollapsed}><SidebarSimple size={21} weight={collapsed ? 'fill' : 'regular'}/><span>收起边栏</span></button>
    </div>}
  </aside>{!demoMode && moreOpen && createPortal(<div ref={moreMenuRef} id={moreMenuId} role="menu" aria-label="更多功能" className="sidebar-more-menu" style={{ left: menuPosition?.left || 0, top: menuPosition?.top || 0, visibility: menuPosition ? 'visible' : 'hidden' }} onKeyDown={menuKeyboard} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget) && event.relatedTarget !== moreTriggerRef.current) setMoreOpen(false);
  }}>
    {actions.map(([key, Icon, label, count]) => <button key={key} type="button" role="menuitem" data-status-trigger className={`sidebar-more-item ${statusPanel === key ? 'is-active' : ''}`} aria-controls="workspace-status-popover" onClick={() => chooseAction(() => onToggleStatus(key))}><Icon size={20}/><span>{label}</span>{count > 0 && <b>{count}</b>}</button>)}
    {!demoMode && <button type="button" role="menuitem" className="sidebar-more-item" aria-haspopup="dialog" onClick={() => chooseAction(onOpenHelp)}><Question size={20}/><span>使用指南</span></button>}
    <button type="button" role="menuitem" data-status-trigger className={`sidebar-more-item ${statusPanel === 'settings' ? 'is-active' : ''}`} aria-haspopup="dialog" aria-controls="workspace-status-popover" onClick={() => chooseAction(onOpenSettings)}><GearSix size={20}/><span>系统设置</span></button>
  </div>, document.body)}</>;
}
