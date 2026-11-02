"""Safe editable HTML/OOXML exports from one frozen writing snapshot.

No model calls, network requests, external relationships, or text repairs.
The service captures the project and review together before calling renderers.
"""
from __future__ import annotations

import base64
import hashlib
import html
from functools import lru_cache
from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from .writing_citations import build_export_citation_plan, citation_parts
from .writing_images import paragraphs as image_paragraphs, valid_placement
from .writing_review import INVALID_XML
from .writing_introduction import introduction_end
from .writing_format import (
    PAGE_WIDTH_MM, PAGE_HEIGHT_MM, MARGIN_TOP_MM, MARGIN_BOTTOM_MM,
    MARGIN_LEFT_MM, MARGIN_RIGHT_MM, BODY_WIDTH_MM, LINE_PT,
    BODY_SIZE_PT, TITLE_SIZE_PT, FIRST_LINE_PT,
    body_heading_level, chapter_heading, mm_twips,
)

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
XML = 'http://www.w3.org/XML/1998/namespace'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
PIC = 'http://schemas.openxmlformats.org/drawingml/2006/picture'
IMAGE_TYPES = {'image/png': ('PNG', 'png'), 'image/jpeg': ('JPEG', 'jpg'), 'image/webp': ('WEBP', 'webp'), 'image/gif': ('GIF', 'gif')}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 8 * 1024 * 1024
ET.register_namespace('w', W)
ET.register_namespace('r', R)
ET.register_namespace('wp', WP)
ET.register_namespace('a', A)
ET.register_namespace('pic', PIC)


@lru_cache(maxsize=1)
def _reference_title_converter():
    from opencc import OpenCC
    return OpenCC('t2s')


def _reference_title(title):
    """Simplify only the exported label, preserving the frozen source title."""
    return _reference_title_converter().convert(title)


def _paragraph_spans(content):
    return image_paragraphs(content)


def validate_image_resource(resource, expected_hash=None):
    """Decode only frozen raster bytes; never fetch an asset or URL here."""
    try:
        if not isinstance(resource, dict) or resource.get('mime') not in IMAGE_TYPES:
            raise ValueError()
        encoded = resource.get('data')
        if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        digest = hashlib.sha256(raw).hexdigest()
        if not raw or len(raw) > MAX_IMAGE_BYTES or resource.get('sha256') != digest or expected_hash and expected_hash != digest:
            raise ValueError()
        from PIL import Image
        with Image.open(BytesIO(raw)) as picture:
            width, height = picture.size
            if picture.format != IMAGE_TYPES[resource['mime']][0] or width * height > 40_000_000 or resource['mime'] != 'image/gif' and getattr(picture, 'n_frames', 1) != 1:
                raise ValueError()
            if any(isinstance(resource.get(key), bool) or not isinstance(resource.get(key), int) for key in ('width', 'height')):
                raise ValueError()
            if (width, height) != (resource['width'], resource['height']) or min(width, height) < 1:
                raise ValueError()
            picture.verify()
        # verify() checks structure; load() also rejects truncated pixel data.
        with Image.open(BytesIO(raw)) as picture:
            picture.load()
        return {'data': encoded, 'raw': raw, 'mime': resource['mime'], 'width': width, 'height': height, 'sha256': digest}
    except Exception:
        raise ValueError('配图资源缺失、损坏或与已确认版本不一致，请重新核对后导出') from None


def raster_image_resource(resource):
    """GIF exports use the same first frame in all formats; source hash stays pinned."""
    if resource['mime'] != 'image/gif':
        return resource
    from PIL import Image
    output = BytesIO()
    with Image.open(BytesIO(resource['raw'])) as picture:
        picture.seek(0)
        picture.convert('RGBA').save(output, format='PNG')
    raw = output.getvalue()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('配图转换后超过单张导出容量，请调整图片后导出')
    return {**resource, 'raw': raw, 'data': base64.b64encode(raw).decode('ascii'), 'mime': 'image/png'}


