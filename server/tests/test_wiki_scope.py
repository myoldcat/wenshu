"""Read-only Wiki candidate selection and atomic source-level build reservations."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from server.prompts import PromptLibrary
from server.tests.test_wiki import environment, publish, execute, compile_page
from server.tests.test_wiki_api import bench, LOCAL


def database_snapshot(store):
    with store.connect() as db:
        return list(db.iterdump())


def test_scope_filters_entire_library_before_limit_and_does_not_resolve_models(environment, monkeypatch):
    store, models, service, built = environment
    compile_page(environment)
    active = publish(store, url='https://example.org/active')
    empty = publish(store, url='https://example.org/empty')
    eligible = [publish(store, url=f'https://example.org/candidate/{index}', title=f'待构建资料 {index}')
                for index in range(23)]
    order = [built, active, empty, *eligible]
    with store.connect() as db:
        for index, source in enumerate(order):
            db.execute('UPDATE sources SET created_at=? WHERE id=?', (f'2026-10-28T00:00:{59-index:02d}+00:00', source))
        db.execute("UPDATE blocks SET text=' \t\r\n　' WHERE revision_id=(SELECT current_revision FROM sources WHERE id=?)", (empty,))
    service.queue([active])
    expected = [{'id': source, 'title': f'待构建资料 {index}', 'revisionId': store.detail(source)['revisionId']}
                for index, source in enumerate(eligible[:20])]
    before = database_snapshot(store)
    calls = len(models.calls)
    def no_model(*args, **kwargs):
        raise AssertionError('Scope preview must not consult a model configuration')
    monkeypatch.setattr(models, 'get_preferences', no_model)
    monkeypatch.setattr(models, 'resolve', no_model)
    scope = service.build_scope()
    assert scope == {
        'documents': expected,
        'total': 26, 'eligible': 23, 'excluded': {'built': 1, 'active': 1, 'empty': 1},
        'limited': True, 'limit': 20,
    }
    assert database_snapshot(store) == before
    assert len(models.calls) == calls


def test_selected_scope_preserves_order_deduplicates_and_never_expands_empty_selection(environment):
    store, models, service, first = environment
    second = publish(store, url='https://example.org/second')
    third = publish(store, url='https://example.org/third')
    before = database_snapshot(store)
    assert [item['id'] for item in service.build_scope([second, first, second])['documents']] == [second, first]
    scope = service.build_scope([])
    assert scope == {'documents': [], 'total': 0, 'eligible': 0,
                     'excluded': {'built': 0, 'active': 0, 'empty': 0}, 'limited': False, 'limit': 20}
    assert third not in [item['id'] for item in service.build_scope([first])['documents']]
    with pytest.raises(KeyError):
        service.build_scope([first, 'missing'])
    assert database_snapshot(store) == before
    assert models.calls == []


def test_current_publication_survives_failed_rebuild_and_model_or_prompt_edits(environment):
    store, models, service, source = environment
    original = compile_page(environment)
    failed = service.queue([source], force=True)['jobs'][0]
    models.fail_at = len(models.calls) + 1
    assert execute(store, service)['status'] == 'failed'
    models.config['version'] += 1
    library = PromptLibrary(store)
    prompt = next(item for item in library.list()['items'] if item['id'] == 'wiki_compile')
    library.update('wiki_compile', prompt['content'] + '\n这是显式修改后的测试提示词。', prompt['version'])
    before = database_snapshot(store)
    scope = service.build_scope([source])
    assert scope['documents'] == [] and scope['excluded'] == {'built': 1, 'active': 0, 'empty': 0}
    assert service.queue([source]) == {'jobs': [], 'skipped': [{'sourceId': source, 'reason': 'already_built'}]}
    assert database_snapshot(store) == before
    assert service.page(original['id'])['version'] == original['version']
    explicit = service.queue([source], force=True)['jobs'][0]
    assert explicit['id'] != failed['id']
    with store.connect() as db:
        assert db.execute('SELECT model_version FROM wiki_runs WHERE job_id=?', (explicit['id'],)).fetchone()[0] == 2
    assert execute(store, service)['status'] == 'succeeded'


def test_old_revision_publication_does_not_exclude_current_revision(environment):
    store, _, service, source = environment
    original = compile_page(environment)
    publish(store, ['更新后的知识治理原文补充了新的固定版本要求，所有资料仍可回查原始证据及准确来源。'])
    scope = service.build_scope([source])
    assert scope['eligible'] == 1 and scope['excluded']['built'] == 0
    assert scope['documents'][0]['revisionId'] != original['revisionId']
    assert service.queue([source])['jobs'][0]['revision_id'] == scope['documents'][0]['revisionId']
    assert execute(store, service)['status'] == 'succeeded'


@pytest.mark.parametrize('status', ['queued', 'running'])
@pytest.mark.parametrize('force', [False, True])
def test_any_revision_active_job_is_reused_even_after_configuration_changes(environment, status, force):
    store, models, service, source = environment
    active = service.queue([source])['jobs'][0]
    claimed = store.claim()
    assert claimed['id'] == active['id']
    publish(store, ['这个更新版本在旧任务进行期间入库，新的 Wiki 请求不能与旧来源任务并行，也不能覆盖固定原文证据。'])
    with store.connect() as db:
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, active['id']))
    models.config['version'] += 1
    store.detail(source)  # Populate the reader's unrelated lazy metrics before snapshotting.
    before = database_snapshot(store)
    scope = service.build_scope([source])
    assert scope['documents'] == [] and scope['excluded']['active'] == 1
    result = service.queue([source], force=force)
    assert [job['id'] for job in result['jobs']] == [active['id']]
    assert result['jobs'][0]['status'] == status
    assert database_snapshot(store) == before
    assert models.calls == []


def test_concurrent_normal_and_force_submissions_reserve_only_one_job(environment):
    store, models, service, source = environment
    assert service.build_scope([source])['eligible'] == 1
    barrier = Barrier(2)
    def submit(force):
        barrier.wait(timeout=10)
        return service.queue([source], force=force)['jobs'][0]['id']
    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(submit, [False, True]))
    assert ids[0] == ids[1]
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM wiki_runs WHERE source_id=?', (source,)).fetchone()[0] == 1
    assert service.build_scope([source])['excluded']['active'] == 1
    assert models.calls == []


def test_preview_then_another_submission_is_rechecked_without_duplicate_tasks(bench):
    client, app, provider, source = bench
    before = database_snapshot(app.state.store)
    response = client.post('/api/wiki/build-scope', headers=LOCAL, json={})
    assert response.status_code == 200
    scope = response.json()
    assert scope['total'] == scope['eligible'] == 1 and scope['documents'][0]['id'] == source
    assert client.post('/api/wiki/build-scope', headers=LOCAL, json={'sourceIds': None}).json() == scope
    assert client.post('/api/wiki/build-scope', headers=LOCAL, json={'sourceIds': []}).json()['total'] == 0
    assert database_snapshot(app.state.store) == before
    first = client.post('/api/wiki/build', headers=LOCAL, json={'sourceIds': [source]}).json()['jobs'][0]
    raced = client.post('/api/wiki/build', headers=LOCAL, json={'sourceIds': [source], 'force': True}).json()['jobs'][0]
    assert raced['id'] == first['id']
    latest = client.post('/api/wiki/build-scope', headers=LOCAL, json={'sourceIds': [source]}).json()
    assert latest['eligible'] == 0 and latest['excluded']['active'] == 1
    assert provider.calls == []


@pytest.mark.parametrize('payload,expected', [
    ({'sourceIds': ['missing']}, 404),
    ({'sourceIds': [' ']}, 422),
    ({'sourceIds': ['x' * 101]}, 422),
    ({'sourceIds': [False]}, 422),
    ({'sourceIds': 'all'}, 422),
    ({'sourceIds': ['x'] * 10001}, 422),
    ({'sourceIds': None, 'force': True}, 422),
])
def test_scope_validation_has_no_side_effects(bench, payload, expected):
    client, app, provider, _ = bench
    before = database_snapshot(app.state.store)
    assert client.post('/api/wiki/build-scope', headers=LOCAL, json=payload).status_code == expected
    assert database_snapshot(app.state.store) == before
    assert provider.calls == []
