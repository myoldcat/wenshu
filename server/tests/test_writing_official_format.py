"""Official-document presentation checks on controlled, model-free exports.

Inspect Word's package and WeasyPrint's rendered geometry instead of matching
stylesheet source strings. These checks do not edit or read a real manuscript.
"""
from __future__ import annotations

import copy
import io
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from server import writing_export, writing_pdf


W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
BODY_PT = 16
LINE_PT = 28.8


def mm_to_twips(value):
    return value * 1440 / 25.4


def mm_to_px(value):
    return value * 96 / 25.4


@pytest.fixture
def official_snapshot():
    content = (
        '首段正文用于检查自然段排版。' + '正文应保持原有内容和引用边界，不因导出改变。' * 5 +
        '\n同一自然段的续行保留原换行。\n\n'
        '一、第一层标题\n\n'
        '（一）第二层标题\n\n'
        '1.第三层标题\n\n'
        '（1）第四层标题\n\n'
        '第二自然段保留 <script> & 特殊字符及未知引用 [E99]。'
    )
    project = {
        'id': 'official_format_controlled', 'version': 7,
        'title': '文稿导出格式验收', 'mode': 'strict', 'sources': [],
        'sections': [{'id': 'controlled_section', 'title': '排版样例',
                      'mode': 'inherit', 'content': content, 'evidence': [],
                      'locked': True, 'manual': True, 'version': 4}],
        'illustrations': [],
    }
    report = {'version': 7, 'issues': [], 'status': 'rules_passed',
              'notice': '测试核对状态不应进入文章文件',
              'counts': {'errors': 0, 'warnings': 0}}
    return project, report


def word_package(project, report):
    with ZipFile(io.BytesIO(writing_export.render_docx(project, report))) as archive:
        assert archive.testzip() is None
        return {name: ET.fromstring(archive.read(name))
                for name in archive.namelist() if name.endswith(('.xml', '.rels'))}


def paragraph_text(node):
    return ''.join(
        (child.text or '') if child.tag == W + 't' else
        '\n' if child.tag in (W + 'br', W + 'cr') else
        '\t' if child.tag == W + 'tab' else ''
        for child in node.iter())


def styles_by_id(parts):
    return {style.get(W + 'styleId'): style
            for style in parts['word/styles.xml'].findall(W + 'style')}


def resolved_style_property(styles, style_id, group, name):
    """Resolve paragraph-style inheritance as an actual Word reader would."""
    seen = set()
    while style_id and style_id not in seen:
        seen.add(style_id)
        style = styles[style_id]
        prop = style.find(f'{W}{group}/{W}{name}')
        if prop is not None:
            return prop
        based = style.find(W + 'basedOn')
        style_id = based.get(W + 'val') if based is not None else None
    return None


def paragraph_property(node, styles, group, name):
    direct = node.find(f'{W}{group}/{W}{name}')
    if direct is not None:
        return direct
    style = node.find(f'{W}pPr/{W}pStyle')
    return resolved_style_property(styles, style.get(W + 'val') if style is not None else 'Normal', group, name)


def test_word_uses_a4_official_body_area_and_typography(official_snapshot):
    project, report = official_snapshot
    before = copy.deepcopy((project, report))
    parts = word_package(project, report)
    section = parts['word/document.xml'].find(f'{W}body/{W}sectPr')
    size, margins = section.find(W + 'pgSz'), section.find(W + 'pgMar')
    assert int(size.get(W + 'w')) == pytest.approx(mm_to_twips(210), abs=1)
    assert int(size.get(W + 'h')) == pytest.approx(mm_to_twips(297), abs=1)
    for key, expected in {'top': 37, 'left': 28, 'right': 26, 'bottom': 35}.items():
        assert int(margins.get(W + key)) == pytest.approx(mm_to_twips(expected), abs=1)

    styles = styles_by_id(parts)
    body = resolved_style_property(styles, 'BodyText', 'rPr', 'sz')
    assert int(body.get(W + 'val')) == BODY_PT * 2
    font = resolved_style_property(styles, 'BodyText', 'rPr', 'rFonts')
    assert '仿宋' in font.get(W + 'eastAsia')
    title = resolved_style_property(styles, 'Title', 'rPr', 'sz')
    assert int(title.get(W + 'val')) == 44
    alignment = resolved_style_property(styles, 'Title', 'pPr', 'jc')
    assert alignment.get(W + 'val') == 'center'
    spacing = resolved_style_property(styles, 'BodyText', 'pPr', 'spacing')
    assert spacing.get(W + 'lineRule') == 'exact'
    assert int(spacing.get(W + 'line')) == pytest.approx(LINE_PT * 20, abs=1)
    first_body = next(node for node in parts['word/document.xml'].iter(W + 'p')
                      if paragraph_text(node).startswith('首段正文'))
    indentation = paragraph_property(first_body, styles, 'pPr', 'ind')
    assert int(indentation.get(W + 'firstLine')) == BODY_PT * 2 * 20
    assert (project, report) == before


