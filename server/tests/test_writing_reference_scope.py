"""Chapter aliases are presentation-only and never widen frozen evidence."""
import copy
import json

import pytest

from server.writing_reference_scope import (CitationScopeError, MISSING_BLOCK_ID,
                                           citation_error, citation_scope_payload,
                                           resolve_reference_block, scope_prepared)


def prepared(ids=('original-a', 'original-b')):
    blocks = [{'blockId': value, 'sourceId': 's-%s' % index, 'revisionId': 'r-%s' % index,
               'text': '不可改变的原文%s' % index, 'title': '资料标题',
               'locator': {'page': index + 1}} for index, value in enumerate(ids)]
    payload = {'section': {'id': 'section-7', 'title': '历史时间线'}, 'blocks': copy.deepcopy(blocks),
               'outputSchema': {'paragraphs': [{'text': '正文', 'evidence': [{'blockId': '从 blocks 复制',
                                                                         'quote': '逐字原文'}]}]}}
    return {'section': copy.deepcopy(payload['section']), 'blocks': blocks, 'effectiveMode': 'strict',
            'messages': [{'role': 'system', 'content': '冻结写作要求'},
                         {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]}


def test_scoping_preserves_originals_and_all_other_packet_values_without_mutation():
    item = prepared()
    before = copy.deepcopy(item)
    scoped = scope_prepared(item, 1)
    assert item == before and scoped is not item
    assert scoped['blocks'] == item['blocks'] and scoped['blocks'] is not item['blocks']
    assert scoped['section'] == item['section'] and scoped['effectiveMode'] == 'strict'
    assert scoped['messages'][0] == item['messages'][0]
    assert scoped['citationScope'] == {'protocol': 'chapter-block-alias-v1', 'chapterNumber': 1,
                                       'namespace': 'C01',
                                       'aliases': {'C01-B001': 'original-a', 'C01-B002': 'original-b'}}
    packet = json.loads(scoped['messages'][1]['content'])
    original = json.loads(item['messages'][1]['content'])
    assert [b['blockId'] for b in packet['blocks']] == ['C01-B001', 'C01-B002']
    for actual, expected in zip(packet['blocks'], original['blocks']):
        assert {k: v for k, v in actual.items() if k != 'blockId'} == {k: v for k, v in expected.items() if k != 'blockId'}
    assert packet['section'] == original['section']
    assert packet['outputSchema']['paragraphs'][0]['evidence'][0]['quote'] == '逐字原文'
    assert 'citationScope.allowedBlockIds' in packet['outputSchema']['paragraphs'][0]['evidence'][0]['blockId']


def test_model_scope_never_exposes_original_alias_mapping_and_is_a_fresh_copy():
    item = scope_prepared(prepared(), 3)
    payload = citation_scope_payload(item)
    assert set(payload) == {'protocol', 'namespace', 'allowedBlockIds', 'requirement'}
    assert payload['namespace'] == 'C03' and payload['allowedBlockIds'] == ['C03-B001', 'C03-B002']
    assert '不得使用其他章节编号' in payload['requirement']
    assert 'original-a' not in json.dumps(payload)
    payload['allowedBlockIds'].append('C99-B001')
    assert citation_scope_payload(item)['allowedBlockIds'] == ['C03-B001', 'C03-B002']


def test_confirmed_planning_blocks_follow_the_same_chapter_aliases_without_leaking_permanent_ids():
    item = prepared()
    packet = json.loads(item['messages'][1]['content'])
    packet['plannedContent'] = {'title': '历史时间线', 'goal': '整理已知事件', 'evidenceBlockIds': ['original-b']}
    item['messages'][1]['content'] = json.dumps(packet)
    before = copy.deepcopy(item)
    scoped = scope_prepared(item, 2)
    actual = json.loads(scoped['messages'][1]['content'])
    assert actual['plannedContent']['evidenceBlockIds'] == ['C02-B002']
    assert 'original-b' not in scoped['messages'][1]['content']
    assert scope_prepared(scoped, 9) == scoped
    assert item == before
    packet['plannedContent']['evidenceBlockIds'] = ['outside-this-chapter']
    item['messages'][1]['content'] = json.dumps(packet)
    with pytest.raises(ValueError, match='规划依据'):
        scope_prepared(item, 2)


def test_same_original_in_different_chapters_has_different_non_interchangeable_aliases():
    first, second = scope_prepared(prepared(), 1), scope_prepared(prepared(), 2)
    assert resolve_reference_block(first, 'C01-B001') == resolve_reference_block(second, 'C02-B001') == 'original-a'
    for item, foreign in ((first, 'C02-B001'), (second, 'C01-B001')):
        with pytest.raises(ValueError, match='本章允许'):
            resolve_reference_block(item, foreign)


def test_resume_reuses_frozen_namespace_and_aliases_without_renumbering():
    scoped = scope_prepared(prepared(), 7)
    resumed = scope_prepared(scoped, 1)
    assert resumed == scoped and resumed is not scoped
    assert resumed['citationScope']['namespace'] == 'C07'
    assert json.loads(resumed['messages'][1]['content'])['blocks'][0]['blockId'] == 'C07-B001'


@pytest.mark.parametrize('bad', ['original-a', 'C02-B001', 'C01-B003', 'C1-B001',
                                'C01-B01', 'c01-B001', ' C01-B001', 'C01-B001 ', 'C01-B001\n'])
def test_new_protocol_never_repairs_or_accepts_an_original_or_foreign_id(bad):
    with pytest.raises(ValueError):
        resolve_reference_block(scope_prepared(prepared(), 1), bad)


@pytest.mark.parametrize('bad', [None, True, 1, {}, [], ''])
def test_missing_empty_and_non_string_model_ids_are_rejected(bad):
    with pytest.raises(ValueError):
        resolve_reference_block(scope_prepared(prepared(), 1), bad)


@pytest.mark.parametrize('mutation', ['other-original', 'outside-frozen', 'extra', 'missing',
                                     'non-string', 'namespace', 'protocol', 'number', 'no-map'])
def test_corrupt_or_forged_alias_mapping_cannot_resolve_even_an_existing_alias(mutation):
    item = scope_prepared(prepared(), 1)
    scope = item['citationScope']
    if mutation == 'other-original':
        scope['aliases']['C01-B001'] = 'original-b'
    elif mutation == 'outside-frozen':
        scope['aliases']['C01-B001'] = 'unfrozen-block'
    elif mutation == 'extra':
        scope['aliases']['C01-B003'] = 'original-a'
    elif mutation == 'missing':
        del scope['aliases']['C01-B002']
    elif mutation == 'non-string':
        scope['aliases']['C01-B002'] = 1
    elif mutation == 'namespace':
        scope['namespace'] = 'C02'
    elif mutation == 'protocol':
        scope['protocol'] = 'future-protocol'
    elif mutation == 'number':
        scope['chapterNumber'] = True
    else:
        del scope['aliases']
    with pytest.raises(ValueError):
        resolve_reference_block(item, 'C01-B001')
    with pytest.raises(ValueError):
        citation_scope_payload(item)
    with pytest.raises(ValueError):
        scope_prepared(item, 1)


def test_null_scope_is_not_silently_treated_as_legacy():
    item = prepared()
    item['citationScope'] = None
    with pytest.raises(ValueError, match='协议'):
        resolve_reference_block(item, 'original-a')


def test_legacy_accepts_only_the_exact_permanent_ids_in_the_original_frozen_scope():
    item = prepared()
    before = copy.deepcopy(item)
    assert citation_scope_payload(item) is None
    assert resolve_reference_block(item, 'original-a') == 'original-a'
    for bad in ('C01-B001', 'other-original', ' original-a', 'original-a '):
        with pytest.raises(ValueError):
            resolve_reference_block(item, bad)
    assert item == before


def test_identical_duplicate_originals_remain_one_identity_but_ambiguous_duplicates_fail():
    item = prepared(('original-a',))
    item['blocks'].append(copy.deepcopy(item['blocks'][0]))
    packet = json.loads(item['messages'][1]['content'])
    packet['blocks'].append(copy.deepcopy(packet['blocks'][0]))
    item['messages'][1]['content'] = json.dumps(packet)
    scoped = scope_prepared(item, 1)
    assert resolve_reference_block(scoped, 'C01-B001') == resolve_reference_block(scoped, 'C01-B002') == 'original-a'
    item['blocks'][1]['revisionId'] = 'another-version'
    with pytest.raises(ValueError, match='重复'):
        scope_prepared(item, 1)


@pytest.mark.parametrize('number', [None, False, 0, -1, '1', 1.0])
def test_new_scope_requires_an_unambiguous_positive_integer_chapter_number(number):
    with pytest.raises(ValueError, match='章节序号'):
        scope_prepared(prepared(), number)


def test_empty_frozen_scope_is_valid_but_cannot_be_used_to_cite_anything():
    item = scope_prepared(prepared(()), 2)
    assert citation_scope_payload(item)['allowedBlockIds'] == []
    with pytest.raises(ValueError):
        resolve_reference_block(item, 'C02-B001')


@pytest.mark.parametrize('change', ['fewer', 'foreign', 'reordered', 'non-object'])
def test_prepared_messages_cannot_change_the_frozen_block_scope(change):
    item = prepared()
    packet = json.loads(item['messages'][1]['content'])
    if change == 'fewer':
        packet['blocks'].pop()
    elif change == 'foreign':
        packet['blocks'][0]['blockId'] = 'foreign'
    elif change == 'reordered':
        packet['blocks'].reverse()
    else:
        packet['blocks'][0] = None
    item['messages'][1]['content'] = json.dumps(packet)
    before = copy.deepcopy(item)
    with pytest.raises(ValueError, match='范围不一致'):
        scope_prepared(item, 1)
    assert item == before


def test_error_includes_specific_chapter_and_one_based_paragraph_and_evidence_location():
    item = scope_prepared(prepared(), 3)
    error = citation_error(item, 2, 1, 'C01-B008', '编号不属于本章')
    assert isinstance(error, ValueError) and isinstance(error, CitationScopeError)
    assert '第3章“历史时间线”（ID：section-7）第3段、第2引用' in str(error)
    assert '模型引用了本次资料范围之外的原文块' in str(error)
    assert '错误原文块编号：C01-B008' in str(error)
    assert (error.chapter_number, error.section_id, error.paragraph_index, error.evidence_index,
            error.block_value) == (3, 'section-7', 2, 1, 'C01-B008')


def test_error_distinguishes_missing_null_non_string_and_empty_identifiers():
    item = scope_prepared(prepared(), 1)
    displayed = [str(citation_error(item, 0, 0, value, '无效'))
                 for value in (MISSING_BLOCK_ID, None, 123, {}, '')]
    assert '缺失 blockId' in displayed[0]
    assert '非字符串：NoneType' in displayed[1]
    assert '非字符串：int' in displayed[2]
    assert '非字符串：dict' in displayed[3]
    assert '空字符串' in displayed[4]
    assert len(set(displayed)) == 5


def test_error_does_not_leak_unbounded_text_or_raw_control_characters():
    item = scope_prepared(prepared(), 2)
    item['section']['title'] = '标题\n\x00\u202e\u2028' + '长' * 500
    item['section']['id'] = 'section\r\t' + 'x' * 500
    value = 'C01-B001\n\x1b\u2067' + 'y' * 500
    message = str(citation_error(item, 0, 0, value, '原因\n\x7f' + 'z' * 500))
    assert len(message) < 1100 and '…' in message
    assert all(char not in message for char in ('\n', '\r', '\t', '\x00', '\x1b', '\u202e', '\u2028', '\u2067'))
    assert '\\u000a' in message and '\\u001b' in message and '\\u202e' in message


def test_legacy_error_still_identifies_chapter_title_id_and_original_bad_id():
    item = prepared()
    item['chapterNumber'] = 4
    message = str(citation_error(item, 0, 0, 'not-in-frozen', '未命中冻结范围'))
    assert '第4章' in message and '历史时间线' in message and 'section-7' in message
    assert 'not-in-frozen' in message


def test_control_heavy_diagnostics_keep_the_bad_id_inside_the_persisted_error_limit():
    item = scope_prepared(prepared(), 2)
    item['section']['title'] = '\u202e' * 200
    item['section']['id'] = '\x00' * 200
    message = str(citation_error(item, 0, 0, 'C01-B999', '\n' * 400))
    assert len(message) < 1100
    assert '错误原文块编号：C01-B999' in message
    assert all(char not in message for char in ('\n', '\x00', '\u202e'))


def test_quote_rejection_can_reuse_location_without_misreporting_scope_rejection():
    item = scope_prepared(prepared(), 2)
    error = citation_error(item, 1, 2, 'C02-B001', '摘录未在冻结原文逐字出现',
                           message_prefix='模型引文无法在冻结原文中逐字定位')
    assert '第2章' in str(error) and '第2段、第3引用' in str(error) and 'C02-B001' in str(error)
    assert '模型引文无法在冻结原文中逐字定位' in str(error)
    assert '本次资料范围之外' not in str(error)
