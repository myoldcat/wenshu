import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:4185';
const out = path.resolve(process.argv[3] || 'output/writing-presets/browser');
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const result = { status: 'running', cases: [], realApiCalls: 0, realModelCalls: 0 };
const reportVariants = [
  { id: 'research-report-problem', name: '调研报告（问题对策型）', hints: ['调研背景与方法', '基本情况', '存在问题', '原因分析', '对策建议'] },
  { id: 'research-report-experience', name: '调研报告（经验总结型）', hints: ['主要做法', '取得成效', '经验启示', '推广建议'] },
  { id: 'research-report-decision', name: '调研报告（决策参考型）', hints: ['现状与趋势', '风险研判', '意见建议（分近期、中期）'] },
];
const defaultReport = reportVariants[2];

async function open(theme, scenario = 'blank') {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, reducedMotion: 'reduce' });
  const leaks = [], errors = [];
  await context.route('**/api/**', route => { leaks.push(route.request().url()); return route.abort(); });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=${scenario}`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__preparation && !window.__preparation.state.loading);
  const state = () => page.evaluate(() => window.__preparation.state);
  const writes = () => page.evaluate(() => window.__preparation.requests.filter(request => request.method !== 'GET'));
  const idle = () => page.waitForFunction(() => !window.__preparation.state.busy);
  const clean = async () => {
    assert.deepEqual(leaks, [], 'No request escaped the mock boundary');
    assert.deepEqual(errors, [], 'No browser runtime error');
    assert.deepEqual(await page.evaluate(() => window.__preparation.unexpected), []);
    assert.equal((await state()).error, '');
  };
  return { context, page, state, writes, idle, clean };
}

async function productionTemplateAction(theme) {
  if (theme === 'demo') return;
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, reducedMotion: 'reduce' });
  const unsafe = [], errors = [];
  await context.route('**/api/**', async route => {
    const request = route.request(), endpoint = new URL(request.url()).pathname, method = request.method();
    const json = value => route.fulfill({ status: 200, json: value });
    if (endpoint === '/api/ai/writing-activity') return json({ active: false });
    if (method !== 'GET') { unsafe.push({ endpoint, method }); return route.fulfill({ status: 409, json: { detail: 'Acceptance blocked an unexpected mutation' } }); }
    if (endpoint === '/api/writing/projects') return json({ projects: [], capabilities: {} });
    if (endpoint === '/api/documents') return json({ documents: [], total: 0 });
    if (endpoint === '/api/models') return json({ models: [], preferences: { autoProcess: false, autoTranslate: false, idleProcess: false } });
    if (endpoint === '/api/jobs') return json({ jobs: [], counts: { queued: 0, running: 0, failed: 0, total: 0 } });
    if (endpoint === '/api/home') return json({ topics: [], writing: { total: 0, totalWords: 0 } });
    return json({ items: [], entries: [], topics: [], images: [], nodes: [], edges: [], results: [], total: 0, ai: {}, jobs: [], prompts: [] });
  });
  const page = await context.newPage(); page.on('pageerror', error => errors.push(String(error)));
  try {
    await page.goto(`${base}/?theme=${theme}`, { waitUntil: 'networkidle' });
    const nav = page.getByRole('navigation', { name: '主导航', exact: true });
    await nav.getByRole('button', { name: '参考模板', exact: true }).click();
    await page.getByRole('heading', { name: defaultReport.name, exact: true }).waitFor();
    assert.match(await page.locator('.template-paper-caption').innerText(), /结构提示/);
    assert.match(await page.locator('.template-paper-caption').innerText(), /正式标题按内容拟定/);
    assert.match(await page.locator('.template-type-hint').innerText(), /正式章标题根据主题与实际内容拟定/);
    assert.match(await page.locator('.template-type-hint').innerText(), /写作要求.*明确.*结构.*优先/);
    assert.match(await page.locator('.template-type-hint').innerText(), /模板仅补充未指定部分/);
    const reportChoices = page.locator('.template-list .template-item').filter({ hasText: /调研报告/ });
    assert.equal(await reportChoices.count(), 3, 'Only the three new report types appear in the library');
    assert.equal(await page.locator('.template-list').getByRole('button', { name: /^调研报告$/ }).count(), 0);
    for (const variant of reportVariants) {
      await page.locator('.template-list').getByRole('button', { name: variant.name }).click();
      await page.getByRole('heading', { name: variant.name, exact: true }).waitFor();
      assert.deepEqual(await page.locator('.template-paper ol li').allTextContents(), variant.hints.map((hint, index) => `${String(index + 1).padStart(2, '0')}${hint}`));
      const introduction = page.locator('.template-paper-background');
      assert.match(await introduction.innerText(), /引言.*不算独立章节/);
      assert(await introduction.evaluate(node => node.compareDocumentPosition(node.parentElement.querySelector('ol')) & Node.DOCUMENT_POSITION_FOLLOWING));
      await page.screenshot({ path: path.join(out, `${theme}-${variant.id}.png`), animations: 'disabled' });
    }
    await page.screenshot({ path: path.join(out, `${theme}-template-library.png`), animations: 'disabled' });
    await page.locator('.template-list').getByRole('button', { name: /观点文章/ }).click();
    await page.getByRole('button', { name: '使用这个模板', exact: true }).click();
    assert.equal(await page.locator('#writing-structure-template').inputValue(), 'opinion');
    await nav.getByRole('button', { name: '参考模板', exact: true }).click();
    await page.locator('.template-list').getByRole('button', { name: /调研报告（决策参考型）/ }).click();
    await page.getByRole('button', { name: '使用这个模板', exact: true }).click();
    assert.equal(await page.locator('#writing-structure-template').inputValue(), defaultReport.id);
    assert.equal(await page.locator('#writing-style').inputValue(), 'official');
    assert.deepEqual(unsafe, []); assert.deepEqual(errors, []);
    result.cases.push({ theme, scenario: 'production-template-to-writing-selection', passed: true });
  } finally { await context.close(); }
}

try {
  for (const theme of ['jade', 'glass', 'civic', 'demo']) {
    {
      const { context, page, state, writes, idle, clean } = await open(theme);
      try {
        const structure = page.locator('#writing-structure-template'), style = page.locator('#writing-style');
        assert.equal(await structure.inputValue(), defaultReport.id);
        assert.equal(await style.inputValue(), 'official');
        assert.equal((await state()).project.template, defaultReport.name);
        assert.equal((await state()).project.style, '政府公文');
        assert.equal(await structure.locator('option[value="research-report"]').count(), 0, 'Old report is not a new-draft choice');
        const reportOptionIds = await structure.locator('option').evaluateAll(options => options.map(option => option.value).filter(value => value.startsWith('research-report')));
        assert.deepEqual(reportOptionIds.sort(), reportVariants.map(variant => variant.id).sort());
        assert.deepEqual(await style.locator('option').allTextContents(), ['政府公文', '技术论述', '新闻报道']);
        await page.screenshot({ path: path.join(out, `${theme}-default.png`), animations: 'disabled' });
        for (const id of ['opinion', 'review', 'report', 'book', 'explainer', 'custom', ...reportVariants.map(variant => variant.id)]) {
          await structure.selectOption(id); assert.equal((await state()).project.structureTemplate, id);
        }
        for (const [id, label] of [['technical', '技术论述'], ['news', '新闻报道'], ['official', '政府公文']]) {
          await style.selectOption(id); assert.equal((await state()).project.writingStyle, id); assert.equal((await state()).project.style, label);
        }
        assert.equal((await writes()).length, 0, 'Choosing preferences does not save or generate');
        await page.locator('#writing-topic').fill('调研报告结构与文风隔离验收');
        const authorStructure = '全文仅分为“治理现状”“改进路径”两章，不要章外引言；按作者指定顺序展开。';
        await page.locator('#writing-purpose').fill(authorStructure);
        assert.match(await page.locator('label[for="writing-purpose"]').innerText(), /明确结构优先于模板/);
        await style.selectOption('technical');
        for (const variant of reportVariants) {
          await structure.selectOption(variant.id);
          assert.equal(await page.locator('#writing-purpose').inputValue(), authorStructure);
          assert(await page.evaluate(() => window.__preparation.hook.save())); await idle();
          const write = (await writes()).at(-1);
          assert.equal(write.body.brief.structureTemplate, variant.id); assert.equal(write.body.brief.writingStyle, 'technical');
          assert.equal(write.body.brief.purpose, authorStructure);
          await page.evaluate(() => window.__preparation.hook.reload()); await idle();
          assert.equal(await structure.inputValue(), variant.id); assert.equal(await style.inputValue(), 'technical');
          assert.equal((await state()).savedProject.brief.structureTemplate, variant.id);
          assert.equal(await page.locator('#writing-purpose').inputValue(), authorStructure);
        }
        await page.screenshot({ path: path.join(out, `${theme}-selected.png`), animations: 'disabled' });
        await clean(); result.cases.push({ theme, scenario: 'defaults-choice-save-reload', passed: true });
        result.cases.push({ theme, scenario: 'explicit-writing-structure-survives-template-roundtrips', passed: true });
      } finally { await context.close(); }
    }
    {
      const { context, page, state, writes, idle, clean } = await open(theme);
      try {
        await page.locator('#writing-topic').fill('保留旧调研报告模板的隔离历史文稿');
        await page.evaluate(() => window.__preparation.hook.setProject(project => ({ ...project,
          structureTemplate: 'research-report', template: '调研报告',
          templateSections: ['当前态势', '存在问题隐患', '对策建议'],
        })));
        assert(await page.evaluate(() => window.__preparation.hook.save())); await idle();
        await page.evaluate(() => window.__preparation.hook.reload()); await idle();
        const structure = page.locator('#writing-structure-template');
        assert.equal(await structure.inputValue(), 'research-report');
        assert.equal((await state()).savedProject.brief.structureTemplate, 'research-report');
        assert.equal((await state()).savedProject.brief.template, '调研报告');
        const before = (await writes()).length, saved = (await state()).savedProject;
        assert(await page.evaluate(() => window.__preparation.hook.save())); await idle();
        assert.equal((await writes()).length, before, 'Old report remains unchanged on no-op save');
        await structure.selectOption(defaultReport.id);
        await structure.selectOption('research-report');
        assert(await page.evaluate(() => window.__preparation.hook.save())); await idle();
        assert.equal((await writes()).length, before, 'Restoring the old saved report remains a no-op');
        assert.deepEqual((await state()).savedProject, saved);
        await page.locator('#writing-topic').fill('旧调研报告只修改主题，保留原模板标识');
        assert(await page.evaluate(() => window.__preparation.hook.save())); await idle();
        const patch = (await writes()).at(-1);
        assert.equal(patch.body.brief.structureTemplate, 'research-report');
        assert.equal(patch.body.brief.template, '调研报告');
        await clean(); result.cases.push({ theme, scenario: 'old-report-id-noop-restore-and-unrelated-save', passed: true });
      } finally { await context.close(); }
    }
    {
      const { context, page, state, writes, idle, clean } = await open(theme, 'other');
      try {
        assert.equal(await page.locator('#writing-structure-template').inputValue(), 'legacy');
        assert.equal(await page.locator('#writing-style').inputValue(), 'legacy');
        const saved = (await state()).savedProject;
        assert.equal(saved.brief.structureTemplate, undefined); assert.equal(saved.brief.writingStyle, undefined);
        assert(await page.evaluate(() => window.__preparation.hook.save())); await idle();
        assert.equal((await writes()).length, 0, 'Unchanged historical project stays a no-op');
        assert.deepEqual((await state()).savedProject, saved);
        const modify = page.locator('[data-writing-modify-step="brief"]'); if (await modify.count()) await modify.click();
        await page.locator('#writing-structure-template').selectOption(defaultReport.id); await page.locator('#writing-style').selectOption('technical');
        await page.locator('#writing-structure-template').selectOption('legacy'); await page.locator('#writing-style').selectOption('legacy');
        assert(await page.evaluate(() => window.__preparation.hook.save())); await idle();
        assert.equal((await writes()).length, 0, 'Restoring legacy preferences stays a no-op');
        await page.locator('#writing-topic').fill('只修改主题，不补写历史模板和文风字段');
        assert(await page.evaluate(() => window.__preparation.hook.save())); await idle();
        const patch = (await writes()).find(request => request.method === 'PATCH');
        assert(patch, 'Unrelated edit was saved to the mock');
        assert(!Object.hasOwn(patch.body.brief, 'structureTemplate')); assert(!Object.hasOwn(patch.body.brief, 'writingStyle'));
        await clean(); result.cases.push({ theme, scenario: 'legacy-browse-noop-unrelated-save', passed: true });
      } finally { await context.close(); }
    }
    {
      const { context, page, writes, clean } = await open(theme, 'v2-readonly');
      try {
        assert(await page.locator('#writing-structure-template').isDisabled()); assert(await page.locator('#writing-style').isDisabled());
        assert.equal((await writes()).length, 0); await clean(); result.cases.push({ theme, scenario: 'running-readonly', passed: true });
      } finally { await context.close(); }
    }
    {
      const { context, page, state, idle, clean } = await open(theme);
      try {
        await page.locator('#writing-topic').fill('文风改变后重新确认辅助题目');
        await page.getByRole('button', { name: '辅助拟题', exact: true }).click();
        await page.waitForFunction(() => window.__preparation.pendingTitles.length === 1);
        const titleRequest = await page.evaluate(() => window.__preparation.requests.find(request => request.path === '/api/writing/assist/title'));
        assert.equal(titleRequest.body.structureTemplate, defaultReport.id);
        assert.equal(titleRequest.body.writingStyle, 'official');
        await page.evaluate(() => window.__preparation.releaseTitle({ titles: ['第一个隔离题目', '第二个隔离题目', '第三个隔离题目'] })); await idle();
        await page.getByRole('button', { name: /第一个隔离题目/ }).click();
        await page.waitForFunction(() => window.__preparation.state.titleAssistance.status === 'selected');
        assert.equal((await state()).titleChoiceRequired, false);
        await page.locator('#writing-style').selectOption('news'); assert.equal((await state()).titleChoiceRequired, true);
        await page.getByRole('button', { name: '使用当前题目', exact: true }).click(); assert.equal((await state()).titleChoiceRequired, false);
        await page.locator('#writing-structure-template').selectOption('opinion'); assert.equal((await state()).titleChoiceRequired, true);
        await page.getByRole('button', { name: '使用当前题目', exact: true }).click();
        await clean(); result.cases.push({ theme, scenario: 'title-reconfirmation-on-preference-change', passed: true });
      } finally { await context.close(); }
    }
    for (const variant of [{ id: 'research-report', name: '调研报告' }, defaultReport]) {
      const { context, page, state, writes, clean } = await open(theme, 'editor');
      try {
        const before = (await writes()).length;
        const legacy = variant.id === 'research-report';
        const titles = legacy ? ['当前态势', '存在问题隐患', '对策建议']
          : ['智能治理的现状与发展趋势', '数据流转中的主要风险与边界', '分阶段完善治理机制的意见建议'];
        const introduction = legacy ? '这是不单列章节的背景介绍。' : '这是不单列章节的引言。';
        await page.evaluate(({ variant, titles, introduction }) => window.__preparation.hook.setProject(project => ({ ...project,
          structureTemplate: variant.id, template: variant.name,
          sections: [
            { ...project.sections[0], title: titles[0], content: introduction + '\n\n这是第一章的正文。', introductionParagraphCount: 1 },
            { ...project.sections[0], id: 'section_presets_problems', title: titles[1], content: '这是第二章的正文。' },
            { ...project.sections[0], id: 'section_presets_suggestions', title: titles[2], content: '近期完善数据清单，中期建立修订机制。' },
          ],
        })), { variant, titles, introduction });
        const openPreview = page.getByRole('button', { name: '全文预览', exact: true }); if (await openPreview.count()) await openPreview.click();
        const preview = page.getByRole('main', { name: '全文内容', exact: true }); await preview.waitFor();
        assert.deepEqual(await preview.locator('h2').allTextContents(), titles);
        assert(await preview.getByText(introduction, { exact: true }).evaluate(node => node.compareDocumentPosition(node.closest('.writing-preview-chapter').querySelector('h2')) & Node.DOCUMENT_POSITION_FOLLOWING));
        assert.equal((await state()).project.sections.length, 3); assert.equal((await writes()).length, before);
        await page.screenshot({ path: path.join(out, `${theme}-${variant.id}-introduction-preview.png`), animations: 'disabled' });
        await page.getByRole('button', { name: '返回章节', exact: true }).click();
        await page.getByRole('button', { name: '阅读', exact: true }).click();
        const reading = page.getByRole('region', { name: '章节正文阅读', exact: true });
        const readingBody = await reading.count() ? reading : page.locator('.writing-body-reading'); await readingBody.waitFor();
        assert(await readingBody.getByText(introduction, { exact: true }).evaluate(node => node.compareDocumentPosition(node.closest('.writing-body-reading').querySelector('h2')) & Node.DOCUMENT_POSITION_FOLLOWING));
        assert.equal((await writes()).length, before); await clean(); result.cases.push({ theme, scenario: `${variant.id}-lead-before-heading-preview-and-read`, passed: true });
      } finally { await context.close(); }
    }
    await productionTemplateAction(theme);
  }
  result.status = 'passed';
} catch (error) {
  result.status = 'failed'; result.error = String(error); result.stack = error.stack; process.exitCode = 1;
} finally {
  await browser.close(); await fs.writeFile(path.join(out, 'acceptance.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ status: result.status, cases: result.cases.length, error: result.error, stack: result.stack, realApiCalls: 0, realModelCalls: 0 }));
}
