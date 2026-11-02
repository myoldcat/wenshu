"""Saved edits need explicit completion; identical saves remain checkpoints."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.store import Store, dumps
from server.tests.test_writing_document import document, ready as draft_ready
from server.tests.test_writing_automatic import automatic
from server.tests.test_writing_integrity import add_source
from server.writing import WritingConflict, WritingService
from server.writing_automatic import WORKFLOW_CONFIRMATION_POLICY


LOCAL = {'X-Wenshu-Client': 'local'}


def reload_project(store, gateway, project):
    return WritingService(Store(store.root), gateway).get(project['id'])


def ready(document, **options):
    project = draft_ready(document, **options)
    return document[2].confirm_step(project['id'], project['version'], 'brief')


def legacy_without_pending(service, project):
    with service.store.connect() as db:
        current = service._load(db, project['id'])
        current.pop('workflowPendingSteps', None)
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(current), project['id']))
    return service.get(project['id'])


def test_new_draft_brief_stays_pending_after_unchanged_save_until_confirmed(document):
    _, gateway, service, source = document
    project = service.create({'title': '新文稿主题', 'brief': {'topic': '原始证据与固定版本'},
                              'mode': 'strict', 'sourceIds': [source]})
    assert project['workflowPendingSteps'] == ['brief']
    history = service.history(project['id'])
    saved = service.patch(project['id'], project['version'], {'title': project['title'],
                          'brief': copy.deepcopy(project['brief']), 'mode': project['mode'], 'sourceIds': project['sourceIds']})
    assert saved['version'] == project['version'] and saved['workflowPendingSteps'] == ['brief']
    assert service.history(project['id']) == history
    confirmed = service.confirm_step(project['id'], saved['version'], 'brief')
    assert confirmed['version'] == saved['version'] + 1 and confirmed['workflowPendingSteps'] == []
    assert not gateway.calls and not gateway.matching_calls


def test_real_brief_edit_persists_pending_until_explicit_confirmation(document):
    store, gateway, service, _ = document
    project = ready(document, words=(200,))
    changed = service.patch(project['id'], project['version'], {'title': project['title'] + '修订'})
    assert {'brief', 'body'} <= set(changed['workflowPendingSteps'])
    assert not changed['outlineConfirmed']
    assert reload_project(store, gateway, project)['workflowPendingSteps'] == changed['workflowPendingSteps']
    confirmed = service.confirm_step(project['id'], changed['version'], 'brief')
    assert confirmed['version'] == changed['version'] + 1
    assert 'brief' not in confirmed['workflowPendingSteps'] and 'body' in confirmed['workflowPendingSteps']
    assert not gateway.calls


def test_material_metadata_edit_needs_material_confirmation_without_invalidating_outline_or_body(document):
    store, gateway, service, _ = document
    project = ready(document, words=(200,))
    project = service.confirm_step(project['id'], project['version'], 'body')
    selection = {**project['materialSelection'], 'queries': ['固定版本治理'], 'items': [],
                 'userSourceIds': list(project['sourceIds']), 'confirmed': False}
    changed = service.patch(project['id'], project['version'], {'materialSelection': selection})
    assert changed['outlineConfirmed'] is True
    assert changed['workflowPendingSteps'] == ['materials']
    assert changed['sections'] == project['sections'] and changed['sources'] == project['sources']
    assert reload_project(store, gateway, project)['workflowPendingSteps'] == ['materials']
    confirmed = service.confirm_step(project['id'], changed['version'], 'materials')
    assert confirmed['workflowPendingSteps'] == [] and confirmed['outlineConfirmed']


@pytest.mark.parametrize('existing_own_ids', [None, []], ids=['legacy-fallback', 'explicit-empty'])
def test_query_only_update_inherits_user_scope_without_invalidating_outline_or_body(document, existing_own_ids):
    store, _, service, _ = document
    project = ready(document, words=(200,))
    project = service.confirm_step(project['id'], project['version'], 'body')
    if existing_own_ids is not None:
        selection = {**project['materialSelection'], 'userSourceIds': existing_own_ids}
        project = service.patch(project['id'], project['version'], {'materialSelection': selection})
        project = service.confirm(project['id'], project['version'])
        for step in ('materials', 'body'):
            project = service.confirm_step(project['id'], project['version'], step)
    else:
        assert 'userSourceIds' not in project['materialSelection']
    before = copy.deepcopy(project)
    same = service.patch(project['id'], project['version'], {'materialSelection': copy.deepcopy(project['materialSelection'])})
    assert same['version'] == project['version'] and same['materialSelection'] == project['materialSelection']
    selection = {**project['materialSelection'], 'queries': ['重新梳理检索条件']}
    selection.pop('userSourceIds', None)
    changed = service.patch(project['id'], project['version'], {'materialSelection': selection})
    assert changed['outlineConfirmed'] is True
    assert changed['workflowPendingSteps'] == ['materials']
    assert changed['sources'] == before['sources'] and changed['sections'] == before['sections']
    if existing_own_ids is None:
        assert 'userSourceIds' not in changed['materialSelection']
    else:
        assert changed['materialSelection']['userSourceIds'] == []


@pytest.mark.parametrize('change', ['mode', 'sources', 'passages', 'user_sources'])
def test_actual_material_scope_change_invalidates_outline_and_body(document, change):
    store, _, service, _ = document
    project = ready(document, words=(200,))
    project = service.confirm_step(project['id'], project['version'], 'body')
    if change == 'mode':
        patch = {'mode': 'extended'}
    elif change == 'sources':
        other = add_source(store, url='https://example.test/confirmation-second-source')
        patch = {'sourceIds': [*project['sourceIds'], other]}
    elif change == 'passages':
        source = project['sources'][0]
        block = store.detail(source['sourceId'], source['revisionId'])['blocks'][0]
        patch = {'materialSelection': {**project['materialSelection'], 'passages': [{
            'sourceId': source['sourceId'], 'revisionId': source['revisionId'], 'blockId': block['id']}]}}
    else:
        patch = {'materialSelection': {**project['materialSelection'], 'userSourceIds': []}}
    changed = service.patch(project['id'], project['version'], patch)
    assert changed['outlineConfirmed'] is False
    assert set(changed['workflowPendingSteps']) == {'materials', 'body'}


def test_body_real_edit_and_outline_change_require_confirmation_but_lock_alone_does_not(document):
    store, gateway, service, _ = document
    project = ready(document, words=(200,))
    project = service.confirm_step(project['id'], project['version'], 'body')
    section = project['sections'][0]
    changed = service.save_section(project['id'], section['id'], project['version'], {'content': '作者实际修改的正文。 \n'})
    assert changed['workflowPendingSteps'] == ['body']
    assert reload_project(store, gateway, project)['workflowPendingSteps'] == ['body']
    confirmed = service.confirm_step(project['id'], changed['version'], 'body')
    assert confirmed['workflowPendingSteps'] == []
    locked = service.save_section(project['id'], section['id'], confirmed['version'], {'locked': True})
    assert locked['workflowPendingSteps'] == []
    unlocked = service.save_section(project['id'], section['id'], locked['version'], {'locked': False})
    outline = copy.deepcopy(unlocked['sections'])
    outline[0]['goal'] += '并关注原始段落语境'
    revised = service.outline(project['id'], unlocked['version'], outline)
    assert revised['workflowPendingSteps'] == ['body'] and not revised['outlineConfirmed']


def test_step_confirmation_is_noop_for_old_absent_flag_and_for_repeated_confirmation(document):
    store, gateway, service, source = document
    project = service.create({'title': '旧完成记录', 'brief': {}, 'mode': 'strict', 'sourceIds': [source]})
    project = legacy_without_pending(service, project)
    history = service.history(project['id'])
    for step in ('brief', 'materials', 'body'):
        result = service.confirm_step(project['id'], project['version'], step)
        assert result['version'] == project['version'] and 'workflowPendingSteps' not in result
    assert service.history(project['id']) == history
    project = ready(document, words=(200,))
    confirmed = service.confirm_step(project['id'], project['version'], 'body')
    service.review(project['id'], confirmed['version'])
    before = reload_project(store, gateway, project)
    history = service.history(project['id'])
    again = service.confirm_step(project['id'], confirmed['version'], 'body')
    assert again['version'] == before['version'] and again['review'] == before['review']
    assert service.history(project['id']) == history


def test_confirmation_requires_version_and_existing_active_task_gate(document):
    store, _, service, _ = document
    project = ready(document, words=(200,))
    with pytest.raises(WritingConflict):
        service.confirm_step(project['id'], project['version'] - 1, 'body')
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    with pytest.raises(WritingConflict):
        service.confirm_step(project['id'], project['version'], 'body')
    assert store.job(queued['job']['id'])['status'] == 'queued'


def test_theme_confirmation_validates_topic_only_when_pending(document):
    _, _, service, source = document
    project = service.create({'title': '文章标题', 'brief': {}, 'mode': 'strict', 'sourceIds': [source]})
    changed = service.patch(project['id'], project['version'], {'title': '新的文章标题'})
    with pytest.raises(ValueError, match='写作主题'):
        service.confirm_step(project['id'], changed['version'], 'brief')


def test_confirm_step_http_contract_and_program_owned_pending_field(document):
    store, gateway, service, _ = document
    project = ready(document, words=(200,))
    app = create_app(data_dir=store.root, model_service=gateway)
    path = '/api/writing/projects/' + project['id']
    with TestClient(app) as client:
        good = client.post(path + '/steps/body/confirm', headers=LOCAL, json={'expectedVersion': project['version']})
        assert good.status_code == 200, good.text
        saved = good.json()['project']
        assert saved['workflowPendingSteps'] == [] and saved['version'] == project['version'] + 1
        assert client.post(path + '/steps/body/confirm', headers=LOCAL, json={'expectedVersion': True}).status_code == 422
        assert client.post(path + '/steps/unknown/confirm', headers=LOCAL, json={'expectedVersion': saved['version']}).status_code == 422
        assert client.post(path + '/steps/body/confirm', json={'expectedVersion': saved['version']}).status_code == 403
        assert client.patch(path, headers=LOCAL, json={'expectedVersion': saved['version'], 'workflowPendingSteps': []}).status_code == 422
        assert client.post('/api/writing/projects', headers=LOCAL,
                           json={'title': '不得伪造完成状态', 'brief': {'topic': '测试主题'}, 'workflowPendingSteps': []}).status_code == 422


@pytest.mark.parametrize('legacy', [False, True])
def test_automatic_completion_clears_flags_atomically_only_for_new_parent_policy(automatic, legacy):
    store, gateway, service, project = automatic
    # These flags represent earlier real edits and are program-owned; the
    # fixture seeds them in its isolated draft without touching real records.
    with store.connect() as db:
        current = service._load(db, project['id'])
        current['workflowPendingSteps'] = ['body', 'brief', 'materials']
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(current), project['id']))
    queued = service.queue_automatic(project['id'], project['version'])
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        assert frozen['workflowConfirmationPolicy'] == WORKFLOW_CONFIRMATION_POLICY
        if legacy:
            frozen.pop('workflowConfirmationPolicy')
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    service.process(store.claim())
    result = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert all(section['content'] for section in result['sections'])
    reasons = [row['reason'] for row in service.history(project['id'])['versions']]
    if legacy:
        assert result['workflowPendingSteps'] == ['body', 'brief', 'materials']
        assert 'automatic_steps_confirmed' not in reasons
    else:
        assert result['workflowPendingSteps'] == []
        assert reasons.count('automatic_steps_confirmed') == 3
        with store.connect() as db:
            state = json.loads(db.execute('SELECT result_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])['automatic']
        assert state['expectedVersion'] == result['version']


def test_automatic_without_pending_flags_does_not_add_completion_writes(automatic):
    store, _, service, project = automatic
    project = legacy_without_pending(service, project)
    assert 'workflowPendingSteps' not in project
    queued = service.queue_automatic(project['id'], project['version'])
    service.process(store.claim())
    result = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert 'workflowPendingSteps' not in result
    assert not any(row['reason'] == 'automatic_steps_confirmed' for row in service.history(project['id'])['versions'])
