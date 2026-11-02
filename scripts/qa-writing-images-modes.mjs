import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:4173';
const out = path.resolve(process.argv[3] || 'output/writing-images-modes/browser');
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const result = { status: 'running', cases: [], realApiCalls: 0, realModelCalls: 0 };
const titles = ['一、设备的基本情况', '二、样本的使用范围', '三、已锁定的人工章节', '四、尚未编写的章节'];
const caseFilter = process.env.IMAGE_QA_CASE ? new RegExp(process.env.IMAGE_QA_CASE) : null;

async function run(theme, name, fn, { scenario = 'fresh', width = 1440, height = 1000, hook = false } = {}) {
  if (caseFilter && !caseFilter.test(`${theme}/${name}`)) return;
  const context = await browser.newContext({ viewport: { width, height }, reducedMotion: 'reduce' });
  const leaks = [], errors = [];
  await context.route('**/api/**', route => { leaks.push(route.request().url()); return route.abort(); });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  const state = () => page.evaluate(() => window.__imageModes.state);
  const snapshot = async () => structuredClone((await state()).project);
  const calls = async () => (await state()).calls;
  const mutations = async () => (await calls()).filter(call => !['list-images', 'open-doc', 'close', 'next'].includes(call.name));
  try {
    await page.goto(`${base}/tests/writing-images-modes.html?theme=${theme}&scenario=${scenario}${hook ? '&hook=1' : ''}`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => !!window.__imageModes);
    await page.getByRole('region', { name: scenario.startsWith('overview-') ? '当前文章配图' : '文章配图', exact: true }).waitFor();
    await fn({ page, state, snapshot, calls, mutations });
    assert.deepEqual(leaks, [], 'All catalog requests remain inside the isolated fixture');
    assert.deepEqual(errors, [], 'No browser runtime errors');
    assert.deepEqual((await state()).unexpected, []);
    result.cases.push({ theme, name, scenario, width, passed: true });
  } catch (error) {
    await page.screenshot({ path: path.join(out, `${theme}-${name}-failed.png`), animations: 'disabled' });
    throw new Error(`${theme}/${name}: ${error.message}`, { cause: error });
  } finally { await context.close(); }
}

async function assertPlacement(page, figureId, title) {
  await page.waitForFunction(() => !document.querySelector('dialog[open][aria-label="选择配图"]'));
  const figure = page.locator(`[data-writing-figure-id="${figureId}"]`);
  await figure.waitFor({ state: 'visible' });
  assert.match(await page.locator('.writing-images-chapters button[aria-current="location"]').innerText(), new RegExp(title));
  await page.waitForFunction(id => document.activeElement?.getAttribute('data-writing-figure-id') === id, figureId);
  const onScreen = await figure.evaluate(node => {
    const box = node.getBoundingClientRect(), pane = node.closest('.writing-images-scroll').getBoundingClientRect();
    return box.top >= pane.top - 2 && box.top < pane.bottom && box.left >= pane.left - 2 && box.left < pane.right;
  });
  assert.equal(onScreen, true, 'Inserted picture is revealed inside the chapter scroll pane');
}

