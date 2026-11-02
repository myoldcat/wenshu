"""Isolated retrieval contracts with actual mock HTTP embeddings, never live AI."""
import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.model_gateway import ModelService
from server.retrieval import RetrievalService, validate_selection, QWEN3_QUERY_PREFIX
from server.retrieval_vector import MAX_NEW_VECTOR_UNITS
from server.store import Store, dumps, now
from server.tests.test_wiki import Gateway, publish as _publish, execute
from server.wiki import WikiService


def publish(store, blocks=None, **kwargs):
    if blocks is not None and len(''.join(blocks)) < 60:
        blocks = [*blocks, 'Supplementary isolated fixture context provides sufficient article content.']
    return _publish(store, blocks=blocks, **kwargs)


@pytest.fixture
def bench(tmp_path):
    store = Store(tmp_path / 'data')
    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith('/embeddings'):
            # Deliberately reverse provider ordering to verify explicit indices.
            vectors = [{'index': i, 'embedding': [0.1, 1.0] if 'irrelevant' in text else [1.0, 0.1]}
                       for i, text in enumerate(body['input'])]
            return httpx.Response(200, json={'data': list(reversed(vectors))})
        return httpx.Response(200, json={'choices': [{'message': {'content': dumps({'queries': ['海上运输', 'maritime shipping']})}}]})

    models = ModelService(store, transport=httpx.MockTransport(provider))
    config = models.save_config({'name': '隔离模型', 'baseUrl': 'http://model.test/v1', 'model': 'embedding-mock'})
    service = RetrievalService(store, models)
    service.save_settings({'queryExpansion': False, 'rerankEnabled': False})
    return store, models, service, config['id'], calls


def channel(result, name):
    return next(value for value in result['channels'] if value['id'] == name)


def test_multichannel_fusion_returns_dozens_and_exact_current_passages(bench):
    store, _, service, _, calls = bench
    ids = [publish(store, [f'人工智能的公共治理需要记录原始证据，资料编号 {i}。'],
                   url=f'https://example.org/{i}', title=f'人工智能治理 {i}') for i in range(45)]
    for source_id in ids[:3]:
        store.patch(source_id, {'tags': ['人工智能', '公共治理']})
    result = service.recommend('人工智能公共治理', source_ids=[ids[-1]])
    assert len(result['items']) == 40
    assert result['queryOrigin'] == 'local'
    assert not calls
    assert any('category' in item['channels'] for item in result['items'])
    for item in result['items']:
        detail = store.detail(item['sourceId'])
        blocks = {block['id']: block['text'] for block in detail['blocks']}
        assert item['revisionId'] == detail['revisionId']
        assert 0 < item['score'] <= 1
        assert all(p['quote'] in blocks[p['blockId']] for p in item['passages'])
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT content_json FROM retrieval_snapshots WHERE id=?', (result['id'],)).fetchone()[0])
    assert frozen['selectionVersions'][ids[-1]] == store.detail(ids[-1])['revisionId']


def test_current_chinese_summary_and_manual_category_can_retrieve_without_body_hit(bench):
    store, _, service, _, _ = bench
    source = publish(store, ['The original article discusses ferries and traffic.'], title='Transport')
    detail = store.detail(source)
    store.patch(source, {'tags': ['城市交通']})
    with store.connect() as db:
        db.execute('INSERT INTO document_chinese VALUES(?,?,?,?,?)',
                   (detail['revisionId'], '海上通勤', '轮渡票价调整影响低收入通勤者', 'ai_summary', '[]'))
    result = service.recommend('轮渡 城市交通')
    item = next(item for item in result['items'] if item['sourceId'] == source)
    assert {'summary', 'category'} <= set(item['channels'])
    assert item['title'] == '海上通勤'
    assert item['passages'][0]['quote'] in detail['blocks'][0]['text']


def test_query_planning_one_bounded_model_call_and_explicit_rerun(bench):
    store, models, service, model_id, calls = bench
    publish(store, ['maritime shipping preserves original records.'])
    models.save_preferences({'defaultModelId': model_id})
    service.save_settings({'queryExpansion': True})
    result = service.recommend('港口贸易', brief={'purpose': '梳理风险'})
    assert result['queryOrigin'] == 'model'
    assert result['queries'] == ['港口贸易', '海上运输', 'maritime shipping']
    assert len(calls) == 1
    result = service.recommend('港口贸易', queries=['maritime'])
    assert result['queries'] == ['maritime']
    assert result['queryOrigin'] == 'manual'
    assert len(calls) == 1
    with pytest.raises(ValueError):
        service.recommend('主题', queries=['a' * 301])
    with pytest.raises(ValueError):
        service.recommend('主题', source_ids=['not-a-source'])
    assert len(calls) == 1


