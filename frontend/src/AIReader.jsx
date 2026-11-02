import React, { useEffect, useRef, useState } from 'react';
import { ArrowClockwise, ArrowRight, Check, PencilLine, Quotes, Sparkle } from '@phosphor-icons/react';
import { api, connectionMessage } from './local-api';
import { DocumentTag, DocumentTags } from './DocumentTags';
import { ARTICLE_TYPES, getDocumentTags, documentArticleType } from './document-tags.mjs';
import { documentExcerptLabel, documentNeedsTranslation, hasChineseTranslation, isChineseDocument, originalLanguageLabel, readingView, translationNotice } from './translation-view.mjs';
import './ai-reader.css';

const kindLabels = { summary: 'AI 摘要', translation: '中文翻译', classification: '自动分类' };
const statusLabels = { not_started: '尚未生成', queued: '等待处理', running: '正在处理', succeeded: '已完成', failed: '处理失败', cancelled: '已取消' };
const errorText = error => typeof error === 'string' ? error : error?.message || (error ? JSON.stringify(error) : '');

function EvidenceLinks({ ids = [], blocks = [], onLocate }) {
  return <span className="ai-evidence-links">{ids.map(id => {
    const index = blocks.findIndex(block => block.id === id);
    return index >= 0 && <button type="button" key={id} onClick={() => onLocate(id)} aria-label={`查看来源段落 ${index + 1}`}>段落 {index + 1}<ArrowRight size={12}/></button>;
  })}</span>;
}

export function AIServiceNotice({ processing }) {
  return <>{processing.loading && <p className="local-library-message" role="status">正在读取文档处理结果…</p>}{processing.error && <div className="local-library-message is-error" role="alert"><span>{processing.error}</span><button className="btn quiet" onClick={() => processing.refresh()}>重新读取</button></div>}{processing.ai?.stale && <div className="reader-note">当前阅读的是历史版本，显示这个版本的处理结果。重新打开最新文档后可生成新结果。</div>}</>;
}

export function ProcessingControls({ kind, processing, settings, onSettings, compact = false }) {
  const run = processing.ai?.[kind];
  const job = processing.ai?.jobs?.find(item => item.id === run?.jobId);
  const pending = ['queued','running'].includes(run?.status);
  const interrupted = ['failed','cancelled'].includes(run?.status);
  const regenerate = !!run?.result || interrupted;
  const configured = !!settings.preferences[`${kind}ModelId`];
  if (compact && run?.status === 'succeeded' && run.result && !run.error) {
    return <button type="button" className="icon-btn ai-regenerate" aria-label={`重新生成${kindLabels[kind]}`} title={`重新生成${kindLabels[kind]}`} disabled={!!processing.busy || processing.loading || processing.ai?.stale} onClick={() => configured ? processing.submit(kind, true) : onSettings()}><ArrowClockwise size={16}/></button>;
  }
  return <div className="ai-processing-controls">
    <div className="ai-processing-top"><span className={`ai-processing-status status-${run?.status || 'not_started'}`} role="status"><Sparkle size={15}/>{kindLabels[kind]} · {statusLabels[run?.status] || '尚未生成'}</span><div className="ai-processing-actions">
      {!configured ? <button type="button" className="inline-link" onClick={onSettings}>配置模型<ArrowRight size={14}/></button> : !pending && <button type="button" className="btn" disabled={!!processing.busy || processing.loading || processing.ai?.stale} onClick={() => processing.submit(kind, regenerate)}>{processing.busy === kind ? '正在提交…' : regenerate ? '重新生成' : `生成${kindLabels[kind]}`}</button>}
      {pending && run.jobId && <button type="button" className="inline-link" disabled={!!processing.busy} onClick={() => processing.jobAction(run.jobId, 'cancel')}>取消任务</button>}
      {interrupted && run.jobId && job?.retryable !== false && job?.stage !== 'superseded' && <button type="button" className="inline-link" disabled={!!processing.busy || processing.ai?.stale} onClick={() => processing.jobAction(run.jobId, 'retry')}>重试原任务</button>}
    </div></div>
    {run?.error && <p className="ai-processing-error" role="alert">{errorText(run.error)}</p>}
    {run?.previousSuccess && <p className="ai-processing-note">当前显示上次结果。</p>}
  </div>;
}

