"""Conservative card cover publication, revision/attempt isolation and vision IO."""
import base64
from io import BytesIO
import json

import httpx
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.card_cover import CardCoverService, PROMPT_VERSION, validate_selection
from server.enrichment import EnrichmentService
from server.model_gateway import ModelError, ModelService
from server.store import Store
from server.worker import run_one

LOCAL = {'X-Wenshu-Client': 'local'}


def image_bytes(size=(640, 400), color='#376951'):
    output = BytesIO()
    Image.new('RGB', size, color).save(output, format='PNG')
    return output.getvalue()


def publish(store, *, images=True, suffix='one', url='https://cover.example/article'):
    job = store.queue('file', {}, '测试配图')
    claimed = store.claim()
    prose = '文章讨论绿色公共空间和城市公园，照片展示文章介绍的城市生态景观。' * 3 + suffix
    parsed = {'title': '城市公园与绿色空间', 'body': [prose], 'language': 'zh', 'parser_version': 'cover-test',
              'images': [{'mime': 'image/png', 'data': image_bytes(), 'alt': '城市公园', 'locator': {'ordinal': 1}}] if images else []}
    store.publish(claimed, {'url': url}, ('<html>' + prose + '</html>').encode(), parsed)
    return store.job(job['id'])['source_id']


class Provider:
    def __init__(self):
        self.calls = []
        self.choice = 'first'
        self.confidence = .94
        self.failure = None
        self.callback = None

    def __call__(self, request):
        payload = json.loads(request.content)
        self.calls.append(payload)
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        if self.failure:
            return httpx.Response(self.failure, json={'error': {'message': 'private-provider-detail'}})
        context = json.loads(payload['messages'][1]['content'][0]['text'])
        selected = context['candidates'][0]['assetId'] if self.choice == 'first' else self.choice
        value = {'assetId': selected, 'confidence': self.confidence, 'reason': '主体清晰且与文章讨论的城市公园直接相关。' if selected else '候选图片均不适合缩略展示。'}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value)}, 'finish_reason': 'stop'}],
                                        'model': payload['model'], 'usage': {'total_tokens': 22}})


@pytest.fixture
def bench(tmp_path):
    store = Store(tmp_path / 'data')
    provider = Provider()
    models = ModelService(store, transport=httpx.MockTransport(provider))
    config = models.save_config({'name': '已选视觉模型', 'baseUrl': 'http://model.invalid/v1', 'model': 'user-chosen-vision', 'contextChars': 4000})
    models.save_preferences({'summaryModelId': config['id']})
    service = CardCoverService(store, models)
    source = publish(store)
    return store, models, provider, service, source, config


def execute(store, service):
    job = store.claim()
    service.process(job)
    return store.job(job['id'])


def test_selects_only_saved_candidate_with_persistent_identity_and_no_duplicate_model_call(bench):
    store, models, provider, service, source, config = bench
    queued = service.queue(source)
    assert queued['enqueued'] is True and queued['cardCover']['status'] == 'queued'
    assert queued['cardCover']['url'] is None
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['cardCover']
    assert result['status'] == 'completed' and result['url'].startswith('/api/assets/')
    assert result['modelId'] == config['id'] and result['modelVersion'] == 1 and result['promptVersion'] == PROMPT_VERSION
    assert store.documents()['documents'][0]['cardCover'] == result
    assert service.queue(source)['enqueued'] is False and len(provider.calls) == 1
    image = provider.calls[0]['messages'][1]['content'][2]['image_url']['url']
    assert image.startswith('data:image/jpeg;base64,')
    with Image.open(BytesIO(base64.b64decode(image.split(',', 1)[1]))) as decoded:
        assert max(decoded.size) <= 1024 and not decoded.getexif()
    with store.connect() as db:
        assert 'base64' not in str([tuple(row) for row in db.execute('SELECT * FROM ai_card_covers')])


@pytest.mark.parametrize('choice,confidence', [(None, .95), ('first', .79)])
def test_model_can_decline_and_low_confidence_is_never_a_fallback(bench, choice, confidence):
    store, models, provider, service, source, config = bench
    provider.choice, provider.confidence = choice, confidence
    service.queue(source)
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['cardCover']
    assert result['status'] == 'completed' and result['assetId'] is None and result['url'] is None


def test_no_candidate_does_not_call_model_or_create_job(bench):
    store, models, provider, service, source, config = bench
    source = publish(store, images=False, url='https://cover.example/text')
    assert service.queue(source)['cardCover']['status'] == 'no_candidates'
    assert not provider.calls and store.claim() is None


