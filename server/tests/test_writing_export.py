"""Independent full-review and Word-export acceptance, with no model calls.

Run with the other isolated backend tests. Each test uses its own temporary
library; fixture registration models an already generated, pinned manuscript.
These checks establish structural traceability, not semantic entailment.
"""
import copy
import io
import threading
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.writing_citations import REFERENCE_INTRO
from server.writing_review import review_requires_draft
from server.writing_sentence_citations import relocate_citations, sentence_ranges, strip_citations, utf16_offset


LOCAL = {'X-Wenshu-Client': 'local'}
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
DOCX_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


class NoCallsGateway:
    """Planning is permitted; any accidental generation fails the test."""
    config = {'id': 'export_test_model', 'version': 1, 'name': '导出测试模型',
              'model': 'controlled', 'contextChars': 24000, 'maxOutputTokens': 8192}

    def get_preferences(self):
        return {'defaultModelId': self.config['id']}

    def resolve(self, model_id=None, version=None):
        assert model_id in (None, self.config['id'])
        return copy.deepcopy(self.config)

    def complete(self, *_args, **_kwargs):
        raise AssertionError('Review and export must not invoke a model')


def add_source(store, index, *, text=None):
    text = text or f'第{index}份固定材料原文：保存来源、原始段落和版本，正文中的引用必须能返回这一份材料，而不是其他章节的同名引用。'
    url = f'https://example.test/export-evidence-{index}'
    queued = store.queue('file', {}, f'导出材料 {index}')
    claimed = store.claim()
    assert claimed['id'] == queued['id']
    parsed = {'title': f'材料 {index}', 'body': [text], 'language': 'zh',
              'parser_version': 'export-acceptance-1',
              'blocks': [{'kind': 'paragraph', 'text': text, 'ordinal': 0}]}
    store.publish(claimed, {'url': url}, ('<p>' + text + '</p>').encode(), parsed, url)
    source_id = store.job(queued['id'])['source_id']
    document = store.detail(source_id)
    block = document['blocks'][0]
    return {'id': 'E1', 'sourceId': source_id, 'revisionId': document['revisionId'],
            'blockId': block['id'], 'start': 0, 'end': len(block['text']),
            'quote': block['text'], 'title': document['title'], 'url': url}


@pytest.fixture
def manuscript(tmp_path):
    app = create_app(data_dir=tmp_path / 'isolated-writing-export', model_service=NoCallsGateway())
    store, service = app.state.store, app.state.writing
    references = [add_source(store, 1), add_source(store, 2)]
    project = service.create({'title': '全文校验与导出验收', 'brief': {'targetWords': 200},
                              'mode': 'strict', 'sourceIds': [ref['sourceId'] for ref in references]})
    project = service.outline(project['id'], project['version'], [
        {'title': '第一部分', 'targetWords': 100}, {'title': '第二部分', 'targetWords': 100}])
    project = service.confirm(project['id'], project['version'])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        saved = service._load(db, project['id'])
        # These compatibility fixtures retain legacy coverage expectations.
        # New workflow behavior has its own six-step check-report tests.
        saved.pop('workflowVersion', None)
        for section, character, reference in zip(saved['sections'], ('甲', '乙'), references):
            section['content'] = character * 100 + '[E1]'
            section['evidence'] = [reference]
            plain = strip_citations(section['content'])
            section['content'], section['citationBindings'] = relocate_citations(section['content'], section['evidence'],
                [{'evidenceId': 'E1', 'start': 0, 'end': utf16_offset(plain, len(plain)), 'text': plain,
                  'status': 'supported', 'reason': '隔离样例的受控句子支持判断，用于验证导出保留定位。'}])
            section['version'] += 1
        service._save(db, saved, 'acceptance_registered_fixture')
    with TestClient(app) as client:
        yield SimpleNamespace(app=app, client=client, store=store, service=service,
                              project_id=project['id'], references=references,
                              path=f'/api/writing/projects/{project["id"]}')


def latest(env):
    return env.service.get(env.project_id)


def fixture_change(env, mutate):
    """Simulate persisted legacy/corrupt records without changing API rules."""
    with env.store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        project = env.service._load(db, env.project_id)
        previous = copy.deepcopy(project)
        mutate(project)
        # These adversarial fixtures model records predating sentence matching.
        # Keep an explicitly changed binding for tests that target stale data.
        for section, old in zip(project['sections'], previous['sections']):
            if any(section.get(key) != old.get(key) for key in ('content', 'evidence')) and section.get('citationBindings') == old.get('citationBindings'):
                section.pop('citationBindings', None)
        env.service._save(db, project, 'acceptance_fixture_change')
    return latest(env)


def review(env):
    response = env.client.post(env.path + '/review', headers=LOCAL,
                               json={'expectedVersion': latest(env)['version']})
    assert response.status_code == 200, response.text
    return response.json()['review']


