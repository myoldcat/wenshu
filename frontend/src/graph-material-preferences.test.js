import test from 'node:test';
import assert from 'node:assert/strict';
import { GRAPH_MATERIAL_STORAGE_KEY, readGraphMaterial, writeGraphMaterial } from './graph-material-preferences.js';

function memoryStorage(values = []) {
  const data = new Map(values);
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), data };
}

test('all themes default to transparent without applying the former global choice', () => {
  const storage = memoryStorage([[GRAPH_MATERIAL_STORAGE_KEY, 'metal']]);
  assert.equal(readGraphMaterial('jade', storage), 'liquid');
  assert.equal(readGraphMaterial('glass', storage), 'liquid');
  assert.equal(readGraphMaterial('civic', storage), 'liquid');
  assert.equal(storage.data.size, 1, 'reading defaults must not write a manual override');
});

test('manual choices survive a fresh read and remain independent across themes', () => {
  const storage = memoryStorage();
  writeGraphMaterial('metal', 'jade', storage);
  writeGraphMaterial('warm', 'glass', storage);
  assert.equal(readGraphMaterial('jade', storage), 'metal');
  assert.equal(readGraphMaterial('glass', storage), 'warm');
  assert.equal(readGraphMaterial('civic', storage), 'liquid');
  writeGraphMaterial('liquid', 'civic', storage);
  assert.equal(readGraphMaterial('jade', storage), 'metal');
  assert.equal(readGraphMaterial('civic', storage), 'liquid');
});

test('invalid stored choices and unavailable storage fall back to the current theme', () => {
  const invalid = memoryStorage([['koohub-graph-material:glass', 'unknown']]);
  assert.equal(readGraphMaterial('glass', invalid), 'liquid');
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.equal(readGraphMaterial('civic', blocked), 'liquid');
  assert.equal(writeGraphMaterial('liquid', 'jade', blocked), 'liquid');
  assert.equal(writeGraphMaterial('invalid', 'glass', blocked), 'liquid');
  assert.equal(readGraphMaterial('unknown', blocked), 'liquid');
});
