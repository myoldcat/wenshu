"""Automatic model selection uses saved settings without probing providers."""
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from server.model_gateway import ModelService
from server.store import Store, dumps
from server.tests.test_model_gateway import TestCipher
from server.tests.test_writing_integrity import add_source, controlled_matching_result, controlled_outline_result
from server.writing import WritingService
from server.writing_sentence_citations import strip_citations


BODY = '原始证据和固定版本应予保留。'
SECRET = 'synthetic-model-selection-private-credential'


class AutomaticProvider:
    def __init__(self):
        self.requests = []
        self.fail_models = set()
        self.paragraphs = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append({'path': request.url.path, 'model': body['model'], 'body': copy.deepcopy(body)})
        if body['model'] in self.fail_models:
            raise httpx.ConnectError(SECRET + ': private network failure', request=request)
        if request.url.path.endswith('/embeddings'):
            return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [0.5, 0.75]}], 'usage': {'total_tokens': 2}})
        try:
            packet = json.loads(body['messages'][-1]['content'])
        except ValueError:
            packet = {}
        if packet.get('task') == 'writing_automatic_material_queries':
            result = {'queries': [packet['topic'][:300]]}
        elif packet.get('task') == 'writing_outline':
            result = {'sections': [{'title': title, 'goal': '整理资料与固定版本。', 'mode': packet['mode'], 'targetWords': 100}
                                   for title in ('原文保存', '版本管理')], 'issues': []}
            result = controlled_outline_result(packet, result)
        elif packet.get('task') == 'writing_citation_match':
            result = json.loads(controlled_matching_result(packet, self.paragraphs)['text'])
        elif packet.get('task') == 'writing_section':
            block = packet['blocks'][0]
            self.paragraphs = [{'text': BODY, 'kind': 'supported', 'evidence': [{
                'blockId': block['blockId'], 'quote': block['text'], 'claim': BODY}]}]
            result = {'paragraphs': copy.deepcopy(self.paragraphs), 'issues': []}
        else:
            assert not packet.get('task'), 'Unexpected model task: ' + str(packet.get('task'))
            result = {'ok': True}  # Only explicit, mocked model-connection tests.
        return httpx.Response(200, json={'model': body['model'], 'choices': [{'message': {
            'content': dumps(result)}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 15}})


@pytest.fixture
def automatic_models(tmp_path):
    store, provider = Store(tmp_path / 'automatic-model-selection'), AutomaticProvider()
    models = ModelService(store, cipher=TestCipher(), transport=httpx.MockTransport(provider))
    service = WritingService(store, models)
    source = add_source(store)
    return SimpleNamespace(store=store, provider=provider, models=models, service=service, source=source, serial=0)


def add_model(env, label, **changes):
    payload = {'name': label, 'baseUrl': 'http://mock-model.test/v1', 'model': label,
               'contextChars': 40000, 'maxOutputTokens': 4096, **changes}
    model = env.models.save_config(payload)
    env.serial += 1
    # Give the actual ModelService list a deterministic order independent of
    # timestamp resolution and randomly generated configuration identifiers.
    with env.store.connect() as db:
        db.execute('UPDATE ai_model_configs SET created_at=? WHERE id=? AND version=?',
                   ('2026-10-25T00:00:%02dZ' % env.serial, model['id'], model['version']))
    return env.models.snapshot(model['id'])


def create(env, model_id=None, automation='automatic'):
    return env.service.create({'title': '自动选择写作模型', 'mode': 'strict', 'sourceIds': [env.source],
                               'modelId': model_id, 'brief': {'automation': automation, 'targetWords': 10000}})


def run_row(env, run_id):
    with env.store.connect() as db:
        row = db.execute('SELECT * FROM writing_runs WHERE id=?', (run_id,)).fetchone()
    return dict(row)


def writing_job_count(env):
    with env.store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM jobs WHERE kind LIKE 'writing_%'").fetchone()[0]


def assert_selected_without_probe(env, queued, model):
    row = run_row(env, queued['runId'])
    assert row['model_id'] == model['id'] and row['model_version'] == model['version']
    assert json.loads(row['model_json'])['id'] == model['id']
    assert env.provider.requests == [], 'Selecting a writing model made an unsolicited provider request'


def verify_text(env, model):
    test = env.models.test_connection(model['id'])
    assert test['textGeneration'] is True
    env.provider.requests.clear()


def test_automatic_prefers_valid_default_over_remembered_manual_model_and_verified_fallback(automatic_models):
    env = automatic_models
    remembered = add_model(env, 'remembered-manual')
    fallback = add_model(env, 'verified-fallback')
    default = add_model(env, 'untested-default')
    verify_text(env, fallback)
    env.models.save_preferences({'defaultModelId': default['id']})
    project = create(env, remembered['id'])
    preferences = copy.deepcopy(env.models.get_preferences())
    queued = env.service.queue_automatic(project['id'], project['version'])
    assert_selected_without_probe(env, queued, default)
    assert env.service.get(project['id'])['modelId'] == remembered['id']
    assert env.models.get_preferences() == preferences


def test_without_default_verified_text_model_precedes_earlier_untested_config(automatic_models):
    env = automatic_models
    earlier = add_model(env, 'earlier-untested')
    verified = add_model(env, 'later-verified')
    verify_text(env, verified)
    assert env.models.get_preferences()['defaultModelId'] is None
    project = create(env, earlier['id'])
    queued = env.service.queue_automatic(project['id'], project['version'])
    assert_selected_without_probe(env, queued, verified)
    assert env.service.get(project['id'])['modelId'] == earlier['id']
    assert env.models.get_preferences()['defaultModelId'] is None


def test_without_default_or_verification_uses_current_list_order_instead_of_remembered_choice(automatic_models):
    env = automatic_models
    first = add_model(env, 'first-current-config')
    remembered = add_model(env, 'remembered-later-config')
    assert [model['id'] for model in env.models.models()] == [first['id'], remembered['id']]
    project = create(env, remembered['id'])
    queued = env.service.queue_automatic(project['id'], project['version'])
    assert_selected_without_probe(env, queued, first)


@pytest.mark.parametrize('embedding_case', ['canonical_untested', 'canonical_verified', 'embedding_only_snapshot'])
def test_embedding_only_default_and_remembered_models_are_skipped(automatic_models, embedding_case):
    env = automatic_models
    embedding = add_model(env, 'vector-configuration', model='qwen3-embedding:4b' if embedding_case != 'embedding_only_snapshot' else 'custom-vector')
    if embedding_case == 'canonical_verified':
        result = env.models.test_connection(embedding['id'])
        assert result['embeddings'] and not result['textGeneration']
    elif embedding_case == 'embedding_only_snapshot':
        # A capability record captured by an earlier embedding connection test.
        with env.store.connect() as db:
            db.execute('INSERT INTO ai_settings VALUES(?,?)', (f"test:{embedding['id']}:{embedding['version']}",
                       dumps({'modelId': embedding['id'], 'configVersion': embedding['version'],
                              'textGeneration': False, 'embeddings': True, 'testedAt': '2026-10-25T00:00:00Z'})))
    text = add_model(env, 'available-text')
    env.models.save_preferences({'defaultModelId': embedding['id']})
    env.provider.requests.clear()
    project = create(env, embedding['id'])
    queued = env.service.queue_automatic(project['id'], project['version'])
    assert_selected_without_probe(env, queued, text)
    assert env.models.get_preferences()['defaultModelId'] == embedding['id']
    assert env.service.get(project['id'])['modelId'] == embedding['id']


def test_broken_default_credential_skips_to_resolvable_text_without_disclosing_key(automatic_models, monkeypatch):
    env = automatic_models
    broken = add_model(env, 'broken-default', apiKey=SECRET)
    usable = add_model(env, 'resolvable-text')
    env.models.save_preferences({'defaultModelId': broken['id']})
    decrypt = env.models.cipher.decrypt

    def reject_broken_key(value):
        if decrypt(value) == SECRET:
            raise RuntimeError(SECRET + ': private credential-storage detail')
        return decrypt(value)

    monkeypatch.setattr(env.models.cipher, 'decrypt', reject_broken_key)
    project = create(env, broken['id'])
    queued = env.service.queue_automatic(project['id'], project['version'])
    assert_selected_without_probe(env, queued, usable)
    assert SECRET not in dumps(queued)
    assert env.models.get_preferences()['defaultModelId'] == broken['id']


def test_transport_failed_default_is_still_eligible_and_not_mislabeled_embedding(automatic_models):
    env = automatic_models
    default = add_model(env, 'temporarily-offline-text')
    fallback = add_model(env, 'verified-other-text')
    verify_text(env, fallback)
    env.provider.fail_models.add(default['model'])
    failed = env.models.test_connection(default['id'])
    assert failed['textGeneration'] is False and failed.get('error')
    assert not failed.get('embeddings')
    env.provider.fail_models.clear()
    env.provider.requests.clear()
    env.models.save_preferences({'defaultModelId': default['id']})
    project = create(env, fallback['id'])
    queued = env.service.queue_automatic(project['id'], project['version'])
    assert_selected_without_probe(env, queued, default)


@pytest.mark.parametrize('availability', ['no_configs', 'embedding_only'])
def test_no_usable_model_fails_safely_before_creating_any_writing_job(automatic_models, availability):
    env = automatic_models
    if availability == 'embedding_only':
        only = add_model(env, 'only-vector', model='qwen3-embedding:4b')
        env.models.save_preferences({'defaultModelId': only['id']})
    project = create(env)
    with pytest.raises(ValueError) as caught:
        env.service.queue_automatic(project['id'], project['version'])
    message = str(caught.value)
    assert '模型' in message and SECRET not in message
    assert writing_job_count(env) == 0 and env.provider.requests == []
    assert env.service.get(project['id'])['version'] == project['version']


def test_all_credential_failures_return_safe_selection_error_before_queue(automatic_models, monkeypatch):
    env = automatic_models
    model = add_model(env, 'unresolvable-only', apiKey=SECRET)
    env.models.save_preferences({'defaultModelId': model['id']})
    project = create(env, model['id'])

    def fail_decrypt(_value):
        raise RuntimeError(SECRET + ': private cipher diagnostic')

    monkeypatch.setattr(env.models.cipher, 'decrypt', fail_decrypt)
    with pytest.raises(ValueError) as caught:
        env.service.queue_automatic(project['id'], project['version'])
    assert '模型' in str(caught.value)
    assert SECRET not in str(caught.value) and 'cipher' not in str(caught.value)
    assert writing_job_count(env) == 0 and env.provider.requests == []


def test_selected_id_and_version_stay_frozen_for_parent_and_all_children_after_settings_change(automatic_models):
    env = automatic_models
    remembered = add_model(env, 'remembered-manual')
    selected = add_model(env, 'selected-original')
    replacement = add_model(env, 'future-default')
    env.models.save_preferences({'defaultModelId': selected['id']})
    project = create(env, remembered['id'])
    queued = env.service.queue_automatic(project['id'], project['version'])
    assert_selected_without_probe(env, queued, selected)
    env.models.save_preferences({'defaultModelId': replacement['id']})
    newer = env.models.save_config({'model': 'selected-future-revision'}, selected['id'])
    assert newer['version'] == selected['version'] + 1
    WritingService(env.store, env.models).process(env.store.claim())
    current = env.service.get(project['id'])
    assert env.store.job(queued['job']['id'])['status'] == 'succeeded'
    assert all(strip_citations(section['content']) == BODY for section in current['sections'])
    assert current['automatic']['report']['summary']['generatedSections'] == 2
    assert current['modelId'] == remembered['id']
    assert env.models.get_preferences()['defaultModelId'] == replacement['id']
    assert env.provider.requests and {request['model'] for request in env.provider.requests} == {selected['model']}
    with env.store.connect() as db:
        rows = db.execute('SELECT model_id,model_version FROM writing_runs WHERE project_id=?', (project['id'],)).fetchall()
    assert len(rows) >= 4
    assert {(row['model_id'], row['model_version']) for row in rows} == {(selected['id'], selected['version'])}


def test_duplicate_automatic_queue_reuses_original_when_config_was_removed_and_default_cleared(automatic_models):
    env = automatic_models
    model = add_model(env, 'removed-after-start')
    env.models.save_preferences({'defaultModelId': model['id']})
    project = create(env, model['id'])
    first = env.service.queue_automatic(project['id'], project['version'])
    env.models.delete_config(model['id'])
    assert env.models.models() == [] and env.models.get_preferences()['defaultModelId'] is None
    repeated = env.service.queue_automatic(project['id'], project['version'])
    assert repeated['runId'] == first['runId'] and repeated['job']['id'] == first['job']['id']
    assert writing_job_count(env) == 1 and env.provider.requests == []
    row = run_row(env, repeated['runId'])
    assert row['model_id'] == model['id'] and row['model_version'] == model['version']


def test_automatic_create_preserves_stale_remembered_model_but_manual_create_rejects_it(automatic_models):
    env = automatic_models
    usable = add_model(env, 'system-default')
    env.models.save_preferences({'defaultModelId': usable['id']})
    stale = 'removed-manual-model'
    automatic = create(env, stale)
    assert automatic['modelId'] == stale
    with pytest.raises(ValueError):
        create(env, stale, automation='manual')
    queued = env.service.queue_automatic(automatic['id'], automatic['version'])
    assert_selected_without_probe(env, queued, usable)
    assert env.service.get(automatic['id'])['modelId'] == stale


def test_full_automatic_save_can_keep_removed_manual_choice_without_weakening_manual_validation(automatic_models):
    env = automatic_models
    remembered = add_model(env, 'former-manual-choice')
    usable = add_model(env, 'current-default')
    env.models.save_preferences({'defaultModelId': usable['id']})
    project = create(env, remembered['id'], automation='manual')
    env.models.delete_config(remembered['id'])
    automatic = env.service.patch(project['id'], project['version'], {
        'title': '切换全自动并保存要求', 'brief': {'automation': 'automatic', 'purpose': '保存已有手动模型选择'},
        'mode': 'strict', 'sourceIds': [env.source], 'modelId': remembered['id']})
    assert automatic['modelId'] == remembered['id'] and automatic['brief']['automation'] == 'automatic'
    with pytest.raises(ValueError):
        env.service.patch(automatic['id'], automatic['version'], {
            'brief': {'automation': 'manual'}, 'modelId': remembered['id']})
    unchanged = env.service.get(project['id'])
    assert unchanged['version'] == automatic['version'] and unchanged['brief']['automation'] == 'automatic'
    queued = env.service.queue_automatic(automatic['id'], automatic['version'])
    assert_selected_without_probe(env, queued, usable)
    assert env.service.get(project['id'])['modelId'] == remembered['id']
