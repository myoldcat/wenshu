import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './local-api';
import { writingMessage } from './writing-messages';
import { canAcceptWritingSnapshot } from './writing-snapshot';
import { reviewRequiresDraft } from './writing-export-policy';
import { imageCandidateBlock, imageCandidatePlacements, imageParagraphs, imagePlacementMode, imageSearchCurrent } from './writing-images';
import { newAutomaticWritingOptions } from './writing-automatic';
import { usesDocumentWriting } from './writing-workflow';
import { DEFAULT_STRUCTURE_TEMPLATE, structureTemplatePatch, writingStylePatch } from './writing-presets';
import { writingProjectPayload as projectBody, writingOutlinePayload as outlineOf,
  writingEditableFingerprint as editable, writingProjectFingerprint, writingOutlineFingerprint, writingProjectPatch } from './writing-semantic';

export const blankWritingProject = () => ({ id: null, title: '', articleTitle: '', assistanceIds: [], topic: '', purpose: '', targetWords: 6000, ...structureTemplatePatch(DEFAULT_STRUCTURE_TEMPLATE), ...writingStylePatch('official'), mode: 'strict', workflow: 'chapter', automation: 'automatic', automaticOptions: newAutomaticWritingOptions(), modelId: '', sourceIds: [], materialSelection: { recommendationId: null, passages: [] }, sources: [], sections: [], runs: [], illustrations: [], illustrationReview: null, outlineConfirmed: false, activeSectionId: '', version: 0 });
const normalize = value => {
  const normalized = { ...blankWritingProject(), style: '清晰克制', template: '观点文章', templateSections: [], ...(typeof value.brief === 'object' ? value.brief : { purpose: value.brief || '' }), ...value, automaticOptions: value.automaticOptions ?? value.brief?.automaticOptions ?? null, articleTitle: Object.hasOwn(value.brief || {}, 'articleTitle') ? value.brief.articleTitle : value.title || '', workflow: value.brief?.workflow === 'oneshot' ? 'oneshot' : 'chapter', automation: value.brief?.automation === 'automatic' ? 'automatic' : 'manual', mode: value.mode === 'extended' ? 'expanded' : value.mode, sections: (value.sections || []).map(section => ({ ...section, mode: section.mode === 'extended' ? 'expanded' : section.mode || 'inherit', words: section.targetWords || 600, content: section.content || '' })) };
  // New defaults belong only to new drafts, never historical records.
  for (const key of ['structureTemplate', 'writingStyle']) {
    if (!Object.hasOwn(value.brief || {}, key) && !Object.hasOwn(value, key)) delete normalized[key];
  }
  return normalized;
};
const activeRun = run => ['queued', 'running'].includes(run.status);
const emptyTitleAssistance = () => ({ status: 'idle', titles: [], selectedTitle: '', error: '', assistanceId: null });
const titleAssistanceScope = value => JSON.stringify([value.id, value.topic || '', value.purpose || '', Number(value.targetWords), value.structureTemplate, value.writingStyle, value.articleTitle || '']);
const titleCandidateScope = value => JSON.stringify([value.id, value.topic || '', value.purpose || '', Number(value.targetWords), value.structureTemplate, value.writingStyle]);
const TITLE_CHOICE_REQUIRED = '请先选择拟定题目，或明确使用当前题目。';

