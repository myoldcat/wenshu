import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { formatPromptText } from '../frontend/src/prompt-format.js';
import { getPromptParagraphs } from '../frontend/src/prompt-paragraphs.js';
import { createPromptDraft, promptDraftChanged } from '../frontend/src/prompt-library-state.js';

const base = process.argv[2] || 'http://127.0.0.1:4173';
const out = path.resolve(process.argv[3] || 'output/prompt-catalog-audit/browser');
const fixturePath = process.argv[4];
assert(fixturePath, 'Provide an isolated /api/prompts catalog JSON as the fourth argument');
const catalog = JSON.parse(await fs.readFile(fixturePath, 'utf8'));
assert(Array.isArray(catalog.items) && Array.isArray(catalog.categories));
await fs.mkdir(out, { recursive: true });
const quoted = text => text.match(/"(?:\\.|[^"\\])*"/gs) || [];
const variables = text => text.match(/\{\{[\s\S]*?\}\}/g) || [];
const normalize = text => text.replace(/\s/gu, '');
const audit = [];
function verifyText(text, id, label, requirePlan = false) {
  const formatted = formatPromptText(text, id);
  assert.equal(normalize(formatted), normalize(text), `${label}: non-whitespace content changed`);
  let cursor = 0;
  for (const character of formatted) {
    const expected = text.codePointAt(cursor);
    if (expected !== undefined && character === String.fromCodePoint(expected)) cursor += character.length;
    else assert.match(character, /\s/u, `${label}: reordered or removed original text`);
  }
  assert.equal(cursor, text.length, `${label}: existing whitespace or characters were removed`);
  assert.deepEqual(quoted(formatted), quoted(text), `${label}: quoted text or JSON strings changed`);
  assert.deepEqual(variables(formatted), variables(text), `${label}: variables changed`);
  assert.equal(formatPromptText(formatted, id), formatted, `${label}: formatting is not idempotent`);
  const paragraphs = getPromptParagraphs(text, id);
  if (requirePlan) assert(paragraphs?.length, `${label}: registered text has no matching semantic plan`);
  if (requirePlan && text.length > 300) assert(paragraphs.length > 1, `${label}: long instructions lack semantic groups`);
  audit.push({ label, characters: text.length, groups: paragraphs?.length || 1, formattedCharacters: formatted.length });
  return formatted;
}

const originalCatalog = JSON.stringify(catalog);
const expected = {};
for (const item of catalog.items) {
  const content = verifyText(item.content, item.id, item.id, true);
  const draft = createPromptDraft(item);
  assert.equal(promptDraftChanged(draft), false, `${item.id}: entering edit produced a dirty draft`);
  assert.equal(draft.version, item.version);
  assert.equal(draft.content, content.length <= (item.maxContentLength || 24000) ? content : item.content);
  const rules = (item.supplementalRules || []).map(rule => ({ ...rule, formatted: verifyText(rule.content, rule.id, `${item.id}/${rule.id}`, rule.content.length > 250) }));
  expected[item.id] = {
    content,
    editing: draft.content,
    effective: item.effectiveContent ? verifyText(item.effectiveContent, item.id, `${item.id}/effective`, true) : null,
    contract: item.readOnlyContract ? verifyText(item.readOnlyContract, `${item.id}:contract`, `${item.id}/contract`) : null,
    rules,
  };
}
assert.equal(JSON.stringify(catalog), originalCatalog, 'Formatting mutated the server catalog');
await fs.writeFile(path.join(out, 'format-audit.json'), JSON.stringify({ status: 'passed', items: catalog.items.length, categories: catalog.categories.filter(item => item.id !== 'all').length, auditedTexts: audit.length, audit }, null, 2));
if (process.argv.includes('--format-only')) {
  console.log(JSON.stringify({ status: 'passed', items: catalog.items.length, auditedTexts: audit.length }));
  process.exit(0);
}

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const report = { status: 'running', items: catalog.items.length, categories: catalog.categories.length, cases: [], realApiRequests: 0, modelCalls: 0 };

