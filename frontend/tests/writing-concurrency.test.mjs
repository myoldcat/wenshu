import test from 'node:test';
import assert from 'node:assert/strict';
import { isWritingProjectActive, writingProjectCardStatus } from '../src/writing-projects.js';
import { automaticRunning, deriveAutomaticProgress } from '../src/writing-automatic.js';

test('three running manuscripts and a queued fourth keep independent visible states', () => {
  const projects = Array.from({ length: 4 }, (_, index) => ({
    id: `concurrent-${index + 1}`,
    automatic: { status: index < 3 ? 'running' : 'queued', stage: 'body' },
    runs: [{ kind: 'automatic', status: index < 3 ? 'running' : 'queued' }],
    sections: [],
  }));
  assert.deepEqual(projects.map(isWritingProjectActive), [true, true, true, false]);
  assert.deepEqual(projects.map(writingProjectCardStatus), ['正在写作', '正在写作', '正在写作', '排队中']);
  assert.ok(projects.every(project => automaticRunning(project.automatic)));
  const queued = deriveAutomaticProgress(projects[3].automatic, projects[3]);
  assert.match(queued.steps.find(step => step.id === queued.currentId).detail, /排队/);
  assert.ok(queued.steps.every(step => step.stateLabel !== '进行中'));
  projects[0].runs[0].status = 'succeeded';
  projects[3].runs[0].status = 'running';
  projects[3].automatic.status = 'running';
  assert.equal(writingProjectCardStatus(projects[3]), '正在写作');
  assert.equal(isWritingProjectActive(projects[3]), true);
  assert.equal(deriveAutomaticProgress(projects[3].automatic).currentId, 'body');
});
