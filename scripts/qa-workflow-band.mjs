import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:4173';
const output = path.resolve(process.argv[3] || 'output/workflow-band/browser-staged');
const before = process.argv.includes('--before');
const ribbon = process.argv.includes('--ribbon');
const edge = process.argv.includes('--edge');
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const result = { status: 'running', cases: [], realApiCalls: 0, realModelCalls: 0 };
async function assertEdge(band) {
  const frame = await band.evaluate(node => {
    const track = node.querySelector('.writing-progress-track'), list = node.querySelector('ol');
    const css = item => getComputedStyle(item), rect = item => { const b = item.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; };
    return { summary: !!node.querySelector('.writing-progress-summary'), text: node.innerText,
      padding: [node, track, list].flatMap(item => ['paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft'].map(key => css(item)[key])),
      borders: ['borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth'].map(key => css(node)[key]),
      shadow: css(node).boxShadow, background: css(node).backgroundColor, band: rect(node), track: rect(track), list: rect(list) };
  });
  assert.equal(frame.summary, false, 'No leading workflow summary');
  assert(!frame.text.includes('写作流程'));
  assert(frame.padding.every(value => value === '0px'), 'No frame padding at any breakpoint');
  assert(frame.borders.every(value => value === '0px'));
  assert.equal(frame.shadow, 'none');
  assert.equal(frame.background, 'rgba(0, 0, 0, 0)');
  for (const box of [frame.track, frame.list]) for (const key of ['x', 'y', 'width', 'height']) assert(Math.abs(box[key] - frame.band[key]) < 1, 'Ribbon fills its entire footer region');
}
async function open(theme, scenario, width = 1440) {
  const context = await browser.newContext({ viewport: { width, height: 1000 } }), leaked = [], errors = [];
  await context.route('**/api/**', route => { leaked.push(route.request().url()); return route.abort(); });
  const page = await context.newPage(); page.on('pageerror', error => errors.push(String(error)));
  await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=${scenario}`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__preparation?.state.project.id && !window.__preparation.state.loading);
  const band = page.locator('[data-writing-progress]');
  const state = () => page.evaluate(() => window.__preparation.state);
  const clean = async () => {
    assert.deepEqual(leaked, []); assert.deepEqual(errors, []);
    assert.deepEqual(await page.evaluate(() => window.__preparation.unexpected), []);
    assert.equal((await state()).error, '');
    assert.equal(await page.evaluate(() => window.__preparation.requests.filter(r => r.method !== 'GET').length), 0, 'Navigation must not save, confirm, search or invoke a model');
  };
  return { context, page, band, state, clean };
}
try {
  for (const theme of ['jade', 'glass', 'civic', 'demo']) {
    const scenarios = before ? ['review'] : ribbon ? ['workflow-review', 'workflow-body', 'workflow-states', 'automatic'] : ['workflow-review', 'workflow-stale', 'workflow-skipped', 'workflow-unchecked', 'workflow-body', 'workflow-materials', 'workflow-confirmed', 'workflow-untimed', 'workflow-states', 'automatic'];
    for (const scenario of scenarios) {
      const { context, page, band, state, clean } = await open(theme, scenario);
      try {
        await band.screenshot({ path: path.join(output, `${theme}-${scenario}.png`) });
        if (before) { await clean(); result.cases.push({ theme, scenario: 'before', passed: true }); continue; }
        for (const button of await band.locator('li>button').all()) assert((await button.boundingBox()).height >= 56, 'Step hit target');
        assert.equal(await band.locator('li').count(), 6);
        if (edge) await assertEdge(band);
        assert.equal(await band.locator('.writing-progress-reminder').count(), 0, 'Reminders are integrated into their own segments');
        const palette = await band.locator('li').evaluateAll(nodes => nodes.map(node => ({
          stage: node.dataset.stage, state: node.dataset.state,
          color: getComputedStyle(node).getPropertyValue('--workflow-stage-color').trim(),
          shared: getComputedStyle(document.documentElement).getPropertyValue('--writing-spectrum-' + node.dataset.stage).trim(),
        })));
        assert(palette.every(item => item.color && item.color === item.shared), 'Footer and external progress share the same palette');
        assert(palette.every(item => ['waiting', 'running', 'skipped', 'complete'].includes(item.state)));
        {
          const paint = await band.evaluate(node => ({
            gaps: ['columnGap', 'rowGap'].map(key => getComputedStyle(node.querySelector('ol'))[key]),
            buttons: [...node.querySelectorAll('li>button, .writing-progress-reminder-action')].map(button => {
              const css = getComputedStyle(button); return [css.borderTopWidth, css.borderRightWidth, css.borderBottomWidth, css.borderLeftWidth];
            }),
            corners: [...node.querySelectorAll('li>button')].map(button => getComputedStyle(button).borderRadius),
          }));
          assert(paint.gaps.every(value => value === '0px'), 'Continuous ribbon has no gaps');
          assert(paint.buttons.every(values => values.every(value => value === '0px')), 'No button frames');
          assert(paint.corners.every(value => value === '0px'), 'No individual card corners');
        }
        if (scenario === 'workflow-review') {
          assert.match(await band.locator('[data-writing-step="review"]').innerText(), /2项待确认/);
          assert.equal(await band.locator('li[data-stage="review"]').getAttribute('data-state'), 'skipped');
          assert.match(await band.locator('[data-writing-step="body"]').innerText(), /用时\s*2分36秒/);
          assert.equal(await band.getAttribute('data-completed'), '6', 'A reminder must not undo completed steps');
          await band.locator('[data-writing-step="review"] .writing-progress-step-action').click();
          await page.getByRole('dialog', { name: '校验报告', exact: true }).waitFor();
          assert.equal(await page.getByRole('dialog').getByRole('button', { name: '已核对，此项无误', exact: true }).count(), 0, 'Saved report remains read-only');
          assert.equal(await band.locator('[aria-current="step"]').getAttribute('data-writing-step'), 'review');
          assert.equal(await band.locator('li[data-stage="review"]').getAttribute('data-state'), 'skipped', 'Viewing a phase does not change its real state');
        } else if (scenario === 'workflow-stale') {
          assert.match(await band.locator('[data-writing-step="review"]').innerText(), /校验报告已过期/);
          assert(!/2项待确认/.test(await band.innerText()), 'Expired issue count must not masquerade as current');
          await band.locator('[data-writing-step="review"]').click();
          await page.getByRole('dialog', { name: '校验报告', exact: true }).waitFor();
        } else if (scenario === 'workflow-skipped' || scenario === 'workflow-unchecked') {
          assert.equal(await band.locator('li[data-stage="review"]').getAttribute('data-notice-tone'), 'info');
          const expected = scenario === 'workflow-skipped' ? 'skipped' : 'running';
          assert.equal(await band.locator('li[data-stage="review"]').getAttribute('data-state'), expected);
          await band.locator('[data-writing-step="review"]').click();
        } else if (scenario === 'workflow-body') {
          assert.match(await band.innerText(), /1章待确认/);
          const saved = (await state()).savedProject;
          await band.locator('[data-writing-step="body"]').click();
          await page.waitForFunction(() => window.__preparation.state.stage === 'editor' && window.__preparation.state.project.activeSectionId === 'section_interactive_second');
          assert.deepEqual((await state()).savedProject, saved);
        } else if (scenario === 'workflow-materials') {
          assert.match(await band.innerText(), /资料待确认/);
          await band.locator('[data-writing-step="materials"]').click();
          await page.waitForFunction(() => window.__preparation.state.stage === 'materials');
          await page.getByRole('button', { name: '生成检索条件', exact: true }).waitFor();
        } else if (scenario === 'workflow-states') {
          assert.deepEqual(palette.map(item => item.state), ['complete', 'complete', 'complete', 'running', 'skipped', 'waiting']);
          const shades = await band.locator('li[data-stage="body"]').evaluate(node => {
            const original = node.dataset.state, button = node.querySelector('button'), transition = button.style.transition, result = [];
            button.style.transition = 'none'; // Compare settled paints, independently of inherited hover transitions.
            for (const state of ['waiting', 'running', 'skipped', 'complete']) { node.dataset.state = state; result.push(getComputedStyle(button).backgroundColor); }
            node.dataset.state = original; button.style.transition = transition; return result;
          });
          assert.equal(new Set(shades).size, 4, 'Four genuinely different state shades');
        } else if (scenario === 'workflow-untimed') {
          assert.equal(await band.locator('.writing-progress-duration').filter({ hasText: '耗时未记录' }).count(), 6);
        } else if (scenario === 'workflow-confirmed') {
          assert.equal(await band.locator('.writing-progress-reminder').count(), 0);
          assert.equal(await band.locator('.writing-progress-notice-badge').count(), 0);
          await band.locator('[data-writing-step="brief"]').focus();
          await page.keyboard.press('End');
          assert.equal(await page.locator(':focus').getAttribute('data-writing-step'), 'review');
          await page.keyboard.press('Home');
          assert.equal(await page.locator(':focus').getAttribute('data-writing-step'), 'brief');
          await page.keyboard.press('ArrowRight');
          assert.equal(await page.locator(':focus').getAttribute('data-writing-step'), 'materials');
          await page.keyboard.press('Enter');
          await page.waitForFunction(() => window.__preparation.state.stage === 'materials');
        } else {
          assert.equal(await band.locator('.writing-progress-reminder').count(), 0);
          assert.match(await band.innerText(), /全自动/);
          await band.locator('[data-writing-step="outline"]').click();
          assert(await page.getByRole('textbox', { name: '第 1 章标题', exact: true }).isDisabled());
        }
        await clean(); result.cases.push({ theme, scenario, width: 1440, passed: true });
      } finally { await context.close(); }
    }
    if (!before) for (const width of [768, 560]) {
      const { context, page, band, clean } = await open(theme, 'workflow-review', width);
      try {
        const box = await band.boundingBox(); assert(box.x >= -1 && box.x + box.width <= width + 1, 'Workflow band fits viewport');
        if (edge) await assertEdge(band);
        for (const button of await band.locator('li>button').all()) assert((await button.boundingBox()).height >= 56);
        const list = band.locator('ol'), scrollable = await list.evaluate(node => node.scrollWidth > node.clientWidth);
        assert.equal(scrollable, width === 560);
        if (ribbon) assert.equal(await list.evaluate(node => getComputedStyle(node).columnGap), '0px');
        await band.locator('[data-writing-step="brief"]').focus(); await page.keyboard.press('End'); await page.keyboard.press('Enter');
        await page.getByRole('dialog', { name: '校验报告', exact: true }).waitFor(); await page.keyboard.press('Escape');
        const review = await band.locator('[data-writing-step="review"]').boundingBox();
        assert(review.x >= box.x && review.x + review.width <= box.x + box.width + 1, 'Current step scrolls into view');
        await band.screenshot({ path: path.join(output, `${theme}-${width}.png`) });
        await clean(); result.cases.push({ theme, scenario: 'responsive-focus-navigation', width, passed: true });
      } finally { await context.close(); }
    }
  }
  result.status = 'passed';
} catch (error) {
  result.status = 'failed'; result.error = String(error); result.stack = error.stack; process.exitCode = 1;
} finally {
  await browser.close(); await fs.writeFile(path.join(output, 'acceptance.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ status: result.status, cases: result.cases.length, error: result.error, stack: result.stack, realApiCalls: 0, realModelCalls: 0 }));
}
