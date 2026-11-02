import React, { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingWorkspace from '../src/Writing';
import { useWriting } from '../src/useWriting';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/glass-refinement.css';
import '../src/theme-layout.css';

const options = new URLSearchParams(location.search), output = document.getElementById('writing-projects-result');
const themes = ['jade', 'glass', 'civic'];
document.documentElement.dataset.theme = themes.includes(options.get('theme')) ? options.get('theme') : 'jade';
const root = createRoot(document.getElementById('writing-projects-root'));
const clone = value => structuredClone(value), pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const requests = [], results = [];
let hook, stage, setStage, remote = [], mode = 'success', releaseDelete = null, releaseList = null, holdList = false, lateList;
let manualExportResult = null;
const simulatedExports = [];
const titles = ['从资料出发理解城市更新', '人工智能与公共服务', '区域协作的下一步', '数字治理观察', '知识如何成为可靠的见解', '社区共治的实践路径'];
const fixtureModels = [
  { id: 'cards-configured', name: '研究写作模型', model: 'research-fixture' },
  { id: 'cards-long-model', name: '面向复杂研究材料与跨领域分析的长名称写作模型配置', model: 'long-model-fixture' },
  { id: 'cards-code-only', model: 'model-code-fixture' },
  { id: 'cards-unnamed' },
];
const fixtureModelIds = ['', 'cards-configured', 'cards-long-model', 'cards-deleted', 'cards-code-only', 'cards-unnamed'];
const fixtureStyles = ['清晰克制', '叙事生动', '', '简明直接', '正式严谨', '自然流畅'];
const fixtureModelLabels = ['系统默认', fixtureModels[0].name, fixtureModels[1].name, '暂不可用', fixtureModels[2].model, '未命名'];
const fixturePicture = `data:image/svg+xml;charset=utf-8,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400"><rect width="640" height="400" fill="#e0ece4"/><path d="M0 300 160 130 315 255 450 90 640 290V400H0Z" fill="#658b76"/><circle cx="480" cy="100" r="35" fill="#f1d59c"/></svg>')}`;
const fixtureBody = '先厘清问题，再依据可靠材料提出见解。';
const fixtureFigure = (section, status = 'accepted') => ({ id: `figure-${section.id}-${status}`, assetId: `asset-${section.id}-${status}`, sectionId: section.id, url: fixturePicture, altText: '城市空间与绿色山线的配图样本', caption: '用于卡片展示的已采纳配图', status, reviewed: true, anchor: { paragraphIndex: 0, paragraphExact: section.content, contentHash: 'stored-paragraph-hash' } });
function fixtures() {
  return titles.map((title, index) => {
    const sections = index % 3 === 2 ? [] : [{ id: `section-cards-${index + 1}`, title: '提出问题', goal: '理清问题', mode: 'inherit', targetWords: 900, content: index % 3 === 0 ? fixtureBody : '', version: 1, locked: false, manual: false, evidence: [] }];
    if (index === 0) sections.push({ id: 'section-cards-1-unwritten', title: '梳理做法', goal: '忠实整理材料', mode: 'inherit', targetWords: 900, content: '', version: 1, locked: false, manual: false, evidence: [] });
    return { id: `cards-${index + 1}`, title, version: 3, mode: index % 2 ? 'extended' : 'strict', modelId: fixtureModelIds[index],
    brief: { topic: title, purpose: index ? '比较不同做法，并梳理适用条件与具体行动。' : '以社区案例为线索，分析城市更新中的公共空间、居民参与和服务改善。', targetWords: 1800, workflow: 'chapter', style: fixtureStyles[index], template: '观点文章' },
    sourceIds: [], sources: [], outlineConfirmed: index % 3 === 0, sections,
    illustrations: index === 0 ? [fixtureFigure(sections[0])] : index === 3 ? [fixtureFigure(sections[0], 'stale'), fixtureFigure(sections[0], 'recommended')] : [],
    runs: index === 3 ? [{ id: 'cards-historical-run', status: 'succeeded', model: { name: '历史运行模型不得当作当前配置' }, candidate: { imageSearch: { candidates: [{ ...fixtureFigure(sections[0]), scopeEligible: true, status: 'recommended' }] } } }] : [], createdAt: `2026-10-${17 + index}T02:00:00Z`, updatedAt: `2026-10-23T0${6 - index}:20:00Z` };
  });
}
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
// Every route is intercepted before rendering the hook. There is no real API,
// model, save, export, or document download fallback in this isolated page.
window.fetch = async (input, init = {}) => {
  const url = typeof input === 'string' ? input : input.url, method = init.method || 'GET';
  const request = { url, method, body: init.body ? JSON.parse(init.body) : null }; requests.push(request);
  if (method === 'GET' && url === '/api/writing/projects') {
    const snapshot = clone(remote);
    if (holdList) { holdList = false; lateList = snapshot; await new Promise(resolve => { releaseList = resolve; }); }
    return response({ projects: snapshot });
  }
  const id = url.match(/^\/api\/writing\/projects\/([^/]+)$/)?.[1], project = remote.find(item => item.id === id);
  if (method === 'GET' && id) return project ? response({ project: clone(project) }) : response({ detail: '文稿已删除。' }, 404);
  if (method === 'DELETE' && id) {
    assert(project, 'Deletion targeted an unknown fixture project');
    assert(Object.keys(request.body).join() === 'expectedVersion', 'Deletion sent unexpected data or attempted to save drafts');
    if (mode === 'delayed') await new Promise(resolve => { releaseDelete = resolve; });
    if (mode === 'conflict') return response({ detail: '文稿已有更新，请重新载入后再删除。' }, 409);
    if (mode === 'failure') return response({ detail: '暂时无法删除文稿，请稍后重试。' }, 503);
    if (request.body.expectedVersion !== project.version) return response({ detail: '文稿已有更新，请重新载入后再删除。' }, 409);
    remote = remote.filter(item => item.id !== id);
    return response({ deleted: true, projectId: id, deletedAt: '2026-10-23T06:30:00Z', version: project.version + 1 });
  }
  throw new Error(`Unexpected isolated request: ${method} ${url}`);
};

function Harness() {
  hook = useWriting();
  const workspaceWriting = options.get('manual') === '1' ? { ...hook, exportDocument: async exportOptions => {
    if (!manualExportResult) return hook.exportDocument(exportOptions);
    simulatedExports.push(clone(exportOptions));
    return clone(manualExportResult);
  } } : hook;
  const [view, changeView] = useState('projects'), [toast, setToast] = useState(''); stage = view; setStage = changeView;
  return <div className="projects-fixture-shell workspace-shell">
    <div className="projects-fixture-controls"><strong>隔离验收样本</strong><select aria-label="样本主题" defaultValue={document.documentElement.dataset.theme} onChange={event => { document.documentElement.dataset.theme = event.target.value; }}>{themes.map(theme => <option key={theme}>{theme}</option>)}</select><button type="button" onClick={() => changeView('projects')}>文稿首页</button><button type="button" onClick={() => hook.setProject(value => ({ ...value, purpose: '这份未保存的修改需要保留。' }))}>添加未保存修改</button><span>所有写作操作仅使用内存样本</span></div>
    <div className="projects-fixture-layout"><aside className="projects-fixture-sidebar">文枢<br/><br/>文档库<br/><br/><strong>写作空间</strong><br/><br/>知识图谱<br/><br/>模板库</aside><main className="projects-fixture-main"><WritingWorkspace stage={view} onStageChange={changeView} writing={workspaceWriting} documents={[]} selectedIds={[]} models={fixtureModels} onToast={setToast}/></main></div>
    {toast && <div className="projects-fixture-toast" role="status">{toast}</div>}
  </div>;
}
async function until(predicate, label) {
  const end = performance.now() + 6000;
  while (!predicate()) { if (performance.now() > end) throw new Error(`Timeout: ${label}`); await pause(15); }
}
const buttons = () => [...document.querySelectorAll('button')];
const button = label => buttons().find(item => item.textContent.trim() === label);
const deleteButton = index => document.querySelector(`button[aria-label="删除文稿：${titles[index]}"]`);
const card = index => document.querySelector(`[data-writing-project-id="cards-${index + 1}"]`);
const dialog = () => document.querySelector('[role="dialog"][aria-label="删除文稿"]');
const deletes = () => requests.filter(item => item.method === 'DELETE');
function passed(name) { results.push({ name, status: 'passed' }); output.textContent = JSON.stringify({ status: 'running', results }, null, 2); }
async function load(name, change) {
  remote = fixtures(); mode = 'success'; releaseDelete = null; holdList = false; releaseList = null; hook = null; change?.(remote);
  root.render(<StrictMode><Harness key={name}/></StrictMode>);
  await until(() => hook?.project.id === 'cards-1' && !hook.loading && document.querySelectorAll('.writing-project-card').length === 6, name);
  await pause(20); requests.length = 0;
}
async function showDelete(index) {
  const previousStage = stage, previousProject = hook.project.id, previousError = hook.error;
  const reads = requests.filter(request => request.method === 'GET' && /^\/api\/writing\/projects\/[^/]+$/.test(request.url)).length;
  deleteButton(index).focus(); deleteButton(index).click(); await until(dialog, 'delete dialog'); await pause(20);
  assert(stage === previousStage && hook.project.id === previousProject && hook.error === previousError, 'The delete button bubbled into manuscript entry');
  assert(requests.filter(request => request.method === 'GET' && /^\/api\/writing\/projects\/[^/]+$/.test(request.url)).length === reads, 'The delete button fetched a manuscript for entry');
}
async function dirtyCurrent() { hook.setProject(value => ({ ...value, purpose: '需要保留的未保存人工修改。' })); await until(() => hook.dirty, 'dirty current draft'); }

async function run() {
  await load('initial');
  if (options.get('manual') === '1') { output.dataset.status = 'manual'; output.textContent = 'MANUAL · 内存样本，可检查三套主题、搜索、编辑和删除确认。'; return; }
  for (let index = 0; index < titles.length; index += 1) {
    const tags = card(index).querySelector('.writing-project-tags');
    const modeTag = tags?.querySelector('.writing-project-mode'), styleTag = tags?.querySelector('.writing-project-style'), modelTag = tags?.querySelector('.writing-project-model');
    assert(modeTag?.textContent === (index % 2 ? '拓展' : '严谨'), `Card ${index + 1} did not show the concise writing mode`);
    const compact = value => Array.from(value).length > 8 ? `${Array.from(value).slice(0, 7).join('')}…` : value;
    assert(styleTag?.textContent === compact(fixtureStyles[index] || '未设置'), `Card ${index + 1} did not show only its saved writing style`);
    assert(modelTag?.textContent === compact(fixtureModelLabels[index]), `Card ${index + 1} did not resolve its concise current model selection`);
    for (const [tag, category, full] of [[modeTag, '写作方式', index % 2 ? '拓展' : '严谨'], [styleTag, '文风', fixtureStyles[index] || '未设置'], [modelTag, '写作模型', fixtureModelLabels[index]]]) {
      assert(Array.from(tag.textContent).length <= 8 && !/^(?:写作方式|模式|风格|文风|模型|写作模型)\s*[：·]/.test(tag.textContent), `Card ${index + 1} retained a category prefix or exceeded eight visible Unicode characters`);
      assert(tag.title === `${category}：${full}` && tag.getAttribute('aria-label') === `${category}：${full}`, `Card ${index + 1} lost its full categorized hover or accessible tag label`);
    }
    assert(!tags.textContent.includes('cards-deleted') && !tags.textContent.includes('历史运行模型不得当作当前配置'), `Card ${index + 1} leaked an identity or borrowed a historical model`);
  }
  assert(requests.length === 0 && !hook.dirty, 'Rendering metadata read extra services or changed the saved manuscript');
  passed('concise tags retain accurate saved settings, fit eight Unicode characters, and expose their category and full value on hover and to assistive technology');

  for (let index = 0; index < titles.length; index += 1) {
    const sections = remote[index].sections, completed = sections.filter(section => section.content.trim()).length;
    const progress = card(index).querySelector('.writing-project-body-progress progress');
    const expectedPercent = sections.length && remote[index].outlineConfirmed ? 20 + completed / sections.length * 40 : 10;
    assert(progress && progress.value === expectedPercent && progress.max === 100, `Card ${index + 1} did not use the saved body chapters and shared forty-percent body weight`);
    const spectrum = card(index).querySelector('.writing-project-progress-spectrum');
    assert(spectrum?.style.gridTemplateColumns === '10fr 10fr 40fr 20fr 20fr', `Card ${index + 1} visual widths diverged from the five-stage weights`);
    assert(progress.getAttribute('aria-label') === '写作进度' && progress.getAttribute('aria-valuetext')?.includes(sections.length ? `${completed} / ${sections.length}` : '尚未拟定纲要'), `Card ${index + 1} lost a meaningful accessible writing and body progress description`);
    const visibleProgress = card(index).querySelector('.writing-project-body-progress').textContent;
    assert(!/(?:写作进度|正文进度|\d+\s*\/\s*\d+\s*章)/.test(visibleProgress), `Card ${index + 1} retained a progress heading or repeated chapter count beside its bar`);
    const chapterMeta = card(index).querySelector('.writing-project-meta').firstElementChild;
    assert(chapterMeta.textContent.replace(/\s/g, '') === `${completed}/${sections.length}个章节`, `Card ${index + 1} did not place its real completed and total chapter counts first in the metadata`);
  }
  assert(card(0).querySelector('.writing-project-meta').firstElementChild.textContent.replace(/\s/g, '') === '1/2个章节' && card(0).querySelector('progress').value === 40 && card(0).querySelector('progress').position === 0.4, 'The half-written two-chapter manuscript does not keep its exact chapter metadata and weighted progress');
  assert(card(2).querySelector('.writing-project-body-progress').textContent.includes('待拟纲要') && card(2).querySelector('progress').value === 10, 'A saved manuscript without an outline did not retain only preparation progress');
  const image = card(0).querySelector('.writing-project-thumbnail img');
  assert(image?.getAttribute('src') === fixturePicture && image.getAttribute('loading') === 'lazy' && image.getAttribute('decoding') === 'async' && image.alt === '城市空间与绿色山线的配图样本', 'The adopted illustration is missing, eagerly loaded or lacks its original alternative text');
  assert(!card(3).querySelector('.writing-project-thumbnail img') && [...document.querySelectorAll('.writing-project-thumbnail img')].length === 1, 'Stale illustrations or image-search candidates were displayed as manuscript pictures');
  const selectedBeforeImageError = hook.project.id;
  image.dispatchEvent(new Event('error'));
  await until(() => !card(0).querySelector('.writing-project-thumbnail img'), 'broken picture falls back to a text card');
  assert(hook.project.id === selectedBeforeImageError && stage === 'projects' && requests.length === 0 && card(0).getAttribute('role') === 'button' && card(0).tabIndex === 0, 'A failed picture entered, mutated or disabled its manuscript');
  card(0).querySelector('.writing-project-info>p').click();
  await until(() => hook.project.id === 'cards-1' && stage === 'editor', 'a card remains openable after its image fails');
  assert(requests.every(request => request.method === 'GET'), 'Opening a card with a failed image issued a mutation');
  passed('body progress reflects persisted chapters, adopted pictures load lazily, stale candidates stay hidden and picture failure preserves card entry');
  await load('card appearance and metadata');

  await dirtyCurrent();
  const cardMetadataGeometry = [];
  for (const theme of themes) {
    document.documentElement.dataset.theme = theme; await pause(40);
    for (const width of [null, 280]) {
      const nodes = [...document.querySelectorAll('.writing-project-card')];
      if (width) nodes.forEach(node => { node.style.width = `${width}px`; });
      await pause(35);
      for (let index = 0; index < nodes.length; index += 1) {
        const node = nodes[index], tags = node.querySelector('.writing-project-tags'), times = node.querySelector('.writing-project-times');
        const titleStyle = getComputedStyle(node.querySelector('h2'));
        assert(parseFloat(titleStyle.fontSize) >= 24 && parseFloat(titleStyle.fontWeight) >= 600, `${theme} title is not visibly larger and stronger`);
        const created = times.querySelector('.writing-project-created'), modified = times.querySelector('.writing-project-modified');
        const footerRect = node.querySelector('.writing-project-card-footer').getBoundingClientRect(), rect = times.getBoundingClientRect();
        const createdRect = created.getBoundingClientRect(), modifiedRect = modified.getBoundingClientRect();
        assert(times.firstElementChild === created && created.nextElementSibling === modified, `${theme} creation and modification are not in reading order`);
        assert(Math.abs(rect.left - footerRect.left) <= 1 && Math.abs(rect.right - footerRect.right) <= 1, `${theme} times do not fill their footer`);
        assert(Math.abs(createdRect.left - rect.left) <= 1 && Math.abs(modifiedRect.right - rect.right) <= 1, `${theme} creation and modification cells are not at opposite edges`);
        assert(getComputedStyle(created).textAlign === 'left' && getComputedStyle(modified).textAlign === 'right', `${theme} dates lost left/right text alignment`);
        const createdParts = [...created.children].map(part => part.getBoundingClientRect()), modifiedParts = [...modified.children].map(part => part.getBoundingClientRect());
        assert(createdParts.length === 2 && modifiedParts.length === 2, `${theme} date labels and values cannot wrap independently`);
        assert(Math.abs(Math.min(...createdParts.map(part => part.left)) - rect.left) <= 1 && Math.abs(Math.max(...modifiedParts.map(part => part.right)) - rect.right) <= 1, `${theme} visible date content is not flush with both edges`);
        assert(times.scrollWidth <= times.clientWidth + 1 && tags.scrollWidth <= tags.clientWidth + 1 && node.scrollWidth <= node.clientWidth + 1, `${theme} metadata overflowed a ${width || 'responsive'}px card`);
        assert(created.lastElementChild.getClientRects().length === 1 && modified.lastElementChild.getClientRects().length === 1, `${theme} a timestamp broke across lines instead of moving as a whole`);
        cardMetadataGeometry.push({ theme, requestedWidth: width, cardWidth: node.getBoundingClientRect().width, createdLeft: createdRect.left - rect.left, modifiedRight: rect.right - modifiedRect.right, tagHeight: tags.getBoundingClientRect().height });
      }
      nodes.forEach(node => { node.style.removeProperty('width'); }); await pause(35);
    }
  }
  assert(hook.project.id === 'cards-1' && hook.dirty && hook.project.purpose === '需要保留的未保存人工修改。' && requests.length === 0, 'Metadata wrapping, theme or card resizing changed the draft, current selection or API state');
  passed('three themes keep creation left and modification right at responsive and 280px card widths without clipping tags or changing the draft');

  await load('missing historical times', rows => { rows[0].createdAt = ''; rows[0].updatedAt = 'invalid-time'; });
  assert(card(0).querySelector('.writing-project-created').textContent.includes('未记录') && card(0).querySelector('.writing-project-modified').textContent.includes('未记录'), 'Missing creation or modification time borrowed another timestamp');
  assert(requests.length === 0 && !hook.dirty, 'Displaying missing time changed a manuscript or read another service');
  passed('missing historical creation and modification timestamps stay visibly unrecorded');
  await load('existing manuscript interactions');
  assert(!document.querySelector('.writing-project-continue') && !button('继续写作'), 'Manuscript cards retained a redundant continue button');
  assert(card(0).getAttribute('role') === 'button' && card(0).tabIndex === 0, 'The manuscript card is not keyboard accessible');
  await showDelete(0); assert(dialog().textContent.includes(titles[0]), 'Confirmation omitted the exact manuscript title'); button('暂不删除').click(); await until(() => !dialog(), 'cancel closed dialog');
  assert(deletes().length === 0 && hook.projects.length === 6, 'Cancel deleted a manuscript'); passed('the delete button opens only confirmation, and cancellation does not enter or mutate a manuscript');

  await showDelete(0); document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await until(() => !dialog(), 'Escape dismissed dialog');
  assert(document.activeElement === deleteButton(0) && deletes().length === 0, 'Escape did not restore focus safely'); passed('Escape cancels and restores the card delete button focus');

  await dirtyCurrent(); await showDelete(0); assert(dialog().textContent.includes('未保存修改'), 'Unsaved deletion consequence is missing'); button('暂不删除').click(); await until(() => !dialog(), 'cancel dirty delete');
  assert(hook.dirty && hook.project.purpose === '需要保留的未保存人工修改。', 'Cancel lost the dirty draft');
  await showDelete(0); button('确认删除').click(); await until(() => hook.projects.length === 5 && !dialog(), 'current manuscript deleted');
  assert(hook.project.id === null && !hook.dirty && !deleteButton(0), 'Confirmed current delete did not remove the selected manuscript');
  assert(deletes().length === 1 && deletes()[0].body.expectedVersion === 3, 'Current deletion did not use one versioned request'); passed('dirty current deletion has an explicit warning and one versioned request');

  await load('delete another dirty draft'); await dirtyCurrent(); await showDelete(1); button('确认删除').click(); await until(() => hook.projects.length === 5 && !dialog(), 'other manuscript deleted');
  assert(hook.project.id === 'cards-1' && hook.dirty && hook.project.purpose === '需要保留的未保存人工修改。', 'Deleting another manuscript lost the current dirty draft'); passed('deleting another manuscript preserves the current unsaved draft');

  for (const failure of ['conflict', 'failure']) {
    await load(failure); await dirtyCurrent(); mode = failure; await showDelete(1); button('确认删除').click(); await until(() => hook.deleteProjectError && !hook.deletingProjectId, failure);
    assert(hook.projects.length === 6 && deleteButton(1) && hook.dirty && hook.project.purpose === '需要保留的未保存人工修改。', `${failure} removed a card or draft`);
    assert(dialog().querySelector('[role="alert"]') && !button('确认删除').disabled, `${failure} did not keep a readable retryable confirmation`); passed(`${failure} preserves all cards and unsaved text`);
  }

  await load('pending duplicates and switching'); mode = 'delayed'; await showDelete(0); button('确认删除').click(); await until(() => releaseDelete && hook.deletingProjectId, 'pending delete');
  assert(button('正在删除…')?.disabled && button('暂不删除').disabled, 'Pending deletion permits duplicate confirmation');
  assert(await hook.deleteProject('cards-1', 3) === null && deletes().length === 1, 'Duplicate pending deletion sent another request');
  await hook.selectProject('cards-2'); await until(() => hook.project.id === 'cards-2', 'switch project while deleting'); await dirtyCurrent();
  releaseDelete(); await until(() => !hook.deletingProjectId && hook.projects.length === 5, 'late delete returned');
  assert(hook.project.id === 'cards-2' && hook.dirty && hook.project.purpose === '需要保留的未保存人工修改。', 'Late deletion cleared a switched project draft'); passed('pending deletion blocks duplicates and preserves the newly opened draft');

  await load('pending delete and new writing'); mode = 'delayed'; const deleting = hook.deleteProject('cards-1', 3); await until(() => releaseDelete, 'pending current delete');
  assert(hook.newProject([]), 'Could not start another clean manuscript during deletion'); await until(() => !hook.project.id, 'new project'); await dirtyCurrent();
  releaseDelete(); await deleting; assert(hook.project.id === null && hook.dirty && hook.project.purpose === '需要保留的未保存人工修改。', 'Late deletion cleared a new unsaved manuscript'); passed('late deletion preserves a new unsaved manuscript');

  await load('late list'); holdList = true; const listing = hook.refreshList(); await until(() => releaseList, 'captured stale list');
  assert(lateList.length === 6, 'Late-list case did not capture the pre-deletion list'); await hook.deleteProject('cards-1', 3); releaseList(); await listing; await pause(20);
  assert(hook.projects.length === 5 && !hook.projects.some(item => item.id === 'cards-1'), 'A late list resurrected a deleted manuscript'); passed('late list reads cannot reintroduce deleted cards');

  await load('active writing tasks', rows => { rows[0].runs = [{ id: 'cards-running', jobId: 'cards-job', kind: 'document', status: 'running' }]; });
  await showDelete(0); assert(dialog().textContent.includes('请先取消任务') && button('确认删除').disabled, 'Active tasks did not block deletion clearly');
  assert(await hook.deleteProject('cards-1', 3) === null && deletes().length === 0, 'Active project deletion reached the API'); passed('active tasks require cancellation or completion before deletion');

  await load('sorting preserves drafts'); await dirtyCurrent();
  const order = document.querySelector('select[aria-label="文稿排序"]');
  const setOrder = value => { order.value = value; order.dispatchEvent(new Event('change', { bubbles: true })); };
  const visibleTitles = () => [...document.querySelectorAll('.writing-project-info h2')].map(item => item.textContent);
  assert([...order.options].map(option => option.textContent).join('|') === '最近修改|最近创建|标题', 'Sorting did not use the concise requested labels');
  assert(visibleTitles()[0] === titles[0] && order.value === 'updatedAt', 'Default order did not retain latest modification first');
  const compactTimes = document.querySelector('.writing-project-times').textContent.replace(/\s/g, '');
  assert(compactTimes.includes('创建2026-10-1710:00') && compactTimes.includes('修改2026-10-2314:20'), 'Card omitted distinct Beijing creation and modification times');
  setOrder('createdAt'); await until(() => visibleTitles()[0] === titles[5], 'creation sort');
  setOrder('title'); await until(() => JSON.stringify(visibleTitles()) === JSON.stringify([titles[0], titles[2], titles[1], titles[5], titles[3], titles[4]]), 'Chinese title sort');
  setOrder('updatedAt'); await until(() => visibleTitles()[0] === titles[0], 'modification sort');
  assert(hook.project.id === 'cards-1' && hook.dirty && hook.project.purpose === '需要保留的未保存人工修改。' && deletes().length === 0, 'Sorting changed a draft or issued a mutation'); passed('creation, modification and Chinese title sorting preserve the unsaved draft');

  await load('keyboard card entry');
  card(2).focus(); const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }); card(2).dispatchEvent(enter);
  await until(() => hook.project.id === 'cards-3' && stage === 'brief', 'Enter opens the saved preparation workflow');
  assert(enter.defaultPrevented, 'Enter did not prevent duplicate native activation');
  setStage('projects'); await until(() => !!card(3), 'back to cards for Space');
  card(3).focus(); const space = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true }); card(3).dispatchEvent(space);
  await until(() => hook.project.id === 'cards-4' && stage === 'editor', 'Space opens the saved body workflow');
  assert(space.defaultPrevented && requests.every(request => request.method === 'GET'), 'Space scrolled the page or keyboard entry issued a mutation');
  passed('Enter and Space activate the card and open the correct saved preparation or body workflow');

  await load('dirty draft blocks card entry'); await dirtyCurrent();
  card(1).querySelector('.writing-project-info>p').click();
  await until(() => hook.error?.includes('未保存修改') && !hook.busy, 'click preserves the dirty current manuscript');
  assert(stage === 'projects' && hook.project.id === 'cards-1' && hook.dirty && hook.project.purpose === '需要保留的未保存人工修改。', 'Card click discarded or replaced an unsaved draft');
  hook.clearError(); await until(() => !hook.error, 'clear the first protected entry message');
  card(2).focus(); card(2).dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  await until(() => hook.error?.includes('未保存修改') && !hook.busy, 'keyboard preserves the dirty current manuscript');
  assert(stage === 'projects' && hook.project.id === 'cards-1' && hook.dirty && hook.project.purpose === '需要保留的未保存人工修改。' && requests.length === 0, 'Protected card entry fetched, saved or discarded the current draft');
  passed('pointer and keyboard card entry preserve unsaved text through the existing project-open guard');

  await load('search and card entry'); const search = document.querySelector('input[aria-label="搜索文稿"]');
  const setSearch = value => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(search, value); search.dispatchEvent(new Event('input', { bubbles: true })); };
  setSearch('社区案例'); await until(() => document.querySelectorAll('.writing-project-card').length === 1, 'purpose search'); setSearch('不会匹配的词'); await until(() => document.querySelectorAll('.writing-project-card').length === 0, 'empty search');
  assert(document.querySelector('.writing-projects').textContent.includes('没有找到匹配的文稿'), 'Empty search has no helpful state'); setSearch(''); await until(() => document.querySelectorAll('.writing-project-card').length === 6, 'clear search');
  card(1).querySelector('.writing-project-info>p').click(); await until(() => hook.project.id === 'cards-2' && stage === 'outline', 'non-button card area opens the correct workflow');
  assert(requests.every(request => request.method === 'GET'), 'Clicking a manuscript card issued a mutation'); passed('search includes summaries, empty results remain clear, and clicking non-button card content opens the correct saved outline workflow');
  setStage('projects'); await until(() => !!document.querySelector('.writing-project-grid'), 'back to cards');
  const themeGeometry = [];
  for (const theme of themes) {
    document.documentElement.dataset.theme = theme; await pause(30);
    const grid = document.querySelector('.writing-project-grid'), style = getComputedStyle(grid);
    assert(style.display === 'grid' && style.gap === '22px', `${theme} lost grid spacing`);
    const sortStyle = getComputedStyle(document.querySelector('select[aria-label="文稿排序"]'));
    assert(sortStyle.backgroundColor === 'rgba(0, 0, 0, 0)' && ['borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth'].every(side => sortStyle[side] === '0px') && sortStyle.boxShadow === 'none', `${theme} retained a framed white sort control`);
    assert(document.documentElement.scrollWidth <= innerWidth + 1, `${theme} overflowed the viewport`);
    themeGeometry.push({ theme, gap: style.gap, columns: style.gridTemplateColumns, width: grid.getBoundingClientRect().width });
  }
  passed('all three themes retain 22px grid gaps and an unframed transparent sort control without page overflow');

  await dirtyCurrent();
  const gallery = () => document.querySelector('.writing-project-gallery'), viewport = () => document.querySelector('.writing-project-viewport');
  const heading = () => document.querySelector('.writing-page-heading'), outer = document.querySelector('.projects-fixture-main');
  const dotNav = () => document.querySelector('nav.doc-dot-nav-overlay[aria-label="文稿卡片定位"]:not([hidden])');
  const headerProgress = () => Number.parseFloat(document.querySelector('.writing-projects').style.getPropertyValue('--gallery-header-progress')) || 0;
  const galleryGeometry = [], mutationsBeforeNavigation = requests.filter(request => request.method !== 'GET').length;
  for (const theme of themes) {
    document.documentElement.dataset.theme = theme;
    viewport().scrollTop = 0; viewport().dispatchEvent(new Event('scroll'));
    await until(() => headerProgress() <= 0.001 && !gallery().classList.contains('is-header-compact'), `${theme} full heading settled`);
    const title = heading().querySelector('h1'), subtitle = heading().querySelector('p');
    const expandedSize = parseFloat(getComputedStyle(title).fontSize), expandedSubtitleHeight = subtitle.getBoundingClientRect().height, outerTop = outer.scrollTop;
    assert(getComputedStyle(viewport()).scrollbarWidth === 'none', `${theme} left a native manuscript scrollbar`);
    await until(() => dotNav()?.querySelectorAll('.doc-dot-nav-button').length === 6, `${theme} one visible portal dot per manuscript`);
    assert(viewport().scrollHeight > viewport().clientHeight + 180, `${theme} fixture cannot exercise progressive scrolling`);
    const samples = [];
    const sample = async (scrollTop, direction) => {
      const progress = Math.min(1, scrollTop / 144), expectedSize = expandedSize - (expandedSize - 26) * progress;
      viewport().scrollTop = scrollTop; viewport().dispatchEvent(new Event('scroll'));
      await until(() => Math.abs(headerProgress() - progress) <= 0.001
        && Math.abs(parseFloat(getComputedStyle(title).fontSize) - expectedSize) <= 0.15
        && gallery().classList.contains('is-header-compact') === (scrollTop >= 144), `${theme} ${direction} ${scrollTop}px header animation settled`);
      const fontSize = parseFloat(getComputedStyle(title).fontSize), subtitleStyle = getComputedStyle(subtitle);
      const subtitleOpacity = parseFloat(subtitleStyle.opacity), subtitleHeight = subtitle.getBoundingClientRect().height;
      const dots = dotNav()?.querySelector('.doc-dot-nav-grid')?.getBoundingClientRect();
      assert(Math.abs(viewport().scrollTop - scrollTop) <= 0.5 && outer.scrollTop === outerTop, `${theme} ${direction} ${scrollTop}px lost the card scroll position or moved the page`);
      assert(Math.abs(fontSize - expectedSize) <= 0.2, `${theme} ${direction} ${scrollTop}px title jumped instead of following the actual scroll (${fontSize}px versus ${expectedSize}px)`);
      assert(subtitleStyle.display !== 'none' && Math.abs(subtitleOpacity - (1 - progress)) <= 0.03, `${theme} ${direction} ${scrollTop}px subtitle did not fade continuously`);
      assert(gallery().classList.contains('is-header-compact') === (scrollTop >= 144), `${theme} ${direction} ${scrollTop}px marked the header compact before its endpoint`);
      assert(dots && Math.abs((dots.top + dots.bottom) / 2 - innerHeight / 2) <= 0.5, `${theme} ${direction} ${scrollTop}px portal dot map moved away from the fixed screen midline`);
      assert(heading().getBoundingClientRect().bottom <= document.querySelector('.writing-list-top').getBoundingClientRect().top + 1, `${theme} ${direction} ${scrollTop}px header overlapped its controls`);
      const result = { scrollTop: viewport().scrollTop, direction, fontSize, subtitleOpacity, subtitleHeight, screenDotCenter: (dots.top + dots.bottom) / 2 };
      samples.push(result); return result;
    };
    const forward = [];
    for (const scrollTop of [0, 24, 48, 72, 96, 144]) forward.push(await sample(scrollTop, 'down'));
    for (let index = 1; index < forward.length; index += 1) {
      assert(forward[index].fontSize < forward[index - 1].fontSize && forward[index].subtitleOpacity < forward[index - 1].subtitleOpacity && forward[index].subtitleHeight <= forward[index - 1].subtitleHeight + 0.5, `${theme} title and subtitle failed to shrink monotonically`);
    }
    assert(Math.abs(forward.at(-1).fontSize - 26) <= 0.1 && forward.at(-1).subtitleHeight <= 0.5 && Math.abs(forward[0].subtitleHeight - expandedSubtitleHeight) <= 0.5, `${theme} progressive contraction did not reach its full and compact endpoints`);
    const compactTop = heading().getBoundingClientRect().top; await sample(180, 'hold');
    assert(Math.abs(heading().getBoundingClientRect().top - compactTop) <= 0.5, `${theme} fully compact header moved during further card scrolling`);
    const reverse = [];
    for (const scrollTop of [96, 72, 48, 24, 0]) reverse.push(await sample(scrollTop, 'up'));
    for (let index = 1; index < reverse.length; index += 1) {
      assert(reverse[index].fontSize > reverse[index - 1].fontSize && reverse[index].subtitleOpacity > reverse[index - 1].subtitleOpacity && reverse[index].subtitleHeight + 0.5 >= reverse[index - 1].subtitleHeight, `${theme} scrolling back did not restore the header monotonically`);
    }
    for (const current of reverse) {
      const original = forward.find(item => item.scrollTop === current.scrollTop);
      assert(Math.abs(current.fontSize - original.fontSize) <= 0.2 && Math.abs(current.subtitleOpacity - original.subtitleOpacity) <= 0.03 && Math.abs(current.subtitleHeight - original.subtitleHeight) <= 0.5, `${theme} reverse scrolling did not restore the same intermediate geometry`);
    }
    galleryGeometry.push({ theme, expandedSize, compactSize: forward.at(-1).fontSize, columns: getComputedStyle(document.querySelector('.writing-project-grid')).gridTemplateColumns, screenDotCenter: forward.at(-1).screenDotCenter, samples });
  }
  passed('three themes progressively contract and restore the fixed header at real scroll samples while the portal dot map stays on the screen midline');

  const orderAfterScroll = document.querySelector('select[aria-label="文稿排序"]'); orderAfterScroll.value = 'createdAt'; orderAfterScroll.dispatchEvent(new Event('change', { bubbles: true }));
  await until(() => document.querySelector('.writing-project-info h2').textContent === titles[5], 'sort before dot navigation');
  const cardIds = () => [...document.querySelectorAll('.writing-project-card')].map(card => card.dataset.writingProjectId);
  const dotsAfterSort = [...dotNav().querySelectorAll('.doc-dot-nav-button')];
  assert(dotsAfterSort.map(dot => dot.getAttribute('aria-label')).join('|') === [...document.querySelectorAll('.writing-project-info h2')].map(title => `定位文稿：${title.textContent}`).join('|'), 'Dot order did not follow sorted cards');
  const targetId = cardIds().at(-1), target = document.querySelector(`[data-writing-project-id="${targetId}"]`);
  dotsAfterSort.at(-1).dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
  await until(() => { const card = target.getBoundingClientRect(), pane = viewport().getBoundingClientRect(); const visibleHeight = Math.max(0, Math.min(card.bottom, pane.bottom) - Math.max(card.top, pane.top)); return viewport().scrollTop > 0 && visibleHeight / Math.min(card.height, pane.height) >= 0.8; }, 'sorted dot locates the matching manuscript');
  assert(stage === 'projects' && hook.project.id === 'cards-2' && hook.dirty && hook.project.purpose === '需要保留的未保存人工修改。', 'Dot navigation opened another manuscript or changed the unsaved draft');
  dotsAfterSort[0].focus(); dotsAfterSort[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true })); await until(() => document.activeElement === dotsAfterSort.at(-1), 'keyboard End selects last dot');
  dotsAfterSort.at(-1).click(); await until(() => document.activeElement === target, 'keyboard locate transfers focus to its manuscript card');
  assert(requests.filter(request => request.method !== 'GET').length === mutationsBeforeNavigation, 'Scrolling or dot navigation issued a mutation');
  passed('sorted dot navigation and keyboard positioning locate the correct card without opening a manuscript, saving a draft or mutating data');
  output.dataset.status = 'passed'; output.textContent = JSON.stringify({ status: 'passed', results, cardMetadataGeometry, themeGeometry, galleryGeometry, realServiceRequests: 0 }, null, 2);
}
window.__writingProjectsFixture = { get hook() { return hook; }, get requests() { return requests; }, get results() { return results; },
  controls: options.get('manual') === '1' ? { updateProject(id, patch, refresh = true) { Object.assign(remote.find(item => item.id === id), clone(patch)); return refresh ? hook.refreshList() : undefined; }, simulateExport(result) { manualExportResult = clone(result); }, get simulatedExports() { return simulatedExports; } } : undefined,
};
run().catch(error => { output.dataset.status = 'failed'; output.textContent = JSON.stringify({ status: 'failed', error: error.stack || error.message, results, requests }, null, 2); console.error(error); });