def image_export_plan(project):
    """Validate positions and bytes before any renderer emits partial output."""
    placements = project.get('illustrations', [])
    if not isinstance(placements, list) or len(placements) > 40:
        raise ValueError('配图列表无效或超过单次导出容量')
    sections = {section['id']: (index, section) for index, section in enumerate(project['sections'])}
    resources = project.get('_imageResources', {})
    if not isinstance(resources, dict):
        raise ValueError('配图导出资源无效')
    result, seen, decoded, total = [], set(), {}, 0
    for placement in placements:
        if not isinstance(placement, dict) or placement.get('status') != 'accepted' or placement.get('reviewed') is not True:
            raise ValueError('配图尚未完成确认或位置已变化，请复核后导出')
        figure_id, asset_id = placement.get('id'), placement.get('assetId')
        if not isinstance(figure_id, str) or not figure_id or figure_id in seen or not isinstance(asset_id, str) or not asset_id:
            raise ValueError('配图登记不完整或重复，请复核后导出')
        seen.add(figure_id)
        anchor = placement.get('anchor')
        target = sections.get(placement.get('sectionId'))
        if not target or not isinstance(anchor, dict):
            raise ValueError('配图不属于当前章节或固定资料范围，请复核后导出')
        if not valid_placement(project, placement):
            raise ValueError('配图位置或参考资料范围已变化，请复核后导出')
        section_index, section = target
        content = section.get('content', '')
        parts = _paragraph_spans(content)
        index = anchor.get('paragraphIndex')
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(parts) or anchor.get('paragraphExact') != parts[index]['text'] or anchor.get('contentHash') != hashlib.sha256(content.encode()).hexdigest():
            raise ValueError('配图位置与当前正文不一致，请复核后导出')
        expected_hash = placement.get('assetHash')
        if not isinstance(expected_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_hash):
            raise ValueError('配图版本登记不完整，请复核后导出')
        if asset_id not in decoded:
            decoded[asset_id] = validate_image_resource(resources.get(asset_id), expected_hash)
            total += len(decoded[asset_id]['raw'])
        resource = decoded[asset_id]
        if resource['sha256'] != expected_hash or total > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError('配图版本不一致或超过单次导出容量，请核对后导出')
        for key in ('caption', 'altText', 'credit', 'sourceTitle'):
            if not isinstance(placement.get(key, ''), str) or INVALID_XML.search(placement.get(key, '')):
                raise ValueError('配图说明含无效文本，请核对后导出')
        result.append({'placement': placement, 'resource': resource, 'sectionIndex': section_index, 'end': parts[index]['end']})
    result.sort(key=lambda item: (item['sectionIndex'], item['placement']['anchor']['paragraphIndex']))
    for number, item in enumerate(result, 1):
        item['number'] = number
    return result


def _figure_caption(item):
    return f'图 {item["number"]}' + (' ' + item['placement']['caption'] if item['placement'].get('caption') else '')


def _figure_credit(item):
    placement = item['placement']
    source = '来源：' + (placement.get('sourceTitle') or '已确认参考资料')
    credit = placement.get('credit', '')
    return source + ('；' + credit if credit and credit != source else '')


def _html_figure(item, image_urls=None):
    resource, placement = raster_image_resource(item['resource']), item['placement']
    url = image_urls[resource['sha256']] if image_urls is not None else 'data:' + resource['mime'] + ';base64,' + resource['data']
    return ('<figure class="writing-figure" id="wenshu-figure-' + str(item['number']) + '"><img src="' + html.escape(url, quote=True)
            + '" alt="' + html.escape(placement.get('altText', ''), quote=True) + '" width="' + str(resource['width']) + '" height="' + str(resource['height'])
            + '"><figcaption>' + html.escape(_figure_caption(item)) + '<br><small>' + html.escape(_figure_credit(item)) + '</small></figcaption></figure>')


