import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { formatPromptText } from '../frontend/src/prompt-format.js';
import { createPromptDraft, currentPromptRules, displayPromptContent, editablePromptContent, isStructurePrompt, promptDraftChanged } from '../frontend/src/prompt-library-state.js';

const base = process.argv[2] || 'http://127.0.0.1:4173';
const out = path.resolve(process.argv[3] || 'output/prompt-current-view/browser');
assert(process.argv[4], 'Provide the new isolated /api/prompts catalog JSON');
const catalog = JSON.parse(await fs.readFile(process.argv[4], 'utf8'));
const items = catalog.items.filter(item => !isStructurePrompt(item));
const categories = catalog.categories.filter(item => !['all', 'structures'].includes(item.id));
await fs.mkdir(out, { recursive: true });
const expected = {};
const audit = [];
const normalize = text => text.replace(/\s/gu, '');
function verify(source, formatted, label) {
  assert.equal(normalize(formatted), normalize(source), `${label}: instruction characters changed`);
  let cursor = 0;
  for (const character of formatted) {
    const next = source.codePointAt(cursor);
    if (next !== undefined && character === String.fromCodePoint(next)) cursor += character.length;
    else assert.match(character, /\s/u, `${label}: source order or original whitespace changed`);
  }
  assert.equal(cursor, source.length);
  assert.deepEqual(formatted.match(/\{\{[\s\S]*?\}\}/g), source.match(/\{\{[\s\S]*?\}\}/g));
  assert.deepEqual(formatted.match(/"(?:\\.|[^"\\])*"/gs), source.match(/"(?:\\.|[^"\\])*"/gs));
}
for (const item of items) {
  const text = displayPromptContent(item);
  const content = formatPromptText(text, item.id);
  verify(text, content, item.id);
  const draft = createPromptDraft(item);
  assert(!promptDraftChanged(draft));
  verify(editablePromptContent(item), draft.content, `${item.id}/edit`);
  assert.equal(draft.contentView, item.contentView || 'source');
  if (/^writing_(outline|section|document)_(strict|extended)$/.test(item.id)) assert.equal(item.contentView, 'effective');
  const rules = currentPromptRules(item);
  assert(!rules.some(rule => /^legacy/.test(rule.scope || '') || /^legacy_/.test(rule.id || '')));
  expected[item.id] = { content, editing: draft.content, rules };
  audit.push({ id: item.id, contentView: draft.contentView, characters: text.length, rules: rules.length });
}
assert(!items.some(isStructurePrompt));
await fs.writeFile(path.join(out, 'format-audit.json'), JSON.stringify({ status: 'passed', items: items.length, categories: categories.length, audit }, null, 2));
if (process.argv.includes('--format-only')) {
  console.log(JSON.stringify({ status: 'passed', items: items.length, categories: categories.length })); process.exit(0);
}

// Add explicit old-version sentinels to the otherwise complete isolated data.
// They must never reach visible or editable DOM, even inside closed details.
const fixture = structuredClone(catalog);
for (const item of fixture.items) {
  if (item.contentView === 'effective') item.content = `OLD_RAW_SENTINEL_${item.id}`;
  item.readOnlyContract = 'OLD_SCHEMA_SENTINEL_should_not_render';
  item.supplementalRules = [...(item.supplementalRules || []),
    { id: 'legacy_qa_sentinel', title: '历史任务兼容', scope: 'legacy', content: 'LEGACY_RULE_SENTINEL_should_not_render' },
    { id: 'qa_duplicate_body', title: '已含正文的重复规则', scope: 'current', content: displayPromptContent(item) },
  ];
}
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const report = { status: 'running', items: items.length, categories: categories.length, cases: [], realApiRequests: 0, modelCalls: 0 };

