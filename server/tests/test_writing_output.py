"""Usable-output repairs are opt-in and cannot manufacture source evidence."""
import copy
import json

import pytest

from server.writing_output import OUTPUT_POLICY, enabled, parse_output


def frozen(policy=OUTPUT_POLICY):
    return {'messages': [{'role': 'system', 'content': '写作'},
                         {'role': 'user', 'content': json.dumps({'generationOutputPolicy': policy})}]}


def response(value, **metadata):
    return {'text': json.dumps(value, ensure_ascii=False), **metadata}


@pytest.mark.parametrize('value', [{}, {'messages': []}, {'messages': [{}, {}]},
                                  {'messages': [{}, {'content': 'broken'}]},
                                  {'messages': [{}, {'content': '[]'}]},
                                  {'generationOutputPolicy': OUTPUT_POLICY},
                                  frozen('preserve-usable-v2'), frozen(True), frozen(None)])
def test_only_exact_frozen_packet_policy_enables_repairs(value):
    assert not enabled(value)


def test_valid_output_unchanged_without_spurious_notice():
    value = {'paragraphs': [{'text': '忠实正文。', 'evidence': [{'blockId': '1', 'quote': '原文'}],
                             'kind': 'supported'}], 'issues': [], 'other': {'preserved': True}}
    parsed, notices = parse_output(frozen(), response(value), 'section')
    assert parsed == value
    assert notices == []


@pytest.mark.parametrize('raw', ['读者正文。', json.dumps('读者正文。', ensure_ascii=False)])
def test_plain_prose_is_kept_outside_legacy_json_contract(raw):
    parsed, notices = parse_output(frozen(), {'text': raw}, 'section')
    assert parsed['paragraphs'] == [{'text': '读者正文。', 'evidence': []}]
    assert notices
    with pytest.raises(ValueError):
        parse_output(frozen(None), {'text': raw}, 'section')


def test_string_paragraph_and_extra_fields_are_preserved():
    value = {'paragraphs': ['正文段落。'], 'other': {'keep': [1, 2]}}
    parsed, notices = parse_output(frozen(), response(value), 'section')
    assert parsed == {'paragraphs': [{'text': '正文段落。', 'evidence': []}], 'other': {'keep': [1, 2]}}
    assert notices


@pytest.mark.parametrize('key', ['text', 'content'])
def test_top_level_body_wrapper_recovers_only_new_section_output(key):
    value = {key: '可用正文[E99]。', 'evidence': [{'blockId': '1', 'quote': '未登记引用'}]}
    parsed, notices = parse_output(frozen(), response(value), 'section')
    assert parsed['paragraphs'] == [{'text': '可用正文。', 'evidence': []}]
    assert notices
    assert parse_output(frozen(None), response(value), 'section') == (value, [])
    assert parse_output(frozen(), response(value), 'outline') == (value, [])
    assert parse_output(frozen(), response(value), 'edit') == (value, [])


def test_split_paragraphs_cannot_inherit_evidence_for_a_different_shape():
    value = {'paragraphs': [{'text': '第一段。\n\n第二段。\u2029第三段。',
                             'kind': 'supported', 'evidence': [{'blockId': '1', 'quote': '第一段。'}]}]}
    parsed, notices = parse_output(frozen(), response(value), 'section')
    assert [p['text'] for p in parsed['paragraphs']] == ['第一段。', '第二段。', '第三段。']
    assert all(p['evidence'] == [] and p['kind'] == 'supported' for p in parsed['paragraphs'])
    assert notices


def test_model_owned_markers_removed_without_deleting_other_body():
    value = {'paragraphs': [{'text': '正文[E123]，[注释]及【待核实】保留。[E7]',
                             'evidence': [{'blockId': '1', 'quote': '正文'}]}]}
    parsed, notices = parse_output(frozen(), response(value), 'section')
    assert parsed['paragraphs'] == [{'text': '正文，[注释]及【待核实】保留。', 'evidence': []}]
    assert notices


def test_skips_only_empty_non_body_items_when_other_body_remains():
    value = {'paragraphs': [None, {'evidence': []}, {'text': ''}, {'text': '可用正文。', 'evidence': []}]}
    parsed, notices = parse_output(frozen(), response(value), 'section')
    assert parsed['paragraphs'] == [{'text': '可用正文。', 'evidence': []}]
    assert notices


@pytest.mark.parametrize('value', [{'paragraphs': []}, {'paragraphs': [None, {'text': ''}]},
                                   {'paragraphs': [{'text': '[E1]'}]}, {'other': '无正文'}])
def test_completely_missing_usable_body_still_fails(value):
    with pytest.raises(ValueError, match='可用正文'):
        parse_output(frozen(), response(value), 'section')


@pytest.mark.parametrize('raw', ['{"paragraphs": [', '[{"text":"段落"}',
                                '```json\n{"paragraphs": [', '```\n{"paragraphs": [',
                                '```JSON\n{"paragraphs": ['])
