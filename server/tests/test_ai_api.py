"""API -> persistent queue -> actual worker AI dispatch, with a fake HTTP provider."""
import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server import enrichment as enrichment_module
from server.app import create_app
from server.model_gateway import ModelService
from server.enrichment import EnrichmentService
from server.store import Store
from server.worker import run_one

LOCAL = {'X-Wenshu-Client': 'local'}
SECRET = 'unit-test-secret-not-a-real-key'


class Cipher:
    def encrypt(self, value):
        return 'test:' + base64.b64encode(value.encode()).decode()

    def decrypt(self, value):
        return base64.b64decode(value[5:]).decode()


class Provider:
    def __init__(self):
        self.calls = []
        self.failure = None

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        if self.failure:
            return httpx.Response(self.failure, json={'error': {'message': SECRET + ' private provider body'}})
        try:
            payload = json.loads(body['messages'][-1]['content'])
        except ValueError:
            payload = {}
        task = payload.get('task')
        if task in ('summary', 'summary_reduce'):
            ids = [b['blockId'] for b in payload['blocks']] if task == 'summary' else [i for s in payload['summaries'] for p in s['keyPoints'] for i in p['blockIds']]
            result = {'summary': '文档说明基层档案治理需要保留来源证据。',
                      'keyPoints': [{'text': '原始资料应保留来源与版本。', 'blockIds': list(dict.fromkeys(ids))[:3]}],
                      'keywords': ['档案治理', '来源证据']}
        elif task == 'translation':
            result = {'segments': [{'id': b['id'], 'text': '译文：' + b['text']} for b in payload['blocks']]}
        elif task == 'classification':
            result = {'paths': [{'level1Id': 'politics_governance', 'level2': '公共治理', 'level3': '档案管理',
                                 'primary': True, 'reason': '正文主要讨论公共部门档案证据保存。', 'blockIds': [payload['blocks'][0]['blockId']]}]}
        elif task == 'classification_reduce':
            result = {'paths': [dict(payload['parts'][0]['paths'][0], primary=True)]}
        else:
            result = {'ok': True}
        if payload.get('articleTypePolicyVersion'):
            result['articleType'] = 'fact'
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result, ensure_ascii=False)}, 'finish_reason': 'stop'}],
                                      'model': body['model'], 'usage': {'prompt_tokens': 20, 'completion_tokens': 20, 'total_tokens': 40}})


@pytest.fixture
def bench(tmp_path):
    store = Store(tmp_path / 'data')
    provider = Provider()
    model_service = ModelService(store, cipher=Cipher(), transport=httpx.MockTransport(provider))
    app = create_app(data_dir=store.root, model_service=model_service)
    with TestClient(app) as client:
        yield client, app.state.store, app.state.enrichment, model_service, provider


def config(client, **changes):
    payload = {'name': '验收模型', 'baseUrl': 'http://configured-model.test/v1', 'model': 'user-model', 'maxOutputTokens': 4096}
    payload.update(changes)
    result = client.post('/api/models', json=payload, headers=LOCAL)
    assert result.status_code == 201, result.text
    return result.json()['model']


def bind(client, model_id, auto=False, kinds=('summary', 'translation', 'classification')):
    result = client.put('/api/ai/preferences', json={**{kind + 'ModelId': model_id for kind in kinds}, 'autoProcess': auto}, headers=LOCAL)
    assert result.status_code == 200, result.text
    return result.json()['preferences']


def html(text='基层档案治理需要保留材料来源与证据位置。每次调整保留原始版本，并允许通过完整正文检索回查依据。'):
    return ('<!doctype html><html><head><meta charset="utf-8"><title>档案验收资料</title></head><body><article><h1>档案验收资料</h1><p>' + text + '</p></article></body></html>').encode()


def upload(client, raw=None):
    result = client.post('/api/imports/files', files=[('files', ('sample.html', raw or html(), 'text/html'))], headers=LOCAL)
    assert result.status_code == 202, result.text
    return result.json()['jobs'][0]


