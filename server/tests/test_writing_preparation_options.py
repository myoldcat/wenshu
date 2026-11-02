"""Saved interactive generation choices, isolated stores and mocked providers."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.store import Store, dumps
from server.tests.test_writing_document import document, ready, queue_and_claim, finished, LOCAL
from server.tests.test_writing_automatic_fast import fast, execute, frozen as automatic_frozen
from server.writing import WritingService


ABSENT = object()


def project_with_choice(document, choice=ABSENT, *, target=2400, workflow='chapter', confirmed=True):
    _, gateway, _, _ = document
    gateway.config.update(contextChars=300000, maxOutputTokens=65536)
    brief = {'topic': '保存的正文生成选择', 'targetWords': target, 'workflow': workflow, 'automation': 'manual'}
    if choice is not ABSENT:
        brief['interactiveGeneration'] = choice
    return ready(document, words=(200, 200, 200), brief=brief, confirmed=confirmed)


def frozen(store, run_id):
    with store.connect() as db:
        return json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run_id,)).fetchone()[0])


@pytest.mark.parametrize('choice', ['chapter', 'single'])
def test_optional_choice_roundtrips_without_read_or_identical_save_migrations(document, choice):
    store, gateway, service, _ = document
    project = project_with_choice(document, choice)
    history = service.history(project['id'])
    assert service.get(project['id']) == project
    saved = service.patch(project['id'], project['version'], {'brief': copy.deepcopy(project['brief'])})
    assert saved == project
    assert service.history(project['id']) == history
    assert saved['brief']['interactiveGeneration'] == choice
    assert not gateway.calls and not store.claim()


def test_absent_choice_keeps_short_single_default_without_persisting_an_inferred_choice(document):
    store, gateway, service, _ = document
    project = project_with_choice(document)
    before = copy.deepcopy(project)
    plan = service.generation_plan(project['id'])
    assert plan['strategy'] == 'single'
    assert plan['interactiveDocumentPolicy'] == 'interactive-single-document-v1'
    assert 'interactiveGenerationChoicePolicy' not in plan
    assert service.get(project['id']) == before
    assert 'interactiveGeneration' not in project['brief']
    assert not gateway.calls and not store.claim()


@pytest.mark.parametrize('choice', [None, False, 0, [], {}, 'oneshot', 'single ', 'CHAPTER'])
def test_invalid_generation_choice_is_rejected_without_creating_a_project(document, choice):
    store, gateway, service, source = document
    with pytest.raises(ValueError, match='交互式正文生成方式'):
        service.create({'title': '无效选择', 'sourceIds': [source],
                        'brief': {'topic': '验证选择', 'interactiveGeneration': choice}})
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM writing_projects').fetchone()[0] == 0
    assert not gateway.calls and not store.claim()


def test_invalid_choice_patch_retains_saved_brief_version_and_history(document):
    _, _, service, _ = document
    project = project_with_choice(document, 'chapter')
    history = service.history(project['id'])
    with pytest.raises(ValueError, match='交互式正文生成方式'):
        service.patch(project['id'], project['version'],
                      {'brief': {**project['brief'], 'interactiveGeneration': []}})
    assert service.get(project['id']) == project
    assert service.history(project['id']) == history


@pytest.mark.parametrize('workflow', ['chapter', 'oneshot'])
def test_explicit_short_chapter_choice_overrides_whole_fit_and_is_frozen(document, workflow):
    store, gateway, service, _ = document
    project = project_with_choice(document, 'chapter', target=9999, workflow=workflow)
    plan = service.generation_plan(project['id'])
    assert plan['canGenerate']
    assert plan['estimatedOutputTokens'] <= plan['safeOutputTokens']
    assert plan['estimatedInputChars'] <= plan['contextChars']
    assert plan['strategy'] == 'sequential'
    assert plan['interactiveGenerationChoicePolicy'] == 'interactive-generation-choice-v1'
    assert plan['interactiveGenerationChoice'] == 'chapter'
    assert 'interactiveDocumentPolicy' not in plan
    assert service.get(project['id']) == project
    current, run = finished(document, project)
    saved = frozen(store, run['runId'])
    assert saved['brief']['interactiveGeneration'] == 'chapter'
    assert saved['plan'] == plan
    assert 'interactiveDocumentPolicy' not in saved
    assert 'chapterConfirmationPolicy' not in current
    assert [call['payload']['task'] for call in gateway.calls] == ['writing_section'] * 3
    assert all(section['content'] for section in current['sections'])
    assert gateway.matching_calls == []


def test_short_chapter_choice_needs_only_each_chapter_to_fit(document):
    _, gateway, service, _ = document
    project = project_with_choice(document, 'chapter')
    gateway.config['maxOutputTokens'] = 5000
    plan = service.generation_plan(project['id'])
    assert plan['estimatedOutputTokens'] > plan['safeOutputTokens']
    assert all(section['estimatedOutputTokens'] <= plan['safeOutputTokens'] for section in plan['eligibleSections'])
    assert plan['strategy'] == 'sequential' and plan['canGenerate']
    current, _ = finished(document, project)
    assert all(section['content'] for section in current['sections'])
    assert len(gateway.calls) == 3


def test_explicit_short_single_choice_keeps_capacity_guard_and_chapter_approval(document):
    store, gateway, service, _ = document
    project = project_with_choice(document, 'single', confirmed=False)
    gateway.config['maxOutputTokens'] = 5000
    plan = service.generation_plan(project['id'])
    assert plan['strategy'] == 'single' and not plan['canGenerate']
    with pytest.raises(ValueError, match='须一次生成全文'):
        service.queue_document(project['id'], project['version'], confirm_outline=True)
    assert service.get(project['id']) == project
    assert not store.claim() and not gateway.calls
    gateway.config['maxOutputTokens'] = 65536
    run, job = queue_and_claim(document, project, confirm_outline=True)
    saved = frozen(store, run['runId'])
    assert saved['plan']['interactiveGenerationChoice'] == 'single'
    assert saved['interactiveDocumentPolicy'] == 'interactive-single-document-v1'
    service.process(job)
    current = service.get(project['id'])
    assert len(gateway.calls) == 1
    assert gateway.calls[0]['payload']['task'] == 'writing_document'
    assert current['chapterConfirmationPolicy'] == 'chapter-confirmation-v1'
    assert all(section['content'] and not section['chapterConfirmed'] for section in current['sections'])


@pytest.mark.parametrize('choice', ['chapter', 'single'])
def test_ten_thousand_boundary_retains_but_ignores_short_choice(document, choice):
    _, gateway, service, _ = document
    project = project_with_choice(document, choice, target=10000)
    gateway.config['maxOutputTokens'] = 20000
    plan = service.generation_plan(project['id'])
    assert plan['canGenerate'] and plan['strategy'] == 'sequential'
    assert 'interactiveGenerationChoicePolicy' not in plan
    assert 'interactiveDocumentPolicy' not in plan
    assert service.get(project['id'])['brief']['interactiveGeneration'] == choice


def test_long_saved_explicit_oneshot_keeps_its_existing_adaptive_plan(document):
    _, _, service, _ = document
    project = project_with_choice(document, 'chapter', target=11000, workflow='oneshot')
    plan = service.generation_plan(project['id'])
    assert plan['canGenerate'] and plan['strategy'] == 'single'
    assert 'interactiveGenerationChoicePolicy' not in plan
    assert 'interactiveDocumentPolicy' not in plan


def test_choice_http_save_reload_and_chapter_outline_confirmation_do_not_start_generation(document):
    store, gateway, service, _ = document
    project = project_with_choice(document, 'single', confirmed=False)
    path = '/api/writing/projects/' + project['id']
    app = create_app(data_dir=store.root, model_service=gateway)
    with TestClient(app) as client:
        brief = {**project['brief'], 'interactiveGeneration': 'chapter'}
        response = client.patch(path, headers=LOCAL, json={'expectedVersion': project['version'], 'brief': brief})
        assert response.status_code == 200, response.text
        saved = response.json()['project']
        assert saved['brief']['interactiveGeneration'] == 'chapter'
        assert client.get(path).json()['project'] == saved
        no_op = client.patch(path, headers=LOCAL, json={'expectedVersion': saved['version'], 'brief': brief})
        assert no_op.json()['project'] == saved
        response = client.post(path + '/confirm-outline', headers=LOCAL, json={'expectedVersion': saved['version']})
        assert response.status_code == 200, response.text
        assert response.json()['project']['outlineConfirmed']
        assert client.get(path + '/generation-plan').json()['plan']['strategy'] == 'sequential'
    assert not gateway.calls and not store.claim()


def test_automatic_short_body_ignores_remembered_interactive_chapter_choice(fast):
    store, gateway, service, project = fast
    project = service.patch(project['id'], project['version'],
                            {'brief': {**project['brief'], 'interactiveGeneration': 'chapter'}})
    run, current = execute((store, gateway, service, project))
    saved = automatic_frozen(store, run['runId'])
    assert saved['automaticBodyStrategy'] == 'single'
    assert len(gateway.document_requests) == 1
    document_run = next(item for item in current['runs'] if item['kind'] == 'document')
    child = frozen(store, document_run['id'])
    assert child['plan']['strategy'] == 'single'
    assert 'interactiveGenerationChoicePolicy' not in child['plan']
    assert all(section['content'] for section in current['sections'])


def test_old_frozen_sequential_task_does_not_recompute_new_default_on_restart(document):
    store, gateway, service, _ = document
    project = project_with_choice(document, 'chapter')
    run, _ = queue_and_claim(document, project)
    with store.connect() as db:
        saved = frozen(store, run['runId'])
        saved['brief'].pop('interactiveGeneration')
        saved['plan'].pop('interactiveGenerationChoicePolicy')
        saved['plan'].pop('interactiveGenerationChoice')
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(saved), run['runId']))
        raw = service._load(db, project['id'])
        raw['brief'].pop('interactiveGeneration')
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(raw), project['id']))
    restarted_store = Store(store.root)
    restarted_store.recover()
    restarted = WritingService(restarted_store, gateway)
    def no_replan(*_):
        raise AssertionError('A persisted plan must not be rebuilt')
    restarted._document_plan = no_replan
    restarted.process(restarted_store.claim())
    current = restarted.get(project['id'])
    assert restarted_store.job(run['job']['id'])['status'] == 'succeeded'
    assert [call['payload']['task'] for call in gateway.calls] == ['writing_section'] * 3
    assert 'chapterConfirmationPolicy' not in current
    assert 'interactiveGeneration' not in current['brief']
