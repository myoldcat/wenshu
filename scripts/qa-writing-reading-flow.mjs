import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:4173';
const out = path.resolve(process.argv[3] || 'output/writing-reading-flow/browser-staged');
const caseFilter = process.argv[4] ? new RegExp(process.argv[4]) : null;
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'msedge' } : {}) });
const results = { status: 'running', cases: [], realApiCalls: 0, realModelCalls: 0 };
const themes = ['jade', 'glass', 'civic', 'demo'];

async function frames(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function run(theme, name, scenario, action, { width = 1440, height = 1050 } = {}) {
  if (caseFilter && !caseFilter.test(name)) return;
  const context = await browser.newContext({ viewport: { width, height }, reducedMotion: 'reduce' });
  const leaks = [], errors = [];
  await context.route('**/api/**', route => {
    leaks.push(route.request().url());
    results.realApiCalls += 1;
    return route.abort();
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  try {
    await page.goto(`${base}/tests/writing-preparation-hook.html?theme=${theme}&scenario=${scenario}`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => window.__preparation?.state.project.id && !window.__preparation.state.loading);
    const before = await page.evaluate(() => ({ saved: window.__preparation.state.savedProject, stored: window.__preparation.savedProjects }));
    const band = page.locator('[data-writing-progress]');
    const step = id => band.locator(`[data-writing-step="${id}"]`);
    await band.waitFor();
    await action({ page, band, step, before, theme });
    await frames(page);
    if (await band.count()) await readableRibbon(band);
    assert.deepEqual(leaks, [], 'All API requests stay inside the isolated harness');
    assert.deepEqual(errors, [], 'No browser runtime errors');
    assert.deepEqual(await page.evaluate(() => window.__preparation.unexpected), [], 'No unsupported API route');
    const after = await page.evaluate(() => ({ saved: window.__preparation.state.savedProject, stored: window.__preparation.savedProjects,
      dirty: window.__preparation.state.dirty, error: window.__preparation.state.error,
      writes: window.__preparation.requests.filter(request => request.method !== 'GET') }));
    assert.deepEqual(after.saved, before.saved, 'Browsing preserves the saved manuscript, report and confirmations');
    assert.deepEqual(after.stored, before.stored, 'No persisted project changes');
    assert.equal(after.dirty, false, 'Browsing creates no unsaved edit');
    assert.equal(after.error, '');
    assert.deepEqual(after.writes, [], 'Reading, navigation and panel controls never save, confirm, search or call a model');
    results.cases.push({ theme, name, scenario, width, passed: true });
  } catch (error) {
    await page.screenshot({ path: path.join(out, `${theme}-${name}-${width}-failed.png`), animations: 'disabled' });
    results.cases.push({ theme, name, scenario, width, passed: false, error: error.message });
    throw new Error(`${theme}/${name}/${width}: ${error.message}`, { cause: error });
  } finally {
    await context.close();
  }
}

async function noEditorSummary(page) {
  assert.equal(await page.locator('.writing-step-footer').count(), 0, 'The extra editor chapter/word-count summary bar is removed');
  const content = page.locator('.writing-editor-view');
  if (await content.count()) assert(!/已完成\s*\d+\s*步|(?:6\s*\/\s*6|7\s*\/\s*7)\s*(?:步|流程)/.test(await content.innerText()), 'No duplicate completed-step summary above the article');
  assert.equal(await page.locator('.writing-flow-actions').getByRole('button', { name: '校验报告', exact: true }).count(), 0, 'Review is opened through the footer step');
}

async function metric(band, id, value, pattern) {
  const item = band.locator(`[data-writing-metric="${id}"]`);
  assert.equal(await item.count(), 1, `${id} has one saved-state metric`);
  assert.equal(await item.getAttribute('data-metric-value'), String(value));
  assert.match(await item.innerText(), pattern);
  assert(await item.isVisible());
  assert(await item.getAttribute('title'), 'The full metric remains available as a tooltip');
  assert(await item.evaluate(node => {
    const bounds = node.getBoundingClientRect(), segment = node.closest('button').getBoundingClientRect();
    return bounds.top >= segment.top - 1 && bounds.bottom <= segment.bottom + 1;
  }), 'Metric text stays inside its fixed ribbon segment');
}

async function readableRibbon(band) {
  const rows = await band.locator('li[data-stage]').evaluateAll(nodes => nodes.map(node => {
    const rect = item => { const bounds = item.getBoundingClientRect(); return { top: bounds.top, bottom: bounds.bottom, left: bounds.left, right: bounds.right }; };
    const heading = node.querySelector('.writing-progress-step-heading strong'), duration = node.querySelector('.writing-progress-duration');
    const metric = node.querySelector('.writing-progress-metric'), button = node.querySelector('button');
    return { titleSize: parseFloat(getComputedStyle(heading).fontSize), gap: parseFloat(getComputedStyle(node.querySelector('.writing-progress-step-text')).rowGap),
      titleClipped: heading.scrollWidth > heading.clientWidth + 1, heading: rect(heading),
      secondarySizes: [duration, metric].filter(Boolean).map(item => parseFloat(getComputedStyle(item).fontSize)),
      duration: duration ? { ...rect(duration), description: duration.title, clocks: duration.querySelectorAll('svg').length } : null,
      button: rect(button), notices: [...node.querySelectorAll('.writing-progress-notice-badge,.writing-progress-inline-notice')].map(item => ({
        ...rect(item), size: parseFloat(getComputedStyle(item).fontSize), clippedVertically: item.scrollHeight > item.clientHeight + 1,
      })) };
  }));
  for (const row of rows) {
    assert.equal(row.titleSize, 15, 'Stage titles use the enlarged readable font');
    assert.equal(row.gap, 4, 'The three text rows retain the larger line spacing');
    assert.equal(row.titleClipped, false, 'A four-character stage title stays complete beside elapsed time');
    assert(row.secondarySizes.every(size => size === 13), 'Metrics and elapsed time use the enlarged secondary font');
    if (row.duration) {
      assert(row.heading.right <= row.duration.left + 1, 'The stage title and trailing elapsed time do not overlap');
      assert(row.duration.description && row.duration.clocks === 1, 'A narrowed duration keeps its clock and full tooltip');
    }
    for (const notice of row.notices) {
      assert.equal(notice.size, 12, 'Pending notices retain a readable font');
      assert.equal(notice.clippedVertically, false, 'The reminder line is not vertically clipped');
      assert(notice.top >= row.button.top - 1 && notice.bottom <= row.button.bottom + 1, 'Reminders stay inside the taller fixed segment');
    }
  }
}

async function stateIcons(band, expected) {
  const values = await band.locator('li[data-stage]').evaluateAll(nodes => nodes.map(node => {
    const button = node.querySelector('button'), number = node.querySelector('.writing-progress-number');
    return { state: node.dataset.state, icons: number.querySelectorAll('svg').length, icon: number.innerHTML,
      numberText: number.textContent.trim(), visible: button.innerText, title: number.title,
      accessible: button.getAttribute('aria-label'), tooltip: button.title };
  }));
  assert.equal(values.length, 6);
  if (expected) assert.deepEqual(values.map(value => value.state), expected);
  for (const value of values) {
    assert.equal(value.icons, 1, `${value.state} uses a state icon`);
    assert.equal(value.numberText, '', 'A state icon replaces the visible number/status text');
    assert(value.title && value.accessible && value.tooltip, 'State meaning remains available to keyboard and assistive technology');
    assert(!/已完成/.test(value.visible), 'The visible repeated completed label is removed');
    assert(!/查看/.test(value.visible), 'Browse actions remain in accessible names instead of repeated visible text');
  }
  assert.equal(await band.locator('.writing-progress-step-action').count(), 0, 'The compact ribbon has no action or arrow row');
  assert(!/用时/.test(await band.innerText()), 'Duration text uses a clock icon instead of a repeated prefix');
  return values;
}

async function previewOverlays(page, view) {
  const content = view.locator('.writing-preview-content');
  const outlineToggle = content.locator('button[aria-controls^="writing-preview-outline-"]');
  const referencesToggle = content.locator('button[aria-controls^="writing-preview-references-"]');
  assert.equal(await content.count(), 1, 'The article has its own overlay area');
  assert.equal(await outlineToggle.count(), 1);
  assert.equal(await referencesToggle.count(), 1);
  assert.equal(await page.locator('.writing-flow-header button[aria-controls^="writing-preview-"]').count(), 0, 'Sidebar controls are inside the article rather than the header');
  for (const [toggle, panel] of [[outlineToggle, '文章目录'], [referencesToggle, '参考文献']]) {
    assert.equal((await toggle.innerText()).trim(), '', 'The overlay control is an icon without a visible label');
    assert.equal(await toggle.locator('svg').count(), 1);
    const label = await toggle.getAttribute('aria-label');
    assert.match(label, new RegExp(`^(收起|展开)${panel}$`));
    assert.equal(await toggle.getAttribute('title'), label, 'Icon controls keep complete accessible names and tooltips');
    const controlId = await toggle.getAttribute('aria-controls');
    assert(await view.locator(`[id="${controlId}"]`).count(), 'The control points to its own sidebar');
  }
  const geometry = await content.evaluate(node => {
    const rect = item => { const bounds = item.getBoundingClientRect(); return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }; };
    return { content: rect(node), article: rect(node.querySelector('.writing-preview-article')),
      outline: rect(node.querySelector('button[aria-controls^="writing-preview-outline-"]')),
      references: rect(node.querySelector('button[aria-controls^="writing-preview-references-"]')) };
  });
  assert(Math.abs(geometry.article.y - geometry.content.y - 32) < 2, 'A fixed blank strip separates the overlay controls from the article scroller');
  for (const bounds of [geometry.outline, geometry.references]) {
    assert(bounds.y >= geometry.content.y - 1 && bounds.y <= geometry.content.y + 24, 'Overlay icons stay in the upper article corners');
    assert(bounds.width >= 26 && bounds.width <= 44 && Math.abs(bounds.width - bounds.height) < 2, 'Overlay hit targets stay compact and square');
    assert(bounds.y + bounds.height <= geometry.article.y + 1, 'Sidebar controls do not cover the scrolling manuscript');
  }
  assert(geometry.outline.x >= geometry.content.x - 1 && geometry.outline.x <= geometry.content.x + 24, 'The outline toggle uses the upper left article corner');
  const right = geometry.content.x + geometry.content.width - geometry.references.x - geometry.references.width;
  assert(right >= -1 && right <= 24, 'The references toggle uses the upper right article corner');
  return { content, outlineToggle, referencesToggle };
}

async function fullPageReview(page, step) {
  await step('review').click();
  const review = page.locator('.writing-review-page');
  await review.waitFor();
  assert(await review.isVisible());
  assert.equal(await page.getByRole('dialog').count(), 0, 'The saved report opens as a workspace page');
  assert.equal(await review.evaluate(node => !!node.closest('[role="dialog"],.writing-dialog,.modal-overlay')), false);
  await noEditorSummary(page);
  return review;
}

async function preview(page, step) {
  await step('body').click();
  const view = page.locator('.writing-preview');
  await view.waitFor();
  assert(await view.isVisible(), 'A completed body opens full-text preview');
  await view.locator('.writing-preview-content [aria-controls^="writing-preview-outline-"]').waitFor();
  assert.equal(await page.getByRole('dialog').count(), 0);
  const references = view.locator('aside[aria-label="全文引用文献"]');
  assert.equal(await references.count(), 1);
  assert.equal(await references.locator('.writing-preview-material-scroll > section').count(), 1, 'The references pane contains only the full-document citation list');
  assert.equal(await view.locator('[aria-label="本段对应材料"],.writing-preview-material-context').count(), 0, 'Per-paragraph material cards are removed');
  return view;
}

async function readingPosition(page) {
  return page.locator('.writing-preview').evaluate(node => {
    const article = node.querySelector('.writing-preview-article'), selected = node.querySelector('.writing-preview-paragraph.selected');
    const heading = node.querySelector('.writing-preview-nav [aria-current="location"] strong');
    return { top: article.scrollTop, width: article.clientWidth, max: article.scrollHeight - article.clientHeight,
      paragraph: selected?.textContent, chapter: heading?.textContent };
  });
}

try {
  for (const theme of themes) {
    await run(theme, 'completed-metrics', 'workflow-confirmed', async ({ page, band }) => {
      await noEditorSummary(page);
      assert.equal(await page.locator('.writing-preview:visible').count(), 1, 'Opening an article with a completed body defaults to full-text reading');
      await metric(band, 'brief', 'manual', /交互模式/);
      await metric(band, 'materials', 2, /2\s*篇资料/);
      await metric(band, 'outline', 2, /共\s*2\s*章/);
      await metric(band, 'body', 2, /2\s*\/\s*2\s*章/);
      await metric(band, 'images', 1, /1\s*张配图/);
      await stateIcons(band, ['complete', 'complete', 'complete', 'complete', 'complete', 'complete']);
      const duration = band.locator('[data-writing-step="body"] .writing-progress-duration');
      assert.equal(await duration.locator('svg').count(), 1, 'Recorded duration has a clock icon');
      assert.equal((await duration.innerText()).trim(), '2分36秒');
      assert.match(await duration.getAttribute('title'), /用时 2分36秒/, 'The complete duration description stays available as a tooltip');
      if (theme === 'glass') {
        const frame = await page.locator('.app-main.view-writing').evaluate(node => {
          const main = node.getBoundingClientRect(), ribbon = node.querySelector('[data-writing-progress]').getBoundingClientRect();
          return { radius: parseFloat(getComputedStyle(node).borderBottomLeftRadius), bottom: main.bottom, ribbonBottom: ribbon.bottom, viewport: innerHeight };
        });
        assert(frame.radius > 0 && frame.bottom < frame.viewport && frame.ribbonBottom <= frame.bottom + 1, 'The blue ribbon stays inside the rounded main frame');
      }
      assert.match(await band.locator('[data-writing-step="brief"]').getAttribute('aria-label'), /查看/, 'The arrow retains its accessible action name');
      await page.screenshot({ path: path.join(out, `${theme}-ribbon-metrics.png`), animations: 'disabled' });
    });

    await run(theme, 'card-palette-proportions', 'workflow-review', async ({ page, band, step, before }) => {
      await fullPageReview(page, step);
      const footer = await band.evaluate(node => {
        const resolve = (parent, property) => {
          const probe = document.createElement('span');
          probe.style.cssText = 'position:absolute;visibility:hidden;width:0;height:0;pointer-events:none';
          probe.style.backgroundColor = `var(${property})`;
          parent.append(probe);
          const color = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return color;
        };
        return { theme: document.documentElement.dataset.theme,
          rows: [...node.querySelectorAll('li[data-stage]')].map(item => {
            const button = item.querySelector('button');
            return { stage: item.dataset.stage, state: item.dataset.state,
              color: resolve(item, '--writing-step-color'), gray: resolve(item, '--writing-spectrum-skipped'),
              grayDeep: resolve(item, '--writing-spectrum-skipped-deep'),
              viewed: button.getAttribute('aria-current') === 'step',
              marker: getComputedStyle(button, '::after').backgroundColor,
              iconColor: getComputedStyle(item.querySelector('.writing-progress-number')).color };
          }) };
      });
      const skipped = footer.rows.find(item => item.stage === 'review');
      assert.equal(skipped.state, 'skipped');
      assert.equal(skipped.viewed, true);
      assert.equal(skipped.color, skipped.gray, 'A skipped footer step uses the shared neutral palette');
      assert.equal(skipped.marker, skipped.grayDeep, 'The viewed-step marker remains gray when review was skipped');
      assert.equal(skipped.iconColor, skipped.grayDeep, 'The skipped-state icon shares the neutral marker color');

      await page.getByRole('button', { name: '返回文稿列表', exact: true }).click();
      await page.waitForFunction(() => window.__preparation.state.stage === 'projects');
      const card = page.getByRole('button', { name: before.saved.title, exact: true });
      await card.waitFor();
      const spectrum = card.locator('.writing-project-progress-spectrum');
      const overview = await spectrum.evaluate(node => ({
        theme: document.documentElement.dataset.theme,
        rows: [...node.querySelectorAll('.writing-project-progress-segment')].map(item => ({
          stage: item.dataset.stage, state: item.dataset.state,
          width: item.getBoundingClientRect().width, filled: item.querySelector('i').getBoundingClientRect().width,
          color: getComputedStyle(item.querySelector('i')).backgroundColor,
        })),
      }));
      assert.equal(overview.theme, footer.theme, 'Returning to cards preserves the actual theme, including demo');
      assert.deepEqual(overview.rows.map(item => item.stage), ['brief', 'materials', 'outline', 'body', 'images', 'review']);
      const weights = [5, 5, 10, 40, 20, 20], width = overview.rows.reduce((sum, item) => sum + item.width, 0);
      assert(width > 0);
      overview.rows.forEach((item, index) => {
        assert(Math.abs(item.width - width * weights[index] / 100) <= 1, `${item.stage} uses its intended share of the track`);
        assert(Math.abs(item.filled - item.width) <= 1, 'A completed or explicitly skipped phase advances the overview');
        assert.equal(item.state, footer.rows[index].state, 'Card state follows the same saved workflow as the footer');
        assert.equal(item.color, footer.rows[index].color, 'Filled card phases share footer palette tokens, including neutral skipped phases');
      });
      assert(overview.rows[3].width / width < .6, 'Body writing occupies less than the previous sixty percent');
      assert(overview.rows[4].width / width > .1 && overview.rows[5].width / width > .1, 'Images and review each gain space beyond the previous ten percent');
      assert.equal(await card.locator('progress[aria-label="写作进度"]').getAttribute('value'), '100', 'An explicit skip advances the workflow without asserting that checks succeeded');
      await page.screenshot({ path: path.join(out, `${theme}-card-palette-proportions.png`), animations: 'disabled' });
    });

    await run(theme, 'four-state-icons', 'workflow-states', async ({ page, band }) => {
      const values = await stateIcons(band, ['complete', 'complete', 'complete', 'running', 'skipped', 'waiting']);
      const icons = new Map(values.map(value => [value.state, value.icon]));
      assert.equal(new Set(icons.values()).size, 4, 'Waiting, running, skipped and complete have distinct icons');
      assert.equal(await band.locator('[aria-current="step"]').count(), 1);
      assert(Math.abs((await band.boundingBox()).height - 88) < 1, 'The ribbon retains its enlarged fixed desktop height');
      await noEditorSummary(page);
    });

    await run(theme, 'incomplete-body-opens-editor', 'workflow-body', async ({ page, band, step }) => {
      assert.equal(await band.locator('li[data-stage="body"]').getAttribute('data-state'), 'running');
      await step('body').click();
      await page.waitForFunction(() => window.__preparation.state.stage === 'editor');
      await page.locator('.writing-editor-view').waitFor();
      assert.equal(await page.locator('.writing-preview:visible').count(), 0, 'An unfinished body stays in chapter editing/review');
      assert.equal(await step('body').getAttribute('aria-current'), 'step');
      await metric(band, 'body', 2, /2\s*\/\s*2\s*章/);
      assert.match(await step('body').innerText(), /1章待确认/, 'Written chapters and author confirmation remain separate');
      await noEditorSummary(page);
    });

    await run(theme, 'running-automatic-metrics', 'automatic', async ({ page, band, step }) => {
      assert.equal(await page.locator('.writing-automatic:not(.is-report)').count(), 0, 'The redundant automatic-writing banner is removed');
      const interrupt = page.locator('.writing-flow-header .writing-flow-actions [data-writing-interrupt]');
      assert.equal(await interrupt.count(), 1);
      assert(await interrupt.isEnabled(), 'The header keeps a usable interruption control while automatic writing runs');
      assert.equal((await interrupt.innerText()).trim(), '中断写作');
      assert.equal(await interrupt.getAttribute('aria-label'), '中断并切换为交互式写作');
      assert.equal(await interrupt.getAttribute('title'), '中断并切换为交互式写作');
      assert(await band.evaluate(node => node.classList.contains('is-automatic')), 'The footer still identifies the automatic workflow');
      await metric(band, 'brief', 'automatic', /自动模式/);
      await metric(band, 'materials', 1, /1\s*篇资料/);
      await metric(band, 'outline', 2, /共\s*2\s*章/);
      await metric(band, 'body', 1, /1\s*\/\s*2\s*章/);
      await metric(band, 'images', 0, /0\s*张配图/);
      assert.match(await step('brief').getAttribute('title'), /自动模式/);
      await noEditorSummary(page);
    });

    await run(theme, 'generation-readonly', 'workflow-confirmed', async ({ page, band }) => {
      const load = async name => {
        await page.evaluate(name => { window.__readingFlowPreviousHook = window.__preparation.hook; window.__preparation.loadScenario(name); }, name);
        await page.waitForFunction(() => window.__preparation.hook !== window.__readingFlowPreviousHook && window.__preparation.state.project.id && !window.__preparation.state.loading);
        await frames(page);
      };
      const names = { section: '取消章节生成任务', document: '取消全文任务', edit: '取消选区修改任务' };
      for (const kind of ['section', 'document', 'edit']) {
        await load(`manual-${kind}-active`);
        const original = await page.evaluate(() => ({ saved: window.__preparation.state.savedProject, stored: window.__preparation.savedProjects }));
        assert.equal(await page.locator('.writing-root').getAttribute('data-writing-body-generation'), 'true');
        const editor = page.locator('.writing-editor-main');
        await editor.waitFor();
        assert.equal(await editor.locator('.writing-document-task').count(), 0, 'The full-generation status block is removed from the body');
        assert(!/全文生成\s*[·・]?\s*进行中|模型正在生成。期间的人工修改/.test(await editor.innerText()), 'The body does not repeat a running-task banner');
        assert.equal(await editor.locator('.writing-editor-toolbar:visible').count(), 0, 'Generation hides editing tools');
        assert.equal(await editor.locator('textarea:not([readonly]):not([disabled]),[contenteditable="true"]').count(), 0, 'No writable editor remains during queued or running body work');
        const edit = editor.getByRole('group', { name: '章节正文显示方式', exact: true }).getByRole('button', { name: '编辑', exact: true });
        if (await edit.count()) assert(await edit.isDisabled(), 'Reading cannot switch into editing while generation is active');
        const cancel = editor.locator(`.writing-generation-controls [data-writing-generation-cancel="${kind}"]`);
        assert.equal(await page.locator('[data-writing-generation-cancel]').count(), 1, 'There is a single cancellation entry');
        assert.equal(await cancel.getAttribute('aria-label'), names[kind]);
        assert(await cancel.isEnabled(), 'Cancellation stays available without opening editing');
        const position = await cancel.evaluate(node => {
          const button = node.getBoundingClientRect(), body = node.closest('.writing-editor-main').getBoundingClientRect();
          const ribbon = document.querySelector('[data-writing-progress]').getBoundingClientRect();
          return { left: button.left - body.left, bottom: body.bottom - button.bottom, clearOfRibbon: button.bottom <= ribbon.top + 1 };
        });
        assert(position.left >= -1 && position.left <= 48 && position.bottom >= -1 && position.bottom <= 64, 'Cancellation sits in the lower left corner of the body');
        assert(position.clearOfRibbon, 'The progress ribbon does not cover cancellation');
        const after = await page.evaluate(() => ({ saved: window.__preparation.state.savedProject, stored: window.__preparation.savedProjects, dirty: window.__preparation.state.dirty }));
        assert.deepEqual(after.saved, original.saved);
        assert.deepEqual(after.stored, original.stored);
        assert.equal(after.dirty, false);
      }
      await load('workflow-confirmed');
      await page.locator('.writing-flow-actions').getByRole('button', { name: '返回章节', exact: true }).click();
      const editor = page.locator('.writing-editor-main');
      await editor.waitFor();
      assert.equal(await editor.locator('.writing-editor-toolbar:visible').count(), 0, 'Normal read-only chapter viewing also hides editing tools');
      assert.equal(await editor.locator('textarea:not([readonly]):not([disabled])').count(), 0);
      await page.locator('.writing-flow-actions [data-writing-modify-step="body"]').click();
      const textarea = editor.locator('textarea.writing-body-editor');
      await textarea.waitFor();
      assert(await textarea.isEditable(), 'Explicit modification opens the existing manuscript for editing');
      for (const name of ['润色', '改写', '缩写']) assert(await editor.locator('.writing-editor-toolbar').getByRole('button', { name, exact: true }).isVisible());
      await editor.getByRole('group', { name: '章节正文显示方式', exact: true }).getByRole('button', { name: '阅读', exact: true }).click();
      assert.equal(await editor.locator('.writing-editor-toolbar:visible').count(), 0, 'Returning to reading hides the text tools again');
      await page.screenshot({ path: path.join(out, `${theme}-generation-readonly.png`), animations: 'disabled' });
      assert.equal(await band.locator('[data-writing-step="body"]').getAttribute('aria-current'), 'step');
    });

    for (const scenario of ['workflow-review', 'workflow-stale', 'workflow-skipped', 'workflow-unchecked']) {
      await run(theme, `full-page-${scenario}`, scenario, async ({ page, band, step }) => {
        const review = await fullPageReview(page, step);
        if (scenario === 'workflow-unchecked') {
          assert.equal(await review.locator('.writing-review-report').count(), 0);
          assert(await review.getByRole('button', { name: '开始校验', exact: true }).isVisible());
          assert(await review.getByRole('button', { name: '跳过校验', exact: true }).isVisible());
        } else {
          const report = review.locator('.writing-review-report');
          await report.waitFor();
          if (scenario === 'workflow-stale') {
            assert.match(await report.locator('.writing-review-summary').innerText(), /此前校验记录/);
            assert.match(await step('review').innerText(), /过期/);
            assert(!/2项待确认/.test(await step('review').innerText()), 'A stale report does not present an old pending count as current');
          } else if (scenario === 'workflow-skipped') {
            assert.match(await report.locator('.writing-review-summary').innerText(), /已跳过校验/);
            assert.equal(await report.locator('.writing-review-results>article').count(), 0, 'Skipping does not invent successful checks');
          } else {
            assert.match(await report.locator('.writing-review-summary').innerText(), /已跳过校验/);
            assert.match(await report.locator('.writing-review-summary').innerText(), /2 项待确认/);
            assert.equal(await report.locator('.writing-review-results>article').count(), 3);
            assert.match(await step('review').innerText(), /2项待确认/, 'Skipped review retains unresolved saved issues');
          }
        }
        assert.equal(await band.locator('[aria-current="step"]').count(), 1);
        assert.equal(await step('review').getAttribute('aria-current'), 'step');
        await page.screenshot({ path: path.join(out, `${theme}-${scenario}-page.png`), animations: 'disabled' });
        if (scenario === 'workflow-review') {
          await review.getByRole('button', { name: '查看对应正文', exact: true }).first().click();
          await page.locator('.writing-preview').waitFor();
          assert.match(await page.locator('.writing-preview-nav [aria-current="location"]').innerText(), /第一章/);
          assert.equal(await page.getByRole('dialog').count(), 0);
          await fullPageReview(page, step);
          assert.match(await step('review').innerText(), /2项待确认/);
        }
      });
    }

    await run(theme, 'preview-paragraph-toggle-and-top-space', 'workflow-reading-long', async ({ page, step }) => {
      const view = await preview(page, step);
      const { content } = await previewOverlays(page, view);
      await view.locator('.writing-preview-nav-scroll>button').nth(1).click();
      const paragraph = view.locator('.writing-preview-chapter').nth(1).locator('.writing-preview-paragraph').nth(6);
      await paragraph.scrollIntoViewIfNeeded();
      await paragraph.click({ position: { x: 24, y: 12 } });
      await frames(page);
      const selected = await readingPosition(page);
      assert.equal(await view.locator('.writing-preview-paragraph.selected').count(), 1);
      const panels = await view.evaluate(node => [node.dataset.outlineCollapsed, node.dataset.referencesCollapsed]);
      await paragraph.click({ position: { x: 24, y: 12 } });
      await frames(page);
      assert.equal(await view.locator('.writing-preview-paragraph.selected').count(), 0, 'Clicking the same paragraph again clears its highlight');
      const cleared = await readingPosition(page);
      assert.equal(cleared.chapter, selected.chapter);
      assert(Math.abs(cleared.top - selected.top) < 2, 'Clearing a highlight does not move the reading position');
      assert.deepEqual(await view.evaluate(node => [node.dataset.outlineCollapsed, node.dataset.referencesCollapsed]), panels);
      const citation = paragraph.locator('.writing-citation-icon').first();
      await citation.hover();
      assert.equal(await view.locator('.writing-preview-paragraph.selected').count(), 0, 'Source hover does not undo a cleared paragraph selection');
      await page.getByRole('button', { name: '关闭引用来源', exact: true }).click();
      await paragraph.click({ position: { x: 24, y: 12 } });
      assert.equal(await view.locator('.writing-preview-paragraph.selected').count(), 1);
      await citation.click();
      assert.equal(await view.locator('.writing-preview-paragraph.selected').count(), 1, 'Clicking a citation is not a paragraph deselection');
      await citation.focus();
      await citation.press('ArrowDown');
      assert.equal(await view.locator('.writing-preview-paragraph.selected').count(), 1, 'Keyboard source navigation keeps the paragraph selected');
      await page.getByRole('button', { name: '关闭引用来源', exact: true }).click();
      const article = content.locator('.writing-preview-article');
      const top = (await article.boundingBox()).y;
      await article.evaluate(node => { node.scrollTop += 120; });
      await frames(page);
      assert(Math.abs((await article.boundingBox()).y - top) < 1, 'Preview whitespace stays outside the scroller while reading moves');
      await previewOverlays(page, view);
      await page.locator('.writing-flow-actions').getByRole('button', { name: '返回章节', exact: true }).click();
      await page.locator('.writing-chapter-list>button').first().click();
      const editor = page.locator('.writing-editor-main'), scroll = editor.locator('.writing-editor-scroll');
      const checkGap = async () => {
        assert.equal(await scroll.evaluate(node => parseFloat(getComputedStyle(node).marginTop)), 20, 'Chapter reading and editing reserve an outer twenty-pixel gap');
        const original = (await scroll.boundingBox()).y;
        await scroll.evaluate(node => { node.scrollTop += 120; });
        await frames(page);
        assert(Math.abs((await scroll.boundingBox()).y - original) < 1, 'The chapter gap remains fixed when manuscript content scrolls');
      };
      await checkGap();
      await page.locator('.writing-flow-actions [data-writing-modify-step="body"]').click();
      assert(await editor.locator('textarea.writing-body-editor').isEditable());
      await checkGap();
      await page.screenshot({ path: path.join(out, `${theme}-paragraph-toggle-top-space.png`), animations: 'disabled' });
    });

    await run(theme, 'preview-collapse-reading-position', 'workflow-reading-long', async ({ page, step }) => {
      const view = await preview(page, step);
      const outline = view.locator('.writing-preview-nav'), references = view.locator('.writing-preview-materials');
      const { outlineToggle, referencesToggle } = await previewOverlays(page, view);
      assert.equal(await outlineToggle.getAttribute('aria-expanded'), 'true');
      assert.equal(await referencesToggle.getAttribute('aria-expanded'), 'true');
      const referenceList = await references.innerText();
      const chapters = await page.evaluate(() => window.__preparation.state.project.sections.map(section => ({ id: section.id, title: section.title })));
      await outline.getByRole('button').filter({ hasText: chapters[1].title }).click();
      const paragraph = view.locator('.writing-preview-chapter').nth(1).locator('.writing-preview-paragraph').nth(8);
      await paragraph.scrollIntoViewIfNeeded();
      await paragraph.click({ position: { x: 24, y: 12 } });
      await frames(page);
      const position = await readingPosition(page);
      assert(position.max > 500 && position.top > 0, 'The isolated long manuscript exercises real article scrolling');
      assert.equal(position.chapter, chapters[1].title);
      assert(position.paragraph, 'The selected paragraph remains associated with its source');
      assert.equal(await references.innerText(), referenceList, 'Paragraph selection does not add material detail to the full-reference list');

      await outlineToggle.click();
      await frames(page);
      assert.equal(await outlineToggle.getAttribute('aria-expanded'), 'false');
      assert.equal(await outlineToggle.getAttribute('aria-label'), '展开文章目录');
      assert.equal(await outline.isVisible(), false);
      const oneClosed = await readingPosition(page);
      assert(oneClosed.width > position.width + 100, 'Collapsing the chapter directory increases reading width');
      await referencesToggle.click();
      await frames(page);
      assert.equal(await referencesToggle.getAttribute('aria-expanded'), 'false');
      assert.equal(await referencesToggle.getAttribute('aria-label'), '展开参考文献');
      assert.equal(await references.isVisible(), false);
      const bothClosed = await readingPosition(page);
      assert(bothClosed.width > oneClosed.width + 100, 'Collapsing references provides more article width');
      assert.equal(bothClosed.chapter, position.chapter);
      assert.equal(bothClosed.paragraph, position.paragraph);
      await page.screenshot({ path: path.join(out, `${theme}-preview-both-collapsed.png`), animations: 'disabled' });

      await outlineToggle.click();
      await referencesToggle.click();
      await frames(page);
      assert.equal(await outlineToggle.getAttribute('aria-expanded'), 'true');
      assert.equal(await referencesToggle.getAttribute('aria-expanded'), 'true');
      await previewOverlays(page, view);
      assert(await outline.isVisible() && await references.isVisible());
      const restored = await readingPosition(page);
      assert.equal(restored.chapter, position.chapter);
      assert.equal(restored.paragraph, position.paragraph);
      assert(Math.abs(restored.width - position.width) < 2);
      assert(Math.abs(restored.top - position.top) < 4, 'Restoring sidebars preserves the reading position');
      await noEditorSummary(page);
      await page.screenshot({ path: path.join(out, `${theme}-preview-restored.png`), animations: 'disabled' });
      await page.locator('.writing-flow-actions').getByRole('button', { name: '返回章节', exact: true }).click();
      await page.locator('.writing-editor-view').waitFor();
      assert.equal(await page.locator('.writing-preview:visible').count(), 0, 'Returning to chapters explicitly opens the chapter view');
      assert.equal(await page.evaluate(() => window.__preparation.state.project.activeSectionId), chapters[1].id, 'Returning targets the chapter being read');
      await preview(page, step);
    });

    await run(theme, 'narrow-footer-review', 'workflow-review', async ({ page, band, step }) => {
      assert(Math.abs((await band.boundingBox()).height - 96) < 1, 'The narrow ribbon keeps its enlarged fixed scroll area');
      await metric(band, 'body', 2, /2\s*\/\s*2\s*章/);
      const review = await fullPageReview(page, step);
      assert(await review.locator('.writing-review-report').isVisible());
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert(overflow <= 1, 'Review content fits the narrow workspace');
      await page.screenshot({ path: path.join(out, `${theme}-review-narrow.png`), animations: 'disabled' });
      const view = await preview(page, step);
      const header = page.locator('.writing-flow-header'), headerHeight = (await header.boundingBox()).height;
      assert(headerHeight <= 88, 'Narrow metadata stays on one line instead of expanding the toolbar');
      const { outlineToggle, referencesToggle } = await previewOverlays(page, view);
      await outlineToggle.click();
      await referencesToggle.click();
      await frames(page);
      assert(Math.abs((await header.boundingBox()).height - headerHeight) < 1, 'Article overlays do not change header height');
      await previewOverlays(page, view);
      const headerBox = await header.boundingBox(), previewBox = await view.boundingBox();
      assert(Math.abs(previewBox.y - headerBox.y - headerBox.height) < 2, 'No separate toolbar row remains above the preview');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({ path: path.join(out, `${theme}-preview-toolbar-narrow.png`), animations: 'disabled' });
    }, { width: 390, height: 900 });
  }
  results.status = 'passed';
} catch (error) {
  results.status = 'failed';
  results.error = String(error.stack || error);
  process.exitCode = 1;
} finally {
  await fs.writeFile(path.join(out, 'results.json'), JSON.stringify(results, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ status: results.status, cases: results.cases.length, error: results.error, realApiCalls: results.realApiCalls, realModelCalls: results.realModelCalls }));
