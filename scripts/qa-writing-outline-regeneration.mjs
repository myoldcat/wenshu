import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:4185';
const out = path.resolve(process.argv[3] || 'output/writing-outline-regeneration/browser');
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const result = { status: 'running', cases: [], realApiCalls: 0, realModelCalls: 0 };
const variants = [
  { id: 'research-report-problem', count: 5 },
  { id: 'research-report-experience', count: 4 },
  { id: 'research-report-decision', count: 3 },
];
const authorStructure = '全文仅分为“治理现状”“改进路径”两章，不要章外引言；按作者指定顺序展开。';

async function open(theme, scenario) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, reducedMotion: 'reduce' });
  const leaks = [], errors = [];
  await context.route('**/api/**', route => { leaks.push(route.request().url()); return route.abort(); });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=${scenario}`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__preparation && !window.__preparation.state.loading && window.__preparation.state.project.id);
  const state = () => page.evaluate(() => window.__preparation.state);
  const writes = () => page.evaluate(() => window.__preparation.requests.filter(request => request.method !== 'GET'));
  const generated = async () => (await writes()).filter(request => request.path.endsWith('/generate-outline'));
  const clean = async () => {
    assert.deepEqual(leaks, [], 'Every API request stays inside the isolated mock boundary');
    assert.deepEqual(errors, [], 'No browser runtime errors');
    assert.deepEqual(await page.evaluate(() => window.__preparation.unexpected), []);
    assert.equal((await state()).error, '');
  };
  return { context, page, state, writes, generated, clean };
}

try {
  for (const theme of ['jade', 'glass', 'civic', 'demo']) {
    for (const variant of variants) {
      const { context, page, state, writes, generated, clean } = await open(theme, 'outline-regen-existing');
      try {
        const before = (await state()).project.sections;
        await page.locator('#writing-structure-template').selectOption(variant.id);
        assert.deepEqual((await state()).project.sections, before, 'Selecting a template never overwrites chapters');
        assert.equal((await writes()).length, 0, 'Selecting a template does not save or generate');
        await page.locator('[data-writing-step="outline"]').click();
        await page.locator('.writing-outline-wrap').waitFor();
        assert.deepEqual((await state()).project.sections, before, 'Browsing the old outline preserves it');
        assert.equal((await generated()).length, 0);
        assert.match(await page.locator('.writing-outline-bottom').innerText(), /切换模板或修改要求不会替换原纲要/);
        const regenerate = page.getByRole('button', { name: '重新生成纲要', exact: true });
        assert.equal(await regenerate.isDisabled(), true, 'A changed or unconfirmed brief must first be confirmed');
        assert.match(await regenerate.getAttribute('title'), /主题设定/);
        await page.locator('[data-writing-step="brief"]').click();
        await page.getByRole('button', { name: '确认主题，筛选资料', exact: true }).click();
        await page.locator('.writing-materials-page').waitFor();
        await page.getByRole('button', { name: '确认资料，继续写作', exact: true }).click();
        await page.locator('.writing-outline-wrap').waitFor();
        assert.equal((await generated()).length, 0, 'Confirming changed inputs never replaces an existing outline automatically');
        assert.deepEqual((await state()).project.sections.map(section => section.title), before.map(section => section.title));
        assert.equal(await regenerate.isEnabled(), true);
        await regenerate.click();
        await page.waitForFunction(count => !window.__preparation.state.busy && window.__preparation.state.project.sections.length === count
          && window.__preparation.state.project.sections[0].id.startsWith('section_outline_regenerated_'), variant.count);
        const requests = await generated();
        assert.equal(requests.length, 1, 'One explicit click makes exactly one outline request');
        assert.equal(requests[0].savedBrief.structureTemplate, variant.id, 'The new template is saved before generation');
        assert.equal(requests[0].savedBrief.templateSections.length, variant.count);
        assert.deepEqual(requests[0].replacedSections.map(section => section.title), before.map(section => section.title));
        assert.equal((await state()).project.outlineConfirmed, false);
        if (variant.id === variants[0].id) {
          await page.locator('.writing-outline-bottom').scrollIntoViewIfNeeded();
          await page.screenshot({ path: path.join(out, `${theme}-explicit-regeneration.png`), animations: 'disabled' });
        }
        await clean(); result.cases.push({ theme, scenario: `${variant.id}-explicit-only-regeneration`, passed: true });
      } finally { await context.close(); }
    }

    {
      const { context, page, state, writes, generated, clean } = await open(theme, 'outline-regen-confirmed');
      try {
        const regenerate = page.getByRole('button', { name: '重新生成纲要', exact: true });
        assert.equal(await regenerate.isDisabled(), true, 'Confirmed outlines open read-only');
        assert.match(await regenerate.getAttribute('title'), /修改/);
        const before = (await state()).project.sections;
        await page.locator('[data-writing-modify-step="outline"]').click();
        assert.equal(await regenerate.isEnabled(), true);
        assert.equal((await state()).project.outlineConfirmed, true, 'Entering edit mode retains confirmed state');
        assert.equal((await writes()).length, 0, 'Entering edit mode never saves or generates');
        await regenerate.click();
        await page.waitForFunction(() => !window.__preparation.state.busy && window.__preparation.state.project.sections[0].id.startsWith('section_outline_regenerated_'));
        assert.equal((await generated()).length, 1);
        assert.deepEqual((await generated())[0].replacedSections.map(section => section.title), before.map(section => section.title));
        await clean(); result.cases.push({ theme, scenario: 'confirmed-outline-requires-explicit-modify', passed: true });
      } finally { await context.close(); }
    }

    for (const protection of ['body', 'manual', 'locked']) {
      const { context, page, state, writes, generated, clean } = await open(theme, `outline-regen-${protection}`);
      try {
        const regenerate = page.getByRole('button', { name: '重新生成纲要', exact: true });
        assert.equal(await regenerate.isDisabled(), true);
        assert.match(await regenerate.getAttribute('title'), /正文|人工|锁定/);
        await page.evaluate(() => window.__preparation.hook.setProject(project => ({ ...project,
          sections: project.sections.map(section => ({ ...section, content: '', manual: false, locked: false })) })));
        assert.equal(await regenerate.isDisabled(), true, 'Unsaved clearing cannot bypass protection of saved content');
        assert.equal((await generated()).length, 0); assert.equal((await writes()).length, 0);
        assert((await state()).savedProject.sections.some(section => section.content || section.manual || section.locked));
        await clean(); result.cases.push({ theme, scenario: `saved-${protection}-protection-survives-unsaved-clearing`, passed: true });
      } finally { await context.close(); }
    }

    {
      const { context, page, writes, generated, clean } = await open(theme, 'outline-regen-running');
      try {
        const regenerate = page.getByRole('button', { name: '重新生成纲要', exact: true });
        assert.equal(await regenerate.isDisabled(), true);
        assert.match(await regenerate.getAttribute('title'), /正在|进行/);
        assert.equal((await generated()).length, 0); assert.equal((await writes()).length, 0);
        await clean(); result.cases.push({ theme, scenario: 'active-automatic-task-blocks-regeneration', passed: true });
      } finally { await context.close(); }
    }

    {
      const { context, page, state, generated, clean } = await open(theme, 'outline-regen-override');
      try {
        const regenerate = page.getByRole('button', { name: '重新生成纲要', exact: true });
        assert.equal(await regenerate.isEnabled(), true, 'An author-defined two-chapter outline is valid');
        assert.equal((await state()).project.sections.length, 2);
        assert.doesNotMatch(await page.locator('.writing-outline-wrap').innerText(), /章节数量.*(?:错误|不符)|章数不符|模板.*不一致/);
        await regenerate.click();
        await page.waitForFunction(() => !window.__preparation.state.busy && window.__preparation.state.project.sections[0].id.startsWith('section_outline_regenerated_'));
        assert.equal((await generated()).length, 1);
        assert.equal((await generated())[0].savedBrief.purpose, authorStructure);
        assert.equal((await generated())[0].savedBrief.structureTemplate, 'research-report-decision');
        assert.deepEqual((await state()).project.sections.map(section => section.title), ['治理现状', '改进路径']);
        assert.doesNotMatch(await page.locator('.writing-outline-wrap').innerText(), /章节数量.*(?:错误|不符)|章数不符|模板.*不一致/);
        await clean(); result.cases.push({ theme, scenario: 'author-two-chapter-override-has-no-false-count-error', passed: true });
      } finally { await context.close(); }
    }
  }

  for (const step of ['brief', 'materials']) {
    const { context, page, writes, generated, clean } = await open('jade', `outline-regen-pending-${step}`);
    try {
      const regenerate = page.getByRole('button', { name: '重新生成纲要', exact: true });
      assert.equal(await regenerate.isDisabled(), true);
      assert.match(await regenerate.getAttribute('title'), step === 'brief' ? /主题设定/ : /资料筛选/);
      assert.equal((await generated()).length, 0); assert.equal((await writes()).length, 0);
      await clean(); result.cases.push({ theme: 'jade', scenario: `unconfirmed-${step}-blocks-regeneration`, passed: true });
    } finally { await context.close(); }
  }

  {
    const { context, page, state, generated, writes, clean } = await open('jade', 'outline-regen-legacy');
    try {
      assert.equal(Object.hasOwn((await state()).savedProject, 'workflowPendingSteps'), false);
      const regenerate = page.getByRole('button', { name: '重新生成纲要', exact: true });
      assert.equal(await regenerate.isEnabled(), true, 'Legacy completeness remains valid without new pending-step metadata');
      assert.equal((await writes()).length, 0, 'Browsing a legacy record never backfills saved metadata');
      await regenerate.click();
      await page.waitForFunction(() => !window.__preparation.state.busy && window.__preparation.state.project.sections[0].id.startsWith('section_outline_regenerated_'));
      assert.equal((await generated()).length, 1);
      await clean(); result.cases.push({ theme: 'jade', scenario: 'legacy-without-pending-step-metadata-can-explicitly-regenerate', passed: true });
    } finally { await context.close(); }
  }

  {
    const { context, page, state, writes, generated, clean } = await open('jade', 'outline-regen-override');
    try {
      const regenerate = page.getByRole('button', { name: '重新生成纲要', exact: true });
      for (const [guard, value] of [['busy', 'save'], ['pending', true], ['remoteChanged', true], ['loading', true], ['titleChoiceRequired', true]]) {
        await page.evaluate(({ guard, value }) => window.__preparation.setPresentationGuards({ [guard]: value }), { guard, value });
        await page.waitForFunction(() => [...document.querySelectorAll('.writing-outline-confirm-actions button')].some(button => button.textContent.includes('重新生成纲要') && button.disabled));
        assert.equal(await regenerate.isDisabled(), true, `${guard} blocks an outline request`);
        assert.equal((await generated()).length, 0);
        await page.evaluate(() => window.__preparation.setPresentationGuards({}));
        await regenerate.waitFor();
        assert.equal(await regenerate.isEnabled(), true);
        result.cases.push({ theme: 'jade', scenario: `${guard}-blocks-explicit-regeneration`, passed: true });
      }
      for (const [patch, expected] of [
        [{ purpose: '保留原来的两章，再补充新的写作要求。' }, /主题设定/],
        [{ articleTitle: '调整后的文章题目' }, /主题设定/],
        [{ modelId: 'model-changed' }, /主题设定/],
        [{ mode: 'strict' }, /资料筛选/],
        [{ sourceIds: ['source-preparation'] }, /资料筛选/],
        [{ materialSelection: { ...(await state()).project.materialSelection, queries: ['调整后的资料范围'] } }, /资料筛选/],
      ]) {
        const before = (await state()).project;
        await page.evaluate(patch => window.__preparation.hook.setProject(project => ({ ...project, ...patch })), patch);
        await page.waitForFunction(() => [...document.querySelectorAll('.writing-outline-confirm-actions button')].some(button => button.textContent.includes('重新生成纲要') && button.disabled));
        assert.equal(await regenerate.isDisabled(), true);
        assert.match(await regenerate.getAttribute('title'), expected);
        assert.equal((await generated()).length, 0);
        await page.evaluate(before => window.__preparation.hook.setProject(before), before);
        assert.equal(await regenerate.isEnabled(), true);
        result.cases.push({ theme: 'jade', scenario: `unconfirmed-current-${Object.keys(patch)[0]}-blocks-regeneration`, passed: true });
      }
      for (const patch of [{ content: '未保存正文。' }, { content: ' ' }, { manual: true }, { locked: true }]) {
        await page.evaluate(patch => window.__preparation.hook.setProject(project => ({ ...project,
          sections: project.sections.map((section, index) => index ? section : { ...section, ...patch }) })), patch);
        assert.equal(await regenerate.isDisabled(), true, 'Current draft protection cannot be bypassed');
        assert.match(await regenerate.getAttribute('title'), /正文|人工|锁定/);
        assert.equal((await generated()).length, 0);
        await page.evaluate(() => window.__preparation.hook.setProject(project => ({ ...project,
          sections: project.sections.map(section => ({ ...section, content: '', manual: false, locked: false })) })));
        assert.equal(await regenerate.isEnabled(), true);
        result.cases.push({ theme: 'jade', scenario: `current-draft-${Object.keys(patch)[0]}-${patch.content === ' ' ? 'whitespace' : 'protected'}`, passed: true });
      }
      assert.equal((await writes()).length, 0);
      await clean();
    } finally { await context.close(); }
  }
  {
    const { context, page, generated, writes, clean } = await open('jade', 'outline-regen-override');
    try {
      await page.locator('.writing-outline-goal').first().fill('作者显式修改的章节目标。');
      const regenerate = page.getByRole('button', { name: '重新生成纲要', exact: true });
      assert.equal(await regenerate.isEnabled(), true, 'Outline-only edits do not require reconfirming unchanged preparation inputs');
      await regenerate.click();
      await page.waitForFunction(() => !window.__preparation.state.busy && window.__preparation.state.project.sections[0].id.startsWith('section_outline_regenerated_'));
      const mutations = await writes();
      const savedOutline = mutations.find(request => request.method === 'PUT' && request.path.endsWith('/outline'));
      assert(savedOutline, 'The existing hook saves outline-only edits before generation');
      const generatedOutline = (await generated())[0];
      assert.equal((await generated()).length, 1);
      assert(savedOutline.id < generatedOutline.id);
      assert.equal(generatedOutline.replacedSections[0].goal, '作者显式修改的章节目标。');
      await clean(); result.cases.push({ theme: 'jade', scenario: 'outline-only-edit-saves-before-explicit-regeneration', passed: true });
    } finally { await context.close(); }
  }
  result.status = 'passed';
} catch (error) {
  result.status = 'failed'; result.error = String(error); result.stack = error.stack; process.exitCode = 1;
} finally {
  await browser.close(); await fs.writeFile(path.join(out, 'acceptance.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ status: result.status, cases: result.cases.length, error: result.error, stack: result.stack, realApiCalls: 0, realModelCalls: 0 }));
}
