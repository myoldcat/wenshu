"""Four revision-bound vector indexes sharing one bounded embedding provider.

The complete current unit catalog is searched, including cached units outside
lexical candidates. New embeddings are scheduled fairly across kinds/articles;
incomplete indexing is explicit. Derived statements never become quotations.
"""
from collections import defaultdict, deque
import hashlib
import math
import re
import struct
import time

import numpy as np

from .model_gateway import ModelError
from .retrieval_relevance import contains, discovery_text, terms
from .retrieval_vector_units import KINDS, iter_units
from .store import dumps, now


INDEX_VERSION = 'wenshu-vector-sources-1'
MAX_NEW_VECTOR_UNITS = 128
MAX_VECTOR_SECONDS = 45
MAX_CATALOG_UNITS = 50000
MAX_MATCHES_PER_KIND = 2
SOURCE_LABELS = {'passage': '原文段落', 'title_summary': '标题与摘要',
                 'claim': '提取观点', 'fact': '提取事实'}
VECTOR_SCHEMA = '''
CREATE TABLE IF NOT EXISTS retrieval_vector_units(
 cache_key TEXT PRIMARY KEY, model_id TEXT NOT NULL, model_version INTEGER NOT NULL,
 source_id TEXT NOT NULL, revision_id TEXT NOT NULL, unit_id TEXT NOT NULL,
 kind TEXT NOT NULL, dependency_hash TEXT NOT NULL, dimensions INTEGER NOT NULL,
 vector_blob BLOB NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS retrieval_vector_units_source
 ON retrieval_vector_units(source_id,revision_id,kind);
'''


def embedding_text(unit):
    """Clean only the discovery copy; immutable evidence keeps original bytes."""
    text = re.sub(r'https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+', ' ', unit['text'], flags=re.I)
    text = re.sub(r'[（(][^()（）\n]{0,120}(?:图片|圖片|image)[^()（）\n]{0,120}[）)]', ' ', text, flags=re.I)
    return re.sub(r'(?:图片|圖片)[^。\n]{0,50}(?:AI|人工智能|生成式)[^。\n]{0,80}(?:。|\n|$)', ' ', text, flags=re.I)


def cache_key(config, unit):
    return hashlib.sha256(dumps([INDEX_VERSION, config['id'], config['version'],
        unit['kind'], unit['dependencyHash'], embedding_text(unit)]).encode()).hexdigest()


def catalog(db, source_ids=None, kinds=None):
    units, excluded, truncated = [], defaultdict(int), False
    for unit in iter_units(db, source_ids, kinds=kinds):
        if len(units) >= MAX_CATALOG_UNITS:
            truncated = True
            break
        # Marker-only units (e.g. an image credit or URL) carry no subject.
        if not re.search(r'[a-z\u4e00-\u9fff]', discovery_text(unit['matchText'])):
            excluded[unit['kind']] += 1
            continue
        units.append(unit)
    return units, dict(excluded), truncated


def encode_vector(vector):
    return struct.pack('<' + 'f' * len(vector), *vector)


def decode_vector(blob, dimensions):
    if not isinstance(dimensions, int) or not 1 <= dimensions <= 8192 or len(blob) != dimensions * 4:
        raise ValueError('invalid vector cache')
    vector = np.frombuffer(blob, dtype='<f4')
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(vector).all() or not math.isfinite(norm) or norm < 1e-8:
        raise ValueError('invalid vector cache')
    return vector / norm


def scheduled_units(units, queries=(), candidates=()):
    """Round-robin source kinds and articles, prioritizing actual query hits."""
    tokens = list(dict.fromkeys(term for query in queries for term in terms(query)))[:40]
    priority = {item['sourceId']: index for index, item in enumerate(candidates)}
    buckets = {kind: defaultdict(list) for kind in KINDS}
    for unit in units:
        strength = sum(contains(discovery_text(unit['matchText']), token) for token in tokens)
        buckets[unit['kind']][unit['sourceId']].append((strength, unit))
    rings = {}
    for kind in KINDS:
        groups = buckets[kind]
        ordered = sorted(groups, key=lambda sid: (-max(pair[0] for pair in groups[sid]),
                                                   priority.get(sid, len(priority)), sid))
        rings[kind] = deque(deque(unit for _, unit in sorted(groups[sid],
            key=lambda pair: (-pair[0], pair[1]['unitId']))) for sid in ordered)
    while any(rings.values()):
        for kind in KINDS:
            if not rings[kind]:
                continue
            queue = rings[kind].popleft()
            yield queue.popleft()
            if queue:
                rings[kind].append(queue)


