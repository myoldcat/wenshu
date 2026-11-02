"""Checkpointed material screening for newly frozen six-step automatic runs."""
from __future__ import annotations

import copy
import hashlib

from .writing_automatic_retry import retry_call, enabled as automatic_retry_enabled

from .enrichment import _json
from .retrieval import RetrievalService, _queries
from .store import dumps
from .writing_operations import _clean
from .writing_source_scope import preserve_citation_sources


AUTOMATIC_MATERIAL_POLICY = 'automatic-whole-document-screening-v1'
AUTOMATIC_SOURCE_OPTIONS_POLICY = 'automatic-material-sources-v2'
from .prompt_task_defaults import MATERIAL_QUERY_INSTRUCTIONS


class AutomaticMaterialScreeningMixin:
    @staticmethod
    def _automatic_selected_sources(project):
        selection = project.get('materialSelection') or {}
        ids = selection['userSourceIds'] if 'userSourceIds' in selection else project.get('sourceIds', [])
        wanted = {value for value in ids if isinstance(value, str)} if isinstance(ids, list) else set()
        return [copy.deepcopy(source) for source in project.get('sources', []) if source['sourceId'] in wanted]

    def _automatic_material_queries(self, run, job, frozen, state, messages):
        config = self._automatic_model_config
        if sum(len(message['content']) for message in messages) > config.get('contextChars', 24000):
            raise ValueError('写作要求超出查询规划输入容量')

        def operation():
            with self.operations.capture(run, job, job['attempts'] + 1, config, messages) as capture:
                response = capture.complete(self.models)
                try:
                    result = _clean(_json(response['text']), config.get('apiKey', ''))
                    queries = _queries(result.get('queries')) if isinstance(result, dict) else []
                    if not queries:
                        raise ValueError('未返回有效检索条件')
                except (ValueError, TypeError, KeyError) as exc:
                    exc.automaticRetryCategory = 'format_error'
                    raise
                capture.validated()
                return queries

        return retry_call(self.store, frozen, 'materials:queries', operation,
            guard=lambda: self._automatic_current(run, job, frozen, state), secret=config.get('apiKey', ''),
            details={'stage': 'materials', 'runId': run['id'], 'label': '检索条件'})

    def _automatic_material_recommendation(self, run, job, frozen, state, project, seed_ids):
        retrieval = RetrievalService(self.store, self.models, operations=self.operations)
        guard = lambda: self._automatic_current(run, job, frozen, state)
        # Only this automatic request's service instance receives the wrappers;
        # ordinary retrieval and old frozen tasks retain their original policy.
        if automatic_retry_enabled(frozen):
            original_embed = retrieval._embed

            def embed(config, texts, observer=None):
                key = hashlib.sha256(dumps([config['id'], config['version'], texts]).encode()).hexdigest()
                return retry_call(self.store, frozen, 'materials:embedding:' + key,
                    lambda: original_embed(config, texts, observer=observer), guard=guard,
                    secret=config.get('apiKey', ''), details={'stage': 'materials', 'runId': run['id'], 'label': '资料语义检索'})

            retrieval._embed = embed
            original_score = retrieval.reranker.score

            def score(payload, **kwargs):
                key = hashlib.sha256(dumps(payload).encode()).hexdigest()
                return retry_call(self.store, frozen, 'materials:rerank:' + key,
                    lambda: list(original_score(payload, **kwargs)), guard=guard,
                    secret=self._automatic_model_config.get('apiKey', ''),
                    details={'stage': 'materials', 'runId': run['id'], 'label': '资料推荐排序'})

            retrieval.reranker.score = score
        return retry_call(self.store, frozen, 'materials:recommendation', lambda: retrieval.recommend(
            project['brief'].get('topic') or project['title'], project['brief'], seed_ids,
            self._automatic_model_config['id'], state['materialQueries']), guard=guard,
            secret=self._automatic_model_config.get('apiKey', ''),
            details={'stage': 'materials', 'runId': run['id'], 'label': '资料推荐'})

    def _automatic_discover_materials(self, run, job, frozen, state, project, seed_ids):
        if 'materialQueries' not in state:
            topic = project['brief'].get('topic') or project['title']
            messages = [{'role': 'system', 'content': frozen['materialQueryInstructions']},
                        {'role': 'user', 'content': dumps({'task': 'writing_automatic_material_queries',
                            'topic': topic, 'purpose': project['brief'].get('purpose', ''),
                            'title': project['title'], 'targetWords': project['brief'].get('targetWords')})}]
            try:
                state['materialQueries'] = self._automatic_material_queries(run, job, frozen, state, messages)
            except Exception:
                state['materialQueries'] = [topic[:300]]
                self._automatic_notice(state, 'material_queries_incomplete',
                    '检索条件生成未完成，已按写作主题继续筛选资料。', 'materials')
            self._automatic_current(run, job, frozen, state)
            self._automatic_checkpoint(run, job, state)
        if 'materialRecommendation' not in state:
            project = self._automatic_current(run, job, frozen, state)
            try:
                state['materialRecommendation'] = self._automatic_material_recommendation(run, job, frozen, state, project, seed_ids)
            except Exception:
                state['materialRecommendation'] = None
                self._automatic_notice(state, 'material_search_incomplete',
                    '推荐资料检索未完成，已使用其余允许的资料继续处理。'
                    if frozen.get('automaticOptionsPolicy') == AUTOMATIC_SOURCE_OPTIONS_POLICY else
                    '推荐资料检索未完成，已保留用户所选资料并继续写作。', 'materials')
            self._automatic_current(run, job, frozen, state)
            self._automatic_checkpoint(run, job, state)

    def _automatic_sources_v2(self, run, job, frozen, state, project):
        options = frozen['automaticOptions']
        selected = copy.deepcopy(frozen['automaticSelectedSources']) if options['useSelectedSources'] else []
        own_ids = [source['sourceId'] for source in selected]
        if options['useRecommendedSources']:
            self._automatic_discover_materials(run, job, frozen, state, project, own_ids)
        recommendation = (state.get('materialRecommendation') or {}) if options['useRecommendedSources'] else {}
        recommended = [item['sourceId'] for item in recommendation.get('items', [])
                       if item.get('sourceId') and item.get('score', 0) > 0]
        desired = list(dict.fromkeys([*own_ids, *recommended]))[:100]
        passages = [{'sourceId': source['sourceId'], 'revisionId': source['revisionId'], 'blockId': block_id}
                    for source in selected for block_id in source.get('blockIds', [])]
        selection = {'recommendationId': recommendation.get('id'), 'passages': passages,
                     'queries': state.get('materialQueries', []) if options['useRecommendedSources'] else [],
                     'items': recommendation.get('items', []), 'userSourceIds': own_ids, 'confirmed': True}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._automatic_child_guard(db)
            current = self._load(db, project['id'])
            if current['version'] != state['expectedVersion']:
                from .writing_automatic import _AutomaticStopped
                raise _AutomaticStopped('version_changed')
            previous_sources = copy.deepcopy(current['sources'])
            before = copy.deepcopy(current)
            preserve_citation_sources(current)
            from .writing import WritingConflict
            try:
                current['sources'] = self._sources(db, desired, selected)
                current['sourceIds'] = [source['sourceId'] for source in current['sources']]
                current['materialSelection'] = self._materials(db, current['sources'], selection,
                    current.get('materialSelection'), previous_sources)
            except (ValueError, WritingConflict):
                current['sources'] = copy.deepcopy(selected)
                current['sourceIds'] = list(own_ids)
                selection.update(recommendationId=None, items=[])
                current['materialSelection'] = self._materials(db, current['sources'], selection,
                    current.get('materialSelection'), previous_sources)
                self._automatic_notice(state, 'material_selection_stale',
                    '推荐资料未能确认，已仅保留本次允许的已选资料。' if selected else
                    '推荐资料未能确认，本次没有采用任何资料。', 'materials')
            mode_changed = self._automatic_option_mode(current, frozen)
            if previous_sources != current['sources'] or mode_changed:
                current['outlineConfirmed'] = False
                for section in current['sections']:
                    self._invalidate_planning(section)
            from .writing_images import preserve_approved_illustrations
            preserve_approved_illustrations(before, current, allow_source_change=True)
            self._save(db, current, 'automatic_sources_confirmed')
            if options['useRecommendedSources'] and recommendation.get('warnings'):
                self._automatic_notice(state, 'material_search_partial',
                    '部分推荐渠道未完成，已使用可用结果继续处理。', 'materials')
            if not current['sources'] and (options['useSelectedSources'] or options['useRecommendedSources']):
                self._automatic_notice(state, 'materials_missing',
                    '本次允许的来源没有可用资料，严谨模式未生成无据正文，缺口已记入报告。'
                    if current['mode'] == 'strict' else
                    '本次允许的来源没有可用资料，已继续拓展写作，事实与观点仍待核实。', 'materials')
            message = ('本次不使用已选资料或系统推荐，已按拓展模式继续写作。'
                       if not options['useSelectedSources'] and not options['useRecommendedSources'] else
                       '已按本次勾选的来源完成资料准备，未启用的来源不参与新正文生成。')
            for key in ('sources', 'materialSelection', 'mode'):
                frozen[key] = copy.deepcopy(current[key])
            state['expectedVersion'] = current['version']
            state['materialsReady'] = True
            state['stages'].append({'stage': 'materials', 'status': 'complete', 'message': message})
            db.execute('UPDATE writing_runs SET input_json=?,result_json=? WHERE id=?',
                       (dumps(frozen), dumps({'automatic': state}), run['id']))
        return current

    @staticmethod
    def _automatic_option_mode(project, frozen):
        if 'automaticOptions' not in frozen:
            return False
        actual = frozen['automaticEffectiveMode']
        changed = project['mode'] != actual
        project['mode'] = actual
        for section in project['sections']:
            if (not section.get('content') and not section.get('manual') and not section.get('locked')
                    and section['mode'] not in ('inherit', actual)):
                section['mode'] = 'inherit'
                section['version'] += 1
                changed = True
        return changed

    def _automatic_reuse_materials(self, run, job, frozen, state, project, *, restricted=False):
        """Commit explicit scope/mode options without upgrading pinned sources."""
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._automatic_child_guard(db)
            current = self._load(db, project['id'])
            if current['version'] != state['expectedVersion']:
                from .writing_automatic import _AutomaticStopped
                raise _AutomaticStopped('version_changed')
            if restricted and not current['sources']:
                raise ValueError('没有已选资料，不能限定为严格写作。')
            if self._automatic_option_mode(current, frozen):
                current['outlineConfirmed'] = False
                for section in current['sections']:
                    self._invalidate_planning(section)
            selection = copy.deepcopy(current.get('materialSelection')) or {'recommendationId': None, 'passages': []}
            selection['confirmed'] = True
            if restricted:
                selection['userSourceIds'] = list(current['sourceIds'])
            current['materialSelection'] = selection
            self._save(db, current, 'automatic_material_scope_ready')
            for key in ('sources', 'materialSelection', 'mode'):
                frozen[key] = copy.deepcopy(current[key])
            state['expectedVersion'] = current['version']
            state['materialsReady'] = True
            state['stages'].append({'stage': 'materials', 'status': 'complete', 'message':
                '本次仅使用您已选定的固定资料，已沿用原范围，不添加推荐来源。' if restricted else
                '已沿用文章已确认的资料范围，本次允许拓展写作。'})
            db.execute('UPDATE writing_runs SET input_json=?,result_json=? WHERE id=?',
                       (dumps(frozen), dumps({'automatic': state}), run['id']))
        return current

    def _automatic_materials(self, run, job, frozen, state):
        project = self._automatic_current(run, job, frozen, state)
        if state.get('materialsReady'):
            return project
        if frozen.get('automaticOptionsPolicy') == AUTOMATIC_SOURCE_OPTIONS_POLICY:
            return self._automatic_sources_v2(run, job, frozen, state, project)
        options = frozen.get('automaticOptions')
        if options is not None and options['restrictToSelectedSources']:
            return self._automatic_reuse_materials(run, job, frozen, state, project, restricted=True)
        # Continuing an existing manuscript cannot silently replace its fixed
        # source scope or a user's already-confirmed material decision.
        preserve = ((project.get('materialSelection') or {}).get('confirmed')
                    or any(section.get('content') or section.get('manual') or section.get('locked')
                           for section in project['sections']))
        if preserve:
            if options is not None:
                return self._automatic_reuse_materials(run, job, frozen, state, project)
            state['materialsReady'] = True
            state['stages'].append({'stage': 'materials', 'status': 'complete',
                                    'message': '已沿用文章已确认的资料范围。'})
            self._automatic_checkpoint(run, job, state)
            return project
        if 'materialQueries' not in state:
            topic = project['brief'].get('topic') or project['title']
            messages = [{'role': 'system', 'content': frozen['materialQueryInstructions']},
                        {'role': 'user', 'content': dumps({'task': 'writing_automatic_material_queries',
                            'topic': topic, 'purpose': project['brief'].get('purpose', ''),
                            'title': project['title'], 'targetWords': project['brief'].get('targetWords')})}]
            try:
                state['materialQueries'] = self._automatic_material_queries(run, job, frozen, state, messages)
            except Exception:
                state['materialQueries'] = [topic[:300]]
                self._automatic_notice(state, 'material_queries_incomplete',
                    '检索条件生成未完成，已按写作主题继续筛选资料。', 'materials')
            self._automatic_current(run, job, frozen, state)
            self._automatic_checkpoint(run, job, state)
        if 'materialRecommendation' not in state:
            project = self._automatic_current(run, job, frozen, state)
            try:
                state['materialRecommendation'] = self._automatic_material_recommendation(run, job, frozen, state, project, project['sourceIds'])
            except Exception:
                state['materialRecommendation'] = None
                self._automatic_notice(state, 'material_search_incomplete',
                    '推荐资料检索未完成，已保留用户所选资料并继续写作。', 'materials')
            self._automatic_current(run, job, frozen, state)
            self._automatic_checkpoint(run, job, state)
        recommendation = state['materialRecommendation'] or {}
        for warning in recommendation.get('warnings', []):
            # Provider internals stay in operation records. The article's
            # writing report uses one bounded business-state explanation.
            self._automatic_notice(state, 'material_search_partial',
                '部分资料检索渠道未完成，已使用可用结果继续筛选。', 'materials')
        own_ids = list(project['sourceIds'])
        suggested = [item['sourceId'] for item in recommendation.get('items', [])
                     if item.get('sourceId') and item.get('score', 0) > 0]
        selected_ids = list(dict.fromkeys([*own_ids, *suggested]))[:100]
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._automatic_child_guard(db)
            current = self._load(db, project['id'])
            if current['version'] != state['expectedVersion']:
                from .writing_automatic import _AutomaticStopped
                raise _AutomaticStopped('version_changed')
            previous_sources = copy.deepcopy(current['sources'])
            previous_mode = current['mode']
            selection = {'recommendationId': recommendation.get('id'), 'passages': [],
                         'queries': state['materialQueries'], 'items': recommendation.get('items', []),
                         'userSourceIds': own_ids, 'confirmed': True}
            from .writing import WritingConflict
            try:
                current['sources'] = self._sources(db, selected_ids, previous_sources)
                current['sourceIds'] = [source['sourceId'] for source in current['sources']]
                current['materialSelection'] = self._materials(db, current['sources'], selection,
                    current.get('materialSelection'), previous_sources)
            except (ValueError, WritingConflict):
                # A recommendation may become stale while screening. Keep
                # the user's pinned originals and continue without a new
                # model request; version/cancellation fences above still stop.
                current['sources'] = copy.deepcopy(previous_sources)
                current['sourceIds'] = list(own_ids)
                selection.update(recommendationId=None, items=[])
                current['materialSelection'] = self._materials(db, current['sources'], selection,
                    current.get('materialSelection'), previous_sources)
                self._automatic_notice(state, 'material_selection_stale',
                    '部分推荐资料在确认前发生变化，已保留用户固定资料并继续写作。', 'materials')
            if options is not None:
                mode_changed = self._automatic_option_mode(current, frozen)
            else:
                current['mode'] = ('extended' if not current['sources'] else
                                   'strict' if not previous_sources else current['mode'])
                mode_changed = current['mode'] != previous_mode
            if current['sources'] != previous_sources or mode_changed:
                current['outlineConfirmed'] = False
                for section in current['sections']:
                    self._invalidate_planning(section)
            self._save(db, current, 'automatic_materials_confirmed')
            if not current['sources']:
                self._automatic_notice(state, 'materials_missing',
                    '未找到可用资料，已采用拓展模式；正文中需要资料支持的内容将列入核对报告。', 'materials')
            # The material decision, parent fence and checkpoint commit
            # together. Recovery never re-runs an already-saved screening.
            for key in ('sources', 'materialSelection', 'mode'):
                frozen[key] = copy.deepcopy(current[key])
            state['expectedVersion'] = current['version']
            state['materialsReady'] = True
            state['stages'].append({'stage': 'materials', 'status': 'complete',
                                    'message': '已完成资料筛选并保存整篇资料范围。'})
            db.execute('UPDATE writing_runs SET input_json=?,result_json=? WHERE id=?',
                       (dumps(frozen), dumps({'automatic': state}), run['id']))
        return current
