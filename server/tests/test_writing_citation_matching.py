"""Program-owned sentence identities and strict semantic-review boundaries.

All decisions below are controlled fixtures. They verify coordinate/schema
handling and honest uncertainty, not the factual judgment quality of a model.
"""
import copy
import json

import pytest

from server.prompts import prompt_text
from server.writing_citation_matching import (
    build_matching_packet, matching_messages, partition_matching_packets,
    sentence_ranges, subset_matching_packet, validate_matching_result,
)


def texts(content):
    spans = sentence_ranges(content)
    assert all(content[item['start']:item['end']] == item['text'] for item in spans)
    return [item['text'] for item in spans]


@pytest.mark.parametrize('content,expected', [
    (' 第一项。第二项！第三项？\n\n第四项；仍是第四项。 ', ['第一项。', '第二项！', '第三项？', '第四项；仍是第四项。']),
    ('收益为1.25%，v1.2.3仍可使用。另有一句。', ['收益为1.25%，v1.2.3仍可使用。', '另有一句。']),
    ('Dr. J. R. Smith met the U.S. Navy. The result was 1.25.', ['Dr. J. R. Smith met the U.S. Navy.', 'The result was 1.25.']),
    ('Use e.g. the current policy. It applies to the U.K. Army.', ['Use e.g. the current policy.', 'It applies to the U.K. Army.']),
    ('受访者说：“the number is zero. You just do not know what is there.”换言之，仍然不确定。',
     ['受访者说：“the number is zero. You just do not know what is there.”', '换言之，仍然不确定。']),
    ('他说：“第一句。第二句！”之后一段。', ['他说：“第一句。第二句！”', '之后一段。']),
    ('“First. Second.” Next sentence.', ['“First. Second.”', 'Next sentence.']),
    ('这被称为“可能的威胁”，并非已经确认。其后一句。', ['这被称为“可能的威胁”，并非已经确认。', '其后一句。']),
    ("Don't alter the source's meaning. Keep it.", ["Don't alter the source's meaning.", 'Keep it.']),
    ('标题\n正文第一句。\r\n第二句。', ['标题', '正文第一句。', '第二句。']),
    ('See https://example.test/item?q=one.two&x=1. Next sentence.',
     ['See https://example.test/item?q=one.two&x=1.', 'Next sentence.']),
    ('他说：“受访者回答‘尚未确认。’”换言之，仍需核查。',
     ['他说：“受访者回答‘尚未确认。’”', '换言之，仍需核查。']),
    ('威胁感知可以成立——“the number is zero.”技术清扫仍然必要。',
     ['威胁感知可以成立——“the number is zero.”', '技术清扫仍然必要。']),
    ('材料写道——“尚未确认。”，这仍是一个条件性判断。其后一句。',
     ['材料写道——“尚未确认。”，这仍是一个条件性判断。', '其后一句。']),
    ('材料写道——“尚未确认。”。技术清扫仍然必要。',
     ['材料写道——“尚未确认。”。', '技术清扫仍然必要。']),
    ('材料写道——“尚未确认。”并强调需要核实。其后一句。',
     ['材料写道——“尚未确认。”并强调需要核实。', '其后一句。']),
    ('材料写道——“尚未确认。”  ，这仍是一个条件性判断。其后一句。',
     ['材料写道——“尚未确认。”  ，这仍是一个条件性判断。', '其后一句。']),
    ('He replied—“Not confirmed.” and continued the explanation. Next sentence.',
     ['He replied—“Not confirmed.” and continued the explanation.', 'Next sentence.']),
    ('它强调感知：“People have to perceive safety.”而威胁感知可以成立——“the number is zero.”技术清扫仍然必要。',
     ['它强调感知：“People have to perceive safety.”', '而威胁感知可以成立——“the number is zero.”', '技术清扫仍然必要。']),
    ('⚠\n\n   \n。', []),
])
def test_segmentation_retains_exact_sentences_and_qualified_clauses(content, expected):
    assert texts(content) == expected


