"""Pure bibliography planning: local markers never confer source identity."""
import copy

import pytest

from server.writing_citations import build_citation_plan, citation_parts
from server.writing_sentence_citations import relocate_citations


def reference(eid='E1', source='s1', revision='r1', block='b1', quote='固定原文', start=0):
    return {'id': eid, 'sourceId': source, 'revisionId': revision, 'blockId': block,
            'quote': quote, 'start': start, 'end': start + len(quote), 'title': '资料 ' + source,
            'url': 'https://example.test/' + source}


def snapshot(sections):
    sources = list({(ref['sourceId'], ref['revisionId']):
                    {'sourceId': ref['sourceId'], 'revisionId': ref['revisionId']}
                    for section in sections for ref in section.get('evidence', []) if isinstance(ref, dict)
                    and isinstance(ref.get('sourceId'), str) and isinstance(ref.get('revisionId'), str)}.values())
    return {'version': 8, 'sources': sources, 'sections': sections}, {'version': 8, 'issues': []}


def section(sid, text, refs):
    return {'id': sid, 'title': '章节 ' + sid, 'content': text, 'evidence': refs}


def test_stale_sentence_matching_cannot_reuse_valid_source_numbers():
    chapter = section('a', '原句。[E1]', [reference()])
    chapter['content'], chapter['citationBindings'] = relocate_citations(chapter['content'], chapter['evidence'],
        [{'evidenceId': 'E1', 'start': 0, 'end': 3, 'text': '原句。', 'status': 'supported', 'reason': '受控判断。'}])
    chapter['content'] = '改写后的句子。[E1]'
    project, report = snapshot([chapter])
    before = copy.deepcopy(project)
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{}] and plan['references'] == []
    assert '定位已失效' in plan['unresolved'][0]['reason']
    assert project == before


def test_extended_inference_binding_cannot_be_numbered_as_strict_support_without_a_stale_report():
    chapter = section('a', '这是有据推断。', [reference()])
    chapter['mode'] = 'inherit'
    chapter['content'], chapter['citationBindings'] = relocate_citations(chapter['content'], chapter['evidence'],
        [{'evidenceId': 'E1', 'start': 0, 'end': len(chapter['content']), 'text': chapter['content'],
          'status': 'supported', 'supportBasis': 'inference', 'reason': '受控扩展模式判断。'}], mode='extended')
    project, report = snapshot([chapter])
    project['mode'] = 'strict'
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{}] and plan['references'] == []
    assert len(plan['unresolved']) == 1
    project['mode'] = 'extended'
    assert build_citation_plan(project, report)['sectionNumbers'] == [{'E1': 1}]


@pytest.mark.parametrize('stale_state,stale_report', [(True, False), (False, True), (True, True)])
def test_removed_stale_bindings_cannot_regain_numbers_and_legacy_chapters_remain_compatible(stale_state, stale_report):
    edited = section('edited', '改写后的句子。[E1]', [reference()])
    legacy = section('legacy', '旧协议正文。[E1]', [reference()])
    # This is the persisted shape after _save removes invalid coordinates.
    assert 'citationBindings' not in edited
    if stale_state:
        edited['citationBindingState'] = 'stale'
    project, report = snapshot([edited, legacy])
    message = '保存后的正文已经变化，原有引用句定位失效，请重新匹配引用'
    if stale_report:
        report['issues'].append({'code': 'citation_binding_stale', 'sectionId': 'edited', 'message': message})
    report['issues'].append({'code': 'citation_binding_missing', 'sectionId': 'legacy', 'severity': 'warning',
                             'message': '现有引用只关联段落，尚未核对具体受支持句子'})
    before = copy.deepcopy((project, report))
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{}, {'E1': 1}]
    assert len(plan['references']) == 1
    assert [use['sectionNumber'] for use in plan['references'][0]['uses']] == [2]
    assert len(plan['unresolved']) == 1
    unresolved = plan['unresolved'][0]
    assert unresolved['sectionNumber'] == 1 and unresolved['evidenceId'] == 'E1'
    assert unresolved['records'] == edited['evidence']
    assert '定位失效' in unresolved['reason'] or '定位已失效' in unresolved['reason']
    if stale_report:
        assert message in unresolved['reason']
    # The unresolved raw token stays visible in draft HTML/Word/PDF planning.
    assert list(citation_parts(edited['content'], plan['sectionNumbers'][0])) == [(edited['content'], None)]
    assert ''.join(text for text, _ in citation_parts(legacy['content'], plan['sectionNumbers'][1])) == '旧协议正文。[1]'
    assert (project, report) == before
    assert 'citationBindings' not in legacy  # compatibility does not invent review/highlight metadata


