"""Source language is durable revision metadata, never a topic path."""
from server.enrichment import EnrichmentService
from server.store import Store
from server.tests.test_enrichment import Gateway, execute, publish


def test_classification_identifies_language_without_changing_manual_topics(tmp_path):
    store = Store(tmp_path / 'language')
    source = publish(store, title='Document source')
    store.patch(source, {'tags': ['人工主题']})
    gateway = Gateway()
    gateway.rewrite = lambda result: result | {'sourceLanguage': 'fr'}
    service = EnrichmentService(store, gateway)
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    assert len(gateway.calls) == 1  # Language is part of the classification call.
    document = Store(store.root).detail(source)
    assert document['languageCode'] == 'fr'
    assert document['languageLabel'] == '法语'
    assert document['languageTag'] == '语言：法语'
    assert document['languageIdentification'] == 'model'
    assert document['tags'] == ['人工主题']
    assert document['classificationPaths'] == []
    assert document['classificationManual'] is True
    assert document['blocks'][0]['text'].startswith('Knowledge governance')
    assert service.detail(source)['classification']['result']['applied'] is False
    assert store.documents()['documents'][0]['languageCode'] == 'fr'
    assert store.search('governance')['results'][0]['languageCode'] == 'fr'


def test_language_is_revision_scoped_and_cannot_cross_source(tmp_path):
    store = Store(tmp_path / 'revisions')
    source = publish(store)
    previous = store.detail(source)['revisionId']
    other = publish(store, url='https://example.org/other', title='Other source')
    publish(store, text='A revised source document with new original material.')
    current = store.detail(source)['revisionId']
    with store.connect() as db:
        store.set_document_language(db, source, previous, 'de')
        store.set_document_language(db, other, current, 'ja')
    assert store.detail(source)['languageCode'] == 'en'
    assert store.detail(source, previous)['languageCode'] == 'de'
    assert store.documents()['documents'][0]['languageCode'] == 'en'


def test_compatibility_fallback_cannot_erase_identified_language(tmp_path):
    store = Store(tmp_path / 'fallback')
    source = publish(store)
    revision = store.detail(source)['revisionId']
    with store.connect() as db:
        store.set_document_language(db, source, revision, 'fr')
        store.set_document_language(db, source, revision, 'en', identification='fallback')
        store.set_document_language(db, source, revision, 'und')
        store.set_document_language(db, source, revision, 'foreign')
    assert store.detail(source)['languageTag'] == '语言：法语'
    with store.connect() as db:
        store.set_document_language(db, source, revision, 'fr-FR')
    assert store.detail(source)['languageCode'] == 'fr'


def test_parser_language_tags_available_before_classification(tmp_path):
    store = Store(tmp_path / 'parser')
    source = publish(store)
    assert store.detail(source)['languageTag'] == '语言：英语'
    assert store.detail(source)['languageIdentification'] == 'parser'
    assert store.detail(source)['tags'] == ['未分类']


def test_old_parser_hints_cannot_make_list_and_reader_disagree(tmp_path):
    store = Store(tmp_path / 'old-parser')
    source = publish(store, '我国积极推进 AI 与 GPU 相关技术的研究，并保留原始资料供日后核查。')
    revision = store.detail(source)['revisionId']
    with store.connect() as db:
        db.execute("UPDATE revisions SET language='mixed' WHERE id=?", (revision,))
        store.set_document_language(db, source, revision, 'zh')
    for document in [store.documents()['documents'][0], store.detail(source)]:
        assert document['needsTranslation'] is False
        assert document['languageCode'] == 'zh'
        assert document['languageTag'] == '语言：中文'
    source = publish(store, '日本の行政資料を管理し、原文をそのまま保存します。すべての資料を確認できます。', url='https://example.org/japanese')
    revision = store.detail(source)['revisionId']
    with store.connect() as db:
        db.execute("UPDATE revisions SET language='zh' WHERE id=?", (revision,))
    listed = next(document for document in store.documents()['documents'] if document['id'] == source)
    for document in [listed, store.detail(source)]:
        assert document['needsTranslation'] is True
        assert document['languageCode'] == 'ja'


def test_source_language_change_invalidates_only_its_cached_decision(tmp_path):
    store = Store(tmp_path / 'cache')
    source = publish(store, '人工知能政策研究及関連制度整備状況分析報告。地域産業構造改革及公共制度調査結果。')
    revision = store.detail(source)['revisionId']
    assert store.detail(source)['needsTranslation'] is False
    with store.connect() as db:
        store.set_document_language(db, source, revision, 'ja')
    assert store.documents()['documents'][0]['needsTranslation'] is True
    assert store.detail(source)['needsTranslation'] is True
