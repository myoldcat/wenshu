"""Frozen automatic retry budgets with isolated drafts and controlled models."""
import copy
import json

import httpx
import pytest

from server import writing as writing_module
from server.model_gateway import ModelError, ModelService
from server.store import Store, dumps
from server.tests.test_writing_automatic import AutomaticGateway, PROSE
from server.tests.test_writing_integrity import add_source
from server.writing import WritingService, WritingConflict
from server.writing_automatic_retry import retry_call, retry_policy, POLICY


class RetryGateway(AutomaticGateway):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.failures = []
        self.fail_task = 'writing_section'
        self.before_target = None
        self.observe = None

    def complete(self, config, messages):
        packet = json.loads(messages[-1]['content'])
        self.requests.append((copy.deepcopy(config), copy.deepcopy(messages)))
        if self.observe:
            self.observe(packet)
        targeted = packet['task'] == self.fail_task and (self.fail_task != 'writing_section' or packet['section']['title'] == '原始证据')
        if targeted and self.before_target:
            callback, self.before_target = self.before_target, None
            callback(packet)
        if targeted and self.failures:
            failure = self.failures.pop(0)
            if failure == 'extra_json':
                return {'text': '{"paragraphs":[{"text":"正文仍待完整解析。","kind":"supported","evidence":[]}],"issues":[]},"contentStatus":"usable"}', 'usage': {}}
            if failure == 'interrupted':
                return {'text': dumps({'contentStatus': 'interrupted', 'paragraphs': [], 'issues': []}), 'usage': {}}
            if failure == 'paragraph_shape':
                return {'text': dumps({'contentStatus': 'usable', 'paragraphs': {'text': '错误层级不应被当作正文。'}, 'issues': []}), 'usage': {}}
            if failure == 'empty_paragraphs':
                return {'text': dumps({'contentStatus': 'usable', 'paragraphs': [], 'issues': []}), 'usage': {}}
            if failure == 'network':
                raise httpx.RemoteProtocolError('integrity-private-token: peer closed the response')
            if failure == 'auth':
                raise ModelError('模型服务拒绝认证，请检查密钥和访问权限')
            if failure == 'unrelated':
                return {'text': dumps({'contentStatus': 'unrelated', 'paragraphs': [], 'issues': []}), 'usage': {}}
        return super().complete(config, messages)


@pytest.fixture
def retries(tmp_path):
    store, gateway = Store(tmp_path / 'automatic-retries-isolated'), RetryGateway()
    service = WritingService(store, gateway)
    source = add_source(store)
    project = service.create({'title': '原始证据管理', 'mode': 'strict', 'sourceIds': [source],
        'brief': {'topic': '原始证据和固定版本', 'automation': 'automatic', 'targetWords': 10000,
                  'automaticOptions': {'restrictToSelectedSources': True, 'includeImages': False}}})
    return store, gateway, service, project


def execute(env, *, legacy=False):
    store, _, service, project = env
    queued = service.queue_automatic(project['id'], project['version'])
    if legacy:
        with store.connect() as db:
            frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
            frozen.pop('automaticRetryPolicy')
            frozen.pop('automaticRetryOwnerRunId')
            db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    service.process(store.claim())
    return queued, service.get(project['id'])


def requests_for(gateway, task='writing_section'):
    return [(config, messages) for config, messages in gateway.requests if json.loads(messages[-1]['content'])['task'] == task]


@pytest.mark.parametrize('failure,category', [('extra_json', 'format_error'), ('paragraph_shape', 'format_error'),
    ('empty_paragraphs', 'format_error'), ('interrupted', 'interrupted'), ('network', 'network')])
def test_chapter_retries_twice_before_next_chapter_and_records_recovery(retries, failure, category):
    store, gateway, _, _ = retries
    gateway.failures = [failure, failure]
    queued, project = execute(retries)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    calls = requests_for(gateway)
    assert [json.loads(messages[-1]['content'])['section']['title'] for _, messages in calls] == ['原始证据'] * 3 + ['版本保存']
    assert calls[0] == calls[1] == calls[2]
    assert all(section['content'].startswith(PROSE) for section in project['sections'])
    report = project['automatic']['report']
    recovered = next(item for item in report['retries'] if item['stage'] == 'chapters')
    assert recovered['status'] == 'recovered' and recovered['retryCount'] == recovered['maxRetries'] == 2
    assert recovered['category'] == category
    assert not any(item['code'] in ('step_failed', 'chapter_failed', 'chapter_not_adopted') for item in report['issues'])
    with store.connect() as db:
        children = db.execute("SELECT r.*,j.attempts FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.kind='section' ORDER BY r.created_at").fetchall()
        assert len(children) == 2
        frozen = json.loads(children[0]['input_json'])
        assert frozen['automaticRetryPolicy']['version'] == POLICY
        assert frozen['automaticRetryOwnerRunId'] == queued['runId']
        assert children[0]['attempts'] == 1  # request retries do not create jobs/lanes
        values = '\n'.join(str(tuple(row)) for row in db.execute('SELECT * FROM writing_automatic_retries'))
        assert 'integrity-private-token' not in values


