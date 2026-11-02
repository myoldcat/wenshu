"""Durable, explicitly requested citation rematching without rewriting prose."""
from __future__ import annotations

import copy
import json

from .enrichment import _json
from .model_gateway import ModelError
from .prompts import freeze, prompt_text, snapshot
from .store import dumps, ident, now
from .writing_citation_matching import (
    MATCHING_PROTOCOL, build_matching_packet, matching_messages,
    partition_matching_packets, subset_matching_packet, validate_matching_result,
    temporal_review_snapshot, with_frozen_temporal_review,
    DEFERRED_CITATION_REVIEW_POLICY, matching_sentence_limit, with_selective_review_scope,
)
from .writing_operations import _clean
from .writing_source_scope import citation_sources
from .writing_automatic_retry import retry_call, AutomaticRetryCancelled


CITATION_TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_citation_match_steps(
 run_id TEXT NOT NULL REFERENCES writing_runs(id), section_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL, batch_index INTEGER NOT NULL,
 input_json TEXT NOT NULL, result_json TEXT,
 PRIMARY KEY(run_id,section_id,batch_index)
);
"""


class WritingCitationTasksMixin:
    @staticmethod
    def _citation_temporal_request(frozen, section_id, prepared, checked=None):
        packet = prepared['packet']
        reviews = frozen.get('citationTemporalReviews', {})
        if not isinstance(reviews, dict):
            raise ValueError('引用任务的冻结时点规则登记不正确')
        present = section_id in reviews
        if (present != ('citationTemporalReview' in prepared)
                or present and reviews[section_id] != prepared['citationTemporalReview']):
            raise ValueError('引用任务与分批请求的冻结时点规则不一致')
        expected = {'citationTemporalReview': reviews[section_id], 'brief': frozen['brief']} if present else {}
        expected_packet = with_frozen_temporal_review(packet, expected)
        if expected_packet != packet or present and prepared['messages'] != matching_messages(packet, instruction=frozen['instructions']):
            raise ValueError('引用任务的冻结时点复核请求已变化')
        if checked is not None:
            fields = ('temporalReview', 'temporalReviewHash')
            if any((key in checked) != (key in packet) or checked.get(key) != packet.get(key) for key in fields):
                raise ValueError('引用复核检查点与冻结时点规则不一致')

    @staticmethod
    def _citation_original_blocks(db, project, section):
        """Check the original pinned source/version before any model request."""
        allowed = {(item['sourceId'], item['revisionId']): item for item in citation_sources(project, section)}
        originals = {}
        for evidence in section['evidence']:
            source = allowed.get((evidence.get('sourceId'), evidence.get('revisionId')))
            if source is None or ('blockIds' in source and evidence.get('blockId') not in source['blockIds']):
                raise ValueError('引用不在当前固定资料范围，不能重新匹配')
            row = db.execute('SELECT b.text,r.source_id FROM blocks b JOIN revisions r ON r.id=b.revision_id WHERE b.id=? AND b.revision_id=?',
                             (evidence.get('blockId'), evidence.get('revisionId'))).fetchone()
            if row is None or row['source_id'] != evidence.get('sourceId'):
                raise ValueError('引用的固定版本原文不存在，不能重新匹配')
            originals[evidence['blockId']] = {'blockId': evidence['blockId'], 'text': row['text'],
                'sourceId': evidence['sourceId'], 'revisionId': evidence['revisionId']}
        return list(originals.values())

    def queue_citation_match(self, project_id, expected, *, model_version=None):
        from .writing import WritingConflict, _version, _effective
        project = self.get(project_id)
        _version(project, expected)
        # A scoped administrator run may select an existing historical model
        # version (for example a configured local model endpoint).
        # This freezes only this task and never edits project/default settings.
        config = self._writing_config(project, model_version)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = self._load(db, project_id)
            _version(project, expected)
            self._automatic_child_guard(db)
            active = db.execute("SELECT r.*,j.status FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? AND r.id<>? AND j.status IN ('queued','running') ORDER BY r.created_at DESC LIMIT 1", (project_id, self._automatic_active_run_id() or '')).fetchone()
            if active:
                if active['kind'] == 'citation_match' and active['base_version'] == expected:
                    job_id, run_id = active['job_id'], active['id']
                else:
                    raise WritingConflict('当前项目已有写作任务，请等待完成或取消后再匹配引用')
            else:
                sections = [copy.deepcopy(section) for section in project['sections'] if section.get('content', '').strip()]
                if not sections:
                    raise ValueError('请先保存正文，再重新匹配引用')
                prepared, section_policies, temporal_reviews = [], {}, {}
                # A bounded output cap also bounds per-request sentence count.
                # This prevents small model configurations from accidentally
                # receiving hundreds of required assignment objects at once.
                output_limit = int(config.get('maxOutputTokens', 2048))
                sentence_limit = matching_sentence_limit(config)
                parent_frozen = {}
                if self._automatic_active_run_id():
                    parent = db.execute('SELECT input_json FROM writing_runs WHERE id=?',
                                        (self._automatic_active_run_id(),)).fetchone()
                    parent_frozen = json.loads(parent['input_json']) if parent else {}
                continue_on_failure = parent_frozen.get('citationReviewPolicy') == DEFERRED_CITATION_REVIEW_POLICY
                instructions = (parent_frozen['citationMatchInstructions'] if continue_on_failure
                                else prompt_text('writing_citation_match', db=db))
                for ordinal, section in enumerate(sections):
                    source_context = []
                    for source in citation_sources(project, section):
                        metadata = db.execute('SELECT published_at FROM revisions WHERE id=? AND source_id=?',
                                              (source['revisionId'], source['sourceId'])).fetchone()
                        source_context.append({'sourceId': source['sourceId'], 'revisionId': source['revisionId'],
                                               'publishedAt': metadata['published_at'] if metadata else None})
                    originals = self._citation_original_blocks(db, project, section)
                    packet = build_matching_packet(section['content'], section['evidence'],
                                                   blocks=originals, section_id=section['id'], mode=_effective(project, section))
                    packet = with_selective_review_scope(packet, section['content'],
                        scope=parent_frozen.get('citationReviewScope') if continue_on_failure else None)
                    temporal_review = temporal_review_snapshot(packet['mode'], source_context, as_of=project['brief'].get('asOf'))
                    if temporal_review is not None:
                        temporal_reviews[section['id']] = temporal_review
                        packet = with_frozen_temporal_review(packet, {'citationTemporalReview': temporal_review})
                    section_policies[section['id']] = {'mode': packet['mode'], 'supportPolicy': copy.deepcopy(packet['policy'])}
                    if not packet['sentences']:
                        raise ValueError('正文没有可用于引用匹配的完整文字')
                    unavailable = False
                    try:
                        partitions = partition_matching_packets(packet, int(config.get('contextChars', 24000)), instruction=instructions)
                    except ValueError:
                        if not continue_on_failure:
                            raise
                        partitions, unavailable = [packet], True
                    batches = []
                    for partition in partitions:
                        ids = [sentence['sentenceId'] for sentence in partition['sentences']]
                        for start in range(0, len(ids), sentence_limit):
                            selected = ids[start:start + sentence_limit]
                            batches.append(partition if selected == ids else subset_matching_packet(partition, selected))
                    for batch_index, batch in enumerate(batches):
                        prepared.append((section['id'], ordinal, batch_index,
                            {'packet': batch, 'messages': matching_messages(batch, instruction=instructions),
                             **({'reviewUnavailable': True} if unavailable else {}),
                             **({'citationTemporalReview': copy.deepcopy(temporal_review)} if temporal_review is not None else {})}))
                job_id, run_id, timestamp = ident('job'), ident('writing_run'), now()
                frozen = {'projectId': project_id, 'sources': copy.deepcopy(project['sources']),
                          'title': project['title'], 'brief': copy.deepcopy(project['brief']),
                          'mode': project['mode'], 'modelId': project['modelId'],
                          'sections': sections, 'instructions': instructions,
                          'citationMatchPolicies': section_policies,
                          'citationReviewPolicy': DEFERRED_CITATION_REVIEW_POLICY,
                          'continueOnReviewFailure': continue_on_failure,
                          'maxOutputTokens': output_limit, 'protocol': MATCHING_PROTOCOL}
                if temporal_reviews:
                    frozen['citationTemporalReviews'] = temporal_reviews
                public = {key: config.get(key) for key in ('id', 'version', 'name', 'model')}
                db.execute('INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                           (job_id, ident('writing_batch'), '引用匹配 · ' + project['title'], 'writing_citation_match',
                            dumps(self._automatic_child_payload(run_id)), 'queued', 'queued', timestamp, timestamp))
                db.execute('INSERT INTO writing_runs(id,job_id,project_id,section_id,kind,base_version,model_id,model_version,model_json,prompt_version,input_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (run_id, job_id, project_id, None, 'citation_match', expected, config['id'], config['version'],
                            dumps(public), MATCHING_PROTOCOL, dumps(frozen), timestamp))
                freeze(db, job_id, snapshot(db, ('writing_citation_match',)))
                db.executemany('INSERT INTO writing_citation_match_steps(run_id,section_id,ordinal,batch_index,input_json) VALUES(?,?,?,?,?)',
                               [(run_id, sid, ordinal, index, dumps(value)) for sid, ordinal, index, value in prepared])
                decision = project.get('reviewDisposition')
                if (not self._automatic_active_run_id() and isinstance(decision, dict)
                        and decision.get('version') == expected and decision.get('status') == 'skipped'):
                    # A newly accepted explicit request supersedes this skip.
                    # Keep its findings, text, version and frozen work intact;
                    # a failed preflight never reaches this transaction write.
                    project.pop('reviewDisposition', None)
                    project['review'] = self._review(db, project)
                    db.execute('UPDATE writing_projects SET content_json=? WHERE id=? AND version=?',
                               (dumps(project), project_id, expected))
        return {'job': self.store.job(job_id), 'runId': run_id, 'project': self.get(project_id)}

    def _citation_match_current(self, db, run, frozen):
        from .writing import WritingConflict
        project = self._load(db, run['project_id'])
        if project['version'] != run['base_version']:
            raise WritingConflict('引用匹配期间文稿已变化，保留最新正文，请重新匹配')
        if any(project.get(key) != frozen.get(key) for key in ('sources', 'title', 'brief', 'mode', 'modelId')):
            raise WritingConflict('引用匹配的资料或写作要求已变化，结果未应用')
        for before in frozen['sections']:
            current = self._section(project, before['id'])
            if any(current.get(key) != before.get(key) for key in ('version', 'content', 'evidence', 'manual', 'locked', 'mode', 'citationSourceScope')):
                raise WritingConflict('引用匹配依据的章节已变化，保留最新正文')
        return project

    def _citation_match_publish(self, db, run_id, *, complete=False):
        rows = db.execute('SELECT section_id,batch_index,result_json FROM writing_citation_match_steps WHERE run_id=? ORDER BY ordinal,batch_index', (run_id,)).fetchall()
        sections = {}
        for row in rows:
            section = sections.setdefault(row['section_id'], {'sectionId': row['section_id'], 'completedCalls': 0, 'totalCalls': 0, 'issues': []})
            section['totalCalls'] += 1
            if row['result_json']:
                section['completedCalls'] += 1
                section['issues'].extend(json.loads(row['result_json'])['issues'])
        incomplete = any(json.loads(row['result_json']).get('reviewIncomplete') for row in rows if row['result_json'])
        result = {'citationMatch': {'complete': complete, 'reviewComplete': complete and not incomplete,
                                    'sections': list(sections.values())}}
        db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps(result), run_id))

    def process_citation_match(self, job):
        from .writing import WritingConflict
        from .writing_sentence_citations import content_hash, unreviewed_matches
        attempt, run = job['attempts'] + 1, None
        try:
            with self.store.connect() as db:
                row = db.execute('SELECT * FROM writing_runs WHERE id=? AND job_id=?',
                                 (json.loads(job['input_json'])['runId'], job['id'])).fetchone()
                if row is None or not self._active(db, job, attempt):
                    return
                run = dict(row)
            frozen = json.loads(run['input_json'])
            config = copy.deepcopy(self.models.resolve(run['model_id'], run['model_version']))
            config['maxOutputTokens'] = frozen['maxOutputTokens']
            while True:
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._active(db, job, attempt):
                        return
                    self._citation_match_current(db, run, frozen)
                    pending = db.execute('SELECT * FROM writing_citation_match_steps WHERE run_id=? AND result_json IS NULL ORDER BY ordinal,batch_index LIMIT 1', (run['id'],)).fetchone()
                    if pending is None:
                        rows = db.execute('SELECT section_id,input_json,result_json FROM writing_citation_match_steps WHERE run_id=? ORDER BY ordinal,batch_index', (run['id'],)).fetchall()
                        combined = {section['id']: [] for section in frozen['sections']}
                        for row in rows:
                            checked = json.loads(row['result_json'])
                            self._citation_temporal_request(frozen, row['section_id'], json.loads(row['input_json']), checked)
                            combined[row['section_id']].extend(checked['matches'])
                        project = self._citation_match_current(db, run, frozen)
                        # This helper rechecks original versioned evidence and
                        # complete sentence coverage inside this transaction.
                        self._rebind_citation_sections(db, project,
                            [{'sectionId': section['id'], 'contentHash': content_hash(section['content']),
                              'matches': combined[section['id']],
                              **({'citationReviewPolicy': frozen['citationReviewPolicy']}
                                 if 'citationReviewPolicy' in frozen else {}),
                              **frozen.get('citationMatchPolicies', {}).get(section['id'], {})} for section in frozen['sections']])
                        self._save(db, project, 'citations_rematched')
                        if frozen.get('citationReviewPolicy') == DEFERRED_CITATION_REVIEW_POLICY:
                            # Persist the derived report with the same commit
                            # as the new bindings. Closing/reloading an editor
                            # must not depend on a later browser review call.
                            project['review'] = self._review(db, project)
                            db.execute('UPDATE writing_projects SET content_json=? WHERE id=? AND version=?',
                                       (dumps(project), project['id'], project['version']))
                        self._citation_match_publish(db, run['id'], complete=True)
                        db.execute('UPDATE writing_runs SET applied=1 WHERE id=?', (run['id'],))
                        db.execute("UPDATE jobs SET status='succeeded',stage='ready',error=NULL,updated_at=? WHERE id=? AND status='running' AND attempts=?", (now(), job['id'], attempt))
                        return
                    pending = dict(pending)
                    db.execute('UPDATE jobs SET stage=?,updated_at=? WHERE id=? AND attempts=?', ('matching_citations', now(), job['id'], attempt))
                prepared = json.loads(pending['input_json'])
                packet = prepared['packet']
                self._citation_temporal_request(frozen, pending['section_id'], prepared)
                if not packet['evidence'] and 'reviewScope' not in packet:
                    result = {'assignments': [{'sentenceId': sentence['sentenceId'], 'evidenceIds': [],
                              'support': 'unsupported', 'reason': '当前章节未登记原文证据，无法确认此句的资料支持关系。',
                              **({'supportBasis': 'none'} if packet.get('mode') is not None else {})}
                              for sentence in packet['sentences']]}
                    checked = validate_matching_result(packet, result)
                else:
                    try:
                        if prepared.get('reviewUnavailable'):
                            raise ValueError('冻结的引用核对输入超过模型预算')
                        def guard():
                            with self.store.connect() as db:
                                if not self._active(db, job, attempt):
                                    raise WritingConflict('引用核对任务已取消')
                                self._citation_match_current(db, run, frozen)

                        def operation():
                            with self.operations.capture(run, job, attempt, config, prepared['messages'], [pending['section_id']]) as capture:
                                response = capture.complete(self.models)
                                try:
                                    checked = validate_matching_result(packet, _json(response['text']))
                                    checked = _clean(checked, config.get('apiKey', ''))
                                except (ValueError, TypeError, KeyError) as exc:
                                    exc.automaticRetryCategory = 'format_error'
                                    raise
                                capture.validated()
                                return checked

                        checked = retry_call(self.store, frozen,
                            'citations:' + run['id'] + ':' + pending['section_id'] + ':' + str(pending['batch_index']), operation,
                            guard=guard, secret=config.get('apiKey', ''), details={'stage': 'review',
                                'runId': run['id'], 'sectionId': pending['section_id'], 'label': '引用核对'})
                    except (AutomaticRetryCancelled, WritingConflict):
                        raise
                    except Exception:
                        if not frozen.get('continueOnReviewFailure'):
                            raise
                        checked = validate_matching_result(packet, {'assignments': [{
                            'sentenceId': sentence['sentenceId'], 'evidenceIds': [],
                            'support': 'uncertain', 'supportBasis': 'none',
                            'reason': '本批引用核对未完成，尚未判断原文支持关系。'}
                            for sentence in packet['sentences']]})
                        original = next(section for section in frozen['sections'] if section['id'] == pending['section_id'])
                        checked['matches'] = unreviewed_matches(original['content'], original['evidence'], checked['matches'])
                        checked['reviewIncomplete'] = True
                        checked['issues'] = [{**issue, 'status': 'unreviewed',
                            'message': '本批引用核对未完成，已保留原始引文位置，尚未判断支持关系。'}
                            for issue in checked['issues']]
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._active(db, job, attempt):
                        return
                    self._citation_match_current(db, run, frozen)
                    db.execute('UPDATE writing_citation_match_steps SET result_json=? WHERE run_id=? AND section_id=? AND batch_index=? AND result_json IS NULL',
                               (dumps(checked), run['id'], pending['section_id'], pending['batch_index']))
                    self._citation_match_publish(db, run['id'])
        except Exception as exc:
            message = str(exc) if type(exc) is ValueError or isinstance(exc, (WritingConflict, ModelError)) else '引用匹配任务执行异常，请检查模型配置后重试'
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not self._active(db, job, attempt):
                    return
                db.execute("UPDATE jobs SET status='failed',stage='failed',error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?", (message[:1500], now(), job['id'], attempt))
