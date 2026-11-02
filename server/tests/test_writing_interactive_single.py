"""Isolated interactive whole-draft generation and optional chapter approval."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.store import Store, dumps
from server.tests.test_writing_document import document, ready, queue_and_claim, finished, LOCAL
from server.writing import WritingConflict, WritingService


def short_project(document, target=9999, *, confirmed=True):
    _, gateway, _, _ = document
    gateway.config.update(contextChars=300000, maxOutputTokens=65536)
    return ready(document, words=(200, 200, 200), confirmed=confirmed,
                 brief={'topic': '固定资料与章节确认', 'targetWords': target, 'automation': 'manual',
                        'workflow': 'chapter'})


def test_short_interactive_is_explicit_one_request_and_freezes_new_policy(document):
    store, gateway, service, _ = document
    project = short_project(document, confirmed=False)
    before = copy.deepcopy(project)
    assert service.generation_plan(project['id'])['strategy'] == 'single'
    assert service.get(project['id']) == before
    assert not gateway.calls and not store.claim()
    run, job = queue_and_claim(document, project, confirm_outline=True)
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
    assert frozen['interactiveDocumentPolicy'] == 'interactive-single-document-v1'
    assert frozen['chapterConfirmationPolicy'] == 'chapter-confirmation-v1'
    assert frozen['plan']['requestedWords'] == 9999
    assert run['project']['outlineConfirmed']
    service.process(job)
    current = service.get(project['id'])
    assert store.job(job['id'])['status'] == 'succeeded'
    assert [call['payload']['task'] for call in gateway.calls] == ['writing_document']
    assert gateway.matching_calls == []
    assert all(section['content'] and not section['chapterConfirmed'] for section in current['sections'])


@pytest.mark.parametrize('capacity', ['input', 'output'])
def test_short_capacity_failure_never_splits_or_creates_job(document, capacity):
    store, gateway, service, _ = document
    project = short_project(document, target=2400, confirmed=False)
    if capacity == 'output':
        gateway.config['maxOutputTokens'] = 5000
    else:
        with store.connect() as db:
            _, prepared = service._document_plan(db, project, gateway.config)
        gateway.config['contextChars'] = max(sum(len(message['content']) for message in item['messages'])
                                             for item in prepared) + 1
    before = service.get(project['id'])
    plan = service.generation_plan(project['id'])
    assert not plan['canGenerate'] and plan['strategy'] == 'single'
    with pytest.raises(ValueError, match='须一次生成全文'):
        service.queue_document(project['id'], before['version'], confirm_outline=True)
    assert service.get(project['id']) == before
    assert not store.claim() and gateway.calls == []


def test_ten_thousand_boundary_keeps_chapter_compatible_plan(document):
    _, gateway, service, _ = document
    project = short_project(document, target=10000)
    gateway.config['maxOutputTokens'] = 20000
    plan = service.generation_plan(project['id'])
    assert plan['canGenerate'] and plan['strategy'] == 'sequential'
    assert 'interactiveDocumentPolicy' not in plan
    current, _ = finished(document, project)
    assert len(gateway.calls) == 3
    assert all(call['payload']['task'] == 'writing_section' for call in gateway.calls)
    assert 'chapterConfirmationPolicy' not in current


def test_old_frozen_short_sequential_task_does_not_gain_policy_on_restart(document):
    store, gateway, service, _ = document
    project = short_project(document, target=2400)
    run, _ = queue_and_claim(document, project)
    # Represent a pre-policy persisted task, not a newly requested generation.
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        frozen.pop('interactiveDocumentPolicy')
        frozen.pop('chapterConfirmationPolicy')
        frozen['plan'].pop('interactiveDocumentPolicy')
        frozen['plan']['strategy'] = 'sequential'
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), run['runId']))
        raw = service._load(db, project['id'])
        raw.pop('chapterConfirmationPolicy')
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(raw), project['id']))
    restarted_store = Store(store.root)
    restarted_store.recover()
    restarted = WritingService(restarted_store, gateway)
    restarted.process(restarted_store.claim())
    current = restarted.get(project['id'])
    assert restarted_store.job(run['job']['id'])['status'] == 'succeeded'
    assert [call['payload']['task'] for call in gateway.calls] == ['writing_section'] * 3
    assert 'chapterConfirmationPolicy' not in current
    assert all('chapterConfirmed' not in section for section in current['sections'])


def test_chapter_approval_preserves_other_chapters_review_evidence_and_lock(document):
    store, gateway, service, _ = document
    project, _ = finished(document, short_project(document, target=600))
    project = service.review_skip(project['id'], project['version'])['project']
    saved_review = copy.deepcopy(project['review'])
    saved_disposition = copy.deepcopy(project['reviewDisposition'])
    evidence = [copy.deepcopy(section['evidence']) for section in project['sections']]
    calls = len(gateway.calls)
    first, second = (section['id'] for section in project['sections'][:2])
    project = service.confirm_section(project['id'], first, project['version'])
    assert project['version'] == saved_review['version']
    assert [section['chapterConfirmed'] for section in project['sections']] == [True, False, False]
    assert project['review'] == saved_review and project['reviewDisposition'] == saved_disposition
    assert [section['evidence'] for section in project['sections']] == evidence
    assert not any(section['locked'] for section in project['sections'])
    assert project['review']['semanticVerified'] is False
    assert project['review'].get('authorConfirmed') is False
    before = copy.deepcopy(project)
    assert service.confirm_section(project['id'], first, project['version']) == before
    assert len(gateway.calls) == calls and gateway.matching_calls == []
    project = service.confirm_section(project['id'], second, project['version'])
    project = service.save_section(project['id'], first, project['version'],
                                   {'content': project['sections'][0]['content'] + '\n\n作者补充说明。'})
    assert [section['chapterConfirmed'] for section in project['sections']] == [False, True, False]
    # Reverting a persisted edit must not resurrect the old author approval.
    project = service.save_section(project['id'], first, project['version'],
                                   {'content': before['sections'][0]['content']})
    assert not project['sections'][0]['chapterConfirmed']
    project = service.confirm_step(project['id'], project['version'], 'body')
    assert all(section['chapterConfirmed'] for section in project['sections'])
    assert len(gateway.calls) == calls


def test_chapter_approval_http_version_guards_no_autostart_and_legacy_optout(document):
    store, gateway, service, _ = document
    project = short_project(document, target=600)
    sid = project['sections'][0]['id']
    with pytest.raises(ValueError, match='未启用逐章确认'):
        service.confirm_section(project['id'], sid, project['version'])
    run, job = queue_and_claim(document, project)
    with pytest.raises(WritingConflict):
        service.confirm_section(project['id'], sid, run['project']['version'])
    service.process(job)
    current = service.get(project['id'])
    app = create_app(data_dir=store.root, model_service=gateway)
    path = '/api/writing/projects/' + project['id']
    with TestClient(app) as client:
        assert client.post(path + '/sections/' + sid + '/confirm',
                           json={'expectedVersion': current['version']}).status_code == 403
        assert client.post(path + '/sections/' + sid + '/confirm', headers=LOCAL,
                           json={'expectedVersion': project['version']}).status_code == 409
        assert client.post(path + '/sections/' + sid + '/confirm', headers=LOCAL,
                           json={'expectedVersion': current['version'], 'semanticVerified': True}).status_code == 422
        response = client.post(path + '/sections/' + sid + '/confirm', headers=LOCAL,
                               json={'expectedVersion': current['version']})
        assert response.status_code == 200, response.text
        confirmed = response.json()['project']
        assert confirmed['sections'][0]['chapterConfirmed']
        assert client.get(path).json()['project'] == confirmed
    assert len(gateway.calls) == 1


def test_inflight_cancel_and_manual_changes_cannot_be_overwritten_by_single_result(document):
    store, gateway, service, _ = document
    project = short_project(document, target=600)
    run, job = queue_and_claim(document, project)
    sid = project['sections'][0]['id']

    def stop(_, __):
        store.cancel(job['id'])
        current = service.get(project['id'])
        service.save_section(project['id'], sid, current['version'], {'content': '作者保留的正文。'})

    gateway.callback = stop
    service.process(job)
    current = service.get(project['id'])
    assert store.job(job['id'])['status'] == 'cancelled'
    assert current['sections'][0]['content'] == '作者保留的正文。'
    assert not any(section['content'] for section in current['sections'][1:])
    assert len(gateway.calls) == 1


def test_saved_material_ownership_change_invalidates_approval_without_changing_source_union(document):
    _, _, service, _ = document
    project, _ = finished(document, short_project(document, target=600))
    project = service.confirm_section(project['id'], project['sections'][0]['id'], project['version'])
    before_sources = copy.deepcopy(project['sources'])
    before_content = [section['content'] for section in project['sections']]
    selected = copy.deepcopy(project['materialSelection'])
    selected['userSourceIds'] = []
    # Use the service's existing provenance-only save compatibility branch;
    # no recommendation is fetched or material regenerated.
    project = service.patch(project['id'], project['version'], {'materialSelection': selected})
    assert project['sources'] == before_sources
    assert [section['content'] for section in project['sections']] == before_content
    assert not project['sections'][0]['chapterConfirmed']
    assert 'body' in project['workflowPendingSteps']


def saved_sequential_project(document, *, target=10000, choice=None,
                             workflow='chapter', automation='manual', chapters=3):
    """Save fixture prose directly; no generation task or provider call."""
    _, _, service, _ = document
    brief = {'topic': '逐章作者确认', 'targetWords': target,
             'workflow': workflow, 'automation': automation}
    if choice is not None:
        brief['interactiveGeneration'] = choice
    project = ready(document, brief=brief)
    for index in range(chapters):
        project = service.save_section(project['id'], project['sections'][index]['id'],
                                       project['version'], {'content': f'第{index + 1}章已保存的作者正文。'})
    return project


@pytest.mark.parametrize('target,choice,workflow', [
    (9999, 'chapter', 'chapter'), (9999, 'chapter', 'oneshot'),
    (10000, None, 'chapter'), (10000, 'single', 'chapter'),
])
def test_explicit_sequential_chapter_approval_enables_only_current_saved_chapter(
        document, target, choice, workflow):
    store, gateway, service, _ = document
    project = saved_sequential_project(document, target=target, choice=choice,
                                       workflow=workflow, chapters=1)
    first = project['sections'][0]['id']
    before = copy.deepcopy(project)
    history = service.history(project['id'])
    assert 'chapterConfirmationPolicy' not in service.get(project['id'])
    assert service.list()[0].get('chapterConfirmationPolicy') is None
    confirmed = service.confirm_section(project['id'], first, project['version'])
    assert confirmed['chapterConfirmationPolicy'] == 'chapter-confirmation-v1'
    assert [section['chapterConfirmed'] for section in confirmed['sections']] == [True, False, False]
    assert confirmed['version'] == before['version']
    assert confirmed['updatedAt'] == before['updatedAt']
    assert 'body' in confirmed['workflowPendingSteps']
    assert [(section['content'], section['version'], section['locked'], section['evidence'])
            for section in confirmed['sections']] == [
        (section['content'], section['version'], section['locked'], section['evidence'])
        for section in before['sections']]
    assert service.history(project['id']) == history
    assert service.confirm_section(project['id'], first, project['version']) == confirmed
    with pytest.raises(ValueError, match='各章正文'):
        service.confirm_step(project['id'], project['version'], 'body')
    assert service.get(project['id']) == confirmed
    assert not store.claim() and gateway.calls == [] and gateway.matching_calls == []


@pytest.mark.parametrize('target,choice,workflow,automation', [
    (9999, None, 'chapter', 'manual'),
    (9999, 'single', 'chapter', 'manual'),
    (9999, None, 'oneshot', 'manual'),
    (10000, None, 'oneshot', 'manual'),
    (10000, None, 'chapter', 'automatic'),
    (9999, 'chapter', 'chapter', 'automatic'),
])
def test_single_and_automatic_drafts_do_not_opt_into_sequential_approval(
        document, target, choice, workflow, automation):
    store, gateway, service, _ = document
    project = saved_sequential_project(document, target=target, choice=choice,
                                       workflow=workflow, automation=automation, chapters=1)
    with pytest.raises(ValueError, match='未启用逐章确认'):
        service.confirm_section(project['id'], project['sections'][0]['id'], project['version'])
    assert service.get(project['id']) == project
    assert not store.claim() and gateway.calls == [] and gateway.matching_calls == []


def test_sequential_approval_requires_current_version_and_existing_nonempty_chapter(document):
    store, gateway, service, _ = document
    project = saved_sequential_project(document, chapters=0)
    first = project['sections'][0]['id']
    with pytest.raises(WritingConflict):
        service.confirm_section(project['id'], first, project['version'] - 1)
    with pytest.raises(KeyError):
        service.confirm_section(project['id'], 'missing-chapter', project['version'])
    with pytest.raises(ValueError, match='请先保存本章正文'):
        service.confirm_section(project['id'], first, project['version'])
    assert service.get(project['id']) == project
    assert not store.claim() and gateway.calls == []


def test_active_task_blocks_first_sequential_approval_without_touching_frozen_inputs(document):
    store, gateway, service, _ = document
    project = saved_sequential_project(document, chapters=1)
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][1]['id'])
    before = service.get(project['id'])
    with store.connect() as db:
        frozen = db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0]
    with pytest.raises(WritingConflict):
        service.confirm_section(project['id'], project['sections'][0]['id'], project['version'])
    assert service.get(project['id']) == before
    with store.connect() as db:
        assert db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0] == frozen
    assert store.job(queued['job']['id'])['status'] == 'queued'
    assert gateway.calls == [] and gateway.matching_calls == []


def test_first_sequential_approval_preserves_report_locks_version_and_resets_pending_body(document):
    store, gateway, service, _ = document
    project = saved_sequential_project(document)
    first = project['sections'][0]['id']
    project = service.save_section(project['id'], first, project['version'], {'locked': True})
    project = service.confirm_step(project['id'], project['version'], 'body')
    assert 'body' not in project['workflowPendingSteps']
    project = service.review_skip(project['id'], project['version'])['project']
    history = service.history(project['id'])
    confirmed = service.confirm_section(project['id'], first, project['version'])
    assert confirmed['version'] == project['version']
    assert confirmed['updatedAt'] == project['updatedAt']
    assert confirmed['review'] == project['review']
    assert confirmed['reviewDisposition'] == project['reviewDisposition']
    assert confirmed['review'].get('authorConfirmed') is False
    assert confirmed['review']['semanticVerified'] is False
    assert [section['chapterConfirmed'] for section in confirmed['sections']] == [True, False, False]
    assert confirmed['sections'][0]['locked'] is True
    assert 'body' in confirmed['workflowPendingSteps']
    assert service.history(project['id']) == history
    assert not store.claim() and gateway.calls == [] and gateway.matching_calls == []


def test_sequential_author_approval_invalidates_only_the_changed_chapter(document):
    store, gateway, service, _ = document
    project = saved_sequential_project(document)
    first, second = (section['id'] for section in project['sections'][:2])
    project = service.confirm_section(project['id'], first, project['version'])
    project = service.confirm_section(project['id'], second, project['version'])
    original = project['sections'][0]['content']
    project = service.save_section(project['id'], first, project['version'],
                                   {'content': original + '\n作者修订。'})
    assert [section['chapterConfirmed'] for section in project['sections']] == [False, True, False]
    project = service.save_section(project['id'], first, project['version'], {'content': original})
    assert [section['chapterConfirmed'] for section in project['sections']] == [False, True, False]
    assert not store.claim() and gateway.calls == [] and gateway.matching_calls == []


def test_first_sequential_approval_does_not_resurrect_hidden_historical_approvals(document):
    store, gateway, service, _ = document
    from server.writing_chapter_confirmation import confirm
    project = saved_sequential_project(document)
    with store.connect() as db:
        raw = service._load(db, project['id'])
        for section in raw['sections']:
            confirm(raw, section)
        assert 'chapterConfirmationPolicy' not in raw
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(raw), project['id']))
    current = service.confirm_section(project['id'], project['sections'][0]['id'], project['version'])
    assert [section['chapterConfirmed'] for section in current['sections']] == [True, False, False]
    assert all('chapterConfirmation' not in section for section in current['sections'][1:])
    assert not store.claim() and gateway.calls == [] and gateway.matching_calls == []