def test_word_recognizes_four_explicit_heading_levels_without_rewriting_text(official_snapshot):
    project, report = official_snapshot
    parts = word_package(project, report)
    styles = styles_by_id(parts)
    expected = [('一、第一层标题', 0, '黑体'), ('（一）第二层标题', 1, '楷体'),
                ('1.第三层标题', 2, '仿宋'), ('（1）第四层标题', 3, '仿宋')]
    paragraphs = {paragraph_text(node): node for node in parts['word/document.xml'].iter(W + 'p')}
    for text, level, font_name in expected:
        node = paragraphs[text]
        outline = paragraph_property(node, styles, 'pPr', 'outlineLvl')
        assert outline is not None and int(outline.get(W + 'val')) == level
        font = paragraph_property(node, styles, 'rPr', 'rFonts')
        assert font_name in font.get(W + 'eastAsia')
        size = paragraph_property(node, styles, 'rPr', 'sz')
        assert int(size.get(W + 'val')) == BODY_PT * 2
    text = '\n'.join(paragraph_text(node) for node in parts['word/document.xml'].iter(W + 'p'))
    assert '同一自然段的续行保留原换行。' in text
    assert '第二自然段保留 <script> & 特殊字符及未知引用 [E99]。' in text
    assert report['notice'] not in text and '已锁定' not in text and '人工修改' not in text


def test_word_has_dynamic_outside_page_numbers_and_complete_package_relationships(official_snapshot):
    project, report = official_snapshot
    parts = word_package(project, report)
    settings = parts['word/settings.xml']
    assert settings.find(W + 'evenAndOddHeaders') is not None
    section = parts['word/document.xml'].find(f'{W}body/{W}sectPr')
    footer_refs = {node.get(W + 'type'): node.get(R + 'id')
                   for node in section.findall(W + 'footerReference')}
    assert set(footer_refs) == {'default', 'even'}
    relationships = {node.get('Id'): node for node in parts['word/_rels/document.xml.rels']}
    content_types = {node.get('PartName'): node.get('ContentType')
                     for node in parts['[Content_Types].xml']}
    for footer_type, expected_alignment in [('default', 'right'), ('even', 'left')]:
        relationship = relationships[footer_refs[footer_type]]
        assert relationship.get('Type').endswith('/footer')
        target = 'word/' + relationship.get('Target')
        assert content_types['/' + target].endswith('wordprocessingml.footer+xml')
        footer = parts[target]
        paragraphs = footer.findall(W + 'p')
        assert len(paragraphs) == 1
        paragraph = paragraphs[0]
        assert paragraph.find(f'{W}pPr/{W}jc').get(W + 'val') == expected_alignment
        instructions = ' '.join(node.get(W + 'instr', '') for node in footer.iter(W + 'fldSimple'))
        instructions += ' ' + ' '.join(node.text or '' for node in footer.iter(W + 'instrText'))
        assert re.search(r'\bPAGE\b', instructions, flags=re.IGNORECASE)
        assert not re.search(r'\bNUMPAGES\b', instructions, flags=re.IGNORECASE)
        assert paragraph_text(footer).count('—') >= 2
        sizes = [node.get(W + 'val') for node in footer.iter(W + 'sz')]
        assert sizes and all(value == '28' for value in sizes)
        fonts = list(footer.iter(W + 'rFonts'))
        assert fonts and all('宋' in node.get(W + 'eastAsia', '') for node in fonts)
    assert not any(node.get('TargetMode') == 'External' for node in relationships.values())


