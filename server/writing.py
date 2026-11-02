"""Durable, evidence-bound writing with optimistic edits and fenced model jobs.

Sentence coordinates are program-owned; a separate model pass assesses what
each frozen source excerpt supports. All evidence stays pinned to original
library revisions; Wiki/summary text is excluded from the registry.
"""
from __future__ import annotations

import copy
from collections import Counter
import hashlib
import json
import re
import time

from .enrichment import _json
from .model_gateway import ModelError, ModelService
from .store import dumps, ident, now
from .writing_document import DOCUMENT_SCHEMA, WritingDocumentMixin
from .writing_operations import WritingOperations, _clean
from .writing_reference_scope import CitationScopeError, MISSING_BLOCK_ID, citation_error, citation_scope_payload, resolve_reference_block, scope_prepared
from .prompts import snapshot, freeze, prompt_text, writing_prompt_id
from .writing_review import build_review, review_requires_draft
from .writing_check_report import WritingCheckReportMixin
from .writing_assist import WritingAssistMixin
from .writing_output import (OUTPUT_POLICY, CITATION_RETENTION_POLICY, SECTION_ENVELOPE_POLICY, enabled as preserve_usable_output, parse_output,
                             automatic_citations_report_only)
from .writing_sentence_citations import (content_hash as citation_content_hash, exact_sentence,
                                         relocate_citations, strip_citations, valid_bindings,
                                         RESOLVED_STATUSES)
from .writing_citation_tasks import CITATION_TASK_SCHEMA, WritingCitationTasksMixin
from .writing_citation_matching import (matching_policy, temporal_review_snapshot, freeze_generation_temporal_review,
                                        DEFERRED_CITATION_REVIEW_POLICY, SELECTIVE_REVIEW_POLICY)
from .writing_outline_planning import (OUTLINE_ISSUE_REVIEW_INSTRUCTIONS, OUTLINE_PLANNING_PROTOCOL,
    ABSTRACT_OUTLINE_POLICY, ABSTRACT_OUTLINE_REQUIREMENT, abstract_outline,
    WritingOutlinePlanningMixin, prepare_strict_outline)
from .writing_image_tasks import WritingImageTasksMixin
from .writing_automatic import WritingAutomaticMixin
from .writing_automatic_retry import (SCHEMA as AUTOMATIC_RETRY_SCHEMA, retry_call,
    enabled as retry_enabled, AutomaticRetryCancelled)
from .writing_source_scope import citation_sources, preserved_citation_scope_valid
from .writing_composition import POLICY as COMPOSITION_POLICY, snapshot as composition_snapshot_for_task, chapter_context
from .writing_preferences import (snapshot as preference_snapshot_for_task, validate as validate_preferences,
                                  apply as apply_preferences, preserve_introduction, INTRODUCTION_POLICY,
                                  AUTHOR_STRUCTURE_POLICY)
from .writing_material_packet import POLICY as MATERIAL_INPUT_POLICY, TEMPORAL_REQUIREMENT, date_block, material_order
from .writing_images import (refresh_illustrations, freeze_image_resources, search_fingerprint,
                             reanchor_citation_only_images, preserve_approved_illustrations)
from . import writing_export
from . import writing_pdf

PROMPT_VERSION = 'wenshu-writing-1.0'
EDIT_PROMPT_VERSION = 'wenshu-writing-edit-1.0'
EDIT_OPERATIONS = {'polish': '润色', 'rewrite': '改写', 'shorten': '缩写', 'expand': '扩写'}
SUPPLEMENT = '【模型补充·待核实】'
CITATION_PATTERN = r'\[E\d+\]'
CITATION_PLACEMENT = 'sentence-evidence-reviewed-v3'
LEGACY_UNMATCHED_CITATION_POLICY = 'retain-as-unverified-v1'
UNMATCHED_CITATION_POLICY = 'retain-as-prose-v2'
REVIEWED_CITATION_PROTOCOLS = {'sentence-evidence-reviewed-v2', CITATION_PLACEMENT}
INLINE_CITATION_PROTOCOLS = {'claim-exact-v1', *REVIEWED_CITATION_PROTOCOLS}
MODES = ('strict', 'extended')


def default_model_id(preferences):
    # Support both preference schemas during the independently deployed model
    # settings migration. An explicit cleared unified default stays cleared.
    return preferences.get('defaultModelId') if 'defaultModelId' in preferences else preferences.get('summaryModelId')


SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_projects(
 id TEXT PRIMARY KEY, version INTEGER NOT NULL, content_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS writing_history(
 project_id TEXT NOT NULL REFERENCES writing_projects(id), version INTEGER NOT NULL,
 content_json TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(project_id,version)
);
CREATE TABLE IF NOT EXISTS writing_runs(
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 project_id TEXT NOT NULL REFERENCES writing_projects(id), section_id TEXT,
 kind TEXT NOT NULL, base_version INTEGER NOT NULL, model_id TEXT NOT NULL,
 model_version INTEGER NOT NULL, model_json TEXT NOT NULL, prompt_version TEXT NOT NULL,
 input_json TEXT NOT NULL, result_json TEXT, applied INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS writing_runs_project ON writing_runs(project_id,created_at);
CREATE TABLE IF NOT EXISTS writing_calls(
 run_id TEXT NOT NULL REFERENCES writing_runs(id), attempt INTEGER NOT NULL,
 duration_ms INTEGER NOT NULL, usage_json TEXT NOT NULL, validation_status TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(run_id,attempt)
);
"""


class WritingConflict(ValueError):
    """The requested base is stale, or protected writing would be overwritten."""


class WritingExportConflict(WritingConflict):
    """An export gate with a reviewable, machine-readable reason."""
    def __init__(self, code, message, review=None):
        super().__init__(message)
        self.detail = {'code': code, 'message': message}
        if review is not None:
            self.detail['review'] = review


def _text(value, label, maximum=200, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum:
        raise ValueError(f'{label}必须为文本且不超过 {maximum} 字符')
    return value.strip()


def _raw_text(value, label, maximum=60000, *, empty=False):
    """Editor text must keep exact whitespace and Unicode scalar boundaries."""
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum:
        raise ValueError(f'{label}必须为文本且不超过 {maximum} 字符')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError(f'{label}包含无效的 Unicode 字符') from None
    return value


def _content_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _utf16_position(text, offset):
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError('选区偏移必须为非负整数')
    count = 0
    for index, character in enumerate(text):
        if count == offset:
            return index
        count += 2 if ord(character) > 0xFFFF else 1
        if count > offset:
            raise ValueError('选区不能切开 Unicode 字符，请重新选择完整文字')
    if count == offset:
        return len(text)
    raise ValueError('选区偏移超出当前章节范围')


def _mode(value, *, inherit=False):
    allowed = (*MODES, 'inherit') if inherit else MODES
    if value not in allowed:
        raise ValueError('写作模式必须为 strict 或 extended')
    return value


def _effective(project, section):
    return project['mode'] if section['mode'] == 'inherit' else section['mode']


def _brief(value):
    if not isinstance(value, dict) or len(dumps(value)) > 12000:
        raise ValueError('写作需求必须为对象且不超过 12000 字符')
    if ('interactiveGeneration' in value
            and (not isinstance(value['interactiveGeneration'], str)
                 or value['interactiveGeneration'] not in ('chapter', 'single'))):
        raise ValueError('交互式正文生成方式须为逐章生成或一次成文')
    validate_preferences(value)
    return copy.deepcopy(value)


def _version(project, expected):
    if not isinstance(expected, int) or isinstance(expected, bool) or project['version'] != expected:
        raise WritingConflict('项目已发生变化，请刷新后再保存；当前修改未覆盖最新版本')


class WritingService(WritingAssistMixin, WritingCheckReportMixin, WritingAutomaticMixin, WritingImageTasksMixin, WritingOutlinePlanningMixin, WritingDocumentMixin, WritingCitationTasksMixin):
    def __init__(self, store, models=None):
        self.store = store
        self.models = models or ModelService(store)
        with store.connect() as db:
            db.executescript(SCHEMA)
            if 'deleted_at' not in {row['name'] for row in db.execute('PRAGMA table_info(writing_projects)')}:
                db.execute('ALTER TABLE writing_projects ADD COLUMN deleted_at TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS writing_projects_active ON writing_projects(deleted_at,updated_at)')
            db.executescript(DOCUMENT_SCHEMA)
            db.executescript(CITATION_TASK_SCHEMA)
            db.executescript(AUTOMATIC_RETRY_SCHEMA)
        self.operations = WritingOperations(store)

    @staticmethod
    def _load(db, project_id):
        row = db.execute('SELECT content_json FROM writing_projects WHERE id=? AND deleted_at IS NULL', (project_id,)).fetchone()
        if not row:
            raise KeyError(project_id)
        return json.loads(row['content_json'])

    @staticmethod
    def _section(project, section_id):
        section = next((s for s in project['sections'] if s['id'] == section_id), None)
        if not section:
            raise KeyError(section_id)
        return section

    @staticmethod
    def _invalidate_planning(section):
        if section.get('planningEvidence') is not None or section.get('planningReview') is not None:
            section.pop('planningEvidence', None)
            section.pop('planningReview', None)
            section['outlinePlanningState'] = 'stale'

    @staticmethod
    def _mark_steps_pending(project, *steps):
        project['workflowPendingSteps'] = sorted(set(project.get('workflowPendingSteps', [])) | set(steps))

    @staticmethod
    def _material_scope(project):
        selection = project.get('materialSelection') or {}
        source_ids = set(project.get('sourceIds', []))
        return {
            'mode': project['mode'],
            'sources': sorted((source['sourceId'], source['revisionId'],
                               tuple(sorted(set(source['blockIds']))) if 'blockIds' in source else None)
                              for source in project['sources']),
            'passages': sorted({(item['sourceId'], item['revisionId'], item['blockId'])
                                for item in selection.get('passages', [])}),
            'userSourceIds': sorted(set(selection.get('userSourceIds', source_ids)) & source_ids),
        }

    def _save(self, db, project, reason):
        before = project['version']
        project['version'] += 1
        project['updatedAt'] = now()
        project['review'] = None
        from .writing_chapter_confirmation import invalidate_changed
        invalidate_changed(project)
        for section in project['sections']:
            if section.get('citationBindings') is not None and not valid_bindings(section.get('content', ''), section.get('evidence', []), section['citationBindings'], mode=_effective(project, section)):
                section.pop('citationBindings', None)
                section['citationBindingState'] = 'stale'
        refresh_illustrations(project)
        encoded = dumps(project)
        changed = db.execute('UPDATE writing_projects SET version=?,content_json=?,updated_at=? WHERE id=? AND version=? AND deleted_at IS NULL',
                             (project['version'], encoded, project['updatedAt'], project['id'], before)).rowcount
        if changed != 1:
            raise WritingConflict('项目版本冲突，未覆盖最新稿件')
        db.execute('INSERT INTO writing_history VALUES(?,?,?,?,?)',
                   (project['id'], project['version'], encoded, reason, project['updatedAt']))
        self.operations.attach_recommendation(db, project)

    def _sources(self, db, source_ids, existing=()):
        if not isinstance(source_ids, list) or len(source_ids) > 100 or any(not isinstance(x, str) or not x or len(x) > 100 for x in source_ids):
            raise ValueError('每个写作项目最多选择 100 篇原始资料')
        pinned = {s['sourceId']: s for s in existing}
        result = []
        for source_id in dict.fromkeys(source_ids):
            if source_id in pinned:
                # Editing a brief never silently upgrades the underlying source.
                result.append(copy.deepcopy(pinned[source_id]))
                continue
            row = db.execute('SELECT s.id,r.id revision_id,r.title,r.final_url FROM sources s JOIN revisions r ON r.id=s.current_revision WHERE s.id=?', (source_id,)).fetchone()
            if not row:
                raise ValueError('选中的原始资料不存在或尚未完成导入')
            result.append({'sourceId': source_id, 'revisionId': row['revision_id'], 'title': row['title'], 'url': row['final_url']})
        return result

    def _materials(self, db, sources, selection, previous=None, previous_sources=(), *, preserve_confirmed_scope=False):
        """Confirm original blocks; discovery metadata never becomes evidence.

        Scope is stored on each frozen source so existing generation/edit fences
        also reject late candidates after a passage-only selection change.
        """
        if selection is None:
            # Older clients/projects use whole articles. An omitted selection in
            # patch is handled by the caller and does not clear narrowed scope.
            selection = {'recommendationId': None, 'passages': []}
        if not isinstance(selection, dict) or set(selection) - {'recommendationId', 'passages', 'queries', 'items', 'userSourceIds', 'confirmed', 'assistanceIds'}:
            raise ValueError('资料确认格式不正确')
        recommendation_id = selection.get('recommendationId')
        if recommendation_id is not None and (not isinstance(recommendation_id, str) or not recommendation_id or len(recommendation_id) > 100):
            raise ValueError('推荐记录标识不正确')
        passages = selection.get('passages', [])
        if not isinstance(passages, list) or len(passages) > 1000:
            raise ValueError('最多确认 1000 个原文段落')
        by_source = {source['sourceId']: source for source in sources}
        cleaned, seen, grouped = [], set(), {}
        for passage in passages:
            if not isinstance(passage, dict) or set(passage) != {'sourceId', 'revisionId', 'blockId'}:
                raise ValueError('段落确认必须包含资料、版本和原文块标识')
            if any(not isinstance(value, str) or not value or len(value) > 100 for value in passage.values()):
                raise ValueError('段落标识格式不正确')
            source = by_source.get(passage['sourceId'])
            if not source or source['revisionId'] != passage['revisionId']:
                raise WritingConflict('段落不属于所选资料的固定版本，请重新核对材料')
            key = (passage['sourceId'], passage['revisionId'], passage['blockId'])
            if key in seen:
                continue
            block = db.execute('SELECT text FROM blocks WHERE id=? AND revision_id=?', (passage['blockId'], passage['revisionId'])).fetchone()
            if not block or not block['text'].strip():
                raise ValueError('确认的段落不存在或没有可用原文')
            seen.add(key)
            cleaned.append(copy.deepcopy(passage))
            grouped.setdefault(passage['sourceId'], []).append(passage['blockId'])
        normalized = {'recommendationId': recommendation_id, 'passages': cleaned}
        if any(key in selection for key in ('queries', 'items', 'userSourceIds', 'confirmed')):
            from .retrieval import _queries, _ids
            normalized['queries'] = _queries(selection.get('queries', []))
            normalized['userSourceIds'] = _ids(selection.get('userSourceIds', []))
            if not isinstance(selection.get('confirmed', False), bool):
                raise ValueError('资料确认状态须为布尔值')
            normalized['confirmed'] = selection.get('confirmed', False)
            items = selection.get('items', [])
            if not isinstance(items, list) or len(items) > 100 or len(dumps(items)) > 1000000 or any(not isinstance(item, dict) for item in items):
                raise ValueError('资料推荐列表格式不正确或过大')
            # Discovery metadata is display-only; it is never used as evidence.
            normalized['items'] = copy.deepcopy(items)
        if 'assistanceIds' in selection:
            from .retrieval import _ids
            normalized['assistanceIds'] = _ids(selection['assistanceIds'], '写作辅助记录')
        if (preserve_confirmed_scope and (previous is None or isinstance(previous, dict))
                and 'userSourceIds' not in (previous or {}) and 'userSourceIds' not in selection):
            # A metadata save must not promote an unclassified legacy
            # recommendation into an explicit user-source exemption.
            normalized.pop('userSourceIds', None)
        unchanged = normalized == previous and {(s['sourceId'], s['revisionId']) for s in sources} == {(s['sourceId'], s['revisionId']) for s in previous_sources}
        if recommendation_id and not unchanged and not preserve_confirmed_scope:
            from .retrieval import validate_selection
            # User-selected whole documents need not appear in system results.
            # Their identities/revisions were validated by _sources already.
            own_ids = set(normalized.get('userSourceIds', []))
            recommended = [sid for sid in by_source if sid not in own_ids]
            versions = validate_selection(db, recommendation_id, recommended,
                                          [p for p in cleaned if p['sourceId'] not in own_ids])
            if any(versions.get(s['sourceId']) != s['revisionId'] for s in sources if s['sourceId'] not in own_ids):
                raise WritingConflict('推荐材料与项目固定版本不一致，请重新核对材料，原有引用未被替换')
        for source in sources:
            source.pop('blockIds', None)
            if source['sourceId'] in grouped:
                source['blockIds'] = sorted(grouped[source['sourceId']])
        return normalized

    def create(self, data):
        timestamp = now()
        brief = _brief(data.get('brief', {}))
        if not brief.get('template'):
            brief.setdefault('structureTemplate', 'research-report-decision')
        if not brief.get('style'):
            brief.setdefault('writingStyle', 'official')
        brief.setdefault('targetWords', 6000)
        brief.setdefault('workflow', 'chapter')
        brief.setdefault('automation', 'manual')
        model_id = data.get('modelId')
        if model_id and brief.get('automation') != 'automatic':
            self.models.resolve(model_id)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            sources = self._sources(db, data.get('sourceIds', []))
            material_selection = self._materials(db, sources, data.get('materialSelection'))
            title = data.get('title') or (brief.get('topic') or '')[:200]
            project = {'id': ident('writing'), 'version': 1, 'title': _text(title, '文章标题'),
                       'workflowVersion': 'writing-six-step-v1',
                       'brief': brief, 'mode': _mode(data.get('mode', 'strict')) if sources else 'extended',
                       'modelId': model_id, 'sourceIds': [s['sourceId'] for s in sources], 'sources': sources,
                       'materialSelection': material_selection,
                       'workflowPendingSteps': ['brief'],
                       'outlineConfirmed': False, 'sections': [], 'review': None,
                       'createdAt': timestamp, 'updatedAt': timestamp}
            db.execute('INSERT INTO writing_projects(id,version,content_json,created_at,updated_at) VALUES(?,?,?,?,?)', (project['id'], 1, dumps(project), timestamp, timestamp))
            db.execute('INSERT INTO writing_history VALUES(?,?,?,?,?)', (project['id'], 1, dumps(project), 'created', timestamp))
            self.operations.attach_recommendation(db, project)
        return self.get(project['id'])

    def list(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT id,content_json FROM writing_projects WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT 100').fetchall()
            projects = {row['id']: {**json.loads(row['content_json']), 'runs': []} for row in rows}
            if projects:
                placeholders = ','.join('?' for _ in projects)
                active = db.execute(f'''SELECT r.id,r.job_id,r.project_id,r.kind,j.status
                    FROM writing_runs r JOIN jobs j ON j.id=r.job_id
                    WHERE r.project_id IN ({placeholders}) AND j.status IN ('queued','running')
                    ORDER BY r.created_at DESC,r.id DESC''', list(projects)).fetchall()
                for run in active:
                    projects[run['project_id']]['runs'].append({
                        'id': run['id'], 'jobId': run['job_id'], 'kind': run['kind'], 'status': run['status'],
                    })
        # Only automatic manuscripts need their independent terminal report.
        # Ordinary cards retain the existing small activity-only response.
        for project in projects.values():
            if project.get('brief', {}).get('automation') == 'automatic' or any(run['kind'] == 'automatic' for run in project['runs']):
                self.public_automatic(project)
        return list(projects.values())

    @staticmethod
    def _assert_no_active_tasks(db, project_id, ignore_run_id=None):
        active = db.execute("SELECT 1 FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? AND r.id<>? AND j.status IN ('queued','running') LIMIT 1", (project_id, ignore_run_id or '')).fetchone()
        if active:
            raise WritingConflict('文章仍有写作任务，请先等待完成或在任务中心取消后删除')

    def _automatic_active_run_id(self):
        return getattr(self, '_automatic_parent_run', None)

    def _automatic_child_payload(self, run_id):
        payload = {'runId': run_id}
        if self._automatic_active_run_id():
            payload.update(automaticParentRunId=self._automatic_active_run_id(),
                           automaticStep=getattr(self, '_automatic_step', None))
        return payload

    def _automatic_child_guard(self, db):
        parent_id = self._automatic_active_run_id()
        if parent_id:
            parent = db.execute('SELECT j.status,j.attempts FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.id=?', (parent_id,)).fetchone()
            job = getattr(self, '_automatic_parent_job', None)
            if not parent or parent['status'] != 'running' or (job and parent['attempts'] != job['attempts'] + 1):
                raise WritingConflict('全自动写作已停止，未继续修改文稿')

    def delete(self, project_id, expected):
        """Hide a manuscript while retaining all original text and audit history."""
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            self._assert_no_active_tasks(db, project_id)
            timestamp = now()
            project.update(version=expected + 1, deletedAt=timestamp, updatedAt=timestamp)
            encoded = dumps(project)
            changed = db.execute('UPDATE writing_projects SET version=?,content_json=?,updated_at=?,deleted_at=? WHERE id=? AND version=? AND deleted_at IS NULL',
                                 (project['version'], encoded, timestamp, timestamp, project_id, expected)).rowcount
            if changed != 1:
                raise WritingConflict('文章已发生变化，请刷新后再删除')
            db.execute('INSERT INTO writing_history VALUES(?,?,?,?,?)', (project_id, project['version'], encoded, 'deleted', timestamp))
            # The shared task center must not offer retries which could revive
            # a deleted manuscript. Keep status and all captured outputs intact.
            for row in db.execute('SELECT j.id,j.input_json FROM jobs j JOIN writing_runs r ON r.job_id=j.id WHERE r.project_id=?', (project_id,)).fetchall():
                payload = json.loads(row['input_json'])
                payload.update(retryable=False, retryBlockedReason='文章已删除，无法重试此写作任务')
                db.execute('UPDATE jobs SET input_json=? WHERE id=?', (dumps(payload), row['id']))
        return {'deleted': True, 'projectId': project_id, 'deletedAt': timestamp, 'version': project['version']}

    def get(self, project_id):
        with self.store.connect() as db:
            project = self._load(db, project_id)
            rows = db.execute('SELECT r.*,j.status,j.stage,j.error FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? ORDER BY r.created_at DESC LIMIT 30', (project_id,)).fetchall()
            project['runs'] = [{'id': row['id'], 'jobId': row['job_id'], 'kind': row['kind'], 'sectionId': row['section_id'],
                                'baseVersion': row['base_version'], 'status': row['status'], 'stage': row['stage'], 'error': row['error'],
                                'applied': bool(row['applied']), 'candidate': json.loads(row['result_json']) if row['result_json'] else None,
                                'model': json.loads(row['model_json']), 'promptVersion': row['prompt_version'], 'createdAt': row['created_at']} for row in rows]
            for row, run in zip(rows, project['runs']):
                if row['kind'] == 'images' and run['candidate']:
                    frozen = json.loads(row['input_json'])
                    section = next((s for s in project['sections'] if s['id'] == frozen['sectionId']), None)
                    run['candidate']['imageSearch']['stale'] = not section or search_fingerprint(project, section) != frozen['fingerprint']
                elif row['kind'] == 'edit':
                    frozen = json.loads(row['input_json'])
                    run['operation'] = frozen['operation']
                    run['editTarget'] = self._public_target(frozen['editTarget'])
                elif row['kind'] == 'document':
                    frozen = json.loads(row['input_json'])
                    run['plan'] = frozen['plan']
                    if run['candidate']:
                        step_inputs = {item['section_id']: json.loads(item['input_json']) for item in db.execute('SELECT section_id,input_json FROM writing_document_steps WHERE run_id=?', (row['id'],))}
                        for step in run['candidate']['document']['steps']:
                            reason = self._document_safe(project, frozen, step_inputs[step['sectionId']]) if step['status'] == 'candidate' else ''
                            step['canApply'] = step['status'] == 'candidate' and not reason and run['status'] in ('succeeded', 'failed', 'cancelled')
                            step['applyBlockedReason'] = reason
                            if step['status'] == 'generating' and run['status'] == 'cancelled':
                                step['status'] = 'cancelled'
            from .writing_progress_timing import workflow_timings
            project['workflowTimings'] = workflow_timings(db, project)
            from .writing_library_availability import library_availability
            project['libraryAvailability'] = library_availability(db, project)
        for section in project['sections']:
            section['effectiveMode'] = _effective(project, section)
            from .writing_chapter_confirmation import enabled as chapter_confirmation_enabled, confirmed as chapter_confirmed
            if chapter_confirmation_enabled(project):
                section['chapterConfirmed'] = chapter_confirmed(project, section)
            section['contentHash'] = _content_hash(section['content'])
            from .writing_sentence_citations import evidence_hash
            section['evidenceHash'] = evidence_hash(section.get('evidence', []))
            section['citationSourceScopeValid'] = preserved_citation_scope_valid(section)
        project['capabilities'] = {'outline': True, 'chapterGeneration': True, 'manualEdit': True, 'sectionLock': True,
                                   'originalEvidence': True, 'ruleReview': True, 'semanticVerification': False,
                                   'webSearch': False, 'wordExport': True, 'pdfExport': True, 'htmlExport': True,
                                   'selectionEdit': True, 'editOperations': list(EDIT_OPERATIONS),
                                   'documentGeneration': True, 'adaptiveGenerationPlan': True, 'automaticWriting': True,
                                   'imageSearch': True, 'illustrations': True}
        return self.public_automatic(project)

    def patch(self, project_id, expected, data):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            original = copy.deepcopy(project)
            image_before = copy.deepcopy(project)
            previous_sources = copy.deepcopy(project['sources'])
            if 'title' in data:
                project['title'] = _text(data['title'], '文章标题')
            if 'brief' in data:
                project['brief'] = _brief(data['brief'])
            if 'mode' in data:
                project['mode'] = _mode(data['mode'])
            if 'modelId' in data:
                if data['modelId'] and project['brief'].get('automation') != 'automatic':
                    self.models.resolve(data['modelId'])
                project['modelId'] = data['modelId']
            if 'sourceIds' in data:
                selected = self._sources(db, data['sourceIds'], project['sources'])
                # The picker has no ordering control. Reposting the same
                # selection keeps its pinned scope and recommendation record.
                if {source['sourceId'] for source in selected} != set(project['sourceIds']):
                    project['sources'] = selected
                    project['sourceIds'] = [s['sourceId'] for s in selected]
            if 'materialSelection' in data:
                selection = data['materialSelection']
                previous_selection = project.get('materialSelection')
                previous_material = previous_selection or {}

                def passages_key(value):
                    if not isinstance(value, list) or any(not isinstance(item, dict) or set(item) != {'sourceId', 'revisionId', 'blockId'}
                            or any(not isinstance(part, str) for part in item.values()) for item in value):
                        return None
                    return frozenset((item['sourceId'], item['revisionId'], item['blockId']) for item in value)

                old_passages = passages_key(previous_material.get('passages', []))
                same_origin = (isinstance(selection, dict) and old_passages is not None
                    and passages_key(selection.get('passages', [])) == old_passages
                    and selection.get('recommendationId') == previous_material.get('recommendationId')
                    and {(item['sourceId'], item['revisionId']) for item in project['sources']}
                    == {(item['sourceId'], item['revisionId']) for item in previous_sources})
                if (same_origin and 'userSourceIds' not in selection and 'userSourceIds' in previous_material
                        and selection != previous_selection):
                    selection = copy.deepcopy(selection)
                    own_ids = previous_material.get('userSourceIds', project['sourceIds'])
                    selection['userSourceIds'] = [sid for sid in own_ids if sid in project['sourceIds']]
                # Queries and result-card metadata do not accept a new source
                # decision. A changed recommendation/passage scope still goes
                # through its original snapshot and fixed-version checks.
                old_own = previous_material.get('userSourceIds', original['sourceIds'])
                new_own = selection.get('userSourceIds', project['sourceIds']) if isinstance(selection, dict) else None
                same_ownership = (isinstance(old_own, list) and isinstance(new_own, list)
                    and all(isinstance(item, str) for item in [*old_own, *new_own])
                    and set(old_own) & set(original['sourceIds']) == set(new_own) & set(project['sourceIds']))
                project['materialSelection'] = self._materials(db, project['sources'], selection,
                    project.get('materialSelection'), previous_sources,
                    preserve_confirmed_scope=same_origin and same_ownership)
            elif project['sourceIds'] != original['sourceIds'] and project.get('materialSelection'):
                # Retain narrowed blocks on unchanged sources for older clients.
                project['materialSelection']['passages'] = [p for p in project['materialSelection']['passages'] if p['sourceId'] in project['sourceIds']]
                project['materialSelection']['recommendationId'] = None
            if project.get('workflowVersion') == 'writing-six-step-v1' and not project['sourceIds']:
                project['mode'] = 'extended'
            if project != original:
                brief_changed = any(project.get(key) != original.get(key) for key in ('title', 'brief', 'modelId'))
                scope_changed = self._material_scope(project) != self._material_scope(original)
                materials_changed = scope_changed or project.get('materialSelection') != original.get('materialSelection')
                if brief_changed:
                    self._mark_steps_pending(project, 'brief', 'body')
                if materials_changed:
                    self._mark_steps_pending(project, 'materials')
                if scope_changed:
                    self._mark_steps_pending(project, 'body')
                if scope_changed or any(project.get(key) != original.get(key) for key in ('title', 'brief')):
                    project['outlineConfirmed'] = False
                    for section in project['sections']:
                        self._invalidate_planning(section)
                preserve_approved_illustrations(image_before, project)
                self._save(db, project, 'brief_saved')
        return self.get(project_id)

    def _sections(self, items, mode, existing=()):
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise ValueError('纲要必须包含 1 至 20 个章节')
        by_id = {s['id']: s for s in existing}
        result, seen = [], set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('章节须为对象')
            sid = item.get('id')
            if sid and sid not in by_id:
                raise ValueError('纲要包含不属于当前项目的章节')
            sid = sid or ident('section')
            if sid in seen:
                raise ValueError('纲要章节标识不能重复')
            seen.add(sid)
            previous = by_id.get(sid)
            section = copy.deepcopy(previous) if previous else {'id': sid, 'version': 1, 'content': '', 'evidence': [], 'locked': False, 'manual': False}
            words = item.get('targetWords', (previous or {}).get('targetWords', 600))
            if not isinstance(words, int) or isinstance(words, bool) or not 100 <= words <= 5000:
                raise ValueError('章节目标字数须为 100 至 5000')
            edits = {'title': _text(item.get('title'), '章节标题'), 'goal': _text(item.get('goal', ''), '章节目标', 2000, empty=True),
                     'mode': _mode(item.get('mode') or (previous or {}).get('mode') or 'inherit', inherit=True), 'targetWords': words}
            if previous and previous['locked'] and any(previous.get(key) != val for key, val in edits.items()):
                raise WritingConflict('锁定章节不可调整纲要，请先解锁')
            section.update(edits)
            if previous and any(previous.get(key) != edits[key] for key in ('title', 'goal', 'mode')):
                self._invalidate_planning(section)
            if previous and any(previous.get(key) != val for key, val in edits.items()):
                section['version'] += 1
            result.append(section)
        if any((s['content'] or s['locked']) and s['id'] not in seen for s in existing):
            raise WritingConflict('不能从纲要删除已有正文或已锁定章节，请先保存备份并清空、解锁该章')
        return result

    def outline(self, project_id, expected, sections):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            normalized = self._sections(sections, project['mode'], project['sections'])
            if normalized != project['sections']:
                project['sections'] = normalized
                project['outlineConfirmed'] = False
                self._mark_steps_pending(project, 'body')
                self._save(db, project, 'outline_saved')
        return self.get(project_id)

    def confirm(self, project_id, expected):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            if not project['sections']:
                raise ValueError('请先保存至少一个章节的纲要')
            if not project['outlineConfirmed']:
                project['outlineConfirmed'] = True
                self._save(db, project, 'outline_confirmed')
        return self.get(project_id)

    def save_section(self, project_id, section_id, expected, data):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            section = self._section(project, section_id)
            original = copy.deepcopy(section)
            if section['locked'] and any(key in data for key in ('content', 'mode')):
                raise WritingConflict('章节已锁定，请先解锁后编辑')
            if 'content' in data:
                content = _raw_text(data['content'], '章节正文', 60000, empty=True)
                if content != section['content']:
                    preserve_introduction(section, content)
                    section['content'] = content
                    section['manual'] = True
            if 'mode' in data:
                before_mode = _effective(project, section)
                section['mode'] = _mode(data['mode'], inherit=True)
                if _effective(project, section) != before_mode:
                    self._invalidate_planning(section)
                if section['mode'] != original['mode']:
                    project['outlineConfirmed'] = False
            if 'locked' in data:
                if not isinstance(data['locked'], bool):
                    raise ValueError('锁定值须为布尔值')
                section['locked'] = data['locked']
            if section != original:
                if any(section.get(key) != original.get(key) for key in ('content', 'mode')):
                    self._mark_steps_pending(project, 'body')
                section['version'] += 1
                self._save(db, project, 'section_saved')
        return self.get(project_id)

    def confirm_step(self, project_id, expected, step):
        """Confirm saved business content without generating or rewriting it."""
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            self._automatic_child_guard(db)
            self._assert_no_active_tasks(db, project_id, self._automatic_active_run_id())
            if step not in ('brief', 'materials', 'body'):
                raise ValueError('只支持确认主题、资料或正文步骤')
            pending = project.get('workflowPendingSteps', [])
            from .writing_chapter_confirmation import enabled as chapter_confirmation_enabled, confirm as confirm_chapter
            approved = False
            chapter_body = (step == 'body' and chapter_confirmation_enabled(project)
                            and not self._automatic_active_run_id())
            if chapter_body:
                if not project['sections'] or any(not section['content'].strip() for section in project['sections']):
                    raise ValueError('请先完成各章正文，再确认正文')
                for section in project['sections']:
                    approved = confirm_chapter(project, section) or approved
            if step in pending:
                if step == 'brief':
                    _text(project['brief'].get('topic'), '写作主题', 1000)
                elif step == 'materials' and project['mode'] == 'strict' and not project['sources']:
                    raise ValueError('严格模式缺少可用原文资料，请先选择资料或改用拓展模式')
                project['workflowPendingSteps'] = sorted(set(pending) - {step})
                if chapter_body:
                    self._save_chapter_confirmation(db, project, expected)
                else:
                    self._save(db, project, 'step_confirmed_' + step)
            elif approved:
                self._save_chapter_confirmation(db, project, expected)
        return self.get(project_id)

    @staticmethod
    def _save_chapter_confirmation(db, project, expected):
        # Like review acknowledgements, an author's approval changes metadata,
        # not the saved writing version or its existing verification report.
        changed = db.execute('UPDATE writing_projects SET content_json=? WHERE id=? AND version=? AND deleted_at IS NULL',
                             (dumps(project), project['id'], expected)).rowcount
        if changed != 1:
            raise WritingConflict('项目版本冲突，未覆盖最新稿件')

    def confirm_section(self, project_id, section_id, expected):
        """Approve a saved chapter without model calls, locking or verification."""
        from .writing_chapter_confirmation import POLICY, can_enable_sequential, enabled, confirm
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            self._automatic_child_guard(db)
            self._assert_no_active_tasks(db, project_id, self._automatic_active_run_id())
            activate = not enabled(project)
            if activate and not can_enable_sequential(project):
                raise ValueError('当前文稿未启用逐章确认')
            section = self._section(project, section_id)
            if not section['content'].strip():
                raise ValueError('请先保存本章正文，再确认本章')
            if activate:
                # Only this explicit approval opts a sequential draft in. Any
                # unexposed historical records cannot approve other chapters.
                for chapter in project['sections']:
                    chapter.pop('chapterConfirmation', None)
                project['chapterConfirmationPolicy'] = POLICY
                self._mark_steps_pending(project, 'body')
            if confirm(project, section):
                self._save_chapter_confirmation(db, project, expected)
        return self.get(project_id)

    def rebind_citations(self, project_id, expected, sections, *, method='model_review'):
        """Atomically relocate citations while preserving every prose byte.

        ``sections`` contains sectionId, contentHash of the originally read
        raw section, and validated matching decisions against marker-stripped
        UTF-16 offsets. Even locked/manual prose is safe because no body edit
        or evidence replacement is accepted by this narrow operation.
        """
        if not isinstance(sections, list) or not sections or len(sections) > 20:
            raise ValueError('重新匹配须包含 1 至 20 个章节')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            if db.execute("SELECT 1 FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? AND j.status IN ('queued','running') LIMIT 1", (project_id,)).fetchone():
                raise WritingConflict('请等待当前写作任务完成或取消后再重新匹配引用')
            self._rebind_citation_sections(db, project, sections, method=method)
            self._save(db, project, 'citations_rematched')
        return self.get(project_id)

    def _rebind_citation_sections(self, db, project, sections, *, method='model_review'):
        """Validate/apply in caller's transaction; caller owns CAS and _save."""
        if not isinstance(sections, list) or not sections or len(sections) > 20:
            raise ValueError('重新匹配须包含 1 至 20 个章节')
        before_citations = copy.deepcopy(project)
        seen = set()
        for item in sections:
            if not isinstance(item, dict) or item.get('sectionId') in seen:
                raise ValueError('重新匹配的章节格式无效或重复')
            sid = item.get('sectionId')
            seen.add(sid)
            section = self._section(project, sid)
            allowed = {(source['sourceId'], source['revisionId']): source for source in citation_sources(project, section)}
            if item.get('contentHash') != citation_content_hash(section['content']):
                raise WritingConflict('引用匹配依据的正文已变化，保留最新正文，请重新匹配')
            for evidence in section['evidence']:
                source = allowed.get((evidence['sourceId'], evidence['revisionId']))
                if not source or ('blockIds' in source and evidence['blockId'] not in source['blockIds']):
                    raise ValueError('引用不在当前固定资料范围，不能重新绑定')
                block = db.execute('SELECT b.text,r.source_id FROM blocks b JOIN revisions r ON r.id=b.revision_id WHERE b.id=? AND b.revision_id=?', (evidence['blockId'], evidence['revisionId'])).fetchone()
                if not block or block['source_id'] != evidence['sourceId'] or not (0 <= evidence['start'] < evidence['end'] <= len(block['text'])) or block['text'][evidence['start']:evidence['end']] != evidence['quote']:
                    raise ValueError('引用的固定版本原文定位失效，不能重新绑定')
            review_mode = item.get('mode')
            if review_mode is not None and review_mode != _effective(project, section):
                raise WritingConflict('引用匹配模式与当前章节不一致，请重新核对')
            updated, binding = relocate_citations(section['content'], section['evidence'], item.get('matches'), method=method,
                                                 mode=review_mode, support_policy=item.get('supportPolicy'))
            if strip_citations(updated) != strip_citations(section['content']):
                raise WritingConflict('重新匹配改变了正文，结果未应用')
            section['content'] = updated
            if item.get('citationReviewPolicy') == DEFERRED_CITATION_REVIEW_POLICY:
                section['citationReviewPolicy'] = DEFERRED_CITATION_REVIEW_POLICY
            section['citationBindings'] = binding
            section['citationBindingState'] = 'matched' if all(entry['status'] in RESOLVED_STATUSES for entry in binding['items']) else 'needs_review'
            section['citationIssues'] = [{'status': entry['status'], 'sentence': entry['text'], 'reason': entry['reason'], 'evidenceId': entry.get('evidenceId')}
                                         for entry in binding['items'] if entry['status'] not in RESOLVED_STATUSES]
            section['version'] += 1

        reanchor_citation_only_images(before_citations, project)

    def _messages(self, project, kind, section, config, db, *, outline_policy=ABSTRACT_OUTLINE_POLICY,
                  citation_retention_policy=CITATION_RETENTION_POLICY,
                  citation_review_policy=DEFERRED_CITATION_REVIEW_POLICY,
                  section_envelope_policy=SECTION_ENVELOPE_POLICY,
                  composition_snapshot=None, composition_prompt=None, preference_snapshot=None):
        actual_mode = _effective(project, section) if section else project['mode']
        prompt_id = writing_prompt_id(kind, actual_mode)
        if composition_snapshot is not None and composition_snapshot.get('policy') != COMPOSITION_POLICY:
            raise ValueError('写作组织规则版本不受支持')
        system = (composition_prompt if composition_prompt is not None else
                  prompt_text(prompt_id, db=db, composition_policy=COMPOSITION_POLICY) if composition_snapshot is not None else
                  prompt_text(prompt_id, db=db))
        payload = {'task': 'writing_' + kind, 'promptVersion': PROMPT_VERSION, 'title': project['title'],
                   'generationOutputPolicy': OUTPUT_POLICY,
                   'brief': project['brief'], 'mode': actual_mode, 'promptId': prompt_id, 'blocks': []}
        if composition_snapshot is not None:
            payload['compositionPolicy'] = COMPOSITION_POLICY
            instructions = composition_snapshot['proseInstructions' if section else 'outlineInstructions']
            if actual_mode == 'strict':
                instructions += '\n' + composition_snapshot['strictInstructions']
            system += '\n' + instructions
            if section:
                # Previously saved prose provides continuity only. It never
                # enters blocks, the source registry or citation aliases.
                payload['chapterContext'] = chapter_context(project, section, composition_snapshot)
        source_context = []
        for source in project['sources']:
            metadata = db.execute('SELECT published_at FROM revisions WHERE id=? AND source_id=?',
                                  (source['revisionId'], source['sourceId'])).fetchone()
            source_context.append({'sourceId': source['sourceId'], 'revisionId': source['revisionId'],
                                   'publishedAt': metadata['published_at'] if metadata else None})
        payload.update(originalInputPolicy=MATERIAL_INPUT_POLICY, sourceContext=source_context,
                       temporalAndAttributionRequirements=TEMPORAL_REQUIREMENT)
        if section:
            temporal_review = temporal_review_snapshot(actual_mode, source_context, as_of=project['brief'].get('asOf'))
            if temporal_review is not None:
                payload['citationTemporalReview'] = temporal_review
            payload['section'] = {key: section[key] for key in ('id', 'title', 'goal', 'mode', 'targetWords')}
            payload['section']['mode'] = actual_mode
            payload['outline'] = [{'title': s['title'], 'goal': s['goal'], 'mode': s['mode']} for s in project['sections']]
            payload['outputSchema'] = {'paragraphs': [{'text': '正文段落', 'kind': 'supported 或 model_supplement',
                                                     'evidence': [{'blockId': '从 blocks 复制', 'quote': '逐字原文',
                                                                   'claim': '从本段 text 逐字复制的唯一完整受支持句子'}]}], 'issues': []}
            payload['outputSchema']['contentStatus'] = 'usable；仅完全偏题填 unrelated，异常中断填 interrupted'
            payload['generationOutputRequirements'] = ('一般内容、引用或格式问题写入 issues，仍返回可用正文；'
                '提示必须放在正文之外，不能在正文添加检查标签。只有完全偏题或异常中断才不作为可用结果。')
            if section_envelope_policy == SECTION_ENVELOPE_POLICY:
                payload['sectionOutputEnvelopePolicy'] = section_envelope_policy
                payload['generationOutputRequirements'] += (
                    '章节结果对象直接包含 paragraphs、issues 和 contentStatus，不要添加 type/content 包装。')
            payload['citationPlacement'] = CITATION_PLACEMENT
            if citation_review_policy is not None:
                payload['citationReviewPolicy'] = citation_review_policy
                payload['citationReviewScope'] = SELECTIVE_REVIEW_POLICY
                payload['citationReviewRequirement'] = (
                    '生成正文时同步返回事实、观点及明确引用的对应完整句 claim 与逐字原文 quote，'
                    '由程序定位后登记引用，支持关系在最后检查校验阶段核对。'
                    '忠实总结前文、过渡、重复提及及常识介绍不要求每句或每段附引用；'
                    '严格模式仍不得新增材料未明示的事实、因果、预测、意图、评价或机制。'
                    '无可定位依据时 evidence=[]，将材料缺口写入 issues，不编造引用或混入正文。')
                system += ('\n本次正文引用规则：' + payload['citationReviewRequirement']
                           + ' 此规则替代此前对总结、过渡或重复表述逐段逐句登记引用的要求；严格模式的事实边界保持。')
            if citation_retention_policy is not None:
                payload['citationRetentionPolicy'] = citation_retention_policy
            if actual_mode == 'extended':
                payload['unmatchedCitationPolicy'] = UNMATCHED_CITATION_POLICY
                payload['unmatchedCitationRequirement'] = ('允许超出所选原文的补充内容；无法对应原文时使用 model_supplement 和空 evidence，'
                    '正文正常呈现，不自动添加模型补充或待核实标签，不编造原文引用。')
            if outline_policy != ABSTRACT_OUTLINE_POLICY and actual_mode == 'strict' and section.get('planningEvidence') and section.get('planningReview', {}).get('state') == 'supported':
                payload['plannedContent'] = {'title': section['title'], 'goal': section['goal'],
                    'evidenceBlockIds': list(dict.fromkeys(reference['blockId'] for reference in section['planningEvidence'])),
                    'requirements': '本章按已确认的有据规划组织正文，保持本次实际模式，不能推理补足规划外的内容。'}
        else:
            payload['outputSchema'] = {'sections': [{'title': '章节标题', 'goal': '论证目标及需补资料', 'mode': 'strict 或 extended', 'targetWords': 600}], 'issues': []}
            payload['requirements'] = (preference_snapshot['authorOutlineRequirement']
                if preference_snapshot is not None and preference_snapshot.get('authorStructurePolicy') == AUTHOR_STRUCTURE_POLICY
                else '生成 2 至 8 章纲要。纲要是待用户确认的计划，不写正文；所有章节保持请求的 mode。')
            if outline_policy == ABSTRACT_OUTLINE_POLICY:
                payload['outlinePolicy'] = ABSTRACT_OUTLINE_POLICY
                payload['requirements'] += ABSTRACT_OUTLINE_REQUIREMENT
                payload['outputSchema']['sections'][0]['mode'] = actual_mode
                system += '\n' + ABSTRACT_OUTLINE_REQUIREMENT
            elif actual_mode == 'strict':
                payload['outlinePlanningProtocol'] = OUTLINE_PLANNING_PROTOCOL
                payload['outputSchema'] = {'protocol': OUTLINE_PLANNING_PROTOCOL, 'sections': [{
                    'title': '现有原文明示内容可支持的章节标题', 'goal': '本章有据写作范围和要点，不写待补推断',
                    'mode': 'strict', 'targetWords': 600,
                    'planningEvidence': [{'blockId': '从本次 blocks 复制', 'quote': '支持本章规划的唯一逐字原文'}]}],
                    'issues': []}
                payload['requirements'] = ('只在本次 blocks 明示内容范围内规划 1 至 8 章，忠实归纳而不推理扩充；'
                    '每章标题及目标的全部写作内容均须有 planningEvidence 支撑，不能只因话题相关就规划原文未给的机制、影响、对策或预测。'
                    '无法覆盖的需求写入 issues；没有可写章节时 sections=[] 且 issues 非空，不为章节数或目标字数填入缺料计划。'
                    '只返回纲要，等待用户确认，不写正文；每章 mode=strict。')
        system = apply_preferences(system, payload, preference_snapshot, section=section, sections=project['sections'])
        budget = int(config.get('contextChars', 24000))
        if len(system) + len(dumps(payload)) + 500 > budget:
            raise ValueError('写作需求超出模型输入预算，请缩短需求或提高模型字符预算')
        blocks = []
        for source in project['sources']:
            selected_blocks = set(source['blockIds']) if 'blockIds' in source else None
            for block in db.execute('SELECT id,text,kind,ordinal,locator_json FROM blocks WHERE revision_id=? ORDER BY ordinal', (source['revisionId'],)):
                if not block['text'].strip() or selected_blocks is not None and block['id'] not in selected_blocks:
                    continue
                blocks.append({'blockId': block['id'], 'text': block['text'][:4000], **source,
                               'originalTextChars': len(block['text']),
                               'ordinal': block['ordinal'], 'kind': block['kind'],
                               'locator': json.loads(block['locator_json'])})
        query = (section['title'] + ' ' + section['goal']) if section else project['title'] + ' ' + str(project['brief'].get('purpose') or project['brief'].get('topic', ''))
        planned_ids = set()
        if outline_policy != ABSTRACT_OUTLINE_POLICY and section and actual_mode == 'strict' and section.get('planningEvidence'):
            planned_ids = {reference['blockId'] for reference in section['planningEvidence']}
            if not planned_ids.issubset({block['blockId'] for block in blocks}):
                raise ValueError('本章纲要的原文依据已不在当前资料范围，请重新规划纲要')
        blocks = material_order(blocks, query, outline=kind == 'outline', planned_ids=planned_ids)
        if kind == 'outline':
            # Keep a dated original fragment near each source's first substantive
            # fragment. Dates are original text, never reconstructed from import time.
            first_ids, dates, seen_sources = [], {}, set()
            for block in blocks:
                if date_block(block):
                    dates.setdefault(block['sourceId'], block)
            for block in blocks:
                if block['sourceId'] not in seen_sources:
                    first_ids.append(block['blockId'])
                    seen_sources.add(block['sourceId'])
            first = [block for block in blocks if block['blockId'] in set(first_ids)]
            prefix = list({block['blockId']: block for block in first + list(dates.values())}.values())
            prefix_ids = {block['blockId'] for block in prefix}
            blocks = prefix + [block for block in blocks if block['blockId'] not in prefix_ids]
        payload['inputCoverage'] = {'policy': MATERIAL_INPUT_POLICY,
            'selectedSources': len(project['sources']), 'includedSources': 0,
            'availableBlocks': len(blocks), 'includedBlocks': 0, 'truncatedBlocks': 0, 'complete': False,
            'requirement': '只依据实际提供的blocks；未提供的原文不能据此断言整篇资料没有相应信息。'}
        chosen = []
        for block in blocks:
            # The model can distinguish original sources and their pinned
            # revisions, but only block.text may supply a verbatim quote.
            # Full locators remain frozen for program-side registration; an
            # unusually large locator cannot consume the evidence budget.
            locator = block['locator']
            if len(dumps(locator)) > 1000:
                locator = {'ordinal': block['ordinal'], 'metadataTruncated': True}
            entry = {key: block[key] for key in ('blockId', 'sourceId', 'revisionId', 'text', 'ordinal')}
            entry.update(title=block['title'][:200], url=block['url'][:2000], locator=locator)
            payload['blocks'].append(entry)
            if len(system) + len(dumps(payload)) > budget - 200:
                payload['blocks'].pop()
                continue
            chosen.append(block)
            if len(chosen) >= (400 if kind == 'outline' else 40):
                break
        if kind == 'outline':
            missing = {source['sourceId'] for source in project['sources']} - {block['sourceId'] for block in chosen}
            if missing:
                raise ValueError('当前输入预算无法覆盖每篇所选资料的原文，请减少资料范围或提高模型输入预算')
        payload['inputCoverage'].update(includedSources=len({b['sourceId'] for b in chosen}),
            includedBlocks=len(chosen), truncatedBlocks=sum(b['originalTextChars'] > len(b['text']) for b in chosen),
            complete=len(chosen) == len(blocks) and not any(b['originalTextChars'] > len(b['text']) for b in chosen))
        if len(system) + len(dumps(payload)) > budget:
            raise ValueError('资料覆盖说明超出模型输入预算，请减少资料范围或提高模型输入预算')
        if outline_policy != ABSTRACT_OUTLINE_POLICY and section and actual_mode == 'strict' and section.get('planningEvidence'):
            if not planned_ids.issubset({block['blockId'] for block in chosen}):
                raise ValueError('本章纲要依据超过当前模型输入预算，请缩小章节规划或提高模型输入预算')
        if actual_mode == 'strict' and not chosen:
            raise ValueError('严格模式缺少可用原文证据，请先选择原始资料或补充资料')
        return [{'role': 'system', 'content': system}, {'role': 'user', 'content': dumps(payload)}], chosen

    def queue(self, project_id, expected, kind, section_id=None):
        project = self.get(project_id)
        _version(project, expected)
        config = self._writing_config(project)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            self._automatic_child_guard(db)
            section = self._section(project, section_id) if kind == 'section' else None
            if section:
                if not project['outlineConfirmed']:
                    raise ValueError('请先确认最新纲要，再按章生成正文')
                if section['locked']:
                    raise WritingConflict('锁定章节不可生成，请先解锁')
            elif any(s['content'] or s['locked'] for s in project['sections']):
                raise WritingConflict('已有正文的项目不能重新生成纲要，请手工调整或新建项目')
            active = db.execute("SELECT r.*,j.* FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? AND r.id<>? AND j.status IN ('queued','running') ORDER BY r.created_at DESC LIMIT 1", (project_id, self._automatic_active_run_id() or '')).fetchone()
            if active:
                if active['kind'] == kind and active['section_id'] == section_id and active['base_version'] == expected:
                    job_id, run_id = active['job_id'], active['id']
                else:
                    raise WritingConflict('当前项目已有写作任务，请等待完成或在任务中心取消后继续')
            else:
                # A parent queued under the previous outline contract keeps it
                # even if it had not yet created its outline child at upgrade.
                parent_frozen = None
                if self._automatic_active_run_id():
                    parent = db.execute('SELECT input_json FROM writing_runs WHERE id=?',
                                        (self._automatic_active_run_id(),)).fetchone()
                    parent_frozen = json.loads(parent['input_json']) if parent else {}
                outline_policy = (ABSTRACT_OUTLINE_POLICY if parent_frozen is None or abstract_outline(parent_frozen) else None)
                retention_policy = (CITATION_RETENTION_POLICY if parent_frozen is None else parent_frozen.get('citationRetentionPolicy'))
                review_policy = (DEFERRED_CITATION_REVIEW_POLICY if parent_frozen is None else parent_frozen.get('citationReviewPolicy'))
                envelope_policy = (SECTION_ENVELOPE_POLICY if parent_frozen is None else parent_frozen.get('sectionOutputEnvelopePolicy'))
                composition = (composition_snapshot_for_task(db=db) if parent_frozen is None else parent_frozen.get('compositionSnapshot'))
                preferences = (preference_snapshot_for_task(project['brief'], db=db) if parent_frozen is None
                               else parent_frozen.get('preferenceSnapshot'))
                prompt_id = writing_prompt_id(kind, _effective(project, section) if section else project['mode'])
                inherited_prompts = parent_frozen.get('compositionPrompts', {}) if parent_frozen is not None else {}
                inherited_prompt = inherited_prompts.get(prompt_id) if composition is not None else None
                if composition is not None and parent_frozen is not None and not inherited_prompt:
                    raise ValueError('自动写作缺少已冻结的写作组织指令')
                messages, blocks = self._messages(project, kind, section, config, db,
                                                  outline_policy=outline_policy, citation_retention_policy=retention_policy,
                                                  citation_review_policy=review_policy, composition_snapshot=composition,
                                                  section_envelope_policy=envelope_policy,
                                                  preference_snapshot=preferences,
                                                  composition_prompt=inherited_prompt['content'] if inherited_prompt else None)
                job_id, run_id, timestamp = ident('job'), ident('writing_run'), now()
                frozen = {'projectId': project_id, 'section': copy.deepcopy(section), 'mode': project['mode'],
                          'title': project['title'], 'brief': project['brief'], 'effectiveMode': _effective(project, section) if section else project['mode'],
                          'promptId': writing_prompt_id(kind, _effective(project, section) if section else project['mode']),
                          'sources': project['sources'], 'messages': messages, 'blocks': blocks}
                if composition is not None:
                    frozen['compositionSnapshot'] = copy.deepcopy(composition)
                if preferences is not None:
                    frozen['preferenceSnapshot'] = copy.deepcopy(preferences)
                if kind == 'section' and review_policy is not None:
                    frozen['citationReviewPolicy'] = review_policy
                if kind == 'section' and envelope_policy == SECTION_ENVELOPE_POLICY:
                    frozen['sectionOutputEnvelopePolicy'] = envelope_policy
                if kind == 'outline' and outline_policy == ABSTRACT_OUTLINE_POLICY:
                    frozen['outlinePolicy'] = ABSTRACT_OUTLINE_POLICY
                if parent_frozen is not None:
                    if automatic_citations_report_only(parent_frozen):
                        frozen['automaticCitationPolicy'] = parent_frozen['automaticCitationPolicy']
                if kind == 'section' or (kind == 'outline' and frozen['effectiveMode'] == 'strict' and not abstract_outline(frozen)):
                    frozen['citationMatchInstructions'] = prompt_text('writing_citation_match', db=db)
                    frozen['citationMatchMode'] = frozen['effectiveMode']
                    frozen['citationMatchPolicy'] = matching_policy(frozen['effectiveMode'])
                if kind == 'outline' and frozen['effectiveMode'] == 'strict' and not abstract_outline(frozen):
                    frozen['outlinePlanningProtocol'] = OUTLINE_PLANNING_PROTOCOL
                    frozen['outlineIssueReviewInstructions'] = OUTLINE_ISSUE_REVIEW_INSTRUCTIONS
                    frozen = prepare_strict_outline(frozen, context_chars=config.get('contextChars', 24000))
                elif kind == 'section':
                    frozen = scope_prepared(frozen, next(index for index, item in enumerate(project['sections'], 1) if item['id'] == section_id))
                    frozen = freeze_generation_temporal_review(frozen)
                    if sum(len(message['content']) for message in frozen['messages']) > int(config.get('contextChars', 24000)):
                        raise ValueError('章节冻结引用范围超过模型输入预算，请缩小材料范围或提高字符预算')
                public = {key: config.get(key) for key in ('id', 'version', 'name', 'model')}
                db.execute('INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                           (job_id, ident('writing_batch'), '写作 · ' + (section['title'] if section else project['title'] + '纲要'),
                            'writing_' + kind, dumps(self._automatic_child_payload(run_id)), 'queued', 'queued', timestamp, timestamp))
                db.execute('INSERT INTO writing_runs(id,job_id,project_id,section_id,kind,base_version,model_id,model_version,model_json,prompt_version,input_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (run_id, job_id, project_id, section_id, kind, expected, config['id'], config['version'], dumps(public), PROMPT_VERSION, dumps(frozen), timestamp))
                prompt_ids = (frozen['promptId'], 'writing_citation_match') if 'citationMatchInstructions' in frozen else (frozen['promptId'],)
                prompt_snapshots = snapshot(db, prompt_ids, composition_policy=COMPOSITION_POLICY) if composition is not None else snapshot(db, prompt_ids)
                if inherited_prompt:
                    prompt_snapshots[frozen['promptId']] = copy.deepcopy(inherited_prompt)
                prompt_snapshots.update(copy.deepcopy((preferences or {}).get('stylePromptSnapshot', {})))
                prompt_snapshots.update(copy.deepcopy((preferences or {}).get('structurePromptSnapshot', {})))
                prompt_snapshots.update(copy.deepcopy((composition or {}).get('compositionPromptSnapshot', {})))
                freeze(db, job_id, prompt_snapshots)
        return {'job': self.store.job(job_id), 'runId': run_id, 'project': self.get(project_id)}

    @staticmethod
    def _public_target(target):
        return {key: value for key, value in target.items() if key not in ('startCodepoint', 'endCodepoint')}

    def _edit_target(self, project, section, data):
        if section['locked']:
            raise WritingConflict('章节已锁定，请先解锁后选择文字修改')
        section_version = data.get('expectedSectionVersion')
        if not isinstance(section_version, int) or isinstance(section_version, bool) or section_version != section['version'] or data.get('baseContentHash') != _content_hash(section['content']):
            raise WritingConflict('章节版本或正文已改变，请保存并重新选择文字')
        selection = data.get('selection')
        if not isinstance(selection, dict) or selection.get('unit') != 'utf16':
            raise ValueError('选区必须明确使用 UTF-16 偏移')
        exact = _raw_text(selection.get('selectedExact'), '选区原文', 20000)
        start = _utf16_position(section['content'], selection.get('start'))
        end = _utf16_position(section['content'], selection.get('end'))
        if start >= end or section['content'][start:end] != exact:
            raise WritingConflict('选区原文与已保存正文不一致，请重新选择')
        for marker in re.finditer(CITATION_PATTERN + '|' + re.escape(SUPPLEMENT), section['content']):
            if marker.start() < start < marker.end() or marker.start() < end < marker.end():
                raise ValueError('选区不能切开引用编号或模型补充标记，请扩大或调整选区')
        return {'projectVersion': project['version'], 'sectionVersion': section['version'],
                'contentHash': _content_hash(section['content']), 'selectionHash': _content_hash(exact),
                'start': selection['start'], 'end': selection['end'], 'unit': 'utf16', 'selectedExact': exact,
                'startCodepoint': start, 'endCodepoint': end}

    def _edit_messages(self, project, section, target, operation, instruction, config, db, *, preference_snapshot=None):
        start, end = target['startCodepoint'], target['endCodepoint']
        content = section['content']
        # Include evidence attached to the enclosing paragraph, even when the
        # user selects a phrase before a citation at the paragraph's end.
        separators = list(re.finditer(r'\n\s*\n', content))
        left = max((match.end() for match in separators if match.end() <= start), default=0)
        right = min((match.start() for match in separators if match.start() >= end), default=len(content))
        related_ids = set(re.findall(r'\[(E\d+)\]', content[left:right]))
        refs = {ref['id']: ref for ref in section['evidence']}
        if related_ids - set(refs):
            raise ValueError('选区所在段落包含未登记引用，请先修正引用编号')
        allowed = {(source['sourceId'], source['revisionId']) for source in project['sources']}
        narrowed = {(source['sourceId'], source['revisionId']): set(source['blockIds']) for source in project['sources'] if 'blockIds' in source}
        evidence = []
        for eid in sorted(related_ids):
            ref = refs[eid]
            block = db.execute('SELECT b.text,r.source_id FROM blocks b JOIN revisions r ON r.id=b.revision_id WHERE b.id=? AND b.revision_id=?', (ref['blockId'], ref['revisionId'])).fetchone()
            scope = (ref['sourceId'], ref['revisionId'])
            if scope not in allowed or scope in narrowed and ref['blockId'] not in narrowed[scope] or not block or block['source_id'] != ref['sourceId'] or block['text'][ref['start']:ref['end']] != ref['quote']:
                raise ValueError('选区引用不在当前资料范围或无法定位原文，请先修正资料引用')
            evidence.append(copy.deepcopy(ref))
        actual_mode = _effective(project, section)
        if operation == 'expand' and actual_mode == 'strict' and not evidence:
            raise ValueError('严格扩写需要选区所在段落已登记的原文证据，请先补充有据正文或调整选区')
        system = prompt_text('writing_edit_' + operation, db=db)
        # Only new edits compile these rules. Execution and retries reuse the
        # persisted messages, leaving historical defaults and jobs unchanged.
        from .prompt_defaults import WRITING_READER_CONTENT_BOUNDARY, WRITING_NATURAL_CONTINUITY
        system += ('\n程序固定正文约束：' + WRITING_READER_CONTENT_BOUNDARY + WRITING_NATURAL_CONTINUITY
                   + ' 仅在 selectedText 范围内调整，不补写或改动选区外正文；选区外问题写入 issues。'
                   + 'changeSummary 只向作者说明修改，不复制进 replacementText。')
        payload = {'task': 'writing_edit', 'promptVersion': EDIT_PROMPT_VERSION, 'operation': operation,
                   'operationName': EDIT_OPERATIONS[operation], 'mode': actual_mode, 'title': project['title'],
                   'generationOutputPolicy': OUTPUT_POLICY,
                   'brief': project['brief'], 'section': {'title': section['title'], 'goal': section['goal']},
                   'instruction': instruction, 'editTarget': self._public_target(target), 'selectedText': target['selectedExact'],
                   'context': {'before': content[left:start][-1500:], 'after': content[end:right][:1500],
                               'note': '仅供衔接，选区外内容不得改写；上下文不是新的原文证据。'},
                   'evidence': evidence,
                   'outputSchema': {'replacementText': '仅选区替换文本，保留空白与引用标记', 'changeSummary': '修改说明', 'issues': [], 'supplement': False}}
        if actual_mode == 'extended':
            payload['unmatchedCitationPolicy'] = UNMATCHED_CITATION_POLICY
        preferences = (preference_snapshot if preference_snapshot is not None else
                       preference_snapshot_for_task(project['brief'], db=db))
        system = apply_preferences(system, payload, preferences,
                                   section=section, sections=project['sections'], edit=True)
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': dumps(payload)}]
        if sum(len(message['content']) for message in messages) > int(config.get('contextChars', 24000)):
            raise ValueError('选区、需求和证据超出模型输入预算，请缩小选区或提高模型字符预算')
        return messages, evidence

    def queue_edit(self, project_id, expected, section_id, data):
        operation = data.get('operation')
        if not isinstance(operation, str) or operation not in EDIT_OPERATIONS:
            raise ValueError('请选择润色、改写、缩写或扩写')
        instruction = _text(data.get('instruction', ''), '修改要求', 2000, empty=True)
        project = self.get(project_id)
        _version(project, expected)
        config = self._writing_config(project)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            section = self._section(project, section_id)
            target = self._edit_target(project, section, data)
            active = db.execute("SELECT r.* FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? AND j.status IN ('queued','running') ORDER BY r.created_at DESC LIMIT 1", (project_id,)).fetchone()
            if active:
                previous = json.loads(active['input_json'])
                if active['kind'] == 'edit' and active['section_id'] == section_id and active['base_version'] == expected and previous.get('editTarget') == target and previous.get('operation') == operation and previous.get('instruction') == instruction:
                    job_id, run_id = active['job_id'], active['id']
                else:
                    raise WritingConflict('当前项目已有写作任务，请等待完成或取消后继续')
            else:
                preferences = preference_snapshot_for_task(project['brief'], db=db)
                messages, evidence = self._edit_messages(project, section, target, operation, instruction, config, db,
                                                         preference_snapshot=preferences)
                job_id, run_id, timestamp = ident('job'), ident('writing_run'), now()
                frozen = {'projectId': project_id, 'section': copy.deepcopy(section), 'mode': project['mode'],
                          'title': project['title'], 'brief': project['brief'], 'effectiveMode': _effective(project, section),
                          'sources': project['sources'], 'operation': operation, 'instruction': instruction, 'editTarget': target,
                          'messages': messages, 'evidence': evidence, 'preferenceSnapshot': copy.deepcopy(preferences)}
                public = {key: config.get(key) for key in ('id', 'version', 'name', 'model')}
                db.execute('INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                           (job_id, ident('writing_batch'), '选区' + EDIT_OPERATIONS[operation] + ' · ' + section['title'],
                            'writing_edit', dumps({'runId': run_id}), 'queued', 'queued', timestamp, timestamp))
                db.execute('INSERT INTO writing_runs(id,job_id,project_id,section_id,kind,base_version,model_id,model_version,model_json,prompt_version,input_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (run_id, job_id, project_id, section_id, 'edit', expected, config['id'], config['version'], dumps(public), EDIT_PROMPT_VERSION, dumps(frozen), timestamp))
                prompt_snapshots = snapshot(db, ('writing_edit_' + operation,))
                prompt_snapshots.update(copy.deepcopy(preferences.get('stylePromptSnapshot', {})))
                prompt_snapshots.update(copy.deepcopy(preferences.get('structurePromptSnapshot', {})))
                freeze(db, job_id, prompt_snapshots)
        return {'job': self.store.job(job_id), 'runId': run_id, 'project': self.get(project_id)}

    def _edit_candidate(self, frozen, result):
        replacement = _raw_text(result.get('replacementText'), '选区替换文本', 30000)
        target = frozen['editTarget']
        exact = target['selectedExact']
        messages = frozen.get('messages') or []
        packet = json.loads(messages[1]['content']) if len(messages) > 1 else {}
        plain_extended = (frozen['effectiveMode'] == 'extended' and
                          packet.get('unmatchedCitationPolicy') == UNMATCHED_CITATION_POLICY)
        if plain_extended and SUPPLEMENT not in exact:
            replacement = _raw_text(replacement.replace(SUPPLEMENT, ''), '选区替换文本', 30000)
        if Counter(re.findall(CITATION_PATTERN, replacement)) != Counter(re.findall(CITATION_PATTERN, exact)):
            raise ValueError('修改候选改变了选区引用编号或数量，未保存；当前版本只允许保留已有引用')
        if replacement.count(SUPPLEMENT) < exact.count(SUPPLEMENT):
            raise ValueError('修改候选删除了待核实标记，未保存')
        supplement = result.get('supplement', False)
        if not isinstance(supplement, bool):
            raise ValueError('模型补充标识必须为布尔值')
        if frozen['effectiveMode'] == 'strict' and (supplement or replacement.count(SUPPLEMENT) > exact.count(SUPPLEMENT)):
            raise ValueError('严格模式修改不能增加无据模型补充')
        if frozen['effectiveMode'] == 'extended' and not plain_extended and (supplement or frozen['operation'] == 'expand') and SUPPLEMENT not in replacement:
            replacement = SUPPLEMENT + replacement
        original = frozen['section']['content']
        composed = original[:target['startCodepoint']] + replacement + original[target['endCodepoint']:]
        # A replacement can accidentally complete a marker across its boundary
        # ("[E" + "99" + "]"). Validate the actual composed document as well.
        if Counter(re.findall(CITATION_PATTERN, composed)) != Counter(re.findall(CITATION_PATTERN, original)):
            raise ValueError('选区边界拼接改变了章节引用标记，未保存候选')
        if composed.count(SUPPLEMENT) < original.count(SUPPLEMENT):
            raise ValueError('选区边界拼接删除了待核实标记，未保存候选')
        if frozen['effectiveMode'] == 'strict' and composed.count(SUPPLEMENT) > original.count(SUPPLEMENT):
            raise ValueError('严格模式选区拼接不能新增模型补充标记')
        if len(composed) > 60000:
            raise ValueError('修改后的章节超过 60000 字符上限')
        summary = _text(result.get('changeSummary', ''), '修改说明', 1000, empty=True)
        issues = result.get('issues', [])
        if not isinstance(issues, list) or len(issues) > 12 or any(not isinstance(issue, str) or len(issue) > 500 for issue in issues):
            raise ValueError('修改候选的问题说明格式无效')
        notices = (['引用标记保留不代表语义支持已核验，请对照原文确认修改没有强化或改变事实。']
                   if frozen['evidence'] or not plain_extended else [])
        if not frozen['evidence'] and not plain_extended:
            notices.append('选区没有已登记原文证据；此次仅调整表达，原稿事实仍需核实。')
        if frozen['effectiveMode'] == 'extended' and not plain_extended and frozen['operation'] == 'expand':
            notices.append('扩展模式扩写作为模型补充保留待核实标记。')
        return {'edit': {'replacementText': replacement, 'selectedExact': exact, 'start': target['start'], 'end': target['end'],
                         'unit': 'utf16', 'operation': frozen['operation'], 'changeSummary': summary,
                         'issues': list(dict.fromkeys([*issues, *notices])), 'semanticVerified': False,
                         **({'extendedContentPolicy': UNMATCHED_CITATION_POLICY} if plain_extended else {})}}

    def _candidate(self, run, frozen, response):
        result, generation_notices = parse_output(frozen, response, run['kind'])
        preserve_output = preserve_usable_output(frozen)
        if run['kind'] == 'edit':
            candidate = self._edit_candidate(frozen, result)
            candidate['edit']['issues'] = list(dict.fromkeys([*candidate['edit']['issues'], *generation_notices]))
            return candidate
        if run['kind'] == 'outline':
            if abstract_outline(frozen):
                raw_sections = result.get('sections')
                # Treat unsolicited citation fields as unused model output;
                # they never become an outline evidence registry or a gate.
                sections = self._sections(raw_sections, frozen['mode']) if raw_sections else []
                if any(s['mode'] not in ('inherit', frozen['mode']) for s in sections):
                    raise ValueError('模型纲要改变了用户指定的章节模式，未应用')
                if raw_sections is not None and not isinstance(raw_sections, list):
                    raise ValueError('纲要章节须为列表')
                if not sections and not generation_notices and not result.get('issues'):
                    raise ValueError('模型未返回可用纲要，请调整写作要求后重试')
                candidate = {'sections': sections, 'outlinePolicy': ABSTRACT_OUTLINE_POLICY}
                if generation_notices:
                    candidate['generationNotices'] = generation_notices
                return candidate
            planned = self._validate_planned_outline(frozen, result)
            if planned is not None:
                if generation_notices:
                    planned['generationNotices'] = list(dict.fromkeys([*planned.get('generationNotices', []), *generation_notices]))
                    for section in planned.get('sections', []):
                        section['generationNotices'] = list(dict.fromkeys([*section.get('generationNotices', []), *generation_notices]))
                return planned
            sections = self._sections(result.get('sections'), frozen['mode'])
            if any(s['mode'] not in ('inherit', frozen['mode']) for s in sections):
                raise ValueError('模型纲要改变了用户指定的章节模式，未应用')
            candidate = {'sections': sections}
            if generation_notices:
                candidate['generationNotices'] = generation_notices
                for section in sections:
                    section['generationNotices'] = generation_notices.copy()
            return candidate
        paragraphs = result.get('paragraphs')
        if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= 80:
            raise ValueError('模型未返回可用正文，原文可能不足；请补充资料或调整章节目标')
        known = {}
        for block in frozen['blocks']:
            previous = known.get(block['blockId'])
            if previous is not None and any(previous[key] != block[key] for key in ('sourceId', 'revisionId', 'text')):
                raise ValueError('冻结资料包含不明确的重复原文块标识，不能登记引用')
            known[block['blockId']] = block
        # Citation placement is part of the frozen model request. Old queued
        # jobs retain their paragraph-end protocol rather than acquiring new
        # required output fields when the worker is upgraded.
        messages = frozen.get('messages') or []
        packet = json.loads(messages[1]['content']) if len(messages) > 1 else {}
        inline_citations = packet.get('citationPlacement') in INLINE_CITATION_PROTOCOLS
        exact_sentences = packet.get('citationPlacement') in REVIEWED_CITATION_PROTOCOLS
        deferred_review = frozen.get('citationReviewPolicy', packet.get('citationReviewPolicy')) == DEFERRED_CITATION_REVIEW_POLICY
        evidence, content, seen = [], [], {}
        mode = frozen['effectiveMode']
        plain_extended = mode == 'extended' and packet.get('unmatchedCitationPolicy') == UNMATCHED_CITATION_POLICY
        retain_unverified = preserve_output or (mode == 'extended' and packet.get('unmatchedCitationPolicy') in (
            LEGACY_UNMATCHED_CITATION_POLICY, UNMATCHED_CITATION_POLICY))
        retain_individual_refs = (preserve_output and
                                  packet.get('citationRetentionPolicy') == CITATION_RETENTION_POLICY)
        # A broken program-owned snapshot is never a model citation mismatch.
        citation_scope_payload(frozen)
        unverified_paragraphs, citation_issues, reference_issues, strict_supplements = [], [], [], []
        for paragraph_index, paragraph in enumerate(paragraphs):
            if not isinstance(paragraph, dict):
                raise ValueError('模型段落格式无效')
            value = (_raw_text if preserve_output else _text)(paragraph.get('text'), '模型正文段落', 10000)
            if re.search(r'\n\s*\n|\u2029', value):
                raise ValueError('每个正文段落须独立提供证据，不能在同一段落对象中包含多个空行分段')
            if re.search(r'\[E\d+\]', value):
                raise ValueError('模型不可自行编写引用编号')
            if plain_extended:
                value = (_raw_text if preserve_output else _text)(value.replace(SUPPLEMENT, ''), '模型正文段落', 10000)
            evidence_count, previous_seen = len(evidence), seen.copy()
            try:
                refs = paragraph.get('evidence', [])
                if not isinstance(refs, list) or len(refs) > 12:
                    raise citation_error(frozen, paragraph_index, 0, MISSING_BLOCK_ID, '模型证据列表无效')
                if paragraph.get('kind') == 'model_supplement' and refs:
                    raise citation_error(frozen, paragraph_index, 0, MISSING_BLOCK_ID,
                                         '模型补充段落不能借原文引用伪装为受支持事实，请拆分有据段落与待核实补充')
                ids, placements = [], {}
                for evidence_index, ref in enumerate(refs):
                    block_value = ref.get('blockId', MISSING_BLOCK_ID) if isinstance(ref, dict) else MISSING_BLOCK_ID
                    try:
                        try:
                            if not isinstance(ref, dict):
                                raise ValueError('引用项格式错误，必须是包含 blockId 和 quote 的对象')
                            original_block_id = resolve_reference_block(frozen, block_value)
                            if original_block_id not in known:
                                raise ValueError('该原文块不在本章冻结的材料白名单内')
                        except ValueError as exc:
                            raise citation_error(frozen, paragraph_index, evidence_index, block_value, str(exc)) from None
                        block = known[original_block_id]
                        try:
                            quote = _text(ref.get('quote'), '原文引文', 4000)
                        except ValueError as exc:
                            raise citation_error(frozen, paragraph_index, evidence_index, block_value, str(exc),
                                                 message_prefix='模型原文引文格式无效') from None
                        start = block['text'].find(quote)
                        if start < 0:
                            raise citation_error(frozen, paragraph_index, evidence_index, block_value,
                                                 '摘录必须逐字来自当前编号对应的冻结原文块',
                                                 message_prefix='模型引文无法在冻结原文中逐字定位')
                        if block['text'].find(quote, start + 1) >= 0:
                            raise citation_error(frozen, paragraph_index, evidence_index, block_value,
                                                 '请扩大摘录范围后重新生成',
                                                 message_prefix='模型引文在同一原文块中重复，无法唯一定位')
                        if inline_citations:
                            try:
                                claim = _raw_text(ref.get('claim'), '正文引用句 claim', 10000)
                            except ValueError as exc:
                                raise citation_error(frozen, paragraph_index, evidence_index, block_value, str(exc),
                                                     message_prefix='模型正文引用位置无效') from None
                            claim_start = value.find(claim)
                            if claim_start < 0:
                                raise citation_error(frozen, paragraph_index, evidence_index, block_value,
                                                     'claim 必须逐字复制当前段落 text 中的受支持句子，不能使用原文 quote 或其他段落文字',
                                                     message_prefix='模型正文引用位置无法逐字定位')
                            if value.find(claim, claim_start + 1) >= 0:
                                raise citation_error(frozen, paragraph_index, evidence_index, block_value,
                                                     'claim 在当前段落重复，请扩大为可唯一定位的受支持句子',
                                                     message_prefix='模型正文引用位置不唯一')
                            if exact_sentences and not exact_sentence(value, claim_start, claim_start + len(claim), claim):
                                raise citation_error(frozen, paragraph_index, evidence_index, block_value,
                                                     'claim 必须是一个完整句子，不能截取短语或包含多个句子；每个受支持句子须分别登记',
                                                     message_prefix='模型正文引用句范围无效')
                            placement = claim_start + len(claim)
                    except CitationScopeError as exc:
                        if not retain_individual_refs:
                            raise
                        # Source and placement are validated before any registry
                        # mutation. One invalid item cannot erase other anchors.
                        reason = {
                            '模型原文引文格式无效': '原文摘录格式不完整',
                            '模型引文无法在冻结原文中逐字定位': '摘录未逐字出现在对应原文中',
                            '模型引文在同一原文块中重复，无法唯一定位': '摘录在对应原文中重复，无法唯一定位',
                            '模型正文引用位置无效': '正文引用位置格式不完整',
                            '模型正文引用位置无法逐字定位': '引用对应句未逐字出现在本段正文中',
                            '模型正文引用位置不唯一': '引用对应句在本段重复，无法唯一定位',
                            '模型正文引用句范围无效': '引用对应位置不是一个完整句子',
                        }.get(exc.message_prefix, '原文位置不在本章允许范围内，或引用信息不完整')
                        reference_issues.append({'paragraphIndex': paragraph_index + 1,
                                                 'referenceIndex': evidence_index + 1, 'reason': reason})
                        generation_notices.append('第%d段第%d项引用未采用：%s；其余有效引用已保留。' % (
                            paragraph_index + 1, evidence_index + 1, reason))
                        continue
                    key = (block['blockId'], start, start + len(quote))
                    if key not in seen:
                        eid = 'E' + str(len(evidence) + 1)
                        seen[key] = eid
                        evidence.append({'id': eid, 'blockId': block['blockId'], 'sourceId': block['sourceId'], 'revisionId': block['revisionId'],
                                         'title': block['title'], 'url': block['url'], 'quote': quote, 'start': start, 'end': start + len(quote), 'locator': block['locator']})
                    ids.append(seen[key])
                    if inline_citations:
                        placements.setdefault(placement, []).append(seen[key])
            except CitationScopeError:
                if not retain_unverified:
                    raise
                # Roll back the whole paragraph so a partially registered
                # citation cannot make the unverified prose appear supported.
                del evidence[evidence_count:]
                seen, ids, placements = previous_seen, [], {}
                unverified_paragraphs.append(paragraph_index)
                if preserve_output:
                    generation_notices.append('第%d段的引用未能与所选原文对应，正文已保留，未使用这条引用。' % (paragraph_index + 1))
                elif not plain_extended:
                    citation_issues.append({'status': 'unsupported', 'paragraphIndex': paragraph_index + 1,
                        'message': '本段引用未能与所选原文对应，正文已保留为待核实的模型补充。'})
            if mode == 'strict' and not deferred_review and (not ids or paragraph.get('kind') == 'model_supplement'):
                if not preserve_output:
                    raise ValueError('严格模式段落缺少原文依据，未将无据内容写入正文')
                generation_notices.append('第%d段尚缺少有效原文依据，正文已保留供比较，引用支持仍需核对。' % (paragraph_index + 1))
            if deferred_review and mode == 'strict' and paragraph.get('kind') == 'model_supplement':
                strict_supplements.append(paragraph_index + 1)
                generation_notices.append('第%d段被模型标为资料外补充，尚不符合严格模式的材料范围，已保留为待处理候选。' % (paragraph_index + 1))
            if not ids and not plain_extended and not preserve_output and not value.startswith(SUPPLEMENT):
                value = SUPPLEMENT + value
            if inline_citations:
                # Insert right-to-left so every validated body anchor keeps its
                # original offset. Reused source excerpts may cite several
                # sentences, but each boundary receives a marker only once.
                for position, positioned_ids in sorted(placements.items(), reverse=True):
                    markers = ''.join('[' + eid + ']' for eid in dict.fromkeys(positioned_ids))
                    value = value[:position] + markers + value[position:]
                content.append(value)
            else:
                content.append(value + (' ' + ''.join('[' + eid + ']' for eid in dict.fromkeys(ids)) if ids else ''))
        text = '\n\n'.join(content)
        if len(text) > 60000:
            raise ValueError('生成章节超过正文长度上限')
        candidate = {'section': {'content': text, 'evidence': evidence}}
        if packet.get('introductionPolicy') == INTRODUCTION_POLICY:
            count = result.get('introductionParagraphCount')
            if type(count) is int and (0 < count < len(content) or
                    count == 0 and packet.get('authorStructurePolicy') == AUTHOR_STRUCTURE_POLICY):
                candidate['section']['introductionParagraphCount'] = count
            else:
                generation_notices.append('本次背景介绍与章节正文的分段位置未明确，请在首章检查开头内容。')
        if preserve_output:
            candidate['section']['generationNotices'] = list(dict.fromkeys(generation_notices))[:24]
        if plain_extended:
            candidate['section']['extendedContentPolicy'] = UNMATCHED_CITATION_POLICY
        if unverified_paragraphs:
            candidate['unverifiedParagraphs'] = unverified_paragraphs
            if citation_issues:
                candidate['section']['citationIssues'] = citation_issues
        if reference_issues:
            candidate['citationReferenceIssues'] = reference_issues
        if deferred_review:
            candidate['section']['citationReviewPolicy'] = DEFERRED_CITATION_REVIEW_POLICY
            known_scope_problem = mode == 'strict' and (strict_supplements or reference_issues or unverified_paragraphs)
            candidate['section']['citationBindingState'] = 'needs_review' if known_scope_problem else 'unreviewed'
            if strict_supplements:
                candidate['section'].setdefault('citationIssues', []).extend(
                    {'status': 'unreviewed', 'paragraphIndex': index,
                     'message': '模型将本段标为资料外补充，严格模式的材料范围尚未满足。'}
                    for index in strict_supplements)
            if mode == 'strict' and (reference_issues or unverified_paragraphs):
                candidate['section'].setdefault('citationIssues', []).append(
                    {'status': 'unreviewed', 'message': '部分引用信息未通过原文定位检查，已保留有效引用及正文候选；支持关系尚未核对。'})
        elif exact_sentences:
            candidate['citationMatchingPending'] = True
        return candidate

    def _match_candidate_citations(self, run, job, attempt, config, frozen, candidate):
        """Assess support independently of generation and bind exact sentences.

        Old frozen jobs retain their old contract. New jobs never claim that
        copying a model-supplied ``claim`` alone establishes semantic support.
        """
        from .writing_citation_matching import (build_matching_packet, matching_messages,
                                                partition_matching_packets, subset_matching_packet,
                                                validate_matching_result, with_frozen_temporal_review)
        from .writing_sentence_citations import unreviewed_matches
        messages = frozen.get('messages') or []
        packet = json.loads(messages[1]['content']) if len(messages) > 1 else {}
        if packet.get('citationPlacement') not in REVIEWED_CITATION_PROTOCOLS or 'section' not in candidate:
            return candidate
        retain_generated = packet.get('citationRetentionPolicy') == CITATION_RETENTION_POLICY
        section = candidate['section']
        # Only new v3 generation requests acquire mode-aware support rules.
        # Already queued v2 jobs/checkpoints retain their original v1 schema.
        mode_aware = packet.get('citationPlacement') == CITATION_PLACEMENT
        review_mode = frozen.get('citationMatchMode') if mode_aware else None
        if mode_aware and review_mode != frozen['effectiveMode']:
            raise ValueError('引用复核缺少冻结的章节模式，未应用生成结果')
        support_policy = frozen.get('citationMatchPolicy') if mode_aware else None
        if mode_aware and support_policy is None:
            raise ValueError('引用复核缺少冻结的支持规则，未应用生成结果')
        matching_packet = build_matching_packet(section['content'], section['evidence'], blocks=frozen['blocks'], section_id=frozen['section']['id'],
                                                mode=review_mode, policy=support_policy)
        matching_packet = with_frozen_temporal_review(matching_packet, frozen, generation_packet=packet)
        # The matcher is part of the original job's prompt snapshot. Changes
        # made to the live prompt library cannot alter a resumed generation.
        instruction = frozen.get('citationMatchInstructions')
        decisions, issues = [], copy.deepcopy(section.get('citationIssues', []))

        def unconfirmed(batch):
            candidate['citationReviewIncomplete'] = True
            notice = ('部分引用的自动核对未能完成，生成内容已保留，问题已记入报告。'
                      if automatic_citations_report_only(frozen) else
                      '部分引用的自动核对未能完成，生成内容已保留，可查看原文后调整。')
            section['generationNotices'] = list(dict.fromkeys([*section.get('generationNotices', []), notice]))
            checked = validate_matching_result(batch, {'assignments': [{
                'sentenceId': item['sentenceId'], 'evidenceIds': [],
                'support': 'uncertain' if retain_generated else 'unsupported',
                'reason': '自动引用核对未完成，尚未判断原文支持关系。' if retain_generated else '自动引用核对未能完成，未认定原文支持。',
                **({'supportBasis': 'none'} if review_mode is not None else {})}
                for item in batch['sentences']]})
            if retain_generated:
                checked['reviewIncomplete'] = True
            return checked

        # A failed model reference must not gain support from another
        # paragraph's registry during automatic sentence matching.
        fallback_indexes = set(candidate.get('unverifiedParagraphs', []))
        fallback_sentences, offset = [], 0
        for index, paragraph in enumerate(matching_packet['content'].split('\n\n')):
            if index in fallback_indexes:
                fallback_sentences.extend(item['sentenceId'] for item in matching_packet['sentences']
                                          if offset <= item['start'] < offset + len(paragraph))
            offset += len(paragraph) + 2
        fallback_ids = set(fallback_sentences)
        regular_ids = [item['sentenceId'] for item in matching_packet['sentences']
                       if item['sentenceId'] not in fallback_ids]
        packets = []
        if regular_ids:
            packets.append(subset_matching_packet(matching_packet, regular_ids) if fallback_ids else matching_packet)
        if fallback_ids:
            unverified = build_matching_packet(section['content'], [], mode=review_mode, policy=support_policy,
                                               section_id=frozen['section']['id'])
            unverified = with_frozen_temporal_review(unverified, frozen, generation_packet=packet)
            packets.append(subset_matching_packet(unverified, fallback_sentences))
        budget_unconfirmed = False
        try:
            partitions = [part for packet in packets for part in partition_matching_packets(
                packet, int(config.get('contextChars', 24000)), instruction=instruction)]
        except ValueError:
            if not automatic_citations_report_only(frozen):
                raise
            # The program-owned registry has already been validated above.
            # A review budget failure cannot discard the completed prose.
            partitions, budget_unconfirmed = [matching_packet], True
        matching_config = copy.deepcopy(config)
        output_limit = min(int(config.get('maxOutputTokens', 2048)), 65536 if retain_generated else 12000)
        matching_config['maxOutputTokens'] = output_limit
        sentence_limit = max(1, min(8 if retain_generated else 60,
                                    (output_limit - 256) // (500 if retain_generated else 160)))
        batches = []
        for partition in partitions:
            ids = [sentence['sentenceId'] for sentence in partition['sentences']]
            for offset in range(0, len(ids), sentence_limit):
                selected = ids[offset:offset + sentence_limit]
                batches.append(partition if selected == ids else subset_matching_packet(partition, selected))
        checkpoints = candidate.setdefault('citationMatchingCheckpoints', {})
        for batch in batches:
            checkpoint_key = citation_content_hash(dumps({'packet': batch, 'instruction': instruction}))
            if checkpoint_key in checkpoints:
                fields = ('sentenceId', 'evidenceIds', 'support', 'reason', 'supportBasis') if review_mode is not None else ('sentenceId', 'evidenceIds', 'support', 'reason')
                saved_assignments = [{key: assignment[key] for key in fields}
                                     for assignment in checkpoints[checkpoint_key]['assignments']]
                checked = validate_matching_result(batch, {'assignments': saved_assignments})
                if retain_generated and checkpoints[checkpoint_key].get('reviewIncomplete'):
                    checked['reviewIncomplete'] = True
            elif budget_unconfirmed:
                checked = unconfirmed(batch)
            elif not batch['evidence']:
                result = {'assignments': [{'sentenceId': item['sentenceId'], 'evidenceIds': [],
                                         'support': 'unsupported', 'reason': '本句尚未登记可支持论述的原文摘录。',
                                         **({'supportBasis': 'none'} if review_mode is not None else {})}
                                        for item in batch['sentences']]}
                checked = validate_matching_result(batch, result)
            else:
                request = matching_messages(batch, instruction=instruction)
                with self.store.connect() as db:
                    if not self._active(db, job, attempt):
                        raise WritingConflict('任务已停止，未继续匹配引用')
                with self.operations.capture(run, job, attempt, matching_config, request, [frozen['section']['id']]) as capture:
                    response = None
                    try:
                        response = capture.complete(self.models)
                    except (ModelError, RuntimeError, TimeoutError):
                        if not automatic_citations_report_only(frozen):
                            raise
                        capture.validation = 'unavailable'
                        checked = unconfirmed(batch)
                    try:
                        if response is not None:
                            checked = validate_matching_result(batch, _json(response['text']))
                    except ValueError:
                        if not preserve_usable_output(frozen):
                            raise
                        # The recorded model return remains invalid. Keep the
                        # prose and produce only program-owned unsupported
                        # decisions, never invented source support.
                        capture.validation = 'invalid'
                        checked = unconfirmed(batch)
                    else:
                        if response is not None:
                            capture.validated()
                    checked = _clean(checked, config.get('apiKey', ''))
            if retain_generated and checked.get('reviewIncomplete'):
                checked['matches'] = unreviewed_matches(section['content'], section['evidence'], checked['matches'])
                checked['issues'] = [{**issue, 'status': 'unreviewed',
                                      'message': '自动引用核对未完成，尚未判断原文支持关系。'}
                                     for issue in checked.get('issues', [])]
            if checkpoint_key not in checkpoints:
                checkpoints[checkpoint_key] = checked
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._active(db, job, attempt):
                        raise WritingConflict('任务已停止，未应用引用匹配结果')
                    if run['kind'] == 'document':
                        db.execute("UPDATE writing_document_steps SET candidate_json=?,status='matching' WHERE run_id=? AND section_id=?",
                                   (dumps(candidate), run['id'], frozen['section']['id']))
                    else:
                        db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps(candidate), run['id']))
            decisions.extend(checked['matches'])
            issues.extend(checked.get('issues', []))
        updated, binding = relocate_citations(section['content'], section['evidence'], decisions,
                                             mode=review_mode, support_policy=support_policy)
        section['content'] = updated
        section['citationBindings'] = binding
        plain_extended = (frozen['effectiveMode'] == 'extended' and
                          section.get('extendedContentPolicy') == UNMATCHED_CITATION_POLICY)
        section['citationBindingState'] = 'matched' if all(item['status'] in RESOLVED_STATUSES or
            plain_extended and item.get('evidenceId') is None for item in binding['items']) else 'needs_review'
        if candidate.get('citationReviewIncomplete'):
            section['citationBindingState'] = 'needs_review'
        if plain_extended:
            cited_sentences = {item['sentenceId'] for checkpoint in checkpoints.values()
                               for item in checkpoint['assignments'] if item.get('evidenceIds')}
            issues = [issue for issue in issues if issue.get('status') == 'unreviewed'
                      or issue.get('sentenceId') in cited_sentences]
        section['citationIssues'] = issues
        candidate.pop('citationMatchingPending', None)
        candidate.pop('citationMatchingCheckpoints', None)
        return candidate

    def _active(self, db, job, attempt):
        row = db.execute('SELECT j.status,j.attempts FROM jobs j JOIN writing_runs r ON r.job_id=j.id JOIN writing_projects p ON p.id=r.project_id WHERE j.id=? AND p.deleted_at IS NULL', (job['id'],)).fetchone()
        active = bool(row and row['status'] == 'running' and row['attempts'] == attempt)
        parent_id = json.loads(job.get('input_json') or '{}').get('automaticParentRunId')
        if active and parent_id:
            parent = db.execute('SELECT j.status,r.input_json,r.result_json,r.project_id FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.id=?', (parent_id,)).fetchone()
            if not parent or parent['status'] != 'running':
                if not parent or parent['status'] not in ('queued', 'running'):
                    db.execute("UPDATE jobs SET status='cancelled',stage='cancelled',updated_at=? WHERE id=? AND status='running' AND attempts=?", (now(), job['id'], attempt))
                return False
            current = self._load(db, parent['project_id'])
            frozen_parent = json.loads(parent['input_json'])
            child = db.execute('SELECT base_version,kind,input_json FROM writing_runs WHERE job_id=?', (job['id'],)).fetchone()
            expected_version = child['base_version'] if child else None
            if (child and child['kind'] == 'document'
                    and json.loads(child['input_json']).get('automaticDocumentPolicy') == 'automatic-single-document-v1'
                    and frozen_parent.get('automaticBodyPolicy') == 'automatic-body-length-v1'):
                expected_version = json.loads(parent['result_json']).get('automatic', {}).get('expectedVersion')
            if not child or expected_version != current['version'] or any(
                current.get(key) != frozen_parent.get(key)
                for key in ('title', 'brief', 'mode', 'sources', 'materialSelection', 'modelId')
            ):
                return False
        if active and self._automatic_active_run_id():
            try:
                self._automatic_child_guard(db)
            except WritingConflict:
                return False
        return active

    def _generation_result_active(self, db, job, attempt):
        """Keep a valid candidate while retaining cancellation/attempt fences."""
        row = db.execute('''SELECT j.status,j.attempts FROM jobs j JOIN writing_runs r ON r.job_id=j.id
            JOIN writing_projects p ON p.id=r.project_id WHERE j.id=? AND p.deleted_at IS NULL''', (job['id'],)).fetchone()
        if not row or row['status'] != 'running' or row['attempts'] != attempt:
            return False
        parent_id = json.loads(job.get('input_json') or '{}').get('automaticParentRunId')
        if parent_id:
            parent = db.execute('SELECT j.status FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.id=?', (parent_id,)).fetchone()
            if not parent or parent['status'] != 'running':
                return False
        try:
            self._automatic_child_guard(db)
        except WritingConflict:
            return False
        return True

    def process(self, job):
        if job['kind'] == 'writing_automatic':
            return self.process_automatic(job)
        if job['kind'] == 'writing_images':
            return self.process_image_search(job)
        if job['kind'] == 'writing_image_batch':
            return self.process_image_batch(job)
        if job['kind'] == 'writing_document':
            return self.process_document(job)
        if job['kind'] == 'writing_citation_match':
            return self.process_citation_match(job)
        attempt = job['attempts'] + 1
        try:
            run_id = json.loads(job['input_json'])['runId']
            with self.store.connect() as db:
                run = db.execute('SELECT * FROM writing_runs WHERE id=? AND job_id=?', (run_id, job['id'])).fetchone()
                if not run or not self._active(db, job, attempt):
                    return
                run = dict(run)
            frozen = json.loads(run['input_json'])
            if run['result_json']:
                candidate = json.loads(run['result_json'])
            else:
                config = self.models.resolve(run['model_id'], run['model_version'])
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._active(db, job, attempt):
                        return
                    db.execute('UPDATE jobs SET stage=?,updated_at=? WHERE id=? AND attempts=?', ('generating', now(), job['id'], attempt))
                started = time.monotonic()

                def generation_guard():
                    with self.store.connect() as db:
                        if not self._active(db, job, attempt):
                            raise AutomaticRetryCancelled('写作任务已停止，未继续请求')
                        current = self._load(db, run['project_id'])
                        if current['version'] != run['base_version']:
                            raise WritingConflict('项目版本已变化，未继续重试')
                        if run['kind'] == 'section':
                            target = self._section(current, run['section_id'])
                            if target.get('locked') or target.get('manual') or target.get('content'):
                                raise WritingConflict('章节已有正文、人工修改或锁定，未继续重试')

                def generation_result_guard():
                    with self.store.connect() as db:
                        if not self._generation_result_active(db, job, attempt):
                            raise AutomaticRetryCancelled('写作任务已停止，未采纳迟到结果')

                def generate():
                    with self.operations.capture(run, job, attempt, config, frozen['messages']) as capture:
                        try:
                            response = capture.complete(self.models)
                        except ModelError:
                            raise
                        except Exception:
                            if retry_enabled(frozen):
                                raise
                            raise RuntimeError('写作模型调用失败') from None
                        generated = self._candidate(run, frozen, response)
                        capture.validated()
                        usage = {key: val for key, val in (response.get('usage') or {}).items()
                                 if key in ('prompt_tokens', 'completion_tokens', 'total_tokens') and isinstance(val, (int, float)) and not isinstance(val, bool)}
                        return {'candidate': generated, 'usage': usage}

                generated = retry_call(self.store, frozen, 'generation:' + run_id, generate,
                    guard=generation_guard, secret=config.get('apiKey', ''),
                    result_guard=generation_result_guard,
                    details={'runId': run_id, 'stage': 'chapters' if run['kind'] == 'section' else 'outline',
                             'sectionId': run.get('section_id')})
                candidate = generated['candidate']
                response = {'usage': generated['usage']}
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    active_result = (self._generation_result_active(db, job, attempt) if retry_enabled(frozen)
                                     else self._active(db, job, attempt))
                    if not active_result:
                        return
                    db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps(candidate), run_id))
                    # Only numeric usage fields are persisted; no arbitrary provider metadata.
                    usage = {key: val for key, val in (response.get('usage') or {}).items() if key in ('prompt_tokens', 'completion_tokens', 'total_tokens') and isinstance(val, (int, float)) and not isinstance(val, bool)}
                    db.execute('INSERT OR REPLACE INTO writing_calls VALUES(?,?,?,?,?,?)', (run_id, attempt, round((time.monotonic() - started) * 1000), dumps(usage), 'rules_valid', now()))
            # A successful draft is durably saved before the independent
            # semantic call. Retrying that call never regenerates its prose.
            if run['kind'] == 'section' and candidate.get('citationMatchingPending'):
                config = self.models.resolve(run['model_id'], run['model_version'])
                candidate = self._match_candidate_citations(run, job, attempt, config, frozen, candidate)
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._active(db, job, attempt):
                        return
                    db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps(candidate), run_id))
            if run['kind'] == 'outline' and candidate.get('outlinePlanningPending'):
                config = self.models.resolve(run['model_id'], run['model_version'])
                candidate = self._review_planned_outline(run, job, attempt, config, frozen, candidate)
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._active(db, job, attempt):
                        return
                    db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps(candidate), run_id))
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not self._active(db, job, attempt):
                    if retry_enabled(frozen) and self._generation_result_active(db, job, attempt):
                        db.execute("UPDATE jobs SET status='succeeded',stage='candidate',error=NULL,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                                   (now(), job['id'], attempt))
                    return
                project = self._load(db, run['project_id'])
                safe = project['version'] == run['base_version']
                if run['kind'] == 'edit':
                    # A selection edit is always a reviewable proposal, even
                    # when the original version still matches exactly.
                    safe = False
                elif run['kind'] == 'section':
                    section = self._section(project, run['section_id'])
                    safe = safe and project['outlineConfirmed'] and not section['locked'] and not section['content'] and not section['manual']
                    safe = safe and (automatic_citations_report_only(frozen) or frozen['effectiveMode'] != 'strict'
                                     or candidate['section'].get('citationBindingState') != 'needs_review')
                else:
                    safe = safe and not any(s['content'] or s['locked'] for s in project['sections'])
                    if run['kind'] == 'outline' and abstract_outline(frozen):
                        safe = safe and bool(candidate.get('sections'))
                    if run['kind'] == 'outline' and candidate.get('outlinePlanningProtocol'):
                        safe = safe and candidate.get('outlinePlanningState') == 'supported' and bool(candidate.get('sections'))
                if safe:
                    self._apply_result(project, run, candidate)
                    self._save(db, project, 'generated_' + run['kind'])
                    db.execute('UPDATE writing_runs SET applied=1 WHERE id=?', (run_id,))
                db.execute("UPDATE jobs SET status='succeeded',stage=?,error=NULL,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                           ('ready' if safe else 'candidate', now(), job['id'], attempt))
        except Exception as exc:
            # Only controlled local validation errors and sanitized ModelError are safe.
            message = str(exc) if type(exc) is ValueError or isinstance(exc, (CitationScopeError, ModelError)) else '写作模型任务执行异常，请检查模型配置后重试'
            if getattr(exc, 'automaticRetryManaged', False) and getattr(exc, 'automaticRetryCategory', None):
                reason = {'format_error': '返回格式错误', 'interrupted': '生成结果异常中断', 'network': '模型连接中断或请求超时'}[exc.automaticRetryCategory]
                message = f'{reason}，已重试{exc.automaticRetryCount}次，仍未完成本次生成'
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',stage='failed',error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                           (message[:1500], now(), job['id'], attempt))

    def _apply_result(self, project, run, candidate):
        if run['kind'] == 'outline':
            project['sections'] = copy.deepcopy(candidate['sections'])
            project['outlineConfirmed'] = False
        elif run['kind'] == 'edit':
            frozen = json.loads(run['input_json'])
            target = frozen['editTarget']
            section = self._section(project, run['section_id'])
            content = (section['content'][:target['startCodepoint']] + candidate['edit']['replacementText']
                       + section['content'][target['endCodepoint']:])
            preserve_introduction(section, content)
            section['content'] = content
            if candidate['edit'].get('extendedContentPolicy') == UNMATCHED_CITATION_POLICY:
                section['extendedContentPolicy'] = UNMATCHED_CITATION_POLICY
            # Explicit human acceptance protects the resulting draft, including
            # all pre-existing manual content outside the selected range.
            section['manual'] = True
            section['version'] += 1
        else:
            section = self._section(project, run['section_id'])
            section.pop('citationSourceScope', None)
            section.pop('citationBindings', None)
            section.pop('citationBindingState', None)
            section.pop('citationIssues', None)
            section.pop('extendedContentPolicy', None)
            section.pop('generationNotices', None)
            section.pop('introductionParagraphCount', None)
            section.update(copy.deepcopy(candidate['section']))
            section['manual'] = False
            section['version'] += 1

    def apply(self, project_id, run_id, expected, replace_manual=False):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            row = db.execute('SELECT r.*,j.status FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.id=? AND r.project_id=?', (run_id, project_id)).fetchone()
            if not row:
                raise KeyError(run_id)
            run = dict(row)
            if run['kind'] == 'document':
                raise WritingConflict('全文候选须逐章对比采纳，不能整篇覆盖稿件')
            if run['kind'] == 'citation_match':
                raise WritingConflict('引用重新匹配须基于当前版本完成任务，不能作为正文生成候选采纳')
            if run['applied']:
                raise WritingConflict('该候选已经应用，不能重复覆盖当前正文')
            if run['status'] != 'succeeded' or not run['result_json']:
                raise ValueError('只有已完成的有效候选可以应用')
            frozen = json.loads(run['input_json'])
            if project['sources'] != frozen['sources']:
                raise WritingConflict('资料范围已改变，请基于当前资料重新生成')
            if project['title'] != frozen['title'] or project['brief'] != frozen['brief']:
                raise WritingConflict('写作需求已改变，请基于当前需求重新生成')
            candidate = json.loads(run['result_json'])
            if candidate.get('citationMatchingPending'):
                raise WritingConflict('逐句引用匹配尚未完成，请重试任务后再采纳候选')
            if run['kind'] == 'outline' and abstract_outline(frozen) and not candidate.get('sections'):
                raise WritingConflict('本次没有可用纲要，已保留现有结构，请调整写作要求后重试')
            if run['kind'] == 'outline' and candidate.get('outlinePlanningProtocol'):
                if candidate.get('outlinePlanningPending') or candidate.get('outlinePlanningState') != 'supported' or not candidate.get('sections'):
                    raise WritingConflict('该纲要仍有现有资料无法支持的规划，请先补充资料或调整范围后重新生成')
            if run['kind'] == 'edit':
                section = self._section(project, run['section_id'])
                if section['locked']:
                    raise WritingConflict('章节已锁定，不能应用选区修改')
                if project['version'] != run['base_version']:
                    raise WritingConflict('选区候选基于旧项目版本，请保留候选并从最新正文重新发起修改')
                target = frozen['editTarget']
                if section['version'] != target['sectionVersion'] or _content_hash(section['content']) != target['contentHash']:
                    raise WritingConflict('章节已修改，原选区候选不能覆盖当前正文')
                if section['content'][target['startCodepoint']:target['endCodepoint']] != target['selectedExact']:
                    raise WritingConflict('选区原文已变化，未应用候选')
                if _effective(project, section) != frozen['effectiveMode']:
                    raise WritingConflict('章节实际模式已改变，请重新生成选区候选')
            elif run['kind'] == 'section':
                section = self._section(project, run['section_id'])
                if section['locked']:
                    raise WritingConflict('锁定章节不能应用候选，请先解锁')
                if not project['outlineConfirmed']:
                    raise WritingConflict('请先确认当前纲要')
                if any(section[key] != frozen['section'][key] for key in ('mode', 'title', 'goal', 'targetWords')):
                    raise WritingConflict('章节要求已改变，请重新生成候选')
                if _effective(project, section) != frozen['effectiveMode']:
                    raise WritingConflict('章节实际模式已改变，请重新生成候选')
                if section['content'] and not replace_manual:
                    raise WritingConflict('该章已有正文；确认替换后才能应用候选，旧稿保留在版本历史')
            elif any(s['content'] or s['locked'] for s in project['sections']):
                raise WritingConflict('已有正文或锁定章节，不能应用纲要候选')
            elif project['mode'] != frozen['mode']:
                raise WritingConflict('项目模式已改变，请重新生成纲要')
            self._apply_result(project, run, candidate)
            self._save(db, project, 'selection_' + frozen['operation'] if run['kind'] == 'edit' else 'candidate_applied')
            db.execute('UPDATE writing_runs SET applied=1 WHERE id=?', (run_id,))
        return self.get(project_id)

    def _review(self, db, project):
        return build_review(db, project)

    def review(self, project_id, expected):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            report = self._review(db, project)
            project['review'] = report
            # Derived review does not change the writing version or invalidate a job.
            db.execute('UPDATE writing_projects SET content_json=? WHERE id=? AND version=?', (dumps(project), project_id, expected))
        return {'project': self.get(project_id), 'review': report}

    def history(self, project_id):
        with self.store.connect() as db:
            self._load(db, project_id)
            rows = db.execute('SELECT version,reason,created_at FROM writing_history WHERE project_id=? ORDER BY version DESC LIMIT 100', (project_id,)).fetchall()
        return {'versions': [dict(row) for row in rows]}

    def historical(self, project_id, version):
        with self.store.connect() as db:
            self._load(db, project_id)
            row = db.execute('SELECT content_json FROM writing_history WHERE project_id=? AND version=?', (project_id, version)).fetchone()
            if not row:
                raise KeyError(project_id)
            project = json.loads(row['content_json'])
            from .writing_library_availability import library_availability
            project['libraryAvailability'] = library_availability(db, project)
        return project

    def export(self, project_id):
        """Legacy draft HTML download; it always exposes fresh review issues."""
        with self.store.connect() as db:
            db.execute('BEGIN')
            project = self._load(db, project_id)
            report = self._review(db, project)
            freeze_image_resources(self.store, db, project)
        return writing_export.render_html(project, report), project['title']

    def export_snapshot(self, project_id, expected, format='docx', allow_issues=False):
        """Review and freeze exactly one saved version, then render outside DB.

        A later edit cannot alter these bytes. The response includes the frozen
        version so the client can discard a download if its local draft moved.
        No generation result, candidate, lock, text, or manual flag is changed.
        """
        if format not in ('docx', 'html', 'pdf') or not isinstance(allow_issues, bool):
            raise ValueError('导出格式须为 docx、pdf 或 html，草稿确认须为布尔值')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            active = db.execute("SELECT 1 FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? AND j.status IN ('queued','running') LIMIT 1", (project_id,)).fetchone()
            if active:
                raise WritingExportConflict('generation_active', '写作任务仍在生成，请等待完成或取消后重新校验并导出')
            report = self._review(db, project)
            if review_requires_draft(report) and not allow_issues:
                raise WritingExportConflict('review_issues', '全文校验仍有问题，请处理后导出，或明确选择保留问题导出草稿', report)
            project['review'] = report
            db.execute('UPDATE writing_projects SET content_json=? WHERE id=? AND version=?', (dumps(project), project_id, expected))
            try:
                freeze_image_resources(self.store, db, project)
            except ValueError as exc:
                raise WritingExportConflict('illustrations_invalid', str(exc), report) from None
        render = {'docx': writing_export.render_docx, 'html': writing_export.render_html, 'pdf': writing_pdf.render_pdf}[format]
        content = render(project, report)
        return {'content': content, 'title': project['title'], 'version': project['version'], 'review': report}
