"""Model configuration is versioned, secret-safe, and independent of providers."""
import base64
import json
import os

import httpx
import pytest

from server.model_gateway import DPAPICipher, ModelError, ModelService, normalize_base_url
from server.store import Store


class TestCipher:
    __test__ = False

    def encrypt(self, text):
        return 'test-only:' + base64.b64encode(text.encode()).decode()

    def decrypt(self, text):
        return base64.b64decode(text.removeprefix('test-only:')).decode()


@pytest.fixture
def service(tmp_path):
    return ModelService(Store(tmp_path / 'models'), cipher=TestCipher())


def save(service, **changes):
    model = service.save_config(dict(name='本地文本模型', baseUrl='http://127.0.0.1:11434/v1/', model='user-chosen-model', **changes))
    service.save_preferences({'defaultModelId': model['id']})
    return model


def response(text='{"ok":true}', **changes):
    body = {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}],
            'model': 'actual-model', 'usage': {'prompt_tokens': 12, 'completion_tokens': 4, 'total_tokens': 16}}
    body.update(changes)
    return httpx.Response(200, json=body)


def test_new_model_requires_user_values(service):
    assert service.models() == []
    with pytest.raises(ModelError):
        service.resolve()
    with pytest.raises(ModelError):
        service.save_config({'name': '未配置'})
    assert service.models() == []


def test_config_public_redaction_immutable_revisions_and_restart(service):
    saved = save(service, apiKey='secret-key-never-public')
    assert saved['hasApiKey'] is True
    assert saved['version'] == 1
    assert saved['baseUrl'] == 'http://127.0.0.1:11434/v1'
    assert 'secret-key' not in json.dumps(saved)
    with service.store.connect() as db:
        row = db.execute('SELECT * FROM ai_model_configs').fetchone()
        assert 'secret-key-never-public' not in str(dict(row))
    second = service.save_config({'model': 'second-user-model', 'apiKey': ''}, saved['id'])
    assert second['id'] == saved['id'] and second['version'] == 2
    assert service.resolve(saved['id'], 1)['model'] == 'user-chosen-model'
    assert service.resolve(saved['id'], 2)['model'] == 'second-user-model'
    assert service.resolve()['apiKey'] == 'secret-key-never-public'
    restarted = ModelService(Store(service.store.root), cipher=TestCipher())
    assert restarted.models() == [second]
    assert restarted.resolve()['apiKey'] == 'secret-key-never-public'
    cleared = service.save_config({'clearApiKey': True}, saved['id'])
    assert cleared['hasApiKey'] is False
    assert service.resolve()['apiKey'] == ''
    assert service.resolve(saved['id'], 1)['apiKey'] == 'secret-key-never-public'


def test_key_failure_never_falls_back_to_plaintext(service):
    class BrokenCipher:
        def encrypt(self, text):
            raise RuntimeError('DO NOT ECHO user-secret')
    service.cipher = BrokenCipher()
    with pytest.raises(ModelError) as caught:
        save(service, apiKey='user-secret')
    assert 'user-secret' not in str(caught.value)
    assert service.models() == []
    # No-key local model works even if credential protection is unavailable.
    assert save(service)['hasApiKey'] is False


@pytest.mark.parametrize('url', ['https://user:pass@example.com/v1', 'http://127.0.0.1/v1?key=secret',
    'https://example.com/v1#fragment', 'file:///tmp/model', 'https://example.com:bad',
    'http://example.com/hello world', 'http://example.com\\evil', 'http://example.com/?'])
def test_endpoint_rejects_credential_or_ambiguous_url(url):
    with pytest.raises(ModelError):
        normalize_base_url(url)


def test_endpoint_preserves_explicit_path_and_local_lan():
    assert normalize_base_url('http://model.test:11434/v1') == 'http://model.test:11434/v1'
    assert normalize_base_url('https://EXAMPLE.COM/proxy/v1/chat/completions/') == 'https://example.com/proxy/v1'
    assert normalize_base_url('http://localhost:11434') == 'http://localhost:11434'
    assert normalize_base_url('http://[::1]:1234/v1') == 'http://[::1]:1234/v1'


