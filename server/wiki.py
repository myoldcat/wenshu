"""Evidence-bound Wiki pages, durable compilation and bounded lexical/graph search.

Wiki content is a derived index, never a new primary source. Identity is scoped
to a source: equal names in unrelated documents do not silently merge entities.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import re
import sqlite3
import time
import unicodedata

from .enrichment import Superseded, _json, _simplified
from .model_gateway import ModelError, ModelService, default_model_id
from .store import dumps, ident, now, tokenize
from .prompts import snapshot, intent_signature, freeze, job_prompts, prompt_text
from .wiki_graph import GraphProjection, decorate_relation, group_page, neighborhood_plan, page_order
from .wiki_bulk import WikiAllMixin, ALL_SCHEMA
from .wiki_pruning import (POLICY_VERSION as SELECTION_POLICY_VERSION,
                           INSTRUCTIONS as SELECTION_INSTRUCTIONS, prune_result)
from .graph_analytics import GraphAnalytics, GraphSnapshotConflict, source_snapshot, validate_scope
from .year_context import (POLICY_VERSION as YEAR_CONTEXT_POLICY_VERSION,
                           INSTRUCTIONS as YEAR_CONTEXT_INSTRUCTIONS,
                           build_contexts as build_year_contexts,
                           prompt_contexts as prompt_year_contexts,
                           check_years as check_context_years)

PROMPT_VERSION = 'wenshu-wiki-1.2'
MAX_BATCHES = 24
MAX_ENTRIES = 120
SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_runs(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id), source_id TEXT NOT NULL REFERENCES sources(id),
 revision_id TEXT NOT NULL REFERENCES revisions(id), model_id TEXT NOT NULL,
 model_version INTEGER NOT NULL, model_json TEXT NOT NULL, prompt_version TEXT NOT NULL,
 fingerprint TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS wiki_runs_source ON wiki_runs(source_id,seq);
CREATE TABLE IF NOT EXISTS wiki_checkpoints(
 run_id TEXT NOT NULL REFERENCES wiki_runs(id), checkpoint_key TEXT NOT NULL,
 input_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,checkpoint_key)
);
CREATE TABLE IF NOT EXISTS wiki_calls(
 run_id TEXT NOT NULL REFERENCES wiki_runs(id), checkpoint_key TEXT NOT NULL,
 attempt INTEGER NOT NULL, repair INTEGER NOT NULL, duration_ms INTEGER NOT NULL,
 usage_json TEXT NOT NULL, observed_model TEXT NOT NULL, validation_status TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(run_id,checkpoint_key,attempt,repair)
);
CREATE TABLE IF NOT EXISTS wiki_entries(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), identity_key TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(source_id,identity_key)
);
CREATE TABLE IF NOT EXISTS wiki_pages(
 entry_id TEXT NOT NULL REFERENCES wiki_entries(id), version INTEGER NOT NULL,
 run_id TEXT NOT NULL REFERENCES wiki_runs(id), source_id TEXT NOT NULL REFERENCES sources(id),
 revision_id TEXT NOT NULL REFERENCES revisions(id), content_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(entry_id,version)
);
CREATE INDEX IF NOT EXISTS wiki_pages_run ON wiki_pages(run_id);
CREATE TABLE IF NOT EXISTS wiki_publications(
 source_id TEXT PRIMARY KEY REFERENCES sources(id), run_id TEXT NOT NULL REFERENCES wiki_runs(id),
 created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_search_index USING fts5(
 entry_id UNINDEXED, title_tokens, body_tokens, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS wiki_unified_outbox(
 source_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
 error TEXT,created_at TEXT NOT NULL
);
"""


