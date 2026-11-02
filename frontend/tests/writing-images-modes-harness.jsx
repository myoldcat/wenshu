import React, { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingImages, { WritingImagesOverview } from '../src/WritingImages';
import { useWriting } from '../src/useWriting';
import { IMAGE_QUALITY_POLICY, imageParagraphs } from '../src/writing-images';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';
import '../src/demo-theme.css';

const params = new URLSearchParams(location.search);
const themes = ['jade', 'glass', 'civic', 'demo'];
const calls = [], requests = [], unexpected = [], notices = [];
const clone = value => structuredClone(value);
const picture = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#edf5ef"/><path d="M60 60V300H580" stroke="#587566" fill="none" stroke-width="3"/><path d="M110 220H180V300H110ZM270 170H340V300H270ZM430 110H500V300H430Z" fill="#62977b"/><text x="70" y="45" fill="#294a38" font-size="22">设备样本比较</text></svg>');
const section = (id, title, content, extra = {}) => ({ id, title, content, contentHash: `hash-${id}`, version: 2, targetWords: 700, words: 700, mode: 'inherit', goal: '并列整理已知事实。', locked: false, evidence: [], ...extra });
const initialSections = [
  section('chapter-a', '一、设备的基本情况', '第一章第一段介绍三类设备的结构，保留作者已经确认的原始正文。\n\n第一章第二段说明样本的统计范围和适用条件，图表仅展示已登记的比较结果。'),
  section('chapter-b', '二、样本的使用范围', '第二章第一段说明三类样本的共同适用条件。\n\n第二章第二段交代使用时需要核对的边界。'),
  section('chapter-locked', '三、已锁定的人工章节', '这段人工正文已经锁定，不能因配图而改变。', { locked: true, manual: true }),
  section('chapter-empty', '四、尚未编写的章节', ''),
];
const makeAsset = (id, library = false) => ({ id, assetId: id, sourceId: library ? 'library-source' : 'reference-source', revisionId: library ? 'library-revision' : 'reference-revision', sourceTitle: library ? '图库设备资料' : '本稿设备参考资料', documentTitle: library ? '图库设备资料' : '本稿设备参考资料', sourceType: library ? 'library' : 'reference', url: picture, originalCaption: library ? '设备适用范围示意。' : '三类设备的样本比较。', alt: library ? '设备适用范围示意。' : '三类设备的样本比较。', altText: '设备样本图', caption: library ? '设备适用范围示意' : '三类设备比较', credit: '隔离验收图表', scopeEligible: true, status: 'recommended', qualityGate: { policy: IMAGE_QUALITY_POLICY, passed: true } });
const assets = [makeAsset('asset-reference-a'), makeAsset('asset-reference-b'), makeAsset('asset-library-a', true)];
const makeFigure = (asset = assets[0], sectionValue = initialSections[0], index = 0) => ({ ...asset, id: `figure-${asset.assetId}-${sectionValue.id}`, sectionId: sectionValue.id, status: 'accepted', anchor: { paragraphIndex: index, paragraphExact: imageParagraphs(sectionValue.content)[index], contentHash: sectionValue.contentHash }, locked: false });
function makeProject(scenario) {
    return { id: `image-mode-fixture-${scenario}`, title: '设备与样本范围调研报告', topic: '设备与样本范围', articleTitle: '设备与样本范围调研报告', version: 7, mode: 'strict', automation: 'manual', sourceIds: ['reference-source'], sources: [{ sourceId: 'reference-source', revisionId: 'reference-revision', title: '本稿设备参考资料' }], sections: clone(initialSections), runs: [], illustrations: ['existing', 'stale', 'locked-figure', 'locked-chapter-figure', 'overview-complete'].includes(scenario) ? [makeFigure()] : [], illustrationReview: scenario.startsWith('overview-') ? { status: scenario === 'overview-complete' ? 'complete' : 'skipped' } : null, activeSectionId: 'chapter-a', ...(scenario === 'stale' ? { illustrations: [{ ...makeFigure(), status: 'stale' }] } : {}), ...(scenario === 'locked-figure' ? { illustrations: [{ ...makeFigure(), locked: true }] } : {}), ...(scenario === 'locked-chapter-figure' ? { illustrations: [makeFigure(assets[0], initialSections[2], 0)] } : {}) };
}
let current, setScenario;
const catalogDeferrals = [];
const manualDeferrals = [];
let deferCatalog = false, deferManual = false, failManual = false, hookPending = false;
let hookRemote = makeProject('hook-poll');
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const manualControls = {
  deferManual: value => { deferManual = value; },
  failManual: value => { failManual = value; },
  releaseManual: () => { deferManual = false; manualDeferrals.splice(0).forEach(release => release()); },
};
function listed(body = {}) {
  const scope = body.searchScope || body.scope || 'references', query = String(body.query || body.q || '').trim();
  return assets.filter(asset => (scope === 'library' || asset.sourceType !== 'library') && (!query || asset.sourceTitle.includes(query) || asset.caption.includes(query) || asset.originalCaption.includes(query)));
}
// All data is synthetic; requests not explicitly handled below cannot leave this page.
window.fetch = async (input, init = {}) => {
  const url = new URL(String(input), location.origin), method = init.method || 'GET', body = init.body ? JSON.parse(init.body) : null;
  requests.push({ url: url.pathname + url.search, method, body });
  if (params.get('hook') === '1') {
    if (url.pathname === '/api/writing/projects' && method === 'GET') return response({ projects: [hookRemote] });
    if (url.pathname.endsWith('/generation-plan') && method === 'GET') return response({ plan: { eligible: true, strategy: 'single' } });
    if (url.pathname === `/api/writing/projects/${hookRemote.id}` && method === 'GET') {
      if (hookPending) {
        hookPending = false;
        hookRemote = { ...hookRemote, version: hookRemote.version + 1, illustrations: [makeFigure()], runs: hookRemote.runs.map(run => ({ ...run, status: 'succeeded', candidate: { imageBatch: { sectionIds: ['chapter-a'], completedSectionIds: ['chapter-a'], issues: [], status: 'complete' } } })) };
      }
      return response({ project: clone(hookRemote) });
    }
    if (url.pathname.endsWith('/image-batch') && method === 'POST') {
      calls.push({ name: 'automatic-illustrations', body, projectId: hookRemote.id });
      hookRemote = { ...hookRemote, version: hookRemote.version + 1, runs: [{ id: 'hook-image-batch', kind: 'image_batch', status: 'running', jobId: 'hook-image-job', candidate: { imageBatch: { sectionIds: body.sectionIds, completedSectionIds: [], issues: [], status: 'running' } } }] };
      hookPending = true;
      return response({ project: clone(hookRemote), run: hookRemote.runs[0], job: { id: 'hook-image-job', status: 'running' } });
    }
    if (url.pathname.endsWith('/manual-image') && method === 'POST') {
      calls.push({ name: 'manual-illustration', body, projectId: hookRemote.id });
      if (failManual) return response({ detail: '模拟保存失败，请保留选图重试。' }, 409);
      const target = hookRemote.sections.find(item => item.id === body.sectionId), asset = assets.find(item => item.assetId === body.assetId);
      const illustration = { ...makeFigure(asset, target, body.paragraphIndex), caption: body.caption || asset.caption, altText: body.altText || asset.altText, reviewMethod: 'human' };
      hookRemote = { ...hookRemote, version: hookRemote.version + 1, illustrations: [...hookRemote.illustrations, illustration] };
      const result = clone({ project: hookRemote, illustration });
      if (deferManual) return new Promise(resolve => manualDeferrals.push(() => resolve(response(result))));
      return response(result);
    }
  }
  if (method === 'GET' && url.pathname.endsWith('/image-assets')) {
    // Browsing exposes source assets only, without any prior model assessment.
    const items = listed(Object.fromEntries(url.searchParams)).map(({ qualityGate, status, ...asset }) => asset);
    if (deferCatalog) return new Promise(resolve => catalogDeferrals.push(() => resolve(response({ items, total: items.length }))));
    return response({ items, images: items, assets: items, total: items.length, hasMore: false, offset: 0, limit: 60 });
  }
  unexpected.push({ url: url.pathname + url.search, method, body });
  throw new Error(`Unexpected isolated image request: ${method} ${url.pathname}`);
};

function Harness({ scenario }) {
  const [project, updateProject] = useState(() => makeProject(scenario));
  const [overviewOpen, setOverviewOpen] = useState(scenario.startsWith('overview-'));
  const projectRef = useRef(project);
  projectRef.current = project;
  // Match useWriting's synchronous current-project ref, so a response delivered
  // before React commits cannot accidentally overwrite an already requested edit.
  const updateFixture = value => {
    const next = typeof value === 'function' ? value(projectRef.current) : value;
    projectRef.current = next;
    updateProject(next);
  };
  const [busy, setBusy] = useState('');
  const [remoteChanged, setRemoteChanged] = useState(false);
  const [theme, setTheme] = useState(themes.includes(params.get('theme')) ? params.get('theme') : 'civic');
  document.documentElement.dataset.theme = theme;
  const record = (name, body) => calls.push({ name, body: clone(body ?? null), projectId: project.id });
  const mutate = operation => updateFixture(before => ({ ...operation(before), version: before.version + 1 }));
  const searchImages = async body => {
    record('search-images', body);
    const target = project.sections.find(item => item.id === body.sectionId), paragraphIndex = body.paragraphIndex ?? 0;
    const candidateAssets = listed(body).map(asset => ({ ...asset, placementSuggestions: [{ paragraphIndex, paragraphExact: imageParagraphs(target.content)[paragraphIndex], contentHash: target.contentHash, status: 'recommended', qualityPassed: true, reason: '图表对应本段已经说明的统计范围。' }] }));
    const run = { id: `run-${calls.length}`, kind: 'images', status: 'succeeded', imageTarget: body, candidate: { imageSearch: { ...body, contentHash: target.contentHash, paragraphExact: imageParagraphs(target.content)[paragraphIndex], qualityPolicy: IMAGE_QUALITY_POLICY, candidates: candidateAssets, warnings: [], coverage: { sourcesSearched: 1, imagesConsidered: candidateAssets.length, visualReviewedCount: candidateAssets.length } } } };
    mutate(before => ({ ...before, runs: [run, ...before.runs] }));
    return { project: { ...project, runs: [run, ...project.runs] }, run };
  };
  const acceptIllustration = async body => {
    record('accept-illustration', body);
    const run = project.runs.find(item => item.id === body.runId), target = project.sections.find(item => item.id === run?.candidate?.imageSearch?.sectionId || item.id === body.sectionId) || project.sections[0];
    const asset = assets.find(item => item.assetId === body.assetId) || assets[0], figure = { ...makeFigure(asset, target, body.paragraphIndex ?? 0), caption: body.caption || asset.caption, altText: body.altText || asset.altText };
    mutate(before => ({ ...before, illustrations: [...before.illustrations, figure] }));
    return { project: { ...project, illustrations: [...project.illustrations, figure] }, illustration: figure };
  };
  const listIllustrationImages = async body => { record('list-images', body); const items = listed(body); return { items, images: items, assets: items, total: items.length, hasMore: false }; };
  const manualIllustration = async body => {
    record('manual-illustration', body);
    const target = project.sections.find(item => item.id === body.sectionId), asset = assets.find(item => item.assetId === body.assetId) || assets[0];
    const figure = { ...makeFigure(asset, target, body.paragraphIndex), caption: body.caption || asset.caption, altText: body.altText || asset.altText, reviewMethod: 'human' };
    setBusy('manual-image');
    if (deferManual) await new Promise(resolve => manualDeferrals.push(resolve));
    setBusy('');
    if (failManual) return null;
    const saved = { ...project, version: project.version + 1, illustrations: [...project.illustrations, figure] };
    if (projectRef.current.id !== project.id) return null;
    const latest = projectRef.current.sections.find(item => item.id === target.id);
    if (latest?.content !== target.content || latest?.contentHash !== target.contentHash) { setRemoteChanged(true); return { project: saved, illustration: figure }; }
    updateFixture(saved);
    return { project: saved, illustration: figure };
  };
  const automaticIllustrations = async body => {
    record('automatic-illustrations', body);
    const ids = body.sectionIds || body.selectedSectionIds || [];
    const additions = project.sections.filter(item => ids.includes(item.id)).map(item => makeFigure(assets[0], item, 0));
    const run = { id: `batch-${calls.length}`, kind: 'image_batch', status: 'succeeded', candidate: { imageBatch: { sectionIds: ids, completedSectionIds: ids, issues: [], status: 'complete' } } };
    mutate(before => ({ ...before, illustrations: [...before.illustrations, ...additions], runs: [run, ...before.runs] }));
    return { project: { ...project, illustrations: [...project.illustrations, ...additions], runs: [run, ...project.runs] }, job: { id: 'automatic-image-job', status: 'succeeded' } };
  };
  const writing = {
    project, busy, dirty: scenario === 'dirty' || scenario === 'overview-dirty', remoteChanged: scenario === 'remote' || scenario === 'overview-remote' || remoteChanged, pending: scenario === 'pending' || scenario === 'overview-pending', loading: false, error: null,
    save: async () => { record('save'); return null; },
    searchImages, acceptIllustration, listIllustrationImages, manualIllustration, insertManualIllustration: manualIllustration, automaticIllustrations,
    completeIllustrations: async (body = {}) => { record('complete-images', body); mutate(before => ({ ...before, ...(body.clearExisting ? { illustrations: [] } : {}), illustrationReview: { status: body.skip ? 'skipped' : 'complete' } })); return { project }; },
    updateIllustration: async (id, body) => { record('update-image', { id, ...body }); mutate(before => ({ ...before, illustrations: before.illustrations.map(item => item.id === id ? { ...item, ...body, status: 'accepted' } : item) })); return { project }; },
    removeIllustration: async id => { record('remove-image', { id }); mutate(before => ({ ...before, illustrations: before.illustrations.filter(item => item.id !== id) })); return { project }; },
    cancelRun: async id => { record('cancel-run', { id }); return { project }; },
  };
  current = { project, busy, remoteChanged, scenario, calls, requests, unexpected, notices };
  window.__imageModes = { get state() { return current; }, calls, requests, unexpected, notices, assets, setBusy, setProject: updateFixture, loadScenario: value => setScenario(value), deferCatalog: value => { deferCatalog = value; }, releaseCatalog: () => { deferCatalog = false; catalogDeferrals.splice(0).forEach(release => release()); }, ...manualControls };
  return <div className="image-modes-fixture"><div className="image-modes-fixture-tools"><strong>文章配图隔离样本</strong><span>内存模拟 · 不调用模型或写入真实文稿</span><select aria-label="测试主题" value={theme} onChange={event => setTheme(event.target.value)}>{themes.map(value => <option key={value}>{value}</option>)}</select></div><div className="image-modes-fixture-main">{overviewOpen ? <WritingImagesOverview writing={writing} readOnly={scenario === 'overview-automatic'} onStart={() => setOverviewOpen(false)}/> : <WritingImages writing={writing} models={[{ id: 'image-model', name: '图片核对模型' }]} initialSectionId="chapter-a" onClose={() => record('close')} onNext={() => record('next')} onOpenDoc={body => record('open-doc', body)} onToast={value => notices.push(value)}/>}</div></div>;
}
function App() { const [scenario, updateScenario] = useState(params.get('scenario') || 'fresh'); setScenario = updateScenario; return <Harness key={scenario} scenario={scenario}/>; }
function HookHarness() {
  const writing = useWriting();
  document.documentElement.dataset.theme = themes.includes(params.get('theme')) ? params.get('theme') : 'civic';
  current = { project: writing.project, busy: writing.busy, loading: writing.loading, dirty: writing.dirty, pending: writing.pending, remoteChanged: writing.remoteChanged, error: writing.error, calls, requests, unexpected, notices };
  window.__imageModes = { get state() { return current; }, calls, requests, unexpected, notices, setProject: writing.setProject, ...manualControls };
  return <div className="image-modes-fixture"><div className="image-modes-fixture-tools">真实 Hook · 内存模拟 API</div><div className="image-modes-fixture-main">{writing.loading ? <p>读取隔离文稿</p> : <WritingImages writing={writing} models={[]} initialSectionId="chapter-a" onClose={() => {}} onNext={() => {}} onToast={value => notices.push(value)}/>}</div></div>;
}
createRoot(document.getElementById('image-modes-root')).render(params.get('hook') === '1' ? <HookHarness/> : <App/>);