def ingest(bench, raw=None):
    client, store, enrichment, _, _ = bench
    job = upload(client, raw)
    assert run_one(store, enrichment)
    finished = store.job(job['id'])
    assert finished['status'] == 'succeeded', finished
    return finished['source_id']


def enqueue(client, source_id, **payload):
    result = client.post(f'/api/documents/{source_id}/ai', json=payload, headers=LOCAL)
    assert result.status_code == 202, result.text
    return result.json()['jobs']


def drain(store, enrichment, limit=20):
    for _ in range(limit):
        if not run_one(store, enrichment):
            return
    raise AssertionError('worker did not finish bounded test queue')


def empty_taxonomy(monkeypatch):
    monkeypatch.setattr(enrichment_module, 'CATEGORY_NAMES', ())
    monkeypatch.setattr(enrichment_module, 'CATEGORIES', {})
    monkeypatch.setattr(enrichment_module, 'TAXONOMY_VERSION', 'test-taxonomy-empty-v2')


def test_empty_taxonomy_api_rejects_explicit_classification_but_processes_other_kinds(bench, monkeypatch):
    client, store, enrichment, _, provider = bench
    first = config(client)
    bind(client, first['id'])
    source = ingest(bench)
    empty_taxonomy(monkeypatch)
    assert client.get('/api/ai/taxonomy').json()['categories'] == []
    detail = client.get(f'/api/documents/{source}/ai').json()['ai']
    assert detail['classificationAvailable'] is False
    assert detail['classification']['result'] is None
    rejected = client.post(f'/api/documents/{source}/ai',
                           json={'kinds': ['summary', 'classification'], 'force': True}, headers=LOCAL)
    assert rejected.status_code == 422
    assert '暂无可用的分类体系' in rejected.json()['detail']
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_runs').fetchone()[0] == 0
    assert provider.calls == []
    jobs = enqueue(client, source)
    assert {job['kind'] for job in jobs} == {'ai_summary', 'ai_translation'}
    drain(store, enrichment)
    detail = client.get(f'/api/documents/{source}/ai').json()['ai']
    assert detail['summary']['result'] and detail['translation']['result']
    assert detail['classification']['result'] is None
    assert {json.loads(call['messages'][-1]['content'])['task'] for call in provider.calls} == {'summary'}
    assert detail['translation']['telemetry']['calls'] == 0
    assert [segment['text'] for segment in detail['translation']['result']['segments']] == store.detail(source)['body']


@pytest.mark.parametrize('only_classification', [False, True])
@pytest.mark.parametrize('foreign', [False, True])
def test_empty_taxonomy_auto_outbox_finishes_without_classification_calls(bench, monkeypatch, only_classification, foreign):
    client, store, enrichment, _, provider = bench
    first = config(client)
    kinds = ('classification',) if only_classification else ('summary', 'translation', 'classification')
    bind(client, first['id'], auto=True, kinds=kinds)
    empty_taxonomy(monkeypatch)
    source = ingest(bench, html('Public archives preserve original evidence and document versions for later review.') if foreign else None)
    outbox = next(job for job in store.jobs()['jobs'] if job['kind'] == 'ai_schedule')
    drain(store, enrichment)
    assert store.job(outbox['id'])['status'] == 'succeeded'
    assert not any(job['kind'] == 'ai_classification' for job in store.jobs()['jobs'])
    tasks = {json.loads(call['messages'][-1]['content'])['task'] for call in provider.calls}
    # A legacy classification binding now selects the one shared default too.
    assert tasks == ({'summary', 'translation'} if foreign else {'summary'})
    assert enrichment.detail(source)['translation']['status'] == ('succeeded' if foreign else 'not_started')
    assert store.detail(source)['body']
    assert client.get(f'/api/documents/{source}/ai').json()['ai']['classificationAvailable'] is False


