import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(path.resolve('frontend/package.json'));
const { parse } = require('@babel/parser');
const before = process.argv[2];
assert(before, 'Provide the comparison source directory as the first argument');
const after = process.argv[3] || 'frontend/src';
const out = process.argv[4] || 'output/prompt-current-view/hmr-hooks.json';

function hookSignature(source, jsx) {
  const tree = parse(source, { sourceType: 'module', plugins: jsx ? ['jsx'] : [] });
  const result = [];
  function walk(node, parent) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && /^use[A-Z]/u.test(node.callee.name)) {
      result.push({
        hook: node.callee.name,
        binding: parent?.type === 'VariableDeclarator' ? source.slice(parent.id.start, parent.id.end) : '',
        initial: ['useState', 'useRef'].includes(node.callee.name) && node.arguments[0] ? source.slice(node.arguments[0].start, node.arguments[0].end) : '',
      });
    }
    for (const [key, value] of Object.entries(node)) {
      if (['loc', 'start', 'end', 'comments', 'tokens'].includes(key)) continue;
      if (Array.isArray(value)) for (const child of value) walk(child, node);
      else if (value && typeof value === 'object') walk(value, node);
    }
  }
  walk(tree, null); return result;
}
const files = [];
for (const name of ['usePromptLibrary.js', 'PromptWorkspace.jsx']) {
  const oldSource = await fs.readFile(path.join(before, name), 'utf8');
  const newSource = await fs.readFile(path.join(after, name), 'utf8');
  const previous = hookSignature(oldSource, name.endsWith('jsx'));
  const current = hookSignature(newSource, name.endsWith('jsx'));
  assert.deepEqual(current, previous, `${name}: HMR hook call order, binding or initializer changed`);
  files.push({ name, hooks: current, status: 'passed' });
}
await fs.mkdir(path.dirname(out), { recursive: true });
await fs.writeFile(out, JSON.stringify({ status: 'passed', files }, null, 2));
console.log(JSON.stringify({ status: 'passed', files: files.map(file => ({ name: file.name, calls: file.hooks.length })) }));