@pytest.mark.parametrize('failure', ['extra_json', 'paragraph_shape', 'empty_paragraphs'])
def test_exhausted_chapter_stays_empty_and_final_body_stage_is_partial(retries, failure):
    store, gateway, _, _ = retries
    gateway.failures = [failure] * 3
    queued, project = execute(retries)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert not project['sections'][0]['content'] and project['sections'][1]['content']
    assert len(requests_for(gateway)) == 4
    report = project['automatic']['report']
    assert report['status'] == 'completed_with_issues'
    assert report['summary']['generatedSections'] == 1 and report['summary']['skippedSections'] == 1
    stage = next(item for item in report['stages'] if item['stage'] == 'chapters')
    assert stage['status'] == 'partial' and '1/2' in stage['message']
    retry = next(item for item in report['retries'] if item['stage'] == 'chapters')
    assert retry['status'] == 'exhausted' and retry['reason'] == '返回格式错误' and retry['retryCount'] == 2
    assert not any(item['code'] == 'chapter_not_adopted' for item in report['issues'])


def test_outline_json_failure_uses_same_frozen_request_and_retries(retries):
    store, gateway, _, _ = retries
    gateway.fail_task, gateway.failures = 'writing_outline', ['extra_json', 'extra_json']
    queued, project = execute(retries)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    calls = requests_for(gateway, 'writing_outline')
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    assert all(section['content'] for section in project['sections'])
    assert any(item['stage'] == 'outline' and item['category'] == 'format_error' for item in project['automatic']['report']['retries'])


@pytest.mark.parametrize('failure', ['auth', 'unrelated'])
def test_business_failures_are_not_retried(retries, failure):
    _, gateway, _, _ = retries
    gateway.failures = [failure]
    _, project = execute(retries)
    assert len(requests_for(gateway)) == 2
    assert not project['sections'][0]['content'] and project['sections'][1]['content']
    assert not project['automatic']['report']['retries']


def test_old_parent_without_retry_policy_keeps_one_attempt_and_old_stage_contract(retries):
    _, gateway, _, _ = retries
    gateway.failures = ['extra_json'] * 3
    _, project = execute(retries, legacy=True)
    assert len(requests_for(gateway)) == 2
    assert not project['sections'][0]['content']
    assert 'retries' not in project['automatic']['report']
    assert next(item for item in project['automatic']['report']['stages'] if item['stage'] == 'chapters')['status'] == 'complete'


@pytest.mark.parametrize('failure', ['paragraph_shape', 'empty_paragraphs'])
def test_old_frozen_format_list_does_not_gain_structural_retries(retries, failure):
    store, gateway, service, project = retries
    queued = service.queue_automatic(project['id'], project['version'])
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        frozen['automaticRetryPolicy']['formatErrors'].remove('模型未返回可用正文，请重新生成')
        db.execute('UPDATE writing_runs SET input_json=? WHERE id=?', (dumps(frozen), queued['runId']))
    gateway.failures = [failure] * 3
    service.process(store.claim())
    current = service.get(project['id'])
    assert len(requests_for(gateway)) == 2  # one failed first chapter, one successful next chapter
    assert not current['sections'][0]['content'] and current['sections'][1]['content']
    assert not current['automatic']['report']['retries']
    with store.connect() as db:
        row = db.execute("SELECT * FROM writing_automatic_retries WHERE owner_run_id=? AND logical_key LIKE 'generation:%' AND status='failed'",
                         (queued['runId'],)).fetchone()
        assert row['attempt'] == 1 and row['category'] is None
        parent = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        assert parent['automaticRetryPolicy'] == frozen['automaticRetryPolicy']
        assert parent['automaticRetryOwnerRunId'] == frozen['automaticRetryOwnerRunId']


