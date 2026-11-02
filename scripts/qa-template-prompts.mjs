import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:4173';
const out = path.resolve(process.argv[3] || 'output/prompt-current-view/templates');
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const report = { status: 'running', cases: [], realApiRequests: 0, modelCalls: 0 };
try {
  for (const theme of ['jade', 'glass', 'civic']) {
    for (const viewport of [{ width: 1600, height: 1000 }, { width: 1180, height: 820 }]) {
      const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
      const escaped = [], errors = [];
      await context.route('**/api/**', route => { escaped.push(route.request().url()); return route.abort(); });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(String(error)));
      const name = `${theme}-${viewport.width}x${viewport.height}`;
      try {
        await page.goto(`${base}/tests/template-prompt-library.html?theme=${theme}`, { waitUntil: 'networkidle' });
        await page.waitForFunction(() => {
          try { return ['passed', 'failed'].includes(JSON.parse(document.getElementById('template-prompt-result').textContent).status); }
          catch { return false; }
        }, undefined, { timeout: 45000 });
        const result = JSON.parse(await page.locator('#template-prompt-result').innerText());
        assert.equal(result.status, 'passed', result.error || 'Template prompt interaction failed');
        assert.deepEqual(errors, []);
        assert.deepEqual(escaped, [], 'A request escaped the in-memory mock');
        assert.equal(result.realApiRequests, 0);
        assert.equal(result.modelCalls, 0);
        await page.addStyleTag({ content: 'body>aside{display:none!important}#template-prompt-root{height:100vh}.workspace-shell::before{display:none!important}' });
        const workspace = page.locator('.template-workspace');
        const panel = workspace.getByRole('region', { name: '结构模板提示词', exact: true });
        await panel.scrollIntoViewIfNeeded();
        const geometry = await workspace.evaluate(node => {
          const detail = node.querySelector('.template-detail-scroll');
          const content = node.querySelector('.template-prompt-content');
          const list = node.querySelector('.template-list');
          return { detail: detail.clientHeight, list: list.clientHeight, content: content.clientWidth, contentScroll: content.scrollWidth,
            pageOverflow: document.documentElement.scrollWidth > innerWidth + 1 };
        });
        assert(geometry.detail > 200 && geometry.list > 200 && !geometry.pageOverflow, 'Template panes should remain independently usable');
        assert(geometry.contentScroll <= geometry.content + 1, 'Template instructions must wrap');
        await page.screenshot({ path: path.join(out, `${name}-view.png`), animations: 'disabled' });
        await panel.getByRole('button', { name: '编辑提示词', exact: true }).click();
        const editor = panel.getByRole('textbox', { name: '模板提示词正文', exact: true });
        await editor.waitFor();
        assert(await panel.getByRole('button', { name: '保存提示词', exact: true }).isDisabled(), 'Opening an editor must stay clean');
        await panel.getByRole('button', { name: '取消编辑', exact: true }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(out, `${name}-edit.png`), animations: 'disabled' });
        await panel.getByRole('button', { name: '取消编辑', exact: true }).click();
        report.cases.push({ name, status: 'passed', interactions: result.results, geometry });
      } catch (error) {
        report.cases.push({ name, status: 'failed', error: error.message });
        await page.screenshot({ path: path.join(out, `${name}-failure.png`), animations: 'disabled' }).catch(() => {});
        throw error;
      } finally { await context.close(); }
    }
  }
  report.status = 'passed';
} catch (error) { report.status = 'failed'; report.error = error.message; throw error; }
finally { await fs.writeFile(path.join(out, 'results.json'), JSON.stringify(report, null, 2)); await browser.close(); }
console.log(JSON.stringify({ status: report.status, cases: report.cases.length, realApiRequests: 0, modelCalls: 0 }));
