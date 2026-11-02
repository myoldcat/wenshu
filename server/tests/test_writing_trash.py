"""Manuscript deletion uses isolated stores and controlled, offline gateways."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.store import Store, dumps, now
from server.writing import WritingConflict, WritingService
from server.tests.test_writing_integrity import ControlledGateway, writing, enqueue, finish


LOCAL = {'X-Wenshu-Client': 'local'}


def test_legacy_schema_migration_preserves_manuscript_and_is_idempotent(tmp_path):
    store = Store(tmp_path / 'legacy-writing')
    timestamp = now()
    original = {'id': 'old', 'version': 7, 'title': '保留旧文稿', 'sections': []}
    with store.connect() as db:
        db.execute('CREATE TABLE writing_projects(id TEXT PRIMARY KEY,version INTEGER NOT NULL,content_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)')
        db.execute('INSERT INTO writing_projects VALUES(?,?,?,?,?)', ('old', 7, dumps(original), timestamp, timestamp))
    service = WritingService(store, ControlledGateway())
    assert service.list() == [{**original, 'runs': []}]
    with store.connect() as db:
        row = db.execute('SELECT * FROM writing_projects WHERE id=?', ('old',)).fetchone()
        assert row['deleted_at'] is None and json.loads(row['content_json']) == original
    WritingService(store, ControlledGateway())
    assert service.list() == [{**original, 'runs': []}]
    assert service.create({'title': '迁移后的新稿'})['version'] == 1


def test_soft_delete_preserves_original_text_history_and_audit(writing):
    store, _, service, source, _ = writing
    project = finish(writing)
    history = service.history(project['id'])['versions']
    with store.connect() as db:
        original = json.loads(db.execute('SELECT content_json FROM writing_projects WHERE id=?', (project['id'],)).fetchone()[0])
    result = service.delete(project['id'], project['version'])
    assert result == {'deleted': True, 'projectId': project['id'], 'deletedAt': result['deletedAt'], 'version': project['version'] + 1}
    assert result['deletedAt'] and service.list() == []
    with store.connect() as db:
        row = db.execute('SELECT * FROM writing_projects WHERE id=?', (project['id'],)).fetchone()
        saved = json.loads(row['content_json'])
        assert row['deleted_at'] == result['deletedAt']
        assert saved['sections'] == original['sections']
        assert db.execute('SELECT count(*) FROM writing_history WHERE project_id=?', (project['id'],)).fetchone()[0] == len(history) + 1
        assert db.execute('SELECT count(*) FROM writing_model_interactions WHERE project_id=?', (project['id'],)).fetchone()[0] == 1  # Generation only; no implicit review.
        assert db.execute('SELECT current_revision FROM sources WHERE id=?', (source,)).fetchone()
    item = service.operations.projects(state='deleted')['items'][0]
    assert item['id'] == project['id'] and item['deletedAt'] == result['deletedAt'] and item['version'] == result['version']
    assert service.operations.runs(project['id'])['total'] == 1
    assert service.operations.projects(state='active')['total'] == 0
    assert service.operations.projects()['total'] == 1


@pytest.mark.parametrize('operation', ['get', 'history', 'historical', 'patch', 'outline', 'confirm', 'save', 'review', 'export', 'export_snapshot', 'generation_plan', 'queue', 'document', 'citation', 'rebind', 'edit', 'apply', 'apply_document', 'delete'])
def test_deleted_manuscript_is_not_readable_or_mutable_through_normal_service(writing, operation):
    _, _, service, _, project = writing
    project_id, version, section = project['id'], project['version'], project['sections'][0]['id']
    service.delete(project_id, version)
    actions = {
        'get': lambda: service.get(project_id),
        'history': lambda: service.history(project_id),
        'historical': lambda: service.historical(project_id, 1),
        'patch': lambda: service.patch(project_id, version, {'title': '不能复活'}),
        'outline': lambda: service.outline(project_id, version, []),
        'confirm': lambda: service.confirm(project_id, version),
        'save': lambda: service.save_section(project_id, section, version, {'content': '不能复活'}),
        'review': lambda: service.review(project_id, version),
        'export': lambda: service.export(project_id),
        'export_snapshot': lambda: service.export_snapshot(project_id, version, 'html', True),
        'generation_plan': lambda: service.generation_plan(project_id),
        'queue': lambda: service.queue(project_id, version, 'section', section),
        'document': lambda: service.queue_document(project_id, version),
        'citation': lambda: service.queue_citation_match(project_id, version),
        'rebind': lambda: service.rebind_citations(project_id, version, [{'sectionId': section}]),
        'edit': lambda: service.queue_edit(project_id, version, section, {'operation': 'polish'}),
        'apply': lambda: service.apply(project_id, 'missing-run', version),
        'apply_document': lambda: service.apply_document_section(project_id, 'missing-run', section, version),
        'delete': lambda: service.delete(project_id, version),
    }
    with pytest.raises(KeyError):
        actions[operation]()


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_soft_delete_checks_version_and_rejects_active_tasks(writing, status):
    store, _, service, _, project = writing
    with pytest.raises(WritingConflict):
        service.delete(project['id'], project['version'] - 1)
    queued, _ = enqueue(writing)
    with store.connect() as db:
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, queued['job']['id']))
    with pytest.raises(WritingConflict, match='任务'):
        service.delete(project['id'], project['version'])
    assert service.get(project['id'])['version'] == project['version']
    store.cancel(queued['job']['id'])
    deleted = service.delete(project['id'], project['version'])
    assert deleted['version'] == project['version'] + 1
    assert store.job(queued['job']['id'])['retryable'] is False
    with pytest.raises(ValueError, match='文章已删除'):
        store.retry(queued['job']['id'])
    # The database fence also rejects stale task payloads from older clients.
    with store.connect() as db:
        db.execute('UPDATE jobs SET input_json=? WHERE id=?', (dumps({'runId': queued['runId']}), queued['job']['id']))
    with pytest.raises(ValueError, match='文章已删除'):
        store.retry(queued['job']['id'])


def seed_cleanup_records(writing):
    store, _, service, source, _ = writing
    project = finish(writing)
    other = service.create({'title': '其他文章必须保留', 'sourceIds': [source]})
    queued = service.queue(other['id'], other['version'], 'outline')
    store.cancel(queued['job']['id'])
    run = project['runs'][0]
    timestamp = now()
    with store.connect() as db:
        db.execute('INSERT INTO writing_document_steps VALUES(?,?,?,?,?,?,?)', (run['id'], 'checkpoint', 0, '{"frozen":true}', '{"checkpoint":true}', 'candidate', '保留检查点'))
        db.execute('INSERT INTO writing_document_calls VALUES(?,?,?,?,?,?)', (run['id'], 'checkpoint', 1, 2, '{}', timestamp))
        db.execute('INSERT INTO writing_citation_match_steps VALUES(?,?,?,?,?,?)', (run['id'], 'checkpoint', 0, 0, '{"frozen":true}', '{"assignments":[]}'))
        for recommendation in ('shared-materials', 'exclusive-materials'):
            db.execute('INSERT INTO writing_project_recommendations VALUES(?,?,?)', (project['id'], recommendation, timestamp))
            db.execute('INSERT INTO writing_material_interactions(id,recommendation_id,http_attempt,transport,model_json,request_json,started_at,status) VALUES(?,?,?,?,?,?,?,?)', ('call-' + recommendation, recommendation, 1, 'injected', '{}', '{"messages":[]}', timestamp, 'succeeded'))
        db.execute('INSERT INTO writing_project_recommendations VALUES(?,?,?)', (other['id'], 'shared-materials', timestamp))
    return project, other, run


def table_counts(store):
    with store.connect() as db:
        tables = [row['name'] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name GLOB 'writing_*' OR name IN ('jobs','job_prompt_snapshots','sources','revisions','blocks'))")]
        return {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in tables}


def test_purge_deletes_project_tree_but_preserves_sources_shared_materials_and_other_project(writing):
    store, _, service, source, _ = writing
    project, other, run = seed_cleanup_records(writing)
    with pytest.raises(WritingConflict, match='先在写作空间删除'):
        service.operations.purge(project['id'], project['version'])
    before = table_counts(store)
    deleted = service.delete(project['id'], project['version'])
    with pytest.raises(WritingConflict):
        service.operations.purge(project['id'], project['version'])
    assert service.operations.purge(project['id'], deleted['version']) == {'purged': True, 'projectId': project['id']}
    with store.connect() as db:
        for table in ('writing_projects', 'writing_history', 'writing_runs', 'writing_model_interactions', 'writing_project_recommendations'):
            key = 'id' if table == 'writing_projects' else 'project_id'
            assert not db.execute(f'SELECT 1 FROM {table} WHERE {key}=?', (project['id'],)).fetchone()
        for table in ('writing_calls', 'writing_document_steps', 'writing_document_calls', 'writing_citation_match_steps'):
            assert not db.execute(f'SELECT 1 FROM {table} WHERE run_id=?', (run['id'],)).fetchone()
        assert not db.execute('SELECT 1 FROM jobs WHERE id=?', (run['jobId'],)).fetchone()
        assert not db.execute('SELECT 1 FROM job_prompt_snapshots WHERE job_id=?', (run['jobId'],)).fetchone()
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('SELECT current_revision FROM sources WHERE id=?', (source,)).fetchone()
    after = table_counts(store)
    assert {key: after[key] for key in ('sources', 'revisions', 'blocks', 'writing_material_interactions')} == {key: before[key] for key in ('sources', 'revisions', 'blocks', 'writing_material_interactions')}
    assert service.get(other['id'])['title'] == other['title']
    assert service.operations.runs(other['id'])['total'] == 2  # Its own run plus shared recommendation.
    assert service.operations.runs('preparations')['items'][0]['id'] == 'materials:exclusive-materials'
    with pytest.raises(KeyError):
        store.retry(run['jobId'])


def test_purge_rolls_back_all_leaf_deletions_on_unexpected_dependency(writing):
    store, _, service, _, _ = writing
    project, _, _ = seed_cleanup_records(writing)
    deleted = service.delete(project['id'], project['version'])
    with store.connect() as db:
        db.execute('CREATE TABLE deletion_guard(project_id TEXT REFERENCES writing_projects(id))')
        db.execute('INSERT INTO deletion_guard VALUES(?)', (project['id'],))
    before = table_counts(store)
    with pytest.raises(sqlite3.IntegrityError):
        service.operations.purge(project['id'], deleted['version'])
    assert table_counts(store) == before
    assert service.operations.projects(state='deleted')['items'][0]['id'] == project['id']


def test_purge_rechecks_task_state_and_deleted_version(writing):
    store, _, service, _, project = writing
    queued, _ = enqueue(writing)
    store.cancel(queued['job']['id'])
    deleted = service.delete(project['id'], project['version'])
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (queued['job']['id'],))
    with pytest.raises(WritingConflict, match='任务'):
        service.operations.purge(project['id'], deleted['version'])
    assert service.operations.projects(state='deleted')['total'] == 1


def test_cancelled_inflight_reply_cannot_resurrect_purged_manuscript(writing):
    store, gateway, service, _, project = writing
    queued, job = enqueue(writing)

    def remove_during_call():
        store.cancel(queued['job']['id'])
        deleted = service.delete(project['id'], project['version'])
        service.operations.purge(project['id'], deleted['version'])

    gateway.callback = remove_during_call
    service.process(job)
    assert len(gateway.calls) == 1 and service.list() == []
    assert service.operations.projects()['total'] == 0
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM writing_model_interactions').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM writing_runs').fetchone()[0] == 0
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_http_deletion_contract_filters_and_private_operations_boundary(writing):
    store, gateway, _, _, project = writing
    app = create_app(store.root, model_service=gateway)
    with TestClient(app) as client:
        path = '/api/writing/projects/' + project['id']
        ops = '/api/operations/writing/projects'
        version = {'expectedVersion': project['version']}
        assert client.request('DELETE', path, json=version).status_code == 403
        assert client.request('DELETE', path, headers=LOCAL, json={'expectedVersion': 0}).status_code == 422
        assert client.request('DELETE', ops + '/' + project['id'], headers=LOCAL, json=version).status_code == 409
        response = client.request('DELETE', path, headers=LOCAL, json=version)
        assert response.status_code == 200, response.text
        deleted = response.json()
        assert deleted['deleted'] is True and deleted['projectId'] == project['id']
        assert client.get('/api/writing/projects').json() == {'projects': []}
        for suffix in ('', '/history', '/history/1', '/export', '/generation-plan'):
            assert client.get(path + suffix).status_code == 404
        assert client.patch(path, headers=LOCAL, json={**version, 'title': '不能恢复'}).status_code == 404
        items = client.get(ops, params={'state': 'deleted'}).json()['items']
        assert items[0]['deletedAt'] == deleted['deletedAt'] and items[0]['version'] == deleted['version']
        assert client.get(ops, params={'state': 'active'}).json()['total'] == 0
        assert client.get(ops, params={'state': 'invalid'}).status_code == 422
        purge_path = ops + '/' + project['id']
        current = {'expectedVersion': deleted['version']}
        assert client.request('DELETE', purge_path, headers={**LOCAL, 'Origin': 'https://public.test'}, json=current).status_code == 403
        assert client.request('DELETE', purge_path, headers=LOCAL, json=version).status_code == 409
        assert client.request('DELETE', purge_path, headers=LOCAL, json=current).json() == {'purged': True, 'projectId': project['id']}
        assert client.get(ops).json()['total'] == 0
        assert client.request('DELETE', purge_path, headers=LOCAL, json=current).status_code == 404
