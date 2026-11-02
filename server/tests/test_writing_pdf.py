"""Real PDF extraction and adversarial export checks for the isolated runtime.

The controlled fixture has no model access. Text extraction verifies that the
manuscript and compact reference titles survive conversion; fixed evidence
details stay in the saved snapshot. Page-image inspection remains separate.
"""
import io
import re
import subprocess
from types import SimpleNamespace

import pytest
from lxml import html as html_tree
from pypdf import PdfReader

from server import writing_pdf
from server.writing_citations import REFERENCE_INTRO
from server.tests.test_writing_export import (
    export, fixture_change, html_reference_entries, latest, manuscript, preserved, review,
)


PRIVATE_ERROR = 'private-converter-stderr-and-manuscript-must-not-be-exposed'


def compact(value):
    return re.sub(r'\s+', '', value)


def read_pdf(response):
    assert response.status_code == 200, response.text[:1000]
    assert response.headers['content-type'].split(';')[0] == 'application/pdf'
    assert response.content.startswith(b'%PDF-') and response.content.rstrip().endswith(b'%%EOF')
    reader = PdfReader(io.BytesIO(response.content), strict=True)
    assert len(reader.pages) >= 1 and not reader.is_encrypted
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    assert text.strip() and '\ufffd' not in text
    return reader, text


def assert_no_pdf_actions(reader):
    root = reader.trailer['/Root']
    assert '/OpenAction' not in root and '/AA' not in root
    assert '/JavaScript' not in (root.get('/Names') or {})
    for page in reader.pages:
        assert '/AA' not in page
        for annotation in page.get('/Annots', []):
            value = annotation.get_object()
            action = value.get('/A', {})
            assert action.get('/S') not in ('/JavaScript', '/Launch', '/SubmitForm', '/GoToR')
            assert not str(action.get('/URI', '')).lower().startswith(('file:', 'javascript:', 'data:'))


def test_real_pdf_preserves_chinese_body_and_references_without_review_appendix(manuscript):
    env = manuscript
    before, history = latest(env), env.service.history(env.project_id)
    report = review(env)
    response = export(env, format='pdf')
    reader, text = read_pdf(response)
    normalized = compact(text)
    assert response.headers['x-wenshu-project-version'] == str(before['version'])
    assert response.headers['x-wenshu-review-status'] == 'rules_passed'
    assert 'attachment;' in response.headers['content-disposition'] and '.pdf' in response.headers['content-disposition']
    assert latest(env)['capabilities']['pdfExport'] is True
    for number, section in enumerate(before['sections'], 1):
        assert compact(section['title']) in normalized and compact(section['content'].replace('[E1]', f'[{number}]')) in normalized
        reference = section['evidence'][0]
        for key in ('quote', 'sourceId', 'revisionId', 'blockId', 'url'):
            assert compact(reference[key]) not in normalized, key
    assert '[1]材料1' in normalized and '[2]材料2' in normalized
    assert '参考文献' in normalized and '全文校验记录' not in normalized
    reference_tail = text.split('参考文献', 1)[1]
    assert compact(re.sub(r'—\s*\d+\s*—', '', reference_tail)) == '[1]材料1[2]材料2'
    assert compact(REFERENCE_INTRO) not in normalized
    assert {'wenshu-ref-1', 'wenshu-ref-2'} <= reader.named_destinations.keys()
    assert compact(report['notice']) not in normalized
    assert '建议范围' not in normalized
    for page in reader.pages:
        assert 594 <= float(page.mediabox.width) <= 596
        assert 841 <= float(page.mediabox.height) <= 843
    assert_no_pdf_actions(reader)
    assert preserved(latest(env)) == preserved(before)
    assert env.service.history(env.project_id) == history


def test_manual_locked_pdf_requires_explicit_draft_and_retains_pending_text(manuscript):
    env = manuscript
    project = latest(env)
    content = '  人工正文不能被导出改写 [E1]\n\n【待核实】新增意见需核实 [E1]  '
    project = env.service.save_section(env.project_id, project['sections'][0]['id'], project['version'], {'content': content})
    project = env.service.save_section(env.project_id, project['sections'][0]['id'], project['version'], {'locked': True})
    history = env.service.history(env.project_id)
    denied = export(env, format='pdf')
    assert denied.status_code == 409 and denied.json()['detail']['code'] == 'review_issues'
    response = export(env, format='pdf', allow_issues=True)
    _, text = read_pdf(response)
    # Editing invalidates support. Display uses numeric superscripts while
    # preserving the unresolved review and withholding source links.
    assert compact(content.replace('[E1]', '[1]')) in compact(text)
    assert '[E1]' not in text
    assert '未分配参考文献编号' not in compact(text)
    assert '【待核实】' in text and '已锁定' not in text and '人工修改' not in text
    assert '草稿' not in text and '全文校验记录' not in text
    assert '本段包含待核实或待核验内容' not in compact(text)
    assert response.headers['x-wenshu-review-status'] == 'needs_attention'
    assert preserved(latest(env)) == preserved(project)
    assert env.service.history(env.project_id) == history