def test_valid_late_candidate_is_preserved_after_manual_edit_without_retry_or_overwrite(retries):
    _, gateway, service, project = retries

    def edit(packet):
        current = service.get(project['id'])
        service.save_section(project['id'], packet['section']['id'], current['version'], {'content': '作者在模型请求期间实际写入的正文。'})

    gateway.before_target = edit
    _, result = execute(retries)
    assert result['sections'][0]['content'] == '作者在模型请求期间实际写入的正文。'
    child = next(run for run in result['runs'] if run['kind'] == 'section')
    assert child['candidate']['section']['content'].startswith(PROSE) and not child['applied']
    assert child['status'] == 'succeeded' and child['stage'] == 'candidate'
    assert len(requests_for(gateway)) == 1
    assert any(issue['code'] == 'project_changed' for issue in result['automatic']['report']['issues'])


def test_cancellation_during_first_failure_never_sends_second_attempt(retries):
    store, gateway, service, project = retries
    queued = service.queue_automatic(project['id'], project['version'])
    gateway.failures = ['extra_json']
    gateway.before_target = lambda _: store.cancel(queued['job']['id'])
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'cancelled'
    assert len(requests_for(gateway)) == 1
    assert not any(section['content'] for section in service.get(project['id'])['sections'])


def test_third_success_cached_before_apply_survives_worker_recovery_without_fourth_request(retries, monkeypatch):
    store, gateway, service, project = retries
    project = service.outline(project['id'], project['version'], [{'title': '原始证据', 'goal': '保留原始证据', 'targetWords': 100}])
    project = service.confirm(project['id'], project['version'])
    parent = service.queue_automatic(project['id'], project['version'])
    parent_job = store.claim()
    with store.connect() as db:
        run = dict(db.execute('SELECT * FROM writing_runs WHERE id=?', (parent['runId'],)).fetchone())
    frozen, state = json.loads(run['input_json']), json.loads(run['result_json'])['automatic']
    service._automatic_parent_run, service._automatic_parent_job = run['id'], parent_job
    service._automatic_model_config = gateway.resolve(gateway.config['id'], gateway.config['version'])
    gateway.failures = ['extra_json', 'extra_json']
    original = writing_module.retry_call

    class SimulatedCrash(BaseException):
        pass

    def crash_after_cache(*args, **kwargs):
        value = original(*args, **kwargs)
        raise SimulatedCrash()

    monkeypatch.setattr(writing_module, 'retry_call', crash_after_cache)
    with pytest.raises(SimulatedCrash):
        service._automatic_child(run, parent_job, frozen, state, 'section:' + project['sections'][0]['id'],
            lambda: service.queue(project['id'], project['version'], 'section', project['sections'][0]['id']))
    assert len(requests_for(gateway)) == 3
    child_id = state['children']['section:' + project['sections'][0]['id']]['runId']
    with store.connect() as db:
        assert db.execute('SELECT result_json FROM writing_runs WHERE id=?', (child_id,)).fetchone()[0] is None
    monkeypatch.setattr(writing_module, 'retry_call', original)
    restarted_store = Store(store.root)
    restarted_store.recover()
    resumed_job = restarted_store.claim()
    assert resumed_job['id'] == parent_job['id']
    restarted = WritingService(restarted_store, gateway)
    restarted._automatic_parent_run, restarted._automatic_parent_job = run['id'], resumed_job
    restarted._automatic_model_config = gateway.resolve(gateway.config['id'], gateway.config['version'])
    with restarted_store.connect() as db:
        state = json.loads(db.execute('SELECT result_json FROM writing_runs WHERE id=?', (run['id'],)).fetchone()[0])['automatic']
    child, _ = restarted._automatic_child(run, resumed_job, frozen, state, 'section:' + project['sections'][0]['id'],
                                          lambda: pytest.fail('The existing child run must be reused'))
    assert child['id'] == child_id and child['applied']
    assert len(requests_for(gateway)) == 3
    assert restarted.get(project['id'])['sections'][0]['content'].startswith(PROSE)


def test_managed_exhausted_request_cannot_multiply_budget_on_resume(retries):
    store, _, service, project = retries
    queued = service.queue_automatic(project['id'], project['version'])
    store.claim()
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
    calls = []

    def malformed():
        calls.append(1)
        json.loads('{"broken":true},"contentStatus":"usable"}')

    for _ in range(2):
        with pytest.raises(ValueError):
            retry_call(store, frozen, 'durable-failure', malformed)
    assert len(calls) == 3
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM writing_automatic_retries WHERE owner_run_id=?', (queued['runId'],)).fetchone()[0] == 3


