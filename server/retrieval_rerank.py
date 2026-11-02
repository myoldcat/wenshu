"""Validated optional Qwen3 reranking over the raw discovery candidate pool.

There is deliberately no score cache and no probability threshold. The normal
retriever's qualified results survive a bounded union with reranked discoveries;
model failure restores the normal retriever's exact ordering.
"""
from __future__ import annotations

from contextlib import nullcontext
import json
import math
import os
import time
from urllib.parse import urlsplit

import httpx

from .model_gateway import ModelError, normalize_base_url
from .retrieval_rerank_packets import INSTRUCTION, PACKET_VERSION, digest, prepare_packet, query_text


MODEL = 'Qwen/Qwen3-Reranker-4B'
MODEL_REVISION = '22e683669bc0f0bd69640a1354a6d0aebcfeede5'
TEMPLATE_VERSION = 'qwen-official-transformers-parts-1'
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 125
FALLBACK_WARNING = '增强排序暂不可用，已按常规方式推荐，可稍后重试。'
CHANGED_WARNING = '检索期间有资料或加工结果变化，已按常规方式推荐；请重新检索。'


def _provider_failure(category=None):
    error = ModelError(FALLBACK_WARNING)
    if category is not None:
        error.automaticRetryCategory = category
    return error


class RerankClient:
    def __init__(self, url=None, transport=None):
        self.url = url
        self.transport = transport

    def score(self, payload, *, operations=None, recommendation_id=None):
        configured = self.url if self.url is not None else os.environ.get('WENSHU_RERANK_URL', 'http://127.0.0.1:8788')
        if not configured:
            raise ModelError(FALLBACK_WARNING)
        base = normalize_base_url(configured)
        endpoint = urlsplit(base)
        if endpoint.hostname not in ('127.0.0.1', 'localhost', '::1') or endpoint.path:
            raise ModelError(FALLBACK_WARNING)
        config = {'id': 'qwen3-reranker-4b', 'version': 1, 'name': 'Qwen3 增强排序',
                  'model': MODEL, 'phase': 'rerank', 'timeoutSeconds': TIMEOUT_SECONDS}
        scope = (operations.capture_rerank(recommendation_id, config)
                 if operations and recommendation_id else nullcontext(None))
        raw, body, status_code, truncated = bytearray(), None, None, False
        started = time.monotonic()
        with scope as capture:
            call_id = capture.start(payload, config, 1, 'rerank_http') if capture else None
            status = 'failed'
            try:
                with httpx.Client(transport=self.transport, trust_env=False, follow_redirects=False,
                                  timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=5)) as client:
                    with client.stream('POST', base + '/v1/rerank', json=payload) as response:
                        status_code = response.status_code
                        for part in response.iter_bytes():
                            raw.extend(part)
                            if len(raw) > MAX_RESPONSE_BYTES or time.monotonic() - started > TIMEOUT_SECONDS:
                                del raw[MAX_RESPONSE_BYTES:]
                                truncated = True
                                raise _provider_failure(None if len(raw) >= MAX_RESPONSE_BYTES else 'network')
                if not 200 <= status_code < 300:
                    try:
                        body = json.loads(raw)
                    except (ValueError, TypeError):
                        pass
                    raise _provider_failure('network' if status_code in (408, 429, 500, 502, 503, 504) else None)
                try:
                    body = json.loads(raw)
                except (ValueError, TypeError):
                    raise _provider_failure('format_error') from None
                # A deliberately different server/model protocol needs a
                # configuration fix, rather than another identical request.
                if isinstance(body, dict) and any(key in body and body[key] != expected for key, expected in (
                        ('modelRevision', MODEL_REVISION), ('templateVersion', TEMPLATE_VERSION))):
                    raise ModelError(FALLBACK_WARNING)
                try:
                    ordered = self._validate(body, payload)
                except ModelError as exc:
                    exc.automaticRetryCategory = 'format_error'
                    raise
                status = 'succeeded'
                if capture:
                    capture.validated()
                return ordered, body, {'httpStatus': status_code,
                    'requestHash': digest(payload), 'responseHash': digest(body),
                    'durationMs': round((time.monotonic() - started) * 1000)}
            except ModelError:
                raise
            except httpx.HTTPError as exc:
                transient = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))
                raise _provider_failure('network' if transient else None) from None
            except (ValueError, TypeError, KeyError):
                raise ModelError(FALLBACK_WARNING) from None
            finally:
                if capture and call_id:
                    capture.finish(call_id, raw=bytes(raw) if raw else None, body=body, status=status,
                        http_status=status_code, error=None if status == 'succeeded' else FALLBACK_WARNING,
                        duration_ms=round((time.monotonic() - started) * 1000), truncated=truncated)

    @staticmethod
    def _validate(body, payload):
        if not isinstance(body, dict) or body.get('modelRevision') != MODEL_REVISION or body.get('templateVersion') != TEMPLATE_VERSION:
            raise ModelError(FALLBACK_WARNING)
        values = body.get('scores')
        ids = {item['id'] for item in payload['documents']}
        if not isinstance(values, list) or len(values) != len(ids):
            raise ModelError(FALLBACK_WARNING)
        found = {}
        for row in values:
            if not isinstance(row, dict) or row.get('id') not in ids or row['id'] in found:
                raise ModelError(FALLBACK_WARNING)
            for field in ('score', 'logitDiff'):
                value = row.get(field)
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                    raise ModelError(FALLBACK_WARNING)
            if not 0 <= row['score'] <= 1:
                raise ModelError(FALLBACK_WARNING)
            tokens = row.get('inputTokens')
            if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= 4096 or not isinstance(row.get('truncated'), bool):
                raise ModelError(FALLBACK_WARNING)
            found[row['id']] = row
        return sorted(found, key=lambda sid: (-found[sid]['score'], -found[sid]['logitDiff'], sid))


