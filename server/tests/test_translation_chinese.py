"""Chinese-first translation with isolated SQLite and deterministic models."""
import json

import pytest

from server.enrichment import EnrichmentService
from server.language import detect_language, document_needs_translation, needs_translation
from server.parser import parse_html
from server.store import Store
from server.store import dumps
from server.tests.test_enrichment import Gateway, execute, publish


@pytest.fixture
def workbench(tmp_path):
    store = Store(tmp_path / 'chinese-first')
    models = Gateway()
    return store, models, EnrichmentService(store, models)


@pytest.mark.parametrize('text,hint,expected', [
    ('这是完全使用中文写成的资料。', '', 'zh'),
    ('人工智能 AI 的发展和 GPU 芯片。', '', 'zh'),
    ('这篇中文资料分析OpenAI、Google和Nvidia在人工智能产业中的作用，正文继续用中文说明来源、证据与结论。', '', 'zh'),
    ('日本の人工知能政策について説明します。', '', 'ja'),
    ('人工知能政策研究', 'ja-JP', 'ja'),
    ('人工智能政策研究', 'en', 'zh'),
    ('한국의 인공지능 정책을 설명합니다.', '', 'ko'),
    ('Политика искусственного интеллекта.', '', 'foreign'),
    ('Политика искусственного интеллекта.', 'ru', 'ru'),
    ('تطوير الذكاء الاصطناعي', '', 'foreign'),
    ('تطوير الذكاء الاصطناعي', 'ar', 'ar'),
    ('Політика штучного інтелекту та збереження доказів.', 'uk-UA', 'uk'),
    ('سیاست هوش مصنوعی و حفظ مدارک اصلی', 'fa-IR', 'fa'),
    ('مصنوعی ذہانت اور اصل ریکارڈ کا تحفظ', 'ur-PK', 'ur'),
    ('कृत्रिम बुद्धिमत्ता और मूल साक्ष्य का संरक्षण', '', 'foreign'),
    ('कृत्रिम बुद्धिमत्ता और मूल साक्ष्य का संरक्षण', 'hi-IN', 'hi'),
    ('La gouvernance de l’intelligence artificielle.', 'fr', 'fr'),
    ('This is a document about evidence.', 'en-US', 'en'),
    ('1234 $500 20% https://example.org/source', '', 'und'),
    ('中文背景 with a substantial foreign paragraph.', '', 'foreign'),
    ('這是使用繁體中文撰寫的資料，保留來源與原始證據。', '', 'zh'),
    ('這是使用繁體中文撰寫的資料，保留來源與原始證據。', 'ja', 'zh'),
    ('简体中文与繁體中文共同保存原始資料與證據。', 'zh-Hant', 'zh'),
])
def test_script_detection_does_not_treat_japanese_as_chinese(text, hint, expected):
    assert detect_language(text, hint) == expected
    assert needs_translation(text, hint) == (expected not in ('zh', 'und'))


def test_parser_preserves_japanese_hint_before_removing_head():
    raw = b'<html lang="ja-JP"><head><title>Japanese</title></head><body><article><p>' + '人工知能政策研究'.encode() + b'</p></article></body></html>'
    assert parse_html(raw)['language'] == 'ja'
    raw = raw.replace(b' lang="ja-JP"', b'').replace(b'<title>', b'<meta http-equiv="Content-Language" content="ja"><title>')
    assert parse_html(raw)['language'] == 'ja'


def test_parser_uses_main_language_for_short_foreign_block_in_long_chinese_document():
    raw = ('<html><body><article><p>' + '这段中文介绍资料来源和证据保存。' * 30 +
           '</p><p>Public records retain evidence.</p></article></body></html>').encode()
    parsed = parse_html(raw)
    assert parsed['language'] == 'zh'
    raw = raw.replace(b'<p>Public records retain evidence.</p>', b'<pre>print("hello world")</pre>')
    assert parse_html(raw)['language'] == 'zh'


def test_chinese_inline_brand_names_do_not_offer_translation():
    paragraph = '这篇中文资料分析OpenAI、Google和Nvidia在人工智能产业中的作用，正文继续用中文说明来源、证据与结论。'
    document = {'title': '中文产业分析', 'language': 'mixed',
                'blocks': [{'id': 'paragraph', 'kind': 'paragraph', 'text': paragraph}]}
    assert detect_language(paragraph) == 'zh'
    assert not document_needs_translation(document)
    document['blocks'].append({'id': 'foreign', 'kind': 'paragraph',
                               'text': 'These companies develop products with distinct data governance practices.'})
    assert document_needs_translation(document)