def export(env, *, allow_issues=False, format='docx', version=None):
    return env.client.post(env.path + '/export', headers=LOCAL,
                           json={'expectedVersion': version or latest(env)['version'],
                                 'format': format, 'allowIssues': allow_issues})


def preserved(project):
    result = copy.deepcopy(project)
    result.pop('review', None)
    return result


def word_parts(response):
    assert response.status_code == 200, response.text[:1000]
    assert response.headers['content-type'].split(';')[0] == DOCX_TYPE
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert {'[Content_Types].xml', '_rels/.rels', 'word/document.xml', 'word/styles.xml'} <= names
        parsed = {name: ET.fromstring(archive.read(name)) for name in names if name.endswith(('.xml', '.rels'))}
        xml = archive.read('word/document.xml')
    return parsed, xml


def paragraph_text(paragraph):
    values = []
    for node in paragraph.iter():
        if node.tag == W + 't':
            values.append(node.text or '')
        elif node.tag == W + 'tab':
            values.append('\t')
        elif node.tag in (W + 'br', W + 'cr'):
            values.append('\n')
    return ''.join(values)


def word_text(parts):
    return '\n'.join(paragraph_text(node) for node in parts['word/document.xml'].iter(W + 'p'))


def word_reference_entries(parts):
    paragraphs = [paragraph_text(node) for node in parts['word/document.xml'].iter(W + 'p')]
    return paragraphs[paragraphs.index('参考文献') + 1:] if '参考文献' in paragraphs else []


def html_reference_entries(source):
    from lxml import html

    document = html.fromstring(source)
    entries = document.xpath('//div[@class="reference"]')
    for entry in entries:
        assert len(entry) == 1 and entry[0].tag == 'p'
        assert not list(entry[0])  # Titles are escaped text, without detail blocks.
    assert not document.xpath('//p[@class="materials-intro"]|//div[@class="reference-anchor"]')
    return [entry[0].text_content() for entry in entries]


def test_clean_full_review_and_docx_export_are_read_only_and_traceable(manuscript):
    env = manuscript
    before = latest(env)
    history = env.service.history(env.project_id)
    report = review(env)
    assert report['version'] == before['version'] and report['semanticVerified'] is False
    assert report['status'] == 'rules_passed' and report['issues'] == []
    assert report['requiresDraftConsent'] is False
    assert report['counts'] == {'errors': 0, 'warnings': 0}
    assert report['wordCounts']['total'] == report['wordCounts']['target'] == 200
    assert report['wordCounts']['min'] == 160 and report['wordCounts']['max'] == 240
    assert report['coverage']['paragraphs'] == report['coverage']['validlyCitedParagraphs'] == 2
    response = export(env)
    parts, _ = word_parts(response)
    text = word_text(parts)
    assert response.headers['x-wenshu-project-version'] == str(before['version'])
    assert response.headers['x-wenshu-review-status'] == 'rules_passed'
    assert response.headers['x-wenshu-draft-required'] == 'false'
    assert 'attachment;' in response.headers['content-disposition']
    assert "filename*=UTF-8''" in response.headers['content-disposition']
    assert latest(env)['capabilities']['wordExport'] is True
    for number, section in enumerate(before['sections'], 1):
        assert section['title'] in text and section['content'].replace('[E1]', f'[{number}]') in text
        ref = section['evidence'][0]
        for field in ('quote', 'revisionId', 'blockId', 'sourceId', 'url'):
            assert ref[field] not in text, (field, ref[field])
    assert word_reference_entries(parts) == ['[1] 材料 1', '[2] 材料 2']
    assert REFERENCE_INTRO not in text
    assert report['notice'] not in text and '全文校验记录' not in text
    assert preserved(latest(env)) == preserved(before)
    assert env.service.history(env.project_id) == history
    styles = {node.get(W + 'styleId'): node for node in parts['word/styles.xml'].iter(W + 'style')}
    assert 'Heading1' in styles
    # Long document titles remain wrappable; the heading styles retain their
    # ordering and keep-with-next behavior without adding an explanatory line.
    for paragraph in parts['word/document.xml'].iter(W + 'p'):
        wrap = paragraph.find(f'{W}pPr/{W}wordWrap')
        assert wrap is not None and wrap.get(W + 'val') == '1'
    language = parts['word/styles.xml'].find(f'{W}docDefaults/{W}rPrDefault/{W}rPr/{W}lang')
    assert language is not None
    assert language.get(W + 'val') == 'en-US' and language.get(W + 'eastAsia') == 'zh-CN'
    compatibility = parts['word/settings.xml'].find(f'{W}compat/{W}compatSetting')
    assert compatibility is not None
    assert compatibility.attrib == {W + 'name': 'compatibilityMode',
                                     W + 'uri': 'http://schemas.microsoft.com/office/word', W + 'val': '15'}
    for style_id in ('Heading1', 'Heading2'):
        properties = styles[style_id].find(W + 'pPr')
        ordered_tags = [node.tag for node in properties]
        assert ordered_tags.index(W + 'keepNext') < ordered_tags.index(W + 'spacing')
    assert not any(node.get('TargetMode') == 'External' for root in parts.values() for node in root.iter())
    # Every body citation is a real Word superscript and points to a bibliography
    # bookmark, with no external document relationship.
    links = list(parts['word/document.xml'].iter(W + 'hyperlink'))
    assert [link.get(W + 'anchor') for link in links] == ['wenshu_ref_1', 'wenshu_ref_2']
    assert [paragraph_text(link) for link in links] == ['[1]', '[2]']
    for link in links:
        assert link.find(f'{W}r/{W}rPr/{W}vertAlign').get(W + 'val') == 'superscript'
    bookmarks = list(parts['word/document.xml'].iter(W + 'bookmarkStart'))
    assert [node.get(W + 'name') for node in bookmarks] == ['wenshu_ref_1', 'wenshu_ref_2']


