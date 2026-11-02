"""Explicit whole-scope scheduling with isolated documents and a fake gateway."""
import pytest

from server.tests.test_wiki_api import bench, LOCAL
from server.tests.test_wiki_scope import database_snapshot
from server.worker import run_one


def test_bulk_reads_are_pure_and_click_freezes_scope_without_running_models(bench):
    client, app, provider, source = bench
    before = database_snapshot(app.state.store)
    assert client.get('/api/wiki/build-all').json() == {'runs': []}
    assert database_snapshot(app.state.store) == before
    response = client.post('/api/wiki/build-all', headers=LOCAL, json={'sourceIds': [source, source]})
    assert response.status_code == 202
    created = response.json()
    run = created['run']
    assert created['reused'] is False and run['total'] == 1 and run['batchSize'] == 20
    assert run['scope'] == 'selected' and run['counts']['pending'] == 1
    assert run['job']['kind'] == 'wiki_build_all' and run['job']['retryable'] is False
    repeated = client.post('/api/wiki/build-all', headers=LOCAL, json={'sourceIds': [source]}).json()
    assert repeated['reused'] is True and repeated['run']['id'] == run['id']
    before = database_snapshot(app.state.store)
    assert client.get('/api/wiki/build-all').json()['runs'][0] == run
    assert client.get('/api/wiki/build-all/' + run['id']).json() == {'run': run}
    assert database_snapshot(app.state.store) == before and provider.calls == []


def test_bulk_worker_dispatches_child_without_parent_lane_deadlock(bench):
    client, app, provider, source = bench
    run = client.post('/api/wiki/build-all', headers=LOCAL, json={}).json()['run']
    assert run['scope'] == 'library'
    assert run_one(app.state.store, app.state.enrichment, app.state.wiki)
    assert app.state.wiki.all_run(run['id'])['counts']['succeeded'] == 1
    assert len(provider.calls) == 1
    assert app.state.wiki.advance_all()
    finished = client.get('/api/wiki/build-all/' + run['id']).json()['run']
    assert finished['job']['status'] == 'succeeded' and finished['counts']['pending'] == 0


def test_bulk_cancel_cancels_only_its_pending_scope_and_blocks_parent_retry(bench):
    client, app, provider, source = bench
    run = client.post('/api/wiki/build-all', headers=LOCAL, json={'sourceIds': [source]}).json()['run']
    response = client.post(f"/api/jobs/{run['id']}/cancel", headers=LOCAL)
    assert response.status_code == 200 and response.json()['job']['status'] == 'cancelled'
    cancelled = client.get('/api/wiki/build-all/' + run['id']).json()['run']
    assert cancelled['counts']['cancelled'] == 1 and cancelled['counts']['pending'] == 0
    assert app.state.wiki.advance_all() is False
    assert client.post(f"/api/jobs/{run['id']}/retry", headers=LOCAL).status_code == 422
    assert provider.calls == []


def test_bulk_explicit_empty_scope_does_not_expand_to_library(bench):
    client, app, provider, _ = bench
    before = database_snapshot(app.state.store)
    assert client.post('/api/wiki/build-all', headers=LOCAL, json={'sourceIds': []}).json() == {'run': None, 'reused': False}
    assert database_snapshot(app.state.store) == before and provider.calls == []


@pytest.mark.parametrize('payload,status', [
    ({'sourceIds': ['missing']}, 404), ({'sourceIds': [' ']}, 422),
    ({'sourceIds': [False]}, 422), ({'sourceIds': ['x'] * 10001}, 422),
    ({'sourceIds': None, 'force': True}, 422),
])
def test_bulk_invalid_scope_never_schedules(payload, status, bench):
    client, app, provider, _ = bench
    before = database_snapshot(app.state.store)
    assert client.post('/api/wiki/build-all', headers=LOCAL, json=payload).status_code == status
    assert database_snapshot(app.state.store) == before and provider.calls == []
