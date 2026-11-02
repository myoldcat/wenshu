"""Chinese display/search is a projection; original evidence never changes."""
import pytest

from server.store import Store
from server.enrichment import EnrichmentService
from server.model_gateway import ModelService
from server.tests.test_enrichment import Gateway, publish, execute
from server.tests.test_ai_api import Cipher, bench, config, LOCAL
from server.worker import run_one


@pytest.fixture
def translated(tmp_path):
    store = Store(tmp_path / 'chinese')
    gateway = Gateway()
    service = EnrichmentService(store, gateway)
    source = publish(store, title='Ferry information')
    gateway.rewrite = lambda result: ({'segments': [dict(part, text='天星渡轮服务调整' if ':title' in part['id'] else '渡轮航线将调整班次，乘客需要保留船票。')
                                                 for part in result['segments']]} if 'segments' in result else result)
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    return store, gateway, service, source


def test_translated_body_search_preserves_original_block_and_original_query(translated):
    store, _, _, source = translated
    document = store.detail(source)
    assert document['originalTitle'] == 'Ferry information'
    assert document['title'] != document['originalTitle']
    assert document['summarySource'] == 'translation'
    assert document['summaryLanguage'] == 'zh-CN'
    assert 'Knowledge governance' in document['blocks'][0]['text']
    result = store.search('渡轮')['results'][0]
    assert result['id'] == source
    assert result['matchBlockId'] == document['blocks'][0]['id']
    assert result['matchLanguage'] == 'zh-CN'
    assert store.search('governance')['results'][0]['id'] == source
    assert store.search('渡轮', source_ids=[])['total'] == 0
    assert store.search('渡轮', source_ids=[source])['total'] == 1


def test_manual_translation_replaces_index_and_survives_success_and_failure(translated):
    store, gateway, service, source = translated
    block = store.detail(source)['blocks'][0]
    service.edit_translation(source, block['id'], '人工核定的港口运输资料，船票可用于核查。')
    assert store.search('人工核定')['results'][0]['matchBlockId'] == block['id']
    assert store.search('班次')['total'] == 0
    service.queue(source, ['translation'], force=True)
    assert execute(store, service)['status'] == 'succeeded'
    assert store.search('人工核定')['total'] == 1
    gateway.fail_at = len(gateway.calls) + 1
    service.queue(source, ['translation'], force=True)
    assert execute(store, service)['status'] == 'failed'
    assert store.search('人工核定')['total'] == 1


def test_new_revision_and_old_revision_reindex_cannot_leak_old_translation(translated):
    store, _, _, source = translated
    old_revision = store.detail(source)['revisionId']
    assert publish(store, text='New environmental science document with different evidence and no transport claims.',
                   title='Ecology update') == source
    current = store.detail(source)
    assert current['title'] == 'Ecology update'
    assert current['summarySource'] == 'original'
    assert store.search('班次')['total'] == 0
    with store.connect() as db:
        store.refresh_document_index(db, source, old_revision)
    assert store.search('班次')['total'] == 0
    assert store.detail(source, old_revision)['summarySource'] == 'translation'


def test_summary_keywords_search_and_reopen_migration(translated):
    store, _, service, source = translated
    service.queue(source, ['summary'])
    assert execute(store, service)['status'] == 'succeeded'
    assert store.detail(source)['summarySource'] == 'ai_summary'
    assert store.search('知识治理')['total'] == 1
    with store.connect() as db:
        db.execute('DELETE FROM document_chinese')
        db.execute('DELETE FROM translated_blocks')
        db.execute('DELETE FROM schema_version WHERE version=2')
    reopened = Store(store.root)
    assert reopened.search('班次')['total'] == 1
    assert reopened.detail(source)['summarySource'] == 'ai_summary'


def test_auto_translate_default_persists_and_validates(tmp_path):
    store = Store(tmp_path / 'preferences')
    models = ModelService(store, cipher=Cipher())
    assert models.get_preferences()['autoTranslate'] is True
    assert models.get_preferences()['autoProcess'] is False
    models.save_preferences({'autoTranslate': False})
    assert ModelService(Store(store.root), cipher=Cipher()).get_preferences()['autoTranslate'] is False
    with pytest.raises(ValueError):
        models.save_preferences({'autoTranslate': 'false'})


def test_manual_retry_after_switch_off_reuses_saved_translation_checkpoint(tmp_path):
    store = Store(tmp_path / 'retry')
    gateway = Gateway()
    service = EnrichmentService(store, gateway)
    source = publish(store, ['Original evidence must be retained. ' * 180, 'Records preserve earlier versions. ' * 180])
    service.maybe_queue_auto(source)
    gateway.callback = lambda: gateway.preferences.update(autoTranslate=False)
    stopped = execute(store, service)
    assert stopped['stage'] == 'auto_disabled'
    assert len(gateway.calls) == 1
    store.retry(stopped['id'])
    assert execute(store, service)['status'] == 'succeeded'
    with store.connect() as db:
        checkpoints = db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0]
    assert checkpoints > 1
    assert len(gateway.calls) == checkpoints  # The first completed call was reused.


@pytest.mark.parametrize('auto_process,auto_translate,expected', [
    (False, False, set()), (False, True, {'ai_translation'}),
    (True, False, {'ai_summary', 'ai_classification'}),
    (True, True, {'ai_translation', 'ai_summary', 'ai_classification'}),
])
def test_new_import_automatic_switch_matrix(bench, auto_process, auto_translate, expected):
    client, store, service, models, provider = bench
    model = config(client)
    response = client.put('/api/ai/preferences', headers=LOCAL, json={
        'defaultModelId': model['id'], 'autoProcess': auto_process, 'autoTranslate': auto_translate})
    assert response.status_code == 200
    source = publish(store)
    if expected:
        assert run_one(store, service)
    with store.connect() as db:
        kinds = {row[0] for row in db.execute("SELECT kind FROM jobs WHERE source_id=? AND kind IN ('ai_translation','ai_summary','ai_classification')", (source,))}
    assert kinds == expected
    assert provider.calls == []  # Scheduling never calls the model itself.