@pytest.mark.parametrize('format', ['docx', 'html'])
def test_reference_list_contains_only_used_numbered_titles_without_deleting_saved_evidence(manuscript, format):
    env = manuscript

    def change(project):
        project['sections'][0]['evidence'].append({**env.references[1], 'id': 'E2'})
        project['sections'][1]['content'] = '未引用资料的正文仍须保留。'

    before = fixture_change(env, change)
    history = env.service.history(env.project_id)
    response = export(env, format=format, allow_issues=True)
    if format == 'docx':
        parts, _ = word_parts(response)
        assert word_reference_entries(parts) == ['[1] 材料 1']
        text = word_text(parts)
        assert [paragraph_text(link) for link in parts['word/document.xml'].iter(W + 'hyperlink')] == ['[1]']
    else:
        assert response.status_code == 200, response.text
        assert html_reference_entries(response.text) == ['[1] 材料 1']
        assert 'href="#wenshu-ref-1"' in response.text and 'id="wenshu-ref-2"' not in response.text
        text = response.text
    assert '材料 2' not in text and REFERENCE_INTRO not in text
    for reference in env.references:
        for field in ('quote', 'revisionId', 'blockId', 'sourceId', 'url'):
            assert reference[field] not in text
    assert before['sections'][0]['evidence'][1]['quote'] == env.references[1]['quote']
    assert preserved(latest(env)) == preserved(before)
    assert env.service.history(env.project_id) == history


def test_locked_manual_export_keeps_exact_text_and_separates_report(manuscript):
    env = manuscript
    project = latest(env)
    content = '  人工 <script>& "引号" 😀\t保留空白 [E1]  \n\n  【模型补充·待核实】这段内容尚未核实 [E1]  '
    project = env.service.save_section(env.project_id, project['sections'][0]['id'], project['version'], {'content': content})
    project = env.service.save_section(env.project_id, project['sections'][0]['id'], project['version'], {'locked': True})
    before, history = copy.deepcopy(project), env.service.history(env.project_id)
    report = review(env)
    assert any(issue['code'] == 'manual_edit_recheck' for issue in report['issues'])
    assert any(issue['code'] == 'citation_binding_stale' for issue in report['issues'])
    assert project['sections'][0]['citationBindingState'] == 'stale'
    assert 'citationBindings' not in project['sections'][0]
    denied = export(env)
    assert denied.status_code == 409 and denied.json()['detail']['code'] == 'review_issues'
    parts, xml = word_parts(export(env, allow_issues=True))
    text = word_text(parts)
    for paragraph in content.split('\n\n'):
        assert paragraph.replace('[E1]', '[1]') in text
    body_paragraphs = [paragraph_text(node) for node in parts['word/document.xml'].iter(W + 'p')
                       if node.find(f'{W}pPr/{W}pStyle').get(W + 'val') == 'BodyText']
    assert body_paragraphs[:3] == content.replace('[E1]', '[1]').split('\n')
    first_body = next(node for node in parts['word/document.xml'].iter(W + 'p')
                      if node.find(f'{W}pPr/{W}pStyle').get(W + 'val') == 'BodyText')
    assert first_body.find(f'.//{W}hyperlink') is None
    assert first_body.find(f'.//{W}vertAlign').get(W + 'val') == 'superscript'
    assert b'<script>' not in xml and b'&lt;script&gt;' in xml
    assert '【模型补充·待核实】' in text
    assert report['notice'] not in text
    assert report['issues'][0]['message'] not in text
    assert '全文校验记录' not in text and '已锁定' not in text and '人工修改' not in text
    assert preserved(latest(env)) == preserved(before)
    assert env.service.history(env.project_id) == history
    with pytest.raises(ValueError, match='锁定'):
        env.service.save_section(env.project_id, project['sections'][0]['id'], project['version'], {'content': '不得覆盖'})


