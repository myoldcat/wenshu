"""Citation faults are report items, while usable automatic prose survives."""
import copy
import json

import pytest

from server.store import dumps
from server.tests.test_writing_automatic import PROSE, automatic, run_automatic
from server.writing import WritingService
from server.writing_sentence_citations import strip_citations, valid_bindings


def assert_completed_with_report(env, queued, current):
    store, gateway, service, _ = env
    report = current['automatic']['report']
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert current['automatic']['status'] == 'completed_with_issues'
    assert report['outputAvailable']
    assert report['summary']['generatedSections'] == 2
    assert report['summary']['skippedSections'] == 0
    assert {'chapters', 'images', 'review'} <= {
        item['stage'] for item in report['stages'] if item['status'] == 'complete'
    }
    assert report['review'] is not None
    assert report['projectVersion'] == current['version']
    assert current['illustrationReview']['status'] == 'skipped'
    assert any(item['stage'] in ('chapters', 'review') and item.get('sectionTitle') == '原始证据'
               for item in report['issues'])
    assert all(strip_citations(section['content']) == PROSE for section in current['sections'])
    assert all(not section['manual'] for section in current['sections'])
    assert not any(run['status'] in ('queued', 'running') for run in current['runs'])
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone()
    calls = copy.deepcopy(gateway.generated)
    assert WritingService(store, gateway).get(current['id'])['automatic']['report'] == report
    assert gateway.generated == calls
    return report


@pytest.mark.parametrize('mode', ['strict', 'extended'])
@pytest.mark.parametrize('problem', ['alias', 'quote', 'claim', 'malformed_evidence'])
def test_bad_chapter_reference_keeps_prose_and_continues_all_stages(automatic, mode, problem):
    store, gateway, service, project = automatic
    if mode != project['mode']:
        project = service.patch(project['id'], project['version'], {'mode': mode})
    env = store, gateway, service, project
    original = gateway.complete

    def bad_first_reference(config, messages):
        packet = json.loads(messages[-1]['content'])
        response = original(config, messages)
        if packet['task'] == 'writing_section' and packet['section']['title'] == '原始证据':
            result = json.loads(response['text'])
            paragraph = result['paragraphs'][0]
            if problem == 'alias':
                paragraph['evidence'][0]['blockId'] = 'C99-B999'
            elif problem == 'quote':
                paragraph['evidence'][0]['quote'] = '这段文字没有出现在所选原文中。'
            elif problem == 'claim':
                paragraph['evidence'][0]['claim'] = '这句话没有出现在生成正文中。'
            else:
                paragraph['evidence'] = {'blockId': 'C99-B999'}
            response = {**response, 'text': dumps(result)}
        return response

    gateway.complete = bad_first_reference
    queued, current = run_automatic(env)
    report = assert_completed_with_report(env, queued, current)
    first, second = current['sections']
    assert first['content'] == PROSE and first['evidence'] == []
    assert second['evidence'] and '[E1]' in second['content']
    assert first.get('citationBindingState') == 'needs_review' or mode == 'extended'
    assert all(item['status'] != 'supported' for item in first['citationBindings']['items'])
    assert any('引用' in item['message'] or '原文' in item['message'] for item in report['issues'])
    assert 'C99-B999' not in dumps(report)
    assert sum(task == 'writing_section' for task, _, _ in gateway.generated) == 2
    with store.connect() as db:
        rows = db.execute("SELECT input_json FROM writing_runs WHERE kind IN ('automatic','section')").fetchall()
    assert all(json.loads(row['input_json']).get('automaticCitationPolicy') == 'report-and-continue-v1'
               for row in rows)


def test_strict_unsupported_body_is_saved_without_claiming_source_support(automatic):
    _, gateway, _, _ = automatic
    gateway.unsupported_body = True
    queued, current = run_automatic(automatic)
    report = assert_completed_with_report(automatic, queued, current)
    for section in current['sections']:
        assert section['content'] == PROSE
        assert section['citationBindingState'] == 'needs_review'
        assert all(item['status'] != 'supported' for item in section['citationBindings']['items'])
    assert report['review']['issues']
    assert all(run['applied'] for run in current['runs'] if run['kind'] == 'section')