def _html_print_body(text, numbers, links=None):
    pieces, continued = [], False
    heading_pending = True
    for line in re.split(r'\r\n|\r|\n', text):
        level = body_heading_level(line)
        if level:
            pieces.append(f'<h{level + 2} class="body-heading level-{level}">' + _html_body(line, numbers, links) + f'</h{level + 2}>')
            heading_pending = True
        elif line.strip():
            cls = 'manuscript-line continuation' if continued else 'manuscript-line'
            pieces.append('<p class="' + cls + '">' + _html_body(line, numbers, links) + '</p>')
            heading_pending = False
        else:
            cls = 'blank-line after-heading' if heading_pending else 'blank-line'
            pieces.append('<p class="' + cls + '" aria-hidden="true">' + html.escape(line) + '</p>')
        continued = bool(line.strip()) and not level
    return '\n'.join(pieces)


def _html_section(section, numbers, figures, image_urls, print_layout=False, *, heading=None, lead_end=0, links=None):
    content, cursor, pieces = section.get('content', ''), 0, []
    body_renderer = _html_print_body if print_layout else _html_body
    events = [(figure['end'], 0, _html_figure(figure, image_urls)) for figure in figures]
    if heading is not None:
        events.append((lead_end, 1, '<h2>' + html.escape(heading) + '</h2>'))
    for end, _, markup in sorted(events, key=lambda event: event[:2]):
        if end > cursor:
            pieces.append('<div class="body">' + body_renderer(content[cursor:end], numbers, links) + '</div>')
        pieces.append(markup)
        cursor = max(cursor, end)
        # The figure already separates paragraphs. Preserved blank lines here
        # become line boxes and can satisfy PDF orphans before any body text.
        separator = re.match(r'\n\s*\n', content[cursor:])
        if separator:
            cursor += separator.end()
    if cursor < len(content) or not events:
        pieces.append('<div class="body">' + body_renderer(content[cursor:], numbers, links) + '</div>')
    return '\n'.join(pieces)


def _tag(name):
    return '{' + W + '}' + name


def _node(parent, tag_name, **attrs):
    return ET.SubElement(parent, _tag(tag_name), {_tag(key): str(value) for key, value in attrs.items()})


def _xml(root):
    # OOXML property children have a schema-defined order. Renderers differ in
    # how forgiving they are, especially for direct paragraph overrides.
    orders = {
        'pPr': 'pStyle keepNext keepLines pageBreakBefore framePr widowControl numPr suppressLineNumbers pBdr shd tabs suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct autoSpaceDE autoSpaceDN bidi adjustRightInd snapToGrid spacing ind contextualSpacing mirrorIndents suppressOverlap jc textDirection textAlignment textboxTightWrap outlineLvl divId cnfStyle rPr sectPr pPrChange',
        'rPr': 'rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof snapToGrid vanish webHidden color spacing w kern position sz szCs highlight u effect bdr shd fitText vertAlign rtl cs em lang eastAsianLayout specVanish oMath rPrChange',
    }
    for name, order in orders.items():
        positions = {_tag(tag): index for index, tag in enumerate(order.split())}
        for node in root.iter(_tag(name)):
            node[:] = sorted(node, key=lambda child: positions.get(child.tag, len(positions)))
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _mode(project, section):
    mode = project['mode'] if section['mode'] == 'inherit' else section['mode']
    return '严格模式' if mode == 'strict' else '扩展模式'


def _state(project, section):
    return _mode(project, section) + (' · 已锁定' if section.get('locked') else '') + (' · 人工修改' if section.get('manual') else '')


def _html_body(text, numbers, links=None):
    linked = set((numbers if links is None else links).values())
    pieces = []
    for part, number in citation_parts(text, numbers, links=links):
        if number is None:
            pieces.append(html.escape(part))
            continue
        label = '[' + str(number) + ']'
        if number in linked:
            label = ('<a href="#wenshu-ref-' + str(number) +
                     '" aria-label="参考文献 ' + str(number) + '">' + label + '</a>')
        pieces.append('<sup class="citation">' + label + '</sup>')
    return ''.join(pieces)


def _location(project, issue):
    section = next((s for s in project['sections'] if s['id'] == issue.get('sectionId')), None)
    label = section['title'] if section else '全文'
    if issue.get('paragraphIndex'):
        label += f' 第 {issue["paragraphIndex"]} 段'
    return label