@pytest.mark.parametrize('change', ['content', 'locked', 'brief', 'mode'])
def test_review_is_invalidated_by_saved_content_lock_requirement_or_mode(manuscript, change):
    env = manuscript
    report = review(env)
    project = latest(env)
    section = project['sections'][0]
    if change == 'content':
        saved = env.service.save_section(env.project_id, section['id'], project['version'], {'content': section['content'] + '修改'})
    elif change == 'locked':
        saved = env.service.save_section(env.project_id, section['id'], project['version'], {'locked': True})
    elif change == 'brief':
        saved = env.service.patch(env.project_id, project['version'], {'brief': {'targetWords': 400}})
    else:
        saved = env.service.save_section(env.project_id, section['id'], project['version'], {'mode': 'extended'})
    assert saved['version'] > report['version'] and saved['review'] is None
    assert export(env, version=report['version'], allow_issues=True).status_code == 409
    assert review(env)['version'] == saved['version']


@pytest.mark.parametrize('actual,status', [(79, 'below_target'), (80, 'within_range'),
                                           (120, 'within_range'), (121, 'above_target')])
def test_total_and_chapter_word_count_use_inclusive_twenty_percent_bounds(manuscript, actual, status):
    env = manuscript
    def change(project):
        for section in project['sections']:
            section['content'] = '文' * actual + '[E1]'
    fixture_change(env, change)
    report = review(env)
    words = report['wordCounts']
    assert (words['total'], words['target'], words['status']) == (2 * actual, 200, status)
    for section in words['sections']:
        assert (section['actual'], section['target'], section['min'], section['max'], section['status']) == (actual, 100, 80, 120, status)
    codes = {issue['code'] for issue in report['issues']}
    assert ('total_word_count' in codes) == (status != 'within_range')
    assert ('section_word_count' in codes) == (status != 'within_range')


def test_word_count_reminders_preserve_report_without_requiring_draft_consent(manuscript):
    env = manuscript
    fixture_change(env, lambda project: project['brief'].update(targetWords=400))
    report = review(env)
    assert {issue['code'] for issue in report['issues']} == {'section_word_count', 'total_word_count'}
    assert report['status'] == 'needs_attention' and report['requiresDraftConsent'] is False
    response = export(env)
    parts, _ = word_parts(response)
    assert response.headers['x-wenshu-review-status'] == 'needs_attention'
    assert response.headers['x-wenshu-draft-required'] == 'false'
    text = word_text(parts)
    assert '文枢写作稿件 · 版本' not in text and '文枢写作草稿' not in text
    assert all(issue['message'] not in text for issue in report['issues'])
    assert '全文校验记录' not in text and report['notice'] not in text
    assert '本次规则检查通过' not in text
    assert latest(env)['review']['issues'] == report['issues']


@pytest.mark.parametrize('issues', [
    [{'code': code, 'severity': 'warning', 'message': '保留此项一般提醒'}]
    for code in ('section_word_count', 'total_word_count', 'source_new_revision',
                 'image_caption_missing', 'images_unconfirmed')
])
def test_legacy_general_reminders_allow_export_without_consent(manuscript, monkeypatch, issues):
    env = manuscript
    report = review(env)
    report.pop('requiresDraftConsent')
    report.update(issues=issues, status='needs_attention', counts={'errors': 0, 'warnings': len(issues)})
    monkeypatch.setattr(env.service, '_review', lambda _db, _project: copy.deepcopy(report))
    assert review_requires_draft(report) is False
    response = export(env, format='html')
    assert response.status_code == 200, response.text
    assert response.headers['x-wenshu-draft-required'] == 'false'
    assert '<p>文枢写作稿件 · 版本 ' not in response.text
    assert '文枢写作草稿' not in response.text and '本次规则检查通过' not in response.text
    assert issues[0]['message'] not in response.text


@pytest.mark.parametrize('issue', [
    {'code': 'citation_sentence_uncertain', 'severity': 'warning'},
    {'code': 'uncited_paragraph', 'severity': 'warning'},
    {'code': 'unknown_future_check', 'severity': 'warning'},
    {'code': 'section_word_count', 'severity': 'error'},
    {'code': 'section_word_count'},
])
@pytest.mark.parametrize('with_reminder', [False, True])
def test_content_and_unknown_issues_still_require_explicit_draft_consent(manuscript, monkeypatch, issue, with_reminder):
    env = manuscript
    report = review(env)
    issue = {**issue, 'message': '该问题仍需要核对'}
    issues = [issue]
    if with_reminder:
        issues.insert(0, {'code': 'total_word_count', 'severity': 'warning', 'message': '字数提醒'})
    # A stale or forged derived flag cannot weaken the issue-based policy.
    report.update(issues=issues, status='needs_attention', requiresDraftConsent=False)
    monkeypatch.setattr(env.service, '_review', lambda _db, _project: copy.deepcopy(report))
    assert review_requires_draft(report) is True
    denied = export(env, format='html')
    assert denied.status_code == 409 and denied.json()['detail']['code'] == 'review_issues'
    assert denied.json()['detail']['review']['issues'] == issues
    if 'severity' not in issue:
        # The malformed legacy row tests conservative gating only; it is not
        # a complete issue record that the renderers can display.
        return
    allowed = export(env, format='html', allow_issues=True)
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers['x-wenshu-draft-required'] == 'true'
    assert '<p>文枢写作草稿 · 版本 ' not in allowed.text
    assert all(row['message'] not in allowed.text for row in issues)


