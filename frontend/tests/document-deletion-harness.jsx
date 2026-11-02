import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useDocumentLibrary } from '../src/useDocumentLibrary';
import { DocumentCardGrid } from '../src/DocumentCardGrid';
import { DocumentReader } from '../src/DocumentReader';
import { DocumentDeleteDialog } from '../src/DocumentDeleteDialog';
import { initializeDemoMode } from '../src/demo-mode';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';
import '../src/document-cards.css';
import '../src/reader-layout.css';
import '../src/demo-theme.css';

initializeDemoMode();
document.documentElement.dataset.theme = new URLSearchParams(location.search).get('theme') || 'civic';
const documents = [1, 2].map(index => ({ id: `source-${index}`, revisionId: `revision-${index}`, title: index === 1 ? '被文字和图片共同引用的隔离样本文档' : '另一份保留不动的隔离文档', body: ['这是独立验收使用的模拟资料，没有真实内容。'], summary: '这是一份模拟资料，用于验证引用提示、删除确认与迟到响应保护。', tags: [], classificationPaths: [], selected: true, favorite: false, status: 'ready', createdAt: '2026-10-27', date: '2026-10-27', language: 'zh', languageCode: 'zh' }));
const fixture = { documents, requests: [], unexpected: [], changed: [], impactVersion: 1, scenario: 'references', deleted: false, releaseCatalog: null, holdCatalog: false, holdDelete: false, releaseDelete: null, holdImpact: false, releaseImpact: null, library: null };
window.__deletionFixture = fixture;
window.addEventListener('wenshu-library-changed', event => fixture.changed.push(event.detail));
const impactFor = sourceId => ({ sourceId, title: documents.find(doc => doc.id === sourceId)?.title, revisionId: `revision-${sourceId.at(-1)}`, impactToken: `impact-${fixture.impactVersion}`, referenceCount: fixture.scenario === 'plain' ? 0 : 1, hasReferences: fixture.scenario !== 'plain', revisionCount: 2, imageCount: 3, activeJobs: fixture.scenario === 'busy' ? [{ id: 'job', label: '模拟资料加工' }] : [], canDelete: fixture.scenario !== 'busy', references: fixture.scenario === 'plain' ? [] : [{ projectId: 'project', title: '含配图的已保存隔离文稿', textReferences: 2, imageReferences: 1, selectedSource: true, deleted: false }] });
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
window.fetch = async (input, options = {}) => {
  const url = new URL(input, location.origin), method = options.method || 'GET';
  const request = { path: url.pathname, method, body: options.body ? JSON.parse(options.body) : null };
  fixture.requests.push(request);
  if (method === 'GET' && url.pathname === '/api/documents') {
    const snapshot = fixture.documents.filter(doc => !fixture.deleted || doc.id !== 'source-1');
    if (fixture.holdCatalog) { fixture.holdCatalog = false; await new Promise(resolve => { fixture.releaseCatalog = resolve; }); }
    return json({ documents: snapshot, total: snapshot.length });
  }
  if (method === 'GET' && url.pathname === '/api/jobs') return json({ jobs: [], counts: {} });
  const sourceId = url.pathname.split('/')[3];
  if (method === 'GET' && url.pathname.endsWith('/deletion-impact')) {
    const impact = impactFor(sourceId);
    if (fixture.holdImpact) { fixture.holdImpact = false; await new Promise(resolve => { fixture.releaseImpact = resolve; }); }
    return json({ impact });
  }
  if (method === 'DELETE' && url.pathname === `/api/documents/${sourceId}`) {
    if (fixture.holdDelete) { fixture.holdDelete = false; await new Promise(resolve => { fixture.releaseDelete = resolve; }); }
    if (fixture.scenario === 'failure') return json({ detail: '隔离模拟删除失败，资料未改动。' }, 503);
    if (request.body.impactToken !== `impact-${fixture.impactVersion}`) return json({ detail: { message: '引用范围已变化，请重新确认。', impact: impactFor(sourceId) } }, 409);
    fixture.deleted = true;
    return json({ deleted: true, sourceId, removed: {} });
  }
  if (method === 'PATCH' && url.pathname === `/api/documents/${sourceId}`) return json({ document: Object.assign(documents.find(doc => doc.id === sourceId), request.body) });
  fixture.unexpected.push(request); throw new Error(`Unexpected fixture request: ${method} ${url.pathname}`);
};

function Harness() {
  const library = useDocumentLibrary();
  const [target, setTarget] = useState(null), [reader, setReader] = useState(null), [tab, setTab] = useState('信息');
  fixture.library = library;
  fixture.open = id => setTarget(documents.find(doc => doc.id === id));
  const deleted = id => { setTarget(null); setReader(current => current?.id === id ? null : current); };
  return <div className="deletion-fixture workspace-shell"><div className="deletion-fixture-toolbar"><strong>文档删除隔离样本</strong><span data-selected-count>{library.documents.filter(doc => doc.selected).length} 份已选资料</span><button className="btn" onClick={() => library.refresh({ quiet: true })}>刷新列表</button></div>
    <DocumentCardGrid documents={library.documents} onOpenDoc={id => setReader(library.documents.find(doc => doc.id === id))} onFavorite={() => {}} onToggleSelected={id => library.patchDocument(id, { selected: false })} onCategoryChange={() => {}} onOpenImage={() => {}} onDelete={setTarget}/>
    {reader && <DocumentReader doc={reader} selected={true} onToggleSelected={() => {}} onFavorite={() => {}} onClose={() => setReader(null)} tab={tab} onTabChange={setTab} processing={{ ai: null }} settings={{ preferences: {} }} languageView="auto" onLanguageChange={() => {}} onLocate={() => {}} onDelete={setTarget}/>}
    {target && <DocumentDeleteDialog key={target.id} document={target} onDelete={library.deleteDocument} onDeleted={deleted} onClose={() => setTarget(null)}/>}
  </div>;
}
createRoot(document.getElementById('root')).render(<Harness/>);