try {
  for (const theme of ['jade', 'glass', 'civic']) {
    for (const viewport of [{ width: 1600, height: 1000 }, { width: 1180, height: 820 }]) {
      const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
      const escaped = [], errors = [];
      await context.route('**/api/**', route => { escaped.push(route.request().url()); return route.abort(); });
      await context.addInitScript(input => {
        window.__styleCatalogMock = true;
        window.__currentPromptRemote = structuredClone(input);
        window.__currentPromptRequests = [];
        window.fetch = async (source, options = {}) => {
          const url = typeof source === 'string' ? source : source.url;
          const method = options.method || 'GET';
          const body = options.body ? JSON.parse(options.body) : null;
          window.__currentPromptRequests.push({ url, method, body });
          const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
          if (url === '/api/prompts' && method === 'GET') return response(window.__currentPromptRemote);
          if (url.startsWith('/api/prompts/') && method === 'PUT') {
            const id = decodeURIComponent(url.split('/').at(-1));
            const item = window.__currentPromptRemote.items.find(entry => entry.id === id);
            if (!item) return response({ detail: 'Unknown isolated prompt' }, 404);
            if (body.expectedVersion !== item.version) return response({ detail: 'Isolated version conflict' }, 409);
            if (body.contentView !== (item.contentView || 'source')) return response({ detail: 'Incorrect explicit content view' }, 422);
            if (body.content.length > (item.maxContentLength || 24000)) return response({ detail: 'Content exceeds fixed capacity' }, 422);
            if (body.content !== item.editableContent) item.version += 1;
            const fixedContent = item.contentView === 'effective' ? 'CURRENT_FIXED_SENTINEL_仅由程序合入的独立当前规则。' : '';
            const display = body.content + (fixedContent ? '\n\n' + fixedContent : '');
            Object.assign(item, { content: body.content, displayContent: display, editableContent: body.content, effectiveContent: display, fixedContent, updatedAt: '2026-10-28T00:00:00Z' });
            return response(item);
          }
          throw new Error(`Unexpected isolated request: ${method} ${url}`);
        };
      }, fixture);
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(String(error)));
      const name = `${theme}-${viewport.width}x${viewport.height}`;
      try {
        await page.goto(`${base}/tests/style-prompt-catalog.html?theme=${theme}`, { waitUntil: 'networkidle' });
        const workspace = page.locator('.runtime-prompt-library');
        await workspace.locator('.prompt-category-tabs button').first().waitFor();
        assert.equal(await workspace.locator('.prompt-category-tabs button').count(), categories.length);
        assert(!(await workspace.locator('.prompt-category-tabs').innerText()).includes('结构模板'));
        async function select(item) {
          const index = categories.findIndex(entry => entry.id === item.category);
          const category = workspace.locator('.prompt-category-tabs button').nth(index);
          if (await category.getAttribute('aria-pressed') !== 'true') await category.click();
          const group = items.filter(entry => entry.category === item.category);
          await workspace.locator('.prompt-card').nth(group.findIndex(entry => entry.id === item.id)).click();
          await page.waitForFunction(title => document.querySelector('.prompt-detail-title h2')?.textContent === title, item.title);
        }
        for (const item of items) {
          await select(item);
          assert.equal(await workspace.locator('.prompt-content').count(), 1);
          assert.equal(await workspace.locator('.prompt-content').textContent(), expected[item.id].content);
          assert.equal(await workspace.locator('.prompt-effective-instructions, .prompt-effective-note').count(), 0);
          assert.deepEqual(await workspace.locator('.prompt-supplemental-rule pre').allTextContents(), expected[item.id].rules.map(rule => rule.formatted));
          const allText = await workspace.textContent();
          assert(!/OLD_RAW_SENTINEL|OLD_SCHEMA_SENTINEL|LEGACY_RULE_SENTINEL/.test(allText), `${item.id}: obsolete text remains mounted`);
          assert(!allText.includes('已含正文的重复规则'));
          assert(!allText.includes('历史兼容说明') && !allText.includes('已保存指令原文'));
          assert(!allText.includes('已排队任务和检查点重试继续使用原版本'));
          assert.equal(await workspace.locator('.prompt-card').count(), items.filter(entry => entry.category === item.category).length);
          await workspace.getByRole('button', { name: '编辑正文', exact: true }).click();
          const editor = workspace.getByRole('textbox', { name: '指令正文', exact: true });
          assert.equal(await editor.inputValue(), expected[item.id].editing);
          assert.equal(await editor.getAttribute('maxlength'), String(item.maxContentLength || 24000));
          assert(await workspace.getByRole('button', { name: '保存修改', exact: true }).isDisabled());
          assert.equal(await workspace.locator('.prompt-draft-notice').count(), 0);
          await workspace.getByRole('button', { name: '取消编辑', exact: true }).click();
        }
        assert.deepEqual(await page.evaluate(() => window.__currentPromptRequests), [{ url: '/api/prompts', method: 'GET', body: null }]);

        const draftItem = items.find(item => item.id === 'writing_style_official');
        const modeItem = items.find(item => item.id === 'writing_section_strict');
        await select(draftItem); await workspace.getByRole('button', { name: '编辑正文', exact: true }).click();
        const retained = expected[draftItem.id].editing + '\n\n受控文风草稿。';
        await workspace.getByRole('textbox', { name: '指令正文', exact: true }).fill(retained);
        await select(modeItem); await workspace.getByRole('button', { name: '编辑正文', exact: true }).click();
        const edited = expected[modeItem.id].editing + '\n\n受控当前有效指令修改。';
        await workspace.getByRole('textbox', { name: '指令正文', exact: true }).fill(edited);
        await page.evaluate(id => {
          const item = window.__currentPromptRemote.items.find(value => value.id === id);
          item.version += 1; item.content = 'OLD_RAW_SENTINEL_conflicting_storage';
          item.displayContent = item.editableContent = item.effectiveContent = '其他页面刚保存的当前有效指令。';
        }, modeItem.id);
        await workspace.getByRole('button', { name: '保存修改', exact: true }).click();
        await workspace.getByRole('heading', { name: '请对照最新版本', exact: true }).waitFor();
        assert.equal(await workspace.getByRole('textbox', { name: '指令正文', exact: true }).inputValue(), edited);
        await workspace.getByRole('button', { name: '加载最新版本', exact: true }).click();
        assert.equal(await workspace.locator('.prompt-version-conflict pre').textContent(), '其他页面刚保存的当前有效指令。');
        assert(!(await workspace.textContent()).includes('OLD_RAW_SENTINEL'));
        await workspace.getByRole('button', { name: `保留我的内容，基于 v${modeItem.version + 1} 继续编辑`, exact: true }).click();
        await workspace.getByRole('button', { name: '保存修改', exact: true }).click();
        await workspace.getByRole('button', { name: '编辑正文', exact: true }).waitFor();
        const writes = await page.evaluate(() => window.__currentPromptRequests.filter(request => request.method === 'PUT'));
        assert.equal(writes.length, 2);
        assert.deepEqual(writes[0].body, { content: edited, expectedVersion: modeItem.version, contentView: 'effective' });
        assert.deepEqual(writes[1].body, { content: edited, expectedVersion: modeItem.version + 1, contentView: 'effective' });
        const fixed = 'CURRENT_FIXED_SENTINEL_仅由程序合入的独立当前规则。';
        assert.equal(await workspace.locator('.prompt-content').textContent(), edited + '\n\n' + fixed);
        assert.equal(await workspace.locator('.prompt-edit-fixed-rules').count(), 0, 'Reading must not repeat the fixed contract below the displayed instruction');
        await workspace.getByRole('button', { name: '编辑正文', exact: true }).click();
        const secondEditor = workspace.getByRole('textbox', { name: '指令正文', exact: true });
        assert.equal(await secondEditor.inputValue(), edited, 'A second edit must receive only the author editing body');
        assert.equal(await workspace.locator('.prompt-edit-fixed-rules pre').textContent(), fixed);
        const twiceEdited = edited + '\n再次保存新的偏好。';
        await secondEditor.fill(twiceEdited);
        await workspace.getByRole('button', { name: '保存修改', exact: true }).click();
        await workspace.getByRole('button', { name: '编辑正文', exact: true }).waitFor();
        const finalWrites = await page.evaluate(() => window.__currentPromptRequests.filter(request => request.method === 'PUT'));
        assert.equal(finalWrites.length, 3);
        assert.deepEqual(finalWrites[2].body, { content: twiceEdited, expectedVersion: modeItem.version + 2, contentView: 'effective' });
        assert(!finalWrites[2].body.content.includes('CURRENT_FIXED_SENTINEL'), 'A generated fixed contract leaked into the author PUT body');
        const currentText = await workspace.locator('.prompt-content').textContent();
        assert.equal(currentText, twiceEdited + '\n\n' + fixed);
        assert.equal(currentText.split('CURRENT_FIXED_SENTINEL').length - 1, 1, 'Consecutive edits duplicated the fixed contract');
        await page.screenshot({ path: path.join(out, `${name}-current-writing.png`), animations: 'disabled' });
        await select(draftItem);
        assert.equal(await workspace.getByRole('textbox', { name: '指令正文', exact: true }).inputValue(), retained);
        await workspace.getByRole('button', { name: '取消编辑', exact: true }).click();
        await workspace.getByRole('button', { name: '放弃修改', exact: true }).click();
        const longItem = items.find(item => item.id === 'classification');
        await select(longItem);
        const geometry = await workspace.evaluate(node => {
          const detail = node.querySelector('.prompt-detail'), pre = node.querySelector('.prompt-content');
          return { detailHeight: detail.clientHeight, detailScroll: detail.scrollHeight, width: pre.clientWidth, scrollWidth: pre.scrollWidth, overflow: document.documentElement.scrollWidth > innerWidth + 1 };
        });
        assert(geometry.detailHeight > 200 && geometry.detailScroll > geometry.detailHeight && !geometry.overflow && geometry.scrollWidth <= geometry.width + 1);
        await page.screenshot({ path: path.join(out, `${name}-current-classification.png`), animations: 'disabled' });
        assert.deepEqual(escaped, []); assert.deepEqual(errors, []);
        const result = { theme, viewport, status: 'passed', currentItems: items.length, categories: categories.length, cleanEdits: items.length, conflictAndEffectiveSave: true, consecutiveAuthorOnlySave: true, retainedDraft: true, geometry, realApiRequests: 0, modelCalls: 0 };
        report.cases.push(result); console.log(JSON.stringify(result));
      } catch (error) {
        await page.screenshot({ path: path.join(out, `${name}-failed.png`), animations: 'disabled' }).catch(() => {}); throw error;
      } finally { await context.close(); }
    }
  }
  report.status = 'passed';
} catch (error) { report.status = 'failed'; report.error = error.stack || String(error); throw error; }
finally { await browser.close(); await fs.writeFile(path.join(out, 'results.json'), JSON.stringify(report, null, 2)); }