def test_worker_recovery_does_not_publish_retired_classification(bench, monkeypatch):
    client, store, enrichment, models, provider = bench
    first = config(client)
    bind(client, first['id'])
    source = ingest(bench)
    before = store.detail(source)
    queued = enqueue(client, source, kinds=['classification'])[0]
    assert store.claim()['id'] == queued['id']
    empty_taxonomy(monkeypatch)
    restarted = Store(store.root)
    restarted.recover()
    drain(restarted, EnrichmentService(restarted, models))
    assert restarted.job(queued['id'])['status'] == 'failed'
    assert provider.calls == []
    assert restarted.detail(source)['tags'] == before['tags']
    assert restarted.detail(source)['body'] == before['body']
    assert client.get(f'/api/documents/{source}/ai').json()['ai']['classification']['result'] is None
    with restarted.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_outputs').fetchone()[0] == 0


def test_model_api_versions_redaction_preferences_and_real_protocol_test(bench):
    client, _, _, models, provider = bench
    first = config(client, apiKey=SECRET, reasoningEffort='none', jsonMode=True)
    assert first['hasApiKey'] and SECRET not in json.dumps(first)
    assert client.get('/api/models').json()['models'] == [first]
    bind(client, first['id'])
    updated = client.put(f"/api/models/{first['id']}", json={'name': '修改名称', 'apiKey': ''}, headers=LOCAL)
    assert updated.status_code == 200
    assert updated.json()['model']['version'] == 2 and updated.json()['model']['hasApiKey']
    assert models.resolve(first['id'], 1)['name'] == '验收模型'
    assert models.resolve(first['id'], 2)['apiKey'] == SECRET
    tested = client.post(f"/api/models/{first['id']}/test", json={}, headers=LOCAL)
    assert tested.status_code == 200
    result = tested.json()['result']
    assert result['textGeneration'] and result['jsonParseable'] and result['structuredOutput'] == 'not_tested'
    assert provider.calls[-1]['reasoning_effort'] == 'none'
    assert provider.calls[-1]['response_format'] == {'type': 'json_object'}
    assert SECRET not in client.get('/api/models').text
    cleared = client.put(f"/api/models/{first['id']}", json={'clearApiKey': True}, headers=LOCAL)
    assert cleared.status_code == 200 and not cleared.json()['model']['hasApiKey']


def test_model_output_budget_64k_is_versioned_and_preserves_other_settings(bench):
    client, store, _, models, provider = bench
    original = config(client, maxOutputTokens=8192, apiKey=SECRET)
    before_preferences = models.get_preferences()
    response = client.put(f"/api/models/{original['id']}", json={'maxOutputTokens': 65536}, headers=LOCAL)
    assert response.status_code == 200, response.text
    latest = response.json()['model']
    assert latest['maxOutputTokens'] == 65536
    assert latest['version'] == original['version'] + 1
    for key in ('id', 'name', 'baseUrl', 'model', 'contextChars', 'timeoutSeconds',
                'reasoningEffort', 'jsonMode', 'hasApiKey'):
        assert latest[key] == original[key]
    restarted = ModelService(Store(store.root), cipher=Cipher())
    assert restarted.snapshot(original['id'])['maxOutputTokens'] == 65536
    assert restarted.resolve(original['id'], original['version'])['maxOutputTokens'] == 8192
    assert restarted.resolve(original['id'])['apiKey'] == SECRET
    assert restarted.get_preferences() == before_preferences
    rejected = client.put(f"/api/models/{original['id']}", json={'maxOutputTokens': 65537}, headers=LOCAL)
    assert rejected.status_code == 422
    assert models.snapshot(original['id']) == latest
    assert provider.calls == []


