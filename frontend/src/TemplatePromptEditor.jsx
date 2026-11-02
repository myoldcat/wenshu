import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, PencilSimple, WarningCircle } from '@phosphor-icons/react';
import { displayPromptContent, editablePromptContent, promptDraftChanged } from './prompt-library-state';
import { formatPromptText } from './prompt-format';
import './template-prompt-editor.css';

export function TemplatePromptEditor({ template, library, active = true, onToast = () => {} }) {
  const id = `writing_structure_${template.id}`;
  const item = library.items.find(entry => entry.id === id);
  const draft = library.drafts[id];
  const conflict = library.conflicts[id];
  const dirty = promptDraftChanged(draft);
  const saving = library.savingId === id;
  const reloading = library.reloadingId === id;
  const maximum = item?.maxContentLength || 24000;
  const [discarding, setDiscarding] = useState(false);
  const [feedback, setFeedback] = useState('');
  const editorRef = useRef(null);
  const editButtonRef = useRef(null);
  const contextRef = useRef({ id, active, mounted: true });
  contextRef.current = { ...contextRef.current, id, active };
  const readable = useMemo(() => formatPromptText(displayPromptContent(item), id), [item, id]);
  const latest = useMemo(() => formatPromptText(editablePromptContent(conflict?.latest), id), [conflict?.latest, id]);

  useEffect(() => { contextRef.current.mounted = true; return () => { contextRef.current.mounted = false; }; }, []);
  useEffect(() => { setDiscarding(false); setFeedback(''); }, [id]);
  useEffect(() => { if (active && draft) editorRef.current?.focus({ preventScroll: true }); }, [id, Boolean(draft), active]);
  const focusEdit = () => requestAnimationFrame(() => {
    if (contextRef.current.mounted && contextRef.current.id === id && contextRef.current.active) editButtonRef.current?.focus({ preventScroll: true });
  });
  const discard = () => { library.discard(id); setDiscarding(false); focusEdit(); };
  const cancel = () => { if (dirty) setDiscarding(true); else discard(); };
  const save = async event => {
    event.preventDefault();
    const saved = await library.save(id);
    if (!saved) return;
    const message = `「${template.name}」提示词已保存，后续新任务将使用此版本。`;
    onToast(message);
    if (contextRef.current.mounted && contextRef.current.id === id) { setFeedback(message); focusEdit(); }
  };

  return <section className="template-prompt-editor" aria-label="结构模板提示词" aria-busy={library.loading || saving}>
    <div className="template-prompt-heading"><h3>模板提示词</h3>{item && <span>v{item.version}</span>}{item && !draft && <button type="button" className="btn quiet" ref={editButtonRef} onClick={() => { library.startEdit(item); setFeedback(''); }}><PencilSimple size={15}/>编辑提示词</button>}</div>
    {library.loadError && <div className="template-prompt-message is-error" role="alert"><WarningCircle size={17}/><p>{library.loadError}</p><button type="button" className="btn quiet" disabled={library.loading} onClick={library.refresh}>重新加载</button></div>}
    {!item ? <p className="template-prompt-message" role="status">{library.loading ? '正在加载模板提示词…' : library.loadError ? '服务恢复后可重新加载模板提示词。' : '暂未找到此模板的提示词。'}{!library.loading && !library.loadError && <button type="button" className="btn quiet" onClick={library.refresh}>重新加载</button>}</p> : <>
      {draft ? <form onSubmit={save} className="template-prompt-form">
        <textarea ref={editorRef} className="template-paper template-prompt-content" aria-label="模板提示词正文" maxLength={maximum} value={draft.content} disabled={saving || reloading} onChange={event => library.edit(id, event.target.value)} spellCheck={false}/>
        <div className="template-prompt-meta"><span>{dirty ? '有未保存修改' : '尚未修改'}</span><span>{draft.content.length.toLocaleString()} / {maximum.toLocaleString()} 字符</span></div>
        {library.errors[id] && <p className="template-prompt-error" role="alert">{library.errors[id]}</p>}
        {conflict && <section className="template-prompt-conflict" aria-label="模板提示词版本冲突"><h4><WarningCircle size={17}/>请对照最新版本</h4>{conflict.latest ? <><p>服务器已更新到 v{conflict.latest.version}。上方保留你的修改，下方为当前已保存提示词。</p><pre className="template-paper template-prompt-content">{latest}</pre><button type="button" className="btn" disabled={reloading} onClick={() => library.continueFromLatest(id)}>保留我的内容，基于 v{conflict.latest.version} 继续编辑</button><p>继续编辑后仍需点击“保存提示词”才会保存。</p></> : <p>加载最新版本会保留上方的修改，便于对照。</p>}<button type="button" className="btn quiet" disabled={reloading} onClick={() => library.reloadConflict(id)}>{reloading ? '正在加载…' : conflict.latest ? '再次加载最新版本' : '加载最新版本'}</button></section>}
        {discarding ? <div className="template-prompt-discard" role="group" aria-label="放弃模板提示词修改"><p>放弃此模板提示词的未保存修改？</p><div><button type="button" className="btn quiet" onClick={() => { setDiscarding(false); editorRef.current?.focus(); }}>继续编辑</button><button type="button" className="btn" onClick={discard}>放弃修改</button></div></div> : <div className="template-prompt-actions"><button type="button" className="btn quiet" disabled={saving || reloading} onClick={cancel}>取消编辑</button><button type="submit" className="btn primary" disabled={!dirty || !draft.content.trim() || Boolean(library.savingId) || reloading || Boolean(conflict)}><Check size={16}/>{saving ? '正在保存…' : '保存提示词'}</button></div>}
      </form> : <pre className="template-paper template-prompt-content">{readable}</pre>}
      <p className="template-prompt-note">保存后用于后续新任务。</p>
    </>}
    <p className="template-prompt-feedback" role="status" aria-live="polite">{feedback}</p>
  </section>;
}
