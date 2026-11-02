import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:4189';
const out = path.resolve(process.argv[3] || 'output/document-deletion/frontend');
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const results = { passed: [], realApiCalls: 0, realModelCalls: 0 };
try {
  for (const theme of ['jade', 'glass', 'civic', 'demo']) for (const viewport of [{ width: 1440, height: 1000 }, { width: 480, height: 820 }]) {
    const name = `${theme}-${viewport.width}`, errors = [], leaks = [];
    const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
    await context.route('**/api/**', route => { leaks.push(route.request().url()); return route.abort(); });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    const state = expression => page.evaluate(expression);
    const modal = page.getByRole('alertdialog', { name: '删除文档', exact: true });
    const confirm = () => modal.getByRole('button', { name: '永久删除', exact: true });
    const consent = () => modal.getByRole('checkbox');
    const open = async () => { await page.getByRole('button', { name: '删除文档 被文字和图片共同引用的隔离样本文档', exact: true }).click(); await modal.waitFor(); await page.waitForFunction(() => !document.querySelector('.document-delete-dialog').getAttribute('aria-busy').includes('true')); };
    try {
      await page.goto(`${base}/tests/document-deletion.html?theme=${theme}`, { waitUntil: 'networkidle' });
      await page.waitForFunction(() => window.__deletionFixture?.library.documents.length === 2);
      await open();
      assert(await confirm().isDisabled());
      assert((await modal.innerText()).includes('文字引用 2 处 · 图片引用 1 处 · 已选为写作资料'));
      assert((await modal.innerText()).includes('无法撤销'));
      await consent().check(); assert(await confirm().isEnabled());
      const hit = await confirm().evaluate(element => { const box = element.getBoundingClientRect(); return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, hit: element.contains(document.elementFromPoint((box.left + box.right) / 2, (box.top + box.bottom) / 2)) }; });
      assert(hit.left >= 0 && hit.right <= viewport.width && hit.top >= 0 && hit.bottom <= viewport.height && hit.hit, `Unreachable delete hit target ${JSON.stringify(hit)}`);
      await page.screenshot({ path: path.join(out, `${name}-warning.png`), animations: 'disabled' });
      await modal.getByRole('button', { name: '取消', exact: true }).click();
      assert.equal(await state(() => window.__deletionFixture.requests.filter(request => request.method === 'DELETE').length), 0);
      // A late impact response cannot reopen a dismissed modal or replace another document.
      await state(() => { window.__deletionFixture.holdImpact = true; });
      await page.getByRole('button', { name: '删除文档 被文字和图片共同引用的隔离样本文档', exact: true }).click();
      await page.waitForFunction(() => window.__deletionFixture.releaseImpact);
      await page.keyboard.press('Escape'); await modal.waitFor({ state: 'detached' });
      await state(() => window.__deletionFixture.open('source-2'));
      await modal.waitFor(); await page.waitForFunction(() => document.querySelector('.document-delete-name')?.textContent === '另一份保留不动的隔离文档');
      await state(() => window.__deletionFixture.releaseImpact());
      await page.waitForTimeout(40); assert.equal(await modal.locator('.document-delete-name').innerText(), '另一份保留不动的隔离文档');
      await page.keyboard.press('Escape');
      // Reader deletion reuses the same impact UI and Escape preserves reading.
      await page.getByRole('button', { name: '阅读 被文字和图片共同引用的隔离样本文档', exact: true }).click();
      await page.getByRole('button', { name: '删除当前文档', exact: true }).click(); await modal.waitFor();
      await page.keyboard.press('Escape'); await modal.waitFor({ state: 'detached' });
      assert(await page.locator('.reader-panel').isVisible());
      await page.getByRole('button', { name: '关闭资料阅读', exact: true }).click();
      // Active related work disables deletion, still allowing read-only inspection and cancellation.
      await state(() => { window.__deletionFixture.scenario = 'busy'; }); await open();
      await consent().check(); assert(await confirm().isDisabled()); assert((await modal.innerText()).includes('模拟资料加工'));
      await page.keyboard.press('Escape');
      await state(() => { window.__deletionFixture.scenario = 'plain'; }); await open();
      assert.equal(await consent().count(), 0); assert(await confirm().isEnabled());
      await page.keyboard.press('Escape');
      // Failed deletion retains the modal and sources, and remains retryable.
      await state(() => { window.__deletionFixture.scenario = 'failure'; }); await open(); await consent().check(); await confirm().click();
      await modal.getByRole('alert').waitFor(); assert((await modal.getByRole('alert').innerText()).includes('模拟删除失败'));
      assert.equal(await state(() => window.__deletionFixture.library.documents.length), 2);
      assert(await consent().isChecked());
      // New citations/version invalidate the old consent; only a new explicit confirmation may proceed.
      await state(() => { window.__deletionFixture.scenario = 'references'; window.__deletionFixture.impactVersion += 1; });
      await confirm().click(); await page.waitForFunction(() => document.querySelector('.document-delete-error')?.textContent.includes('引用范围已变化'));
      assert(!await consent().isChecked()); assert(await confirm().isDisabled());
      await consent().check();
      // Catalog fetched before deletion must not resurrect the source, even if the mock ignores abort.
      await state(() => { window.__deletionFixture.holdCatalog = true; window.__deletionFixture.library.refresh({ quiet: true }); });
      await page.waitForFunction(() => window.__deletionFixture.releaseCatalog);
      await state(() => { window.__deletionFixture.holdDelete = true; });
      const deleteCount = await state(() => window.__deletionFixture.requests.filter(request => request.method === 'DELETE').length);
      await confirm().click(); await page.waitForFunction(() => window.__deletionFixture.releaseDelete);
      assert(await modal.getByRole('button', { name: '正在删除…' }).isDisabled());
      await page.keyboard.press('Escape'); assert(await modal.isVisible());
      await state(() => window.__deletionFixture.releaseDelete());
      await modal.waitFor({ state: 'detached' });
      await state(() => window.__deletionFixture.releaseCatalog());
      await page.waitForTimeout(50);
      assert.equal(await state(() => window.__deletionFixture.requests.filter(request => request.method === 'DELETE').length), deleteCount + 1);
      assert.equal(await state(() => window.__deletionFixture.library.documents.length), 1);
      assert.equal(await page.locator('[data-selected-count]').innerText(), '1 份已选资料');
      assert.deepEqual(await state(() => window.__deletionFixture.changed), [{ sourceId: 'source-1' }]);
      // Even a later stale proxy list may not restore the deleted ID in this session.
      await state(async () => { window.__deletionFixture.deleted = false; await window.__deletionFixture.library.refresh({ quiet: true }); });
      assert.equal(await state(() => window.__deletionFixture.library.documents.length), 1);
      assert(await page.getByRole('button', { name: '删除文档 另一份保留不动的隔离文档' }).isVisible());
      assert.deepEqual(errors, []); assert.deepEqual(leaks, []); assert.deepEqual(await state(() => window.__deletionFixture.unexpected), []);
      const writes = await state(() => window.__deletionFixture.requests.filter(request => request.method !== 'GET'));
      assert(writes.every(request => request.method === 'DELETE' && request.path === '/api/documents/source-1'));
      assert.equal(writes.at(-1).body.impactToken, 'impact-2'); assert.equal(writes.at(-1).body.confirmReferences, true);
      results.passed.push({ name, cases: ['card and reader entries', 'text/image consent', 'no browsing writes', 'late impact guard', 'active task block', 'failure retained', 'changed impact resets consent', 'one pending request', 'late catalog tombstone', 'selection/event cleanup', 'reachable narrow actions'] });
    } catch (error) { await page.screenshot({ path: path.join(out, `${name}-failed.png`), animations: 'disabled' }); throw new Error(`${name}: ${error.message}`, { cause: error }); }
    finally { await context.close(); }
  }
  await fs.writeFile(path.join(out, 'results.json'), JSON.stringify(results, null, 2));
  process.stdout.write(`${JSON.stringify(results)}\n`);
} finally { await browser.close(); }
