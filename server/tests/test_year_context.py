"""Year completion uses immutable, local source context without guessing dates."""

import copy
import json

import pytest

from server.store import Store
from server.year_context import (
    build_contexts,
    check_years,
    explicit_years,
    load_contexts,
    prompt_contexts,
    reference_key,
)


def document(original_title, rows, *, source='source-a', revision='revision-a', **metadata):
    blocks = []
    for index, row in enumerate(rows):
        text, kind, xpath = row if isinstance(row, tuple) else (
            row, 'paragraph', f'/html/body/article[1]/p[{index + 1}]')
        blocks.append({'id': f'{revision}_b{index + 1}', 'ordinal': index,
                       'kind': kind, 'text': text, 'locator': {'xpath': xpath}})
    return {'id': source, 'revisionId': revision, 'originalTitle': original_title,
            'title': original_title, 'blocks': blocks, **metadata}


def reference(doc, index, quote=None, **overrides):
    block = doc['blocks'][index]
    quote = block['text'] if quote is None else quote
    start = block['text'].index(quote)
    return {'sourceId': doc['id'], 'revisionId': doc['revisionId'],
            'blockId': block['id'], 'start': start, 'end': start + len(quote),
            'quote': quote, **overrides}


def reject_year(text, refs, docs):
    with pytest.raises(ValueError):
        contexts = build_contexts(docs, refs)
        check_years(text, refs, contexts)


def test_legacy_guard_uses_only_the_cited_fragment():
    doc = document('2026年香港网络安全统计', ['受理事件共四十宗。'])
    ref = reference(doc, 0, title=doc['originalTitle'], yearContext='2026年统计')
    with pytest.raises(ValueError, match='2026'):
        check_years('2026年受理事件共四十宗。', [ref])
    check_years('受理事件共四十宗。', [ref])


def test_original_title_can_supply_a_year_absent_from_the_quote():
    doc = document('2026年香港网络安全事件统计', ['受理事件共四十宗。'])
    ref = reference(doc, 0)
    contexts = build_contexts([doc], [ref])
    assert '2026' in contexts[reference_key(ref)]['years']
    check_years('2026年受理事件共四十宗。', [ref], contexts)


def test_same_paragraph_explicit_year_applies_to_its_continuation():
    doc = document('网络安全事件统计', ['2026年受理事件共一百宗。其中勒索软件事件四十宗。'])
    ref = reference(doc, 0, '其中勒索软件事件四十宗。')
    check_years('2026年勒索软件事件四十宗。', [ref], build_contexts([doc], [ref]))


def test_immediately_preceding_context_applies_to_a_clear_continuation():
    doc = document('网络安全统计', [
        '2026年受理网络安全事件共一百宗。',
        '其中勒索软件事件四十宗，仍需保持警惕。',
    ])
    ref = reference(doc, 1)
    check_years('2026年勒索软件事件四十宗。', [ref], build_contexts([doc], [ref]))


def test_english_independent_sentence_does_not_transfer_its_event_year():
    doc = document('Cybersecurity statistics', [
        'The centre was founded in 2026. Police recorded forty cybersecurity incidents.',
    ])
    ref = reference(doc, 0, 'Police recorded forty cybersecurity incidents.')
    reject_year('Police recorded forty cybersecurity incidents in 2026.', [ref], [doc])


def test_english_sentence_with_explicit_continuation_can_use_previous_year():
    doc = document('Cybersecurity statistics', [
        'In 2026, one hundred cybersecurity incidents were reported. These cases included forty ransomware incidents.',
    ])
    ref = reference(doc, 0, 'These cases included forty ransomware incidents.')
    check_years('In 2026, forty ransomware incidents were reported.', [ref], build_contexts([doc], [ref]))


def test_following_sentence_can_explicitly_date_the_preceding_data():
    doc = document('网络安全统计', ['受理事件共四十宗。上述数据对应2026年。'])
    ref = reference(doc, 0, '受理事件共四十宗。')
    check_years('2026年受理事件共四十宗。', [ref], build_contexts([doc], [ref]))


@pytest.mark.parametrize('year', ['2025', '2026'])
def test_conflicting_explicit_context_does_not_choose_a_convenient_year(year):
    doc = document('网络安全统计', [
        '2025年受理事件共一百宗。其中勒索软件事件四十宗。上述数据对应2026年。',
    ])
    ref = reference(doc, 0, '其中勒索软件事件四十宗。')
    reject_year(f'{year}年勒索软件事件四十宗。', [ref], [doc])


