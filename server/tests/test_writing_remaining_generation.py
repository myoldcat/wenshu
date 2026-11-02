"""Explicit remaining-chapter jobs use isolated stores and a controlled provider."""
import copy
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from server.app import create_app
from server.store import Store, dumps
from server.writing import WritingConflict, WritingService
from server.writing_api import GenerateDocument
from server.writing_document import REMAINING_GENERATION_POLICY
from server.tests.test_writing_document import document, ready, LOCAL


def sequential_project(document, *, target=10000, choice=None, workflow='chapter',
                       automation='manual', words=(200, 200, 200)):
    _, gateway, _, _ = document
    gateway.config.update(contextChars=300000, maxOutputTokens=65536)
    brief = {'topic': '从当前章开始逐一生成', 'targetWords': target,
             'automation': automation, 'workflow': workflow}
    if choice is not None:
        brief['interactiveGeneration'] = choice
    return ready(document, words=words, brief=brief)


def frozen_run(store, run_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])


def start_remaining(document, project, start=1):
    store, _, service, _ = document
    run = service.queue_document(project['id'], project['version'],
                                 remaining_from_section_id=project['sections'][start]['id'])
    job = store.claim()
    assert job['id'] == run['job']['id']
    return run, job


@pytest.mark.parametrize('target,choice', [(9999, 'chapter'), (10000, None)])
def test_remaining_scope_is_frozen_sequential_and_keeps_global_ordinals(document, target, choice):
    store, gateway, service, _ = document
    project = sequential_project(document, target=target, choice=choice)
    if target == 10000:
        assert service.generation_plan(project['id'])['strategy'] == 'single'
    before = copy.deepcopy(project)
    run, job = start_remaining(document, project)
    frozen = frozen_run(store, run['runId'])
    scope = [section['id'] for section in project['sections'][1:]]
    assert frozen['plan']['strategy'] == 'sequential'
    assert frozen['remainingGenerationPolicy'] == REMAINING_GENERATION_POLICY
    assert frozen['remainingFromSectionId'] == scope[0]
    assert frozen['remainingSectionIds'] == scope
    assert frozen['plan']['remainingFromSectionId'] == scope[0]
    assert [section['id'] for section in frozen['outline']] == [section['id'] for section in before['sections']]
    assert 'chapterConfirmationPolicy' not in frozen
    assert run['project']['brief'] == before['brief']
    assert run['project']['sections'] == before['sections']
    assert run['project']['review'] == before['review']
    assert run['project']['workflowPendingSteps'] == before['workflowPendingSteps']
    assert gateway.calls == []
    with store.connect() as db:
        steps = db.execute('SELECT section_id,ordinal,status,reason FROM writing_document_steps '
                           'WHERE run_id=? ORDER BY ordinal', (run['runId'],)).fetchall()
    assert [row['ordinal'] for row in steps] == [0, 1, 2]
    assert steps[0]['status'] == 'skipped' and '范围' in steps[0]['reason']
    service.process(job)
    current = service.get(project['id'])
    assert store.job(job['id'])['status'] == 'succeeded'
    assert current['sections'][0] == before['sections'][0]
    assert all(section['content'] for section in current['sections'][1:])
    assert [call['payload']['section']['id'] for call in gateway.calls] == scope
    assert [call['payload']['chapterContext']['position']['ordinal'] for call in gateway.calls] == [2, 3]
    assert all(call['payload']['task'] == 'writing_section' for call in gateway.calls)
    assert gateway.matching_calls == []
    assert current['brief'] == before['brief']
    assert 'chapterConfirmationPolicy' not in current
    assert 'body' in current['workflowPendingSteps']


def test_out_of_scope_large_chapter_does_not_block_remaining_capacity(document):
    _, gateway, service, _ = document
    project = sequential_project(document, words=(5000, 100, 100))
    gateway.config['maxOutputTokens'] = 8000
    assert not service.generation_plan(project['id'])['canGenerate']
    run, job = start_remaining(document, project)
    assert run['project']['runs'][0]['plan']['canGenerate']
    service.process(job)
    assert [call['payload']['section']['id'] for call in gateway.calls] == [s['id'] for s in project['sections'][1:]]
    assert not service.get(project['id'])['sections'][0]['content']


