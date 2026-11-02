"""Produce and inspect a synthetic illustrated export; never access the user library.

Generate: python scripts/qa-writing-image-export.py --output output/writing-images/export-qa
Inspect copied PDF locally: python scripts/qa-writing-image-export.py --inspect-only PATH --output DIR
The repository and output paths do not depend on the process working directory.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def compact(value):
    return re.sub(r'\s+', '', value)


def inspect(pdf_path, output, expected):
    from pypdf import PdfReader
    raw = pdf_path.read_bytes()
    reader = PdfReader(io.BytesIO(raw))
    result = {'pdf': str(pdf_path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
              'pages': len(reader.pages), 'embeddedImages': sum(len(page.images) for page in reader.pages),
              'textExtractors': {}, 'renderedPages': [], 'visualReview': 'pending'}
    result['paintedImages'] = sum(1 for page in reader.pages for operands, operator in page.get_contents().operations
                                  if operator == b'Do' and page['/Resources']['/XObject'][operands[0]].get_object().get('/Subtype') == '/Image')
    texts = {'pypdf': '\n'.join(page.extract_text() or '' for page in reader.pages)}
    executable = shutil.which('pdftotext')
    if executable:
        command = subprocess.run([executable, '-layout', '-enc', 'UTF-8', str(pdf_path), '-'], capture_output=True, timeout=60, check=True)
        texts['pdftotext'] = command.stdout.decode('utf-8')
    if importlib.util.find_spec('pdfplumber'):
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            texts['pdfplumber'] = '\n'.join(page.extract_text() or '' for page in pdf.pages)
    if importlib.util.find_spec('fitz'):
        import fitz
        with fitz.open(pdf_path) as pdf:
            texts['pymupdf'] = '\n'.join(page.get_text() for page in pdf)
    if importlib.util.find_spec('pypdfium2'):
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(pdf_path) as pdf:
            texts['pdfium'] = '\n'.join(page.get_textpage().get_text_bounded() for page in pdf)
    result['expectedChecksAvailable'] = bool(expected)
    for name, text in texts.items():
        (output / f'{name}-text.txt').write_text(text, encoding='utf-8')
        result['textExtractors'][name] = {'expectedPresent': {item: item in compact(text) for item in expected},
                                          'replacementCharacters': text.count('\ufffd')}
    fonts = []
    for page_number, page in enumerate(reader.pages, 1):
        page_fonts = page['/Resources'].get('/Font') or {}
        if hasattr(page_fonts, 'get_object'):
            page_fonts = page_fonts.get_object()
        for label, reference in page_fonts.items():
            font = reference.get_object()
            cmap = font.get('/ToUnicode')
            font_record = {'page': page_number, 'label': label, 'baseFont': str(font.get('/BaseFont')), 'subtype': str(font.get('/Subtype')),
                           'encoding': str(font.get('/Encoding')), 'hasToUnicode': cmap is not None}
            if cmap is not None:
                filename = f'page-{page_number}-font-{label.lstrip("/")}-ToUnicode.txt'
                (output / filename).write_bytes(cmap.get_object().get_data())
                font_record['cmapFile'] = filename
            fonts.append(font_record)
    result['fonts'] = fonts
    renderer = shutil.which('pdftoppm')
    if renderer:
        prefix = output / 'page'
        subprocess.run([renderer, '-scale-to', '1600', '-png', str(pdf_path), str(prefix)], capture_output=True, timeout=120, check=True)
        result['renderedPages'] = [str(path) for path in sorted(output.glob('page-*.png'))]
    else:
        result['renderingUnavailable'] = 'pdftoppm is not installed in this environment'
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'output/writing-images/export-qa')
    parser.add_argument('--inspect-only', type=Path)
    parser.add_argument('--expected-text', type=Path, help='JSON array of exact expected text excerpts')
    parser.add_argument('--include-unsupported-glyph', action='store_true', help='Reproduce the original emoji font/CMap boundary')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    expected = []
    if args.inspect_only:
        pdf_path = args.inspect_only.resolve()
    else:
        from server import writing_export, writing_pdf
        from server.tests.test_writing_image_export import illustrated, pdf_illustrated
        project, report = illustrated.__wrapped__()
        if not args.include_unsupported_glyph:
            project, report = pdf_illustrated.__wrapped__((project, report))
        expected = ['首段🙂。同段下一行。' if args.include_unsupported_glyph else '首段。同段下一行。',
                    '第二段。', '后章正文。', '图1图像说明1', '图2图像说明2', '原始资料<来源>']
        pdf_path = output / 'illustrated-export.pdf'
        pdf_path.write_bytes(writing_pdf.render_pdf(project, report))
        (output / 'illustrated-export.docx').write_bytes(writing_export.render_docx(project, report))
        (output / 'illustrated-export.html').write_text(writing_export.render_html(project, report), encoding='utf-8')
        (output / 'pdf-input.html').write_text(writing_pdf.build_pdf_html(project, report), encoding='utf-8')
        (output / 'expected-text.json').write_text(json.dumps({'sections': project['sections'], 'illustrations': project['illustrations']}, ensure_ascii=False, indent=2), encoding='utf-8')
        (output / 'expected-excerpts.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding='utf-8')
    expected_path = args.expected_text or pdf_path.with_name('expected-excerpts.json')
    if expected_path.exists():
        expected = json.loads(expected_path.read_text(encoding='utf-8'))
        if not isinstance(expected, list) or any(not isinstance(item, str) for item in expected):
            raise ValueError('Expected excerpts must be a JSON array of strings')
    result = inspect(pdf_path, output, expected)
    (output / 'inspection.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
