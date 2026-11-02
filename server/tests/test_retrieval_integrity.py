"""Independent retrieval checks: pinned scopes, revision races and real vector IO.

All articles are synthetic and all provider responses use MockTransport. These
checks run in isolated test directories and never use the configured model.
"""
import copy
import json

import httpx
import pytest

from server.model_gateway import ModelService
from server.retrieval import CHANNELS, RetrievalService
from server.store import Store
from server.writing import WritingService
from server.tests.test_model_gateway import TestCipher
from server.tests.test_wiki_exploration import ExplorationBench, entry
from server.tests.test_wiki import publish


def original(store, marker='initial', url='https://example.test/retrieval-integrity'):
    paragraphs = [f'人工智能治理的来源和版本必须准确。{marker}。每项政策分析都应保留可定位的原文证据。',
                  f'人工智能治理还应记录审查程序及人工判断，不能将摘要当作独立事实。{marker}。']
    job = store.queue('file', {}, '检索完整性资料')
    claimed = store.claim()
    assert claimed['id'] == job['id']
    store.publish(claimed, {'url': url}, '\n'.join(paragraphs).encode(),
                  {'title': '人工智能治理参考', 'body': paragraphs, 'language': 'zh',
                   'parser_version': 'retrieval-integrity',
                   'blocks': [{'kind': 'paragraph', 'text': text, 'ordinal': index}
                              for index, text in enumerate(paragraphs)]}, url)
    source_id = store.job(job['id'])['source_id']
    document = store.detail(source_id)
    return source_id, document['revisionId'], document['blocks']


class Vectors:
    def __init__(self):
        self.requests = []
        self.callback = None
        self.dimension_mismatch = False

    def __call__(self, request):
        assert request.url.path == '/v1/embeddings'
        body = json.loads(request.content)
        self.requests.append(copy.deepcopy(body))
        if self.callback:
            self.callback(len(self.requests), body)
        dimensions = 3 if self.dimension_mismatch and len(self.requests) > 1 else 2
        # Deliberately return response items in reverse order. The API index,
        # rather than array position, identifies each requested document.
        data = [{'index': index, 'embedding': ([index + 1.0, 2.0] if dimensions == 2 else [1.0, 2.0, 3.0])}
                for index in reversed(range(len(body['input'])))]
        return httpx.Response(200, json={'data': data})


@pytest.fixture
def retrieval(tmp_path):
    store = Store(tmp_path / 'isolated-retrieval-integrity')
    provider = Vectors()
    models = ModelService(store, cipher=TestCipher(), transport=httpx.MockTransport(provider))
    model = models.save_config({'name': '隔离向量测试', 'baseUrl': 'http://embedding.test/v1',
                                'model': 'vector-fixture', 'apiKey': 'isolated-private-provider-token'})
    service = RetrievalService(store, models)
    service.save_settings({'queryExpansion': False, 'rerankEnabled': False})
    return store, provider, models, service, model


def narrow(item, index=0):
    return {'sourceId': item['sourceId'], 'revisionId': item['revisionId'],
            'blockId': item['passages'][index]['blockId']}


def vector_only(service, model):
    service.save_settings({'channels': {key: key == 'vector' for key in CHANNELS},
                           'embeddingModelId': model['id']})


def test_unchanged_selection_survives_revision_advance_but_changed_scope_requires_reconfirmation(retrieval):
    store, _, models, service, _ = retrieval
    source, revision, blocks = original(store)
    result = service.recommend('人工智能治理')
    item = next(item for item in result['items'] if item['sourceId'] == source)
    selection = {'recommendationId': result['id'], 'passages': [narrow(item)]}
    writing = WritingService(store, models)
    project = writing.create({'title': '治理文章', 'sourceIds': [source], 'materialSelection': selection})
    original(store, 'advanced')
    updated = writing.patch(project['id'], project['version'], {'title': '人工调整标题',
                            'sourceIds': [source], 'materialSelection': copy.deepcopy(selection)})
    assert updated['sources'] == project['sources']
    assert updated['sources'][0]['revisionId'] == revision
    assert updated['materialSelection'] == selection
    different_block = next(block for block in blocks if block['id'] != selection['passages'][0]['blockId'])
    changed = {'recommendationId': result['id'], 'passages': [
        {'sourceId': source, 'revisionId': revision, 'blockId': different_block['id']}]}
    with pytest.raises(ValueError, match='版本已变化'):
        writing.patch(project['id'], updated['version'], {'materialSelection': changed})
    assert writing.get(project['id'])['version'] == updated['version']
    assert writing.get(project['id'])['sources'] == project['sources']