def test_query_failure_is_safe_and_local_fallback_still_returns_articles(bench):
    store, models, service, model_id, _ = bench
    publish(store, ['人工智能治理保留原文证据。'])
    models.save_preferences({'defaultModelId': model_id})
    service.save_settings({'queryExpansion': True})
    models.transport = httpx.MockTransport(lambda request: httpx.Response(500, text='provider-secret-do-not-display'))
    result = service.recommend('人工智能治理')
    assert result['queryOrigin'] == 'local' and result['items']
    assert result['warnings']
    assert 'provider-secret' not in dumps(result)


def test_missing_fulltext_index_degrades_only_keyword_channel(bench):
    store, _, service, _, _ = bench
    source = publish(store, ['人工智能治理保留原文证据。'])
    store.patch(source, {'tags': ['人工智能']})
    with store.connect() as db:
        db.execute('DROP TABLE search_index')
    result = service.recommend('人工智能')
    assert channel(result, 'keyword')['status'] == 'unavailable'
    assert channel(result, 'category')['status'] == 'ready'
    assert result['items'][0]['sourceId'] == source
    assert 'keyword' not in result['items'][0]['channels']


def test_real_embeddings_semantic_only_candidates_cache_and_version_invalidation(bench):
    store, models, service, model_id, calls = bench
    source = publish(store, ['Unrelated lexical wording about transportation.'], title='Harbor')
    service.save_settings({'embeddingModelId': model_id, 'channels': {
        'keyword': False, 'summary': False, 'category': False, 'claims': False, 'facts': False, 'graph': False, 'vector': True}})
    first = service.recommend('海上运输')
    assert first['items'][0]['sourceId'] == source
    assert first['items'][0]['channels'] == ['vector']
    assert channel(first, 'vector')['status'] == 'ready'
    assert len(calls) == 2
    assert all(path == '/v1/embeddings' for path, _ in calls)
    document_inputs = calls[1][1]['input']
    assert len(document_inputs) == 2
    assert any(text.startswith('【标题与摘要】\nHarbor\n') for text in document_inputs)
    assert any(text.startswith('【匹配段落】\nUnrelated lexical wording') for text in document_inputs)
    assert all(not text.startswith(QWEN3_QUERY_PREFIX) for text in document_inputs)
    service.recommend('海上运输')
    assert len(calls) == 3  # Query re-embedded, both unchanged units reused.
    old_revision = store.detail(source)['revisionId']
    publish(store, ['A revised article about transportation.'], title='Harbor', url='https://example.org/wiki')
    updated = service.recommend('海上运输')
    assert updated['items'][0]['revisionId'] != old_revision
    assert len(calls) == 5
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM retrieval_vector_units WHERE revision_id=?', (old_revision,)).fetchone()[0] == 0
    models.save_config({'model': 'new-embedding-model'}, id=model_id)
    service.recommend('海上运输')
    assert len(calls) == 7


@pytest.mark.parametrize('model_name,prefix', [
    ('qwen3-embedding:4b', QWEN3_QUERY_PREFIX),
    ('qwen3-embedding', QWEN3_QUERY_PREFIX),
    ('QWEN3-EMBEDDING:0.6b', QWEN3_QUERY_PREFIX),
    ('bge-m3', ''),
    ('embeddinggemma', ''),
    ('other-qwen3-embedding:4b', ''),
    ('qwen3-embedding:', ''),
    ('qwen3-embedding:4b:extra', ''),
])
def test_search_instruction_is_qwen3_query_only(bench, model_name, prefix):
    store, models, service, model_id, calls = bench
    models.save_config({'model': model_name}, id=model_id)
    source = publish(store, ['Ports coordinate international shipping and customs clearance.'], title='Port logistics')
    service.save_settings({'embeddingModelId': model_id, 'channels': {
        key: key == 'vector' for key in service.get_settings()['channels']}})
    queries = ['港口通关效率', 'international shipping']
    result = service.recommend('港口物流', queries=queries)
    assert result['items'][0]['sourceId'] == source
    assert len(calls) == 2
    assert calls[0][1]['input'] == [prefix + '\n'.join(queries)]
    document_inputs = calls[1][1]['input']
    assert len(document_inputs) == 2
    assert any(text.startswith('【标题与摘要】\nPort logistics\n') for text in document_inputs)
    assert any(text.startswith('【匹配段落】\nPorts coordinate') for text in document_inputs)
    assert all(not text.startswith(QWEN3_QUERY_PREFIX) for text in document_inputs)
    assert result['queries'] == queries  # Provider formatting is not a user query.