def test_model_input_character_budget_above_200k_is_versioned_and_persisted(bench):
    client, store, _, models, provider = bench
    original = config(client, contextChars=16000, maxOutputTokens=8192,
                      timeoutSeconds=300, reasoningEffort='medium', jsonMode=True, apiKey=SECRET)
    other = config(client, name='保持默认预算')
    before_preferences = models.get_preferences()
    latest = original
    for budget in (200001, 1000000):
        response = client.put(f"/api/models/{original['id']}", json={'contextChars': budget}, headers=LOCAL)
        assert response.status_code == 200, response.text
        latest = response.json()['model']
        assert latest['contextChars'] == budget
        for key in ('name', 'baseUrl', 'model', 'maxOutputTokens', 'timeoutSeconds',
                    'reasoningEffort', 'jsonMode', 'hasApiKey'):
            assert latest[key] == original[key]
    assert latest['version'] == original['version'] + 2
    restarted = ModelService(Store(store.root), cipher=Cipher())
    assert restarted.snapshot(original['id'])['contextChars'] == 1000000
    assert restarted.resolve(original['id'], original['version'])['contextChars'] == 16000
    assert restarted.snapshot(other['id'])['contextChars'] == other['contextChars'] == 24000
    assert restarted.get_preferences() == before_preferences
    assert provider.calls == []
    with store.connect() as db:
        rows = db.execute('SELECT version,config_json FROM ai_model_configs WHERE id=? ORDER BY version',
                          (original['id'],)).fetchall()
    assert [json.loads(row['config_json'])['contextChars'] for row in rows] == [16000, 200001, 1000000]


def test_model_input_character_budget_above_1m_is_rejected_without_new_revision(bench):
    client, _, _, models, provider = bench
    original = config(client, contextChars=1000000)
    response = client.put(f"/api/models/{original['id']}", json={'contextChars': 1000001}, headers=LOCAL)
    assert response.status_code == 422
    assert models.snapshot(original['id']) == original
    assert provider.calls == []


@pytest.mark.parametrize('invalid', [
    {'apiKey': {'secret': SECRET}}, {'apiKey': SECRET * 500},
    {'maxOutputTokens': SECRET}, {'contextChars': True}, {'unexpected': SECRET},
])
def test_model_api_validation_never_echoes_secret(bench, invalid):
    client = bench[0]
    result = client.post('/api/models', json={'name': 'x', 'baseUrl': 'http://localhost:11434/v1', 'model': 'x', **invalid}, headers=LOCAL)
    assert result.status_code == 422
    assert SECRET not in result.text and 'private provider' not in result.text


def test_model_api_local_boundary_and_endpoint_validation(bench):
    client = bench[0]
    assert client.post('/api/models', json={}).status_code == 403
    assert client.post('/api/models', json={}, headers=LOCAL | {'Origin': 'https://unrelated.example'}).status_code == 403
    response = client.post('/api/models', json={'name': 'x', 'model': 'x', 'baseUrl': 'http://user:' + SECRET + '@localhost/v1'}, headers=LOCAL)
    assert response.status_code == 422 and SECRET not in response.text
    assert client.put('/api/ai/preferences', json={'summaryModelId': 'missing'}, headers=LOCAL).status_code == 422
    assert client.put('/api/ai/preferences', json={'autoProcess': True}, headers=LOCAL).status_code == 422


def test_default_model_api_is_shared_by_enrichment_wiki_and_writing(bench):
    client, store, _, _, provider = bench
    first, second = config(client), config(client, name='统一默认', model='chosen-default')
    response = client.put('/api/ai/preferences', headers=LOCAL, json={'defaultModelId': second['id']})
    assert response.status_code == 200, response.text
    prefs = client.get('/api/models').json()['preferences']
    assert all(prefs[key] == second['id'] for key in ('defaultModelId', 'summaryModelId', 'translationModelId', 'classificationModelId'))
    source = ingest(bench)
    enqueue(client, source)
    wiki = client.post('/api/wiki/build', headers=LOCAL, json={'sourceIds': [source]})
    assert wiki.status_code == 202, wiki.text
    created = client.post('/api/writing/projects', headers=LOCAL, json={'title': '统一默认验收', 'sourceIds': [source]})
    assert created.status_code == 201, created.text
    project = created.json()['project']
    writing = client.post(f"/api/writing/projects/{project['id']}/generate-outline", headers=LOCAL,
                          json={'expectedVersion': project['version']})
    assert writing.status_code == 202, writing.text
    with store.connect() as db:
        rows = db.execute('SELECT model_id FROM ai_runs UNION ALL SELECT model_id FROM wiki_runs UNION ALL SELECT model_id FROM writing_runs').fetchall()
    assert len(rows) == 5 and {row['model_id'] for row in rows} == {second['id']}
    assert provider.calls == []