@pytest.mark.parametrize('fault', ['invalid_result', 'provider_exception', 'matching_budget'])
def test_body_matcher_failure_is_reported_without_losing_prose_or_retrying(automatic, monkeypatch, fault):
    store, gateway, service, project = automatic
    original = gateway.complete
    failed = False
    body_match_calls = 0

    def interrupted_match(config, messages):
        nonlocal failed, body_match_calls
        packet = json.loads(messages[-1]['content'])
        response = original(config, messages)
        if packet['task'] == 'writing_citation_match' and packet.get('reviewPurpose') != 'outline_scope':
            body_match_calls += 1
            if not failed and fault != 'matching_budget':
                failed = True
                if fault == 'provider_exception':
                    raise RuntimeError('private-matcher-token: internal provider failure')
                return {**response, 'text': dumps({'assignments': [{'sentenceId': 'foreign-sentence',
                         'evidenceIds': ['E999'], 'support': 'supported', 'supportBasis': 'direct',
                         'reason': 'private-matcher-token'}]})}
        return response

    gateway.complete = interrupted_match
    if fault == 'matching_budget':
        from server import writing_citation_tasks
        partition = writing_citation_tasks.partition_matching_packets

        def budget_failure(packet, *args, **kwargs):
            nonlocal failed
            if not failed and packet.get('reviewPurpose') != 'outline_scope' and packet['content'] == PROSE:
                failed = True
                raise ValueError('引用核对输入预算不足 private-matcher-token')
            return partition(packet, *args, **kwargs)

        monkeypatch.setattr(writing_citation_tasks, 'partition_matching_packets', budget_failure)
    # This historical contract intentionally continues after one failed review;
    # newly frozen retry recovery is covered independently by internal_retry.
    queued = service.queue_automatic(project['id'], project['version'])
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        frozen.pop('automaticRetryPolicy', None)
        frozen.pop('automaticRetryOwnerRunId', None)
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    service.process(store.claim())
    current = service.get(project['id'])
    report = assert_completed_with_report(automatic, queued, current)
    assert failed
    first, second = current['sections']
    assert first['content'] == PROSE + '[E1]' and first['citationBindingState'] == 'needs_review'
    assert len(first['evidence']) == 1
    assert valid_bindings(first['content'], first['evidence'], first['citationBindings'], mode='strict')
    bindings = first['citationBindings']['items']
    assert len(bindings) == 1
    assert bindings[0]['status'] == 'unreviewed' and bindings[0]['supportBasis'] == 'none'
    assert bindings[0]['evidenceId'] == 'E1' and bindings[0]['text'] == PROSE
    assert 'markerStart' in bindings[0]
    reference = first['evidence'][0]
    with store.connect() as db:
        block = db.execute('SELECT text FROM blocks WHERE id=? AND revision_id=?',
                           (reference['blockId'], reference['revisionId'])).fetchone()
    assert block['text'][reference['start']:reference['end']] == reference['quote']
    assert '[E1]' in second['content'] and second['citationBindingState'] == 'matched'
    assert not any(item['status'] == 'supported' for item in first.get('citationBindings', {}).get('items', []))
    assert any(issue.get('sectionTitle') == '原始证据' and '未完成' in issue['message']
               for issue in report['issues'])
    assert 'private-matcher-token' not in dumps(report)
    assert 'internal provider' not in dumps(report)
    assert body_match_calls == (1 if fault == 'matching_budget' else 2)
    assert sum(task == 'writing_section' for task, _, _ in gateway.generated) == 2


def test_legacy_automatic_frozen_run_keeps_candidate_only_support_gate(automatic):
    store, gateway, service, project = automatic
    gateway.unsupported_body = True
    queued = service.queue_automatic(project['id'], project['version'])
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?',
                                      (queued['runId'],)).fetchone()[0])
        frozen.pop('automaticCitationPolicy', None)
        frozen.pop('citationReviewPolicy', None)
        frozen.pop('automaticMaterialPolicy', None)
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    service.process(store.claim())
    current = service.get(project['id'])
    assert all(not section['content'] for section in current['sections'])
    assert current['automatic']['report']['summary']['skippedSections'] == 2
    assert all(not run['applied'] for run in current['runs'] if run['kind'] == 'section')
    with store.connect() as db:
        rows = db.execute("SELECT input_json FROM writing_runs WHERE kind='section'").fetchall()
    assert all('automaticCitationPolicy' not in json.loads(row['input_json']) for row in rows)


