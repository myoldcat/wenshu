"""Internal automatic calls retry before their existing safe fallbacks."""
import json
from types import SimpleNamespace

import httpx
import pytest

from server.model_gateway import ModelError
from server.retrieval import RetrievalService
from server.retrieval_rerank import RerankClient, MODEL_REVISION, TEMPLATE_VERSION, FALLBACK_WARNING
from server.store import dumps
from server.tests.test_writing_automatic import automatic, run_automatic
from server.tests.test_writing_image_tasks import images_env
from server.tests.test_writing_automatic_images import finish_picture_automation


def retries(store, owner, prefix):
    with store.connect() as db:
        rows = db.execute('SELECT * FROM writing_automatic_retries WHERE owner_run_id=? AND logical_key LIKE ? ORDER BY logical_key,attempt',
                          (owner, prefix + '%')).fetchall()
        return [dict(row) for row in rows]


@pytest.mark.parametrize('fault', ['network', 'format'])
def test_material_query_model_retries_twice_before_topic_fallback(automatic, fault):
    store, gateway, _, _ = automatic
    original, calls = gateway.complete, []

    def complete(config, messages):
        packet = json.loads(messages[-1]['content'])
        if packet['task'] == 'writing_automatic_material_queries':
            calls.append(packet)
            if len(calls) < 3:
                if fault == 'format':
                    return {'text': '{"queries":', 'usage': {}}
                raise TimeoutError('controlled query request timeout')
        return original(config, messages)

    gateway.complete = complete
    queued, current = run_automatic(automatic)
    rows = retries(store, queued['runId'], 'materials:queries')
    assert len(calls) == len(rows) == 3
    assert [row['status'] for row in rows] == ['failed', 'failed', 'succeeded']
    assert current['materialSelection']['queries'] and current['automatic']['report']['outputAvailable']
    assert store.job(queued['job']['id'])['attempts'] == 1
    assert gateway.config['apiKey'] not in dumps(rows)


@pytest.mark.parametrize('phase', ['embedding', 'rerank'])
def test_material_discovery_retries_before_channel_swallows_network_fault(automatic, monkeypatch, phase):
    store, _, _, _ = automatic
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(True)
        if len(calls) < 3:
            raise TimeoutError('controlled discovery channel timeout')
        return [[1., 0.]] if phase == 'embedding' else (['fixed-source'], {}, {})

    def recommend(retrieval, *args, **kwargs):
        # The normal service publishes other available channels after failure.
        try:
            if phase == 'embedding':
                retrieval._embed({'id': 'fixed-vector', 'version': 1, 'apiKey': 'vector-private'}, ['fixed query'])
            else:
                retrieval.reranker.score({'query': 'fixed query', 'documents': []})
        except Exception:
            return {'items': [], 'warnings': ['部分渠道未完成']}
        return {'items': [], 'warnings': []}

    monkeypatch.setattr(RetrievalService, '_embed', interrupted)
    monkeypatch.setattr(RerankClient, 'score', interrupted)
    monkeypatch.setattr(RetrievalService, 'recommend', recommend)
    queued, current = run_automatic(automatic)
    rows = retries(store, queued['runId'], 'materials:' + phase + ':')
    assert len(calls) == len(rows) == 3 and rows[-1]['status'] == 'succeeded'
    assert current['automatic']['report']['outputAvailable']
    assert 'vector-private' not in dumps(rows)


@pytest.mark.parametrize('legacy,failures,fault', [(False, 2, 'network'), (False, 2, 'format'), (False, 99, 'network'), (True, 99, 'network')])
def test_citation_batches_retry_before_preserving_unreviewed_citations(automatic, legacy, failures, fault):
    store, gateway, service, project = automatic
    original, calls = gateway.complete, []

    def complete(config, messages):
        packet = json.loads(messages[-1]['content'])
        if packet['task'] == 'writing_citation_match':
            calls.append(packet)
            if len(calls) <= failures:
                if fault == 'format':
                    return {'text': '{"assignments":', 'usage': {}}
                raise TimeoutError('controlled citation request timeout')
        return original(config, messages)

    gateway.complete = complete
    queued = service.queue_automatic(project['id'], project['version'])
    if legacy:
        with store.connect() as db:
            frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
            frozen.pop('automaticRetryPolicy', None)
            frozen.pop('automaticRetryOwnerRunId', None)
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    service.process(store.claim())
    current = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert all(section['content'] and section['evidence'] for section in current['sections'])
    if legacy:
        assert len(calls) == 2
        with store.connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='writing_automatic_retries'").fetchone():
                assert not db.execute('SELECT 1 FROM writing_automatic_retries WHERE owner_run_id=?', (queued['runId'],)).fetchone()
    else:
        rows = retries(store, queued['runId'], 'citations:')
        counts = {}
        for row in rows:
            counts.setdefault(row['logical_key'], []).append(row)
        assert len(counts) == 2 and max(map(len, counts.values())) == 3
        assert len(calls) == (4 if failures == 2 else 6)
    if failures == 99:
        assert current['automatic']['report']['review']['citationReviewCompleted'] is False
        assert all('[E1]' in section['content'] for section in current['sections'])
        assert all(item['status'] != 'supported' for section in current['sections'] for item in section['citationBindings']['items'])


