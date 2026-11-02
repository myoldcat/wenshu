"""Short automatic drafts use one frozen request; review remains optional."""
import copy
import json

import pytest

from server.store import Store, dumps
from server.tests.test_writing_automatic import AutomaticGateway, PROSE
from server.tests.test_writing_integrity import add_source
from server.writing import WritingService


class FastGateway(AutomaticGateway):
    def __init__(self):
        super().__init__()
        self.config.update(contextChars=300000, maxOutputTokens=65536)
        self.document_requests = []
        self.document_callback = None
        self.document_transform = None

    def complete(self, config, messages):
        packet = json.loads(messages[-1]['content'])
        if packet['task'] != 'writing_document':
            return super().complete(config, messages)
        assert sum(len(message['content']) for message in messages) <= config['contextChars']
        self.generated.append((packet['task'], None, config['version']))
        self.document_requests.append(copy.deepcopy(messages))
        if self.document_callback:
            callback, self.document_callback = self.document_callback, None
            callback(packet)
        result = {'sections': [{'sectionId': section['id'], 'paragraphs': [{
            'text': PROSE, 'kind': 'supported', 'evidence': [{
                'blockId': section['blocks'][0]['blockId'], 'quote': section['blocks'][0]['text'], 'claim': PROSE}]}],
            'issues': ['一次成文受控提示。']} for section in packet['sections']], 'issues': []}
        if self.document_transform:
            result = self.document_transform(result)
        return {'text': dumps(result), 'usage': {'completion_tokens': 50, 'total_tokens': 100}}


@pytest.fixture
def fast(tmp_path):
    store, gateway = Store(tmp_path / 'automatic-fast-isolated'), FastGateway()
    service = WritingService(store, gateway)
    source = add_source(store)
    project = service.create({'title': '一次成文验收', 'mode': 'strict', 'sourceIds': [source],
        'brief': {'topic': '固定原文与版本', 'targetWords': 6000, 'automation': 'automatic',
            'automaticOptions': {'useSelectedSources': True, 'useRecommendedSources': False,
                                 'mode': 'strict', 'includeImages': False, 'skipReview': True}}})
    return store, gateway, service, project


def patch(env, *, words=None, skip=None):
    store, gateway, service, project = env
    brief = copy.deepcopy(project['brief'])
    if words is not None:
        brief['targetWords'] = words
    if skip is not None:
        brief['automaticOptions']['skipReview'] = skip
    project = service.patch(project['id'], project['version'], {'brief': brief})
    return store, gateway, service, project


def execute(env):
    store, gateway, service, project = env
    queued = service.queue_automatic(project['id'], project['version'])
    service.process(store.claim())
    return queued, service.get(project['id'])


def frozen(store, rid):
    with store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (rid,)).fetchone()[0])


def test_new_options_skip_expensive_steps_and_adopt_all_document_sections(fast):
    store, gateway, service, project = fast
    queued, after = execute(fast)
    saved = frozen(store, queued['runId'])
    assert saved['automaticOptions']['includeImages'] is False
    assert saved['automaticOptions']['skipReview'] is True
    assert saved['automaticBodyStrategy'] == 'single'
    assert len(gateway.document_requests) == 1
    assert all(section['content'].startswith(PROSE) for section in after['sections'])
    assert after['automatic']['report']['summary']['generatedSections'] == 2
    assert not any(run['kind'] in ('section', 'images', 'citation_match') for run in after['runs'])
    assert after['review']['disposition']['status'] == 'skipped'
    assert after['review']['semanticVerified'] is False and after['review']['authorConfirmed'] is False
    assert after['review']['citationReviewCompleted'] is False
    assert after['review']['requiresDraftConsent'] is False
    assert after['review']['unconfirmedCounts']['total'] == 0
    assert not any(issue['confirmed'] for issue in after['review']['issues'])
    assert all(section['evidence'] for section in after['sections'])
    assert all(section['citationBindingState'] == 'unreviewed' for section in after['sections'])
    assert any(stage['stage'] == 'review' and stage['status'] == 'skipped'
               for stage in after['automatic']['report']['stages'])
    child = next(run for run in after['runs'] if run['kind'] == 'document')
    assert frozen(store, child['id'])['plan']['strategy'] == 'single'
    with store.connect() as db:
        payload = json.loads(db.execute('SELECT input_json FROM jobs WHERE id=?', (child['jobId'],)).fetchone()[0])
    assert payload['automaticParentRunId'] == queued['runId']
    assert not any(run['status'] in ('queued', 'running') for run in after['runs'])


@pytest.mark.parametrize('words,strategy,documents,chapters', [(9999, 'single', 1, 0), (10000, 'chapter', 0, 2)])
def test_target_length_boundary_is_frozen(fast, words, strategy, documents, chapters):
    env = patch(fast, words=words)
    queued, after = execute(env)
    assert frozen(env[0], queued['runId'])['automaticBodyStrategy'] == strategy
    assert len(env[1].document_requests) == documents
    assert sum(task == 'writing_section' for task, _, _ in env[1].generated) == chapters
    assert all(section['content'] for section in after['sections'])