def _count_text(item, total=False):
    actual = item['total'] if total else item['actual']
    return (f'{actual} 字，目标 {item["target"]} 字，建议范围 {item["min"]}–{item["max"]} 字'
            if item['target'] else f'{actual} 字，未设置目标')


def render_html(project, report, *, image_urls=None, print_layout=False):
    escape = html.escape
    citations = build_export_citation_plan(project, report)
    figures = image_export_plan(project)
    pieces = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
              '<title>' + escape(project['title']) + '</title><style>body{max-width:900px;margin:40px auto;padding:0 24px;font-family:system-ui,sans-serif;line-height:1.8;color:#222}p,.body{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#555}h2{margin-top:2em}h3{margin-top:1.5em}.reference{margin:16px 0}a{color:#80511d}.citation{vertical-align:super;font-size:.75em;line-height:0}.citation a{text-decoration:none}.reference{scroll-margin-top:24px}.writing-figure{margin:1.5em 0;break-inside:avoid;text-align:center}.writing-figure img{max-width:100%;height:auto;max-height:700px;object-fit:contain}.writing-figure figcaption{font-size:.9em;line-height:1.5;overflow-wrap:anywhere;white-space:pre-wrap}</style></head><body>',
              '<h1>' + escape(project['title']) + '</h1>']
    for number, section in enumerate(project['sections'], 1):
        heading = chapter_heading(number, section['title']) if print_layout else str(number) + '. ' + section['title']
        lead_end = introduction_end(section) if number == 1 else 0
        if not lead_end:
            pieces.append('<h2>' + escape(heading) + '</h2>')
        pieces.append(_html_section(section, citations['sectionNumbers'][number - 1],
                      [item for item in figures if item['sectionIndex'] == number - 1], image_urls, print_layout,
                      heading=heading if lead_end else None, lead_end=lead_end,
                      links=citations['sectionLinks'][number - 1]))
    if citations['references']:
        pieces.append('<h2 class="appendix">参考文献</h2>')
    for entry in citations['references']:
        number = entry['number']
        pieces.append('<div class="reference" id="wenshu-ref-' + str(number) + '"><p>[' + str(number) + '] ' + escape(_reference_title(entry['reference']['title'])) + '</p></div>')
    pieces.append('</body></html>')
    return '\n'.join(pieces)


def _text_runs(parent, text, *, superscript=False):
    for part in re.split(r'(\t|\r\n|\r|\n)', text):
        run = _node(parent, 'r')
        if superscript:
            properties = _node(run, 'rPr')
            _node(properties, 'sz', val='24')
            _node(properties, 'szCs', val='24')
            _node(properties, 'vertAlign', val='superscript')
        if part == '\t':
            _node(run, 'tab')
        elif part in ('\r\n', '\r', '\n'):
            _node(run, 'br')
        else:
            node = _node(run, 't')
            node.set('{' + XML + '}space', 'preserve')
            node.text = part


def _paragraph(body, text, style='Normal', *, keep=False, numbers=None, bookmark=None, continued=False, links=None):
    if INVALID_XML.search(text):
        raise ValueError('稿件或材料含 Word 不支持的控制字符，请人工处理后重新导出；原稿未改动')
    p = _node(body, 'p')
    properties = _node(p, 'pPr')
    _node(properties, 'pStyle', val=style)
    if continued:
        _node(properties, 'ind', firstLine='0', firstLineChars='0')
    if keep:
        _node(properties, 'keepNext')
    # Keep this paired with the modern compatibility setting and Latin language
    # below: this combination is verified with actual Word rendering. Word can
    # still wrap a single URL/identifier longer than the available line.
    _node(properties, 'wordWrap', val='1')
    if bookmark is not None:
        _node(p, 'bookmarkStart', id=bookmark, name='wenshu_ref_' + str(bookmark))
    linked = set(((numbers or {}) if links is None else links).values())
    for part, number in citation_parts(text, numbers or {}, links=links):
        if number is None:
            _text_runs(p, part)
        elif number in linked:
            link = _node(p, 'hyperlink', anchor='wenshu_ref_' + str(number), history='1')
            _text_runs(link, part, superscript=True)
        else:
            _text_runs(p, part, superscript=True)
    if bookmark is not None:
        _node(p, 'bookmarkEnd', id=bookmark)
    return p


