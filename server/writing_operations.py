"""Persistent inspection and explicit cleanup of writing model interactions.

Only an explicit writing scope enables gateway observation. Request bodies and
responses are persisted independently of candidate validation and publication.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
import time

from .model_gateway import ModelError, ModelService, model_request_observer
from .store import dumps, ident, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_model_interactions(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES writing_runs(id),
 project_id TEXT NOT NULL REFERENCES writing_projects(id), job_id TEXT NOT NULL REFERENCES jobs(id),
 kind TEXT NOT NULL, section_ids_json TEXT NOT NULL, attempt INTEGER NOT NULL,
 http_attempt INTEGER NOT NULL, transport TEXT NOT NULL, model_json TEXT NOT NULL,
 request_json TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
 duration_ms INTEGER, http_status INTEGER, status TEXT NOT NULL,
 validation_status TEXT NOT NULL DEFAULT 'pending', error TEXT,
 response_raw TEXT, response_text TEXT, usage_json TEXT NOT NULL DEFAULT '{}',
 finish_reason TEXT, response_truncated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS writing_interactions_run ON writing_model_interactions(run_id,started_at,id);
CREATE INDEX IF NOT EXISTS writing_interactions_project ON writing_model_interactions(project_id,started_at);
CREATE TABLE IF NOT EXISTS writing_material_interactions(
 id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL, http_attempt INTEGER NOT NULL,
 transport TEXT NOT NULL, model_json TEXT NOT NULL, request_json TEXT NOT NULL,
 timeout_seconds INTEGER NOT NULL DEFAULT 90,
 started_at TEXT NOT NULL, finished_at TEXT, duration_ms INTEGER, http_status INTEGER,
 status TEXT NOT NULL, validation_status TEXT NOT NULL DEFAULT 'pending', error TEXT,
 response_raw TEXT, response_text TEXT, usage_json TEXT NOT NULL DEFAULT '{}',
 finish_reason TEXT, response_truncated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS writing_material_calls_recommendation ON writing_material_interactions(recommendation_id,started_at,id);
CREATE TABLE IF NOT EXISTS writing_project_recommendations(
 project_id TEXT NOT NULL REFERENCES writing_projects(id), recommendation_id TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(project_id,recommendation_id)
);
CREATE VIEW IF NOT EXISTS writing_operation_interactions AS
 SELECT id,run_id,project_id,job_id,kind,section_ids_json,attempt,http_attempt,transport,model_json,
 request_json,started_at,finished_at,duration_ms,http_status,status,validation_status,error,
 response_raw,response_text,usage_json,finish_reason,response_truncated,NULL recommendation_id,NULL timeout_seconds
 FROM writing_model_interactions
 UNION ALL
 SELECT id,'materials:'||recommendation_id,NULL,NULL,'materials','[]',1,http_attempt,transport,model_json,
 request_json,started_at,finished_at,duration_ms,http_status,status,validation_status,error,
 response_raw,response_text,usage_json,finish_reason,response_truncated,recommendation_id,timeout_seconds
 FROM writing_material_interactions;
"""


def _clean(value, secret):
    """Never retain a credential even when a provider echoes it inside JSON."""
    if isinstance(value, str):
        if not secret:
            return value
        cleaned = value.replace(secret, '[REDACTED]')
        # Model message.content may itself be JSON whose escaped string values
        # decode into credentials. Redact those spellings before retaining the
        # outer wire response; leave unrelated literal escapes untouched.
        pattern = ''.join('(?:' + re.escape(char) + '|' + re.escape('\\u' + format(ord(char), '04x')) + ')'
                          for char in secret)
        return re.sub(pattern, '[REDACTED]', cleaned, flags=re.IGNORECASE)
    if isinstance(value, list):
        return [_clean(item, secret) for item in value]
    if isinstance(value, dict):
        return {_clean(key, secret): _clean(item, secret) for key, item in value.items()}
    return value