def test_parent_heading_can_supply_year_to_a_child_section():
    doc = document('网络安全统计', [
        ('2026年事件统计', 'heading', '/html/body/article[1]/h2[1]'),
        ('勒索软件事件', 'heading', '/html/body/article[1]/h3[1]'),
        ('受理事件共四十宗。', 'paragraph', '/html/body/article[1]/p[1]'),
    ])
    ref = reference(doc, 2)
    check_years('2026年受理事件共四十宗。', [ref], build_contexts([doc], [ref]))


def test_more_specific_child_heading_overrides_parent_year():
    doc = document('网络安全统计', [
        ('2026年形势报告', 'heading', '/html/body/article[1]/h2[1]'),
        ('2025年历史事件', 'heading', '/html/body/article[1]/h3[1]'),
        ('受理事件共四十宗。', 'paragraph', '/html/body/article[1]/p[1]'),
    ])
    ref = reference(doc, 2)
    contexts = build_contexts([doc], [ref])
    check_years('2025年受理事件共四十宗。', [ref], contexts)
    with pytest.raises(ValueError, match='2026'):
        check_years('2026年受理事件共四十宗。', [ref], contexts)


def test_sibling_heading_does_not_inherit_the_previous_siblings_year():
    doc = document('网络安全统计', [
        ('2026年国际合作情况', 'heading', '/html/body/article[1]/h2[1]'),
        ('完成合作项目三项。', 'paragraph', '/html/body/article[1]/p[1]'),
        ('事件统计', 'heading', '/html/body/article[1]/h2[2]'),
        ('受理事件共四十宗。', 'paragraph', '/html/body/article[1]/p[2]'),
    ])
    ref = reference(doc, 3)
    reject_year('2026年受理事件共四十宗。', [ref], [doc])


def test_local_sentence_year_overrides_the_document_title():
    doc = document('2026年网络安全形势报告', ['2025年发生网络安全事件四十宗。'])
    ref = reference(doc, 0, '发生网络安全事件四十宗。')
    contexts = build_contexts([doc], [ref])
    check_years('2025年发生网络安全事件四十宗。', [ref], contexts)
    with pytest.raises(ValueError, match='2026'):
        check_years('2026年发生网络安全事件四十宗。', [ref], contexts)


def test_compilation_articles_do_not_share_dates():
    doc = document('网络安全资料汇编', [
        ('2026年事件统计', 'heading', '/html/body/article[1]/h2[1]'),
        ('受理事件共四十宗。', 'paragraph', '/html/body/article[1]/p[1]'),
        ('国际合作情况', 'heading', '/html/body/article[2]/h2[1]'),
        ('完成合作项目三项。', 'paragraph', '/html/body/article[2]/p[1]'),
    ])
    ref = reference(doc, 3)
    reject_year('2026年完成合作项目三项。', [ref], [doc])


def test_compilation_articles_are_independent_support_groups():
    doc = document('网络安全资料汇编', [
        ('2026年事件统计', 'heading', '/html/body/article[1]/h2[1]'),
        ('受理事件共四十宗。', 'paragraph', '/html/body/article[1]/p[1]'),
        ('国际合作情况', 'heading', '/html/body/article[2]/h2[1]'),
        ('完成合作项目三项。', 'paragraph', '/html/body/article[2]/p[1]'),
    ])
    refs = [reference(doc, 1), reference(doc, 3)]
    reject_year('2026年受理事件四十宗并完成合作项目三项。', refs, [doc])


def test_new_section_prevents_borrowing_an_adjacent_historical_date():
    doc = document('网络安全资料', [
        ('历史案例', 'heading', '/html/body/article[1]/h2[1]'),
        ('2025年发生过一次历史事件。', 'paragraph', '/html/body/article[1]/p[1]'),
        ('当前工作', 'heading', '/html/body/article[1]/h2[2]'),
        ('完成合作项目三项。', 'paragraph', '/html/body/article[1]/p[2]'),
    ])
    ref = reference(doc, 3)
    reject_year('2025年完成合作项目三项。', [ref], [doc])


