"""Reader exports use numeric superscripts without inventing valid sources.

All fixtures are immutable synthetic snapshots; no store or model is involved.
"""
import copy
import io
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from lxml import html

from server.writing_citations import build_citation_plan, build_export_citation_plan
from server.writing_export import render_docx, render_html


W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
MARKER = re.compile(r'\[E\d+\]')
NUMBER = re.compile(r'\[\d+\]')


def reference(eid='E1', source='source-valid', revision='revision-valid', block='block-valid'):
    quote = '隔离固定版本中的逐字原文。'
    return {'id': eid, 'sourceId': source, 'revisionId': revision, 'blockId': block,
            'start': 0, 'end': len(quote), 'quote': quote, 'title': '资料 ' + source}


def section(sid, content, evidence):
    return {'id': sid, 'title': '章节 ' + sid, 'mode': 'inherit',
            'content': content, 'evidence': evidence}


def snapshot(sections):
    sources = {(ref['sourceId'], ref['revisionId'])
               for chapter in sections for ref in chapter.get('evidence', [])
               if isinstance(ref, dict) and ref.get('sourceId') and ref.get('revisionId')}
    return {
        'title': '模拟文稿引用导出', 'version': 8, 'mode': 'strict', 'illustrations': [],
        'sources': [{'sourceId': source, 'revisionId': revision}
                    for source, revision in sorted(sources)],
        'sections': sections,
    }, {'version': 8, 'issues': []}


def text(node):
    return ''.join(part.text or '' for part in node.iter(W + 't'))


def inspect_export(project, report, format):
    """Return body superscripts, links and bibliography numbers for each format."""
    if format != 'docx':
        rendered = render_html(project, report, print_layout=format == 'pdf-input')
        assert not MARKER.search(rendered)
        root = html.fromstring(rendered)
        superscripts = root.xpath('//sup[contains(concat(" ", normalize-space(@class), " "), " citation ")]')
        values = [node.text_content() for node in superscripts]
        assert all(NUMBER.fullmatch(value) for value in values)
        links = [(node.text_content(), node.get('href'))
                 for node in root.xpath('//sup/a')]
        references = [(node.get('id'), node.text_content())
                      for node in root.xpath('//div[@class="reference"]')]
        return values, links, references

    with ZipFile(io.BytesIO(render_docx(project, report))) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    assert not MARKER.search(text(root))
    paragraphs = [node for node in root.iter(W + 'p')
                  if node.find(f'{W}pPr/{W}pStyle').get(W + 'val') == 'BodyText']
    values = []
    for paragraph in paragraphs:
        for run in paragraph.iter(W + 'r'):
            value = text(run)
            if NUMBER.fullmatch(value):
                alignment = run.find(f'{W}rPr/{W}vertAlign')
                assert alignment is not None and alignment.get(W + 'val') == 'superscript'
                values.append(value)
    links = [(text(node), '#' + node.get(W + 'anchor').replace('wenshu_ref_', 'wenshu-ref-'))
             for paragraph in paragraphs for node in paragraph.iter(W + 'hyperlink')]
    references = []
    for paragraph in root.iter(W + 'p'):
        if paragraph.find(f'{W}pPr/{W}pStyle').get(W + 'val') != 'Source':
            continue
        bookmark = paragraph.find(W + 'bookmarkStart')
        references.append((bookmark.get(W + 'name').replace('wenshu_ref_', 'wenshu-ref-'), text(paragraph)))
    return values, links, references


@pytest.mark.parametrize('failure', [
    'unknown', 'duplicate', 'incomplete', 'anchor', 'scope', 'narrowed-scope',
    'stale-state', 'stale-bindings', 'stale-review',
])
@pytest.mark.parametrize('format', ['html', 'pdf-input', 'docx'])
def test_unresolved_references_are_numeric_superscripts_without_source_links(failure, format):
    candidate = reference('E11', 'source-candidate', 'revision-candidate', 'block-candidate')
    evidence = [candidate]
    if failure == 'unknown':
        evidence = []
    elif failure == 'duplicate':
        evidence.append(reference('E11', 'source-other', 'revision-other', 'block-other'))
    elif failure == 'incomplete':
        candidate['quote'] = ''
    unresolved = section('unresolved', '首次引用[E11]。再次引用[E11]。', evidence)
    project, report = snapshot([unresolved, section('valid', '有效引用[E1]。', [reference()])])
    if failure == 'anchor':
        report['issues'].append({'code': 'citation_anchor_invalid', 'sectionId': 'unresolved',
                                 'evidenceId': 'E11', 'message': '固定原文定位失效'})
    elif failure == 'scope':
        project['sources'] = [source for source in project['sources']
                              if source['sourceId'] != 'source-candidate']
    elif failure == 'narrowed-scope':
        next(source for source in project['sources']
             if source['sourceId'] == 'source-candidate')['blockIds'] = ['another-block']
    elif failure == 'stale-state':
        unresolved['citationBindingState'] = 'stale'
    elif failure == 'stale-bindings':
        unresolved['citationBindings'] = {'version': 1, 'content': '旧正文'}
    elif failure == 'stale-review':
        report['issues'].append({'code': 'citation_binding_stale', 'sectionId': 'unresolved',
                                 'message': '当前引用句定位失效'})
    before = copy.deepcopy((project, report))
    strict = build_citation_plan(project, report)
    plan = build_export_citation_plan(project, report)
    assert strict['sectionNumbers'] == [{}, {'E1': 1}]
    assert plan['sectionNumbers'] == [{'E11': 1}, {'E1': 2}]
    assert plan['sectionLinks'] == [{}, {'E1': 2}]
    assert [entry['number'] for entry in plan['references']] == [2]
    assert plan['unresolved'] == strict['unresolved']

    values, links, references = inspect_export(project, report, format)
    assert values == ['[1]', '[1]', '[2]']
    assert links == [('[2]', '#wenshu-ref-2')]
    assert references == [('wenshu-ref-2', '[2] 资料 source-valid')]
    assert (project, report) == before


