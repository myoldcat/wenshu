"""Bounded, isolated PDF rendering of an already-frozen writing snapshot.

The API process only builds escaped, fixed-template HTML and starts a child.
WeasyPrint and native font libraries load in that child, after the transaction
has closed. No model, browser, external resource, or document write is involved.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
from string import Template
import sys
import tempfile
import threading

from .writing_export import MAX_TOTAL_IMAGE_BYTES, image_export_plan, raster_image_resource, render_html, validate_image_resource

from .writing_format import (
    PAGE_WIDTH_MM, PAGE_HEIGHT_MM, MARGIN_TOP_MM, MARGIN_BOTTOM_MM,
    MARGIN_LEFT_MM, MARGIN_RIGHT_MM, BODY_SIZE_PT, TITLE_SIZE_PT, LINE_PT, FIRST_LINE_PT,
)

PDF_TIMEOUT_SECONDS = 60
MAX_HTML_BYTES = 16 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_PDF_INPUT_BYTES = MAX_HTML_BYTES + (MAX_TOTAL_IMAGE_BYTES + 2) // 3 * 4 + 1024 * 1024
_PDF_SLOTS = threading.BoundedSemaphore(2)

PRINT_CSS = Template('''
@page {
  size: ${page_width}mm ${page_height}mm;
  margin: ${top}mm ${right}mm ${bottom}mm ${left}mm;
  @bottom-left { content: none; }
  @bottom-right { content: none; }
}
@page:right {
  @bottom-right {
    content: "— " counter(page) " —";
    font-family: "SimSun", "FandolSong", "Noto Serif CJK SC", serif;
    font-size: 14pt; line-height: 14pt; color: #000;
    vertical-align: top; padding-top: 7mm; margin-right: 14pt;
  }
}
@page:left {
  @bottom-left {
    content: "— " counter(page) " —";
    font-family: "SimSun", "FandolSong", "Noto Serif CJK SC", serif;
    font-size: 14pt; line-height: 14pt; color: #000;
    vertical-align: top; padding-top: 7mm; margin-left: 14pt;
  }
}
html { font-family: "FangSong", "FandolFang", "Noto Serif CJK SC", serif; font-size: ${body_size}pt; color: #000; }
body { margin: 0; padding: 0; max-width: none; line-height: ${line}pt; }
h1, h2, h3, h4, h5, h6 {
  color: #000; line-height: ${line}pt; margin: 0;
  break-after: avoid; page-break-after: avoid;
  overflow-wrap: anywhere; word-break: normal;
  font-weight: normal;
}
h1 {
  font-family: "方正小标宋简体", "FZXiaoBiaoSong-B05S", "FandolSong", "Noto Serif CJK SC", serif;
  font-size: ${title_size}pt; font-weight: bold; text-align: center;
  margin: 0 0 ${line}pt; text-indent: 0;
}
h2, .level-1 { font-family: "SimHei", "FandolHei", "Noto Sans CJK SC", sans-serif; font-size: ${body_size}pt; text-indent: ${indent}pt; }
.level-2 { font-family: "KaiTi", "FandolKai", "Noto Serif CJK SC", serif; font-size: ${body_size}pt; text-indent: ${indent}pt; }
.level-3, .level-4 { font-family: "FangSong", "FandolFang", "Noto Serif CJK SC", serif; font-size: ${body_size}pt; text-indent: ${indent}pt; }
p, .body {
  word-break: normal; overflow-wrap: anywhere; hyphens: none;
  orphans: 3; widows: 3;
}
p { margin: 0; white-space: pre-wrap; }
.body { margin: 0; white-space: normal; tab-size: 4; }
.manuscript-line { text-indent: ${indent}pt; text-align: justify; line-height: ${line}pt; }
.manuscript-line.continuation { text-indent: 0; }
.blank-line { height: ${line}pt; line-height: ${line}pt; }
.blank-line.after-heading { break-after: avoid; page-break-after: avoid; }
.body-heading { white-space: pre-wrap; }
.citation { vertical-align: super; font-size: 75%; line-height: 0; }
.citation a { color: #000; text-decoration: none; }
small { font-size: 12pt; color: #000; line-height: 20pt; }
.appendix { break-before: page; page-break-before: always; margin-top: 0; text-indent: 0; }
.reference { margin: 0; break-inside: avoid; page-break-inside: avoid; }
.reference p { font-size: 14pt; line-height: 20pt; text-indent: 0; }
.writing-figure { margin: 5mm 0; text-align: center; break-inside: avoid; page-break-inside: avoid; }
.writing-figure img { max-width: 100%; height: auto; max-height: 160mm; object-fit: contain; }
.writing-figure figcaption { font-size: 12pt; line-height: 20pt; white-space: pre-wrap; overflow-wrap: anywhere; }
''').substitute(
    page_width=PAGE_WIDTH_MM, page_height=PAGE_HEIGHT_MM,
    top=MARGIN_TOP_MM, right=MARGIN_RIGHT_MM, bottom=MARGIN_BOTTOM_MM, left=MARGIN_LEFT_MM,
    body_size=BODY_SIZE_PT, title_size=TITLE_SIZE_PT, line=LINE_PT, indent=FIRST_LINE_PT,
)


class PDFExportError(Exception):
    """Public failure reason that contains no renderer output or source text."""

    def __init__(self, code, message, status_code=503):
        super().__init__(message)
        self.status_code = status_code
        self.detail = {'code': code, 'message': message}


class PDFResourceDenied(ValueError):
    """All resources outside the frozen raster whitelist are prohibited."""


def deny_url_fetcher(url, headers=None):
    # Never include the URL in an exception: it may contain private file names,
    # query credentials, or author text even though no request was attempted.
    raise PDFResourceDenied('PDF external resources are disabled')


def frozen_url_fetcher(url, resources):
    """Exact opaque keys only: no parsing, URL decoding, paths or networking."""
    if not isinstance(url, str) or url not in resources:
        return deny_url_fetcher(url)
    resource = resources[url]
    return {'string': resource['raw'], 'mime_type': resource['mime'], 'redirected_url': url}


def _frozen_image_resources(project):
    resources = {}
    for item in image_export_plan(project):
        resource = item['resource']
        resources['wenshu-image:' + resource['sha256']] = {key: resource[key] for key in ('data', 'mime', 'width', 'height', 'sha256')}
    return resources


def build_pdf_html(project, report):
    """Reuse the escaped export body and replace only our fixed stylesheet."""
    urls = {item['resource']['sha256']: 'wenshu-image:' + item['resource']['sha256'] for item in image_export_plan(project)}
    source = render_html(project, report, image_urls=urls, print_layout=True)
    prefix, marker, rest = source.partition('<style>')
    _, closing, suffix = rest.partition('</style>')
    if not marker or not closing:
        raise PDFExportError('pdf_failed', 'PDF 导出暂不可用，请稍后重试；原稿保持不变')
    return prefix + '<style>' + PRINT_CSS + '</style>' + suffix


def render_pdf(project, report):
    if not _PDF_SLOTS.acquire(blocking=False):
        raise PDFExportError('pdf_busy', 'PDF 导出任务较多，请稍后重试；原稿保持不变')
    try:
        html = build_pdf_html(project, report)
        if len(html.encode('utf-8')) > MAX_HTML_BYTES:
            raise PDFExportError('pdf_too_large', '当前稿件及材料超过单次 PDF 导出容量，请分篇导出；原稿保持不变')
        resources = _frozen_image_resources(project)
        source = (json.dumps({'version': 1, 'html': html, 'images': resources}, ensure_ascii=False).encode('utf-8')
                  if resources else html.encode('utf-8'))
        if len(source) > MAX_PDF_INPUT_BYTES:
            raise PDFExportError('pdf_too_large', '当前稿件及配图超过单次 PDF 导出容量，请分篇导出；原稿保持不变')
        # The parent owns cleanup, including after a timeout kills the child.
        # stdin carries the text, so neither argv nor a shared file exposes it.
        try:
            with tempfile.TemporaryDirectory(prefix='wenshu-pdf-') as temporary:
                env = os.environ.copy()
                env.update({'TMPDIR': temporary, 'TEMP': temporary, 'TMP': temporary,
                            'XDG_CACHE_HOME': temporary, 'PYTHONIOENCODING': 'utf-8'})
                if getattr(sys, 'frozen', False):
                    from desktop.runtime import pdf_command
                    command = pdf_command()
                else:
                    command = [sys.executable, '-m', 'server.writing_pdf']
                process_options = ({'creationflags': subprocess.CREATE_NO_WINDOW}
                                   if os.name == 'nt' else {})
                result = subprocess.run(
                    command, input=source,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    cwd=Path(__file__).resolve().parents[1], env=env,
                    timeout=PDF_TIMEOUT_SECONDS, check=False, **process_options)
        except subprocess.TimeoutExpired:
            raise PDFExportError('pdf_timeout', 'PDF 排版超时，请稍后重试或分篇导出；原稿保持不变', 504) from None
        except OSError:
            raise PDFExportError('pdf_failed', 'PDF 导出服务暂不可用，请稍后重试；原稿保持不变') from None
        content = result.stdout
        if (result.returncode != 0 or not isinstance(content, bytes) or
                not content.startswith(b'%PDF-') or not content.rstrip().endswith(b'%%EOF') or
                len(content) > MAX_PDF_BYTES):
            raise PDFExportError('pdf_failed', 'PDF 排版未完成，请稍后重试或分篇导出；原稿保持不变')
        return content
    finally:
        _PDF_SLOTS.release()


def _process_limits():
    if sys.platform != 'linux':
        return
    import resource
    # These limits apply only to the disposable conversion process. The API
    # thread, database, and other exports are unaffected by a failed conversion.
    memory = 1536 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (PDF_TIMEOUT_SECONDS, PDF_TIMEOUT_SECONDS + 1))


def _render_in_child(source, image_resources=None):
    from weasyprint import HTML
    from weasyprint.urls import FatalURLFetchingError, URLFetcher, URLFetcherResponse

    validated = {}
    total = 0
    for url, resource in (image_resources or {}).items():
        image = validate_image_resource(resource)
        if url != 'wenshu-image:' + image['sha256']:
            raise PDFResourceDenied('PDF image whitelist is invalid')
        total += len(image['raw'])
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise PDFResourceDenied('PDF image whitelist is too large')
        validated[url] = raster_image_resource(image)

    fetched = set()

    class FrozenImageFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            try:
                resource = frozen_url_fetcher(url, validated)
                fetched.add(url)
                return URLFetcherResponse(url, resource['string'], {'Content-Type': resource['mime_type']})
            except PDFResourceDenied:
                raise FatalURLFetchingError('PDF external resources are disabled') from None

    logging.getLogger('weasyprint').disabled = True
    fetcher = FrozenImageFetcher(allowed_protocols=('wenshu-image',), allow_redirects=False, fail_on_errors=True)
    # Fontconfig resolves only the installed font families in PRINT_CSS. There
    # are no @font-face URLs, attachments, scripts, or user-authored HTML tags.
    content = HTML(string=source, url_fetcher=fetcher, media_type='print').write_pdf(
        pdf_tags=True, pdf_forms=False, attachments=None, custom_metadata=False)
    if fetched != set(validated):
        raise PDFResourceDenied('PDF did not include every frozen image')
    return content


def main():
    try:
        if os.name == 'nt' and (getattr(sys, 'frozen', False) or os.environ.get('WENSHU_DESKTOP_CONTROL_DIR')):
            from desktop.runtime import configure_runtime
            configure_runtime()
        _process_limits()
        raw = sys.stdin.buffer.read(MAX_PDF_INPUT_BYTES + 1)
        if not raw or len(raw) > MAX_PDF_INPUT_BYTES:
            raise ValueError('Invalid PDF input size')
        source, images = raw.decode('utf-8'), {}
        if source.startswith('{'):
            payload = json.loads(source)
            if payload.get('version') != 1 or not isinstance(payload.get('html'), str) or not isinstance(payload.get('images'), dict) or len(payload['images']) > 40:
                raise ValueError('Invalid PDF resource envelope')
            source, images = payload['html'], payload['images']
        if len(source.encode('utf-8')) > MAX_HTML_BYTES:
            raise ValueError('Invalid PDF HTML size')
        content = _render_in_child(source, images)
        if len(content) > MAX_PDF_BYTES:
            raise ValueError('PDF output size limit')
        sys.stdout.buffer.write(content)
        sys.stdout.buffer.flush()
    except BaseException:
        # The HTTP process discards this channel. Never print a traceback,
        # rendered HTML, resource URL, user text, or native-library path.
        sys.stderr.write('PDF conversion failed.\n')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
