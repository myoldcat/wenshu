import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:4174';
const out = path.resolve(process.argv[3] || 'output/document-deletion/writing-browser');
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const cases = [], errors = [], leaks = [];
const frames = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
async function context(width) {
  const value = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce' });
  await value.route('**/api/**', route => { leaks.push(route.request().url()); return route.abort(); });
  const page = await value.newPage(); page.on('pageerror', error => errors.push(String(error)));
  return { value, page };
}
try {
  for (const theme of ['jade', 'glass', 'civic', 'demo']) for (const width of [1440, 560]) {
    const { value, page } = await context(width);
    try {
      console.log(`Checking ${theme}/${width}`);
      await page.goto(`${base}/tests/writing-deleted-sources.html?theme=${theme}`, { waitUntil: 'networkidle' });
      await page.locator('.writing-body-reading .writing-citation-icon').first().waitFor();
      const before = await page.evaluate(() => JSON.stringify(window.__deletedWriting.state.project));
      const icon = page.locator('.writing-body-reading .writing-citation-icon').first();
      assert.equal(await icon.innerText(), '1');
      assert.equal(await page.locator('.writing-image-unavailable').innerText(), '图片来源文档已删除，原图已不可用。');
      assert.equal(await page.locator('.writing-body-reading img').count(), 0);
      assert.equal(await page.locator('.writing-citation-popover').count(), 0, 'No unsolicited source popover');
      assert.equal(await page.locator('.writing-root > .writing-preview-warning,.writing-flow-actions .writing-preview-warning').count(), 0, 'No global deleted-source banner');
      await icon.hover();
      const popup = page.locator('.writing-citation-popover'); await popup.waitFor();
      assert.match(await popup.innerText(), /引用文档已删除/);
      assert.equal(await popup.locator('blockquote').innerText(), '保存的来源摘录，必须保留。');
      assert(await popup.getByRole('button', { name: '查看写作时选定的原文' }).isDisabled());
      await page.keyboard.press('Escape'); await popup.waitFor({ state: 'detached' });
      await icon.evaluate(node => { node.blur(); node.focus(); }); await popup.waitFor();
      assert.match(await popup.innerText(), /引用文档已删除/);
      await page.keyboard.press('ArrowDown');
      await frames(page);
      const bounds = await popup.boundingBox(); assert(bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: '全文预览', exact: true }).click();
      await page.locator('.writing-preview').waitFor();
      const previewIcon = page.locator('.writing-preview-article .writing-citation-icon').first();
      await previewIcon.hover(); await popup.waitFor();
      assert.match(await popup.innerText(), /引用文档已删除/);
      assert.equal(await previewIcon.innerText(), '1');
      assert.equal(await page.locator('.writing-preview-article .writing-image-unavailable').innerText(), '图片来源文档已删除，原图已不可用。');
      await page.keyboard.press('Escape');
      await page.screenshot({ path: path.join(out, `${theme}-${width}-preview.png`), animations: 'disabled' });
      assert.equal(await page.evaluate(() => JSON.stringify(window.__deletedWriting.state.project)), before);
      assert.deepEqual(await page.evaluate(() => window.__deletedWriting.opened), []);
      assert.deepEqual(await page.evaluate(() => window.__deletedWriting.mutations), []);
      cases.push({ theme, width, name: 'chapter-hover-focus-and-preview-deleted-source', passed: true });
    } catch (error) {
      await page.screenshot({ path: path.join(out, `${theme}-${width}-failed.png`), animations: 'disabled' });
      throw error;
    } finally { await value.close(); }
  }
  {
    const { value, page } = await context(1440);
    try {
      await page.goto(`${base}/tests/writing-deleted-sources.html?theme=civic&deleted=0`, { waitUntil: 'networkidle' });
      const icon = page.locator('.writing-body-reading .writing-citation-icon').first(); await icon.hover();
      const popup = page.locator('.writing-citation-popover'); await popup.waitFor();
      assert(await popup.getByRole('button', { name: '查看写作时选定的原文' }).isEnabled());
      await page.evaluate(() => window.__deletedWriting.markDeleted());
      await popup.waitFor({ state: 'detached' });
      await icon.evaluate(node => { node.blur(); node.focus(); }); await popup.waitFor();
      assert.match(await popup.innerText(), /引用文档已删除/);
      assert(await popup.getByRole('button', { name: '查看写作时选定的原文' }).isDisabled());
      cases.push({ name: 'live-library-delete-dismisses-old-popover', passed: true });
    } finally { await value.close(); }
  }
  {
    const { value, page } = await context(1440);
    try {
      await page.goto(`${base}/tests/writing-deleted-sources.html?mode=hook&deleted=0`, { waitUntil: 'networkidle' });
      await page.waitForFunction(() => window.__deletedWriting.state?.project.id && !window.__deletedWriting.state.loading);
      await page.evaluate(() => { const test = window.__deletedWriting; test.state.setProject(project => ({ ...project,
        activeSectionId: 'fixture-section', sections: project.sections.map(section => ({ ...section, content: '必须保留的未保存草稿。[E1]', locked: true })) })); });
      await page.waitForFunction(() => window.__deletedWriting.state.dirty);
      await page.evaluate(() => { const test = window.__deletedWriting; test.remote.version = 5; test.remote.sections[0].content = '迟到的远端正文'; test.markDeleted(); });
      await page.waitForFunction(() => window.__deletedWriting.state.project.libraryAvailability.sources[0].status === 'deleted');
      assert.deepEqual(await page.evaluate(() => { const state=window.__deletedWriting.state; return {content:state.project.sections[0].content,
        locked:state.project.sections[0].locked, active:state.project.activeSectionId, dirty:state.dirty, version:state.project.version}; }),
      { content: '必须保留的未保存草稿。[E1]', locked: true, active: 'fixture-section', dirty: true, version: 4 });
      assert.deepEqual(await page.evaluate(() => window.__deletedWriting.mutations), []);
      cases.push({ name: 'library-refresh-preserves-dirty-prose-lock-section-and-version', passed: true });
    } finally { await value.close(); }
  }
  assert.deepEqual(errors, []); assert.deepEqual(leaks, []);
  await fs.writeFile(path.join(out, 'results.json'), JSON.stringify({ passed: true, cases, errors, realApiCalls: leaks.length, realModelCalls: 0 }, null, 2));
  console.log(JSON.stringify({ passed: true, cases: cases.length, realApiCalls: 0, realModelCalls: 0 }));
} catch (error) {
  await fs.writeFile(path.join(out, 'results.json'), JSON.stringify({ passed: false, cases, errors, leaks, error: String(error), stack: error.stack }, null, 2));
  throw error;
} finally { await browser.close(); }