def test_delete_model_api_preserves_queued_work_and_enforces_local_boundary(bench):
    client, store, enrichment, _, provider = bench
    first = config(client)
    client.put('/api/ai/preferences', headers=LOCAL, json={'defaultModelId': first['id']})
    source = ingest(bench)
    queued = enqueue(client, source, kinds=['summary'])[0]
    assert client.delete(f"/api/models/{first['id']}").status_code == 403
    deleted = client.delete(f"/api/models/{first['id']}", headers=LOCAL)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()['models'] == []
    assert deleted.json()['preferences']['defaultModelId'] is None
    assert deleted.json()['preferences']['autoProcess'] is False
    assert client.post(f'/api/documents/{source}/ai', headers=LOCAL, json={'kinds': ['summary']}).status_code == 422
    assert client.post(f"/api/models/{first['id']}/test", headers=LOCAL).status_code == 422
    drain(store, enrichment)
    assert store.job(queued['id'])['status'] == 'succeeded'
    assert len(provider.calls) == 1


def test_document_ai_routes_dispatch_worker_freeze_config_and_preserve_manual_tags(bench):
    client, store, enrichment, models, provider = bench
    first = config(client, apiKey=SECRET)
    bind(client, first['id'])
    source = ingest(bench)
    before = client.get(f'/api/documents/{source}').json()['document']
    jobs = enqueue(client, source, revisionId=before['revisionId'])
    assert len(jobs) == 3
    update = client.put(f"/api/models/{first['id']}", json={'model': 'new-model'}, headers=LOCAL)
    assert update.status_code == 200
    assert client.patch(f'/api/documents/{source}', json={'tags': ['人工归档']}, headers=LOCAL).status_code == 200
    drain(store, enrichment)
    result = client.get(f'/api/documents/{source}/ai').json()['ai']
    for kind in ('summary', 'translation', 'classification'):
        assert result[kind]['status'] == 'succeeded'
        assert result[kind]['model']['version'] == 1
    assert all(call['model'] == 'user-model' for call in provider.calls)
    assert result['classification']['result']['manualOverride'] is True
    assert client.get(f'/api/documents/{source}').json()['document']['tags'] == ['人工归档']
    assert SECRET not in json.dumps(result)
    assert client.get('/api/search', params={'q': '基层档案治理'}).json()['total'] == 1
    assert models.resolve(first['id'])['model'] == 'new-model'


def test_revision_conflict_route_and_current_revision_remains_processable(bench):
    client, store, enrichment, _, _ = bench
    first = config(client)
    bind(client, first['id'])
    source = ingest(bench)
    enqueue(client, source, kinds=['summary'])
    drain(store, enrichment)
    old_revision = store.detail(source)['revisionId']
    # Revisions are source-bound: a URL update represents changed content at the
    # same source, unlike intentionally separate local file content identities.
    with store.connect() as db:
        db.execute('UPDATE sources SET identity=?,url=? WHERE id=?', ('url:https://example.org/fixture', 'https://example.org/fixture', source))
    raw = html('基层档案治理的新版本新增核验程序和修订记录，所有材料保留可回查的证据位置，帮助审阅者核对政策内容。')
    _, relative = store.put_blob(raw)
    imported = store.queue('file', {'path': str(store.root / relative), 'url': 'https://example.org/fixture'}, 'revision two')
    assert run_one(store, enrichment)
    assert store.job(imported['id'])['source_id'] == source
    conflict = client.post(f'/api/documents/{source}/ai', json={'kinds': ['summary'], 'revisionId': old_revision}, headers=LOCAL)
    assert conflict.status_code == 422
    current = client.get(f'/api/documents/{source}/ai').json()['ai']
    assert not current['stale'] and current['summary']['result'] is None
    assert client.get(f'/api/documents/{source}/ai', params={'revision_id': old_revision}).json()['ai']['stale']
    enqueue(client, source, kinds=['summary'], revisionId=current['sourceRevisionId'])
    drain(store, enrichment)
    assert client.get(f'/api/documents/{source}/ai').json()['ai']['summary']['status'] == 'succeeded'


