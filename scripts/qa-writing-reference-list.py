"""Synthetic reference-list exports with no database access or model calls."""
import copy
import json
from pathlib import Path
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.writing_export import render_docx, render_html
from server.writing_pdf import render_pdf


def main():
    output = Path(__file__).resolve().parents[1] / 'output/reference-list/sample'
    output.mkdir(parents=True, exist_ok=True)
    titles = ['新一代信息技术与产业创新发展研究', '公共数据治理与安全管理实践']
    references = [dict(id=eid, sourceId=f'synthetic-source-{index}', revisionId=f'synthetic-revision-{index}',
                       blockId=f'synthetic-block-{index}', start=0, end=len(quote), quote=quote,
                       title=title, url=f'https://example.test/synthetic-{index}')
                  for index, (eid, title, quote) in enumerate(zip(
                      ['E4', 'E9'], titles, ['不应进入文件的受控原文摘录甲', '不应进入文件的受控原文摘录乙']), 1)]
    unused = {**references[0], 'id': 'E8', 'sourceId': 'synthetic-unused', 'title': '未实际引用的资料'}
    project = {
        'id': 'synthetic-reference-list', 'version': 1, 'title': '参考引用列表排版样例', 'mode': 'strict',
        'sources': [dict(sourceId=ref['sourceId'], revisionId=ref['revisionId']) for ref in [*references, unused]],
        'illustrations': [],
        'sections': [
            dict(id='s1', title='编号与标题', mode='inherit', version=1, locked=True, manual=True,
                 content='本样例使用两篇受控资料测试引用呈现。[E9][E4]\n\n再次引用第一篇资料时保留相同编号。[E9]',
                 evidence=[*references, unused]),
            dict(id='s2', title='跨章节复用', mode='inherit', version=1, locked=True, manual=True,
                 content='本章继续引用同一固定版本资料。[E1]', evidence=[{**references[1], 'id': 'E1'}]),
        ],
    }
    report = dict(version=1, issues=[], counts=dict(errors=0, warnings=0), status='rules_passed')
    before = copy.deepcopy((project, report))
    docx = render_docx(project, report)
    source = render_html(project, report)
    (output / 'manuscript.docx').write_bytes(docx)
    (output / 'manuscript.html').write_text(source, encoding='utf-8')
    expected = ['[1] ' + titles[1], '[2] ' + titles[0]]
    with ZipFile(BytesIO(docx)) as package:
        root = ET.fromstring(package.read('word/document.xml'))
        ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        paragraphs = [''.join(node.itertext()) for node in root.iter(ns + 'p')]
        assert paragraphs[paragraphs.index('参考文献') + 1:] == expected
    for ref in [*references, unused]:
        assert ref['quote'] not in source and ref['sourceId'] not in source and ref['url'] not in source
    assert unused['title'] not in source
    if '--docx-only' not in sys.argv:
        from pypdf import PdfReader
        pdf = render_pdf(project, report)
        (output / 'manuscript.pdf').write_bytes(pdf)
        reader = PdfReader(BytesIO(pdf))
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        for ref in [*references, unused]:
            assert ref['quote'] not in text and ref['sourceId'] not in text and ref['url'] not in text
        assert unused['title'] not in text
        assert all(item.replace(' ', '') in ''.join(text.split()) for item in expected)
    assert before == (project, report)
    result = dict(synthetic=True, modelCalls=0, databaseAccess=False, references=expected,
                  bibliographyOnlyNumberAndTitle=True, unusedOmitted=True, snapshotUnchanged=True)
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