@pytest.mark.parametrize('change', [{'contextChars': 1999}, {'contextChars': 1000001}, {'contextChars': True},
    {'maxOutputTokens': 0}, {'maxOutputTokens': 65537}, {'timeoutSeconds': 301}, {'timeoutSeconds': '90'},
    {'apiKey': '\r\ninjected'}, {'clearApiKey': 'yes'}, {'apiKey': None}, {'reasoningEffort': 'magic'}, {'jsonMode': 'yes'}])
def test_configuration_bounds(service, change):
    with pytest.raises(ModelError):
        save(service, **change)


def test_preferences_preserved_and_validated(service):
    assert service.get_preferences()['autoProcess'] is False
    first = save(service)
    assert service.save_preferences({'summaryModelId': first['id']})['summaryModelId'] == first['id']
    assert service.save_preferences({'autoProcess': True})['summaryModelId'] == first['id']
    with pytest.raises(ModelError):
        service.save_preferences({'translationModelId': 'missing'})
    with pytest.raises(ModelError):
        service.save_preferences({'summaryModelId': None})
    assert service.get_preferences()['summaryModelId'] == first['id']
    assert ModelService(service.store).get_preferences()['autoProcess'] is True


def test_model_creation_does_not_silently_select_a_default(service):
    first = service.save_config({'name': '待选择', 'baseUrl': 'http://localhost:11434/v1', 'model': 'test'})
    assert service.get_preferences()['defaultModelId'] is None
    with pytest.raises(ModelError, match='默认模型'):
        service.resolve()
    assert service.resolve(first['id'])['id'] == first['id']


def test_one_default_is_persisted_and_legacy_fields_are_only_mirrors(service):
    first, second = save(service), save(service)
    prefs = service.save_preferences({'defaultModelId': first['id'], 'autoProcess': True})
    assert all(prefs[key] == first['id'] for key in ('defaultModelId', 'summaryModelId', 'translationModelId', 'classificationModelId'))
    assert service.resolve()['id'] == first['id']
    with service.store.connect() as db:
        stored = json.loads(db.execute("SELECT value_json FROM ai_settings WHERE key='preferences'").fetchone()[0])
    assert stored == {'defaultModelId': first['id'], 'autoProcess': True, 'autoTranslate': True,
                      'idleProcess': False, 'idleStart': '00:00', 'idleEnd': '08:00'}
    changed = service.save_preferences({'translationModelId': second['id']})
    assert changed['defaultModelId'] == changed['summaryModelId'] == changed['classificationModelId'] == second['id']
    with pytest.raises(ModelError, match='统一'):
        service.save_preferences({'summaryModelId': first['id'], 'classificationModelId': second['id']})
    assert service.resolve()['id'] == second['id']


@pytest.mark.parametrize('legacy_field', ['summaryModelId', 'translationModelId', 'classificationModelId'])
def test_legacy_preferences_migrate_to_one_default_and_survive_restart(service, legacy_field):
    first, second = save(service), save(service)
    legacy = {legacy_field: first['id'], 'autoProcess': True}
    if legacy_field == 'summaryModelId':
        legacy['translationModelId'] = second['id']
        legacy['classificationModelId'] = second['id']
    with service.store.connect() as db:
        db.execute("INSERT OR REPLACE INTO ai_settings VALUES('preferences',?)", (json.dumps(legacy),))
    reopened = ModelService(service.store, cipher=TestCipher())
    assert reopened.get_preferences()['defaultModelId'] == first['id']
    assert reopened.resolve()['id'] == first['id']
    with service.store.connect() as db:
        saved = json.loads(db.execute("SELECT value_json FROM ai_settings WHERE key='preferences'").fetchone()[0])
    assert saved == {'defaultModelId': first['id'], 'autoProcess': True, 'autoTranslate': True,
                     'idleProcess': False, 'idleStart': '00:00', 'idleEnd': '08:00'}