def test_explicit_review_runs_after_single_document(fast):
    queued, after = execute(patch(fast, skip=False))
    assert len(fast[1].document_requests) == 1
    assert any(task == 'writing_citation_match' for task, _, _ in fast[1].generated)
    assert after['review']['citationReviewCompleted'] is True
    assert not after['review'].get('disposition')


def test_insufficient_single_capacity_is_reported_without_sequential_fallback(fast):
    fast[1].config['maxOutputTokens'] = 4096
    queued, after = execute(fast)
    assert not fast[1].document_requests
    assert not any(task == 'writing_section' for task, _, _ in fast[1].generated)
    assert not any(section['content'] for section in after['sections'])
    assert after['automatic']['report']['outputAvailable'] is False
    assert any('一次生成全文' in issue['message'] for issue in after['automatic']['report']['issues'])


def test_partial_whole_result_retries_same_frozen_request_once(fast):
    count = 0
    def partial_once(result):
        nonlocal count
        count += 1
        if count == 1:
            result['sections'].pop()
        return result
    fast[1].document_transform = partial_once
    _, after = execute(fast)
    assert len(fast[1].document_requests) == 2
    assert fast[1].document_requests[0] == fast[1].document_requests[1]
    assert all(section['content'] for section in after['sections'])
    assert after['automatic']['report']['summary']['recoveredRequests'] == 1


def test_incomplete_whole_response_does_not_claim_completed_body(fast):
    fast[1].document_transform = lambda result: {**result, 'sections': result['sections'][:1]}
    _, after = execute(fast)
    assert len(fast[1].document_requests) == 3
    assert not any(section['content'] for section in after['sections'])
    assert after['automatic']['report']['summary']['generatedSections'] == 0
    assert after['automatic']['report']['summary']['exhaustedRequests'] == 1


def test_cancelled_during_single_request_cannot_apply_late_result(fast):
    store, gateway, service, project = fast
    queued = service.queue_automatic(project['id'], project['version'])
    gateway.document_callback = lambda _: store.cancel(queued['job']['id'])
    service.process(store.claim())
    after = service.get(project['id'])
    assert len(gateway.document_requests) == 1
    assert not any(section['content'] for section in after['sections'])
    assert after['automatic']['status'] == 'cancelled'


def test_restart_after_document_save_reuses_all_saved_chapters(fast, monkeypatch):
    store, gateway, service, project = fast
    original = service._automatic_current
    class PowerLoss(BaseException):
        pass
    stopped = False
    def stop_after_saved(run, job, frozen_parent, state):
        nonlocal stopped
        current = original(run, job, frozen_parent, state)
        if not stopped and state.get('stage') == 'chapters' and len(state.get('generatedSectionIds', [])) == 2:
            stopped = True
            raise PowerLoss()
        return current
    monkeypatch.setattr(service, '_automatic_current', stop_after_saved)
    monkeypatch.setattr(service, '_automatic_finish', lambda *args, **kwargs: None)
    queued = service.queue_automatic(project['id'], project['version'])
    with pytest.raises(PowerLoss):
        service.process(store.claim())
    assert all(section['content'] for section in service.get(project['id'])['sections'])
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='queued' WHERE id=?", (queued['job']['id'],))
    restarted = WritingService(store, gateway)
    restarted.process(store.claim())
    after = restarted.get(project['id'])
    assert len(gateway.document_requests) == 1
    assert after['automatic']['report']['summary']['generatedSections'] == 2
    assert after['review']['disposition']['status'] == 'skipped'


def test_restart_after_success_cache_before_candidate_publication_reuses_one_request(fast, monkeypatch):
    from server import writing_document as document_module

    store, gateway, service, project = fast
    original_retry = document_module.retry_call

    class PowerLoss(BaseException):
        pass

    def stop_after_cache(*args, **kwargs):
        # retry_call has already committed its successful response; the caller
        # has not yet written candidates into writing_document_steps.
        original_retry(*args, **kwargs)
        raise PowerLoss()

    monkeypatch.setattr(document_module, 'retry_call', stop_after_cache)
    monkeypatch.setattr(service, '_automatic_finish', lambda *args, **kwargs: None)
    queued = service.queue_automatic(project['id'], project['version'])
    with pytest.raises(PowerLoss):
        service.process(store.claim())

    assert len(gateway.document_requests) == 1
    assert not any(section['content'] for section in service.get(project['id'])['sections'])
    with store.connect() as db:
        child = db.execute("SELECT id FROM writing_runs WHERE project_id=? AND kind='document'",
                           (project['id'],)).fetchone()
        assert child
        steps = db.execute('SELECT candidate_json FROM writing_document_steps WHERE run_id=?',
                           (child['id'],)).fetchall()
        assert len(steps) == 2 and all(row['candidate_json'] is None for row in steps)
        cached = db.execute('SELECT status FROM writing_automatic_retries WHERE owner_run_id=? AND logical_key LIKE ?',
                            (queued['runId'], 'document-generation:%')).fetchall()
        assert [row['status'] for row in cached] == ['succeeded']

    monkeypatch.setattr(document_module, 'retry_call', original_retry)
    restarted_store = Store(store.root)
    restarted_store.recover()
    parent_job = restarted_store.claim()
    assert parent_job['id'] == queued['job']['id']
    restarted = WritingService(restarted_store, gateway)
    restarted.process(parent_job)
    after = restarted.get(project['id'])
    assert len(gateway.document_requests) == 1
    assert all(section['content'].startswith(PROSE) for section in after['sections'])
    assert after['automatic']['report']['summary']['generatedSections'] == 2
    assert after['review']['disposition']['status'] == 'skipped'
    assert not any(run['status'] in ('queued', 'running') for run in after['runs'])


