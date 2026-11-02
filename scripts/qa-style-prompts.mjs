import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:4173';
const out = path.resolve(process.argv[3] || 'output/style-prompts/browser');
const catalog = process.argv[4] ? JSON.parse(await fs.readFile(process.argv[4], 'utf8')) : null;
if (catalog) {
  assert(Array.isArray(catalog.items) && Array.isArray(catalog.categories), 'Catalog fixture must use the /api/prompts response format');
  assert.equal(catalog.items.filter(item => item.category === 'styles').length, 4);
}
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const report = { status: 'running', cases: [], realApiRequests: 0, modelCalls: 0 };

try {
  for (const theme of ['jade', 'glass', 'civic']) {
    for (const viewport of [{ width: 1600, height: 1000 }, { width: 1180, height: 820 }]) {
      const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
      const escaped = [], errors = [];
      // The harness implements its own complete in-memory fetch service. Any
      // browser-level API request is a leak and is aborted before reaching a real service.
      await context.route('**/api/**', route => { escaped.push({ url: route.request().url(), method: route.request().method() }); return route.abort(); });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(String(error)));
      const name = `${theme}-${viewport.width}x${viewport.height}`;
      try {
        await page.goto(`${base}/tests/prompt-library.html?theme=${theme}`, { waitUntil: 'networkidle' });
        await page.waitForFunction(() => {
          try { return ['passed', 'failed'].includes(JSON.parse(document.getElementById('prompt-library-result').textContent).status); }
          catch { return false; }
        }, undefined, { timeout: 45000 });
        const result = JSON.parse(await page.locator('#prompt-library-result').innerText());
        assert.equal(result.status, 'passed', result.error || 'Prompt component harness failed');
        assert(result.results.filter(entry => /文风|公文/.test(entry.name)).length >= 4, 'Missing style-specific interaction acceptance');
        assert.equal(result.realApiRequests, 0);
        assert.equal(result.modelCalls, 0);
        assert.deepEqual(escaped, [], 'A request escaped the in-memory service');
        assert.deepEqual(errors, [], 'Browser runtime error');

        // Constrain the actual component exactly as the functional app panel
        // does; keep the test report out of screenshot geometry.
        await page.addStyleTag({ content: 'body>aside{display:none!important}#prompt-library-root{height:100vh}.workspace-shell{display:block!important;height:100vh;min-height:0;box-sizing:border-box}.workspace-shell>div:not([hidden]){height:100%;min-height:0}.workspace-shell::before{display:none!important}' });
        const workspace = page.locator('.runtime-prompt-library');
        await workspace.locator('.prompt-category-tabs').getByRole('button', { name: /^文风/ }).click();
        assert.equal(await workspace.locator('.prompt-card').count(), 4);
        await workspace.locator('.prompt-list').getByRole('button', { name: /政府公文·正文/ }).click();
        await workspace.getByRole('heading', { name: '政府公文·正文', exact: true }).waitFor();
        assert.match(await workspace.locator('.prompt-flow-context').innerText(), /不用于拟题或纲要规划/);
        const longText = await workspace.locator('.prompt-content').innerText();
        assert.match(longText, /8\. 隔离模拟公文规则/);
        const geometry = await workspace.evaluate(node => {
          const detail = node.querySelector('.prompt-detail');
          const content = node.querySelector('.prompt-content');
          const list = node.querySelector('.prompt-list');
          return {
            detailHeight: detail.clientHeight,
            detailScroll: detail.scrollHeight,
            contentWidth: content.clientWidth,
            contentScroll: content.scrollWidth,
            listHeight: list.clientHeight,
            overflow: document.documentElement.scrollWidth > innerWidth + 1,
          };
        });
        assert(geometry.detailHeight > 200 && geometry.detailScroll > geometry.detailHeight, 'Long official instructions need a usable independent scroller');
        assert(geometry.listHeight > 200 && !geometry.overflow, 'Style list should remain usable without horizontal page overflow');
        assert(geometry.contentScroll <= geometry.contentWidth + 1, 'Long instruction text must wrap instead of scrolling horizontally');
        await page.screenshot({ path: path.join(out, `${name}-body.png`), animations: 'disabled' });

        await workspace.getByRole('button', { name: '编辑正文', exact: true }).click();
        const editor = workspace.getByRole('textbox', { name: '指令正文', exact: true });
        assert.equal(await editor.inputValue(), longText);
        await workspace.getByRole('button', { name: '保存修改', exact: true }).scrollIntoViewIfNeeded();
        assert(await workspace.getByRole('button', { name: '保存修改', exact: true }).isDisabled(), 'Opening the style editor cannot mark a clean prompt dirty');
        assert(await workspace.getByRole('button', { name: '取消编辑', exact: true }).isVisible(), 'Cancel must remain reachable with long instructions');
        await page.screenshot({ path: path.join(out, `${name}-editor.png`), animations: 'disabled' });
        await workspace.getByRole('button', { name: '取消编辑', exact: true }).click();
        assert.equal(await editor.count(), 0);
        assert.deepEqual(escaped, []);
        assert.deepEqual(errors, []);
        report.cases.push({ theme, viewport, status: 'passed', interactions: result.cases, simulatedReads: result.reads, simulatedWrites: result.writes, geometry });
        await fs.writeFile(path.join(out, `${name}-interactions.json`), JSON.stringify(result, null, 2));
      } catch (error) {
        await page.screenshot({ path: path.join(out, `${name}-failed.png`), animations: 'disabled' }).catch(() => {});
        throw error;
      } finally { await context.close(); }

      if (catalog) {
        const catalogContext = await browser.newContext({ viewport, reducedMotion: 'reduce' });
        const escapedCatalog = [], catalogErrors = [];
        await catalogContext.route('**/api/**', route => { escapedCatalog.push(route.request().url()); return route.abort(); });
        await catalogContext.addInitScript(fixture => {
          window.__styleCatalogMock = true;
          window.__styleCatalogRequests = [];
          window.fetch = async (input, options = {}) => {
            const url = typeof input === 'string' ? input : input.url;
            const method = options.method || 'GET';
            window.__styleCatalogRequests.push({ url, method });
            if (url === '/api/prompts' && method === 'GET') return new Response(JSON.stringify(fixture), { status: 200, headers: { 'Content-Type': 'application/json' } });
            throw new Error(`完整目录验收只允许模拟读取，禁止请求：${method} ${url}`);
          };
        }, catalog);
        const catalogPage = await catalogContext.newPage();
        catalogPage.on('pageerror', error => catalogErrors.push(String(error)));
        try {
          await catalogPage.goto(`${base}/tests/style-prompt-catalog.html?theme=${theme}`, { waitUntil: 'networkidle' });
          const workspace = catalogPage.locator('.runtime-prompt-library');
          const expectedCategories = catalog.categories.filter(item => item.id !== 'all');
          await workspace.locator('.prompt-category-tabs button').first().waitFor();
          assert.equal(await workspace.locator('.prompt-category-tabs button').count(), expectedCategories.length);
          assert.equal(expectedCategories.length, 6, 'The complete catalog must include six actual groups');
          const styleCategory = expectedCategories.find(item => item.id === 'styles');
          assert(styleCategory, 'The complete catalog is missing the styles group');
          await workspace.locator('.prompt-category-tabs').getByRole('button', { name: new RegExp(`^${styleCategory.name}`) }).click();
          const styleItems = catalog.items.filter(item => item.category === 'styles');
          assert.deepEqual(await workspace.locator('.prompt-card strong').allTextContents(), styleItems.map(item => item.title));
          for (const item of styleItems) {
            await workspace.locator('.prompt-list').getByRole('button', { name: new RegExp(item.title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }).click();
            assert.equal(await workspace.locator('.prompt-content').textContent(), item.content, `Complete default text changed: ${item.id}`);
          }
          const body = styleItems.find(item => item.id === 'writing_style_official_body');
          assert(body && body.content.length > 1000, 'The body fixture must include the complete eight official prose rules');
          for (let index = 1; index <= 8; index += 1) assert(body.content.includes(`\n${index}. `), `Missing original rule ${index}`);
          await workspace.locator('.prompt-list').getByRole('button', { name: new RegExp(body.title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }).click();
          const before = await workspace.locator('.prompt-content').textContent();
          assert.equal(before, body.content);
          const geometry = await workspace.evaluate(node => {
            const detail = node.querySelector('.prompt-detail'), content = node.querySelector('.prompt-content');
            return { height: detail.clientHeight, scrollHeight: detail.scrollHeight, contentWidth: content.clientWidth, contentScroll: content.scrollWidth, whiteSpace: getComputedStyle(content).whiteSpace, overflow: document.documentElement.scrollWidth > innerWidth + 1 };
          });
          assert(geometry.height > 200 && geometry.scrollHeight > geometry.height && geometry.whiteSpace === 'pre-wrap');
          assert(!geometry.overflow && geometry.contentScroll <= geometry.contentWidth + 1);
          await catalogPage.screenshot({ path: path.join(out, `${name}-complete-catalog.png`), animations: 'disabled' });
          await workspace.getByRole('button', { name: '编辑正文', exact: true }).click();
          assert.equal(await workspace.getByRole('textbox', { name: '指令正文', exact: true }).inputValue(), body.content);
          await workspace.getByRole('button', { name: '保存修改', exact: true }).scrollIntoViewIfNeeded();
          assert(await workspace.getByRole('button', { name: '保存修改', exact: true }).isDisabled());
          assert(await workspace.getByRole('button', { name: '取消编辑', exact: true }).isVisible());
          await catalogPage.screenshot({ path: path.join(out, `${name}-complete-editor.png`), animations: 'disabled' });
          await workspace.getByRole('button', { name: '取消编辑', exact: true }).click();
          assert.equal(await workspace.locator('.prompt-content').textContent(), before);
          assert.deepEqual(await catalogPage.evaluate(() => window.__styleCatalogRequests), [{ url: '/api/prompts', method: 'GET' }], 'Complete catalog browsing/editing must issue only one simulated GET');
          assert.deepEqual(escapedCatalog, []); assert.deepEqual(catalogErrors, []);
          report.cases.push({ theme, viewport, scenario: 'complete-readonly-catalog', status: 'passed', categories: expectedCategories.length, styles: styleItems.length, bodyCharacters: body.content.length, geometry });
        } catch (error) {
          await catalogPage.screenshot({ path: path.join(out, `${name}-complete-failed.png`), animations: 'disabled' }).catch(() => {});
          throw error;
        } finally { await catalogContext.close(); }
      }
    }
  }
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = error.stack || String(error);
  throw error;
} finally {
  await browser.close();
  await fs.writeFile(path.join(out, 'results.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}
