// Synthetic browser acceptance: every /api request is intercepted in the browser.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const base = (process.argv[2] || 'http://127.0.0.1:4173').replace(/\/$/, '');
const output = path.resolve(process.argv[3] || path.join(root, 'output/demo-theme'));
await fs.mkdir(output, { recursive: true });

const document = {
  id: 'qa-demo-document', revisionId: 'qa-demo-revision', status: 'ready',
  title: '演示界面受控阅读样例', kind: 'article', read: true,
  source: '隔离验收样例', date: '2026-10-26', createdAt: '2026-10-26T00:00:00Z',
  summary: '本资料仅用于检查阅读界面，不连接真实资料库或模型。',
  tags: [], classificationPaths: [], bodyWordCount: 42, language: 'zh',
  blocks: [{ id: 'qa-demo-block', kind: 'paragraph', text: '演示模式保留文档阅读与文稿写作。' }],
  assets: [], imageAssets: [], cardCover: { status: 'completed' },
};
const project = {
  id: 'qa-demo-project', title: '演示界面受控文稿', topic: '演示界面受控文稿',
  brief: { automation: 'manual', workflow: 'chapter', articleTitle: '演示界面受控文稿' },
  mode: 'strict', version: 1, targetWords: 6000, sources: [], sourceIds: [],
  sections: [], runs: [], illustrations: [], outlineConfirmed: false,
  createdAt: '2026-10-26T00:00:00Z', updatedAt: '2026-10-26T00:00:00Z',
};
function response(url) {
  if (url === '/api/documents') return { documents: [document], total: 1 };
  if (url === '/api/documents/qa-demo-document') return { document };
  if (url.endsWith('/card-cover')) return { cardCover: { status: 'completed', revisionId: document.revisionId } };
  if (url === '/api/models') return { models: [], preferences: { autoProcess: false, autoTranslate: false, idleProcess: false } };
  if (url === '/api/jobs') return { jobs: [], counts: { queued: 0, running: 0, failed: 0, total: 0 } };
  if (url === '/api/writing/projects') return { projects: [project], capabilities: {} };
  if (url === '/api/writing/projects/qa-demo-project') return { project };
  if (url === '/api/ai/writing-activity') return { active: true };
  if (url === '/api/prompts') return { prompts: [] };
  if (url === '/api/home') return { topics: [], writing: { total: 1, totalWords: 0 } };
  if (url.startsWith('/api/documents/') && url.endsWith('/ai')) return { ai: {}, jobs: [] };
  if (url === '/api/ai/taxonomy') return { taxonomy: [], paths: [] };
  return { items: [], entries: [], topics: [], images: [], nodes: [], edges: [], total: 0, results: [], ai: {} };
}
async function isolatedContext(browser, stored, viewport = { width: 1600, height: 1000 }) {
  const context = await browser.newContext({ viewport, reducedMotion: 'no-preference' });
  const requests = [], writes = [], errors = [];
  await context.addInitScript(value => {
    if (!sessionStorage.getItem('__demoQASeeded')) {
      localStorage.setItem('koohub-appearance', value);
      sessionStorage.setItem('__demoQASeeded', 'yes');
    }
  }, stored);
  await context.route('**/api/**', route => {
    const request = route.request(), url = new URL(request.url()).pathname;
    requests.push({ method: request.method(), path: url });
    if (!['GET', 'HEAD'].includes(request.method()) && url !== '/api/ai/writing-activity') {
      writes.push({ method: request.method(), path: url });
      return route.fulfill({ status: 409, json: { detail: 'Acceptance test blocked a non-read request.' } });
    }
    return route.fulfill({ status: 200, json: response(url) });
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  return { context, page, requests, writes, errors };
}
const preference = page => page.evaluate(() => localStorage.getItem('koohub-appearance'));
async function heading(page, selector, expected) {
  await page.locator(selector).waitFor({ state: 'visible' });
  assert.equal(await page.locator(selector).textContent(), expected);
}
async function openAction(page, label) {
  const button = page.getByRole('button', { name: label, exact: true });
  if (!await button.count() || !await button.first().isVisible()) {
    await page.getByRole('button', { name: '展开更多功能', exact: true }).click();
    await page.getByRole('menuitem', { name: label, exact: true }).click();
  } else await button.first().click();
}
async function demoShell(page) {
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'demo');
  assert.match(page.url(), /[?&]theme=demo(?:&|$)/);
  const navigation = page.getByRole('navigation', { name: '主导航', exact: true });
  assert.deepEqual(await navigation.getByRole('button').allTextContents(), ['文档阅读', '文稿写作']);
  assert.equal(await page.locator('.app-sidebar button').count(), 2, 'Demo sidebar contains only its two primary destinations');
  assert.equal(await page.locator('.sidebar-bottom,.sidebar-more-menu,.sidebar-more-trigger,.sidebar-collapse').count(), 0, 'Demo has no lower sidebar controls or overflow menu');
  assert.equal(await page.locator('.sidebar-brand-row,.ambient-background,.home-ornament').count(), 0);
  assert.equal(await page.locator('.graph-workspace,.image-library,.template-workspace,.runtime-prompt-library').count(), 0);
  for (const label of ['外观主题', '使用指南', '关于文枢', '返回主页']) {
    assert.equal(await page.getByRole('button', { name: label, exact: true }).count(), 0, label);
  }
  assert.equal(await page.locator('.theme-option').count(), 0);
  const icons = await page.locator('link[rel~="icon"]').evaluateAll(nodes => nodes.map(node => node.href));
  assert(icons.every(value => value.startsWith('data:')), JSON.stringify(icons));
  assert(!/文枢/.test(await page.title()));
  const animated = await page.evaluate(() => [...window.document.querySelectorAll('body *')].filter(el => {
    if (!el.getClientRects().length) return false;
    return [null, '::before', '::after'].some(pseudo => {
      const style = getComputedStyle(el, pseudo);
      return style.animationName.split(',').some(value => value.trim() !== 'none') ||
        style.transitionDuration.split(',').some(value => parseFloat(value) > 0);
    });
  }).map(el => ({ tag: el.tagName, classes: String(el.className) })).slice(0, 20));
  assert.deepEqual(animated, []);
  assert.equal(await page.evaluate(() => window.document.getAnimations().filter(animation => animation.playState === 'running').length), 0);
}
async function demoCase(browser, viewport) {
  const { context, page, requests, writes, errors } = await isolatedContext(browser, 'jade', viewport);
  const shot = viewport.height === 520 ? `${viewport.width}x${viewport.height}` : String(viewport.width);
  try {
    await page.goto(`${base}/?theme=demo`, { waitUntil: 'networkidle' });
    await heading(page, '.library-heading h1', '文档阅读');
    await page.locator('.document-card').waitFor();
    assert.equal(await page.locator('.document-card').count(), 1);
    await demoShell(page);
    assert.equal(await preference(page), 'jade');
    assert.equal(await page.title(), '文档阅读');
    const card = page.locator('.document-card').first();
    const before = await card.evaluate(el => { const rect = el.getBoundingClientRect(); return { x: rect.x, y: rect.y }; });
    await card.hover();
    const after = await card.evaluate(el => { const rect = el.getBoundingClientRect(), style = getComputedStyle(el); return { x: rect.x, y: rect.y, identity: new DOMMatrix(style.transform === 'none' ? undefined : style.transform).isIdentity, shadow: style.boxShadow }; });
    assert(Math.abs(after.x - before.x) < .5 && Math.abs(after.y - before.y) < .5, JSON.stringify({ before, after }));
    assert(after.identity, JSON.stringify(after));
    assert.equal(after.shadow, 'none');
    await page.mouse.move(1, 1);
    await page.screenshot({ path: path.join(output, `demo-${shot}-library.png`) });
    await page.locator('.document-card-title').click();
    await page.locator('.reader-panel').waitFor({ state: 'visible' });
    await page.locator('.reader-body .source-block').waitFor();
    assert.equal(await page.locator('.reader-body .source-block').count(), 1);
    await demoShell(page);
    await page.screenshot({ path: path.join(output, `demo-${shot}-reader.png`) });
    await page.getByRole('button', { name: '关闭资料阅读', exact: true }).click();
    const nav = page.getByRole('navigation', { name: '主导航', exact: true });
    await nav.getByRole('button', { name: '文稿写作', exact: true }).click();
    await heading(page, '.writing-page-heading h1', '文稿写作');
    await demoShell(page);
    assert.equal(await page.title(), '文稿写作');
    assert.equal(await page.locator('.writing-project-card').count(), 1);
    await page.screenshot({ path: path.join(output, `demo-${shot}-writing.png`) });
    await page.getByRole('button', { name: '开始新的写作', exact: true }).click();
    await page.locator('#writing-topic').waitFor({ state: 'visible' });
    await demoShell(page);
    await page.screenshot({ path: path.join(output, `demo-${shot}-brief.png`) });
    await page.getByRole('button', { name: '返回文稿列表', exact: true }).click();
    await heading(page, '.writing-page-heading h1', '文稿写作');
    await nav.getByRole('button', { name: '文档阅读', exact: true }).click();
    await heading(page, '.library-heading h1', '文档阅读');
    await page.reload({ waitUntil: 'networkidle' });
    await heading(page, '.library-heading h1', '文档阅读');
    await demoShell(page);
    assert.equal(await preference(page), 'jade');
    assert.deepEqual(writes, []); assert.deepEqual(errors, []);
    return { case: 'demo', viewport, primaryNavigation: 2, lowerSidebarControls: 0, overflowMenu: false, preferencePreserved: true,
      refreshPreserved: true, unexpectedWrites: writes, pageErrors: errors, requests };
  } catch (error) {
    await page.screenshot({ path: path.join(output, `failed-demo-${shot}.png`) });
    throw error;
  } finally { await context.close(); }
}
async function normalCase(browser, theme) {
  const { context, page, writes, errors } = await isolatedContext(browser, 'glass');
  try {
    await page.goto(`${base}/?theme=${theme}`, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
    assert.equal(await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('button').count(), 6);
    assert(await page.locator('.sidebar-brand-row').isVisible());
    assert.equal(await page.locator('.sidebar-bottom').count(), 1, 'Normal themes retain lower sidebar controls');
    assert(await page.locator('.sidebar-collapse').isVisible(), 'Normal themes retain the collapse control');
    const lowerLabels = ['入库进度', '写作进度', '已选资料', '外观主题', '使用指南', '系统设置'];
    const more = page.getByRole('button', { name: '展开更多功能', exact: true });
    if (await more.count() && await more.isVisible()) {
      await more.click();
      for (const label of lowerLabels) assert(await page.getByRole('menuitem', { name: label, exact: true }).isVisible(), label);
      await page.keyboard.press('Escape');
    } else {
      for (const label of lowerLabels) assert(await page.locator('.sidebar-bottom').getByRole('button', { name: label, exact: true }).isVisible(), label);
    }
    await openAction(page, '外观主题');
    assert.equal(await page.locator('.theme-option').count(), 3);
    assert.equal(await page.locator('.theme-option').filter({ hasText: /演示/ }).count(), 0, 'Demo must not appear among appearance options');
    assert.equal(await preference(page), theme);
    await page.screenshot({ path: path.join(output, `normal-${theme}.png`) });
    assert.deepEqual(writes, [], 'Unexpected write requests'); assert.deepEqual(errors, [], 'Browser page errors');
    return { case: 'normal', theme, primaryNavigation: 6, themeChoices: 3, lowerSidebarActions: lowerLabels, collapseControl: true,
      preference: await preference(page), pageErrors: errors };
  } catch (error) {
    error.message += `\nNormal theme ${theme}; pageErrors=${JSON.stringify(errors)}; writes=${JSON.stringify(writes)}`;
    await page.screenshot({ path: path.join(output, `failed-normal-${theme}.png`) });
    throw error;
  } finally { await context.close(); }
}
async function fallbackCase(browser, search) {
  const { context, page, writes, errors } = await isolatedContext(browser, 'demo');
  try {
    await page.goto(`${base}/${search}`, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'civic');
    assert.equal(await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('button').count(), 6);
    assert.equal(await preference(page), 'civic');
    assert.deepEqual(writes, []); assert.deepEqual(errors, []);
    return { case: 'invalid-saved-demo', search, actualTheme: 'civic', pageErrors: errors };
  } finally { await context.close(); }
}
const result = { status: 'running', synthetic: true, realApiCalls: 0, modelCalls: 0, saveCalls: 0, cases: [] };
let browser;
try {
  browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
  for (const viewport of [{ width: 1600, height: 1000 }, { width: 1280, height: 720 }, { width: 1280, height: 520 }]) result.cases.push(await demoCase(browser, viewport));
  for (const theme of ['civic', 'jade', 'glass']) result.cases.push(await normalCase(browser, theme));
  for (const search of ['', '?theme=unknown']) result.cases.push(await fallbackCase(browser, search));
  result.status = 'passed';
} catch (error) {
  result.status = 'failed'; result.error = String(error); result.stack = error.stack;
  process.exitCode = 1;
} finally {
  await browser?.close();
  await fs.writeFile(path.join(output, 'browser-acceptance.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ status: result.status, cases: result.cases.length, error: result.error,
    output: path.join(output, 'browser-acceptance.json'), realApiCalls: 0, modelCalls: 0 }));
}
