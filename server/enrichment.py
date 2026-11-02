"""Version-bound AI processing; model calls never hold a database transaction.

Outputs are immutable. A separate edit layer protects human translations, and
the job attempt plus run sequence fences late workers at every checkpoint.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
import hashlib
import json
import math
import re
import time
from .store import dumps, ident, now
from . import classification as classification_rules
from .classification import CATEGORY_NAMES, CATEGORIES, TAXONOMY_VERSION
from .article_type import (ARTICLE_TYPE_POLICY_VERSION, ARTICLE_TYPE_INSTRUCTIONS, ARTICLE_TYPES,
                           classification_article_type)
from .model_gateway import default_model_id, model_request_guard
from .idle_processing import IdlePaused, idle_block_reason
from .language import (LANGUAGE_POLICY_VERSION, SOURCE_LANGUAGE_CODES, detect_language,
                       document_language_text, document_needs_translation, language_shares, language_weight,
                       merge_source_languages, needs_translation, normalize_language_code,
                       source_language_from_shares)
from .prompts import (ENRICHMENT_IDS, snapshot as prompt_snapshot, intent_signature, freeze,
                      job_prompts, prompt_text, compiled_snapshot_text)

KINDS = ('summary', 'translation', 'classification')
PROMPT_VERSION = 'wenshu-content-1.5'
TRANSLATION_PROMPT_VERSION = 'wenshu-translation-1.6'
NORMALIZER_VERSION = 'opencc-t2s-1.4.2'
SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_runs(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id), source_id TEXT NOT NULL REFERENCES sources(id),
 revision_id TEXT NOT NULL REFERENCES revisions(id), kind TEXT NOT NULL,
 model_id TEXT NOT NULL, model_version INTEGER NOT NULL, model_json TEXT NOT NULL,
 prompt_version TEXT NOT NULL, target_language TEXT NOT NULL,
 fingerprint TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_run_object ON ai_runs(source_id,revision_id,kind,seq);
CREATE TABLE IF NOT EXISTS ai_checkpoints(
 run_id TEXT NOT NULL REFERENCES ai_runs(id), checkpoint_key TEXT NOT NULL,
 input_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,checkpoint_key)
);
CREATE TABLE IF NOT EXISTS ai_outputs(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES ai_runs(id),
 result_json TEXT NOT NULL, published INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_calls(
 run_id TEXT NOT NULL REFERENCES ai_runs(id), checkpoint_key TEXT NOT NULL,
 attempt INTEGER NOT NULL, duration_ms INTEGER NOT NULL, usage_json TEXT NOT NULL,
 observed_model TEXT NOT NULL, validation_status TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,checkpoint_key,attempt)
);
CREATE TABLE IF NOT EXISTS ai_translation_edits(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL REFERENCES revisions(id),
 block_id TEXT NOT NULL REFERENCES blocks(id), text TEXT NOT NULL,
 locked INTEGER NOT NULL, base_run_seq INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_translation_edit_object ON ai_translation_edits(revision_id,block_id,seq);
"""


class Superseded(Exception):
    """An old or cancelled attempt is no longer allowed to publish."""


class AutoTranslationDisabled(Exception):
    """Stop automatic translation before the next outbound model request."""


def prompt_version(kind):
    """Version each changed processing contract without invalidating summaries."""
    if kind == 'classification':
        return classification_rules.PROMPT_VERSION
    return TRANSLATION_PROMPT_VERSION if kind == 'translation' else PROMPT_VERSION


def taxonomy():
    return {'version': TAXONOMY_VERSION, 'levels': 3, 'maxPaths': classification_rules.MAX_PATHS,
            'categories': [{'id': key, 'name': name, 'scope': classification_rules.CATEGORY_SCOPE.get(key, '')}
                           for key, name in CATEGORY_NAMES]}


def _hash(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def _text(value, label, maximum=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} 必须为非空文本且不超过 {maximum} 字符')
    return value.strip()


def _refs(value, allowed):
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or x not in allowed for x in value):
        raise ValueError('AI 结果包含缺失或未知的原文块引用，未发布')
    return list(dict.fromkeys(value))


def _json(text):
    if not isinstance(text, str):
        raise ValueError('模型响应不是文本')
    # A JSON-only fenced response is safe to unwrap; prose or partial JSON is not.
    text = text.strip()
    if text.startswith('```'):
        match = re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            text = match.group(1)
    try:
        value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限数字')))
    except (ValueError, TypeError) as exc:
        raise ValueError('模型输出不是完整有效的 JSON，未发布') from exc
    if not isinstance(value, dict):
        raise ValueError('模型 JSON 必须为对象')
    return value


@lru_cache(maxsize=1)
def _simplifier():
    from opencc import OpenCC
    return OpenCC('t2s')


def _simplified(text):
    # Applied only to generated text. Original evidence and human edits are
    # intentionally outside this normalization path.
    return _simplifier().convert(text)