def refs():
    return [{'id': 'E1', 'sourceId': 'source', 'revisionId': 'revision', 'blockId': 'b1',
             'quote': 'Ships may be affected if mines are present.', 'start': 7, 'end': 50,
             'summary': 'This must never become evidence.', 'translation': '译文不能作为新的证据。'}]


def judgment(sid, ids=None, support='supported', reason='原文保留可能性及必要条件，支持该句相同范围内的论述。'):
    return {'sentenceId': sid, 'evidenceIds': ['E1'] if ids is None else ids, 'support': support, 'reason': reason}


def packet(content='若存在水雷，船只可能受到影响。[E1]仍需确认实际情况。'):
    return build_matching_packet(content, refs())


def test_packet_strips_only_markers_owns_ids_and_never_uses_derived_evidence():
    source = '  相同句子。[E1]相同句子。\n\n保留空白。 '
    built = packet(source)
    assert built['content'] == '  相同句子。相同句子。\n\n保留空白。 '
    assert [item['sentenceId'] for item in built['sentences']] == ['S1', 'S2', 'S3']
    assert built['sentences'][0]['text'] == built['sentences'][1]['text']
    assert 'summary' not in built['evidence'][0] and 'translation' not in built['evidence'][0]
    messages = matching_messages(built)
    payload = json.loads(messages[1]['content'])
    assert payload['task'] == 'writing_citation_match'
    assert 'content' not in payload and payload['sentences'] == built['sentences']
    system = messages[0]['content']
    for contract in ('条件', '否定', '因果', 'unsupported', 'partial', '推理', 'quote是唯一', '每个sentenceId必须恰好判断一次'):
        assert contract in system


def test_packet_context_checks_actual_original_quote_and_frozen_source_version():
    evidence = refs()
    original = 'Before ' + evidence[0]['quote'] + ' After.'
    assert original[7:50] == evidence[0]['quote']
    block = {'blockId': 'b1', 'sourceId': 'source', 'revisionId': 'revision', 'text': original}
    built = build_matching_packet('只读正文。', evidence, blocks=[block])
    assert built['evidence'][0]['contextBefore'] == 'Before '
    assert built['evidence'][0]['contextAfter'] == ' After.'
    for change in ({'start': 0}, {'end': 99}, {'revisionId': 'other'}, {'quote': 'invented'}):
        invalid = [dict(evidence[0], **change)]
        with pytest.raises(ValueError):
            build_matching_packet('只读正文。', invalid, blocks=[block])


def test_exhaustive_review_maps_by_id_not_response_order_and_uses_utf16():
    built = packet('🛳若存在水雷，船只可能受到影响。[E1]仍需确认实际情况。')
    before = copy.deepcopy(built)
    checked = validate_matching_result(built, {'assignments': [
        judgment('S2', [], 'unsupported', '所给摘录未说明已经确认的实际布雷情况。'), judgment('S1')]})
    assert built == before
    assert [item['sentenceId'] for item in checked['assignments']] == ['S1', 'S2']
    first = checked['matches'][0]
    assert first['start'] == 0 and first['end'] == len(first['text']) + 1
    assert checked['matches'][1]['evidenceId'] is None
    assert checked['matches'][1]['status'] == 'unsupported'
    assert len(checked['issues']) == 1 and checked['issues'][0]['status'] == 'unsupported'


@pytest.mark.parametrize('mutate', [
    lambda value: value.update(text='Rewrite prose.'),
    lambda value: value['assignments'].pop(),
    lambda value: value['assignments'][1].update(sentenceId='S1'),
    lambda value: value['assignments'][0].update(sentenceId='S99'),
    lambda value: value['assignments'][0].update(evidenceIds=['made-up']),
    lambda value: value['assignments'][0].update(evidenceIds=['E1', 'E1']),
    lambda value: value['assignments'][0].update(evidenceIds=[]),
    lambda value: value['assignments'][0].update(start=0),
    lambda value: value['assignments'][0].update(text='changed'),
    lambda value: value['assignments'][0].update(support='verified'),
    lambda value: value['assignments'][0].update(support=[]),
    lambda value: value['assignments'][0].update(reason=' '),
    lambda value: value['assignments'][1].update(evidenceIds=['E1']),
])
def test_response_cannot_omit_sentences_invent_evidence_change_body_or_supply_offsets(mutate):
    value = {'assignments': [judgment('S1'), judgment('S2', [], 'unsupported')]}
    mutate(value)
    with pytest.raises(ValueError):
        validate_matching_result(packet(), value)


