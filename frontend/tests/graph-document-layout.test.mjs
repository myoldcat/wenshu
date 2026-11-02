import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { Rect } = require('@antv/g6');
const { TextService } = require('@antv/g');
// Exercise the actual document class without mounting React or changing the
// production module's exports. Material drawing is outside this text-layout
// test; body dimensions below were read from each shipped atlas' alpha bounds.
const canvasSource = readFileSync(new URL('../src/G6GraphCanvas.jsx', import.meta.url), 'utf8');
const documentSource = canvasSource.slice(canvasSource.indexOf('class WenshuDocument extends Rect'), canvasSource.indexOf('\nclass WenshuRelation'));
const WenshuDocument = new Function('Rect', 'drawMaterialSkin', 'resizeMaterialSkin', 'cancelMaterialMotion', `${documentSource}; return WenshuDocument;`)(Rect, () => {}, () => {}, () => {});
const bodies = { warm: { width: 436, height: 180 }, liquid: { width: 466, height: 170 }, metal: { width: 462, height: 184 } };
const titles = ['城市公共服务数字化转型与治理机制研究报告', 'InternationalGovernanceAndPublicServiceResearchReport', '人工智能 AI Governance 2026 综合研究与实践'];

for (const [material, body] of Object.entries(bodies)) {
  test(`${material}: real capsule bounds constrain document icon and single-line bilingual text`, () => {
    for (const title of titles) {
      const node = new WenshuDocument({ style: {
        size: [180, 60], label: false, icon: true, iconText: 'D', iconWidth: 23, iconHeight: 23, iconX: -65,
        materialSprite: { body }, documentTitle: title, subtitleText: 'International Public Service Research Institute 公共服务研究机构',
        themeFont: 'sans-serif', themeInk: '#303a33', themeMuted: '#65746b',
      } });
      try {
        const visibleWidth = body.width * Math.min(180 / body.width, 60 / body.height);
        assert.ok(visibleWidth < 180, 'the PNG capsule is narrower than its key/hit area');
        const icon = node.getIconStyle(node.attributes), content = node.getDocumentContentBounds(node.attributes);
        assert.ok(icon.x - icon.width / 2 >= -visibleWidth / 2 + 11.9, 'icon stays inside the visible capsule');
        assert.ok(content.textX >= icon.x + icon.width / 2 + 7.9, 'icon and text retain their gap');
        for (const name of ['document-title', 'document-source']) {
          const text = node.getShape(name), style = text.attributes;
          assert.equal(style.wordWrap, true); assert.equal(style.maxLines, 1); assert.equal(style.textOverflow, 'ellipsis');
          assert.ok(style.x + style.wordWrapWidth <= visibleWidth / 2 - 11.9, 'text budget ends inside the capsule');
          // Run G's real word wrapping/ellipsis implementation with explicit
          // synthetic font metrics. Browser QA still verifies actual font pixels.
          const measure = value => Array.from(value).reduce((width, char) => width + (/[\u2e80-\u9fff]/u.test(char) ? Number(style.fontSize) : Number(style.fontSize) * .58), 0);
          const service = new TextService({ offscreenCanvasCreator: { getOrCreateContext: () => ({ measureText: value => ({ width: measure(value) }) }) } });
          const line = service.wordWrap(String(style.text), { ...style, letterSpacing: 0 }, null);
          assert.ok(line.endsWith('...'), 'Chinese, mixed and unbroken English titles receive ellipsis');
          assert.ok(!line.includes('\n') && measure(line) <= style.wordWrapWidth + .001, 'rendered line fits the real available width');
        }
        for (const opacity of [.18, 1]) {
          node.update({ opacity });
          for (const name of ['document-title', 'document-source']) {
            assert.equal(node.getShape(name).attributes.opacity, opacity, 'custom document text follows dimming and restores completely');
            assert.equal(node.getShape(name).attributes.wordWrapWidth, content.textWidth, 'dimming preserves document text geometry');
          }
        }
      } finally { node.destroy(); }
    }
  });
}
