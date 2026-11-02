"""HTTP -> queue -> worker -> Wiki/FTS -> exact revision evidence, with a fake provider."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.model_gateway import ModelService
from server.store import Store
from server.worker import run_one

LOCAL = {'X-Wenshu-Client': 'local'}


class Provider:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        if self.fail:
            return httpx.Response(503, json={'error': {'message': 'private-provider-body'}})
        payload = json.loads(body['messages'][1]['content'])
        assert payload['task'] == 'wiki_compile'
        block = payload['blocks'][-1]
        evidence = [{'blockId': block['blockId'], 'quote': block['text']}]
        entries = []
        for key, label, kind in [('k1', '原文证据', 'concept'), ('k2', '档案版本', 'entity')]:
            entries.append({'localKey': key, 'label': label, 'type': kind, 'subtype': '', 'aliases': [], 'standalone': True,
                            'summary': {'text': label + '用于知识治理的来源核对。', 'evidence': evidence, 'clear': True},
                            'sections': [{'title': '来源记载', 'claims': [{'text': '保留原文证据及其固定版本。', 'evidence': evidence, 'clear': True}]}]})
        result = {'entries': entries, 'relations': [{'from': 'k1', 'to': 'k2', 'type': 'association',
                  'label': '绑定版本', 'evidence': evidence, 'clear': True}], 'issues': []}
        return httpx.Response(200, json={'model': body['model'], 'choices': [{'message': {
            'content': json.dumps(result, ensure_ascii=False)}, 'finish_reason': 'stop'}],
            'usage': {'total_tokens': 50}})


@pytest.fixture
def bench(tmp_path):
    store = Store(tmp_path / 'data')
    provider = Provider()
    models = ModelService(store, transport=httpx.MockTransport(provider))
    app = create_app(data_dir=store.root, model_service=models)
    with TestClient(app) as client:
        config = client.post('/api/models', headers=LOCAL, json={
            'name': 'Wiki 测试', 'baseUrl': 'http://model.test/v1', 'model': 'wiki-test', 'maxOutputTokens': 4096}).json()['model']
        assert client.put('/api/ai/preferences', headers=LOCAL, json={'summaryModelId': config['id']}).status_code == 200
        raw = '<!doctype html><html><head><meta charset="utf-8"><title>档案治理</title></head><body><article><h1>档案治理</h1><p>知识治理应保留原文证据及固定档案版本，读者可以回查原始资料的准确段落，并检查时间、语境和数据口径。每次调整均保留原始版本，并允许通过完整正文检索回查依据。</p></article></body></html>'.encode()
        upload = client.post('/api/imports/files', headers=LOCAL, files={'files': ('sample.html', raw, 'text/html')}).json()['jobs'][0]
        assert run_one(app.state.store, app.state.enrichment, app.state.wiki)
        finished = app.state.store.job(upload['id'])
        assert finished['status'] == 'succeeded', finished
        source_id = finished['source_id']
        yield client, app, provider, source_id


def build(bench, force=False):
    client, app, _, source = bench
    response = client.post('/api/wiki/build', headers=LOCAL, json={'sourceIds': [source], 'force': force})
    assert response.status_code == 202, response.text
    job = response.json()['jobs'][0]
    assert run_one(app.state.store, app.state.enrichment, app.state.wiki)
    return app.state.store.job(job['id'])


def test_wiki_full_http_worker_persistence_and_version_anchor(bench):
    client, app, provider, source = bench
    assert client.get('/api/wiki').json()['nodes'] == []
    job = build(bench)
    assert job['status'] == 'succeeded', job
    graph = client.get('/api/wiki').json()
    assert graph['stats']['pages'] == 2 and graph['stats']['sources'] == 1
    assert any(edge['type'] == 'association' for edge in graph['edges'])
    pages = [node for node in graph['nodes'] if node['type'] != 'document']
    detail = client.get('/api/wiki/pages/' + pages[0]['id']).json()
    assert detail['sections'] and detail['history']
    for evidence in detail['evidence']:
        doc = client.get('/api/documents/' + evidence['sourceId'], params={'revision_id': evidence['revisionId']}).json()['document']
        block = next(item for item in doc['blocks'] if item['id'] == evidence['blockId'])
        assert block['text'][evidence['start']:evidence['end']] == evidence['quote']
    historical = client.get('/api/wiki/pages/' + detail['id'], params={'version': detail['version']}).json()
    assert historical['sections'] == detail['sections']
    search = client.get('/api/wiki/search', params={'q': '原文证据', 'hops': 1}).json()
    assert search['results'] and search['evidence']
    assert client.get('/api/wiki/search?q=原文证据&hops=0').json()['expanded'] == []
    assert client.get('/api/wiki/search?q=does_not_exist_35e48').json()['results'] == []
    duplicate = client.post('/api/wiki/build', headers=LOCAL, json={'sourceIds': [source, source]}).json()
    assert duplicate['jobs'] == [] and len(duplicate['skipped']) == 1
    assert len(provider.calls) == 1
    restarted = create_app(data_dir=app.state.store.root, model_service=app.state.models)
    with TestClient(restarted) as new_client:
        assert new_client.get('/api/wiki/pages/' + detail['id']).json()['sections'] == detail['sections']


def test_failed_rebuild_keeps_successful_page_and_retries_same_task(bench):
    client, app, provider, source = bench
    assert build(bench)['status'] == 'succeeded'
    node = next(node for node in client.get('/api/wiki').json()['nodes'] if node['type'] != 'document')
    old = client.get('/api/wiki/pages/' + node['id']).json()
    provider.fail = True
    failed = build(bench, force=True)
    assert failed['status'] == 'failed' and 'private-provider-body' not in failed['error']
    current = client.get('/api/wiki/pages/' + node['id']).json()
    assert current['sections'] == old['sections'] and current['version'] == old['version']
    duplicate = client.post('/api/wiki/build', headers=LOCAL, json={'sourceIds': [source]}).json()
    assert duplicate == {'jobs': [], 'skipped': [{'sourceId': source, 'reason': 'already_built'}]}
    provider.fail = False
    assert client.post(f"/api/jobs/{failed['id']}/retry", headers=LOCAL).status_code == 200
    assert run_one(app.state.store, app.state.enrichment, app.state.wiki)
    assert app.state.store.job(failed['id'])['status'] == 'succeeded'
    current = client.get('/api/wiki/pages/' + node['id']).json()
    assert current['version'] != old['version'] and len(current['history']) == 2
    assert client.get('/api/wiki/pages/' + node['id'], params={'version': old['version']}).json()['sections'] == old['sections']


@pytest.mark.parametrize('payload', [
    {'sourceIds': []}, {'sourceIds': ['x'] * 21}, {'sourceIds': [False]},
    {'sourceIds': ['x'], 'force': 'yes'}, {'sourceIds': ['x'], 'unknown': True},
])
def test_wiki_build_scope_validation_has_no_side_effects(bench, payload):
    client, app, provider, source = bench
    before = app.state.store.jobs()['counts']['total']
    assert client.post('/api/wiki/build', headers=LOCAL, json=payload).status_code == 422
    assert app.state.store.jobs()['counts']['total'] == before
    assert provider.calls == []


def test_wiki_boundaries_and_atomic_invalid_scope(bench):
    client, app, provider, source = bench
    assert client.post('/api/wiki/build', json={'sourceIds': [source]}).status_code == 403
    assert client.post('/api/wiki/build', headers=LOCAL | {'Origin': 'https://unrelated.test'}, json={'sourceIds': [source]}).status_code == 403
    assert client.post('/api/wiki/build', headers=LOCAL, json={'sourceIds': [source, 'missing']}).status_code == 404
    assert client.get('/api/wiki/pages/missing').status_code == 404
    for params in ({'hops': 3}, {'limit': 999}, {'q': 'x' * 301}):
        assert client.get('/api/wiki/search', params=params).status_code == 422
    assert client.get('/api/wiki').json()['jobs'] == []
    assert provider.calls == []