def test_word_package_has_only_local_page_fields_without_forced_updates(official_snapshot):
    project, report = official_snapshot
    literal = r'{ INCLUDETEXT "C:\external.docx" }'
    quote = '固定证据原文'
    reference = dict(id='E1', sourceId='field-audit-source', revisionId='field-audit-revision',
                     blockId='field-audit-block', start=0, end=len(quote), quote=quote,
                     title='域与内部引用测试资料', url='https://example.test/field-audit')
    project['sources'] = [dict(sourceId=reference['sourceId'], revisionId=reference['revisionId'])]
    project['sections'][0].update(
        content='正文按字面保留域样式文本：' + literal + '；引用仍指向文内列表。[E1]',
        evidence=[reference])
    before = copy.deepcopy((project, report))
    with ZipFile(io.BytesIO(writing_export.render_docx(project, report))) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert not any('embeddings/' in name or 'vbaproject' in name.lower() for name in names)
        parts = {name: ET.fromstring(archive.read(name)) for name in names
                 if name.endswith(('.xml', '.rels'))}

    instructions = []
    for name, root in parts.items():
        if name.endswith('.rels'):
            for relation in root:
                assert relation.get('TargetMode', '').lower() != 'external'
                assert not urlsplit(relation.get('Target', '')).scheme
                assert not relation.get('Type', '').endswith('/attachedTemplate')
        pending = []
        for node in root.iter():
            assert node.tag not in (W + 'attachedTemplate', W + 'altChunk', W + 'object')
            assert not node.tag.endswith('}OLEObject')
            if node.tag == W + 'updateFields':
                assert node.get(W + 'val') in {'false', '0', 'off'}
            if W + 'dirty' in node.attrib:
                assert node.get(W + 'dirty') in {'false', '0', 'off'}
            if node.tag == W + 'fldSimple':
                instructions.append(node.get(W + 'instr', ''))
            elif node.tag == W + 'fldChar':
                kind = node.get(W + 'fldCharType')
                if kind == 'begin':
                    pending.append(dict(code=[], collecting=True))
                elif kind == 'separate':
                    assert pending
                    pending[-1]['collecting'] = False
                elif kind == 'end':
                    assert pending
                    instructions.append(''.join(pending.pop()['code']))
            elif node.tag == W + 'instrText':
                assert pending and pending[-1]['collecting']
                pending[-1]['code'].append(node.text or '')
        assert not pending
    assert [instruction.strip().upper() for instruction in instructions] == ['PAGE', 'PAGE']

    document = parts['word/document.xml']
    assert literal in paragraph_text(document)
    assert any(literal in (node.text or '') for node in document.iter(W + 't'))
    links = list(document.iter(W + 'hyperlink'))
    bookmarks = {node.get(W + 'name') for node in document.iter(W + 'bookmarkStart')}
    assert len(links) == 1 and paragraph_text(links[0]) == '[1]'
    assert links[0].get(W + 'anchor') == 'wenshu_ref_1'
    assert links[0].get(W + 'anchor') in bookmarks
    assert links[0].get(R + 'id') is None
    assert (project, report) == before


def test_whitespace_separator_keeps_authored_characters_and_restarts_paragraph_indent(official_snapshot):
    from lxml import html

    project, report = official_snapshot
    project['sections'][0]['content'] = '首段正文。\n \t\n后段正文。'
    parts = word_package(project, report)
    paragraphs = [node for node in parts['word/document.xml'].iter(W + 'p')
                  if node.find(f'{W}pPr/{W}pStyle').get(W + 'val') == 'BodyText']
    assert [paragraph_text(node) for node in paragraphs] == ['首段正文。', ' \t', '后段正文。']
    indentation = paragraph_property(paragraphs[-1], styles_by_id(parts), 'pPr', 'ind')
    assert indentation.get(W + 'firstLine') == '640'
    source = html.fromstring(writing_pdf.build_pdf_html(project, report))
    last = source.xpath('//p[text()="后段正文。"]')[0]
    assert 'continuation' not in last.get('class').split()
    assert source.xpath('//p[@class="blank-line"]/text()') == [' \t']


def box_text(box):
    return ''.join(child.text for child in box.descendants() if hasattr(child, 'text'))