class EnrichmentService:
    def __init__(self, store, model_service=None):
        self.store = store
        if model_service is None:
            from .model_gateway import ModelService
            model_service = ModelService(store)
        self.models = model_service
        with store.connect() as db:
            db.executescript(SCHEMA)

    def queue(self, source_id, kinds=None, force=False, model_id=None, target_language='zh-CN', revision_id=None, schedule_job=None, origin='foreground', automatic=False, missing_only=False):
        if origin not in ('foreground', 'idle'):
            raise ValueError('无效的加工来源')
        defaults = [kind for kind in KINDS if kind != 'classification' or CATEGORIES]
        kinds = list(dict.fromkeys(kinds if kinds is not None else defaults))
        if not kinds or any(kind not in KINDS for kind in kinds):
            raise ValueError('加工类型只允许 summary、translation、classification')
        if 'classification' in kinds and not CATEGORIES:
            raise ValueError('暂无可用的分类体系，可先手动整理资料')
        if target_language != 'zh-CN':
            raise ValueError('当前仅支持翻译为简体中文 zh-CN')
        document = self.store.detail(source_id)
        if revision_id is not None and revision_id != document['revisionId']:
            raise ValueError('阅读中的来源版本已过期，请刷新后重新发起加工')
        preferences = self.models.get_preferences()
        configurations = {}
        for kind in kinds:
            selected = (model_id.get(kind) if isinstance(model_id, dict) else model_id) or default_model_id(preferences, kind)
            if not selected:
                raise ValueError('请先在系统设置绑定默认模型')
            # Idle discovery must not decrypt before there is a durable task:
            # credential failures belong to process() and stay visible once,
            # instead of being retried invisibly by each scheduler tick.
            snapshot = getattr(self.models, 'snapshot', self.models.resolve) if origin == 'idle' else self.models.resolve
            configurations[kind] = snapshot(selected)
        jobs = []
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if origin == 'idle' and (idle_block_reason(db) or db.execute(
                    "SELECT 1 FROM jobs WHERE origin='idle' AND status IN ('queued','running') LIMIT 1").fetchone()):
                return []
            if schedule_job is not None:
                parent = db.execute('SELECT status,attempts FROM jobs WHERE id=?', (schedule_job['id'],)).fetchone()
                if not parent or parent['status'] != 'running' or parent['attempts'] != schedule_job['attempts'] + 1:
                    raise Superseded()
            current = db.execute('SELECT current_revision FROM sources WHERE id=?', (source_id,)).fetchone()
            if not current or current['current_revision'] != document['revisionId'] or (revision_id and current['current_revision'] != revision_id):
                raise ValueError('来源版本刚发生变化，请重新发起加工')
            batch = ident('ai_batch')
            for kind in kinds:
                # Backfill only absent capabilities, independent of model or
                # prompt upgrades. Failed/cancelled intents require an explicit
                # retry; changing defaults must never trigger endless retries.
                if (origin == 'idle' or missing_only) and (db.execute(
                        'SELECT 1 FROM ai_runs WHERE source_id=? AND revision_id=? AND kind=? LIMIT 1',
                        (source_id, document['revisionId'], kind)).fetchone() or
                        self._manual_capability(db, source_id, document['revisionId'], kind)):
                    continue
                config = configurations[kind]
                version = prompt_version(kind)
                prompts = prompt_snapshot(db, ENRICHMENT_IDS[kind])
                public_model = {key: config.get(key) for key in ('id', 'version', 'name', 'model')}
                signature = [source_id, document['revisionId'], kind, config['id'], config['version'], version, target_language, TAXONOMY_VERSION]
                if kind == 'classification':
                    article_type_instructions = compiled_snapshot_text('classification_article_type', prompts['classification_article_type'])
                    signature += [ARTICLE_TYPE_POLICY_VERSION, _hash(article_type_instructions)]
                signature_prompts = {key: item for key, item in prompts.items()
                                     if key != 'classification_article_type' or item['version'] > 1}
                fingerprint = _hash(intent_signature(signature, signature_prompts))
                existing = None if force else db.execute(
                    'SELECT j.* FROM ai_runs r JOIN jobs j ON j.id=r.job_id WHERE r.fingerprint=? ORDER BY r.seq DESC LIMIT 1', (fingerprint,)).fetchone()
                if existing:
                    if not automatic and origin != 'idle' and existing['status'] in ('queued', 'running'):
                        existing_payload = json.loads(existing['input_json'])
                        existing_payload.pop('automatic', None)
                        db.execute("UPDATE jobs SET origin='foreground',input_json=?,label=?,error=NULL,updated_at=? WHERE id=?",
                                   (dumps(existing_payload), existing['label'].removeprefix('闲时处理 · '), now(), existing['id']))
                        existing = db.execute('SELECT * FROM jobs WHERE id=?', (existing['id'],)).fetchone()
                    jobs.append(self.store.job_dto(existing))
                    continue
                job_id, run_id, timestamp = ident('job'), ident('ai'), now()
                payload = {'runId': run_id, 'sourceId': source_id, 'revisionId': document['revisionId'],
                           'kind': kind, 'modelId': config['id'], 'modelVersion': config['version'], 'targetLanguage': target_language}
                if automatic or origin == 'idle':
                    payload['automatic'] = True
                if kind == 'classification':
                    payload['taxonomyVersion'] = TAXONOMY_VERSION
                    # New jobs opt in without altering old prompt snapshots,
                    # request hashes or validated classification checkpoints.
                    payload['articleTypePolicyVersion'] = ARTICLE_TYPE_POLICY_VERSION
                    payload['articleTypeInstructions'] = article_type_instructions
                    payload['articleTypePromptVersion'] = prompts['classification_article_type']['version']
                    # Freeze reusable paths with this intent. Retry/checkpoint
                    # inputs cannot drift when other documents are classified.
                    payload['existingPaths'] = classification_rules.existing_paths(db)
                label = dict(summary='AI 摘要', translation='中文翻译', classification='自动分类')[kind] + ' · ' + document['title']
                if origin == 'idle':
                    label = '闲时处理 · ' + label
                db.execute("INSERT INTO jobs(id,batch_id,request_key,label,kind,input_json,status,stage,source_id,revision_id,created_at,updated_at,origin) VALUES(?,?,?,?,?,?,'queued','queued',?,?,?,?,?)",
                           (job_id, batch, ident('ai_request'), label, 'ai_' + kind, dumps(payload), source_id, document['revisionId'], timestamp, timestamp, origin))
                db.execute('INSERT INTO ai_runs(id,job_id,source_id,revision_id,kind,model_id,model_version,model_json,prompt_version,target_language,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (run_id, job_id, source_id, document['revisionId'], kind, config['id'], config['version'], dumps(public_model), version, target_language, fingerprint, timestamp))
                freeze(db, job_id, prompts)
                jobs.append(self.store.job_dto(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()))
            if schedule_job is not None:
                db.execute("UPDATE jobs SET status='succeeded',stage='ready',error=NULL,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                           (now(), schedule_job['id'], schedule_job['attempts'] + 1))
        return jobs

    def _queue_newly_detected_translation(self, run, previously_needed):
        """A new source-language finding may make one untranslated source eligible.

        This follows only the successfully classified current revision. It does
        not retry old translation intents, schedule other capabilities or turn
        idle work into foreground work. Scheduling errors cannot undo a result.
        """
        if previously_needed:
            return []
        try:
            preferences = self.models.get_preferences()
            if not preferences.get('autoTranslate', True) or not default_model_id(preferences, 'translation'):
                return []
            with self.store.connect() as db:
                current = db.execute('SELECT current_revision FROM sources WHERE id=?', (run['source_id'],)).fetchone()
                job = db.execute('SELECT origin FROM jobs WHERE id=?', (run['job_id'],)).fetchone()
                if not current or current[0] != run['revision_id'] or not job or job['origin'] == 'idle':
                    return []
                if db.execute("SELECT 1 FROM ai_runs WHERE source_id=? AND revision_id=? AND kind='translation' LIMIT 1",
                              (run['source_id'], run['revision_id'])).fetchone() or self._manual_capability(
                                  db, run['source_id'], run['revision_id'], 'translation'):
                    return []
            if not document_needs_translation(self.store.detail(run['source_id'], run['revision_id'])):
                return []
            # queue repeats the no-prior-intent/manual-edit check inside its
            # transaction, covering a concurrent user request or worker.
            return self.queue(run['source_id'], ['translation'], revision_id=run['revision_id'],
                              automatic=True, missing_only=True)
        except Exception:
            return []

    @staticmethod
    def _manual_capability(db, source_id, revision_id, kind):
        if kind == 'classification':
            row = db.execute('SELECT tags_manual FROM sources WHERE id=?', (source_id,)).fetchone()
            return bool(row and row[0])
        if kind == 'translation':
            return bool(db.execute('SELECT 1 FROM ai_translation_edits WHERE revision_id=? LIMIT 1', (revision_id,)).fetchone())
        return False

    def maybe_queue_idle(self):
        """Queue at most one missing capability, without a full-library outbox."""
        preferences = self.models.get_preferences()
        candidate = None
        with self.store.connect() as db:
            if idle_block_reason(db) or db.execute(
                    "SELECT 1 FROM jobs WHERE origin='idle' AND status IN ('queued','running') LIMIT 1").fetchone():
                return []
            preset_filter = ''
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='preset_imports'").fetchone():
                # Bundled reading material is deliberately model-free. The
                # publication job links the new source in the same commit as
                # its snapshot, closing the importing->registered race. An
                # identical user-owned source has duplicate=1 and keeps its
                # original idle behavior; explicit user processing is allowed.
                preset_filter = ''' AND NOT EXISTS(
                    SELECT 1 FROM preset_imports p JOIN jobs pj ON pj.id=p.job_id
                    WHERE p.kind='document' AND pj.source_id=s.id AND pj.duplicate=0)'''
            candidates = db.execute('''WITH kinds(kind,priority) AS
                    (VALUES('translation',0),('summary',1),('classification',2))
                SELECT s.id,s.current_revision,k.kind,COALESCE(l.code,r.language) language,r.title,r.text_content
                FROM sources s JOIN revisions r ON r.id=s.current_revision
                LEFT JOIN document_languages l ON l.revision_id=r.id CROSS JOIN kinds k
                WHERE s.current_revision IS NOT NULL''' + preset_filter + '''
                  AND (k.kind<>'classification' OR ?)
                  AND (k.kind<>'translation' OR ?)
                  AND NOT EXISTS(SELECT 1 FROM ai_runs r WHERE r.source_id=s.id
                      AND r.revision_id=s.current_revision AND r.kind=k.kind)
                  AND NOT (k.kind='classification' AND s.tags_manual=1)
                  AND NOT (k.kind='translation' AND EXISTS(SELECT 1 FROM ai_translation_edits e
                      WHERE e.revision_id=s.current_revision))
                ORDER BY s.created_at,s.id,k.priority''',
                (bool(CATEGORIES), preferences.get('autoTranslate', True)))
            for row in candidates:
                if not default_model_id(preferences, row['kind']):
                    continue
                if row['kind'] == 'translation' and not document_needs_translation({
                        'title': row['title'], 'languageCode': row['language'],
                        'blocks': [dict(block) for block in db.execute(
                            'SELECT kind,text FROM blocks WHERE revision_id=?', (row['current_revision'],))]}):
                    continue
                candidate = row
                break
        if not candidate:
            return []
        # queue() repeats eligibility, missing-capability and version checks in
        # its publication transaction, closing races with new foreground work.
        try:
            return self.queue(candidate['id'], [candidate['kind']], revision_id=candidate['current_revision'], origin='idle', automatic=True)
        except (ValueError, KeyError):
            # A source/configuration may change between discovery and queueing.
            # Do not let a scheduler error kill the long-lived worker.
            return []

    def maybe_queue_auto(self, source_id, revision_id=None, schedule_job=None):
        preferences = self.models.get_preferences()
        document = self.store.detail(source_id, revision_id)
        # Translation is independently enabled for new imports. This entrypoint
        # never scans an existing library or turns idle processing on.
        kinds = []
        if (preferences.get('autoTranslate', True) and default_model_id(preferences, 'translation')
                and document_needs_translation(document)):
            kinds.append('translation')
        if preferences.get('autoProcess', False):
            kinds.extend(kind for kind in ('summary', 'classification') if default_model_id(preferences, kind)
                         and (kind != 'classification' or CATEGORIES))
        return self.queue(source_id, kinds, revision_id=revision_id, schedule_job=schedule_job, automatic=True) if kinds else []

    def _alive(self, db, run, attempt, latest=True):
        state = db.execute('SELECT status,attempts FROM jobs WHERE id=?', (run['job_id'],)).fetchone()
        if not state or state['status'] != 'running' or state['attempts'] != attempt:
            return False
        if latest:
            newest = db.execute('SELECT max(seq) FROM ai_runs WHERE source_id=? AND revision_id=? AND kind=?',
                                (run['source_id'], run['revision_id'], run['kind'])).fetchone()[0]
            return newest == run['seq']
        return True

    def _check(self, run, attempt):
        with self.store.connect() as db:
            if not self._alive(db, run, attempt):
                raise Superseded()
            job = db.execute('SELECT origin,input_json FROM jobs WHERE id=?', (run['job_id'],)).fetchone()
            if (run['kind'] == 'translation' and
                    (job['origin'] == 'idle' or json.loads(job['input_json']).get('automatic')) and
                    not self.models.get_preferences().get('autoTranslate', True)):
                raise AutoTranslationDisabled('自动翻译已关闭，已保留完成的检查点；可手动重新生成')
            if job['origin'] == 'idle':
                source = db.execute('SELECT current_revision FROM sources WHERE id=?', (run['source_id'],)).fetchone()
                if not source or source[0] != run['revision_id'] or self._manual_capability(
                        db, run['source_id'], run['revision_id'], run['kind']):
                    raise Superseded()
                reason = idle_block_reason(db, exclude_job=run['job_id'])
                if reason:
                    raise IdlePaused(reason)

    def _checkpoint(self, run, attempt, key, request, validator, config):
        fingerprint = _hash(request)
        with self.store.connect() as db:
            if not self._alive(db, run, attempt):
                raise Superseded()
            previous = db.execute('SELECT * FROM ai_checkpoints WHERE run_id=? AND checkpoint_key=?', (run['id'], key)).fetchone()
            if previous and previous['input_hash'] == fingerprint:
                saved = json.loads(previous['result_json'])
                validated = validator(saved)
                if key.startswith('classification') and saved.get('languageIdentification') in ('parser', 'model'):
                    # Only a previously validated program checkpoint can carry
                    # this provenance flag. Raw model fields are never trusted.
                    validated['languageIdentification'] = saved['languageIdentification']
                return validated
            db.execute('UPDATE jobs SET stage=?,updated_at=? WHERE id=?', (key, now(), run['job_id']))
        # Summaries and classifications may miss contract fields even when
        # generation succeeds. Regenerate once from the same evidence; never
        # feed rejected output back as evidence or relax reference checks.
        current_request = request
        repairable = key.startswith(('summary', 'classification'))
        for repair in range(2 if repairable else 1):
            self._check(run, attempt)
            if sum(len(message['content']) for message in current_request) > int(config.get('contextChars', 24000)):
                raise ValueError('文档加工提示词与原文超出模型输入预算，请缩短提示词或提高 contextChars')
            call_key = key if repair == 0 else key + ':repair:1'
            started = time.monotonic()
            try:
                with model_request_guard(lambda: self._check(run, attempt)):
                    response = self.models.complete(config, current_request)
            except Exception:
                self._record_call(run, attempt, call_key, {}, 'error', started)
                raise
            try:
                validated = validator(_json(response['text'] if isinstance(response, dict) else response))
            except ValueError as exc:
                self._record_call(run, attempt, call_key, response, 'rejected', started)
                if repair or not repairable:
                    raise
                current_request = [dict(message) for message in request]
                current_request[0]['content'] += prompt_text('knowledge_repair', values={'validationError': str(exc)})
                continue
            self._record_call(run, attempt, call_key, response, 'validated', started)
            break
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._alive(db, run, attempt):
                raise Superseded()
            db.execute('INSERT OR REPLACE INTO ai_checkpoints VALUES(?,?,?,?,?)', (run['id'], key, fingerprint, dumps(validated), now()))
        return validated

    def _record_call(self, run, attempt, key, response, status, started):
        """Persist timing and reported token counts, never prompts or credentials.

        Validated describes JSON/business validation only; publication still has
        its own later attempt fence, so a validated call can be cancelled.
        """
        response = response if isinstance(response, dict) else {}
        usage = response.get('usage') or {}
        usage = {field: value for field, value in usage.items() if field in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                 and isinstance(value, int) and not isinstance(value, bool) and value >= 0} if isinstance(usage, dict) else {}
        duration = response.get('durationMs')
        if not isinstance(duration, (float, int)) or isinstance(duration, bool) or not 0 <= duration <= 86400000:
            duration = round((time.monotonic() - started) * 1000)
        observed = response.get('model')
        observed = observed[:200] if isinstance(observed, str) else ''
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO ai_calls VALUES(?,?,?,?,?,?,?,?)',
                       (run['id'], key, attempt, int(duration), dumps(usage), observed, status, now()))

    @staticmethod
    def _messages(task, payload, summary_budget=None):
        return [
            {'role': 'system', 'content': prompt_text(task, values={'summaryBudget': summary_budget})},
            {'role': 'user', 'content': dumps({'task': task, **payload})},
        ]

    @staticmethod
    def _translation_units(document):
        """Use stored table cells, never ask the model to recreate a table."""
        title = document.get('originalTitle', document.get('title', ''))
        units = [{'id': document['revisionId'] + ':title', 'kind': 'title', 'text': title}] if title else []
        layouts = {}
        tables = document.get('tables', [])
        table_cursor = 0
        for block in document['blocks']:
            if block['kind'] != 'table':
                units.append(block)
                continue
            # Parser block text folds whitespace, whereas tables_json preserves
            # rows/cells. Match in source order, including skipped empty tables.
            normalized = ' '.join(block['text'].split())
            rows = None
            while table_cursor < len(tables):
                candidate = tables[table_cursor]
                table_cursor += 1
                rendered = ' '.join('\n'.join(' | '.join(row) for row in candidate).split())
                if rendered == normalized:
                    rows = candidate
                    break
            if rows is None:
                raise ValueError('表格缺少匹配的原始行列结构，无法安全翻译；请重新解析来源')
            layouts[block['id']] = rows
            for row_index, row in enumerate(rows):
                for column_index, cell in enumerate(row):
                    if cell.strip():
                        units.append({'id': f"{block['id']}:r{row_index}:c{column_index}",
                                      'blockId': block['id'], 'kind': 'table_cell', 'text': cell,
                                      'row': row_index, 'column': column_index})
        return units, layouts

    @staticmethod
    def _evidence_batches(blocks, config):
        # Summary/classification output is bounded independently of input.
        # Send each evidence ID once; translation alone needs per-piece IDs.
        # Reserve space for instructions, title, taxonomy and one repair hint.
        budget = int(config.get('contextChars', 24000)) - 6000
        if budget < 300:
            raise ValueError('模型输入预算过小；请提高 contextChars')
        batches, batch, size = [], [], 2
        for block in blocks:
            text, start = block['text'], 0
            base = {'blockId': block['id'], 'kind': block['kind']}
            while start < len(text):
                def evidence_piece(count):
                    result = {**base, 'text': text[start:start + count]}
                    if config.get('includeEvidenceOffsets'):
                        result.update(pieceId=f"{block['id']}@{start}:{start + count}", start=start, end=start + count)
                    return result
                # Measure serialized JSON, including escapes, not just text.
                low, high, count = 1, min(len(text) - start, budget), 0
                while low <= high:
                    middle = (low + high) // 2
                    candidate = evidence_piece(middle)
                    if len(dumps(candidate)) + 2 <= budget:
                        count, low = middle, middle + 1
                    else:
                        high = middle - 1
                if not count:
                    raise ValueError('原文块标识超过模型输入预算')
                if start + count < len(text):
                    boundaries = list(re.finditer(r'[\s。！？；]', text[start + count // 2:start + count]))
                    if boundaries:
                        count = count // 2 + boundaries[-1].end()
                piece = evidence_piece(count)
                cost = len(dumps(piece)) + (1 if batch else 0)
                if batch and size + cost > budget:
                    batches.append(batch)
                    batch, size, cost = [], 2, len(dumps(piece))
                batch.append(piece)
                size += cost
                start += count
        if batch:
            batches.append(batch)
        if len(batches) > 1500:
            raise ValueError('正文超过本次 1500 个分块上限，请分篇导入以控制模型调用量')
        return batches

    @staticmethod
    def _pieces(blocks, config):
        # contextChars is the explicitly configured request budget. Translation
        # additionally caps source size to leave room for Chinese output + JSON.
        context = int(config.get('contextChars', 24000))
        output = int(config.get('maxOutputTokens', 4096))
        budget = min(6000, context - 6000, int(output * .55))
        if budget < 300:
            raise ValueError('模型上下文或输出预算过小；请提高 contextChars 或 maxOutputTokens')
        pieces = []
        for block in blocks:
            text = block['text']
            start, index = 0, 0
            while start < len(text):
                def make_piece(end):
                    piece = {'id': f"{block['id']}:{index}", 'unitId': block['id'],
                             'blockId': block.get('blockId', block['id']), 'kind': block['kind'],
                             'text': text[start:end], 'start': start, 'end': end}
                    if block['kind'] == 'table_cell':
                        piece.update(row=block['row'], column=block['column'])
                    return piece
                # JSON escaping, stable IDs and table coordinates also consume
                # the input/output allowance. A piece itself must fit a batch.
                low, high, count = 1, min(len(text) - start, budget), 0
                while low <= high:
                    middle = (low + high) // 2
                    if len(dumps(make_piece(start + middle))) + 2 <= budget:
                        count, low = middle, middle + 1
                    else:
                        high = middle - 1
                if not count:
                    raise ValueError('翻译分块标识超过模型输入或输出预算，请提高模型配置')
                end = start + count
                if end < len(text):
                    # Prefer a sentence/word boundary while retaining every
                    # original character, including the boundary whitespace.
                    boundaries = list(re.finditer(r'[\s。！？；]', text[start + count // 2:end]))
                    if boundaries:
                        end = start + count // 2 + boundaries[-1].end()
                piece = make_piece(end)
                pieces.append(piece)
                start, index = end, index + 1
        batches, batch, size = [], [], 0
        for piece in pieces:
            cost = len(dumps(piece))
            if batch and size + cost > budget:
                batches.append(batch); batch = []; size = 0
            batch.append(piece); size += cost
        if batch:
            batches.append(batch)
        if len(batches) > 1500:
            raise ValueError('正文超过本次 1500 个分块上限，请分篇导入以控制模型调用量')
        return batches

    def _translation_request(self, document, pieces, units, config, previous=(), whole=False):
        """Bounded reading context is never an output target or a new source.

        Previous translations come only from this run's validated checkpoints;
        their text helps maintain vocabulary without an additional model call.
        """
        base = {'sourceRevisionId': document['revisionId'], 'title': document['title'][:1000],
                'sourceLanguage': document.get('languageCode') or document.get('language', ''), 'targetLanguage': 'zh-CN', 'blocks': pieces}
        if whole:
            # Short mixed-language documents need their complete Chinese and
            # locked paragraphs too. Do not excerpt this path: if the full
            # article cannot fit, use the explicitly contextual long-doc path.
            context = {'fullArticle': [
                {'position': index, 'kind': unit['kind'], 'text': unit['text']}
                for index, unit in enumerate(units)]}
            request = self._messages('translation', {**base, 'context': context, 'translationMode': 'whole_document'})
            if sum(len(message['content']) for message in request) <= int(config.get('contextChars', 24000)):
                return request
            raise ValueError('全文翻译上下文超过模型输入预算')
        positions = {unit['id']: index for index, unit in enumerate(units)}
        first, last = pieces[0], pieces[-1]
        first_index, last_index = positions[first['unitId']], positions[last['unitId']]
        first_unit, last_unit = units[first_index], units[last_index]
        before = first_unit['text'][:first['start']] if first['start'] else (units[first_index - 1]['text'] if first_index else '')
        after = last_unit['text'][last['end']:] if last['end'] < len(last_unit['text']) else (units[last_index + 1]['text'] if last_index + 1 < len(units) else '')
        present = {piece['unitId'] for piece in pieces}
        retained = [unit for unit in units[first_index:last_index + 1] if unit['id'] not in present]
        for limit in (400, 200, 80, 32):
            context = {
                'before': before[-limit:], 'after': after[:limit],
                'retainedSource': [{'kind': unit['kind'], 'text': unit['text'][:limit]} for unit in retained[:3]],
                'previousTranslations': [{'source': item['source'][-limit:], 'chinese': item['chinese'][-limit:]}
                                         for item in previous[-2:]],
            }
            payload = {**base, 'context': context, 'translationMode': 'contextual_batch'}
            request = self._messages('translation', payload)
            if sum(len(message['content']) for message in request) <= int(config.get('contextChars', 24000)):
                return request
        raise ValueError('翻译正文与必要上下文超过模型输入预算；请提高 contextChars 或缩短翻译提示词')

    def _translation_batches(self, document, remote_units, all_units, config):
        if not remote_units:
            return [], False
        whole = []
        for unit in remote_units:
            piece = {'id': unit['id'] + ':0', 'unitId': unit['id'], 'blockId': unit.get('blockId', unit['id']),
                     'kind': unit['kind'], 'text': unit['text'], 'start': 0, 'end': len(unit['text'])}
            if unit['kind'] == 'table_cell':
                piece.update(row=unit['row'], column=unit['column'])
            whole.append(piece)
        # Translation expansion varies by language. Reserve a conservative
        # output allowance for Chinese text, JSON and all original segment IDs.
        output_budget = int(int(config.get('maxOutputTokens', 4096)) * .55)
        estimated_output = dumps({'segments': [{'id': piece['id'], 'text': piece['text']} for piece in whole]})
        if len(estimated_output) <= output_budget:
            try:
                self._translation_request(document, whole, all_units, config, whole=True)
            except ValueError:
                pass
            else:
                # A short document is translated in one request; paragraph IDs
                # and table cells remain separate inside that single response.
                return [whole], True
        empty = self._messages('translation', {'sourceRevisionId': document['revisionId'],
            'title': document['title'][:1000], 'sourceLanguage': document.get('languageCode') or document.get('language', ''),
            'targetLanguage': 'zh-CN', 'blocks': [], 'context': {}})
        available = int(config.get('contextChars', 24000)) - sum(len(message['content']) for message in empty) - 2600
        if available < 300:
            raise ValueError('模型输入预算不足以保留翻译上下文；请提高 contextChars 或缩短翻译提示词')
        # _pieces reserves 6000 for instructions by default. Replace that fixed
        # estimate with the actual immutable prompt size plus reading context.
        return self._pieces(remote_units, {**config, 'contextChars': available + 6000}), False

    @staticmethod
    def _summary(value, allowed, max_chars=6000):
        """Validate shape, evidence IDs and budgets without scoring language."""
        summary = _simplified(_text(value.get('summary'), '摘要', 800))
        points = value.get('keyPoints')
        keywords = value.get('keywords')
        if not isinstance(points, list) or not 1 <= len(points) <= 6:
            raise ValueError('摘要要点需为 1 至 6 项')
        clean = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError('摘要要点格式错误')
            refs = _refs(point.get('blockIds'), allowed)
            if len(refs) > 3:
                raise ValueError('每个摘要要点最多关联 3 个最直接的原文块，请重试精简引用')
            clean.append({'text': _simplified(_text(point.get('text'), '要点', 300)), 'blockIds': refs})
        if not isinstance(keywords, list) or not 1 <= len(keywords) <= 12:
            raise ValueError('关键词需为 1 至 12 项')
        result = {'summary': summary, 'keyPoints': clean, 'keywords': list(dict.fromkeys(_simplified(_text(x, '关键词', 40)) for x in keywords)),
                  'normalizerVersion': NORMALIZER_VERSION}
        if len(dumps(result)) > max_chars:
            raise ValueError('摘要结果超过后续合并预算，请提高模型输入预算或重试生成更精简摘要')
        return result

    @staticmethod
    def _translation(value, pieces):
        """Validate structure only; content fidelity remains a prompt instruction.

        Segment identity and program-owned table layout protect source
        alignment. Amounts, currencies and prose language do not gate adoption.
        """
        segments = value.get('segments')
        expected = {piece['id']: piece for piece in pieces}
        if not isinstance(segments, list) or len(segments) != len(expected):
            raise ValueError('译文分块数量与原文不一致，未发布完整译文')
        found = {}
        for segment in segments:
            if (not isinstance(segment, dict) or not isinstance(segment.get('id'), str)
                    or segment['id'] not in expected or segment['id'] in found):
                raise ValueError('译文包含未知或重复的原文分块 ID')
            found[segment['id']] = _simplified(_text(segment.get('text'), '译文', 30000))
            if expected[segment['id']]['kind'] == 'table_cell':
                # Cell content cannot create new rows. Cell boundaries remain
                # entirely program-owned in the final structured table.
                found[segment['id']] = ' '.join(found[segment['id']].split())
        if set(found) != set(expected):
            raise ValueError('译文缺少原文分块')
        return {'segments': [{'id': piece['id'], 'text': found[piece['id']]} for piece in pieces]}

    @staticmethod
    def _classification_language_shares(value):
        """Validate semantic language estimates without counting arbitrary labels."""
        if not isinstance(value, dict) or not value:
            raise ValueError('sourceLanguageShares 必须为非空语言占比对象')
        shares = Counter()
        for raw_code, fraction in value.items():
            code = normalize_language_code(raw_code, default='')
            if code not in SOURCE_LANGUAGE_CODES or code in ('mixed', 'und'):
                raise ValueError('sourceLanguageShares 只能使用实际原文语言代码，不得使用 mixed 或 und')
            if (type(fraction) not in (int, float) or not math.isfinite(fraction)
                    or not 0 <= fraction <= 1):
                raise ValueError('sourceLanguageShares 占比必须是0至1的有限数字')
            shares[code] += fraction
        total = sum(shares.values())
        if not .98 <= total <= 1.02:
            raise ValueError('sourceLanguageShares 的原文语言占比合计必须为1')
        return {code: fraction / total for code, fraction in shares.items() if fraction > 0}

    @staticmethod
    def _classification(value, allowed, fallback_language='und', source_text=None, article_type_policy=False):
        result = classification_rules.validate_paths(value, allowed)
        if article_type_policy:
            result['articleType'] = classification_article_type(value, required=True)
        observed = detect_language(source_text, fallback_language) if source_text is not None else fallback_language
        offline_shares = language_shares(source_text, fallback_language) if source_text is not None else {}
        if source_text is not None:
            result['languageWeight'] = language_weight(source_text)
        if 'sourceLanguage' not in value:
            # Legacy prompts/checkpoints remain usable. Never infer a language
            # from the Chinese classification output or model-generated tags.
            result['sourceLanguage'] = normalize_language_code(observed)
            result['languageIdentification'] = 'parser'
            if offline_shares:
                result['sourceLanguageShares'] = offline_shares
            return result
        code = value['sourceLanguage']
        if not isinstance(code, str) or code not in SOURCE_LANGUAGE_CODES:
            raise ValueError('sourceLanguage 必须是 allowedSourceLanguages 提供的原文语言代码，不是标签或输出语言')
        shares = None
        if 'sourceLanguageShares' in value:
            shares = EnrichmentService._classification_language_shares(value['sourceLanguageShares'])
            code = source_language_from_shares(shares, fallback=code)
        elif code == 'mixed':
            # A bare claim or disagreement between batches cannot establish
            # two languages each covering 30% of substantive source prose.
            code = source_language_from_shares(offline_shares, fallback=normalize_language_code(observed))
            shares = offline_shares
        elif code not in ('und', 'other'):
            shares = {code: 1.0}
        if source_text is not None:
            # Semantic shares can exclude a long foreign entity name that
            # script counting cannot recognize. Genuine Han source text must
            # still exist; a wholly foreign body cannot be relabeled Chinese.
            if (code == 'zh' and observed not in ('zh', 'mixed', 'und') and
                    not ('sourceLanguageShares' in value and shares and re.search(r'[\u3400-\u9fff]', source_text))):
                raise ValueError('sourceLanguage 应识别提供的外语原文，不能把简体中文分类输出的语言写成原文语言')
        result.update(sourceLanguage=code, languageIdentification='model')
        if shares:
            result['sourceLanguageShares'] = shares
        return result

    def _process_classification(self, run, attempt, job, document, config):
        frozen = json.loads(job['input_json'])
        article_type_policy = frozen.get('articleTypePolicyVersion')
        if article_type_policy not in (None, ARTICLE_TYPE_POLICY_VERSION):
            raise ValueError('此文章性质任务使用未知策略，未发布分类结果')
        article_type_enabled = article_type_policy == ARTICLE_TYPE_POLICY_VERSION
        instructions = frozen.get('articleTypeInstructions') if article_type_enabled else None
        if article_type_enabled and (not isinstance(instructions, str) or not instructions.strip()):
            raise ValueError('文章性质任务缺少已冻结的实际指令，未发布分类结果')

        def messages(task, payload):
            request = self._messages(task, payload)
            if article_type_enabled:
                request[0]['content'] += '\n' + instructions
            return request

        total_chars = sum(len(block['text']) for block in document['blocks'])
        fallback_language = normalize_language_code(document.get('languageCode') or document.get('language'))
        base = {'sourceRevisionId': run['revision_id'], 'title': document['title'][:1000],
                'totalChars': total_chars, 'taxonomy': taxonomy(),
                'allowedSourceLanguages': list(SOURCE_LANGUAGE_CODES),
                'languagePolicyVersion': LANGUAGE_POLICY_VERSION,
                'parserLanguageHint': fallback_language,
                'existingPaths': frozen.get('existingPaths', [])}
        if article_type_enabled:
            base.update(articleTypePolicyVersion=article_type_policy, allowedArticleTypes=list(ARTICLE_TYPES))
        empty = messages('classification', {**base, 'blocks': []})
        context = int(config.get('contextChars', 24000))
        # Leave room for one targeted schema/reference repair instruction.
        repair_allowance = 700
        evidence_budget = context - sum(len(message['content']) for message in empty) - repair_allowance
        # Reserve the actual taxonomy + boundary rules, rather than assuming
        # they fit the generic summary instruction allowance.
        batches = self._evidence_batches(document['blocks'], {'contextChars': evidence_budget + 6000, 'includeEvidenceOffsets': True})
        if not batches:
            raise ValueError('文档无可加工正文块')
        results = []
        for index, pieces in enumerate(batches):
            allowed = {piece['blockId'] for piece in pieces}
            source_text = document_language_text({'blocks': pieces})
            request = messages('classification', {**base, 'blocks': pieces})
            result = self._checkpoint(run, attempt, f'classification:{index + 1}/{len(batches)}', request,
                                      lambda value, allowed=allowed, source_text=source_text: self._classification(
                                          value, allowed, fallback_language, source_text, article_type_enabled), config)
            # Recompute from immutable original evidence on retry as well;
            # model-reported lengths and cached weights cannot change votes.
            result['languageWeight'] = language_weight(source_text)
            for path in result['paths']:
                # Coverage comes only from cited pieces in this original
                # request, never from a model-supplied count or group size.
                path['support'] = [{key: piece[key] for key in ('pieceId', 'blockId', 'start', 'end')}
                                   for piece in pieces if piece['blockId'] in path['blockIds']]
            results.append(result)
        def merged_language(parts):
            return merge_source_languages(
                [part['sourceLanguage'] for part in parts], fallback_language,
                weights=[part['languageWeight'] for part in parts],
                shares=[part.get('sourceLanguageShares') for part in parts])

        def merged_shares(parts):
            counts = Counter()
            total = sum(part['languageWeight'] for part in parts)
            for part in parts:
                weight = part['languageWeight']
                for code, fraction in (part.get('sourceLanguageShares') or {part['sourceLanguage']: 1.0}).items():
                    counts[code] += fraction * weight
            return {code: count / total for code, count in counts.items()} if total else {}

        document_language = merged_language(results)
        document_shares = merged_shares(results)
        document_identification = 'model' if any(part['languageIdentification'] == 'model' for part in results) else 'parser'
        by_id = {block['id']: block['text'] for block in document['blocks']}

        def support_union(paths):
            pieces = {piece['pieceId']: piece for path in paths for piece in path['support']}
            return sorted(pieces.values(), key=lambda piece: (piece['blockId'], piece['start'], piece['end']))

        def prompt_parts(group, excerpt_chars):
            parts = []
            for part in group:
                paths = []
                for path in part['paths']:
                    support = path['support']
                    # Keep every cited block represented, then sample a long
                    # single-block span at its actual first/middle/last piece.
                    selected = []
                    for ref in path['blockIds']:
                        selected.append(next(piece for piece in support if piece['blockId'] == ref))
                    for piece in (support[0], support[len(support) // 2], support[-1]):
                        if len(selected) < 3 and piece not in selected:
                            selected.append(piece)
                    evidence = [{**piece, 'excerpt': by_id[piece['blockId']][piece['start']:min(piece['end'], piece['start'] + excerpt_chars)]}
                                for piece in selected]
                    paths.append({key: value for key, value in path.items() if key != 'support'} | {
                        'supportChars': sum(piece['end'] - piece['start'] for piece in support),
                        'supportPieceCount': len(support), 'evidence': evidence})
                parts.append({'paths': paths, 'sourceLanguage': part['sourceLanguage'],
                              'sourceLanguageShares': part.get('sourceLanguageShares', {}),
                              'languageWeight': part['languageWeight']})
                if article_type_enabled:
                    parts[-1]['articleType'] = part.get('articleType')
            return parts

        level = 0
        while len(results) > 1:
            level += 1
            reduced = []
            for index in range(0, len(results), 2):
                group = results[index:index + 2]
                if len(group) == 1:
                    reduced.append(group[0]); continue
                candidates = [path for item in group for path in item['paths']]
                allowed = {ref for path in candidates for ref in path['blockIds']}
                refs_by_path = {}
                support_by_path = {}
                for path in candidates:
                    key = classification_rules.path_key(path)
                    refs_by_path.setdefault(key, set()).update(path['blockIds'])
                    support_by_path[key] = support_union([path] + [{'support': support_by_path.get(key, [])}])
                group_language = merged_language(group)
                group_shares = merged_shares(group)
                group_weight = sum(part['languageWeight'] for part in group)
                group_identification = 'model' if any(part['languageIdentification'] == 'model' for part in group) else 'parser'
                def validate_reduction(value, allowed=allowed, refs_by_path=refs_by_path,
                                       group_language=group_language, group_identification=group_identification,
                                       group_shares=group_shares, group_weight=group_weight):
                    result = classification_rules.validate_paths(value, allowed)
                    if article_type_enabled:
                        result['articleType'] = classification_article_type(value, required=True)
                    # The reducer reads Chinese reasons/excerpts, so its own
                    # language guess cannot replace validated source batches.
                    result.update(sourceLanguage=group_language, languageIdentification=group_identification,
                                  sourceLanguageShares=group_shares, languageWeight=group_weight)
                    for path in result['paths']:
                        expected = refs_by_path.get(classification_rules.path_key(path))
                        if expected is None or not set(path['blockIds']).issubset(expected):
                            raise ValueError('分类汇总必须选择有原文依据的完整候选路径，不能拼接或新造路径')
                    return result
                # A reducer may only select complete supplied paths. Unrelated
                # first-level definitions, global path suggestions and the
                # parser's coarse language hint are redundant at this stage.
                candidate_level1_ids = {path['level1Id'] for path in candidates}
                candidate_taxonomy = {**base['taxonomy'], 'categories': [
                    item for item in base['taxonomy']['categories'] if item['id'] in candidate_level1_ids]}
                payload = {key: value for key, value in base.items() if key not in ('existingPaths', 'parserLanguageHint')} | {
                    'taxonomy': candidate_taxonomy,
                    'sourceLanguage': group_language, 'sourceLanguageShares': group_shares,
                    'parts': prompt_parts(group, 160),
                    'evidenceNote': 'supportChars由程序对该路径引用的原始片段去重计数，是这些证据片段的字符覆盖上界，不是精确主题字数或主题占比；totalChars是全篇字数。不得把整个归并组长度算成某条路径支持。证据摘录按pieceId/start/end从真实对应片段截取，可能只覆盖片段开头。请结合实质论述判断全文主题，不按候选次数或覆盖上界机械投票。'}
                request = messages('classification_reduce', payload)
                # Excerpts are supplementary and can be shortened without
                # dropping any candidate, its reason or source reference.
                if sum(len(message['content']) for message in request) > context - repair_allowance:
                    payload['parts'] = prompt_parts(group, 40)
                    request = messages('classification_reduce', payload)
                # The original title is supplementary to complete candidates,
                # their full reasons, exact block IDs and valid source excerpts.
                # Shorten only that redundant context, never remove a candidate
                # or narrow its evidence references to satisfy the input budget.
                for title_limit in (512, 256, 128, 64):
                    if sum(len(message['content']) for message in request) <= context - repair_allowance:
                        break
                    payload['title'] = base['title'][:title_limit]
                    request = messages('classification_reduce', payload)
                if sum(len(message['content']) for message in request) > context - repair_allowance:
                    raise ValueError('分类候选汇总超过模型输入预算，请提高 contextChars')
                result = self._checkpoint(run, attempt, f'classification_reduce:{level}:{index // 2}', request,
                                          validate_reduction, config)
                for path in result['paths']:
                    path['support'] = support_by_path[classification_rules.path_key(path)]
                reduced.append(result)
            results = reduced
        for path in results[0]['paths']:
            path['evidenceCoverage'] = {'supportChars': sum(piece['end'] - piece['start'] for piece in path['support']),
                                        'totalChars': total_chars, 'isUpperBound': True, 'pieces': path['support']}
        output = classification_rules.make_result(results[0], taxonomy_version=frozen['taxonomyVersion']) | {
            'sourceLanguage': document_language, 'languageIdentification': document_identification,
            'sourceLanguageShares': document_shares, 'languagePolicyVersion': LANGUAGE_POLICY_VERSION}
        if article_type_enabled:
            output.update(articleType=results[0].get('articleType'), articleTypePolicyVersion=article_type_policy)
        return output

    @job_prompts
    def process(self, job):
        # Store.claim currently returns the pre-increment row. Frozen attempt is
        # therefore +1; a later retry/recovery cannot validate this old worker.
        attempt = job['attempts'] + 1
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM ai_runs WHERE job_id=?', (job['id'],)).fetchone()
            if not row:
                raise ValueError('AI 任务缺少运行记录')
            run = dict(row)
        try:
            self._check(run, attempt)
            if run['kind'] == 'classification':
                if not CATEGORIES:
                    raise ValueError('暂无可用的分类体系，可先手动整理资料')
                if json.loads(job['input_json']).get('taxonomyVersion') != TAXONOMY_VERSION:
                    raise ValueError('分类体系已更新，请重新生成分类')
            if run['prompt_version'] != prompt_version(run['kind']):
                raise ValueError('此任务的提示词版本已升级，请在文档中重新生成；已完成结果仍保留')
            config = self.models.resolve(run['model_id'], run['model_version'])
            document = self.store.detail(run['source_id'], run['revision_id'])
            document['title'] = document.get('originalTitle', document['title'])
            kind = run['kind']
            if kind == 'classification':
                previously_needed = document_needs_translation(document)
                result = self._process_classification(run, attempt, job, document, config)
                self._publish(run, attempt, result)
                self._queue_newly_detected_translation(run, previously_needed)
                return
            units, layouts = self._translation_units(document) if kind == 'translation' else (document['blocks'], {})
            retained = {}
            locked_snapshots = {}
            whole_translation = False
            if kind == 'translation':
                # Preserve Chinese, numbers, code and locked human edits without
                # sending those units to a model. Every original block still
                # appears once in the complete result and keeps its original ID.
                with self.store.connect() as db:
                    edits = {row['block_id']: row for row in db.execute(
                        'SELECT * FROM ai_translation_edits WHERE revision_id=? ORDER BY seq', (run['revision_id'],))}
                locked_snapshots = {block_id: {'text': edit['text'], 'seq': edit['seq']}
                                    for block_id, edit in edits.items() if edit['locked']}
                remote_units = []
                translation_needed = document_needs_translation(document)
                for unit in units:
                    edit = edits.get(unit.get('blockId', unit['id']))
                    if edit and edit['locked']:
                        # Freeze the whole human block below. A table edit must
                        # not be split into cells, and later unlocking cannot
                        # make this skipped block fall back to foreign source.
                        retained[unit['id']] = [unit['text']]
                    elif (not translation_needed or unit['kind'] == 'code' or
                          not needs_translation(unit['text'], document.get('languageCode') or document.get('language', ''))):
                        retained[unit['id']] = [unit['text']]
                    else:
                        remote_units.append(unit)
                batches, whole_translation = self._translation_batches(document, remote_units, units, config)
            else:
                batches = self._evidence_batches(units, config)
            if not batches and kind != 'translation':
                raise ValueError('文档无可加工正文块')
            results = []
            previous_translations = []
            for index, pieces in enumerate(batches):
                allowed = {piece['blockId'] for piece in pieces}
                payload = {'sourceRevisionId': run['revision_id'], 'title': document['title'][:1000], 'sourceLanguage': document.get('language', ''), 'targetLanguage': run['target_language'], 'blocks': pieces}
                if kind == 'summary':
                    summary_budget = min(6000, (int(config.get('contextChars', 24000)) - 1000) // 2)
                    validator = lambda value, allowed=allowed: self._summary(value, allowed, summary_budget)
                elif kind == 'translation':
                    validator = lambda value, pieces=pieces: self._translation(value, pieces)
                request = (self._translation_request(document, pieces, units, config, previous_translations, whole=whole_translation)
                           if kind == 'translation' else self._messages(kind, payload, summary_budget))
                chunk = self._checkpoint(run, attempt, f'{kind}:{index + 1}/{len(batches)}', request, validator, config)
                results.append(chunk)
                if kind == 'translation':
                    by_id = {piece['id']: piece for piece in pieces}
                    previous_translations = [{'source': by_id[segment['id']]['text'], 'chinese': segment['text']}
                                             for segment in chunk['segments'][-2:]]
            if kind == 'summary':
                result = self._reduce_summary(run, attempt, results, config)
            elif kind == 'translation':
                translations = {segment['id']: segment['text'] for chunk in results for segment in chunk['segments']}
                merged = {unit['id']: list(retained.get(unit['id'], [])) for unit in units}
                for pieces in batches:
                    for piece in pieces:
                        merged[piece['unitId']].append(translations[piece['id']])
                segments = []
                for block in document['blocks']:
                    segment = {'blockId': block['id'], 'locked': False, 'edited': False}
                    if block['id'] in locked_snapshots:
                        segment.update(text=locked_snapshots[block['id']]['text'], edited=True)
                    elif block['id'] in layouts:
                        rows = [[' '.join(merged.get(f"{block['id']}:r{row_index}:c{column_index}", []))
                                 for column_index, _ in enumerate(row)] for row_index, row in enumerate(layouts[block['id']])]
                        segment.update(text='\n'.join(' | '.join(row) for row in rows), table=rows)
                    else:
                        segment['text'] = '\n'.join(merged[block['id']])
                    segments.append(segment)
                result = {'targetLanguage': run['target_language'], 'normalizerVersion': NORMALIZER_VERSION,
                          'sourceLanguage': document.get('languageCode') or document.get('language', ''),
                          'translatedTitle': '\n'.join(merged.get(document['revisionId'] + ':title', [document['title']])),
                          'preservedManualEdits': [{'blockId': block_id, 'editSeq': value['seq']}
                                                   for block_id, value in locked_snapshots.items()],
                          'segments': segments}
            self._publish(run, attempt, result)
        except AutoTranslationDisabled as exc:
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='cancelled',stage='auto_disabled',error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                           (str(exc), now(), job['id'], attempt))
        except IdlePaused as exc:
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='queued',stage='idle_paused',error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                           (str(exc), now(), job['id'], attempt))
        except Superseded:
            with self.store.connect() as db:
                # Cancel/retry belongs to its own caller; only finish this exact
                # running attempt when it was displaced by a newer run.
                db.execute("UPDATE jobs SET status='cancelled',stage='superseded',error='已由新加工任务替代',updated_at=? WHERE id=? AND status='running' AND attempts=?",
                           (now(), job['id'], attempt))
        except Exception as exc:
            message = str(exc)[:1000]
            # Provider exceptions must already be credential-safe. Do not store
            # arbitrary model responses or the request body in task diagnostics.
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?", (message, now(), job['id'], attempt))

    def _reduce_summary(self, run, attempt, results, config):
        level = 0
        summary_budget = min(6000, (int(config.get('contextChars', 24000)) - 1000) // 2)
        while len(results) > 1:
            level += 1
            next_results = []
            for index in range(0, len(results), 2):
                group = results[index:index + 2]
                if len(group) == 1:
                    next_results.append(group[0]); continue
                allowed = {block for result in group for point in result['keyPoints'] for block in point['blockIds']}
                request = self._messages('summary_reduce', {'summaries': group}, summary_budget)
                next_results.append(self._checkpoint(run, attempt, f'summary_reduce:{level}:{index // 2}', request,
                                                      lambda value, allowed=allowed: self._summary(value, allowed, summary_budget), config))
            results = next_results
        return results[0]

    def _publish(self, run, attempt, result):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._alive(db, run, attempt):
                raise Superseded()
            source = db.execute('SELECT * FROM sources WHERE id=?', (run['source_id'],)).fetchone()
            if run['kind'] == 'classification':
                if not CATEGORIES or result.get('taxonomyVersion') != TAXONOMY_VERSION:
                    raise ValueError('分类体系已更新，本次分类未发布')
                if result.get('sourceLanguage'):
                    self.store.set_document_language(db, run['source_id'], run['revision_id'], result['sourceLanguage'],
                                                     identification=result.get('languageIdentification', 'fallback'))
                if result.get('articleTypePolicyVersion') == ARTICLE_TYPE_POLICY_VERSION:
                    self.store.set_document_article_type(db, run['source_id'], run['revision_id'], result.get('articleType'))
                result['manualOverride'] = bool(source['tags_manual'])
                result['applied'] = not source['tags_manual'] and source['current_revision'] == run['revision_id']
                if result['applied']:
                    db.execute('UPDATE sources SET tags_json=?,classification_paths_json=? WHERE id=?',
                               (dumps(result['tags']), dumps(result['classificationPaths']), run['source_id']))
            db.execute('INSERT INTO ai_outputs VALUES(?,?,?,?,?)', (ident('out'), run['id'], dumps(result), 1, now()))
            if run['kind'] in ('summary', 'translation'):
                self.store.refresh_document_index(db, run['source_id'], run['revision_id'])
            db.execute("UPDATE jobs SET status='succeeded',stage='ready',error=NULL,updated_at=? WHERE id=? AND attempts=?", (now(), run['job_id'], attempt))

    def detail(self, source_id, revision_id=None):
        document = self.store.detail(source_id, revision_id)
        revision_id = document['revisionId']
        response = {'sourceRevisionId': revision_id, 'stale': False, 'jobs': [],
                    'classificationAvailable': bool(CATEGORIES)}
        with self.store.connect() as db:
            source = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
            response['stale'] = source['current_revision'] != revision_id
            response['hasPreviousRevisionResults'] = bool(db.execute(
                'SELECT 1 FROM ai_outputs o JOIN ai_runs r ON r.id=o.run_id WHERE r.source_id=? AND r.revision_id!=? LIMIT 1', (source_id, revision_id)).fetchone())
            for kind in KINDS:
                latest = db.execute('SELECT r.*,j.status,j.error,j.updated_at FROM ai_runs r JOIN jobs j ON j.id=r.job_id WHERE r.source_id=? AND r.revision_id=? AND r.kind=? ORDER BY r.seq DESC LIMIT 1', (source_id, revision_id, kind)).fetchone()
                successful = db.execute('SELECT r.*,o.result_json,o.created_at output_created FROM ai_runs r JOIN ai_outputs o ON o.run_id=r.id WHERE r.source_id=? AND r.revision_id=? AND r.kind=? AND o.published=1 ORDER BY r.seq DESC LIMIT 1', (source_id, revision_id, kind)).fetchone()
                stage = {'status': 'not_started', 'result': None, 'runId': None, 'jobId': None, 'error': None,
                         'model': None, 'promptVersion': prompt_version(kind), 'sourceRevisionId': revision_id, 'locked': False,
                         'previousSuccess': False, 'updatedAt': None}
                if latest:
                    stage.update(status=latest['status'], runId=latest['id'], jobId=latest['job_id'], error=latest['error'],
                                 model=json.loads(latest['model_json']), promptVersion=latest['prompt_version'], updatedAt=latest['updated_at'])
                    response['jobs'].append(self.store.job_dto(db.execute('SELECT * FROM jobs WHERE id=?', (latest['job_id'],)).fetchone()))
                    calls = db.execute('SELECT duration_ms,usage_json,validation_status FROM ai_calls WHERE run_id=?', (latest['id'],)).fetchall()
                    usage_totals = Counter()
                    for call in calls:
                        usage_totals.update(json.loads(call['usage_json']))
                    stage['telemetry'] = {'calls': len(calls), 'durationMs': sum(call['duration_ms'] for call in calls),
                                          'reportedUsage': dict(usage_totals),
                                          'validatedCalls': sum(call['validation_status'] == 'validated' for call in calls),
                                          'rejectedCalls': sum(call['validation_status'] == 'rejected' for call in calls),
                                          'failedCalls': sum(call['validation_status'] == 'error' for call in calls)}
                if successful and kind == 'classification' and (not CATEGORIES or
                        json.loads(successful['result_json']).get('taxonomyVersion') != TAXONOMY_VERSION):
                    successful = None
                    stage['retiredResult'] = True
                if successful:
                    result = json.loads(successful['result_json'])
                    stage.update(result=result, previousSuccess=successful['id'] != latest['id'], resultRunId=successful['id'],
                                 resultModel=json.loads(successful['model_json']), resultPromptVersion=successful['prompt_version'])
                    if kind == 'translation':
                        edits = {row['block_id']: row for row in db.execute('SELECT * FROM ai_translation_edits WHERE revision_id=? ORDER BY seq', (revision_id,))}
                        for segment in result['segments']:
                            edit = edits.get(segment['blockId'])
                            if edit and (edit['locked'] or successful['seq'] <= edit['base_run_seq']):
                                segment.update(text=edit['text'], locked=bool(edit['locked']), edited=True)
                                # Human block edits may intentionally alter table
                                # text; do not expose stale generated cells for it.
                                segment.pop('table', None)
                        stage['locked'] = any(segment['locked'] for segment in result['segments'])
                    if kind == 'classification' and source['tags_manual']:
                        result.update(applied=False, manualOverride=True)
                response[kind] = stage
        return response

    def edit_translation(self, source_id, block_id, text, revision_id=None, locked=True):
        if not isinstance(locked, bool):
            raise ValueError('locked 必须为布尔值')
        text = _text(text, '人工译文', 100000)
        document = self.store.detail(source_id, revision_id)
        revision_id = document['revisionId']
        if block_id not in {block['id'] for block in document['blocks']}:
            raise ValueError('译文编辑位置不属于此来源版本')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            output = db.execute("SELECT r.seq,o.result_json FROM ai_runs r JOIN ai_outputs o ON o.run_id=r.id WHERE r.source_id=? AND r.revision_id=? AND r.kind='translation' AND o.published=1 ORDER BY r.seq DESC LIMIT 1", (source_id, revision_id)).fetchone()
            if not output:
                raise ValueError('请先完成此版本的翻译，再修正译文')
            if block_id not in {segment['blockId'] for segment in json.loads(output['result_json'])['segments']}:
                raise ValueError('完整译文中缺少此原文块')
            db.execute('INSERT INTO ai_translation_edits(revision_id,block_id,text,locked,base_run_seq,created_at) VALUES(?,?,?,?,?,?)',
                       (revision_id, block_id, text, int(locked), output['seq'], now()))
            self.store.refresh_document_index(db, source_id, revision_id)
        return self.detail(source_id, revision_id)