def test_invalid_foreign_image_and_failure_never_retry_or_publish(bench):
    store, models, provider, service, source, config = bench
    provider.choice = 'https://foreign.example/image.png'
    first = service.queue(source)
    assert execute(store, service)['status'] == 'failed'
    assert service.detail(source)['cardCover']['url'] is None
    assert service.queue(source)['job']['id'] == first['job']['id']
    assert store.claim() is None and len(provider.calls) == 1
    provider.failure = 429
    service.queue(source, force=True)
    assert execute(store, service)['status'] == 'failed'
    assert service.detail(source)['cardCover']['url'] is None
    assert 'private-provider' not in json.dumps(service.detail(source))


@pytest.mark.parametrize('action', ['cancel', 'new_revision', 'force', 'retry_attempt'])
def test_late_model_result_cannot_publish_after_identity_or_attempt_changes(bench, action):
    store, models, provider, service, source, config = bench
    first = service.queue(source)
    def change():
        if action == 'cancel':
            store.cancel(first['job']['id'])
        elif action == 'new_revision':
            publish(store, suffix='two')
        elif action == 'force':
            service.queue(source, force=True)
        else:
            store.cancel(first['job']['id'])
            store.retry(first['job']['id'])
            assert store.claim()['id'] == first['job']['id']
    provider.callback = change
    execute(store, service)
    assert service.detail(source)['cardCover']['url'] is None
    with store.connect() as db:
        assert db.execute('SELECT result_json FROM ai_card_covers WHERE job_id=?', (first['job']['id'],)).fetchone()[0] is None


def test_model_version_or_candidate_update_hides_previous_result(bench):
    store, models, provider, service, source, config = bench
    service.queue(source)
    execute(store, service)
    models.save_config({'name': '模型参数更新'}, config['id'])
    assert service.detail(source)['cardCover']['status'] == 'not_processed'
    assert service.detail(source)['cardCover']['modelVersion'] == 2
    assert store.documents()['documents'][0]['cardCover']['status'] == 'not_processed'
    service.queue(source)
    execute(store, service)
    revision = store.detail(source)['revisionId']
    with store.connect() as db:
        db.execute("UPDATE revision_image_candidates SET candidates_json='[]' WHERE revision_id=?", (revision,))
    assert service.detail(source)['cardCover']['url'] is None
    assert store.documents()['documents'][0]['cardCover']['url'] is None


def test_api_revision_fencing_and_selected_model(bench):
    store, models, provider, service, source, config = bench
    app = create_app(data_dir=store.root, model_service=models)
    with TestClient(app) as client:
        revision = store.detail(source)['revisionId']
        assert client.post(f'/api/documents/{source}/card-cover', json={}, headers=LOCAL).status_code == 202
        assert run_one(store, EnrichmentService(store, models), card_covers=service)
        response = client.get(f'/api/documents/{source}/card-cover', params={'revision_id': revision, 'model_id': config['id']})
        assert response.json()['cardCover']['url']
        assert client.post(f'/api/documents/{source}/card-cover', json={'revisionId': 'old'}, headers=LOCAL).status_code == 422
        assert client.post(f'/api/documents/{source}/card-cover', json={'force': 'yes'}, headers=LOCAL).status_code == 422
        models.save_preferences({'summaryModelId': None})
        assert client.post(f'/api/documents/{source}/card-cover', json={}, headers=LOCAL).status_code == 422


def test_vision_gateway_rejects_remote_unknown_oversized_or_disguised_images_and_text_contract_stays(bench):
    store, models, provider, service, source, config = bench
    snapshot = models.resolve(config['id'])
    for uri in ('https://remote.example/image.png', 'file:///private.png', 'data:image/svg+xml;base64,PHN2Zy8+',
                'data:image/png;base64,' + base64.b64encode(b'not-an-image').decode(),
                'data:image/png;base64,' + base64.b64encode(image_bytes((1025, 10))).decode()):
        with pytest.raises(ModelError):
            models.complete_vision(snapshot, [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': uri}}]}])
    with pytest.raises(ModelError, match='文本消息'):
        models.complete(snapshot, [{'role': 'user', 'content': [{'type': 'text', 'text': 'hello'}]}])
    assert not provider.calls


def test_validation_rejects_numerical_or_url_injection():
    candidates = [{'assetId': 'local'}]
    for bad in ({'assetId': 'other', 'confidence': .95, 'reason': '理由'},
                {'assetId': 'local', 'confidence': True, 'reason': '理由'},
                {'assetId': 'local', 'confidence': float('nan'), 'reason': '理由'},
                {'assetId': 'local', 'confidence': .95, 'reason': ''},
                {'assetId': 'local', 'confidence': .95, 'reason': '理由', 'url': 'https://foreign.example'}):
        with pytest.raises(ValueError):
            validate_selection(bad, candidates)
