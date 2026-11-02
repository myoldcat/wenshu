import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import WritingWorkspace from '../src/Writing';
import { blankWritingProject } from '../src/useWriting';
import '../src/styles.css';
import '../src/themes.css';
import '../src/jade-theme.css';
import '../src/glass-theme.css';
import '../src/civic-theme.css';
import '../src/theme-layout.css';

const theme = new URLSearchParams(location.search).get('theme');
document.documentElement.dataset.theme = ['jade', 'glass', 'civic'].includes(theme) ? theme : 'jade';
const requests = [], mutations = [], opened = [];
window.fetch = async (url, options = {}) => { requests.push({ url: String(url), method: options.method || 'GET' }); throw new Error('This isolated fixture permits no HTTP requests.'); };
const titles = { A: '实际引用资料甲', B: '实际引用资料乙', unused: '已选但未引用资料', old: '旧章保留的原始资料' };
const pin = id => ({ sourceId: id, revisionId: `revision-${id}`, title: titles[id] });
const evidence = (id, sourceId) => ({ id, ...pin(sourceId), blockId: `block-${sourceId}`, quote: '这是写作时保存的原文。', start: 0, end: 12, locator: { paragraph: 1 } });
const chapter = (id, title, content, entries, extra = {}) => ({ id, title, content, goal: '只读检查实际引用列表。', mode: 'inherit', words: 600, targetWords: 600, locked: false, manual: false, evidence: entries, ...extra });
const fixture = {
  ...blankWritingProject(), id: 'writing-cited-sources-fixture', version: 7, title: '正文实际引用列表', topic: '实际引用与编号',
  mode: 'strict', automation: 'manual', sourceIds: ['A', 'B', 'unused'], sources: ['A', 'B', 'unused'].map(pin),
  materialSelection: { confirmed: true, userSourceIds: ['A', 'B', 'unused'], passages: [], queries: [], items: [] },
  outlineConfirmed: true, runs: [], illustrations: [], activeSectionId: 'first',
  sections: [
    chapter('first', '第一章：只引用资料乙', '第一段引用资料乙。[E1]\n\n第二段仅承接上文，不含引文。', [evidence('E1', 'B'), evidence('E9', 'unused')]),
    chapter('second', '第二章：引用甲与乙', '这一章引用资料甲。[E1]\n\n随后引用资料乙。[E2]', [evidence('E1', 'A'), evidence('E2', 'B')]),
    chapter('archived', '第三章：保留旧原文', '原有章节仍引用当时的资料。[E1]', [evidence('E1', 'old')], {
      evidenceHash: 'fixture-evidence-hash', citationSourceScopeValid: true,
      citationSourceScope: { policy: 'preserved-chapter-evidence-scope-v1', sectionId: 'archived', evidenceHash: 'fixture-evidence-hash', sources: [pin('old')] },
    }),
  ],
};
// Keep the exact codepoint anchor independent of the fixture's prose length.
fixture.sections.forEach(section => section.evidence.forEach(item => { item.end = Array.from(item.quote).length; }));
const blocked = name => async () => { mutations.push(name); throw new Error(`Unexpected writing action: ${name}`); };
function Harness() {
  const [project, setProject] = useState(fixture), [stage, setStage] = useState('editor');
  const writing = {
    project, savedProject: fixture, projects: [fixture], setProject, busy: null, loading: false, dirty: false,
    error: '', pending: false, remoteChanged: false,
    save: blocked('save'), confirmStep: blocked('confirmStep'), review: blocked('review'),
    generateSection: blocked('generateSection'), generateOutline: blocked('generateOutline'), startAutomatic: blocked('startAutomatic'),
  };
  window.__citedSources = { get project() { return project; }, requests, mutations, opened, ready: true };
  return <div className="cited-sources-shell"><WritingWorkspace stage={stage} onStageChange={setStage} writing={writing}
    documents={['A', 'B', 'unused'].map(id => ({ id, title: `当前库标题：${titles[id]}` }))} models={[]}
    onOpenDoc={value => opened.push(value)}/></div>;
}
createRoot(document.getElementById('cited-sources-root')).render(<Harness/>);
