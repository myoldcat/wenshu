"""Durable, explicitly requested Wiki compilation in bounded source batches."""
import hashlib
import json

from .model_gateway import ModelError, default_model_id
from .prompts import snapshot, freeze, intent_signature
from .store import dumps, ident, now
from .wiki_pruning import POLICY_VERSION as SELECTION_POLICY_VERSION, INSTRUCTIONS as SELECTION_INSTRUCTIONS

ALL_POLICY = 'wenshu-wiki-all-1'
ALL_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_all_runs(
 id TEXT PRIMARY KEY,job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 scope TEXT NOT NULL,scope_key TEXT NOT NULL,scope_json TEXT NOT NULL,
 frozen_json TEXT NOT NULL,excluded_json TEXT NOT NULL,batches INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS wiki_all_scope ON wiki_all_runs(scope_key);
CREATE TABLE IF NOT EXISTS wiki_all_items(
 run_id TEXT NOT NULL REFERENCES wiki_all_runs(id),ordinal INTEGER NOT NULL,
 source_id TEXT NOT NULL,revision_id TEXT NOT NULL,title TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',reason TEXT,child_job_id TEXT,
 owned INTEGER NOT NULL DEFAULT 0,batch INTEGER,
 PRIMARY KEY(run_id,ordinal),UNIQUE(run_id,source_id)
);
CREATE INDEX IF NOT EXISTS wiki_all_child ON wiki_all_items(child_job_id);
"""


def _digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


class WikiAllMixin:
    @staticmethod
    def _all_rows(db, source_ids):
        if source_ids is not None and (not isinstance(source_ids, list) or len(source_ids) > 10000
                or any(not isinstance(value, str) or not value.strip() or len(value) > 100 for value in source_ids)):
            raise ValueError('Wiki 构建范围须为有效文档标识列表')
        selected = None if source_ids is None else list(dict.fromkeys(source_ids))
        where, args = ('', ()) if selected is None else (
            ' WHERE s.id IN (SELECT value FROM json_each(?))', (dumps(selected),))
        rows = db.execute('''SELECT s.id,r.id revision_id,r.title
            FROM sources s JOIN revisions r ON r.id=s.current_revision AND r.source_id=s.id'''
            + where + ' ORDER BY s.created_at DESC,s.id', args).fetchall()
        if selected is not None:
            by_id = {row['id']: row for row in rows}
            if any(source_id not in by_id for source_id in selected):
                raise KeyError('文档不存在')
            rows = [by_id[source_id] for source_id in selected]
        return rows

    def _all_dto(self, db, row):
        job = db.execute('SELECT * FROM jobs WHERE id=?', (row['job_id'],)).fetchone()
        counts = dict.fromkeys(('pending', 'queued', 'running', 'succeeded', 'failed', 'cancelled', 'skipped'), 0)
        for item in db.execute('''SELECT i.status,j.status child_status,j.stage child_stage FROM wiki_all_items i
                LEFT JOIN jobs j ON j.id=i.child_job_id WHERE i.run_id=?''', (row['id'],)):
            status = item['child_status'] if item['status'] in ('queued', 'running') and item['child_status'] else item['status']
            if status == 'succeeded' and item['child_stage'] == 'empty':
                status = 'skipped'
            counts[status] += 1
        return {'id': row['job_id'], 'job': self.store.job_dto(job), 'scope': row['scope'],
                'total': sum(counts.values()), 'batchSize': 20, 'batches': row['batches'],
                'counts': counts, 'excluded': json.loads(row['excluded_json'])}

    def all_run(self, job_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM wiki_all_runs WHERE job_id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            return self._all_dto(db, row)

    def all_runs(self):
        with self.store.connect() as db:
            db.execute('BEGIN')
            rows = db.execute('''SELECT r.* FROM wiki_all_runs r JOIN jobs j ON j.id=r.job_id
                ORDER BY CASE WHEN j.status IN ('queued','running') THEN 0 ELSE 1 END,
                r.created_at DESC,r.id DESC LIMIT 20''').fetchall()
            return {'runs': [self._all_dto(db, row) for row in rows]}

    def queue_all(self, source_ids=None):
        # Models are resolved only after an explicit create request, never by a
        # read/list or by a browser poll. Only the public version is persisted.
        selected = default_model_id(self.models.get_preferences())
        if not selected:
            raise ModelError('请先在系统设置选择默认模型')
        config = self.models.resolve(selected)
        from .wiki import PROMPT_VERSION, YEAR_CONTEXT_POLICY_VERSION, YEAR_CONTEXT_INSTRUCTIONS
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = self._all_rows(db, source_ids)
            scope = 'library' if source_ids is None else 'selected'
            scope_key = _digest([ALL_POLICY, scope, [[row['id'], row['revision_id']] for row in rows],
                                 SELECTION_POLICY_VERSION, _digest(SELECTION_INSTRUCTIONS)])
            existing = db.execute('''SELECT r.* FROM wiki_all_runs r JOIN jobs j ON j.id=r.job_id
                WHERE r.scope_key=? AND j.status IN ('queued','running') ORDER BY r.created_at LIMIT 1''',
                (scope_key,)).fetchone()
            if existing:
                return {'run': self._all_dto(db, existing), 'reused': True}
            published = self._published_sources(db)
            unclear = self._processed_empty_sources(db)
            active = {row['source_id'] for row in db.execute("SELECT DISTINCT source_id FROM jobs WHERE kind='wiki_build' AND status IN ('queued','running')")}
            excluded, candidates = {'built': 0, 'active': 0, 'empty': 0}, []
            for row in rows:
                readable = db.execute('SELECT text FROM blocks WHERE revision_id=?', (row['revision_id'],)).fetchall()
                reason = ('active' if row['id'] in active else 'built' if row['id'] in published
                          else 'unclear' if row['id'] in unclear
                          else 'empty' if not any(block['text'].strip() for block in readable) else None)
                if reason:
                    excluded[reason] = excluded.get(reason, 0) + 1
                else:
                    candidates.append(row)
            if not candidates:
                return {'run': None, 'reused': False}
            prompts = snapshot(db, ('wiki_compile', 'wiki_repair'))
            frozen = {'policy': ALL_POLICY, 'promptVersion': PROMPT_VERSION,
                      'yearContextPolicyVersion': YEAR_CONTEXT_POLICY_VERSION,
                      'yearContextInstructions': YEAR_CONTEXT_INSTRUCTIONS,
                      'selectionPolicyVersion': SELECTION_POLICY_VERSION,
                      'selectionInstructions': SELECTION_INSTRUCTIONS,
                      'model': {key: config.get(key) for key in ('id', 'version', 'name', 'model')},
                      'prompts': prompts}
            run_id, job_id, timestamp = ident('wiki_all'), ident('job'), now()
            payload = {'runId': run_id, 'policy': ALL_POLICY, 'retryable': False,
                       'retryBlockedReason': '请在单篇任务中重试失败资料，或重新构建待处理资料'}
            db.execute("""INSERT INTO jobs(id,batch_id,request_key,label,kind,input_json,status,stage,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'queued','queued',?,?)""",
                (job_id, ident('wiki_all_batch'), ident('wiki_all_request'), '构建全部待构建资料', 'wiki_build_all', dumps(payload), timestamp, timestamp))
            db.execute('INSERT INTO wiki_all_runs(id,job_id,scope,scope_key,scope_json,frozen_json,excluded_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
                       (run_id, job_id, scope, scope_key, dumps([[row['id'], row['revision_id']] for row in rows]), dumps(frozen), dumps(excluded), timestamp))
            db.executemany('INSERT INTO wiki_all_items(run_id,ordinal,source_id,revision_id,title) VALUES(?,?,?,?,?)',
                           [(run_id, index, row['id'], row['revision_id'], row['title']) for index, row in enumerate(candidates)])
            freeze(db, job_id, prompts)
            row = db.execute('SELECT * FROM wiki_all_runs WHERE id=?', (run_id,)).fetchone()
            return {'run': self._all_dto(db, row), 'reused': False}

    def advance_all(self):
        """Coordinator tick: never holds a worker lane while children run."""
        from .wiki import PROMPT_VERSION, YEAR_CONTEXT_POLICY_VERSION
        worked = False
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parents = db.execute("""SELECT r.*,j.status parent_status FROM wiki_all_runs r
                JOIN jobs j ON j.id=r.job_id WHERE j.status IN ('queued','running') ORDER BY r.created_at,r.id""").fetchall()
            for parent in parents:
                frozen = json.loads(parent['frozen_json'])
                selection_policy = frozen.get('selectionPolicyVersion')
                if (frozen.get('policy') != ALL_POLICY or frozen.get('promptVersion') != PROMPT_VERSION
                        or frozen.get('yearContextPolicyVersion') != YEAR_CONTEXT_POLICY_VERSION
                        or selection_policy not in (None, SELECTION_POLICY_VERSION)
                        or (selection_policy is not None and (not isinstance(frozen.get('selectionInstructions'), str)
                                                              or not frozen['selectionInstructions'].strip()))):
                    db.execute("UPDATE jobs SET status='failed',stage='unsupported',error=?,updated_at=? WHERE id=?",
                               ('Wiki 全量任务冻结协议不受支持，请重新选择范围', now(), parent['job_id']))
                    self.store._cancel_orphaned_wiki_all_children(db)
                    worked = True
                    continue
                if parent['parent_status'] == 'queued':
                    db.execute("UPDATE jobs SET status='running',stage='scheduling',attempts=attempts+1,error=NULL,updated_at=? WHERE id=?", (now(), parent['job_id']))
                    worked = True
                # Resolve the previous batch before considering the next one.
                children = db.execute("""SELECT i.ordinal,i.child_job_id,j.status child_status,j.stage child_stage
                    FROM wiki_all_items i LEFT JOIN jobs j ON j.id=i.child_job_id
                    WHERE i.run_id=? AND i.status IN ('queued','running')""", (parent['id'],)).fetchall()
                active = False
                for child in children:
                    if child['child_status'] in ('queued', 'running'):
                        active = True
                    else:
                        no_clear = child['child_status'] == 'succeeded' and child['child_stage'] == 'empty'
                        status = 'skipped' if no_clear else child['child_status'] or 'skipped'
                        db.execute('UPDATE wiki_all_items SET status=?,reason=? WHERE run_id=? AND ordinal=?',
                                   (status, 'no_clear_entries' if no_clear else None if child['child_status'] else 'source_deleted', parent['id'], child['ordinal']))
                        worked = True
                if active:
                    continue
                pending = db.execute("SELECT * FROM wiki_all_items WHERE run_id=? AND status='pending' ORDER BY ordinal", (parent['id'],)).fetchall()
                published = self._published_sources(db)
                unclear = self._processed_empty_sources(db) if selection_policy else set()
                batch = parent['batches'] + 1
                considered = 0
                for item in pending:
                    if considered >= 20:
                        break
                    considered += 1
                    current = db.execute('SELECT current_revision FROM sources WHERE id=?', (item['source_id'],)).fetchone()
                    reason = ('source_deleted' if not current else 'source_changed' if current['current_revision'] != item['revision_id']
                              else 'already_built' if item['source_id'] in published
                              else 'no_clear_entries' if item['source_id'] in unclear else None)
                    if reason is None and self._active_job(db, item['source_id']):
                        reason = 'already_active'
                    if reason:
                        db.execute("UPDATE wiki_all_items SET status='skipped',reason=?,batch=? WHERE run_id=? AND ordinal=?", (reason, batch, parent['id'], item['ordinal']))
                        worked = True
                        continue
                    model, prompts = frozen['model'], frozen['prompts']
                    signature = [item['source_id'], item['revision_id'], model['id'], model['version'],
                                 frozen['promptVersion'], frozen['yearContextPolicyVersion'], _digest(frozen['yearContextInstructions'])]
                    if selection_policy:
                        signature.extend([selection_policy, _digest(frozen['selectionInstructions'])])
                    fingerprint = _digest(intent_signature(signature, prompts))
                    previous = db.execute('''SELECT j.* FROM wiki_runs r JOIN jobs j ON j.id=r.job_id
                        WHERE r.fingerprint=? ORDER BY r.seq DESC LIMIT 1''', (fingerprint,)).fetchone()
                    if previous:
                        status = 'skipped' if previous['status'] == 'succeeded' else previous['status']
                        db.execute('UPDATE wiki_all_items SET status=?,reason=?,child_job_id=?,batch=? WHERE run_id=? AND ordinal=?',
                                   (status, 'existing_task', previous['id'], batch, parent['id'], item['ordinal']))
                        worked = True
                        continue
                    job_id, run_id, timestamp = ident('job'), ident('wiki'), now()
                    payload = {'runId': run_id, 'sourceId': item['source_id'], 'revisionId': item['revision_id'],
                               'modelId': model['id'], 'modelVersion': model['version'],
                               'yearContextPolicyVersion': frozen['yearContextPolicyVersion'],
                               'yearContextInstructions': frozen['yearContextInstructions'], 'wikiAllRunId': parent['id']}
                    if selection_policy:
                        payload.update(selectionPolicyVersion=selection_policy,
                                       selectionInstructions=frozen['selectionInstructions'])
                    db.execute("""INSERT INTO jobs(id,batch_id,request_key,label,kind,input_json,status,stage,source_id,revision_id,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,'queued','queued',?,?,?,?)""", (job_id, parent['job_id'], ident('wiki_request'), 'Wiki 构建 · ' + item['title'],
                        'wiki_build', dumps(payload), item['source_id'], item['revision_id'], timestamp, timestamp))
                    db.execute('INSERT INTO wiki_runs(id,job_id,source_id,revision_id,model_id,model_version,model_json,prompt_version,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (run_id, job_id, item['source_id'], item['revision_id'], model['id'], model['version'], dumps(model), frozen['promptVersion'], fingerprint, timestamp))
                    freeze(db, job_id, prompts)
                    db.execute("UPDATE wiki_all_items SET status='queued',child_job_id=?,owned=1,batch=? WHERE run_id=? AND ordinal=?",
                               (job_id, batch, parent['id'], item['ordinal']))
                    worked = True
                if considered:
                    db.execute('UPDATE wiki_all_runs SET batches=? WHERE id=?', (batch, parent['id']))
                counts = self._all_dto(db, parent)['counts']
                remaining = counts['pending'] + counts['queued'] + counts['running']
                if not remaining:
                    errors = counts['failed'] + counts['cancelled']
                    db.execute('UPDATE jobs SET status=?,stage=?,error=?,updated_at=? WHERE id=?',
                               ('failed' if errors else 'succeeded', 'complete',
                                f'{errors} 篇资料未完成，请在单篇任务中查看或重试' if errors else None, now(), parent['job_id']))
                    worked = True
                elif considered:
                    db.execute("UPDATE jobs SET stage=?,updated_at=? WHERE id=?", (f"wiki_all:{sum(counts.values()) - remaining}/{sum(counts.values())}", now(), parent['job_id']))
        return worked