@pytest.mark.parametrize('code,requires_draft', [
    ('section_word_count', False),
    ('total_word_count', False),
    ('source_new_revision', False),
    ('image_caption_missing', False),
    ('images_unconfirmed', False),
    ('pending_content', True),
    ('unknown_future_check', True),
])
def test_pdf_html_omits_issue_policy_labels_and_reminders(manuscript, code, requires_draft):
    env = manuscript
    project = latest(env)
    report = review(env)
    # Model a stored report whose derived export flag predates or disagrees
    # with the current policy. Neither labels nor findings enter the PDF body.
    report.update(issues=[{'code': code, 'severity': 'warning', 'message': '仍须阅读此项提醒'}],
                  counts={'errors': 0, 'warnings': 1}, status='needs_attention',
                  requiresDraftConsent=not requires_draft)
    source = writing_pdf.build_pdf_html(project, report)
    assert '<p>文枢写作草稿 · 版本 ' not in source and '<p>文枢写作稿件 · 版本 ' not in source
    assert '仍须阅读此项提醒' not in source and report['notice'] not in source
    assert '全文校验记录' not in source and '本次规则检查通过' not in source
    assert '<sup class="citation">' in source and 'id="wenshu-ref-1"' in source
    assert report['semanticVerified'] is False


def test_literal_html_in_body_and_source_metadata_cannot_become_pdf_actions(manuscript):
    env = manuscript
    literal = '<script>alert("保留字面文本")</script><img src="file:///etc/passwd">'
    def change(project):
        project['title'] = 'PDF <标题> & 引文'
        project['sections'][0]['content'] += literal
        project['sections'][0]['evidence'][0]['title'] = '<img src="http://example.invalid/private">材料'
        project['sections'][0]['evidence'][0]['url'] = 'javascript:alert(1)'
    before = fixture_change(env, change)
    report = review(env)
    html = writing_pdf.build_pdf_html(before, report)
    assert '<script>' not in html and '<img ' not in html
    assert '&lt;script&gt;' in html and '&lt;img ' in html
    assert 'href="javascript:' not in html
    reader, text = read_pdf(export(env, format='pdf', allow_issues=True))
    assert compact(literal) in compact(text)
    assert compact(before['sections'][0]['evidence'][0]['title']) in compact(text)
    assert_no_pdf_actions(reader)
    assert preserved(latest(env)) == preserved(before)


def test_pdf_html_has_superscript_internal_references_and_no_unknown_marker_link(manuscript):
    env = manuscript
    before = fixture_change(env, lambda p: p['sections'][0].update(content='  有据 [E1]\n\n未知 [E99]  '))
    report = review(env)
    source = writing_pdf.build_pdf_html(before, report)
    document = html_tree.fromstring(source)
    # Chapter headings lead directly into manuscript text without author state.
    headings = document.xpath('//h2[not(@class)]')
    assert len(headings) == len(before['sections'])
    for label, section, heading in zip(('一', '二'), before['sections'], headings):
        assert heading.text_content() == label + '、' + section['title']
        body = heading.getnext()
        assert body.tag == 'div' and body.get('class') == 'body'
        assert body.xpath('./p[contains(concat(" ", @class, " "), " manuscript-line ")]')
    links = document.xpath('//sup[@class="citation"]/a')
    assert any(link.get('href') == '#wenshu-ref-1' and link.text_content() == '[1]'
               and link.get('aria-label') == '参考文献 1' for link in links)
    assert document.xpath('//div[@class="reference"]/@id') == ['wenshu-ref-1', 'wenshu-ref-3']
    assert html_reference_entries(source) == ['[1] 材料 1', '[3] 材料 2']
    assert document.xpath('//h2[@class="appendix"]/text()') == ['参考文献']
    assert '全文校验记录' not in document.text_content() and '未解决的引用' not in document.text_content()
    assert document.xpath('//p[starts-with(text(), "未知 ")]/sup[text()="[2]"]')
    assert '[E99]' not in document.text_content()
    assert not document.xpath('//a[@href="#wenshu-ref-99"]')
    assert '@font-face' not in source and '<script' not in source
    assert preserved(latest(env)) == preserved(before)


