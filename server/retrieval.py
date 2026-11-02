"""Bounded writing-reference retrieval over existing, revision-bound evidence.

Derived summaries/Wiki pages are discovery indexes only. Every returned passage
is copied from an immutable current original block. Retrieval never queues
enrichment. Semantic retrieval uses a real embeddings endpoint and reports its
candidate/billing budget instead of relabelling lexical scores as vectors.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time

import httpx

from .model_gateway import ModelError, default_model_id, normalize_base_url
from .retrieval_relevance import category_names, contains, discovery_queries, lexical_relevance, query_groups, semantic_relevance, topic_alignment
from .store import dumps, ident, now, tokenize
from .retrieval_vector import VectorRetrievalMixin, VECTOR_SCHEMA, SOURCE_LABELS
from .retrieval_vector_units import KINDS, iter_units
from .retrieval_rerank import RerankClient, rerank, FALLBACK_WARNING, CHANGED_WARNING
from .retrieval_rerank_packets import dependencies_current


CHANNELS = ('keyword', 'vector', 'category', 'summary', 'claims', 'facts', 'graph')
LABELS = dict(zip(CHANNELS, ('关键词', '向量语义', '主题分类', '摘要', '提取观点', '提取事实', '知识图谱')))
DEFAULTS = {'resultLimit': 40, 'passagesPerSource': 3, 'candidateLimit': 200,
            'queryExpansion': True, 'channels': {key: key != 'vector' for key in CHANNELS},
            'embeddingModelId': None, 'graphHops': 1, 'minimumScore': 0, 'rerankEnabled': True}
MAX_NEW_EMBEDDINGS = 64
MAX_WIKI_PAGES = 5000
QWEN3_QUERY_PREFIX = ('Instruct: Given a search query, retrieve relevant passages that answer the query'
                     '\nQuery: ')
SCHEMA = """
CREATE TABLE IF NOT EXISTS retrieval_settings(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS retrieval_snapshots(
 id TEXT PRIMARY KEY,content_json TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval_embeddings(
 cache_key TEXT PRIMARY KEY,model_id TEXT NOT NULL,model_version INTEGER NOT NULL,
 source_id TEXT NOT NULL,revision_id TEXT NOT NULL,vector_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS retrieval_embeddings_source ON retrieval_embeddings(source_id,revision_id);
"""
STOPWORDS = {'的', '了', '和', '与', '在', '是', '对', '及', '或', '为', '以', '等', '从', '有',
             '如何', '什么', '关于', '进行', '分析', '研究', '报告', '文章', '写作', 'the', 'a', 'an',
             'and', 'or', 'of', 'to', 'in', 'for', 'on', 'with', 'is', 'are'}


def _tokens(text):
    return list(dict.fromkeys(t for t in tokenize(text).split() if t not in STOPWORDS and len(t) > 1
                             and not re.fullmatch(r'\d+[年月日号]?', t)))[:40]


def _match(text, tokens):
    text = text.casefold()
    return sum(1 + min(len(token), 10) / 10 for token in tokens if contains(text, token))


def _ids(values, label='资料'):
    if not isinstance(values, list) or len(values) > 100 or any(
            not isinstance(v, str) or not v.strip() or len(v) > 100 for v in values):
        raise ValueError(f'{label}标识须为不超过 100 项的文本列表')
    return list(dict.fromkeys(values))


def _queries(values):
    if not isinstance(values, list) or len(values) > 8 or any(
            not isinstance(v, str) or not v.strip() or len(v) > 300 for v in values):
        raise ValueError('检索条件须为最多 8 条、每条 1 至 300 字符的文本')
    return list(dict.fromkeys(v.strip() for v in values))


def _embedding_query(config, queries):
    """Apply Qwen3's asymmetric search instruction within the existing budget."""
    is_qwen3 = re.fullmatch(r'qwen3-embedding(?::[^\s:]+)?', config['model'], flags=re.IGNORECASE)
    prefix = QWEN3_QUERY_PREFIX if is_qwen3 else ''
    return prefix + '\n'.join(queries)[:2400 - len(prefix)]


def validate_selection(db, recommendation_id, source_ids, passage_selections=None):
    """Validate confirmation against the exact recommendation snapshot/current DB.

    Called inside the writing transaction. Returns pinned revision IDs; accepting
    a snapshot never silently upgrades an existing writing project's revisions.
    """
    ids = _ids(source_ids)
    if not isinstance(recommendation_id, str) or not recommendation_id or len(recommendation_id) > 100:
        raise ValueError('请选择有效的推荐记录')
    row = db.execute('SELECT content_json FROM retrieval_snapshots WHERE id=?', (recommendation_id,)).fetchone()
    if not row:
        raise ValueError('推荐记录不存在，请重新检索并确认参考材料')
    snapshot = json.loads(row['content_json'])
    versions = snapshot['selectionVersions']
    result = {}
    for source_id in ids:
        revision_id = versions.get(source_id)
        current = db.execute('SELECT current_revision FROM sources WHERE id=?', (source_id,)).fetchone()
        if not revision_id or not current or current['current_revision'] != revision_id:
            raise ValueError('参考材料不在本次推荐范围或版本已变化，请重新检索并确认')
        result[source_id] = revision_id
    passages = passage_selections if passage_selections is not None else []
    if not isinstance(passages, list) or len(passages) > 1000:
        raise ValueError('段落选择须为不超过 1000 项的列表')
    allowed = {(item['sourceId'], p['blockId']): p for item in snapshot['items'] for p in item['passages']}
    for selection in passages:
        if not isinstance(selection, dict):
            raise ValueError('段落选择格式不正确')
        source_id, block_id = selection.get('sourceId'), selection.get('blockId')
        if not isinstance(source_id, str) or not isinstance(block_id, str):
            raise ValueError('段落选择须包含资料及原文块标识')
        passage = allowed.get((source_id, block_id))
        revision_id = result.get(source_id)
        if not revision_id or selection.get('revisionId', revision_id) != revision_id:
            raise ValueError('所选段落不属于已确认资料的本次推荐版本')
        block = db.execute('SELECT text FROM blocks WHERE id=? AND revision_id=?', (block_id, revision_id)).fetchone()
        quote = selection.get('quote', passage['quote'] if passage else None)
        if not block or (quote is not None and (not isinstance(quote, str) or not quote or quote not in block['text'])):
            raise ValueError('推荐段落与原文不一致，请重新检索并确认')
    return result


class RetrievalService(VectorRetrievalMixin):
    def __init__(self, store, models, operations=None, reranker=None):
        self.store, self.models = store, models
        self.operations = operations
        self.reranker = reranker if reranker is not None else RerankClient()
        with store.connect() as db:
            db.executescript(SCHEMA + VECTOR_SCHEMA)

    def get_settings(self):
        with self.store.connect() as db:
            row = db.execute("SELECT value_json FROM retrieval_settings WHERE key='settings'").fetchone()
        result = copy.deepcopy(DEFAULTS)
        if row:
            saved = json.loads(row['value_json'])
            result.update({k: v for k, v in saved.items() if k in DEFAULTS and k != 'channels'})
            result['channels'].update(saved.get('channels', {}))
        return result

    def save_settings(self, changes):
        if not isinstance(changes, dict) or set(changes) - set(DEFAULTS):
            raise ValueError('知识库检索配置包含未知选项')
        result = self.get_settings()
        for key, value in changes.items():
            if key == 'channels':
                if not isinstance(value, dict) or set(value) - set(CHANNELS) or any(not isinstance(v, bool) for v in value.values()):
                    raise ValueError('检索通道须使用已知通道名称和布尔开关')
                result['channels'].update(value)
            else:
                result[key] = value
        for key, low, high in (('resultLimit', 1, 100), ('passagesPerSource', 1, 5), ('candidateLimit', 20, 1000), ('graphHops', 0, 2)):
            if isinstance(result[key], bool) or not isinstance(result[key], int) or not low <= result[key] <= high:
                raise ValueError(f'{key} 须为 {low} 至 {high} 的整数')
        if result['candidateLimit'] < result['resultLimit']:
            raise ValueError('候选文章上限不能小于推荐文章数')
        if not isinstance(result['queryExpansion'], bool):
            raise ValueError('自主扩展查询须为布尔开关')
        if not isinstance(result['rerankEnabled'], bool):
            raise ValueError('增强排序须为布尔开关')
        score = result['minimumScore']
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('最低融合分数须在 0 至 1 之间')
        model_id = result['embeddingModelId']
        if model_id is not None:
            if not isinstance(model_id, str) or not model_id or len(model_id) > 100:
                raise ValueError('请选择有效的向量模型配置')
            self.models.snapshot(model_id)
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO retrieval_settings VALUES('settings',?)", (dumps(result),))
        return result

    def _plan(self, topic, brief, model_id, explicit, settings, warnings, recommendation_id=None, query_prompt=None):
        if explicit:
            return _queries(explicit), 'manual'
        local = list(dict.fromkeys([topic[:300], *[v.strip()[:300] for v in re.split(r'[；;。\n]', topic) if v.strip()]]))[:5]
        # Writing purpose/audience contain format instructions and negative
        # examples. They may guide the planner, but are not standalone queries.
        if not settings['queryExpansion']:
            return local, 'local'
        try:
            selected = model_id or default_model_id(self.models.get_preferences())
            if not selected:
                warnings.append('未配置查询模型，已按主题与写作需求生成本地检索条件。')
                return local, 'local'
            config = self.models.resolve(selected)
            config = dict(config, timeoutSeconds=min(config.get('timeoutSeconds', 60), 60),
                          maxOutputTokens=min(config.get('maxOutputTokens', 1200), 1200))
            from .prompts import snapshot as prompt_snapshot, compiled_snapshot_text
            if query_prompt is None:
                with self.store.connect() as db:
                    query_prompt = prompt_snapshot(db, ('retrieval_query_expansion',))
            instruction = compiled_snapshot_text('retrieval_query_expansion', query_prompt['retrieval_query_expansion'])
            config = dict(config, promptSnapshot=query_prompt)
            messages = [{'role': 'system', 'content': instruction},
                        {'role': 'user', 'content': dumps({'topic': topic, 'brief': brief})}]
            if self.operations and recommendation_id:
                with self.operations.capture_materials(recommendation_id, config, messages) as capture:
                    response = capture.complete(self.models)
                    generated = self._planned_queries(response)
                    capture.validated()
            else:
                response = self.models.complete(config, messages)
                generated = self._planned_queries(response)
            return list(dict.fromkeys([topic[:300], *generated]))[:8], 'model'
        except Exception:
            warnings.append('模型查询扩展未完成，已保留主题并使用本地检索条件；可修改条件后重试。')
            return local, 'local'

    @staticmethod
    def _planned_queries(response):
        from .enrichment import _json
        planned = _json(response['text'])
        generated = _queries(planned.get('queries')) if isinstance(planned, dict) else []
        if not generated:
            raise ValueError('模型未返回有效的检索条件')
        return generated

    def _lexical(self, db, tokens, settings, statuses, warnings):
        """Use persisted FTS for body discovery; metadata scans never load bodies."""
        scores = {key: {} for key in CHANNELS}
        cap = settings['candidateLimit']
        if not tokens:
            return scores
        if settings['channels']['keyword']:
            expression = ' OR '.join('"' + t.replace('"', '""') + '"' for t in tokens)
            try:
                rows = db.execute('''SELECT s.id,bm25(search_index,0,5,1) score FROM search_index
                    JOIN sources s ON s.id=search_index.source_id WHERE search_index MATCH ?
                    AND s.current_revision IS NOT NULL ORDER BY score,s.id LIMIT ?''', (expression, cap)).fetchall()
                scores['keyword'] = {r['id']: 1 / (1 + index) for index, r in enumerate(rows)}
            except Exception:
                statuses['keyword'] = ('unavailable', '关键词索引不可用，其他通道继续工作')
                warnings.append('关键词索引读取失败，请检查资料索引后重试。')
        for key, expression in (
                ('summary', "coalesce(nullif(c.summary,''),r.summary)"),
                ('category', "s.tags_json || ' ' || s.classification_paths_json")):
            if not settings['channels'][key]:
                continue
            # instr keeps user percent/underscore characters literal, not LIKE syntax.
            score_sql = ' + '.join(f'(instr(lower({expression}),?)>0)' for _ in tokens)
            try:
                rows = db.execute(f'''SELECT s.id,{expression} matched,({score_sql}) score
                    FROM sources s JOIN revisions r ON r.id=s.current_revision
                    LEFT JOIN document_chinese c ON c.revision_id=r.id
                    WHERE score>0 ORDER BY score DESC,s.id LIMIT ?''', (*tokens, cap)).fetchall()
                scores[key] = {r['id']: _match(r['matched'], tokens) for r in rows}
            except Exception:
                statuses[key] = ('unavailable', f'{LABELS[key]}索引不可用，其他通道继续工作')
                warnings.append(f'{LABELS[key]}索引读取失败，请检查后重试。')
        return scores

    def _wiki(self, db, tokens, settings, scores, anchors, reasons, statuses, warnings):
        active = [key for key in ('claims', 'facts', 'graph') if settings['channels'][key]]
        if not active:
            return
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='wiki_pages'").fetchone():
            for key in active:
                statuses[key] = ('unavailable', '尚无已提取的 Wiki 观点、事实或图谱，未自动启动加工')
            return
        rows = db.execute('''SELECT p.content_json FROM wiki_pages p
            JOIN wiki_publications u ON u.run_id=p.run_id AND u.source_id=p.source_id
            JOIN sources s ON s.id=p.source_id AND s.current_revision=p.revision_id
            ORDER BY p.created_at DESC,p.entry_id LIMIT ?''', (MAX_WIKI_PAGES + 1,)).fetchall()
        if len(rows) > MAX_WIKI_PAGES:
            warnings.append(f'Wiki 检索已按本次预算限制为最新 {MAX_WIKI_PAGES} 个词条。')
        current = {r['id']: r['current_revision'] for r in db.execute('SELECT id,current_revision FROM sources')}
        from .wiki import WikiService
        from .wiki_graph import GraphProjection
        pages = [WikiService._annotate(json.loads(row['content_json']), current) for row in rows[:MAX_WIKI_PAGES]]
        pages = [page for page in pages if not page['stale']]
        by_id = {page['id']: page for page in pages}
        seeds = []
        for page in pages:
            text = page['label'] + ' ' + ' '.join(page.get('aliases', []))
            claims = [claim for section in page.get('sections', []) for claim in section.get('claims', [])]
            score = _match(text + ' ' + ' '.join(claim['text'] for claim in claims), tokens)
            if score:
                seeds.append((score, page['id']))
            kind = 'facts' if page['type'] == 'fact' else 'claims'
            if kind not in active:
                continue
            for claim in claims:
                value = _match(text + ' ' + claim['text'], tokens)
                if value:
                    for ref in claim['evidence']:
                        if current.get(ref['sourceId']) != ref['revisionId']:
                            continue
                        source = ref['sourceId']
                        scores[kind][source] = max(value, scores[kind].get(source, 0))
                        anchors.setdefault(source, []).append((kind, ref))
                        reasons.setdefault(source, []).append(f"{LABELS[kind]}：{claim['text'][:90]}")
        for key in active:
            if key != 'graph' and not any(page['type'] == ('fact' if key == 'facts' else 'claim') for page in pages):
                detail = '使用已发布词条中的有据陈述' if key == 'claims' and pages else '尚无当前版本的已提取内容'
                statuses[key] = ('ready' if key == 'claims' and pages else 'unavailable', detail)
        if 'graph' in active:
            if not pages:
                statuses['graph'] = ('unavailable', '尚无当前版本的已发布图谱')
                return
            graph = GraphProjection(pages, {})
            seed_ids = [value[1] for value in sorted(seeds, reverse=True)[:30]]
            for entry_id in seed_ids:
                page = by_id[entry_id]
                source = page['sourceId']
                scores['graph'][source] = max(scores['graph'].get(source, 0), 1)
                reasons.setdefault(source, []).append(f"图谱词条直接命中：{page['label']}")
                anchors.setdefault(source, []).extend(('graph', ref) for ref in page.get('evidence', []) if ref['sourceId'] == source)
            paths, truncated = graph.walk(seed_ids, settings['graphHops'], settings['candidateLimit'])
            for path in paths:
                page = by_id[path['id']]
                if any(edge['stale'] for edge in path['pathEdges']):
                    continue
                source = page['sourceId']
                scores['graph'][source] = max(scores['graph'].get(source, 0), 1 / path['hopCount'])
                navigation = any(edge['layer'] == 'navigation' for edge in path['pathEdges'])
                reason = '同定义导航' if navigation else '有据关系'
                reasons.setdefault(source, []).append(f"图谱 {path['hopCount']} 跳{reason}：{page['label']}")
                # The target's own original evidence is the selectable passage;
                # cross-source edge evidence must never masquerade as its text.
                anchors.setdefault(source, []).extend(('graph', ref) for ref in page.get('evidence', []) if ref['sourceId'] == source)
            statuses['graph'] = ('ready', f"已检索词条及当前有据关系与同定义导航，最多扩展 {settings['graphHops']} 跳")
            if truncated:
                warnings.append('图谱扩展达到候选预算，部分关联未纳入本次推荐。')

    def _embed(self, config, texts, observer=None):
        """One bounded, non-redirecting request; no automatic provider retries."""
        def failure(message, category=None):
            error = ModelError(message)
            if category is not None:
                error.automaticRetryCategory = category
            return error

        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if config.get('apiKey'):
            headers['Authorization'] = 'Bearer ' + config['apiKey']
        timeout = min(config.get('timeoutSeconds', 30), 30)
        started = time.monotonic()
        payload = {'model': config['model'], 'input': texts, 'encoding_format': 'float'}
        call_id = observer.start(payload, config, 1, 'http') if observer else None
        raw, body, status_code, truncated = bytearray(), None, None, False
        status = 'failed'
        try:
            with httpx.Client(transport=getattr(self.models, 'transport', None), trust_env=False,
                              follow_redirects=False, timeout=httpx.Timeout(timeout, connect=min(10, timeout))) as client:
                with client.stream('POST', normalize_base_url(config['baseUrl']) + '/embeddings',
                                   headers=headers, json=payload) as response:
                    status_code = response.status_code
                    if not 200 <= status_code < 300 and observer is None:
                        raise failure('向量服务不可用，请核对 Embeddings 模型与服务地址',
                                      'network' if status_code in (408, 429, 500, 502, 503, 504) else None)
                    for part in response.iter_bytes():
                        raw.extend(part)
                        if len(raw) > 8 * 1024 * 1024 or time.monotonic() - started > timeout:
                            truncated = True
                            if observer:
                                del raw[8 * 1024 * 1024:]
                            raise ModelError('向量响应超出时间或大小预算')
            if not 200 <= status_code < 300:
                try:
                    body = json.loads(raw)
                except (ValueError, TypeError):
                    pass
                raise failure('向量服务不可用，请核对 Embeddings 模型与服务地址',
                              'network' if status_code in (408, 429, 500, 502, 503, 504) else None)
            try:
                body = json.loads(raw)
            except (ValueError, TypeError) as exc:
                exc.automaticRetryCategory = 'format_error'
                raise
            if not isinstance(body, dict):
                raise failure('向量服务响应数量不匹配', 'format_error')
            data = body.get('data')
            if not isinstance(data, list) or len(data) != len(texts):
                raise failure('向量服务响应数量不匹配', 'format_error')
            ordered = {}
            for item in data:
                if not isinstance(item, dict):
                    raise failure('向量服务响应序号不正确', 'format_error')
                index, vector = item.get('index'), item.get('embedding')
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(texts) or index in ordered:
                    raise failure('向量服务响应序号不正确', 'format_error')
                if not isinstance(vector, list) or not 1 <= len(vector) <= 8192 or any(
                        isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in vector):
                    raise failure('向量服务未返回有效浮点向量', 'format_error')
                norm = math.sqrt(sum(v * v for v in vector))
                if not math.isfinite(norm) or not norm:
                    raise failure('向量服务返回无效长度', 'format_error')
                ordered[index] = [v / norm for v in vector]
            status = 'succeeded'
            return [ordered[index] for index in range(len(texts))]
        finally:
            if observer and call_id:
                observer.finish(call_id, raw=bytes(raw) if raw else None, body=body, status=status,
                                http_status=status_code, error=None if status == 'succeeded' else '向量请求或响应校验失败',
                                duration_ms=round((time.monotonic() - started) * 1000), truncated=truncated)

    def recommend(self, topic, brief=None, source_ids=None, model_id=None, queries=None):
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 1000:
            raise ValueError('写作主题须为 1 至 1000 字符')
        topic, brief = topic.strip(), brief if brief is not None else {}
        if not isinstance(brief, dict) or len(dumps(brief)) > 12000:
            raise ValueError('写作需求须为不超过 12000 字符的对象')
        ids = _ids(source_ids if source_ids is not None else [])
        explicit = _queries(queries if queries is not None else [])
        if model_id is not None and (not isinstance(model_id, str) or not model_id or len(model_id) > 100):
            raise ValueError('请选择有效的查询模型')
        settings, warnings = self.get_settings(), []
        # Validate manual seeds before any billable query expansion.
        with self.store.connect() as db:
            originals = {row['id']: row['current_revision'] for row in db.execute(
                'SELECT id,current_revision FROM sources WHERE id IN (SELECT value FROM json_each(?)) AND current_revision IS NOT NULL', (dumps(ids),))}
        if len(originals) != len(ids):
            raise ValueError('部分已选资料不存在，请刷新资料列表')
        recommendation_id = ident('recommendation')
        query_prompt = None
        if not explicit and settings['queryExpansion']:
            from .prompts import snapshot as prompt_snapshot
            with self.store.connect() as db:
                query_prompt = prompt_snapshot(db, ('retrieval_query_expansion',))
        queries, origin = self._plan(topic, brief, model_id, explicit, settings, warnings, recommendation_id,
                                     query_prompt=query_prompt)
        tokens = _tokens(' '.join(discovery_queries(queries)))
        statuses = {key: ('ready', '检索当前版本的已保存内容') if settings['channels'][key] else ('disabled', '已在检索配置中关闭') for key in CHANNELS}
        anchors, reasons = {}, {}
        with self.store.connect() as db:
            scores = self._lexical(db, tokens, settings, statuses, warnings)
            try:
                self._wiki(db, tokens, settings, scores, anchors, reasons, statuses, warnings)
            except Exception:
                for key in ('claims', 'facts', 'graph'):
                    if settings['channels'][key]:
                        scores[key] = {}
                        statuses[key] = ('unavailable', '当前派生索引不可用，其他通道继续工作')
                warnings.append('观点、事实或图谱索引读取失败，已继续关键词、分类与摘要检索。')
            preliminary = {}
            for values in scores.values():
                for rank, source_id in enumerate(sorted(values, key=lambda sid: (-values[sid], sid))):
                    preliminary[source_id] = preliminary.get(source_id, 0) + 1 / (20 + rank)
            candidates = sorted(preliminary, key=lambda sid: (-preliminary[sid], sid))
            candidate_ids = list(dict.fromkeys([*candidates, *ids]))[:settings['candidateLimit']]
            if settings['channels']['vector']:
                # A bounded recent-document supplement permits genuine semantic
                # matches even when the query shares no lexical terms.
                recent = [row['id'] for row in db.execute('SELECT id FROM sources WHERE current_revision IS NOT NULL ORDER BY updated_at DESC,id LIMIT ?', (settings['candidateLimit'],))]
                candidate_ids = list(dict.fromkeys([*candidate_ids, *recent]))[:settings['candidateLimit']]
            rows = db.execute('''SELECT s.id sourceId,r.id revisionId,coalesce(nullif(c.title,''),r.title) title,
                coalesce(nullif(c.summary,''),r.summary) summary,substr(r.text_content,1,2400) vectorText,
                r.title originalTitle,s.tags_json tagsJson,s.classification_paths_json pathsJson,
                substr(r.text_content,1,60000) relevanceText
                FROM sources s JOIN revisions r ON r.id=s.current_revision
                LEFT JOIN document_chinese c ON c.revision_id=r.id
                WHERE s.id IN (SELECT value FROM json_each(?))''', (dumps(candidate_ids),)).fetchall()
            documents = {row['sourceId']: dict(row) for row in rows}
        vector_result = self._vector([documents[sid] for sid in candidate_ids if sid in documents], queries, settings, scores, statuses, warnings)
        vector_model = vector_result['model']
        vector_matches = vector_result['matches']
        # Cached vectors from all current articles are searchable independently
        # of the lexical pool. Materialize only the best article candidates.
        extra_ids = [sid for sid in sorted(scores['vector'], key=lambda sid: (-scores['vector'][sid], sid))
                     [:settings['candidateLimit']] if sid not in documents]
        if extra_ids:
            with self.store.connect() as db:
                extra_rows = db.execute('''SELECT s.id sourceId,r.id revisionId,
                    coalesce(nullif(c.title,''),r.title) title,coalesce(nullif(c.summary,''),r.summary) summary,
                    r.title originalTitle,s.tags_json tagsJson,s.classification_paths_json pathsJson,
                    substr(r.text_content,1,60000) relevanceText
                    FROM sources s JOIN revisions r ON r.id=s.current_revision
                    LEFT JOIN document_chinese c ON c.revision_id=r.id
                    WHERE s.id IN (SELECT value FROM json_each(?))''', (dumps(extra_ids),)).fetchall()
                documents.update({row['sourceId']: dict(row) for row in extra_rows})
        # Recall-oriented OR search and graph traversal discover candidates;
        # neither a channel rank nor a positive cosine establishes relevance.
        groups = query_groups(queries, topic=topic, origin=origin)
        best_vector = max(scores['vector'].values(), default=0)
        relevance = {}
        for source_id, document in documents.items():
            fields = [document['title'], document['originalTitle'], document['summary'],
                      category_names(document['tagsJson'], document['pathsJson'])]
            aligned = topic_alignment(topic, fields)
            # Published grounded claims and directly matched labels can supply
            # synonyms missing from the article metadata. A graph hop alone
            # cannot qualify its target as a recommendation.
            fields.extend(reason.split('：', 1)[-1] for reason in reasons.get(source_id, [])
                          if reason.startswith(('提取观点：', '提取事实：', '图谱词条直接命中：')))
            lexical = lexical_relevance(fields, document['relevanceText'], groups)
            # A segment near the end of a long article can supply the exact
            # topic match even when it lies beyond the lexical preview budget.
            for match in vector_matches.get(source_id, []):
                if match['kind'] == 'passage':
                    lexical = max(lexical, lexical_relevance([], match['matchText'], groups))
            vector = scores['vector'].get(source_id, 0)
            # A same-language expansion is an angle on the topic. It cannot
            # qualify an article solely through a secondary ambiguous phrase
            # (e.g. an MCP software server in a chip-supply search).
            cross_language = bool(re.search(r'[\u4e00-\u9fff]', topic)) and any(
                re.search(r'[A-Za-z]{3,}', query) and not re.search(r'[\u4e00-\u9fff]', query) for query in queries)
            if not aligned and not cross_language:
                # A concrete subtopic may be relevant even when the umbrella
                # term is absent (e.g. Gaza aid under Middle East affairs).
                # It must match actual text coherently; combining unrelated
                # metadata or generated labels cannot establish that link.
                lexical = max(lexical_relevance([field], '', groups) for field in fields[:3])
                lexical = max(lexical, lexical_relevance([], document['relevanceText'], groups))
                lexical = max([lexical, *(lexical_relevance([], match['matchText'], groups)
                    for match in vector_matches.get(source_id, []) if match['kind'] == 'passage')])
            if lexical or semantic_relevance(vector, best_vector, vector_model):
                relevance[source_id] = max(lexical, vector)
        fused, channel_hits = {}, {}
        enabled_count = max(1, sum(settings['channels'].values()))
        for channel in CHANNELS:
            if channel == 'vector':
                # Four independent rankings share the one vector channel's
                # total weight. More passages never create more article votes.
                for kind in KINDS:
                    values = vector_result['sourceScores'].get(kind, {})
                    for rank, source_id in enumerate(sorted(values, key=lambda sid: (-values[sid], sid))):
                        if source_id in relevance:
                            fused[source_id] = fused.get(source_id, 0) + 20 / (20 + rank) / enabled_count / len(KINDS)
                            if 'vector' not in channel_hits.setdefault(source_id, []):
                                channel_hits[source_id].append('vector')
                continue
            for rank, source_id in enumerate(sorted(scores[channel], key=lambda sid: (-scores[channel][sid], sid))):
                if source_id in relevance:
                    fused[source_id] = fused.get(source_id, 0) + 20 / (20 + rank) / enabled_count
                    channel_hits.setdefault(source_id, []).append(channel)
        chosen = sorted((sid for sid in fused if fused[sid] >= settings['minimumScore']),
                        key=lambda sid: (-(.7 * relevance[sid] + .3 * fused[sid]), sid))[:settings['resultLimit']]
        baseline_chosen = list(chosen)
        rerank_execution = {'status': 'disabled'}
        rerank_packets, rerank_fused, rerank_hits = [], {}, {}
        if settings['rerankEnabled']:
            chosen, rerank_execution, rerank_packets, rerank_fused, rerank_hits = rerank(
                self, topic=topic, queries=queries, documents=documents, scores=scores,
                vector_result=vector_result, anchors=anchors, settings=settings,
                chosen=chosen, recommendation_id=recommendation_id, kinds=KINDS)
            if rerank_execution['status'] == 'fallback':
                warnings.append(FALLBACK_WARNING)
        items = []
        with self.store.connect() as db:
            # All provider IO is finished. Hold one short transaction while
            # checking published dependencies and saving the selectable result,
            # so a same-revision summary/Wiki replacement cannot slip between.
            db.execute('BEGIN IMMEDIATE')
            if rerank_execution['status'] == 'succeeded':
                if not dependencies_current(db, rerank_packets):
                    # A ranking across changed inputs cannot be published as a
                    # successful use of the frozen request. Restore old order.
                    chosen = baseline_chosen
                    rerank_execution.update(status='fallback', failure='input-dependencies-changed', returnCount=len(baseline_chosen))
                    warnings.append(CHANGED_WARNING)
                    if self.operations:
                        self.operations.discard_rerank(db, recommendation_id, CHANGED_WARNING)
                else:
                    fused, channel_hits = rerank_fused, rerank_hits
            fresh_units = {unit['unitId']: unit['dependencyHash'] for unit in
                           iter_units(db, [sid for sid in chosen if vector_matches.get(sid)])}
            for source_id in chosen:
                document = documents[source_id]
                if not db.execute('SELECT 1 FROM sources WHERE id=? AND current_revision=?', (source_id, document['revisionId'])).fetchone():
                    warnings.append('检索期间有资料版本更新，已排除旧结果；请重新检索。')
                    continue
                if any(fresh_units.get(match['unitId']) != match['dependencyHash']
                       for match in vector_matches.get(source_id, [])):
                    warnings.append('检索期间有资料版本更新或加工结果变化，已排除旧结果；请重新检索。')
                    continue
                # Bound materialized text per candidate, preserving exact quotes.
                blocks = db.execute('SELECT id,text FROM blocks WHERE revision_id=? ORDER BY ordinal LIMIT 500', (document['revisionId'],)).fetchall()
                matched_block_ids = list(dict.fromkeys(block_id for match in vector_matches.get(source_id, []) for block_id in match['blockIds']))
                if matched_block_ids:
                    existing_ids = {block['id'] for block in blocks}
                    blocks.extend(block for block in db.execute('SELECT id,text FROM blocks WHERE revision_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY ordinal',
                        (document['revisionId'], dumps(matched_block_ids))) if block['id'] not in existing_ids)
                block_map = {block['id']: block['text'] for block in blocks}
                evidence = {}
                for channel, ref in anchors.get(source_id, []):
                    text = block_map.get(ref.get('blockId'), '')
                    quote = ref.get('quote', '')
                    if ref.get('revisionId') == document['revisionId'] and quote and quote in text:
                        evidence.setdefault(ref['blockId'], {'quote': quote[:600], 'channels': set()})['channels'].add(channel)
                for match in vector_matches.get(source_id, []):
                    for ref in match['evidence']:
                        text = block_map.get(ref['blockId'], '')
                        if ref['revisionId'] != document['revisionId'] or not ref['quote'] or ref['quote'] not in text:
                            continue
                        old = evidence.get(ref['blockId'])
                        if old is None or match['cosine'] > old.get('vectorScore', 0):
                            # Passage units are bounded at construction. Keep
                            # their complete exact anchor, including a match
                            # near the end, rather than truncating its evidence.
                            evidence[ref['blockId']] = {'quote': ref['quote'],
                                'channels': (old['channels'] if old else set()) | {'vector'}, 'vectorScore': match['cosine']}
                        else:
                            old['channels'].add('vector')
                passages = []
                for block in blocks:
                    value = _match(block['text'], tokens)
                    anchor = evidence.get(block['id'])
                    if not value and not anchor:
                        continue
                    start = max(0, min((block['text'].casefold().find(token) for token in tokens if token in block['text'].casefold()), default=0) - 80)
                    quote = anchor['quote'] if anchor else block['text'][start:start + 600]
                    channels = set(anchor['channels']) if anchor else set()
                    if value and 'keyword' in channel_hits[source_id]:
                        channels.add('keyword')
                    passages.append({'blockId': block['id'], 'quote': quote, 'score': round(value + (5 if anchor else 0), 4),
                        'channels': sorted(channels), **({'vectorScore': anchor['vectorScore']} if anchor and 'vectorScore' in anchor else {})})
                preview_only = not passages and bool(blocks)
                if preview_only:
                    passages = [{'blockId': blocks[0]['id'], 'quote': blocks[0]['text'][:600], 'score': 0, 'channels': []}]
                passages.sort(key=lambda part: (-part.get('vectorScore', 0), -part['score'], part['blockId']))
                why = list(dict.fromkeys(reasons.get(source_id, [])))[:3]
                why = [f"{LABELS[channel]}命中" for channel in channel_hits[source_id]] + why
                if preview_only:
                    why.append('匹配在文章层面，所示为开头原文预览，请自行确认段落相关性')
                if 'vector' in channel_hits[source_id]:
                    why.extend(SOURCE_LABELS[kind] + '语义匹配' for kind in KINDS
                               if any(match['kind'] == kind for match in vector_matches.get(source_id, [])))
                items.append({key: document[key] for key in ('sourceId', 'revisionId', 'title', 'summary')} |
                             {'score': round(fused[source_id], 6), 'channels': channel_hits[source_id], 'reasons': why,
                              'passages': passages[:settings['passagesPerSource']],
                              'vectorMatches': [{key: match[key] for key in ('kind', 'unitId', 'cosine', 'blockIds', 'spans')}
                                                for match in vector_matches.get(source_id, [])]})
            result = {'id': recommendation_id, 'topic': topic, 'queries': queries, 'queryOrigin': origin,
                      'items': items, 'channels': [{'id': key, 'status': statuses[key][0], 'detail': statuses[key][1]} for key in CHANNELS],
                      'warnings': list(dict.fromkeys(warnings)), 'settings': settings,
                      'vectorSources': vector_result['stats'],
                      'rerank': {'enabled': settings['rerankEnabled'], 'status': rerank_execution['status']}}
            snapshot = dict(result, selectionVersions={**originals, **{item['sourceId']: item['revisionId'] for item in items}})
            snapshot['rerankExecution'] = rerank_execution
            if query_prompt is not None and origin == 'model':
                snapshot['queryPromptSnapshot'] = copy.deepcopy(query_prompt)
            db.execute('INSERT INTO retrieval_snapshots VALUES(?,?,?)', (result['id'], dumps(snapshot), now()))
        return result
