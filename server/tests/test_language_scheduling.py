"""Real new-import outbox decisions, using an isolated store and fake provider."""
import pytest

from server.enrichment import EnrichmentService
from server.language import document_needs_translation
from server.model_gateway import ModelService
from server.store import Store
from server.tests.test_ai_api import Cipher
from server.tests.test_enrichment import Gateway, publish


@pytest.mark.parametrize('main_text', [
    '原始资料保留完整证据和版本，整理过程使用中文记录并支持人工核对。',
    '原始資料保留完整證據與版本，整理過程使用中文記錄並支援人工核對。',
])
def test_import_outbox_ignores_minor_foreign_block_in_chinese_main(tmp_path, monkeypatch, main_text):
    store = Store(tmp_path / 'language-scheduling')
    models = ModelService(store, cipher=Cipher())
    model = models.save_config({'name': '语言调度测试模型', 'baseUrl': 'http://model.test/v1',
                                'model': 'fake-model', 'contextChars': 24000, 'maxOutputTokens': 4096})
    models.save_preferences({'defaultModelId': model['id'], 'autoProcess': False,
                             'autoTranslate': True, 'idleProcess': False})
    gateway = Gateway()
    monkeypatch.setattr(models, 'complete', gateway.complete)
    service = EnrichmentService(store, models)

    source = publish(store, [main_text * 25, 'The original records remain available for review.'],
                     title='Original records remain available for review')
    document = store.detail(source)
    assert document['languageCode'] == 'zh'
    assert not document_needs_translation(document)
    assert service.maybe_queue_auto(source) == []
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE revision_id=? AND kind IN ('ai_schedule','ai_translation')",
                              (document['revisionId'],)).fetchone()

    # A foreign control proves the default model and independent translation
    # switch are active during the actual source-publication transaction.
    foreign = publish(store, 'The original records remain available for review.',
                      url='https://example.org/foreign-control', title='Foreign control')
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM jobs WHERE source_id=? AND kind='ai_schedule' AND status='queued'",
                          (foreign,)).fetchone()
    assert gateway.calls == []
