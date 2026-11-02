"""Explicitly skipped checks retain findings without pending export prompts."""
import pytest

from server.store import dumps
from server.tests.test_writing_citation_tasks import environment
from server.writing import WritingExportConflict


@pytest.mark.parametrize('format', ['html', 'docx'])
def test_current_skip_allows_export_without_consent_or_fabricated_confirmation(environment, format):
    store, gateway, service, before = environment
    initial = service.review(before['id'], before['version'])['review']
    history = service.history(before['id'])
    skipped = service.review_skip(before['id'], before['version'])['review']
    assert skipped['reportId'] == initial['reportId']
    assert skipped['counts'] == initial['counts'] and skipped['counts']['errors'] > 0
    assert skipped['status'] == 'needs_attention' and not skipped['semanticVerified']
    assert not skipped['authorConfirmed'] and not skipped['citationReviewCompleted']
    assert skipped['unconfirmedCounts'] == {'errors': 0, 'warnings': 0, 'total': 0}
    assert not skipped['requiresDraftConsent'] and not any(issue['confirmed'] for issue in skipped['issues'])
    exported = service.export_snapshot(before['id'], before['version'], format)
    assert exported['content'] and exported['version'] == before['version']
    assert exported['review']['reportId'] == skipped['reportId']
    assert not exported['review']['requiresDraftConsent']
    assert service.get(before['id'])['sections'] == before['sections']
    assert service.history(before['id']) == history and not gateway.calls
    report = service.export_review(before['id'], before['version'])['content']
    assert '作者已跳过校验' in report
    assert '；尚未确认' not in report and '<p>待核对 ·' not in report
    assert all(issue['message'] in report for issue in skipped['issues'])


def test_skip_saved_with_legacy_pending_flags_is_rederived_only_on_explicit_export(environment):
    store, gateway, service, before = environment
    skipped = service.review_skip(before['id'], before['version'])['project']
    with store.connect() as db:
        saved = service._load(db, before['id'])
        saved['review']['requiresDraftConsent'] = True
        saved['review']['unconfirmedCounts'] = {'errors': 5, 'warnings': 3, 'total': 8}
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(saved), before['id']))
    assert service.get(before['id'])['review']['requiresDraftConsent'] is True
    result = service.export_snapshot(before['id'], before['version'], 'html')
    assert result['review']['requiresDraftConsent'] is False
    assert result['review']['unconfirmedCounts']['total'] == 0
    assert service.get(before['id'])['reviewDisposition'] == skipped['reviewDisposition']
    assert not gateway.calls


def test_new_saved_version_does_not_inherit_a_previous_skip(environment):
    store, gateway, service, before = environment
    skipped = service.review_skip(before['id'], before['version'])['review']
    section = before['sections'][0]
    current = service.save_section(before['id'], section['id'], before['version'],
                                   {'content': section['content'] + '\n\n作者新增事实。'})
    report = service.review(before['id'], current['version'])['review']
    assert report['disposition'] is None and not report['authorConfirmed']
    assert report['unconfirmedCounts']['total'] > 0 and report['requiresDraftConsent']
    with pytest.raises(WritingExportConflict) as error:
        service.export_snapshot(before['id'], current['version'], 'html')
    assert error.value.detail['code'] == 'review_issues'
    assert skipped['version'] != report['version'] and not gateway.calls