def _styles():
    body_half_points, title_half_points = BODY_SIZE_PT * 2, TITLE_SIZE_PT * 2
    line_twips, indent_twips = str(round(LINE_PT * 20)), str(FIRST_LINE_PT * 20)
    root = ET.Element(_tag('styles'))
    defaults = _node(root, 'docDefaults')
    run = _node(_node(defaults, 'rPrDefault'), 'rPr')
    _node(run, 'rFonts', ascii='Times New Roman', hAnsi='Times New Roman', eastAsia='仿宋', cs='Times New Roman')
    _node(run, 'sz', val=body_half_points)
    _node(run, 'szCs', val=body_half_points)
    _node(run, 'color', val='000000')
    _node(run, 'lang', val='en-US', eastAsia='zh-CN')
    paragraph = _node(_node(defaults, 'pPrDefault'), 'pPr')
    _node(paragraph, 'spacing', before='0', after='0', line=str(round(LINE_PT * 20)), lineRule='exact')
    _node(paragraph, 'widowControl')
    for name, label, size, before, after, font, level in (
            ('Normal', 'Normal', body_half_points, 0, 0, '仿宋', None),
            ('Title', 'Title', title_half_points, 0, line_twips, '方正小标宋简体', None),
            ('Heading1', 'heading 1', body_half_points, 0, 0, '黑体', 0),
            ('Heading2', 'heading 2', body_half_points, 0, 0, '楷体', 1),
            ('Heading3', 'heading 3', body_half_points, 0, 0, '仿宋', 2),
            ('Heading4', 'heading 4', body_half_points, 0, 0, '仿宋', 3),
            ('AppendixHeading', 'Appendix Heading', body_half_points, 0, 0, '黑体', 0),
            ('Metadata', 'Metadata', 24, 0, 0, '仿宋', None),
            ('Source', 'Source', 28, 0, 0, '仿宋', None),
            ('BodyText', 'Body Text', body_half_points, 0, 0, '仿宋', None),
            ('Footer', 'Footer', 28, 0, 0, '宋体', None)):
        style = _node(root, 'style', type='paragraph', styleId=name)
        if name == 'Normal':
            style.set(_tag('default'), '1')
        _node(style, 'name', val=label)
        if name != 'Normal':
            _node(style, 'basedOn', val='Normal')
        _node(style, 'next', val='Normal')
        _node(style, 'qFormat')
        p = _node(style, 'pPr')
        if level is not None or name == 'Title':
            _node(p, 'keepNext')
            _node(p, 'keepLines')
        _node(p, 'spacing', before=before, after=after, line=line_twips if size >= body_half_points else '400', lineRule='exact')
        _node(p, 'ind', firstLine=indent_twips if name in ('Normal', 'BodyText') or level is not None else '0')
        if name == 'AppendixHeading':
            p.find(_tag('ind')).set(_tag('firstLine'), '0')
            _node(p, 'pageBreakBefore')
        _node(p, 'jc', val='center' if name == 'Title' else 'both' if name in ('Normal', 'BodyText') else 'left')
        if level is not None:
            _node(p, 'outlineLvl', val=level)
        r = _node(style, 'rPr')
        _node(r, 'rFonts', ascii='Times New Roman', hAnsi='Times New Roman', eastAsia=font, cs='Times New Roman')
        _node(r, 'sz', val=size)
        _node(r, 'szCs', val=size)
        _node(r, 'color', val='000000')
    return _xml(root)


