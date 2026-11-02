import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import WritingPreview from './WritingPreview';
import { WritingReadingPanelHeader, WritingReadingPanelControls } from './WritingReadingPanels';
import WritingImages, { WritingImagesOverview, WritingInlineFigures } from './WritingImages';
import WritingProgress from './WritingProgress';
import WritingAutomatic, { downloadAutomaticReport } from './WritingAutomatic';
import { automaticRunning, deriveAutomaticProgress, automaticWritingOptions, automaticWritingOptionPatch, effectiveAutomaticMode, automaticUserSourceIds } from './writing-automatic';
import { deriveWritingProgress } from './writing-progress';
import { writingBodyEntry } from './writing-body-entry';
import { deriveWritingProgressNotices } from './writing-progress-notices';
import { decorateWritingProgress } from './writing-progress-appearance';
import { writingMaterialScopeFingerprint, writingEditableFingerprint, writingProjectPatch } from './writing-semantic';
import { STRUCTURE_REQUIREMENTS_HINT, structureTemplates, structureTemplateOptions, writingStyles, structureTemplatePatch, writingStylePatch, structureTemplateLabel, writingStyleLabel, restoreLegacyWritingPreset } from './writing-presets';
import { usesDocumentWriting, hasChapterConfirmations, chapterWritingConfirmed } from './writing-workflow';
import { exportedAsDraft, reviewRequiresDraft } from './writing-export-policy';
import WritingReview, { WritingReviewReport, WritingParagraphChecks, WritingReviewConfirmAll, WritingReviewStart } from './WritingReview';
import WritingDocumentTask, { WritingGenerationPlan } from './WritingDocumentTask';
import WritingMaterials from './WritingMaterials';
import WritingGenerationNotices from './WritingGenerationNotices';
import { writingMessage, writingText } from './writing-messages';
import { CitedParagraph, WritingCitationScope, WritingCitationSourceCard, WritingCitationBindingIssues, scrollCitationCard } from './WritingCitations';
import { buildWritingPreview, citationForOccurrence } from './writing-preview';
import { writingCitedSources } from './writing-cited-sources';
import { writingSourceNotice } from './writing-library-availability';
import { materialContext, MAX_WRITING_SOURCES } from './writing-materials';
import { formatWritingProjectTime, isWritingProjectActive, sortWritingProjects, writingProjectPhase } from './writing-projects';
import { WritingProjectGallery } from './WritingProjectGallery';
import { isDemoMode } from './demo-mode';
import { WritingProjectChapterCount, WritingProjectHeading, WritingProjectProgress, WritingProjectTags } from './WritingProjectCard';
import { ArrowRight, ArrowLeft, ArrowUp, ArrowDown, Plus, X, Check, FileText, BookOpen, ListBullets, Sparkle, DownloadSimple, Quotes, CaretRight, CaretLeft, PencilSimple, ShieldCheck, CheckCircle, WarningCircle, LinkSimple, LockSimple, LockSimpleOpen, FloppyDisk, Trash, MagnifyingGlass } from '@phosphor-icons/react';
import './writing.css';
import './writing-projects.css';
import './writing-brief.css';
import './writing-review-page.css';
import './writing-generation-controls.css';
import './writing-chapter-panels.css';

const MODES = { inherit: '跟随全文', strict: '严格模式', expanded: '拓展模式' };
const EDIT_OPERATIONS = { polish: '润色', rewrite: '改写', shorten: '缩写', expand: '扩写' };
const EXPORT_LABELS = { docx: 'Word', html: 'HTML', pdf: 'PDF' };
const blankSection = index => ({ id: `draft-${Date.now()}-${index}`, title: '新的章节', goal: '', mode: 'inherit', words: 600, content: '', locked: false, evidence: [] });
// Match the service's document length: one Han character or ASCII word/number.
const charCount = content => ((content || '').replace(/\[E\d+\]|【模型补充·待核实】/g, '').match(/[\u3400-\u9fff]|[A-Za-z0-9]+/g) || []).length;
const WORD_COUNT_NOTE = '按汉字和英文、数字词组计数，不计引用、模型补充标记及标点。';
const dateLabel = value => value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
const running = run => ['queued', 'running'].includes(run.status);
function projectModelLabel(project, models) {
  if (project.automation === 'automatic' || project.brief?.automation === 'automatic') return '自动选择';
  if (!project.modelId) return '系统默认';
  const model = models.find(item => item.id === project.modelId);
  return model ? model.name || model.model || '未命名' : '暂不可用';
}
function outlinePlanning(candidate) {
  if (candidate?.outlinePlanningProtocol !== 'evidence-plan-v1') return null;
  const pending = !!candidate.outlinePlanningPending || candidate.outlinePlanningState === 'pending';
  const empty = !Array.isArray(candidate.sections) || !candidate.sections.length;
  const blocked = pending ? '正在核对纲要的材料依据，请等待完成后再比较采用。' : empty ? '现有资料不足以形成有依据的纲要，请补充材料或调整写作要求。' : candidate.outlinePlanningState !== 'supported' ? '部分章节的规划尚未得到材料支持，请补充资料或调整相关章节后重新生成。' : '';
  const message = (value, fallback) => {
    const text = writingText(value);
    if (/outlinePlanning|evidence-plan-|supportBasis|sentence-evidence-review|section_[a-z0-9]+|src_[a-z0-9]+/i.test(text)) return fallback;
    return writingMessage(text, fallback).replace(/\b(?:E|P)\d+\b/g, '相关摘录');
  };
  const issues = [...(Array.isArray(candidate.outlinePlanningIssues) ? candidate.outlinePlanningIssues : []), ...(Array.isArray(candidate.issues) ? candidate.issues : [])].map(issue => {
    const fallback = '这部分规划还缺少足够的原文材料，请补充资料或调整写作要求。';
    const explanation = message(issue, message(issue?.reason, fallback));
    const reason = typeof issue === 'object' && issue?.reason ? message(issue.reason, '') : '';
    const title = typeof issue === 'object' && issue?.sectionTitle ? message(issue.sectionTitle, '') : '';
    return `${title ? `${title}：` : ''}${explanation}${reason && reason !== explanation ? `（${reason}）` : ''}`;
  });
  return { blocked, empty, issues: [...new Set(issues)] };
}
function resizeEditor(element) {
  if (!element?.isConnected || !element.getClientRects().length) return;
  const container = element.closest('.writing-editor-scroll'), position = container?.scrollTop;
  element.style.height = 'auto'; element.style.height = `${Math.max(400, element.scrollHeight)}px`;
  if (container) container.scrollTop = position;
}
function WritingOutlineGoal({ value, ...props }) {
  const field = useRef(null);
  useLayoutEffect(() => {
    const element = field.current;
    const fit = () => {
      if (!element?.isConnected) return;
      element.style.height = 'auto';
      element.style.height = `${element.scrollHeight}px`;
    };
    fit();
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth !== width) { width = element.clientWidth; fit(); }
    });
    observer.observe(element);
    document.fonts?.ready.then(fit);
    return () => observer.disconnect();
  }, [value]);
  return <textarea {...props} ref={field} value={value} rows={1} wrap="soft"/>;
}
function Dialog({ title, children, onClose, wide = false, className = '', headerActions }) {
  const box = useRef(null);
  const close = useRef(onClose); close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement;
    box.current?.querySelector('button,input,textarea,select')?.focus();
    const key = event => {
      if (event.key === 'Escape') close.current();
      if (event.key !== 'Tab') return;
      const items = [...(box.current?.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]') || [])].filter(item => item.offsetParent !== null);
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
    };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); previous?.focus?.(); };
  }, []);
  return createPortal(<div className="writing-overlay" onMouseDown={event => event.target === event.currentTarget && onClose()}><section ref={box} className={`writing-dialog ${wide ? 'writing-dialog-wide' : ''} ${className}`} role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2>{headerActions ? <div className="writing-report-dialog-actions">{headerActions}<button className="icon-btn" onClick={onClose} aria-label="关闭对话框"><X size={20}/></button></div> : <button className="icon-btn" onClick={onClose} aria-label="关闭对话框"><X size={20}/></button>}</header>{children}</section></div>, document.body);
}