async function createContext(theme, viewport) {
  const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
  const escaped = [], errors = [];
  await context.route('**/api/**', route => { escaped.push({ method: route.request().method(), url: route.request().url() }); return route.abort(); });
  await context.addInitScript(fixture => {
    window.__styleCatalogMock = true;
    window.__catalogRemote = structuredClone(fixture);
    window.__catalogRequests = [];
    window.fetch = async (input, options = {}) => {
      const url = typeof input === 'string' ? input : input.url;
      const method = options.method || 'GET';
      const body = options.body ? JSON.parse(options.body) : null;
      window.__catalogRequests.push({ url, method, body });
      const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
      if (url === '/api/prompts' && method === 'GET') return response(window.__catalogRemote);
      if (url.startsWith('/api/prompts/') && method === 'PUT') {
        const id = decodeURIComponent(url.split('/').at(-1));
        const item = window.__catalogRemote.items.find(entry => entry.id === id);
        if (!item) return response({ detail: 'Unknown isolated prompt' }, 404);
        if (body.expectedVersion !== item.version) return response({ detail: 'Prompt version changed' }, 409);
        if (!body.content.trim() || body.content.length > (item.maxContentLength || 24000)) return response({ detail: 'Prompt content limit' }, 422);
        item.content = body.content; item.version += 1; item.updatedAt = '2026-10-28T00:00:00Z';
        return response(item);
      }
      throw new Error(`Blocked unexpected test request: ${method} ${url}`);
    };
  }, catalog);
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(`${base}/tests/style-prompt-catalog.html?theme=${theme}`, { waitUntil: 'networkidle' });
  const workspace = page.locator('.runtime-prompt-library');
  await workspace.locator('.prompt-category-tabs button').first().waitFor();
  const categories = catalog.categories.filter(item => item.id !== 'all');
  async function select(item) {
    const categoryIndex = categories.findIndex(entry => entry.id === item.category);
    assert(categoryIndex >= 0, `Unknown catalog category ${item.category}`);
    const category = workspace.locator('.prompt-category-tabs button').nth(categoryIndex);
    if ((await category.getAttribute('aria-pressed')) !== 'true') await category.click();
    const members = catalog.items.filter(entry => entry.category === item.category);
    await workspace.locator('.prompt-card').nth(members.findIndex(entry => entry.id === item.id)).click();
    await page.waitForFunction(title => document.querySelector('.prompt-detail-title h2')?.textContent === title, item.title);
  }
  return { context, page, workspace, select, escaped, errors, categories };
}

