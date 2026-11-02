"""Source language is identified within existing classification model calls."""
import json

import pytest

from server.enrichment import EnrichmentService
from server.language import (LANGUAGE_LABELS, SOURCE_LANGUAGE_CODES, document_needs_translation,
                             language_label, language_tag, language_weight, merge_source_languages,
                             normalize_language_code, source_language_from_shares)
from server.store import Store
from server.tests.test_enrichment import Gateway, execute, publish
from server.tests.test_idle_processing import idle_environment, publish_old_document


@pytest.fixture
def workbench(tmp_path):
    store = Store(tmp_path / 'language-classification')
    models = Gateway()
    return store, models, EnrichmentService(store, models)


@pytest.mark.parametrize('code,label', [('en', '英语'), ('ja', '日语'), ('fr', '法语'), ('zh', '中文'), ('mixed', '多语混合')])
def test_language_labels_are_program_owned(code, label):
    assert language_label(code) == label
    assert language_tag(code) == '语言：' + label
    assert code in SOURCE_LANGUAGE_CODES
    assert normalize_language_code('zh-CN') == 'zh'
    assert normalize_language_code('foreign') == 'other'
    assert language_label('自行生成恶意标签') == LANGUAGE_LABELS['und']


@pytest.mark.parametrize('codes,expected', [
    (['en', 'en'], 'en'), (['en', 'fr'], 'und'), (['zh', 'ja'], 'und'),
    (['en', 'und'], 'und'), (['en', 'other'], 'und'), (['mixed', 'und'], 'und'),
])
def test_batch_language_agreement_is_conservative(codes, expected):
    assert merge_source_languages(codes) == expected


@pytest.mark.parametrize('weights,expected', [([70, 30], 'mixed'), ([71, 29], 'en'), ([99, 1], 'en')])
def test_batch_language_merge_uses_effective_body_weight(weights, expected):
    assert merge_source_languages(['en', 'fr'], weights=weights) == expected


def test_classification_identifies_source_language_without_extra_model_call(workbench):
    store, models, service = workbench
    source = publish(store, 'La gouvernance des archives conserve les documents originaux et leurs versions pour examen.')
    models.rewrite = lambda value: {**value, 'sourceLanguage': 'fr', 'languageLabel': '自行编造', 'languageTag': '恶意标签'}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    assert len(models.calls) == 1
    request = json.loads(models.calls[0][1][-1]['content'])
    assert request['task'] == 'classification'
    assert request['languagePolicyVersion']
    assert set(request['allowedSourceLanguages']) == set(SOURCE_LANGUAGE_CODES)
    result = service.detail(source)['classification']['result']
    assert result['sourceLanguage'] == 'fr'
    assert result['languageIdentification'] == 'model'
    assert 'languageLabel' not in result and 'languageTag' not in result
    document = store.detail(source)
    assert document['languageCode'] == 'fr' and document['languageLabel'] == '法语'
    assert document['languageTag'] == '语言：法语'
    assert document['language'] == 'en'  # Original parser metadata is immutable.
    assert '语言：法语' not in document['tags']


@pytest.mark.parametrize('shares,expected', [
    ({'zh': .70, 'en': .30}, 'mixed'),
    ({'zh': .71, 'en': .29}, 'zh'),
    ({'zh': .5, 'en': .25, 'fr': .25}, 'zh'),
    ({'zh': .42, 'en': .29, 'fr': .29}, 'zh'),
    ({'zh-CN': .5, 'zh-TW': .5}, 'zh'),
])
def test_model_language_shares_enforce_threshold_and_unify_chinese(workbench, shares, expected):
    store, models, service = workbench
    # Each natural-language sentence has ten effective alphabetic characters,
    # so repeat counts produce the exact advertised 70/30,71/29,... coverage.
    prose = {'zh': '資料完整保留原始證據。', 'en': 'We see it all. ', 'fr': 'On garde ici. ',
             'zh-CN': '资料完整保留原始证据。', 'zh-TW': '資料完整保留原始證據。'}
    assert all(language_weight(prose[code]) == 10 for code in shares)
    source = publish(store, [prose[code] * round(fraction * 100) for code, fraction in shares.items()])
    models.rewrite = lambda value: {**value, 'sourceLanguage': 'mixed', 'sourceLanguageShares': shares}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['classification']['result']
    assert result['sourceLanguage'] == expected
    assert store.detail(source)['languageCode'] == expected
    assert len(models.calls) == 1