def test_unsupported_citations_are_completed_business_results_not_retried(automatic):
    store, gateway, _, _ = automatic
    gateway.unsupported_body = True
    queued, current = run_automatic(automatic)
    rows = retries(store, queued['runId'], 'citations:')
    assert len(rows) == 2 and all(row['attempt'] == 1 and row['status'] == 'succeeded' for row in rows)
    assert all(item['status'] != 'supported' for section in current['sections'] for item in section['citationBindings']['items'])


@pytest.mark.parametrize('failures,fault', [(2, 'network'), (2, 'format'), (99, 'network')])
def test_picture_vision_retries_before_existing_quality_fallback(images_env, failures, fault):
    env = images_env
    original, calls = env.gateway.complete_vision, []

    def vision(config, messages):
        calls.append(messages)
        if len(calls) <= failures:
            if fault == 'format':
                return {'text': '{"items":', 'usage': {}}
            raise TimeoutError('controlled picture request timeout')
        return original(config, messages)

    env.gateway.complete_vision = vision
    before, current, queued = finish_picture_automation(env)
    rows = retries(env.store, queued['runId'], 'images:')
    assert len(calls) == len(rows) == 3
    assert [row['status'] for row in rows] == ['failed', 'failed', 'succeeded' if failures == 2 else 'failed']
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert current['sections'][0]['content'].replace('[E1]', '') == before['sections'][0]['content'].replace('[E1]', '')
    assert bool(current.get('illustrations')) is (failures == 2)
    assert env.gateway.config['apiKey'] not in dumps(rows)


def test_unreadable_picture_is_not_retried(images_env):
    env = images_env
    original = env.gateway.complete_vision

    def vision(config, messages):
        response = original(config, messages)
        result = json.loads(response['text'])
        for item in result['items']:
            for match in item['matches']:
                match['readable'] = False
        return {**response, 'text': dumps(result)}

    env.gateway.complete_vision = vision
    _, current, queued = finish_picture_automation(env)
    rows = retries(env.store, queued['runId'], 'images:')
    assert len(rows) == len(env.gateway.calls) == 1 and rows[0]['status'] == 'succeeded'
    assert not current.get('illustrations')


@pytest.mark.parametrize('status,body,category', [
    (503, b'unavailable', 'network'), (429, b'throttled', 'network'),
    (401, b'not-json', None), (400, b'not-json', None), (422, b'unsupported', None),
    (200, b'{"scores":', 'format_error'),
    (200, dumps({'modelRevision': MODEL_REVISION, 'templateVersion': TEMPLATE_VERSION, 'scores': []}).encode(), 'format_error'),
    (200, dumps({'modelRevision': 'different-config', 'templateVersion': TEMPLATE_VERSION, 'scores': []}).encode(), None),
])
def test_rerank_internal_classification_never_adds_global_retries(status, body, category):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, content=body)

    client = RerankClient(url='http://127.0.0.1:8788', transport=httpx.MockTransport(handle))
    with pytest.raises(ModelError) as caught:
        client.score({'query': 'topic', 'documents': [{'id': 'source-one', 'text': 'original'}]})
    assert str(caught.value) == FALLBACK_WARNING
    assert getattr(caught.value, 'automaticRetryCategory', None) == category
    assert len(calls) == 1


@pytest.mark.parametrize('status,body,category', [
    (500, {}, 'network'), (401, {}, None), (400, {}, None),
    (200, [], 'format_error'), (200, {'data': [None]}, 'format_error'),
    (200, {'data': []}, 'format_error'),
    (200, {'data': [{'index': 0, 'embedding': [0, 0]}]}, 'format_error'),
])
def test_embeddings_internal_classification_is_not_a_global_retry(status, body, category):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    service = SimpleNamespace(models=SimpleNamespace(transport=httpx.MockTransport(handle)))
    config = {'baseUrl': 'http://127.0.0.1:9999', 'model': 'mock-embedding', 'timeoutSeconds': 3}
    with pytest.raises(ModelError) as caught:
        RetrievalService._embed(service, config, ['original query'])
    assert getattr(caught.value, 'automaticRetryCategory', None) == category
    assert len(calls) == 1
