"""Progress views select their own durable jobs before limiting and counting."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.store import SCHEMA, Store
from server.tests.test_writing_integrity import writing, enqueue
from server.writing import WritingService


@pytest.fixture
def progress(tmp_path):
    app = create_app(data_dir=tmp_path / 'isolated-progress')
    with TestClient(app) as client:
        yield client, app.state.store


def queue_status(store, kind, status):
    job = store.queue(kind, {}, kind)
    with store.connect() as db:
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, job['id']))
    return job['id']


def test_progress_scopes_separate_all_writing_kinds_and_keep_default_compatible(progress):
    client, store = progress
    ingestion = {
        queue_status(store, 'file', 'succeeded'),
        queue_status(store, 'url', 'queued'),
        queue_status(store, 'corpus', 'cancelled'),
        queue_status(store, 'ai_translation', 'running'),
        queue_status(store, 'wiki_build', 'failed'),
        queue_status(store, 'cover_selection', 'queued'),
        # The underscore in writing_ is literal, rather than a SQL wildcard.
        queue_status(store, 'writingXdocument', 'succeeded'),
    }
    writing = {
        queue_status(store, 'writing_outline', 'succeeded'),
        queue_status(store, 'writing_section', 'failed'),
        queue_status(store, 'writing_edit', 'cancelled'),
        queue_status(store, 'writing_document', 'running'),
        queue_status(store, 'writing_future', 'queued'),
    }
    default = client.get('/api/jobs').json()
    assert default == client.get('/api/jobs', params={'scope': 'all'}).json()
    assert {job['id'] for job in default['jobs']} == ingestion | writing
    assert default['counts']['total'] == 12

    ingested = client.get('/api/jobs', params={'scope': 'ingest'})
    assert ingested.status_code == 200
    assert {job['id'] for job in ingested.json()['jobs']} == ingestion
    assert ingested.json()['counts'] == {
        'queued': 2, 'running': 1, 'succeeded': 2,
        'failed': 1, 'cancelled': 1, 'total': 7,
    }
    written = client.get('/api/jobs', params={'scope': 'writing'})
    assert written.status_code == 200
    assert {job['id'] for job in written.json()['jobs']} == writing
    assert written.json()['counts'] == {
        'queued': 1, 'running': 1, 'succeeded': 1,
        'failed': 1, 'cancelled': 1, 'total': 5,
    }


@pytest.mark.parametrize('scope,own_kind,other_kind', [
    ('ingest', 'file', 'writing_document'),
    ('writing', 'writing_document', 'file'),
])
def test_progress_filter_precedes_limit_and_counts_include_unlisted_jobs(progress, scope, own_kind, other_kind):
    client, store = progress
    own = {queue_status(store, own_kind, 'succeeded') for _ in range(3)}
    # Higher-priority jobs from the other view must not fill this view's page.
    for _ in range(4):
        queue_status(store, other_kind, 'running')
    response = client.get('/api/jobs', params={'scope': scope, 'limit': 2})
    assert response.status_code == 200
    result = response.json()
    assert len(result['jobs']) == 2
    assert {job['id'] for job in result['jobs']} <= own
    assert result['counts'] == {
        'queued': 0, 'running': 0, 'succeeded': 3,
        'failed': 0, 'cancelled': 0, 'total': 3,
    }


def test_empty_scope_has_zero_counts_and_unknown_scope_is_rejected(progress):
    client, store = progress
    queue_status(store, 'file', 'queued')
    empty = client.get('/api/jobs', params={'scope': 'writing'}).json()
    assert empty == {'jobs': [], 'counts': {
        'queued': 0, 'running': 0, 'succeeded': 0,
        'failed': 0, 'cancelled': 0, 'total': 0,
    }}
    for scope in ('', 'imports', "writing' OR 1=1 --"):
        assert client.get('/api/jobs', params={'scope': scope}).status_code == 422
    with pytest.raises(ValueError, match='任务范围'):
        store.jobs(scope='unknown')
    assert store.jobs()['counts']['total'] == 1


def test_hidden_ingest_failure_leaves_progress_counts_but_preserves_job_audit(progress):
    client, store = progress
    old_failure = queue_status(store, 'ai_translation', 'failed')
    completed = queue_status(store, 'file', 'succeeded')
    writing_failure = queue_status(store, 'writing_section', 'failed')
    with store.connect() as db:
        db.execute('UPDATE jobs SET error=? WHERE id=?', ('历史加工错误', old_failure))
    original = client.get(f'/api/jobs/{old_failure}').json()
    with store.connect() as db:
        db.execute('UPDATE jobs SET progress_hidden=1 WHERE id=?', (old_failure,))

    result = client.get('/api/jobs', params={'scope': 'ingest'}).json()
    assert {job['id'] for job in result['jobs']} == {completed}
    assert result['counts'] == {
        'queued': 0, 'running': 0, 'succeeded': 1,
        'failed': 0, 'cancelled': 0, 'total': 1,
    }
    all_jobs = client.get('/api/jobs').json()
    assert {job['id'] for job in all_jobs['jobs']} == {completed, writing_failure}
    assert all_jobs['counts']['failed'] == 1
    assert client.get(f'/api/jobs/{old_failure}').json() == original
    assert store.job(old_failure)['error'] == '历史加工错误'


def test_new_ingest_progress_and_future_failure_remain_visible_after_cleanup(progress):
    client, store = progress
    old_failure = queue_status(store, 'file', 'failed')
    with store.connect() as db:
        db.execute('UPDATE jobs SET progress_hidden=1 WHERE id=?', (old_failure,))
    new_job = store.queue('file', {}, '新入库资料')['id']
    result = client.get('/api/jobs', params={'scope': 'ingest'}).json()
    assert [job['id'] for job in result['jobs']] == [new_job]
    assert result['counts']['queued'] == 1
    with store.connect() as db:
        assert db.execute('SELECT progress_hidden FROM jobs WHERE id=?', (new_job,)).fetchone()[0] == 0
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (new_job,))
    store.fail(new_job, '新任务错误仍需显示')
    result = client.get('/api/jobs', params={'scope': 'ingest'}).json()
    assert [job['id'] for job in result['jobs']] == [new_job]
    assert result['jobs'][0]['error'] == '新任务错误仍需显示'
    assert result['counts']['failed'] == result['counts']['total'] == 1


def test_explicit_retry_restores_hidden_ingest_progress(progress):
    client, store = progress
    old_failure = queue_status(store, 'file', 'failed')
    with store.connect() as db:
        db.execute('UPDATE jobs SET progress_hidden=1 WHERE id=?', (old_failure,))
    response = client.post(f'/api/jobs/{old_failure}/retry', headers={'X-Wenshu-Client': 'local'})
    assert response.status_code == 200
    assert response.json()['job']['status'] == 'queued'
    with store.connect() as db:
        assert db.execute('SELECT progress_hidden FROM jobs WHERE id=?', (old_failure,)).fetchone()[0] == 0
    result = client.get('/api/jobs', params={'scope': 'ingest'}).json()
    assert [job['id'] for job in result['jobs']] == [old_failure]
    assert result['counts']['queued'] == result['counts']['total'] == 1


@pytest.mark.parametrize('kind,scope', [('file', 'ingest'), ('writing_section', 'writing')])
@pytest.mark.parametrize('status', ['queued', 'running'])
def test_progress_hidden_flag_never_hides_active_tasks(progress, kind, scope, status):
    client, store = progress
    active = queue_status(store, kind, status)
    with store.connect() as db:
        db.execute('UPDATE jobs SET progress_hidden=1 WHERE id=?', (active,))
    result = client.get('/api/jobs', params={'scope': scope}).json()
    assert [job['id'] for job in result['jobs']] == [active]
    assert result['counts'][status] == result['counts']['total'] == 1


def test_existing_writing_progress_cleanup_keeps_ingest_and_active_writing(progress):
    client, store = progress
    completed = {queue_status(store, 'writing_section', status)
                 for status in ('succeeded', 'failed', 'cancelled')}
    active = {queue_status(store, 'writing_section', status) for status in ('queued', 'running')}
    ingest = queue_status(store, 'file', 'failed')
    assert store.clear_writing_progress() == 3
    assert store.clear_writing_progress() == 0
    result = client.get('/api/jobs', params={'scope': 'writing'}).json()
    assert {job['id'] for job in result['jobs']} == active
    assert result['counts'] == {
        'queued': 1, 'running': 1, 'succeeded': 0,
        'failed': 0, 'cancelled': 0, 'total': 2,
    }
    assert {job['id'] for job in client.get('/api/jobs').json()['jobs']} == active | {ingest}
    assert all(client.get(f'/api/jobs/{job_id}').status_code == 200 for job_id in completed)


def test_clear_writing_progress_is_scoped_idempotent_and_filters_before_limit(progress):
    client, store = progress
    ingest = {queue_status(store, kind, 'succeeded') for kind in ('file', 'writingXdocument')}
    ingest_before = store.jobs(scope='ingest')
    hidden = {queue_status(store, 'writing_document', status)
              for status in ('succeeded', 'failed', 'cancelled')}
    active = {queue_status(store, 'writing_document', status) for status in ('queued', 'running')}
    assert store.clear_writing_progress() == 3
    assert store.clear_writing_progress() == 0
    assert store.jobs(scope='ingest') == ingest_before
    for scope, expected, counts in (
        ('writing', active, {'queued': 1, 'running': 1, 'succeeded': 0,
                            'failed': 0, 'cancelled': 0, 'total': 2}),
        ('ingest', ingest, {'queued': 0, 'running': 0, 'succeeded': 2,
                           'failed': 0, 'cancelled': 0, 'total': 2}),
        ('all', active | ingest, {'queued': 1, 'running': 1, 'succeeded': 2,
                                'failed': 0, 'cancelled': 0, 'total': 4}),
    ):
        full = client.get('/api/jobs', params={'scope': scope}).json()
        assert {job['id'] for job in full['jobs']} == expected
        assert full['counts'] == counts
        limited = client.get('/api/jobs', params={'scope': scope, 'limit': 1}).json()
        assert len(limited['jobs']) == 1
        assert limited['jobs'][0]['id'] in expected
        assert limited['counts'] == counts
    with store.connect() as db:
        assert {row['id'] for row in db.execute('SELECT id FROM jobs WHERE progress_hidden=1')} == hidden
    # Finishing an active task after the clear must not silently hide its result.
    finished = next(iter(active))
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (finished,))
    new = queue_status(store, 'writing_section', 'succeeded')
    assert {job['id'] for job in store.jobs(scope='writing')['jobs']} == active | {new}


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_explicit_retry_restores_hidden_writing_progress(progress, status):
    _, store = progress
    job_id = queue_status(store, 'writing_section', status)
    assert store.clear_writing_progress() == 1
    assert store.jobs(scope='writing')['counts']['total'] == 0
    assert store.job(job_id)['status'] == status
    retried = store.retry(job_id)
    assert retried['status'] == 'queued'
    assert store.jobs(scope='writing')['jobs'] == [retried]
    assert store.jobs(scope='all')['counts']['queued'] == 1
    with store.connect() as db:
        assert db.execute('SELECT progress_hidden FROM jobs WHERE id=?', (job_id,)).fetchone()[0] == 0


def test_clear_preserves_manuscript_sources_history_and_operations_after_restart(writing):
    store, gateway, service, _, initial = writing
    run, job = enqueue(writing)
    service.process(job)
    job_before = store.job(job['id'])
    assert job_before['status'] == 'succeeded'
    project_before = service.get(initial['id'])
    assert project_before['sections'][0]['content']
    run_before = service.operations.run(run['runId'])
    calls_before = service.operations.calls(run['runId'])
    assert calls_before['total'] > 0
    call_before = service.operations.call(calls_before['items'][0]['id'])
    tables = ('sources', 'revisions', 'blocks', 'writing_projects', 'writing_history',
              'writing_runs', 'writing_model_interactions', 'writing_calls',
              'writing_document_steps', 'writing_document_calls',
              'writing_citation_match_steps', 'job_prompt_snapshots')

    def snapshot():
        with store.connect() as db:
            return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                    for table in tables}

    before = snapshot()
    assert store.clear_writing_progress() == 1
    assert snapshot() == before
    # A new Store/Service instance represents the persisted application state.
    restarted = Store(store.root)
    restored_service = WritingService(restarted, gateway)
    assert restarted.jobs(scope='writing')['counts']['total'] == 0
    assert restarted.job(job['id']) == job_before
    assert restored_service.get(initial['id']) == project_before
    assert restored_service.operations.run(run['runId']) == run_before
    assert restored_service.operations.calls(run['runId']) == calls_before
    assert restored_service.operations.call(calls_before['items'][0]['id']) == call_before
    assert restarted.jobs(scope='ingest')['counts']['total'] == 1
    with restarted.connect() as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_existing_database_progress_visibility_migration_is_idempotent(tmp_path):
    root = tmp_path / 'legacy-progress'
    root.mkdir()
    legacy_schema = SCHEMA.replace(' progress_hidden INTEGER NOT NULL DEFAULT 0,\n', '')
    assert legacy_schema != SCHEMA
    with sqlite3.connect(root / 'library.sqlite3') as db:
        db.executescript(legacy_schema)
        db.execute("""INSERT INTO jobs(id,batch_id,label,kind,input_json,status,stage,created_at,updated_at)
            VALUES('legacy-job','legacy-batch','保留任务','writing_section','{}',
                   'failed','failed','original-time','original-time')""")
    upgraded = Store(root)
    assert upgraded.jobs(scope='writing')['counts']['total'] == 1
    with upgraded.connect() as db:
        assert db.execute('SELECT progress_hidden FROM jobs').fetchone()[0] == 0
    assert upgraded.clear_writing_progress() == 1
    restarted = Store(root)
    assert restarted.clear_writing_progress() == 0
    assert restarted.jobs(scope='writing')['counts']['total'] == 0
    assert restarted.job('legacy-job')['updated_at'] == 'original-time'