def test_translation_edit_route_locks_persists_and_survives_regeneration(bench):
    client, store, enrichment, _, _ = bench
    first = config(client)
    bind(client, first['id'])
    source = ingest(bench)
    enqueue(client, source, kinds=['translation'])
    drain(store, enrichment)
    detail = store.detail(source)
    block = detail['blocks'][0]['id']
    payload = {'blockId': block, 'text': '已经人工核对的译文', 'locked': True, 'revisionId': detail['revisionId']}
    edited = client.patch(f'/api/documents/{source}/ai/translation', json=payload, headers=LOCAL)
    assert edited.status_code == 200, edited.text
    enqueue(client, source, kinds=['translation'], force=True)
    drain(store, enrichment)
    segments = client.get(f'/api/documents/{source}/ai').json()['ai']['translation']['result']['segments']
    assert segments[0]['text'] == payload['text'] and segments[0]['locked']
    # Foreign block ids cannot be used to write cross-document edits.
    rejected = client.patch(f'/api/documents/{source}/ai/translation', json={**payload, 'blockId': 'foreign'}, headers=LOCAL)
    assert rejected.status_code == 422
    reloaded = create_app(data_dir=store.root)
    with TestClient(reloaded) as other:
        assert other.get(f'/api/documents/{source}/ai').json()['ai']['translation']['result']['segments'][0]['text'] == payload['text']


def test_auto_only_new_import_never_scans_existing_or_reprocesses_duplicate(bench):
    client, store, enrichment, _, provider = bench
    first = config(client)
    source = ingest(bench)
    bind(client, first['id'], auto=True, kinds=('summary',))
    assert not run_one(store, enrichment)
    assert provider.calls == []
    duplicate = upload(client)
    drain(store, enrichment)
    assert store.job(duplicate['id'])['duplicate'] is True
    assert provider.calls == []
    next_source = ingest(bench, html('新增资料介绍基层档案治理的核验清单与工作步骤。原始网页需要完整保存，并根据来源分配权限及审阅责任。'))
    assert next_source != source
    drain(store, enrichment)
    assert provider.calls
    assert client.get(f'/api/documents/{next_source}/ai').json()['ai']['summary']['status'] == 'succeeded'
    assert client.get(f'/api/documents/{source}/ai').json()['ai']['summary']['status'] == 'not_started'


def test_model_failure_does_not_damage_ingestion_and_job_retry_is_independent(bench):
    client, store, enrichment, _, provider = bench
    first = config(client, apiKey=SECRET)
    bind(client, first['id'], auto=True, kinds=('summary',))
    provider.failure = 401
    source = ingest(bench)
    drain(store, enrichment)
    ai = client.get(f'/api/documents/{source}/ai').json()['ai']['summary']
    assert ai['status'] == 'failed' and ai['result'] is None
    assert SECRET not in ai['error'] and 'private provider' not in ai['error']
    assert store.detail(source)['body']
    assert client.get('/api/search', params={'q': '基层档案治理'}).json()['total'] == 1
    provider.failure = None
    assert client.post(f"/api/jobs/{ai['jobId']}/retry", json={}, headers=LOCAL).status_code == 200
    drain(store, enrichment)
    assert client.get(f'/api/documents/{source}/ai').json()['ai']['summary']['status'] == 'succeeded'