def test_model_shares_exclude_long_foreign_entity_from_chinese_language_weight(workbench):
    store, models, service = workbench
    source = publish(store, '中文資料記錄機構名稱：University of Northumbria at Newcastle International Research and Development Council。')
    models.rewrite = lambda value: {**value, 'sourceLanguage': 'zh', 'sourceLanguageShares': {'zh': 1}}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    assert service.detail(source)['classification']['result']['sourceLanguage'] == 'zh'
    assert store.detail(source)['languageCode'] == 'zh'
    assert not document_needs_translation(store.detail(source))
    assert len(models.calls) == 1


def test_chinese_shares_cannot_relabel_wholly_foreign_body(workbench):
    store, models, service = workbench
    source = publish(store)
    models.rewrite = lambda value: {**value, 'sourceLanguage': 'zh', 'sourceLanguageShares': {'zh': 1}}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'failed'
    assert service.detail(source)['classification']['result'] is None


def test_bare_mixed_claim_cannot_make_chinese_technical_terms_foreign(workbench):
    store, models, service = workbench
    source = publish(store, '資料治理保留原始記錄與版本。OpenAI、GPT、API、SQL、Docker 與 Kubernetes 是本文使用的技術名稱。')
    models.rewrite = lambda value: {**value, 'sourceLanguage': 'mixed'}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    assert store.detail(source)['languageCode'] == 'zh'
    assert len(models.calls) == 1
    assert not any(job['kind'] == 'ai_translation' for job in store.jobs()['jobs'])


@pytest.mark.parametrize('shares', [
    {'en': True}, {'en': -1, 'zh': 2}, {'en': .5},
    {'mixed': 1}, {'made-up': 1}, {'en': float('inf')},
])
def test_invalid_model_language_shares_never_publish(workbench, shares):
    store, models, service = workbench
    source = publish(store)
    models.rewrite = lambda value: {**value, 'sourceLanguage': 'en', 'sourceLanguageShares': shares}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'failed'
    assert service.detail(source)['classification']['result'] is None
    assert all(json.loads(messages[-1]['content'])['task'] == 'classification' for _, messages in models.calls)


def test_manual_topic_classification_does_not_block_language_identification(workbench):
    store, models, service = workbench
    source = publish(store)
    models.callback = lambda: store.patch(source, {'tags': ['人工保留主题']})
    models.rewrite = lambda value: {**value, 'sourceLanguage': 'en'}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    document = store.detail(source)
    assert document['tags'] == ['人工保留主题']
    assert document['languageCode'] == 'en' and document['languageTag'] == '语言：英语'
    result = service.detail(source)['classification']['result']
    assert result['manualOverride'] and not result['applied']


def test_legacy_response_uses_parser_fallback_and_does_not_downgrade_known_language(workbench):
    store, models, service = workbench
    source = publish(store, 'La gouvernance des archives conserve les documents originaux et leurs versions pour examen.')
    revision = store.detail(source)['revisionId']
    with store.connect() as db:
        store.set_document_language(db, source, revision, 'fr', identification='model')
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    assert store.detail(source)['languageCode'] == 'fr'
    result = service.detail(source)['classification']['result']
    assert result['sourceLanguage'] == 'fr' and result['languageIdentification'] == 'parser'
    assert len(models.calls) == 1


@pytest.mark.parametrize('invalid', ['英语', 'made-up', 'zh', {'code': 'en'}])
def test_language_validation_rejects_arbitrary_labels_and_output_language(workbench, invalid):
    store, models, service = workbench
    source = publish(store)
    models.rewrite = lambda value: {**value, 'sourceLanguage': invalid}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'failed'
    assert service.detail(source)['classification']['result'] is None
    assert len(models.calls) == 2  # Same classification repair, no language-only request.
    assert {json.loads(messages[-1]['content'])['task'] for _, messages in models.calls} == {'classification'}