@pytest.mark.parametrize('budget', ['input', 'output'])
def test_remaining_request_does_not_bypass_individual_chapter_capacity(document, budget):
    store, gateway, service, _ = document
    project = sequential_project(document)
    gateway.config['contextChars' if budget == 'input' else 'maxOutputTokens'] = 1000
    with pytest.raises(ValueError, match='输入预算|输入空间|输出预算'):
        service.queue_document(project['id'], project['version'],
                               remaining_from_section_id=project['sections'][1]['id'])
    assert service.get(project['id']) == project
    assert not store.claim() and gateway.calls == []


def test_existing_manual_locked_and_prior_author_approvals_are_preserved(document):
    store, gateway, service, _ = document
    project = sequential_project(document, words=(200,) * 5)
    ids = [section['id'] for section in project['sections']]
    project = service.save_section(project['id'], ids[0], project['version'], {'content': '作者已经完成并确认的第一章。'})
    project = service.confirm_section(project['id'], ids[0], project['version'])
    project = service.save_section(project['id'], ids[1], project['version'], {'content': '已有第二章必须保留。'})
    project = service.save_section(project['id'], ids[2], project['version'], {'locked': True})
    with store.connect() as db:
        raw = service._load(db, project['id'])
        raw['sections'][3]['manual'] = True
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(raw), project['id']))
    project = service.get(project['id'])
    reviewed = service.review(project['id'], project['version'])['project']
    run, job = start_remaining(document, reviewed)
    assert run['project']['review'] == reviewed['review']
    assert run['project']['sections'] == reviewed['sections']
    service.process(job)
    current = service.get(project['id'])
    assert current['sections'][:4] == reviewed['sections'][:4]
    assert current['sections'][4]['content'] and not current['sections'][4]['chapterConfirmed']
    assert current['sections'][0]['chapterConfirmed']
    assert 'body' in current['workflowPendingSteps']
    assert current['review'] is None
    assert [call['payload']['section']['id'] for call in gateway.calls] == [ids[4]]
    assert gateway.matching_calls == []


def test_active_document_deduplication_includes_the_remaining_option(document):
    store, gateway, service, _ = document
    project = sequential_project(document)
    middle, last = [section['id'] for section in project['sections'][1:]]
    first = service.queue_document(project['id'], project['version'], remaining_from_section_id=middle)
    same = service.queue_document(project['id'], project['version'], remaining_from_section_id=middle)
    assert same['runId'] == first['runId']
    for different in [None, last]:
        with pytest.raises(WritingConflict):
            service.queue_document(project['id'], project['version'], remaining_from_section_id=different)
    store.cancel(first['job']['id'])
    plain = service.queue_document(project['id'], project['version'])
    assert service.queue_document(project['id'], project['version'])['runId'] == plain['runId']
    with pytest.raises(WritingConflict):
        service.queue_document(project['id'], project['version'], remaining_from_section_id=middle)
    assert gateway.calls == []


@pytest.mark.parametrize('target,choice,workflow,automation', [
    (9999, None, 'chapter', 'manual'), (9999, 'single', 'chapter', 'manual'),
    (10000, None, 'oneshot', 'manual'), (10000, None, 'chapter', 'automatic'),
])
def test_remaining_mode_is_an_explicit_manual_chapter_action(document, target, choice, workflow, automation):
    store, gateway, service, _ = document
    project = sequential_project(document, target=target, choice=choice, workflow=workflow, automation=automation)
    with pytest.raises(ValueError, match='交互式逐章'):
        service.queue_document(project['id'], project['version'], remaining_from_section_id=project['sections'][0]['id'])
    assert service.get(project['id']) == project
    assert not store.claim() and gateway.calls == []


@pytest.mark.parametrize('invalid', ['', ' ', 'x' * 101, 7, 'missing-section'])
def test_invalid_remaining_origin_does_not_queue_or_change_manuscript(document, invalid):
    store, gateway, service, _ = document
    project = sequential_project(document)
    with pytest.raises(ValueError, match='起始章节'):
        service.queue_document(project['id'], project['version'], remaining_from_section_id=invalid)
    assert service.get(project['id']) == project
    assert not store.claim() and gateway.calls == []


def test_remaining_requires_current_version_confirmed_outline_and_nonempty_eligible_scope(document):
    store, gateway, service, _ = document
    project = sequential_project(document)
    last = project['sections'][-1]['id']
    with pytest.raises(WritingConflict):
        service.queue_document(project['id'], project['version'] - 1, remaining_from_section_id=last)
    project = service.save_section(project['id'], last, project['version'], {'content': '末章已经保存。'})
    with pytest.raises(ValueError, match='没有可生成'):
        service.queue_document(project['id'], project['version'], remaining_from_section_id=last)
    with store.connect() as db:
        raw = service._load(db, project['id'])
        raw['outlineConfirmed'] = False
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(raw), project['id']))
    with pytest.raises(ValueError, match='纲要'):
        service.queue_document(project['id'], project['version'], remaining_from_section_id=project['sections'][0]['id'])
    assert not store.claim() and gateway.calls == []