@pytest.mark.parametrize('stale', [False, True])
def test_real_pdf_mixed_unresolved_and_valid_references_use_only_numeric_superscripts(manuscript, stale):
    env = manuscript

    def change(project):
        project['sections'][0]['content'] = '未知引用 [E11]；已有引用 [E1]。'
        if stale:
            project['sections'][0]['citationBindingState'] = 'stale'

    before = fixture_change(env, change)
    history = env.service.history(env.project_id)
    report = review(env)
    source = writing_pdf.build_pdf_html(before, report)
    document = html_tree.fromstring(source)
    assert document.xpath('//sup[@class="citation"]/text()')[0] == '[1]'
    reader, text = read_pdf(export(env, format='pdf', allow_issues=True))
    assert not re.search(r'\[E\d+\]', text)
    assert '未知引用[1]；已有引用[2]。' in compact(text)
    assert '乙' * 100 + '[3]' in compact(text)
    assert set(reader.named_destinations) == ({'wenshu-ref-3'} if stale else {'wenshu-ref-2', 'wenshu-ref-3'})
    assert report['requiresDraftConsent'] is True
    assert_no_pdf_actions(reader)
    assert preserved(latest(env)) == preserved(before)
    assert env.service.history(env.project_id) == history


def test_pdf_html_collapses_adjacent_same_source_excerpts_without_deleting_saved_anchors(manuscript):
    env = manuscript
    ref = env.references[0]
    middle = len(ref['quote']) // 2
    first = {**ref, 'id': 'E1', 'end': middle, 'quote': ref['quote'][:middle]}
    second = {**ref, 'id': 'E2', 'start': middle, 'quote': ref['quote'][middle:]}
    before = fixture_change(env, lambda p: p['sections'][0].update(
        content='句中[E1][E2]继续论述。另一句[E2][E1]。', evidence=[first, second]))
    source = writing_pdf.build_pdf_html(before, review(env))
    document = html_tree.fromstring(source)
    body = document.xpath('//div[@class="body"]')[0]
    assert len(body.xpath('.//sup[@class="citation"]')) == 2
    assert '继续论述。另一句' in body.text_content()
    assert len(document.xpath('//*[@id="wenshu-ref-1"]')) == 1
    assert html_reference_entries(source) == ['[1] 材料 1', '[2] 材料 2']
    assert first['quote'] not in source and second['quote'] not in source
    assert '引用于：' not in source and '原文位置：' not in source
    assert before['sections'][0]['evidence'] == [first, second]
    assert preserved(latest(env)) == preserved(before)


def test_pdf_converter_receives_frozen_version_even_if_manual_save_finishes_first(manuscript, monkeypatch):
    env = manuscript
    before = latest(env)
    original = writing_pdf.render_pdf
    changed_text = '转换期间保存的人工正文必须保留，不能被旧 PDF 快照写回。'
    seen = []

    def convert_with_manual_edit(project, report):
        seen.append((project['version'], report['version']))
        env.service.save_section(env.project_id, before['sections'][0]['id'], before['version'], {'content': changed_text})
        return original(project, report)

    monkeypatch.setattr(writing_pdf, 'render_pdf', convert_with_manual_edit)
    response = export(env, format='pdf')
    _, text = read_pdf(response)
    assert seen == [(before['version'], before['version'])]
    assert response.headers['x-wenshu-project-version'] == str(before['version'])
    assert compact(before['sections'][0]['content'].replace('[E1]', '[1]')) in compact(text)
    assert compact(changed_text) not in compact(text)
    after = latest(env)
    assert after['sections'][0]['content'] == changed_text and after['sections'][0]['manual']
    assert after['version'] == before['version'] + 1 and after['review'] is None


@pytest.mark.parametrize('guard', ['stale', 'issues', 'queued', 'running'])
def test_pdf_version_and_review_and_active_task_gates_run_before_conversion(manuscript, monkeypatch, guard):
    env = manuscript
    original = latest(env)
    requested_version = original['version']
    if guard == 'stale':
        env.service.save_section(env.project_id, original['sections'][0]['id'], original['version'], {'locked': True})
    elif guard == 'issues':
        changed = env.service.save_section(env.project_id, original['sections'][0]['id'], original['version'], {'content': '【待核实】没有材料的新论述'})
        requested_version = changed['version']
    else:
        changed = fixture_change(env, lambda project: project['sections'][0].update(content='', evidence=[]))
        requested_version = changed['version']
        queued = env.service.queue_document(env.project_id, changed['version'])
        if guard == 'running':
            assert env.store.claim()['id'] == queued['job']['id']
    before = latest(env)

    def forbidden_converter(*_args, **_kwargs):
        pytest.fail('The PDF converter must not run before export gates succeed')

    monkeypatch.setattr(writing_pdf, 'render_pdf', forbidden_converter)
    response = export(env, format='pdf', version=requested_version, allow_issues=guard != 'issues')
    assert response.status_code == 409
    if guard != 'stale':
        assert response.json()['detail']['code'] == ('review_issues' if guard == 'issues' else 'generation_active')
    assert preserved(latest(env)) == preserved(before)