def test_distant_paragraph_year_is_not_document_wide_support():
    doc = document('网络安全资料', [
        '2025年曾发生历史事件。',
        *['本段介绍背景信息和一般措施。' for _ in range(9)],
        '受理事件共四十宗。',
    ])
    ref = reference(doc, 10)
    reject_year('2025年受理事件共四十宗。', [ref], [doc])


@pytest.mark.parametrize('date_kind', ['发布日期', '抓取时间', '导入日期'])
def test_administrative_dates_do_not_date_the_reported_event(date_kind):
    doc = document('网络安全统计', [f'{date_kind}：2026年10月1日。', '受理事件共四十宗。'],
                   publishedAt='2026-10-01', fetchedAt='2026-10-02', createdAt='2026-10-03')
    ref = reference(doc, 1)
    reject_year('2026年受理事件共四十宗。', [ref], [doc])


def test_last_year_is_not_resolved_by_arithmetic_or_the_current_clock():
    doc = document('2026年网络安全情况', ['去年受理事件共四十宗。'])
    ref = reference(doc, 0)
    reject_year('2025年受理事件共四十宗。', [ref], [doc])


def test_reporting_year_does_not_date_a_last_year_event_in_the_same_sentence():
    doc = document('网络安全统计', ['2026年发布报告，去年受理事件共四十宗。'])
    ref = reference(doc, 0, '受理事件共四十宗。')
    reject_year('2026年受理事件共四十宗。', [ref], [doc])


def test_continuation_of_last_year_data_does_not_fall_back_to_reporting_title():
    doc = document('2026年网络安全报告', ['去年受理事件共一百宗。其中勒索软件事件四十宗。'])
    ref = reference(doc, 0, '其中勒索软件事件四十宗。')
    reject_year('2026年勒索软件事件四十宗。', [ref], [doc])


def test_ai_display_title_and_reference_metadata_cannot_authorize_a_year():
    doc = document('Cybersecurity statistics', ['受理事件共四十宗。'],
                   title='2026年网络安全统计', translatedTitle='2026年网络安全统计')
    ref = reference(doc, 0, title='2026年网络安全统计',
                    yearContext={'years': ['2026'], 'text': '2026年'}, supportedYears=['2026'])
    reject_year('2026年受理事件共四十宗。', [ref], [doc])


def test_new_policy_cve_identifier_in_quote_does_not_authorize_an_event_year():
    doc = document('漏洞修复情况', ['已完成CVE-2026-12345漏洞修复。'])
    ref = reference(doc, 0)
    check_years('2026年完成漏洞修复。', [ref])
    reject_year('2026年完成漏洞修复。', [ref], [doc])


def test_unchanged_cve_identifier_in_generated_text_remains_usable():
    doc = document('漏洞修复情况', ['已完成CVE-2026-12345漏洞修复。'])
    ref = reference(doc, 0)
    check_years('已完成CVE-2026-12345漏洞修复。', [ref], build_contexts([doc], [ref]))


def test_cve_identifier_ends_at_an_english_sentence_boundary():
    doc = document('Patch status', ['CVE-2026-12345. The patch fixed the issue.'])
    ref = reference(doc, 0)
    check_years('The patch fixed CVE-2026-12345.', [ref], build_contexts([doc], [ref]))


def test_cve_identifier_cannot_be_changed_to_an_unquoted_year_or_number():
    doc = document('漏洞修复情况', ['已完成CVE-2026-12345漏洞修复。'])
    ref = reference(doc, 0)
    reject_year('已完成CVE-2027-99999漏洞修复。', [ref], [doc])


def test_explicit_event_year_does_not_authorize_a_different_cve_identifier():
    doc = document('漏洞修复情况', ['2027年完成CVE-2026-12345漏洞修复。'])
    ref = reference(doc, 0)
    reject_year('2027年完成CVE-2027-99999漏洞修复。', [ref], [doc])


@pytest.mark.parametrize('context_location', ['title', 'previous_sentence'])
def test_cve_identifier_in_context_does_not_authorize_an_event_year(context_location):
    if context_location == 'title':
        doc = document('CVE-2026-12345漏洞情况', ['已完成该漏洞修复。'])
        ref = reference(doc, 0)
    else:
        doc = document('漏洞情况', ['CVE-2026-12345漏洞已公开。该漏洞已完成修复。'])
        ref = reference(doc, 0, '该漏洞已完成修复。')
    reject_year('2026年完成漏洞修复。', [ref], [doc])


