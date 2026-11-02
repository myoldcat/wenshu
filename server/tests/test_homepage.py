"""Read-only homepage statistics use isolated stores, never real materials."""
import copy

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.home_api import writing_phase
from server.store import dumps


STAMP = '2026-10-24T01:00:00+00:00'
PHASES = ('brief', 'outline', 'body', 'images', 'review', 'ready')


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / 'isolated-homepage')

    def forbidden_model(*args, **kwargs):
        raise AssertionError('Homepage operations must not call a model')

    monkeypatch.setattr(app.state.models, 'complete', forbidden_model)
    with TestClient(app) as client:
        yield client, app.state.store, app.state.writing


def preserved_content(store):
    tables = ('sources', 'revisions', 'blocks', 'jobs', 'writing_projects', 'writing_history')
    with store.connect() as db:
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in tables}


def insert_project(store, project_id, **updates):
    project = {'id': project_id, 'version': 3, 'title': project_id, 'sections': [],
               'outlineConfirmed': False, 'createdAt': STAMP, 'updatedAt': STAMP,
               'brief': {}, **updates}
    with store.connect() as db:
        db.execute('INSERT INTO writing_projects(id,version,content_json,created_at,updated_at,deleted_at) '
                   'VALUES(?,?,?,?,?,?)', (project_id, project['version'], dumps(project), STAMP, STAMP,
                                         project.get('deletedAt')))
    return project


def test_home_empty_and_get_is_read_only(workbench):
    client, store, _ = workbench
    before = preserved_content(store)
    response = client.get('/api/home')
    assert response.status_code == 200
    assert response.json() == {'writing': {'total': 0, 'totalWords': 0,
                                          'phaseCounts': dict.fromkeys(PHASES, 0)}}
    assert preserved_content(store) == before


def test_home_counts_all_current_manuscripts_beyond_list_cap_and_excludes_deleted(workbench):
    client, store, writing = workbench
    # 105 rows exceed the separate manuscript-card API's cap of 100.
    for index in range(105):
        insert_project(store, f'project-{index}', sections=[{'content': '中文 alpha 123 [E2]【模型补充·待核实】'}])
    insert_project(store, 'deleted-manuscript', deletedAt=STAMP,
                   sections=[{'content': '删除正文不应计入首页。' * 200}])
    with store.connect() as db:
        old = {'id': 'project-0', 'sections': [{'content': '过往正文不应重复计入。' * 200}]}
        db.execute('INSERT INTO writing_history VALUES(?,?,?,?,?)',
                   ('project-0', 1, dumps(old), 'old-revision', STAMP))
    before = preserved_content(store)
    assert len(writing.list()) == 100
    response = client.get('/api/home')
    assert response.status_code == 200
    summary = response.json()['writing']
    assert summary['total'] == 105
    # Existing count rule: each Han character plus each Latin/number word group;
    # registered evidence markers and model-supplement labels are excluded.
    assert summary['totalWords'] == 105 * 4
    assert summary['phaseCounts'] == {**dict.fromkeys(PHASES, 0), 'outline': 105}
    assert sum(summary['phaseCounts'].values()) == summary['total']
    assert preserved_content(store) == before


def test_home_phase_counts_are_mutually_exclusive_and_use_current_saved_text(workbench):
    client, store, _ = workbench
    full = [{'content': '中文 alpha 123 [E2]【模型补充·待核实】'}]
    insert_project(store, 'brief')
    insert_project(store, 'outline', sections=copy.deepcopy(full))
    insert_project(store, 'body', outlineConfirmed=True, sections=[{'content': '中文'}, {'content': '  '}])
    insert_project(store, 'images', outlineConfirmed=True, sections=copy.deepcopy(full))
    insert_project(store, 'review', outlineConfirmed=True, sections=copy.deepcopy(full),
                   illustrationReview={'status': 'skipped'}, review={'version': 2, 'wordCounts': {'total': 999999}})
    insert_project(store, 'ready', outlineConfirmed=True, sections=copy.deepcopy(full),
                   illustrationReview={'status': 'complete'}, review={'version': 3, 'wordCounts': {'total': 999999}})
    before = preserved_content(store)
    summary = client.get('/api/home').json()['writing']
    assert summary == {'total': 6, 'totalWords': 18, 'phaseCounts': dict.fromkeys(PHASES, 1)}
    assert preserved_content(store) == before


@pytest.mark.parametrize(('changes', 'phase'), [
    ({'sections': []}, 'brief'),
    ({'outlineConfirmed': False}, 'outline'),
    ({'sections': [{'content': '\n  '}]}, 'body'),
    ({'sections': [{'content': '\ufeff\u3000'}]}, 'body'),
    ({'illustrationReview': None}, 'images'),
    ({'illustrationReview': {'status': 'stale'}}, 'images'),
    ({'illustrations': [{'status': 'stale'}]}, 'images'),
    ({'review': None}, 'review'),
    ({'review': {'version': 2, 'wordCounts': {}}}, 'review'),
    ({'review': {'version': 3, 'wordCounts': False}}, 'review'),
    ({'review': {'version': 3, 'wordCounts': {}}}, 'ready'),
    ({'illustrationReview': {'status': 'skipped'}}, 'ready'),
])
def test_phase_matches_frontend_priority_and_empty_object_truthiness(changes, phase):
    project = {'version': 3, 'sections': [{'content': '正文'}], 'outlineConfirmed': True,
               'illustrationReview': {'status': 'complete'}, 'illustrations': [],
               'review': {'version': 3, 'wordCounts': {'total': 2}}, **changes}
    assert writing_phase(project) == phase