@pytest.mark.parametrize('format', ['html', 'pdf-input', 'docx'])
def test_display_numbers_follow_reading_order_and_keep_chapter_and_version_identity(format):
    first = reference('E7', 'source-a', 'revision-a1', 'block-a1')
    excerpt = reference('E8', 'source-a', 'revision-a1', 'block-a2')
    project, report = snapshot([
        section('one', '未知[E99]，首引甲[E7][E8]。另处甲[E8]。', [first, excerpt]),
        section('two', '乙[E7]，再引甲[E1]，新版甲[E2]，本章未知[E99]。', [
            reference('E7', 'source-b', 'revision-b', 'block-b'),
            reference('E1', 'source-a', 'revision-a1', 'block-a1'),
            reference('E2', 'source-a', 'revision-a2', 'block-a3'),
        ]),
    ])
    before = copy.deepcopy((project, report))
    plan = build_export_citation_plan(project, report)
    assert plan['sectionNumbers'] == [
        {'E99': 1, 'E7': 2, 'E8': 2}, {'E7': 3, 'E1': 2, 'E2': 4, 'E99': 5}]
    assert plan['sectionLinks'] == [{'E7': 2, 'E8': 2}, {'E7': 3, 'E1': 2, 'E2': 4}]
    assert [(entry['number'], entry['reference']['sourceId'], entry['reference']['revisionId'])
            for entry in plan['references']] == [
        (2, 'source-a', 'revision-a1'), (3, 'source-b', 'revision-b'), (4, 'source-a', 'revision-a2')]
    assert len(plan['references'][0]['anchors']) == 2

    values, links, references = inspect_export(project, report, format)
    assert values == ['[1]', '[2]', '[2]', '[3]', '[2]', '[4]', '[5]']
    assert links == [(f'[{number}]', f'#wenshu-ref-{number}') for number in (2, 2, 3, 2, 4)]
    assert references == [
        ('wenshu-ref-2', '[2] 资料 source-a'), ('wenshu-ref-3', '[3] 资料 source-b'),
        ('wenshu-ref-4', '[4] 资料 source-a')]
    assert (project, report) == before


@pytest.mark.parametrize('format', ['html', 'pdf-input', 'docx'])
def test_stale_citation_cannot_borrow_a_later_valid_source_link(format):
    stale = section('stale', '旧引用[E1]。', [reference()])
    stale['citationBindingState'] = 'stale'
    project, report = snapshot([stale, section('legacy', '旧协议有效引用[E1]。', [reference()])])
    before = copy.deepcopy((project, report))
    values, links, references = inspect_export(project, report, format)
    assert values == ['[1]', '[2]']
    assert links == [('[2]', '#wenshu-ref-2')]
    assert references == [('wenshu-ref-2', '[2] 资料 source-valid')]
    assert (project, report) == before


@pytest.mark.parametrize('format', ['html', 'pdf-input', 'docx'])
def test_stale_snapshot_review_only_formats_numbers_without_fabricating_a_bibliography(format):
    project, report = snapshot([section('one', '旧校验引用[E1]。未知[E99]。', [reference()])])
    report['version'] -= 1
    before = copy.deepcopy((project, report))
    values, links, references = inspect_export(project, report, format)
    assert values == ['[1]', '[2]']
    assert links == references == []
    assert (project, report) == before


@pytest.mark.parametrize('format', ['html', 'pdf-input', 'docx'])
def test_malformed_tokens_are_not_guessed_into_numeric_source_references(format):
    content = '完整未知[E11]。坏标记[Efoo] [E-1] [E1'
    project, report = snapshot([section('one', content, [])])
    before = copy.deepcopy((project, report))
    values, links, references = inspect_export(project, report, format)
    assert values == ['[1]'] and links == references == []
    if format == 'docx':
        with ZipFile(io.BytesIO(render_docx(project, report))) as archive:
            rendered_text = text(ET.fromstring(archive.read('word/document.xml')))
    else:
        rendered_text = html.fromstring(render_html(project, report, print_layout=format == 'pdf-input')).text_content()
    assert '坏标记[Efoo] [E-1] [E1' in rendered_text
    assert (project, report) == before