def test_stale_review_prevents_numbering_even_when_obsolete_binding_metadata_looks_valid():
    chapter = section('a', '原句。[E1]', [reference()])
    chapter['content'], chapter['citationBindings'] = relocate_citations(chapter['content'], chapter['evidence'],
        [{'evidenceId': 'E1', 'start': 0, 'end': 3, 'text': '原句。', 'status': 'supported', 'reason': '受控判断。'}])
    project, report = snapshot([chapter])
    report['issues'] = [{'code': 'citation_binding_stale', 'sectionId': 'a', 'message': '本次校验判定引用句定位已失效'}]
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{}] and plan['references'] == []
    assert '本次校验判定引用句定位已失效' in plan['unresolved'][0]['reason']


def test_current_sentence_matching_numbers_only_supported_markers_and_keeps_diagnostics():
    chapter = section('a', '原句。缺证句。[E1]', [reference()])
    chapter['content'], chapter['citationBindings'] = relocate_citations(chapter['content'], chapter['evidence'], [
        {'evidenceId': 'E1', 'start': 0, 'end': 3, 'text': '原句。', 'status': 'supported', 'reason': '受控支持。'},
        {'evidenceId': None, 'start': 3, 'end': 7, 'text': '缺证句。', 'status': 'unsupported', 'reason': '原文没有这项主张。'},
    ])
    project, report = snapshot([chapter])
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{'E1': 1}] and len(plan['references']) == 1
    assert chapter['content'] == '原句。[E1]缺证句。'
    assert chapter['citationBindings']['items'][1]['status'] == 'unsupported'


def test_numbers_follow_first_appearance_and_chapter_local_markers_never_collide():
    a, b, c = reference(), reference(source='s2', revision='r2'), reference('E8', 's3', 'r3')
    project, report = snapshot([section('a', '先引第三份 [E8]，再引第一份 [E1]。', [a, c]),
                                section('b', '本章的 E1 指向第二份。[E1][E1]', [b])])
    before = copy.deepcopy((project, report))
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{'E8': 1, 'E1': 2}, {'E1': 3}]
    assert [(row['number'], row['reference']['sourceId']) for row in plan['references']] == [(1, 's3'), (2, 's1'), (3, 's2')]
    assert plan['unresolved'] == []
    assert (project, report) == before


def test_same_source_version_reuses_number_and_keeps_all_distinct_exact_anchors():
    a = reference('E8', block='b-first', quote='第一处逐字摘录')
    b = reference('E2', block='b-second', quote='第二处逐字摘录', start=17)
    again = dict(a, id='E1')
    project, report = snapshot([section('a', '先第二处 [E2]，后第一处 [E8][E2]。', [a, b]),
                                section('b', '继续引用第一处 [E1]。', [again])])
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{'E2': 1, 'E8': 1}, {'E1': 1}]
    assert len(plan['references']) == 1
    entry = plan['references'][0]
    assert [row['reference']['blockId'] for row in entry['anchors']] == ['b-second', 'b-first']
    assert [row['reference']['quote'] for row in entry['anchors']] == [b['quote'], a['quote']]
    assert [row['evidenceId'] for row in entry['anchors'][1]['uses']] == ['E8', 'E1']
    assert [row['sectionNumber'] for row in entry['uses']] == [1, 1, 2]


def test_different_versions_of_same_source_are_separate_references():
    project, report = snapshot([section('a', '旧版 [E1] 新版 [E2]。',
                                        [reference(), reference('E2', revision='r2')])])
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{'E1': 1, 'E2': 2}]
    assert [row['reference']['revisionId'] for row in plan['references']] == ['r1', 'r2']


