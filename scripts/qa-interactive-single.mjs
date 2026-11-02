// Real workspace/hook with synthetic HTTP boundaries; no real documents/models.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = (process.argv[2] || 'http://127.0.0.1:4173').replace(/\/$/, '');
const output = path.resolve(process.argv[3] || 'output/interactive-single/browser');
await fs.mkdir(output, { recursive: true });
const result = { status: 'running', realApiCalls: 0, realModelCalls: 0, cases: [] };
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
async function open(theme, scenario) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const leaked = [], errors = [];
  await context.route('**/api/**', route => { leaked.push(route.request().url()); return route.abort(); });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=${scenario}`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__preparation?.state.project.id === 'writing-interactive-short' && !window.__preparation.state.loading);
  const state = () => page.evaluate(() => window.__preparation.state);
  const requests = () => page.evaluate(() => window.__preparation.requests.filter(request => request.method !== 'GET'));
  const saved = () => page.evaluate(() => window.__preparation.savedProjects[0]);
  const idle = () => page.waitForFunction(() => !window.__preparation.state.busy);
  const clean = async () => {
    assert.deepEqual(leaked, [], 'Real API boundary reached');
    assert.deepEqual(errors, [], 'Browser errors');
    assert.deepEqual(await page.evaluate(() => window.__preparation.unexpected), [], 'Unexpected synthetic request');
    assert.equal((await state()).error, '');
  };
  return { context, page, state, saved, requests, idle, clean };
}
try {
  for (const theme of ['jade', 'glass', 'civic', 'demo']) {
    for (const scenario of ['interactive-short', 'interactive-boundary', 'interactive-capacity']) {
      const test = await open(theme, scenario), { context, page, state, saved, requests, idle, clean } = test;
      try {
        if (scenario !== 'interactive-boundary') await page.waitForFunction(() => !!window.__preparation.hook.generationPlan);
        assert.equal((await requests()).length, 0, 'Opening must not write');
        if (scenario === 'interactive-capacity') {
          assert(await page.getByRole('button', { name: '确认纲要并生成全文', exact: true }).isDisabled());
          assert(await page.getByText(/当前模型容量不足/).count());
          await page.getByRole('button', { name: '仅保存并确认纲要', exact: true }).click();
        } else {
          await page.getByRole('button', { name: scenario === 'interactive-short' ? '确认纲要并生成全文' : '保存并确认纲要', exact: true }).click();
        }
        await page.waitForFunction(() => window.__preparation.state.stage === 'editor');
        await idle();
        const writes = await requests(), project = await saved();
        const whole = writes.filter(request => request.path.endsWith('/generate-document'));
        assert.equal(whole.length, scenario === 'interactive-short' ? 1 : 0);
        if (whole.length) {
          assert.equal(whole[0].body.confirmOutline, true);
          assert(project.sections.every(section => section.content.trim()));
          assert.equal(project.chapterConfirmationPolicy, 'chapter-confirmation-v1');
          assert(project.workflowPendingSteps.includes('body'));
        } else {
          assert(writes.some(request => request.path.endsWith('/confirm-outline')));
          assert(project.sections.every(section => !section.content.trim()));
          assert.equal(project.chapterConfirmationPolicy, undefined);
        }
        await clean();
        result.cases.push({ theme, scenario, passed: true });
      } finally { await context.close(); }
    }
    {
      const { context, page, state, saved, requests, idle, clean } = await open(theme, 'interactive-generated');
      try {
        const chapter = n => page.getByRole('button', { name: new RegExp(`^第 ${n} 章：`) });
        const approval = () => page.getByRole('button', { name: '保存并确认本章', exact: true });
        await chapter(2).click(); await chapter(1).click();
        assert.equal((await requests()).length, 0, 'Chapter navigation must not write');
        const initialVersion = (await saved()).version;
        await approval().click();
        await page.waitForFunction(() => window.__preparation.state.project.activeSectionId === 'section_interactive_second');
        await idle();
        assert.equal((await saved()).version, initialVersion, 'Approval must preserve writing version and review');
        assert((await saved()).sections[0].chapterConfirmed);
        await approval().click(); await idle();
        assert((await saved()).sections.every(section => section.chapterConfirmed));
        assert.equal((await saved()).version, initialVersion);
        await chapter(1).click();
        await page.getByRole('group', { name: '章节正文显示方式' }).getByRole('button', { name: '编辑', exact: true }).click();
        const editor = page.getByRole('textbox', { name: '章节正文', exact: true });
        const original = await editor.inputValue(), priorWrites = (await requests()).length;
        await editor.fill(original + '这是一处待保存的修改。');
        assert(await approval().isEnabled());
        assert(await page.locator('.writing-step-footer').getByText(/1 \/ 2 章已确认/).count());
        await editor.fill(original);
        assert(await page.getByRole('button', { name: '本章已确认', exact: true }).isDisabled());
        assert(await page.evaluate(() => window.__preparation.hook.save()));
        assert.equal((await requests()).length, priorWrites, 'Reverted content must not write');
        await editor.fill(original + '这是保留的人工修订。');
        await approval().click();
        await page.waitForFunction(() => window.__preparation.state.project.activeSectionId === 'section_interactive_second');
        await idle();
        const edited = await saved(), newWrites = (await requests()).slice(priorWrites);
        assert.equal(edited.version, initialVersion + 1, 'Only actual content save advances writing version');
        assert(edited.sections[0].content.endsWith('这是保留的人工修订。'));
        assert(edited.sections[0].manual);
        assert(edited.sections.every(section => section.chapterConfirmed), 'Unrelated chapter approval must survive');
        assert.equal(newWrites.filter(request => request.method === 'PATCH').length, 1);
        assert.equal(newWrites.filter(request => request.path.endsWith('/confirm')).length, 1);
        const countBeforeReload = (await requests()).length;
        await page.evaluate(() => window.__preparation.hook.reload()); await idle();
        assert((await state()).project.sections.every(section => section.chapterConfirmed));
        assert.equal((await requests()).length, countBeforeReload, 'Reload must not write');
        await page.screenshot({ path: path.join(output, `${theme}-chapter-confirmation.png`) });
        await page.getByRole('button', { name: '确认正文，文章配图', exact: true }).click();
        await page.waitForFunction(() => !window.__preparation.state.project.workflowPendingSteps.includes('body'));
        await idle();
        assert.equal((await saved()).version, edited.version, 'Final approval preserves content version');
        assert((await saved()).sections.every(section => section.chapterConfirmed));
        assert.equal((await requests()).filter(request => /generate|review|citation/.test(request.path)).length, 0, 'Author approval must not generate or check content');
        await clean();
        result.cases.push({ theme, scenario: 'chapter-save-confirm-next-revert-reload-final', passed: true });
      } finally { await context.close(); }
    }
    {
      const { context, page, saved, requests, clean } = await open(theme, 'interactive-legacy');
      try {
        assert.equal(await page.getByRole('button', { name: '保存并确认本章', exact: true }).count(), 0);
        assert.equal((await saved()).chapterConfirmationPolicy, undefined);
        assert.equal((await requests()).length, 0, 'Opening old documents must not migrate');
        await clean(); result.cases.push({ theme, scenario: 'legacy-no-migration', passed: true });
      } finally { await context.close(); }
    }
  }
  result.status = 'passed';
} catch (error) {
  result.status = 'failed'; result.error = String(error); result.stack = error.stack; process.exitCode = 1;
} finally {
  await browser.close();
  await fs.writeFile(path.join(output, 'acceptance.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ status: result.status, cases: result.cases.length, error: result.error, stack: result.stack, realApiCalls: 0, realModelCalls: 0 }));
}