@pytest.mark.parametrize('report', [None, {'issues': None}, {'issues': [None]},
                                   {'issues': [{'code': [], 'severity': 'warning'}]}])
def test_malformed_legacy_review_does_not_relax_draft_consent(report):
    assert review_requires_draft(report) is True


def test_larger_brief_target_scales_chapter_targets_and_count_ignores_markers(manuscript):
    env = manuscript
    def change(project):
        project['brief']['targetWords'] = '400'
        project['sections'][0]['content'] = '汉字 alpha beta42 123 [E1]【模型补充·待核实】'
    fixture_change(env, change)
    words = review(env)['wordCounts']
    assert words['target'] == 400 and words['total'] == 105
    assert [section['target'] for section in words['sections']] == [200, 200]
    assert words['sections'][0]['actual'] == 5


@pytest.mark.parametrize('requested', [100, 0, -1, True, 'not a count'])
def test_invalid_or_smaller_total_target_never_weakens_outline_count_requirement(manuscript, requested):
    env = manuscript
    fixture_change(env, lambda p: p['brief'].update(targetWords=requested))
    words = review(env)['wordCounts']
    assert words['target'] == 200 and [row['target'] for row in words['sections']] == [100, 100]


def test_uncited_paragraph_is_reported_at_exact_paragraph_without_chapter_leakage(manuscript):
    env = manuscript
    project = fixture_change(env, lambda p: p['sections'][0].update(content='有依据 [E1]\n\n无依据段落\n\n仅有未知编号 [E99]'))
    report = review(env)
    section_id = project['sections'][0]['id']
    missing = [issue for issue in report['issues'] if issue['code'] == 'uncited_paragraph' and issue['sectionId'] == section_id]
    assert {issue['paragraphIndex'] for issue in missing} == {2, 3}
    assert report['coverage']['paragraphs'] == 4
    assert report['coverage']['validlyCitedParagraphs'] == 2
    unknown = next(issue for issue in report['issues'] if issue['code'] == 'unknown_citation')
    assert unknown['sectionId'] == section_id and unknown['paragraphIndex'] == 3


@pytest.mark.parametrize('marker', ['[Efoo]', '[E-1]', '[E1'])
def test_malformed_inline_citation_is_reported_even_beside_valid_reference(manuscript, marker):
    env = manuscript
    fixture_change(env, lambda p: p['sections'][0].update(content='甲' * 100 + '[E1] ' + marker))
    report = review(env)
    issues = [issue for issue in report['issues'] if issue['code'] == 'citation_id_invalid']
    assert issues and all(issue['paragraphIndex'] == 1 and issue['severity'] == 'error' for issue in issues)
    assert report['coverage']['validlyCitedParagraphs'] == 2
    assert export(env).status_code == 409


def test_duplicate_registry_id_does_not_choose_arbitrary_first_source(manuscript):
    env = manuscript
    fixture_change(env, lambda p: p['sections'][0]['evidence'].append(copy.deepcopy(env.references[1])))
    report = review(env)
    assert any(issue['code'] == 'duplicate_citation' and issue['severity'] == 'error' for issue in report['issues'])
    assert report['coverage']['validlyCitedParagraphs'] == 1
    assert export(env).status_code == 409
    parts, _ = word_parts(export(env, allow_issues=True))
    document = parts['word/document.xml']
    first_body = next(node for node in document.iter(W + 'p')
                      if node.find(f'{W}pPr/{W}pStyle').get(W + 'val') == 'BodyText')
    assert paragraph_text(first_body) == '甲' * 100 + '[1]'
    assert first_body.find(f'.//{W}vertAlign').get(W + 'val') == 'superscript'
    assert first_body.find(f'.//{W}hyperlink') is None
    assert '重复登记' not in word_text(parts) and '未解决的引用' not in word_text(parts)
    assert word_reference_entries(parts) == ['[2] 材料 2']
    assert env.references[1]['quote'] not in word_text(parts)
    assert env.references[0]['quote'] not in word_text(parts)