def _hash(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def _name(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip().casefold()


def _subtype(kind, value):
    # Subtypes refine their owning base type; irrelevant model fields must not
    # turn a claim into a method or invalidate otherwise grounded content.
    return value if (kind == 'concept' and value == 'method') or (kind == 'entity' and value == 'person') else ''


def _text(value, field, limit=1200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'Wiki {field} 必须为非空文本且不超过 {limit} 字符')
    return _simplified(value.strip())


def _list(value, field, minimum=0, maximum=12):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f'Wiki {field} 数量必须为 {minimum} 至 {maximum}')
    return value


def _unique_evidence(items):
    result, seen = [], set()
    for item in items:
        key = (item['revisionId'], item['blockId'], item['start'], item['end'])
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _explicit_years(text):
    years = set()
    for match in re.finditer(r'(?<![0-9A-Za-z])(?:19|20|21)\d{2}(?![0-9A-Za-z])', text):
        before, after = text[max(0, match.start() - 20):match.start()], text[match.end():match.end() + 24]
        explicit_date = (re.match(r'\s*年', after) or re.search(r'(?:year|年份)\s*$', before, re.I)
                         or re.match(r'[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])(?:\D|$)', after))
        if not explicit_date:
            money_before = re.search(r'(?:[$¥￥€£]|人民币|美元|港币|欧元|日元|英镑|USD|CNY|RMB|HKD)\s*$', before, re.I)
            unit_after = re.match(r'\s*(?:(?:[亿万千百]?(?:元|美元|港元|港币|欧元|英镑|日元|人|个|项|条|件|次|份|字|页|公斤|千克|吨|米|公里|小时|分钟|秒))|[%‰]|(?:dollars?|USD|CNY|RMB|HKD|EUR|GBP|JPY)\b)', after, re.I)
            decimal_after = re.match(r'[.,]\d', after)
            if money_before or unit_after or decimal_after:
                continue
        years.add(match.group())
    return years


def _check_years(text, evidence, year_contexts=None):
    """A narrow date guard: generated explicit years need cited source text.

    Legacy jobs remain quote-only. New jobs pass server-derived, source-bound
    context; neither path accepts retrieval timestamps or model guesses.
    """
    if year_contexts is not None:
        return check_context_years(text, evidence, year_contexts)
    generated = _explicit_years(text)
    supported = {year for reference in evidence for year in _explicit_years(reference['quote'])}
    if not generated <= supported:
        unsupported = '、'.join(sorted(generated - supported))
        raise ValueError(f'Wiki 文案包含所引原文未记载的年份（{unsupported}），请删除补全年份，只保留原文日期，不得改猜其他年份')


class WikiService(WikiAllMixin):
    def __init__(self, store, models=None):
        self.store = store
        self.models = models or ModelService(store)
        self._graph_analytics = GraphAnalytics()
        with store.connect() as db:
            db.executescript(SCHEMA)
            db.executescript(ALL_SCHEMA)

    @staticmethod
    def _published_sources(db):
        # A failed rebuild does not revoke the last published current revision.
        # Model and prompt edits only apply when the author requests a rebuild.
        return {row['source_id'] for row in db.execute('''SELECT p.source_id
            FROM wiki_publications p JOIN wiki_runs r ON r.id=p.run_id AND r.source_id=p.source_id
            JOIN jobs j ON j.id=r.job_id AND j.status='succeeded'
            JOIN sources s ON s.id=p.source_id AND s.current_revision=r.revision_id''')}

    @staticmethod
    def _processed_empty_sources(db):
        return {row['source_id'] for row in db.execute('''SELECT DISTINCT r.source_id
            FROM wiki_runs r JOIN jobs j ON j.id=r.job_id
            JOIN sources s ON s.id=r.source_id AND s.current_revision=r.revision_id
            WHERE j.status='succeeded' AND j.stage='empty'
            AND json_extract(j.input_json,'$.selectionPolicyVersion')=?''',
            (SELECTION_POLICY_VERSION,))}

    @staticmethod
    def _active_job(db, source_id):
        # An older revision still owns the source until its queued/running job
        # finishes or is explicitly cancelled. Force is not a concurrency bypass.
        return db.execute('''SELECT * FROM jobs WHERE source_id=? AND kind='wiki_build'
            AND status IN ('queued','running') ORDER BY created_at DESC,id LIMIT 1''',
            (source_id,)).fetchone()

    def build_scope(self, source_ids=None):
        if source_ids is not None and (not isinstance(source_ids, list) or len(source_ids) > 10000
                or any(not isinstance(value, str) or not value.strip() or len(value) > 100 for value in source_ids)):
            raise ValueError('Wiki 构建范围须为有效文档标识列表')
        selected = None if source_ids is None else list(dict.fromkeys(source_ids))
        # Preview has a coherent read snapshot and cannot change the catalogue,
        # resolve a model, schedule a task or touch an existing frozen request.
        with closing(sqlite3.connect(self.store.db.as_uri() + '?mode=ro', uri=True, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            db.create_function('wiki_nonempty', 1, lambda text: bool(text.strip()), deterministic=True)
            db.execute('BEGIN')
            where, args = ('', ()) if selected is None else (
                ' WHERE s.id IN (SELECT value FROM json_each(?))', (dumps(selected),))
            rows = db.execute('''SELECT s.id,r.title,r.id revision_id,
                EXISTS(SELECT 1 FROM blocks b WHERE b.revision_id=r.id AND wiki_nonempty(b.text)) readable
                FROM sources s JOIN revisions r ON r.id=s.current_revision AND r.source_id=s.id'''
                + where + ' ORDER BY s.created_at DESC,s.id', args).fetchall()
            if selected is not None:
                by_id = {row['id']: row for row in rows}
                if any(source_id not in by_id for source_id in selected):
                    raise KeyError('文档不存在')
                rows = [by_id[source_id] for source_id in selected]
            active = {row['source_id'] for row in db.execute('''SELECT DISTINCT source_id FROM jobs
                WHERE kind='wiki_build' AND status IN ('queued','running')''')}
            published = self._published_sources(db)
            unclear = self._processed_empty_sources(db)
            documents, excluded, eligible = [], {'built': 0, 'active': 0, 'empty': 0}, 0
            for row in rows:
                reason = ('active' if row['id'] in active else 'built' if row['id'] in published
                          else 'unclear' if row['id'] in unclear
                          else 'empty' if not row['readable'] else None)
                if reason:
                    excluded[reason] = excluded.get(reason, 0) + 1
                    continue
                eligible += 1
                if len(documents) < 20:
                    documents.append({'id': row['id'], 'title': row['title'], 'revisionId': row['revision_id']})
        return {'documents': documents, 'total': len(rows), 'eligible': eligible,
                'excluded': excluded, 'limited': eligible > 20, 'limit': 20}

    def queue(self, source_ids, force=False, model_id=None):
        if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 20 or any(not isinstance(x, str) or not x for x in source_ids):
            raise ValueError('每次请选择 1 至 20 篇文档构建 Wiki')
        if not isinstance(force, bool):
            raise ValueError('重新构建选项须为布尔值')
        documents = [self.store.detail(source) for source in dict.fromkeys(source_ids)]
        selected = model_id or default_model_id(self.models.get_preferences())
        if not selected:
            raise ModelError('请先在系统设置选择默认模型')
        config = self.models.resolve(selected)
        jobs, skipped = [], []
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            batch = ident('wiki_batch')
            published = self._published_sources(db) if not force else set()
            for document in documents:
                source_id, revision_id = document['id'], document['revisionId']
                current = db.execute('SELECT current_revision FROM sources WHERE id=?', (source_id,)).fetchone()
                if not current or current['current_revision'] != revision_id:
                    raise ValueError('来源版本刚发生变化，请刷新后重新构建 Wiki')
                active = self._active_job(db, source_id)
                if active:
                    jobs.append(self.store.job_dto(active))
                    continue
                if source_id in published:
                    skipped.append({'sourceId': source_id, 'reason': 'already_built'})
                    continue
                if not any(block['text'].strip() for block in document['blocks']):
                    skipped.append({'sourceId': source_id, 'reason': 'empty_source'})
                    continue
                prompts = snapshot(db, ('wiki_compile', 'wiki_repair'))
                fingerprint = _hash(intent_signature([source_id, revision_id, config['id'], config['version'], PROMPT_VERSION,
                    YEAR_CONTEXT_POLICY_VERSION, _hash(YEAR_CONTEXT_INSTRUCTIONS),
                    SELECTION_POLICY_VERSION, _hash(SELECTION_INSTRUCTIONS)], prompts))
                previous = None if force else db.execute(
                    'SELECT j.* FROM wiki_runs r JOIN jobs j ON j.id=r.job_id WHERE r.fingerprint=? ORDER BY r.seq DESC LIMIT 1',
                    (fingerprint,)).fetchone()
                if previous:
                    if previous['status'] == 'succeeded':
                        skipped.append({'sourceId': source_id, 'reason': 'no_clear_entries' if previous['stage'] == 'empty' else 'already_built'})
                    else:
                        jobs.append(self.store.job_dto(previous))
                    continue
                job_id, run_id, timestamp = ident('job'), ident('wiki'), now()
                payload = {'runId': run_id, 'sourceId': source_id, 'revisionId': revision_id,
                           'modelId': config['id'], 'modelVersion': config['version'],
                           'yearContextPolicyVersion': YEAR_CONTEXT_POLICY_VERSION,
                           'yearContextInstructions': YEAR_CONTEXT_INSTRUCTIONS,
                           'selectionPolicyVersion': SELECTION_POLICY_VERSION,
                           'selectionInstructions': SELECTION_INSTRUCTIONS}
                public = {key: config.get(key) for key in ('id', 'version', 'name', 'model')}
                db.execute("INSERT INTO jobs(id,batch_id,request_key,label,kind,input_json,status,stage,source_id,revision_id,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued','queued',?,?,?,?)",
                           (job_id, batch, ident('wiki_request'), 'Wiki 构建 · ' + document['title'], 'wiki_build', dumps(payload), source_id, revision_id, timestamp, timestamp))
                db.execute('INSERT INTO wiki_runs(id,job_id,source_id,revision_id,model_id,model_version,model_json,prompt_version,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (run_id, job_id, source_id, revision_id, config['id'], config['version'], dumps(public), PROMPT_VERSION, fingerprint, timestamp))
                freeze(db, job_id, prompts)
                jobs.append(self.store.job_dto(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()))
        return {'jobs': jobs, 'skipped': skipped}

    def _alive(self, db, run, attempt):
        state = db.execute('SELECT status,attempts,input_json FROM jobs WHERE id=?', (run['job_id'],)).fetchone()
        owner = json.loads(state['input_json']).get('wikiAllRunId') if state else None
        owner_alive = not owner or db.execute('''SELECT 1 FROM wiki_all_runs r JOIN jobs j ON j.id=r.job_id
            JOIN wiki_all_items i ON i.run_id=r.id AND i.child_job_id=? AND i.owned=1
            WHERE r.id=? AND j.status IN ('queued','running')''', (run['job_id'], owner)).fetchone()
        current = db.execute('SELECT current_revision FROM sources WHERE id=?', (run['source_id'],)).fetchone()
        latest = db.execute('SELECT max(seq) FROM wiki_runs WHERE source_id=?', (run['source_id'],)).fetchone()[0]
        return bool(owner_alive and state and state['status'] == 'running' and state['attempts'] == attempt
                    and current and current['current_revision'] == run['revision_id'] and latest == run['seq'])

    def _check(self, run, attempt):
        with self.store.connect() as db:
            if not self._alive(db, run, attempt):
                raise Superseded()

    @staticmethod
    def _batches(blocks, config):
        # Register source segments before generation. The model selects these
        # IDs; it cannot create quotations, offsets or provenance identifiers.
        # Each segment preserves a contiguous slice, including whitespace.
        budget = int(config.get('contextChars', 24000)) - 6000
        if budget < 300:
            raise ValueError('Wiki 模型输入预算过小；请提高 contextChars')
        batches, batch, size, sequence = [], [], 2, 0
        for block in blocks:
            text, start = block['text'], 0
            while start < len(text):
                sequence += 1
                base = {'blockId': block['id'], 'kind': block['kind'], 'start': start, 'evidenceId': f'e{sequence}'}
                low, high, count = 1, min(500, len(text) - start), 0
                while low <= high:
                    middle = (low + high) // 2
                    if len(dumps({**base, 'text': text[start:start + middle]})) + 2 <= budget:
                        count, low = middle, middle + 1
                    else:
                        high = middle - 1
                if not count:
                    raise ValueError('Wiki 原文块标识超过模型输入预算')
                # One complete sentence is usually the tightest reusable
                # evidence. Never alter its characters while splitting.
                boundary = re.search(r'[。！？!?；;\n]|\.(?=\s|$)', text[start:start + count])
                if boundary:
                    count = boundary.end()
                piece = {**base, 'text': text[start:start + count]}
                cost = len(dumps(piece)) + (1 if batch else 0)
                if batch and size + cost > budget:
                    batches.append(batch)
                    batch, size, cost = [], 2, len(dumps(piece))
                batch.append(piece)
                size += cost
                start += count
        if batch:
            batches.append(batch)
        if len(batches) > MAX_BATCHES:
            raise ValueError(f'此文档超过 Wiki 单次 {MAX_BATCHES} 批次上限，请拆分文档')
        return batches

    @staticmethod
    def _evidence(value, pieces, document):
        result = []
        for reference in _list(value, '引用', 1, 4):
            if not isinstance(reference, dict):
                raise ValueError('Wiki 引用格式不正确')
            if 'evidenceId' in reference:
                evidence_id = reference['evidenceId']
                if not isinstance(evidence_id, str):
                    raise ValueError('Wiki evidenceId 必须是本批已注册的原文证据编号')
                piece = next((piece for piece in pieces if piece.get('evidenceId') == evidence_id), None)
                if not piece or not piece['text'].strip():
                    raise ValueError('Wiki evidenceId 未在本批原文注册，未发布')
                if (('blockId' in reference and reference['blockId'] != piece['blockId'])
                        or ('quote' in reference and reference['quote'] != piece['text'])
                        or ('start' in reference and reference['start'] != piece['start'])
                        or ('end' in reference and reference['end'] != piece['start'] + len(piece['text']))):
                    raise ValueError('Wiki 模型引用与程序注册的原文证据不一致，未发布')
                result.append({'sourceId': document['id'], 'revisionId': document['revisionId'], 'blockId': piece['blockId'],
                    'quote': piece['text'], 'start': piece['start'], 'end': piece['start'] + len(piece['text']),
                    'title': document['title'], 'stale': False, 'evidenceId': evidence_id})
                continue
            block_id, quote = reference.get('blockId'), reference.get('quote')
            if not isinstance(block_id, str) or not isinstance(quote, str) or not quote.strip() or len(quote) > 1000:
                raise ValueError('Wiki 引用必须包含原文 blockId 和 1 至 1000 字符精确引文')
            matches = [(piece, piece['text'].find(quote)) for piece in pieces if piece['blockId'] == block_id and quote in piece['text']]
            if not matches:
                raise ValueError('Wiki 引文不属于本批原文，或并非原文精确子串，未发布')
            piece, position = matches[0]
            start = piece['start'] + position
            result.append({'sourceId': document['id'], 'revisionId': document['revisionId'], 'blockId': block_id,
                           'quote': quote, 'start': start, 'end': start + len(quote), 'title': document['title'], 'stale': False})
        return _unique_evidence(result)

    @classmethod
    def _validate(cls, value, pieces, document, year_contexts=None, selection_policy=None):
        if selection_policy is not None:
            if selection_policy != SELECTION_POLICY_VERSION:
                raise ValueError('Wiki 明确性筛选策略不受支持，请重新排队')
            return prune_result(cls, value, pieces, document, year_contexts)
        def check_years(text, references):
            # Context is reconstructed from this immutable original, never
            # from extra title/context fields supplied in the model response.
            trusted = build_year_contexts([document], references) if year_contexts is not None else None
            _check_years(text, references, trusted)

        entries, relations, keys = [], [], set()
        for entry in _list(value.get('entries'), '词条', 1, 12):
            if not isinstance(entry, dict):
                raise ValueError('Wiki 词条格式不正确')
            key = _text(entry.get('localKey'), '本批词条键', 80)
            if key in keys:
                raise ValueError('Wiki 本批词条键重复')
            keys.add(key)
            kind = entry.get('type')
            if kind not in ('concept', 'entity', 'claim', 'fact'):
                raise ValueError('Wiki 词条类型只允许 concept、entity、claim、fact')
            subtype = _subtype(kind, entry.get('subtype'))
            summary = entry.get('summary')
            if not isinstance(summary, dict):
                raise ValueError('Wiki 定义必须包含 text 和 evidence')
            definition = {'text': _text(summary.get('text'), '定义', 500),
                          'evidence': cls._evidence(summary.get('evidence'), pieces, document)}
            check_years(definition['text'], definition['evidence'])
            sections = []
            for section in _list(entry.get('sections'), '章节', 1, 4):
                if not isinstance(section, dict):
                    raise ValueError('Wiki 章节格式不正确')
                claims = []
                for claim in _list(section.get('claims'), '章节陈述', 1, 6):
                    if not isinstance(claim, dict):
                        raise ValueError('Wiki 陈述格式不正确')
                    statement = {'text': _text(claim.get('text'), '陈述', 800),
                                 'evidence': cls._evidence(claim.get('evidence'), pieces, document)}
                    check_years(statement['text'], statement['evidence'])
                    claims.append(statement)
                title = _text(section.get('title'), '章节标题', 80)
                check_years(title, [reference for claim in claims for reference in claim['evidence']])
                sections.append({'title': title, 'claims': claims})
            aliases = list(dict.fromkeys(_text(alias, '别名', 100) for alias in _list(entry.get('aliases', []), '别名', 0, 8)))
            label = _text(entry.get('label'), '名称', 150)
            page_evidence = definition['evidence'] + [reference for section in sections for claim in section['claims'] for reference in claim['evidence']]
            check_years(label, page_evidence)
            for alias in aliases:
                check_years(alias, page_evidence)
            entries.append({'localKey': key, 'label': label, 'type': kind,
                            'subtype': subtype, 'summary': definition, 'aliases': aliases, 'sections': sections})
        for relation in _list(value.get('relations', []), '关系', 0, 30):
            if (not isinstance(relation, dict) or not isinstance(relation.get('from'), str)
                    or not isinstance(relation.get('to'), str) or relation.get('from') not in keys
                    or relation.get('to') not in keys or relation.get('from') == relation.get('to')):
                raise ValueError('Wiki 关系包含未知或相同的本批词条键')
            kind = relation.get('type')
            if kind not in ('hierarchy', 'association'):
                raise ValueError('Wiki 关系类型只允许 hierarchy 或 association')
            clean_relation = {'from': relation['from'], 'to': relation['to'], 'type': kind,
                              'label': _text(relation.get('label'), '关系名称', 100),
                              'evidence': cls._evidence(relation.get('evidence'), pieces, document)}
            check_years(clean_relation['label'], clean_relation['evidence'])
            relations.append(clean_relation)
        issues = [_text(issue, '待核对事项', 300) for issue in _list(value.get('issues', []), '待核对事项', 0, 8)]
        return {'entries': entries, 'relations': relations, 'issues': issues}

    @staticmethod
    def _messages(document, pieces, year_contexts=None, year_instructions=None,
                  selection_policy=None, selection_instructions=None):
        contract = prompt_text('wiki_compile')
        payload = {'task': 'wiki_compile', 'title': document['title'][:500],
                   'sourceRevisionId': document['revisionId'], 'blocks': pieces}
        if selection_policy is not None:
            contract += '\n\n本任务冻结的明确性与独立词条筛选规则：\n' + (selection_instructions or SELECTION_INSTRUCTIONS)
            payload['selectionPolicyVersion'] = selection_policy
        if year_contexts is not None:
            contract += '\n\n本任务冻结的年份支持规则（优先于前文仅限精确引文的旧规则）：\n' + (year_instructions or YEAR_CONTEXT_INSTRUCTIONS)
            payload['yearContexts'] = prompt_year_contexts(year_contexts, max_chars=1800)
        return [{'role': 'system', 'content': contract}, {'role': 'user', 'content': dumps(payload)}]

    def _checkpoint(self, run, attempt, key, request, pieces, document, config, year_contexts=None,
                    selection_policy=None):
        fingerprint = _hash(request)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._alive(db, run, attempt):
                raise Superseded()
            previous = db.execute('SELECT * FROM wiki_checkpoints WHERE run_id=? AND checkpoint_key=?', (run['id'], key)).fetchone()
            if previous and previous['input_hash'] == fingerprint:
                # Stored values are already validated, program-owned evidence.
                stored = json.loads(previous['result_json'])
                if selection_policy is None:
                    return stored
                if isinstance(stored, dict) and stored.get('selectionPolicyVersion') == selection_policy:
                    return prune_result(type(self), stored, pieces, document, year_contexts, trusted=True)
            db.execute('UPDATE jobs SET stage=?,updated_at=? WHERE id=?', (key, now(), run['job_id']))
        current_request = request
        for repair in range(2):
            self._check(run, attempt)
            if sum(len(message['content']) for message in current_request) > int(config.get('contextChars', 24000)):
                raise ValueError('Wiki 请求超出模型输入预算，请提高 contextChars')
            started = time.monotonic()
            try:
                response = self.models.complete(config, current_request)
            except Exception:
                self._record_call(run, attempt, key, repair, {}, 'error', started)
                raise
            self._check(run, attempt)
            try:
                result = self._validate(_json(response['text'] if isinstance(response, dict) else response),
                                        pieces, document, year_contexts, selection_policy)
            except ValueError as exc:
                self._record_call(run, attempt, key, repair, response, 'rejected', started)
                if repair:
                    raise
                current_request = [dict(message) for message in request]
                current_request[0]['content'] += prompt_text('wiki_repair', values={'validationError': str(exc)})
                continue
            self._record_call(run, attempt, key, repair, response, 'validated', started)
            break
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._alive(db, run, attempt):
                raise Superseded()
            db.execute('INSERT OR REPLACE INTO wiki_checkpoints VALUES(?,?,?,?,?)', (run['id'], key, fingerprint, dumps(result), now()))
        return result

    def _record_call(self, run, attempt, key, repair, response, status, started):
        response = response if isinstance(response, dict) else {}
        usage = response.get('usage') or {}
        usage = {field: value for field, value in usage.items()
                 if field in ('prompt_tokens', 'completion_tokens', 'total_tokens') and isinstance(value, int)
                 and not isinstance(value, bool) and value >= 0} if isinstance(usage, dict) else {}
        duration = response.get('durationMs')
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not 0 <= duration <= 86400000:
            duration = round((time.monotonic() - started) * 1000)
        observed = response.get('model')
        observed = observed[:200] if isinstance(observed, str) else ''
        with self.store.connect() as db:
            state = db.execute('SELECT attempts FROM jobs WHERE id=?', (run['job_id'],)).fetchone()
            if state and state['attempts'] == attempt:
                db.execute('INSERT OR REPLACE INTO wiki_calls VALUES(?,?,?,?,?,?,?,?,?)',
                    (run['id'], key, attempt, repair, round(duration), dumps(usage), observed, status, now()))

    @staticmethod
    def _compile(run, chunks, selection_policy=None):
        pages, edges = {}, {}
        if selection_policy is not None and any(chunk.get('selectionPolicyVersion') != selection_policy for chunk in chunks):
            raise ValueError('Wiki 检查点未按本任务冻结的明确性规则验证')
        issues = [] if selection_policy else list(dict.fromkeys(issue for chunk in chunks for issue in chunk['issues']))
        for chunk in chunks:
            local = {}
            for entry in chunk['entries']:
                # Reapply for checkpoints created before subtype normalization.
                subtype = _subtype(entry['type'], entry.get('subtype'))
                identity = _hash([entry['type'], subtype, _name(entry['label'])])
                entry_id = 'wiki_' + _hash([run['source_id'], identity])[:32]
                local[entry['localKey']] = entry_id
                if entry_id not in pages:
                    pages[entry_id] = {'id': entry_id, 'identityKey': identity, 'label': entry['label'], 'type': entry['type'],
                        'subtype': subtype, 'aliases': [], 'summary': entry['summary']['text'],
                        'sections': [{'title': '定义与范围', 'claims': [entry['summary']]}],
                        'issues': list(issues), 'relations': [], 'evidence': []}
                page = pages[entry_id]
                page['aliases'] = list(dict.fromkeys(page['aliases'] + entry['aliases']))
                # Preserve differing statements across batches; never silently
                # ask a reducing model to reconcile contradictions.
                if page['summary'] != entry['summary']['text']:
                    page['sections'][0]['claims'].append(entry['summary'])
                    if not selection_policy and '不同原文片段对本词条有补充或差异，请结合引文核对。' not in page['issues']:
                        page['issues'].append('不同原文片段对本词条有补充或差异，请结合引文核对。')
                for section in entry['sections']:
                    existing = next((s for s in page['sections'] if s['title'] == section['title']), None)
                    if existing:
                        known = {dumps(claim) for claim in existing['claims']}
                        existing['claims'].extend(claim for claim in section['claims'] if dumps(claim) not in known)
                    else:
                        page['sections'].append(section)
            for relation in chunk['relations']:
                source, target = local[relation['from']], local[relation['to']]
                if source == target:
                    if selection_policy:
                        continue
                    raise ValueError('Wiki 同一词条的别名关系不可形成自连接，请重新构建')
                signature = (source, target, relation['type'], relation['label'])
                if signature not in edges:
                    edges[signature] = {**relation, 'id': 'edge_' + _hash(signature)[:32], 'from': source, 'to': target}
                else:
                    edges[signature]['evidence'] = _unique_evidence(edges[signature]['evidence'] + relation['evidence'])
        if len(pages) > MAX_ENTRIES:
            raise ValueError(f'单篇 Wiki 词条超过 {MAX_ENTRIES} 个上限，请拆分文档')
        for edge in edges.values():
            for source, target in ((edge['from'], edge['to']), (edge['to'], edge['from'])):
                pages[source]['relations'].append({'targetId': target, 'label': edge['label'], 'type': edge['type'],
                    'targetLabel': pages[target]['label'], 'targetType': pages[target]['type'],
                    'direction': 'outgoing' if source == edge['from'] else 'incoming', 'edgeId': edge['id'], 'evidence': edge['evidence']})
        for page in pages.values():
            page['evidence'] = _unique_evidence([ref for section in page['sections'] for claim in section['claims'] for ref in claim['evidence']])
        return pages

    @job_prompts
    def process(self, job):
        attempt = job['attempts'] + 1
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM wiki_runs WHERE job_id=?', (job['id'],)).fetchone()
        if row is None:
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',error='Wiki 任务缺少运行记录',updated_at=? WHERE id=? AND status='running' AND attempts=?", (now(), job['id'], attempt))
            return
        run = dict(row)
        try:
            self._check(run, attempt)
            if run['prompt_version'] != PROMPT_VERSION:
                raise ValueError('Wiki 提示词已升级，请重新构建；已有成功页面仍保留')
            config = self.models.resolve(run['model_id'], run['model_version'])
            document = self.store.detail(run['source_id'], run['revision_id'])
            payload = json.loads(job['input_json'])
            policy = payload.get('yearContextPolicyVersion')
            if policy is not None and policy != YEAR_CONTEXT_POLICY_VERSION:
                raise ValueError('Wiki 年份上下文策略不受支持，请重新排队')
            instructions = payload.get('yearContextInstructions') if policy else None
            if policy and (not isinstance(instructions, str) or not instructions.strip()):
                raise ValueError('Wiki 任务缺少年份上下文冻结指令，请重新排队')
            selection_policy = payload.get('selectionPolicyVersion')
            selection_instructions = payload.get('selectionInstructions') if selection_policy else None
            if selection_policy is not None and selection_policy != SELECTION_POLICY_VERSION:
                raise ValueError('Wiki 明确性筛选策略不受支持，请重新排队')
            if selection_policy and (not isinstance(selection_instructions, str) or not selection_instructions.strip()):
                raise ValueError('Wiki 任务缺少明确性筛选冻结指令，请重新排队')
            batches = self._batches(document['blocks'], config)
            if not batches:
                raise ValueError('文档没有可构建 Wiki 的正文')
            chunks = []
            for index, pieces in enumerate(batches):
                contexts = None
                if policy:
                    references = [{'sourceId': document['id'], 'revisionId': document['revisionId'],
                        'blockId': piece['blockId'], 'start': piece['start'],
                        'end': piece['start'] + len(piece['text']), 'quote': piece['text'],
                        'evidenceId': piece['evidenceId']} for piece in pieces]
                    contexts = build_year_contexts([document], references)
                request = self._messages(document, pieces, contexts, instructions, selection_policy, selection_instructions)
                chunks.append(self._checkpoint(run, attempt, f'wiki:{index + 1}/{len(batches)}',
                                               request, pieces, document, config, contexts, selection_policy))
            pages = self._compile(run, chunks, selection_policy)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not self._alive(db, run, attempt):
                    raise Superseded()
                timestamp = now()
                if selection_policy and not pages:
                    db.execute("UPDATE jobs SET status='succeeded',stage='empty',error=NULL,updated_at=? WHERE id=? AND attempts=?",
                               (timestamp, run['job_id'], attempt))
                    return
                db.execute('DELETE FROM wiki_search_index WHERE entry_id IN (SELECT id FROM wiki_entries WHERE source_id=?)', (run['source_id'],))
                for page in pages.values():
                    db.execute('INSERT OR IGNORE INTO wiki_entries VALUES(?,?,?,?)', (page['id'], run['source_id'], page.pop('identityKey'), timestamp))
                    page.update(version=run['seq'], updatedAt=timestamp, sourceId=run['source_id'], revisionId=run['revision_id'])
                    db.execute('INSERT INTO wiki_pages VALUES(?,?,?,?,?,?,?)', (page['id'], run['seq'], run['id'], run['source_id'], run['revision_id'], dumps(page), timestamp))
                    body = ' '.join(claim['text'] for section in page['sections'] for claim in section['claims'])
                    db.execute('INSERT INTO wiki_search_index VALUES(?,?,?)', (page['id'], tokenize(page['label'] + ' ' + ' '.join(page['aliases'])), tokenize(body)))
                db.execute('INSERT OR REPLACE INTO wiki_publications VALUES(?,?,?)', (run['source_id'], run['id'], timestamp))
                db.execute("INSERT OR REPLACE INTO wiki_unified_outbox VALUES(?,?,'pending',NULL,?)", (run['source_id'], run['id'], timestamp))
                db.execute("UPDATE jobs SET status='succeeded',stage='ready',error=NULL,updated_at=? WHERE id=? AND attempts=?", (timestamp, run['job_id'], attempt))
        except Superseded:
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='cancelled',stage='superseded',error='来源或构建任务已更新，本次结果未发布',updated_at=? WHERE id=? AND status='running' AND attempts=?", (now(), job['id'], attempt))
        except Exception as exc:
            # Never persist arbitrary adapter exceptions, response bodies or keys.
            message = str(exc)[:500] if isinstance(exc, (ModelError, ValueError)) and (isinstance(exc, ModelError) or str(exc).startswith(('Wiki ', '文档', '单篇', '此文档', '模型输入', '原文块', '正文超过'))) else 'Wiki 构建失败，请检查模型配置后重试'
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?", (message, now(), job['id'], attempt))

    @staticmethod
    def _annotate(page, current, archived=False):
        page = json.loads(dumps(page))
        # Read normalization also covers immutable historical pages. Preserve
        # their IDs/content/version; never rewrite persisted user data here.
        page['subtype'] = _subtype(page['type'], page.get('subtype'))
        refs = page.get('evidence', []) + [ref for section in page['sections'] for claim in section['claims'] for ref in claim['evidence']] + [ref for relation in page['relations'] for ref in relation['evidence']]
        for ref in refs:
            ref['stale'] = current.get(ref['sourceId']) != ref['revisionId']
        page['stale'] = archived or any(ref['stale'] for ref in refs)
        page['archived'] = archived
        page['relations'] = [decorate_relation(page, relation) for relation in page['relations']]
        page['navigationGroups'] = []
        if archived:
            page['issues'] = page['issues'] + ['此词条已不属于该来源的最新成功构建，当前展示历史内容。']
        return page

    def page(self, entry_id, version=None):
        if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
            raise ValueError('Wiki 版本须为正整数')
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM wiki_pages WHERE entry_id=?' + (' AND version=?' if version is not None else '') + ' ORDER BY version DESC LIMIT 1', (entry_id, version) if version is not None else (entry_id,)).fetchone()
            if not row:
                raise KeyError(entry_id)
            current = {r['id']: r['current_revision'] for r in db.execute('SELECT id,current_revision FROM sources')}
            publication = db.execute('SELECT run_id FROM wiki_publications WHERE source_id=?', (row['source_id'],)).fetchone()
            result = self._annotate(json.loads(row['content_json']), current, not publication or publication['run_id'] != row['run_id'])
            result['history'] = [{'version': r['version'], 'createdAt': r['created_at'], 'sourceIds': [r['source_id']], 'revisionId': r['revision_id']}
                                 for r in db.execute('SELECT version,created_at,source_id,revision_id FROM wiki_pages WHERE entry_id=? ORDER BY version DESC', (entry_id,))]
            run = db.execute('SELECT model_json,prompt_version FROM wiki_runs WHERE id=?', (row['run_id'],)).fetchone()
            result['provenance'] = {'model': json.loads(run['model_json']), 'promptVersion': run['prompt_version'], 'identityScope': 'source', 'evidenceType': 'original_blocks'}
            calls = db.execute('SELECT duration_ms,usage_json,validation_status FROM wiki_calls WHERE run_id=?', (row['run_id'],)).fetchall()
            usage = {}
            for call in calls:
                for name, count in json.loads(call['usage_json']).items():
                    usage[name] = usage.get(name, 0) + count
            result['provenance']['telemetry'] = {'calls': len(calls), 'durationMs': sum(call['duration_ms'] for call in calls),
                'reportedUsage': usage, 'rejectedCalls': sum(call['validation_status'] == 'rejected' for call in calls)}
        if not result['archived'] and version is None:
            _, pages, _ = self._snapshot()
            live = next((page for page in pages if page['id'] == entry_id), None)
            if live:
                result['relations'] = live['relations']
                result['issues'] = live['issues']
                result['navigationGroups'] = live['navigationGroups']
        return result

    @staticmethod
    def _cross_source_links(pages, memberships=None):
        """Decorate current copies with bounded previews of equivalence groups."""
        GraphProjection(pages, memberships or {}).decorate_pages()

    def _snapshot(self):
        with self.store.connect() as db:
            # Source revisions, publications and page annotations must describe
            # one read snapshot when a worker publishes concurrently.
            db.execute('BEGIN')
            sources = {r['id']: dict(r) for r in db.execute('SELECT s.id,s.current_revision,s.tags_json,s.classification_paths_json,r.title FROM sources s JOIN revisions r ON r.id=s.current_revision')}
            for source_id in self._processed_empty_sources(db):
                if source_id in sources:
                    sources[source_id]['wikiEmpty'] = True
            rows = db.execute('SELECT p.* FROM wiki_pages p JOIN wiki_publications u ON u.run_id=p.run_id ORDER BY p.created_at DESC,p.entry_id').fetchall()
            current = {source: r['current_revision'] for source, r in sources.items()}
            pages = [self._annotate(json.loads(row['content_json']), current) for row in rows]
            # Old failed attempts must not displace a source's latest success
            # from the bounded status list after repeated rebuilds.
            jobs = [self.store.job_dto(r) for r in db.execute('''SELECT j.* FROM jobs j
                JOIN wiki_runs r ON r.job_id=j.id
                JOIN (SELECT source_id,max(seq) seq FROM wiki_runs GROUP BY source_id) latest ON latest.source_id=r.source_id
                ORDER BY CASE WHEN r.seq=latest.seq THEN 0 ELSE 1 END,r.seq DESC LIMIT 50''')]
        pages.sort(key=page_order)
        _, memberships = self._topics(sources, pages)
        self._cross_source_links(pages, memberships)
        return sources, pages, jobs

    @staticmethod
    def _topics(sources, pages):
        topics, membership = {}, {}
        for page in pages:
            source = sources.get(page['sourceId'])
            if not source:
                continue
            tags = [tag for tag in json.loads(source['tags_json']) if isinstance(tag, str) and tag and tag != '未分类']
            values = [('tag:' + _hash(tag)[:16], tag) for tag in tags] or [('source:' + source['id'], source['title'])]
            membership[page['id']] = []
            for topic_id, label in values:
                topic = topics.setdefault(topic_id, {'id': topic_id, 'label': label, 'count': 0, 'root': page['id']})
                topic['count'] += 1
                membership[page['id']].append(topic_id)
        return list(topics.values()), membership

    @staticmethod
    def _node(page):
        return {key: page[key] for key in ('id', 'label', 'type', 'summary', 'aliases', 'stale', 'revisionId')} | {
            'sourceId': page['sourceId'], 'subtype': _subtype(page['type'], page.get('subtype'))}

    def overview(self, topic_id=None, limit=100):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 300:
            raise ValueError('Wiki 图谱节点上限须为 1 至 300')
        sources, pages, jobs = self._snapshot()
        topics, memberships = self._topics(sources, pages)
        graph = GraphProjection(pages, memberships)
        selected = topic_id if topic_id is not None else topics[0]['id'] if topics else None
        if selected and selected not in {topic['id'] for topic in topics}:
            raise KeyError(selected)
        candidates = [page for page in pages if selected in memberships.get(page['id'], [])]
        candidate_ids = {page['id'] for page in candidates}
        cross_ids = {member['id'] for page in candidates for group_id in graph.group_ids.get(page['id'], [])
                     for member in graph.groups[group_id]['members']}
        candidates.extend(page for page in pages if page['id'] in cross_ids and page['id'] not in candidate_ids)
        shown, source_slots = [], set()
        for page in candidates:
            future_sources = source_slots | {page['sourceId']}
            if len(shown) + 1 + len(future_sources) > limit:
                continue
            shown.append(page)
            source_slots = future_sources
        nodes, edges = graph.render([page['id'] for page in shown])
        built = {page['sourceId'] for page in pages}
        stale = {page['sourceId'] for page in pages if page['stale']}
        empty = {source_id for source_id, source in sources.items() if source.get('wikiEmpty')}
        return {'topics': topics, 'topicId': selected, 'nodes': nodes, 'edges': edges,
                'snapshotId': source_snapshot(sources, pages), 'truncated': len(candidates) > len(shown),
                'truncation': {'nodeBudget': len(candidates) > len(shown)}, 'jobs': jobs,
                'stats': {'pages': len(pages), 'sources': len(built), 'pending': len(sources.keys() - built - empty) + len(stale - empty), 'stale': len(stale)}, 'mode': 'fulltext'}

    def analytics(self, scope='topic', topic_id=None, snapshot_id=None):
        validate_scope(scope, topic_id)
        sources, pages, _ = self._snapshot()
        token = source_snapshot(sources, pages)
        if snapshot_id is not None and snapshot_id != token:
            raise GraphSnapshotConflict(token)
        topics, memberships = self._topics(sources, pages)
        if scope == 'topic' and topic_id not in {topic['id'] for topic in topics}:
            raise KeyError(topic_id)
        chosen = [{**page, 'topicId': topic_id if scope == 'topic' else next(iter(memberships.get(page['id'], [])), None)}
                  for page in pages if scope == 'all' or topic_id in memberships.get(page['id'], [])]
        projection = GraphProjection(pages, memberships)
        # No cross-source navigation expansion and no visual node budget.
        edges = [relation for page in chosen for relation in projection.semantic_relations(page['id'])]
        return self._graph_analytics.analyze(mode='source', snapshot_id=token,
            nodes=chosen, edges=edges, scope=scope, topic_id=topic_id, expected_snapshot=snapshot_id)

    def group(self, group_id, offset=0, limit=20):
        if not isinstance(group_id, str) or not group_id or len(group_id) > 100:
            raise ValueError('Wiki 导航组标识不正确')
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError('Wiki 导航组 offset 须为非负整数')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('Wiki 导航组每页成员数须为 1 至 100')
        sources, pages, _ = self._snapshot()
        _, memberships = self._topics(sources, pages)
        groups = GraphProjection(pages, memberships).groups
        if group_id not in groups:
            raise KeyError(group_id)
        return group_page(groups[group_id], offset, limit)

    def neighborhood(self, entry_id, hops=1, limit=100, offset=0, snapshot_id=None):
        if not isinstance(entry_id, str) or not entry_id or len(entry_id) > 100:
            raise ValueError('Wiki 邻域词条标识不正确')
        if isinstance(hops, bool) or hops not in (0, 1, 2):
            raise ValueError('Wiki 图扩展仅支持 0、1 或 2 跳')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 4 <= limit <= 300:
            raise ValueError('Wiki 邻域节点上限须为 4 至 300（包含焦点及原文节点）')
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError('Wiki 邻域 offset 须为非负整数')
        if snapshot_id is not None and (not isinstance(snapshot_id, str) or len(snapshot_id) > 100):
            raise ValueError('Wiki 邻域快照标识不正确')
        sources, pages, _ = self._snapshot()
        _, memberships = self._topics(sources, pages)
        graph = GraphProjection(pages, memberships)
        # The fingerprint includes parameters and all current content versions,
        # source revisions and classifications: never mix pages from two views.
        snapshot = _hash(['wiki-neighborhood-1', entry_id, hops, limit,
                          [(page['id'], page['version'], page['revisionId'], page['stale'], memberships.get(page['id'], [])) for page in pages],
                          sorted((source_id, source['current_revision']) for source_id, source in sources.items())])
        if entry_id not in graph.pages:
            if snapshot_id is None:
                raise KeyError(entry_id)
            return {'rootId': entry_id, 'focusId': entry_id,
                    'scope': {'kind': 'neighborhood', 'entryId': entry_id, 'hops': hops, 'topicIds': []},
                    'snapshotId': snapshot, 'graphSnapshotId': source_snapshot(sources, pages),
                    'offset': offset, 'limit': limit, 'totalCount': 0,
                    'nodes': [], 'edges': [], 'mergeEdges': [], 'requiredNodeIds': [],
                    'counts': {'entries': 0, 'documents': 0, 'nodes': 0, 'edges': 0},
                    'hasMore': False, 'nextOffset': None, 'reloadRequired': True, 'reason': 'focus_unavailable',
                    'truncated': False, 'truncation': {'nodeBudget': False}}
        paths, _ = graph.walk([entry_id], hops)
        scope = {'kind': 'neighborhood', 'entryId': entry_id, 'hops': hops,
                 'topicIds': sorted({topic for node_id in [entry_id] + [path['id'] for path in paths]
                                     for topic in memberships.get(node_id, [])})}
        base = {'rootId': entry_id, 'focusId': entry_id, 'scope': scope, 'snapshotId': snapshot,
                'graphSnapshotId': source_snapshot(sources, pages),
                'offset': offset, 'limit': limit, 'totalCount': len(paths) + 1}
        if snapshot_id is not None and snapshot_id != snapshot:
            return {**base, 'nodes': [], 'edges': [], 'mergeEdges': [], 'requiredNodeIds': [],
                    'counts': {'entries': 0, 'documents': 0, 'nodes': 0, 'edges': 0},
                    'hasMore': False, 'nextOffset': None, 'reloadRequired': True, 'reason': 'snapshot_changed',
                    'truncated': False, 'truncation': {'nodeBudget': False}}
        if offset and snapshot_id is None:
            raise ValueError('Wiki 邻域继续加载须提供上一页的 snapshot_id')
        plan = neighborhood_plan(entry_id, [path['id'] for path in paths], graph.pages, limit)
        anchors = graph.navigation_anchors([entry_id] + [path['id'] for path in paths], entry_id)
        page_index = next((index for index, (start, _) in enumerate(plan) if start == offset), None)
        if page_index is None:
            raise ValueError('Wiki 邻域 offset 不在分页边界，请使用返回的 nextOffset')
        chosen = [entry_id] + plan[page_index][1]
        nodes, edges = graph.render(chosen, anchors)
        current_node_ids = {node['id'] for node in nodes}
        previous = {entry_id} | {node_id for _, entries in plan[:page_index] for node_id in entries}
        allowed = previous | set(chosen)
        merge_edges = [edge for edge in graph.edges_for(chosen, allowed, anchors)
                       if edge['from'] not in current_node_ids or edge['to'] not in current_node_ids]
        required = sorted({node_id for edge in merge_edges for node_id in (edge['from'], edge['to'])
                           if node_id not in current_node_ids})
        has_more = page_index + 1 < len(plan)
        counts = {'entries': len(chosen), 'documents': len(nodes) - len(chosen), 'nodes': len(nodes), 'edges': len(edges)}
        return {**base, 'nodes': nodes, 'edges': edges, 'mergeEdges': merge_edges, 'requiredNodeIds': required,
                'counts': counts, 'hasMore': has_more, 'nextOffset': plan[page_index + 1][0] if has_more else None,
                'reloadRequired': False, 'truncated': len(plan) > 1, 'truncation': {'nodeBudget': len(plan) > 1}}

    @staticmethod
    def _query(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 300:
            raise ValueError('Wiki 检索词须为 1 至 300 字符')
        return value.strip()

    def search(self, q, limit=20, hops=1):
        q = self._query(q)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError('Wiki 检索条数须为 1 至 50')
        if isinstance(hops, bool) or hops not in (0, 1, 2):
            raise ValueError('Wiki 图扩展仅支持 0、1 或 2 跳')
        sources, pages, _ = self._snapshot()
        _, membership = self._topics(sources, pages)
        by_id = {page['id']: page for page in pages}
        graph = GraphProjection(pages, membership)
        tokens = list(dict.fromkeys(tokenize(q).split()))[:20]
        expression = ' AND '.join('"' + token.replace('"', '""') + '"' for token in tokens)
        ranked, lexical_truncated = [], False
        if expression:
            with self.store.connect() as db:
                rows = db.execute('SELECT entry_id,bm25(wiki_search_index,0,5,1) score FROM wiki_search_index WHERE wiki_search_index MATCH ? ORDER BY score,entry_id LIMIT ?', (expression, limit + 1)).fetchall()
            lexical_truncated = len(rows) > limit
            ranked = [(row['entry_id'], '词条全文命中', -row['score']) for row in rows[:limit] if row['entry_id'] in by_id]
        # Literal substring fallback covers one-character Chinese queries and
        # punctuation without exposing FTS syntax or interpreting user operators.
        seen = {entry_id for entry_id, _, _ in ranked}
        literal = [page for page in pages if page['id'] not in seen and q.casefold() in (page['label'] + ' ' + ' '.join(page['aliases']) + ' ' + ' '.join(claim['text'] for section in page['sections'] for claim in section['claims'])).casefold()]
        for page in literal:
            if len(ranked) >= limit:
                lexical_truncated = True
                break
            ranked.append((page['id'], '词条文本匹配', 0))
            seen.add(page['id'])
        # Query original evidence independently; a page cannot replace its
        # source in the retrieval corpus. Store.search bounds document results.
        raw = self.store.search(q, limit=min(limit, 20))
        raw_limit = min(limit, 20)
        raw_documents = sorted(raw['results'], key=lambda document: document['id'])
        raw_ids = {document['id'] for document in raw_documents}
        with self.store.connect() as db:
            literal_sources = db.execute('''SELECT s.id,r.id revision_id,min(b.ordinal) ordinal
                FROM sources s JOIN revisions r ON r.id=s.current_revision JOIN blocks b ON b.revision_id=r.id
                WHERE instr(lower(b.text),lower(?))>0 GROUP BY s.id,r.id ORDER BY s.id LIMIT ?''', (q, raw_limit + 1)).fetchall()
        raw_truncated = raw['total'] > len(raw['results']) or len(literal_sources) > raw_limit
        for hit in literal_sources:
            if hit['id'] in raw_ids:
                continue
            if len(raw_documents) >= raw_limit:
                raw_truncated = True
                break
            detail = self.store.detail(hit['id'], hit['revision_id'])
            detail['matchBlockId'] = next(block['id'] for block in detail['blocks'] if block['ordinal'] == hit['ordinal'])
            raw_documents.append(detail)
            raw_ids.add(hit['id'])
        evidence = []
        for document in raw_documents:
            if not document.get('matchBlockId'):
                continue
            detail = self.store.detail(document['id'], document['revisionId'])
            block = next((block for block in detail['blocks'] if block['id'] == document['matchBlockId']), None)
            if not block:
                continue
            positions = [block['text'].casefold().find(token.casefold()) for token in tokens if token.casefold() in block['text'].casefold()]
            start = max(0, min(positions, default=0) - 70)
            quote = block['text'][start:start + 300]
            evidence.append({'sourceId': document['id'], 'revisionId': document['revisionId'], 'blockId': block['id'], 'quote': quote,
                             'start': start, 'end': start + len(quote), 'title': document['title'], 'stale': False})
            for page in pages:
                if page['id'] in seen or page['sourceId'] != document['id'] or not any(ref['blockId'] == block['id'] for ref in page['evidence']):
                    continue
                if len(ranked) < limit:
                    ranked.append((page['id'], '原文命中关联词条', 0))
                    seen.add(page['id'])
                else:
                    lexical_truncated = True
        results = []
        for entry_id, reason, score in ranked:
            page = by_id[entry_id]
            claims = [claim for section in page['sections'] for claim in section['claims']]
            matched = max(claims, key=lambda claim: (q.casefold() in claim['text'].casefold(),
                sum(token.casefold() in claim['text'].casefold() for token in tokens)), default=None)
            if not matched or not any(token.casefold() in matched['text'].casefold() for token in tokens):
                matched = page['sections'][0]['claims'][0]
            results.append({'id': entry_id, 'label': page['label'], 'type': page['type'], 'subtype': page['subtype'], 'summary': page['summary'],
                'snippet': matched['text'][:300], 'reason': reason, 'score': score, 'evidence': matched['evidence'][:4], 'topicId': membership[entry_id][0], 'stale': page['stale']})
        expanded = []
        expansion_limit = min(40, limit * 2)
        paths, graph_truncated = graph.walk([entry_id for entry_id, _, _ in ranked], hops, expansion_limit)
        for path in paths:
            page = by_id[path['id']]
            last_edge = path['pathEdges'][-1]
            navigation = any(edge['layer'] == 'navigation' for edge in path['pathEdges'])
            reason = '同定义导航' if len(path['pathEdges']) == 1 and navigation else '关系路径（含同定义导航）' if navigation else '有据关系'
            expanded.append({**path, 'label': page['label'], 'type': page['type'], 'subtype': page['subtype'],
                'reason': f"{path['hopCount']} 跳{reason}：{last_edge['label']}",
                'evidence': last_edge['evidence'][:4], 'topicId': membership[page['id']][0],
                'stale': page['stale'] or any(edge['stale'] for edge in path['pathEdges']),
                'layer': last_edge['layer'], 'origin': last_edge['origin'], 'supportKind': last_edge['supportKind'],
                'derivedBy': last_edge['derivedBy'],
                'pathNodes': [{'id': node_id, 'label': by_id[node_id]['label'], 'type': by_id[node_id]['type'],
                               'topicId': next(iter(membership.get(node_id, [])), None)} for node_id in path['pathNodeIds']]})
        return {'query': q, 'mode': 'fulltext+graph', 'results': results, 'evidence': _unique_evidence(evidence), 'expanded': expanded,
                'truncated': lexical_truncated or graph_truncated or raw_truncated,
                'truncation': {'lexical': lexical_truncated, 'graph': graph_truncated, 'raw': raw_truncated}}