class InteractionCapture:
    def __init__(self, service, run, job, attempt, config, messages, section_ids, recommendation_id=None):
        self.service, self.run, self.job = service, run, job
        self.attempt, self.config, self.messages = attempt, config, messages
        self.section_ids = section_ids
        self.ids = []
        self.validation = 'pending'
        self.recommendation_id = recommendation_id
        self.table = 'writing_material_interactions' if recommendation_id else 'writing_model_interactions'

    def start(self, payload, config, http_attempt, transport='http'):
        call_id = ident('writing_call')
        secret = self.config.get('apiKey', '')
        request = {key: payload[key] for key in ('model', 'messages', 'input', 'encoding_format', 'max_tokens', 'max_completion_tokens', 'stream', 'reasoning_effort', 'response_format', 'query', 'documents') if key in payload}
        model = {key: config.get(key) for key in ('id', 'version', 'name', 'model')}
        if isinstance(config.get('promptSnapshot'), dict):
            model['promptSnapshot'] = config['promptSnapshot']
        if config.get('phase') == 'rerank':
            model['phase'] = 'rerank'
        with self.service.store.connect() as db:
            if self.recommendation_id:
                db.execute('INSERT INTO writing_material_interactions(id,recommendation_id,http_attempt,transport,model_json,request_json,timeout_seconds,started_at,status) VALUES(?,?,?,?,?,?,?,?,?)',
                           (call_id, self.recommendation_id, http_attempt, transport, dumps(_clean(model, secret)), dumps(_clean(request, secret)), config.get('timeoutSeconds', 90), now(), 'in_progress'))
            else:
                db.execute('INSERT INTO writing_model_interactions(id,run_id,project_id,job_id,kind,section_ids_json,attempt,http_attempt,transport,model_json,request_json,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (call_id, self.run['id'], self.run['project_id'], self.job['id'], self.run['kind'], dumps(self.section_ids), self.attempt, http_attempt, transport, dumps(_clean(model, secret)), dumps(_clean(request, secret)), now(), 'in_progress'))
        self.ids.append(call_id)
        return call_id

    def finish(self, call_id, *, raw=None, body=None, status='succeeded', http_status=None, error=None, duration_ms=None, truncated=False):
        secret = self.config.get('apiKey', '')
        if isinstance(raw, bytes):
            # Match the JSON decoder's accepted wire encodings. Decoding a
            # UTF-16 response as UTF-8 could leave recoverable credential
            # characters separated by NULs in the supposedly sanitized text.
            raw = raw.decode(json.detect_encoding(raw), errors='replace')
        # Replace in decoded objects before serialization too: JSON may escape
        # credential characters, so replacing only in the raw wire text is insufficient.
        safe_body = _clean(body, secret)
        safe_raw = _clean(raw, secret)
        if secret and raw:
            try:
                raw_object = json.loads(raw)
            except (ValueError, TypeError):
                raw_object = None
            cleaned_raw_object = _clean(raw_object, secret)
            if cleaned_raw_object != raw_object:
                # A JSON string may spell any credential character as \uXXXX.
                # Clean the actual wire object, including injected gateways'
                # distinct result shape, rather than synthesizing a response.
                safe_raw = dumps(cleaned_raw_object)
        if secret and raw:
            for escaped in (json.dumps(secret, ensure_ascii=True)[1:-1], json.dumps(secret, ensure_ascii=False)[1:-1]):
                safe_raw = safe_raw.replace(escaped, '[REDACTED]')
            if body is None:
                decoded = re.sub(r'\\u([0-9a-fA-F]{4})', lambda match: chr(int(match[1], 16)), safe_raw)
                if secret in decoded:
                    safe_raw = decoded.replace(secret, '[REDACTED]')
        text = None
        finish_reason = None
        usage = {}
        if isinstance(safe_body, dict):
            try:
                choice = safe_body['choices'][0]
                text = choice['message'].get('content')
                text = text if isinstance(text, str) else None
                finish_reason = choice.get('finish_reason')
                finish_reason = finish_reason if isinstance(finish_reason, str) else None
            except (KeyError, TypeError, IndexError, AttributeError):
                pass
            supplied_usage = safe_body.get('usage') or {}
            if isinstance(supplied_usage, dict):
                usage = {key: supplied_usage[key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens') if isinstance(supplied_usage.get(key), (int, float)) and not isinstance(supplied_usage[key], bool) and supplied_usage[key] >= 0}
        with self.service.store.connect() as db:
            db.execute(f'UPDATE {self.table} SET finished_at=?,duration_ms=?,http_status=?,status=?,validation_status=?,error=?,response_raw=?,response_text=?,usage_json=?,finish_reason=?,response_truncated=? WHERE id=?',
                       (now(), duration_ms, http_status, status, 'pending' if status == 'succeeded' else 'unavailable', _clean(error, secret), safe_raw, text, dumps(usage), finish_reason, int(truncated), call_id))

    def complete(self, models):
        if isinstance(models, ModelService):
            with model_request_observer(self):
                return models.complete(self.config, self.messages)
        # Injected test/offline gateways expose no HTTP transport. Their actual
        # submitted messages and returned result are explicitly labelled.
        payload = {'messages': self.messages, 'model': self.config.get('model'), 'max_tokens': self.config.get('maxOutputTokens'), 'stream': False}
        call_id = self.start(payload, self.config, 1, 'injected')
        started = time.monotonic()
        try:
            result = models.complete(self.config, self.messages)
        except BaseException as exc:
            message = str(exc) if isinstance(exc, ModelError) else '写作模型调用失败'
            self.finish(call_id, status='failed', error=message, duration_ms=round((time.monotonic() - started) * 1000))
            raise
        body = {'choices': [{'message': {'content': result.get('text')}, 'finish_reason': result.get('finishReason')}], 'usage': result.get('usage') or {}}
        # This is the injected gateway result, never represented as a wire reply.
        self.finish(call_id, raw=dumps(result), body=body, duration_ms=round((time.monotonic() - started) * 1000))
        return result

    def validated(self):
        self.validation = 'valid'

    def close(self, exc=None):
        if not self.ids:
            return
        with self.service.store.connect() as db:
            row = db.execute('SELECT status,attempts FROM jobs WHERE id=?', (self.job['id'],)).fetchone() if self.job else None
            active = bool(self.recommendation_id or (row and row['status'] == 'running' and row['attempts'] == self.attempt))
            validation = 'discarded' if not active else ('invalid' if exc else self.validation)
            # Negotiation and transport failures did not return a candidate.
            db.execute(f"UPDATE {self.table} SET validation_status=CASE WHEN status='succeeded' THEN ? ELSE 'unavailable' END WHERE id=?", (validation, self.ids[-1]))
            if exc and active:
                from .writing_reference_scope import CitationScopeError
                message = str(exc) if type(exc) is ValueError or isinstance(exc, (ModelError, CitationScopeError)) else '写作结果校验失败'
                db.execute(f"UPDATE {self.table} SET error=COALESCE(error,?) WHERE id=?", (_clean(message[:1500], self.config.get('apiKey', '')), self.ids[-1]))


class WritingOperations:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def capture(self, run, job, attempt, config, messages, section_ids=None):
        capture = InteractionCapture(self, run, job, attempt, config, messages, section_ids or ([run['section_id']] if run.get('section_id') else []))
        try:
            yield capture
        except BaseException as exc:
            capture.close(exc)
            raise
        else:
            capture.close()

    @contextmanager
    def capture_materials(self, recommendation_id, config, messages):
        capture = InteractionCapture(self, None, None, 1, config, messages, [], recommendation_id)
        try:
            yield capture
        except BaseException as exc:
            capture.close(exc)
            raise
        else:
            capture.close()

    @contextmanager
    def capture_rerank(self, recommendation_id, config):
        """Record the actual ranking request separately from query planning."""
        with self.capture_materials(recommendation_id, dict(config, phase='rerank'), []) as capture:
            yield capture

    @staticmethod
    def discard_rerank(db, recommendation_id, reason):
        """Input validation failed after HTTP success; retain the real reply."""
        db.execute('''UPDATE writing_material_interactions
            SET validation_status='discarded',error=? WHERE recommendation_id=?
            AND json_extract(model_json,'$.phase')='rerank' AND status='succeeded' ''',
            (reason, recommendation_id))

    @staticmethod
    def attach_recommendation(db, project):
        recommendation_id = project.get('materialSelection', {}).get('recommendationId')
        if recommendation_id:
            db.execute('INSERT OR IGNORE INTO writing_project_recommendations VALUES(?,?,?)', (project['id'], recommendation_id, now()))
        brief_ids = project.get('brief', {}).get('assistanceIds', [])
        material_ids = project.get('materialSelection', {}).get('assistanceIds', [])
        assistance_ids = [*(brief_ids if isinstance(brief_ids, list) else []),
                          *(material_ids if isinstance(material_ids, list) else [])]
        for assistance_id in set(value for value in assistance_ids if isinstance(value, str)):
            if db.execute('SELECT 1 FROM writing_material_interactions WHERE recommendation_id=? LIMIT 1', (assistance_id,)).fetchone():
                db.execute('INSERT OR IGNORE INTO writing_project_recommendations VALUES(?,?,?)', (project['id'], assistance_id, now()))

    @staticmethod
    def _page(items, total, limit, offset):
        return {'items': items, 'total': total, 'limit': limit, 'offset': offset, 'hasMore': offset + len(items) < total}

    def projects(self, limit=30, offset=0, state='all'):
        if state not in ('all', 'active', 'deleted'):
            raise ValueError('文章状态必须为 all、active 或 deleted')
        where = {'all': '', 'active': ' WHERE p.deleted_at IS NULL', 'deleted': ' WHERE p.deleted_at IS NOT NULL'}[state]
        query = """SELECT p.id,json_extract(p.content_json,'$.title') title,json_extract(p.content_json,'$.mode') mode,p.created_at,p.updated_at,p.deleted_at,p.version,
        (SELECT count(*) FROM writing_runs r WHERE r.project_id=p.id)+(SELECT count(DISTINCT m.recommendation_id) FROM writing_material_interactions m JOIN writing_project_recommendations a ON a.recommendation_id=m.recommendation_id WHERE a.project_id=p.id) run_count,
        (SELECT count(*) FROM writing_model_interactions c WHERE c.project_id=p.id)+(SELECT count(*) FROM writing_material_interactions m JOIN writing_project_recommendations a ON a.recommendation_id=m.recommendation_id WHERE a.project_id=p.id) call_count,
        (SELECT j.status FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=p.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1) status FROM writing_projects p""" + where
        if state != 'deleted':
            query += """ UNION ALL SELECT 'preparations','未归入文稿的材料推荐',NULL,min(started_at),max(started_at),NULL,NULL,count(DISTINCT recommendation_id),count(*),NULL
            FROM writing_material_interactions m WHERE NOT EXISTS(SELECT 1 FROM writing_project_recommendations a WHERE a.recommendation_id=m.recommendation_id) HAVING count(*)>0"""
        with self.store.connect() as db:
            total = db.execute(f'SELECT count(*) FROM ({query})').fetchone()[0]
            rows = db.execute(f'SELECT * FROM ({query}) ORDER BY updated_at DESC,id LIMIT ? OFFSET ?', (limit, offset)).fetchall()
        return self._page([{'id': row['id'], 'title': row['title'], 'mode': row['mode'], 'createdAt': row['created_at'], 'updatedAt': row['updated_at'], 'deletedAt': row['deleted_at'], 'version': row['version'], 'runCount': row['run_count'], 'callCount': row['call_count'], 'status': row['status']} for row in rows], total, limit, offset)

    def purge(self, project_id, expected):
        """Atomically remove only one explicitly soft-deleted manuscript.

        Source revisions, models and material recommendation records are shared
        library data; deleting their manuscript association must preserve them.
        """
        from .writing import WritingConflict, WritingService, _version
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT version,deleted_at FROM writing_projects WHERE id=?', (project_id,)).fetchone()
            if not row:
                raise KeyError(project_id)
            _version(dict(row), expected)
            if not row['deleted_at']:
                raise WritingConflict('请先在写作空间删除文章，再到运维页面彻底删除')
            WritingService._assert_no_active_tasks(db, project_id)
            # Delete leaf records first, with foreign keys enabled throughout.
            # An unexpected dependency aborts the whole transaction rather than
            # leaving a partly removed manuscript or deleting shared data.
            run_ids = 'SELECT id FROM writing_runs WHERE project_id=?'
            job_ids = 'SELECT job_id FROM writing_runs WHERE project_id=?'
            db.execute('DELETE FROM writing_model_interactions WHERE project_id=?', (project_id,))
            for table in ('writing_calls', 'writing_document_steps', 'writing_document_calls', 'writing_citation_match_steps'):
                db.execute(f'DELETE FROM {table} WHERE run_id IN ({run_ids})', (project_id,))
            db.execute(f'DELETE FROM job_prompt_snapshots WHERE job_id IN ({job_ids})', (project_id,))
            # Capture ids before removing the run-to-task ownership relation.
            jobs = [item['job_id'] for item in db.execute(job_ids, (project_id,))]
            db.execute('DELETE FROM writing_runs WHERE project_id=?', (project_id,))
            db.execute('DELETE FROM writing_history WHERE project_id=?', (project_id,))
            db.execute('DELETE FROM writing_project_recommendations WHERE project_id=?', (project_id,))
            db.execute('DELETE FROM writing_projects WHERE id=?', (project_id,))
            db.executemany('DELETE FROM jobs WHERE id=?', [(job_id,) for job_id in jobs])
        return {'purged': True, 'projectId': project_id}

    def runs(self, project_id, limit=30, offset=0):
        with self.store.connect() as db:
            if project_id != 'preparations' and not db.execute('SELECT 1 FROM writing_projects WHERE id=?', (project_id,)).fetchone():
                raise KeyError(project_id)
            material_query, material_args = self._material_query(project_id)
            query = ("SELECT r.id,r.project_id,r.job_id,r.kind,r.section_id,r.base_version,r.model_json,r.prompt_version,r.created_at,r.applied,j.status,j.stage,j.error,j.attempts,(SELECT count(*) FROM writing_model_interactions c WHERE c.run_id=r.id) call_count,CASE WHEN r.kind='automatic' THEN r.result_json ELSE NULL END result_json FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.project_id=? UNION ALL " + material_query)
            args = [project_id, *material_args]
            total = db.execute(f'SELECT count(*) FROM ({query})', args).fetchone()[0]
            rows = db.execute(f'SELECT * FROM ({query}) ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        items = [self._run(row) for row in rows]
        return self._page(items, total, limit, offset)

    @staticmethod
    def _material_query(project_id=None, recommendation_id=None):
        if recommendation_id is not None:
            where, args = 'm.recommendation_id=?', [recommendation_id]
            owner = "COALESCE((SELECT project_id FROM writing_project_recommendations a WHERE a.recommendation_id=m.recommendation_id ORDER BY created_at LIMIT 1),'preparations')"
        elif project_id == 'preparations':
            where, args, owner = 'NOT EXISTS(SELECT 1 FROM writing_project_recommendations a WHERE a.recommendation_id=m.recommendation_id)', [], "'preparations'"
        else:
            where, args, owner = 'm.recommendation_id IN (SELECT recommendation_id FROM writing_project_recommendations WHERE project_id=?)', [project_id], '?'
            args.insert(0, project_id)
        query = f"""SELECT 'materials:'||m.recommendation_id id,{owner} project_id,NULL job_id,'materials' kind,NULL section_id,NULL base_version,
        (SELECT model_json FROM writing_material_interactions c WHERE c.recommendation_id=m.recommendation_id ORDER BY started_at,id LIMIT 1) model_json,'writing-materials-1.0' prompt_version,min(m.started_at) created_at,0 applied,
        CASE WHEN max(m.status='in_progress' AND (julianday('now')-julianday(m.started_at))*86400<m.timeout_seconds+15) THEN 'running' WHEN max(m.validation_status='valid') THEN 'succeeded' ELSE 'failed' END status,
        'materials' stage,(SELECT error FROM writing_material_interactions c WHERE c.recommendation_id=m.recommendation_id ORDER BY started_at DESC,id DESC LIMIT 1) error,1 attempts,count(*) call_count,NULL result_json
        FROM writing_material_interactions m WHERE {where} GROUP BY m.recommendation_id"""
        return query, args

    @staticmethod
    def _run(row):
        result = {'id': row['id'], 'projectId': row['project_id'], 'jobId': row['job_id'], 'kind': row['kind'], 'sectionId': row['section_id'], 'baseVersion': row['base_version'], 'model': json.loads(row['model_json']), 'promptVersion': row['prompt_version'], 'createdAt': row['created_at'], 'applied': bool(row['applied']), 'status': row['status'], 'stage': row['stage'], 'error': row['error'], 'attempts': row['attempts'], 'callCount': row['call_count'], 'logCoverage': 'recorded' if row['call_count'] else ('legacy_unavailable' if row['attempts'] else 'not_called')}
        if row['kind'] == 'automatic' and row['result_json']:
            state = json.loads(row['result_json']).get('automatic', {})
            outcome = {'status': state.get('status')}
            report = state.get('report')
            if isinstance(report, dict) and state.get('status') not in ('queued', 'running'):
                # List rows carry the frozen outcome, never the full report or
                # its source text. Job success still means execution finished.
                summary = report.get('summary') if isinstance(report.get('summary'), dict) else {}
                outcome['report'] = {'outputAvailable': report.get('outputAvailable'),
                                     'summary': {key: summary[key] for key in
                                         ('generatedSections', 'preservedSections', 'skippedSections') if key in summary}}
            result['automatic'] = outcome
        return result

    def run(self, run_id):
        """Read frozen legacy inputs without claiming a historical wire reply."""
        if run_id.startswith('materials:'):
            query, args = self._material_query(recommendation_id=run_id[len('materials:'):])
            with self.store.connect() as db:
                row = db.execute(query, args).fetchone()
                has_response = db.execute('SELECT 1 FROM writing_material_interactions WHERE recommendation_id=? AND response_raw IS NOT NULL LIMIT 1', (run_id[len('materials:'):],)).fetchone()
            if not row:
                raise KeyError(run_id)
            return {'run': self._run(row), 'history': {'requests': [], 'candidate': None, 'rawResponseAvailable': bool(has_response), 'inputKind': 'recorded_calls', 'note': '材料推荐的完整请求与结果保存在逐次调用记录中。'}}
        with self.store.connect() as db:
            row = db.execute('SELECT r.*,j.status,j.stage,j.error,j.attempts,(SELECT count(*) FROM writing_model_interactions c WHERE c.run_id=r.id) call_count FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.id=?', (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            has_response = db.execute('SELECT 1 FROM writing_model_interactions WHERE run_id=? AND response_raw IS NOT NULL LIMIT 1', (run_id,)).fetchone()
            frozen = json.loads(row['input_json'])
            requests = []
            if row['kind'] == 'document':
                for step in db.execute('SELECT section_id,input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run_id,)):
                    item = json.loads(step['input_json'])
                    if item.get('messages'):
                        requests.append({'label': item.get('section', {}).get('title') or '章节输入', 'sectionIds': [step['section_id']], 'messages': item['messages']})
            elif frozen.get('messages'):
                requests.append({'label': '任务冻结输入', 'sectionIds': [row['section_id']] if row['section_id'] else [], 'messages': frozen['messages']})
        run = self._run(row)
        return {'run': run, 'history': {'requests': requests, 'candidate': json.loads(row['result_json']) if row['result_json'] else None, 'rawResponseAvailable': bool(has_response), 'inputKind': 'frozen_inputs', 'note': '冻结输入用于还原任务准备内容；全文任务的章节可能合并发送。候选结果已经过规则校验，不是模型原始响应。原始交互请以逐次调用记录为准。'}}

    @staticmethod
    def _call(row):
        result = {'id': row['id'], 'runId': row['run_id'], 'projectId': row['project_id'], 'jobId': row['job_id'], 'kind': row['kind'], 'sectionIds': json.loads(row['section_ids_json']), 'attempt': row['attempt'], 'httpAttempt': row['http_attempt'], 'transport': row['transport'], 'model': json.loads(row['model_json']), 'status': row['status'], 'validationStatus': row['validation_status'], 'startedAt': row['started_at'], 'finishedAt': row['finished_at'], 'durationMs': row['duration_ms'], 'httpStatus': row['http_status'], 'error': row['error'], 'usage': json.loads(row['usage_json']), 'finishReason': row['finish_reason'], 'responseTruncated': bool(row['response_truncated'])}
        active = row['current_job_status'] == 'running' and row['current_job_attempts'] == row['attempt'] if row['job_id'] else (datetime.now(timezone.utc) - datetime.fromisoformat(row['started_at'])).total_seconds() < (row['timeout_seconds'] or 90) + 15
        if result['status'] == 'in_progress' and not active:
            result.update(status='interrupted', validationStatus='unavailable', error='调用过程已中断，未记录完整返回')
        elif result['validationStatus'] == 'pending' and not active:
            result['validationStatus'] = 'unavailable'
        return result

    def calls(self, run_id, limit=30, offset=0):
        with self.store.connect() as db:
            is_material = run_id.startswith('materials:')
            exists = db.execute('SELECT 1 FROM writing_material_interactions WHERE recommendation_id=? LIMIT 1', (run_id[len('materials:'):],)).fetchone() if is_material else db.execute('SELECT 1 FROM writing_runs WHERE id=?', (run_id,)).fetchone()
            if not exists:
                raise KeyError(run_id)
            total = db.execute('SELECT count(*) FROM writing_operation_interactions WHERE run_id=?', (run_id,)).fetchone()[0]
            # Explicitly omit large prompt and response columns from list reads.
            columns = 'id,run_id,project_id,job_id,kind,section_ids_json,attempt,http_attempt,transport,model_json,status,validation_status,started_at,finished_at,duration_ms,http_status,error,usage_json,finish_reason,response_truncated,timeout_seconds'
            selected = ','.join('c.' + item for item in columns.split(','))
            rows = db.execute(f'SELECT {selected},j.status current_job_status,j.attempts current_job_attempts FROM writing_operation_interactions c LEFT JOIN jobs j ON j.id=c.job_id WHERE c.run_id=? ORDER BY c.started_at,c.id LIMIT ? OFFSET ?', (run_id, limit, offset)).fetchall()
        return self._page([self._call(row) for row in rows], total, limit, offset)

    def call(self, call_id):
        with self.store.connect() as db:
            row = db.execute('SELECT c.*,j.status current_job_status,j.attempts current_job_attempts FROM writing_operation_interactions c LEFT JOIN jobs j ON j.id=c.job_id WHERE c.id=?', (call_id,)).fetchone()
        if not row:
            raise KeyError(call_id)
        result = self._call(row)
        result['request'] = json.loads(row['request_json'])
        result['messages'] = result['request'].get('messages', [])
        try:
            body = json.loads(row['response_raw']) if row['response_raw'] is not None else None
        except (ValueError, TypeError):
            body = None
        result['response'] = {'raw': row['response_raw'], 'text': row['response_text'], 'json': body}
        return {'call': result}
