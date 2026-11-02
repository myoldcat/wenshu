"""The report lead precedes chapter one in all frozen export formats."""
import copy
import io
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from server.writing_export import render_docx, render_html
from server.writing_introduction import introduction_end


def snapshot():
    return {
        'title': '调研报告', 'mode': 'strict', 'sources': [], 'illustrations': [],
        'sections': [
            {'id': 'current', 'title': '当前态势', 'mode': 'inherit', 'evidence': [],
             'content': '背景介绍：产业发展背景。\n\n背景范围保留 <原文>。\n\n态势正文。', 'introductionParagraphCount': 2},
            {'id': 'issues', 'title': '存在问题隐患', 'mode': 'inherit', 'evidence': [], 'content': '问题正文。'},
            {'id': 'actions', 'title': '对策建议', 'mode': 'inherit', 'evidence': [], 'content': '建议正文。'},
        ],
    }, {'issues': []}


@pytest.mark.parametrize('print_layout', [False, True])
def test_html_and_pdf_input_lead_before_first_heading(print_layout):
    project, report = snapshot()
    before = copy.deepcopy(project)
    rendered = render_html(project, report, print_layout=print_layout)
    assert rendered.index('背景介绍：产业发展背景。') < rendered.index('<h2>') < rendered.index('态势正文。')
    assert '背景范围保留 &lt;原文&gt;。' in rendered
    assert rendered.count('<h2>') == 3
    assert project == before


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_word_lead_is_body_before_three_numbered_chapters(newline):
    project, report = snapshot()
    project['sections'][0]['content'] = project['sections'][0]['content'].replace('\n', newline)
    before = copy.deepcopy(project)
    with ZipFile(io.BytesIO(render_docx(project, report))) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    rows = [(p.find('w:pPr/w:pStyle', ns).get('{'+ns['w']+'}val'), ''.join(p.itertext()))
            for p in root.findall('w:body/w:p', ns)]
    assert [text for style, text in rows if style == 'Heading1'] == ['一、当前态势', '二、存在问题隐患', '三、对策建议']
    background = next(i for i, (_, text) in enumerate(rows) if '背景范围保留' in text)
    heading = next(i for i, (style, _) in enumerate(rows) if style == 'Heading1')
    body = next(i for i, (_, text) in enumerate(rows) if text == '态势正文。')
    assert background < heading < body
    assert project == before


@pytest.mark.parametrize('count', [None, True, '2', 0, -1, 3, 100])
def test_invalid_lead_count_keeps_original_presentation(count):
    project, report = snapshot()
    project['sections'][0]['introductionParagraphCount'] = count
    assert introduction_end(project['sections'][0]) == 0
    rendered = render_html(project, report)
    assert rendered.index('<h2>') < rendered.index('背景介绍：产业发展背景。')


def test_other_chapters_cannot_create_another_article_lead():
    project, report = snapshot()
    project['sections'][1].update(content='普通开头。\n\n问题正文。', introductionParagraphCount=1)
    rendered = render_html(project, report)
    assert rendered.index('2. 存在问题隐患') < rendered.index('普通开头。')
