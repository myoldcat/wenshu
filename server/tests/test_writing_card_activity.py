"""Home cards read current job activity from isolated stores without a provider."""
from contextlib import contextmanager
import json

import pytest

from server.store import Store, dumps
from server.writing import WritingService


class OfflineGateway:
    def __getattr__(self, name):
        raise AssertionError(f'Card activity must not access a model gateway: {name}')


@pytest.fixture
def cards(tmp_path):
    store = Store(tmp_path / 'isolated-card-activity')
    return store, WritingService(store, OfflineGateway())


def project(store, project_id, *, timestamp='2026-10-23T01:00:00Z', deleted=False, **changes):
    value = {'id': project_id, 'version': 3, 'title': f'文稿 {project_id}', 'sections': [], **changes}
    with store.connect() as db:
        db.execute('INSERT INTO writing_projects(id,version,content_json,created_at,updated_at,deleted_at) VALUES(?,?,?,?,?,?)',
                   (project_id, 3, dumps(value), timestamp, timestamp, timestamp if deleted else None))
    return value


def run(store, project_id, run_id, status, *, kind='section', timestamp='2026-10-23T02:00:00Z'):
    job_id = f'job-{run_id}'
    with store.connect() as db:
        db.execute('INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                   (job_id, 'offline-fixture', '隔离写作任务', f'writing_{kind}', '{"privatePrompt":"not-for-home-cards"}', status, 'fixture', timestamp, timestamp))
        db.execute('''INSERT INTO writing_runs(id,job_id,project_id,kind,base_version,model_id,model_version,
                   model_json,prompt_version,input_json,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (run_id, job_id, project_id, kind, 3, 'offline-model', 1, '{"name":"private-model-details"}',
                    'offline', '{"prompt":"private-prompt-details"}', '{"candidate":"private-candidate-details"}', timestamp))
    return {'id': run_id, 'jobId': job_id, 'kind': kind, 'status': status}


def test_empty_list_never_asks_a_provider(cards):
    _, service = cards
    assert service.list() == []


def test_list_groups_only_current_queued_and_running_jobs_with_lightweight_fields(cards):
    store, service = cards
    first = project(store, 'first', timestamp='2026-10-23T03:00:00Z')
    second = project(store, 'second')
    expected = [run(store, 'first', 'pending', 'queued', kind='outline'),
                run(store, 'first', 'writing', 'running', kind='document')]
    image = run(store, 'second', 'images', 'running', kind='images')
    citation = run(store, 'second', 'citations', 'queued', kind='citation_match')
    for status in ['succeeded', 'failed', 'cancelled', 'paused']:
        run(store, 'first', f'historical-{status}', status)
    listed = service.list()
    assert [item['id'] for item in listed] == ['first', 'second']
    assert listed[0] == {**first, 'runs': sorted(expected, key=lambda item: item['id'], reverse=True)}
    assert listed[1] == {**second, 'runs': sorted([image, citation], key=lambda item: item['id'], reverse=True)}
    assert all(set(item) == {'id', 'jobId', 'kind', 'status'} for value in listed for item in value['runs'])
    encoded = json.dumps(listed)
    for private in ['privatePrompt', 'private-model-details', 'private-prompt-details', 'private-candidate-details']:
        assert private not in encoded


def test_list_uses_live_status_and_clears_serialized_activity_without_writing_snapshots(cards):
    store, service = cards
    saved = project(store, 'live', runs=[{'id': 'old', 'kind': 'document', 'status': 'running', 'candidate': 'historical'}])
    active = run(store, 'live', 'current', 'queued')
    assert service.list() == [{**saved, 'runs': [active]}]
    for status in ['running', 'succeeded', 'failed', 'cancelled']:
        with store.connect() as db:
            db.execute('UPDATE jobs SET status=? WHERE id=?', (status, active['jobId']))
        expected = [{**active, 'status': status}] if status == 'running' else []
        assert service.list() == [{**saved, 'runs': expected}]
    with store.connect() as db:
        assert json.loads(db.execute('SELECT content_json FROM writing_projects WHERE id=?', ('live',)).fetchone()[0]) == saved
        assert db.execute('SELECT count(*) FROM writing_history').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM writing_runs').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1


def test_list_reads_activity_in_one_join_scoped_to_the_visible_hundred(cards, monkeypatch):
    store, service = cards
    for index in range(101):
        project_id = f'visible-{index:03d}'
        project(store, project_id, timestamp=f'2026-10-{1 + index // 24:02d}T{index % 24:02d}:00:00Z')
        run(store, project_id, f'active-{index:03d}', 'running')
    project(store, 'deleted', timestamp='2026-10-31T00:00:00Z', deleted=True)
    run(store, 'deleted', 'deleted-active', 'running')
    statements = []
    original_connect = store.connect

    @contextmanager
    def traced_connect():
        with original_connect() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, 'connect', traced_connect)
    listed = service.list()
    assert len(listed) == 100
    assert [item['id'] for item in listed] == [f'visible-{index:03d}' for index in range(100, 0, -1)]
    assert all(len(item['runs']) == 1 and item['runs'][0]['status'] == 'running' for item in listed)
    activity_queries = [sql for sql in statements if 'FROM writing_runs r JOIN jobs j' in sql]
    assert len(activity_queries) == 1
    assert "'deleted'" not in activity_queries[0] and "'visible-000'" not in activity_queries[0]
    assert "'visible-001'" in activity_queries[0] and "'visible-100'" in activity_queries[0]