export function AISummary({ doc, processing, settings, onSettings, onLocate }) {
  const result = processing.ai?.summary?.result;
  const translatedExcerpt = processing.ai?.translation?.result?.segments?.map(segment => segment.text).filter(Boolean).join('\n').slice(0, 260);
  const excerptDoc = translatedExcerpt && doc.summarySource !== 'ai_summary' ? { ...doc, snippet: '', summary: translatedExcerpt, summarySource: 'translation' } : doc;
  return <div className="reader-body ai-summary">
    {(!result || processing.ai?.summary?.status !== 'succeeded' || processing.ai?.summary?.error) && <ProcessingControls kind="summary" processing={processing} settings={settings} onSettings={onSettings}/>}
    {result ? <>
      <div className="ai-result-heading"><h3>核心摘要</h3>{processing.ai?.summary?.status === 'succeeded' && !processing.ai?.summary?.error && <ProcessingControls compact kind="summary" processing={processing} settings={settings} onSettings={onSettings}/>}</div><div className="reading-callout"><Quotes size={24}/><p>{result.summary}</p></div>
      {!!result.keyPoints?.length && <><h3>关键要点</h3><ol className="ai-key-points">{result.keyPoints.map((point, index) => <li key={index}><p>{point.text}</p><EvidenceLinks ids={point.blockIds} blocks={doc.blocks} onLocate={onLocate}/></li>)}</ol></>}
    </> : <><h3>{documentExcerptLabel(excerptDoc)}</h3><p className="muted">{excerptDoc.summarySource === 'translation' ? '摘自已保存的中文译文，供摘要生成前浏览。' : excerptDoc.summarySource === 'ai_summary' ? '已保存的中文摘要；正在读取完整要点与来源。' : '摘自原文开头，供生成前浏览。'}</p><div className="reading-callout"><Quotes size={24}/><p>{excerptDoc.summary || '没有可展示的正文摘录。'}</p></div></>}
  </div>;
}