def test_delete_default_disables_new_use_but_preserves_frozen_versions(service):
    first = save(service, apiKey='historical-key')
    second = save(service)
    service.save_preferences({'defaultModelId': first['id'], 'autoProcess': True})
    removed = service.delete_config(first['id'])
    assert removed['models'] == [second]
    assert removed['preferences']['defaultModelId'] is None
    assert removed['preferences']['autoProcess'] is False
    assert service.resolve(first['id'], first['version'])['apiKey'] == 'historical-key'
    for operation in (lambda: service.resolve(first['id']), lambda: service.resolve(),
                      lambda: service.save_config({'name': 'revive'}, first['id']),
                      lambda: service.save_preferences({'defaultModelId': first['id']})):
        with pytest.raises(ModelError):
            operation()
    reopened = ModelService(service.store, cipher=TestCipher())
    assert reopened.models() == [second]
    assert reopened.get_preferences()['defaultModelId'] is None
    assert reopened.resolve(first['id'], 1)['apiKey'] == 'historical-key'


def test_delete_other_model_keeps_default_and_auto_process(service):
    first, second = save(service), save(service)
    before = service.save_preferences({'defaultModelId': first['id'], 'autoProcess': True})
    assert service.delete_config(second['id'])['preferences'] == before


def test_gateway_sends_only_user_model_frozen_key_and_explicit_options(service):
    first = save(service, apiKey='frozen-key', reasoningEffort='none', jsonMode=True)
    frozen = service.resolve(first['id'], first['version'])
    service.save_config({'model': 'new-model', 'apiKey': 'new-key'}, first['id'])
    calls = []
    def handle(request):
        calls.append(request)
        assert str(request.url) == 'http://127.0.0.1:11434/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer frozen-key'
        payload = json.loads(request.content)
        assert payload['model'] == 'user-chosen-model'
        assert payload['max_tokens'] == 2048
        assert payload['stream'] is False
        assert payload['reasoning_effort'] == 'none'
        assert payload['response_format'] == {'type': 'json_object'}
        assert payload['messages'] == [{'role': 'user', 'content': 'JSON please'}]
        return response()
    service.transport = httpx.MockTransport(handle)
    result = service.complete(frozen, [{'role': 'user', 'content': 'JSON please', 'unexpected': 'discard'}])
    assert result['text'] == '{"ok":true}' and result['usage']['total_tokens'] == 16
    assert result['model'] == 'actual-model' and isinstance(result['durationMs'], int)
    assert len(calls) == 1


def test_no_key_local_model_sends_no_auth_header_and_no_optional_parameters(service):
    save(service)
    def handle(request):
        assert 'authorization' not in request.headers
        payload = json.loads(request.content)
        assert 'reasoning_effort' not in payload and 'response_format' not in payload
        return response()
    service.transport = httpx.MockTransport(handle)
    assert service.complete(service.resolve(), [{'role': 'user', 'content': 'Hello'}])['text']


def test_explicit_max_tokens_unsupported_gets_one_bounded_negotiation(service):
    save(service)
    calls = []
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(400, json={'error': {'param': 'max_tokens', 'code': 'unsupported_parameter', 'message': 'Use max_completion_tokens'}})
        assert 'max_tokens' not in body and body['max_completion_tokens'] == 2048
        return response()
    service.transport = httpx.MockTransport(handle)
    service.complete(service.resolve(), [{'role': 'user', 'content': 'hello'}])
    assert len(calls) == 2


@pytest.mark.parametrize('status', [301, 302, 307, 400, 401, 403, 404, 429, 500])
def test_http_errors_redact_provider_body_and_no_redirect_or_retry(service, status):
    save(service, apiKey='actual-secret')
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={'error': 'actual-secret and private content'}, headers={'location': 'https://foreign.example/steal'})
    service.transport = httpx.MockTransport(handle)
    with pytest.raises(ModelError) as caught:
        service.complete(service.resolve(), [{'role': 'user', 'content': 'private content'}])
    assert len(calls) == 1
    assert 'actual-secret' not in str(caught.value) and 'private content' not in str(caught.value)