def _font_table():
    root = ET.Element(_tag('fonts'))
    for name, fallback in (('仿宋', 'FandolFang'), ('黑体', 'FandolHei'), ('楷体', 'FandolKai'),
                           ('宋体', 'FandolSong'), ('方正小标宋简体', '宋体')):
        font = _node(root, 'font', name=name)
        _node(font, 'altName', val=fallback)
        _node(font, 'family', val='roman')
        _node(font, 'pitch', val='variable')
    return _xml(root)


def _footer(alignment):
    root = ET.Element(_tag('ftr'))
    paragraph = _node(root, 'p')
    properties = _node(paragraph, 'pPr')
    _node(properties, 'pStyle', val='Footer')
    _node(properties, 'spacing', before='0', after='0', line='400', lineRule='exact')
    _node(properties, 'ind', left='280', right='280', firstLine='0')
    _node(properties, 'jc', val=alignment)

    def run():
        result = _node(paragraph, 'r')
        props = _node(result, 'rPr')
        _node(props, 'rFonts', ascii='宋体', hAnsi='宋体', eastAsia='宋体')
        _node(props, 'sz', val='28')
        _node(props, 'color', val='000000')
        return result

    _node(run(), 't').text = '— '
    # PAGE recalculates during normal pagination. Do not request an explicit
    # field refresh on open, which can trigger Word's generic file-field prompt.
    _node(run(), 'fldChar', fldCharType='begin')
    instruction = _node(run(), 'instrText')
    instruction.set('{' + XML + '}space', 'preserve')
    instruction.text = ' PAGE '
    _node(run(), 'fldChar', fldCharType='separate')
    _node(run(), 't').text = '1'
    _node(run(), 'fldChar', fldCharType='end')
    _node(run(), 't').text = ' —'
    for text in root.iter(_tag('t')):
        text.set('{' + XML + '}space', 'preserve')
    return _xml(root)


def _docx_media(figures):
    media = {}
    for figure in figures:
        resource = figure['resource']
        digest = resource['sha256']
        if digest in media:
            continue
        raw, mime = resource['raw'], resource['mime']
        if mime in ('image/webp', 'image/gif'):
            from PIL import Image
            output = BytesIO()
            with Image.open(BytesIO(raw)) as picture:
                picture.seek(0)
                picture.convert('RGBA' if mime == 'image/gif' or 'A' in picture.getbands() else 'RGB').save(output, format='PNG')
            raw, mime = output.getvalue(), 'image/png'
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError('配图转换后超过单张导出容量，请调整图片后导出')
        number = len(media) + 1
        media[digest] = {'raw': raw, 'mime': mime, 'extension': IMAGE_TYPES[mime][1],
                         'name': f'image{number}.' + IMAGE_TYPES[mime][1], 'relationship': f'rIdImage{number}'}
    return media