def test_auto_scheduling_failure_is_safe_and_retryable(bench, monkeypatch):
    client, store, enrichment, _, _ = bench
    first = config(client)
    bind(client, first['id'], auto=True, kinds=('summary',))
    original = enrichment.maybe_queue_auto
    def fail_schedule(source, revision_id=None, schedule_job=None):
        raise RuntimeError(SECRET + ' arbitrary sensitive diagnostic')
    monkeypatch.setattr(enrichment, 'maybe_queue_auto', fail_schedule)
    source = ingest(bench)
    assert run_one(store, enrichment)
    failed = [j for j in store.jobs()['jobs'] if j['kind'] == 'ai_schedule']
    assert len(failed) == 1 and failed[0]['status'] == 'failed'
    assert SECRET not in failed[0]['error']
    assert store.detail(source)['body']
    monkeypatch.setattr(enrichment, 'maybe_queue_auto', original)
    assert client.post(f"/api/jobs/{failed[0]['id']}/retry", json={}, headers=LOCAL).status_code == 200
    drain(store, enrichment)
    assert store.job(failed[0]['id'])['status'] == 'succeeded'
    assert client.get(f'/api/documents/{source}/ai').json()['ai']['summary']['status'] == 'succeeded'


def test_auto_outbox_survives_crash_immediately_after_source_publication(bench, monkeypatch):
    client, store, enrichment, models, provider = bench
    first = config(client)
    bind(client, first['id'], auto=True, kinds=('summary',))
    original_publish = store.publish
    def crash_after_commit(*args, **kwargs):
        original_publish(*args, **kwargs)
        raise SystemExit('simulated process loss after committed publication')
    monkeypatch.setattr(store, 'publish', crash_after_commit)
    imported = upload(client)
    with pytest.raises(SystemExit):
        run_one(store, enrichment)
    assert store.job(imported['id'])['status'] == 'succeeded'
    outbox = [j for j in store.jobs()['jobs'] if j['kind'] == 'ai_schedule']
    assert len(outbox) == 1 and outbox[0]['status'] == 'queued'
    assert outbox[0]['source_id'] == store.job(imported['id'])['source_id']
    assert outbox[0]['revision_id'] == store.job(imported['id'])['revision_id']
    assert provider.calls == []
    restarted = Store(store.root)
    restarted.recover()
    drain(restarted, EnrichmentService(restarted, models))
    assert restarted.job(outbox[0]['id'])['status'] == 'succeeded'
    assert provider.calls
    assert enrichment.detail(outbox[0]['source_id'])['summary']['status'] == 'succeeded'


def test_auto_outbox_and_source_publish_roll_back_together(bench):
    client, store, enrichment, _, _ = bench
    first = config(client)
    bind(client, first['id'], auto=True, kinds=('summary',))
    with store.connect() as db:
        db.executescript("CREATE TRIGGER fail_outbox BEFORE INSERT ON jobs WHEN NEW.kind='ai_schedule' BEGIN SELECT RAISE(ABORT,'simulated outbox disk failure'); END;")
    imported = upload(client)
    assert run_one(store, enrichment)
    assert store.job(imported['id'])['status'] == 'failed'
    assert store.documents()['total'] == 0
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM revisions').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM search_index').fetchone()[0] == 0


def test_pending_auto_outbox_respects_disabled_preference(bench):
    client, store, enrichment, _, provider = bench
    first = config(client)
    bind(client, first['id'], auto=True, kinds=('summary',))
    source = ingest(bench)
    outbox = next(j for j in store.jobs()['jobs'] if j['kind'] == 'ai_schedule')
    assert client.put('/api/ai/preferences', json={'autoProcess': False, 'autoTranslate': False}, headers=LOCAL).status_code == 200
    drain(store, enrichment)
    assert store.job(outbox['id'])['status'] == 'cancelled'
    assert store.job(outbox['id'])['stage'] == 'auto_disabled'
    assert enrichment.detail(source)['summary']['status'] == 'not_started'
    assert provider.calls == []