def test_new_recommendation_cannot_upgrade_existing_pinned_source_silently(retrieval):
    store, _, models, service, _ = retrieval
    source, revision, _ = original(store)
    writing = WritingService(store, models)
    project = writing.create({'title': '保留历史依据', 'sourceIds': [source]})
    _, current_revision, _ = original(store, 'new-revision')
    assert revision != current_revision
    result = service.recommend('人工智能治理', source_ids=[source])
    with pytest.raises(ValueError, match='固定版本'):
        writing.patch(project['id'], project['version'], {'sourceIds': [source],
                      'materialSelection': {'recommendationId': result['id'], 'passages': []}})
    assert writing.get(project['id'])['sources'][0]['revisionId'] == revision


def test_revision_changed_during_embedding_is_excluded_from_result(retrieval):
    store, provider, _, service, model = retrieval
    source, old_revision, _ = original(store)
    vector_only(service, model)
    provider.callback = lambda count, body: original(store, 'updated-during-embedding') if count == 2 else None
    result = service.recommend('人工智能治理')
    assert store.detail(source)['revisionId'] != old_revision
    assert result['items'] == []
    assert any('版本更新' in warning for warning in result['warnings'])
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM retrieval_embeddings WHERE revision_id=?', (old_revision,)).fetchone()[0] == 0


def test_embedding_cache_reuses_revision_and_invalidates_model_version_without_storing_key(retrieval):
    store, provider, models, service, model = retrieval
    original(store)
    vector_only(service, model)
    first = service.recommend('人工智能治理')
    assert first['items'][0]['channels'] == ['vector']
    assert len(provider.requests) == 2
    service.recommend('人工智能治理')
    assert len(provider.requests) == 3  # Fresh query, cached article.
    upgraded = models.save_config({'model': 'vector-fixture-v2'}, model['id'])
    assert upgraded['version'] == 2
    last = service.recommend('人工智能治理')
    assert len(provider.requests) == 5
    assert all(request['model'] == 'vector-fixture-v2' for request in provider.requests[-2:])
    with store.connect() as db:
        stored = [dict(row) for row in db.execute('SELECT * FROM retrieval_snapshots')]
        stored += [dict(row) for row in db.execute('SELECT * FROM retrieval_embeddings')]
    assert 'isolated-private-provider-token' not in json.dumps([first, last, stored])


def test_embedding_response_indexes_determine_vector_order(retrieval):
    _, _, models, service, model = retrieval
    values = service._embed(models.resolve(model['id']), ['first', 'second'])
    assert values[0] == pytest.approx([1 / 5 ** .5, 2 / 5 ** .5])
    assert values[1] == pytest.approx([2 ** -.5, 2 ** -.5])


def test_embedding_dimension_mismatch_is_reported_without_false_vector_hits(retrieval):
    store, provider, _, service, model = retrieval
    original(store)
    vector_only(service, model)
    provider.dimension_mismatch = True
    result = service.recommend('人工智能治理')
    assert result['items'] == []
    assert next(channel for channel in result['channels'] if channel['id'] == 'vector')['status'] == 'unavailable'
    assert len(provider.requests) == 2


def test_stale_wiki_claims_cannot_reenter_current_reference_results(tmp_path):
    bench = ExplorationBench(tmp_path / 'isolated-retrieval-wiki')
    url = 'https://example.test/retrieval-wiki'
    source, pages = bench.build([entry('claim', 'GhostNeedle', kind='claim',
                         summary='GhostNeedle 只存在于旧版资料，旧版记录必须保持独立且不能成为新版依据。')], url=url)
    service = RetrievalService(bench.store, bench.models)
    service.save_settings({'queryExpansion': False, 'rerankEnabled': False, 'channels': {key: key == 'claims' for key in CHANNELS}})
    before = service.recommend('GhostNeedle')
    assert [item['sourceId'] for item in before['items']] == [source]
    publish(bench.store, ['新版本内容已改为另外的主题，只讨论原始来源的固定版本要求，旧的特殊词条内容已被移除。'], url=url)
    assert bench.service.page(pages['GhostNeedle']['id'])['stale']
    after = service.recommend('GhostNeedle')
    assert after['items'] == []
