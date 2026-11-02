"""Six-step review decisions and clean exports, using an isolated manuscript."""
import io
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from server.tests.test_writing_export import manuscript, latest, fixture_change, review, export, LOCAL
from server.writing_check_report import WORKFLOW_VERSION
from server.writing_review import review_requires_draft


def new_flow(env):
    return fixture_change(env, lambda project: project.update(workflowVersion=WORKFLOW_VERSION))


def text_only(env):
    def mutate(project):
        project['workflowVersion'] = WORKFLOW_VERSION
        section = project['sections'][0]
        section.update(content='下面回顾前文已经介绍的内容。\n\n这一段转入下一项主题。', evidence=[])
    return fixture_change(env, mutate)


def test_six_step_does_not_require_every_paragraph_to_have_a_citation(manuscript):
    env = manuscript
    text_only(env)
    result = review(env)
    codes = {item['code'] for item in result['issues']}
    assert 'uncited_paragraph' not in codes
    assert 'citation_review_pending' in codes
    assert result['citationReviewCompleted'] is False
    assert result['requiresDraftConsent'] is True
    assert all(item['id'] and not item['confirmed'] for item in result['issues'])


def test_author_confirmation_is_stable_read_only_and_never_semantic_support(manuscript):
    env = manuscript
    text_only(env)
    before = latest(env)
    history = env.service.history(env.project_id)
    report = review(env)
    first_ids = [item['id'] for item in report['issues']]
    report_again = review(env)
    assert report_again['reportId'] == report['reportId']
    assert [item['id'] for item in report_again['issues']] == first_ids
    response = env.client.post(env.path + '/review/confirm', headers=LOCAL,
                               json={'expectedVersion': before['version'], 'all': True})
    assert response.status_code == 200, response.text
    confirmed = response.json()['review']
    assert confirmed['authorConfirmed'] is True
    assert confirmed['unconfirmedCounts']['total'] == 0
    assert confirmed['semanticVerified'] is False
    assert confirmed['citationReviewCompleted'] is False
    assert confirmed['requiresDraftConsent'] is False
    assert all(item['confirmed'] for item in confirmed['issues'])
    after = latest(env)
    assert after['version'] == before['version']
    assert env.service.history(env.project_id) == history
    assert after['sections'] == before['sections']
    assert export(env).status_code == 200
    assert review(env)['authorConfirmed'] is True


def test_individual_confirmation_and_stale_issue_rejection(manuscript):
    env = manuscript
    text_only(env)
    initial = review(env)
    issue = next(item for item in initial['issues'] if item['code'] == 'citation_review_pending')
    response = env.client.post(env.path + '/review/confirm', headers=LOCAL,
                               json={'expectedVersion': latest(env)['version'], 'issueIds': [issue['id']]})
    assert response.status_code == 200, response.text
    checked = response.json()['review']
    assert next(item for item in checked['issues'] if item['id'] == issue['id'])['confirmed'] is True
    assert checked['unconfirmedCounts']['total'] == len(initial['issues']) - 1
    stale = env.client.post(env.path + '/review/confirm', headers=LOCAL,
                            json={'expectedVersion': latest(env)['version'], 'issueIds': ['check_unknown']})
    assert stale.status_code == 409


def test_edit_invalidates_confirmation_and_old_version_cannot_confirm(manuscript):
    env = manuscript
    text_only(env)
    old = latest(env)
    accepted = env.client.post(env.path + '/review/confirm', headers=LOCAL,
                               json={'expectedVersion': old['version'], 'all': True})
    assert accepted.status_code == 200
    section = latest(env)['sections'][0]
    changed = env.client.patch(env.path + '/sections/' + section['id'], headers=LOCAL,
                               json={'expectedVersion': old['version'], 'content': section['content'] + '\n\n作者增加的新内容。'})
    assert changed.status_code == 200, changed.text
    assert review(env)['authorConfirmed'] is False
    assert all(not item['confirmed'] for item in review(env)['issues'])
    stale = env.client.post(env.path + '/review/confirm', headers=LOCAL,
                            json={'expectedVersion': old['version'], 'all': True})
    assert stale.status_code == 409