def test_bare_date_block_does_not_date_the_following_reported_event():
    doc = document('网络安全统计', ['2026-10-01', '该报告记载受理事件共四十宗。'])
    ref = reference(doc, 1)
    reject_year('2026年受理事件共四十宗。', [ref], [doc])


def test_explicit_date_in_an_event_sentence_can_support_its_event_year():
    doc = document('网络安全统计', ['2026-10-01，受理事件共四十宗。'])
    ref = reference(doc, 0, '受理事件共四十宗。')
    check_years('2026年受理事件共四十宗。', [ref], build_contexts([doc], [ref]))


def test_context_completion_requires_support_from_each_cited_source():
    first = document('2026年事件统计', ['受理事件共四十宗。'])
    second = document('国际合作情况', ['完成合作项目三项。'], source='source-b', revision='revision-b')
    refs = [reference(first, 0), reference(second, 0)]
    reject_year('2026年受理事件四十宗并完成合作项目三项。', refs, [first, second])

    second['originalTitle'] = '2026年国际合作情况'
    contexts = build_contexts([first, second], refs)
    check_years('2026年受理事件四十宗并完成合作项目三项。', refs, contexts)


def test_direct_quote_year_support_preserves_legacy_only_for_old_policy():
    first = document('事件统计', ['2026年受理事件共四十宗。'])
    second = document('国际合作情况', ['完成合作项目三项。'], source='source-b', revision='revision-b')
    refs = [reference(first, 0), reference(second, 0)]
    check_years('2026年受理事件共四十宗。', refs)
    reject_year('2026年受理事件共四十宗。', refs, [first, second])


@pytest.mark.parametrize(('other_quote', 'supported'), [
    ('2025年完成合作项目三项。', False),
    ('完成合作项目三项。', False),
    ('2026年完成合作项目三项。', True),
])
def test_new_policy_direct_year_requires_each_cited_sources_support(other_quote, supported):
    first = document('事件统计', ['2026年受理事件共四十宗。'])
    second = document('国际合作情况', [other_quote], source='source-b', revision='revision-b')
    refs = [reference(first, 0), reference(second, 0)]
    text = '2026年受理事件四十宗并完成合作项目三项。'
    check_years(text, refs)
    if supported:
        check_years(text, refs, build_contexts([first, second], refs))
    else:
        reject_year(text, refs, [first, second])


@pytest.mark.parametrize(('other_quote', 'supported'), [
    ('2025年完成合作项目三项。', False),
    ('完成合作项目三项。', False),
    ('2026年完成合作项目三项。', True),
])
def test_direct_quote_years_do_not_leak_between_compilation_articles(other_quote, supported):
    doc = document('网络安全资料汇编', [
        ('2026年受理事件共四十宗。', 'paragraph', '/html/body/article[1]/p[1]'),
        (other_quote, 'paragraph', '/html/body/article[2]/p[1]'),
    ])
    refs = [reference(doc, 0), reference(doc, 1)]
    text = '2026年受理事件四十宗并完成合作项目三项。'
    check_years(text, refs)
    if supported:
        check_years(text, refs, build_contexts([doc], refs))
    else:
        reject_year(text, refs, [doc])


def test_different_source_years_require_separate_cited_claims():
    first = document('事件统计', ['2026年受理事件共四十宗。'])
    second = document('国际合作情况', ['2025年完成合作项目三项。'], source='source-b', revision='revision-b')
    refs = [reference(first, 0), reference(second, 0)]
    contexts = build_contexts([first, second], refs)
    with pytest.raises(ValueError):
        check_years('2026年受理事件四十宗，2025年完成合作项目三项。', refs, contexts)
    check_years('2026年受理事件共四十宗。', refs[:1], contexts)
    check_years('2025年完成合作项目三项。', refs[1:], contexts)


def test_empty_context_map_does_not_restore_cross_source_legacy_union():
    first = document('事件统计', ['2026年受理事件共四十宗。'])
    second = document('国际合作情况', ['完成合作项目三项。'], source='source-b', revision='revision-b')
    refs = [reference(first, 0), reference(second, 0)]
    check_years('2026年受理事件共四十宗。', refs)
    with pytest.raises(ValueError):
        check_years('2026年受理事件共四十宗。', refs, {})