def test_qwen3_instruction_keeps_query_within_existing_character_budget(bench):
    store, models, service, model_id, calls = bench
    models.save_config({'model': 'qwen3-embedding:4b'}, id=model_id)
    publish(store, ['Ports coordinate international shipping and customs clearance.'])
    service.save_settings({'embeddingModelId': model_id, 'channels': {'vector': True}})
    queries = [str(index) + '港' * 299 for index in range(8)]
    service.recommend('港口物流', queries=queries)
    query_input = calls[0][1]['input'][0]
    assert len(query_input) == 2400
    assert query_input == QWEN3_QUERY_PREFIX + '\n'.join(queries)[:2400 - len(QWEN3_QUERY_PREFIX)]


def test_legacy_article_cache_cannot_replace_the_new_independent_vector_sources(bench):
    store, models, service, model_id, calls = bench
    model = models.save_config({'model': 'qwen3-embedding:4b'}, id=model_id)
    source = publish(store, ['Ports coordinate international shipping and customs clearance.'], title='Port logistics')
    document = store.detail(source)
    # The legacy article vector described title, summary and a truncated body
    # together. It is not either of the new independent vector sources.
    with store.connect() as db:
        revision = db.execute('SELECT summary,text_content FROM revisions WHERE id=?', (document['revisionId'],)).fetchone()
        text = ('Port logistics\n' + revision['summary'] + '\n' + revision['text_content'][:2400])[:2400]
        key = hashlib.sha256(dumps([model_id, model['version'], document['revisionId'], text]).encode()).hexdigest()
        db.execute('INSERT INTO retrieval_embeddings VALUES(?,?,?,?,?,?,?)',
                   (key, model_id, model['version'], source, document['revisionId'], dumps([1.0, 0.1]), now()))
        legacy = dict(db.execute('SELECT * FROM retrieval_embeddings WHERE cache_key=?', (key,)).fetchone())
    service.save_settings({'embeddingModelId': model_id, 'channels': {
        key: key == 'vector' for key in service.get_settings()['channels']}})
    result = service.recommend('港口通关效率', queries=['港口通关效率'])
    assert result['items'][0]['sourceId'] == source
    assert len(calls) == 2
    assert calls[0][1]['input'] == [QWEN3_QUERY_PREFIX + '港口通关效率']
    assert len(calls[1][1]['input']) == 2
    assert any(text.startswith('【标题与摘要】\nPort logistics\n') for text in calls[1][1]['input'])
    assert any(text.startswith('【匹配段落】\nPorts coordinate') for text in calls[1][1]['input'])
    assert all(not text.startswith(QWEN3_QUERY_PREFIX) for text in calls[1][1]['input'])
    service.recommend('港口通关效率', queries=['港口通关效率'])
    assert len(calls) == 3  # The second request reuses both new unit caches.
    assert calls[2][1]['input'] == [QWEN3_QUERY_PREFIX + '港口通关效率']
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM retrieval_embeddings').fetchone()[0] == 1
        assert dict(db.execute('SELECT * FROM retrieval_embeddings WHERE cache_key=?', (key,)).fetchone()) == legacy
        rows = db.execute('SELECT kind FROM retrieval_vector_units WHERE source_id=?', (source,)).fetchall()
        assert sorted(row['kind'] for row in rows) == ['passage', 'title_summary']


def test_embedding_failure_degrades_without_secret_or_fake_vector(bench):
    store, models, service, model_id, _ = bench
    publish(store, ['人工智能治理保留原文证据。'])
    service.save_settings({'embeddingModelId': model_id, 'channels': {'vector': True}})
    models.transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b'{"data":[{"index":0,"embedding":[NaN]}]}'))
    result = service.recommend('人工智能治理')
    assert result['items']
    assert channel(result, 'vector')['status'] == 'unavailable'
    assert all('vector' not in item['channels'] for item in result['items'])
    service.save_settings({'embeddingModelId': None})
    result = service.recommend('人工智能治理')
    assert channel(result, 'vector')['status'] == 'unavailable'