def test_auto_translation_is_independent_default_and_respects_disabled_option(workbench):
    store, models, service = workbench
    source = publish(store)
    jobs = service.maybe_queue_auto(source)
    assert [job['kind'] for job in jobs] == ['ai_translation']
    with store.connect() as db:
        payload = db.execute('SELECT input_json FROM jobs WHERE id=?', (jobs[0]['id'],)).fetchone()[0]
        assert json.loads(payload)['automatic'] is True
    assert execute(store, service)['status'] == 'succeeded'
    second = publish(store, url='https://example.org/second')
    models.preferences['autoTranslate'] = False
    assert service.maybe_queue_auto(second) == []
    models.preferences['autoProcess'] = True
    assert {job['kind'] for job in service.maybe_queue_auto(second)} == {'ai_summary', 'ai_classification'}


def test_chinese_documents_do_not_create_translation_work(workbench):
    store, models, service = workbench
    original = '中文原文保留证据和来源。每篇资料完整保存原文，方便后续阅读和核查原始版本。'
    source = publish(store, original)
    assert not document_needs_translation(store.detail(source))
    assert service.maybe_queue_auto(source) == []
    # Explicit conversion remains available and costs no model request.
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    assert models.calls == []
    result = service.detail(source)['translation']['result']
    assert result['segments'][0]['text'] == original


def test_foreign_title_is_translated_and_chinese_paragraph_is_retained(workbench):
    store, models, service = workbench
    source = publish(store, ['中文正文保持原有内容。', 'Public records preserve original evidence.'], title='Knowledge governance')
    title_before = store.detail(source)['title']
    original_blocks = store.detail(source)['blocks']
    models.rewrite = lambda value: {'segments': [
        {**segment, 'text': '知识治理' if segment['id'].endswith(':title:0') else '公共档案保留原始证据。'}
        for segment in value['segments']]}
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['translation']['result']
    assert result['translatedTitle'] == '知识治理'
    assert result['segments'][0]['text'] == original_blocks[0]['text']
    assert result['segments'][1]['text'] == '公共档案保留原始证据。'
    sent = [block for _, messages in models.calls for block in json.loads(messages[1]['content'])['blocks']]
    assert any(block['kind'] == 'title' and block['text'] == title_before for block in sent)
    assert all(block['text'] != original_blocks[0]['text'] for block in sent)
    assert store.detail(source)['blocks'] == original_blocks


def test_summary_and_translation_only_check_structure_not_output_language(workbench):
    store, models, service = workbench
    source = publish(store)
    models.rewrite = lambda value: {**value, 'summary': 'Original evidence is retained.'}
    service.queue(source, ['summary'])
    assert execute(store, service)['status'] == 'succeeded'
    assert service.detail(source)['summary']['result']['summary'] == 'Original evidence is retained.'
    models.rewrite = lambda value: {'segments': [{**segment, 'text': 'Original evidence is retained.'} for segment in value['segments']]}
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    assert service.detail(source)['translation']['result']['segments'][0]['text'] == 'Original evidence is retained.'


def test_switch_off_stops_next_automatic_request_and_keeps_checkpoint(workbench):
    store, models, service = workbench
    source = publish(store, ['Evidence must remain available. ' * 150, 'Records keep the source version. ' * 150])
    jobs = service.maybe_queue_auto(source)
    models.callback = lambda: models.preferences.update(autoTranslate=False)
    job = execute(store, service)
    assert job['status'] == 'cancelled' and job['stage'] == 'auto_disabled'
    assert len(models.calls) == 1
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 1
    assert service.detail(source)['translation']['result'] is None
    # A user request is still allowed with auto translation disabled.
    service.queue(source, ['translation'], force=True)
    assert execute(store, service)['status'] == 'succeeded'


def test_locked_translation_is_not_sent_for_regeneration(workbench):
    store, models, service = workbench
    source = publish(store)
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    block_id = store.detail(source)['blocks'][0]['id']
    service.edit_translation(source, block_id, '人工确认中文译文。')
    count = len(models.calls)
    service.queue(source, ['translation'], force=True)
    assert execute(store, service)['status'] == 'succeeded'
    assert len(models.calls) == count
    segment = service.detail(source)['translation']['result']['segments'][0]
    assert segment['text'] == '人工确认中文译文。' and segment['locked'] and segment['edited']


