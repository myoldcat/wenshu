"""Durable local catalog, immutable revisions, and Chinese FTS search."""
from __future__ import annotations
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from itertools import groupby
from urllib.parse import urlsplit, urlunsplit
import jieba
from .document_metrics import BODY_COUNT_VERSION, count_body_words
from .image_metadata import library_image_eligible, raster_dimensions, ranked_candidates
from .language import LANGUAGE_POLICY_VERSION

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('WENSHU_DATA_DIR', ROOT / 'data' / 'wenshu')).resolve()
MAX_FILE = 64 * 1024 * 1024
DEFAULT_WRITING_CONCURRENCY = 3
jieba.setLogLevel(logging.ERROR)

def now():
    return datetime.now(timezone.utc).isoformat()

def ident(prefix):
    return prefix + '_' + uuid.uuid4().hex

def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

def normalized_url(value):
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('仅支持不含用户名和密码的 HTTP 或 HTTPS 网页地址')
    host = parts.hostname.encode('idna').decode().lower()
    if ':' in host:
        host = '[' + host + ']'
    port = parts.port
    netloc = host + (f':{port}' if port and port != (443 if parts.scheme.lower() == 'https' else 80) else '')
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or '/', parts.query, ''))

def tokenize(text):
    # Space-separated jieba search tokens allow SQLite's unicode61 to index Chinese.
    text = re.sub(r'\s+', ' ', text)
    return ' '.join(t.lower() for t in jieba.cut_for_search(text) if re.search(r'\w', t))

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS sources(
 id TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE, url TEXT NOT NULL DEFAULT '',
 current_revision TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 favorite INTEGER NOT NULL DEFAULT 0, read INTEGER NOT NULL DEFAULT 0,
 selected INTEGER NOT NULL DEFAULT 0, tags_json TEXT NOT NULL DEFAULT '[]',
 tags_manual INTEGER NOT NULL DEFAULT 0,
 classification_paths_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS revisions(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), blob_hash TEXT NOT NULL,
 blob_path TEXT NOT NULL, byte_size INTEGER NOT NULL, created_at TEXT NOT NULL,
 fetched_at TEXT, title TEXT NOT NULL, language TEXT NOT NULL,
 published_at TEXT, author TEXT, parser_version TEXT NOT NULL, extraction_method TEXT NOT NULL,
 summary TEXT NOT NULL, text_content TEXT NOT NULL, tables_json TEXT NOT NULL,
 warnings_json TEXT NOT NULL, final_url TEXT NOT NULL DEFAULT '',
 UNIQUE(source_id,blob_hash,parser_version)
);
CREATE TABLE IF NOT EXISTS blocks(
 id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions(id),
 ordinal INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, locator_json TEXT NOT NULL,
 UNIQUE(revision_id,ordinal)
);
CREATE INDEX IF NOT EXISTS revisions_blob_parser ON revisions(blob_hash,parser_version);
CREATE INDEX IF NOT EXISTS blocks_revision ON blocks(revision_id,ordinal);
CREATE TABLE IF NOT EXISTS document_body_metrics(
 revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
 algorithm_version INTEGER NOT NULL,
 body_word_count INTEGER NOT NULL CHECK(body_word_count>=0),
 PRIMARY KEY(revision_id,algorithm_version)
);
CREATE TABLE IF NOT EXISTS assets(
 id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions(id),
 blob_path TEXT NOT NULL, mime TEXT NOT NULL, alt TEXT NOT NULL, locator_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS assets_revision ON assets(revision_id);
CREATE TABLE IF NOT EXISTS image_dimensions(
 blob_path TEXT PRIMARY KEY, width INTEGER NOT NULL, height INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS document_blob_cleanup(
 blob_path TEXT PRIMARY KEY, created_at TEXT NOT NULL,
 next_attempt REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS revision_image_candidates(
 revision_id TEXT PRIMARY KEY REFERENCES revisions(id),
 algorithm_version INTEGER NOT NULL, candidates_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, request_key TEXT UNIQUE,
 label TEXT NOT NULL, kind TEXT NOT NULL, input_json TEXT NOT NULL,
 status TEXT NOT NULL, stage TEXT NOT NULL, error TEXT,
 source_id TEXT, revision_id TEXT, duplicate INTEGER NOT NULL DEFAULT 0,
 origin TEXT NOT NULL DEFAULT 'foreground',
 progress_hidden INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,created_at);
CREATE TABLE IF NOT EXISTS writing_activity(
 session_id TEXT PRIMARY KEY, expires_at REAL NOT NULL,
 sequence INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
 source_id UNINDEXED, title_tokens, body_tokens, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS document_chinese(
 revision_id TEXT PRIMARY KEY REFERENCES revisions(id),
 title TEXT NOT NULL, summary TEXT NOT NULL, summary_source TEXT NOT NULL,
 keywords_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS document_languages(
 revision_id TEXT PRIMARY KEY REFERENCES revisions(id),
 code TEXT NOT NULL, identification TEXT NOT NULL,
 policy_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS document_article_types(
 revision_id TEXT PRIMARY KEY REFERENCES revisions(id),
 automatic_type TEXT CHECK(automatic_type IS NULL OR automatic_type IN ('fact','opinion','technical')),
 manual_type TEXT CHECK(manual_type IS NULL OR manual_type IN ('fact','opinion','technical')),
 policy_version TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS document_language_analysis(
 revision_id TEXT PRIMARY KEY REFERENCES revisions(id),
 hint TEXT NOT NULL, version INTEGER NOT NULL,
 needs_translation INTEGER NOT NULL, detected_code TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS translated_blocks(
 revision_id TEXT NOT NULL REFERENCES revisions(id),
 block_id TEXT NOT NULL REFERENCES blocks(id), text TEXT NOT NULL,
 PRIMARY KEY(revision_id,block_id)
);
"""

class Store:
    IMAGE_CANDIDATE_VERSION = 1
    LANGUAGE_ANALYSIS_VERSION = LANGUAGE_POLICY_VERSION
    BODY_COUNT_VERSION = BODY_COUNT_VERSION

    def __init__(self, data_dir=None):
        self.root = Path(data_dir or DATA).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ('blobs', 'assets', 'staging'):
            (self.root / name).mkdir(exist_ok=True)
        self.db = self.root / 'library.sqlite3'
        # Keep jieba's optional dictionary cache inside the local data directory.
        jieba.dt.tmp_dir = str(self.root)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            from .prompts import SCHEMA as PROMPT_SCHEMA
            db.executescript(PROMPT_SCHEMA)
            if 'classification_paths_json' not in {row['name'] for row in db.execute('PRAGMA table_info(sources)')}:
                db.execute("ALTER TABLE sources ADD COLUMN classification_paths_json TEXT NOT NULL DEFAULT '[]'")
            if 'origin' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'foreground'")
            if 'progress_hidden' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN progress_hidden INTEGER NOT NULL DEFAULT 0')
            if 'policy_version' not in {row['name'] for row in db.execute('PRAGMA table_info(document_languages)')}:
                db.execute('ALTER TABLE document_languages ADD COLUMN policy_version INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE INDEX IF NOT EXISTS jobs_origin_queue ON jobs(origin,status,created_at)')
            activity_columns = {row['name'] for row in db.execute('PRAGMA table_info(writing_activity)')}
            if 'sequence' not in activity_columns:
                db.execute('ALTER TABLE writing_activity ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0')
            if 'active' not in activity_columns:
                db.execute('ALTER TABLE writing_activity ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
            # Upgrade only already-saved outputs. Never enqueue work or call a
            # provider to rebuild a derived search index.
            db.execute('BEGIN IMMEDIATE') if not db.in_transaction else None
            if not db.execute('SELECT 1 FROM schema_version WHERE version=2').fetchone():
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_outputs'").fetchone():
                    revisions = db.execute('SELECT DISTINCT r.source_id,r.revision_id FROM ai_runs r '
                        'JOIN ai_outputs o ON o.run_id=r.id WHERE o.published=1').fetchall()
                    for revision in revisions:
                        self.refresh_document_index(db, revision['source_id'], revision['revision_id'])
                db.execute('INSERT OR IGNORE INTO schema_version VALUES(2)')

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        db.create_function('url_hostname', 1, lambda value: urlsplit(value).hostname or '', deterministic=True)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=30000')
        # Windows service/sandbox TEMP directories may not be writable. Search
        # and catalog sorting must not fail when SQLite spills temporary rows.
        db.execute('PRAGMA temp_store=MEMORY')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def put_blob(self, data, kind='blobs', suffix='.html'):
        digest = hashlib.sha256(data).hexdigest()
        dest = self.root / kind / digest[:2] / (digest + suffix)
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists():
            fd, temporary = tempfile.mkstemp(dir=self.root / 'staging')
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary, dest)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return digest, str(dest.relative_to(self.root))

    def queue(self, kind, payload, label, batch_id=None, request_key=None):
        job_id = ident('job')
        timestamp = now()
        with self.connect() as db:
            if request_key:
                existing = db.execute('SELECT * FROM jobs WHERE request_key=?', (request_key,)).fetchone()
                if existing:
                    return self.job_dto(existing)
            db.execute('INSERT INTO jobs(id,batch_id,request_key,label,kind,input_json,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (job_id, batch_id or ident('batch'), request_key, label, kind, dumps(payload), 'queued', 'queued', timestamp, timestamp))
        return self.job(job_id)

    @staticmethod
    def job_dto(row):
        dto = {key: bool(row[key]) if key == 'duplicate' else row[key] for key in (
            'id','batch_id','label','kind','status','stage','source_id','revision_id','error','duplicate','attempts','created_at','updated_at','origin')}
        dto['retryable'] = row['stage'] != 'superseded' and json.loads(row['input_json']).get('retryable', True) is not False
        return dto

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            return self.job_dto(row)

    def jobs(self, limit=100, scope='all'):
        # Keep the shared worker queue while separating progress views. GLOB
        # treats the underscore literally and matches Python's writing_ prefix.
        scopes = {'all': '', 'ingest': " AND kind NOT GLOB 'writing_*'",
                  'writing': " AND kind GLOB 'writing_*'"}
        if scope not in scopes:
            raise ValueError('无效的任务范围')
        # Cleared progress hides only terminal tasks. Keep active/new tasks
        # visible and preserve durable jobs and audit records for their own APIs.
        where = (" WHERE NOT (progress_hidden=1"
                 " AND status IN ('succeeded','failed','cancelled'))" + scopes[scope])
        with self.connect() as db:
            # Active/failed tasks stay visible even during a large successful batch.
            rows = db.execute("SELECT * FROM jobs" + where + " ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'failed' THEN 2 ELSE 3 END,created_at DESC LIMIT ?", (limit,)).fetchall()
            counts = {key: 0 for key in ('queued','running','succeeded','failed','cancelled')}
            counts.update({r['status']: r['n'] for r in db.execute('SELECT status,count(*) n FROM jobs' + where + ' GROUP BY status')})
            counts['total'] = sum(counts.values())
            return {'jobs': [self.job_dto(r) for r in rows], 'counts': counts}

    def clear_writing_progress(self):
        """Hide completed writing progress without deleting tasks or audit data."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return db.execute("""UPDATE jobs SET progress_hidden=1
                WHERE kind GLOB 'writing_*' AND progress_hidden=0
                AND status IN ('succeeded','failed','cancelled')""").rowcount

    def retry(self, job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT kind,status,stage,input_json FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row['stage']=='superseded':
                raise ValueError('此任务已由新加工任务替代，请在文档中重新生成')
            if json.loads(row['input_json']).get('retryable', True) is False:
                raise ValueError(json.loads(row['input_json']).get('retryBlockedReason') or '此上传未通过校验，无法重试；请重新上传符合要求的 HTML 文件')
            if row['status'] not in ('failed','cancelled'):
                raise ValueError('只有失败或已取消的任务可以重试')
            if row['kind'].startswith('writing_') and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='writing_runs'").fetchone():
                deleted = db.execute('SELECT 1 FROM writing_runs r JOIN writing_projects p ON p.id=r.project_id WHERE r.job_id=? AND p.deleted_at IS NOT NULL', (job_id,)).fetchone()
                if deleted:
                    raise ValueError('文章已删除，无法重试此写作任务')
                active = db.execute("SELECT 1 FROM writing_runs old JOIN writing_runs current ON current.project_id=old.project_id JOIN jobs j ON j.id=current.job_id WHERE old.job_id=? AND current.job_id<>? AND j.status IN ('queued','running') LIMIT 1", (job_id, job_id)).fetchone()
                if active:
                    raise ValueError('当前项目已有写作任务，请等待完成或取消后重试')
            # An explicit retry is foreground work, even when the first attempt
            # came from idle backfill. It is no longer tied to the night window.
            payload = json.loads(row['input_json'])
            owner = payload.pop('wikiAllRunId', None)
            if owner is not None:
                # An explicit single-source retry owns its existing checkpoints
                # independently; a cancelled/completed batch cannot veto it.
                db.execute('UPDATE jobs SET input_json=? WHERE id=?', (dumps(payload), job_id))
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wiki_all_items'").fetchone():
                    db.execute('UPDATE wiki_all_items SET owned=0,status=?,reason=? WHERE child_job_id=? AND run_id=?',
                               (row['status'], 'independent_retry', job_id, owner))
            if payload.pop('automatic', None) is not None:
                db.execute('UPDATE jobs SET input_json=? WHERE id=?', (dumps(payload), job_id))
            db.execute("UPDATE jobs SET status='queued',stage='queued',origin='foreground',progress_hidden=0,error=NULL,updated_at=? WHERE id=?", (now(),job_id))
        return self.job(job_id)

    def cancel(self, job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM jobs WHERE id=?',(job_id,)).fetchone():
                raise KeyError(job_id)
            db.execute("UPDATE jobs SET status='cancelled',updated_at=? WHERE id=? AND status IN ('running','queued')", (now(),job_id))
            self._cancel_orphaned_wiki_all_children(db)
            self._cancel_orphaned_writing_children(db)
        return self.job(job_id)

    @staticmethod
    def _cancel_orphaned_wiki_all_children(db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wiki_all_runs'").fetchone():
            return
        db.execute("""UPDATE jobs SET status='cancelled',stage='cancelled',error=?,updated_at=?
            WHERE status IN ('queued','running') AND kind='wiki_build'
            AND EXISTS (SELECT 1 FROM wiki_all_items i JOIN wiki_all_runs r ON r.id=i.run_id
                JOIN jobs parent ON parent.id=r.job_id WHERE i.child_job_id=jobs.id AND i.owned=1
                AND r.id=json_extract(jobs.input_json,'$.wikiAllRunId')
                AND parent.status NOT IN ('queued','running'))""", ('所属全部构建任务已结束，步骤已取消', now()))
        db.execute("""UPDATE wiki_all_items SET status='cancelled',reason='parent_cancelled'
            WHERE status='pending' AND EXISTS (SELECT 1 FROM wiki_all_runs r JOIN jobs j ON j.id=r.job_id
                WHERE r.id=wiki_all_items.run_id AND j.status NOT IN ('queued','running'))""")

    @staticmethod
    def _cancel_orphaned_writing_children(db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='writing_runs'").fetchone():
            return
        db.execute("""UPDATE jobs SET status='cancelled',stage='cancelled',error=?,updated_at=?
            WHERE status IN ('queued','running') AND kind GLOB 'writing_*'
            AND json_extract(input_json,'$.automaticParentRunId') IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM writing_runs owner JOIN jobs parent ON parent.id=owner.job_id
                WHERE owner.id=json_extract(jobs.input_json,'$.automaticParentRunId')
                AND parent.status IN ('queued','running'))""",
            ('所属全自动写作已结束，步骤已取消', now()))

    def recover(self):
        # Only called after acquiring the single-worker OS lock.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE jobs SET status='queued',stage='queued',error='上次运行中断，已恢复排队',updated_at=? WHERE status='running' AND COALESCE(origin,'foreground')<>'preset'", (now(),))
            # Offline preset publication has its own idempotent recovery and
            # must never become an unknown task in the normal model worker.
            db.execute("UPDATE jobs SET status='failed',stage='preset_interrupted',error='预置资料添加中断，请在使用指南中重试',progress_hidden=1,updated_at=? WHERE status='running' AND origin='preset'", (now(),))
            self._cancel_orphaned_writing_children(db)
            self._cancel_orphaned_wiki_all_children(db)

    def claim(self, *, scope='all', writing_limit=DEFAULT_WRITING_CONCURRENCY, excluded_project_ids=(), allow_idle=True):
        """Atomically reserve one eligible task, keeping each article serial.

        Automatic children belong to their parent's synchronous workflow. They
        never enter a second executor lane, including after worker recovery.
        Physical tasks that have been cancelled but have not returned are
        additionally excluded by the worker's in-memory project reservations.
        """
        if scope not in ('all', 'writing', 'other'):
            raise ValueError('无效的任务领取范围')
        if isinstance(writing_limit, bool) or not isinstance(writing_limit, int) or writing_limit < 1:
            raise ValueError('写作并发上限须为正整数')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._cancel_orphaned_writing_children(db)
            self._cancel_orphaned_wiki_all_children(db)
            has_runs = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='writing_runs'").fetchone()
            join = ' LEFT JOIN writing_runs r ON r.job_id=j.id' if has_runs else ''
            project = ("COALESCE(r.project_id,json_extract(j.input_json,'$.projectId'),j.id)" if has_runs
                       else "COALESCE(json_extract(j.input_json,'$.projectId'),j.id)")
            running = db.execute('SELECT j.kind,' + project + " AS writing_project_id FROM jobs j" + join
                                 + " WHERE j.status='running'").fetchall()
            writing_projects = {r['writing_project_id'] for r in running if r['kind'].startswith('writing_')}
            writing_blocked = writing_projects | set(excluded_project_ids)
            # A cancelled request may still be inside an HTTP call. Its physical
            # reservation consumes capacity until the executor future returns.
            writing_capacity = len(writing_blocked) < writing_limit
            other_running = any(not r['kind'].startswith('writing_') and r['kind'] != 'wiki_build_all' for r in running)
            rows = db.execute('SELECT j.*,' + project + ' AS writing_project_id FROM jobs j' + join
                              + " WHERE j.status='queued' AND j.origin<>'idle'"
                              + " AND j.kind<>'wiki_build_all'"
                              + " AND (j.kind NOT GLOB 'writing_*' OR json_extract(j.input_json,'$.automaticParentRunId') IS NULL)"
                              + ' ORDER BY j.created_at,j.rowid').fetchall()
            row = None
            for candidate in rows:
                if candidate['kind'].startswith('writing_'):
                    if scope == 'other' or not writing_capacity or candidate['writing_project_id'] in writing_blocked:
                        continue
                # The coordinator explicitly reserves the serial "other"
                # lane. Legacy direct claims can still simulate a source
                # publication arriving while an older model reply is in flight.
                elif scope == 'writing' or (scope == 'other' and other_running):
                    continue
                row = candidate
                break
            if not row:
                if scope == 'writing' or (scope == 'other' and other_running) or not allow_idle:
                    return None
                from .idle_processing import idle_block_reason
                reason = idle_block_reason(db)
                if reason:
                    db.execute("UPDATE jobs SET stage='idle_paused',error=? WHERE status='queued' AND origin='idle' AND (stage<>'idle_paused' OR error IS NOT ?)", (reason, reason))
                    return None
                row = db.execute("SELECT * FROM jobs WHERE status='queued' AND origin='idle' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not row:
                return None
            initial_stage='preparing' if row['kind'].startswith(('ai_', 'writing_')) else 'fetching' if row['kind']=='url' else 'parsing'
            db.execute("UPDATE jobs SET status='running',stage=?,error=NULL,attempts=attempts+1,updated_at=? WHERE id=? AND status='queued'", (initial_stage,now(),row['id']))
            return dict(row)

    def writing_activity(self, session_id, active, sequence=None):
        from .idle_processing import LEASE_SECONDS, utc_now
        if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session_id) or not isinstance(active, bool):
            raise ValueError('写作活动标识或状态无效')
        if sequence is not None and (isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 9007199254740991):
            raise ValueError('写作活动序号无效')
        timestamp = utc_now().timestamp()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Keep tombstones after lease expiry: a delayed older active ping
            # must not undo a newer release from the same browser session.
            db.execute('DELETE FROM writing_activity WHERE expires_at<=?', (timestamp - 86400,))
            previous = db.execute('SELECT * FROM writing_activity WHERE session_id=?', (session_id,)).fetchone()
            sequence = sequence if sequence is not None else (previous['sequence'] + 1 if previous else 0)
            if previous and sequence <= previous['sequence']:
                return {'active': bool(previous['active'] and previous['expires_at'] > timestamp), 'accepted': False, 'ttlSeconds': LEASE_SECONDS}
            db.execute('INSERT OR REPLACE INTO writing_activity(session_id,expires_at,sequence,active) VALUES(?,?,?,?)',
                       (session_id, timestamp + LEASE_SECONDS, sequence, int(active)))
        return {'active': active, 'accepted': True, 'ttlSeconds': LEASE_SECONDS}

    def stage(self, job_id, stage):
        with self.connect() as db:
            db.execute("UPDATE jobs SET stage=?,updated_at=? WHERE id=? AND status='running'", (stage,now(),job_id))

    def fail(self, job_id, error):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='running'", (str(error)[:1500],now(),job_id))

    @staticmethod
    def _existing_import_revision(db, identity, digest, parser_version):
        # Preserve an established source's identity and parser-upgrade behavior.
        # In particular, equal bytes at two declared URLs remain two sources.
        source = db.execute('SELECT id FROM sources WHERE identity=?', (identity,)).fetchone()
        if source:
            return db.execute(
                'SELECT source_id,id revision_id FROM revisions '
                'WHERE source_id=? AND blob_hash=? AND parser_version=?',
                (source['id'], digest, parser_version),
            ).fetchone()
        if not identity.startswith('file:'):
            return None
        # An upload has no declared URL. The very same offline snapshot may
        # already belong to a URL/corpus/preset source; reuse it without creating
        # a second document, moving its active revision, or changing annotations.
        return db.execute(
            'SELECT s.id source_id,r.id revision_id FROM revisions r '
            'JOIN sources s ON s.id=r.source_id '
            'WHERE r.blob_hash=? AND r.parser_version=? AND s.current_revision IS NOT NULL '
            'ORDER BY (s.current_revision=r.id) DESC,s.created_at,s.id,r.created_at,r.id LIMIT 1',
            (digest, parser_version),
        ).fetchone()

    def reuse_revision(self, job, payload, raw, parser_version):
        """Finish a duplicate before parsing, without changing the active revision.

        Return True when the task is handled (duplicate or already cancelled),
        and False when the caller should parse and publish a new revision.
        The publication path repeats the check to remain safe against races.
        """
        digest = hashlib.sha256(raw).hexdigest()
        identity = 'url:' + normalized_url(payload['url']) if payload.get('url') else 'file:' + digest
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if not state or state['status'] != 'running':
                return True
            previous = self._existing_import_revision(db, identity, digest, parser_version)
            if not previous:
                return False
            db.execute(
                "UPDATE jobs SET status='succeeded',stage='ready',source_id=?,revision_id=?,duplicate=1,error=NULL,updated_at=? WHERE id=?",
                (previous['source_id'], previous['revision_id'], now(), job['id']),
            )
            return True

    def publish(self, job, payload, raw, parsed, final_url='', *, schedule_enrichment=True):
        digest, blob_path = self.put_blob(raw)
        url = normalized_url(payload['url']) if payload.get('url') else ''
        # A local upload without a declared URL is content-addressed: avoid silently
        # merging unrelated files that happen to have the same filename.
        identity = 'url:' + url if url else 'file:' + digest
        source_id, revision_id = ident('src'), ident('rev')
        title = parsed.get('title') or payload.get('title') or job['label']
        blocks = parsed.get('blocks') or [{'ordinal':i,'kind':'paragraph','text':text,'locator':{'ordinal':i}} for i,text in enumerate(parsed['body'])]
        text_content = '\n'.join(b['text'] for b in blocks)
        if len(text_content.strip()) < 30:
            raise ValueError('正文不足 30 字符，未发布到文档库；请检查网页或上传完整 HTML')
        title_tokens, body_tokens = tokenize(title), tokenize(text_content)
        asset_rows=[]
        for asset in parsed.get('images',[]):
            mime=asset['mime']; suffix={'image/png':'.png','image/jpeg':'.jpg','image/gif':'.gif','image/webp':'.webp'}.get(mime)
            if suffix:
                _, path=self.put_blob(asset['data'],kind='assets',suffix=suffix)
                asset_rows.append((ident('asset'),revision_id,path,mime,asset.get('alt',''),dumps(asset.get('locator',{}))))
        timestamp=now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if not state or state['status']!='running':
                return
            previous = self._existing_import_revision(db, identity, digest, parsed['parser_version'])
            if previous:
                db.execute("UPDATE jobs SET status='succeeded',stage='ready',source_id=?,revision_id=?,duplicate=1,error=NULL,updated_at=? WHERE id=?",
                           (previous['source_id'],previous['revision_id'],timestamp,job['id']))
                return
            # A confirmed source deletion may collect an unreferenced shared
            # blob after our initial parsing write. Recreate it under the same
            # database write lock used by collection before publishing rows.
            self.put_blob(raw)
            for image in parsed.get('images', []):
                suffix={'image/png':'.png','image/jpeg':'.jpg','image/gif':'.gif','image/webp':'.webp'}.get(image['mime'])
                if suffix:
                    self.put_blob(image['data'],kind='assets',suffix=suffix)
            source=db.execute('SELECT * FROM sources WHERE identity=?',(identity,)).fetchone()
            if source:
                source_id=source['id']
            else:
                db.execute('INSERT INTO sources(id,identity,url,created_at,updated_at,tags_json) VALUES(?,?,?,?,?,?)',
                           (source_id,identity,url,timestamp,timestamp,dumps(payload.get('tags') or ['未分类'])))
            db.execute('INSERT INTO revisions(id,source_id,blob_hash,blob_path,byte_size,created_at,fetched_at,title,language,published_at,author,parser_version,extraction_method,summary,text_content,tables_json,warnings_json,final_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (revision_id,source_id,digest,blob_path,len(raw),timestamp,payload.get('fetched_at'),title,parsed.get('language','zh'),parsed.get('published_at'),parsed.get('author'),parsed['parser_version'],parsed.get('extraction_method','html'),text_content[:260],text_content,dumps(parsed.get('tables',[])),dumps(parsed.get('warnings',[])),final_url or url))
            for ordinal,b in enumerate(blocks):
                db.execute('INSERT INTO blocks VALUES(?,?,?,?,?,?)', (f'{revision_id}_b{ordinal+1}',revision_id,ordinal,b['kind'],b['text'],dumps(b.get('locator',{}))))
            db.executemany('INSERT INTO assets VALUES(?,?,?,?,?,?)', asset_rows)
            db.execute('DELETE FROM search_index WHERE source_id=?',(source_id,))
            db.execute('INSERT INTO search_index(source_id,title_tokens,body_tokens) VALUES(?,?,?)',(source_id,title_tokens,body_tokens))
            db.execute('UPDATE sources SET current_revision=?,updated_at=? WHERE id=?',(revision_id,timestamp,source_id))
            db.execute("UPDATE jobs SET status='succeeded',stage='ready',source_id=?,revision_id=?,duplicate=0,error=NULL,updated_at=? WHERE id=?",(source_id,revision_id,timestamp,job['id']))
            # Register new-import work in the same commit as the source version.
            # A crash immediately after publication can then lose neither the
            # document nor its pending automatic processing. Existing/duplicate
            # versions return above and never backfill automatic work.
            if schedule_enrichment and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_settings'").fetchone():
                setting = db.execute("SELECT value_json FROM ai_settings WHERE key='preferences'").fetchone()
                preferences = json.loads(setting['value_json']) if setting else {}
                from .language import document_needs_translation
                automatic_translation = preferences.get('autoTranslate', True) and document_needs_translation(
                    {'title': title, 'language': parsed.get('language', ''), 'blocks': blocks})
                if preferences.get('autoProcess') is True or (automatic_translation and preferences.get('defaultModelId')):
                    outbox_payload = dumps({'sourceId': source_id, 'revisionId': revision_id})
                    db.execute("INSERT INTO jobs(id,batch_id,request_key,label,kind,input_json,status,stage,source_id,revision_id,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued','queued',?,?,?,?)",
                               (ident('job'), job.get('batch_id') or ident('batch'), 'auto:' + revision_id,
                                'AI 自动加工排队 · ' + title, 'ai_schedule', outbox_payload,
                                source_id, revision_id, timestamp, timestamp))

    @staticmethod
    def document_dto(row):
        data=dict(row)
        return dict(id=data['source_id'],title=data['title'],summary=data['summary'],
                    source=urlsplit(data['url']).hostname if data['url'] else '本地导入',url=data['url'],
                    date=data['source_created'][:10],createdAt=data['source_created'],updatedAt=data['source_updated'],
                    publishedAt=data['published_at'],fetchedAt=data['fetched_at'],author=data['author'],
                    tags=json.loads(data['tags_json']),kind='资料',favorite=bool(data['favorite']),read=bool(data['read']),selected=bool(data['selected']),
                    classificationPaths=json.loads(data['classification_paths_json']),classificationManual=bool(data['tags_manual']),
                    pending=False,status='ready',revisionId=data['revision_id'],revisionCount=data['revision_count'],
                    language=data['language'],concepts=[],table=bool(data['has_table']),
                    warnings=json.loads(data['warnings_json']),parserVersion=data['parser_version'],extractionMethod=data['extraction_method'],
                    image=f"/api/assets/{data['image_id']}" if data.get('image_id') else None,
                    coverCandidates=data.get('cover_candidates', []),
                    ruleCoverImage=data.get('cover_candidates', [{}])[0].get('url') if data.get('cover_candidates') else None)

    def set_document_language(self, db, source_id, revision_id, code, identification='model'):
        """Save source language separately from manual topics and immutable text."""
        from .language import normalize_language_code
        if not db.execute('SELECT 1 FROM revisions WHERE id=? AND source_id=?', (revision_id, source_id)).fetchone():
            return
        code = normalize_language_code(code)
        identification = 'model' if identification == 'model' else 'fallback'
        previous = db.execute('SELECT code,identification FROM document_languages WHERE revision_id=?', (revision_id,)).fetchone()
        if previous:
            # Compatibility fallbacks and uncertain later batches must not erase
            # a previously identified language for the same immutable revision.
            if previous['identification'] == 'model' and identification != 'model':
                return
            if previous['code'] not in ('und', 'other') and code in ('und', 'other'):
                return
        db.execute('INSERT INTO document_languages(revision_id,code,identification,policy_version) VALUES(?,?,?,?) '
                   'ON CONFLICT(revision_id) DO UPDATE SET code=excluded.code,identification=excluded.identification,'
                   'policy_version=excluded.policy_version', (revision_id, code, identification, LANGUAGE_POLICY_VERSION))
        db.execute('DELETE FROM document_language_analysis WHERE revision_id=?', (revision_id,))

    def set_document_article_type(self, db, source_id, revision_id, value, *, manual=False):
        """Save a purpose decision for one immutable revision, retaining overrides."""
        from .article_type import ARTICLE_TYPE_POLICY_VERSION, validate_article_type
        value = validate_article_type(value)
        if not db.execute('SELECT 1 FROM revisions WHERE id=? AND source_id=?', (revision_id, source_id)).fetchone():
            raise KeyError(source_id)
        if manual:
            db.execute('INSERT INTO document_article_types(revision_id,manual_type) VALUES(?,?) '
                       'ON CONFLICT(revision_id) DO UPDATE SET manual_type=excluded.manual_type',
                       (revision_id, value))
        else:
            db.execute('INSERT INTO document_article_types(revision_id,automatic_type,policy_version) VALUES(?,?,?) '
                       'ON CONFLICT(revision_id) DO UPDATE SET automatic_type=excluded.automatic_type,'
                       'policy_version=excluded.policy_version', (revision_id, value, ARTICLE_TYPE_POLICY_VERSION))

    def refresh_document_index(self, db, source_id, revision_id):
        """Project effective Chinese output without changing immutable evidence.

        Called inside the same publication/edit transaction. A late result may
        update its historical reader cache, but only the current revision can
        replace a source's searchable content.
        """
        revision = db.execute('SELECT r.*,s.current_revision FROM revisions r JOIN sources s ON s.id=r.source_id '
                              'WHERE r.id=? AND r.source_id=?', (revision_id, source_id)).fetchone()
        if not revision:
            return
        outputs = {}
        for row in db.execute('SELECT r.kind,r.seq,o.result_json FROM ai_runs r JOIN ai_outputs o ON o.run_id=r.id '
                              "WHERE r.revision_id=? AND r.kind IN ('translation','summary') AND o.published=1 ORDER BY r.seq",
                              (revision_id,)):
            outputs[row['kind']] = (row['seq'], json.loads(row['result_json']))
        blocks = db.execute('SELECT id,text FROM blocks WHERE revision_id=? ORDER BY ordinal', (revision_id,)).fetchall()
        allowed = {block['id'] for block in blocks}
        translations, title, summary, summary_source, keywords = {}, '', '', 'original', []
        if 'translation' in outputs:
            sequence, translated = outputs['translation']
            title = translated.get('translatedTitle', '')
            translations = {part['blockId']: part['text'] for part in translated.get('segments', []) if part.get('blockId') in allowed}
            edits = {row['block_id']: row for row in db.execute(
                'SELECT * FROM ai_translation_edits WHERE revision_id=? ORDER BY seq', (revision_id,))}
            for block_id, edit in edits.items():
                if block_id in translations and (edit['locked'] or sequence <= edit['base_run_seq']):
                    translations[block_id] = edit['text']
            summary = '\n'.join(translations[block['id']] for block in blocks if block['id'] in translations)[:260]
            if summary:
                summary_source = 'translation'
        if 'summary' in outputs:
            result = outputs['summary'][1]
            summary = result.get('summary', '')
            keywords = result.get('keywords', [])
            summary_source = 'ai_summary'
        db.execute('INSERT INTO document_chinese VALUES(?,?,?,?,?) ON CONFLICT(revision_id) DO UPDATE SET '
                   'title=excluded.title,summary=excluded.summary,summary_source=excluded.summary_source,keywords_json=excluded.keywords_json',
                   (revision_id, title, summary, summary_source, dumps(keywords)))
        db.execute('DELETE FROM translated_blocks WHERE revision_id=?', (revision_id,))
        db.executemany('INSERT INTO translated_blocks VALUES(?,?,?)',
                       [(revision_id, block_id, text) for block_id, text in translations.items()])
        if revision['current_revision'] == revision_id:
            content = '\n'.join([revision['text_content'], *translations.values(), summary, *keywords])
            db.execute('DELETE FROM search_index WHERE source_id=?', (source_id,))
            db.execute('INSERT INTO search_index(source_id,title_tokens,body_tokens) VALUES(?,?,?)',
                       (source_id, tokenize(revision['title'] + '\n' + title), tokenize(content)))

    def _asset_dimensions(self, db, rows):
        """Reuse dimensions by immutable blob, including failed header probes.

        Callers supply rows joined to image_dimensions so a warm request opens
        no image files. Repeated occurrences of one blob are probed only once,
        and immutable originals and asset associations are never changed.
        """
        known_dimensions = {}
        pending_dimensions = []
        for row in rows:
            blob_path = row['blob_path']
            if blob_path in known_dimensions:
                continue
            if row['width'] is not None:
                dimensions = row['width'], row['height']
            else:
                path = (self.root / blob_path).resolve()
                dimensions = raster_dimensions(path) if path.is_relative_to(self.root / 'assets') else (0, 0)
                pending_dimensions.append((blob_path, *dimensions))
            known_dimensions[blob_path] = dimensions
        # All filesystem probing precedes this short write transaction. Shared
        # content-addressed blobs and INSERT OR IGNORE make backfills equivalent.
        if pending_dimensions:
            db.executemany('INSERT OR IGNORE INTO image_dimensions(blob_path,width,height) VALUES(?,?,?)', pending_dimensions)
        return known_dimensions

    def _image_candidates(self, db, revision_ids):
        """Lazily inspect only requested revisions, then reuse durable results.

        Both failures and empty shortlists are cached. A page/search request does
        not rescan the catalog or reopen assets whose metadata is already known.
        Separate CREATE-IF-NOT-EXISTS tables avoid competing ALTER migrations
        when the API and Worker start together.
        """
        revision_ids = list(dict.fromkeys(revision_ids))
        if not revision_ids:
            return {}
        placeholders = ','.join('?' for _ in revision_ids)
        cached = {row['revision_id']: json.loads(row['candidates_json']) for row in db.execute(
            'SELECT revision_id,candidates_json FROM revision_image_candidates '
            f'WHERE algorithm_version=? AND revision_id IN ({placeholders})',
            (self.IMAGE_CANDIDATE_VERSION, *revision_ids),
        )}
        missing = [revision for revision in revision_ids if revision not in cached]
        if not missing:
            return cached
        placeholders = ','.join('?' for _ in missing)
        rows = db.execute(
            'SELECT a.rowid asset_order,a.id,a.revision_id,a.blob_path,a.locator_json,d.width,d.height '
            'FROM assets a LEFT JOIN image_dimensions d ON d.blob_path=a.blob_path '
            f'WHERE a.revision_id IN ({placeholders}) ORDER BY a.revision_id,a.rowid', missing,
        ).fetchall()
        known_dimensions = self._asset_dimensions(db, rows)
        by_revision = {revision: [] for revision in missing}
        for row in rows:
            dimensions = known_dimensions[row['blob_path']]
            assets = by_revision[row['revision_id']]
            # Historical assets already carry their document order in locator.
            # Malformed/missing legacy locators safely fall back to import order.
            try:
                ordinal = json.loads(row['locator_json']).get('ordinal', len(assets))
            except (ValueError, AttributeError, TypeError):
                ordinal = len(assets)
            if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
                ordinal = len(assets)
            assets.append({'assetId': row['id'], 'url': '/api/assets/' + row['id'],
                           'width': dimensions[0], 'height': dimensions[1], 'ordinal': ordinal})
        computed = {revision: ranked_candidates(assets) for revision, assets in by_revision.items()}
        db.executemany(
            'INSERT INTO revision_image_candidates(revision_id,algorithm_version,candidates_json) VALUES(?,?,?) '
            'ON CONFLICT(revision_id) DO UPDATE SET algorithm_version=excluded.algorithm_version,candidates_json=excluded.candidates_json',
            [(revision, self.IMAGE_CANDIDATE_VERSION, dumps(candidates)) for revision, candidates in computed.items()],
        )
        return {**cached, **computed}

    def image_candidates(self, revision_id):
        """Return safe local candidates for a particular immutable revision."""
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM revisions WHERE id=?', (revision_id,)).fetchone():
                raise KeyError(revision_id)
            return self._image_candidates(db, [revision_id])[revision_id]

    def _language_facts(self, db, rows, languages):
        """Reuse exact original-block decisions in lists and the reader alike."""
        from .language import detect_language, document_needs_translation, document_language_text, normalize_language_code
        hints = {row['revision_id']: (languages[row['revision_id']]['code'] if row['revision_id'] in languages
                                     else normalize_language_code(row['language'])) for row in rows}
        cached = {row['revision_id']: dict(row) for row in db.execute(
            'SELECT * FROM document_language_analysis WHERE revision_id IN (SELECT value FROM json_each(?))',
            (dumps(list(hints)),)) if row['version'] == self.LANGUAGE_ANALYSIS_VERSION and row['hint'] == hints[row['revision_id']]}
        missing = {row['revision_id']: row for row in rows if row['revision_id'] not in cached}
        blocks = {revision: [] for revision in missing}
        if missing:
            for block in db.execute('SELECT revision_id,kind,text FROM blocks '
                                    'WHERE revision_id IN (SELECT value FROM json_each(?)) ORDER BY revision_id,ordinal',
                                    (dumps(list(missing)),)):
                blocks[block['revision_id']].append(dict(block))
        for revision, row in missing.items():
            hint = hints[revision]
            original = {'originalTitle': row['title'], 'languageCode': hint, 'blocks': blocks[revision]}
            identified = languages.get(revision)
            if identified:
                original.update(languageIdentification=identified['identification'], languagePolicyVersion=identified['policy_version'])
            needed = document_needs_translation(original)
            text = document_language_text(original) or row['title']
            detected = normalize_language_code(detect_language(text, hint))
            if identified and identified['identification'] == 'model' and identified['policy_version'] == LANGUAGE_POLICY_VERSION:
                detected = hint
            cached[revision] = {'needs_translation': needed, 'detected_code': detected}
            db.execute('INSERT INTO document_language_analysis VALUES(?,?,?,?,?) ON CONFLICT(revision_id) DO UPDATE SET '
                       'hint=excluded.hint,version=excluded.version,needs_translation=excluded.needs_translation,detected_code=excluded.detected_code',
                       (revision, hint, self.LANGUAGE_ANALYSIS_VERSION, int(needed), detected))
        return cached

    def _body_word_counts(self, db, revision_ids):
        """Lazily derive only requested immutable revisions, then reuse counts."""
        revision_ids = list(dict.fromkeys(revision_ids))
        cached = {row['revision_id']: row['body_word_count'] for row in db.execute(
            'SELECT revision_id,body_word_count FROM document_body_metrics '
            'WHERE algorithm_version=? AND revision_id IN (SELECT value FROM json_each(?))',
            (self.BODY_COUNT_VERSION, dumps(revision_ids)))}
        missing = [revision for revision in revision_ids if revision not in cached]
        if missing:
            counts = dict.fromkeys(missing, 0)
            blocks = db.execute('SELECT revision_id,kind,text FROM blocks '
                                'WHERE revision_id IN (SELECT value FROM json_each(?)) '
                                'ORDER BY revision_id,ordinal', (dumps(missing),))
            for revision, items in groupby(blocks, lambda block: block['revision_id']):
                counts[revision] = count_body_words(dict(block) for block in items)
            db.executemany('INSERT OR IGNORE INTO document_body_metrics '
                           '(revision_id,algorithm_version,body_word_count) VALUES(?,?,?)',
                           ((revision, self.BODY_COUNT_VERSION, count) for revision, count in counts.items()))
            cached.update(counts)
        return cached

    def _document_dtos(self, db, rows):
        from .card_cover import cover_dtos
        from .language import normalize_language_code, language_label, language_tag
        from .article_type import article_type_label
        candidates = self._image_candidates(db, [row['revision_id'] for row in rows])
        covers = cover_dtos(self, [row['revision_id'] for row in rows], db=db)
        chinese = {item['revision_id']: item for item in db.execute(
            'SELECT * FROM document_chinese WHERE revision_id IN (SELECT value FROM json_each(?))',
            (dumps([row['revision_id'] for row in rows]),))}
        languages = {item['revision_id']: item for item in db.execute(
            'SELECT * FROM document_languages WHERE revision_id IN (SELECT value FROM json_each(?))',
            (dumps([row['revision_id'] for row in rows]),))}
        article_types = {item['revision_id']: item for item in db.execute(
            'SELECT * FROM document_article_types WHERE revision_id IN (SELECT value FROM json_each(?))',
            (dumps([row['revision_id'] for row in rows]),))}
        language_facts = self._language_facts(db, rows, languages)
        body_counts = self._body_word_counts(db, [row['revision_id'] for row in rows])
        documents = []
        for row in rows:
            dto = self.document_dto({**dict(row), 'cover_candidates': candidates[row['revision_id']]})
            dto['bodyWordCount'] = body_counts[row['revision_id']]
            purpose = article_types.get(row['revision_id'])
            manual_type = purpose['manual_type'] if purpose else None
            article_type = manual_type if manual_type is not None else purpose['automatic_type'] if purpose else None
            dto.update(articleType=article_type, articleTypeLabel=article_type_label(article_type),
                       articleTypeManual=manual_type is not None)
            identified = languages.get(row['revision_id'])
            code = identified['code'] if identified else normalize_language_code(dto['language'])
            facts = language_facts[row['revision_id']]
            needed = bool(facts['needs_translation'])
            if code == 'mixed' and (not identified or identified['policy_version'] < LANGUAGE_POLICY_VERSION):
                code = facts['detected_code']
            if not needed and facts['detected_code'] == 'zh':
                code = 'zh'
            elif needed and code in ('zh', 'und'):
                code = facts['detected_code'] if facts['detected_code'] not in ('zh', 'und') else 'mixed'
            dto.update(languageCode=code, languageLabel=language_label(code), languageTag=language_tag(code),
                       languageIdentification=identified['identification'] if identified else 'parser',
                       languagePolicyVersion=identified['policy_version'] if identified else LANGUAGE_POLICY_VERSION)
            dto.update(originalTitle=dto['title'], originalSummary=dto['summary'], summarySource='original',
                       summaryLanguage=code, needsTranslation=needed)
            localized = chinese.get(row['revision_id'])
            if localized:
                if dto['needsTranslation']:
                    dto['title'] = localized['title'] or dto['title']
                if localized['summary'] and (dto['needsTranslation'] or localized['summary_source'] == 'ai_summary'):
                    dto.update(summary=localized['summary'], summarySource=localized['summary_source'], summaryLanguage='zh-CN')
                dto['keywords'] = json.loads(localized['keywords_json'])
            dto.update(covers[row['revision_id']])
            if not candidates[row['revision_id']] and dto['cardCover']['status'] == 'not_processed':
                dto['cardCover']['status'] = 'no_candidates'
            documents.append(dto)
        return documents

    # Lists and search never pull full article text or table JSON into their
    # sort rows. The reader fetches those independently through DETAIL_SELECT.
    SELECT_FIELDS="""s.id source_id,s.url,s.created_at source_created,s.updated_at source_updated,s.favorite,s.read,s.selected,s.tags_json,s.tags_manual,s.classification_paths_json,
      r.id revision_id,r.title,r.summary,r.published_at,r.fetched_at,r.author,r.language,r.warnings_json,r.parser_version,r.extraction_method,
      (r.tables_json!='[]') has_table,(SELECT count(*) FROM revisions rv WHERE rv.source_id=s.id) revision_count,
      (SELECT id FROM assets a WHERE a.revision_id=r.id LIMIT 1) image_id"""
    SELECT_FROM=' FROM sources s JOIN revisions r ON r.id=s.current_revision'
    SELECT='SELECT '+SELECT_FIELDS+SELECT_FROM
    DETAIL_SELECT='SELECT '+SELECT_FIELDS+',r.tables_json,r.blob_hash,r.byte_size'+SELECT_FROM

    def documents(self,limit=500,offset=0):
        with self.connect() as db:
            total=db.execute('SELECT count(*) FROM sources WHERE current_revision IS NOT NULL').fetchone()[0]
            rows=db.execute(self.SELECT+' ORDER BY s.created_at DESC,s.id LIMIT ? OFFSET ?',(limit,offset)).fetchall()
            return {'documents':self._document_dtos(db, rows),'total':total}

    def images(self,q='',limit=48,offset=0,source_id=None):
        """List usable illustrations from published current revisions.

        An asset belongs to a source and immutable revision even when several
        articles share its content-addressed image file. Keep every association
        that passes the library rule, independently of the card-cover shortlist.
        Historical dimensions are lazily cached only within the requested scope.
        """
        joined=(' FROM sources s JOIN revisions r ON r.id=s.current_revision AND r.source_id=s.id '
                'JOIN assets a ON a.revision_id=r.id '
                'LEFT JOIN document_chinese c ON c.revision_id=r.id '
                'LEFT JOIN image_dimensions d ON d.blob_path=a.blob_path')
        clauses=[]; args=[]
        if source_id is not None:
            clauses.append('s.id=?');args.append(source_id)
        query=q.strip()
        if query:
            # LIKE receives a literal substring; %, _ and backslash never
            # become user-provided wildcard syntax.
            pattern='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            clauses.append("(r.title LIKE ? ESCAPE '\\' OR c.title LIKE ? ESCAPE '\\' OR a.alt LIKE ? ESCAPE '\\')")
            args.extend([pattern]*3)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        with self.connect() as db:
            # One lightweight association query is a consistent catalog snapshot.
            # Filter it before counting or slicing so an icon between two usable
            # illustrations cannot leave page holes or inflate either count.
            rows=db.execute(
                "SELECT a.id,a.mime,a.alt,a.locator_json,a.blob_path,d.width,d.height,s.id source_id,r.id revision_id,"
                "COALESCE(NULLIF(c.title,''),r.title) document_title,r.title original_title,s.url,s.created_at,"
                '(SELECT count(*) FROM assets earlier WHERE earlier.revision_id=a.revision_id '
                'AND earlier.rowid<a.rowid) saved_ordinal'+joined+where+
                ' ORDER BY s.created_at DESC,s.id,a.rowid',args).fetchall()
            dimensions=self._asset_dimensions(db,rows)
            eligible=[row for row in rows if library_image_eligible(*dimensions[row['blob_path']])]
            total=len(eligible)
            document_count=len({row['source_id'] for row in eligible})
            rows=eligible[offset:offset+limit]
        items=[]
        for row in rows:
            ordinal=row['saved_ordinal']
            try:
                located=json.loads(row['locator_json']).get('ordinal')
                if isinstance(located,int) and not isinstance(located,bool) and located>=0:
                    ordinal=located
            except (ValueError,AttributeError,TypeError):
                pass
            items.append({'id':row['id'],'url':'/api/assets/'+row['id'],'alt':row['alt'],'mime':row['mime'],
                          'width':dimensions[row['blob_path']][0],'height':dimensions[row['blob_path']][1],
                          'sourceId':row['source_id'],'revisionId':row['revision_id'],
                          'documentTitle':row['document_title'],'originalTitle':row['original_title'],
                          'source':urlsplit(row['url']).hostname if row['url'] else '本地导入',
                          'date':row['created_at'][:10],'ordinal':ordinal})
        return {'items':items,'total':total,'documentCount':document_count,
                'limit':limit,'offset':offset}

    def detail(self,source_id,revision_id=None):
        with self.connect() as db:
            sql=self.DETAIL_SELECT
            if revision_id:
                sql=sql.replace('r.id=s.current_revision','r.source_id=s.id AND r.id=?')
                args=(revision_id,source_id)
            else:
                args=(source_id,)
            row=db.execute(sql+' WHERE s.id=?',args).fetchone()
            if not row:
                raise KeyError(source_id)
            dto=self._document_dtos(db, [row])[0]
            blocks=[{'id':b['id'],'ordinal':b['ordinal'],'kind':b['kind'],'text':b['text'],'locator':json.loads(b['locator_json'])} for b in db.execute('SELECT * FROM blocks WHERE revision_id=? ORDER BY ordinal',(row['revision_id'],))]
            dto.update(body=[b['text'] for b in blocks],blocks=blocks,tables=json.loads(row['tables_json']),originalUrl=f'/api/documents/{source_id}/original?revision_id={row["revision_id"]}',previewUrl=f'/api/documents/{source_id}/preview?revision_id={row["revision_id"]}',byteSize=row['byte_size'],blobHash=row['blob_hash'])
            dto['tableData']=dto['tables'][0] if dto['tables'] else []
            dto['assets']=[{'id':a['id'],'url':'/api/assets/'+a['id'],'alt':a['alt'],'mime':a['mime']} for a in db.execute('SELECT * FROM assets WHERE revision_id=?',(row['revision_id'],))]
            image_rows=db.execute(
                'SELECT a.id,a.mime,a.alt,a.blob_path,d.width,d.height FROM assets a '
                'LEFT JOIN image_dimensions d ON d.blob_path=a.blob_path '
                'WHERE a.revision_id=? ORDER BY a.rowid',(row['revision_id'],)).fetchall()
            dimensions=self._asset_dimensions(db,image_rows)
            dto['imageAssets']=[{'id':a['id'],'url':'/api/assets/'+a['id'],'alt':a['alt'],'mime':a['mime'],
                                 'width':dimensions[a['blob_path']][0],'height':dimensions[a['blob_path']][1]}
                                for a in image_rows if library_image_eligible(*dimensions[a['blob_path']])]
            dto['revisions']=[{'id':r['id'],'createdAt':r['created_at'],'title':r['title'],'hash':r['blob_hash']} for r in db.execute('SELECT id,created_at,title,blob_hash FROM revisions WHERE source_id=? ORDER BY created_at DESC',(source_id,))]
            return dto

    def patch(self,source_id,changes):
        from .classification import canonicalize_manual_paths, path_tags
        pairs=[]; values=[]
        for field in ('favorite','read','selected'):
            if field in changes:
                pairs.append(field+'=?'); values.append(int(changes[field]))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT tags_json,classification_paths_json,current_revision FROM sources WHERE id=?',(source_id,)).fetchone()
            if not row:
                raise KeyError(source_id)
            if 'articleType' in changes:
                from .article_type import ArticleTypeRevisionConflict
                if not changes.get('revisionId'):
                    raise ValueError('修改文章性质须提供当前原文版本 revisionId')
                if changes['revisionId'] != row['current_revision']:
                    raise ArticleTypeRevisionConflict('原文版本已改变，请刷新后重新设置文章性质')
                self.set_document_article_type(db, source_id, row['current_revision'], changes['articleType'], manual=True)
            old_paths=json.loads(row['classification_paths_json'])
            paths=canonicalize_manual_paths(changes['classificationPaths']) if 'classificationPaths' in changes else old_paths
            if 'classificationPaths' in changes:
                pairs.append('classification_paths_json=?');values.append(dumps(paths))
            if 'tags' in changes or 'classificationPaths' in changes:
                # Path-derived tags cannot drift from the saved hierarchy. Retain
                # auxiliary tags when paths change, and remove obsolete path names.
                old_names=set(path_tags(old_paths))
                auxiliary=changes.get('tags',[t for t in json.loads(row['tags_json']) if t not in old_names])
                tags=list(dict.fromkeys([*path_tags(paths),*(t.strip() for t in auxiliary if t.strip())]))
                pairs.extend(['tags_json=?','tags_manual=1']);values.append(dumps(tags))
            if pairs:
                db.execute('UPDATE sources SET '+','.join(pairs)+' WHERE id=?',(*values,source_id))
        return self.detail(source_id)

    def search(self,q='',limit=100,offset=0,category='',source='',filter='all',since='',sort='relevance',source_ids=None):
        tokens=list(dict.fromkeys(tokenize(q).split()))[:40]
        # Treat the query as plain text, not executable FTS syntax.
        expression=' AND '.join('"'+t.replace('"','""')+'"' for t in tokens)
        clauses=[]; args=[]; join=''
        if expression:
            join=' JOIN search_index f ON f.source_id=s.id'
            clauses.append('search_index MATCH ?');args.append(expression)
        if source_ids is not None:
            # One JSON parameter avoids SQLite's binding limit for a large folder.
            clauses.append('s.id IN (SELECT value FROM json_each(?))');args.append(dumps(source_ids))
        elif category=='未分类':
            clauses.append("(json_array_length(s.tags_json)=0 OR EXISTS(SELECT 1 FROM json_each(s.tags_json) WHERE value='未分类'))")
        elif category and category not in ('全部','全部分类'):
            clauses.append('EXISTS(SELECT 1 FROM json_each(s.tags_json) WHERE value=?)');args.append(category)
        if source and source not in ('全部','全部来源'):
            if source=='本地导入':clauses.append("s.url=''")
            else:clauses.append('url_hostname(s.url)=?');args.append(source.strip().lower())
        if filter=='favorite':clauses.append('s.favorite=1')
        if filter=='unread':clauses.append('s.read=0')
        if since:clauses.append('s.created_at>=?');args.append(since)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        order='bm25(search_index,0.0,5.0,1.0),s.created_at DESC' if expression and sort=='relevance' else ('r.title COLLATE NOCASE,s.id' if sort=='title' else 's.created_at DESC,s.id')
        with self.connect() as db:
            total=db.execute('SELECT count(*) FROM sources s JOIN revisions r ON r.id=s.current_revision'+join+where,args).fetchone()[0]
            rows=db.execute(self.SELECT+join+where+' ORDER BY '+order+' LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
            results=[]
            for r, dto in zip(rows, self._document_dtos(db, rows)):
                # Chinese hits keep the original block ID so evidence links and
                # comparison locate the same immutable source paragraph.
                blocks=[dict(b) for b in db.execute("SELECT block_id id,text,'zh-CN' language FROM translated_blocks WHERE revision_id=?",
                                                    (r['revision_id'],))]
                blocks += [dict(b, language=r['language']) for b in db.execute('SELECT id,text FROM blocks WHERE revision_id=? ORDER BY ordinal',(r['revision_id'],))]
                scored=[(sum(t.lower() in b['text'].lower() for t in tokens),b) for b in blocks]
                score,best=max(scored,key=lambda item:item[0],default=(0,None))
                if score==0:best=None
                snippet=best['text'] if best else dto['summary']
                pos=min((snippet.lower().find(t.lower()) for t in tokens if t.lower() in snippet.lower()),default=0)
                start=max(0,pos-70)
                dto.update(snippet=('…' if start else '')+snippet[start:start+240],matchBlockId=best['id'] if best else None,
                           matchLanguage=best['language'] if best else dto['summaryLanguage'])
                results.append(dto)
        return {'results':results,'total':total,'mode':'fulltext','query':q}

    def original(self,source_id,revision_id=None):
        with self.connect() as db:
            row=db.execute('SELECT r.blob_path,r.title FROM sources s JOIN revisions r ON r.id=coalesce(?,s.current_revision) AND r.source_id=s.id WHERE s.id=?',(revision_id,source_id)).fetchone()
            if not row:raise KeyError(source_id)
            return self.root/row['blob_path'],row['title']

    def preview_source(self,source_id,revision_id=None,*,include_assets=False):
        """Resolve exactly one owned revision without materializing article text.

        HEAD uses this inexpensive existence check; GET additionally selects only
        that revision's saved raster assets. Never resolve URLs or files outside
        immutable local storage during a preview.
        """
        with self.connect() as db:
            row=db.execute('SELECT r.id,r.blob_path FROM sources s JOIN revisions r ON r.id=coalesce(?,s.current_revision) AND r.source_id=s.id WHERE s.id=?',(revision_id,source_id)).fetchone()
            if not row:raise KeyError(source_id)
            path=(self.root/row['blob_path']).resolve()
            if not path.is_relative_to(self.root/'blobs') or not path.is_file():
                raise KeyError(source_id)
            assets=[]
            if include_assets:
                for asset in db.execute('SELECT blob_path,mime,locator_json FROM assets WHERE revision_id=?',(row['id'],)):
                    asset_path=(self.root/asset['blob_path']).resolve()
                    if asset_path.is_relative_to(self.root/'assets'):
                        assets.append({'path':asset_path,'mime':asset['mime'],'locator_json':asset['locator_json']})
            return path,assets

    def asset(self,asset_id):
        with self.connect() as db:
            row=db.execute('SELECT blob_path,mime FROM assets WHERE id=?',(asset_id,)).fetchone()
            if not row:raise KeyError(asset_id)
            return self.root/row['blob_path'],row['mime']