@pytest.mark.parametrize('mutation', [
    {'start': -1}, {'start': True}, {'end': 999999}, {'end': 0}, {'quote': ''},
    {'quote': '不在原文中的伪造引文'}, {'sourceId': 'missing-source'},
    {'revisionId': 'missing-revision'}, {'blockId': 'missing-block'},
    {'id': 'Efoo'}, {'start': '0'}, {'sourceId': None},
])
def test_malformed_or_broken_anchor_never_crashes_or_counts_as_valid_evidence(manuscript, mutation):
    env = manuscript
    fixture_change(env, lambda p: p['sections'][0]['evidence'][0].update(mutation))
    report = review(env)
    assert report['counts']['errors'] > 0
    assert report['coverage']['validlyCitedParagraphs'] == 1
    assert export(env).status_code == 409
    # Draft export must be robust to legacy bad references and retain warnings.
    parts, _ = word_parts(export(env, allow_issues=True))
    first_body = next(node for node in parts['word/document.xml'].iter(W + 'p')
                      if node.find(f'{W}pPr/{W}pStyle').get(W + 'val') == 'BodyText')
    assert paragraph_text(first_body) == '甲' * 100 + '[1]'
    assert first_body.find(f'.//{W}vertAlign').get(W + 'val') == 'superscript'
    assert first_body.find(f'.//{W}hyperlink') is None


@pytest.mark.parametrize('registry', [None, 'corrupt legacy registry', [None], [{'id': 'E1'}]])
def test_malformed_legacy_registry_remains_reviewable_and_draft_exportable(manuscript, registry):
    env = manuscript
    fixture_change(env, lambda p: p['sections'][0].update(evidence=registry))
    report = review(env)
    assert any(issue['code'] == 'citation_incomplete' for issue in report['issues'])
    assert report['coverage']['validlyCitedParagraphs'] == 1
    word_parts(export(env, allow_issues=True))


def test_existing_anchor_outside_project_scope_is_not_valid_coverage(manuscript):
    env = manuscript
    def change(project):
        project['sources'] = project['sources'][1:]
        project['sourceIds'] = project['sourceIds'][1:]
    fixture_change(env, change)
    report = review(env)
    assert any(issue['code'] == 'citation_out_of_scope' for issue in report['issues'])
    assert report['coverage']['validlyCitedParagraphs'] == 1


@pytest.mark.parametrize('mode,severity', [('strict', 'error'), ('extended', 'warning')])
@pytest.mark.parametrize('marker', ['【模型补充·待核实】', '【待核实】', 'TODO'])
def test_pending_content_is_never_hidden_by_a_valid_citation(manuscript, mode, severity, marker):
    env = manuscript
    fixture_change(env, lambda p: p['sections'][0].update(mode=mode, content=marker + '甲' * 100 + '[E1]'))
    report = review(env)
    pending = [issue for issue in report['issues'] if issue['code'] in {'model_supplement', 'pending_content', 'placeholder_content'}]
    assert pending and all(issue['severity'] == severity for issue in pending)
    assert all(issue['paragraphIndex'] == 1 for issue in pending)
    assert report['coverage']['pendingParagraphs'] == 1
    assert export(env).status_code == 409
    parts, _ = word_parts(export(env, allow_issues=True))
    assert marker in word_text(parts)


def test_chapter_local_e1_references_remain_separate_in_docx(manuscript):
    env = manuscript
    parts, _ = word_parts(export(env))
    text = word_text(parts)
    first, second = env.references
    assert first['quote'] != second['quote']
    # A global E1 map would silently discard one of these independently pinned records.
    for reference in (first, second):
        assert reference['quote'] not in text
        assert reference['revisionId'] not in text and reference['blockId'] not in text
    body = [paragraph_text(node) for node in parts['word/document.xml'].iter(W + 'p')
            if node.find(f'{W}pPr/{W}pStyle').get(W + 'val') == 'BodyText']
    assert body == ['甲' * 100 + '[1]', '乙' * 100 + '[2]']
    assert word_reference_entries(parts) == ['[1] 材料 1', '[2] 材料 2']
    assert '参考文献' in text and text.index('[1] 材料 1') < text.index('[2] 材料 2')