def raw_fusion(scores, vector_result, channels, kinds):
    """Positive ten-path union, with the current one-channel vector budget."""
    fused, hits = {}, {}
    enabled_count = max(1, sum(channels.values()))
    for channel, values in scores.items():
        rankings = [values] if channel != 'vector' else [vector_result['sourceScores'].get(kind, {}) for kind in kinds]
        for ranking in rankings:
            positive = [sid for sid in sorted(ranking, key=lambda sid: (-ranking[sid], sid)) if ranking[sid] > 0]
            for rank, sid in enumerate(positive):
                fused[sid] = fused.get(sid, 0) + 20 / (20 + rank) / enabled_count / (len(kinds) if channel == 'vector' else 1)
                if channel not in hits.setdefault(sid, []):
                    hits[sid].append(channel)
    return fused, hits


def merge_ranked_candidates(baseline, ranked, fused, minimum_score, result_limit):
    """Keep qualified baseline results while admitting bounded discoveries.

    K is the unique baseline count. Consider the reranker's first K candidates
    that meet the existing fusion-score threshold (baseline entries already
    qualified upstream). Preserve the baseline, fill available result slots
    from those new discoveries, then order the whole union by the reranker.
    No result is added merely to fill the configured result limit.
    """
    baseline = list(dict.fromkeys(baseline))
    ranked = list(dict.fromkeys(ranked))
    baseline_ids = set(baseline)
    if len(baseline) > result_limit or not baseline_ids.issubset(ranked):
        raise ValueError('排序结果无法保留常规候选')
    eligible = [sid for sid in ranked if sid in baseline_ids or fused.get(sid, 0) >= minimum_score]
    top = eligible[:len(baseline)]
    additions = [sid for sid in top if sid not in baseline_ids]
    admitted = additions[:result_limit - len(baseline)]
    selected_ids = baseline_ids | set(admitted)
    selected = [sid for sid in ranked if sid in selected_ids]
    return selected, {'baselineCount': len(baseline), 'rerankTopCount': len(top),
        'unionCandidateCount': len(baseline) + len(additions),
        'addedSourceIds': admitted, 'budgetExcludedSourceIds': additions[len(admitted):],
        'minimumScoreExcludedSourceIds': [sid for sid in ranked if sid not in eligible],
        'overflowPolicy': 'preserve-baseline-fill-rerank', 'returnCount': len(selected)}