def test_multiple_batches_merge_source_languages_and_ignore_reducer_output_language(workbench):
    store, models, service = workbench
    models.config['contextChars'] = 16000
    source = publish(store, ['Original English records remain available for every review. ' * 220,
                             'Les documents originaux restent disponibles pour examen. ' * 220])
    block_languages = {block['id']: code for block, code in zip(store.detail(source)['blocks'], ('en', 'fr'))}
    def identify(value):
        request = json.loads(models.calls[-1][1][-1]['content'])
        if request['task'] == 'classification_reduce':
            # Reducers see Chinese reasons, which must not become source language.
            return {**value, 'sourceLanguage': 'zh'}
        counts = {'en': 0, 'fr': 0}
        for piece in request['blocks']:
            counts[block_languages[piece['blockId']]] += language_weight(piece['text'])
        total = sum(counts.values())
        shares = {code: count / total for code, count in counts.items() if count}
        return {**value, 'sourceLanguage': source_language_from_shares(shares), 'sourceLanguageShares': shares}
    models.rewrite = identify
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    payloads = [json.loads(messages[-1]['content']) for _, messages in models.calls]
    assert sum(payload['task'] == 'classification' for payload in payloads) >= 2
    assert any(payload['task'] == 'classification_reduce' for payload in payloads)
    assert all(payload['task'] in ('classification', 'classification_reduce') for payload in payloads)
    result = service.detail(source)['classification']['result']
    assert result['sourceLanguage'] == 'mixed'
    assert store.detail(source)['languageCode'] == 'mixed'


def test_short_foreign_batch_does_not_turn_long_dominant_document_into_mixed(workbench):
    store, models, service = workbench
    models.config['contextChars'] = 16000
    source = publish(store, ['Original English records remain available for every review. ' * 350,
                             'Les documents originaux restent disponibles pour examen. ' * 10])
    block_languages = {block['id']: code for block, code in zip(store.detail(source)['blocks'], ('en', 'fr'))}

    def identify(value):
        request = json.loads(models.calls[-1][1][-1]['content'])
        if request['task'] == 'classification_reduce':
            return {**value, 'sourceLanguage': 'mixed', 'sourceLanguageShares': {'en': .5, 'fr': .5}}
        counts = {'en': 0, 'fr': 0}
        for piece in request['blocks']:
            counts[block_languages[piece['blockId']]] += language_weight(piece['text'])
        total = sum(counts.values())
        shares = {code: count / total for code, count in counts.items() if count}
        return {**value, 'sourceLanguage': source_language_from_shares(shares), 'sourceLanguageShares': shares}

    models.rewrite = identify
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['classification']['result']
    assert result['sourceLanguage'] == 'en'
    assert result['sourceLanguageShares']['fr'] < .3
    assert any(json.loads(messages[-1]['content'])['task'] == 'classification_reduce' for _, messages in models.calls)


def test_japanese_identification_makes_shared_han_text_translatable(workbench):
    store, models, service = workbench
    source = publish(store, '人工知能政策研究及関連制度整備状況分析報告。地域産業構造改革及公共制度調査結果。')
    with store.connect() as db:
        db.execute("UPDATE revisions SET language='zh' WHERE id=?", (store.detail(source)['revisionId'],))
    assert not document_needs_translation(store.detail(source))
    models.rewrite = lambda value: {**value, 'sourceLanguage': 'ja'}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    assert document_needs_translation(store.detail(source))
    assert store.detail(source)['languageCode'] == 'ja'
    queued = [job for job in store.jobs()['jobs'] if job['kind'] == 'ai_translation']
    assert len(queued) == 1 and queued[0]['status'] == 'queued'
    assert len(models.calls) == 1
    with store.connect() as db:
        payload = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', (queued[0]['id'],)).fetchone()[0])
        assert payload['automatic'] is True