@pytest.mark.parametrize('failure,expected_status,expected_code', [
    ('timeout', 504, 'pdf_timeout'), ('os-error', 503, 'pdf_failed'),
    ('nonzero', 503, 'pdf_failed'), ('invalid-output', 503, 'pdf_failed'),
])
def test_conversion_failure_is_friendly_and_never_changes_manual_or_locked_draft(manuscript, monkeypatch, failure, expected_status, expected_code):
    env = manuscript
    project = latest(env)
    project = env.service.save_section(env.project_id, project['sections'][0]['id'], project['version'], {'content': '人工稿需要在转换失败时完整保留 [E1]'})
    project = env.service.save_section(env.project_id, project['sections'][0]['id'], project['version'], {'locked': True})
    history = env.service.history(env.project_id)
    invocations = []

    def fail_converter(command, **options):
        invocations.append((command, options))
        assert options['timeout'] <= 60
        assert options['stderr'] == subprocess.DEVNULL
        assert isinstance(options['input'], bytes) and b'<!doctype html>' in options['input'].lower()
        assert not options.get('shell')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, options['timeout'], output=PRIVATE_ERROR.encode())
        if failure == 'os-error':
            raise OSError(PRIVATE_ERROR)
        return SimpleNamespace(returncode=1 if failure == 'nonzero' else 0, stdout=PRIVATE_ERROR.encode())

    monkeypatch.setattr(writing_pdf.subprocess, 'run', fail_converter)
    response = export(env, format='pdf', allow_issues=True)
    assert invocations
    assert response.status_code == expected_status, response.text
    detail = response.json()['detail']
    assert detail['code'] == expected_code and detail['message']
    assert PRIVATE_ERROR not in response.text and '人工稿需要' not in response.text
    assert preserved(latest(env)) == preserved(project)
    assert env.service.history(env.project_id) == history
    # The concurrency slot must be returned even when conversion fails.
    held = 0
    try:
        while held < 2 and writing_pdf._PDF_SLOTS.acquire(blocking=False):
            held += 1
        assert held == 2
    finally:
        for _ in range(held):
            writing_pdf._PDF_SLOTS.release()


def test_pdf_capacity_failure_is_nonblocking_and_does_not_mutate_manuscript(manuscript, monkeypatch):
    env = manuscript
    before, history = latest(env), env.service.history(env.project_id)
    held = 0
    try:
        while held < 10 and writing_pdf._PDF_SLOTS.acquire(blocking=False):
            held += 1
        assert 0 < held < 10, 'Expected a bounded PDF concurrency gate'
        monkeypatch.setattr(writing_pdf.subprocess, 'run', lambda *_args, **_kwargs: pytest.fail('A saturated converter must not start'))
        response = export(env, format='pdf')
        assert response.status_code == 503 and response.json()['detail']['code'] == 'pdf_busy'
        assert preserved(latest(env)) == preserved(before)
        assert env.service.history(env.project_id) == history
    finally:
        for _ in range(held):
            writing_pdf._PDF_SLOTS.release()


def test_private_temporary_directory_failure_is_friendly_and_preserves_locked_manual_text(manuscript, monkeypatch):
    env = manuscript
    project = latest(env)
    project = env.service.save_section(env.project_id, project['sections'][0]['id'], project['version'], {'content': '临时目录不可用时仍须完整保留人工稿 [E1]'})
    project = env.service.save_section(env.project_id, project['sections'][0]['id'], project['version'], {'locked': True})
    history = env.service.history(env.project_id)

    def denied_directory(*_args, **_kwargs):
        raise OSError(PRIVATE_ERROR + ': /srv/test-user/private/pdf-cache')

    monkeypatch.setattr(writing_pdf.tempfile, 'TemporaryDirectory', denied_directory)
    monkeypatch.setattr(writing_pdf.subprocess, 'run', lambda *_args, **_kwargs: pytest.fail('Cannot start conversion without a private temporary directory'))
    response = export(env, format='pdf', allow_issues=True)
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'pdf_failed'
    assert PRIVATE_ERROR not in response.text and '/srv/test-user/private' not in response.text
    assert preserved(latest(env)) == preserved(project)
    assert env.service.history(env.project_id) == history
    held = 0
    try:
        while held < 2 and writing_pdf._PDF_SLOTS.acquire(blocking=False):
            held += 1
        assert held == 2
    finally:
        for _ in range(held):
            writing_pdf._PDF_SLOTS.release()


@pytest.mark.parametrize('url', ['https://example.invalid/private', 'http://127.0.0.1:8787/private',
                                'file:///etc/passwd', 'data:text/plain;base64,cHJpdmF0ZQ=='])
def test_pdf_fetcher_rejects_all_resource_schemes_without_echoing_private_paths(url):
    with pytest.raises(writing_pdf.PDFResourceDenied) as caught:
        writing_pdf.deny_url_fetcher(url)
    assert url not in str(caught.value)