def test_vector_new_unit_budget_is_enforced_and_reported(bench):
    store, _, service, model_id, calls = bench
    article_count = MAX_NEW_VECTOR_UNITS // 2 + 3  # One passage + one title/summary each.
    for index in range(article_count):
        publish(store, [f'Shipping item {index}.'], url=f'https://example.org/vector/{index}')
    service.save_settings({'resultLimit': 70, 'candidateLimit': 80, 'embeddingModelId': model_id,
                          'channels': {key: key == 'vector' for key in service.get_settings()['channels']}})
    result = service.recommend('海上运输')
    assert channel(result, 'vector')['status'] == 'degraded'
    assert 0 < len(result['items']) <= result['settings']['resultLimit']
    assert sum(len(body['input']) for _, body in calls) == MAX_NEW_VECTOR_UNITS + 1
    assert all(len(body['input']) <= 16 for _, body in calls)
    stats = result['vectorSources']
    assert sum(value['totalUnits'] for value in stats) == article_count * 2
    assert sum(value['indexedUnits'] for value in stats) == MAX_NEW_VECTOR_UNITS
    assert sum(value['pendingUnits'] for value in stats) == article_count * 2 - MAX_NEW_VECTOR_UNITS
    assert all(value['indexedUnits'] + value['pendingUnits'] == value['totalUnits'] for value in stats)
    assert result['warnings'], 'Incomplete coverage must be explicit.'


def test_wiki_claim_fact_and_graph_discovery_excludes_old_revision(bench):
    store, _, service, _, _ = bench
    models = Gateway()
    wiki = WikiService(store, models)
    source = publish(store)

    def fact(result):
        result['entries'][1]['type'] = 'fact'
        return result

    models.rewrite = fact
    wiki.queue([source])
    assert execute(store, wiki)['status'] == 'succeeded'
    result = service.recommend('知识治理')
    item = next(item for item in result['items'] if item['sourceId'] == source)
    assert 'claims' in item['channels']
    assert 'graph' in item['channels']
    facts = service.recommend('原始版本')
    assert 'facts' in facts['items'][0]['channels']
    old_revision = store.detail(source)['revisionId']
    publish(store, ['A completely unrelated new body.'], title='Changed')
    stale = service.recommend('知识治理')
    assert all(item['revisionId'] != old_revision for item in stale['items'])
    assert channel(stale, 'graph')['status'] == 'unavailable'


def test_confirmation_freezes_revisions_rejects_cross_source_and_preserves_previous_passages(bench):
    store, _, service, _, _ = bench
    first = publish(store, ['人工智能原文段落。', '此前用户保留的另一段文字。'])
    second = publish(store, ['城市交通的原文段落。'], url='https://example.org/other')
    original = store.detail(first)
    other = store.detail(second)
    result = service.recommend('完全无匹配词', source_ids=[first])
    assert not result['items']
    preserved = {'sourceId': first, 'revisionId': original['revisionId'], 'blockId': original['blocks'][1]['id']}
    with store.connect() as db:
        assert validate_selection(db, result['id'], [first], [preserved]) == {first: original['revisionId']}
        with pytest.raises(ValueError):
            validate_selection(db, result['id'], [second], [])
        with pytest.raises(ValueError):
            validate_selection(db, result['id'], [first], [dict(preserved, blockId=other['blocks'][0]['id'])])
        with pytest.raises(ValueError):
            validate_selection(db, result['id'], [first], [dict(preserved, quote='伪造引文')])
        with pytest.raises(ValueError, match='1000'):
            validate_selection(db, result['id'], [first], [preserved] * 1001)
    publish(store, ['更新版本，不允许静默替换。'])
    with store.connect() as db:
        with pytest.raises(ValueError, match='版本已变化'):
            validate_selection(db, result['id'], [first], [preserved])


def test_settings_persistence_and_http_validation(bench):
    store, models, service, _, _ = bench
    service.save_settings({'resultLimit': 30, 'channels': {'facts': False}})
    assert RetrievalService(store, models).get_settings()['resultLimit'] == 30
    app = create_app(data_dir=store.root, model_service=models)
    with TestClient(app) as client:
        assert client.get('/api/retrieval/settings').json()['settings']['channels']['facts'] is False
        assert client.put('/api/retrieval/settings', headers={'X-Wenshu-Client': 'local'},
                          json={'resultLimit': 101}).status_code == 422
        assert client.put('/api/retrieval/settings', headers={'X-Wenshu-Client': 'local'},
                          json={'unknown': True}).status_code == 422
        response = client.post('/api/retrieval/recommendations', headers={'X-Wenshu-Client': 'local'},
                               json={'topic': '人工智能', 'queries': ['人工智能']})
        assert response.status_code == 200
        assert response.json()['items'] == []
    with pytest.raises(ValueError):
        service.save_settings({'resultLimit': 100, 'candidateLimit': 20})
    with pytest.raises(ValueError):
        service.save_settings({'minimumScore': float('nan')})
    with pytest.raises(ValueError):
        service.save_settings({'channels': {'imaginary': True}})