function TranslationBlock({ block, segment, view, matched, processing, sourceChinese, revising }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(segment?.text || '');
  useEffect(() => { setEditing(false); setDraft(segment?.text || ''); }, [block.id]);
  const original = block.kind === 'heading' ? <h3>{block.text}</h3> : <p>{block.text}</p>;
  const translated = segment ? block.kind === 'table' && segment.table ? <div className="material-table ai-translated-table"><table aria-label="译文表格"><tbody>{segment.table.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></div> : block.kind === 'heading' ? <h3>{segment.text}</h3> : <p>{segment.text}</p> : sourceChinese ? original : <><small className="ai-column-label">该段暂无中文译文，以下为原文</small>{original}</>;
  return <section className={`source-block ${matched ? 'is-search-match' : ''}`} id={`source-block-${block.id}`}>
    <div className={`ai-translation-block view-${view}`}>
      {view !== 'translation' && <div className="ai-original-text" aria-label="原文">{original}</div>}
      {view !== 'original' && <div className={`ai-translated-text ${revising && segment && !editing ? 'has-revision-action' : ''}`} aria-label="中文译文">{editing ? <form className="ai-translation-edit" onSubmit={async event => { event.preventDefault(); if (await processing.editTranslation(block.id, draft, true)) setEditing(false); }}><label>修订这一段译文<textarea required rows={Math.min(12, Math.max(4, Math.ceil(draft.length / 45)))} value={draft} onChange={event => setDraft(event.target.value)}/></label><div><button type="button" className="btn quiet" disabled={!!processing.busy} onClick={() => setEditing(false)}>取消</button><button type="submit" className="btn" disabled={!!processing.busy || processing.ai?.stale || ['queued','running'].includes(processing.ai?.translation?.status) || !draft.trim()}><Check size={14}/>{processing.busy === block.id ? '正在保存…' : '保存并锁定'}</button></div></form> : <>{translated}{segment && <>{segment.edited && <small className="ai-translation-lock">{segment.locked ? '人工修订 · 已锁定' : '人工修订'}</small>}{revising && <button type="button" className="ai-translation-pencil" aria-label="修订这一段译文" title="修订这一段译文" disabled={!!processing.busy || processing.ai?.stale || ['queued','running'].includes(processing.ai?.translation?.status)} onClick={() => { setDraft(segment.text); setEditing(true); }}><PencilLine size={16}/></button>}</>}</>}</div>}
    </div>
  </section>;
}

export function AITranslationBody({ doc, processing, settings, onSettings, view, onViewChange, matched, showControls = true }) {
  const [revising, setRevising] = useState(false);
  useEffect(() => setRevising(false), [doc.id, doc.revisionId]);
  const run = processing.ai?.translation;
  const result = run?.result;
  const segments = new Map((result?.segments || []).map(segment => [segment.blockId, segment]));
  const sourceChinese = isChineseDocument(doc);
  const needsTranslation = documentNeedsTranslation(doc);
  const translated = hasChineseTranslation(run);
  const activeView = readingView(view, doc, run);
  const notice = translationNotice(doc, processing, settings.preferences);
  return <>
    {needsTranslation && <div className="ai-reading-toolbar"><div className="tabs" aria-label="正文语言视图">{[['translation','中文'],['original','原文'],['compare','对照']].map(([value,label]) => <button type="button" key={value} aria-pressed={activeView === value} className={activeView === value ? 'active' : ''} disabled={value !== 'original' && !translated} title={value !== 'original' && !translated ? '中文译文生成后可查看' : undefined} onClick={() => onViewChange(value)}>{label}</button>)}</div><div className="ai-language-actions"><span className="ai-language-hint">{originalLanguageLabel(doc)}</span><button type="button" className="inline-link ai-revision-toggle" aria-pressed={revising} disabled={!translated} onClick={() => { if (!revising && activeView === 'original') onViewChange('translation'); setRevising(value => !value); }}><PencilLine size={14}/>修订翻译</button></div></div>}
    {notice && <div className="reader-note ai-translation-notice" role="status">{notice}</div>}
    {needsTranslation && (showControls || (!processing.loading && !!processing.ai && (!translated || ['queued', 'running', 'failed', 'cancelled'].includes(run?.status)))) && <ProcessingControls kind="translation" processing={processing} settings={settings} onSettings={onSettings}/>}
    {doc.warnings?.length > 0 && <div className="reader-note">解析提示：{doc.warnings.join('；')}</div>}
    {doc.blocks?.length ? doc.blocks.map(block => <TranslationBlock key={block.id} block={block} segment={segments.get(block.id)} matched={matched === block.id} view={activeView} sourceChinese={sourceChinese} processing={processing} revising={revising}/>) : (doc.body || []).map((text,index) => <section className="source-block" key={index}><p>{text}</p></section>)}
  </>;
}

const classificationKey = path => (path.nodes || []).map(node => node.id).join('/');
const editablePaths = paths => paths.map(path => ({ level1Id: path.nodes?.[0]?.id || '', level2: path.nodes?.[1]?.name || '', level3: path.nodes?.[2]?.name || '' }));
const blankPath = () => ({ level1Id: '', level2: '', level3: '' });

export function AIClassification({ doc, processing, settings, onSettings, onLocate, onSaveTags, onSaveClassification, onSaveArticleType }) {
  const classificationAvailable = processing.ai?.classificationAvailable !== false;
  const result = classificationAvailable ? processing.ai?.classification?.result : null;
  const paths = doc.classificationPaths || [];
  const suggestions = result?.classificationPaths || [];
  const keywords = processing.ai?.summary?.result?.keywords || [];
  const derivedTags = [...new Set(paths.flatMap(path => (path.nodes || []).map(node => node.name)))];
  const tags = doc.tags || [];
  const auxiliaryTags = getDocumentTags(doc).filter(tag => !['classification', 'language', 'articleType'].includes(tag.kind) && !derivedTags.includes(tag.label));
  const showSuggestions = suggestions.length > 0 && (result.manualOverride || !result.applied || JSON.stringify(paths.map(classificationKey)) !== JSON.stringify(suggestions.map(classificationKey)));
  const [newTag, setNewTag] = useState('');
  const [editing, setEditing] = useState(false);
  const [draftPaths, setDraftPaths] = useState([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [taxonomy, setTaxonomy] = useState([]);
  const [taxonomyError, setTaxonomyError] = useState('');
  const [taxonomyLoading, setTaxonomyLoading] = useState(false);
  const [taxonomyAttempt, setTaxonomyAttempt] = useState(0);
  const [articleTypeDraft, setArticleTypeDraft] = useState(doc.articleType || '');
  const [articleTypeTouched, setArticleTypeTouched] = useState(false);
  const identity = `${doc.id}:${doc.revisionId || ''}`;
  const activeIdentity = useRef(identity);
  activeIdentity.current = identity;
  useEffect(() => { setNewTag(''); setEditing(false); setDraftPaths([]); setSaving(false); setSaveError(''); setArticleTypeDraft(doc.articleType || ''); setArticleTypeTouched(false); }, [identity]);
  useEffect(() => { if (!articleTypeTouched) setArticleTypeDraft(doc.articleType || ''); }, [doc.articleType, articleTypeTouched]);
  useEffect(() => {
    if (!classificationAvailable || !editing) return;
    const controller = new AbortController();
    setTaxonomyLoading(true); setTaxonomyError('');
    api('/ai/taxonomy', { signal: controller.signal })
      .then(response => { if (!controller.signal.aborted) setTaxonomy(response.categories || []); })
      .catch(problem => { if (!controller.signal.aborted) setTaxonomyError(connectionMessage(problem)); })
      .finally(() => { if (!controller.signal.aborted) setTaxonomyLoading(false); });
    return () => controller.abort();
  }, [classificationAvailable, editing, taxonomyAttempt]);
  const updatePath = (index, changes) => setDraftPaths(items => items.map((item, itemIndex) => itemIndex === index ? { ...item, ...changes } : item));
  const saveChange = async (write, onSuccess, failureMessage) => {
    if (saving || processing.ai?.stale) return;
    const requestIdentity = identity;
    setSaving(true); setSaveError('');
    try {
      const saved = await write();
      if (activeIdentity.current !== requestIdentity) return;
      if (saved === false) setSaveError(failureMessage);
      else onSuccess?.();
    } catch (problem) { if (activeIdentity.current === requestIdentity) setSaveError(connectionMessage(problem)); }
    finally { if (activeIdentity.current === requestIdentity) setSaving(false); }
  };
  const savePaths = event => {
    event.preventDefault();
    if (!onSaveClassification) return;
    return saveChange(() => onSaveClassification(draftPaths.map(path => ({ level1Id: path.level1Id, level2: path.level2.trim(), level3: path.level3.trim() }))), () => setEditing(false), '分类未保存，请检查提示后重试。');
  };
  const saveTags = (nextTags, onSuccess) => onSaveTags && saveChange(() => onSaveTags(nextTags), onSuccess, '标签未保存，请检查提示后重试。');
  const saveArticleType = value => onSaveArticleType && classificationAvailable && saveChange(
    () => onSaveArticleType(value), () => setArticleTypeTouched(false), '文章类型未保存，请检查提示后重试。');
  return <div className="reader-knowledge">
    {classificationAvailable && (processing.ai?.classification?.status !== 'succeeded' || !result || processing.ai?.classification?.error) && <ProcessingControls kind="classification" processing={processing} settings={settings} onSettings={onSettings}/>}
    {saveError && <p className="ai-processing-error" role="alert">{saveError}</p>}
    <section className="ai-article-type" aria-label="文章类型">
      <div className="ai-classification-heading"><h3>文章类型</h3>{onSaveArticleType && classificationAvailable && doc.articleTypeManual && <button type="button" className="inline-link" disabled={saving || processing.ai?.stale} onClick={() => saveArticleType(null)}>恢复自动</button>}</div>
      <div className="reader-tags">{documentArticleType(doc) ? <DocumentTags doc={doc} kinds={['articleType']}/> : <span className="muted">未标注</span>}</div>
      <p className="muted ai-article-type-description">新闻报道属于事实类，新闻评述属于观点类，纯技术文章属于技术类。</p>
      {onSaveArticleType && classificationAvailable && <form className="ai-custom-tag ai-article-type-editor" onSubmit={event => { event.preventDefault(); if (articleTypeDraft && articleTypeDraft !== (doc.articleType || '')) return saveArticleType(articleTypeDraft); }}>
        <label className="field">文章类型<select aria-label="文章类型" value={articleTypeDraft} disabled={saving || processing.ai?.stale} onChange={event => { setArticleTypeDraft(event.target.value); setArticleTypeTouched(event.target.value !== (doc.articleType || '')); }}><option value="" disabled>未标注</option>{ARTICLE_TYPES.map(type => <option key={type.value} value={type.value}>{type.label}</option>)}</select></label>
        <button type="submit" className="btn" disabled={saving || processing.ai?.stale || !articleTypeDraft || articleTypeDraft === (doc.articleType || '')}><Check size={14}/>{saving ? '正在保存…' : '保存类型'}</button>
      </form>}
    </section>
    {!!keywords.length && <><h3>关键词</h3><div className="reader-tags ai-keywords">{keywords.map((word, index) => <span className="pill" key={`${word}-${index}`}>{word}</span>)}</div></>}
    <section className="ai-current-classification">
      <div className="ai-classification-heading"><h3>分类标签</h3><div className="ai-result-heading-actions">{onSaveClassification && classificationAvailable && !editing && <button type="button" className="inline-link" disabled={saving || processing.ai?.stale} onClick={() => { setDraftPaths(paths.length ? editablePaths(paths) : [blankPath()]); setSaveError(''); setEditing(true); }}><PencilLine size={13}/>调整分类</button>}{classificationAvailable && processing.ai?.classification?.status === 'succeeded' && result && !processing.ai?.classification?.error && <ProcessingControls compact kind="classification" processing={processing} settings={settings} onSettings={onSettings}/>}</div></div>
      {derivedTags.length ? <div className="reader-tags"><DocumentTags doc={{ classificationPaths: paths }}/></div> : <p className="muted">暂无分类标签</p>}
      {editing && <form className="ai-classification-editor" onSubmit={savePaths}>
        <p className="muted">第一条为主路径。手动保存后，自动分类会保留你的选择；无法细分时可填写“未归类”。</p>
        {taxonomyLoading && <p className="muted" role="status">正在读取一级分类…</p>}
        {taxonomyError && <p className="ai-processing-error" role="alert">{taxonomyError} <button type="button" className="inline-link" onClick={() => setTaxonomyAttempt(value => value + 1)}>重试读取分类</button></p>}
        {draftPaths.map((path, index) => <fieldset key={index} disabled={saving || processing.ai?.stale} className="ai-classification-fields">
          <legend>{index === 0 ? '主路径' : `辅助路径 ${index}`}</legend>
          <div className="ai-path-editor-row"><label className="field">一级<select required value={path.level1Id} disabled={taxonomyLoading || !taxonomy.length} onChange={event => updatePath(index, { level1Id: event.target.value })}><option value="">选择一级主题</option>{taxonomy.map(category => <option key={category.id} value={category.id} title={category.scope}>{category.name}</option>)}</select></label><button className="inline-link" type="button" onClick={() => setDraftPaths(items => items.filter((_, itemIndex) => itemIndex !== index))}>移除路径</button></div>
          <div className="ai-path-editor-row"><label className="field">二级<input required maxLength={32} value={path.level2} onChange={event => updatePath(index, { level2: event.target.value, ...(event.target.value.trim() === '未归类' ? { level3: '未归类' } : {}) })} placeholder="更具体的主题"/></label><label className="field">三级<input required maxLength={32} value={path.level3} onChange={event => updatePath(index, { level3: event.target.value })} placeholder="主题下的具体方向"/></label></div>
        </fieldset>)}
        {!draftPaths.length && <p className="muted">保存后清除当前分类路径，资料将显示在“未归类”。</p>}
        <div className="ai-path-editor-actions"><button type="button" className="inline-link" disabled={saving || processing.ai?.stale || draftPaths.length >= 3} onClick={() => setDraftPaths(items => [...items, blankPath()])}>添加路径（{draftPaths.length}/3）</button><span/><button type="button" className="btn quiet" disabled={saving} onClick={() => setEditing(false)}>取消</button><button type="submit" className="btn" disabled={saving || processing.ai?.stale || (draftPaths.length > 0 && (taxonomyLoading || !taxonomy.length))}><Check size={14}/>{saving ? '正在保存…' : '保存分类'}</button></div>
      </form>}
    </section>
    {showSuggestions && <section className="ai-classification-result"><h3>自动分类建议</h3><p className="muted">{result.manualOverride ? '保留人工分类。' : '当前分类未更改。'}</p><div className="reader-tags"><DocumentTags doc={{ classificationPaths: suggestions }}/></div><EvidenceLinks ids={[...new Set(suggestions.flatMap(path => path.blockIds || []))]} blocks={doc.blocks} onLocate={onLocate}/></section>}
    <h3>原文语言</h3><div className="reader-tags"><DocumentTags doc={doc} kinds={['language']}/></div>
    <h3>自定义标签</h3>
    <div className="category-choices">{auxiliaryTags.map(tag => <DocumentTag key={tag.label} tag={tag} disabled={saving || processing.ai?.stale || !onSaveTags} ariaLabel={`移除自定义标签 ${tag.label}`} onClick={() => saveTags(tags.filter(item => item !== tag.label))}><Check size={13}/></DocumentTag>)}</div>
    <form className="ai-custom-tag" onSubmit={event => { event.preventDefault(); const tag = newTag.trim(); if (tag && !tags.includes(tag) && !derivedTags.includes(tag)) return saveTags([...tags, tag], () => setNewTag('')); }}><label className="field">自定义标签<input maxLength={80} value={newTag} disabled={saving || processing.ai?.stale || !onSaveTags} onChange={event => setNewTag(event.target.value)} placeholder="输入自定义标签"/></label><button className="btn" type="submit" disabled={saving || processing.ai?.stale || !onSaveTags || !newTag.trim() || tags.includes(newTag.trim()) || derivedTags.includes(newTag.trim())}>添加</button></form>
  </div>;
}
