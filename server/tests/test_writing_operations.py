"""Writing audit logs are transport facts, independent of candidate publication."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.model_gateway import ModelError, ModelService
from server.store import Store
from server.writing import WritingService
from server.tests.test_model_gateway import TestCipher
from server.tests.test_writing_integrity import writing, enqueue
from server.tests.test_writing_document import document, ready, queue_and_claim
from server.tests.test_writing_document_mode_prompts import chapter_instructions
from server.tests.test_writing_automatic import automatic, run_automatic


def logged(environment, run):
    service = environment[2]
    summaries = service.operations.calls(run['runId'])['items']
    return [service.operations.call(item['id'])['call'] for item in summaries]


def test_injected_success_is_durable_and_details_are_lazy(writing):
    store, gateway, service, _, _ = writing
    run, job = enqueue(writing)
    service.process(job)
    summaries = service.operations.calls(run['runId'])
    assert summaries['total'] == 1  # Generation logs one call; citation review is explicit and later.
    summary = summaries['items'][0]
    assert 'request' not in summary and 'response' not in summary
    call = logged(writing, run)[0]
    assert call['transport'] == 'injected'
    assert call['status'] == 'succeeded' and call['validationStatus'] == 'valid'
    assert call['request']['messages'] == gateway.calls[0][1]
    assert json.loads(call['response']['raw'])['text'] == call['response']['text']
    assert call['usage']['total_tokens'] == 23
    assert call['finishedAt'] and call['durationMs'] >= 0
    assert gateway.config['apiKey'] not in json.dumps(call)
    assert WritingService(store, gateway).operations.call(call['id']) == {'call': call}


def test_validation_failure_keeps_returned_content(writing):
    store, gateway, service, _, _ = writing
    gateway.transform = lambda result, request: {'paragraphs': []}
    run, job = enqueue(writing)
    service.process(job)
    call = logged(writing, run)[0]
    assert store.job(job['id'])['status'] == 'failed'
    assert call['status'] == 'succeeded' and call['validationStatus'] == 'invalid'
    assert json.loads(call['response']['text']) == {'paragraphs': []}
    assert call['error']


def test_failed_and_cancelled_calls_remain_visible(writing):
    store, gateway, service, _, _ = writing
    gateway.fail = True
    run, job = enqueue(writing)
    service.process(job)
    call = logged(writing, run)[0]
    assert call['status'] == 'failed' and call['response']['raw'] is None
    assert gateway.config['apiKey'] not in json.dumps(call)
    store.retry(job['id'])
    retry = store.claim()
    gateway.fail = False
    gateway.callback = lambda: store.cancel(job['id'])
    service.process(retry)
    calls = logged(writing, run)
    assert len(calls) == 2 and {item['attempt'] for item in calls} == {1, 2}
    assert calls[-1]['status'] == 'succeeded'
    assert calls[-1]['validationStatus'] == 'discarded'
    assert calls[-1]['response']['text']
    assert store.job(job['id'])['status'] == 'cancelled'


def native(tmp_path, provider):
    store = Store(tmp_path / 'native-writing-operations')
    models = ModelService(store, cipher=TestCipher(), transport=httpx.MockTransport(provider))
    model = models.save_config({'name': '日志模型', 'model': 'audit-model', 'baseUrl': 'http://model.test/v1', 'apiKey': 'never-log-credential'})
    service = WritingService(store, models)
    project = service.create({'title': '独立写作记录', 'mode': 'extended', 'modelId': model['id']})
    queued = service.queue(project['id'], project['version'], 'outline')
    return store, models, service, queued, store.claim()


def valid_reply():
    content = {'sections': [{'title': '观点', 'goal': '分析主题', 'mode': 'extended', 'targetWords': 600}], 'issues': []}
    return {'choices': [{'message': {'content': json.dumps(content, ensure_ascii=False)}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 34}}


def test_http_negotiation_is_two_actual_calls_and_observer_does_not_leak(tmp_path):
    requests = []
    state = {}

    def provider(request):
        with state['store'].connect() as db:
            assert db.execute("SELECT count(*) FROM writing_model_interactions WHERE status='in_progress'").fetchone()[0] == 1
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(400, json={'error': {'param': 'max_tokens', 'code': 'unsupported_parameter', 'message': 'use max_completion_tokens never-log-credential'}})
        return httpx.Response(200, json=valid_reply())

    store, models, service, queued, job = native(tmp_path, provider)
    state['store'] = store
    service.process(job)
    calls = [service.operations.call(item['id'])['call'] for item in service.operations.calls(queued['runId'])['items']]
    assert len(calls) == 2
    assert [item['httpAttempt'] for item in calls] == [1, 2]
    assert calls[0]['status'] == 'negotiated'
    assert calls[0]['validationStatus'] == 'unavailable'
    assert calls[1]['validationStatus'] == 'valid'
    assert 'max_tokens' in calls[0]['request'] and 'max_completion_tokens' in calls[1]['request']
    assert calls[1]['response']['json'] == valid_reply()
    assert 'never-log-credential' not in json.dumps(calls)
    models.transport = httpx.MockTransport(lambda _: httpx.Response(200, json=valid_reply()))
    models.complete(models.resolve(calls[1]['model']['id']), [{'role': 'user', 'content': '普通模型调用'}])
    assert service.operations.calls(queued['runId'])['total'] == 2


@pytest.mark.parametrize('failure', ['http', 'network', 'protocol', 'length', 'credential'])
def test_http_failures_keep_sanitized_wire_reply(tmp_path, failure):
    def provider(request):
        if failure == 'network':
            raise httpx.ConnectError('never-log-credential')
        if failure == 'http':
            return httpx.Response(401, json={'error': 'never-log-credential'})
        if failure == 'protocol':
            return httpx.Response(200, content=b'not-json')
        body = valid_reply()
        if failure == 'length':
            body['choices'][0]['finish_reason'] = 'length'
        else:
            body['choices'][0]['message']['content'] = 'never-log-credential'
        return httpx.Response(200, json=body)

    store, _, service, queued, job = native(tmp_path, provider)
    service.process(job)
    call = service.operations.call(service.operations.calls(queued['runId'])['items'][0]['id'])['call']
    assert store.job(job['id'])['status'] == 'failed'
    assert call['status'] == 'failed' and call['finishedAt']
    assert 'never-log-credential' not in json.dumps(call)
    assert (call['response']['raw'] is None) == (failure == 'network')


def test_read_only_api_pagination_legacy_and_boundaries(writing):
    store, gateway, service, _, project = writing
    run, job = enqueue(writing)
    service.process(job)
    app = create_app(store.root, model_service=gateway)
    client = TestClient(app)
    base = '/api/operations/writing'
    projects = client.get(base + '/projects?limit=1').json()
    assert projects['total'] == 1 and projects['items'][0]['callCount'] == 1
    runs = client.get(base + f'/projects/{project["id"]}/runs').json()
    assert runs['items'][0]['logCoverage'] == 'recorded'
    assert client.get(base + f'/runs/{run["runId"]}/calls?offset=1').json()['items'] == []
    assert client.get(base + '/projects?limit=101').status_code == 422
    assert client.get(base + '/calls/missing').status_code == 404
    assert client.post(base + '/projects', headers={'X-Wenshu-Client': 'local'}).status_code == 405
    with store.connect() as db:
        db.execute('DELETE FROM writing_model_interactions')
    history = client.get(base + f'/runs/{run["runId"]}').json()
    assert history['run']['logCoverage'] == 'legacy_unavailable'
    assert history['history']['rawResponseAvailable'] is False
    assert history['history']['requests'][0]['messages'] == gateway.calls[0][1]
    assert history['history']['candidate']


@pytest.mark.parametrize('wire_encoding', ['utf-8', 'utf-16'])
def test_arbitrary_json_unicode_escaping_cannot_reveal_credentials(tmp_path, wire_encoding):
    escaped = ''.join('\\u%04x' % ord(char) for char in 'never-log-credential')
    store, _, service, queued, job = native(tmp_path, lambda _: httpx.Response(401, content=('{"error":"' + escaped + '"}').encode(wire_encoding)))
    service.process(job)
    call = service.operations.call(service.operations.calls(queued['runId'])['items'][0]['id'])['call']
    assert 'never-log-credential' not in json.dumps(call)
    assert call['response']['json'] == {'error': '[REDACTED]'}
    with store.connect() as db:
        raw = db.execute('SELECT response_raw FROM writing_model_interactions').fetchone()[0]
    assert json.loads(raw) == {'error': '[REDACTED]'}


def test_operations_api_rejects_public_host_even_when_main_app_allows_it(writing, monkeypatch):
    store, gateway, _, _, _ = writing
    monkeypatch.setenv('WENSHU_ALLOWED_HOSTS', 'public.example.test')
    app = create_app(store.root, model_service=gateway)
    client = TestClient(app, base_url='http://public.example.test')
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/operations/writing/projects').status_code == 403
    assert TestClient(app).get('/api/operations/writing/projects').status_code == 200


def test_interrupted_attempt_is_derived_without_inventing_finish_time(writing):
    store, gateway, service, _, _ = writing
    run, job = enqueue(writing)
    with store.connect() as db:
        row = dict(db.execute('SELECT * FROM writing_runs WHERE id=?', (run['runId'],)).fetchone())
    config = gateway.resolve()
    messages = json.loads(row['input_json'])['messages']
    with service.operations.capture(row, job, job['attempts'] + 1, config, messages) as capture:
        call_id = capture.start({'model': config['model'], 'messages': messages}, config, 1)
    store.recover()
    call = service.operations.call(call_id)['call']
    assert call['status'] == 'interrupted' and call['finishedAt'] is None
    assert call['validationStatus'] == 'unavailable'
    with store.connect() as db:
        assert db.execute('SELECT status FROM writing_model_interactions WHERE id=?', (call_id,)).fetchone()[0] == 'in_progress'


def test_document_call_records_exact_combined_request_and_section_ids(document):
    store, gateway, service, _ = document
    project = ready(document, words=(200, 200), modes=('strict', 'extended'))
    queued, job = queue_and_claim(document, project)
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    calls = service.operations.calls(queued['runId'])['items']
    details = [service.operations.call(item['id'])['call'] for item in calls]
    generations = [item for item in details
                   if json.loads(item['messages'][1]['content'])['task'] == 'writing_document']
    assert len(details) == len(generations) == 1
    assert not gateway.matching_calls  # Body generation never starts semantic review.
    extended = service.get(project['id'])['sections'][1]
    assert extended['content'] == gateway.paragraphs({'mode': 'extended'})[0]['text']
    assert '【模型补充·待核实】' not in extended['content']
    assert extended['extendedContentPolicy'] == 'retain-as-prose-v2'
    assert extended['evidence'] == []
    detail = generations[0]
    assert detail['kind'] == 'document' and detail['validationStatus'] == 'valid'
    assert set(detail['sectionIds']) == {section['id'] for section in project['sections']}
    packet = json.loads(detail['messages'][1]['content'])
    assert packet == gateway.calls[0]['payload']
    assert [section['documentPromptId'] for section in packet['sections']] == [
        'writing_document_strict', 'writing_document_extended']
    with store.connect() as db:
        frozen_sections = {row['section_id']: json.loads(row['input_json']) for row in db.execute(
            'SELECT section_id,input_json FROM writing_document_steps WHERE run_id=?', (queued['runId'],))}
    for section in packet['sections']:
        frozen_section = frozen_sections[section['id']]
        assert section['documentPromptVersion'] == frozen_section['documentPrompt']['version'] == 1
        document_instructions = chapter_instructions(packet, section, 'document')
        writing_instructions = chapter_instructions(packet, section, 'section')
        assert document_instructions and document_instructions == frozen_section['documentPrompt']['content']
        assert writing_instructions and writing_instructions == frozen_section['sectionInstructions']
        for key in ('documentInstructionRef', 'writingInstructionRef'):
            if section.get(key):
                assert packet['instructionCatalog'][section[key]]['version'] == 1
    assert gateway.config['apiKey'] not in json.dumps(detail)
    assert json.loads(detail['response']['raw'])['text'] == detail['response']['text']

    # The later explicit citation check owns its own run and transport records.
    # Neither the original request nor its raw response is relabeled as a review.
    generated = service.get(project['id'])
    citation_run = service.queue_citation_match(project['id'], generated['version'])
    citation_job = store.claim()
    assert citation_job['id'] == citation_run['job']['id']
    assert citation_run['runId'] != queued['runId']
    service.process(citation_job)
    assert store.job(citation_job['id'])['status'] == 'succeeded'
    matching = [service.operations.call(item['id'])['call']
                for item in service.operations.calls(citation_run['runId'])['items']]
    assert len(matching) == len(gateway.matching_calls) == 2
    assert {item['runId'] for item in matching} == {citation_run['runId']}
    assert all(item['kind'] == 'citation_match' and item['validationStatus'] == 'valid'
               for item in matching)
    strict_call = next(item for item in matching
                       if json.loads(item['messages'][1]['content'])['sectionId']
                       == project['sections'][0]['id'])
    matching_packet = json.loads(strict_call['messages'][1]['content'])
    assert matching_packet['mode'] == 'strict'
    assert matching_packet == gateway.matching_calls[0]['payload']
    assert all(json.loads(item['response']['raw'])['text'] == item['response']['text']
               for item in matching)
    assert gateway.config['apiKey'] not in json.dumps(matching)
    assert service.operations.calls(queued['runId'])['total'] == 1
    assert service.operations.call(detail['id'])['call'] == detail


def test_material_recommendation_is_logged_before_project_and_stays_associated(writing):
    store, gateway, service, source, _ = writing
    gateway.complete = lambda config, messages: {'text': json.dumps({'queries': ['知识治理', '原始证据']}), 'usage': {'total_tokens': 12}}
    app = create_app(store.root, model_service=gateway)
    app.state.retrieval.save_settings({'rerankEnabled': False})
    client = TestClient(app)
    result = client.post('/api/retrieval/recommendations', headers={'X-Wenshu-Client': 'local'}, json={'topic': '知识治理', 'sourceIds': [source]}).json()
    assert result['queryOrigin'] == 'model'
    preparations = service.operations.runs('preparations')
    assert preparations['total'] == 1
    material_run = preparations['items'][0]
    assert material_run['id'] == 'materials:' + result['id']
    assert material_run['jobId'] is None and material_run['kind'] == 'materials'
    detail = service.operations.call(service.operations.calls(material_run['id'])['items'][0]['id'])['call']
    assert detail['validationStatus'] == 'valid' and detail['response']['text']
    assert service.operations.projects()['items'][0]['id'] == 'preparations'
    project = service.create({'title': '确认推荐后保存', 'mode': 'strict', 'modelId': gateway.config['id'], 'sourceIds': [source], 'materialSelection': {'recommendationId': result['id'], 'passages': []}})
    assert service.operations.runs(project['id'])['items'][0]['id'] == material_run['id']
    assert service.operations.runs('preparations')['total'] == 0
    project = service.patch(project['id'], project['version'], {'materialSelection': {'recommendationId': None, 'passages': []}})
    assert service.operations.runs(project['id'])['items'][0]['id'] == material_run['id']
    assert service.operations.run(material_run['id'])['run']['projectId'] == project['id']


def test_failed_material_query_keeps_raw_reply_and_local_fallback(writing):
    store, gateway, service, _, _ = writing
    gateway.complete = lambda config, messages: {'text': '{"queries":[]}', 'usage': {}}
    app = create_app(store.root, model_service=gateway)
    app.state.retrieval.save_settings({'rerankEnabled': False})
    client = TestClient(app)
    response = client.post('/api/retrieval/recommendations', headers={'X-Wenshu-Client': 'local'}, json={'topic': '知识治理'})
    assert response.status_code == 200 and response.json()['queryOrigin'] == 'local'
    material = service.operations.runs('preparations')['items'][0]
    call = service.operations.call(service.operations.calls(material['id'])['items'][0]['id'])['call']
    assert call['status'] == 'succeeded' and call['validationStatus'] == 'invalid'
    assert call['response']['text'] == '{"queries":[]}'


@pytest.mark.parametrize('failure,generated,skipped,available', [
    ('unsupported_body', 2, 0, True), ('fail_chapter', 1, 1, True), ('materials_missing', 2, 0, True)])
def test_automatic_execution_and_manuscript_outcome_are_reported_separately(automatic, failure, generated, skipped, available):
    _, gateway, service, project = automatic
    if failure == 'materials_missing':
        project = service.create({'title': '缺少原文', 'mode': 'strict', 'modelId': gateway.config['id'],
                                  'brief': {'automation': 'automatic'}})
        automatic = (*automatic[:3], project)
    else:
        setattr(gateway, failure, '原始证据' if failure == 'fail_chapter' else True)
    queued, current = run_automatic(automatic)
    expected = {'status': 'completed_with_issues', 'report': {
        'outputAvailable': available,
        'summary': {'generatedSections': generated, 'preservedSections': 0, 'skippedSections': skipped}}}
    item = next(run for run in service.operations.runs(project['id'])['items'] if run['id'] == queued['runId'])
    assert item['status'] == 'succeeded'  # Execution finished; body is a separate outcome.
    assert item['automatic'] == expected
    assert service.operations.run(queued['runId'])['run']['automatic'] == expected
    assert 'issues' not in item['automatic']['report']
    assert 'stages' not in item['automatic']['report']
    assert current['automatic']['report']['issues']  # Full report is retained elsewhere.
    if failure == 'materials_missing':
        assert current['mode'] == 'extended' and current['sourceIds'] == []
        assert len(current['sections']) == 2 and all(section['content'] for section in current['sections'])
        assert any(issue['code'] == 'materials_missing' for issue in current['automatic']['report']['issues'])
    if failure == 'unsupported_body':
        assert all(section['citationBindingState'] == 'needs_review' for section in current['sections'])
        assert any(issue['code'] == 'citation_support' for issue in current['automatic']['report']['issues'])
