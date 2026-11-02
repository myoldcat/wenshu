import { isDemoMode } from './demo-mode';
import React, { useState } from 'react';
import { BookOpen, Check, DownloadSimple, ImageSquare, Plus, SidebarSimple, Star, Trash, X } from '@phosphor-icons/react';
import { AIServiceNotice, AISummary, AITranslationBody, AIClassification } from './AIReader';
import { OriginalFilePreview } from './OriginalFilePreview';
import { documentLanguageName, readingTitles, readingView } from './translation-view.mjs';
import { OriginalLanguageBadge } from './OriginalLanguageBadge';
import { BODY_WORD_COUNT_DESCRIPTION, bodyWordCountLabel } from './document-metrics.mjs';

const sidebarTabs = ['摘要', '知识', '图片', '信息'];

export function DocumentReader({ doc, selected, onToggleSelected, onFavorite, onClose, tab, onTabChange,
  loading, error, onRetry, processing, settings, onSettings, languageView, onLanguageChange,
  matched, onLocate, onSaveTags, onSaveClassification, onSaveArticleType, onDelete, revision }) {
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [originalVisible, setOriginalVisible] = useState(false);
  const [originalOpened, setOriginalOpened] = useState(false);
  const activeTab = sidebarTabs.includes(tab) ? tab : '摘要';
  const previewRevision = revision || doc.revisionId;
  const originalUrl = `/api/documents/${encodeURIComponent(doc.id)}/original${previewRevision ? `?revision_id=${encodeURIComponent(previewRevision)}` : ''}`;
  const previewUrl = `/api/documents/${encodeURIComponent(doc.id)}/preview${previewRevision ? `?revision_id=${encodeURIComponent(previewRevision)}` : ''}`;
  const assets = Array.isArray(doc.imageAssets) ? doc.imageAssets : doc.assets?.length ? doc.assets : doc.image ? [{ id: 'cover', url: doc.image, alt: '原始文档中的配图' }] : [];
  const tables = (doc.tables?.length ? doc.tables : [doc.tableData]).filter(table => (Array.isArray(table) ? table : table?.rows || table?.data || []).length > 0);
  const titles = readingTitles(doc, processing.ai?.translation, readingView(languageView, doc, processing.ai?.translation));
  const showOriginal = () => { setOriginalOpened(true); setOriginalVisible(true); };
  const locateInArticle = id => { setOriginalVisible(false); onLocate(id); if (originalVisible) requestAnimationFrame(() => document.getElementById(`source-block-${id}`)?.scrollIntoView({ block: 'center', behavior: isDemoMode() || matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })); };

  return <div className="reader-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <aside className="reader-panel reader-expanded" role="dialog" aria-modal="true" aria-label="资料阅读" aria-labelledby="reader-article-title">
      <header className="reader-window-header">
        <div className="reader-window-leading"><span className="reader-window-label"><BookOpen size={19}/><span>文章阅览</span></span><div className="reader-view-switch" role="group" aria-label="阅览方式"><button type="button" aria-pressed={!originalVisible} onClick={() => setOriginalVisible(false)}>正文阅读</button><button type="button" aria-pressed={originalVisible} disabled={loading || !!error} onClick={showOriginal}>原文阅读</button></div><a href={originalUrl} className="btn quiet reader-file-download" download><DownloadSimple size={16}/>下载文件</a></div>
        <div className="reader-window-actions">
          {onDelete && <button type="button" className="icon-btn document-reader-delete" aria-label="删除当前文档" title="删除文档" onClick={() => onDelete(doc)}><Trash size={20}/></button>}
          <button className={`btn reader-selection-action ${selected ? 'is-selected' : ''}`} onClick={onToggleSelected}>{selected ? <Check size={17}/> : <Plus size={17}/>}{selected ? '已加入选材' : '加入选材'}</button>
          <button className="icon-btn" aria-label="收藏当前资料" aria-pressed={!!doc.favorite} title={doc.favorite ? '取消收藏' : '收藏资料'} onClick={onFavorite}><Star size={20} weight={doc.favorite ? 'fill' : 'regular'}/></button>
          <button className="icon-btn reader-sidebar-toggle" aria-label={sidebarHidden ? '展开阅读侧栏' : '收起阅读侧栏'} title={sidebarHidden ? '展开阅读侧栏' : '收起阅读侧栏'} aria-expanded={!sidebarHidden} aria-controls="reader-sidebar" onClick={() => setSidebarHidden(value => !value)}><SidebarSimple size={21}/></button>
          <button className="icon-btn" aria-label="关闭资料阅读" title="关闭阅览" onClick={onClose}><X size={23}/></button>
        </div>
      </header>
      <div className={`reader-workspace ${sidebarHidden ? 'sidebar-hidden' : ''}`}>
        <main className="reader-main" aria-label="文章正文">
          <div className="reader-scroll reader-content-scroll" hidden={originalVisible}>
            <header className="reader-article-heading"><h1 id="reader-article-title"><OriginalLanguageBadge doc={doc}/>{titles.title}</h1>{titles.original && <p className="reader-original-title"><span>原文标题</span>{titles.original}</p>}</header>
            {loading && <p className="local-library-message" role="status">正在读取已保存的正文…</p>}
            {error && <div className="local-library-message is-error" role="alert"><span>{error}</span><button className="btn quiet" onClick={onRetry}>重新读取</button></div>}
            {!loading && !error && <div className="reader-body">
              <AITranslationBody doc={doc} processing={processing} settings={settings} onSettings={onSettings}
                view={languageView} onViewChange={onLanguageChange} matched={matched} showControls={false}/>
            </div>}
          </div>
          {originalOpened && <section className="reader-original-view" aria-label="原始文件" hidden={!originalVisible}><OriginalFilePreview key={previewUrl} url={previewUrl} title={doc.originalTitle || doc.title}/></section>}
        </main>
        <aside className="reader-sidebar" id="reader-sidebar" aria-label="阅读辅助信息" hidden={sidebarHidden}>
          <nav className="reader-sidebar-tabs" aria-label="阅读侧栏内容">{sidebarTabs.map(name => <button type="button" key={name} className={activeTab === name ? 'active' : ''} aria-pressed={activeTab === name} onClick={() => onTabChange(name)}>{name}</button>)}</nav>
          <div className="reader-sidebar-scroll">
            <AIServiceNotice processing={processing}/>
            {!loading && !error && <section className="reader-sidebar-section" aria-label={activeTab}>
              {activeTab === '摘要' && <AISummary doc={doc} processing={processing} settings={settings} onSettings={onSettings} onLocate={locateInArticle}/>}
              {activeTab === '知识' && <AIClassification doc={doc} processing={processing} settings={settings} onSettings={onSettings} onLocate={locateInArticle} onSaveTags={onSaveTags} onSaveClassification={onSaveClassification} onSaveArticleType={onSaveArticleType}/>}
              {activeTab === '图片' && <div className="reader-materials">
                {assets.map((asset, index) => <figure key={asset.id || index}><img src={asset.url} alt={asset.alt || `原始文档配图 ${index + 1}`} loading="lazy"/><figcaption>{asset.alt || `原文配图 ${index + 1}`} · 本地保存</figcaption></figure>)}
                {!assets.length && <div className="reader-sidebar-empty"><ImageSquare size={29} weight="light"/><h3>这份资料暂无可用配图</h3><p>暂未收录适合展示的正文图片。</p></div>}
              </div>}
              {activeTab === '信息' && <div className="reader-metadata">
                <h3>文档信息</h3>
                <dl><div><dt>来源</dt><dd>{doc.source || '本地文档'}</dd></div><div><dt>日期</dt><dd>{doc.date || '未提供'}</dd></div><div><dt>原文语言</dt><dd>{documentLanguageName(doc) || '未识别'}</dd></div><div title={BODY_WORD_COUNT_DESCRIPTION}><dt>正文字数</dt><dd>{bodyWordCountLabel(doc)}</dd></div><div><dt>正文段落</dt><dd>{doc.blocks?.length ?? doc.body?.length ?? 0}</dd></div>{doc.revisionCount > 1 && <div><dt>已保存版本</dt><dd>{doc.revisionCount}</dd></div>}{(revision || doc.revisionId) && <div><dt>当前版本</dt><dd>{revision || doc.revisionId}</dd></div>}</dl>
                {!!tables.length && <details className="reader-materials"><summary>原文表格 · {tables.length}</summary>{tables.map((table, index) => {
                  const rows = Array.isArray(table) ? table : table.rows || table.data || [];
                  return rows.length > 0 && <div className="material-table" key={index}><h3>原文表格 {index + 1}</h3><table><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{typeof cell === 'string' ? cell : cell?.text || String(cell ?? '')}</td>)}</tr>)}</tbody></table></div>;
                })}</details>}
              </div>}
            </section>}
          </div>
        </aside>
      </div>
    </aside>
  </div>;
}