def test_retry_cache_cascades_only_with_its_own_parent_run(retries):
    store, _, service, project = retries
    first = service.queue_automatic(project['id'], project['version'])
    store.claim()
    other = service.create({'title': '独立的另一篇文章', 'brief': {'topic': '保留另一篇记录'}})
    second = service.queue_automatic(other['id'], other['version'])
    store.claim()
    for queued in (first, second):
        with store.connect() as db:
            frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (queued['runId'],)).fetchone()[0])
        assert retry_call(store, frozen, 'isolated-cache', lambda: {'result': '保留模型结果'}) == {'result': '保留模型结果'}
    with store.connect() as db:
        db.execute('DELETE FROM writing_runs WHERE id=?', (first['runId'],))
        assert db.execute('SELECT COUNT(*) FROM writing_automatic_retries WHERE owner_run_id=?', (first['runId'],)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM writing_automatic_retries WHERE owner_run_id=?', (second['runId'],)).fetchone()[0] == 1


def test_retry_then_business_rejection_keeps_retry_history_without_third_request(retries):
    _, gateway, _, _ = retries
    gateway.failures = ['network', 'auth']
    _, project = execute(retries)
    assert len(requests_for(gateway)) == 3  # two first-chapter requests, one next chapter
    event = next(item for item in project['automatic']['report']['retries'] if item['stage'] == 'chapters')
    assert event['retryCount'] == 1 and event['status'] == 'exhausted' and event['category'] == 'network'
    assert not project['sections'][0]['content'] and project['sections'][1]['content']


def test_current_retry_is_visible_only_while_the_automatic_request_is_running(retries):
    _, gateway, service, project = retries
    gateway.failures = ['extra_json']
    seen = []

    def observe(packet):
        if packet['task'] == 'writing_section' and packet['section']['title'] == '原始证据':
            seen.append(service.get(project['id'])['automatic'].get('currentRetry'))

    gateway.observe = observe
    _, result = execute(retries)
    assert seen[0] is None
    assert seen[1]['retryCount'] == 1 and seen[1]['status'] == 'retrying'
    assert seen[1]['sectionTitle'] == '原始证据' and seen[1]['category'] == 'format_error'
    assert 'currentRetry' not in result['automatic']


def test_nested_managed_failure_is_not_retried_again_by_an_outer_logical_call(retries):
    store, _, service, project = retries
    parent = service.queue_automatic(project['id'], project['version'])
    store.claim()
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (parent['runId'],)).fetchone()[0])
    inner_calls, outer_calls = [], []

    def malformed():
        inner_calls.append(1)
        json.loads('{"value":true},"contentStatus":"usable"}')

    def outer():
        outer_calls.append(1)
        return retry_call(store, frozen, 'inner-managed', malformed)

    for _ in range(2):
        with pytest.raises(ValueError) as error:
            retry_call(store, frozen, 'outer-managed', outer)
        assert error.value.automaticRetryCount == 2
    assert len(inner_calls) == 3 and len(outer_calls) == 1
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM writing_automatic_retries WHERE logical_key='outer-managed'").fetchone()[0] == 1


@pytest.mark.parametrize('change', ['cancel', 'version'])
def test_stop_or_edit_during_parameter_negotiation_prevents_second_http_request(retries, change):
    store, gateway, service, project = retries
    parent = service.queue_automatic(project['id'], project['version'])
    store.claim()
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (parent['runId'],)).fetchone()[0])
    http_calls = []

    def transport(request):
        http_calls.append(request)
        if change == 'cancel':
            store.cancel(parent['job']['id'])
        else:
            service.patch(project['id'], project['version'], {'title': '作者在参数协商期间改题'})
        return httpx.Response(400, json={'error': {'param': 'max_tokens', 'code': 'unsupported_parameter', 'message': 'Use max_completion_tokens'}})

    models = ModelService(store, transport=httpx.MockTransport(transport))
    config = {**gateway.config, 'baseUrl': 'http://model.test/v1', 'timeoutSeconds': 10}

    def guard():
        if service.get(project['id'])['version'] != project['version']:
            raise WritingConflict('项目版本已变化')

    with pytest.raises(ModelError):
        retry_call(store, frozen, 'parameter-negotiation',
                   lambda: models.complete(config, [{'role': 'user', 'content': '只使用隔离模拟模型。'}]), guard=guard,
                   secret=config['apiKey'])
    assert len(http_calls) == 1