export function useWriting() {
  const [project, setProjectState] = useState(blankWritingProject);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [deletingProjectId, setDeletingProjectId] = useState(''), [deleteProjectError, setDeleteProjectError] = useState(null);
  const [remoteChanged, setRemoteChanged] = useState(false);
  const [capabilities, setCapabilities] = useState({});
  const [savedFingerprint, setSavedFingerprint] = useState('');
  const [generationPlan, setGenerationPlan] = useState(null), [planLoading, setPlanLoading] = useState(false), [planError, setPlanError] = useState('');
  const [titleAssistance, setTitleAssistanceState] = useState(emptyTitleAssistance);
  const [requestGeneration, setRequestGeneration] = useState(0);
  const current = useRef(project), baseline = useRef(null), observedSnapshot = useRef(null), epoch = useRef(0), requestEpoch = useRef(0), mounted = useRef(true), busyRef = useRef(false);
  const pendingReview = useRef(null);
  const titleAssistanceRef = useRef(titleAssistance);
  const deletingRef = useRef(''), deletedProjects = useRef(new Set());
  current.current = project;
  const dirty = project.id ? editable(project) !== savedFingerprint : Boolean(project.topic || project.title || project.purpose || project.sections.length);
  const setTitleAssistance = useCallback(value => { titleAssistanceRef.current = value; setTitleAssistanceState(value); }, []);
  const titleChoiceUnresolved = () => {
    const assistance = titleAssistanceRef.current;
    return ['pending', 'choosing', 'applying', 'error'].includes(assistance.status)
      || ['selected', 'kept'].includes(assistance.status) && (!current.current.articleTitle?.trim() || assistance.scope !== titleCandidateScope(current.current));
  };
  const setProject = useCallback(change => {
    const previous = current.current, next = typeof change === 'function' ? change(previous) : change;
    if (editable(next) !== editable(previous)) epoch.current += 1;
    current.current = next; setProjectState(next);
  }, []);
  useEffect(() => {
    if (titleAssistance.status === 'applying' && titleAssistance.selectedTitle === project.articleTitle
      && titleAssistance.token === requestEpoch.current && titleAssistance.context === titleAssistanceScope(project)) {
      setTitleAssistance({ ...titleAssistance, status: 'selected' });
    }
  }, [project, titleAssistance, setTitleAssistance]);
  const beginRequestSession = () => {
    setTitleAssistance(emptyTitleAssistance());
    const token = ++requestEpoch.current;
    setRequestGeneration(token);
    return token;
  };
  const remember = useCallback(value => {
    const next = normalize(value);
    if (!deletedProjects.current.has(next.id) && !value.deletedAt) setProjects(items => [next, ...items.filter(item => item.id !== next.id)].sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || '')));
    return next;
  }, []);
  const adopt = useCallback((value, activeId) => {
    if (deletedProjects.current.has(value.id) || value.deletedAt) return current.current;
    if (current.current.id === value.id && !canAcceptWritingSnapshot(value, current.current, baseline.current, observedSnapshot.current)) return current.current;
    const next = remember(value);
    next.activeSectionId = next.sections.some(section => section.id === activeId) ? activeId : next.sections[0]?.id || '';
    observedSnapshot.current = { id: next.id, version: next.version };
    baseline.current = next; current.current = next; setProjectState(next); setSavedFingerprint(editable(next)); setRemoteChanged(false);
    if (next.capabilities) setCapabilities(next.capabilities);
    return next;
  }, [remember]);
  const refreshList = useCallback(async () => {
    const result = await api('/writing/projects');
    if (!mounted.current) return;
    setProjects((result.projects || []).filter(item => !item.deletedAt && !deletedProjects.current.has(item.id)).map(normalize));
    if (result.capabilities) setCapabilities(result.capabilities);
    return result;
  }, []);
  useEffect(() => {
    mounted.current = true;
    const initialEpoch = epoch.current, initialRequest = requestEpoch.current;
    refreshList().then(async result => {
      if (!mounted.current || epoch.current !== initialEpoch || requestEpoch.current !== initialRequest) return;
      if (result?.projects?.length) { const detail = await api(`/writing/projects/${result.projects[0].id}`); if (mounted.current && epoch.current === initialEpoch && requestEpoch.current === initialRequest) adopt(detail.project); }
      setError('');
    }).catch(problem => { if (mounted.current) setError(writingMessage(problem)); }).finally(() => { if (mounted.current) setLoading(false); });
    return () => { mounted.current = false; requestEpoch.current += 1; };
  }, [adopt, refreshList]);
  useEffect(() => {
    const warn = event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => {
    let generation = 0;
    const refreshAvailability = async () => {
      const id = current.current.id, token = requestEpoch.current, request = ++generation;
      if (!id) return;
      try {
        const result = await api(`/writing/projects/${encodeURIComponent(id)}`);
        if (!mounted.current || request !== generation || token !== requestEpoch.current || current.current.id !== id) return;
        // A library change must never adopt server prose over an unsaved draft,
        // select a different chapter, or create a save/review/model request.
        const availability = result.project.libraryAvailability;
        if (!availability) return;
        const next = { ...current.current, libraryAvailability: availability };
        if (baseline.current?.id === id) baseline.current = { ...baseline.current, libraryAvailability: availability };
        current.current = next; setProjectState(next);
      } catch { /* Reading remains available when a library refresh fails. */ }
    };
    window.addEventListener('wenshu-library-changed', refreshAvailability);
    return () => { generation += 1; window.removeEventListener('wenshu-library-changed', refreshAvailability); };
  }, []);
  const runAction = async (name, action) => {
    if (busyRef.current) return null;
    busyRef.current = true; setBusy(name); setError('');
    try { return await action(); }
    catch (problem) { if (mounted.current) setError(writingMessage(problem)); return null; }
    finally { busyRef.current = false; if (mounted.current) setBusy(''); }
  };
  const checkpoint = (saved, snapshot, token, assignSectionIds = false) => {
    if (!mounted.current || requestEpoch.current !== token) return;
    const before = current.current;
    const idMap = new Map(snapshot.sections.map((section, index) => [section.id, assignSectionIds && section.id?.startsWith('draft-') ? saved.sections[index]?.id || section.id : section.id]));
    const sections = before.sections.map(section => {
      const id = idMap.get(section.id) || section.id, serverSection = saved.sections.find(item => item.id === id);
      return serverSection ? { ...serverSection, title: section.title, goal: section.goal, mode: section.mode, words: section.words, content: section.content, locked: section.locked } : { ...section, id };
    });
    const next = { ...before, id: saved.id, version: saved.version, sources: saved.sources, libraryAvailability: saved.libraryAvailability, runs: saved.runs, review: saved.review, illustrations: saved.illustrations || [], illustrationReview: saved.illustrationReview, createdAt: saved.createdAt, updatedAt: saved.updatedAt, outlineConfirmed: saved.outlineConfirmed, workflowPendingSteps: saved.workflowPendingSteps, sections, activeSectionId: idMap.get(before.activeSectionId) || before.activeSectionId };
    const assistance = titleAssistanceRef.current;
    if (snapshot.id !== saved.id && assistance.scope === titleCandidateScope(snapshot)
      && assistance.scope === titleCandidateScope({ ...next, id: snapshot.id })) {
      setTitleAssistance({ ...assistance, scope: titleCandidateScope(next),
        context: assistance.context === titleAssistanceScope(snapshot) ? titleAssistanceScope(next) : assistance.context });
    }
    snapshot.id = saved.id; snapshot.sections = snapshot.sections.map(section => ({ ...section, id: idMap.get(section.id) || section.id }));
    baseline.current = saved; current.current = next; setProjectState(next); setSavedFingerprint(editable(saved)); remember(saved);
    if (saved.capabilities) setCapabilities(saved.capabilities);
  };
  const saveInternal = async () => {
    const snapshot = { ...current.current, sections: current.current.sections.map(section => ({ ...section })) }, editEpoch = epoch.current, token = requestEpoch.current;
    if (!(snapshot.title || snapshot.topic || '').trim()) throw new Error('请先填写写作主题。');
    if (snapshot.id && baseline.current && editable(snapshot) === editable(baseline.current)) {
      const saved = baseline.current;
      if (mounted.current && requestEpoch.current === token && epoch.current === editEpoch) adopt(saved, snapshot.activeSectionId);
      return saved;
    }
    let result, saved;
    if (!snapshot.id) {
      result = await api('/writing/projects', { method: 'POST', body: JSON.stringify(projectBody(snapshot)) });
      saved = normalize(result.project);
      checkpoint(saved, snapshot, token);
    } else {
      saved = baseline.current || snapshot;
      if (writingProjectFingerprint(snapshot) !== writingProjectFingerprint(saved)) {
        result = await api(`/writing/projects/${snapshot.id}`, { method: 'PATCH', body: JSON.stringify({ ...writingProjectPatch(snapshot, saved), expectedVersion: saved.version }) });
        saved = normalize(result.project);
        checkpoint(saved, snapshot, token);
      }
    }
    // Unlock first: outline changes to locked sections are correctly rejected by the service.
    for (const draft of snapshot.sections) {
      const existing = saved.sections.find(section => section.id === draft.id);
      if (existing?.locked && !draft.locked) {
        result = await api(`/writing/projects/${saved.id}/sections/${existing.id}`, { method: 'PATCH', body: JSON.stringify({ expectedVersion: saved.version, locked: false }) });
        saved = normalize(result.project); checkpoint(saved, snapshot, token);
      }
    }
    if (writingOutlineFingerprint(snapshot) !== writingOutlineFingerprint(saved)) {
      result = await api(`/writing/projects/${saved.id}/outline`, { method: 'PUT', body: JSON.stringify({ expectedVersion: saved.version, sections: outlineOf(snapshot) }) });
      saved = normalize(result.project);
      checkpoint(saved, snapshot, token, true);
    }
    for (let index = 0; index < snapshot.sections.length; index += 1) {
      const draft = snapshot.sections[index];
      let existing = saved.sections.find(section => section.id === draft.id) || saved.sections[index];
      if (!existing) continue;
      if (existing.locked && !draft.locked) {
        result = await api(`/writing/projects/${saved.id}/sections/${existing.id}`, { method: 'PATCH', body: JSON.stringify({ expectedVersion: saved.version, locked: false }) });
        saved = normalize(result.project); checkpoint(saved, snapshot, token); existing = saved.sections.find(section => section.id === existing.id);
      }
      if (draft.content !== existing.content || !!draft.locked !== !!existing.locked) {
        const changes = { expectedVersion: saved.version };
        if (draft.content !== existing.content) changes.content = draft.content || '';
        if (!!draft.locked !== !!existing.locked) changes.locked = !!draft.locked;
        result = await api(`/writing/projects/${saved.id}/sections/${existing.id}`, { method: 'PATCH', body: JSON.stringify(changes) });
        saved = normalize(result.project);
        checkpoint(saved, snapshot, token);
      }
    }
    if (result?.capabilities) setCapabilities(result.capabilities);
    if (!mounted.current || requestEpoch.current !== token) return saved;
    remember(saved);
    if (epoch.current === editEpoch) adopt(saved, snapshot.activeSectionId);
    else { baseline.current = saved; setSavedFingerprint(editable(saved)); }
    return saved;
  };
  const save = () => runAction('save', saveInternal);
  const action = (kind, path, body = {}) => runAction(kind, async () => {
    if (titleChoiceUnresolved() && ['outline', 'confirm', 'confirm-only', 'document'].includes(kind)) throw new Error(TITLE_CHOICE_REQUIRED);
    const token = requestEpoch.current, editEpoch = epoch.current;
    const snapshot = await saveInternal();
    if (token !== requestEpoch.current || editEpoch !== epoch.current) throw new Error('保存期间有新的修改，请保存后再次执行。');
    const result = await api(`/writing/projects/${snapshot.id}/${typeof path === 'function' ? path(snapshot) : path}`, { method: 'POST', body: JSON.stringify({ ...body, expectedVersion: snapshot.version }) });
    if (mounted.current && token === requestEpoch.current && result.project) {
      if (epoch.current === editEpoch) adopt(result.project, current.current.activeSectionId);
      else { remember(result.project); mergeRunStatus(result.project); setRemoteChanged(true); }
    }
    return result;
  });
  const confirmStep = step => {
    if (!['brief', 'materials', 'body'].includes(step)) { setError('请选择可确认的写作步骤。'); return Promise.resolve(null); }
    if (step === 'brief' && titleChoiceUnresolved()) { setError(TITLE_CHOICE_REQUIRED); return Promise.resolve(null); }
    return action('confirm-step', `steps/${step}/confirm`);
  };
  const generateTitle = () => runAction('title', async () => {
    const before = current.current, token = requestEpoch.current, editEpoch = epoch.current;
    if (!before.topic?.trim()) throw new Error('请先填写写作主题。');
    const context = titleAssistanceScope(before), scope = titleCandidateScope(before);
    setTitleAssistance({ ...emptyTitleAssistance(), status: 'pending', token, context, scope });
    try {
      const result = await api('/writing/assist/title', { method: 'POST', body: JSON.stringify({ topic: before.topic, purpose: before.purpose || '', title: before.articleTitle || '', targetWords: Number(before.targetWords), ...(before.structureTemplate ? { structureTemplate: before.structureTemplate } : {}), ...(before.writingStyle ? { writingStyle: before.writingStyle } : {}), candidateCount: 3 }) });
      if (!mounted.current || token !== requestEpoch.current) return null;
      if (editEpoch !== epoch.current || context !== titleAssistanceScope(current.current)) throw new Error('写作要求或题目已改变，请重新拟题，或使用当前题目。');
      const titles = Array.isArray(result?.titles) ? result.titles.map(value => typeof value === 'string' ? value.trim() : '') : [];
      if (titles.length !== 3 || titles.some(value => !value || Array.from(value).length > 200) || new Set(titles).size !== 3) throw new Error('暂未生成3个不同的可用题目，请重试。');
      setTitleAssistance({ status: 'choosing', titles, selectedTitle: '', error: '', assistanceId: result.assistanceId || null, token, context, scope });
      return result;
    } catch (problem) {
      if (!mounted.current || token !== requestEpoch.current) return null;
      setTitleAssistance({ ...emptyTitleAssistance(), status: 'error', error: writingMessage(problem), token, context, scope });
      throw problem;
    }
  });
  const selectTitle = title => {
    const assistance = titleAssistanceRef.current, before = current.current;
    if (busyRef.current || !['choosing', 'selected', 'kept'].includes(assistance.status) || !assistance.titles.includes(title)) return false;
    if (assistance.token !== requestEpoch.current || assistance.scope !== titleCandidateScope(before)) {
      const message = '写作要求或题目已改变，请重新拟题，或使用当前题目。';
      setTitleAssistance({ ...emptyTitleAssistance(), status: 'error', error: message }); setError(message); return false;
    }
    if (before.articleTitle !== title) setProject(previous => ({ ...previous, articleTitle: title, title, assistanceIds: [...new Set([...(previous.assistanceIds || []), assistance.assistanceId].filter(Boolean))] }));
    setTitleAssistance({ ...assistance, status: 'applying', selectedTitle: title, error: '', context: titleAssistanceScope(current.current) });
    setError(''); return true;
  };
  const keepCurrentTitle = () => {
    if (busyRef.current || !current.current.articleTitle?.trim()) { if (!busyRef.current) setError('请先填写文章题目。'); return false; }
    setTitleAssistance({ ...titleAssistanceRef.current, status: 'kept', selectedTitle: '', error: '',
      token: requestEpoch.current, context: titleAssistanceScope(current.current), scope: titleCandidateScope(current.current) });
    setError(''); return true;
  };
  const exportReview = () => runAction('review-export', async () => {
    const before = current.current, token = requestEpoch.current, editEpoch = epoch.current;
    if (!before.id || !before.review || dirty || before.review.version !== before.version) throw new Error('请先保存并生成当前版本的校验报告。');
    const response = await fetch('/api/writing/projects/' + before.id + '/review/export', { method: 'POST', headers: { 'X-Wenshu-Client': 'local', 'Content-Type': 'application/json' }, body: JSON.stringify({ expectedVersion: before.version, format: 'html' }) });
    if (!response.ok) { const result = await response.json().catch(() => ({})); throw new Error(typeof result.detail === 'string' ? result.detail : '报告导出失败，请稍后重试。'); }
    const blob = await response.blob();
    if (token !== requestEpoch.current || editEpoch !== epoch.current || current.current.id !== before.id || current.current.version !== before.version) throw new Error('文稿已改变，请重新导出报告。');
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = (before.title || before.topic || '文枢文稿').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_') + '·校验报告.html'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); return true;
  });
  const startAutomatic = () => {
    if (titleChoiceUnresolved()) { setError(TITLE_CHOICE_REQUIRED); return Promise.resolve(null); }
    const options = current.current.automaticOptions;
    const hasSourceOptions = options && ['useSelectedSources', 'useRecommendedSources', 'mode'].some(key => Object.hasOwn(options, key));
    if (!hasSourceOptions && current.current.mode === 'strict' && !(current.current.sourceIds || []).length) {
      setError('请先在“审阅与调整材料”中选定参考资料，再开始全自动写作。');
      return Promise.resolve(null);
    }
    return action('automatic', 'automatic');
  };
  const interruptAutomatic = () => runAction('interrupt-automatic', async () => {
    const before = current.current, token = requestEpoch.current, editEpoch = epoch.current;
    const runId = before.automatic?.runId || before.runs.find(run => run.kind === 'automatic' && activeRun(run))?.id;
    if (!before.id || !runId) throw new Error('当前没有可中断的智能化写作。');
    let result;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const latest = await api(`/writing/projects/${before.id}`);
      if (token !== requestEpoch.current || current.current.id !== before.id) return null;
      if (latest.project.automatic?.runId && latest.project.automatic.runId !== runId) throw new Error('自动写作任务已变化，请确认当前任务后再中断。');
      try {
        result = await api(`/writing/projects/${before.id}/automatic/interrupt`, { method: 'POST', body: JSON.stringify({ expectedVersion: latest.project.version, runId }) });
        break;
      } catch (problem) {
        if (problem.status !== 409 || attempt === 1) throw problem;
      }
    }
    if (!mounted.current || token !== requestEpoch.current || current.current.id !== before.id) return result;
    if (epoch.current === editEpoch) adopt(result.project, current.current.activeSectionId);
    else {
      remember(result.project); mergeRunStatus(result.project);
      setProject(previous => ({ ...previous, automation: 'manual' })); setRemoteChanged(true);
    }
    return result;
  });
  const generateEdit = (sectionId, operation, selection, instruction = '') => runAction('edit', async () => {
    const token = requestEpoch.current, editEpoch = epoch.current, before = current.current;
    const section = before.sections.find(item => item.id === sectionId);
    if (!section || section.locked) throw new Error('章节已锁定或不存在，请先解锁并保存。');
    if (!selection || selection.projectId !== before.id || selection.sectionId !== sectionId || section.content !== selection.content || section.content.slice(selection.start, selection.end) !== selection.selectedExact || !selection.selectedExact.trim()) throw new Error('选区已变化，请重新选择需要修改的文字。');
    const saved = await saveInternal();
    if (token !== requestEpoch.current || editEpoch !== epoch.current) throw new Error('保存期间有新的修改，已保留你的文字。请重新选择后操作。');
    const savedSection = saved.sections.find(item => item.id === sectionId);
    if (!savedSection || savedSection.locked || savedSection.content !== selection.content || !savedSection.contentHash) throw new Error('保存后的正文已变化，请重新选择需要修改的文字。');
    const result = await api(`/writing/projects/${saved.id}/sections/${sectionId}/edit`, { method: 'POST', body: JSON.stringify({ expectedVersion: saved.version, expectedSectionVersion: savedSection.version, baseContentHash: savedSection.contentHash, operation, selection: { start: selection.start, end: selection.end, selectedExact: selection.selectedExact, unit: 'utf16' }, instruction }) });
    if (mounted.current && token === requestEpoch.current && result.project) {
      if (epoch.current === editEpoch) adopt(result.project, current.current.activeSectionId);
      else { remember(result.project); mergeRunStatus(result.project); setRemoteChanged(true); }
    }
    return result;
  });
  const applyEdit = (runId, sectionId, projectId) => runAction('apply-edit', async () => {
    const before = current.current, token = requestEpoch.current, editEpoch = epoch.current;
    if (before.id !== projectId || before.activeSectionId !== sectionId) throw new Error('当前项目或章节已改变，请返回候选所属章节后比较。');
    if (!baseline.current || editable(before) !== editable(baseline.current)) throw new Error('当前有未保存修改，请先保存后再采纳建议。');
    const section = before.sections.find(item => item.id === sectionId), run = before.runs.find(item => item.id === runId && item.kind === 'edit');
    if (!section || section.locked) throw new Error('章节已锁定，不能采纳修改。');
    if (!run?.candidate?.edit || run.sectionId !== sectionId || run.applied || run.status !== 'succeeded') throw new Error('这份修改建议当前不可采纳。');
    if (before.version !== run.baseVersion) throw new Error('文稿版本已变化，这份修改建议已过期。请根据当前正文重新生成。');
    if (section.version !== run.editTarget?.sectionVersion || section.contentHash !== run.editTarget?.contentHash) throw new Error('本章正文已变化，这份建议已过期。请根据当前正文重新生成。');
    const edit = run.candidate.edit;
    if (section.content.slice(edit.start, edit.end) !== edit.selectedExact) throw new Error('原选区文字已变化，请重新选择并生成修改建议。');
    let result;
    try {
      result = await api(`/writing/projects/${before.id}/runs/${runId}/apply`, { method: 'POST', body: JSON.stringify({ expectedVersion: before.version }) });
    } catch (problem) {
      // A second tab may have changed the draft; keep its text untouched here.
      try {
        const fresh = await api(`/writing/projects/${before.id}`);
        if (mounted.current && token === requestEpoch.current && current.current.id === before.id) {
          mergeRunStatus(fresh.project);
          if (fresh.project.version !== before.version) setRemoteChanged(true);
        }
      } catch { /* Preserve the original apply error when refresh is unavailable. */ }
      throw problem;
    }
    if (mounted.current && token === requestEpoch.current && result.project) {
      if (epoch.current === editEpoch) adopt(result.project, current.current.activeSectionId);
      else { remember(result.project); mergeRunStatus(result.project); setRemoteChanged(true); }
    }
    return result;
  });
  const applyDocumentSection = (runId, sectionId, projectId) => runAction('apply-document', async () => {
    const before = current.current, token = requestEpoch.current, editEpoch = epoch.current;
    if (before.id !== projectId || before.activeSectionId !== sectionId) throw new Error('当前项目或章节已改变，请回到候选所属章节后比较。');
    if (!baseline.current || editable(before) !== editable(baseline.current)) throw new Error('当前有未保存修改，请先保存，候选能否采纳将按保存后的章节重新检查。');
    const section = before.sections.find(item => item.id === sectionId), run = before.runs.find(item => item.id === runId && item.kind === 'document');
    const step = run?.candidate?.document?.steps?.find(item => item.sectionId === sectionId);
    if (!section || section.locked) throw new Error('章节已锁定，不能采纳候选。');
    if (activeRun(run || {}) || step?.status !== 'candidate' || !step.section?.content) throw new Error('这份全文生成候选当前不可采纳。');
    if (step.canApply === false) throw new Error(writingMessage(step.applyBlockedReason, '章节或写作要求已变化，这份候选不能覆盖当前文稿。'));
    let result;
    try {
      result = await api(`/writing/projects/${before.id}/runs/${runId}/sections/${sectionId}/apply`, { method: 'POST', body: JSON.stringify({ expectedVersion: before.version }) });
    } catch (problem) {
      try {
        const fresh = await api(`/writing/projects/${before.id}`);
        if (mounted.current && token === requestEpoch.current && current.current.id === before.id) {
          mergeRunStatus(fresh.project);
          if (fresh.project.version !== before.version) setRemoteChanged(true);
        }
      } catch { /* Keep local text and the original conflict message. */ }
      throw problem;
    }
    if (mounted.current && token === requestEpoch.current && result.project) {
      if (epoch.current === editEpoch) adopt(result.project, current.current.activeSectionId);
      else { remember(result.project); mergeRunStatus(result.project); setRemoteChanged(true); }
    }
    return result;
  });
  const illustrationAction = (name, method, path, body, validate) => runAction(name, async () => {
    const before = current.current, token = requestEpoch.current, editEpoch = epoch.current;
    if (!before.id || !baseline.current || editable(before) !== editable(baseline.current)) throw new Error('当前有未保存修改，请先保存正文，再检索或调整配图。');
    if (before.runs.some(activeRun)) throw new Error('写作任务仍在进行，请等任务结束后调整配图。');
    if (observedSnapshot.current?.id === before.id && observedSnapshot.current.version > before.version) throw new Error('已保存文稿有更新，请重新载入并核对后调整配图。');
    validate?.(before);
    let result;
    try {
      result = await api(`/writing/projects/${before.id}/${path}`, { method, body: JSON.stringify({ ...body, expectedVersion: before.version }) });
    } catch (problem) {
      try {
        const fresh = await api(`/writing/projects/${before.id}`);
        if (mounted.current && token === requestEpoch.current && current.current.id === before.id) {
          mergeRunStatus(fresh.project);
          if (fresh.project.version !== before.version) setRemoteChanged(true);
        }
      } catch { /* Keep the draft and report the original operation failure. */ }
      throw problem;
    }
    if (!mounted.current || token !== requestEpoch.current || current.current.id !== before.id) return null;
    if (result.project) {
      if (epoch.current === editEpoch) adopt(result.project, current.current.activeSectionId);
      else { remember(result.project); mergeRunStatus(result.project); setRemoteChanged(true); }
    }
    return result;
  });
  const searchImages = ({ sectionId, paragraphIndex, query = '', modelId = null, searchScope = 'references', placementMode = 'paragraph' }) => illustrationAction('image-search', 'POST', 'image-search', { sectionId, ...(placementMode === 'paragraph' ? { paragraphIndex } : {}), query, modelId: modelId || null, searchScope, placementMode }, before => {
    const section = before.sections.find(item => item.id === sectionId);
    if (!['library', 'references'].includes(searchScope) || !['chapter', 'paragraph'].includes(placementMode)) throw new Error('请选择有效的检索范围与位置方式。');
    if (!section || !imageParagraphs(section.content).length) throw new Error('请选择已有保存正文的章节。');
    if (placementMode === 'paragraph' && (!Number.isInteger(paragraphIndex) || paragraphIndex < 0 || !imageParagraphs(section.content)[paragraphIndex])) throw new Error('请选择已保存正文中的一个段落。');
  });
  const acceptIllustration = body => illustrationAction('accept-image', 'POST', 'illustrations', body, before => {
    const run = before.runs.find(item => item.id === body.runId && item.kind === 'images');
    const search = run?.candidate?.imageSearch, candidate = search?.candidates?.find(item => item.assetId === body.assetId);
    if (run?.status !== 'succeeded' || !imageSearchCurrent(before, search) || !candidate) throw new Error('正文或图片推荐已变化，请重新检索后再选择。');
    if (before.sections.find(item => item.id === search.sectionId)?.locked) throw new Error('本章已锁定，请先解锁并保存，再插入图片。');
    const paragraphIndex = body.paragraphIndex ?? (imagePlacementMode(search) === 'paragraph' ? search.paragraphIndex : undefined);
    const placement = imageCandidatePlacements(before, search, candidate).find(item => item.paragraphIndex === paragraphIndex);
    const reason = imageCandidateBlock(candidate, body.confirmUnverified === true, { placement, requirePlacement: true, confirmLibrarySource: body.confirmLibrarySource === true });
    if (reason) throw new Error(reason);
  });
  const updateIllustration = (figureId, body) => illustrationAction('update-image', 'PATCH', `illustrations/${figureId}`, body, before => {
    const figure = before.illustrations?.find(item => item.id === figureId);
    if (!figure) throw new Error('这张文稿配图已变化，请重新载入并核对。');
    if (before.sections.find(item => item.id === figure.sectionId)?.locked) throw new Error('本章已锁定，请先解锁并保存，再调整图片。');
    if (figure.locked && !(body.locked === false && Object.keys(body).length === 1)) throw new Error('图片已锁定，请先解锁再调整。');
  });
  const removeIllustration = figureId => illustrationAction('remove-image', 'DELETE', `illustrations/${figureId}`, {}, before => {
    const figure = before.illustrations?.find(item => item.id === figureId);
    if (!figure || figure.locked) throw new Error('图片已锁定或发生变化，请先解锁并核对。');
    if (before.sections.find(item => item.id === figure.sectionId)?.locked) throw new Error('本章已锁定，请先解锁并保存，再移除图片。');
  });
  const automaticIllustrations = ({ sectionIds, searchScope = 'references', modelId = null, query = '', confirmLibrarySource = false }) => illustrationAction('image-batch', 'POST', 'image-batch', { sectionIds, searchScope, modelId: modelId || null, query, confirmLibrarySource }, before => {
    if (!Array.isArray(sectionIds) || !sectionIds.length || sectionIds.some(id => !before.sections.some(section => section.id === id && !section.locked && section.content?.trim()))) throw new Error('请选择已有保存正文且未锁定的章节。');
  });
  const insertManualIllustration = ({ sectionId, paragraphIndex, paragraphExact, contentHash, assetId, searchScope = 'references', caption, altText, confirmLibrarySource = false }) => illustrationAction('manual-image', 'POST', 'manual-image', { sectionId, paragraphIndex, paragraphExact, contentHash, assetId, searchScope, caption, altText, confirmLibrarySource }, before => {
    const section = before.sections.find(item => item.id === sectionId), paragraph = imageParagraphs(section?.content)[paragraphIndex];
    if (!section || section.locked || !Number.isInteger(paragraphIndex) || !paragraph || !assetId) throw new Error('请选择未锁定章节中的段落及图片。');
    if (paragraphExact !== undefined && paragraphExact !== paragraph || contentHash !== undefined && contentHash !== section.contentHash) throw new Error('正文已变化，请重新选择插入位置。');
  });
  const completeIllustrations = ({ skip = false, clearExisting = false } = {}) => illustrationAction('complete-images', 'POST', 'illustrations/complete', { skip, ...(clearExisting ? { clearExisting: true } : {}) }, before => {
    if (!before.sections.some(section => section.content?.trim())) throw new Error('请先编写并保存正文。');
    if (skip && before.illustrations?.length && !clearExisting) throw new Error('文稿已有配图，请核对后完成配图。');
    if (clearExisting && before.illustrations?.some(figure => figure.locked || before.sections.find(section => section.id === figure.sectionId)?.locked)) throw new Error('图片或章节已锁定，请先解锁再移除配图。');
    if (!clearExisting && before.illustrations?.some(figure => figure.status === 'stale')) throw new Error('还有图片需要重新核对，请核对图文关系后完成。');
  });
  const matchCitations = () => runAction('match-citations', async () => {
    const before = current.current, token = requestEpoch.current, editEpoch = epoch.current;
    if (!before.id || !baseline.current || editable(before) !== editable(baseline.current)) throw new Error('当前有未保存修改，请先保存后重新核对引用。');
    if (before.runs.some(activeRun)) throw new Error('写作任务仍在进行，请等任务结束后重新核对引用。');
    if (observedSnapshot.current?.id === before.id && observedSnapshot.current.version > before.version) throw new Error('已保存文稿有更新，请重新载入并核对后再重新核对引用。');
    if (!before.sections.some(section => section.content?.trim())) throw new Error('文稿尚无正文，请先完成并保存正文。');
    const result = await api(`/writing/projects/${before.id}/match-citations`, { method: 'POST', body: JSON.stringify({ expectedVersion: before.version }) });
    if (!mounted.current || token !== requestEpoch.current || current.current.id !== before.id) return null;
    if (result.project) {
      if (epoch.current === editEpoch) adopt(result.project, current.current.activeSectionId);
      else { remember(result.project); mergeRunStatus(result.project); setRemoteChanged(true); }
    }
    return result;
  });
  const reviewRules = () => runAction('review', async () => {
    if (current.current.runs.some(activeRun)) throw new Error('生成任务仍在进行，请等任务结束后校验全文。');
    const token = requestEpoch.current, editEpoch = epoch.current;
    const saved = await saveInternal();
    if (!mounted.current || token !== requestEpoch.current || editEpoch !== epoch.current) throw new Error('保存期间有新的修改，已保留你的草稿。请保存后重新校验。');
    const result = await api(`/writing/projects/${saved.id}/review`, { method: 'POST', body: JSON.stringify({ expectedVersion: saved.version }) });
    if (!mounted.current || token !== requestEpoch.current || current.current.id !== saved.id) return null;
    if (!canAcceptWritingSnapshot(result.project, current.current, baseline.current, observedSnapshot.current)) throw new Error('文稿有更新，请重新载入并核对后再校验。');
    if (epoch.current === editEpoch) adopt(result.project, current.current.activeSectionId);
    else {
      // A read-only report must never roll back a draft edited while it was running.
      remember(result.project);
      const next = { ...current.current, review: result.review || result.project.review };
      current.current = next; setProjectState(next);
    }
    return result;
  });
  const reviewDocument = async () => {
    const saved = await save(); if (!saved) return null;
    const result = await matchCitations();
    if (result) pendingReview.current = { projectId: current.current.id, runId: result.run?.id || result.project?.runs?.find(run => ['citations', 'citation_match'].includes(run.kind) && activeRun(run))?.id };
    return result;
  };
  const exportDocument = ({ format = 'docx', allowIssues = false } = {}) => runAction('export', async () => {
    const before = current.current, token = requestEpoch.current, editEpoch = epoch.current;
    if (!['docx', 'html', 'pdf'].includes(format)) throw new Error('当前支持 Word、HTML 和 PDF 导出。');
    if (before.runs.some(activeRun)) throw new Error('生成任务仍在进行，请等任务结束后校验并导出。');
    if (!before.id || !baseline.current || editable(before) !== editable(baseline.current)) throw new Error('请先保存修改，再导出当前版本。');
    if (observedSnapshot.current?.id === before.id && observedSnapshot.current.version > before.version) throw new Error('文稿有更新，请重新载入后导出。');
    if ((!before.review?.wordCounts || before.review.version !== before.version) && !allowIssues) throw new Error('当前文稿尚未完成校验，可先校验或选择忽略待核实事项后导出。');
    if (reviewRequiresDraft(before.review) && !allowIssues) throw new Error('仍有待核实事项，请先核对，或明确选择忽略后导出。');
    const response = await fetch(`/api/writing/projects/${before.id}/export`, { method: 'POST', headers: { 'X-Wenshu-Client': 'local', 'Content-Type': 'application/json' }, body: JSON.stringify({ expectedVersion: before.version, format, allowIssues: allowIssues === true }) });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.message || '导出失败，请重新校验后重试。');
    }
    if (response.headers.get('X-Wenshu-Project-Version') !== String(before.version)) throw new Error('导出的文稿版本与已校验版本不一致，已取消下载。');
    if (format === 'pdf' && response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/pdf') throw new Error('PDF 文稿暂时无法下载，请重新导出。');
    const draftHeader = response.headers.get('X-Wenshu-Draft-Required');
    const requiresDraftConsent = reviewRequiresDraft(before.review) || draftHeader === 'true';
    if (requiresDraftConsent && !allowIssues) throw new Error('导出校验发现待核实内容，请重新校验并确认是否导出草稿。');
    const blob = await response.blob();
    if (!mounted.current || token !== requestEpoch.current || editEpoch !== epoch.current || current.current.id !== before.id || current.current.version !== before.version || editable(current.current) !== editable(before) || current.current.runs.some(activeRun) || (observedSnapshot.current?.id === before.id && observedSnapshot.current.version > before.version)) throw new Error('导出期间文稿或任务状态有变化，已保留当前草稿并取消下载。请重新校验后导出。');
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = `${(before.title || '文枢文稿').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')}.${format}`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    return { format, version: before.version, reviewStatus: response.headers.get('X-Wenshu-Review-Status'), requiresDraftConsent };
  });
  const selectProject = id => runAction('open', async () => {
    if (dirty) throw new Error('当前文稿有未保存修改，请先保存，或重新载入已保存版本。');
    if (deletingRef.current === id || deletedProjects.current.has(id)) throw new Error('这份文稿已删除或正在删除，请选择其他文稿。');
    const token = beginRequestSession();
    const result = await api(`/writing/projects/${id}`);
    if (deletedProjects.current.has(id) || result.project?.deletedAt) return null;
    if (mounted.current && token === requestEpoch.current) adopt(result.project);
    return result.project;
  });
  const deleteProject = async (id, expectedVersion) => {
    if (deletingRef.current || busyRef.current || !id) return null;
    const selected = current.current.id === id ? current.current : projects.find(item => item.id === id);
    if (!selected || deletedProjects.current.has(id)) return null;
    setDeleteProjectError(null);
    if (!Number.isInteger(expectedVersion) || expectedVersion < 1) {
      setDeleteProjectError({ projectId: id, message: '暂时无法确认文稿版本，请重新载入后再删除。' }); return null;
    }
    if (selected.runs?.some(activeRun)) {
      setDeleteProjectError({ projectId: id, message: '这份文稿仍有写作任务进行中，请先取消任务或等任务结束后再删除。' }); return null;
    }
    const token = requestEpoch.current;
    deletingRef.current = id; setDeletingProjectId(id);
    try {
      const result = await api(`/writing/projects/${id}`, { method: 'DELETE', body: JSON.stringify({ expectedVersion }) });
      if (!result.deleted || result.projectId !== id) throw new Error('暂时未能确认删除结果，请重新载入文稿列表后检查。');
      deletedProjects.current.add(id);
      if (!mounted.current) return result;
      setProjects(items => items.filter(item => item.id !== id));
      // A response for another project must never clear the draft opened since
      // confirmation. Tombstones also fence already-started list/detail reads.
      if (current.current.id === id && requestEpoch.current === token) {
        beginRequestSession(); epoch.current += 1; baseline.current = null; observedSnapshot.current = null;
        const next = blankWritingProject(); current.current = next; setProjectState(next);
        setSavedFingerprint(''); setRemoteChanged(false); setError('');
      }
      return result;
    } catch (problem) {
      if (mounted.current) setDeleteProjectError({ projectId: id, message: writingMessage(problem) });
      return null;
    } finally {
      deletingRef.current = '';
      if (mounted.current) setDeletingProjectId('');
    }
  };
  const newProject = sourceIds => {
    if (busyRef.current) return false;
    if (dirty) { setError('当前文稿有未保存修改，请先保存后新建。'); return false; }
    beginRequestSession(); epoch.current += 1; baseline.current = null; observedSnapshot.current = null; setSavedFingerprint(''); setError(''); setRemoteChanged(false);
    const next = { ...blankWritingProject(), mode: sourceIds.length ? 'strict' : 'expanded', sourceIds: [...sourceIds], materialSelection: { recommendationId: null, passages: [], queries: [], items: [], userSourceIds: [...sourceIds], confirmed: false } }; current.current = next; setProjectState(next); return true;
  };
  const reload = () => runAction('reload', async () => {
    beginRequestSession();
    if (!current.current.id) { const next = blankWritingProject(); current.current = next; setProjectState(next); setError(''); return; }
    const result = await api(`/writing/projects/${current.current.id}`); adopt(result.project, current.current.activeSectionId);
  });
  const mergeRunStatus = value => {
    const latest = current.current;
    if (!canAcceptWritingSnapshot(value, latest, baseline.current, observedSnapshot.current)) return false;
    observedSnapshot.current = { id: value.id, version: value.version };
    const next = { ...latest, libraryAvailability: value.libraryAvailability ?? latest.libraryAvailability, runs: value.runs || [], automatic: value.automatic, capabilities: value.capabilities || latest.capabilities };
    current.current = next; setProjectState(next);
    return true;
  };
  const pending = (project.runs || []).some(activeRun);
  useEffect(() => {
    let cancelled = false;
    const id = project.id, version = project.version, token = requestEpoch.current, editEpoch = epoch.current;
    setGenerationPlan(null); setPlanError('');
    if (!id || !project.sections.length || !usesDocumentWriting(project) || dirty || pending) { setPlanLoading(false); return; }
    setPlanLoading(true);
    api(`/writing/projects/${id}/generation-plan`).then(result => {
      if (!cancelled && token === requestEpoch.current && editEpoch === epoch.current && current.current.id === id && current.current.version === version) setGenerationPlan(result.plan);
    }).catch(problem => { if (!cancelled) setPlanError(writingMessage(problem)); }).finally(() => { if (!cancelled) setPlanLoading(false); });
    return () => { cancelled = true; };
  }, [project.id, project.version, project.workflow, project.interactiveGeneration, project.automation, project.targetWords, project.sections.length, dirty, pending, requestGeneration]);
  useEffect(() => {
    if (!project.id || !pending) return;
    let cancelled = false, timer;
    const id = project.id, token = requestEpoch.current;
    const poll = async () => {
      try {
        const result = await api(`/writing/projects/${id}`);
        if (cancelled || token !== requestEpoch.current || current.current.id !== id) return;
        if (!canAcceptWritingSnapshot(result.project, current.current, baseline.current, observedSnapshot.current)) {
          // Discard an old read without ending the still-active polling loop.
          if (current.current.runs?.some(activeRun)) timer = setTimeout(poll, 2000);
          return;
        }
        const awaiting = pendingReview.current;
        const checkRun = awaiting?.projectId === id ? result.project.runs?.find(run => run.id === awaiting.runId || !awaiting.runId && ['citations', 'citation_match'].includes(run.kind)) : null;
        if (checkRun && !activeRun(checkRun)) {
          pendingReview.current = null;
          if (result.project.review?.version !== result.project.version) try {
            const checked = await api('/writing/projects/' + id + '/review', { method: 'POST', body: JSON.stringify({ expectedVersion: result.project.version }) });
            if (cancelled || token !== requestEpoch.current || current.current.id !== id) return;
            result.project = checked.project || result.project;
          } catch (problem) { if (!cancelled) setError(writingMessage(problem)); }
        }
        const fresh = remember(result.project);
        if (baseline.current && editable(current.current) === editable(baseline.current) && !busyRef.current) adopt(fresh, current.current.activeSectionId);
        else { mergeRunStatus(fresh); if (fresh.version !== current.current.version) setRemoteChanged(true); }
        if (fresh.runs.some(activeRun)) timer = setTimeout(poll, 2000);
      } catch (problem) {
        if (!cancelled) { setError(writingMessage(problem)); timer = setTimeout(poll, 5000); }
      }
    };
    timer = setTimeout(poll, 1500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [project.id, pending, requestGeneration, adopt, remember]);
  return { project, savedProject: baseline.current, requestGeneration, setProject, projects, loading, busy, error, dirty, pending, remoteChanged, capabilities, generationPlan, planLoading, planError, save, refreshList, selectProject, newProject, reload, deleteProject, deletingProjectId, deleteProjectError, clearDeleteProjectError: () => setDeleteProjectError(null), generateEdit, applyEdit, applyDocumentSection, clearError: () => setError(''),
    startAutomatic, interruptAutomatic, generateTitle, titleAssistance, titleChoiceRequired: titleChoiceUnresolved(), selectTitle, keepCurrentTitle, exportReview, confirmStep,
    skipReview: () => action('review-skip', 'review/skip'),
    confirmReview: issueIds => action('review-confirm', 'review/confirm', issueIds?.length ? { issueIds } : { all: true }),
    generateOutline: () => action('outline', 'generate-outline'), confirmOutline: () => usesDocumentWriting(current.current) ? action('confirm', 'generate-document', { confirmOutline: true }) : action('confirm', 'confirm-outline'),
    confirmSection: id => action('confirm-section', `sections/${id}/confirm`),
    confirmOutlineOnly: () => action('confirm-only', 'confirm-outline'),
    generateDocument: () => action('document', 'generate-document', { confirmOutline: false }),
    generateRemainingSections: fromSectionId => action('document', 'generate-document', { confirmOutline: false, remainingFromSectionId: fromSectionId }),
    generateSection: id => action('generate', `sections/${id}/generate`), matchCitations, review: reviewDocument, reviewRules, exportDocument,
    searchImages, acceptIllustration, updateIllustration, removeIllustration, completeIllustrations, automaticIllustrations, insertManualIllustration,
    applyRun: id => action('apply', `runs/${id}/apply`, { replaceManual: true }),
    cancelRun: jobId => runAction('cancel', async () => { const id = current.current.id, token = requestEpoch.current; await api(`/jobs/${jobId}/cancel`, { method: 'POST', body: '{}' }); const result = await api(`/writing/projects/${id}`); if (token === requestEpoch.current && id === current.current.id) { if (baseline.current && editable(current.current) === editable(baseline.current)) adopt(result.project, current.current.activeSectionId); else mergeRunStatus(result.project); } return result; }),
    exportHtml: () => exportDocument({ format: 'html' }),
  };
}