try {
  for (const theme of ['jade', 'glass', 'civic']) {
    for (const viewport of [{ width: 1600, height: 1000 }, { width: 1180, height: 820 }]) {
      const { context, page, workspace, select, escaped, errors, categories } = await createContext(theme, viewport);
      const name = `${theme}-${viewport.width}x${viewport.height}`;
      const counts = { textChecks: 0, cleanEdits: 0, effectiveChecks: 0, supplementalChecks: 0 };
      try {
        assert.equal(await workspace.locator('.prompt-category-tabs button').count(), categories.length);
        for (const item of catalog.items) {
          await select(item);
          const desired = expected[item.id];
          assert.equal(await workspace.locator('.prompt-content').textContent(), desired.content, `${item.id}: incorrect reading text`);
          assert.equal(await workspace.locator('.prompt-card').count(), catalog.items.filter(entry => entry.category === item.category).length);
          if (item.effectiveContent && item.effectiveContent !== item.content) {
            assert.match(await workspace.locator('.prompt-document-toolbar').innerText(), /已保存指令原文/);
            assert.match(await workspace.locator('.prompt-effective-note').innerText(), /新任务采用当前基础规则/);
            await workspace.locator('.prompt-effective-note').getByRole('button', { name: '点击查看', exact: true }).click();
            const effective = workspace.locator('.prompt-effective-instructions');
            assert.equal(await effective.getAttribute('open'), '');
            assert.equal(await effective.locator('summary').innerText(), '当前新任务基础指令');
            assert.match(await effective.locator('p').innerText(), /组织、结构与文风按本次选择另行合入；完整请求可在写作运维中查看/);
            assert.equal(await effective.locator('pre').textContent(), desired.effective);
            assert.equal(await effective.locator('textarea, [contenteditable="true"]').count(), 0);
            counts.effectiveChecks += 1;
          } else assert.equal(await workspace.locator('.prompt-effective-instructions').count(), 0);
          if (desired.rules.length) {
            const sections = workspace.locator('.prompt-supplemental-rule');
            assert.equal(await sections.count(), desired.rules.length);
            assert.deepEqual(await sections.locator('pre').allTextContents(), desired.rules.map(rule => rule.formatted));
            assert.deepEqual(await sections.locator('h3').allTextContents(), desired.rules.map(rule => `${rule.title || '固定规则'}只读`));
            assert.equal(await sections.locator('textarea, [contenteditable="true"]').count(), 0);
            counts.supplementalChecks += desired.rules.length;
          }
          const contract = workspace.locator('.prompt-runtime-contract:not(.prompt-effective-instructions):not(.prompt-supplemental-rules)');
          if (item.readOnlyContract) assert.equal(await contract.locator('p').textContent(), desired.contract);
          counts.textChecks += 1;

          await workspace.getByRole('button', { name: '编辑正文', exact: true }).click();
          const editor = workspace.getByRole('textbox', { name: '指令正文', exact: true });
          assert.equal(await editor.inputValue(), desired.editing, `${item.id}: editor read from effective content instead of original`);
          assert.equal(await editor.getAttribute('maxlength'), String(item.maxContentLength || 24000));
          assert(await workspace.getByRole('button', { name: '保存修改', exact: true }).isDisabled());
          assert.equal(await workspace.locator('.prompt-draft-notice').count(), 0);
          const prevented = await page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; });
          assert.equal(prevented, false, `${item.id}: a clean editor installed a dirty unload guard`);
          await workspace.getByRole('button', { name: '取消编辑', exact: true }).click();
          assert.equal(await editor.count(), 0);
          counts.cleanEdits += 1;
        }
        const browseRequests = await page.evaluate(() => window.__catalogRequests);
        assert.deepEqual(browseRequests, [{ url: '/api/prompts', method: 'GET', body: null }], 'Browsing all prompts or clean editing wrote configuration');

        const longItem = catalog.items.find(item => item.id === 'classification');
        await select(longItem);
        const geometry = await workspace.evaluate(node => {
          const detail = node.querySelector('.prompt-detail'), content = node.querySelector('.prompt-content'), list = node.querySelector('.prompt-list');
          return { detailHeight: detail.clientHeight, detailScroll: detail.scrollHeight, contentWidth: content.clientWidth, contentScroll: content.scrollWidth, listHeight: list.clientHeight, overflow: document.documentElement.scrollWidth > innerWidth + 1 };
        });
        assert(geometry.detailHeight > 200 && geometry.detailScroll > geometry.detailHeight, 'Long prompts require a usable independent scroll region');
        assert(geometry.listHeight > 200 && !geometry.overflow && geometry.contentScroll <= geometry.contentWidth + 1);
        await page.screenshot({ path: path.join(out, `${name}-classification.png`), animations: 'disabled' });

        const draftItem = catalog.items.find(item => item.id === 'writing_style_official');
        await select(draftItem); await workspace.getByRole('button', { name: '编辑正文', exact: true }).click();
        const retainedDraft = expected[draftItem.id].editing + '\n\n保留此条未保存的表达要求。';
        await workspace.getByRole('textbox', { name: '指令正文', exact: true }).fill(retainedDraft);
        const effectiveItem = catalog.items.find(item => item.id === 'writing_section_strict' && item.effectiveContent !== item.content);
        assert(effectiveItem, 'The isolated fixture needs a differing raw/effective instruction');
        await select(effectiveItem);
        await workspace.locator('.prompt-effective-note').getByRole('button', { name: '点击查看', exact: true }).click();
        await workspace.locator('.prompt-supplemental-rules').evaluate(node => { node.open = true; });
        await page.screenshot({ path: path.join(out, `${name}-effective-and-rules.png`), animations: 'disabled' });
        await workspace.getByRole('button', { name: '编辑正文', exact: true }).click();
        const changedRaw = expected[effectiveItem.id].editing + '\n\n受控测试：仅修改此条原文。';
        assert.notEqual(changedRaw, expected[effectiveItem.id].effective);
        await workspace.getByRole('textbox', { name: '指令正文', exact: true }).fill(changedRaw);
        await workspace.getByRole('button', { name: '保存修改', exact: true }).click();
        await workspace.getByRole('button', { name: '编辑正文', exact: true }).waitFor();
        const writes = await page.evaluate(() => window.__catalogRequests.filter(request => request.method !== 'GET'));
        assert.equal(writes.length, 1);
        assert.deepEqual(writes[0], { url: `/api/prompts/${effectiveItem.id}`, method: 'PUT', body: { content: changedRaw, expectedVersion: effectiveItem.version } });
        assert(!Object.hasOwn(writes[0].body, 'effectiveContent') && !Object.hasOwn(writes[0].body, 'supplementalRules'));
        await select(draftItem);
        assert.equal(await workspace.getByRole('textbox', { name: '指令正文', exact: true }).inputValue(), retainedDraft, 'Saving another category lost the unsaved prompt');
        assert.match(await workspace.locator('.prompt-draft-notice').innerText(), /有 1 条未保存修改/);
        await workspace.getByRole('button', { name: '取消编辑', exact: true }).click();
        await workspace.getByRole('button', { name: '放弃修改', exact: true }).click();
        assert.equal(await workspace.locator('.prompt-draft-notice').count(), 0);

        const role = catalog.items.find(item => item.id === 'writing_role_development');
        await select(role); await workspace.getByRole('button', { name: '编辑正文', exact: true }).click();
        const roleEditor = workspace.getByRole('textbox', { name: '指令正文', exact: true });
        assert.equal(await roleEditor.getAttribute('maxlength'), '180');
        await roleEditor.fill('角色表达'.repeat(60));
        assert.equal((await roleEditor.inputValue()).length, 180, 'Native role editor must enforce its 180-character capacity');
        await workspace.getByRole('button', { name: '取消编辑', exact: true }).click();
        await workspace.getByRole('button', { name: '放弃修改', exact: true }).click();
        assert.equal((await page.evaluate(() => window.__catalogRequests.filter(request => request.method !== 'GET'))).length, 1);
        assert.deepEqual(escaped, []); assert.deepEqual(errors, []);
        const result = { theme, viewport, status: 'passed', ...counts, geometry, simulatedReads: 1, simulatedWrites: 1, realApiRequests: 0, modelCalls: 0 };
        report.cases.push(result);
        await fs.writeFile(path.join(out, `${name}-results.json`), JSON.stringify(result, null, 2));
        console.log(JSON.stringify(result));
      } catch (error) {
        await page.screenshot({ path: path.join(out, `${name}-failed.png`), animations: 'disabled' }).catch(() => {});
        throw error;
      } finally { await context.close(); }
    }
  }
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = error.stack || String(error); throw error;
} finally {
  await browser.close();
  await fs.writeFile(path.join(out, 'results.json'), JSON.stringify(report, null, 2));
}