class VectorRetrievalMixin:
    def _vector_cached(self, config, units, *, dimensions=None):
        valid, pending, invalid = {}, [], []
        with self.store.connect() as db:
            for unit in units:
                key = cache_key(config, unit)
                row = db.execute('SELECT dimensions,vector_blob FROM retrieval_vector_units WHERE cache_key=?', (key,)).fetchone()
                try:
                    if row is None:
                        raise LookupError()
                    vector = decode_vector(row['vector_blob'], row['dimensions'])
                    if dimensions is not None and len(vector) != dimensions:
                        raise ValueError('changed embedding dimensions')
                    valid[unit['unitId']] = key
                except LookupError:
                    pending.append(unit)
                except ValueError:
                    pending.append(unit)
                    invalid.append(key)
            if invalid:
                db.executemany('DELETE FROM retrieval_vector_units WHERE cache_key=?', [(key,) for key in invalid])
        return valid, pending, len(invalid)

    def _vector_warm(self, config, pending, *, queries=(), candidates=(), max_new_units=MAX_NEW_VECTOR_UNITS,
                     dimensions=None, started=None):
        if isinstance(max_new_units, bool) or not isinstance(max_new_units, int) or not 0 <= max_new_units <= MAX_NEW_VECTOR_UNITS:
            raise ValueError('向量单元预算不正确')
        selected = []
        for unit in scheduled_units(pending, queries, candidates):
            if len(selected) >= max_new_units:
                break
            selected.append(unit)
        completed, stopped = 0, False
        for offset in range(0, len(selected), 16):
            if started is not None and time.monotonic() - started >= MAX_VECTOR_SECONDS:
                stopped = True
                break
            batch = selected[offset:offset + 16]
            vectors = self._embed(config, [embedding_text(unit) for unit in batch])
            if len({len(vector) for vector in vectors}) != 1 or dimensions is not None and any(len(v) != dimensions for v in vectors):
                raise ModelError('查询与分段向量维度不匹配')
            dimensions = dimensions or len(vectors[0])
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for unit, vector in zip(batch, vectors):
                    if not db.execute('SELECT 1 FROM sources WHERE id=? AND current_revision=?',
                                      (unit['sourceId'], unit['revisionId'])).fetchone():
                        continue
                    db.execute('INSERT OR REPLACE INTO retrieval_vector_units VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (cache_key(config, unit), config['id'], config['version'], unit['sourceId'], unit['revisionId'],
                         unit['unitId'], unit['kind'], unit['dependencyHash'], len(vector), encode_vector(vector), now()))
            completed += len(batch)
        return completed, stopped

    def warm_vector_index(self, *, max_new_units=MAX_NEW_VECTOR_UNITS, source_ids=None, kinds=None):
        """Explicit bounded maintenance, used by isolated acceptance scripts only."""
        settings = self.get_settings()
        if not settings['embeddingModelId']:
            raise ValueError('尚未绑定向量模型')
        config = self.models.resolve(settings['embeddingModelId'])
        with self.store.connect() as db:
            units, excluded, truncated = catalog(db, source_ids, kinds)
        _, pending, _ = self._vector_cached(config, units)
        completed, stopped = self._vector_warm(config, pending, max_new_units=max_new_units,
                                               started=time.monotonic())
        counts = {kind: sum(unit['kind'] == kind for unit in units) for kind in KINDS}
        valid, pending, _ = self._vector_cached(config, units)
        return {'totalUnits': len(units), 'indexedUnits': len(valid), 'pendingUnits': len(pending),
                'newUnits': completed, 'timeBudgetReached': stopped, 'catalogTruncated': truncated,
                'totalBySource': counts, 'excludedBySource': excluded}

    def _vector(self, candidates, queries, settings, scores, statuses, warnings):
        empty = {'model': '', 'matches': {}, 'sourceScores': {}, 'stats': []}
        if not settings['channels']['vector']:
            return empty
        if not settings['embeddingModelId']:
            statuses['vector'] = ('unavailable', '尚未绑定支持 Embeddings 的向量模型')
            warnings.append('向量检索未配置，已继续其他可用检索通道。')
            return empty
        from .retrieval import _embedding_query
        started = time.monotonic()
        stats, matches, by_kind = [], defaultdict(list), {kind: {} for kind in KINDS}
        try:
            config = self.models.resolve(settings['embeddingModelId'])
            with self.store.connect() as db:
                units, excluded, truncated = catalog(db)
            if not units:
                statuses['vector'] = ('ready', '当前没有可用于语义检索的资料内容')
                return empty | {'stats': [dict(id=kind, totalUnits=0, indexedUnits=0, pendingUnits=0,
                    matchedSources=0, matchedUnits=0, status='empty') for kind in KINDS]}
            query = np.asarray(self._embed(config, [_embedding_query(config, queries)])[0], dtype=np.float32)
            _, pending, invalid = self._vector_cached(config, units, dimensions=len(query))
            warm_failed = False
            try:
                new, stopped = self._vector_warm(config, pending, queries=queries, candidates=candidates,
                    dimensions=len(query), started=started)
            except Exception:
                # A failed new batch must not erase independent sources that
                # already have valid current vectors. No provider retry here.
                new, stopped, warm_failed = 0, False, True
            qwen = config['model'].casefold().split(':')[0] == 'qwen3-embedding'
            floor = .4 if qwen else .5
            totals, indexed, hit_units = defaultdict(int), defaultdict(int), defaultdict(int)
            top = defaultdict(list)
            with self.store.connect() as db:
                for unit in units:
                    totals[unit['kind']] += 1
                    row = db.execute('SELECT dimensions,vector_blob FROM retrieval_vector_units WHERE cache_key=?',
                                     (cache_key(config, unit),)).fetchone()
                    if row is None:
                        continue
                    try:
                        vector = decode_vector(row['vector_blob'], row['dimensions'])
                        if len(vector) != len(query):
                            continue
                    except ValueError:
                        continue
                    indexed[unit['kind']] += 1
                    cosine = float(np.dot(query, vector))
                    if cosine < floor:
                        continue
                    hit_units[unit['kind']] += 1
                    match = dict(unit, cosine=min(1.0, cosine))
                    group = top[(unit['sourceId'], unit['kind'])]
                    group.append(match)
                    group.sort(key=lambda item: (-item['cosine'], item['unitId']))
                    del group[MAX_MATCHES_PER_KIND:]
            # A publication/summary/revision can change during HTTP IO. Rebuild
            # only hit articles and require the exact current dependency hash.
            hit_sources = {source for source, _ in top}
            with self.store.connect() as db:
                fresh = {unit['unitId']: unit['dependencyHash'] for unit in iter_units(db, hit_sources)}
            raced = 0
            for (source_id, kind), values in top.items():
                for match in values:
                    if fresh.get(match['unitId']) != match['dependencyHash']:
                        raced += 1
                        continue
                    matches[source_id].append(match)
                    by_kind[kind][source_id] = max(by_kind[kind].get(source_id, 0), match['cosine'])
            for source_id, values in matches.items():
                scores['vector'][source_id] = max(value['cosine'] for value in values)
            incomplete = truncated or stopped or invalid or raced or warm_failed or any(indexed[kind] < totals[kind] for kind in KINDS)
            for kind in KINDS:
                stats.append({'id': kind, 'label': SOURCE_LABELS[kind], 'totalUnits': totals[kind],
                    'indexedUnits': indexed[kind], 'pendingUnits': totals[kind] - indexed[kind],
                    'matchedSources': len(by_kind[kind]),
                    'matchedUnits': sum(match['kind'] == kind for values in matches.values() for match in values),
                    'excludedUnits': excluded.get(kind, 0),
                    'status': 'empty' if not totals[kind] else ('degraded' if indexed[kind] < totals[kind] else 'ready')})
            statuses['vector'] = ('unavailable' if warm_failed and not sum(indexed.values()) else 'degraded' if incomplete else 'ready',
                '按原文段落、标题与摘要、提取观点、提取事实分别检索并合并')
            if incomplete:
                warnings.append('部分资料尚未完成语义索引或内容已更新，本次语义结果可能不完整；其他检索结果已保留。')
            if raced:
                warnings.append('检索期间有资料版本更新或加工结果变化，已排除旧的语义结果，请重新检索。')
            with self.store.connect() as db:
                db.execute('DELETE FROM retrieval_vector_units WHERE NOT EXISTS('
                    'SELECT 1 FROM sources s WHERE s.id=source_id AND s.current_revision=revision_id)')
                db.execute('DELETE FROM retrieval_vector_units WHERE cache_key IN ('
                    'SELECT cache_key FROM retrieval_vector_units ORDER BY created_at DESC,cache_key LIMIT -1 OFFSET 100000)')
            return {'model': config['model'], 'matches': dict(matches), 'sourceScores': by_kind, 'stats': stats,
                    'newUnits': new, 'catalogTruncated': truncated}
        except Exception:
            scores['vector'] = {}
            statuses['vector'] = ('unavailable', '向量请求或分段索引校验未完成，其他检索方式继续工作')
            warnings.append('语义检索未完成，请检查向量模型；已保留其他可用的检索结果。')
            return empty | {'stats': stats}
