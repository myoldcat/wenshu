import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingWorkspace from '../src/Writing';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';

const options = new URLSearchParams(window.location.search);
document.documentElement.dataset.theme = ['jade', 'glass', 'civic'].includes(options.get('theme')) ? options.get('theme') : 'jade';
const output = document.getElementById('outline-test-result'), root = createRoot(document.getElementById('outline-test-root'));
const results = [], requests = [], unexpectedActions = [], adopted = [];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const assert = (condition, message) => { if (!condition) throw new Error(message); };
async function until(predicate, label) { for (let count = 0; count < 200; count += 1) { if (predicate()) return; await wait(20); } throw new Error(`Timed out: ${label}`); }
const existingSections = [{ id: 'existing-chapter', title: '保留的人工章节', goal: '原有的写作边界', mode: 'inherit', words: 500, version: 3, content: '必须逐字保留的人工正文。', locked: true, manual: true, evidence: [] }];
const candidateSections = [{ id: 'new-chapter', title: '已有事实整理', goal: '并列整理原文明确记载的事实。', mode: 'inherit', words: 500 }];
const baseCandidate = { sections: candidateSections, issues: [], outlinePlanningProtocol: 'evidence-plan-v1', outlinePlanningPending: false, outlinePlanningState: 'supported', outlinePlanningIssues: [] };
const scenarios = {
  empty: { ...baseCandidate, sections: [], outlinePlanningState: 'insufficient', issues: ['现有资料没有覆盖所要求的事件，请补充相关原文。'] },
  gap: { ...baseCandidate, outlinePlanningState: 'needs_review', outlinePlanningIssues: [{ sectionId: 'section_private123', sectionTitle: '影响分析', field: 'goal', sentence: '事件将导致长期政策变化。', status: 'unsupported', message: '资料未明确说明长期政策变化。', reason: 'P1 只记载本次事件，E2 不能支持后续影响。' }], issues: ['outlinePlanningProtocol evidence-plan-v1 supportBasis section_private123'] },
  pending: { ...baseCandidate, outlinePlanningPending: true, outlinePlanningState: 'pending' },
  supported: baseCandidate,
  legacy: { sections: candidateSections, issues: ['旧候选继续保留原有采纳流程。'] },
  dirty: baseCandidate,
};
let current, state;
// This fixture has no hook, API, model, save or document write path. A mistaken
// service request or unrelated action fails instead of reaching a real project.
window.fetch = async (url, init = {}) => { requests.push({ url: String(url), method: init.method || 'GET' }); throw new Error(`Unexpected fixture request: ${url}`); };
function Harness({ scenario }) {
  const [project, setProject] = useState(() => ({ id: `outline-fixture-${scenario}`, title: '纲要材料核对隔离样本', topic: '整理已知事实', version: 7, mode: 'strict', workflow: 'chapter', activeSectionId: 'existing-chapter', sourceIds: [], sources: [], brief: {}, outlineConfirmed: false, createdAt: '2026-10-23T00:00:00Z', updatedAt: '2026-10-23T00:00:00Z', sections: structuredClone(existingSections), runs: [{ id: `run-${scenario}`, kind: 'outline', status: 'succeeded', applied: false, createdAt: '2026-10-23T00:00:00Z', candidate: structuredClone(scenarios[scenario]) }] }));
  state = project;
  const blocked = name => () => { unexpectedActions.push(name); throw new Error(`Unexpected fixture action: ${name}`); };
  const writing = { project, setProject, projects: [], busy: '', loading: false, dirty: scenario === 'dirty', error: null, pending: false, remoteChanged: false, generationPlan: null, planLoading: false, planError: null, clearError() {}, applyRun: async id => { adopted.push(id); return null; }, save: blocked('save'), confirmOutline: blocked('confirm-outline'), confirmOutlineOnly: blocked('confirm-outline-only'), generateOutline: blocked('generate-outline'), cancelRun: blocked('cancel') };
  return <div style={{ height: 'calc(100vh - 70px)' }}><WritingWorkspace stage="outline" onStageChange={blocked('navigate')} writing={writing}/></div>;
}
const button = (text, container = document) => [...container.querySelectorAll('button')].find(item => item.textContent.trim() === text);
const dialog = () => document.querySelector('[role="dialog"][aria-label="比较生成候选"]');
function passed(name) { results.push({ name, status: 'passed' }); output.textContent = JSON.stringify({ status: 'running', results }, null, 2); }
async function openScenario(scenario) {
  current = scenario;
  root.render(<Harness key={scenario} scenario={scenario}/>);
  await until(() => state?.id === `outline-fixture-${scenario}` && button('查看纲要候选'), `${scenario} candidate banner`);
  await wait(40); // Allow project-change effects to settle before opening dialog.
  button('查看纲要候选').click();
  await until(dialog, `${scenario} compare dialog`);
}
async function run() {
  for (const scenario of ['empty', 'gap', 'pending']) {
    await openScenario(scenario);
    const adopt = button('采用纲要候选', dialog()), before = adopted.length;
    assert(adopt?.disabled, `${scenario}: unsupported/pending outline can be adopted`);
    adopt.click(); await wait(20);
    assert(adopted.length === before, `${scenario}: disabled candidate reached applyRun`);
    assert(dialog().querySelector('[role="status"]')?.textContent.includes(scenario === 'empty' ? '现有资料不足' : scenario === 'gap' ? '尚未得到材料支持' : '正在核对'), `${scenario}: missing clear author notice`);
    assert(JSON.stringify(state.sections) === JSON.stringify(existingSections), `${scenario}: existing manual text or locks changed`);
    passed(`${scenario}: clear material notice, disabled adoption and unchanged manual chapter`);
    if (scenario === 'empty') {
      assert(dialog().textContent.includes('现有资料尚不足以形成可采用的纲要。') && dialog().textContent.includes('现有资料没有覆盖所要求的事件'), 'Empty outline lost the reason or fallback');
      passed('empty outline keeps material explanation visible with no generated sections');
    }
    if (scenario === 'gap') {
      const issues = dialog().querySelector('[aria-label="纲要材料说明"]')?.textContent || '';
      assert(issues.includes('影响分析') && issues.includes('资料未明确说明长期政策变化') && issues.includes('不能支持后续影响'), 'Unsupported planning explanation lost chapter or evidence limitation');
      assert(!/\b(?:E|P)\d+\b|section_private123|evidence-plan|outlinePlanning|supportBasis/.test(dialog().textContent), 'Internal protocol or evidence identifiers leaked into the candidate UI');
      passed('planning gaps identify the affected chapter and redact internal identifiers');
    }
  }
  for (const scenario of ['supported', 'legacy']) {
    await openScenario(scenario);
    const adopt = button('采用纲要候选', dialog()), before = adopted.length;
    assert(adopt && !adopt.disabled, `${scenario}: valid/legacy candidate was unnecessarily blocked`);
    assert(!dialog().querySelector('[role="status"]'), `${scenario}: incorrect material gap warning`);
    adopt.click(); await until(() => adopted.length === before + 1, `${scenario} controlled adoption intent`);
    assert(adopted.at(-1) === `run-${scenario}`, `${scenario}: wrong candidate adopted`);
    assert(JSON.stringify(state.sections) === JSON.stringify(existingSections), `${scenario}: controlled adoption altered live-like prose or locks`);
    passed(`${scenario}: existing candidate adoption remains available without changing current manual text`);
  }
  await openScenario('dirty');
  const before = adopted.length;
  assert(button('采用纲要候选', dialog()).disabled, 'Supported outline bypassed unsaved-draft guard');
  button('采用纲要候选', dialog()).click(); await wait(20);
  assert(adopted.length === before && JSON.stringify(state.sections) === JSON.stringify(existingSections), 'Unsaved candidate modified manual content or lock');
  assert(!requests.length && !unexpectedActions.length, 'Fixture accessed a service, model, save, or unrelated action');
  passed('unsaved draft guard stays in place; no network, save or model action occurs');
  if (options.get('manual')) await openScenario(options.get('manual') in scenarios ? options.get('manual') : 'gap');
  output.dataset.status = 'passed';
  output.textContent = JSON.stringify({ status: 'passed', theme: document.documentElement.dataset.theme, tests: results.length, results, adopted, requests, unexpectedActions }, null, 2);
}
run().catch(error => { output.dataset.status = 'failed'; output.textContent = JSON.stringify({ status: 'failed', scenario: current, message: error.message, results, requests, unexpectedActions }, null, 2); console.error(error); });