def test_short_document_translates_all_paragraphs_together_and_preserves_order(workbench):
    store, models, service = workbench
    paragraphs = [
        'The archive introduced a review process for its records.',
        'It keeps their original versions so reviewers can check them later.',
        'This process makes the evidence available without changing the source.',
    ]
    source = publish(store, paragraphs, title='The archive review process')
    document = store.detail(source)
    models.rewrite = lambda result: {'segments': [dict(segment, text='档案审核流程' if ':title:' in segment['id']
        else f'第{index}段中文译文保留原始证据。') for index, segment in enumerate(result['segments'])]}
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    assert len(models.calls) == 1
    payload = json.loads(models.calls[0][1][1]['content'])
    assert [block['text'] for block in payload['blocks']] == [document['originalTitle'], *paragraphs]
    assert [block['kind'] for block in payload['blocks']] == ['title', 'paragraph', 'paragraph', 'paragraph']
    result = service.detail(source)['translation']['result']
    assert result['translatedTitle'] == '档案审核流程'
    assert [segment['blockId'] for segment in result['segments']] == [block['id'] for block in document['blocks']]
    assert len(result['segments']) == 3
    assert store.detail(source)['body'] == paragraphs


def test_long_translation_receives_adjacent_context_and_verified_previous_translations(workbench):
    store, models, service = workbench
    models.config.update(contextChars=7000, maxOutputTokens=1400)
    paragraphs = [
        'The "evidence ledger" retains the original record. ' * 30,
        'It links each version to the same ledger, preserving $190 and the original document. ' * 20,
    ]
    source = publish(store, paragraphs)
    document = store.detail(source)
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    assert len(models.calls) > 1
    sent_by_block = {block['id']: [] for block in document['blocks']}
    previous_payload = None
    for configuration, messages in models.calls:
        assert sum(len(message['content']) for message in messages) <= configuration['contextChars']
        payload = json.loads(messages[-1]['content'])
        pieces, context = payload['blocks'], payload['context']
        estimate = dumps({'segments': [{'id': piece['id'], 'text': piece['text']} for piece in pieces]})
        assert len(estimate) <= int(configuration['maxOutputTokens'] * .55)
        for piece in pieces:
            sent_by_block[piece['blockId']].append(piece['text'])
        assert context['before'] or context['after']
        if previous_payload:
            assert context['previousTranslations']
            previous_source = previous_payload['blocks'][-1]['text']
            assert previous_source.endswith(context['previousTranslations'][-1]['source'])
            assert ('译文：' + previous_source).strip().endswith(context['previousTranslations'][-1]['chinese'])
        else:
            assert context['previousTranslations'] == []
        first = pieces[0]
        if first['start']:
            original = next(block['text'] for block in document['blocks'] if block['id'] == first['blockId'])
            assert original[:first['start']].endswith(context['before'])
        previous_payload = payload
    assert [''.join(sent_by_block[block['id']]) for block in document['blocks']] == paragraphs
    result = service.detail(source)['translation']['result']
    assert len(result['segments']) == len(document['blocks'])
    assert [segment['blockId'] for segment in result['segments']] == [block['id'] for block in document['blocks']]


def test_context_cannot_add_output_segments(workbench):
    store, models, service = workbench
    source = publish(store, 'Evidence is preserved for later review. ' * 100)
    models.rewrite = lambda result: {'segments': [*result['segments'], {'id': 'context-before', 'text': '这不是本批待译段落。'}]}
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'failed'
    assert service.detail(source)['translation']['result'] is None


def test_short_mixed_document_full_context_includes_every_retained_paragraph(workbench):
    store, models, service = workbench
    paragraphs = ['这段中文介绍公共档案如何保留原始记录和证据。' for _ in range(4)]
    paragraphs.insert(2, 'This process keeps all the original records available for later review.')
    source = publish(store, paragraphs)
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    assert len(models.calls) == 1
    payload = json.loads(models.calls[0][1][-1]['content'])
    assert payload['translationMode'] == 'whole_document'
    assert [unit['text'] for unit in payload['context']['fullArticle']] == ['知识治理', *paragraphs]
    assert [unit['text'] for unit in payload['blocks']] == [paragraphs[2]]
    result = service.detail(source)['translation']['result']
    assert len(result['segments']) == len(paragraphs)
    assert result['segments'][0]['text'] == paragraphs[0]


def test_unlocking_during_other_paragraph_translation_keeps_frozen_human_text(workbench):
    store, models, service = workbench
    source = publish(store, ['The first paragraph retains original evidence for future review.',
                             'The second paragraph explains how records preserve their source versions.'])
    service.queue(source, ['translation'])
    assert execute(store, service)['status'] == 'succeeded'
    first_id = store.detail(source)['blocks'][0]['id']
    manual = '人工确认的第一段中文译文，仍应保留。'
    service.edit_translation(source, first_id, manual, locked=True)
    models.callback = lambda: service.edit_translation(source, first_id, manual, locked=False)
    service.queue(source, ['translation'], force=True)
    assert execute(store, service)['status'] == 'succeeded'
    result = service.detail(source)['translation']['result']
    assert result['segments'][0]['text'] == manual
    assert result['segments'][0]['locked'] is False
    assert result['segments'][0]['edited'] is True
    assert store.search('人工确认')['results'][0]['matchBlockId'] == first_id