try {
  for (const theme of ['jade', 'glass', 'civic', 'demo']) {
    await run(theme, 'skipped-overview-empty-art-and-explicit-start', async ({ page, snapshot, calls, mutations }) => {
      const before = await snapshot();
      await page.locator('[data-writing-images-empty] .writing-images-empty-art').waitFor();
      assert.deepEqual(await calls(), [], 'Opening the skipped overview does not save or generate');
      const start = page.locator('[data-writing-start-images]');
      assert.equal(await start.innerText(), '开始配图');
      await start.click();
      await page.getByLabel('配图范围', { exact: true }).waitFor();
      assert.deepEqual(await snapshot(), before, 'Opening configuration preserves the saved skipped decision');
      assert.deepEqual(await mutations(), [], 'Opening configuration never creates a task');
      await page.getByRole('button', { name: '为所选章节自动配图', exact: true }).click();
      await page.waitForFunction(() => window.__imageModes.calls.some(call => call.name === 'automatic-illustrations'));
      assert.deepEqual((await calls()).find(call => call.name === 'automatic-illustrations').body.sectionIds, ['chapter-a', 'chapter-b']);
      assert.deepEqual((await snapshot()).sections, before.sections);
      assert.deepEqual((await snapshot()).illustrationReview, before.illustrationReview);
    }, { scenario: 'overview-skipped' });

    await run(theme, 'completed-overview-explicit-start-retains-existing-images', async ({ page, snapshot, calls }) => {
      const before = await snapshot();
      const start = page.locator('[data-writing-start-images]');
      assert.equal(await start.innerText(), '继续配图');
      await start.click();
      await page.getByLabel('配图范围', { exact: true }).waitFor();
      assert.deepEqual(await snapshot(), before);
      assert.deepEqual(await calls(), []);
    }, { scenario: 'overview-complete' });

    await run(theme, 'automatic-layout-scope-and-selection', async ({ page, snapshot, calls, mutations }) => {
      const before = await snapshot();
      const chapters = page.locator('.writing-images-chapters');
      assert.equal(await chapters.locator('input[type="checkbox"]').count(), 5);
      assert.equal(await chapters.getByRole('checkbox', { name: `选择章节：${titles[2]}`, exact: true }).isDisabled(), true);
      assert.equal(await chapters.getByRole('checkbox', { name: `选择章节：${titles[3]}`, exact: true }).isDisabled(), true);
      assert.equal(await page.getByLabel('配图范围', { exact: true }).inputValue(), 'references');
      await page.getByLabel('配图范围', { exact: true }).selectOption('library');
      await page.getByLabel('配图范围', { exact: true }).selectOption('references');
      await chapters.getByRole('checkbox', { name: `选择章节：${titles[0]}`, exact: true }).uncheck();
      await chapters.getByRole('checkbox', { name: `选择章节：${titles[1]}`, exact: true }).uncheck();
      assert.equal(await page.getByRole('button', { name: '为所选章节自动配图', exact: true }).isDisabled(), true);
      await chapters.getByRole('checkbox', { name: `选择章节：${titles[1]}`, exact: true }).check();
      assert.deepEqual(await snapshot(), before);
      assert.equal((await mutations()).length, 0, 'Configuration and chapter selection do not save or search');
      await page.getByRole('button', { name: '为所选章节自动配图', exact: true }).click();
      await page.waitForFunction(() => window.__imageModes.calls.some(call => call.name === 'automatic-illustrations'));
      const batch = (await calls()).find(call => call.name === 'automatic-illustrations');
      assert.deepEqual(batch.body.sectionIds, ['chapter-b']);
      assert.equal(batch.body.searchScope, 'references');
      assert.deepEqual((await snapshot()).sections, before.sections, 'Automatic illustrations never edit body text');
      await page.screenshot({ path: path.join(out, `${theme}-automatic.png`), animations: 'disabled' });
    });

    await run(theme, 'manual-insertion-browse-search-and-position', async ({ page, snapshot, calls, mutations }) => {
      const before = await snapshot();
      await page.getByLabel('图片核对模型', { exact: true }).selectOption('image-model');
      await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
      await page.getByRole('button', { name: '在第 2 段后插入图片', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
      await dialog.waitFor();
      assert.equal(await dialog.getByLabel('图片范围', { exact: true }).inputValue(), 'references');
      await dialog.locator('.writing-image-catalog-card').first().waitFor();
      assert.equal(await dialog.locator('.writing-image-catalog-card').count(), 2);
      assert.equal((await mutations()).length, 0, 'Opening a paragraph chooser only reads the image catalog');
      assert.deepEqual(await snapshot(), before);
      await dialog.getByLabel('图片范围', { exact: true }).selectOption('library');
      await page.waitForFunction(() => document.querySelectorAll('.writing-image-picker[open] .writing-image-catalog-card').length === 3 || document.querySelectorAll('dialog[open] .writing-image-catalog-card').length === 3);
      const search = dialog.getByRole('textbox', { name: /检索|搜索/ }).first();
      await search.fill('三类设备比较');
      await dialog.getByRole('button', { name: '检索', exact: true }).click();
      await page.waitForFunction(() => document.querySelectorAll('dialog[open] .writing-image-catalog-card').length === 2);
      assert((await page.evaluate(() => window.__imageModes.requests)).some(request => request.method === 'GET' && request.url.includes('q=')));
      await dialog.locator('.writing-image-catalog-card').first().getByRole('button', { name: '插入图片', exact: true }).click();
      await page.waitForFunction(() => window.__imageModes.state.project.illustrations.length === 1);
      const inserted = (await calls()).find(call => call.name === 'manual-illustration');
      assert.equal(inserted.body.sectionId, 'chapter-a');
      assert.equal(inserted.body.paragraphIndex, 1);
      assert.equal(inserted.body.assetId, 'asset-reference-a');
      assert.equal(inserted.body.searchScope, 'library');
      assert.deepEqual((await snapshot()).sections, before.sections);
      assert.equal(Object.hasOwn(inserted.body, 'modelId'), false, 'Manual insertion does not request a model');
      await assertPlacement(page, 'figure-asset-reference-a-chapter-a', titles[0]);
      await page.getByRole('button', { name: '切换自动（已有配图保留）', exact: true }).click();
      assert.equal((await snapshot()).illustrations.length, 1);
      assert.equal((await mutations()).filter(call => call.name === 'manual-illustration').length, 1);
      await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
      await page.screenshot({ path: path.join(out, `${theme}-manual.png`), animations: 'disabled' });
      await page.getByRole('button', { name: '完成配图', exact: true }).click();
      assert.equal((await calls()).filter(call => call.name === 'complete-images').length, 1);
      assert.equal((await calls()).find(call => call.name === 'complete-images').body.skip, false);
    });

    await run(theme, 'existing-figures-preserved-and-explicit-skip', async ({ page, snapshot, calls, mutations }) => {
      const before = await snapshot();
      await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
      await page.getByRole('button', { name: '切换自动（已有配图保留）', exact: true }).click();
      assert.deepEqual(await snapshot(), before);
      assert.equal((await mutations()).length, 0);
      await page.getByRole('button', { name: '跳过，本文不配图', exact: true }).click();
      const confirm = page.getByRole('dialog', { name: '确认本文不配图', exact: true });
      await confirm.waitFor();
      assert.deepEqual(await snapshot(), before, 'Opening skip confirmation preserves images');
      await confirm.getByRole('button', { name: '保留配图', exact: true }).click();
      await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
      await page.getByRole('button', { name: '本文不配图', exact: true }).click();
      await confirm.getByRole('button', { name: '移除配图并跳过', exact: true }).click();
      const cleared = (await calls()).find(call => call.name === 'complete-images');
      assert.deepEqual(cleared.body, { skip: true, clearExisting: true });
      assert.equal((await snapshot()).illustrations.length, 0);
      assert.deepEqual((await snapshot()).sections, before.sections);
      assert.deepEqual((await snapshot()).sources, before.sources);
    }, { scenario: 'existing' });

    await run(theme, 'library-consent-and-select-all', async ({ page, calls, mutations }) => {
      const chapters = page.locator('.writing-images-chapters');
      await chapters.getByRole('checkbox', { name: '选择全部章节', exact: true }).uncheck();
      assert.equal(await page.getByRole('button', { name: '为所选章节自动配图', exact: true }).isDisabled(), true);
      await chapters.getByRole('checkbox', { name: '选择全部章节', exact: true }).check();
      await page.getByLabel('配图范围', { exact: true }).selectOption('library');
      assert.equal(await page.getByRole('button', { name: '为所选章节自动配图', exact: true }).isDisabled(), true);
      await page.getByRole('checkbox', { name: '确认自动图库图片仅作配图', exact: true }).check();
      assert.equal((await mutations()).length, 0);
      await page.getByRole('button', { name: '为所选章节自动配图', exact: true }).click();
      const batch = (await calls()).find(call => call.name === 'automatic-illustrations');
      assert.deepEqual(batch.body.sectionIds, ['chapter-a', 'chapter-b']);
      assert.equal(batch.body.confirmLibrarySource, true);
      await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
      await page.getByRole('button', { name: '在第 2 段后插入图片', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
      await dialog.getByLabel('图片范围', { exact: true }).selectOption('library');
      const card = dialog.locator('.writing-image-catalog-card').filter({ hasText: '图库设备资料' });
      await card.waitFor();
      assert.equal(await card.getByRole('button', { name: '插入图片', exact: true }).isDisabled(), true);
      await card.getByRole('checkbox', { name: '确认图库图片仅作配图', exact: true }).check();
      await card.getByRole('button', { name: '插入图片', exact: true }).click();
      const manual = (await calls()).find(call => call.name === 'manual-illustration');
      assert.equal(manual.body.assetId, 'asset-library-a');
      assert.equal(manual.body.confirmLibrarySource, true);
      assert.equal(manual.body.paragraphIndex, 1);
      assert.equal(Object.hasOwn(manual.body, 'modelId'), false);
      await assertPlacement(page, 'figure-asset-library-a-chapter-a', titles[0]);
    });

    await run(theme, 'locked-chapter-and-empty-body-navigation', async ({ page, snapshot, mutations }) => {
      const before = await snapshot();
      await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
      await page.locator('.writing-images-chapters button').filter({ hasText: titles[2] }).click();
      assert.equal(await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).isDisabled(), true);
      await page.locator('.writing-images-chapters button').filter({ hasText: titles[3] }).click();
      assert.equal(await page.locator('.writing-image-insert-gap').count(), 0);
      await page.getByText('本章尚无正文', { exact: true }).waitFor();
      assert.deepEqual(await snapshot(), before);
      assert.equal((await mutations()).length, 0);
    });

    for (const scenario of ['locked-figure', 'locked-chapter-figure']) await run(theme, scenario, async ({ page, snapshot, mutations }) => {
      const before = await snapshot();
      assert.equal(await page.getByRole('button', { name: '跳过，本文不配图', exact: true }).isDisabled(), true);
      await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
      assert.equal(await page.getByRole('button', { name: '本文不配图', exact: true }).isDisabled(), true);
      assert.deepEqual(await snapshot(), before);
      assert.equal((await mutations()).length, 0);
    }, { scenario });

    await run(theme, 'stale-image-must-recheck-before-complete', async ({ page, mutations }) => {
      await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
      assert.equal(await page.getByRole('button', { name: '完成配图', exact: true }).isDisabled(), true);
      await page.getByRole('button', { name: '保存配图修改', exact: true }).click();
      assert.equal(await page.getByRole('button', { name: '完成配图', exact: true }).isEnabled(), true);
      assert.deepEqual((await mutations()).map(call => call.name), ['update-image']);
    }, { scenario: 'stale' });

    for (const scenario of ['dirty', 'remote', 'pending']) {
      await run(theme, `guard-${scenario}`, async ({ page, mutations }) => {
        assert.equal(await page.getByRole('button', { name: '为所选章节自动配图', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '跳过，本文不配图', exact: true }).isDisabled(), true);
        await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
        assert.equal(await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '完成配图', exact: true }).isDisabled(), true);
        assert.equal((await mutations()).length, 0);
      }, { scenario });
    }

    await run(theme, 'empty-image-completion', async ({ page, calls }) => {
      await page.getByRole('button', { name: '跳过，本文不配图', exact: true }).click();
      const completed = (await calls()).find(call => call.name === 'complete-images');
      assert.equal(completed.body.skip, true);
    });

    for (const width of [768, 390]) {
      await run(theme, `responsive-${width}`, async ({ page, mutations }) => {
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'No horizontal page overflow');
        const footer = page.locator('.writing-images-footer');
        await footer.scrollIntoViewIfNeeded();
        for (const name of ['切换手动配图', '为所选章节自动配图', '跳过，本文不配图']) assert(await footer.getByRole('button', { name, exact: true }).isVisible());
        await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
        await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
        await dialog.locator('.writing-image-catalog-card').first().waitFor();
        const bounds = await dialog.boundingBox();
        assert(bounds.x >= -1 && bounds.x + bounds.width <= width + 1, 'Image picker fits the viewport');
        assert.equal((await mutations()).length, 0);
        await page.screenshot({ path: path.join(out, `${theme}-${width}-picker.png`), animations: 'disabled' });
      }, { width, height: 820 });
    }
  }

  for (const status of ['dirty', 'remote', 'pending', 'automatic']) {
    await run('jade', `skipped-overview-blocked-${status}`, async ({ page, snapshot, calls }) => {
      const before = await snapshot();
      assert.equal(await page.locator('[data-writing-start-images]').isDisabled(), true);
      assert.deepEqual(await snapshot(), before);
      assert.deepEqual(await calls(), []);
    }, { scenario: `overview-${status}` });
  }

  await run('civic', 'manual-success-returns-to-target-chapter-after-pending-navigation', async ({ page, state, snapshot }) => {
    const before = await snapshot();
    await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
    await page.locator('.writing-images-chapters button').filter({ hasText: titles[1] }).click();
    await page.getByRole('button', { name: '在第 2 段后插入图片', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
    await dialog.locator('.writing-image-catalog-card').first().waitFor();
    await page.evaluate(() => window.__imageModes.deferManual(true));
    await dialog.locator('.writing-image-catalog-card').first().getByRole('button', { name: '插入图片', exact: true }).click();
    await page.waitForFunction(() => window.__imageModes.state.busy === 'manual-image');
    await page.evaluate(title => [...document.querySelectorAll('.writing-images-chapters button')].find(node => node.textContent.includes(title)).click(), titles[0]);
    assert.match(await page.locator('.writing-images-chapters button[aria-current="location"]').innerText(), new RegExp(titles[0]));
    await page.evaluate(() => window.__imageModes.releaseManual());
    await assertPlacement(page, 'figure-asset-reference-a-chapter-b', titles[1]);
    assert.deepEqual((await snapshot()).sections, before.sections);
    assert.equal((await state()).project.runs.length, 0);
  });

  await run('civic', 'manual-failure-keeps-image-chooser-and-caption', async ({ page, snapshot }) => {
    const before = await snapshot();
    await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
    await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
    const card = dialog.locator('.writing-image-catalog-card').first();
    await card.waitFor();
    await card.getByRole('textbox', { name: '文章图注', exact: true }).fill('作者保留的图注');
    await page.evaluate(() => window.__imageModes.failManual(true));
    await card.getByRole('button', { name: '插入图片', exact: true }).click();
    await page.waitForFunction(() => !window.__imageModes.state.busy && window.__imageModes.calls.some(call => call.name === 'manual-illustration'));
    assert.equal(await dialog.isVisible(), true);
    assert.equal(await card.getByRole('textbox', { name: '文章图注', exact: true }).inputValue(), '作者保留的图注');
    assert.deepEqual(await snapshot(), before);
  });

  await run('civic', 'late-manual-result-preserves-changed-body-and-chooser', async ({ page, snapshot, state }) => {
    await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
    await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
    await dialog.locator('.writing-image-catalog-card').first().waitFor();
    await page.evaluate(() => window.__imageModes.deferManual(true));
    await dialog.locator('.writing-image-catalog-card').first().getByRole('button', { name: '插入图片', exact: true }).click();
    await page.waitForFunction(() => window.__imageModes.state.busy === 'manual-image');
    await page.evaluate(() => window.__imageModes.setProject(before => ({ ...before, sections: before.sections.map(section => section.id === 'chapter-a' ? { ...section, content: '保存配图期间作者改变的正文。', contentHash: 'changed-during-manual' } : section) })));
    await page.evaluate(() => window.__imageModes.releaseManual());
    await page.waitForFunction(() => window.__imageModes.state.remoteChanged && !window.__imageModes.state.busy);
    assert.equal(await dialog.isVisible(), true);
    assert.equal((await snapshot()).sections[0].content, '保存配图期间作者改变的正文。');
    assert.equal((await snapshot()).illustrations.length, 0);
    assert.equal(await page.locator('[data-writing-figure-id]').count(), 0);
    assert.equal((await state()).project.runs.length, 0);
  });

  await run('civic', 'late-manual-result-does-not-navigate-switched-project', async ({ page, snapshot }) => {
    await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
    await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
    await dialog.locator('.writing-image-catalog-card').first().waitFor();
    await page.evaluate(() => window.__imageModes.deferManual(true));
    await dialog.locator('.writing-image-catalog-card').first().getByRole('button', { name: '插入图片', exact: true }).click();
    await page.waitForFunction(() => window.__imageModes.state.busy === 'manual-image');
    await page.evaluate(() => window.__imageModes.setProject(before => ({ ...before, id: 'other-manual-project', title: '切换后的文稿', illustrations: [] })));
    await page.evaluate(() => window.__imageModes.releaseManual());
    await page.waitForFunction(() => !window.__imageModes.state.busy);
    assert.equal((await snapshot()).id, 'other-manual-project');
    assert.equal((await snapshot()).illustrations.length, 0);
    assert.equal(await page.getByRole('dialog', { name: '选择配图', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '切换手动配图', exact: true }).isVisible(), true);
  });

  await run('civic', 'late-catalog-scope-response-isolation', async ({ page, snapshot, mutations }) => {
    const before = await snapshot();
    await page.evaluate(() => window.__imageModes.deferCatalog(true));
    await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
    await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
    await page.waitForFunction(() => window.__imageModes.requests.length === 1);
    await dialog.getByLabel('图片范围', { exact: true }).selectOption('library');
    await page.waitForFunction(() => window.__imageModes.requests.length === 2);
    await page.evaluate(() => window.__imageModes.releaseCatalog());
    await page.waitForFunction(() => document.querySelectorAll('dialog[open] .writing-image-catalog-card').length === 3);
    assert.equal(await dialog.getByLabel('图片范围', { exact: true }).inputValue(), 'library');
    assert.equal(await dialog.locator('.writing-image-catalog-card').filter({ hasText: '图库设备资料' }).count(), 1);
    assert.deepEqual(await snapshot(), before);
    assert.equal((await mutations()).length, 0);
  });

  await run('civic', 'late-catalog-project-switch-isolation', async ({ page, mutations }) => {
    await page.evaluate(() => window.__imageModes.deferCatalog(true));
    await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
    await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).click();
    await page.waitForFunction(() => window.__imageModes.requests.length === 1);
    await page.evaluate(() => window.__imageModes.setProject(before => ({ ...before, id: 'switched-project', title: '切换后的独立文稿', illustrations: [] })));
    await page.getByRole('button', { name: '切换手动配图', exact: true }).waitFor();
    await page.evaluate(() => window.__imageModes.releaseCatalog());
    assert.equal(await page.getByRole('dialog', { name: '选择配图', exact: true }).count(), 0);
    assert.equal((await mutations()).length, 0);
    assert.equal(await page.evaluate(() => window.__imageModes.state.project.id), 'switched-project');
  });

  await run('civic', 'open-target-body-change-blocks-insertion', async ({ page, mutations }) => {
    await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
    await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
    await dialog.locator('.writing-image-catalog-card').first().waitFor();
    await page.evaluate(() => window.__imageModes.setProject(before => ({ ...before, sections: before.sections.map(section => section.id === 'chapter-a' ? { ...section, content: '作者已经改变的正文段落。', contentHash: 'new-hash' } : section) })));
    assert.equal(await dialog.locator('.writing-image-catalog-card').getByRole('button', { name: '插入图片', exact: true }).first().isDisabled(), true);
    assert.match(await dialog.innerText(), /正文已变化/);
    assert.equal((await mutations()).length, 0);
  });

  await run('civic', 'hook-poll-adopts-completed-image-batch', async ({ page, state }) => {
    const checkbox = page.getByRole('checkbox', { name: `选择章节：${titles[1]}`, exact: true });
    await checkbox.uncheck();
    const before = (await state()).project.sections;
    await page.getByRole('button', { name: '为所选章节自动配图', exact: true }).click();
    await page.waitForFunction(() => window.__imageModes.state.project.runs.some(run => run.kind === 'image_batch' && run.status === 'running'));
    await page.waitForFunction(() => window.__imageModes.state.project.illustrations.length === 1 && !window.__imageModes.state.pending);
    const after = await state();
    assert.equal(after.project.runs[0].status, 'succeeded');
    assert.equal(after.project.version, 9);
    assert.equal(after.dirty, false); assert.equal(after.remoteChanged, false);
    assert.deepEqual(after.project.sections, before);
    assert.equal(after.error, '');
    assert.equal(after.requests.filter(request => request.method !== 'GET').length, 1);
  }, { hook: true });

  await run('civic', 'hook-poll-preserves-draft-while-image-batch-finishes', async ({ page, state }) => {
    await page.getByRole('checkbox', { name: `选择章节：${titles[1]}`, exact: true }).uncheck();
    await page.getByRole('button', { name: '为所选章节自动配图', exact: true }).click();
    await page.waitForFunction(() => window.__imageModes.state.pending && !window.__imageModes.state.busy);
    await page.evaluate(() => window.__imageModes.setProject(before => ({ ...before, sections: before.sections.map(section => section.id === 'chapter-a' ? { ...section, content: '轮询期间必须保留的人工草稿。' } : section) })));
    await page.waitForFunction(() => window.__imageModes.state.remoteChanged && !window.__imageModes.state.pending);
    const after = await state();
    assert.equal(after.project.sections[0].content, '轮询期间必须保留的人工草稿。');
    assert.equal(after.project.illustrations.length, 0, 'A dirty draft retains its own illustration snapshot until explicit reload');
    assert.equal(after.project.runs[0].status, 'succeeded');
    assert.equal(after.dirty, true);
    assert.equal(after.requests.filter(request => request.method !== 'GET').length, 1);
    assert.equal(after.error, '');
  }, { hook: true });

  await run('civic', 'hook-manual-insertion-returns-directly-without-job-or-model', async ({ page, state }) => {
    const before = (await state()).project.sections;
    await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
    await page.locator('.writing-images-chapters button').filter({ hasText: titles[1] }).click();
    await page.getByRole('button', { name: '在第 2 段后插入图片', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
    await dialog.locator('.writing-image-catalog-card').first().waitFor();
    await dialog.locator('.writing-image-catalog-card').first().getByRole('button', { name: '插入图片', exact: true }).click();
    await assertPlacement(page, 'figure-asset-reference-a-chapter-b', titles[1]);
    const after = await state();
    assert.equal(after.project.illustrations.length, 1);
    assert.equal(after.project.illustrations[0].reviewMethod, 'human');
    assert.equal(after.project.runs.length, 0);
    assert.equal(after.pending, false); assert.equal(after.dirty, false); assert.equal(after.remoteChanged, false);
    assert.deepEqual(after.project.sections, before);
    const mutations = after.requests.filter(request => request.method !== 'GET');
    assert.equal(mutations.length, 1);
    assert.match(mutations[0].url, /\/manual-image$/);
    assert.equal(Object.hasOwn(mutations[0].body, 'modelId'), false);
    assert.equal(after.error, '');
  }, { hook: true });

  await run('civic', 'hook-manual-response-preserves-concurrent-draft-without-jump', async ({ page, state }) => {
    await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
    await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
    await dialog.locator('.writing-image-catalog-card').first().waitFor();
    await page.evaluate(() => window.__imageModes.deferManual(true));
    await dialog.locator('.writing-image-catalog-card').first().getByRole('button', { name: '插入图片', exact: true }).click();
    await page.waitForFunction(() => window.__imageModes.state.busy === 'manual-image');
    await page.evaluate(() => window.__imageModes.setProject(before => ({ ...before, sections: before.sections.map(section => section.id === 'chapter-a' ? { ...section, content: '手动配图期间保留的人工草稿。' } : section) })));
    await page.waitForFunction(() => window.__imageModes.state.project.sections[0].content === '手动配图期间保留的人工草稿。' && window.__imageModes.state.dirty);
    await page.evaluate(() => window.__imageModes.releaseManual());
    await page.waitForFunction(() => window.__imageModes.state.remoteChanged && !window.__imageModes.state.busy);
    const after = await state();
    assert.equal(after.project.sections[0].content, '手动配图期间保留的人工草稿。');
    assert.equal(after.project.illustrations.length, 0);
    assert.equal(after.dirty, true);
    assert.equal(after.pending, false);
    assert.equal(await dialog.isVisible(), true);
    assert.equal(await page.locator('[data-writing-figure-id]').count(), 0);
    assert.equal(after.requests.filter(request => request.method !== 'GET').length, 1);
  }, { hook: true });

  await run('civic', 'hook-manual-api-failure-preserves-modal-and-image-selection', async ({ page, state }) => {
    const before = (await state()).project;
    await page.getByRole('button', { name: '切换手动配图', exact: true }).click();
    await page.getByRole('button', { name: '在第 1 段后插入图片', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '选择配图', exact: true });
    await dialog.locator('.writing-image-catalog-card').first().waitFor();
    await page.evaluate(() => window.__imageModes.failManual(true));
    await dialog.locator('.writing-image-catalog-card').first().getByRole('button', { name: '插入图片', exact: true }).click();
    await page.waitForFunction(() => !window.__imageModes.state.busy && window.__imageModes.state.error);
    assert.equal(await dialog.isVisible(), true);
    assert.deepEqual((await state()).project.sections, before.sections);
    assert.equal((await state()).project.illustrations.length, 0);
    assert.equal((await state()).project.runs.length, 0);
  }, { hook: true });
  result.status = 'passed';
} catch (error) {
  result.status = 'failed'; result.error = String(error); result.stack = error.stack + '\n' + (error.cause?.stack || ''); process.exitCode = 1;
} finally {
  await browser.close(); await fs.writeFile(path.join(out, 'acceptance.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ status: result.status, cases: result.cases.length, error: result.error, stack: result.stack, realApiCalls: 0, realModelCalls: 0 }));
}