@pytest.mark.parametrize('locked', [False, True])
def test_citation_tolerance_cannot_overwrite_manual_text_saved_during_generation(automatic, locked):
    _, gateway, service, project = automatic
    original = gateway.complete
    changed = False

    def manual_edit_during_body(config, messages):
        nonlocal changed
        packet = json.loads(messages[-1]['content'])
        response = original(config, messages)
        if packet['task'] == 'writing_section' and not changed:
            changed = True
            latest = service.get(project['id'])
            service.save_section(project['id'], latest['sections'][0]['id'], latest['version'],
                                 {'content': '人工保存的正文必须保留。', 'locked': locked})
            result = json.loads(response['text'])
            result['paragraphs'][0]['evidence'][0]['blockId'] = 'C99-B999'
            response = {**response, 'text': dumps(result)}
        return response

    gateway.complete = manual_edit_during_body
    _, current = run_automatic(automatic)
    first = current['sections'][0]
    assert changed and first['content'] == '人工保存的正文必须保留。'
    assert first['manual'] and first['locked'] == locked
    assert current['sections'][1]['content'] == ''
    assert any(issue['code'] == 'project_changed' for issue in current['automatic']['report']['issues'])
    assert sum(task == 'writing_section' for task, _, _ in gateway.generated) == 1


def test_restart_after_bad_citation_child_commit_recovers_its_report_without_regeneration(automatic, monkeypatch):
    store, gateway, service, project = automatic
    original_complete = gateway.complete
    original_process = service.process
    interrupted = False

    class PowerLoss(BaseException):
        pass

    def bad_first_reference(config, messages):
        packet = json.loads(messages[-1]['content'])
        response = original_complete(config, messages)
        if packet['task'] == 'writing_section' and packet['section']['title'] == '原始证据':
            result = json.loads(response['text'])
            result['paragraphs'][0]['evidence'][0]['blockId'] = 'C99-B999'
            return {**response, 'text': dumps(result)}
        return response

    def lose_power_after_child_commit(job):
        nonlocal interrupted
        result = original_process(job)
        # The child has saved its prose and terminal result, but the parent
        # has not yet imported its notices or saved its next checkpoint.
        if job['kind'] == 'writing_section' and not interrupted:
            interrupted = True
            raise PowerLoss()
        return result

    gateway.complete = bad_first_reference
    monkeypatch.setattr(service, 'process', lose_power_after_child_commit)
    # A real process death cannot execute the parent's terminal-report save.
    monkeypatch.setattr(service, '_automatic_finish', lambda *_args, **_kwargs: None)
    queued = service.queue_automatic(project['id'], project['version'])
    with pytest.raises(PowerLoss):
        service.process(store.claim())
    with store.connect() as db:
        state = json.loads(db.execute('SELECT result_json FROM writing_runs WHERE id=?',
                                      (queued['runId'],)).fetchone()[0])['automatic']
        assert not any('第1段第1项引用未采用' in issue.get('message', '') for issue in state['issues'])
        child = db.execute("SELECT r.applied,j.status FROM writing_runs r JOIN jobs j ON j.id=r.job_id "
                           "WHERE r.project_id=? AND r.kind='section'", (project['id'],)).fetchone()
        assert child['applied'] and child['status'] == 'succeeded'
        db.execute("UPDATE jobs SET status='queued' WHERE id=?", (queued['job']['id'],))
    restarted = WritingService(store, gateway)
    restarted.process(store.claim())
    current = restarted.get(project['id'])
    report = assert_completed_with_report((store, gateway, restarted, project), queued, current)
    first = current['sections'][0]
    assert first['content'] == PROSE and first['evidence'] == []
    assert first['citationBindingState'] == 'needs_review'
    assert all(item['status'] != 'supported' for item in first['citationBindings']['items'])
    recovered_notices = [issue for issue in report['issues'] if issue.get('sectionTitle') == '原始证据'
                         and '第1段第1项引用未采用' in issue['message']]
    assert len(recovered_notices) == 1
    assert '原文位置不在本章允许范围内，或引用信息不完整' in recovered_notices[0]['message']
    assert 'C99-B999' not in dumps(report)
    assert sum(task == 'writing_section' and title == '原始证据'
               for task, title, _ in gateway.generated) == 1
    assert sum(task == 'writing_section' for task, _, _ in gateway.generated) == 2