def test_skip_is_explicit_and_report_export_stays_separate_from_article(manuscript):
    env = manuscript
    text_only(env)
    before = latest(env)
    response = env.client.post(env.path + '/review/skip', headers=LOCAL,
                               json={'expectedVersion': before['version']})
    assert response.status_code == 200, response.text
    skipped = response.json()['review']
    assert skipped['disposition']['status'] == 'skipped'
    assert skipped['authorConfirmed'] is False
    assert skipped['requiresDraftConsent'] is False
    assert skipped['unconfirmedCounts'] == {'errors': 0, 'warnings': 0, 'total': 0}
    assert skipped['issues'] and not any(issue['confirmed'] for issue in skipped['issues'])
    assert skipped['status'] == 'needs_attention' and skipped['semanticVerified'] is False
    for format in ('html', 'json'):
        report_response = env.client.post(env.path + '/review/export', headers=LOCAL,
                                          json={'expectedVersion': before['version'], 'format': format})
        assert report_response.status_code == 200, report_response.text
        if format == 'json':
            parsed = report_response.json()
            assert parsed['review']['disposition']['status'] == 'skipped'
            assert parsed['review']['issues']
        else:
            assert '核对报告' in report_response.text
            assert '作者已跳过校验' in report_response.text
            assert '事实、观点和明确引文仍待检查' in report_response.text
            assert '尚未确认' not in report_response.text and '待核对' not in report_response.text
    for format in ('html', 'docx'):
        result = export(env, format=format)
        assert result.status_code == 200, result.text[:1000]
        assert result.headers['x-wenshu-draft-required'] == 'false'
    result = export(env, format='html')
    assert result.status_code == 200, result.text
    assert '核对报告' not in result.text and '全文校验记录' not in result.text
    assert '本章尚未进行引用核对' not in result.text
    assert '人工修改' not in result.text and '严格模式' not in result.text
    assert latest(env)['version'] == before['version']


def test_edit_invalidates_skip_without_acknowledging_or_rewriting_findings(manuscript):
    env = manuscript
    text_only(env)
    before = latest(env)
    skipped = env.service.review_skip(env.project_id, before['version'])['review']
    assert not review_requires_draft(skipped)
    section = latest(env)['sections'][0]
    changed = env.client.patch(env.path + '/sections/' + section['id'], headers=LOCAL,
                               json={'expectedVersion': before['version'], 'content': section['content'] + '\n\n新增一项内容。'})
    assert changed.status_code == 200, changed.text
    current = review(env)
    assert current['disposition'] is None and current['authorConfirmed'] is False
    assert current['unconfirmedCounts']['total'] > 0 and current['requiresDraftConsent'] is True
    assert export(env).status_code == 409
    assert export(env, version=before['version']).status_code == 409


@pytest.mark.parametrize('decision', [
    {'status': 'skipped'},
    {'status': 'skipped', 'version': 1, 'reportId': 'other'},
    {'status': 'skipped', 'version': 0, 'reportId': 'report_current'},
])
def test_unbound_or_stale_skip_does_not_waive_export_consent(decision):
    report = {'version': 1, 'reportId': 'report_current', 'disposition': decision,
              'issues': [{'code': 'citation_review_pending', 'severity': 'warning'}]}
    assert review_requires_draft(report) is True


@pytest.mark.parametrize('format', ('html', 'docx'))
def test_article_has_references_and_authored_text_without_check_metadata(manuscript, format):
    env = manuscript
    new_flow(env)
    result = export(env, allow_issues=True, format=format)
    assert result.status_code == 200, result.text[:1000]
    if format == 'html':
        output = result.text
    else:
        with ZipFile(io.BytesIO(result.content)) as archive:
            output = '\n'.join(''.join(ET.fromstring(archive.read(name)).itertext())
                               for name in ('word/document.xml', 'docProps/core.xml'))
    assert '参考文献' in output and '材料 1' in output
    assert '甲' * 100 in output and '乙' * 100 in output
    for text in ('全文校验记录', '校验时间', '规则状态', '严格模式', '已锁定', '人工修改', '文枢写作草稿'):
        assert text not in output


def test_legacy_rule_coverage_is_preserved(manuscript):
    env = manuscript
    def mutate(project):
        project.pop('workflowVersion', None)
        project['sections'][0].update(content='这是尚未关联依据的旧版正文。', evidence=[])
    fixture_change(env, mutate)
    assert 'uncited_paragraph' in {item['code'] for item in review(env)['issues']}