@pytest.mark.parametrize('change', ['title', 'manual', 'lock', 'lock_unlock'])
def test_inflight_edit_or_lock_stops_single_document_without_retry_or_overwrite(fast, change):
    store, gateway, service, project = fast
    manual_text = '作者在整稿请求期间保存的正文必须保留。'
    changed = {}

    def edit_during_request(packet):
        current = service.get(project['id'])
        sid = packet['sections'][0]['id']
        if change == 'title':
            current = service.patch(project['id'], current['version'], {'title': '作者修改后的标题'})
        else:
            values = {'content': manual_text} if change == 'manual' else {'locked': True}
            current = service.save_section(project['id'], sid, current['version'], values)
            if change == 'lock_unlock':
                current = service.save_section(project['id'], sid, current['version'], {'locked': False})
        changed.update(title=current['title'], sections=copy.deepcopy(current['sections']))

    gateway.document_callback = edit_during_request
    _, after = execute(fast)
    assert len(gateway.document_requests) == 1
    assert after['title'] == changed['title']
    assert after['sections'] == changed['sections']
    assert after['automatic']['report']['summary']['generatedSections'] == 0
    assert any(issue['code'] == 'project_changed' for issue in after['automatic']['report']['issues'])
    child = next(run for run in after['runs'] if run['kind'] == 'document')
    assert not child['applied']
    assert not any(run['status'] in ('queued', 'running') for run in after['runs'])


def test_preserved_manual_and_locked_chapters_are_excluded_from_one_remaining_request(fast):
    store, gateway, service, project = fast
    project = service.outline(project['id'], project['version'], [
        {'title': title, 'goal': '保留固定版本原文与人工记录。', 'targetWords': 100}
        for title in ('人工章节', '锁定章节', '待写章节一', '待写章节二')])
    first, second, third, fourth = [section['id'] for section in project['sections']]
    project = service.save_section(project['id'], first, project['version'], {'content': '作者写下的现有正文。'})
    project = service.save_section(project['id'], second, project['version'], {'content': '已经保存并锁定的正文。'})
    project = service.save_section(project['id'], second, project['version'], {'locked': True})
    protected = copy.deepcopy(project['sections'][:2])
    _, after = execute((store, gateway, service, project))

    assert len(gateway.document_requests) == 1
    packet = json.loads(gateway.document_requests[0][-1]['content'])
    assert [section['id'] for section in packet['sections']] == [third, fourth]
    assert after['sections'][:2] == protected
    assert all(section['content'].startswith(PROSE) for section in after['sections'][2:])
    summary = after['automatic']['report']['summary']
    assert summary['generatedSections'] == summary['preservedSections'] == 2
    assert summary['skippedSections'] == 0
    assert after['automatic']['report']['outputAvailable'] is True
    child = next(run for run in after['runs'] if run['kind'] == 'document')
    statuses = {step['sectionId']: step['status'] for step in child['candidate']['document']['steps']}
    assert statuses == {first: 'skipped', second: 'skipped', third: 'applied', fourth: 'applied'}


def test_pre_policy_frozen_parent_keeps_sequential_generation(fast):
    store, gateway, service, project = fast
    queued = service.queue_automatic(project['id'], project['version'])
    with store.connect() as db:
        old = frozen(store, queued['runId'])
        for key in ('automaticBodyPolicy', 'automaticBodyStrategy', 'automaticTargetWords'):
            old.pop(key)
        old['automaticOptions'].pop('skipReview')
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(old), queued['runId']))
    service.process(store.claim())
    after = service.get(project['id'])
    assert not gateway.document_requests
    assert sum(task == 'writing_section' for task, _, _ in gateway.generated) == 2
    assert any(task == 'writing_citation_match' for task, _, _ in gateway.generated)
    assert all(section['content'] for section in after['sections'])