def _docx_figure(body, figure, media):
    resource, placement = figure['resource'], figure['placement']
    cx, cy = resource['width'] * 9525, resource['height'] * 9525
    scale = min(1, BODY_WIDTH_MM * 36000 / cx, 160 * 36000 / cy)
    cx, cy = max(1, round(cx * scale)), max(1, round(cy * scale))
    paragraph = _node(body, 'p')
    properties = _node(paragraph, 'pPr')
    _node(properties, 'keepNext')
    _node(properties, 'keepLines')
    _node(properties, 'jc', val='center')
    _node(properties, 'ind', firstLine='0')
    # Inline pictures need their natural height, rather than the text grid.
    _node(properties, 'spacing', before='0', after='0', line='240', lineRule='auto')
    drawing = _node(_node(paragraph, 'r'), 'drawing')
    inline = ET.SubElement(drawing, '{' + WP + '}inline', distT='0', distB='0', distL='0', distR='0')
    ET.SubElement(inline, '{' + WP + '}extent', cx=str(cx), cy=str(cy))
    ET.SubElement(inline, '{' + WP + '}docPr', id=str(figure['number']), name=f'图 {figure["number"]}', descr=placement.get('altText', ''))
    frame = ET.SubElement(inline, '{' + WP + '}cNvGraphicFramePr')
    ET.SubElement(frame, '{' + A + '}graphicFrameLocks', noChangeAspect='1')
    graphic = ET.SubElement(inline, '{' + A + '}graphic')
    graphic_data = ET.SubElement(graphic, '{' + A + '}graphicData', uri=PIC)
    picture = ET.SubElement(graphic_data, '{' + PIC + '}pic')
    nv = ET.SubElement(picture, '{' + PIC + '}nvPicPr')
    ET.SubElement(nv, '{' + PIC + '}cNvPr', id='0', name=media[resource['sha256']]['name'])
    ET.SubElement(nv, '{' + PIC + '}cNvPicPr')
    fill = ET.SubElement(picture, '{' + PIC + '}blipFill')
    ET.SubElement(fill, '{' + A + '}blip', {'{' + R + '}embed': media[resource['sha256']]['relationship']})
    ET.SubElement(ET.SubElement(fill, '{' + A + '}stretch'), '{' + A + '}fillRect')
    shape = ET.SubElement(picture, '{' + PIC + '}spPr')
    transform = ET.SubElement(shape, '{' + A + '}xfrm')
    ET.SubElement(transform, '{' + A + '}off', x='0', y='0')
    ET.SubElement(transform, '{' + A + '}ext', cx=str(cx), cy=str(cy))
    ET.SubElement(ET.SubElement(shape, '{' + A + '}prstGeom', prst='rect'), '{' + A + '}avLst')
    caption = _paragraph(body, _figure_caption(figure) + '\n' + _figure_credit(figure), 'Metadata')
    caption_properties = caption.find(_tag('pPr'))
    _node(caption_properties, 'keepLines')
    _node(caption_properties, 'jc', val='center')


def _docx_section(body, section, numbers, figures, media, *, heading=None, lead_end=0, links=None):
    chunks, cursor = re.split(r'(\r\n|\r|\n)', section.get('content', '')), 0
    continued = False
    heading_pending = True
    for index in range(0, len(chunks), 2):
        line = chunks[index]
        level = body_heading_level(line)
        _paragraph(body, line, f'Heading{level}' if level else 'BodyText', numbers=numbers,
                   continued=continued and not level, keep=heading_pending and not line.strip(), links=links)
        if level:
            heading_pending = True
        elif line.strip():
            heading_pending = False
        continued = bool(line.strip()) and not level
        cursor += len(line)
        for figure in figures:
            if figure['end'] == cursor:
                _docx_figure(body, figure, media)
        if heading is not None and cursor == lead_end:
            _paragraph(body, heading, 'Heading1')
            continued, heading_pending = False, True
        if index + 1 < len(chunks):
            cursor += len(chunks[index + 1])


