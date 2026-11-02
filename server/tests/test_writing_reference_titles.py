"""Export-only title normalization from synthetic, fixed-version references."""
import copy
import io
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from lxml import html

from server.tests.test_writing_export_citation_display import (
    inspect_export, reference, section, snapshot, text,
)
from server.tests.test_writing_export import (
    export, fixture_change, latest, manuscript, preserved,
)
from server.writing_export import render_docx, render_html


TITLES = [
    ('臺灣網絡安全與資料治理', '台湾网络安全与资料治理'),
    ('香港資安 report 與AI發展2026', '香港资安 report 与AI发展2026'),
    ('已是简体资料', '已是简体资料'),
    ('English Research & AI <2026>', 'English Research & AI <2026>'),
    ('資訊安全 <script>標題 & "引文"</script>', '资讯安全 <script>标题 & "引文"</script>'),
]


@pytest.mark.parametrize('format', ['html', 'pdf-input', 'docx'])
def test_export_shows_only_simplified_reference_titles_and_preserves_snapshot(format):
    chapters = []
    for index, (original, _) in enumerate(TITLES):
        ref = reference(source=f'source-{index}', revision=f'revision-{index}')
        ref['title'] = original
        chapters.append(section(f'chapter-{index}', '正文保留「臺灣與網絡」[E1]。', [ref]))
    project, report = snapshot(chapters)
    before = copy.deepcopy((project, report))

    values, links, references = inspect_export(project, report, format)
    assert values == [f'[{number}]' for number in range(1, len(TITLES) + 1)]
    assert links == [(f'[{number}]', f'#wenshu-ref-{number}') for number in range(1, len(TITLES) + 1)]
    # Exact entry equality also excludes appended original titles and details.
    assert references == [(f'wenshu-ref-{number}', f'[{number}] {simplified}')
                          for number, (_, simplified) in enumerate(TITLES, 1)]
    if format == 'docx':
        with ZipFile(io.BytesIO(render_docx(project, report))) as archive:
            rendered = text(ET.fromstring(archive.read('word/document.xml')))
    else:
        rendered = html.fromstring(render_html(project, report, print_layout=format == 'pdf-input')).text_content()
    assert '正文保留「臺灣與網絡」' in rendered
    assert (project, report) == before


@pytest.mark.parametrize('format', ['html', 'pdf-input', 'docx'])
def test_simplified_titles_do_not_merge_sources_or_versions(format):
    first = reference('E1', 'same-source', 'old-revision', 'old-block')
    second_excerpt = reference('E2', 'same-source', 'old-revision', 'other-block')
    newer = reference('E3', 'same-source', 'new-revision', 'new-block')
    other = reference('E4', 'other-source', 'other-revision', 'other-block')
    for ref in (first, second_excerpt, newer):
        ref['title'] = '資料與發展'
    other['title'] = '资料与发展'
    project, report = snapshot([section('one', '首引[E1]，另一摘录[E2]，新版[E3]，同名资料[E4]，再引[E1]。',
                                       [first, second_excerpt, newer, other])])
    before = copy.deepcopy((project, report))
    values, links, references = inspect_export(project, report, format)
    assert values == ['[1]', '[1]', '[2]', '[3]', '[1]']
    assert links == [(f'[{number}]', f'#wenshu-ref-{number}') for number in (1, 1, 2, 3, 1)]
    assert references == [(f'wenshu-ref-{number}', f'[{number}] 资料与发展') for number in (1, 2, 3)]
    assert (project, report) == before


def test_real_pdf_exports_only_simplified_titles_without_changing_saved_records(manuscript):
    from pypdf import PdfReader

    def change(project):
        for chapter, (original, _) in zip(project['sections'], TITLES):
            chapter['evidence'][0]['title'] = original

    before = fixture_change(manuscript, change)
    history = manuscript.service.history(manuscript.project_id)
    originals = [manuscript.store.detail(ref['sourceId']) for ref in manuscript.references]
    response = export(manuscript, format='pdf', allow_issues=True)
    assert response.status_code == 200, response.text[:1000]
    reader = PdfReader(io.BytesIO(response.content), strict=True)
    extracted = ''.join(''.join(page.extract_text().split()) for page in reader.pages)
    tail = extracted.split('参考文献', 1)[1]
    for number, (original, simplified) in enumerate(TITLES[:2], 1):
        assert f'[{number}]' + ''.join(simplified.split()) in tail
        assert ''.join(original.split()) not in extracted
    assert {'wenshu-ref-1', 'wenshu-ref-2'} <= reader.named_destinations.keys()
    assert preserved(latest(manuscript)) == preserved(before)
    assert manuscript.service.history(manuscript.project_id) == history
    assert [manuscript.store.detail(ref['sourceId']) for ref in manuscript.references] == originals