@pytest.mark.parametrize('status', ['partial', 'uncertain'])
def test_partial_and_uncertain_retain_honest_diagnostics_without_promotion(status):
    checked = validate_matching_result(packet(), {'assignments': [judgment('S1', support=status), judgment('S2', [], 'unsupported')]})
    assert checked['matches'][0]['status'] == status
    assert len(checked['issues']) == 2


def test_organizational_sentence_needs_no_evidence_or_warning_and_cannot_borrow_a_source():
    built = packet('下文从三个方面展开讨论。')
    decision = judgment('S1', [], 'not_required', '此句仅说明文章组织，没有提出来源事实或分析结论。')
    checked = validate_matching_result(built, {'assignments': [decision]})
    assert checked['issues'] == []
    assert checked['matches'][0]['status'] == 'not_required'
    assert checked['matches'][0]['evidenceId'] is None
    decision['evidenceIds'] = ['E1']
    with pytest.raises(ValueError, match='组织性句子'):
        validate_matching_result(built, {'assignments': [decision]})


@pytest.mark.parametrize('mutate', [
    lambda value: value.update(content=value['content'] + ' changed'),
    lambda value: value['sentences'][0].update(text='changed'),
    lambda value: value['sentences'][0].update(start=1),
    lambda value: value['evidence'][0].update(quote='invented'),
])
def test_mutated_frozen_body_or_registry_cannot_receive_old_review(mutate):
    built = packet()
    mutate(built)
    with pytest.raises(ValueError):
        validate_matching_result(built, {'assignments': [judgment('S1'), judgment('S2', [], 'unsupported')]})


def test_batched_sentences_retain_global_ids_offsets_and_full_evidence_registry():
    built = packet('第一句。第二句。第三句。')
    selected = subset_matching_packet(built, ['S2', 'S3'])
    assert selected['evidence'] == built['evidence']
    checked = validate_matching_result(selected, {'assignments': [judgment('S2'), judgment('S3')]})
    assert checked['matches'][0]['start'] == 4
    assert [item['sentenceId'] for item in checked['matches']] == ['S2', 'S3']
    with pytest.raises(ValueError):
        subset_matching_packet(built, ['S9'])


def test_partition_omits_only_optional_context_and_never_truncates_quotes():
    evidence = refs()
    original = 'x' * 600 + evidence[0]['quote'] + 'y' * 600
    evidence[0].update(start=600, end=600 + len(evidence[0]['quote']))
    built = build_matching_packet('原始正文。' * 12, evidence,
        blocks=[{'blockId': 'b1', 'sourceId': 'source', 'revisionId': 'revision', 'text': original}])
    system = prompt_text('writing_citation_match')
    min_input = sum(len(item['content']) for item in matching_messages(subset_matching_packet(built, ['S1']))) - 1200
    budget = min_input + 250
    batches = partition_matching_packets(built, budget, instruction=system)
    assert len(batches) > 1
    assert [sentence['sentenceId'] for batch in batches for sentence in batch['sentences']] == [sentence['sentenceId'] for sentence in built['sentences']]
    for batch in batches:
        assert batch['evidence'][0]['quote'] == evidence[0]['quote']
        assert 'contextBefore' not in batch['evidence'][0]
        assert sum(len(item['content']) for item in matching_messages(batch, instruction=system)) <= budget
        validate_matching_result(batch, {'assignments': [judgment(sentence['sentenceId']) for sentence in batch['sentences']]})
    with pytest.raises(ValueError, match='不能省略证据'):
        partition_matching_packets(built, len(system) + 1, instruction=system)
