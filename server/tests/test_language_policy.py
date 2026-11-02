"""Whole-document language shares drive display and every translation entry."""
import pytest

from server.language import (LANGUAGE_POLICY_VERSION, detect_language, document_needs_translation,
                             language_weight, source_language_from_shares)
from server.store import Store
from server.enrichment import EnrichmentService
from server.tests.test_enrichment import Gateway, execute, publish


@pytest.mark.parametrize('chinese,foreign,expected', [
    (70, 'the ' * 10, 'mixed'),
    (71, 'the ' * 9 + 'we', 'zh'),
    (30, 'the ' * 20 + 'we ' * 5, 'mixed'),
    (29, 'the ' * 23 + 'we', 'en'),
])
def test_language_share_threshold_includes_exactly_thirty_percent(chinese, foreign, expected):
    assert detect_language('文' * chinese + '\n' + foreign, 'en') == expected


@pytest.mark.parametrize('text', [
    '這份資料保留原始證據，供後續閱讀與核查。',
    '这份资料保留原始证据，供后续阅读与核查。',
    '这份资料與原始證據供后续閱讀。',
    '这份资料分析ソニー公司的产品，并继续用中文记录原始来源。',
    '这份资料分析 삼성 公司的产品，并继续用中文记录原始来源。',
    '這份資料介紹 AI、GPU、API 和 OpenAI、Google、Microsoft、ChatGPT。',
    '這份資料保留證據。\n```python\n' + 'print("hello world")\n' * 100 + '```',
    '這份資料保留證據。\n' + 'const source = records.map(item => item.text);\n' * 100,
])
def test_chinese_scripts_terms_entities_and_code_do_not_create_other_languages(text):
    assert detect_language(text, 'en') == 'zh'
    assert not document_needs_translation({'languageCode': 'mixed', 'blocks': [{'text': text}]})


def test_minor_foreign_blocks_and_foreign_titles_do_not_override_chinese_body():
    document = {'title': 'Public evidence governance', 'languageCode': 'mixed', 'blocks': [
        {'kind': 'paragraph', 'text': '這份資料保留來源與原始證據，方便後續閱讀與核查。' * 30},
        {'kind': 'paragraph', 'text': 'Public records remain available for review.'},
        {'kind': 'code', 'text': 'print("hello world")' * 500}]}
    assert not document_needs_translation(document)
    document['blocks'][0]['text'] = '這份資料保留證據。'
    assert document_needs_translation(document)


def test_three_languages_need_two_individually_above_threshold():
    assert source_language_from_shares({'en': .42, 'fr': .29, 'de': .29}) == 'en'
    assert source_language_from_shares({'en': .4, 'fr': .3, 'de': .3}) == 'mixed'
    assert source_language_from_shares({'zh-CN': .4, 'zh-Hant': .6}) == 'zh'


def test_entity_only_content_has_no_language_weight():
    assert language_weight('OpenAI Google Nvidia Microsoft ChatGPT GPU API') == 0
    assert detect_language('OpenAI Google Nvidia Microsoft ChatGPT GPU API') == 'und'


@pytest.mark.parametrize('text', [
    '日本の人工知能政策について説明します。',
    '政府は新制度の導入を検討しています。現場では住民への説明が進んでいます。',
    '東京の市場は拡大した。',
])
def test_japanese_grammar_is_not_removed_as_an_entity(text):
    assert detect_language(text) == 'ja'


def test_uppercase_foreign_prose_is_not_a_list_of_acronyms():
    assert detect_language('THIS REPORT EXAMINES THE EVIDENCE AND RECORDS FOR PUBLIC REVIEW.', 'en') == 'en'


def test_long_named_entity_phrase_does_not_outweigh_chinese_prose():
    assert detect_language('中文正文介绍公开资料和核查流程。 Acme Research Institute International Policy Council.') == 'zh'


def test_manual_translation_preserves_traditional_chinese_exactly(tmp_path):
    store = Store(tmp_path / 'traditional')
    original = '這份資料記錄資訊治理，保留來源與原始證據，方便後續閱讀與核查。' * 4
    source = publish(store, [original, 'Public records remain available.'], title='繁體中文資料')
    models = Gateway()
    service = EnrichmentService(store, models)
    assert service.maybe_queue_auto(source) == []
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    assert models.calls == []
    result = service.detail(source)['translation']['result']
    assert [segment['text'] for segment in result['segments']] == [original, 'Public records remain available.']
    assert store.detail(source)['needsTranslation'] is False


def test_old_mixed_label_and_cache_are_recomputed_without_model_calls(tmp_path):
    store = Store(tmp_path / 'old-label')
    source = publish(store, 'Original records remain available for every later review. ' * 20)
    revision = store.detail(source)['revisionId']
    with store.connect() as db:
        db.execute('INSERT INTO document_languages(revision_id,code,identification,policy_version) VALUES(?,?,?,?)',
                   (revision, 'mixed', 'model', 0))
        db.execute('UPDATE document_language_analysis SET version=?,detected_code=? WHERE revision_id=?',
                   (LANGUAGE_POLICY_VERSION - 1, 'mixed', revision))
    for document in [store.detail(source), store.documents()['documents'][0]]:
        assert document['languageCode'] != 'mixed'
        assert document['needsTranslation'] is True


def test_current_same_script_model_multilingual_result_keeps_valid_label(tmp_path):
    store = Store(tmp_path / 'same-script')
    source = publish(store, 'Original records remain available. Les documents originaux sont disponibles.')
    with store.connect() as db:
        store.set_document_language(db, source, store.detail(source)['revisionId'], 'mixed')
    assert store.detail(source)['languageCode'] == 'mixed'


def test_semantic_main_language_invalidates_same_code_cached_parser_result(tmp_path):
    store = Store(tmp_path / 'semantic-cache')
    # One Latin script can contain two individually sub-30% languages; script
    # coverage alone cannot determine their semantic combined distribution.
    source = publish(store, ['資料完整保留原始證據。' * 42,
                             'We see it all. ' * 29, 'On garde ici. ' * 29])
    revision = store.detail(source)['revisionId']
    assert store.detail(source)['needsTranslation'] is True
    with store.connect() as db:
        store.set_document_language(db, source, revision, 'zh')
    document = store.detail(source)
    assert document['needsTranslation'] is False
    assert not document_needs_translation(document)
    assert document['languageCode'] == 'zh'
