"""Durable writing orchestration with delegated decisions and a final report.

Child tasks retain source, version and picture protections; new delegated runs
save usable prose and report citation problems without asserting source support.
No child is retried automatically and no failure creates a human-input state.
"""
from __future__ import annotations

import copy
import json
import re

from .store import dumps, ident, now
from .prompts import snapshot as prompt_snapshot, freeze, writing_prompt_id, compiled_snapshot_text
from .writing_composition import POLICY as COMPOSITION_POLICY, snapshot as composition_snapshot_for_task
from .writing_preferences import snapshot as preference_snapshot_for_task
from .writing_document import COMPOSITION_INSTRUCTION_FORMAT, COMPOSITION_CATALOG_REQUIREMENT
from . import prompt_defaults
from .writing_citation_matching import (matching_policy, DEFERRED_CITATION_REVIEW_POLICY,
                                       SELECTIVE_REVIEW_POLICY, SELECTIVE_REVIEW_INSTRUCTION)
from .writing_outline_planning import OUTLINE_PLANNING_PROTOCOL, ABSTRACT_OUTLINE_POLICY, abstract_outline
from .writing_image_tasks import QUALITY_POLICY
from .writing_output import AUTOMATIC_CITATION_POLICY, CITATION_RETENTION_POLICY, SECTION_ENVELOPE_POLICY
from .writing_automatic_retry import retry_policy, enabled as retry_enabled, retry_events
from .writing_automatic_materials import (AutomaticMaterialScreeningMixin,
    AUTOMATIC_MATERIAL_POLICY, MATERIAL_QUERY_INSTRUCTIONS, AUTOMATIC_SOURCE_OPTIONS_POLICY)


AUTOMATIC_PROTOCOL = 'writing-automatic-1'
AUTOMATIC_MODEL_POLICY = 'automatic-model-v1'
AUTOMATIC_OPTIONS_POLICY = 'automatic-writing-options-v1'
AUTOMATIC_BODY_POLICY = 'automatic-body-length-v1'
WORKFLOW_CONFIRMATION_POLICY = 'writing-step-confirmations-v1'
_CONTEXT_KEYS = ('title', 'brief', 'mode', 'sources', 'materialSelection', 'modelId')


class _AutomaticStopped(Exception):
    pass


