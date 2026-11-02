import React, { useEffect, useMemo, useRef, useState } from 'react';
import { CaretRight, Check, FileText, PencilSimple, Quotes, WarningCircle } from '@phosphor-icons/react';
import { usePromptLibrary } from './usePromptLibrary';
import { currentPromptRules, displayPromptContent, editablePromptContent, isStructurePrompt, promptDraftChanged } from './prompt-library-state';
import { formatPromptText } from './prompt-format';
import { publishPromptDraftTransfer } from './prompt-draft-transfer';
import './knowledge.css';
import './template-library.css';
import './prompt-library.css';

function modifiedLabel(value) {
  if (!value) return '系统内置';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '已保存' : `更新于 ${date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}`;
}

export function PromptWorkspace({ onToast, active: visible = true }) {
  const library = usePromptLibrary(visible);
  const [category, setCategory] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [discardId, setDiscardId] = useState(null);
  const [feedback, setFeedback] = useState('');
  const editorRef = useRef(null);
  const editButtonRef = useRef(null);
  const detailRef = useRef(null);
  const effectiveRef = useRef(null);
  const listRef = useRef(null);
  const visibleItems = library.items.filter(entry => !isStructurePrompt(entry));
  const categories = library.categories.filter(entry => entry.id !== 'all' && entry.id !== 'structures');
  const activeCategory = categories.some(entry => entry.id === category) ? category : categories[0]?.id;
  const filtered = visibleItems.filter(item => item.category === activeCategory);
  const item = filtered.find(entry => entry.id === selectedId) || filtered[0];
  const draft = library.drafts[item?.id];
  const conflict = library.conflicts[item?.id];
  const dirty = promptDraftChanged(draft);
  const saving = library.savingId === item?.id;
  const reloading = library.reloadingId === item?.id;
  const contentLimit = Number.isInteger(item?.maxContentLength) && item.maxContentLength > 0 ? item.maxContentLength : 24000;
  const categoryName = id => categories.find(entry => entry.id === id)?.name || id;
  const readableContent = useMemo(() => formatPromptText(displayPromptContent(item), item?.id), [item]);
  // Keep the pre-move hook slots stable during a hot update so existing
  // structure drafts can reach the handoff effect before leaving this view.
  const readableContract = useMemo(() => item?.supplementalRules || [], [item?.supplementalRules]);
  const readableLatest = useMemo(() => formatPromptText(editablePromptContent(conflict?.latest), conflict?.latest?.id), [conflict?.latest]);
  const readableEffective = useMemo(() => formatPromptText(item?.fixedContent || '', `${item?.id}:contract`), [item?.fixedContent, item?.id]);
  const readableRules = useMemo(() => currentPromptRules({ ...item, supplementalRules: readableContract }), [item, readableContract]);

  useEffect(() => { if (visible && draft) editorRef.current?.focus({ preventScroll: true }); }, [item?.id, Boolean(draft), visible]);
  useEffect(() => {
    setDiscardId(current => current && current !== item?.id ? null : current);
    for (const [id, draft] of Object.entries(library.drafts)) {
      if (!isStructurePrompt({ id }) || library.savingId === id) continue;
      const payload = { id, draft, conflict: library.conflicts[id] || null, error: library.errors[id] || '' };
      publishPromptDraftTransfer(payload, () => library.releaseDraftState(payload));
    }
  }, [item?.id, library.drafts, library.conflicts, library.errors, library.savingId]);
  useEffect(() => { if (detailRef.current) detailRef.current.scrollTop = 0; }, [item?.id]);
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = 0; }, [activeCategory]);
  const notify = text => { setFeedback(text); onToast?.(text); };
  const chooseCategory = id => { setCategory(id); setSelectedId(null); setFeedback(''); };
  const chooseItem = id => { setSelectedId(id); setFeedback(''); };
  const cancelEdit = () => {
    if (dirty) setDiscardId(item.id);
    else { library.discard(item.id); requestAnimationFrame(() => editButtonRef.current?.focus()); }
  };
  const discardEdit = () => { library.discard(item.id); setDiscardId(null); requestAnimationFrame(() => editButtonRef.current?.focus()); };
  const save = async event => {
    event.preventDefault();
    const saved = await library.save(item.id);
    if (saved) { notify(`「${saved.title}」已保存为 v${saved.version}，后续新建任务将使用此版本。`); requestAnimationFrame(() => editButtonRef.current?.focus()); }
  };

  return <section className="prompt-workspace runtime-prompt-library" aria-busy={library.loading}>
    <div className="prompt-layout">
    <div className="prompt-main">
    <header className="prompt-heading"><div><h1>让模型，善解人意</h1><p>让知识整理更有章法，让文稿写作更合心意。</p></div></header>
    {library.dirtyCount > 0 && <div className="prompt-draft-notice" role="status"><PencilSimple size={16}/><p>有 {library.dirtyCount} 条未保存修改。切换分类或页面会保留，关闭或刷新前请保存。</p></div>}
    {library.loadError && <div className="prompt-api-message is-error" role="alert"><WarningCircle size={18}/><p>{library.loadError}</p><button type="button" className="btn quiet" disabled={library.loading} onClick={library.refresh}>重新加载</button></div>}
    {library.loading && !library.items.length ? <div className="prompt-api-message" role="status">正在加载智能指令…</div> : <section className="prompt-detail" aria-label="智能指令详情" ref={detailRef} tabIndex={0}>
        {item ? <>
          <div className="prompt-detail-top"><span className="workspace-kicker">{categoryName(item.category)}</span><span className="prompt-detail-badge">智能指令 · v{item.version}</span></div>
          <div className="prompt-detail-title"><span><Quotes size={25}/></span><h2>{item.title}</h2></div>
          <p className="prompt-detail-description">{item.description}</p>
          <dl className="prompt-flow-context"><div><dt>流程步骤</dt><dd>{item.stage}</dd></div><div><dt>作用</dt><dd>{item.usage}</dd></div><div><dt>输入</dt><dd>{item.inputDescription}</dd></div><div><dt>输出</dt><dd>{item.outputDescription}</dd></div></dl>
          <div className="prompt-document-toolbar"><span><FileText size={15}/>{draft ? `编辑指令 · 基于 v${draft.version}` : '指令正文'}</span>{!draft && <button type="button" ref={editButtonRef} onClick={() => { library.startEdit(item); setFeedback(''); }}><PencilSimple size={15}/>编辑正文</button>}</div>
          {draft ? <form className="prompt-edit-form" onSubmit={save}>
            {/\{\{[^}]+\}\}/.test(editablePromptContent(item)) && <p className="prompt-placeholder-note">保留正文中的 {'{{变量}}'} 标记，运行时由程序填入；删除必需变量将无法保存。</p>}
            <label className="prompt-content-field"><span className="sr-only">{item.title}指令正文</span><textarea ref={editorRef} aria-label="指令正文" maxLength={contentLimit} value={draft.content} disabled={saving || reloading} onChange={event => library.edit(item.id, event.target.value)} spellCheck={false}/></label>
            <div className="prompt-editor-meta"><span>{dirty ? '有未保存修改' : '尚未修改'}</span><span>{draft.content.length.toLocaleString()} / {contentLimit.toLocaleString()} 字符</span></div>
            {library.errors[item.id] && <p className="prompt-save-error" role="alert">{library.errors[item.id]}</p>}
            {conflict && <section className="prompt-version-conflict" aria-label="指令版本冲突">
              <h3><WarningCircle size={17}/>请对照最新版本</h3>
              {conflict.latest ? <><p>服务器已更新到 v{conflict.latest.version}。上方保留你的修改，下方为当前已保存正文。</p><pre>{readableLatest}</pre><button type="button" className="btn" disabled={reloading} onClick={() => library.continueFromLatest(item.id)}>保留我的内容，基于 v{conflict.latest.version} 继续编辑</button><p>继续编辑后仍需点击“保存修改”才会保存。</p></> : <p>加载最新版本不会覆盖上方的修改。</p>}
              <button type="button" className="btn quiet" disabled={reloading} onClick={() => library.reloadConflict(item.id)}>{reloading ? '正在加载…' : conflict.latest ? '再次加载最新版本' : '加载最新版本'}</button>
            </section>}
            {discardId === item.id ? <div className="prompt-discard-confirm" role="group" aria-label="放弃未保存修改"><p>放弃此条指令的未保存修改？</p><div><button type="button" className="btn quiet" onClick={() => { setDiscardId(null); editorRef.current?.focus(); }}>继续编辑</button><button type="button" className="btn" onClick={discardEdit}>放弃修改</button></div></div> : <footer className="prompt-edit-footer"><span>修改后请保存</span><button type="button" className="btn quiet" disabled={saving || reloading} onClick={cancelEdit}>取消编辑</button><button type="submit" className="btn primary" disabled={!dirty || !draft.content.trim() || Boolean(library.savingId) || reloading || Boolean(conflict)}><Check size={17}/>{saving ? '正在保存…' : '保存修改'}</button></footer>}
          </form> : <pre className="prompt-content">{readableContent}</pre>}
          {draft && item.fixedContent && <details ref={effectiveRef} className="prompt-runtime-contract prompt-edit-fixed-rules" key={`${item.id}-edit-fixed`}><summary>当前固定规则（只读）</summary><pre>{readableEffective}</pre></details>}
          {readableRules.length > 0 && <details className="prompt-runtime-contract prompt-supplemental-rules" key={`${item.id}-rules`}><summary>当前固定规则</summary>{readableRules.map((rule, index) => <section className="prompt-supplemental-rule" key={rule.id || index} aria-label={rule.title || '固定规则'}><h3>{rule.title || '固定规则'}<span>只读</span></h3>{rule.description && <p>{rule.description}</p>}<pre>{rule.formatted}</pre></section>)}</details>}
          <p className="prompt-updated-at">{modifiedLabel(item.updatedAt)}</p>
        </> : <div className="prompt-detail-empty"><Quotes size={40}/><h2>{library.loadError ? '暂时无法读取智能指令' : '选择一个流程步骤'}</h2><p>{library.loadError ? '服务恢复后重新加载，即可继续查看和修改。' : '从右侧选择指令，查看其作用、输入、输出和正文。'}</p></div>}
      </section>}
    <p className="prompt-feedback" role="status" aria-live="polite">{feedback}</p>
    </div>
      <aside className="prompt-list template-library" aria-label="智能指令列表">
        <div className="prompt-navigation-heading"><h2>指令选择</h2><span>{filtered.length} 条指令</span></div>
        <nav className="prompt-category-tabs" aria-label="智能指令分类">{categories.map(entry => <button type="button" aria-pressed={activeCategory === entry.id} className={activeCategory === entry.id ? 'active' : ''} key={entry.id} onClick={() => chooseCategory(entry.id)}>{entry.name}<span>{visibleItems.filter(value => value.category === entry.id).length}</span></button>)}</nav>
        <div className="prompt-list-scroll" ref={listRef} role="region" aria-label="当前分组指令" tabIndex={0}>
        {filtered.map(entry => <button type="button" key={entry.id} className={`prompt-card template-item ${item?.id === entry.id ? 'active selected' : ''}`} aria-pressed={item?.id === entry.id} onClick={() => chooseItem(entry.id)}>
          <span className="template-item-icon"><FileText size={23}/></span><span className="prompt-card-copy template-item-content"><strong>{entry.title}</strong><span>{entry.description}</span>{promptDraftChanged(library.drafts[entry.id]) && <small><em>未保存</em></small>}</span><CaretRight size={17}/>
        </button>)}
        {!filtered.length && !library.loading && !library.loadError && <div className="prompt-empty"><Quotes size={30}/><h3>此分类暂无指令</h3></div>}
        </div>
        <div className="prompt-library-note"><FileText size={16}/><p>保存后用于后续新建任务。</p></div>
      </aside>
    </div>
  </section>;
}