def test_same_source_version_multiple_quotes_share_one_bibliography_entry_without_losing_anchors(manuscript):
    env = manuscript
    ref = env.references[0]
    middle = len(ref['quote']) // 2
    first = {**ref, 'id': 'E8', 'end': middle, 'quote': ref['quote'][:middle]}
    second = {**ref, 'id': 'E3', 'start': middle, 'quote': ref['quote'][middle:]}

    def change(project):
        project['sections'][0].update(content='  先后两处 [E3][E8] 再用同处 [E3]  ', evidence=[first, second])
        project['sections'][1].update(evidence=[copy.deepcopy(ref)])

    before = fixture_change(env, change)
    parts, _ = word_parts(export(env, allow_issues=True))
    document, text = parts['word/document.xml'], word_text(parts)
    bookmarks = list(document.iter(W + 'bookmarkStart'))
    assert [node.get(W + 'name') for node in bookmarks] == ['wenshu_ref_1']
    links = list(document.iter(W + 'hyperlink'))
    assert [paragraph_text(link) for link in links] == ['[1]', '[1]', '[1]']
    assert '  先后两处 [1] 再用同处 [1]  ' in text
    for exact in (first, second, ref):
        assert exact['quote'] not in text
    assert word_reference_entries(parts) == ['[1] 材料 1']
    assert '引用于：' not in text and '原文位置：' not in text
    rendered = export(env, format='html', allow_issues=True)
    assert rendered.status_code == 200
    assert rendered.text.count('id="wenshu-ref-1"') == 1 and 'id="wenshu-ref-2"' not in rendered.text
    assert rendered.text.count('<sup class="citation"><a href="#wenshu-ref-1"') == 3
    assert html_reference_entries(rendered.text) == ['[1] 材料 1']
    assert before['sections'][0]['evidence'] == [first, second]
    assert before['sections'][1]['evidence'] == [ref]
    assert preserved(latest(env)) == preserved(before)