@pytest.mark.parametrize('overrides', [
    {'sourceId': 'different-source'},
    {'revisionId': 'different-revision'},
    {'blockId': 'different-block'},
    {'start': 1},
    {'end': 1},
    {'quote': '原文中不存在的引文。'},
])
def test_invalid_reference_identity_or_offsets_cannot_use_context(overrides):
    doc = document('2026年事件统计', ['受理事件共四十宗。'])
    ref = {**reference(doc, 0), **overrides}
    with pytest.raises(ValueError):
        build_contexts([doc], [ref])


def test_reference_key_binds_identity_and_location_but_not_model_metadata():
    doc = document('事件统计', ['受理事件共四十宗。'])
    ref = reference(doc, 0)
    assert reference_key(ref) == reference_key({**ref, 'title': '2026年', 'yearContext': ['2026']})
    assert reference_key(ref) != reference_key({**ref, 'revisionId': 'another-revision'})
    assert reference_key(ref) != reference_key({**ref, 'start': 1})


def test_context_from_another_frozen_revision_cannot_be_reused():
    doc = document('2026年事件统计', ['受理事件共四十宗。'])
    ref = reference(doc, 0)
    contexts = build_contexts([doc], [ref])
    changed = {**ref, 'revisionId': 'revision-b'}
    with pytest.raises(ValueError):
        check_years('2026年受理事件共四十宗。', [changed], contexts)


def test_ordinary_quantities_currency_and_model_numbers_are_not_years():
    assert explicit_years('金额2026元、人数2025人、价格USD 2024、比例2023%、型号AB2022。') == set()
    assert explicit_years('2026年金额2025元，日期2024-10-01，year 2023。') == {'2026', '2024', '2023'}
    check_years('金额2026元，参与人数2025人，型号AB2022。', [{'quote': '受理事件。'}])


def test_prompt_contexts_respects_budget_and_does_not_mutate_trusted_contexts():
    docs = [document(f'2026年资料{i}', ['受理事件共四十宗。'],
                     source=f'source-{i}', revision=f'revision-{i}') for i in range(12)]
    refs = [reference(doc, 0) for doc in docs]
    contexts = build_contexts(docs, refs)
    original = copy.deepcopy(contexts)
    rendered = prompt_contexts(contexts, max_chars=600)
    assert len(json.dumps(rendered, ensure_ascii=False, separators=(',', ':'))) <= 600
    assert contexts == original


def publish(store, title, text):
    queued = store.queue('file', {}, title)
    job = store.claim()
    assert job['id'] == queued['id']
    parsed = {'title': title, 'body': [text], 'language': 'zh', 'parser_version': 'year-context-test',
              'blocks': [{'kind': 'paragraph', 'text': text, 'ordinal': 0,
                          'locator': {'xpath': '/html/body/article[1]/p[1]'}}]}
    store.publish(job, {'url': 'https://example.org/year-context'},
                  f'<html><title>{title}</title><p>{text}</p></html>'.encode(), parsed,
                  schedule_enrichment=False)
    finished = store.job(queued['id'])
    return store.detail(finished['source_id'], finished['revision_id'])


@pytest.mark.parametrize('with_connection', [False, True])
def test_store_context_load_uses_the_fixed_historical_revision(tmp_path, with_connection):
    store = Store(tmp_path / 'data')
    old = publish(store, '2025年香港网络安全统计',
                  '受理事件共四十宗。相关部门持续开展安全宣传，并保存了事件统计与处置情况的原始记录。')
    ref = reference(old, 0, '受理事件共四十宗。')
    current = publish(store, '2026年香港网络安全统计',
                      '受理事件共五十宗。相关部门持续开展安全宣传，并保存了事件统计与处置情况的原始记录。')
    assert old['id'] == current['id'] and old['revisionId'] != current['revisionId']
    if with_connection:
        with store.connect() as db:
            contexts = load_contexts(store, [ref], db=db)
    else:
        contexts = load_contexts(store, [ref])
    check_years('2025年受理事件共四十宗。', [ref], contexts)
    with pytest.raises(ValueError, match='2026'):
        check_years('2026年受理事件共四十宗。', [ref], contexts)
