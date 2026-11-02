"""Frozen local raster export checks, using synthetic images and no model calls."""
import base64
import copy
import hashlib
import io
import json
import re
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from PIL import Image

from server import writing_export, writing_pdf
from server.writing_images import scope_hash


def resource(kind='PNG', size=(80, 40), animated=False):
    output = io.BytesIO()
    picture = Image.new('RGB', size, '#326b53')
    options = {'save_all': True, 'append_images': [Image.new('RGB', size, '#ddaa33')], 'duration': 50, 'loop': 0} if animated else {}
    picture.save(output, format=kind, **options)
    raw = output.getvalue()
    return {'data': base64.b64encode(raw).decode('ascii'), 'mime': Image.MIME[kind],
            'width': size[0], 'height': size[1], 'sha256': hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def illustrated():
    sections = [{'id': 'section_1', 'title': '第一章', 'mode': 'inherit', 'content': '首段🙂。\n同段下一行。\n\n第二段。\n\n第三段。', 'evidence': []},
                {'id': 'section_2', 'title': '第二章', 'mode': 'inherit', 'content': '后章正文。', 'evidence': []}]
    project = {'id': 'writing_images_test', 'version': 3, 'title': '图文导出测试', 'mode': 'strict', 'sections': sections,
               'sources': [{'sourceId': 'source_1', 'revisionId': 'revision_1'}],
               '_imageResources': {'asset_1': resource(), 'asset_2': resource('JPEG', (1600, 400))}}
    placements = []
    for index, section in enumerate(sections):
        anchor = writing_export._paragraph_spans(section['content'])[0]
        asset = f'asset_{index + 1}'
        placements.append({'id': f'figure_{index + 1}', 'assetId': asset, 'assetHash': project['_imageResources'][asset]['sha256'],
                           'sourceId': 'source_1', 'revisionId': 'revision_1', 'sourceTitle': '原始资料 <来源>',
                           'sectionId': section['id'], 'anchor': {'paragraphIndex': 0, 'paragraphExact': anchor['text'],
                                                               'contentHash': hashlib.sha256(section['content'].encode()).hexdigest()},
                           'caption': f'图像说明 {index + 1}', 'altText': '受控图像 <alt>', 'credit': '摄影：隔离样例',
                           'status': 'accepted', 'locked': False, 'reviewed': True})
    # Storage order must not decide the reader's figure numbers.
    project['illustrations'] = list(reversed(placements))
    report = {'version': 3, 'issues': [], 'notice': '图文快照测试', 'checkedAt': '2026-10-23T00:00:00Z', 'status': 'rules_passed',
              'counts': {'errors': 0, 'warnings': 0},
              'wordCounts': {'total': 40, 'target': 0, 'min': 0, 'max': 0, 'method': '测试计数', 'sections': []},
              'coverage': {'validlyCitedParagraphs': 0, 'paragraphs': 4, 'pendingParagraphs': 0}}
    return project, report


@pytest.fixture
def pdf_illustrated(illustrated):
    # The reference PDF runtime has CJK fonts, but no emoji font. Keep supplementary
    # Unicode in HTML/DOCX anchor tests; the PDF regression checks the installed
    # font repertoire. An unsupported emoji makes WeasyPrint emit an embedded
    # Encoding CMap that pypdf cannot decode (independently verified by PDFium).
    project, report = illustrated
    section = project['sections'][0]
    section['content'] = section['content'].replace('🙂', '')
    paragraph = writing_export._paragraph_spans(section['content'])[0]
    placement = next(item for item in project['illustrations'] if item['sectionId'] == section['id'])
    placement['anchor'].update(paragraphExact=paragraph['text'], contentHash=hashlib.sha256(section['content'].encode()).hexdigest())
    return project, report


def test_html_places_safe_embedded_figures_after_exact_paragraphs_without_changing_body(illustrated):
    project, report = illustrated
    before = copy.deepcopy(project)
    project['illustrations'][1]['caption'] = '<script>alert("caption")</script>'
    source = writing_export.render_html(project, report)
    assert source.count('<figure ') == 2 and source.count('<img ') == 2
    assert 'src="data:image/png;base64,' in source and 'src="data:image/jpeg;base64,' in source
    assert '<script>' not in source and '&lt;script&gt;' in source
    assert 'alt="受控图像 &lt;alt&gt;"' in source and '原始资料 &lt;来源&gt;' in source
    assert source.index('同段下一行。') < source.index('id="wenshu-figure-1"') < source.index('第二段。')
    assert source.index('后章正文。') < source.index('id="wenshu-figure-2"')
    assert '图 2 图像说明 2' in source and source.count('摄影：隔离样例') == 2
    assert project['sections'] == before['sections'] and project['_imageResources'] == before['_imageResources']


@pytest.mark.parametrize('separator', ['\n\n', '\n \t\n\n'])
@pytest.mark.parametrize('renderer', [writing_export.render_html, writing_pdf.build_pdf_html])
def test_html_figure_consumes_only_its_paragraph_separator(illustrated, separator, renderer):
    project, report = illustrated
    section = project['sections'][0]
    section['content'] = '首段。\n同段下一行。' + separator + '  第二段。\n保留段内换行。\n\n第三段。'
    first = writing_export._paragraph_spans(section['content'])[0]
    placement = project['illustrations'][1]
    placement['anchor'].update(paragraphExact=first['text'], contentHash=hashlib.sha256(section['content'].encode()).hexdigest())
    before = copy.deepcopy(project)
    source = renderer(project, report)
    if renderer is writing_export.render_html:
        assert '</figure>\n<div class="body">  第二段。\n保留段内换行。\n\n第三段。</div>' in source
        assert '<div class="body">首段。\n同段下一行。</div>\n<figure ' in source
    else:
        from lxml import html
        figure = html.fromstring(source).get_element_by_id('wenshu-figure-1')
        before_body, after_body = figure.getprevious(), figure.getnext()
        assert before_body.get('class') == after_body.get('class') == 'body'
        assert [p.text_content() for p in before_body] == ['首段。', '同段下一行。']
        assert [p.text_content() for p in after_body] == ['  第二段。', '保留段内换行。', '', '第三段。']
        assert 'continuation' not in after_body[0].get('class').split()
        assert 'continuation' in after_body[1].get('class').split()
    assert project == before


def test_html_repeated_and_consecutive_figures_do_not_restore_separator_or_empty_body(illustrated):
    project, report = illustrated
    section = project['sections'][0]
    section['content'] = '第一段。\n\n第二段。\n\n'
    parts = writing_export._paragraph_spans(section['content'])
    placement = project['illustrations'][1]
    placement['anchor'].update(paragraphExact=parts[0]['text'], contentHash=hashlib.sha256(section['content'].encode()).hexdigest())
    repeated = copy.deepcopy(placement)
    repeated['id'] = 'figure_same_paragraph'
    final = copy.deepcopy(placement)
    final['id'] = 'figure_final_paragraph'
    final['anchor'].update(paragraphIndex=1, paragraphExact=parts[1]['text'])
    project['illustrations'].extend([repeated, final])
    source = writing_export.render_html(project, report)
    bodies = re.findall(r'<div class="body">(.*?)</div>', source, re.DOTALL)
    assert bodies == ['第一段。', '第二段。', '后章正文。']
    assert source.count('<figure ') == 4


def test_docx_embeds_media_relationships_alt_text_and_proportional_figures(illustrated):
    project, report = illustrated
    content = writing_export.render_docx(project, report)
    with ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        assert {'word/media/image1.png', 'word/media/image2.jpg'} <= set(names)
        document = ET.fromstring(archive.read('word/document.xml'))
        relationships = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
        image_relationships = [node for node in relationships if node.attrib['Type'].endswith('/image')]
        assert len(image_relationships) == 2 and all('TargetMode' not in node.attrib for node in image_relationships)
        assert all(node.attrib['Target'].startswith('media/') for node in image_relationships)
        text = ''.join(document.itertext())
        assert text.index('同段下一行。') < text.index('图 1 图像说明 1') < text.index('第二段。')
        assert text.index('后章正文。') < text.index('图 2 图像说明 2')
        extents = document.findall('.//{' + writing_export.WP + '}extent')
        assert len(extents) == 2 and int(extents[1].attrib['cx']) <= 5943600
        assert int(extents[1].attrib['cx']) / int(extents[1].attrib['cy']) == pytest.approx(4)
        assert all(node.attrib['descr'] == '受控图像 <alt>' for node in document.findall('.//{' + writing_export.WP + '}docPr'))
        for node in document.findall('.//{' + writing_export.W + '}drawing'):
            assert node.find('.//{' + writing_export.A + '}blip').attrib['{' + writing_export.R + '}embed'].startswith('rIdImage')


@pytest.mark.parametrize('damage', ['missing', 'hash', 'mime', 'dimensions', 'corrupt', 'anchor', 'scope', 'stale', 'unreviewed'])
@pytest.mark.parametrize('renderer', [writing_export.render_html, writing_export.render_docx, writing_pdf.build_pdf_html])
def test_bad_image_or_stale_position_fails_instead_of_silently_dropping_it(illustrated, damage, renderer):
    project, report = illustrated
    placement = project['illustrations'][1]
    image = project['_imageResources']['asset_1']
    if damage == 'missing':
        del project['_imageResources']['asset_1']
    elif damage == 'hash':
        placement['assetHash'] = '0' * 64
    elif damage == 'mime':
        image['mime'] = 'image/svg+xml'
    elif damage == 'dimensions':
        image['width'] += 1
    elif damage == 'corrupt':
        image['data'] = base64.b64encode(b'not an image').decode()
        image['sha256'] = placement['assetHash'] = hashlib.sha256(b'not an image').hexdigest()
    elif damage == 'anchor':
        project['sections'][0]['content'] = '新增段落。\n\n' + project['sections'][0]['content']
    elif damage == 'scope':
        placement['revisionId'] = 'revision_unselected'
    elif damage == 'stale':
        placement['status'] = 'stale'
    else:
        placement['reviewed'] = False
    with pytest.raises(ValueError, match='配图'):
        renderer(project, report)


@pytest.mark.parametrize('kind,animated', [('WEBP', False), ('GIF', True)])
def test_docx_transcodes_unsupported_rasters_and_gif_uses_first_frame_everywhere(illustrated, kind, animated):
    project, report = illustrated
    image = resource(kind, animated=animated)
    project['_imageResources']['asset_1'] = image
    project['illustrations'][1]['assetHash'] = image['sha256']
    source = writing_export.render_html(project, report)
    if kind == 'GIF':
        assert 'data:image/gif' not in source and 'data:image/png;base64,' in source
    with ZipFile(io.BytesIO(writing_export.render_docx(project, report))) as archive:
        assert 'word/media/image1.png' in archive.namelist()
        with Image.open(io.BytesIO(archive.read('word/media/image1.png'))) as picture, Image.open(io.BytesIO(base64.b64decode(image['data']))) as original:
            assert picture.size == (80, 40) and picture.convert('RGB').getpixel((0, 0)) == original.convert('RGB').getpixel((0, 0))


def test_same_asset_repeated_does_not_duplicate_docx_media(illustrated):
    project, report = illustrated
    project['illustrations'][0].update(assetId='asset_1', assetHash=project['_imageResources']['asset_1']['sha256'])
    with ZipFile(io.BytesIO(writing_export.render_docx(project, report))) as archive:
        assert len([name for name in archive.namelist() if name.startswith('word/media/')]) == 1


@pytest.mark.parametrize('renderer', [writing_export.render_html, writing_export.render_docx, writing_pdf.build_pdf_html])
def test_confirmed_library_figure_exports_without_adding_prose_reference(illustrated, renderer):
    project, report = illustrated
    item = project['illustrations'][1]
    item.update(sourceId='external-source', revisionId='external-revision', sourceType='library',
                librarySourceConfirmed=True, reviewMethod='human', runId='frozen-image-run',
                scopeHash=scope_hash(project, project['sections'][0]))
    item['imageSourceApproval'] = {key: item[key] for key in
        ('sourceId', 'revisionId', 'assetId', 'assetHash', 'runId', 'scopeHash')}
    item['imageSourceApproval']['confirmedAt'] = '2026-10-23T00:00:00Z'
    before = copy.deepcopy(project['sources'])
    assert renderer(project, report)
    assert project['sources'] == before
    del item['imageSourceApproval']
    with pytest.raises(ValueError, match='配图'):
        renderer(project, report)


def test_pdf_transports_only_frozen_whitelist_and_uses_no_data_or_file_urls(illustrated, monkeypatch):
    project, report = illustrated
    before = copy.deepcopy(project)
    calls = []
    def convert(command, **options):
        payload = json.loads(options['input'])
        calls.append(payload)
        assert payload['version'] == 1 and len(payload['images']) == 2
        assert 'src="wenshu-image:' in payload['html']
        assert not re.search(r'src="(?:data|file|https?):', payload['html'])
        assert all(key == 'wenshu-image:' + value['sha256'] for key, value in payload['images'].items())
        return SimpleNamespace(returncode=0, stdout=b'%PDF-1.7\n%%EOF')
    monkeypatch.setattr(writing_pdf.subprocess, 'run', convert)
    assert writing_pdf.render_pdf(project, report).startswith(b'%PDF-') and calls
    assert project == before


@pytest.mark.parametrize('url', ['http://127.0.0.1/private', 'https://example.invalid/private', 'file:///etc/passwd',
                                'data:image/png;base64,aGVsbG8=', 'wenshu-image:unknown', 'wenshu-image:%2e%2e/private'])
def test_pdf_whitelist_rejects_arbitrary_urls_without_leaking_them(url):
    with pytest.raises(writing_pdf.PDFResourceDenied) as caught:
        writing_pdf.frozen_url_fetcher(url, {})
    assert url not in str(caught.value)


def test_real_pdf_has_embedded_images_caption_and_source(pdf_illustrated):
    from pypdf import PdfReader
    project, report = pdf_illustrated
    result = writing_pdf.render_pdf(project, report)
    reader = PdfReader(io.BytesIO(result))
    text = re.sub(r'\s+', '', '\n'.join(page.extract_text() or '' for page in reader.pages))
    assert '首段。同段下一行。' in text and '第二段。' in text and '第三段。' in text and '后章正文。' in text
    assert '图1图像说明1' in text and '图2图像说明2' in text and '原始资料<来源>' in text
    # Font/image resources may be shared by all pages. Count painted image
    # operators, rather than repeated resource dictionary entries.
    painted = []
    for page in reader.pages:
        images = page['/Resources'].get('/XObject', {})
        for operands, operator in page.get_contents().operations:
            if operator == b'Do' and images[operands[0]].get_object().get('/Subtype') == '/Image':
                painted.append(operands[0])
    assert len(painted) == 2


def test_real_pdf_never_fetches_unregistered_resource_even_when_other_image_is_valid(illustrated):
    from weasyprint.urls import FatalURLFetchingError
    project, report = illustrated
    source = writing_pdf.build_pdf_html(project, report).replace('</body>', '<img src="file:///etc/passwd"></body>')
    with pytest.raises(FatalURLFetchingError):
        writing_pdf._render_in_child(source, writing_pdf._frozen_image_resources(project))


def test_no_image_snapshot_keeps_text_only_export_contract(illustrated):
    project, report = illustrated
    project.pop('illustrations')
    project.pop('_imageResources')
    assert '<figure ' not in writing_export.render_html(project, report)
    with ZipFile(io.BytesIO(writing_export.render_docx(project, report))) as archive:
        assert not any(name.startswith('word/media/') for name in archive.namelist())