class WritingAutomaticMixin(AutomaticMaterialScreeningMixin):
    @staticmethod
    def _automatic_requested_options(project):
        brief = project.get('brief') or {}
        if 'automaticOptions' not in brief:
            return None
        values = brief['automaticOptions']
        if not isinstance(values, dict):
            raise ValueError('智能化写作选项格式不正确')
        if set(values) & {'useSelectedSources', 'useRecommendedSources', 'mode'}:
            if set(values) - {'useSelectedSources', 'useRecommendedSources', 'mode', 'includeImages', 'skipReview'}:
                raise ValueError('智能化写作选项不能混用新旧来源设置')
            result = {'useSelectedSources': values.get('useSelectedSources', True),
                      'useRecommendedSources': values.get('useRecommendedSources', True),
                      'mode': values.get('mode', 'strict'), 'includeImages': values.get('includeImages', True)}
            if any(not isinstance(result[key], bool) for key in ('useSelectedSources', 'useRecommendedSources', 'includeImages')):
                raise ValueError('智能化写作来源和配图选项须为开关值')
            if result['mode'] not in ('strict', 'extended'):
                raise ValueError('智能化写作模式须为严谨模式或拓展模式')
            if 'skipReview' in values:
                if not isinstance(values['skipReview'], bool):
                    raise ValueError('跳过检查校验选项须为开关值')
                result['skipReview'] = values['skipReview']
            return result
        if set(values) - {'restrictToSelectedSources', 'includeImages', 'skipReview'}:
            raise ValueError('智能化写作选项格式不正确')
        result = {'restrictToSelectedSources': values.get('restrictToSelectedSources', False),
                  'includeImages': values.get('includeImages', True)}
        if any(not isinstance(value, bool) for value in result.values()):
            raise ValueError('智能化写作选项须为开关值')
        if 'skipReview' in values:
            if not isinstance(values['skipReview'], bool):
                raise ValueError('跳过检查校验选项须为开关值')
            result['skipReview'] = values['skipReview']
        return result

    def _select_automatic_model(self):
        """Choose one configured text model without making provider requests."""
        from .model_gateway import ModelError
        from .writing import default_model_id

        preferred = default_model_id(self.models.get_preferences())
        candidates = self.models.models()

        def current_test(model):
            test = model.get('lastTest') or {}
            return test if test.get('configVersion', model['version']) == model['version'] else {}

        # Keep the settings' stable order for otherwise equivalent candidates.
        candidates.sort(key=lambda model: (
            model['id'] != preferred,
            current_test(model).get('textGeneration') is not True,
            bool(current_test(model).get('error')),
        ))
        for model in candidates:
            test = current_test(model)
            if (re.fullmatch(r'qwen3-embedding(?::[^\s:]+)?', model.get('model', ''), re.IGNORECASE)
                    or test.get('embeddings') is True and test.get('textGeneration') is not True
                    or test.get('textGeneration') is False and not test.get('error')):
                continue
            try:
                return self.models.resolve(model['id'], model['version'])
            except ModelError:
                # Credentials may be unavailable. Do not expose candidate keys
                # or make paid connection probes while finding another config.
                continue
        raise ModelError('没有可用于写作的模型连接，请先在系统设置中配置文字生成模型。')

    def public_automatic(self, project):
        """The report belongs beside the article, never inside its prose."""
        with self.store.connect() as db:
            query = '''SELECT r.id,r.job_id,r.result_json,j.status FROM writing_runs r
                JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? AND r.kind='automatic'
                ORDER BY r.created_at DESC,r.id DESC LIMIT 1'''
            row = db.execute(query, (project['id'],)).fetchone()
            if row is None:
                project['automatic'] = None
                return project
            saved = json.loads(row['result_json']) if row['result_json'] else {}
            state = saved.get('automatic', saved)
            # Cancelling a queued job never dispatches its processor. Publish
            # its terminal report here too, without touching manuscript/history.
            if row['status'] == 'cancelled' and not state.get('report'):
                # Ordinary reads must not take a writer lock: callers may be
                # deduplicating a queue request inside another transaction.
                db.execute('BEGIN IMMEDIATE')
                row = db.execute(query, (project['id'],)).fetchone()
                saved = json.loads(row['result_json']) if row['result_json'] else {}
                state = saved.get('automatic', saved)
                if row['status'] == 'cancelled' and not state.get('report'):
                    state['status'], state['stage'], state['finishedAt'] = 'cancelled', 'complete', now()
                    self._automatic_notice(state, 'cancelled', '全自动写作已取消，已经保存的内容予以保留。', 'complete')
                    state['report'] = self._automatic_report(project, state)
                    db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps({'automatic': state}), row['id']))
            project['automatic'] = {key: copy.deepcopy(state.get(key)) for key in
                ('status', 'stage', 'startedAt', 'finishedAt', 'report')}
            if row['status'] in ('queued', 'running'):
                project['automatic'].update(status=row['status'], report=None, finishedAt=None)
            project['automatic'].update(runId=row['id'], jobId=row['job_id'])
            if row['status'] == 'running':
                retrying = [item for item in retry_events(db, row['id'], running=True) if item['status'] == 'retrying']
                if retrying:
                    titles = {section['id']: section['title'] for section in project.get('sections', [])}
                    if retrying[-1].get('sectionId') in titles:
                        retrying[-1]['sectionTitle'] = titles[retrying[-1]['sectionId']]
                    project['automatic']['currentRetry'] = retrying[-1]
        return project

    def queue_automatic(self, project_id, expected):
        from .writing import WritingConflict, _version
        initial = self.get(project_id)
        _version(initial, expected)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            active = db.execute('''SELECT r.*,j.status FROM writing_runs r JOIN jobs j ON j.id=r.job_id
                WHERE r.project_id=? AND j.status IN ('queued','running') LIMIT 1''', (project_id,)).fetchone()
            if active:
                if active['kind'] != 'automatic':
                    raise WritingConflict('当前文章仍有写作任务，请等待完成后启动全自动写作')
                job_id, run_id = active['job_id'], active['id']
            else:
                options = self._automatic_requested_options(project)
                if options and options.get('restrictToSelectedSources') and not project['sources']:
                    raise ValueError('请先选择资料，再限定在所选资料中进行严格写作。')
                config = self._select_automatic_model()
                run_id, job_id, timestamp = ident('writing_run'), ident('job'), now()
                frozen = {key: copy.deepcopy(project.get(key)) for key in _CONTEXT_KEYS}
                frozen.update(projectId=project_id, protocol=AUTOMATIC_PROTOCOL,
                    workflowConfirmationPolicy=WORKFLOW_CONFIRMATION_POLICY,
                    citationReviewPolicy=DEFERRED_CITATION_REVIEW_POLICY,
                    automaticMaterialPolicy=AUTOMATIC_MATERIAL_POLICY,
                    materialQueryInstructions=MATERIAL_QUERY_INSTRUCTIONS,
                    citationReviewScope={'version': SELECTIVE_REVIEW_POLICY, 'instruction': SELECTIVE_REVIEW_INSTRUCTION},
                    outlinePolicy=ABSTRACT_OUTLINE_POLICY,
                    automaticCitationPolicy=AUTOMATIC_CITATION_POLICY,
                    citationRetentionPolicy=CITATION_RETENTION_POLICY,
                    sectionOutputEnvelopePolicy=SECTION_ENVELOPE_POLICY,
                    automaticModelPolicy=AUTOMATIC_MODEL_POLICY,
                    modelVersion=config['version'], initialVersion=expected,
                    outlinePlanningProtocol=OUTLINE_PLANNING_PROTOCOL,
                    supportPolicies={mode: matching_policy(mode) for mode in ('strict', 'extended')},
                    imageQualityPolicy=QUALITY_POLICY)
                requested_words = project.get('brief', {}).get('targetWords', 6000)
                if isinstance(requested_words, str) and requested_words.strip().isdigit():
                    requested_words = int(requested_words)
                if type(requested_words) is not int or not 100 <= requested_words <= 100000:
                    raise ValueError('文章目标字数须为100至100000之间的整数')
                frozen.update(automaticBodyPolicy=AUTOMATIC_BODY_POLICY,
                              automaticBodyStrategy='single' if requested_words < 10000 else 'chapter',
                              automaticTargetWords=requested_words)
                if options is not None:
                    if 'useSelectedSources' in options:
                        frozen.update(automaticOptions=options, automaticOptionsPolicy=AUTOMATIC_SOURCE_OPTIONS_POLICY,
                            automaticEffectiveMode=options['mode'] if options['useSelectedSources'] or options['useRecommendedSources'] else 'extended',
                            automaticSelectedSources=self._automatic_selected_sources(project))
                    else:
                        frozen.update(automaticOptions=options, automaticOptionsPolicy=AUTOMATIC_OPTIONS_POLICY,
                                      automaticEffectiveMode='strict' if options['restrictToSelectedSources'] else 'extended')
                from .prompts import prompt_text
                frozen['citationMatchInstructions'] = prompt_text('writing_citation_match', db=db)
                frozen['compositionSnapshot'] = composition_snapshot_for_task(db=db)
                frozen['preferenceSnapshot'] = preference_snapshot_for_task(project['brief'], db=db)
                auxiliary_prompts = prompt_snapshot(db, ('writing_material_queries', 'writing_image_vision', 'writing_image_placement'))
                frozen['materialQueryPromptSnapshot'] = {'writing_material_queries': copy.deepcopy(auxiliary_prompts['writing_material_queries'])}
                frozen['materialQueryInstructions'] = compiled_snapshot_text('writing_material_queries', auxiliary_prompts['writing_material_queries'])
                frozen['imagePromptSnapshot'] = {key: copy.deepcopy(auxiliary_prompts[key])
                                                for key in ('writing_image_vision', 'writing_image_placement')}
                frozen['imageInstructions'] = {key: compiled_snapshot_text(key, auxiliary_prompts[key])
                                               for key in ('writing_image_vision', 'writing_image_placement')}
                frozen['automaticRetryPolicy'] = retry_policy()
                frozen['automaticRetryOwnerRunId'] = run_id
                frozen['compositionInstructionFormat'] = COMPOSITION_INSTRUCTION_FORMAT
                frozen['compositionDocumentOrchestration'] = (
                    prompt_defaults.WRITING_DOCUMENT_ORCHESTRATION + '\n' + frozen['compositionSnapshot']['proseInstructions']
                    + '\n' + COMPOSITION_CATALOG_REQUIREMENT)
                frozen['compositionPrompts'] = prompt_snapshot(db,
                    tuple(writing_prompt_id(kind, mode) for kind in ('outline', 'section', 'document') for mode in ('strict', 'extended')),
                    composition_policy=COMPOSITION_POLICY)
                state = {'status': 'queued', 'stage': 'preparing', 'startedAt': timestamp, 'finishedAt': None,
                         'expectedVersion': expected, 'children': {}, 'stages': [], 'issues': [],
                         'generatedSectionIds': [], 'preservedSectionIds': [], 'skippedSectionIds': [], 'report': None}
                public = {key: config.get(key) for key in ('id', 'version', 'name', 'model')}
                db.execute('''INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''', (job_id, ident('writing_batch'), '全自动写作 · ' + project['title'],
                    'writing_automatic', dumps({'runId': run_id}), 'queued', 'queued', timestamp, timestamp))
                db.execute('''INSERT INTO writing_runs(id,job_id,project_id,section_id,kind,base_version,model_id,
                    model_version,model_json,prompt_version,input_json,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (run_id, job_id, project_id, None, 'automatic', expected, config['id'], config['version'],
                     dumps(public), AUTOMATIC_PROTOCOL, dumps(frozen), dumps({'automatic': state}), timestamp))
                prompt_snapshots = copy.deepcopy(frozen['compositionPrompts'])
                prompt_snapshots.update(copy.deepcopy(frozen['preferenceSnapshot'].get('stylePromptSnapshot', {})))
                prompt_snapshots.update(copy.deepcopy(frozen['preferenceSnapshot'].get('structurePromptSnapshot', {})))
                prompt_snapshots.update(copy.deepcopy(frozen['compositionSnapshot'].get('compositionPromptSnapshot', {})))
                prompt_snapshots.update(copy.deepcopy(auxiliary_prompts))
                freeze(db, job_id, prompt_snapshots)
        return {'job': self.store.job(job_id), 'runId': run_id, 'project': self.get(project_id)}

    def interrupt_automatic(self, project_id, expected, run_id):
        """Atomically stop one delegated run and hand the saved draft to its author."""
        from .writing import WritingConflict, _version
        if not isinstance(run_id, str) or not run_id or len(run_id) > 100:
            raise ValueError('请选择需要中断的智能化写作任务')
        cancelled_jobs = []
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            target = db.execute('''SELECT r.*,j.status FROM writing_runs r JOIN jobs j ON j.id=r.job_id
                WHERE r.id=? AND r.project_id=? AND r.kind='automatic' ''', (run_id, project_id)).fetchone()
            if not target:
                raise KeyError(run_id)
            latest = db.execute("SELECT id FROM writing_runs WHERE project_id=? AND kind='automatic' ORDER BY created_at DESC,id DESC LIMIT 1", (project_id,)).fetchone()
            if not latest or latest['id'] != run_id:
                raise WritingConflict('文章已有新的智能化写作任务，请刷新后再切换写作方式。')
            interrupted = target['status'] in ('queued', 'running')
            if interrupted:
                timestamp = now()
                owned = db.execute('''SELECT j.id FROM jobs j JOIN writing_runs r ON r.job_id=j.id
                    WHERE r.project_id=? AND j.status IN ('queued','running')
                    AND (r.id=? OR json_extract(j.input_json,'$.automaticParentRunId')=?)''',
                    (project_id, run_id, run_id)).fetchall()
                cancelled_jobs = [row['id'] for row in owned]
                for job_id in cancelled_jobs:
                    db.execute("UPDATE jobs SET status='cancelled',stage='cancelled',updated_at=? WHERE id=? AND status IN ('queued','running')", (timestamp, job_id))
            if interrupted or project['brief'].get('automation') != 'manual':
                project['brief']['automation'] = 'manual'
                self._save(db, project, 'automatic_interrupted_to_manual')
            if interrupted:
                saved = json.loads(target['result_json']) if target['result_json'] else {}
                state = saved.get('automatic', saved)
                state.update(status='cancelled', stage='complete', finishedAt=now(),
                             expectedVersion=project['version'], interruptedToManual=True)
                self._automatic_notice(state, 'manual_takeover',
                    '已中断智能化写作，已保存的正文、引用和候选均予保留，后续由您逐步确认。', 'complete')
                state['report'] = self._automatic_report(project, state)
                db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps({'automatic': state}), run_id))
        return {'project': self.get(project_id), 'runId': run_id, 'interrupted': interrupted,
                'cancelledJobIds': cancelled_jobs}

    @staticmethod
    def _automatic_notice(state, code, message, stage, section_id=None):
        item = {'code': code, 'message': message, 'stage': stage}
        if section_id:
            item['sectionId'] = section_id
        if item not in state.setdefault('issues', []):
            state['issues'].append(item)

    @staticmethod
    def _automatic_report(project, state):
        titles = {**state.get('sectionTitles', {}), **{s['id']: s['title'] for s in project['sections']}}
        issues = copy.deepcopy(state.get('issues', []))
        for issue in issues:
            if issue.get('sectionId') in titles:
                issue['sectionTitle'] = titles[issue['sectionId']]
        report = {'status': state.get('status'), 'title': project['title'], 'generatedAt': now(),
                'projectVersion': project.get('version'),
                'outputAvailable': any(s.get('content', '').strip() for s in project['sections']),
                'summary': {'generatedSections': len(set(state.get('generatedSectionIds', []))),
                            'preservedSections': len(set(state.get('preservedSectionIds', []))),
                            'skippedSections': len(set(state.get('skippedSectionIds', []))),
                            'illustrations': len(project.get('illustrations', []))},
                'stages': copy.deepcopy(state.get('stages', [])), 'issues': issues,
                'review': copy.deepcopy(state.get('review'))}
        if 'retries' in state:
            report['retries'] = copy.deepcopy(state['retries'])
            for item in report['retries']:
                if item.get('sectionId') in titles:
                    item['sectionTitle'] = titles[item['sectionId']]
            report['summary'].update(retryRequests=len(state['retries']),
                recoveredRequests=sum(item['status'] == 'recovered' for item in state['retries']),
                exhaustedRequests=sum(item['status'] == 'exhausted' for item in state['retries']))
        return report

    def _automatic_checkpoint(self, run, job, state):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent = db.execute('SELECT status,attempts FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if not parent or parent['attempts'] != job['attempts'] + 1:
                raise _AutomaticStopped('attempt_superseded')
            if parent['status'] != 'running':
                raise _AutomaticStopped('cancelled')
            db.execute('UPDATE writing_runs SET result_json=? WHERE id=? AND job_id=?',
                       (dumps({'automatic': state}), run['id'], job['id']))
            db.execute("UPDATE jobs SET stage=?,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                       (state['stage'], now(), job['id'], job['attempts'] + 1))

    def _automatic_current(self, run, job, frozen, state):
        with self.store.connect() as db:
            if not self._active(db, job, job['attempts'] + 1):
                raise _AutomaticStopped('cancelled')
            project = self._load(db, run['project_id'])
            if any(project.get(key) != frozen.get(key) for key in _CONTEXT_KEYS):
                raise _AutomaticStopped('context_changed')
            if frozen.get('automaticBodyPolicy') == AUTOMATIC_BODY_POLICY:
                # Whole-draft children commit several chapter versions. Each
                # save atomically advances this parent checkpoint; accepting
                # that exact checkpoint never accepts an unrelated later edit.
                saved = db.execute('SELECT result_json FROM writing_runs WHERE id=?', (run['id'],)).fetchone()
                persisted = json.loads(saved['result_json']).get('automatic', {}) if saved else {}
                if (persisted.get('expectedVersion') == project['version'] and
                        persisted.get('expectedVersion', 0) > state['expectedVersion']):
                    state['expectedVersion'] = persisted['expectedVersion']
                    state['generatedSectionIds'] = list(dict.fromkeys([
                        *state['generatedSectionIds'], *persisted.get('generatedSectionIds', [])]))
            if project['version'] != state['expectedVersion']:
                # A process may stop immediately after a child commits its
                # chapter. Reconcile only that one documented child save.
                recovered = False
                if project['version'] == state['expectedVersion'] + 1:
                    for child in state['children'].values():
                        row = db.execute('SELECT applied,base_version,kind,section_id FROM writing_runs WHERE id=?', (child['runId'],)).fetchone()
                        if (row and row['applied'] and row['base_version'] == state['expectedVersion']
                                and not (row['kind'] == 'document' and frozen.get('automaticBodyPolicy') == AUTOMATIC_BODY_POLICY)):
                            recovered = True
                            if row['kind'] == 'section' and row['section_id'] not in state['generatedSectionIds']:
                                state['generatedSectionIds'].append(row['section_id'])
                            break
                if not recovered:
                    raise _AutomaticStopped('version_changed')
                state['expectedVersion'] = project['version']
            return project

    def _automatic_confirm_steps(self, run, job, frozen, state, *steps):
        """Atomically bind program completion to the parent's version fence."""
        project = self._automatic_current(run, job, frozen, state)
        if (frozen.get('workflowConfirmationPolicy') != WORKFLOW_CONFIRMATION_POLICY or
                not set(project.get('workflowPendingSteps', [])) & set(steps)):
            return project
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._active(db, job, job['attempts'] + 1):
                raise _AutomaticStopped('cancelled')
            current = self._load(db, run['project_id'])
            if current['version'] != state['expectedVersion']:
                raise _AutomaticStopped('version_changed')
            current['workflowPendingSteps'] = sorted(set(current.get('workflowPendingSteps', [])) - set(steps))
            self._save(db, current, 'automatic_steps_confirmed')
            checkpoint = {**state, 'expectedVersion': current['version']}
            db.execute('UPDATE writing_runs SET result_json=? WHERE id=? AND job_id=?',
                       (dumps({'automatic': checkpoint}), run['id'], job['id']))
        state['expectedVersion'] = current['version']
        return current

    def _automatic_child(self, run, job, frozen, state, key, queue):
        """Target-claim exactly our child; unrelated queued work stays queued."""
        self._automatic_current(run, job, frozen, state)
        child = state['children'].get(key)
        if child is None:
            self._automatic_step = key
            try:
                queued = queue()
            finally:
                self._automatic_step = None
            child = {'runId': queued['runId'], 'jobId': queued['job']['id']}
            state['children'][key] = child
            # Public child queues atomically tag their parent and step; recovery
            # can therefore find a child even before this checkpoint commits.
            self._automatic_checkpoint(run, job, state)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._active(db, job, job['attempts'] + 1):
                raise _AutomaticStopped('cancelled')
            if retry_enabled(frozen):
                prepared = db.execute('SELECT input_json FROM writing_runs WHERE id=? AND project_id=?',
                                      (child['runId'], run['project_id'])).fetchone()
                value = json.loads(prepared['input_json'])
                if 'automaticRetryPolicy' not in value:
                    value['automaticRetryPolicy'] = copy.deepcopy(frozen['automaticRetryPolicy'])
                    value['automaticRetryOwnerRunId'] = run['id']
                    db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(value), child['runId']))
            row = db.execute('SELECT * FROM jobs WHERE id=?', (child['jobId'],)).fetchone()
            if not row:
                raise ValueError('全自动写作步骤记录不存在')
            claimed = dict(row)
            if row['status'] == 'queued':
                changed = db.execute("UPDATE jobs SET status='running',stage='preparing',attempts=attempts+1,updated_at=? WHERE id=? AND status='queued' AND attempts=?",
                                     (now(), row['id'], row['attempts'])).rowcount
                if changed != 1:
                    raise _AutomaticStopped('child_changed')
            elif row['status'] == 'running':
                # Worker recovery normally changes all running jobs to queued.
                # A direct resumable call continues the same fenced attempt.
                claimed['attempts'] -= 1
            else:
                claimed = None
        if claimed is not None:
            self.process(claimed)
        # Check cancellation before accepting even a completed child result.
        project = self._automatic_current(run, job, frozen, state)
        with self.store.connect() as db:
            row = db.execute('''SELECT r.*,j.status,j.error FROM writing_runs r JOIN jobs j ON j.id=r.job_id
                WHERE r.id=? AND r.project_id=?''', (child['runId'], run['project_id'])).fetchone()
        child['status'] = row['status']
        if row['applied'] and row['kind'] == 'section' and row['section_id'] not in state['generatedSectionIds']:
            state['generatedSectionIds'].append(row['section_id'])
        result = json.loads(row['result_json']) if row['result_json'] else {}
        self._automatic_child_notices(state, row, result)
        state['expectedVersion'] = project['version']
        self._automatic_checkpoint(run, job, state)
        return dict(row), result

    def _automatic_child_notices(self, state, row, result):
        stage = {'section': 'chapters', 'document': 'chapters', 'outline': 'outline', 'images': 'images'}.get(row['kind'], state['stage'])
        if row['status'] != 'succeeded':
            self._automatic_notice(state, 'step_failed', row['error'] or '本步骤未完成，已继续处理其他可执行步骤。', stage, row['section_id'])
        notices = [*result.get('issues', []), *result.get('generationNotices', []),
                   *result.get('section', {}).get('generationNotices', [])]
        for issue in notices:
            if isinstance(issue, str):
                self._automatic_notice(state, 'generation_notice', issue, stage, row['section_id'])
        # Citation concerns belong to the final report even when the draft was
        # saved. Saving prose never turns an unresolved binding into support.
        for issue in result.get('section', {}).get('citationIssues', []):
            if isinstance(issue, dict):
                self._automatic_notice(state, 'citation_support',
                    issue.get('reason') or issue.get('message') or '部分论述的引用对应关系尚需核对。',
                    stage, row['section_id'])
        if row['kind'] == 'document':
            for step in result.get('document', {}).get('steps', []):
                chapter = step.get('section') or {}
                for notice in chapter.get('generationNotices', []):
                    if isinstance(notice, str):
                        self._automatic_notice(state, 'generation_notice', notice, 'chapters', step['sectionId'])
                for issue in chapter.get('citationIssues', []):
                    if isinstance(issue, dict):
                        self._automatic_notice(state, 'citation_support',
                            issue.get('reason') or issue.get('message') or '部分论述的引用对应关系尚需核对。',
                            'chapters', step['sectionId'])

    def _automatic_document(self, run, job, frozen, state):
        """Use one frozen whole-draft child for newly requested short drafts."""
        project = self._automatic_current(run, job, frozen, state)
        eligible = []
        for section in project['sections']:
            if section.get('content') or section.get('manual') or section.get('locked'):
                if section['id'] not in state['generatedSectionIds'] and section['id'] not in state['preservedSectionIds']:
                    state['preservedSectionIds'].append(section['id'])
            else:
                eligible.append(section['id'])
        if not eligible:
            return
        try:
            child, result = self._automatic_child(run, job, frozen, state, 'body:document',
                lambda: self.queue_document(project['id'], project['version']))
            applied = {step['sectionId'] for step in result.get('document', {}).get('steps', [])
                       if step.get('status') == 'applied'}
            state['generatedSectionIds'] = list(dict.fromkeys([*state['generatedSectionIds'], *applied]))
            for sid in eligible:
                if sid not in applied:
                    if sid not in state['skippedSectionIds']:
                        state['skippedSectionIds'].append(sid)
                    self._automatic_notice(state, 'chapter_not_adopted',
                        '本章的一次成文结果未能写入正文，已有内容和候选予以保留。', 'chapters', sid)
        except _AutomaticStopped:
            raise
        except Exception as exc:
            # Capacity checks happen before HTTP. Their actionable explanation
            # belongs in the report, without silently switching to many calls.
            message = str(exc) if type(exc) is ValueError else '一次成文未能完成，已经保存的正文予以保留。'
            for sid in eligible:
                if sid not in state['skippedSectionIds']:
                    state['skippedSectionIds'].append(sid)
                self._automatic_notice(state, 'chapter_failed', message, 'chapters', sid)
        self._automatic_checkpoint(run, job, state)

    @staticmethod
    def _automatic_supported_plan(section):
        review = section.get('planningReview', {})
        return (bool(section.get('planningEvidence')) and review.get('state') == 'supported'
                and review.get('mode') == 'strict'
                and review.get('content') == section['title'] + '\n' + section['goal']
                and bool(review.get('assignments'))
                and all(item.get('support') == 'supported' and item.get('supportBasis') in ('direct', 'paraphrase')
                        for item in review['assignments']))

    def _automatic_outline(self, run, job, frozen, state):
        project = self._automatic_current(run, job, frozen, state)
        protected = any(s.get('content') or s.get('locked') or s.get('manual') for s in project['sections'])
        candidate, child = {}, None
        if not project['sections'] or not protected and not project.get('outlineConfirmed'):
            child, candidate = self._automatic_child(run, job, frozen, state, 'outline',
                lambda: self.queue(project['id'], project['version'], 'outline'))
            project = self._automatic_current(run, job, frozen, state)
            if child['status'] != 'succeeded' or candidate.get('outlinePlanningPending'):
                return project
        proposed = candidate.get('sections', project['sections'])
        state.setdefault('sectionTitles', {}).update({section['id']: section['title'] for section in proposed})
        blocked, global_block = set(), False
        for diagnostic in candidate.get('outlinePlanningIssues', []):
            refs = list(diagnostic.get('sectionIds') or [])
            if diagnostic.get('sectionId'):
                refs.append(diagnostic['sectionId'])
            if refs:
                blocked.update(refs)
            else:
                global_block = True
        allowed, omitted = [], []
        for section in proposed:
            mode = project['mode'] if section['mode'] == 'inherit' else section['mode']
            supported = self._automatic_supported_plan(section) and section['id'] not in blocked and not global_block
            if abstract_outline(frozen) or mode != 'strict' or supported or section.get('content') or section.get('locked') or section.get('manual'):
                allowed.append(copy.deepcopy(section))
            else:
                omitted.append(section)
        for section in omitted:
            if section['id'] not in state['skippedSectionIds']:
                state['skippedSectionIds'].append(section['id'])
            self._automatic_notice(state, 'outline_narrowed', '《' + section['title'] + '》的标题或目标尚未获原文完整支持，已从本次自动写作范围移除。', 'outline', section['id'])
        for issue in candidate.get('outlinePlanningIssues', []):
            self._automatic_notice(state, 'outline_support', issue.get('message') or issue.get('reason') or '部分规划未获原文支持。', 'outline', issue.get('sectionId'))
        if not allowed:
            self._automatic_notice(state, 'no_supported_outline',
                '本次没有生成可用纲要，写作要求与资料缺口已保留在报告中。' if abstract_outline(frozen) else
                '现有固定版本原文不足以形成有据纲要，本次保留资料缺口报告。', 'outline')
            return project
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._automatic_child_guard(db)
            current = self._load(db, project['id'])
            if current['version'] != state['expectedVersion']:
                raise _AutomaticStopped('version_changed')
            current['sections'] = allowed
            current['outlineConfirmed'] = True
            current['outlineApproval'] = {'method': 'automatic', 'runId': run['id'], 'approvedAt': now()}
            self._save(db, current, 'automatic_outline_ready')
            state['expectedVersion'] = current['version']
            # Save readiness and its checkpoint atomically for restart safety.
            state['outlineReady'] = True
            db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps({'automatic': state}), run['id']))
        return current

    def _automatic_images(self, run, job, frozen, state):
        project = self._automatic_current(run, job, frozen, state)
        for section in project['sections']:
            if not section.get('content', '').strip() or section.get('locked'):
                continue
            key = 'images:' + section['id']
            if key in state.setdefault('completedImages', []):
                continue
            project = self._automatic_current(run, job, frozen, state)
            try:
                child, candidate = self._automatic_child(run, job, frozen, state, key,
                    lambda: self.queue_image_search(project['id'], project['version'], section['id'],
                                                   search_scope='references', placement_mode='chapter'))
                if child['status'] == 'succeeded':
                    for image in candidate.get('imageSearch', {}).get('candidates', []):
                        gate = image.get('qualityGate', {})
                        positions = [p for p in image.get('placementSuggestions', [])
                                     if p.get('status') == 'recommended' and p.get('qualityPassed') is True]
                        if (image.get('sourceType', 'reference') != 'reference'
                                or image.get('status') != 'recommended' or image.get('scopeEligible') is not True
                                or gate.get('policy') != frozen['imageQualityPolicy'] or gate.get('passed') is not True
                                or not positions):
                            continue
                        project = self._automatic_current(run, job, frozen, state)
                        # Initial delegation authorizes model-checked selection;
                        # never manufacture a human/unverified confirmation.
                        if any(item.get('assetId') == image['assetId'] for item in project.get('illustrations', [])):
                            continue
                        updated = self.add_illustration(project['id'], project['version'],
                            {'runId': child['id'], 'assetId': image['assetId'],
                             'paragraphIndex': positions[0]['paragraphIndex'], 'confirmUnverified': False})
                        state['expectedVersion'] = updated['version']
                        self._automatic_checkpoint(run, job, state)
                        break  # One directly matched picture per chapter is sufficient.
            except _AutomaticStopped:
                raise
            except Exception:
                self._automatic_notice(state, 'images_skipped', '本章没有完成合格配图，已保留纯文字。', 'images', section['id'])
            state['completedImages'].append(key)
            self._automatic_checkpoint(run, job, state)
        project = self._automatic_current(run, job, frozen, state)
        if any(s.get('content', '').strip() for s in project['sections']):
            try:
                updated = self.complete_illustrations(project['id'], project['version'], skip=not project.get('illustrations'))
                state['expectedVersion'] = updated['version']
                self._automatic_checkpoint(run, job, state)
            except Exception:
                self._automatic_notice(state, 'illustrations_unresolved', '已有配图仍有待核对项目，详情列入最终检查报告。', 'images')
        state['stages'].append({'stage': 'images', 'status': 'complete', 'message':
            '已完成配图检查，未通过质量核对的图片不予采用。' if project.get('illustrations') else
            '没有找到完全符合图文匹配要求的图片，文章保留纯文字；配图步骤已完成。'})

    def _automatic_skip_images(self, run, job, frozen, state):
        """Skip new picture work while preserving existing figures and approvals."""
        project = self._automatic_current(run, job, frozen, state)
        if state.get('imagesSkipped'):
            return
        from .writing_images import review_fingerprint
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._automatic_child_guard(db)
            current = self._load(db, project['id'])
            if current['version'] != state['expectedVersion']:
                raise _AutomaticStopped('version_changed')
            if not current.get('illustrations'):
                current['illustrationReview'] = {'status': 'skipped',
                    'fingerprint': review_fingerprint(current), 'checkedAt': now()}
                self._save(db, current, 'automatic_images_skipped')
                state['expectedVersion'] = current['version']
            state['imagesSkipped'] = True
            state['stages'].append({'stage': 'images', 'status': 'skipped',
                'message': '本次已跳过自动配图，已有配图和锁定状态予以保留。' if current.get('illustrations') else '本次已跳过自动配图，文章保留纯文字。'})
            db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps({'automatic': state}), run['id']))

    def _automatic_finish(self, run, job, state, *, cancelled=False):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent = db.execute('SELECT status,attempts FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if (not parent or parent['attempts'] != job['attempts'] + 1
                    or parent['status'] not in ('running', 'cancelled')):
                return
            if parent['status'] == 'cancelled':
                saved = db.execute('SELECT result_json FROM writing_runs WHERE id=?', (run['id'],)).fetchone()
                recorded = json.loads(saved['result_json']) if saved and saved['result_json'] else {}
                if recorded.get('automatic', recorded).get('interruptedToManual'):
                    return
            for child in state.get('children', {}).values():
                db.execute("UPDATE jobs SET status='cancelled',stage='cancelled',updated_at=? WHERE id=? AND status IN ('queued','running')",
                           (now(), child['jobId']))
            try:
                project = self._load(db, run['project_id'])
            except KeyError:
                frozen = json.loads(run['input_json'])
                project = {'title': frozen['title'], 'sections': [], 'illustrations': []}
            cancelled = cancelled or parent['status'] == 'cancelled'
            state.update(status='cancelled' if cancelled else 'completed_with_issues' if state['issues'] else 'completed',
                         stage='complete', finishedAt=now())
            owner = db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['id'],)).fetchone()
            if owner and retry_enabled(json.loads(owner['input_json'])):
                state['retries'] = retry_events(db, run['id'])
            state['report'] = self._automatic_report(project, state)
            db.execute('UPDATE writing_runs SET result_json=?,applied=1 WHERE id=?', (dumps({'automatic': state}), run['id']))
            db.execute("UPDATE jobs SET status='succeeded',stage='ready',error=NULL,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                       (now(), job['id'], job['attempts'] + 1))

    def _automatic_skip_review(self, run, job, frozen, state):
        """Persist an honest skipped disposition without a semantic request."""
        project = self._automatic_current(run, job, frozen, state)
        if state.get('reviewSkipped'):
            return
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._automatic_child_guard(db)
            current = self._load(db, project['id'])
            if current['version'] != state['expectedVersion']:
                raise _AutomaticStopped('version_changed')
            # A rules snapshot is needed for the existing read-only report and
            # export consent contract. It never invokes a model or changes any
            # sentence's unreviewed/support judgment.
            report = self._review(db, current)
            current['reviewDisposition'] = {'version': current['version'], 'reportId': report['reportId'],
                'status': 'skipped', 'issueIds': [], 'decidedAt': now()}
            current['review'] = self._review(db, current)
            db.execute('UPDATE writing_projects SET content_json=? WHERE id=? AND version=?',
                       (dumps(current), current['id'], current['version']))
            state['review'], state['reviewSkipped'] = current['review'], True
            state['stages'].append({'stage': 'review', 'status': 'skipped',
                'message': '本次已跳过检查校验，正文引用的支持关系尚未核对，可稍后主动检查。'})
            db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps({'automatic': state}), run['id']))

    def _automatic_review_citations(self, run, job, frozen, state):
        """New runs check references only after drafting and illustration."""
        project = self._automatic_current(run, job, frozen, state)
        if not any(section.get('content', '').strip() for section in project['sections']):
            return
        try:
            child, candidate = self._automatic_child(run, job, frozen, state, 'review:citations',
                lambda: self.queue_citation_match(project['id'], project['version']))
            match = candidate.get('citationMatch', {})
            for section in match.get('sections', []):
                for issue in section.get('issues', []):
                    self._automatic_notice(state,
                        'citation_review_incomplete' if issue.get('status') == 'unreviewed' else 'citation_support',
                        issue.get('message') or '部分论述的引用支持关系仍待核对。', 'review', section['sectionId'])
            if child['status'] != 'succeeded' or not match.get('reviewComplete', match.get('complete')):
                self._automatic_notice(state, 'citation_review_incomplete',
                    '部分引用核对未完成，已保留正文和已定位的原始引用，请在检查报告中继续核对。', 'review')
        except _AutomaticStopped:
            raise
        except Exception:
            self._automatic_notice(state, 'citation_review_incomplete',
                '引用核对未完成，已保留正文和已定位的原始引用，其他检查继续进行。', 'review')

    def process_automatic(self, job):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM writing_runs WHERE job_id=? AND kind=?', (job['id'], 'automatic')).fetchone()
            if row is None:
                return
            run = dict(row)
        frozen = json.loads(run['input_json'])
        state = json.loads(run['result_json'])['automatic']
        previous = {name: getattr(self, name, None) for name in
                    ('_automatic_parent_run', '_automatic_parent_job', '_automatic_model_config')}
        self._automatic_parent_run, self._automatic_parent_job = run['id'], job
        cancelled = False
        try:
            if frozen.get('protocol') != AUTOMATIC_PROTOCOL:
                raise ValueError('全自动写作规则不匹配')
            if (frozen.get('outlinePlanningProtocol') != OUTLINE_PLANNING_PROTOCOL
                    or frozen.get('imageQualityPolicy') != QUALITY_POLICY
                    or frozen.get('supportPolicies') != {mode: matching_policy(mode) for mode in ('strict', 'extended')}):
                self._automatic_notice(state, 'frozen_rules_changed', '本次冻结的支持与配图规则已发生变化，已保留现有内容并结束任务。', state['stage'])
                return
            self._automatic_model_config = copy.deepcopy(self.models.resolve(run['model_id'], run['model_version']))
            state['status'] = 'running'
            state['report'], state['finishedAt'] = None, None
            # Recover children queued before their parent checkpoint committed.
            with self.store.connect() as db:
                for row in db.execute('SELECT r.id,r.job_id,j.input_json FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? AND r.id<>?', (run['project_id'], run['id'])):
                    payload = json.loads(row['input_json'])
                    if payload.get('automaticParentRunId') == run['id']:
                        state['children'].setdefault(payload['automaticStep'], {'runId': row['id'], 'jobId': row['job_id']})
                for child in state['children'].values():
                    saved = db.execute('''SELECT r.kind,r.section_id,r.applied,r.result_json,j.status,j.error
                        FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.id=?''', (child['runId'],)).fetchone()
                    if saved and saved['kind'] == 'section' and saved['applied'] and saved['section_id'] not in state['generatedSectionIds']:
                        state['generatedSectionIds'].append(saved['section_id'])
                    if saved and saved['status'] not in ('queued', 'running'):
                        self._automatic_child_notices(state, saved, json.loads(saved['result_json']) if saved['result_json'] else {})
                state['preservedSectionIds'] = [sid for sid in state['preservedSectionIds'] if sid not in state['generatedSectionIds']]
            project = self._automatic_current(run, job, frozen, state)
            project = self._automatic_confirm_steps(run, job, frozen, state, 'brief')
            if frozen.get('automaticMaterialPolicy') == AUTOMATIC_MATERIAL_POLICY:
                state['stage'] = 'materials'
                self._automatic_checkpoint(run, job, state)
                project = self._automatic_materials(run, job, frozen, state)
                if state.get('materialsReady'):
                    project = self._automatic_confirm_steps(run, job, frozen, state, 'materials')
            if not project['sources'] and project['mode'] == 'strict':
                self._automatic_notice(state, 'materials_missing', '没有选择可用的固定版本原文，严格模式无法规划和生成正文。', 'materials')
            else:
                state['stage'] = 'outline'
                self._automatic_checkpoint(run, job, state)
                if not state.get('outlineReady'):
                    project = self._automatic_outline(run, job, frozen, state)
                if state.get('outlineReady'):
                    state['stages'].append({'stage': 'outline', 'status': 'complete', 'message': '已在所选资料范围内完成纲要规划。'})
                    state['stage'] = 'chapters'
                    self._automatic_checkpoint(run, job, state)
                    if (frozen.get('automaticBodyPolicy') == AUTOMATIC_BODY_POLICY and
                            frozen.get('automaticBodyStrategy') == 'single'):
                        self._automatic_document(run, job, frozen, state)
                    else:
                        for original in project['sections']:
                            project = self._automatic_current(run, job, frozen, state)
                            section = self._section(project, original['id'])
                            if section.get('content') or section.get('manual') or section.get('locked'):
                                if section['id'] not in state['generatedSectionIds'] and section['id'] not in state['preservedSectionIds']:
                                    state['preservedSectionIds'].append(section['id'])
                                continue
                            mode = project['mode'] if section['mode'] == 'inherit' else section['mode']
                            if not abstract_outline(frozen) and mode == 'strict' and not self._automatic_supported_plan(section):
                                self._automatic_notice(state, 'chapter_plan_unsupported', '章节规划尚未获得原文完整支持，已跳过本章。', 'chapters', section['id'])
                                state['skippedSectionIds'].append(section['id'])
                                continue
                            try:
                                child, candidate = self._automatic_child(run, job, frozen, state, 'section:' + section['id'],
                                    lambda: self.queue(project['id'], project['version'], 'section', section['id']))
                                if child['applied']:
                                    state['generatedSectionIds'].append(section['id'])
                                else:
                                    state['skippedSectionIds'].append(section['id'])
                                    if retry_enabled(frozen) and child['status'] != 'succeeded' and not candidate.get('section'):
                                        self._automatic_notice(state, 'chapter_failed', '本章未能生成可用正文，其他章节已继续处理。', 'chapters', section['id'])
                                    else:
                                        self._automatic_notice(state, 'chapter_not_adopted', '本章生成结果未通过完整支持与版本检查，候选保留，未写入正文。', 'chapters', section['id'])
                                    for issue in candidate.get('section', {}).get('citationIssues', []):
                                        self._automatic_notice(state, 'citation_support', issue.get('reason', '本章存在未获支持的句子。'), 'chapters', section['id'])
                            except _AutomaticStopped:
                                raise
                            except Exception:
                                state['skippedSectionIds'].append(section['id'])
                                self._automatic_notice(state, 'chapter_failed', '本章未能完成，已继续处理其余章节。', 'chapters', section['id'])
                            self._automatic_checkpoint(run, job, state)
                    state['stages'].append({'stage': 'chapters', 'status': 'complete', 'message':
                        '已完成可执行章节的正文生成和原文引用定位，引用核对将在检查校验时进行。'
                        if frozen.get('citationReviewPolicy') == DEFERRED_CITATION_REVIEW_POLICY else
                        '已完成可执行章节的生成及逐句引用检查。'})
                    project = self._automatic_current(run, job, frozen, state)
                    if retry_enabled(frozen):
                        missing = [section for section in project['sections'] if not section.get('content', '').strip()]
                        if missing:
                            state['stages'][-1].update(status='partial', message=
                                f'正文已写入 {len(project["sections"]) - len(missing)}/{len(project["sections"])} 章，仍有章节未完成；已继续处理配图和检查。')
                            for section in missing:
                                self._automatic_notice(state, 'chapter_incomplete', '本章尚无正文，未计为编写完成。', 'chapters', section['id'])
                    if project['outlineConfirmed'] and project['sections'] and all(section.get('content', '').strip() for section in project['sections']):
                        project = self._automatic_confirm_steps(run, job, frozen, state, 'body')
                    state['stage'] = 'images'
                    self._automatic_checkpoint(run, job, state)
                    if frozen.get('automaticOptions', {}).get('includeImages') is False:
                        self._automatic_skip_images(run, job, frozen, state)
                    else:
                        self._automatic_images(run, job, frozen, state)
            state['stage'] = 'review'
            self._automatic_checkpoint(run, job, state)
            if frozen.get('automaticOptions', {}).get('skipReview') is True:
                self._automatic_skip_review(run, job, frozen, state)
            else:
                if frozen.get('citationReviewPolicy') == DEFERRED_CITATION_REVIEW_POLICY:
                    self._automatic_review_citations(run, job, frozen, state)
                project = self._automatic_current(run, job, frozen, state)
                reviewed = self.review(project['id'], project['version'])
                state['review'] = reviewed['review']
                for issue in reviewed['review'].get('issues', []):
                    self._automatic_notice(state, issue.get('code', 'review_issue'), issue.get('message', '全文检查发现待核对问题。'), 'review', issue.get('sectionId'))
                state['stages'].append({'stage': 'review', 'status': 'complete', 'message': '全文检查已完成，问题汇总在独立报告中。'})
        except _AutomaticStopped as exc:
            cancelled = str(exc) == 'cancelled'
            self._automatic_notice(state, 'cancelled' if cancelled else 'project_changed',
                '全自动写作已取消，已经保存的内容予以保留。' if cancelled else '写作过程中设置、资料或文章版本发生变化，已保留最新内容并结束本次任务。', state['stage'])
        except Exception:
            self._automatic_notice(state, 'automatic_failed', '本次全自动写作未能完成全部步骤；已经保存的内容予以保留。', state['stage'])
        finally:
            try:
                self._automatic_finish(run, job, state, cancelled=cancelled)
            finally:
                for name, value in previous.items():
                    setattr(self, name, value)