def test_pdf_renders_official_page_geometry_natural_indent_and_line_spacing(official_snapshot):
    from weasyprint import HTML

    project, report = official_snapshot
    before = copy.deepcopy((project, report))
    source = writing_pdf.build_pdf_html(project, report)
    document = HTML(string=source, url_fetcher=writing_pdf.deny_url_fetcher).render()
    page = document.pages[0]._page_box
    assert document.pages[0].width == pytest.approx(mm_to_px(210), abs=.05)
    assert document.pages[0].height == pytest.approx(mm_to_px(297), abs=.05)
    assert page.width == pytest.approx(mm_to_px(156), abs=.05)
    assert page.height == pytest.approx(mm_to_px(225), abs=.05)
    assert page.margin_top == pytest.approx(mm_to_px(37), abs=.05)
    assert page.margin_left == pytest.approx(mm_to_px(28), abs=.05)

    # A real laid-out paragraph must contain several actual line boxes. A
    # text-indent rule on the outer chapter div alone would indent only the
    # chapter's first paragraph, which this second paragraph check catches.
    paragraphs = [box for output_page in document.pages for box in output_page._page_box.descendants()
                  if type(box).__name__ == 'BlockBox' and box.children
                  and type(box.children[0]).__name__ == 'LineBox']
    for prefix in ('首段正文', '第二自然段'):
        paragraph = next(box for box in paragraphs if box_text(box).startswith(prefix))
        assert paragraph.style['font_size'] == pytest.approx(BODY_PT * 96 / 72)
        indent = paragraph.style['text_indent']
        assert getattr(indent, 'value', indent) == pytest.approx(32 * 96 / 72)
    paragraph = next(box for box in paragraphs if box_text(box).startswith('首段正文'))
    lines = [box for box in paragraph.children if type(box).__name__ == 'LineBox']
    assert len(lines) >= 3
    for previous, current in zip(lines, lines[1:]):
        assert current.position_y - previous.position_y == pytest.approx(LINE_PT * 96 / 72, abs=.1)
    assert (project, report) == before


def test_real_pdf_keeps_manuscript_text_and_alternating_page_numbers(official_snapshot):
    from pypdf import PdfReader
    project, report = official_snapshot
    # Enough short paragraphs to exercise both page sides and automatic
    # continuation; the sample remains synthetic and makes no model requests.
    project['sections'][0]['content'] = '\n\n'.join(
        f'受控段落{index}：文稿原文必须逐字保留，排版只改变阅读形式。'
        for index in range(36))
    before = copy.deepcopy((project, report))
    reader = PdfReader(io.BytesIO(writing_pdf.render_pdf(project, report)))
    assert len(reader.pages) >= 2
    texts = [page.extract_text() or '' for page in reader.pages]
    normalized = re.sub(r'\s+', '', '\n'.join(texts))
    for paragraph in project['sections'][0]['content'].split('\n\n'):
        assert paragraph in normalized
    for number, text in enumerate(texts, 1):
        assert re.search(r'—\s*' + str(number) + r'\s*—', text)
    assert report['notice'] not in normalized and '全文校验记录' not in normalized
    assert (project, report) == before


@pytest.mark.parametrize('separator', ['\n\n', '\n \t\n', '\n\n\n'])
def test_pdf_heading_keeps_following_body_on_same_page_through_blank_lines(official_snapshot, separator):
    from weasyprint import HTML

    project, report = official_snapshot
    heading, opening = '（一）页尾标题', '必须紧随标题同页的首段。'
    observed_pages = set()
    # Vary the preceding text around the page boundary. The sample must prove
    # both an ordinary same-page case and an actual move to the following page;
    # a short one-page sample would not catch an isolated heading at the foot.
    for fill_lines in range(14, 24):
        project['sections'][0]['content'] = ('\n'.join(['排版填充行'] * fill_lines) + '\n' +
                                            heading + separator + opening)
        before = copy.deepcopy((project, report))
        document = HTML(string=writing_pdf.build_pdf_html(project, report),
                        url_fetcher=writing_pdf.deny_url_fetcher).render()
        texts = [box_text(page._page_box) for page in document.pages]
        heading_pages = [number for number, text in enumerate(texts) if heading in text]
        opening_pages = [number for number, text in enumerate(texts) if opening in text]
        assert len(heading_pages) == len(opening_pages) == 1
        assert heading_pages == opening_pages, (fill_lines, separator, heading_pages, opening_pages)
        observed_pages.add(heading_pages[0])
        assert (project, report) == before
    assert 0 in observed_pages and any(number > 0 for number in observed_pages)
