import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingWorkspace from '../src/Writing';
import { useWriting } from '../src/useWriting';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';
import '../src/demo-theme.css';

const params = new URLSearchParams(location.search);
document.documentElement.dataset.theme = params.get('theme') || 'civic';
const source = { sourceId: 'fixture-source', revisionId: 'fixture-revision' };
const quote = '保存的来源摘录，必须保留。';
const content = '文稿现有正文不因原始资料删除而改变。[E1]\n\n后续段落仍然可以正常阅读。';
const availability = status => ({ sources: [{ ...source, status }], assets: [{ ...source, assetId: 'fixture-asset', status }] });
const fixture = () => ({ id: 'deleted-writing-fixture', title: '来源删除后的写作阅览', version: 4, mode: 'strict',
  topic: '来源删除', articleTitle: '来源删除后的写作阅览', workflow: 'chapter', automation: 'manual',
  brief: { topic: '来源删除', articleTitle: '来源删除后的写作阅览', targetWords: 12000, workflow: 'chapter', automation: 'manual' },
  sourceIds: [source.sourceId], sources: [{ ...source, title: '原始引用文档' }],
  libraryAvailability: availability(params.get('deleted') === '0' ? 'available' : 'deleted'),
  activeSectionId: 'fixture-section', outlineConfirmed: true, workflowPendingSteps: ['body'], runs: [],
  sections: [{ id: 'fixture-section', title: '保留正文与引文', goal: '', mode: 'inherit', words: 600, targetWords: 600,
    version: 2, content, contentHash: 'fixture-content-hash', locked: false, manual: true,
    evidence: [{ ...source, id: 'E1', blockId: 'fixture-block', title: '原始引用文档', quote, start: 0, end: Array.from(quote).length }] }],
  illustrations: [{ ...source, id: 'fixture-figure', sectionId: 'fixture-section', assetId: 'fixture-asset',
    sourceTitle: '原始引用文档', status: 'accepted', reviewed: true, caption: '保留的原有图注',
    url: 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="120"><rect width="300" height="120" fill="#e3e7e4"/></svg>'), credit: '原有署名',
    anchor: { paragraphIndex: 0, paragraphExact: content.split('\n\n')[0], contentHash: 'fixture-content-hash' } }],
});
const requests = [], opened = [], mutations = [];
let remote = fixture(), state, setProject, deferredRead = null;
window.fetch = async (input, options = {}) => {
  const url = String(input), method = options.method || 'GET';
  requests.push({ url, method });
  if (method !== 'GET') { mutations.push({ url, method }); throw new Error('Unexpected mutation in isolated fixture'); }
  if (url === '/api/writing/projects') return Response.json({ projects: [structuredClone(remote)] });
  if (url === '/api/writing/projects/deleted-writing-fixture') {
    if (deferredRead) return new Promise(resolve => { deferredRead.resolve = resolve; });
    return Response.json({ project: structuredClone(remote) });
  }
  if (url.endsWith('/generation-plan')) return Response.json({ plan: { canGenerate: false, blockingReasons: ['隔离验收不生成'] } });
  throw new Error('Unsupported fixture request: ' + url);
};
const blocked = name => () => { mutations.push(name); throw new Error('Unexpected writing action: ' + name); };
function Workspace() {
  const [project, change] = useState(fixture);
  state = { project, dirty: false }; setProject = change;
  const writing = { project, setProject: change, projects: [], busy: '', loading: false, dirty: false, error: '', pending: false,
    remoteChanged: false, generationPlan: null, planLoading: false, planError: '', clearError() {},
    save: blocked('save'), review: blocked('review'), matchCitations: blocked('match'), generateOutline: blocked('outline'),
    generateSection: blocked('section'), generateDocument: blocked('document'), generateEdit: blocked('edit'), exportDocument: blocked('export') };
  return <div style={{ height: '100vh' }}><WritingWorkspace stage="editor" onStageChange={() => {}} writing={writing}
    documents={[]} onOpenDoc={target => opened.push(target)}/></div>;
}
function Hook() {
  state = useWriting(); setProject = state.setProject;
  return <output id="hook-state">{state.loading ? 'loading' : state.dirty ? 'dirty' : 'ready'}</output>;
}
window.__deletedWriting = {
  get state() { return state; }, get remote() { return remote; }, requests, opened, mutations,
  setProject: value => setProject(value),
  markDeleted: () => {
    remote = { ...remote, libraryAvailability: availability('deleted') };
    if (params.get('mode') !== 'hook') setProject(project => ({ ...project, libraryAvailability: availability('deleted') }));
    window.dispatchEvent(new CustomEvent('wenshu-library-changed', { detail: { sourceId: source.sourceId } }));
  },
  defer: () => { deferredRead = {}; },
  release: () => { deferredRead.resolve(Response.json({ project: structuredClone(remote) })); deferredRead = null; },
};
createRoot(document.getElementById('deleted-writing-root')).render(params.get('mode') === 'hook' ? <Hook/> : <Workspace/>);