def test_export_clusters_preserve_sentence_positions_line_breaks_and_unknown_markers(manuscript):
    env = manuscript
    ref = env.references[0]
    excerpt = {**ref, 'id': 'E2', 'start': len(ref['quote']) // 2,
               'quote': ref['quote'][len(ref['quote']) // 2:]}
    content = '  首句[E1][E2]。次句[E2][E1]\n[E1]\t[E2][E99][E1]  '
    before = fixture_change(env, lambda p: p['sections'][0].update(content=content, evidence=[ref, excerpt]))
    parts, _ = word_parts(export(env, allow_issues=True))
    paragraphs = [node for node in parts['word/document.xml'].iter(W + 'p')
                  if node.find(f'{W}pPr/{W}pStyle').get(W + 'val') == 'BodyText']
    assert [paragraph_text(node) for node in paragraphs] == [
        '  首句[1]。次句[1]', '[1]\t[2][1]  ', '乙' * 100 + '[3]']
    assert [len(list(node.iter(W + 'hyperlink'))) for node in paragraphs] == [2, 2, 1]
    rendered = export(env, format='html', allow_issues=True)
    assert rendered.status_code == 200
    body = rendered.text.split('<div class="body">', 1)[1].split('</div>', 1)[0]
    assert body.count('<sup class="citation">') == 5
    assert '\n<sup class="citation">' in body and '\t<sup class="citation">[2]</sup>' in body
    assert preserved(latest(env)) == preserved(before)


def test_unknown_draft_markers_are_numeric_superscripts_without_inventing_source_links(manuscript):
    env = manuscript
    content = '  已登记 [E1]，未知 [E99]；格式有误 [Efoo] [E1  '
    before = fixture_change(env, lambda p: p['sections'][0].update(content=content))
    parts, _ = word_parts(export(env, allow_issues=True))
    first_body = next(node for node in parts['word/document.xml'].iter(W + 'p')
                      if node.find(f'{W}pPr/{W}pStyle').get(W + 'val') == 'BodyText')
    assert paragraph_text(first_body) == content.replace('[E1]', '[1]').replace('[E99]', '[2]')
    links = list(first_body.iter(W + 'hyperlink'))
    assert [paragraph_text(link) for link in links] == ['[1]']
    text = word_text(parts)
    assert '未解决的引用' not in text and '[E99]：未登记' not in text
    rendered = export(env, format='html', allow_issues=True)
    assert rendered.status_code == 200
    body = rendered.text.split('<div class="body">', 1)[1].split('</div>', 1)[0]
    assert body.count('<sup class="citation">') == 2
    assert '<sup class="citation">[2]</sup>' in body and '[Efoo] [E1  ' in body
    assert 'href="#wenshu-ref-99"' not in rendered.text
    assert preserved(latest(env)) == preserved(before)


def test_new_source_revision_does_not_replace_pinned_exported_evidence(manuscript):
    env = manuscript
    before = latest(env)
    replacement = add_source(env.store, 1, text='新版材料改变了原始说明；既有文稿必须继续保留旧版本引文，不能自动换成这一段。')
    assert replacement['sourceId'] == env.references[0]['sourceId']
    assert replacement['revisionId'] != env.references[0]['revisionId']
    report = review(env)
    assert any(issue['code'] == 'source_new_revision' and issue['severity'] == 'warning' for issue in report['issues'])
    assert report['coverage']['validlyCitedParagraphs'] == 2
    assert report['requiresDraftConsent'] is False
    response = export(env)
    assert response.headers['x-wenshu-draft-required'] == 'false'
    parts, _ = word_parts(response)
    text = word_text(parts)
    assert word_reference_entries(parts) == ['[1] 材料 1', '[2] 材料 2']
    assert env.references[0]['quote'] not in text and env.references[0]['revisionId'] not in text
    assert replacement['quote'] not in text
    assert latest(env)['sections'][0]['evidence'][0]['revisionId'] == env.references[0]['revisionId']
    assert preserved(latest(env)) == preserved(before)


def test_xml_and_html_metacharacters_are_escaped_without_losing_text(manuscript):
    env = manuscript
    literal = '<script>alert("正文 & 来源")</script>'
    def change(project):
        project['title'] = '导出 <标题> & "引号"'
        project['sections'][0]['title'] = '<章节 & 一>'
        project['sections'][0]['content'] += literal
        project['sections'][0]['evidence'][0]['title'] = '材料 <script> & "来源"'
        project['sections'][0]['evidence'][0]['url'] = 'javascript:alert(1)'
    fixture_change(env, change)
    parts, xml = word_parts(export(env, allow_issues=True))
    assert literal in word_text(parts)
    assert b'<script>' not in xml
    for root in parts.values():
        assert not any(node.get('Target', '').startswith('javascript:') for node in root.iter())
    html = export(env, format='html', allow_issues=True)
    assert html.status_code == 200 and '<script>' not in html.text and '&lt;script&gt;' in html.text
    assert 'href="javascript:' not in html.text


def test_post_export_rejects_stale_unauthorized_or_invalid_requests(manuscript):
    env = manuscript
    project = latest(env)
    assert env.client.post(env.path + '/export', json={'expectedVersion': project['version']}).status_code == 403
    for body in ({'expectedVersion': True}, {'expectedVersion': project['version'], 'format': 'txt'},
                 {'expectedVersion': project['version'], 'allowIssues': 'true'},
                 {'expectedVersion': project['version'], 'replaceManual': True}):
        assert env.client.post(env.path + '/export', headers=LOCAL, json=body).status_code == 422
    changed = env.service.save_section(env.project_id, project['sections'][0]['id'], project['version'], {'locked': True})
    assert export(env, version=project['version'], allow_issues=True).status_code == 409
    assert preserved(latest(env)) == preserved(changed)
    assert env.client.get(env.path + '/export?format=html').status_code == 200
    assert env.client.get(env.path + '/export?format=docx').status_code == 422


@pytest.mark.parametrize('running', [False, True])
def test_active_generation_blocks_even_explicit_draft_export_without_model_call(manuscript, running):
    env = manuscript
    project = fixture_change(env, lambda p: p['sections'][0].update(content='', evidence=[]))
    queued = env.service.queue_document(env.project_id, project['version'])
    if running:
        assert env.store.claim()['id'] == queued['job']['id']
    response = export(env, allow_issues=True)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'generation_active'
    assert latest(env)['sections'][0]['content'] == ''
    assert env.store.job(queued['job']['id'])['status'] == ('running' if running else 'queued')


def test_export_rechecks_current_rules_instead_of_trusting_stored_report(manuscript):
    env = manuscript
    assert review(env)['status'] == 'rules_passed'
    # A stored report is derived cache, not authorization. A damaged anchor can
    # occur independently of a writing edit, so version CAS alone is not enough.
    with env.store.connect() as db:
        db.execute('UPDATE blocks SET text=? WHERE id=?', ('原文被损坏，旧引文不能继续通过', env.references[0]['blockId']))
    response = export(env)
    assert response.status_code == 409
    assert any(issue['code'] == 'citation_anchor_invalid' for issue in response.json()['detail']['review']['issues'])


def test_export_uses_one_frozen_version_while_concurrent_manual_edit_survives(manuscript, monkeypatch):
    from server import writing_export

    env = manuscript
    before = latest(env)
    original = writing_export.render_docx
    threads, failures = [], []
    changed_text = '并发人工修改必须留在工作区，不能被导出操作写回覆盖。'

    def render_with_edit(project, report):
        def save_newer():
            try:
                env.service.save_section(env.project_id, before['sections'][0]['id'], before['version'], {'content': changed_text})
            except BaseException as exc:
                failures.append(exc)
        thread = threading.Thread(target=save_newer)
        threads.append(thread)
        thread.start()
        return original(project, report)

    monkeypatch.setattr(writing_export, 'render_docx', render_with_edit)
    response = export(env)
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert threads and failures == []
    parts, _ = word_parts(response)
    text = word_text(parts)
    assert response.headers['x-wenshu-project-version'] == str(before['version'])
    assert before['sections'][0]['content'].replace('[E1]', '[1]') in text and changed_text not in text
    after = latest(env)
    assert after['version'] == before['version'] + 1
    assert after['sections'][0]['content'] == changed_text and after['sections'][0]['manual'] is True
    assert after['review'] is None