def rerank(service, *, topic, queries, documents, scores, vector_result, anchors,
           settings, chosen, recommendation_id, kinds):
    execution = {'status': 'skipped', 'packetVersion': PACKET_VERSION,
                 'modelRevision': MODEL_REVISION, 'templateVersion': TEMPLATE_VERSION,
                 'scoreCache': False, 'selectionPolicy': 'baseline-preserving-bounded-union-v1',
                 'baselineChosen': list(chosen), 'returnCount': len(chosen)}
    if not chosen:
        execution['skipReason'] = 'no-normal-results'
        return list(chosen), execution, [], {}, {}
    raw, hits = raw_fusion(scores, vector_result, settings['channels'], kinds)
    ranked = sorted(raw, key=lambda sid: (-raw[sid], sid))
    candidates = list(dict.fromkeys([*chosen, *ranked]))[:settings['candidateLimit']]
    execution.update(rawCandidateCount=len(ranked), candidateCount=len(candidates),
                     trimmedCandidateCount=max(0, len(ranked) - len(candidates)),
                     candidateSourceIds=candidates,
                     rawCandidates=[{'sourceId': sid, 'fusionScore': raw[sid], 'channels': hits[sid]}
                                    for sid in ranked])
    started = time.monotonic()
    packets = []
    try:
        with service.store.connect() as db:
            missing = [sid for sid in candidates if sid not in documents]
            if missing:
                rows = db.execute('''SELECT s.id sourceId,r.id revisionId,
                    coalesce(nullif(c.title,''),r.title) title,
                    coalesce(nullif(c.summary,''),r.summary) summary,r.title originalTitle
                    FROM sources s JOIN revisions r ON r.id=s.current_revision
                    LEFT JOIN document_chinese c ON c.revision_id=r.id
                    WHERE s.id IN (SELECT value FROM json_each(?))''',
                    (json.dumps(missing),)).fetchall()
                documents.update({row['sourceId']: dict(row) for row in rows})
            if any(sid not in documents for sid in candidates):
                raise ValueError('排序候选版本已变化')
            packets = [prepare_packet(db, sid, documents[sid], topic, queries,
                vector_result['matches'].get(sid, []), anchors.get(sid, [])) for sid in candidates]
        payload = {'query': query_text(topic, queries), 'documents': [
            {'id': packet['sourceId'], 'documentParts': packet['parts']} for packet in packets]}
        # All query, packet, metadata and original-span dependencies are frozen
        # here. No score is reused across requests or model/template versions.
        execution.update(queryHash=digest(payload['query']), instruction=INSTRUCTION,
            packets=packets, requestHash=digest(payload),
            prepareMs=round((time.monotonic() - started) * 1000))
        ranked, body, call = service.reranker.score(payload, operations=service.operations,
                                                   recommendation_id=recommendation_id)
        selected, selection = merge_ranked_candidates(chosen, ranked, raw,
            settings['minimumScore'], settings['resultLimit'])
        execution.update(status='succeeded', response=body, call=call,
                         durationMs=round((time.monotonic() - started) * 1000),
                         rankedSourceIds=ranked, **selection)
        return selected, execution, packets, raw, hits
    except Exception:
        # Never expose provider bodies, URLs or credentials in a business UI.
        # Full real wire responses are held only in the operations record.
        execution.update(status='fallback', failure='service-or-packet-unavailable',
                         durationMs=round((time.monotonic() - started) * 1000))
        return list(chosen), execution, [], {}, {}