def test_new_language_never_retries_existing_failed_translation(workbench):
    store, models, service = workbench
    source = publish(store, '人工知能政策研究及関連制度整備状況分析報告。地域産業構造改革及公共制度調査結果。')
    revision = store.detail(source)['revisionId']
    queued = service.queue(source, ['translation'])[0]
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='failed',error='simulated failure' WHERE id=?", (queued['id'],))
    models.rewrite = lambda value: {**value, 'sourceLanguage': 'ja'}
    service.queue(source, ['classification'])
    assert execute(store, service)['status'] == 'succeeded'
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM ai_runs WHERE revision_id=? AND kind='translation'", (revision,)).fetchone()[0] == 1
    assert store.job(queued['id'])['status'] == 'failed'


def test_post_classification_schedule_failure_does_not_fail_published_classification(workbench, monkeypatch):
    store, models, service = workbench
    source = publish(store, '人工知能政策研究及関連制度整備状況分析報告。地域産業構造改革及公共制度調査結果。')
    models.rewrite = lambda value: {**value, 'sourceLanguage': 'ja'}
    queued = service.queue(source, ['classification'])[0]
    def failed_queue(*args, **kwargs):
        raise ValueError('simulated unavailable model configuration')
    monkeypatch.setattr(service, 'queue', failed_queue)
    assert execute(store, service)['status'] == 'succeeded'
    assert store.job(queued['id'])['status'] == 'succeeded'
    assert service.detail(source)['classification']['result']['sourceLanguage'] == 'ja'


def test_idle_language_detection_waits_for_next_idle_tick(idle_environment):
    store, models, gateway, service, _, _ = idle_environment
    source = publish_old_document(store, models,
        '人工知能政策研究及関連制度整備状況分析報告。地域産業構造改革及公共制度調査結果。')
    gateway.rewrite = lambda value: {**value, 'sourceLanguage': 'ja'}
    queued = service.queue(source, ['classification'], origin='idle')
    assert len(queued) == 1
    assert execute(store, service)['status'] == 'succeeded'
    assert not any(job['kind'] == 'ai_translation' for job in store.jobs()['jobs'])
    following = service.maybe_queue_idle()
    assert len(following) == 1
    assert following[0]['kind'] == 'ai_translation' and following[0]['origin'] == 'idle'
    assert len(gateway.calls) == 1


@pytest.mark.parametrize('main_text', [
    '原始资料保留完整证据和版本，整理过程使用中文记录并支持人工核对。',
    '原始資料保留完整證據與版本，整理過程使用中文記錄並支援人工核對。',
])
def test_idle_does_not_translate_chinese_main_with_minor_foreign_block(idle_environment, main_text):
    store, models, gateway, service, _, _ = idle_environment
    source = publish_old_document(store, models, [main_text * 25,
        'The original records remain available for review.'],
        title='Original records remain available for review')
    document = store.detail(source)
    assert document['languageCode'] == 'zh'
    assert not document_needs_translation(document)

    queued = service.maybe_queue_idle()
    assert len(queued) == 1 and queued[0]['source_id'] == source
    # Translation has higher scheduler priority; summary proves the language
    # gate skipped it while keeping the source eligible for other idle work.
    assert queued[0]['kind'] == 'ai_summary'
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM ai_runs WHERE revision_id=? AND kind='translation'",
                              (document['revisionId'],)).fetchone()
    assert gateway.calls == []


def test_checkpoint_retry_keeps_fallback_language_provenance(workbench):
    store, models, service = workbench
    models.config['contextChars'] = 16000
    source = publish(store, 'Original evidence and source document versions remain available for review. ' * 350)
    queued = service.queue(source, ['classification'])[0]
    models.fail_at = 2
    assert execute(store, service)['status'] == 'failed'
    first_request = models.calls[0][1]
    with store.connect() as db:
        stored = json.loads(db.execute('SELECT result_json FROM ai_checkpoints LIMIT 1').fetchone()[0])
        assert stored['languageIdentification'] == 'parser'
    store.retry(queued['id'])
    models.fail_at = None
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['classification']['result']
    assert result['languageIdentification'] == 'parser'
    assert sum(messages == first_request for _, messages in models.calls) == 1