@pytest.mark.parametrize('change', [{'quote': ''}, {'start': True}, {'end': 999}, {'blockId': ''}])
def test_incomplete_or_malformed_references_and_unknown_ids_do_not_receive_numbers(change):
    ref = reference()
    ref.update(change)
    project, report = snapshot([section('a', '未知 [E99]，坏登记 [E1]。', [ref])])
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{}] and plan['references'] == []
    assert [row['evidenceId'] for row in plan['unresolved']] == ['E99', 'E1']
    assert '未登记' in plan['unresolved'][0]['reason']
    assert plan['unresolved'][1]['records'] == [ref]


@pytest.mark.parametrize('code', ['duplicate_citation', 'citation_incomplete', 'citation_out_of_scope', 'citation_anchor_invalid'])
def test_frozen_review_anchor_failures_cannot_be_exported_as_valid_numbered_sources(code):
    project, report = snapshot([section('a', '无效 [E1]。', [reference()])])
    report['issues'] = [{'code': code, 'sectionId': 'a', 'evidenceId': 'E1', 'message': '定位未通过'}]
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{}] and plan['references'] == []
    assert plan['unresolved'][0]['reason'] == '定位未通过'


def test_duplicate_registry_ids_preserve_both_records_and_cannot_choose_one():
    refs = [reference(), reference(source='s2', revision='r2')]
    project, report = snapshot([section('a', '重复 [E1]。', refs)])
    plan = build_citation_plan(project, report)
    assert plan['sectionNumbers'] == [{}] and plan['references'] == []
    assert plan['unresolved'][0]['records'] == refs
    assert '重复登记' in plan['unresolved'][0]['reason']


def test_narrowed_scope_and_stale_review_never_confer_numbered_validity():
    project, report = snapshot([section('a', '引用 [E1]。', [reference()])])
    project['sources'][0]['blockIds'] = ['different-block']
    assert build_citation_plan(project, report)['references'] == []
    project['sources'][0].pop('blockIds')
    report['version'] = 7
    plan = build_citation_plan(project, report)
    assert plan['references'] == [] and '校验版本不一致' in plan['unresolved'][0]['reason']


def test_literal_spans_whitespace_and_unresolved_markers_are_retained_exactly():
    original = '  😀\t[E99]\r\n\r\n  原文 <script> [E1][Efoo] [E2] [E1  '
    parts = list(citation_parts(original, {'E1': 3, 'E2': 1}))
    assert ''.join(part for part, _number in parts) == original.replace('[E1]', '[3]').replace('[E2]', '[1]')
    assert [(part, number) for part, number in parts if number is not None] == [('[3]', 3), ('[1]', 1)]
    assert list(citation_parts('', {})) == [('', None)]


@pytest.mark.parametrize('separator', ['', ' ', '\t', '\u00a0', '\u3000'])
def test_adjacent_same_source_numbers_are_collapsed_without_losing_whitespace(separator):
    original = '一句话[E1]' + separator + '[E2]' + separator + '[E1]。'
    parts = list(citation_parts(original, {'E1': 1, 'E2': 1}))
    assert ''.join(part for part, _ in parts) == '一句话[1]' + separator * 2 + '。'
    assert [(part, number) for part, number in parts if number is not None] == [('[1]', 1)]


def test_each_source_is_shown_once_in_a_mixed_adjacent_cluster():
    parts = list(citation_parts('正文[E1][E2][E3][E2][E4]', {'E1': 1, 'E2': 2, 'E3': 1, 'E4': 3}))
    assert ''.join(part for part, _ in parts) == '正文[1][2][3]'


@pytest.mark.parametrize('separator', ['。另一句', '，另一观点', '\n', '\r\n', '\r', '\v', '\f',
                                        '\x85', '\u2028', '\u2029', '[E99]', '[Efoo]', '[E1'])
def test_distinct_body_positions_line_breaks_and_unresolved_markers_end_the_cluster(separator):
    original = '正文[E1]' + separator + '[E2]'
    parts = list(citation_parts(original, {'E1': 1, 'E2': 1}))
    assert ''.join(part for part, _ in parts) == '正文[1]' + separator + '[1]'
    assert [(part, number) for part, number in parts if number is not None] == [('[1]', 1), ('[1]', 1)]
