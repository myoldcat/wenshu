"""Explicit picture workflows over immutable, quality-checked image searches."""
from __future__ import annotations

import copy
import json

from .store import dumps, ident, now
from .model_gateway import ModelError
from .writing_images import anchor_for, search_fingerprint


IMAGE_BATCH_PROTOCOL = 'writing-image-batch-1'


class WritingImageWorkflowMixin:
    def image_assets(self, project_id, search_scope='references', query='', limit=60, offset=0):
        """Browse saved occurrences without starting work or replacing pinned revisions."""
        from .image_library import freeze_library_scope, library_scope_sources
        if search_scope not in ('references', 'library'):
            raise ValueError('请选择有效的配图范围')
        project = self.get(project_id)
        sources = copy.deepcopy(project.get('sources', []))
        with self.store.connect() as db:
            if search_scope == 'library':
                sources.extend(library_scope_sources(freeze_library_scope(db, project), project))
        items = self._image_catalog_items(sources, query)
        return {'items': items[offset:offset + limit], 'total': len(items), 'offset': offset,
                'limit': limit, 'searchScope': search_scope}

    def _image_catalog_items(self, sources, query='', asset_id=None):
        from .image_retrieval import ImageRetrievalService
        items = []
        reader = ImageRetrievalService(self.store, read_only=True)
        for source in sources:
            data = reader._source_contexts(source, persist=False)
            if data is None:
                continue
            revision, assets, blocks, translated, contexts, _ = data
            allowed_assets = set(source['assetIds']) if 'assetIds' in source else None
            allowed_blocks = set(source['blockIds']) if 'blockIds' in source else None
            for row in assets:
                if asset_id is not None and row['id'] != asset_id:
                    continue
                if not row['mime'].startswith('image/') or (allowed_assets is not None and row['id'] not in allowed_assets):
                    continue
                context = contexts[row['id']]
                caption_ids = [block_id for block_id in context['captionBlockIds'] if block_id in blocks]
                reference_ids = [block_id for block_id in context.get('referenceBlockIds', []) if block_id in blocks]
                scope_ids = caption_ids or [block_id for block_id in reference_ids if allowed_blocks is None or block_id in allowed_blocks]
                eligible = allowed_blocks is None or bool(context['explicitAssociation'] and scope_ids and set(scope_ids) <= allowed_blocks)
                caption = '\n'.join(translated.get(block_id, blocks[block_id]['text']) for block_id in caption_ids) or row['alt']
                if query.strip() and query.strip().casefold() not in (row['alt'] + '\n' + caption + '\n' + revision['title']).casefold():
                    continue
                items.append({'id': row['id'], 'assetId': row['id'], 'url': '/api/assets/' + row['id'],
                              'sourceId': source['sourceId'], 'revisionId': row['revision_id'],
                              'sourceTitle': revision['title'], 'caption': caption, 'altText': row['alt'] or caption,
                              'sourceType': 'library' if allowed_assets is not None else 'reference',
                              'scopeBlockIds': scope_ids,
                              'originalCaption': '\n'.join(blocks[block_id]['text'] for block_id in caption_ids) or row['alt'],
                              'scopeEligible': eligible, 'scopeReason': '' if eligible else '图片图注或明确指向不在本稿已选资料段落范围内。'})
        return items

    def insert_manual_image(self, project_id, expected, data):
        """Save an explicit human selection without resolving or calling a model."""
        from .image_library import freeze_library_scope, library_scope_sources
        from .writing import WritingConflict, _version, _text
        from .writing_images import asset_bytes, asset_owned_by_source, scope_hash, valid_placement
        search_scope = data.get('searchScope', 'references')
        if search_scope not in ('references', 'library'):
            raise ValueError('请选择有效的配图范围')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            self._assert_no_active_tasks(db, project_id)
            section = self._section(project, data['sectionId'])
            if section.get('locked'):
                raise WritingConflict('本章已锁定，请先解锁再配图')
            anchor = anchor_for(section, data['paragraphIndex'])
            if any(key in data and data[key] != anchor[key] for key in ('paragraphExact', 'contentHash')):
                raise WritingConflict('正文或插入位置已变化，请重新选择段落')
            asset = db.execute('SELECT a.id,a.revision_id,r.source_id FROM assets a '
                               'JOIN revisions r ON r.id=a.revision_id WHERE a.id=?', (data['assetId'],)).fetchone()
            sources = project.get('sources', [])
            if search_scope == 'library':
                sources = sources + library_scope_sources(freeze_library_scope(db, project), project)
            source = asset and next((item for item in sources if item['sourceId'] == asset['source_id']
                and item['revisionId'] == asset['revision_id']
                and ('assetIds' not in item or asset['id'] in item['assetIds'])), None)
            if source is None:
                raise ValueError('所选图片不属于当前固定资料或配图范围')
            candidates = self._image_catalog_items([source], asset_id=data['assetId'])
            candidate = next((item for item in candidates if item['scopeEligible']), None)
            if candidate is None:
                raise ValueError('所选图片不属于本稿已选资料段落范围')
            library = candidate['sourceType'] == 'library'
            if library and data.get('confirmLibrarySource') is not True:
                raise WritingConflict('请单独确认图库图片仅作配图，不作为正文依据')
            if not asset_owned_by_source(db, candidate):
                raise ValueError('图片素材与固定来源版本不一致')
            resource, _ = asset_bytes(self.store, db, candidate['assetId'], candidate['revisionId'])
            figures = project.setdefault('illustrations', [])
            if len(figures) >= 40:
                raise ValueError('每篇文章最多使用 40 张图片')
            if any(item.get('assetHash') == resource['sha256'] for item in figures):
                raise ValueError('这张图片已用于本稿，可调整已有图片的位置')
            caption = _text(data.get('caption') or candidate['caption'] or '图片说明', '图注', 2000)
            alt = _text(data.get('altText') or candidate['altText'] or caption, '替代文本', 2000)
            item = {'id': ident('figure'), 'assetId': candidate['assetId'], 'assetHash': resource['sha256'],
                    'url': candidate['url'], 'sourceId': candidate['sourceId'], 'revisionId': candidate['revisionId'],
                    'sourceTitle': candidate['sourceTitle'], 'sectionId': section['id'], 'anchor': anchor,
                    'scopeHash': scope_hash(project, section), 'scopeBlockIds': candidate['scopeBlockIds'],
                    'sourceType': candidate['sourceType'], 'caption': caption, 'altText': alt,
                    'originalCaption': candidate['originalCaption'], 'credit': '来源：' + candidate['sourceTitle'],
                    'status': 'accepted', 'locked': False, 'reviewed': True, 'reviewMethod': 'human',
                    'selectionMethod': 'manual', 'createdAt': now()}
            if library:
                item['librarySourceConfirmed'] = True
                approval = {key: item[key] for key in ('sourceId', 'revisionId', 'assetId', 'assetHash', 'scopeHash')}
                approval.update(method='manual_selection', selectionId=item['id'], confirmedAt=now())
                item['imageSourceApproval'] = approval
            if not valid_placement(project, item):
                raise ValueError('图片依据超出本稿已确认的段落范围')
            figures.append(item)
            self._save(db, project, 'illustration_manually_added')
        return {'project': self.get(project_id), 'illustration': item}

    def queue_image_batch(self, project_id, expected, section_ids, *, search_scope='references',
                          model_id=None, query='', confirm_library_source=False, manual=None):
        from .writing import _version, default_model_id
        from .writing_image_tasks import IMAGE_PROTOCOL, QUALITY_POLICY, _valid_vision_system
        from .image_placement import PLACEMENT_PROTOCOL
        from .image_library import freeze_library_scope
        from .prompts import snapshot as prompt_snapshot, compiled_snapshot_text, freeze
        if (not isinstance(section_ids, list) or not section_ids or len(section_ids) > 20
                or len(set(section_ids)) != len(section_ids)):
            raise ValueError('请选择需要配图的章节')
        if search_scope not in ('references', 'library') or not isinstance(query, str) or len(query) > 500:
            raise ValueError('配图范围或检索条件无效')
        initial = self.get(project_id)
        _version(initial, expected)
        actual_model = model_id or initial.get('modelId') or default_model_id(self.models.get_preferences())
        config = copy.deepcopy(self.models.resolve(actual_model) if actual_model else None)
        public = {key: config.get(key) for key in ('id', 'version', 'name', 'model')} if config else {
            'id': 'local-image-search', 'version': 1, 'name': '资料图片检索', 'model': 'local'}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            self._assert_no_active_tasks(db, project_id)
            selected = [self._section(project, section_id) for section_id in section_ids]
            if any(section.get('locked') or not section.get('content', '').strip() for section in selected):
                raise ValueError('请选择已有保存正文且未锁定的章节')
            scope = freeze_library_scope(db, project) if search_scope == 'library' else None
            if manual:
                asset = db.execute('SELECT a.id,a.revision_id,r.source_id FROM assets a '
                                   'JOIN revisions r ON r.id=a.revision_id WHERE a.id=?', (manual['assetId'],)).fetchone()
                reference = asset and next((source for source in project['sources'] if source['sourceId'] == asset['source_id']
                    and source['revisionId'] == asset['revision_id']), None)
                library = asset and scope and any(source['sourceId'] == asset['source_id'] and source['revisionId'] == asset['revision_id']
                    and asset['id'] in source['assetIds'] for source in scope['sources'])
                if not reference and not library:
                    raise ValueError('所选图片不属于当前固定资料或配图范围')
                if library and not confirm_library_source:
                    raise ValueError('请单独确认图库图片仅作配图，不作为正文依据')
            ids = ('writing_image_vision',) if manual else ('writing_image_vision', 'writing_image_placement')
            image_prompts = prompt_snapshot(db, ids)
            vision_system = _valid_vision_system(compiled_snapshot_text('writing_image_vision', image_prompts['writing_image_vision']))
            placement_system = (None if manual else
                _valid_vision_system(compiled_snapshot_text('writing_image_placement', image_prompts['writing_image_placement'])))
            tasks = []
            for section in selected:
                paragraph_index = manual['paragraphIndex'] if manual else 0
                mode = 'paragraph' if manual else 'chapter'
                task = {'project': copy.deepcopy(project), 'sectionId': section['id'],
                        'anchor': anchor_for(section, paragraph_index), 'query': query.strip(),
                        'fingerprint': search_fingerprint(project, section), 'protocol': IMAGE_PROTOCOL,
                        'qualityPolicy': QUALITY_POLICY, 'visionSystem': vision_system,
                        'searchScope': search_scope, 'placementMode': mode,
                        'placementSystem': placement_system if mode == 'chapter' else None,
                        'placementProtocol': PLACEMENT_PROTOCOL if mode == 'chapter' else None,
                        'libraryScope': copy.deepcopy(scope), 'embedding': None,
                        'imagePromptSnapshot': copy.deepcopy(image_prompts)}
                if manual:
                    task['selectedAssetId'] = manual['assetId']
                tasks.append(task)
            run_id, job_id, timestamp = ident('writing_run'), ident('job'), now()
            frozen = {'protocol': IMAGE_BATCH_PROTOCOL, 'tasks': tasks, 'manual': manual,
                      'confirmLibrarySource': confirm_library_source is True, 'model': public,
                      'imagePromptSnapshot': copy.deepcopy(image_prompts)}
            frozen.update({key: copy.deepcopy(project.get(key)) for key in
                           ('title', 'brief', 'mode', 'sources', 'materialSelection', 'modelId')})
            state = {'status': 'queued', 'sectionIds': list(section_ids), 'completedSectionIds': [],
                     'children': {}, 'issues': [], 'expectedVersion': expected, 'manual': bool(manual)}
            db.execute('INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,created_at,updated_at) '
                       'VALUES(?,?,?,?,?,?,?,?,?)', (job_id, ident('writing_batch'), '手动配图核对' if manual else '所选章节自动配图',
                       'writing_image_batch', dumps({'runId': run_id}), 'queued', 'queued', timestamp, timestamp))
            db.execute('INSERT INTO writing_runs(id,job_id,project_id,section_id,kind,base_version,model_id,model_version,'
                       'model_json,prompt_version,input_json,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (run_id, job_id, project_id, selected[0]['id'], 'image_batch', expected, public['id'], public['version'],
                        dumps(public), IMAGE_BATCH_PROTOCOL, dumps(frozen), dumps({'imageBatch': state}), timestamp))
            freeze(db, job_id, image_prompts)
        return {'job': self.store.job(job_id), 'runId': run_id, 'project': self.get(project_id)}

    def queue_manual_image(self, project_id, expected, data):
        return self.queue_image_batch(project_id, expected, [data['sectionId']], search_scope=data.get('searchScope', 'references'),
            model_id=data.get('modelId'), confirm_library_source=data.get('confirmLibrarySource', False),
            manual={key: data[key] for key in ('assetId', 'paragraphIndex', 'caption', 'altText') if key in data})

    def _image_batch_current(self, run, job):
        from .writing import WritingConflict
        with self.store.connect() as db:
            if not self._active(db, job, job['attempts'] + 1):
                raise WritingConflict('配图任务已取消，已保留现有图片')
            saved = db.execute('SELECT result_json FROM writing_runs WHERE id=?', (run['id'],)).fetchone()
            state = json.loads(saved['result_json'])['imageBatch']
            project = self._load(db, run['project_id'])
            if project['version'] != state['expectedVersion']:
                raise WritingConflict('文稿已变化，已停止配图并保留现有内容')
        return project, state

    def _image_batch_checkpoint(self, run, job, state):
        from .writing import WritingConflict
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._active(db, job, job['attempts'] + 1):
                raise WritingConflict('配图任务已取消，已保留现有图片')
            db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps({'imageBatch': state}), run['id']))

    def process_image_batch(self, job):
        from .writing_image_tasks import QUALITY_POLICY
        from .writing import WritingConflict
        from .prompts import freeze
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM writing_runs WHERE job_id=? AND kind=?', (job['id'], 'image_batch')).fetchone()
        if not row:
            return
        run, frozen = dict(row), json.loads(row['input_json'])
        previous = {key: getattr(self, key, None) for key in ('_automatic_parent_run', '_automatic_parent_job', '_image_workflow_owner', '_image_workflow_manual')}
        self._automatic_parent_run, self._automatic_parent_job, self._image_workflow_owner = run['id'], job, run['id']
        self._image_workflow_manual = bool(frozen.get('manual'))
        try:
            if frozen.get('protocol') != IMAGE_BATCH_PROTOCOL:
                raise ValueError('配图任务规则不完整，请重新提交')
            project, state = self._image_batch_current(run, job)
            state['status'] = 'running'
            self._image_batch_checkpoint(run, job, state)
            for task in frozen['tasks']:
                project, state = self._image_batch_current(run, job)
                section_id = task['sectionId']
                if section_id in state['completedSectionIds']:
                    continue
                section = self._section(project, section_id)
                if section.get('locked') or search_fingerprint(project, section) != task['fingerprint']:
                    raise WritingConflict('正文或资料已变化，已停止配图')
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._active(db, job, job['attempts'] + 1):
                        return
                    child = state['children'].get(section_id)
                    if not child:
                        child = {'runId': ident('writing_run'), 'jobId': ident('job')}
                        timestamp = now()
                        db.execute('INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,created_at,updated_at) '
                            'VALUES(?,?,?,?,?,?,?,?,?)', (child['jobId'], ident('writing_batch'), '配图 · ' + section['title'],
                            'writing_images', dumps({'runId': child['runId'], 'automaticParentRunId': run['id']}),
                            'queued', 'queued', timestamp, timestamp))
                        db.execute('INSERT INTO writing_runs(id,job_id,project_id,section_id,kind,base_version,model_id,model_version,'
                            'model_json,prompt_version,input_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                            (child['runId'], child['jobId'], project['id'], section_id, 'images', project['version'],
                            run['model_id'], run['model_version'], run['model_json'], task['protocol'], dumps(task), timestamp))
                        if 'imagePromptSnapshot' in task:
                            freeze(db, child['jobId'], task['imagePromptSnapshot'])
                        state['children'][section_id] = child
                        db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps({'imageBatch': state}), run['id']))
                    child_job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (child['jobId'],)).fetchone())
                    if child_job['status'] == 'queued':
                        db.execute("UPDATE jobs SET status='running',stage='preparing',attempts=attempts+1,updated_at=? WHERE id=?", (now(), child['jobId']))
                    elif child_job['status'] == 'running':
                        child_job['attempts'] -= 1
                    else:
                        child_job = None
                if child_job:
                    self.process_image_search(child_job)
                project, state = self._image_batch_current(run, job)
                with self.store.connect() as db:
                    result = db.execute('SELECT r.result_json,j.status,j.error FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.id=?', (child['runId'],)).fetchone()
                search = json.loads(result['result_json']).get('imageSearch', {}) if result['result_json'] else {}
                adopted = any(figure.get('runId') == child['runId'] for figure in project.get('illustrations', []))
                if result['status'] != 'succeeded':
                    state['issues'].append({'sectionId': section_id, 'message': result['error'] or '本章图片核对未完成，已保留纯文字。'})
                elif not adopted:
                    for candidate in search.get('candidates', []):
                        manual = frozen.get('manual')
                        approved_library = candidate.get('sourceType') == 'library' and frozen.get('confirmLibrarySource')
                        allowed_statuses = ('recommended', 'needs_review') if manual or approved_library else ('recommended',)
                        if (candidate.get('status') not in allowed_statuses or candidate.get('scopeEligible') is not True
                                or candidate.get('qualityGate', {}).get('policy') != QUALITY_POLICY
                                or candidate['qualityGate'].get('passed') is not True):
                            continue
                        if manual and candidate.get('assetId') != manual['assetId']:
                            continue
                        if candidate.get('sourceType') == 'library' and not frozen.get('confirmLibrarySource'):
                            continue
                        if any(figure.get('assetId') == candidate['assetId'] or figure.get('assetHash') == candidate.get('assetHash')
                               for figure in project.get('illustrations', [])):
                            continue
                        positions = [{'paragraphIndex': manual['paragraphIndex']}] if manual else [position for position in candidate.get('placementSuggestions', [])
                            if position.get('status') in (('recommended', 'needs_review') if approved_library else ('recommended',))
                            and position.get('qualityPassed') is True]
                        if not positions:
                            continue
                        data = {'runId': child['runId'], 'assetId': candidate['assetId'],
                                'paragraphIndex': positions[0]['paragraphIndex'],
                                'confirmUnverified': bool(manual) or candidate.get('sourceType') == 'library',
                                'confirmLibrarySource': frozen.get('confirmLibrarySource', False)}
                        if manual:
                            data.update({key: manual[key] for key in ('caption', 'altText') if manual.get(key)})
                        self.add_illustration(project['id'], project['version'], data)
                        project, state = self._image_batch_current(run, job)
                        adopted = True
                        break
                    if not adopted:
                        state['issues'].append({'sectionId': section_id, 'message': '未找到可直接对应正文的合格配图，已保留现有内容。'})
                state['completedSectionIds'].append(section_id)
                self._image_batch_checkpoint(run, job, state)
            project, state = self._image_batch_current(run, job)
            if not frozen.get('manual') and not state['issues']:
                self.complete_illustrations(project['id'], project['version'], skip=not project.get('illustrations'))
                project, state = self._image_batch_current(run, job)
            state['status'] = 'complete'
            self._image_batch_checkpoint(run, job, state)
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='succeeded',stage='complete',error=NULL,updated_at=? WHERE id=? AND status='running' AND attempts=?", (now(), job['id'], job['attempts'] + 1))
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ValueError, ModelError)) else '配图未完成，已保留正文和现有图片'
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',stage='failed',error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?", (message[:1000], now(), job['id'], job['attempts'] + 1))
        finally:
            for key, value in previous.items():
                setattr(self, key, value)