def test_response_and_input_validation(service, monkeypatch):
    save(service)
    with pytest.raises(ModelError, match='字符预算'):
        service.complete(service.resolve(), [{'role': 'user', 'content': 'a' * 24001}])
    with pytest.raises(ModelError, match='文本消息'):
        service.complete(service.resolve(), [{'role': 'user', 'content': [{'type': 'image_url'}]}])
    for body in ({}, {'choices': []}, {'choices': [{'message': {'content': None}}]}):
        service.transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        with pytest.raises(ModelError):
            service.complete(service.resolve(), [{'role': 'user', 'content': 'hello'}])
    monkeypatch.setattr('server.model_gateway.MAX_RESPONSE_BYTES', 20)
    service.transport = httpx.MockTransport(lambda request: response('x' * 50))
    with pytest.raises(ModelError, match='过大'):
        service.complete(service.resolve(), [{'role': 'user', 'content': 'hello'}])


def test_truncated_response_is_not_a_success(service):
    save(service)
    service.transport = httpx.MockTransport(lambda request: response(choices=[{'message': {'content': '{"partial":'}, 'finish_reason': 'length'}]))
    with pytest.raises(ModelError, match='长度上限'):
        service.complete(service.resolve(), [{'role': 'user', 'content': 'hello'}])


def test_connection_only_reports_observed_capabilities_and_version(service):
    first = save(service)
    service.transport = httpx.MockTransport(lambda request: response())
    result = service.test_connection(first['id'])
    assert result['textGeneration'] is True and result['jsonParseable'] is True
    assert result['structuredOutput'] == 'not_tested'
    assert 'text' not in result and 'apiKey' not in result
    assert service.models()[0]['lastTest'] == result
    second = service.save_config({'name': '更新后配置'}, first['id'])
    assert second['lastTest'] is None
    service.transport = httpx.MockTransport(lambda request: response('ordinary text'))
    result = service.test_connection(first['id'])
    assert result['textGeneration'] is True and result['jsonParseable'] is False
    service.transport = httpx.MockTransport(lambda request: httpx.Response(401, text='do not leak secret'))
    result = service.test_connection(first['id'])
    assert result['textGeneration'] is False and 'do not leak' not in result['error']


@pytest.mark.parametrize('model_name', ['qwen3-embedding', 'qwen3-embedding:4b', 'QWEN3-EMBEDDING:0.6b'])
def test_qwen_embedding_connection_probes_vectors_without_changing_preferences(service, model_name):
    default = save(service)
    preferences = service.save_preferences({'autoTranslate': False, 'autoProcess': True, 'idleProcess': True})
    embedding = service.save_config({'name': '本地向量模型', 'baseUrl': 'http://127.0.0.1:11434/v1',
                                     'model': model_name, 'apiKey': 'embedding-secret'})
    calls = []

    def provider(request):
        calls.append(request)
        assert request.url.path == '/v1/embeddings'
        assert request.headers['authorization'] == 'Bearer embedding-secret'
        assert request.extensions['timeout']['read'] == 30
        body = json.loads(request.content)
        assert body['model'] == model_name and body['encoding_format'] == 'float'
        assert len(body['input']) == 1 and 'Query: 中文资料检索连接测试' in body['input'][0]
        assert 'messages' not in body
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [0.1, -0.2, 0.3]}],
                                         'usage': {'prompt_tokens': 22, 'total_tokens': 22, 'private': 'embedding-secret'}})

    service.transport = httpx.MockTransport(provider)
    result = service.test_connection(embedding['id'])
    assert len(calls) == 1
    assert result['embeddings'] is True and result['dimensions'] == 3
    assert result['textGeneration'] is False and result['jsonParseable'] is False
    assert result['structuredOutput'] == 'not_tested'
    assert '向量维度 3' in result['message']
    assert result['usage'] == {'prompt_tokens': 22, 'total_tokens': 22}
    assert result['modelId'] == embedding['id'] and result['configVersion'] == embedding['version']
    assert 'embedding-secret' not in json.dumps(result) and 'embedding' not in result
    assert service.get_preferences() == preferences and service.resolve()['id'] == default['id']
    assert next(item for item in service.models() if item['id'] == embedding['id'])['lastTest'] == result