def render_docx(project, report):
    citations = build_export_citation_plan(project, report)
    figures = image_export_plan(project)
    media = _docx_media(figures)
    document = ET.Element(_tag('document'))
    body = _node(document, 'body')
    _paragraph(body, project['title'], 'Title')
    for number, section in enumerate(project['sections'], 1):
        heading = chapter_heading(number, section['title'])
        lead_end = introduction_end(section) if number == 1 else 0
        if not lead_end:
            _paragraph(body, heading, 'Heading1')
        # Preserve authored lines and spaces. All complete E-markers receive
        # display numbers; only resolved sources receive bibliography links.
        _docx_section(body, section, citations['sectionNumbers'][number - 1],
                      [item for item in figures if item['sectionIndex'] == number - 1], media,
                      heading=heading if lead_end else None, lead_end=lead_end,
                      links=citations['sectionLinks'][number - 1])

    if citations['references']:
        _paragraph(body, '参考文献', 'AppendixHeading')
    for entry in citations['references']:
        number = entry['number']
        paragraph = _paragraph(body, '[' + str(number) + '] ' + _reference_title(entry['reference']['title']), 'Source', bookmark=number)
        _node(paragraph.find(_tag('pPr')), 'keepLines')
    properties = _node(body, 'sectPr')
    _node(properties, 'footerReference', type='default', **{'id': 'rIdFooterOdd'})
    _node(properties, 'footerReference', type='even', **{'id': 'rIdFooterEven'})
    # Relationship attributes use the relationships namespace, not w:.
    for reference in properties.findall(_tag('footerReference')):
        reference.set('{' + R + '}id', reference.attrib.pop(_tag('id')))
    _node(properties, 'pgSz', w=mm_twips(PAGE_WIDTH_MM), h=mm_twips(PAGE_HEIGHT_MM))
    _node(properties, 'pgMar', top=mm_twips(MARGIN_TOP_MM), right=mm_twips(MARGIN_RIGHT_MM),
          bottom=mm_twips(MARGIN_BOTTOM_MM), left=mm_twips(MARGIN_LEFT_MM), header='0', footer=mm_twips(20), gutter='0')
    _node(properties, 'pgNumType', start='1')

    types = ET.Element('{' + CT + '}Types')
    for extension, mime in (('rels', 'application/vnd.openxmlformats-package.relationships+xml'), ('xml', 'application/xml')):
        ET.SubElement(types, '{' + CT + '}Default', Extension=extension, ContentType=mime)
    for extension, mime in sorted({(item['extension'], item['mime']) for item in media.values()}):
        ET.SubElement(types, '{' + CT + '}Default', Extension=extension, ContentType=mime)
    for part, mime in (
            ('/word/document.xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'),
            ('/word/styles.xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml'),
            ('/word/settings.xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml'),
            ('/word/fontTable.xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml'),
            ('/word/footerOdd.xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml'),
            ('/word/footerEven.xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml'),
            ('/docProps/core.xml', 'application/vnd.openxmlformats-package.core-properties+xml')):
        ET.SubElement(types, '{' + CT + '}Override', PartName=part, ContentType=mime)
    package_rels = ET.Element('{' + REL + '}Relationships')
    ET.SubElement(package_rels, '{' + REL + '}Relationship', Id='rId1', Type=R + '/officeDocument', Target='word/document.xml')
    ET.SubElement(package_rels, '{' + REL + '}Relationship', Id='rId2', Type='http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties', Target='docProps/core.xml')
    document_rels = ET.Element('{' + REL + '}Relationships')
    for number, name in enumerate(('styles', 'settings', 'fontTable'), 1):
        ET.SubElement(document_rels, '{' + REL + '}Relationship', Id=f'rId{number}', Type=R + '/' + name, Target=name + '.xml')
    for name in ('Odd', 'Even'):
        ET.SubElement(document_rels, '{' + REL + '}Relationship', Id='rIdFooter' + name, Type=R + '/footer', Target='footer' + name + '.xml')
    for item in media.values():
        ET.SubElement(document_rels, '{' + REL + '}Relationship', Id=item['relationship'], Type=R + '/image', Target='media/' + item['name'])
    settings = ET.Element(_tag('settings'))
    _node(settings, 'defaultTabStop', val='720')
    _node(settings, 'evenAndOddHeaders')
    _node(settings, 'characterSpacingControl', val='doNotCompress')
    compatibility = _node(settings, 'compat')
    _node(compatibility, 'compatSetting', name='compatibilityMode', uri='http://schemas.microsoft.com/office/word', val='15')
    core_ns, dc = 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties', 'http://purl.org/dc/elements/1.1/'
    core = ET.Element('{' + core_ns + '}coreProperties')
    ET.SubElement(core, '{' + dc + '}title').text = project['title']
    ET.SubElement(core, '{' + dc + '}creator').text = '文枢'
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for name, content in {'[Content_Types].xml': _xml(types), '_rels/.rels': _xml(package_rels),
                              'word/document.xml': _xml(document), 'word/_rels/document.xml.rels': _xml(document_rels),
                              'word/styles.xml': _styles(), 'word/settings.xml': _xml(settings), 'word/fontTable.xml': _font_table(), 'docProps/core.xml': _xml(core),
                              'word/footerOdd.xml': _footer('right'), 'word/footerEven.xml': _footer('left')}.items():
            archive.writestr(name, content)
        for item in media.values():
            archive.writestr('word/media/' + item['name'], item['raw'])
    return output.getvalue()