def test_incomplete_json_cannot_be_disguised_as_prose(raw):
    with pytest.raises(ValueError, match='异常中断'):
        parse_output(frozen(), {'text': raw}, 'section')


def test_incomplete_protocol_object_after_preface_is_still_an_interruption():
    raw = '以下是正文：\n{"paragraphs":[{"text":"残缺'
    with pytest.raises(ValueError, match='异常中断'):
        parse_output(frozen(), {'text': raw, 'finish_reason': 'stop'}, 'section')


def test_complete_prefaced_protocol_object_recovers_fields_and_evidence():
    value = {'paragraphs': [{'text': '可用正文。', 'evidence': [{'blockId': '1', 'quote': '原文'}]}],
             'other': '完整保留'}
    raw = '以下是正文：\n```json\n' + json.dumps(value, ensure_ascii=False) + '\n```'
    parsed, notices = parse_output(frozen(), {'text': raw, 'finish_reason': 'stop'}, 'section')
    assert parsed == value
    assert notices
    with pytest.raises(ValueError):
        parse_output(frozen(None), {'text': raw}, 'section')


def test_ordinary_prose_discussing_json_is_not_a_protocol_wrapper():
    raw = 'JSON 的 paragraphs 字段用于段落；例如 {"paragraphs":[]} 可以表示空列表。'
    parsed, notices = parse_output(frozen(), {'text': raw}, 'section')
    assert parsed['paragraphs'] == [{'text': raw, 'evidence': []}]
    assert notices


@pytest.mark.parametrize('key', ['finish_reason', 'finishReason'])
@pytest.mark.parametrize('reason', ['length', 'max_tokens', 'interrupted', 'content_filter', 'error', 'cancelled'])
def test_explicit_interruption_remains_severe(key, reason):
    with pytest.raises(ValueError, match='异常中断'):
        parse_output(frozen(), response({'paragraphs': ['完整形状。']}, **{key: reason}), 'section')


@pytest.mark.parametrize('status, message', [('unrelated', '完全无关'), ('interrupted', '异常中断')])
def test_explicit_severe_content_status_fails(status, message):
    with pytest.raises(ValueError, match=message):
        parse_output(frozen(), response({'contentStatus': status, 'paragraphs': ['正文。']}), 'section')


def test_body_keywords_are_not_semantic_failure_detection():
    value = {'paragraphs': [{'text': '对话中“完全无关”和“异常中断”是两个问题描述。', 'evidence': []}]}
    assert parse_output(frozen(), response(value, finish_reason='stop'), 'section') == (value, [])


def test_issues_become_bounded_author_notices_and_never_body():
    value = {'paragraphs': [{'text': '正文。', 'evidence': []}], 'issues': '细节应再检查。'}
    parsed, notices = parse_output(frozen(), response(value), 'section')
    assert parsed['paragraphs'] == value['paragraphs']
    assert parsed['issues'] == []
    assert notices == ['细节应再检查。']
    value['issues'] = ['sourceIds=[private-source-id]：资料对应存在问题。', {'sourceId': 'not-a-notice'},
                       '可读说明' * 200, *[f'提示 {i}' for i in range(20)]]
    parsed, notices = parse_output(frozen(), response(value), 'section')
    assert len(notices) <= 12 and all(len(x) <= 500 for x in notices)
    assert 'private-source-id' not in ''.join(notices)
    assert 'sourceId' not in ''.join(notices)


@pytest.mark.parametrize('kind', ['outline', 'edit'])
def test_non_section_output_structure_is_not_repaired(kind):
    value = {'paragraphs': ['不要修改我。\n\n[E9]'], 'replacementText': '字面[E9]', 'sections': '原样'}
    assert parse_output(frozen(), response(value), kind) == (value, [])
    with pytest.raises(ValueError):
        parse_output(frozen(), {'text': '普通正文'}, kind)


def test_legacy_output_ignores_new_metadata_and_performs_no_repair():
    value = {'contentStatus': 'unrelated', 'paragraphs': ['正文[E1]\n\n继续'], 'issues': '旧格式'}
    assert parse_output(frozen(None), response(value, finish_reason='length'), 'section') == (value, [])


def test_outline_issue_notice_does_not_erase_scope_review_input():
    value = {'sections': [], 'issues': ['当前资料不能支持所规划的影响分析。']}
    parsed, notices = parse_output(frozen(), response(value), 'outline')
    assert parsed == value
    assert notices == value['issues']
    value['issues'] = '当前资料不能支持所规划的影响分析。'
    parsed, notices = parse_output(frozen(), response(value), 'outline')
    assert parsed['issues'] == [value['issues']]
    assert notices == [value['issues']]


def test_does_not_truncate_body_or_mutate_caller_data():
    text = '保留全文。' * 14000
    value = {'paragraphs': [{'text': text, 'evidence': []}], 'issues': []}
    request = frozen()
    answer = response(value)
    before = copy.deepcopy((request, answer))
    parsed, notices = parse_output(request, answer, 'section')
    assert parsed['paragraphs'][0]['text'] == text
    assert notices == []
    assert (request, answer) == before
