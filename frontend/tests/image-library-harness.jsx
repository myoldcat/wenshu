import React, { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ImageLibrary } from '../src/ImageLibrary';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/glass-refinement.css';
import '../src/theme-layout.css';

const options = new URLSearchParams(location.search), output = document.getElementById('image-library-result');
const themes = ['jade', 'glass', 'civic'];
document.documentElement.dataset.theme = themes.includes(options.get('theme')) ? options.get('theme') : 'jade';
const requests = [], opened = [], results = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const svg = (index, portrait) => `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${portrait ? 180 : 640}" height="${portrait ? 520 : 380}" viewBox="0 0 ${portrait ? 180 : 640} ${portrait ? 520 : 380}"><rect width="100%" height="100%" fill="${['#dbe9dd', '#dce9f1', '#f1e6d4'][index % 3]}"/><circle cx="${portrait ? 90 : 450}" cy="130" r="55" fill="#e3c894"/><path d="M0 ${portrait ? 320 : 270} Q100 180 220 280 T640 220 V520 H0Z" fill="#86a79b"/><text x="24" y="44" fill="#304e47" font-size="23">Image ${index + 1}</text></svg>`)}`;
const fixture = Array.from({ length: 65 }, (_, index) => ({ id: `image-${index}`, url: index === 5 ? 'data:image/png;base64,invalid' : svg(index, index === 2), alt: index === 0 ? '城市与自然的相遇 <img src=x onerror=alert(1)>' : index === 4 ? '' : `样本图片 ${index + 1}：${index % 3 === 0 ? '城市公共空间的细节' : '一处需要放大查看的图示'}`, mime: 'image/svg+xml', sourceId: `article-${index % 3}`, revisionId: `revision-${index % 3}`, documentTitle: ['城市更新与公共生活', '知识如何连接日常工作', '一份保留完整比例的长图'][index % 3], originalTitle: `Original title ${index % 3}`, source: ['样本观察', '工作札记', '图像记录'][index % 3], date: '2026-10-23', ordinal: index + 1 }));
let controls, releaseSlow, failRead = true;
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
// Reject every unexpected request: this fixture never reaches the real service,
// runs a model, modifies a document, or persists anything.
window.fetch = async (input, init = {}) => {
  const url = new URL(typeof input === 'string' ? input : input.url, location.origin), method = init.method || 'GET';
  requests.push({ url: url.pathname + url.search, method, signal: init.signal });
  assert(method === 'GET' && url.pathname === '/api/images', `Unexpected request ${method} ${url.pathname}`);
  const q = url.searchParams.get('q') || '', limit = Number(url.searchParams.get('limit')), offset = Number(url.searchParams.get('offset'));
  assert(limit === 60, 'Unexpected page size');
  if (q === 'slow') await new Promise(resolve => { releaseSlow = resolve; }); // Intentionally ignore abort to prove the race fence.
  if (q === 'error' && failRead) return response({ detail: '样本读取失败，请重试。' }, 503);
  const matches = q === 'slow' || q === 'fast' ? [{ ...fixture[0], documentTitle: q }] : q === 'error' ? [fixture[0]] : fixture.filter(item => !q || `${item.documentTitle} ${item.alt}`.includes(q));
  return response({ items: matches.slice(offset, offset + limit), total: matches.length, documentCount: new Set(matches.map(item => item.sourceId)).size, limit, offset });
};

function Harness() {
  const [active, setActive] = useState(true), [revision, setRevision] = useState(1);
  controls = { setActive, setRevision };
  return <div className="workspace-shell image-fixture-shell"><div className="image-fixture-controls"><strong>图片库隔离样本</strong><select aria-label="样本主题" defaultValue={document.documentElement.dataset.theme} onChange={event => { document.documentElement.dataset.theme = event.target.value; }}>{themes.map(theme => <option key={theme}>{theme}</option>)}</select><button onClick={() => setActive(value => !value)}>切换页面可见性</button><button onClick={() => setRevision(value => value + 1)}>模拟资料更新</button><span>65 张图片，3 篇文章；仅使用只读内存接口</span></div><div className="image-fixture-layout"><aside className="image-fixture-sidebar">文枢<br/><br/>文档库<br/><br/><strong>图片库</strong><br/><br/>知识图谱<br/><br/>写作空间</aside><main className="image-fixture-main app-main view-images"><div className="workspace-view" hidden={!active}><ImageLibrary active={active} revision={revision} onOpenDoc={item => opened.push(item)} /></div></main></div></div>;
}
createRoot(document.getElementById('image-library-root')).render(<StrictMode><Harness /></StrictMode>);
async function until(predicate, label) { const end = performance.now() + 7000; while (!predicate()) { if (performance.now() > end) throw new Error(`Timeout: ${label}`); await pause(15); } }
const cards = () => [...document.querySelectorAll('.image-library-card')];
const button = label => [...document.querySelectorAll('button')].find(item => item.textContent.trim() === label);
const dialog = () => document.querySelector('dialog[open]');
function search(value) { const input = document.querySelector('.image-library-search input'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); }
function passed(name) { results.push({ name, status: 'passed' }); output.textContent = JSON.stringify({ status: 'running', results }); }
async function run() {
  await until(() => cards().length === 60, 'initial images');
  if (options.get('manual') === '1') { output.dataset.status = 'manual'; output.textContent = 'MANUAL · 65 张只读内存图片，可检查三套主题、长图、搜索、分页和键盘预览。'; window.__imageLibraryFixture = { requests, opened, controls }; return; }
  assert(document.querySelector('.image-library-count').textContent.includes('65') && document.querySelector('.image-library-count').textContent.includes('3 篇'), 'Initial counts are incorrect');
  assert(cards()[0].textContent.includes('<img src=x') && !cards()[0].querySelector('img[src="x"]'), 'Image descriptions were interpreted as markup');
  assert([...document.querySelectorAll('.image-library-picture img')].every(image => image.loading === 'lazy' && getComputedStyle(image).objectFit === 'contain'), 'Images are not lazy loaded in full');
  await until(() => document.querySelector('.image-library-broken'), 'broken-image feedback');
  passed('current-version images, totals, escaped captions, lazy loading and full-aspect images');
  button('加载更多图片').click(); await until(() => cards().length === 65, 'pagination');
  assert(requests.some(request => new URL(request.url, location.origin).searchParams.get('offset') === '60'), 'Pagination did not advance the server offset');
  assert(!button('加载更多图片'), 'Completed pagination retained a load action'); passed('pagination appends the next page once and terminates at the total');
  const trigger = cards()[0].querySelector('.image-library-picture'); trigger.focus(); trigger.click(); await until(dialog, 'preview');
  assert(document.activeElement?.getAttribute('aria-label') === '关闭图片预览', 'Preview did not receive keyboard focus');
  assert(getComputedStyle(dialog().querySelector('img')).objectFit === 'contain', 'Preview crops the source image');
  dialog().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })); await until(() => dialog()?.querySelector('header p').textContent.startsWith('2 /'), 'next preview');
  dialog().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true })); await until(() => dialog()?.querySelector('header p').textContent.startsWith('1 /'), 'previous preview');
  dialog().dispatchEvent(new Event('cancel', { cancelable: true })); await until(() => !dialog(), 'preview dismissal');
  assert(document.activeElement === trigger, 'Preview did not restore trigger focus');
  cards()[0].querySelector('.image-library-card-bottom button').click(); assert(opened[0]?.id === 'article-0' && opened[0]?.revisionId === 'revision-0', 'Article navigation lost the exact revision'); passed('modal keyboard navigation, dismissal, focus return and exact source revision');
  search('不存在'); await until(() => document.querySelector('.image-library-empty h2')?.textContent === '没有找到匹配的图片', 'search empty');
  button('清除搜索').click(); await until(() => cards().length === 60, 'clear search'); passed('search empty state and return to all images');
  search('slow'); await until(() => releaseSlow, 'held old search'); search('fast'); await until(() => cards()[0]?.querySelector('h2').textContent === 'fast', 'latest search'); releaseSlow(); await pause(60);
  assert(cards()[0].querySelector('h2').textContent === 'fast', 'A late old response replaced the current query'); passed('late aborted responses cannot replace the active search');
  search('error'); await until(() => button('重新读取'), 'read error'); failRead = false; button('重新读取').click(); await until(() => cards().length === 1, 'read retry'); passed('failed reads remain retryable without mutating any source');
  controls.setActive(false); await pause(40); const before = requests.length; controls.setRevision(value => value + 1); await pause(280); assert(requests.length === before, 'Hidden page made an API request');
  controls.setActive(true); await until(() => requests.length > before && cards().length === 1, 'reactivation refresh'); passed('hidden pages stop requests and refresh on return or article revision changes');
  search(''); await until(() => cards().length === 60, 'final manual view');
  assert(requests.every(request => request.method === 'GET' && request.url.startsWith('/api/images?')), 'The fixture performed a write or unexpected request');
  output.dataset.status = 'passed'; output.textContent = JSON.stringify({ status: 'passed', results }); window.__imageLibraryFixture = { requests, opened, controls, results };
}
run().catch(error => { output.dataset.status = 'failed'; output.textContent = JSON.stringify({ status: 'failed', message: error.message, results }); console.error(error); });