export function WritingWorkspace({ stage = 'projects', onStageChange, writing, documents = [], selectedIds = [], models = [], onOpenDoc, onOpenSettings, onToast }) {
  const { project, setProject, projects, busy, loading, dirty, error, pending, remoteChanged } = writing;
  const automaticActive = automaticRunning(project.automatic);
  const automaticReadOnly = automaticActive || String(busy || '').includes('interrupt');
  const bodyGenerationRuns = (project.runs || []).filter(run => ['section', 'document', 'edit'].includes(run.kind) && running(run));
  const bodyGenerationSubmitting = ['generate', 'document', 'edit'].includes(busy) || (busy === 'confirm' && usesDocumentWriting(project));
  const bodyGenerationActive = bodyGenerationRuns.length > 0 || bodyGenerationSubmitting;
  const readOnlyWriting = automaticReadOnly ? { ...writing, busy: busy || 'automatic-readonly', pending: true } : writing;
  const [rightTab, setRightTab] = useState('references'), [dialog, setDialog] = useState(null), [searchProject, setSearchProject] = useState('');
  const [projectOrder, setProjectOrder] = useState('updatedAt');
  const [outlineCollapsed, setOutlineCollapsed] = useState(false), [evidenceCollapsed, setEvidenceCollapsed] = useState(true);
  const [selection, setSelection] = useState(null), [editInstruction, setEditInstruction] = useState('');
  const editor = useRef(null), previewButton = useRef(null), previewSectionId = useRef(null), evidenceToggle = useRef(null);
  const outlineToggle = useRef(null), chapterScroller = useRef(null), chapterOutline = useRef(null), chapterOutlineScroll = useRef(null), chapterReferencesPanel = useRef(null), chapterReferencesScroll = useRef(null);
  const chapterReadingAnchor = useRef(null), pendingChapterAnchor = useRef(null), chapterPanelPositions = useRef({}), restoringChapterLayout = useRef(false), chapterRestoreFrame = useRef(null);
  const chapterExplicitScrollTop = useRef(null);
  const [bodyPageOverride, setBodyPageOverride] = useState(null), [previewLocation, setPreviewLocation] = useState(null);
  const [imagesOpen, setImagesOpen] = useState(false);
  const previewOpen = stage === 'editor' && !imagesOpen && (bodyPageOverride?.projectId === project.id
    ? bodyPageOverride.preview : writingBodyEntry({ project, savedProject: writing.savedProject, dirty, remoteChanged, pending }) === 'preview');
  const setPreviewOpen = preview => setBodyPageOverride({ projectId: project.id, preview });
  const materialBrowseProject = useRef(null);
  const [exportFormat, setExportFormat] = useState('docx'), [allowDraftExport, setAllowDraftExport] = useState(false);
  const [bodyView, setBodyView] = useState('read');
  const [editingStepKey, setEditingStepKey] = useState(null);
  const presetSession = useRef(null);
  const [customLength, setCustomLength] = useState(false);
  const customLengthInput = useRef(null);
  useEffect(() => { setCustomLength(![1500, 3000, 6000, 15000].includes(project.targetWords)); }, [project.id]);
  const [exportReceipt, setExportReceipt] = useState(null);
  const [chapterCitation, setChapterCitation] = useState(null);
  const viewStep = dialog === 'export' ? 'review' : stage === 'editor' && imagesOpen ? 'images' : stage === 'editor' ? 'body' : stage;
  const stepKey = (project.id || 'new') + ':' + viewStep;
  const savedForView = writing.savedProject?.id === project.id ? writing.savedProject : project.id && !dirty ? project : null;
  const savedSteps = savedForView ? deriveWritingProgress({ project: savedForView, savedProject: savedForView }).steps : [];
  const savedStepComplete = !!savedSteps.find(step => step.id === viewStep)?.complete;
  const stepEditing = editingStepKey === stepKey;
  const stepReadOnly = savedStepComplete && !stepEditing;
  const bodyGenerationReadOnly = viewStep === 'body' && bodyGenerationActive;
  const viewReadOnly = automaticReadOnly || stepReadOnly || bodyGenerationReadOnly;
  const titleAssistance = writing.titleAssistance || { status: 'idle', titles: [], selectedTitle: '', error: '' };
  const titleChoiceRequired = !!writing.titleChoiceRequired;
  const titleAssistanceActive = titleAssistance.status !== 'idle';
  const inlineTitleError = stage === 'brief' && titleAssistance.status === 'error' && error === titleAssistance.error;
  const nativeStepSave = ['materials', 'images'].includes(viewStep);
  const stepEntry = useRef(null);
  if (stepEntry.current?.key !== stepKey) stepEntry.current = { key: stepKey, complete: savedStepComplete };
  useEffect(() => { setEditingStepKey(null); }, [project.id, viewStep]);

  const deletePending = !!writing.deletingProjectId;
  const visibleProjects = sortWritingProjects(projects.filter(item => !item.deletedAt && `${item.title || ''} ${item.topic || ''} ${item.purpose || ''}`.toLocaleLowerCase().includes(searchProject.trim().toLocaleLowerCase())), projectOrder);
  const hasPendingProjects = projects.some(item => item.runs?.some(running));
  useEffect(() => {
    if (stage !== 'projects' || !hasPendingProjects || typeof writing.refreshList !== 'function') return;
    let stopped = false, timer;
    const refresh = async () => {
      try { if (!document.hidden) await writing.refreshList(); } catch { /* Keep the last readable cards until the next status refresh. */ }
      if (!stopped) timer = setTimeout(refresh, 2000);
    };
    timer = setTimeout(refresh, 2000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [stage, hasPendingProjects, writing.refreshList]);
  const deleteTarget = dialog?.type === 'deleteProject' ? dialog.project : null;
  const deleteTargetActive = deleteTarget && (deleteTarget.id === project.id ? project.runs : deleteTarget.runs)?.some(running);
  const selectedChapterCard = useRef(null);
  const sections = project.sections || [], active = sections.find(section => section.id === project.activeSectionId) || sections[0];
  // An unsaved body deletion must not bypass protection of the saved manuscript.
  const savedOutlineSections = writing.savedProject?.id === project.id ? writing.savedProject.sections || [] : [];
  const outlineInputChanges = savedForView ? writingProjectPatch(project, savedForView) : {};
  const outlineBriefReady = savedSteps.some(step => step.id === 'brief' && step.complete) && !['title', 'brief', 'modelId'].some(key => Object.hasOwn(outlineInputChanges, key));
  const outlineMaterialsReady = savedSteps.some(step => step.id === 'materials' && step.complete) && !['mode', 'sourceIds', 'materialSelection'].some(key => Object.hasOwn(outlineInputChanges, key));
  const regenerateOutlineBlocked = loading ? '正在读取文稿，请稍后。' : automaticReadOnly ? '智能化写作正在进行，请结束后操作。' : viewReadOnly ? '请先点击“修改”开放纲要编辑。' : busy || pending ? '写作任务正在进行，请结束后重新生成。' : remoteChanged ? '已保存文稿有更新，请重新载入后操作。' : [...sections, ...savedOutlineSections].some(section => section.content || section.manual || section.locked) ? '已有正文、人工修改或锁定章节，不能重新生成纲要。' : titleChoiceRequired ? '请先选择拟定题目，或明确使用当前题目。' : !project.topic?.trim() ? '请先填写写作主题。' : !outlineBriefReady ? '请先确认当前主题设定，再重新生成纲要。' : !outlineMaterialsReady ? '请先确认当前资料筛选，再重新生成纲要。' : '';
  const readingPreview = useMemo(() => buildWritingPreview(project), [project]);
  const readingSection = readingPreview.sections.find(section => section.id === active?.id);
  const chapterReferences = useMemo(() => writingCitedSources(readingPreview, active?.id), [readingPreview, active?.id]);
  const readingBody = (viewReadOnly || bodyView === 'read') && !!active?.content;
  const bodyEditing = bodyView === 'edit' && !viewReadOnly;
  const citationParagraph = chapterCitation?.sectionId === active?.id ? readingSection?.paragraphs.find(paragraph => paragraph.id === chapterCitation.paragraphId) : null;
  const selectedCitation = citationForOccurrence(citationParagraph, chapterCitation?.citationId, chapterCitation?.occurrence);
  const references = (project.sourceIds || []).map(id => documents.find(doc => doc.id === id) || { id, title: project.sources?.find(source => source.sourceId === id)?.title || '已绑定资料（当前库中不可用）' });
  const materialReferenceById = new Map(references.map(doc => [doc.id, doc]));
  const materialReferences = [...new Set([...(project.materialSelection?.userSourceIds || project.sourceIds || []), ...references.map(doc => doc.id)])].map(id => materialReferenceById.get(id)).filter(Boolean);
  const chars = sections.reduce((sum, section) => sum + charCount(section.content), 0), planned = sections.reduce((sum, section) => sum + Number(section.words || 0), 0);
  const activeMode = active?.mode === 'inherit' ? project.mode : active?.mode;
  const runs = project.runs || [], currentRuns = runs.filter(run => !run.sectionId || run.sectionId === active?.id);
  const cancellableRuns = runs.filter(run => run.kind !== 'automatic' && running(run) && run.jobId);
  const cancellableRun = cancellableRuns.find(run => run.status === 'running') || cancellableRuns[0];
  const cancelTaskLabel = cancellableRun?.kind === 'document' && cancellableRun.plan?.remainingFromSectionId ? '取消剩余章节生成' : ({ document: '取消全文任务', section: '取消章节生成任务', edit: '取消选区修改任务', citations: '取消引用核对任务', citation_match: '取消引用核对任务', images: '取消配图任务', image_batch: '取消配图任务', outline: '取消纲要生成任务' })[cancellableRun?.kind] || '取消任务';
  const documentRun = runs.find(run => run.kind === 'document');
  const citationMatchRun = runs.find(run => ['citations', 'citation_match'].includes(run.kind));
  const citationMatchBlocked = !project.id ? '请先保存文稿，再核对引用。' : dirty ? '当前有未保存修改，请先保存后核对引用。' : remoteChanged ? '已保存文稿有更新，请重新载入并核对。' : pending ? '写作任务正在进行，请结束后核对引用。' : !sections.some(section => section.content?.trim()) ? '请先完成并保存正文。' : '';
  const documentCandidates = runs.filter(run => run.kind === 'document' && !running(run)).flatMap(run => (run.candidate?.document?.steps || []).filter(step => step.sectionId === active?.id && step.status === 'candidate').map(step => ({ run, step })));
  const documentCandidateRun = dialog?.type === 'documentCandidate' ? runs.find(run => run.id === dialog.runId) : null;
  const documentCandidate = documentCandidateRun?.candidate?.document?.steps?.find(step => step.sectionId === dialog.sectionId);
  const documentCandidateSection = sections.find(section => section.id === dialog?.sectionId);
  const documentBlockReason = !documentCandidate?.section?.content ? '候选正文尚未就绪。' : dialog?.projectId !== project.id || dialog?.sectionId !== active?.id ? '请回到候选所属项目和章节。' : running(documentCandidateRun) ? '请等全文任务结束后再比较采纳。' : documentCandidate.status !== 'candidate' ? '这份候选已处理。' : dirty ? '当前有未保存修改，请先保存后再采纳。' : documentCandidateSection?.locked ? '本章已锁定，不能采纳候选。' : remoteChanged ? '已保存文稿有更新，请重新载入并核对。' : documentCandidate.canApply === false ? writingMessage(documentCandidate.applyBlockedReason, '当前章节或写作要求已变化，这份候选不可采纳。') : '';
  const oneshot = usesDocumentWriting(project);
  const sequentialWriting = project.automation !== 'automatic' && !oneshot;
  const generationChapterIndex = sections.findIndex(section => section.id === active?.id);
  const activeHasSavedBody = !!savedOutlineSections.find(section => section.id === active?.id)?.content?.trim();
  const generateNext = sequentialWriting && activeHasSavedBody;
  const nextGenerationChapter = sections[generationChapterIndex + 1];
  const remainingStartChapter = sections[generationChapterIndex + (activeHasSavedBody ? 1 : 0)];
  const remainingGenerationChapters = remainingStartChapter ? sections.slice(sections.indexOf(remainingStartChapter))
    .filter(section => !section.content?.trim() && !section.manual && !section.locked) : [];
  const chapterGenerationBlocked = viewReadOnly || !!busy || pending || dirty || remoteChanged || loading || !project.outlineConfirmed;
  const interactiveGenerationSelectable = Number.isInteger(Number(project.targetWords)) && Number(project.targetWords) >= 100 && Number(project.targetWords) < 10000;
  const chapterConfirmations = hasChapterConfirmations(project);
  const confirmedChapters = remoteChanged ? [] : sections.filter(section => chapterWritingConfirmed(project, writing.savedProject, section.id));
  const activeChapterConfirmed = confirmedChapters.some(section => section.id === active?.id);
  const chapterView = useRef(null), chapterActionMounted = useRef(false);
  const chapterLocation = JSON.stringify([project.id, active?.id, stage, previewOpen, imagesOpen]);
  chapterView.current = { projectId: project.id, sectionId: active?.id, fingerprint: writingEditableFingerprint(project),
    location: chapterLocation, epoch: (chapterView.current?.epoch || 0) + (chapterView.current?.location === chapterLocation ? 0 : 1), pending, remoteChanged };
  useEffect(() => { chapterActionMounted.current = true; return () => { chapterActionMounted.current = false; }; }, []);
  const automatic = project.automation === 'automatic';
  const selectedUserSources = automaticUserSourceIds(project);
  const hasSelectedSources = selectedUserSources.length > 0;
  const automaticOptions = automaticWritingOptions(project);
  const automaticUsesSources = automaticOptions.useSelectedSources || automaticOptions.useRecommendedSources;
  function updateAutomaticOption(key, value) {
    update(automaticWritingOptionPatch(project, key, value));
  }
  const outlineCandidates = runs.filter(run => run.kind === 'outline' && run.candidate && !run.applied && !running(run) && run.status === 'succeeded');
  const candidateOutlinePlanning = dialog?.type === 'candidate' && dialog.run.kind === 'outline' ? outlinePlanning(dialog.run.candidate) : null;
  const latestSectionRun = runs.find(run => run.sectionId === active?.id);
  const candidates = currentRuns.filter(run => run.kind === 'section' && run.candidate && !run.applied && !running(run) && run.status !== 'failed' && run.status !== 'cancelled');
  const editCandidates = currentRuns.filter(run => run.kind === 'edit' && run.candidate?.edit && !run.applied && run.status === 'succeeded');
  const editRun = dialog?.type === 'editCandidate' ? runs.find(run => run.id === dialog.runId && run.kind === 'edit') : null;
  const editProposal = editRun?.candidate?.edit;
  const selectedRunSection = editRun ? sections.find(section => section.id === editRun.sectionId) : null;
  const validSelection = selection?.projectId === project.id && selection?.sectionId === active?.id && selection?.content === active?.content && selection.selectedExact.trim();
  const selectionHint = active?.locked ? '本章已锁定，请先解锁并保存。' : !validSelection ? '在正文中选中文字，再选择润色、改写、缩写或扩写。' : `已选 ${Array.from(selection.selectedExact).length} 字${dirty ? ' · 生成建议前会先保存当前修改' : ' · 仅修改这一段'}`;
  const editBlockReason = !editRun || !editProposal ? '修改建议尚未就绪。' : editRun.applied ? '这份修改建议已采纳。' : dialog.projectId !== project.id || dialog.sectionId !== active?.id || editRun.sectionId !== active?.id ? '请回到候选所属项目和章节。' : dirty ? '当前有未保存修改，请先保存后再采纳。' : selectedRunSection?.locked ? '本章已锁定，不能采纳修改。' : remoteChanged ? '已保存文稿有更新，请重新载入并核对后再采纳。' : project.version !== editRun.baseVersion ? '文稿版本已变化，这份候选已过期。请从当前正文重新选择并生成。' : selectedRunSection?.version !== editRun.editTarget?.sectionVersion || selectedRunSection?.contentHash !== editRun.editTarget?.contentHash ? '本章正文已变化，这份候选已过期。请根据当前正文重新生成。' : selectedRunSection?.content.slice(editProposal.start, editProposal.end) !== editProposal.selectedExact ? '原选区文字已变化，这份候选已过期。' : '';
  const review = project.review, reviewStale = !!review && (dirty || remoteChanged || pending || review.version !== project.version || !review.wordCounts);
  const exportReady = !!project.id && !pending && !dirty && !remoteChanged && sections.some(section => section.content.trim());
  const reviewNeedsDraft = !review?.wordCounts || reviewStale || reviewRequiresDraft(review);
  const savedLabel = busy === 'save' ? '正在保存…' : dirty ? '有未保存修改' : project.id ? `已保存 · ${dateLabel(project.updatedAt)}` : '尚未保存';
  const update = changes => { if (viewReadOnly && Object.keys(changes).some(key => key !== 'activeSectionId')) return; setProject(previous => ({ ...previous, ...changes })); };
  const presetSessionKey = JSON.stringify([project.id, writing.requestGeneration || 0]);
  if (presetSession.current?.key !== presetSessionKey) presetSession.current = { key: presetSessionKey, project };
  const legacyPresetProject = writing.savedProject?.id === project.id ? writing.savedProject : presetSession.current.project;
  const hasLegacyStructure = !legacyPresetProject.structureTemplate;
  const hasLegacyStyle = !legacyPresetProject.writingStyle;
  const changeWritingPreset = (kind, id) => {
    if (viewReadOnly || busy || pending) return;
    if (id === 'legacy' || kind === 'structure' && id === legacyPresetProject.structureTemplate && structureTemplates.find(item => item.id === id)?.legacy) setProject(previous => restoreLegacyWritingPreset(previous, legacyPresetProject, kind));
    else update(kind === 'structure' ? structureTemplatePatch(id) : writingStylePatch(id));
  };
  const updateSection = (id, changes) => { if (viewReadOnly) return; setProject(previous => ({ ...previous, sections: previous.sections.map(section => section.id === id ? { ...section, ...changes } : section) })); };
  const goEditor = () => onStageChange(project.outlineConfirmed ? 'editor' : sections.length ? 'outline' : 'brief');
  const toast = message => onToast?.(message);
  useEffect(() => { if (automaticReadOnly) { setDialog(null); setSelection(null); } }, [automaticReadOnly]);
  useEffect(() => { setDialog(null); setRightTab('references'); setSelection(null); setBodyPageOverride(null); setImagesOpen(false); setPreviewLocation(null); }, [project.id]);
  useEffect(() => { if (stage === 'projects') setBodyPageOverride(null); }, [stage]);
  useEffect(() => { if (dirty || remoteChanged || pending) setExportReceipt(null); }, [dirty, remoteChanged, pending]);
  useEffect(() => { setExportReceipt(null); }, [project.id, project.version]);
  useEffect(() => { setAllowDraftExport(false); }, [project.id, project.version, review?.checkedAt, dirty, remoteChanged, pending]);
  useEffect(() => { setSelection(null); setDialog(previous => previous?.type?.startsWith('edit') ? null : previous); }, [active?.id]);
  // Decide once when entering a chapter. Typing the first character must not
  // suddenly replace the native editor with the reading surface.
  useEffect(() => { setBodyView(active?.content ? 'read' : 'edit'); }, [project.id, active?.id]);
  useEffect(() => { setChapterCitation(null); }, [project.id, active?.id, active?.content, bodyView, stage]);
  useEffect(() => { scrollCitationCard(selectedChapterCard.current, chapterReferencesScroll.current); }, [chapterCitation, rightTab]);
  useEffect(() => { chapterReadingAnchor.current = null; pendingChapterAnchor.current = null; chapterExplicitScrollTop.current = null; restoringChapterLayout.current = false; }, [project.id, active?.id, active?.content, bodyView, stage, previewOpen, imagesOpen]);
  useEffect(() => { chapterPanelPositions.current = {}; }, [project.id]);
  useEffect(() => () => cancelAnimationFrame(chapterRestoreFrame.current), []);
  useLayoutEffect(() => {
    const anchor = pendingChapterAnchor.current, scroller = chapterScroller.current;
    if (!anchor || !scroller) return;
    pendingChapterAnchor.current = null;
    if (anchor.projectId !== project.id || anchor.sectionId !== active?.id || anchor.content !== active?.content || anchor.bodyView !== bodyView || anchor.stage !== stage || previewOpen || imagesOpen) { restoringChapterLayout.current = false; return; }
    if (bodyEditing) resizeEditor(editor.current);
    const top = anchor.node?.isConnected && scroller.contains(anchor.node)
      ? scroller.scrollTop + anchor.node.getBoundingClientRect().top - scroller.getBoundingClientRect().top - anchor.offset : anchor.scrollTop;
    scroller.scrollTo({ top, behavior: 'instant' });
    chapterExplicitScrollTop.current = scroller.scrollTop;
    for (const [key, node, expanded] of [['outline', chapterOutlineScroll.current, !outlineCollapsed], ['references', chapterReferencesScroll.current, !evidenceCollapsed]]) {
      const position = chapterPanelPositions.current[key];
      if (expanded && node && position) node.scrollTo({ top: position.top, left: position.left, behavior: 'instant' });
    }
    cancelAnimationFrame(chapterRestoreFrame.current);
    chapterRestoreFrame.current = requestAnimationFrame(() => { restoringChapterLayout.current = false; });
    return () => cancelAnimationFrame(chapterRestoreFrame.current);
  }, [outlineCollapsed, evidenceCollapsed]);
  useEffect(() => { setSelection(previous => previous && previous.content !== active?.content ? null : previous); }, [active?.content]);
  useEffect(() => { if (!previewOpen && !imagesOpen && !readingBody) resizeEditor(editor.current); }, [active?.content, active?.id, stage, outlineCollapsed, evidenceCollapsed, previewOpen, imagesOpen, readingBody]);
  useEffect(() => {
    const container = editor.current?.closest('.writing-editor-scroll');
    if (!container) return;
    let width = container.clientWidth, frame;
    const observer = new ResizeObserver(entries => {
      const nextWidth = entries[0].contentRect.width;
      if (Math.abs(nextWidth - width) < .5) return;
      width = nextWidth;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => resizeEditor(editor.current));
    });
    observer.observe(container);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [project.id, active?.id, stage, previewOpen]);
  useEffect(() => {
    const resize = () => requestAnimationFrame(() => resizeEditor(editor.current));
    const observer = new MutationObserver(resize); observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    document.fonts?.addEventListener('loadingdone', resize);
    return () => { observer.disconnect(); document.fonts?.removeEventListener('loadingdone', resize); };
  }, []);
  function beginStepEdit() {
    if (automaticReadOnly || busy || pending || remoteChanged) return;
    setEditingStepKey(stepKey);
    if (viewStep === 'body') { setBodyView('edit'); setPreviewOpen(false); }
  }
  function finishStepEdit() { setEditingStepKey(null); if (viewStep === 'body') setBodyView('read'); }
  async function save() {
    if (viewReadOnly) return;
    if (await writing.save()) { finishStepEdit(); toast('文稿已保存。'); }
  }
  async function matchCitations() { if (viewReadOnly) return; if (await writing.review()) toast('已开始核对事实、观点和明确引用，完成后将显示核对报告。'); }
  async function createOutline(event) {
    event?.preventDefault();
    if (viewReadOnly || busy || pending || loading) return;
    if (titleChoiceRequired) { toast('请先选择拟定题目，或明确使用当前题目。'); return; }
    if (!project.topic?.trim()) { toast('请先填写写作主题。'); return; }
    if (automatic) update({ automaticOptions, mode: effectiveAutomaticMode(automaticOptions) === 'strict' ? 'strict' : 'expanded' });
    if (!await writing.save() || !await writing.confirmStep('brief')) return;
    if (automatic) {
      if (await writing.startAutomatic()) { setDialog(null); setBodyPageOverride(null); setImagesOpen(false); }
      return;
    }
    materialBrowseProject.current = null;
    onStageChange('materials');
  }
  function openMaterials() {
    if (!project.topic?.trim()) { toast('请先填写写作主题。'); return; }
    setDialog(null); setPreviewOpen(false); setImagesOpen(false); onStageChange('materials');
  }
  async function confirmMaterials(selection, context) {
    if (viewReadOnly) return false;
    if (materialContext(project) !== context) throw new Error('写作内容已改变，请返回主题设定重新确认资料。');
    // Whole-source selection must not silently broaden an older paragraph scope.
    const chosen = new Set(selection.sourceIds || []);
    const passages = (project.materialSelection?.passages || []).filter(passage => chosen.has(passage.sourceId));
    const nextSelection = { ...selection, materialSelection: { ...project.materialSelection, ...selection.materialSelection, passages } };
    // Saving an already completed step edits its inputs; it does not add a
    // confirmation that was absent from an older manuscript.
    if (stepEditing && savedStepComplete) {
      if (Object.hasOwn(project.materialSelection || {}, 'confirmed')) nextSelection.materialSelection.confirmed = project.materialSelection.confirmed;
      else delete nextSelection.materialSelection.confirmed;
    }
    const scopeChanged = writingMaterialScopeFingerprint({ ...project, ...nextSelection }) !== writingMaterialScopeFingerprint(project);
    update(nextSelection);
    if (stepEditing && savedStepComplete) {
      if (!await writing.save()) return false;
      finishStepEdit();
      toast(scopeChanged ? '资料筛选已保存，后续步骤需重新确认。' : '资料筛选已保存。');
      return true;
    }
    if (!await writing.save() || !await writing.confirmStep('materials')) return false;
    if (sections.length) {
      onStageChange('outline'); toast('资料筛选已确认，请查看纲要。'); return true;
    }
    const result = await writing.generateOutline();
    if (!result) return false;
    setDialog(null); onStageChange('outline'); toast('资料筛选已保存，正在生成纲要。'); return true;
  }
  async function regenerateOutline() {
    if (regenerateOutlineBlocked) return;
    if (await writing.generateOutline()) toast('正在按当前模板和写作要求重新生成纲要。');
  }
  async function confirmBody() {
    if (viewReadOnly || busy || pending || remoteChanged || sequentialWriting && sections.some(section => !section.content?.trim())) return;
    const view = chapterView.current;
    setPreviewOpen(previewOpen);
    const saved = await writing.save();
    if (!saved || !chapterActionCurrent(view, saved)) return;
    const result = await writing.confirmStep('body');
    if (!result?.project || !chapterActionCurrent(view, result.project)) return;
    finishStepEdit(); setBodyPageOverride(null); setImagesOpen(true);
  }
  function chapterActionCurrent(view, saved) {
    const current = chapterView.current;
    return chapterActionMounted.current && current?.epoch === view?.epoch && saved.id === current.projectId
      && !current.pending && !current.remoteChanged && writingEditableFingerprint(saved) === current.fingerprint;
  }
  async function confirmChapter() {
    if (viewReadOnly || busy || pending || remoteChanged || !active?.content?.trim()) return;
    const view = chapterView.current;
    const sectionId = active.id;
    const result = await writing.confirmSection(sectionId);
    if (!result?.project || !chapterActionCurrent(view, result.project) || sectionId !== chapterView.current.sectionId) return;
    const chapters = result.project.sections || [], index = chapters.findIndex(section => section.id === sectionId);
    const next = chapters[index + 1];
    if (next) { update({ activeSectionId: next.id }); if (sequentialWriting) setBodyView(next.content?.trim() ? 'read' : 'edit'); }
    else if (sequentialWriting) openPreview({ sectionId });
    toast(next ? '本章已确认，继续查看下一章。' : sequentialWriting ? '本章已确认，请查看全文正文。' : '本章已确认。');
  }
  async function generateNextChapter() {
    if (chapterGenerationBlocked || !generateNext || !nextGenerationChapter) return;
    const target = nextGenerationChapter;
    setPreviewOpen(false); setImagesOpen(false); update({ activeSectionId: target.id });
    setBodyView(target.content?.trim() || target.locked ? 'read' : 'edit');
    if (target.content?.trim() || target.manual || target.locked) {
      toast('已切换到下一章，已有正文、人工修改或锁定内容已保留。'); return;
    }
    await writing.generateSection(target.id);
  }
  async function generateRemainingChapters() {
    if (chapterGenerationBlocked || !sequentialWriting || !remainingStartChapter || !remainingGenerationChapters.length) return;
    const target = remainingGenerationChapters[0];
    setPreviewOpen(false); setImagesOpen(false); setBodyView('read'); update({ activeSectionId: target.id });
    await writing.generateRemainingSections(remainingStartChapter.id);
  }
  async function confirmOutline() { if (viewReadOnly) return; if (await writing.confirmOutline()) { onStageChange('editor'); toast(oneshot ? '纲要已确认，全文生成任务已提交。已有正文和锁定章节会保留。' : '纲要已确认，可以逐章生成正文。'); } }
  async function confirmOutlineOnly() { if (viewReadOnly) return; if (await writing.confirmOutlineOnly()) { onStageChange('editor'); toast('已保存并确认纲要。当前正文保持原样，未启动模型生成。'); } }
  function locateReviewIssue(issue) {
    if (!sections.some(section => section.id === issue.sectionId) || reviewStale) return;
    setDialog(null); setImagesOpen(false); setPreviewOpen(false); setBodyView('edit'); update({ activeSectionId: issue.sectionId }); onStageChange('editor'); requestAnimationFrame(() => { setBodyView('edit'); document.querySelector('[data-review-paragraph="' + (issue.paragraphIndex || 1) + '"]')?.scrollIntoView({ block: 'center' }); });
  }
  function openPreview(location = null) {
    previewSectionId.current = location?.sectionId || active?.id;
    setPreviewLocation(location); setImagesOpen(false); setPreviewOpen(true); onStageChange('editor');
  }
  function closePreview() {
    const sectionId = previewSectionId.current;
    if (sections.some(section => section.id === sectionId) && active?.id !== sectionId) update({ activeSectionId: sectionId });
    setPreviewOpen(false); setPreviewLocation(null); onStageChange('editor');
    requestAnimationFrame(() => previewButton.current?.focus());
  }
  function openExport() { writing.clearError(); setAllowDraftExport(false); setExportFormat('docx'); setDialog('export'); }
  function compareDocumentCandidate(runId, sectionId) { update({ activeSectionId: sectionId }); writing.clearError(); setDialog({ type: 'documentCandidate', runId, sectionId, projectId: project.id }); }
  function newProject() { if (selectedIds.length > MAX_WRITING_SOURCES) { toast(`一次写作最多选 ${MAX_WRITING_SOURCES} 篇资料，请先调整知源文库已选范围。`); return; } if (writing.newProject(selectedIds)) onStageChange('brief'); }
  async function openProject(id) { const next = await writing.selectProject(id); if (next) onStageChange(next.outlineConfirmed ? 'editor' : next.sections?.length ? 'outline' : 'brief'); }
  function askDeleteProject(item) { writing.clearDeleteProjectError?.(); setDialog({ type: 'deleteProject', project: item }); }
  async function confirmDeleteProject() {
    if (!deleteTarget || deletePending) return;
    const result = await writing.deleteProject(deleteTarget.id, deleteTarget.version);
    if (result) { setDialog(previous => previous?.type === 'deleteProject' && previous.project.id === result.projectId ? null : previous); toast('文稿已删除。'); }
  }
  function moveSection(index, offset) { const copy = [...sections]; [copy[index], copy[index + offset]] = [copy[index + offset], copy[index]]; update({ sections: copy }); }
  const openEvidence = item => onOpenDoc?.({ id: item.sourceId, revisionId: item.revisionId, matchBlockId: item.blockId });
  function lockSection() { if (viewReadOnly) return; updateSection(active.id, { locked: !active.locked }); toast(active.locked ? '已解除草稿锁定，请保存更改。' : '已标记锁定，请保存以保护人工正文。'); }
  function captureSelection(event) {
    const element = event.currentTarget, start = element.selectionStart, end = element.selectionEnd, content = element.value;
    if (!bodyEditing || !active || active.locked || start === end || !content.slice(start, end).trim()) { setSelection(null); return; }
    setSelection({ projectId: project.id, sectionId: active.id, start, end, selectedExact: content.slice(start, end), content });
  }
  function requestEdit(operation) {
    if (!bodyEditing || busy || pending || !validSelection || active.locked) { toast('请切换到编辑，在正文中明确选择需要修改的文字。'); return; }
    writing.clearError(); setEditInstruction(''); setDialog({ type: 'editRequest', operation, selection: { ...selection }, projectId: project.id, sectionId: active.id });
  }
  const baseProgress = deriveAutomaticProgress(project.automatic, project) || deriveWritingProgress({ project, savedProject: writing.savedProject, dirty, remoteChanged, pending, exportReceipt });
  const progress = decorateWritingProgress({ progress: { ...baseProgress, notices: deriveWritingProgressNotices({ project, savedProject: writing.savedProject, dirty, remoteChanged, pending, progress: baseProgress }) }, project, savedProject: writing.savedProject, dirty, pending });
  const viewedStep = dialog === 'export' ? 'review' : stage === 'editor' && imagesOpen ? 'images' : stage === 'editor' ? 'body' : stage;
  function openReview() { setPreviewOpen(false); setImagesOpen(false); writing.clearError(); setDialog(null); onStageChange('review'); }
  function openBody() { setDialog(null); setImagesOpen(false); setPreviewLocation(null); setBodyPageOverride(null); onStageChange('editor'); }
  function navigateProgress(step) {
    if (step === 'review') { openReview(); return; }
    if (step === 'body') { openBody(); return; }
    setDialog(null); setPreviewOpen(false); setImagesOpen(step === 'images');
    if (step === 'brief') onStageChange('brief');
    else if (step === 'materials') { materialBrowseProject.current = project.id || 'new'; openMaterials(); }
    else if (step === 'outline') onStageChange('outline');
    else if (step === 'body') onStageChange('editor');
    else if (step === 'images') onStageChange('editor');
    else if (step === 'review') openReview();
    else if (step === 'export') { onStageChange('editor'); openExport(); }
  }
  function navigateNotice(notice) {
    if (notice.stepId === 'body' && notice.sectionId && sections.some(section => section.id === notice.sectionId)) update({ activeSectionId: notice.sectionId });
    navigateProgress(notice.stepId);
  }
  function toggleChapterPanel(key, expanded) {
    const collapsed = key === 'outline' ? outlineCollapsed : evidenceCollapsed;
    const nextExpanded = expanded ?? collapsed;
    if (nextExpanded === !collapsed) return;
    const scroller = chapterScroller.current;
    if (scroller) {
      if (!chapterReadingAnchor.current || !chapterReadingAnchor.current.node?.isConnected) {
        const bounds = scroller.getBoundingClientRect(), readingLine = bounds.top + Math.min(80, bounds.height * .15);
        const node = [...scroller.querySelectorAll('.writing-chapter-reading-paragraph')].find(item => item.getBoundingClientRect().bottom > readingLine);
        chapterReadingAnchor.current = { projectId: project.id, sectionId: active?.id, content: active?.content, bodyView, stage,
          node, offset: node ? node.getBoundingClientRect().top - bounds.top : 0, scrollTop: scroller.scrollTop };
      }
      pendingChapterAnchor.current = chapterReadingAnchor.current;
    }
    for (const [name, node, visible] of [['outline', chapterOutlineScroll.current, !outlineCollapsed], ['references', chapterReferencesScroll.current, !evidenceCollapsed]]) {
      if (visible && node) chapterPanelPositions.current[name] = { top: node.scrollTop, left: node.scrollLeft };
    }
    const panel = key === 'outline' ? chapterOutline.current : chapterReferencesPanel.current;
    if (!nextExpanded && panel?.contains(document.activeElement)) (key === 'outline' ? outlineToggle : evidenceToggle).current?.focus({ preventScroll: true });
    cancelAnimationFrame(chapterRestoreFrame.current);
    restoringChapterLayout.current = true;
    if (key === 'outline') setOutlineCollapsed(!nextExpanded);
    else setEvidenceCollapsed(!nextExpanded);
  }
  const bodyTaskControls = stage === 'editor' && !imagesOpen && !automaticReadOnly && cancellableRun ? <footer className="writing-generation-controls" aria-label="正文任务操作">
    <button type="button" className="btn quiet writing-generation-cancel" data-writing-generation-cancel={cancellableRun.kind} aria-label={cancelTaskLabel} aria-busy={busy === 'cancel'} disabled={!!busy || !writing.cancelRun} onClick={() => writing.cancelRun(cancellableRun.jobId)}><X size={14}/>{busy === 'cancel' ? '正在取消…' : cancelTaskLabel}</button>
  </footer> : null;
  function toggleEvidence() {
    toggleChapterPanel('references');
  }
  const chapterGenerationActions = <>{oneshot && <button className="btn primary" disabled={viewReadOnly || !!busy || pending || !project.outlineConfirmed || dirty || writing.generationPlan?.canGenerate === false || !sections.some(section => !section.locked && !section.manual && !section.content.trim())} onClick={() => setDialog('generateDocument')}><Sparkle size={16}/>{pending ? '全文任务进行中' : '生成剩余空白章节'}</button>}<button type="button" className={sequentialWriting ? 'btn primary' : 'btn'} disabled={chapterGenerationBlocked || (generateNext ? !nextGenerationChapter : !active || active.locked)} title={generateNext && !nextGenerationChapter ? '已是最后一章，可确认本章后查看全文。' : dirty ? '请先保存修改。' : undefined} onClick={generateNext ? generateNextChapter : () => setDialog('generate')}><Sparkle size={16}/>{pending ? '模型任务进行中' : generateNext ? '生成下一章节' : '生成当前章节'}</button>{sequentialWriting && <button type="button" className="btn" disabled={chapterGenerationBlocked || !remainingGenerationChapters.length} onClick={generateRemainingChapters}><Sparkle size={16}/>生成剩余全部章节</button>}<small>{project.automation === 'automatic' ? '可查看已有正文，或按需继续修改。' : dirty ? '先保存修改，再生成章节。' : !project.outlineConfirmed ? '请先重新确认纲要。' : sequentialWriting ? '批量生成会保留已有正文、人工修改和锁定章节。' : oneshot ? '一次成文保留已有内容；也可单独生成本章' : '逐章确认，每次只生成一章'}</small></>;
  const flowHeader = <header className={previewOpen ? "writing-flow-header is-preview" : "writing-flow-header"}><div className="writing-flow-title"><button type="button" className="icon-btn" aria-label="返回文稿列表" onClick={() => onStageChange('projects')}><ArrowLeft size={18}/></button><div><h2>{project.title || project.topic || '新的写作'}</h2><small><FloppyDisk size={12}/>{savedLabel}{sections.length > 0 && ` · ${chars.toLocaleString()} 字`}</small></div></div><div className="writing-flow-actions" >{automaticReadOnly && <button type="button" className="btn quiet" data-writing-interrupt aria-label="中断并切换为交互式写作" title="中断并切换为交互式写作" disabled={!!busy || !writing.interruptAutomatic} onClick={() => writing.interruptAutomatic?.()}>{String(busy || '').includes('interrupt') ? '正在中断…' : '中断写作'}</button>}{dialog !== 'export' && !automaticReadOnly && (stepReadOnly ? <button type="button" className="btn" data-writing-modify-step={viewStep} disabled={bodyGenerationReadOnly || !!busy || pending || loading || remoteChanged} onClick={beginStepEdit}><PencilSimple size={15}/>修改</button> : !nativeStepSave && <button type="button" className="btn" disabled={bodyGenerationReadOnly || !!busy || loading || remoteChanged} onClick={save}><FloppyDisk size={15}/>{busy === 'save' ? '保存中…' : '保存'}</button>)}{sections.some(section => section.content?.trim()) && <button type="button" className="btn primary" disabled={automaticReadOnly || !!busy || pending || dirty || remoteChanged} onClick={openExport}><DownloadSimple size={16}/>导出</button>}{project.automatic?.report && <button type="button" className="btn" onClick={() => setDialog('automatic-report')}>写作报告</button>}{previewOpen && sequentialWriting && !automaticActive && !stepReadOnly && <button type="button" className="btn primary" disabled={viewReadOnly || !!busy || pending || remoteChanged || !sections.length || sections.some(section => !section.content?.trim())} onClick={confirmBody}>确认正文<ArrowRight size={15}/></button>}{sections.length > 0 && <button type="button" ref={previewButton} className="btn" onClick={() => previewOpen ? closePreview() : openPreview()}><BookOpen size={16}/>{previewOpen ? '返回章节' : '全文预览'}</button>}</div></header>;

  const automationControl = <div className="writing-workflow-control" role="group" aria-label="写作方式">{[['automatic', '智能化写作', '全程自动完成，无需人工干预'], ['manual', '交互式写作', '每一步由你审阅并确认后继续']].map(([value, label, description]) => <button type="button" aria-pressed={(project.automation || 'manual') === value} disabled={viewReadOnly || !!busy || pending} key={value} onClick={() => update({ automation: value })}><strong>{(project.automation || 'manual') === value && <Check size={13}/>} {label}</strong><span>{description}</span></button>)}</div>;
  const runStatus = <div className="writing-task-list">{runs.filter(run => running(run) || run.status === 'failed' || run.status === 'cancelled').slice(0, 4).map(run => <div className={`writing-task ${run.status}`} key={run.id}><div><strong>{['citations', 'citation_match'].includes(run.kind) ? '引用核对' : ['images', 'image_batch'].includes(run.kind) ? '图片检索' : run.kind === 'outline' ? '纲要生成' : run.kind === 'document' ? '全文生成' : run.kind === 'edit' ? `${EDIT_OPERATIONS[run.operation] || '局部修改'} · ${sections.find(section => section.id === run.sectionId)?.title || '章节'}` : sections.find(section => section.id === run.sectionId)?.title || '章节生成'}</strong><span>{({ queued: '等待处理', running: ['images', 'image_batch'].includes(run.kind) ? '正在查找图片' : ['citations', 'citation_match'].includes(run.kind) ? '正在逐句核对' : '模型正在生成', failed: ['citations', 'citation_match'].includes(run.kind) ? '核对未完成' : '生成失败', cancelled: '已取消' })[run.status]}</span>{run.error && <p>{writingMessage(run.error, '本次生成未完成，请稍后重试。当前文稿已保留。')}</p>}</div>{running(run) && run.jobId && <button className="btn quiet" disabled={viewReadOnly || !!busy} onClick={() => writing.cancelRun(run.jobId)}>取消任务</button>}</div>)}</div>;
  const reviewPanel = viewReadOnly ? <div className="writing-review"><h3>检查校验</h3><div className="writing-review-actions"><WritingReviewStart writing={writing} disabled={automaticReadOnly || bodyGenerationActive}/><WritingReviewConfirmAll writing={writing} stale={reviewStale} disabled={automaticReadOnly || bodyGenerationActive}/>{review && <button type="button" className="btn" disabled={!!busy || pending || reviewStale || automaticReadOnly} onClick={writing.exportReview}><DownloadSimple size={16}/>下载报告</button>}</div><WritingReviewReport review={review} stale={reviewStale} sections={sections} locateLabel="查看对应正文" onLocate={issue => { if (!reviewStale && sections.some(section => section.id === issue.sectionId)) openPreview({ sectionId: issue.sectionId, paragraphIndex: issue.paragraphIndex }); }}/></div> : <WritingReview writing={writing} onLocate={locateReviewIssue}/>;
  function selectChapterCitation(paragraph, citationId, token) {
    setRightTab('references'); toggleChapterPanel('references', true);
    setChapterCitation({ sectionId: active.id, paragraphId: paragraph.id, citationId, occurrence: token.start });
  }
  const chapterReferenceList = <div className="writing-reference-list" aria-label="本章实际引用文献">
    {chapterReferences.map(reference => <article key={reference.key} data-cited-reference={reference.number}>
      <div className="writing-reference-label"><span>[{reference.number}]</span><span>本章引用文献</span></div>
      <button type="button" className="writing-reference-title" disabled={!onOpenDoc || !!writingSourceNotice(reference.sourceStatus)} onClick={() => openEvidence(reference.evidence)}>{reference.title}<CaretRight size={14}/></button>
      {writingSourceNotice(reference.sourceStatus) && <p className="writing-preview-warning">{writingSourceNotice(reference.sourceStatus)}</p>}
      {bodyEditing && <details><summary>引用原文（{reference.citations.length} 条）</summary>{reference.citations.map(({ sectionId, citation }) => <div key={`${sectionId}:${citation.id}`}><p>{citation.evidence.quote}</p><div><button type="button" disabled={!active || active.locked || !!busy} onClick={() => { const value = active.content || '', position = editor.current?.selectionStart ?? value.length; updateSection(active.id, { content: value.slice(0, position) + ` [${citation.id}] ` + value.slice(position) }); setBodyView('edit'); }}><Plus size={13}/>插入引用</button><button type="button" disabled={!onOpenDoc || !!writingSourceNotice(reference.sourceStatus)} onClick={() => openEvidence(citation.evidence)}>定位原文<LinkSimple size={13}/></button></div></div>)}</details>}
    </article>)}
    {!chapterReferences.length && <div className="writing-empty-small">本章尚未引用文献。</div>}
  </div>;
  const evidencePanel = <>
    <div className="writing-citation-match-action"><button type="button" className="btn" disabled={viewReadOnly || !!busy || !!citationMatchBlocked} onClick={matchCitations}><ShieldCheck size={15}/>{busy === 'match-citations' || running(citationMatchRun || {}) ? '正在核对引用…' : '重新核对引用'}</button><p>{citationMatchBlocked || '核对事实、观点与明确引用，保留正文和锁定状态。'}</p>{citationMatchRun?.status === 'succeeded' && !dirty && <p role="status">最近一次引用核对已完成，请检查正文中仍需核实的论述。</p>}{['failed', 'cancelled'].includes(citationMatchRun?.status) && <p role="status">上次引用核对未完成，可重新核对。</p>}</div>
    <div className="writing-evidence-intro"><p>查看正文实际引用的来源原文。</p><span>本章引用 {chapterReferences.length} 篇文献</span></div>
    {chapterReferenceList}
    <div className="writing-evidence-notice"><ShieldCheck size={16}/><p>{activeMode === 'strict' ? '内容依据本次选定的资料生成。请核对原文是否支持相邻论述。' : '扩展内容中的新增事实与推断需要核验。引用仍须回到原文。'}</p></div>
  </>;

  return <WritingCitationScope clean={viewReadOnly || stage === 'editor' && (readingBody || previewOpen)} resetKey={JSON.stringify([project.id, project.version, active?.id, active?.content, bodyView, previewOpen, stage, !!dialog])} onOpenDoc={onOpenDoc}><div data-writing-body-generation={bodyGenerationActive ? 'true' : 'false'} data-writing-readonly={viewReadOnly ? 'true' : 'false'} data-writing-step-readonly={stepReadOnly ? 'true' : 'false'} data-writing-step-mode={viewReadOnly ? 'view' : 'edit'} className={`writing-root writing-stage-${stage} ${automaticReadOnly ? 'writing-is-automatic-readonly' : ''} ${outlineCollapsed ? 'writing-outline-collapsed' : ''} ${evidenceCollapsed ? 'writing-evidence-collapsed' : ''}`}>
    {!automaticActive && (!(stage === 'editor' && (readingBody || previewOpen)) || bodyGenerationReadOnly) && (error && !inlineTitleError || remoteChanged) && <div className="writing-service-notice" role="alert"><WarningCircle size={18}/><div>{error && !inlineTitleError && <p>{error}</p>}{remoteChanged && <p>已保存文稿有更新，当前未保存文字已保留。请先复制需要保留的修改，再重新载入并合并。</p>}</div><button className="btn quiet" disabled={automaticReadOnly || !!busy} onClick={() => setDialog('reload')}>重新载入</button></div>}
    {stage !== 'projects' && flowHeader}
    {stage === 'projects' && <div className="writing-projects">
      <header className="writing-page-heading"><div><h1>{isDemoMode() ? '文稿写作' : '让思考，慢慢成篇'}</h1>{!isDemoMode() && <p>从可靠的资料出发，写下属于自己的见解。</p>}</div><button className="btn primary" disabled={loading || !!busy} onClick={newProject}><Plus size={19}/>开始新的写作</button></header>
      <div className="writing-list-top"><h3>我的文稿 <span>{projects.length}</span></h3><div className="writing-project-list-controls"><div className="search-field writing-project-search"><MagnifyingGlass size={16}/><input aria-label="搜索文稿" placeholder="搜索文稿…" value={searchProject} onChange={event => setSearchProject(event.target.value)}/></div><label>排序<select aria-label="文稿排序" value={projectOrder} onChange={event => setProjectOrder(event.target.value)}><option value="updatedAt">最近修改</option><option value="createdAt">最近创建</option><option value="title">标题</option></select></label></div></div>
      {dirty && <div className="writing-local-draft"><span>{project.title || project.topic || '新文稿'} · 有未保存修改</span><button className="btn quiet" onClick={goEditor}>继续编辑</button><button className="btn" disabled={viewReadOnly || !!busy} onClick={save}>保存</button></div>}
      <WritingProjectGallery projects={visibleProjects} renderProject={(item, cardProps) => <article {...cardProps} key={item.id} data-writing-active={isWritingProjectActive(item)} role="button" tabIndex={busy || writing.deletingProjectId === item.id ? -1 : 0} aria-disabled={!!busy || writing.deletingProjectId === item.id} aria-label={item.title || item.topic || '未命名文稿'} onClick={event => { if (!busy && writing.deletingProjectId !== item.id && !event.target.closest('button,a,input,select,textarea,label')) openProject(item.id); }} onKeyDown={event => { if (event.target !== event.currentTarget || !['Enter', ' '].includes(event.key)) return; event.preventDefault(); if (!busy && writing.deletingProjectId !== item.id) openProject(item.id); }}>
        <div className="writing-project-card-top"><div className="writing-paper-icon"><FileText size={19} weight="light"/></div><span className="writing-status"><i/>{isWritingProjectActive(item) ? '正在写作' : item.runs?.some(run => run.status === 'queued') ? '任务排队中' : writingProjectPhase(item).label}</span><button type="button" className="icon-btn writing-project-delete" aria-label={`删除文稿：${item.title || item.topic || '未命名文稿'}`} disabled={viewReadOnly || !!busy || deletePending} onClick={() => askDeleteProject(item)}><Trash size={18}/></button></div>
        <div className="writing-project-info"><WritingProjectHeading project={item}/><p title={item.purpose || item.topic || '继续整理问题、参考资料与章节。'}>{item.purpose || item.topic || '继续整理问题、参考资料与章节。'}</p><div className="writing-project-meta"><span><WritingProjectChapterCount project={item}/></span><span><strong className="writing-project-word-count">{item.sections.reduce((sum, section) => sum + charCount(section.content), 0).toLocaleString()}</strong> <small>字</small></span><span><strong>{item.sourceIds.length}</strong> <small>篇材料</small></span></div><WritingProjectTags project={item} modelLabel={projectModelLabel(item, models)}/></div>
        <WritingProjectProgress project={item} exportReceipt={exportReceipt}/>
        <footer className="writing-project-card-footer"><div className="writing-project-times"><span className="writing-project-created"><span>创建</span><span>{formatWritingProjectTime(item.createdAt, { compact: true })}</span></span><span className="writing-project-modified"><span>修改</span><span>{formatWritingProjectTime(item.updatedAt, { compact: true })}</span></span></div></footer>
      </article>}>
      {loading && <p className="writing-empty" role="status">正在读取已保存文稿…</p>}
      {!loading && !projects.length && <div className="writing-empty">还没有已保存的文稿。从主题和少量参考资料开始。</div>}
      {!loading && projects.length > 0 && !visibleProjects.length && <div className="writing-empty">没有找到匹配的文稿，请试试其他关键词。</div>}
      <div className="writing-start-note"><div><Quotes size={28} weight="light"/><h3>好的文章，从一个好问题开始。</h3><p>选好资料，确认纲要，一次成文或逐章编写，再核对与修改。</p></div><button className="btn quiet" disabled={loading || !!busy} onClick={newProject}>从主题开始<ArrowRight size={16}/></button></div>
      </WritingProjectGallery>
    </div>}


    {['brief', 'outline'].includes(stage) && <div className="writing-flow-content">
    {stage === 'brief' && <div className="writing-brief-layout writing-brief-only has-writing-options"><form className="writing-brief" aria-label="主题设定" onSubmit={createOutline}><fieldset className="writing-readonly-fields writing-preparation-grid" disabled={viewReadOnly}>
      <div className="writing-form-heading"><h1>主题设定</h1></div>
      <div className="writing-preparation-main">
      <label className="writing-label" htmlFor="writing-topic">写作主题<span>必填</span></label><textarea id="writing-topic" className="writing-topic" required disabled={viewReadOnly || !!busy || pending} value={project.topic || ''} onChange={event => update({ topic: event.target.value, ...(!project.articleTitle ? { title: event.target.value } : {}) })} placeholder="你想围绕什么主题展开？" rows={2}/>
      <label className="writing-label" htmlFor="writing-purpose" title={STRUCTURE_REQUIREMENTS_HINT}>写作要求<span>选填，明确结构优先于模板</span></label><textarea id="writing-purpose" className="writing-purpose" disabled={viewReadOnly || !!busy || pending} value={project.purpose || ''} onChange={event => update({ purpose: event.target.value })} placeholder="读者、重点、表达要求，以及希望保留的边界…" rows={2}/>
      <label className="writing-label" htmlFor="writing-title">文章题目<span>选填，也可由模型辅助拟定</span></label><div className="writing-title-assist"><input id="writing-title" aria-describedby={titleAssistanceActive ? 'writing-title-candidate-status' : undefined} disabled={viewReadOnly || !!busy || pending} value={project.articleTitle || ''} onChange={event => update({ articleTitle: event.target.value, title: event.target.value || project.topic })} placeholder="暂不填写时，以写作主题作为文稿名称"/><button type="button" className="btn" disabled={viewReadOnly || !!busy || pending || !project.topic?.trim()} onClick={writing.generateTitle}><Sparkle size={16}/>{busy === 'title' ? '正在拟题…' : titleAssistance.status === 'error' ? '重新拟题' : '辅助拟题'}</button></div>
      <section className={`writing-title-candidates${titleAssistanceActive ? ' is-active' : ''}`} aria-label="辅助拟题结果" aria-busy={['pending', 'applying'].includes(titleAssistance.status)} hidden={!titleAssistanceActive}>
        <div className="writing-title-candidate-status" id="writing-title-candidate-status" role={titleAssistance.status === 'error' ? 'alert' : 'status'}>{titleAssistance.status === 'pending' ? '正在拟定多个题目，请等待结果。' : titleAssistance.status === 'error' ? titleAssistance.error : titleAssistance.status === 'applying' ? '正在采用所选题目…' : ['selected', 'kept'].includes(titleAssistance.status) && titleChoiceRequired ? '题目或写作要求已修改，请重新拟题或使用当前题目。' : titleAssistance.status === 'selected' ? '已采用所选题目，可继续编辑。' : titleAssistance.status === 'kept' ? '已使用当前题目。' : '请选择一个题目，选中后将填入上方。'}</div>
        <div className="writing-title-candidate-options" role="group" aria-label="候选题目">
          {[0, 1, 2].map(index => {
            const title = titleAssistance.titles[index], selected = !!title && ['applying', 'selected'].includes(titleAssistance.status) && titleAssistance.selectedTitle === title && project.articleTitle === title;
            return <button key={index} type="button" className={`writing-title-candidate${selected ? ' is-selected' : ''}`} aria-pressed={selected} title={title || undefined} disabled={viewReadOnly || !!busy || pending || !title || !['choosing', 'selected', 'kept'].includes(titleAssistance.status)} onClick={() => writing.selectTitle?.(title)}><span className="writing-title-candidate-number" aria-hidden="true">{index + 1}</span><span className="writing-title-candidate-text">{title || (titleAssistance.status === 'pending' ? '等待拟定…' : '暂无候选题目')}</span>{selected && <Check size={15} aria-hidden="true"/>}</button>;
          })}
        </div>
        <button type="button" className="btn quiet writing-title-keep" disabled={viewReadOnly || !!busy || pending || !project.articleTitle?.trim() || titleAssistance.status === 'applying' || titleAssistance.status === 'kept' && !titleChoiceRequired} onClick={() => writing.keepCurrentTitle?.()}>使用当前题目</button>
      </section>
      <div className={`writing-length-grid${customLength || ![1500, 3000, 6000, 15000].includes(project.targetWords) ? ' is-custom' : ''}`}>
        <div className="writing-label writing-length-label" id="writing-length-label">文章长度</div>
        <div className="writing-length-options writing-workflow-control" id="writing-length" role="group" aria-label="文章长度">
          {[[1500, '短篇'], [3000, '中篇'], [6000, '长文'], [15000, '专题']].map(([words, label]) => {
            const selected = !customLength && project.targetWords === words;
            return <button type="button" key={words} aria-label={label} aria-pressed={selected} disabled={viewReadOnly || !!busy || pending} onClick={() => { setCustomLength(false); update({ targetWords: words }); }}><strong>{selected && <Check size={13} aria-hidden="true"/>}{label}</strong><span>约 {words.toLocaleString('zh-CN')} 字</span></button>;
          })}
          <div className={`writing-length-custom${customLength || ![1500, 3000, 6000, 15000].includes(project.targetWords) ? ' is-selected' : ''}`}>
            <button type="button" aria-label="自定义" aria-pressed={customLength || ![1500, 3000, 6000, 15000].includes(project.targetWords)} disabled={viewReadOnly || !!busy || pending} onClick={() => { setCustomLength(true); requestAnimationFrame(() => { customLengthInput.current?.focus({ preventScroll: true }); customLengthInput.current?.select(); }); }}><strong>{(customLength || ![1500, 3000, 6000, 15000].includes(project.targetWords)) && <Check size={13} aria-hidden="true"/>}自定义</strong><span aria-hidden={customLength || ![1500, 3000, 6000, 15000].includes(project.targetWords)}>设置具体字数</span></button>
            {(customLength || ![1500, 3000, 6000, 15000].includes(project.targetWords)) && <div className="writing-length-value"><input id="writing-custom-length" ref={customLengthInput} aria-label="目标字数" type="number" min="100" max="100000" step="1" required disabled={viewReadOnly || !!busy || pending} value={project.targetWords ?? 6000} onChange={event => update({ targetWords: Number(event.target.value) })}/><span>字</span></div>}
          </div>
        </div>
      </div>
      <div className="writing-form-grid writing-preset-fields">
        <label htmlFor="writing-structure-template">结构模板<select id="writing-structure-template" title={STRUCTURE_REQUIREMENTS_HINT} disabled={viewReadOnly || !!busy || pending} value={project.structureTemplate || 'legacy'} onChange={event => changeWritingPreset('structure', event.target.value)}>
          {hasLegacyStructure && <option value="legacy">{structureTemplateLabel(legacyPresetProject)}（原结构）</option>}
          {project.structureTemplate && !structureTemplates.some(item => item.id === project.structureTemplate) && <option value={project.structureTemplate}>{structureTemplateLabel(project)}</option>}
          {structureTemplateOptions(project, legacyPresetProject).map(item => <option key={item.id} value={item.id}>{item.name}{item.legacy ? '（原模板）' : ''}</option>)}
        </select></label>
        <label htmlFor="writing-style">文风<select id="writing-style" disabled={viewReadOnly || !!busy || pending} value={project.writingStyle || 'legacy'} onChange={event => changeWritingPreset('style', event.target.value)}>
          {hasLegacyStyle && <option value="legacy">{writingStyleLabel(legacyPresetProject)}（原文风）</option>}
          {project.writingStyle && !writingStyles.some(item => item.id === project.writingStyle) && <option value={project.writingStyle}>{writingStyleLabel(project)}</option>}
          {writingStyles.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
      </div>
      <label className="writing-label">写作方式<span>两种方式使用相同的六个步骤</span></label>{automationControl}
      <p className="writing-brief-source-note">已选资料 {selectedUserSources.length} 篇。{automatic ? !automaticUsesSources ? '本次不使用资料。' : automaticOptions.useSelectedSources && automaticOptions.useRecommendedSources ? '本次合用已选资料与系统推荐。' : automaticOptions.useSelectedSources ? '本次使用已选资料。' : '系统将按主题推荐资料。' : '下一步可筛选资料，并选择严格模式或拓展模式。'}</p>
      {!automaticReadOnly && runStatus}
      </div>
      <aside className={`writing-preparation-options ${automatic ? 'writing-automatic-options' : 'writing-interactive-options'}`} aria-label={automatic ? '智能化写作配置' : '交互式写作配置'}><header><h2>{automatic ? '智能化写作配置' : '交互式写作配置'}</h2><p>{automatic ? '设置资料使用、配图和检查校验。' : '选择正文的生成方式，逐步确认写作内容。'}</p></header>
      {automatic ? <>
        <section className="writing-automatic-source-settings" aria-labelledby="writing-automatic-sources-label">
        <div className="writing-label" id="writing-automatic-sources-label">资料选用<span>可多选，也可不选</span></div>
        <div className="writing-workflow-control" role="group" aria-labelledby="writing-automatic-sources-label">
          <button type="button" aria-label="已选资料" aria-pressed={automaticOptions.useSelectedSources} disabled={viewReadOnly || !!busy || pending} onClick={() => updateAutomaticOption('useSelectedSources', !automaticOptions.useSelectedSources)}><strong>{automaticOptions.useSelectedSources && <Check size={13} aria-hidden="true"/>}已选资料</strong><span>{hasSelectedSources ? '使用已选的 ' + selectedUserSources.length + ' 篇资料' : '暂无已选资料'}</span></button>
          <button type="button" aria-label="系统推荐" aria-pressed={automaticOptions.useRecommendedSources} disabled={viewReadOnly || !!busy || pending} onClick={() => updateAutomaticOption('useRecommendedSources', !automaticOptions.useRecommendedSources)}><strong>{automaticOptions.useRecommendedSources && <Check size={13} aria-hidden="true"/>}系统推荐</strong><span>按写作主题检索推荐资料</span></button>
        </div>
        <div className="writing-automatic-mode-slot">{automaticUsesSources ? <div className="writing-automatic-mode-section">
          <div className="writing-automatic-subheading" id="writing-automatic-mode-label">所选资料的使用方式</div>
          <div className="writing-workflow-control writing-automatic-mode-control" role="group" aria-label="资料写作模式" aria-describedby="writing-automatic-mode-label">
          <button type="button" aria-label="严谨模式" aria-pressed={automaticOptions.mode === 'strict'} disabled={viewReadOnly || !!busy || pending} onClick={() => updateAutomaticOption('mode', 'strict')}><strong>{automaticOptions.mode === 'strict' && <Check size={13} aria-hidden="true"/>}严谨模式</strong><span>仅依据选用资料中明确的内容撰写</span></button>
          <button type="button" aria-label="拓展模式" aria-pressed={automaticOptions.mode === 'extended'} disabled={viewReadOnly || !!busy || pending} onClick={() => updateAutomaticOption('mode', 'extended')}><strong>{automaticOptions.mode === 'extended' && <Check size={13} aria-hidden="true"/>}拓展模式</strong><span>允许延伸讨论，新增内容需核实</span></button>
          </div>
        </div> : <p className="writing-automatic-no-sources" role="status">本次不使用资料，将采用拓展模式。</p>}</div>
        </section>
        <div className="writing-label" id="writing-automatic-images-label">自动配图<span>耗时较长</span></div>
        <div className="writing-workflow-control" role="group" aria-label="自动配图">
          <button type="button" aria-label="是" aria-pressed={automaticOptions.includeImages} disabled={viewReadOnly || !!busy || pending} onClick={() => updateAutomaticOption('includeImages', true)}><strong>{automaticOptions.includeImages && <Check size={13} aria-hidden="true"/>}是</strong><span>如果有合适的图片，给文章配图</span></button>
          <button type="button" aria-label="否" aria-pressed={!automaticOptions.includeImages} disabled={viewReadOnly || !!busy || pending} onClick={() => updateAutomaticOption('includeImages', false)}><strong>{!automaticOptions.includeImages && <Check size={13} aria-hidden="true"/>}否</strong><span>不配插图，跳过配图步骤</span></button>
        </div>
        <div className="writing-label" id="writing-automatic-review-label">检查校验<span>耗时较长</span></div>
        <div className="writing-workflow-control" role="group" aria-label="检查校验">
          <button type="button" aria-label="是" aria-pressed={!automaticOptions.skipReview} disabled={viewReadOnly || !!busy || pending} onClick={() => updateAutomaticOption('skipReview', false)}><strong>{!automaticOptions.skipReview && <Check size={13} aria-hidden="true"/>}是</strong><span>完成正文后检查事实、观点与明确引用</span></button>
          <button type="button" aria-label="否" aria-pressed={automaticOptions.skipReview} disabled={viewReadOnly || !!busy || pending} onClick={() => updateAutomaticOption('skipReview', true)}><strong>{automaticOptions.skipReview && <Check size={13} aria-hidden="true"/>}否</strong><span>跳过本次检查校验，生成后可手动检查</span></button>
        </div>
      </> : <section className="writing-interactive-generation" aria-labelledby="writing-generation-label">
        <div className="writing-label" id="writing-generation-label">正文生成方式</div>
        <div className="writing-workflow-control" role="group" aria-label="正文生成方式">
          <button type="button" aria-label="一次成文" aria-pressed={oneshot} disabled={viewReadOnly || !!busy || pending || !interactiveGenerationSelectable} onClick={() => update({ interactiveGeneration: 'single' })}><strong>{oneshot && <Check size={13} aria-hidden="true"/>}一次成文</strong><span>确认纲要后，一次生成全文</span></button>
          <button type="button" aria-label="逐章生成" aria-pressed={!oneshot} disabled={viewReadOnly || !!busy || pending || !interactiveGenerationSelectable} onClick={() => update({ interactiveGeneration: 'chapter' })}><strong>{!oneshot && <Check size={13} aria-hidden="true"/>}逐章生成</strong><span>确认纲要后，按章生成正文</span></button>
        </div>
        <p className="writing-interactive-generation-note" role="status">{interactiveGenerationSelectable ? '生成后仍可逐章编辑与确认。' : Number(project.targetWords) >= 10000 ? oneshot ? '当前文稿沿用一次成文，生成后仍可逐章确认。' : '10000 字及以上按原流程逐章生成。' : '请输入 100–100000 之间的整数。'}</p>
      </section>}
      </aside>
      <footer className="writing-preparation-footer"><button type="submit" className="btn primary" disabled={viewReadOnly || !!busy || pending || loading || titleChoiceRequired || !project.topic?.trim()}>{busy === 'automatic' || busy === 'save' ? '正在开始…' : pending ? '写作任务进行中' : automatic ? '开始智能化写作' : '确认主题，筛选资料'}<ArrowRight size={17}/></button></footer>
    </fieldset></form></div>}

    {stage === 'outline' && <div className="writing-outline-wrap"><div className="writing-outline-summary"><div><span>本次写作</span><h2>{project.title || project.topic}</h2></div><div><span className="pill">{structureTemplateLabel(project)}</span><span>{writingStyleLabel(project)}</span><span>{MODES[project.mode]}</span><span>{project.automation === 'automatic' ? '智能化写作' : oneshot ? '一次成文' : '逐章编写'}</span><span>约 {planned.toLocaleString()} 字</span></div></div>{!viewReadOnly && <details className="writing-outline-history" open={pending}><summary>{pending ? '查看当前生成进度' : '生成说明与历史记录'}</summary>{oneshot && <WritingGenerationPlan plan={writing.generationPlan} loading={writing.planLoading} error={writing.planError} dirty={dirty}/>}<WritingDocumentTask run={documentRun} busy={automaticReadOnly || !!busy} onCancel={writing.cancelRun} onSelect={sectionId => { update({ activeSectionId: sectionId }); onStageChange('editor'); }} onCompare={(runId, sectionId) => { compareDocumentCandidate(runId, sectionId); onStageChange('editor'); }}/>{runStatus}</details>}{outlineCandidates.map(run => <div className="writing-generation-banner" key={run.id}><span>{outlinePlanning(run.candidate)?.blocked || '纲要已生成，但写作内容有更新，请比较后再决定是否采用。'}</span><button onClick={() => setDialog({ type: 'candidate', run, sectionId: null })}>查看纲要候选</button></div>)}
      {!sections.length && <div className="writing-empty">{pending ? '模型正在组织纲要，结果会自动显示。' : '暂时没有纲要。可以添加章节手动拟定，或返回写作要求生成。'}</div>}
      <div className="writing-outline-list">{sections.map((section, index) => <article key={section.id} className="writing-outline-item"><span className="writing-section-number">{String(index + 1).padStart(2, '0')}</span><div className="writing-outline-fields"><input disabled={viewReadOnly || !!busy || pending || section.locked} aria-label={`第 ${index + 1} 章标题`} value={section.title} onChange={event => updateSection(section.id, { title: event.target.value })}/><WritingOutlineGoal disabled={viewReadOnly || !!busy || pending || section.locked} aria-label={`第 ${index + 1} 章写作目标`} className="writing-outline-goal" value={section.goal} placeholder="这一章要回答什么问题？" onChange={event => updateSection(section.id, { goal: event.target.value })}/><div><label>写作边界<select disabled={viewReadOnly || !!busy || pending || section.locked} value={section.mode} onChange={event => updateSection(section.id, { mode: event.target.value })}>{Object.entries(MODES).map(([key, value]) => <option value={key} key={key}>{value}</option>)}</select></label><label>预计<input disabled={viewReadOnly || !!busy || pending || section.locked} className="writing-word-input" aria-label={`第 ${index + 1} 章目标字数`} type="number" min="100" max="5000" step="100" value={section.words} onChange={event => updateSection(section.id, { words: Number(event.target.value) })}/>字{section.locked && ' · 已锁定'}</label></div></div><div className="writing-reorder"><button className="icon-btn" disabled={viewReadOnly || index === 0 || !!busy || pending} onClick={() => moveSection(index, -1)} aria-label={`上移第 ${index + 1} 章`}><ArrowUp size={16}/></button><button className="icon-btn" disabled={viewReadOnly || index === sections.length - 1 || !!busy || pending} onClick={() => moveSection(index, 1)} aria-label={`下移第 ${index + 1} 章`}><ArrowDown size={16}/></button><button className="icon-btn" disabled={viewReadOnly || sections.length <= 1 || !!section.content || section.locked || !!busy || pending} onClick={() => update({ sections: sections.filter(item => item.id !== section.id) })} aria-label={`删除第 ${index + 1} 章`} title={section.content ? '已有正文的章节保留，避免误删' : '删除空章节'}><X size={16}/></button></div></article>)}</div><button className="writing-add-section" disabled={viewReadOnly || !!busy || pending} onClick={() => update({ sections: [...sections, blankSection(sections.length)] })}><Plus size={17}/>添加章节</button><div className="writing-outline-bottom"><div><CheckCircle size={19}/><p>{oneshot ? '确认纲要后，自动生成符合条件的空白章节。' : '确认后可以按自己的节奏逐章生成。'}<small>切换模板或修改要求不会替换原纲要，可按需重新生成；已有正文、人工修改或锁定章节受保护。</small></p></div><div className="writing-outline-confirm-actions"><button type="button" className="btn quiet" disabled={!!regenerateOutlineBlocked} title={regenerateOutlineBlocked || '按当前模板、写作要求和资料重新生成纲要'} onClick={regenerateOutline}><Sparkle size={16}/>{busy === 'outline' ? '正在重新生成…' : '重新生成纲要'}</button>{oneshot && writing.generationPlan?.canGenerate === false && <button className="btn quiet" disabled={viewReadOnly || !sections.length || !!busy || pending} onClick={confirmOutlineOnly}>{busy === 'confirm-only' ? '正在确认…' : '仅保存并确认纲要'}</button>}<button className="btn primary" disabled={viewReadOnly || !sections.length || !!busy || pending || (oneshot && !dirty && writing.generationPlan?.canGenerate === false)} onClick={confirmOutline}>{busy === 'confirm' ? '正在确认…' : oneshot ? '确认纲要并生成全文' : '保存并确认纲要'}<ArrowRight size={18}/></button></div></div>
    </div>}

    </div>}
    {viewReadOnly && stage === 'materials' && <section className="writing-automatic-materials-readonly" aria-label="当前写作资料"><header><h1>资料筛选</h1><p>{automaticReadOnly ? '智能化写作正在进行，当前资料仅供查看。' : '资料筛选已完成，可在这里查看已确认的资料。'}</p></header><section className="writing-readonly-material-list materials-card-grid" aria-label="写作参考资料">{materialReferences.map(doc => {
      const own = (project.materialSelection?.userSourceIds || project.sourceIds || []).includes(doc.id);
      const manual = own && (Array.isArray(project.materialSelection?.userSourceIds) || !(project.materialSelection?.recommendationId || project.materialSelection?.items?.length || project.materialSelection?.passages?.length));
      const recommended = !own || project.materialSelection?.items?.some(item => item.sourceId === doc.id);
      return <article key={doc.id} data-material-id={doc.id}><button type="button" className="writing-reference-title" onClick={() => onOpenDoc?.({ id: doc.id, revisionId: project.sources?.find(source => source.sourceId === doc.id)?.revisionId })}>{doc.title}<CaretRight size={14}/></button><div className="material-badges">{manual && <span className="material-origin-badge" data-material-origin="manual">人工筛选</span>}{recommended && <span className="material-origin-badge" data-material-origin="recommended">系统推荐</span>}{!manual && !recommended && <span className="material-origin-badge">来源未记录</span>}</div><small>{project.materialSelection?.passages?.some(passage => passage.sourceId === doc.id) ? '已选段落' : '整篇资料'}</small></article>;
    })}{!references.length && <p>{automaticReadOnly ? '暂未选入资料，系统会继续完成写作。' : '未选用资料，按拓展模式写作。'}</p>}</section></section>}
    {!viewReadOnly && stage === 'materials' && <div className="writing-materials-page"><WritingMaterials key={project.id + ':' + project.version} project={project} documents={documents} models={models} busy={!!busy} confirmationError={error} autoRecommend={!stepEntry.current.complete && materialBrowseProject.current !== (project.id || 'new')} continueLabel={savedStepComplete && stepEditing ? '保存修改' : sections.length ? '确认资料，继续写作' : '确认资料并生成纲要'} onOpenDoc={onOpenDoc} onClose={() => onStageChange('brief')} onConfirm={confirmMaterials}/></div>}
    {stage === 'editor' && !active && <div className="writing-empty">请先完成写作要求和纲要。<button className="btn" onClick={() => onStageChange('brief')}>返回写作准备</button></div>}
    {stage === 'editor' && active && <div className="writing-editor-view writing-chapter-panels" data-outline-collapsed={outlineCollapsed} data-references-collapsed={evidenceCollapsed} style={previewOpen || imagesOpen ? { display: 'none' } : undefined}>
      <div className="writing-editor-grid"><aside id="writing-chapter-navigation" ref={chapterOutline} className="writing-chapter-nav writing-reading-sidebar" aria-label="文章目录" hidden={outlineCollapsed}><WritingReadingPanelHeader icon={ListBullets} actions={<button type="button" className="icon-btn writing-outline-edit" onClick={() => onStageChange('outline')} aria-label="查看纲要" title="查看纲要"><PencilSimple size={15}/></button>}>文章目录</WritingReadingPanelHeader><div ref={chapterOutlineScroll} className="writing-chapter-list">{sections.map((section, index) => <button type="button" key={section.id} className={section.id === active.id ? 'active' : ''} title={`第 ${index + 1} 章 · ${section.title}`} aria-label={`第 ${index + 1} 章：${section.title}${section.locked ? '，已锁定' : ''}`} aria-current={section.id === active.id ? 'step' : undefined} onClick={() => update({ activeSectionId: section.id })}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{section.title}</strong><small>{section.content ? `${charCount(section.content)} 字` : '待编写'}{section.locked ? ' · 已锁定' : ''}</small></div>{section.locked ? <LockSimple size={13}/> : section.content && <Check size={13}/>}</button>)}</div>{!automaticReadOnly && <div className="writing-chapter-bottom">{chapterGenerationActions}</div>}</aside>
      <main className={`writing-editor-main${!bodyEditing ? ' is-reading' : ''}`}><WritingReadingPanelControls label="章节显示设置" outlineExpanded={!outlineCollapsed} referencesExpanded={!evidenceCollapsed} outlineId="writing-chapter-navigation" referencesId="writing-evidence-panel" outlineRef={outlineToggle} referencesRef={evidenceToggle} onToggleOutline={() => toggleChapterPanel('outline')} onToggleReferences={toggleEvidence}/>{bodyEditing && <><div className="writing-editor-toolbar"><div className="writing-toolbar-actions">{Object.entries(EDIT_OPERATIONS).map(([operation, label], index) => <button key={operation} disabled={viewReadOnly || !!busy || pending || active.locked || readingBody || !validSelection} title={readingBody ? '切换编辑后选中文字' : active.locked ? '请先解锁并保存' : validSelection ? `只${label}选中的文字` : '请先在正文中选中文字'} onMouseDown={event => event.preventDefault()} onClick={() => requestEdit(operation)}>{index === 0 && <Sparkle size={15}/>} {label}</button>)}<span/><button disabled={viewReadOnly || !!busy} onClick={lockSection} title="更改后需要保存">{active.locked ? <LockSimple size={17}/> : <LockSimpleOpen size={17}/>}<span>{active.locked ? '解锁' : '锁定'}</span></button></div><button type="button" className="btn" disabled={viewReadOnly || !!busy || !!citationMatchBlocked} onClick={matchCitations}><ShieldCheck size={15}/>引用校验</button><span className="writing-mode-label">{MODES[activeMode]}</span></div><div className="writing-selection-status" role="status">{selectionHint}</div></>}
        {!automaticReadOnly && <>
        {documentCandidates.length > 0 && <div className="writing-generation-banner"><span>本章有 {documentCandidates.length} 份全文生成候选，当前正文已保留。</span><button onClick={() => compareDocumentCandidate(documentCandidates[0].run.id, active.id)}>比较全文候选</button></div>}
        {editCandidates.length > 0 && <div className="writing-generation-banner"><span>有 {editCandidates.length} 份选区修改建议，等待你比较。</span><button onClick={() => setDialog({ type: 'editCandidates', projectId: project.id, sectionId: active.id })}>查看修改建议</button></div>}
        {candidates.length > 0 && <div className="writing-generation-banner"><span>有 {candidates.length} 份生成候选，尚未覆盖当前正文。</span><button onClick={() => setDialog({ type: 'candidate', run: candidates[0], sectionId: active.id })}>比较候选</button></div>}
        {latestSectionRun?.status === 'failed' && <div className="writing-generation-banner" role="alert"><span>最近生成失败：{writingMessage(latestSectionRun.error, '请稍后重新生成，或在系统设置中检查写作模型。')}</span></div>}
        {!project.outlineConfirmed && <div className="writing-generation-banner"><span>写作要求或纲要已改变，请重新确认。</span><button onClick={() => onStageChange('outline')}>确认纲要</button></div>}
        </>}<div ref={chapterScroller} className="writing-editor-scroll" onScroll={event => { if (restoringChapterLayout.current || chapterExplicitScrollTop.current !== null && Math.abs(event.currentTarget.scrollTop - chapterExplicitScrollTop.current) < 1) return; chapterExplicitScrollTop.current = null; chapterReadingAnchor.current = null; }}>{bodyEditing && <WritingGenerationNotices key={active.id} notices={active.generationNotices} collapsible/>}<article className="writing-paper">{(!readingBody || !readingSection?.introductionParagraphCount) && <><div className="writing-paper-overline"><span>第 {sections.findIndex(section => section.id === active.id) + 1} 章</span>{bodyEditing && <span>{active.locked ? <LockSimple size={13}/> : <ShieldCheck size={13}/>} {active.locked ? '人工正文已锁定' : MODES[activeMode]}</span>}</div><h2 className="writing-chapter-title-input">{active.title}</h2></>}{bodyEditing && <details className="writing-chapter-goal"><summary>本章目标与写作边界</summary><p className="writing-chapter-intention">{active.goal || '尚未填写，可在纲要中补充。'}</p></details>}{(sequentialWriting || oneshot && !automaticReadOnly) && <div className="writing-mobile-generation-actions">{chapterGenerationActions}</div>}<div className="writing-body-view-controls"><div className="writing-body-view-tabs" role="group" aria-label="章节正文显示方式"><button type="button" aria-pressed={!bodyEditing} disabled={!active.content} onClick={() => setBodyView('read')}><BookOpen size={14}/>阅读</button><button type="button" aria-pressed={bodyEditing} disabled={viewReadOnly} onClick={() => setBodyView('edit')}><PencilSimple size={14}/>编辑</button></div>{!automaticActive && !stepReadOnly && <div className="writing-body-flow-actions">{!sequentialWriting && chapterConfirmations && <button type="button" className="btn" disabled={viewReadOnly || !!busy || pending || remoteChanged || !active.content?.trim() || activeChapterConfirmed} aria-label={activeChapterConfirmed ? '本章已确认' : '保存并确认本章'} onClick={confirmChapter}>{activeChapterConfirmed ? <Check size={15}/> : busy === 'confirm-section' ? '正在确认…' : '保存并确认本章'}</button>}<button type="button" className="btn primary" disabled={viewReadOnly || !!busy || pending || remoteChanged || (sequentialWriting ? !active.content?.trim() : !sections.some(section => section.content?.trim()))} onClick={sequentialWriting ? confirmChapter : confirmBody}>{sequentialWriting ? '确认本章' : '确认正文'}<ArrowRight size={15}/></button></div>}</div>{readingBody && <div className="writing-body-reading" aria-label="章节正文阅读">{readingSection?.paragraphs.map(paragraph => <React.Fragment key={paragraph.id}>{readingSection.introductionParagraphCount > 0 && paragraph.index === readingSection.introductionParagraphCount && <><div className="writing-paper-overline"><span>第 1 章</span></div><h2 className="writing-chapter-title-input">{active.title}</h2></>}<div className="writing-chapter-reading-paragraph" data-paragraph-id={paragraph.id}><CitedParagraph clean project={project} section={readingSection} paragraph={paragraph} sectionIndex={sections.findIndex(section => section.id === active.id)} onSelect={(citationId, token) => selectChapterCitation(paragraph, citationId, token)}/><WritingInlineFigures project={project} sectionId={active.id} paragraph={paragraph} dirty={dirty} onOpenDoc={onOpenDoc}/></div></React.Fragment>)}</div>}{!bodyEditing && !active.content && <p className="writing-readonly-chapter-empty">本章尚无正文，可选择其他章节阅读。</p>}<textarea hidden={!bodyEditing} ref={editor} onSelect={captureSelection} aria-label="章节正文" className="writing-body-editor" readOnly={!bodyEditing || active.locked || !!busy} value={active.content || ''} spellCheck={false} placeholder={'让第一个想法落在这里…\n\n可以直接手写并保存，也可以确认纲要后，基于已绑定的原文证据生成本章。'} onChange={event => { if (bodyEditing && !active.locked) updateSection(active.id, { content: event.target.value }); }}/>{bodyEditing && (review || citationMatchRun) && <WritingParagraphChecks section={readingSection} review={review} stale={reviewStale || dirty} busy={!!busy || pending} onConfirm={issueId => writing.confirmReview([issueId])}/>} {!automaticReadOnly && !sequentialWriting && !active.content && <button className="btn writing-start-chapter" disabled={viewReadOnly || !!busy || pending || active.locked || !project.outlineConfirmed || dirty} onClick={() => setDialog('generate')}><Sparkle size={17}/>{pending ? '正在生成…' : '依据资料生成本章'}</button>}{bodyEditing && <div className="writing-paper-footnote"><Quotes size={16}/><span>编辑时请保留引用标记，切换阅读可查看编号与来源。</span></div>}</article><div className="writing-chapter-pagination"><button className="btn quiet" disabled={sections[0]?.id === active.id} onClick={() => update({ activeSectionId: sections[sections.findIndex(section => section.id === active.id) - 1].id })}><ArrowLeft size={15}/>上一章</button><span>{sections.findIndex(section => section.id === active.id) + 1} / {sections.length}</span><button className="btn quiet" disabled={sections.at(-1)?.id === active.id} onClick={() => update({ activeSectionId: sections[sections.findIndex(section => section.id === active.id) + 1].id })}>下一章<ArrowRight size={15}/></button></div></div>{!previewOpen && bodyTaskControls}{bodyEditing && <footer className="writing-editor-status"><span title={WORD_COUNT_NOTE}>{charCount(active.content)} 字 <i/>目标 {active.words} 字</span><span>{active.manual ? '已包含人工修改' : '逐章编写与核对'} · 选区修改需比较后采纳</span></footer>}</main>
      <aside id="writing-evidence-panel" ref={chapterReferencesPanel} className="writing-evidence-panel writing-reading-sidebar" aria-label="本章参考文献" hidden={evidenceCollapsed}><WritingReadingPanelHeader icon={Quotes} actions={bodyEditing ? <button type="button" className="icon-btn" onClick={openReview} aria-label="检查校验" title="检查校验"><ShieldCheck size={16}/></button> : null}>本章参考文献</WritingReadingPanelHeader><div ref={chapterReferencesScroll} className="writing-chapter-reference-scroll">{<>{selectedCitation && <section className="writing-chapter-selected-citation" aria-label="正文对应引用"><div className="writing-preview-material-context"><strong>正文对应引用</strong><span>第 {citationParagraph.index + 1} 段 · 参考文献 {selectedCitation.number ? `[${selectedCitation.number}]` : '未编号'}</span></div><WritingCitationSourceCard citation={selectedCitation} clean={readingBody || viewReadOnly} project={project} active onOpenDoc={onOpenDoc} cardRef={selectedChapterCard}/></section>}{readingBody || viewReadOnly ? chapterReferenceList : evidencePanel}</>}</div></aside></div></div>}
    {stage === 'editor' && imagesOpen && !previewOpen && (viewReadOnly ? <WritingImagesOverview writing={writing} readOnly={automaticReadOnly} onStart={() => { if (loading || dirty || automaticReadOnly || busy || pending || remoteChanged) return; beginStepEdit(); }} onOpenDoc={onOpenDoc}/> : <WritingImages writing={writing} models={models} initialSectionId={active?.id} onOpenDoc={onOpenDoc} onToast={toast} onClose={() => setImagesOpen(false)} onNext={openReview}/>)} 
    {stage === 'editor' && previewOpen && <WritingPreview project={project} dirty={dirty} initialSectionId={previewLocation?.sectionId || active?.id} initialParagraphIndex={previewLocation?.paragraphIndex} initialCitationId={previewLocation?.citationId} onOpenDoc={onOpenDoc} onSectionChange={sectionId => { previewSectionId.current = sectionId; }}/>}

    {stage === 'editor' && previewOpen && bodyTaskControls}
    {stage === 'review' && <section className="writing-review-page" aria-label="检查校验">{reviewPanel}<footer className="writing-review-page-actions"><button type="button" className="btn quiet" onClick={openBody}><ArrowLeft size={15}/>返回正文</button><button type="button" className="btn" disabled={automaticReadOnly || !!busy || pending || dirty || remoteChanged} onClick={openExport}><DownloadSimple size={16}/>导出文稿</button></footer></section>}
    {stage !== 'projects' && <WritingProgress progress={progress} viewedStep={viewedStep} busy={loading || !!busy && !automaticReadOnly} onStep={navigateProgress} onNotice={navigateNotice}/>}

    {deleteTarget && <Dialog title="删除文稿" className="writing-delete-dialog" onClose={() => !deletePending && setDialog(null)}><p className="writing-dialog-description">确认删除《{deleteTarget.title || deleteTarget.topic || '未命名文稿'}》？删除后，这份文稿将从写作空间移除。</p>{dirty && project.id === deleteTarget.id && <p className="writing-delete-warning"><WarningCircle size={18}/>当前文稿有未保存修改，删除后这些修改也会被移除。需要保留时，请先取消并保存。</p>}{deleteTargetActive && <p className="writing-delete-warning"><WarningCircle size={18}/>这份文稿仍有写作任务进行中，请先取消任务或等任务结束后再删除。</p>}{writing.deleteProjectError?.projectId === deleteTarget.id && <p className="writing-candidate-error" role="alert">{writing.deleteProjectError.message}</p>}<div className="writing-dialog-footer"><button type="button" className="btn quiet" disabled={viewReadOnly || deletePending} onClick={() => setDialog(null)}>暂不删除</button><button type="button" className="btn writing-delete-confirm" disabled={viewReadOnly || deletePending || !!busy || deleteTargetActive} onClick={confirmDeleteProject}><Trash size={16}/>{deletePending ? '正在删除…' : '确认删除'}</button></div></Dialog>}

    {dialog?.type === 'materials' && <Dialog title="确认写作参考材料" className="writing-materials-dialog" wide onClose={() => !busy && setDialog(null)}><WritingMaterials project={project} documents={documents} models={models} busy={!!busy} confirmationError={error} autoRecommend={dialog.autoRecommend} onOpenDoc={onOpenDoc} onClose={() => setDialog(null)} onConfirm={confirmMaterials} continueLabel={dialog.next === 'generate' ? '确认材料并生成纲要' : dialog.next === 'manual' ? '确认材料并拟定纲要' : '确认并保存材料'}/></Dialog>}

    {dialog === 'reload' && <Dialog title="重新载入已保存文稿" onClose={() => setDialog(null)}><p className="writing-dialog-description">{dirty ? '当前有未保存修改。重新载入会替换这些修改，请先复制需要保留的文字。' : '读取最新保存的文稿、写作进度和校验结果。'}</p><div className="writing-dialog-footer"><button className="btn quiet" onClick={() => setDialog(null)}>继续编辑</button><button className="btn primary" disabled={automaticReadOnly || !!busy} onClick={async () => { await writing.reload(); setDialog(null); }}>重新载入</button></div></Dialog>}
    {dialog === 'references' && <Dialog title="本章参考证据与检查" onClose={() => setDialog(null)}>{evidencePanel}</Dialog>}
    {dialog === 'generateDocument' && <Dialog title="继续生成全文" onClose={() => setDialog(null)}><p className="writing-dialog-description">按照已确认纲要继续生成空白章节，保留已有正文、人工修改和锁定章节。</p><WritingGenerationPlan plan={writing.generationPlan} loading={writing.planLoading} error={writing.planError} dirty={dirty}/>{error && <p className="writing-candidate-error" role="alert">{error}</p>}<div className="writing-dialog-footer"><button className="btn quiet" onClick={() => setDialog(null)}>返回编辑</button><button className="btn primary" disabled={viewReadOnly || !!busy || pending || dirty || !project.outlineConfirmed || writing.generationPlan?.canGenerate === false} onClick={async () => { if (await writing.generateDocument()) { setDialog(null); toast('已提交全文任务，将保留已有正文。'); } }}><Sparkle size={16}/>{busy === 'document' ? '正在提交…' : '生成剩余空白章节'}</button></div></Dialog>}
    {dialog?.type === 'documentCandidate' && <Dialog title={`比较全文候选：${documentCandidateSection?.title || '章节'}`} wide onClose={() => setDialog(null)}><p className="writing-dialog-description">{documentCandidateRun?.model?.name || '写作模型'} · {dateLabel(documentCandidateRun?.createdAt)} · 仅采纳本章，不改动其他章节。</p><div className="writing-proposal-columns"><div><h4>当前正文</h4><p>{documentCandidateSection?.content || '尚无正文'}</p></div><div><h4>全文任务生成的候选</h4><p>{documentCandidate?.section?.content || '候选尚未就绪'}</p></div></div><WritingGenerationNotices notices={documentCandidate?.section?.generationNotices} compact/><p className="writing-demo-note">生成期间的修改会保留。只有本章正文、参考资料和写作要求未改变，且章节未锁定时，才能采纳。</p>{documentBlockReason && <p className="writing-candidate-error" role="status">{documentBlockReason}</p>}{error && <p className="writing-candidate-error" role="alert">{error}</p>}<div className="writing-dialog-footer"><button className="btn quiet" onClick={() => setDialog(null)}>保留当前正文</button><button className="btn primary" disabled={viewReadOnly || !!busy || !!documentBlockReason} onClick={async () => { if (await writing.applyDocumentSection(dialog.runId, dialog.sectionId, dialog.projectId)) { setDialog(null); toast('本章候选已采纳并保存，其他章节保持原样。'); } }}><Check size={16}/>{busy === 'apply-document' ? '正在采纳…' : '采纳本章候选'}</button></div></Dialog>}
    {dialog === 'generate' && active && <Dialog title={`生成：${active.title}`} onClose={() => setDialog(null)}><p className="writing-dialog-description">使用项目的 {references.length} 篇资料及已确认纲要，生成当前章节。已有正文会保留，新的结果作为候选稿供你比较。</p><div className="writing-gentle-note"><ShieldCheck size={18}/><p>{MODES[activeMode]} · 请核对事实、引文和证据适用范围。每次只启动当前这一章。</p></div><div className="writing-dialog-footer"><button className="btn quiet" onClick={() => setDialog(null)}>返回编辑</button><button className="btn primary" disabled={viewReadOnly || !!busy || pending || active.locked} onClick={async () => { const result = await writing.generateSection(active.id); if (result) setDialog(null); }}><Sparkle size={16}/>开始生成本章</button></div></Dialog>}
    {dialog?.type === 'candidate' && <Dialog title="比较生成候选" wide onClose={() => setDialog(null)}><p className="writing-dialog-description">{dialog.run.model?.name || '模型'} · {dateLabel(dialog.run.createdAt)} · {candidateOutlinePlanning ? '请核对候选各章是否都有材料依据，再决定是否采用。' : '采纳需要当前章节未锁定，且生成后未修改相关内容。'}</p>{candidateOutlinePlanning?.blocked && <div className="writing-gentle-note" role="status"><WarningCircle size={18}/><p>{candidateOutlinePlanning.blocked}</p></div>}<div className="writing-proposal-columns"><div><h4>{dialog.run.kind === 'outline' ? '当前纲要' : '当前正文'}</h4><p>{dialog.run.kind === 'outline' ? sections.map(section => `${section.title}\n${section.goal}`).join('\n\n') || '尚无纲要' : sections.find(section => section.id === dialog.sectionId)?.content || '尚无正文'}</p></div><div><h4>生成候选</h4><p>{dialog.run.kind === 'outline' ? (dialog.run.candidate?.sections || []).map(section => `${section.title}\n${section.goal}`).join('\n\n') || (candidateOutlinePlanning ? '现有资料尚不足以形成可采用的纲要。' : '') : dialog.run.candidate?.section?.content || '候选没有可用正文'}</p></div></div><WritingGenerationNotices notices={dialog.run.candidate?.section?.generationNotices} additionalNotices={dialog.run.candidate?.generationNotices} compact/>{candidateOutlinePlanning?.issues.length > 0 && <ul className="writing-edit-issues" aria-label="纲要材料说明">{candidateOutlinePlanning.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>}<div className="writing-dialog-footer"><button className="btn quiet" onClick={() => setDialog(null)}>{dialog.run.kind === 'outline' ? '保留当前纲要' : '保留当前正文'}</button><button className="btn primary" disabled={viewReadOnly || !!busy || dirty || !!candidateOutlinePlanning?.blocked || (dialog.run.kind === 'section' && (active?.locked || !dialog.run.candidate?.section || active?.id !== dialog.sectionId))} onClick={async () => { if (candidateOutlinePlanning?.blocked) return; if (await writing.applyRun(dialog.run.id)) { setDialog(null); toast('候选已采纳并保存。'); } }}>{dialog.run.kind === 'outline' ? '采用纲要候选' : '采纳并替换本章'}</button></div></Dialog>}
    {dialog?.type === 'editRequest' && <Dialog title={`${EDIT_OPERATIONS[dialog.operation]}选中的文字`} onClose={() => setDialog(null)}><p className="writing-dialog-description">只处理下面这段选区。{dirty ? '提交前会先保存当前文稿。' : ''}模型返回独立建议，比较后由你决定是否采纳。</p><div className="writing-selection-preview"><h4>原选区 · {Array.from(dialog.selection.selectedExact).length} 字</h4><p>{dialog.selection.selectedExact}</p></div><label className="writing-label" htmlFor="writing-edit-instruction">额外要求<span>选填</span></label><textarea id="writing-edit-instruction" className="writing-purpose" rows={3} maxLength={2000} disabled={viewReadOnly || !!busy} value={editInstruction} onChange={event => setEditInstruction(event.target.value)} placeholder="例如：保持谨慎语气，让表达更简洁。"/><p className="writing-demo-note">{MODES[activeMode]} · 保留事实、限定条件与引用；选区之外的正文保持原样。</p>{error && <p className="writing-candidate-error" role="alert">{error}</p>}<div className="writing-dialog-footer"><button className="btn quiet" onClick={() => setDialog(null)}>返回原稿</button><button className="btn primary" disabled={viewReadOnly || !!busy || pending || active?.locked || active?.id !== dialog.sectionId || project.id !== dialog.projectId || active?.content !== dialog.selection.content} onClick={async () => { const result = await writing.generateEdit(dialog.sectionId, dialog.operation, dialog.selection, editInstruction.trim()); if (result) { setDialog(null); setSelection(null); toast('已提交选区修改任务；原稿保持不变，完成后可比较建议。'); } }}><Sparkle size={16}/>{busy === 'edit' ? '正在提交…' : `生成${EDIT_OPERATIONS[dialog.operation]}建议`}</button></div></Dialog>}
    {dialog?.type === 'editCandidates' && <Dialog title="本章的修改建议" onClose={() => setDialog(null)}><p className="writing-dialog-description">建议连同原始选区保存在项目中。正文或写作要求改变后，旧建议仍可阅读，但不能覆盖当前文稿。</p><div className="writing-edit-candidate-list">{editCandidates.map(run => <button key={run.id} onClick={() => { writing.clearError(); setDialog({ type: 'editCandidate', runId: run.id, sectionId: run.sectionId, projectId: project.id }); }}><div><strong>{EDIT_OPERATIONS[run.operation || run.candidate.edit.operation] || '局部修改'}</strong><span>{dateLabel(run.createdAt)} · {run.model?.name || '写作模型'} · {run.baseVersion === project.version ? '待比较' : '文稿已更新'}</span><p>{run.candidate.edit.selectedExact}</p></div><CaretRight size={18}/></button>)}</div></Dialog>}
    {dialog?.type === 'editCandidate' && editProposal && <Dialog title={`比较${EDIT_OPERATIONS[editRun.operation || editProposal.operation] || '局部修改'}建议`} wide onClose={() => setDialog(null)}><div className="writing-proposal-info"><span className="pill">仅替换原选区</span><span>{editRun.model?.name || '写作模型'} · {dateLabel(editRun.createdAt)}</span></div><div className="writing-proposal-columns"><div><h4>原始选区 · {Array.from(editProposal.selectedExact || '').length} 字</h4><p>{editProposal.selectedExact}</p></div><div><h4>修改建议 · {Array.from(editProposal.replacementText || '').length} 字</h4><p>{editProposal.replacementText}</p></div></div>{writingText(editProposal.changeSummary) && <p className="writing-dialog-description">修改说明：{writingText(editProposal.changeSummary)}</p>}{editProposal.issues?.length > 0 && <ul className="writing-edit-issues">{editProposal.issues.map((issue, index) => <li key={index}>{writingMessage(issue, '请核对这段修改是否符合写作要求。')}</li>)}</ul>}<p className="writing-demo-note">请核对修改是否准确、引用是否合适。采纳后仅替换原选区，其他正文保留。</p>{editBlockReason && <p className="writing-candidate-error" role="status">{editBlockReason}</p>}{error && <p className="writing-candidate-error" role="alert">{error}</p>}<div className="writing-dialog-footer"><button className="btn quiet" onClick={() => setDialog({ type: 'editCandidates', projectId: project.id, sectionId: active.id })}>其他建议</button><button className="btn quiet" onClick={() => setDialog(null)}>保留原稿</button><button className="btn primary" disabled={viewReadOnly || !!busy || !!editBlockReason} onClick={async () => { if (await writing.applyEdit(editRun.id, dialog.sectionId, dialog.projectId)) { setDialog(null); setSelection(null); toast('已采纳选区修改并保存，未选中的文字保持不变。'); } }}><Check size={16}/>{busy === 'apply-edit' ? '正在采纳…' : '采纳并替换选区'}</button></div></Dialog>}
    {dialog === 'automatic-report' && <Dialog title="写作报告" className="writing-report-dialog" onClose={() => setDialog(null)} wide headerActions={<button type="button" className="btn" disabled={automaticActive || !project.automatic?.report} onClick={() => downloadAutomaticReport(project)}><DownloadSimple size={16}/>下载报告</button>}><WritingAutomatic project={project} busy={busy} reportOnly/></Dialog>}
    {dialog === 'export' && <Dialog title="导出文章" className="writing-export-dialog" onClose={() => setDialog(null)} wide><p className="writing-dialog-description">{project.title || project.topic} · {sections.length} 章 · {chars.toLocaleString()} 字</p><div className="writing-export-formats">{[['docx', 'Word', '可编辑文稿 · .docx'], ['html', 'HTML', '网页文稿 · .html'], ['pdf', 'PDF', '固定版式文稿 · .pdf']].map(([format, label, description]) => <button className={format === exportFormat ? 'selected' : ''} aria-pressed={format === exportFormat} disabled={automaticReadOnly || !!busy} key={format} onClick={() => setExportFormat(format)}><FileText size={24} weight="light"/><strong>{label}</strong><small>{description}</small></button>)}</div><p className="writing-dialog-description">导出仅包含文章与参考文献，校验结果可在检查校验页面单独导出。</p>{reviewNeedsDraft && <div className="writing-export-check"><div><strong>{!review || reviewStale ? '当前文章尚未完成校验' : '仍有待核实事项'}</strong><p>可以先核对，也可以选择忽略后导出。</p></div><button type="button" className="btn" disabled={automaticReadOnly || !!busy || pending} onClick={openReview}><ShieldCheck size={16}/>查看检查校验</button></div>}{reviewNeedsDraft && <label className="writing-export-consent"><input type="checkbox" checked={allowDraftExport} disabled={automaticReadOnly || !!busy} onChange={event => setAllowDraftExport(event.target.checked)}/><span>忽略待核实事项，继续导出<small>导出的文章不附带核对情况。</small></span></label>}{!exportReady && <p className="writing-candidate-error" role="status">{dirty ? '请先保存当前修改。' : pending ? '请等待写作任务结束。' : '请先完成并保存正文。'}</p>}{error && <p className="writing-candidate-error" role="alert">{error}</p>}<div className="writing-dialog-footer"><button className="btn quiet" onClick={() => setDialog(null)}>返回文章</button><button className="btn primary" disabled={automaticReadOnly || !!busy || !exportReady || reviewNeedsDraft && !allowDraftExport} onClick={async () => { const result = await writing.exportDocument({ format: exportFormat, allowIssues: reviewNeedsDraft && allowDraftExport }); if (result) { setExportReceipt({ projectId: project.id, version: project.version, format: result.format, reviewStatus: result.reviewStatus, requiresDraftConsent: result.requiresDraftConsent }); toast(EXPORT_LABELS[result.format] + ' 文章已下载。'); setDialog(null); } }}><DownloadSimple size={17}/>{busy === 'export' ? '正在导出…' : '下载 ' + EXPORT_LABELS[exportFormat]}</button></div></Dialog>}
  </div></WritingCitationScope>;
}

export default WritingWorkspace;
