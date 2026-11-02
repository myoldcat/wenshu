import assert from 'node:assert/strict';
import test from 'node:test';
import { isDemoMode, demoDestination } from '../src/demo-mode.js';

test('deployments without a demo default retain normal entry and explicit demo links', () => {
  for (const defaultTheme of [undefined, '', 'civic', 'jade', 'glass', 'unknown']) {
    assert.equal(isDemoMode('', defaultTheme), false);
    assert.equal(isDemoMode('?theme=unknown', defaultTheme), false);
    assert.equal(isDemoMode('?theme=demo', defaultTheme), true);
  }
});

test('demo deployment defaults apply without requiring a theme query', () => {
  for (const search of ['', '?other=1', '?theme=', '?theme=unknown']) {
    assert.equal(isDemoMode(search, 'demo'), true);
  }
});

test('valid explicit theme links override the deployment default', () => {
  for (const theme of ['jade', 'glass', 'civic']) {
    assert.equal(isDemoMode(`?other=1&theme=${theme}`, 'demo'), false);
  }
  assert.equal(isDemoMode('?theme=demo', 'demo'), true);
});

test('the deployment default keeps demo navigation restrictions and explicit themes restore normal navigation', () => {
  for (const destination of ['home', 'knowledge', 'images', 'templates', 'prompts', 'library', 'writing']) {
    assert.equal(demoDestination(destination, isDemoMode('', 'demo')), destination === 'writing' ? 'writing' : 'library');
    assert.equal(demoDestination(destination, isDemoMode('?theme=civic', 'demo')), destination);
  }
});