@pytest.mark.parametrize('interruption', ['cancel', 'failure'])
def test_remaining_checkpoint_restarts_only_unfinished_scoped_chapters(document, interruption):
    store, gateway, service, _ = document
    project = sequential_project(document)
    run, job = start_remaining(document, project)
    if interruption == 'cancel':
        gateway.callback = lambda index, _: store.cancel(job['id']) if index == 2 else None
    else:
        gateway.fail_calls = {2}
    service.process(job)
    stopped = service.get(project['id'])
    assert store.job(job['id'])['status'] == ('cancelled' if interruption == 'cancel' else 'failed')
    assert not stopped['sections'][0]['content']
    assert stopped['sections'][1]['content'] and not stopped['sections'][2]['content']
    preserved = copy.deepcopy(stopped['sections'][1])
    frozen = frozen_run(store, run['runId'])
    gateway.callback = None
    restarted_store = Store(store.root)
    restarted = WritingService(restarted_store, gateway)
    restarted_store.retry(job['id'])
    restarted.process(restarted_store.claim())
    current = restarted.get(project['id'])
    assert restarted_store.job(job['id'])['status'] == 'succeeded'
    assert current['sections'][1] == preserved
    assert not current['sections'][0]['content'] and current['sections'][2]['content']
    assert len(gateway.calls) == 3
    assert frozen_run(store, run['runId']) == frozen
    assert all(call['payload']['task'] == 'writing_section' for call in gateway.calls)


def test_future_scoped_chapter_changed_during_run_is_not_generated_or_overwritten(document):
    store, gateway, service, _ = document
    project = sequential_project(document)
    final = project['sections'][2]['id']
    run, job = start_remaining(document, project)
    def manual_change(index, _):
        if index == 1:
            current = service.get(project['id'])
            service.save_section(project['id'], final, current['version'], {'content': '用户提前写好的末章。'})
    gateway.callback = manual_change
    service.process(job)
    current = service.get(project['id'])
    assert store.job(job['id'])['status'] == 'succeeded'
    assert not current['sections'][0]['content']
    assert current['sections'][1]['content'] and current['sections'][2]['content'] == '用户提前写好的末章。'
    assert len(gateway.calls) == 1


def test_old_ordinary_frozen_job_never_gains_remaining_scope_or_forced_strategy(document):
    store, gateway, service, _ = document
    project = sequential_project(document)
    run = service.queue_document(project['id'], project['version'])
    job = store.claim()
    frozen = frozen_run(store, run['runId'])
    assert frozen['plan']['strategy'] == 'single'
    assert not any(key.startswith('remaining') for key in frozen)
    restarted = WritingService(Store(store.root), gateway)
    restarted.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    assert frozen_run(store, run['runId']) == frozen
    assert len(gateway.calls) == 1 and gateway.calls[0]['payload']['task'] == 'writing_document'


@pytest.mark.parametrize('invalid', ['', 'x' * 101, 4, True])
def test_remaining_api_field_is_a_bounded_optional_string(invalid):
    with pytest.raises(ValidationError):
        GenerateDocument.model_validate({'expectedVersion': 1, 'remainingFromSectionId': invalid})
    assert GenerateDocument.model_validate({'expectedVersion': 1}).remainingFromSectionId is None


def test_http_remaining_option_queues_the_scoped_plan_without_model_calls(document):
    store, gateway, service, _ = document
    project = sequential_project(document)
    app = create_app(data_dir=store.root, model_service=gateway)
    path = '/api/writing/projects/' + project['id'] + '/generate-document'
    body = {'expectedVersion': project['version'], 'remainingFromSectionId': project['sections'][1]['id']}
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 403
        response = client.post(path, headers=LOCAL, json=body)
        assert response.status_code == 202, response.text
        plan = response.json()['project']['runs'][0]['plan']
        assert plan['strategy'] == 'sequential'
        assert plan['remainingFromSectionId'] == body['remainingFromSectionId']
        assert client.post(path, headers=LOCAL, json={**body, 'remainingFromSectionId': 'x' * 101}).status_code == 422
    assert gateway.calls == []
