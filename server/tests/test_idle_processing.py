"""Idle work uses durable settings, local-time windows and cooperative yielding.

All provider responses are deterministic fakes. No real library or model is used.
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from server import idle_processing
from server.enrichment import EnrichmentService
from server.model_gateway import ModelError, ModelService
from server.store import Store
from server.tests.test_ai_api import Cipher, LOCAL, bench, config
from server.tests.test_enrichment import Gateway, publish
from server.worker import run_one


def publish_old_document(store, models, *args, **kwargs):
    """Seed history without new-import work, then restore idle eligibility."""
    auto_translate = models.get_preferences()['autoTranslate']
    models.save_preferences({'autoTranslate': False})
    try:
        source = publish(store, *args, **kwargs)
    finally:
        models.save_preferences({'autoTranslate': auto_translate})
    revision = store.detail(source)['revisionId']
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE kind='ai_schedule' AND revision_id=?",
                              (revision,)).fetchone()
    return source


@pytest.fixture
def idle_environment(tmp_path, monkeypatch):
    clock = {'now': datetime(2026, 10, 21, 17, tzinfo=timezone.utc)}
    monkeypatch.setattr(idle_processing, 'utc_now', lambda: clock['now'])
    store = Store(tmp_path / 'idle')
    models = ModelService(store, cipher=Cipher())
    model = models.save_config({'name': '闲时测试模型', 'baseUrl': 'http://model.test/v1',
                                'model': 'fake-model', 'contextChars': 24000, 'maxOutputTokens': 4096})
    models.save_preferences({'defaultModelId': model['id'], 'autoProcess': False,
                             'autoTranslate': True, 'idleProcess': True})
    gateway = Gateway()
    monkeypatch.setattr(models, 'complete', gateway.complete)
    service = EnrichmentService(store, models)
    source = publish_old_document(store, models)
    return store, models, gateway, service, source, clock


@pytest.mark.parametrize('instant,expected', [
    ('2026-10-21T15:59:59+00:00', False),
    ('2026-10-21T16:00:00+00:00', True),
    ('2026-10-21T23:59:59+00:00', True),
    ('2026-10-22T00:00:00+00:00', False),
    ('2026-10-22T07:30:00+08:00', True),
])
def test_default_window_uses_beijing_time_with_exclusive_end(instant, expected):
    assert idle_processing.in_idle_window({}, datetime.fromisoformat(instant)) is expected


@pytest.mark.parametrize('hour,expected', [(21, False), (22, True), (23, True), (0, True), (1, True), (2, False)])
def test_configured_window_can_cross_midnight(hour, expected):
    moment = datetime(2026, 10, 22, hour, tzinfo=timezone(timedelta(hours=8)))
    assert idle_processing.in_idle_window({'idleStart': '22:00', 'idleEnd': '02:00'}, moment) is expected


@pytest.mark.parametrize('change', [
    {'idleStart': '24:00'}, {'idleEnd': '08:60'}, {'idleStart': '0:00'},
    {'idleEnd': None}, {'idleProcess': 'true'}, {'idleStart': '08:00', 'idleEnd': '08:00'},
])
def test_preferences_reject_invalid_idle_values_atomically(idle_environment, change):
    _, models, _, _, _, _ = idle_environment
    previous = models.get_preferences()
    with pytest.raises(ValueError):
        models.save_preferences(change)
    assert models.get_preferences() == previous


def test_idle_preferences_default_off_and_survive_restart(tmp_path):
    store = Store(tmp_path / 'settings')
    models = ModelService(store, cipher=Cipher())
    defaults = models.get_preferences()
    assert {name: defaults[name] for name in ('idleProcess', 'idleStart', 'idleEnd')} == {
        'idleProcess': False, 'idleStart': '00:00', 'idleEnd': '08:00'}
    with pytest.raises(ModelError):
        models.save_preferences({'idleProcess': True})
    model = models.save_config({'name': '测试', 'baseUrl': 'http://model.test/v1', 'model': 'fake'})
    models.save_preferences({'defaultModelId': model['id'], 'idleProcess': True,
                             'idleStart': '23:30', 'idleEnd': '06:15'})
    reopened = ModelService(Store(store.root), cipher=Cipher())
    assert reopened.get_preferences() == models.get_preferences()
    assert reopened.get_preferences()['autoProcess'] is False


def test_idle_schedules_only_one_missing_capability_at_a_time(idle_environment):
    store, _, gateway, service, source, _ = idle_environment
    scheduled = []
    for _ in range(3):
        jobs = service.maybe_queue_idle()
        assert len(jobs) == 1
        queued = jobs[0]
        assert queued['origin'] == 'idle' and queued['source_id'] == source
        assert service.maybe_queue_idle() == []
        claimed = store.claim()
        assert claimed['id'] == queued['id']
        service.process(claimed)
        assert store.job(queued['id'])['status'] == 'succeeded'
        scheduled.append(queued['kind'])
    assert scheduled == ['ai_translation', 'ai_summary', 'ai_classification']
    assert service.maybe_queue_idle() == []
    assert len(gateway.calls) == 3


@pytest.mark.parametrize('status', ['succeeded', 'failed', 'cancelled'])
def test_idle_never_repeats_existing_manual_runs_after_model_change(idle_environment, status):
    store, models, gateway, service, source, _ = idle_environment
    jobs = service.queue(source)
    assert all(job['origin'] == 'foreground' for job in jobs)
    for job in jobs:
        if status == 'cancelled':
            store.cancel(job['id'])
        else:
            claimed = store.claim()
            if status == 'failed':
                gateway.fail_at = len(gateway.calls) + 1
            service.process(claimed)
        assert store.job(job['id'])['status'] == status
    calls = len(gateway.calls)
    models.save_config({'model': 'new-version'}, models.get_preferences()['defaultModelId'])
    assert service.maybe_queue_idle() == []
    assert len(gateway.calls) == calls


def test_credential_failure_is_durable_and_does_not_repeat_forever(idle_environment, monkeypatch):
    store, models, gateway, service, _, _ = idle_environment

    def unavailable_credentials(*args, **kwargs):
        raise ModelError('safe credential failure')

    monkeypatch.setattr(models, 'resolve', unavailable_credentials)
    failed_kinds = []
    for _ in range(3):
        queued = service.maybe_queue_idle()
        assert len(queued) == 1
        job = queued[0]
        assert job['kind'] not in failed_kinds
        service.process(store.claim())
        assert store.job(job['id'])['status'] == 'failed'
        assert 'safe credential failure' in store.job(job['id'])['error']
        failed_kinds.append(job['kind'])
    assert service.maybe_queue_idle() == []
    assert gateway.calls == []


def test_idle_considers_only_current_revision(idle_environment):
    store, models, _, service, source, _ = idle_environment
    for job in service.queue(source):
        store.cancel(job['id'])
    previous_revision = store.detail(source)['revisionId']
    assert service.maybe_queue_idle() == []
    assert publish_old_document(store, models, 'A revised document has new evidence about source preservation.') == source
    current_revision = store.detail(source)['revisionId']
    assert current_revision != previous_revision
    queued = service.maybe_queue_idle()
    assert len(queued) == 1 and queued[0]['revision_id'] == current_revision
    assert queued[0]['kind'] == 'ai_translation'


def test_idle_preserves_manual_classification(idle_environment):
    store, _, gateway, service, source, _ = idle_environment
    store.patch(source, {'tags': ['人工保留的标签']})
    for job in service.queue(source, ['summary', 'translation']):
        store.cancel(job['id'])
    assert service.maybe_queue_idle() == []
    assert store.detail(source)['tags'] == ['人工保留的标签']
    assert gateway.calls == []


@pytest.mark.parametrize('blocker', ['disabled', 'outside_window', 'writing', 'new_file'])
def test_no_idle_queue_or_model_call_when_ineligible(idle_environment, blocker):
    store, models, gateway, service, _, clock = idle_environment
    if blocker == 'disabled':
        models.save_preferences({'idleProcess': False})
    elif blocker == 'outside_window':
        clock['now'] = datetime(2026, 10, 22, 1, tzinfo=timezone.utc)
    elif blocker == 'writing':
        store.writing_activity('writing_tab', True)
    else:
        store.queue('file', {}, '新文件优先')
    assert service.maybe_queue_idle() == []
    assert gateway.calls == []


def test_claim_prioritizes_new_work_and_blocks_idle_while_foreground_runs(idle_environment):
    store, _, _, service, source, _ = idle_environment
    idle_job = service.queue(source, ['summary'], origin='idle')[0]
    foreground = store.queue('file', {}, '刚加入的新文件')
    assert store.claim()['id'] == foreground['id']
    assert store.claim() is None
    assert store.job(idle_job['id'])['status'] == 'queued'
    store.cancel(foreground['id'])
    assert store.claim()['id'] == idle_job['id']


def test_writing_activity_is_per_session_and_expires_after_sixty_seconds(idle_environment):
    store, _, _, service, source, clock = idle_environment
    queued = service.queue(source, ['summary'], origin='idle')[0]
    assert store.writing_activity('first_tab', True)['ttlSeconds'] == 60
    store.writing_activity('second_tab', True)
    store.writing_activity('first_tab', False)
    assert store.claim() is None
    clock['now'] += timedelta(seconds=59)
    assert store.claim() is None
    clock['now'] += timedelta(seconds=1)
    assert store.claim()['id'] == queued['id']


def test_writing_heartbeat_renews_lease(idle_environment):
    store, _, _, service, source, clock = idle_environment
    queued = service.queue(source, ['summary'], origin='idle')[0]
    store.writing_activity('tab', True)
    clock['now'] += timedelta(seconds=50)
    store.writing_activity('tab', True)
    clock['now'] += timedelta(seconds=20)
    assert store.claim() is None
    store.writing_activity('tab', False)
    assert store.claim()['id'] == queued['id']


@pytest.mark.parametrize('blocker', ['disabled', 'outside_window', 'writing', 'new_file'])
def test_idle_yields_before_first_model_call_when_state_changes_after_claim(idle_environment, blocker):
    store, models, gateway, service, source, clock = idle_environment
    queued = service.queue(source, ['summary'], origin='idle')[0]
    claimed = store.claim()
    assert claimed['id'] == queued['id']
    if blocker == 'disabled':
        models.save_preferences({'idleProcess': False})
    elif blocker == 'outside_window':
        clock['now'] = datetime(2026, 10, 22, 0, tzinfo=timezone.utc)
    elif blocker == 'writing':
        store.writing_activity('tab', True)
    else:
        store.queue('file', {}, '新文件到达')
    service.process(claimed)
    paused = store.job(queued['id'])
    assert paused['status'] == 'queued' and paused['stage'] == 'idle_paused'
    assert gateway.calls == []
    assert service.detail(source)['summary']['result'] is None


@pytest.mark.parametrize('blocker', ['disabled', 'outside_window', 'writing', 'new_file'])
def test_long_idle_job_preserves_checkpoints_and_resumes_without_duplicate_calls(idle_environment, blocker):
    store, models, gateway, service, _, clock = idle_environment
    source = publish_old_document(store, models, 'Evidence and governance. ' * 500,
                                  url='https://example.org/long-idle')
    queued = service.queue(source, ['translation'], origin='idle')[0]
    blocking_jobs = []

    def interrupt():
        if blocker == 'disabled':
            models.save_preferences({'idleProcess': False})
        elif blocker == 'outside_window':
            clock['now'] = datetime(2026, 10, 22, 0, tzinfo=timezone.utc)
        elif blocker == 'writing':
            store.writing_activity('tab', True)
        else:
            blocking_jobs.append(store.queue('file', {}, '新文件优先'))

    gateway.callback = interrupt
    service.process(store.claim())
    paused = store.job(queued['id'])
    assert paused['status'] == 'queued' and paused['stage'] == 'idle_paused'
    assert len(gateway.calls) == 1
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 1
    assert service.detail(source)['translation']['result'] is None
    first_input = gateway.calls[0][1]
    models.save_preferences({'idleProcess': True})
    clock['now'] = datetime(2026, 10, 21, 17, tzinfo=timezone.utc)
    store.writing_activity('tab', False)
    for job in blocking_jobs:
        store.cancel(job['id'])
    reopened = EnrichmentService(Store(store.root), models)
    reopened.process(store.claim())
    assert store.job(queued['id'])['status'] == 'succeeded'
    assert len(gateway.calls) > 1
    assert sum(messages == first_input for _, messages in gateway.calls) == 1
    assert reopened.detail(source)['translation']['result']['segments']


def test_manual_retry_of_idle_failure_becomes_foreground(idle_environment):
    store, models, gateway, service, source, _ = idle_environment
    queued = service.queue(source, ['summary'], origin='idle')[0]
    gateway.fail_at = 1
    service.process(store.claim())
    assert store.job(queued['id'])['status'] == 'failed'
    models.save_preferences({'idleProcess': False})
    retried = store.retry(queued['id'])
    assert retried['origin'] == 'foreground'
    gateway.fail_at = None
    service.process(store.claim())
    assert store.job(queued['id'])['status'] == 'succeeded'


def test_explicit_processing_promotes_paused_idle_intent(idle_environment):
    store, models, gateway, service, source, _ = idle_environment
    idle_job = service.queue(source, ['summary'], origin='idle')[0]
    models.save_preferences({'idleProcess': False})
    assert store.claim() is None
    explicit = service.queue(source, ['summary'])[0]
    assert explicit['id'] == idle_job['id'] and explicit['origin'] == 'foreground'
    service.process(store.claim())
    assert store.job(idle_job['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 1


def test_idle_repair_call_also_yields_to_writing(idle_environment):
    store, _, gateway, service, source, _ = idle_environment
    queued = service.queue(source, ['summary'], origin='idle')[0]
    gateway.rewrite = lambda _: {'summary': '缺少必填证据字段'}
    gateway.callback = lambda: store.writing_activity('tab', True)
    service.process(store.claim())
    paused = store.job(queued['id'])
    assert paused['status'] == 'queued' and paused['stage'] == 'idle_paused'
    assert len(gateway.calls) == 1
    assert service.detail(source)['summary']['result'] is None
    gateway.rewrite = None
    store.writing_activity('tab', False)
    service.process(store.claim())
    assert store.job(queued['id'])['status'] == 'succeeded'
    assert len(gateway.calls) == 2


def test_protocol_negotiation_yields_before_a_second_http_request(idle_environment, monkeypatch):
    store, models, _, service, source, _ = idle_environment
    requests = []

    def transport(request):
        requests.append(request)
        store.writing_activity('tab', True, sequence=1)
        return httpx.Response(400, json={'error': {'param': 'max_tokens',
            'code': 'unsupported_parameter', 'message': 'Use max_completion_tokens'}})

    models.transport = httpx.MockTransport(transport)
    monkeypatch.setattr(models, 'complete', ModelService.complete.__get__(models))
    queued = service.queue(source, ['summary'], origin='idle')[0]
    service.process(store.claim())
    paused = store.job(queued['id'])
    assert paused['status'] == 'queued' and paused['stage'] == 'idle_paused'
    assert len(requests) == 1
    assert service.detail(source)['summary']['result'] is None


def test_worker_discovers_old_documents_when_foreground_queue_is_empty(idle_environment):
    store, _, gateway, service, source, _ = idle_environment
    assert run_one(store, service) is True
    assert len(gateway.calls) == 1
    with store.connect() as db:
        jobs = db.execute("SELECT kind,status,origin FROM jobs WHERE kind LIKE 'ai_%'").fetchall()
    assert len(jobs) == 1 and dict(jobs[0]) == {
        'kind': 'ai_translation', 'status': 'succeeded', 'origin': 'idle'}
    detail = service.detail(source)
    assert detail['translation']['result']
    assert detail['summary']['result'] is None


def test_idle_preferences_api_and_writing_activity_respect_local_boundary(bench):
    client, store, _, _, _ = bench
    model = config(client)
    response = client.put('/api/ai/preferences', headers=LOCAL, json={
        'defaultModelId': model['id'], 'idleProcess': True, 'idleStart': '23:00', 'idleEnd': '07:30'})
    assert response.status_code == 200
    assert response.json()['preferences']['idleStart'] == '23:00'
    assert client.post('/api/ai/writing-activity', json={'sessionId': 'tab', 'active': True}).status_code == 403
    active = client.post('/api/ai/writing-activity', headers=LOCAL, json={'sessionId': 'tab', 'active': True})
    assert active.status_code == 200
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM writing_activity').fetchone()[0] == 1
    released = client.post('/api/ai/writing-activity', headers=LOCAL, json={'sessionId': 'tab', 'active': False})
    assert released.status_code == 200
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM writing_activity WHERE active=1').fetchone()[0] == 0


def test_late_heartbeat_cannot_revive_released_writing_activity(idle_environment):
    store, _, _, service, source, _ = idle_environment
    queued = service.queue(source, ['summary'], origin='idle')[0]
    store.writing_activity('tab', True, sequence=1)
    assert store.claim() is None
    store.writing_activity('tab', False, sequence=3)
    store.writing_activity('tab', True, sequence=2)
    assert store.claim()['id'] == queued['id']
    with store.connect() as db:
        row = db.execute('SELECT active,sequence FROM writing_activity WHERE session_id=?', ('tab',)).fetchone()
    assert dict(row) == {'active': 0, 'sequence': 3}


@pytest.mark.parametrize('payload', [
    {'sessionId': '', 'active': True}, {'sessionId': 'bad/session', 'active': True},
    {'sessionId': 'tab', 'active': 'true'}, {'sessionId': 'tab', 'active': True, 'unknown': 1},
    {'sessionId': 'tab', 'active': True, 'sequence': -1},
    {'sessionId': 'tab', 'active': True, 'sequence': 1.5},
    {'sessionId': 'tab', 'active': True, 'sequence': True},
])
def test_writing_activity_api_rejects_invalid_payloads(bench, payload):
    client, store, _, _, _ = bench
    assert client.post('/api/ai/writing-activity', headers=LOCAL, json=payload).status_code == 422
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM writing_activity').fetchone()[0] == 0