@pytest.mark.parametrize('model_name', ['bge-m3', 'embeddinggemma', 'other-qwen3-embedding:4b',
                                      'qwen3-embedding:', 'qwen3-embedding:4b:extra'])
def test_connection_does_not_guess_unknown_embedding_models(service, model_name):
    model = service.save_config({'name': '用户模型', 'baseUrl': 'http://localhost:11434/v1', 'model': model_name})
    calls = []

    def provider(request):
        calls.append(request)
        assert request.url.path == '/v1/chat/completions'
        return response()

    service.transport = httpx.MockTransport(provider)
    result = service.test_connection(model['id'])
    assert len(calls) == 1 and result['textGeneration'] is True
    assert 'embeddings' not in result


@pytest.mark.parametrize('body', [
    {}, {'data': []}, {'data': [None]}, {'data': [{'index': 0, 'embedding': [1.0]}] * 2},
    {'data': [{'index': True, 'embedding': [1.0]}]}, {'data': [{'index': 1, 'embedding': [1.0]}]},
    {'data': [{'index': 0, 'embedding': []}]}, {'data': [{'index': 0, 'embedding': [0.0, 0]}]},
    {'data': [{'index': 0, 'embedding': [True]}]}, {'data': [{'index': 0, 'embedding': ['secret']}]},
    {'data': [{'index': 0, 'embedding': [float('nan')]}]},
    {'data': [{'index': 0, 'embedding': [float('inf')]}]},
    {'data': [{'index': 0, 'embedding': [1.0] * 8193}]},
])
def test_qwen_embedding_connection_rejects_invalid_vectors_without_chat_fallback(service, body):
    model = service.save_config({'name': '本地向量', 'baseUrl': 'http://localhost:11434/v1', 'model': 'qwen3-embedding:4b'})
    calls = []

    def provider(request):
        calls.append(request)
        assert request.url.path == '/v1/embeddings'
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=json.dumps(body).encode())

    service.transport = httpx.MockTransport(provider)
    result = service.test_connection(model['id'])
    assert len(calls) == 1 and result['embeddings'] is False and result['textGeneration'] is False
    assert 'error' in result and 'secret' not in result['error']
    assert 'dimensions' not in result


@pytest.mark.parametrize('failure', ['redirect', 'http', 'timeout', 'json', 'size'])
def test_qwen_embedding_connection_failure_is_bounded_redacted_and_not_retried(service, monkeypatch, failure):
    model = service.save_config({'name': '本地向量', 'baseUrl': 'http://localhost:11434/v1', 'model': 'qwen3-embedding:4b'})
    calls = []

    def provider(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('provider-secret', request=request)
        if failure == 'redirect':
            return httpx.Response(302, headers={'location': 'https://foreign.example/provider-secret'})
        if failure == 'http':
            return httpx.Response(500, text='provider-secret')
        if failure == 'json':
            return httpx.Response(200, text='provider-secret')
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1.0, 2.0]}]})

    if failure == 'size':
        monkeypatch.setattr('server.model_gateway.MAX_RESPONSE_BYTES', 20)
    service.transport = httpx.MockTransport(provider)
    result = service.test_connection(model['id'])
    assert len(calls) == 1 and result['embeddings'] is False
    assert result['textGeneration'] is False and 'provider-secret' not in result['error']
    assert 'dimensions' not in result


@pytest.mark.skipif(os.name != 'nt', reason='Windows current-user DPAPI round trip')
def test_actual_dpapi_roundtrip_is_ciphertext():
    cipher = DPAPICipher()
    encrypted = cipher.encrypt('test-key-generated-only-for-this-test')
    assert encrypted.startswith('dpapi:v1:') and 'test-key' not in encrypted
    assert cipher.decrypt(encrypted) == 'test-key-generated-only-for-this-test'
    with pytest.raises(ModelError):
        cipher.decrypt('not-a-protected-value')
