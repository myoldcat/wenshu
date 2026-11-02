import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingWorkspace from '../src/Writing';
import { useWriting } from '../src/useWriting';
import { StatusPopover } from '../src/WorkspacePanels';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';

const query = new URLSearchParams(location.search);
document.documentElement.dataset.theme = ['jade', 'glass', 'civic'].includes(query.get('theme')) ? query.get('theme') : 'jade';
const output = document.getElementById('writing-concurrency-result');
const root = createRoot(document.getElementById('writing-concurrency-root'));
const clone = value => structuredClone(value);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const requests = [], results = [], remote = new Map();
let hook, changeStage, stage;
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
function fixture(index) {
  const title = `并发验收文稿${index}`;
  const run = { id: `run-concurrent-${index}`, jobId: `job-concurrent-${index}`, kind: 'automatic', status: 'running', stage: 'body' };
  return { id: `concurrent-${index}`, title, version: 3, mode: 'extended', modelId: '', workflowVersion: 'writing-six-step-v1',
    brief: { topic: title, articleTitle: title, purpose: '', targetWords: 6000, style: '清晰克制', template: '观点文章', workflow: 'chapter', automation: 'automatic', automaticOptions: { useSelectedSources: false, useRecommendedSources: false, mode: 'strict', includeImages: false } },
    sourceIds: [], sources: [], materialSelection: { recommendationId: null, passages: [], queries: [], items: [], userSourceIds: [], confirmed: true },
    sections: [{ id: `section-concurrent-${index}`, title: '保留的正文', goal: '', targetWords: 6000, mode: 'inherit', content: '并发编写期间，这份已保存的正文必须保留。', version: 1, evidence: [], manual: false, locked: false }],
    outlineConfirmed: true, illustrations: [], illustrationReview: null, review: null, runs: [run], automatic: { runId: run.id, jobId: run.jobId, status: 'running', stage: 'body' },
    createdAt: '2026-10-25T01:00:00Z', updatedAt: '2026-10-25T01:00:00Z' };
}
for (let index = 1; index <= 3; index++) { const project = fixture(index); remote.set(project.id, project); }
const initial = JSON.stringify([...remote.values()]);
// All network boundaries are replaced before mounting the real hook. This
// fixture validates navigation, queue labels and polling without real models.
window.fetch = async (input, options = {}) => {
  const path = typeof input === 'string' ? input : input.url, method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({ path, method, body });
  if (path === '/api/writing/projects' && method === 'GET') return response({ projects: clone([...remote.values()]) });
  if (path === '/api/writing/projects' && method === 'POST') {
    const project = { ...clone(body), id: 'concurrent-4', version: 1, workflowVersion: 'writing-six-step-v1',
      sections: [], runs: [], sources: [], illustrations: [], illustrationReview: null, review: null, outlineConfirmed: false,
      createdAt: '2026-10-25T02:00:00Z', updatedAt: '2026-10-25T02:00:00Z' };
    remote.set(project.id, project); return response({ project: clone(project) }, 201);
  }
  const match = path.match(/^\/api\/writing\/projects\/([^/]+)(.*)$/);
  const project = match && remote.get(match[1]), tail = match?.[2];
  if (project && !tail && method === 'GET') return response({ project: clone(project) });
  if (project && tail === '/steps/brief/confirm' && method === 'POST') {
    assert(body.expectedVersion === project.version, 'Brief confirmation lost version guard');
    return response({ project: clone(project) });
  }
  if (project && tail === '/automatic' && method === 'POST') {
    assert(project.id === 'concurrent-4' && body.expectedVersion === project.version, 'Wrong project or version queued');
    const run = { id: 'run-concurrent-4', jobId: 'job-concurrent-4', kind: 'automatic', status: 'queued', stage: 'materials' };
    project.runs = [run]; project.automatic = { runId: run.id, jobId: run.jobId, status: 'queued', stage: 'materials' };
    return response({ project: clone(project), runId: run.id, job: { id: run.jobId, status: 'queued' } }, 202);
  }
  throw new Error(`Unexpected isolated request: ${method} ${path}`);
};
function Harness() {
  hook = useWriting();
  const [view, setView] = useState('projects'); stage = view; changeStage = setView;
  return <div className="concurrency-fixture workspace-shell"><main className="workspace-view"><WritingWorkspace stage={view} onStageChange={setView} writing={hook} documents={[]} selectedIds={[]} models={[]}/></main><div hidden><StatusPopover kind="writing" writingJobs={[{ id: 'queued-job', kind: 'writing_automatic', status: 'queued', stage: 'writing_automatic', label: '排队验收' }]} writingJobCounts={{ queued: 1, total: 1 }}/></div></div>;
}
const button = text => [...document.querySelectorAll('button')].find(item => item.textContent.trim() === text);
async function until(predicate, label, timeout = 7000) {
  const end = performance.now() + timeout;
  while (!predicate()) { if (performance.now() > end) throw new Error(`Timeout: ${label}`); await pause(20); }
}
const passed = name => results.push({ name, status: 'passed' });
async function run() {
  root.render(<Harness/>);
  await until(() => hook?.project.id === 'concurrent-1' && !hook.loading, 'three active manuscripts');
  assert(document.querySelectorAll('.writing-project-card[data-writing-active="true"]').length === 3, 'Three running manuscripts not shown independently');
  const newButton = button('开始新的写作');
  assert(newButton && !newButton.disabled && hook.pending, 'Selected active manuscript blocks creation from project list');
  passed('project list allows creation while the selected manuscript is running');
  newButton.click();
  await until(() => !hook.project.id && stage === 'brief', 'new fourth manuscript');
  hook.setProject(value => ({ ...value, title: '等待空位的第四篇', topic: '等待空位的第四篇', automaticOptions: { useSelectedSources: false, useRecommendedSources: false, mode: 'strict', includeImages: false }, mode: 'expanded' }));
  await until(() => button('开始智能化写作') && !button('开始智能化写作').disabled, 'fourth submit enabled');
  button('开始智能化写作').click();
  await until(() => hook.project.id === 'concurrent-4' && hook.project.automatic?.status === 'queued' && !hook.busy, 'fourth enqueued');
  assert(document.querySelector('.writing-automatic h3')?.textContent.includes('排队中'), 'Queued automatic status claims execution started');
  assert(document.querySelector('.writing-automatic p')?.textContent.includes('有空位时会自动开始'), 'Queued status omits automatic admission');
  assert(document.querySelector('#writing-topic')?.disabled && document.querySelector('[data-writing-interrupt]'), 'Queued project lost read-only or explicit interruption control');
  assert([...document.querySelectorAll('.local-job-status')].some(item => item.textContent.includes('排队中')), 'Writing status popover does not identify queued automatic job');
  assert(JSON.stringify([...remote.values()].slice(0, 3)) === initial, 'Creating fourth rewrote an existing manuscript');
  passed('fourth manuscript saves and queues without changing three running manuscripts');
  document.querySelector('[aria-label="返回文稿列表"]').click();
  await until(() => stage === 'projects' && document.querySelectorAll('.writing-project-card').length === 4, 'four cards');
  const queuedCard = [...document.querySelectorAll('.writing-project-card')].find(item => item.getAttribute('aria-label') === '等待空位的第四篇');
  assert(queuedCard?.dataset.writingActive === 'false' && queuedCard.textContent.includes('排队'), 'Queued card animates or loses queue state');
  assert(!button('开始新的写作').disabled, 'Selected queued manuscript blocks further creation');
  queuedCard.click();
  await until(() => stage === 'brief' && hook.project.id === 'concurrent-4' && !hook.busy, 'reopen queued fourth');
  const queuedDetailReads = requests.filter(item => item.path === '/api/writing/projects/concurrent-4' && item.method === 'GET').length;
  remote.get('concurrent-4').runs[0].status = 'running'; remote.get('concurrent-4').automatic.status = 'running';
  await until(() => hook.project.automatic?.status === 'running', 'queued manuscript status polling');
  assert(requests.filter(item => item.path === '/api/writing/projects/concurrent-4' && item.method === 'GET').length > queuedDetailReads, 'Queued manuscript stopped polling');
  passed('queued card stays stationary and polling follows automatic start without resubmission');
  changeStage('projects');
  await until(() => stage === 'projects' && button('开始新的写作'), 'list after automatic start');
  button('开始新的写作').click();
  await until(() => !hook.project.id && stage === 'brief', 'fifth draft while others active');
  hook.setProject(value => ({ ...value, topic: '未保存的第五篇', title: '未保存的第五篇' }));
  await until(() => hook.dirty, 'fifth unsaved draft');
  assert(!hook.newProject([]), 'New project silently discards an unsaved draft');
  assert(hook.project.topic === '未保存的第五篇', 'Unsaved draft lost after blocked creation');
  await until(() => hook.error.includes('未保存'), 'draft guard message');
  assert(requests.filter(item => item.method === 'POST' && item.path.endsWith('/automatic')).length === 1, 'Queue polling or navigation submitted duplicate automatic work');
  assert(requests.filter(item => item.method !== 'GET').every(item => item.path === '/api/writing/projects' || item.path.startsWith('/api/writing/projects/concurrent-4/')), 'Fixture modified a different running project');
  passed('another new draft remains available during writing and unsaved-draft guards remain intact');
  // Leave a useful stable list for screenshots after reporting all assertions.
  await hook.reload(); changeStage('projects'); await hook.refreshList();
  await hook.selectProject('concurrent-4');
  remote.get('concurrent-4').runs[0].status = 'queued'; remote.get('concurrent-4').automatic.status = 'queued';
  await hook.reload(); await hook.refreshList();
  output.dataset.status = 'passed'; output.textContent = JSON.stringify({ status: 'passed', theme: document.documentElement.dataset.theme, results, requests }, null, 2);
}
window.writingConcurrencyFixture = { get hook() { return hook; }, get requests() { return clone(requests); }, get projects() { return clone([...remote.values()]); }, changeStage: value => changeStage(value) };
run().catch(error => { output.dataset.status = 'failed'; output.textContent = JSON.stringify({ status: 'failed', message: error.message, stack: error.stack, results, requests }, null, 2); });
