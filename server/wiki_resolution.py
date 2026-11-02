"""Explicit, bounded Wiki resolution and evidence-bound canonical synthesis.

No constructor, read, upgrade or idle-worker path queues work. Source Wiki
pages remain immutable; a model can only propose identity decisions. Human
decisions belong to WikiCanonicalService, never to a similarity threshold.
"""
from __future__ import annotations

from collections import defaultdict
import json
import re

from .enrichment import Superseded, _json
from .language import require_chinese
from .model_gateway import ModelError, ModelService, default_model_id, model_request_guard
from .prompts import freeze, job_prompts, prompt_text, snapshot as prompt_snapshot
from .store import dumps, ident, now
from .wiki import WikiService, _check_years, _hash, _name, _text, _unique_evidence
from .wiki_pruning import POLICY_VERSION as SELECTION_POLICY_VERSION
from .year_context import (POLICY_VERSION as YEAR_CONTEXT_POLICY_VERSION,
                           INSTRUCTIONS as YEAR_CONTEXT_INSTRUCTIONS,
                           load_contexts as load_year_contexts,
                           prompt_contexts as prompt_year_contexts)

PROMPT_VERSION = 'wenshu-wiki-resolution-1.0'
JUDGMENTS = {'same_as', 'broader_than', 'narrower_than', 'related', 'distinct', 'uncertain'}
MAX_CANDIDATES = 8
SELECTION_REPAIR_INSTRUCTIONS = (
    '\n\n上次输出的整体JSON结构未通过检查：{validationError}。'
    '请按本任务已冻结的字段和数量要求重新返回完整JSON；sections和issues必须为数组。'
    '不明确或缺乏独立含义的内容直接省略，不补写事实；没有合格陈述时允许sections为空数组。'
    '每条候选陈述仍须提供clear布尔判断及原文evidenceId，不添加JSON以外的说明。'
)
SYNTHESIS_SELECTION_INSTRUCTIONS = (
    '本任务冻结的明确性筛选规则优先于前文至少一章、每章至少一条等旧输出要求。'
    '只保留原文明示、对象和适用范围明确、脱离本文叙述仍有独立意义的陈述。'
    '仅用于组织本文的临时框架名称，例如缺乏独立定义和明确所指的“香港三大机遇”，'
    '不要作为知识结论收录；这是原文语义判断，不是字串禁用规则。'
    '不明确、依赖未给出上下文、归属冲突或证据不足的事项直接省略，不补写、不猜测，'
    '保留有明确依据的适用条件与原文不确定性，不把推断冒充事实。'
    '在同一次JSON输出中为每条sections[].claims[]提供布尔clear，只有全部实质内容'
    '被对应精确原文及适用上下文明示支持时才为true；缺失、false或非布尔不算通过。'
    '不能明确支持的年份、标题、陈述或引用舍弃所在最小完整项，不牵连其他合格结果。'
    '根字段仍只使用sections和issues，不输出entries或relations，也不新增standalone字段；'
    '允许sections=[]，issues始终为空数组，不把待核事项写入知识结果。'
    '其余固定来源、精确引用、单句、语言、字段类型与全局数量长度边界保持。'
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_resolution_runs(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id), kind TEXT NOT NULL,
 target_id TEXT NOT NULL, model_id TEXT NOT NULL, model_version INTEGER NOT NULL,
 model_json TEXT NOT NULL, prompt_version TEXT NOT NULL, fingerprint TEXT NOT NULL,
 input_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS wiki_resolution_intent ON wiki_resolution_runs(fingerprint,seq);
CREATE TABLE IF NOT EXISTS wiki_resolution_checkpoints(
 run_id TEXT NOT NULL REFERENCES wiki_resolution_runs(id), unit_id TEXT NOT NULL,
 content_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,unit_id)
);
CREATE TABLE IF NOT EXISTS wiki_resolution_results(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES wiki_resolution_runs(id),
 source_entry_id TEXT NOT NULL, target_id TEXT NOT NULL, entry_ids_json TEXT NOT NULL,
 entries_json TEXT NOT NULL, label TEXT NOT NULL, judgment TEXT NOT NULL,
 reason TEXT NOT NULL, snapshot_id TEXT NOT NULL, status TEXT NOT NULL,
 target_member_count INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
"""


def _terms(text):
    """Finite lexical postings, including Chinese bigrams and Latin terms."""
    terms = set()
    for token in re.findall(r'[\u3400-\u9fff]+|[a-z0-9_]+', _name(text)):
        if re.fullmatch(r'[\u3400-\u9fff]+', token) and len(token) > 2:
            terms.update(token[i:i + 2] for i in range(min(len(token) - 1, 80)))
        else:
            terms.add(token)
    return sorted(terms)[:160]


def _groups(state):
    value = state['groups']
    return list(value.values()) if isinstance(value, dict) else value


class WikiResolutionService:
    def __init__(self, store, models=None, canonical=None):
        self.store = store
        self.models = models or ModelService(store)
        WikiService(store, self.models)
        if canonical is None:
            from .wiki_canonical import WikiCanonicalService
            canonical = WikiCanonicalService(store)
        self.canonical = canonical
        with store.connect() as db:
            db.executescript(SCHEMA)
            if 'target_member_count' not in {row['name'] for row in db.execute('PRAGMA table_info(wiki_resolution_results)')}:
                db.execute('ALTER TABLE wiki_resolution_results ADD COLUMN target_member_count INTEGER NOT NULL DEFAULT 1')

    @staticmethod
    def _load(db):
        sources = {row['id']: dict(row) for row in db.execute('''
            SELECT s.id,s.current_revision,s.tags_json,r.title,u.run_id
            FROM sources s JOIN revisions r ON r.id=s.current_revision
            LEFT JOIN wiki_publications u ON u.source_id=s.id''')}
        pages = []
        for row in db.execute('''SELECT p.* FROM wiki_pages p
                JOIN wiki_publications u ON u.run_id=p.run_id
                ORDER BY p.entry_id'''):
            page = WikiService._annotate(json.loads(row['content_json']),
                {source_id: source['current_revision'] for source_id, source in sources.items()})
            page.update(runId=row['run_id'], version=row['version'],
                        sourceId=row['source_id'], revisionId=row['revision_id'])
            page['stale'] = (row['source_id'] not in sources or
                             sources[row['source_id']]['current_revision'] != row['revision_id'])
            pages.append(page)
        return sources, pages

    @staticmethod
    def _source_state(sources, pages, source_ids):
        return [{'sourceId': source_id,
                 'revisionId': sources[source_id]['current_revision'],
                 'runId': sources[source_id]['run_id'],
                 'pages': [{'entryId': p['id'], 'version': p['version'],
                            'revisionId': p['revisionId'], 'runId': p['runId']}
                           for p in pages if p['sourceId'] == source_id]}
                for source_id in sorted(source_ids) if source_id in sources]

    @staticmethod
    def _compact(page, sources):
        source = sources.get(page['sourceId'], {})
        return {'id': page['id'], 'label': page['label'], 'type': page['type'],
                'subtype': page.get('subtype', ''), 'aliases': page.get('aliases', [])[:20],
                'definition': page.get('summary', '')[:700],
                'sourceTitle': source.get('title', '')[:200],
                'topics': json.loads(source.get('tags_json', '[]'))[:15]}

    def _units(self, pages, sources, state, source_ids):
        groups = _groups(state)
        by_id = {group['id']: group for group in groups}
        names, terms, topics = defaultdict(set), defaultdict(set), defaultdict(set)
        compact = {}
        for group in groups:
            members = [p for p in group['members'] if not p.get('stale')]
            if not members or len(members) != len(group['members']):
                continue
            aliases = list(dict.fromkeys([group['label']] +
                           [name for p in members for name in [p['label'], *p.get('aliases', [])]]))
            context = ' '.join(p.get('summary', '')[:700] for p in members[:4])
            labels = ' '.join(aliases)
            group_topics = list(dict.fromkeys(tag for p in members
                for tag in json.loads(sources.get(p['sourceId'], {}).get('tags_json', '[]'))))
            compact[group['id']] = {'id': group['id'], 'label': group['label'],
                'type': group['type'], 'subtype': group.get('subtype', ''),
                'aliases': aliases[:20], 'definition': context[:900], 'topics': group_topics[:15]}
            for alias in aliases:
                names[_name(alias)].add(group['id'])
            for term in _terms(labels + ' ' + context):
                terms[term].add(group['id'])
            for topic in group_topics:
                topics[topic].add(group['id'])
        rejected = {tuple(sorted(pair)) for pair in state.get('rejectedPairs', [])}
        result, compared_groups = [], set()
        for page in pages:
            if page['sourceId'] not in source_ids or page.get('stale'):
                continue
            scores = defaultdict(int)
            aliases = [page['label'], *page.get('aliases', [])]
            for alias in aliases:
                for target in names[_name(alias)]:
                    scores[target] += 100
            for term in _terms(' '.join(aliases)):
                for target in terms[term]:
                    scores[target] += 6
            for term in _terms(page.get('summary', '')):
                for target in terms[term]:
                    scores[target] += 1
            page_topics = json.loads(sources[page['sourceId']]['tags_json'])
            for topic in page_topics:
                for target in topics[topic]:
                    scores[target] += 2
            candidates = []
            for target, score in sorted(scores.items(), key=lambda item: (-item[1], item[0])):
                group = by_id[target]
                if page['id'] in group['entryIds'] or set(group['sourceIds']) == {page['sourceId']}:
                    continue
                own_group = state['entryToCanonical'][page['id']]
                if any(tuple(sorted((own, member))) in rejected
                       for own in by_id[own_group]['entryIds'] for member in group['entryIds']):
                    continue
                # Candidate generation checks kinds before model work. Other
                # graph relations can still connect different kinds elsewhere.
                if group['type'] != page['type'] or group.get('subtype', '') != page.get('subtype', ''):
                    continue
                if score < 2:
                    continue
                comparison = tuple(sorted((own_group, target)))
                if comparison in compared_groups:
                    continue
                candidates.append(compact[target])
                compared_groups.add(comparison)
                if len(candidates) == MAX_CANDIDATES:
                    break
            if candidates:
                result.append({'entry': self._compact(page, sources), 'candidates': candidates,
                    'candidateEntries': {candidate['id']: [
                        {'id': member['id'], 'label': member['label'], 'sourceId': member['sourceId'],
                         'sourceTitle': sources.get(member['sourceId'], {}).get('title', '')}
                        for member in by_id[candidate['id']]['members']]
                        for candidate in candidates}})
        return result

    def enqueue(self, source_ids, force=False, model_id=None):
        if (not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 10 or
                any(not isinstance(value, str) or not value for value in source_ids)):
            raise ValueError('Wiki 每次请选择1至10篇已构建的资料进行跨来源整理')
        if not isinstance(force, bool):
            raise ValueError('Wiki 重新整理选项须为布尔值')
        source_ids = sorted(set(source_ids))
        with self.store.connect() as db:
            sources, pages = self._load(db)
        for source_id in source_ids:
            if source_id not in sources:
                raise KeyError(source_id)
            selected = [page for page in pages if page['sourceId'] == source_id]
            if not selected or any(page['stale'] for page in selected):
                raise ValueError('Wiki 请先为所选资料的当前版本构建词条，再整理跨来源候选')
        state = self.canonical.snapshot(pages)
        units = self._units(pages, sources, state, set(source_ids))
        if not units:
            return {'jobs': [], 'skipped': [{'sourceId': source_id, 'reason': 'no_candidates'}
                                           for source_id in source_ids]}
        related_sources = set(source_ids)
        group_map = {group['id']: group for group in _groups(state)}
        for unit in units:
            for candidate in unit['candidates']:
                related_sources.update(group_map[candidate['id']]['sourceIds'])
        frozen = {'selectedSourceIds': source_ids, 'snapshotId': state['snapshotId'],
                  'sourceState': self._source_state(sources, pages, related_sources), 'units': units}
        return self._queue('wiki_resolve', _hash(source_ids), frozen, model_id, force)

    def enqueue_synthesis(self, canonical_id, model_id=None, force=False):
        with self.store.connect() as db:
            sources, pages = self._load(db)
        frozen = self.canonical.prepare_synthesis(canonical_id, pages)
        members = [p for p in pages if p['id'] in {m['entryId'] for m in frozen['members']}]
        if len(members) < 2 or len({p['sourceId'] for p in members}) < 2:
            raise ValueError('Wiki 跨文档综合需要至少两篇当前资料的已确认贡献')
        if any(p.get('stale') for p in members):
            raise ValueError('Wiki 来源版本已变化，请先构建当前词条后再综合')
        # Frozen original quotations are revalidated against immutable raw
        # blocks, rather than trusting a derived page as a primary source.
        registry = []
        seen = set()
        for reference in frozen['evidence']:
            key = tuple(reference.get(field) for field in ('sourceId', 'revisionId', 'blockId', 'start', 'end', 'quote'))
            if key in seen:
                continue
            seen.add(key)
            document = self.store.detail(reference['sourceId'], reference['revisionId'])
            block = next((block for block in document['blocks'] if block['id'] == reference['blockId']), None)
            start, end, quote = reference.get('start'), reference.get('end'), reference.get('quote')
            if (not block or isinstance(start, bool) or not isinstance(start, int) or
                    isinstance(end, bool) or not isinstance(end, int) or not isinstance(quote, str) or
                    not quote.strip() or not 0 <= start < end <= len(block['text']) or
                    block['text'][start:end] != quote):
                raise ValueError('Wiki 综合原始证据定位无效，未排队')
            registry.append({**reference, 'evidenceId': 's' + str(len(registry) + 1),
                             'originalEvidence': dict(reference)})
        if not registry:
            raise ValueError('Wiki 统一词条缺少可综合的原始证据')
        frozen.update(sourceState=self._source_state(sources, pages, {p['sourceId'] for p in members}),
                      registry=registry)
        frozen.update(yearContextPolicyVersion=YEAR_CONTEXT_POLICY_VERSION,
                      yearContextInstructions=YEAR_CONTEXT_INSTRUCTIONS,
                      yearContexts=load_year_contexts(self.store, registry))
        frozen.update(selectionPolicyVersion=SELECTION_POLICY_VERSION,
                      selectionInstructions=SYNTHESIS_SELECTION_INSTRUCTIONS,
                      selectionRepairInstructions=SELECTION_REPAIR_INSTRUCTIONS)
        return self._queue('wiki_synthesize', canonical_id, frozen, model_id, force)

    def refresh_source(self, source_id):
        """Directed hook after explicit source publication, never a backfill."""
        with self.store.connect() as db:
            _, pages = self._load(db)
        state = self.canonical.snapshot(pages)
        groups = [group for group in _groups(state) if group.get('manual') and source_id in group['sourceIds']
                  and len(group['sourceIds']) >= 2 and len(group['entryIds']) >= 2
                  and not any(p.get('stale') for p in group['members'])]
        jobs, skipped = [], []
        for group in groups:
            response = self.enqueue_synthesis(group['id'])
            jobs.extend(response['jobs'])
            skipped.extend(response['skipped'])
        if not groups:
            skipped.append({'sourceId': source_id, 'reason': 'no_confirmed_group'})
        return {'jobs': jobs, 'skipped': skipped}

    def _queue(self, kind, target_id, frozen, model_id, force):
        selected = model_id or default_model_id(self.models.get_preferences())
        if not selected:
            raise ModelError('请先在系统设置选择默认模型')
        config = self.models.resolve(selected)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            sources, pages = self._load(db)
            actual = self._source_state(sources, pages, {s['sourceId'] for s in frozen['sourceState']})
            if actual != frozen['sourceState']:
                raise ValueError('Wiki 来源刚发生变化，请刷新后重试')
            prompts = prompt_snapshot(db, (kind,))
            # The last synthesized output version is a publication fence, not
            # a new source intent. Replaying a publication/outbox event must
            # return its old durable job instead of consuming another call.
            intent = {key: value for key, value in frozen.items() if key != 'expectedSynthesisVersion'}
            fingerprint = _hash([kind, target_id, intent, config['id'], config['version'],
                                 prompts, PROMPT_VERSION])
            previous = None if force else db.execute('''SELECT j.* FROM wiki_resolution_runs r
                JOIN jobs j ON j.id=r.job_id WHERE r.fingerprint=? ORDER BY r.seq DESC LIMIT 1''',
                (fingerprint,)).fetchone()
            if previous:
                return {'jobs': [self.store.job_dto(previous)], 'skipped': []}
            job_id, run_id, timestamp = ident('job'), ident('wresolve'), now()
            payload = {'runId': run_id, 'modelId': config['id'], 'modelVersion': config['version']}
            label = 'Wiki 跨来源辨析' if kind == 'wiki_resolve' else 'Wiki 跨文档综合'
            public = {key: config.get(key) for key in ('id', 'version', 'name', 'model')}
            db.execute('''INSERT INTO jobs(id,batch_id,request_key,label,kind,input_json,
                status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued','queued',?,?)''',
                (job_id, ident('wiki_batch'), ident('wiki_request'), label, kind, dumps(payload), timestamp, timestamp))
            db.execute('''INSERT INTO wiki_resolution_runs(id,job_id,kind,target_id,model_id,
                model_version,model_json,prompt_version,fingerprint,input_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (run_id, job_id, kind, target_id, config['id'], config['version'], dumps(public),
                 PROMPT_VERSION, fingerprint, dumps(frozen), timestamp))
            freeze(db, job_id, prompts)
            job = self.store.job_dto(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
        return {'jobs': [job], 'skipped': []}

    def _alive(self, db, run, attempt, frozen):
        job = db.execute('SELECT status,attempts FROM jobs WHERE id=?', (run['job_id'],)).fetchone()
        if not job or job['status'] != 'running' or job['attempts'] != attempt:
            return False
        sources, pages = self._load(db)
        current = self._source_state(sources, pages, {s['sourceId'] for s in frozen['sourceState']})
        if current != frozen['sourceState']:
            return False
        state = self.canonical.snapshot(pages, db=db)
        if state['snapshotId'] != frozen['snapshotId']:
            return False
        if run['kind'] == 'wiki_synthesize':
            latest = db.execute('SELECT max(seq) FROM wiki_resolution_runs WHERE kind=? AND target_id=?',
                                (run['kind'], run['target_id'])).fetchone()[0]
            if latest != run['seq']:
                return False
        return True

    def _check(self, run, attempt, frozen):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._alive(db, run, attempt, frozen):
                raise Superseded()

    @staticmethod
    def _validate_resolution(value, unit):
        items = value.get('judgments') if isinstance(value, dict) else None
        allowed = {candidate['id'] for candidate in unit['candidates']}
        if not isinstance(items, list) or len(items) != len(allowed):
            raise ValueError('Wiki 辨析必须恰好返回全部输入候选')
        result, seen = [], set()
        for item in items:
            if not isinstance(item, dict) or set(item) != {'candidateId', 'judgment', 'reason'}:
                raise ValueError('Wiki 辨析字段不正确')
            candidate = item['candidateId']
            if not isinstance(candidate, str) or candidate not in allowed or candidate in seen:
                raise ValueError('Wiki 辨析包含未知或重复候选')
            if item['judgment'] not in JUDGMENTS:
                raise ValueError('Wiki 辨析关系类型不正确')
            reason = require_chinese(_text(item['reason'], '辨析理由', 400), 'Wiki 辨析理由')
            result.append({'candidateId': candidate, 'judgment': item['judgment'], 'reason': reason})
            seen.add(candidate)
        return result

    @staticmethod
    def _validate_synthesis(value, registry, year_contexts=None, selection=False):
        if selection:
            return WikiResolutionService._select_synthesis(value, registry, year_contexts)
        if not isinstance(value, dict) or set(value) != {'sections', 'issues'}:
            raise ValueError('Wiki 综合字段不正确')
        sections, issues = value['sections'], value['issues']
        if not isinstance(sections, list) or not 1 <= len(sections) <= 6:
            raise ValueError('Wiki 综合需要1至6个章节')
        if not isinstance(issues, list) or len(issues) > 12:
            raise ValueError('Wiki 综合问题数量不正确')
        by_id = {reference['evidenceId']: reference for reference in registry}
        result = []
        for section in sections:
            if not isinstance(section, dict) or set(section) != {'title', 'claims'}:
                raise ValueError('Wiki 综合章节字段不正确')
            title = require_chinese(_text(section['title'], '综合章节名', 80), 'Wiki 综合章节名')
            if not isinstance(section['claims'], list) or not 1 <= len(section['claims']) <= 8:
                raise ValueError('Wiki 综合每章需要1至8条陈述')
            claims = []
            for claim in section['claims']:
                if not isinstance(claim, dict) or set(claim) != {'text', 'evidence'}:
                    raise ValueError('Wiki 综合陈述字段不正确')
                text = require_chinese(_text(claim['text'], '综合陈述', 800), 'Wiki 综合陈述')
                # A citation applies to this one sentence, never to a multi-
                # sentence paragraph with an unsupported trailing statement.
                if len([part for part in re.split(r'[。！？!?]+(?:[”’\"\']*)|(?<!\d)\.(?=\s|$)', text) if part.strip()]) > 1:
                    raise ValueError('Wiki 综合每条陈述只能包含一个完整句子')
                references = claim['evidence']
                if not isinstance(references, list) or not 1 <= len(references) <= 4:
                    raise ValueError('Wiki 综合每句必须引用1至4个原文证据')
                evidence = []
                for reference in references:
                    if not isinstance(reference, dict) or set(reference) != {'evidenceId'}:
                        raise ValueError('Wiki 综合只能选择程序注册的原文evidenceId')
                    evidence_id = reference['evidenceId']
                    if not isinstance(evidence_id, str) or evidence_id not in by_id:
                        raise ValueError('Wiki 综合包含未注册的原文证据编号')
                    selected = by_id[evidence_id]
                    evidence.append(dict(selected.get('originalEvidence', selected)))
                evidence = _unique_evidence(evidence)
                _check_years(text, evidence, year_contexts)
                claims.append({'text': text, 'evidence': evidence})
            _check_years(title, [ref for claim in claims for ref in claim['evidence']], year_contexts)
            result.append({'title': title, 'claims': claims})
        checked_issues = [require_chinese(_text(issue, '综合问题', 400), 'Wiki 综合问题') for issue in issues]
        for issue in checked_issues:
            _check_years(issue, registry)
        return {'sections': result, 'issues': checked_issues}

    @staticmethod
    def _select_synthesis(value, registry, year_contexts=None):
        # Only envelope damage is repairable. Each unclear/unsupported item is
        # omitted while its independent supported neighbours remain usable.
        if not isinstance(value, dict) or set(value) != {'sections', 'issues'}:
            raise ValueError('Wiki 综合字段不正确')
        sections, issues = value['sections'], value['issues']
        if not isinstance(sections, list) or len(sections) > 6:
            raise ValueError('Wiki 综合sections须为最多6个章节的数组')
        if not isinstance(issues, list) or len(issues) > 12:
            raise ValueError('Wiki 综合问题数量不正确')
        result = []
        for section in sections:
            if (not isinstance(section, dict) or set(section) != {'title', 'claims'}
                    or not isinstance(section['claims'], list) or len(section['claims']) > 8):
                continue
            try:
                title = require_chinese(_text(section['title'], '综合章节名', 80), 'Wiki 综合章节名')
            except ValueError:
                continue
            claims = []
            for claim in section['claims']:
                if not isinstance(claim, dict) or claim.get('clear') is not True:
                    continue
                candidate = {key: item for key, item in claim.items() if key != 'clear'}
                try:
                    checked = WikiResolutionService._validate_synthesis(
                        {'sections': [{'title': '来源记载', 'claims': [candidate]}], 'issues': []},
                        registry, year_contexts)
                    claims.append(checked['sections'][0]['claims'][0])
                except ValueError:
                    continue
            if not claims:
                continue
            try:
                _check_years(title, [ref for claim in claims for ref in claim['evidence']], year_contexts)
            except ValueError:
                continue
            result.append({'title': title, 'claims': claims})
        # Unlocated "issues" are not supported Wiki assertions. Operational
        # diagnostics remain on the task rather than entering knowledge pages.
        return {'sections': result, 'issues': [], 'selectionPolicyVersion': SELECTION_POLICY_VERSION}

    def _call(self, run, attempt, frozen, unit_id, payload, validate, config):
        self._check(run, attempt, frozen)
        with self.store.connect() as db:
            old = db.execute('SELECT content_json FROM wiki_resolution_checkpoints WHERE run_id=? AND unit_id=?',
                             (run['id'], unit_id)).fetchone()
        if old:
            return json.loads(old['content_json'])
        contract = prompt_text(run['kind'])
        if run['kind'] == 'wiki_synthesize' and frozen.get('yearContextPolicyVersion'):
            contract += '\n\n本任务冻结的年份支持规则（优先于前文仅限精确引文的旧规则）：\n' + frozen['yearContextInstructions']
        selection = run['kind'] == 'wiki_synthesize' and frozen.get('selectionPolicyVersion') is not None
        if selection:
            contract += '\n\n本任务冻结的明确性与独立收录规则（优先于旧输出约束）：\n' + frozen['selectionInstructions']
        messages = [{'role': 'system', 'content': contract}, {'role': 'user', 'content': dumps(payload)}]
        for repair in range(2 if selection else 1):
            if sum(len(message['content']) for message in messages) > int(config.get('contextChars', 24000)):
                raise ValueError('Wiki 综合或辨析输入超过模型预算，请提高contextChars或缩小已确认分组')
            self._check(run, attempt, frozen)
            with model_request_guard(lambda: self._check(run, attempt, frozen)):
                try:
                    response = self.models.complete(config, messages)
                except (Superseded, ModelError):
                    raise
                except Exception:
                    raise ModelError('Wiki 模型请求失败，请检查模型配置后重试') from None
            self._check(run, attempt, frozen)
            try:
                result = validate(_json(response['text'] if isinstance(response, dict) else response))
                break
            except ValueError as exc:
                if not selection or repair:
                    raise
                messages = [dict(message) for message in messages]
                messages[0]['content'] += frozen['selectionRepairInstructions'].replace('{validationError}', str(exc)[:500])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._alive(db, run, attempt, frozen):
                raise Superseded()
            db.execute('INSERT OR REPLACE INTO wiki_resolution_checkpoints VALUES(?,?,?,?)',
                       (run['id'], unit_id, dumps(result), now()))
        return result

    @job_prompts
    def process(self, job):
        attempt = job['attempts'] + 1
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM wiki_resolution_runs WHERE job_id=?', (job['id'],)).fetchone()
        if not row:
            self._finish(job, attempt, 'failed', 'failed', 'Wiki 整理任务缺少运行记录')
            return
        run = dict(row)
        frozen = json.loads(run['input_json'])
        try:
            self._check(run, attempt, frozen)
            policy = frozen.get('yearContextPolicyVersion') if run['kind'] == 'wiki_synthesize' else None
            if policy is not None and policy != YEAR_CONTEXT_POLICY_VERSION:
                raise ValueError('Wiki 年份上下文策略不受支持，请重新排队')
            if policy and (not isinstance(frozen.get('yearContextInstructions'), str)
                           or not frozen['yearContextInstructions'].strip()
                           or not isinstance(frozen.get('yearContexts'), dict)):
                raise ValueError('Wiki 综合任务缺少年份上下文冻结信息，请重新排队')
            if run['prompt_version'] != PROMPT_VERSION:
                raise ValueError('Wiki 整理提示词协议已升级，请重新排队；原有成功结果保留')
            selection = frozen.get('selectionPolicyVersion') if run['kind'] == 'wiki_synthesize' else None
            if selection is not None and (selection != SELECTION_POLICY_VERSION
                    or not isinstance(frozen.get('selectionInstructions'), str) or not frozen['selectionInstructions'].strip()
                    or not isinstance(frozen.get('selectionRepairInstructions'), str) or not frozen['selectionRepairInstructions'].strip()):
                raise ValueError('Wiki 综合明确性策略冻结信息不受支持，请重新排队')
            config = self.models.resolve(run['model_id'], run['model_version'])
            if run['kind'] == 'wiki_resolve':
                judgments = []
                for unit in frozen['units']:
                    result = self._call(run, attempt, frozen, unit['entry']['id'],
                        {'task': 'wiki_resolve', 'entry': unit['entry'], 'candidates': unit['candidates']},
                        lambda value: self._validate_resolution(value, unit), config)
                    judgments.append((unit, result))
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._alive(db, run, attempt, frozen):
                        raise Superseded()
                    for unit, items in judgments:
                        for item in items:
                            entry = unit['entry']
                            source_id = next(state['sourceId'] for state in frozen['sourceState']
                                if any(page['entryId'] == entry['id'] for page in state['pages']))
                            target_entries = unit['candidateEntries'][item['candidateId']]
                            representative = next((target for target in target_entries
                                if target['sourceId'] != source_id), target_entries[0])
                            # These two representatives identify complete
                            # canonical groups under the frozen snapshot; they
                            # are not a truncated claim of all group members.
                            entries = [{'id': entry['id'], 'label': entry['label'], 'sourceId': source_id,
                                        'sourceTitle': entry['sourceTitle']}, representative]
                            entry_ids = sorted(set(e['id'] for e in entries))
                            result_id = 'wproposal_' + _hash([run['id'], entry['id'], item['candidateId']])[:32]
                            db.execute('''INSERT OR REPLACE INTO wiki_resolution_results
                                (id,run_id,source_entry_id,target_id,entry_ids_json,entries_json,label,
                                 judgment,reason,snapshot_id,status,target_member_count,created_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                                (result_id, run['id'], entry['id'], item['candidateId'], dumps(entry_ids),
                                 dumps(entries), entry['label'], item['judgment'], item['reason'],
                                 frozen['snapshotId'], 'pending', len(target_entries), now()))
                    db.execute("UPDATE jobs SET status='succeeded',stage='ready',error=NULL,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                               (now(), job['id'], attempt))
            elif run['kind'] == 'wiki_synthesize':
                payload = {'task': 'wiki_synthesize', 'canonicalId': run['target_id'],
                           'label': frozen.get('label', ''),
                           'members': frozen['members'],
                           'evidence': [{'evidenceId': r['evidenceId'], 'text': r['quote'],
                                         'sourceTitle': r.get('title', ''), 'sourceId': r['sourceId']}
                                        for r in frozen['registry']]}
                contexts = frozen['yearContexts'] if policy else None
                if policy:
                    payload['yearContexts'] = prompt_year_contexts(contexts, max_chars=1800)
                if selection:
                    payload['selectionPolicyVersion'] = selection
                result = self._call(run, attempt, frozen, 'synthesis', payload,
                    lambda value: self._validate_synthesis(value, frozen['registry'], contexts, bool(selection)), config)
                self._check(run, attempt, frozen)
                with self.store.connect() as db:
                    _, pages = self._load(db)
                # Canonical service performs membership, synthesis version and
                # job-attempt fences within its own publication transaction.
                published = self.canonical.publish_synthesis(run['target_id'], frozen, result, pages,
                                                           job_id=job['id'], attempt=attempt)
                self._finish(job, attempt, 'succeeded', 'empty' if published.get('empty') else 'ready')
            else:
                raise ValueError('Wiki 整理任务类型不正确')
        except Superseded:
            self._finish(job, attempt, 'cancelled', 'superseded', '来源、归并决定或任务版本已变化，本次结果未发布')
        except Exception as exc:
            message = (str(exc)[:500] if isinstance(exc, (ModelError, ValueError)) and
                       (isinstance(exc, ModelError) or str(exc).startswith('Wiki ')) else
                       'Wiki 整理失败，请检查模型配置后重试')
            self._finish(job, attempt, 'failed', 'failed', message)

    def _finish(self, job, attempt, status, stage, error=None):
        with self.store.connect() as db:
            db.execute('''UPDATE jobs SET status=?,stage=?,error=?,updated_at=?
                WHERE id=? AND status='running' AND attempts=?''',
                (status, stage, error, now(), job['id'], attempt))

    def results(self, job_id=None):
        with self.store.connect() as db:
            _, pages = self._load(db)
            rows = db.execute('''SELECT p.*,r.job_id FROM wiki_resolution_results p
                JOIN wiki_resolution_runs r ON r.id=p.run_id'''
                + (' WHERE r.job_id=?' if job_id else '') + ' ORDER BY p.created_at DESC,p.id LIMIT 300',
                (job_id,) if job_id else ()).fetchall()
        state = self.canonical.snapshot(pages)
        proposals = []
        for row in rows:
            status = row['status']
            if status == 'pending' and row['snapshot_id'] != state['snapshotId']:
                status = 'stale'
            proposals.append({'id': row['id'], 'jobId': row['job_id'], 'entryIds': json.loads(row['entry_ids_json']),
                'entries': json.loads(row['entries_json']), 'label': row['label'], 'judgment': row['judgment'],
                'reason': row['reason'], 'snapshotId': row['snapshot_id'], 'status': status,
                'targetCanonicalId': row['target_id'], 'targetMemberCount': row['target_member_count'],
                'createdAt': row['created_at']})
        return {'proposals': proposals, 'snapshotId': state['snapshotId']}

    def set_proposal_status(self, entry_ids, status):
        if status not in ('confirmed', 'rejected', 'related', 'deferred'):
            raise ValueError('Wiki 建议状态不正确')
        wanted = set(entry_ids)
        with self.store.connect() as db:
            for row in db.execute("SELECT id,entry_ids_json FROM wiki_resolution_results WHERE status='pending'").fetchall():
                if wanted == set(json.loads(row['entry_ids_json'])):
                    db.execute('UPDATE wiki_resolution_results SET status=? WHERE id=?', (status, row['id']))