def test_outdated_auto_outbox_cannot_schedule_new_source_revision(bench):
    client, store, enrichment, _, provider = bench
    first = config(client)
    bind(client, first['id'], auto=True, kinds=('summary',))
    source = ingest(bench)
    old = next(j for j in store.jobs()['jobs'] if j['kind'] == 'ai_schedule')
    # Delay the first outbox while another source version is published.
    store.cancel(old['id'])
    with store.connect() as db:
        db.execute('UPDATE sources SET identity=?,url=? WHERE id=?', ('url:https://example.org/versioned', 'https://example.org/versioned', source))
    raw = html('新版基层档案治理资料要求保留核验记录并建立文档修订流程。审阅时须根据原始来源核对数字、时间和相关条件。')
    _, relative = store.put_blob(raw)
    imported = store.queue('file', {'path': str(store.root / relative), 'url': 'https://example.org/versioned'}, 'new revision')
    assert run_one(store, enrichment)
    assert store.job(imported['id'])['status'] == 'succeeded'
    assert store.detail(source)['revisionId'] != old['revision_id']
    store.retry(old['id'])
    assert run_one(store, enrichment)
    assert store.job(old['id'])['status'] == 'cancelled' and store.job(old['id'])['stage'] == 'superseded'
    assert store.job(old['id'])['retryable'] is False
    assert provider.calls == []
    drain(store, enrichment)
    assert enrichment.detail(source)['summary']['status'] == 'succeeded'
    assert enrichment.detail(source, old['revision_id'])['summary']['status'] == 'not_started'


def test_unexpected_ai_worker_error_cannot_echo_secret(bench, monkeypatch):
    client, store, enrichment, _, _ = bench
    first = config(client)
    bind(client, first['id'])
    source = ingest(bench)
    job = enqueue(client, source, kinds=['summary'])[0]
    def fail_process(job):
        raise RuntimeError(SECRET + ' Authorization: raw-provider-body')
    monkeypatch.setattr(enrichment, 'process', fail_process)
    assert run_one(store, enrichment)
    failure = store.job(job['id'])
    assert failure['status'] == 'failed'
    assert SECRET not in failure['error'] and 'Authorization' not in failure['error']


def test_cancel_running_auto_outbox_before_queue_creates_no_children(bench, monkeypatch):
    client, store, enrichment, _, provider = bench
    first = config(client)
    bind(client, first['id'], auto=True, kinds=('summary',))
    source = ingest(bench)
    outbox = next(j for j in store.jobs()['jobs'] if j['kind'] == 'ai_schedule')
    original = enrichment.maybe_queue_auto
    def cancel_between_check_and_queue(source_id, revision_id=None, schedule_job=None):
        store.cancel(outbox['id'])
        return original(source_id, revision_id=revision_id, schedule_job=schedule_job)
    monkeypatch.setattr(enrichment, 'maybe_queue_auto', cancel_between_check_and_queue)
    assert run_one(store, enrichment)
    assert store.job(outbox['id'])['status'] == 'cancelled'
    assert not run_one(store, enrichment)
    assert enrichment.detail(source)['summary']['status'] == 'not_started'
    assert provider.calls == []


def test_auto_outbox_completion_and_child_queue_commit_atomically(bench, monkeypatch):
    client, store, enrichment, models, provider = bench
    first = config(client)
    bind(client, first['id'], auto=True, kinds=('summary',))
    source = ingest(bench, html('Public archives preserve original evidence and document versions for later review.'))
    outbox = next(j for j in store.jobs()['jobs'] if j['kind'] == 'ai_schedule')
    original = enrichment.maybe_queue_auto
    def crash_after_children_commit(source_id, revision_id=None, schedule_job=None):
        original(source_id, revision_id=revision_id, schedule_job=schedule_job)
        raise SystemExit('simulated scheduler loss after child queue commit')
    monkeypatch.setattr(enrichment, 'maybe_queue_auto', crash_after_children_commit)
    with pytest.raises(SystemExit):
        run_one(store, enrichment)
    assert store.job(outbox['id'])['status'] == 'succeeded'
    assert enrichment.detail(source)['summary']['status'] == 'queued'
    restarted = Store(store.root)
    restarted.recover()
    drain(restarted, EnrichmentService(restarted, models))
    assert enrichment.detail(source)['summary']['status'] == 'succeeded'
    assert {json.loads(call['messages'][-1]['content'])['task'] for call in provider.calls} == {'summary', 'translation', 'classification'}
