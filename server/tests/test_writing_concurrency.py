"""Article slots are durable, bounded and held until physical calls return."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import time

import pytest

from server.store import Store
from server.worker import ConcurrentWorker, run_one


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'isolated-writing-concurrency')


def writing(store, project, *, parent=None):
    payload = {'projectId': project}
    if parent is not None:
        payload['automaticParentRunId'] = parent
    return store.queue('writing_automatic' if parent is None else 'writing_outline', payload, project)


def finish(store, job):
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='succeeded' WHERE id=? AND status='running' AND attempts=?",
                   (job['id'], job['attempts'] + 1))


def test_default_three_articles_and_excess_can_still_queue(store):
    queued = [writing(store, str(i)) for i in range(5)]
    assert [store.claim()['id'] for _ in range(3)] == [j['id'] for j in queued[:3]]
    assert store.claim() is None
    assert [store.job(j['id'])['status'] for j in queued] == ['running'] * 3 + ['queued'] * 2
    extra = writing(store, 'new-after-full')
    assert extra['status'] == 'queued'
    store.cancel(queued[1]['id'])
    assert store.claim()['id'] == queued[3]['id']


def test_same_article_is_serial_but_other_articles_fill_capacity(store):
    first, repeated, second, third = [writing(store, p) for p in ('a', 'a', 'b', 'c')]
    assert [store.claim()['id'] for _ in range(3)] == [j['id'] for j in (first, second, third)]
    assert store.claim() is None
    store.cancel(first['id'])
    assert store.claim()['id'] == repeated['id']


def test_fifo_uses_enqueue_order_for_equal_timestamps(store):
    queued = [writing(store, p) for p in ('first', 'second', 'third')]
    with store.connect() as db:
        for job, job_id in zip(queued, ('job_z', 'job_y', 'job_x')):
            db.execute('UPDATE jobs SET id=?,created_at=? WHERE id=?', (job_id, '2026-10-25', job['id']))
    assert [store.claim()['id'] for _ in range(3)] == ['job_z', 'job_y', 'job_x']


def test_generic_claim_never_steals_owned_automatic_children_after_recovery(store):
    child = writing(store, 'a', parent='frozen-parent-run')
    parent = writing(store, 'a')
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='running',attempts=1 WHERE id IN (?,?)", (child['id'], parent['id']))
    store.recover()
    claim = store.claim()
    assert claim['id'] == parent['id'] and claim['attempts'] == 1
    assert store.claim() is None
    assert store.job(child['id'])['status'] == 'queued'
    finish(store, claim)
    assert store.claim() is None


def test_other_lane_is_serial_and_not_blocked_by_three_writing_articles(store):
    for i in range(3):
        writing(store, str(i))
        assert store.claim(scope='writing')
    first = store.queue('ai_schedule', {}, 'first non-writing')
    second = store.queue('file', {}, 'second non-writing')
    claimed = store.claim(scope='other')
    assert claimed['id'] == first['id']
    assert store.claim(scope='other') is None
    assert store.claim(scope='writing') is None
    finish(store, claimed)
    assert store.claim(scope='other')['id'] == second['id']


def test_direct_claim_can_publish_a_new_revision_while_older_model_reply_is_inflight(store):
    writing(store, 'active-article')
    assert store.claim(scope='writing')
    old_model = store.queue('ai_classification', {}, 'old revision classification')
    assert store.claim()['id'] == old_model['id']
    new_revision = store.queue('file', {}, 'new source revision')
    # Production keeps its non-writing lane serial; direct claim callers retain
    # the ability to exercise an independently published version's reply fences.
    assert store.claim(scope='other') is None
    assert store.claim()['id'] == new_revision['id']


def test_atomic_claims_from_competing_threads_cannot_exceed_article_limit(store):
    for i in range(12):
        writing(store, str(i))
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: store.claim(scope='writing'), range(12)))
    claimed = [job for job in claims if job is not None]
    assert len(claimed) == len({job['id'] for job in claimed}) == 3
    assert store.jobs(scope='writing')['counts']['queued'] == 9


def test_configured_limit_and_physical_project_reservations(store):
    a, b, c = [writing(store, p) for p in ('a', 'b', 'c')]
    assert store.claim(scope='writing', writing_limit=1)['id'] == a['id']
    assert store.claim(scope='writing', writing_limit=1) is None
    store.cancel(a['id'])
    assert store.claim(scope='writing', writing_limit=1, excluded_project_ids={'a'}) is None
    assert store.claim(scope='writing', writing_limit=2, excluded_project_ids={'a'})['id'] == b['id']
    assert store.claim(scope='writing', writing_limit=2, excluded_project_ids={'a'}) is None
    assert store.job(c['id'])['status'] == 'queued'


class NoIdle:
    def maybe_queue_idle(self):
        return []


def wait_for(predicate, tick=None):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, 'controlled worker did not reach expected state'
        if tick is not None:
            tick()
        time.sleep(.01)


def controlled_processor():
    started, release, lock = {}, {}, Lock()

    def process(store, job):
        with lock:
            gate = release.setdefault(job['id'], Event())
            started.setdefault(job['id'], Event()).set()
        assert gate.wait(5), 'controlled HTTP request was not released'
        finish(store, job)

    return process, started, release


def test_worker_runs_three_physical_articles_and_promotes_fifo_on_return(store):
    queued = [writing(store, str(i)) for i in range(5)]
    processor, started, release = controlled_processor()
    worker = ConcurrentWorker(store, NoIdle(), processor=processor)
    try:
        worker.tick()
        wait_for(lambda: len(started) == 3)
        assert all(store.job(j['id'])['status'] == 'running' for j in queued[:3])
        assert all(store.job(j['id'])['status'] == 'queued' for j in queued[3:])
        store.cancel(queued[0]['id'])
        worker.tick()
        assert len(started) == 3 and store.job(queued[3]['id'])['status'] == 'queued'
        release[queued[1]['id']].set()
        wait_for(lambda: queued[3]['id'] in started, worker.tick)
        assert store.job(queued[4]['id'])['status'] == 'queued'
    finally:
        for gate in list(release.values()):
            gate.set()
        worker.close()


def test_cancelled_article_cannot_overlap_its_new_attempt_while_http_returns(store):
    first, second = [writing(store, p) for p in ('a', 'b')]
    processor, started, release = controlled_processor()
    worker = ConcurrentWorker(store, NoIdle(), processor=processor)
    try:
        worker.tick()
        wait_for(lambda: len(started) == 2)
        store.cancel(first['id'])
        retry, third = writing(store, 'a'), writing(store, 'c')
        worker.tick()
        wait_for(lambda: third['id'] in started)
        assert retry['id'] not in started and store.job(retry['id'])['status'] == 'queued'
        release[first['id']].set()
        wait_for(lambda: retry['id'] in started, worker.tick)
        assert store.job(first['id'])['status'] == 'cancelled'
    finally:
        for gate in list(release.values()):
            gate.set()
        worker.close()


def test_run_one_remains_a_synchronous_single_job_operation(store):
    first, second = writing(store, 'a'), writing(store, 'b')

    class Writing:
        def process(self, job):
            finish(store, job)

    assert run_one(store, NoIdle(), writing=Writing()) is True
    assert store.job(first['id'])['status'] == 'succeeded'
    assert store.job(second['id'])['status'] == 'queued'


@pytest.mark.parametrize('owner_status', ['queued', 'running', 'succeeded', 'failed', 'cancelled', 'missing'])
def test_recovery_preserves_active_owned_children_and_cancels_terminal_orphans(store, owner_status):
    parent, child = writing(store, 'article'), writing(store, 'article', parent='owner-run')
    with store.connect() as db:
        # Isolate the scheduler's ownership contract from generation protocols.
        db.execute('CREATE TABLE writing_runs(id TEXT PRIMARY KEY,job_id TEXT,project_id TEXT)')
        if owner_status != 'missing':
            db.execute('INSERT INTO writing_runs VALUES(?,?,?)', ('owner-run', parent['id'], 'article'))
        db.execute('INSERT INTO writing_runs VALUES(?,?,?)', ('child-run', child['id'], 'article'))
        db.execute('UPDATE jobs SET status=? WHERE id=?', (owner_status if owner_status != 'missing' else 'succeeded', parent['id']))
        db.execute("UPDATE jobs SET status='running',attempts=1 WHERE id=?", (child['id'],))
    store.recover()
    assert store.job(child['id'])['status'] == ('queued' if owner_status in ('queued', 'running') else 'cancelled')


def test_claim_cancels_terminal_owned_children_without_dispatch(store):
    parent, child = writing(store, 'a'), writing(store, 'a', parent='owner-run')
    with store.connect() as db:
        db.execute('CREATE TABLE writing_runs(id TEXT PRIMARY KEY,job_id TEXT,project_id TEXT)')
        db.execute('INSERT INTO writing_runs VALUES(?,?,?)', ('owner-run', parent['id'], 'a'))
        db.execute("UPDATE jobs SET status='failed' WHERE id=?", (parent['id'],))
    assert store.claim() is None
    assert store.job(child['id'])['status'] == 'cancelled'


def test_cancelled_physical_request_still_suppresses_idle_scheduling_and_claim(store, monkeypatch):
    from datetime import datetime, timezone
    from server import idle_processing

    monkeypatch.setattr(idle_processing, 'stored_preferences', lambda db: {
        'idleProcess': True, 'defaultModelId': 'controlled-model', 'idleStart': '00:00', 'idleEnd': '08:00'})
    monkeypatch.setattr(idle_processing, 'utc_now', lambda: datetime(2026, 10, 25, 17, tzinfo=timezone.utc))
    article = writing(store, 'a')
    processor, started, release = controlled_processor()

    class IdleCounter:
        calls = 0

        def maybe_queue_idle(self):
            self.calls += 1
            return []

    idle = IdleCounter()
    worker = ConcurrentWorker(store, idle, processor=processor)
    try:
        worker.tick()
        wait_for(lambda: article['id'] in started)
        store.cancel(article['id'])
        queued_idle = store.queue('ai_summary', {}, 'idle')
        with store.connect() as db:
            db.execute("UPDATE jobs SET origin='idle' WHERE id=?", (queued_idle['id'],))
        worker.tick()
        assert idle.calls == 0 and queued_idle['id'] not in started
        assert store.job(queued_idle['id'])['status'] == 'queued'
        release[article['id']].set()
        wait_for(lambda: queued_idle['id'] in started, worker.tick)
    finally:
        for gate in list(release.values()):
            gate.set()
        worker.close()


def test_executor_failure_is_contained_and_next_article_is_dispatched(store):
    first, second = writing(store, 'a'), writing(store, 'b')

    def failed(store, job):
        if job['id'] == first['id']:
            raise RuntimeError('private-provider-header-do-not-persist')
        finish(store, job)

    worker = ConcurrentWorker(store, NoIdle(), writing_limit=1, processor=failed)
    try:
        worker.tick()
        wait_for(lambda: store.job(second['id'])['status'] == 'succeeded', worker.tick)
        failed_job = store.job(first['id'])
        assert failed_job['status'] == 'failed'
        assert 'private-provider' not in failed_job['error']
    finally:
        worker.close()


@pytest.mark.parametrize('limit', [0, -1, True, '3'])
def test_invalid_limits_are_rejected(store, limit):
    with pytest.raises(ValueError, match='正整数'):
        store.claim(writing_limit=limit)
    with pytest.raises(ValueError, match='正整数'):
        ConcurrentWorker(store, NoIdle(), writing_limit=limit)
