// Production WritingWorkspace + useWriting, with the existing synthetic HTTP harness.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:4173').replace(/\/$/, '');
const output = path.resolve(process.argv[3] || 'output/automatic-fast-defaults/browser');
await fs.mkdir(output, { recursive: true });
const result = { status: 'running', realApiCalls: 0, realModelCalls: 0, cases: [] };
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });

async function open(theme, scenario = 'blank') {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const leaked = [], errors = [];
  await context.route('**/api/**', route => { leaked.push(route.request().url()); return route.abort(); });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=${scenario}`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__preparation && !window.__preparation.state.loading);
  const group = name => page.getByRole('group', { name, exact: true });
  async function pressed(name, choice, expected) {
    assert.equal(await group(name).getByRole('button', { name: choice, exact: true }).getAttribute('aria-pressed'), String(expected), `${theme} ${name} ${choice}`);
  }
  async function clean() {
    assert.deepEqual(leaked, [], 'A real API boundary was reached');
    assert.deepEqual(errors, [], 'Browser errors');
    assert.deepEqual(await page.evaluate(() => window.__preparation.unexpected), [], 'Unexpected synthetic request');
  }
  return { context, page, group, pressed, clean };
}

try {
  for (const theme of ['jade', 'glass', 'civic', 'demo']) {
    const { context, page, group, pressed, clean } = await open(theme);
    try {
      await pressed('自动配图', '否', true);
      await pressed('跳过检查校验', '是', true);
      await page.locator('#writing-topic').fill('隔离验收：新默认与保存恢复');
      await group('跳过检查校验').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `${theme}-defaults.png`) });
      await group('自动配图').getByRole('button', { name: '是', exact: true }).click();
      await group('跳过检查校验').getByRole('button', { name: '否', exact: true }).click();
      assert(await page.evaluate(() => window.__preparation.hook.save()));
      let saved = await page.evaluate(() => window.__preparation.savedProjects[0]);
      assert.equal(saved.brief.automaticOptions.includeImages, true);
      assert.equal(saved.brief.automaticOptions.skipReview, false);
      await page.evaluate(() => window.__preparation.hook.reload());
      assert.equal(await page.evaluate(() => window.__preparation.state.error), '');
      await pressed('自动配图', '是', true);
      await pressed('跳过检查校验', '否', true);
      const writesBefore = await page.evaluate(() => window.__preparation.requests.filter(r => r.method !== 'GET').length);
      await group('跳过检查校验').getByRole('button', { name: '是', exact: true }).click();
      await group('跳过检查校验').getByRole('button', { name: '否', exact: true }).click();
      assert(await page.evaluate(() => window.__preparation.hook.save()));
      assert.equal(await page.evaluate(() => window.__preparation.requests.filter(r => r.method !== 'GET').length), writesBefore, 'A reverted skip option must not create a save/version');
      await clean();
      result.cases.push({ theme, scenario: 'defaults-toggle-save-reload-noop', passed: true });
    } finally { await context.close(); }

    for (const scenario of ['v2-default', 'v2-readonly', 'v2-legacy-restricted', 'v2-legacy-unrestricted']) {
      const { context, page, group, pressed, clean } = await open(theme, scenario);
      try {
        await page.waitForFunction(() => window.__preparation.state.project.id === 'writing-options-v2');
        await pressed('自动配图', scenario.startsWith('v2-legacy-') ? '否' : '是', true);
        await pressed('跳过检查校验', '否', true);
        if (scenario === 'v2-readonly') {
          for (const name of ['自动配图', '跳过检查校验']) {
            for (const button of await group(name).getByRole('button').all()) assert(await button.isDisabled());
          }
        }
        if (scenario.startsWith('v2-legacy-')) {
          const modify = page.locator('[data-writing-modify-step="brief"]');
          if (await modify.count()) await modify.click();
          for (const name of ['自动配图', '跳过检查校验']) {
            await group(name).getByRole('button', { name: '是', exact: true }).click();
            await group(name).getByRole('button', { name: '否', exact: true }).click();
          }
          assert(await page.evaluate(() => window.__preparation.hook.save()));
          const options = await page.evaluate(() => window.__preparation.savedProjects[0].brief.automaticOptions);
          assert.equal(options.restrictToSelectedSources, scenario === 'v2-legacy-restricted');
          assert(!Object.hasOwn(options, 'useSelectedSources'), 'Picture/review changes must not migrate legacy source settings');
        }
        assert.equal(await page.evaluate(() => window.__preparation.requests.filter(r => r.method !== 'GET').length), 0, 'Opening a saved record must not write');
        await group('跳过检查校验').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, `${theme}-${scenario}.png`) });
        await clean();
        result.cases.push({ theme, scenario, passed: true });
      } finally { await context.close(); }
    }
  }
  for (const words of [9900, 10000]) {
    for (const skipReview of [true, false]) {
      const { context, page, group, clean } = await open('civic');
      try {
        await page.locator('#writing-topic').fill(`隔离任务 ${words} 字`);
        await page.locator('#writing-length').selectOption('custom');
        await page.locator('#writing-custom-length').fill(String(words));
        if (!skipReview) await group('跳过检查校验').getByRole('button', { name: '否', exact: true }).click();
        await page.getByRole('button', { name: '开始智能化写作', exact: true }).click();
        await page.waitForFunction(() => window.__preparation.requests.some(r => /\/automatic$/.test(r.path)));
        const state = await page.evaluate(() => ({ saved: window.__preparation.savedProjects[0], state: window.__preparation.state }));
        assert.equal(state.saved.brief.targetWords, words);
        assert.equal(state.saved.brief.automaticOptions.includeImages, false);
        assert.equal(state.saved.brief.automaticOptions.skipReview, skipReview);
        for (const name of ['自动配图', '跳过检查校验']) {
          for (const button of await group(name).getByRole('button').all()) assert(await button.isDisabled());
        }
        await clean();
        result.cases.push({ scenario: 'start-serialized-options', words, skipReview, passed: true });
      } finally { await context.close(); }
    }
  }
  result.status = 'passed';
} catch (error) {
  result.status = 'failed'; result.error = String(error); result.stack = error.stack;
  process.exitCode = 1;
} finally {
  await browser.close();
  await fs.writeFile(path.join(output, 'acceptance.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ status: result.status, cases: result.cases.length, error: result.error, stack: result.stack, realApiCalls: 0, realModelCalls: 0 }));
}
