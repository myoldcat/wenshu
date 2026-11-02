"""Render synthetic manuscripts only; never open the application database."""
import copy
import io
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.writing_export import render_docx
from server.writing_pdf import render_pdf


def main():
    output = Path(__file__).resolve().parents[1] / 'output/official-format/sample'
    output.mkdir(parents=True, exist_ok=True)
    project = {
        'id': 'synthetic-format-sample', 'title': '文枢文稿导出排版样例', 'version': 1,
        'mode': 'strict', 'sources': [], 'illustrations': [], 'sections': [],
    }
    opening = (
        '本文件用于检查文枢导出文稿的纸张、字体、字号、段落、标题和页码。所有内容均为排版测试文字，不涉及真实文稿。'
        '\n\n（一）段落与层次\n\n'
        '正文使用三号仿宋，首行缩进两个汉字，换行后顶格。导出仅调整呈现形式，不改变已经保存的文章内容。'
        '\n同一自然段内主动换行时，这一行保持顶格。'
        '\n\n1.中英文混排\n\n'
        '中文与 English words、2026年10月25日、A4 等内容共同出现时，应保持完整和清晰。测试字符 <example> & $2.50 按原文呈现。'
        '\n\n（1）版面检查\n\n'
        '检查标题与后文是否同页，页码是否位于版心外，长段是否自然分页。'
    )
    for number, (title, content) in enumerate((
        ('排版示例', opening),
        ('连续正文与分页', '\n\n'.join(
            f'第{index + 1}段：' + '本文为受控排版样例。正文应在页面内自然换行，段落保持完整，页面边缘不应出现文字截断。'
            for index in range(14))),
    ), 1):
        project['sections'].append({'id': f'sample-{number}', 'title': title, 'mode': 'inherit',
                                    'content': content, 'evidence': [], 'version': 1, 'locked': True, 'manual': True})
    report = {'version': 1, 'issues': [], 'counts': {'errors': 0, 'warnings': 0},
              'status': 'rules_passed', 'notice': '测试核对记录不可混入导出正文'}
    before = copy.deepcopy((project, report))
    (output / 'manuscript.docx').write_bytes(render_docx(project, report))
    if '--docx-only' in sys.argv:
        assert before == (project, report)
        print(json.dumps({'synthetic': True, 'docx': str(output / 'manuscript.docx')}))
        return
    from pypdf import PdfReader
    pdf = render_pdf(project, report)
    (output / 'manuscript.pdf').write_bytes(pdf)
    reader = PdfReader(io.BytesIO(pdf))
    # Remove only the generated footer from each controlled sample page before
    # comparing a section that may span several pages.
    extracted = '\n'.join(re.sub(r'—\s*' + str(number) + r'\s*—', '', page.extract_text() or '')
                          for number, page in enumerate(reader.pages, 1))
    compact = lambda value: re.sub(r'\s+', '', value)
    for section in project['sections']:
        assert compact(section['content']) in compact(extracted)
    assert report['notice'] not in compact(extracted)
    assert before == (project, report)
    fonts = set()
    for page in reader.pages:
        for font in page['/Resources'].get('/Font', {}).values():
            fonts.add(str(font.get_object().get('/BaseFont')))
    result = {'synthetic': True, 'modelCalls': 0, 'databaseAccess': False,
              'pages': len(reader.pages), 'fonts': sorted(fonts), 'exactText': True,
              'snapshotUnchanged': True, 'pdfBytes': len(pdf)}
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
