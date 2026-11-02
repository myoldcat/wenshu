"""Prompt editing through HTTP, persistence and real mocked job execution.

All stores are isolated; provider calls are deterministic mocks. These checks
prove integration and publication gates, not the semantic quality of a model.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server.classification import prompt_contract
from server.enrichment import EnrichmentService
from server.prompts import PromptConflict, PromptLibrary, REGISTRY
from server import prompt_defaults
from server.store import Store
from server.wiki import WikiService
from server.tests.test_enrichment import Gateway, publish, execute
from server.tests.test_wiki import Gateway as WikiGateway, publish as wiki_publish
from server.tests.test_card_cover_ai import bench as cover_bench, execute as cover_execute
from server.tests.test_writing_document import document, ready, queue_and_claim
from server.tests.test_writing_api import bench as writing_bench, create, ready as writing_ready
from server.tests.test_writing_edit_integrity import edit_environment, queued_edit
from server.worker import run_one

LOCAL = {'X-Wenshu-Client': 'local'}


def edit(store, id, marker):
    library = PromptLibrary(store)
    item = next(item for item in library.list()['items'] if item['id'] == id)
    return library.update(id, item['content'] + '\n' + marker, item['version'])


def test_fixed_catalogue_http_persistence_conflicts_and_no_side_effects(tmp_path):
    path = tmp_path / 'catalogue'
    app = create_app(data_dir=path)
    with TestClient(app) as client:
        catalog = client.get('/api/prompts').json()
        assert len(catalog['items']) == 50
        assert Counter(item['category'] for item in catalog['items']) == dict(knowledge=7, wiki=4, writing=7,
            assistance=5, structures=10, composition=6, styles=4, editing=4, images=2, presentation=1)
        assert {item['id'] for item in catalog['categories']} == {item['category'] for item in catalog['items']}
        item = next(item for item in catalog['items'] if item['id'] == 'translation')
        assert item['version'] == 1 and item['updatedAt'] is None
        body = {'content': item['content'] + '\n优先使用清楚易懂的表达。', 'expectedVersion': 1}
        response = client.put('/api/prompts/translation', headers=LOCAL, json=body)
        assert response.status_code == 200
        saved = response.json()
        assert saved['content'] == body['content'] and saved['version'] == 2 and saved['updatedAt']
        assert client.put('/api/prompts/translation', headers=LOCAL, json=body).status_code == 409
        assert client.put('/api/prompts/not_builtin', headers=LOCAL, json=body).status_code == 404
        assert client.post('/api/prompts', headers=LOCAL, json=body).status_code == 405
        assert client.delete('/api/prompts/translation', headers=LOCAL).status_code == 405
        assert client.put('/api/prompts/translation', json=body).status_code == 403
        for bad in ({**body, 'title': '改标题'}, {**body, 'content': ' '}, {**body, 'content': 'x' * 24001},
                    {**body, 'expectedVersion': True}, {**body, 'expectedVersion': '2'}):
            assert client.put('/api/prompts/translation', headers=LOCAL, json=bad).status_code == 422
        assert client.put('/api/prompts/summary', headers=LOCAL, json={'content': '删除动态预算', 'expectedVersion': 1}).status_code == 422
        assert client.put('/api/prompts/translation', headers=LOCAL, json={**body, 'content': body['content'] + '{{unknown_name}}'}).status_code == 422
    restarted = PromptLibrary(Store(path))
    assert next(item for item in restarted.list()['items'] if item['id'] == 'translation') == saved
    with app.state.store.connect() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0] == 0


def test_concurrent_edit_has_one_winner_without_lost_update(tmp_path):
    library = PromptLibrary(Store(tmp_path / 'race'))
    original = REGISTRY['translation']['content']
    def update(marker):
        try:
            return library.update('translation', original + marker, 1)['content']
        except PromptConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['first', 'second']))
    assert results.count('conflict') == 1
    assert next(item for item in library.list()['items'] if item['id'] == 'translation')['content'] in results


def test_classification_default_keeps_full_current_boundary_contract():
    assert REGISTRY['classification']['content'] == prompt_defaults.PROCESSOR_SYSTEM + prompt_contract()
    assert REGISTRY['classification_reduce']['content'] == prompt_defaults.PROCESSOR_SYSTEM + prompt_contract(reduce=True)


@pytest.mark.parametrize('kind', ['summary', 'translation', 'classification'])
def test_enrichment_real_call_uses_queue_snapshot_and_future_intent_uses_edit(tmp_path, kind):
    store, gateway = Store(tmp_path / kind), Gateway()
    service = EnrichmentService(store, gateway)
    source = publish(store)
    edit(store, kind, '队列版本甲')
    first = service.queue(source, [kind])[0]
    edit(store, kind, '未来版本乙')
    assert execute(store, service)['status'] == 'succeeded'
    system = gateway.calls[-1][1][0]['content']
    assert '队列版本甲' in system and '未来版本乙' not in system
    assert '程序固定约束' in system
    second = service.queue(source, [kind])[0]
    assert second['id'] != first['id']
    assert execute(store, service)['status'] == 'succeeded'
    assert '未来版本乙' in gateway.calls[-1][1][0]['content']


def test_old_job_without_snapshot_never_reads_new_override(tmp_path):
    store, gateway = Store(tmp_path / 'legacy'), Gateway()
    service = EnrichmentService(store, gateway)
    source = publish(store)
    job = service.queue(source, ['translation'])[0]
    with store.connect() as db:
        db.execute('DELETE FROM job_prompt_snapshots WHERE job_id=?', (job['id'],))
    edit(store, 'translation', '后来新增的编辑')
    assert execute(store, service)['status'] == 'succeeded'
    assert gateway.calls[0][1][0]['content'] == prompt_defaults.TRANSLATION


def test_retry_after_edit_reuses_checkpoint_and_original_prompt(tmp_path):
    store, gateway = Store(tmp_path / 'retry'), Gateway()
    service = EnrichmentService(store, gateway)
    source = publish(store, 'Evidence and governance. ' * 500)
    edit(store, 'translation', '旧任务保持甲')
    job = service.queue(source, ['translation'])[0]
    gateway.fail_at = 2
    assert execute(store, service)['status'] == 'failed'
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM ai_checkpoints').fetchone()[0] == 1
    edit(store, 'translation', '新任务改为乙')
    store.retry(job['id'])
    gateway.fail_at = None
    assert execute(store, EnrichmentService(Store(store.root), gateway))['status'] == 'succeeded'
    first_request = gateway.calls[0][1]
    assert sum(request == first_request for _, request in gateway.calls) == 1
    assert all('旧任务保持甲' in request[0]['content'] and '新任务改为乙' not in request[0]['content'] for _, request in gateway.calls)


@pytest.mark.parametrize('kind', ['summary', 'classification'])
def test_enrichment_repair_is_frozen_and_output_guards_remain(tmp_path, kind):
    store, gateway = Store(tmp_path / 'repair'), Gateway()
    service = EnrichmentService(store, gateway)
    source = publish(store)
    edit(store, 'knowledge_repair', '修复甲')
    service.queue(source, [kind])
    edit(store, 'knowledge_repair', '修复乙')
    gateway.rewrite = lambda result: {} if len(gateway.calls) == 1 else result
    assert execute(store, service)['status'] == 'succeeded'
    assert len(gateway.calls) == 2
    assert '修复甲' in gateway.calls[1][1][0]['content'] and '修复乙' not in gateway.calls[1][1][0]['content']
    assert '{{validationError}}' not in gateway.calls[1][1][0]['content']
    edit(store, kind, '允许随意编造引用，不必校验')
    gateway.rewrite = lambda result: {'summary': '伪造', 'keyPoints': [{'text': '伪造', 'blockIds': ['not_registered']}], 'keywords': ['错误']}
    service.queue(source, [kind], force=True)
    assert execute(store, service)['status'] == 'failed'


def test_long_edited_prompt_is_rejected_before_provider_call(tmp_path):
    store, gateway = Store(tmp_path / 'budget'), Gateway()
    service = EnrichmentService(store, gateway)
    source = publish(store)
    gateway.config['contextChars'] = 7000
    edit(store, 'translation', '长' * 10000)
    service.queue(source, ['translation'])
    job = execute(store, service)
    assert job['status'] == 'failed' and '预算' in job['error']
    assert gateway.calls == []


def test_wiki_primary_and_repair_use_frozen_instructions(tmp_path):
    store, gateway = Store(tmp_path / 'wiki'), WikiGateway()
    service = WikiService(store, gateway)
    source = wiki_publish(store)
    edit(store, 'wiki_compile', '编排甲')
    edit(store, 'wiki_repair', '编排修复甲')
    service.queue([source])
    edit(store, 'wiki_compile', '编排乙')
    edit(store, 'wiki_repair', '编排修复乙')
    gateway.rewrite = lambda value: {} if len(gateway.calls) == 1 else value
    assert execute(store, service)['status'] == 'succeeded'
    assert len(gateway.calls) == 2
    assert all('编排甲' in messages[0]['content'] and '编排乙' not in messages[0]['content'] for _, messages in gateway.calls)
    assert '编排修复甲' in gateway.calls[-1][1][0]['content'] and '编排修复乙' not in gateway.calls[-1][1][0]['content']


def test_card_cover_vision_uses_frozen_edit(cover_bench):
    store, _, provider, service, source, _ = cover_bench
    edit(store, 'card_cover', '视觉选择甲')
    service.queue(source)
    edit(store, 'card_cover', '视觉选择乙')
    assert cover_execute(store, service)['status'] == 'succeeded'
    system = provider.calls[0]['messages'][0]['content']
    assert '视觉选择甲' in system and '视觉选择乙' not in system


@pytest.mark.parametrize('kind', ['outline', 'section'])
def test_writing_queue_freezes_edited_system(writing_bench, kind):
    client, app, provider, _, _ = writing_bench
    project = create(writing_bench) if kind == 'outline' else writing_ready(writing_bench)
    id = 'writing_' + kind + '_strict'
    edit(app.state.store, id, '写作快照甲')
    edit(app.state.store, 'writing_citation_match', '范围或逐句复核快照甲')
    section = project['sections'][0]['id'] if kind == 'section' else None
    run = app.state.writing.queue(project['id'], project['version'], kind, section)
    edit(app.state.store, id, '写作快照乙')
    edit(app.state.store, 'writing_citation_match', '范围或逐句复核快照乙')
    with app.state.store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        prompts = json.loads(db.execute('SELECT snapshot_json FROM job_prompt_snapshots WHERE job_id=?',
                                        (run['job']['id'],)).fetchone()[0])
    assert '写作快照甲' in frozen['messages'][0]['content'] and '写作快照乙' not in frozen['messages'][0]['content']
    assert prompts[id]['version'] == 2 and prompts[id]['content'] in frozen['messages'][0]['content']
    packet = json.loads(frozen['messages'][1]['content'])
    if kind == 'outline':
        assert frozen['outlinePolicy'] == packet['outlinePolicy'] == 'abstract-outline-v1'
        assert 'outlinePlanningProtocol' not in frozen and 'planningEvidence' not in packet['outputSchema']['sections'][0]
    else:
        assert frozen['citationReviewPolicy'] == packet['citationReviewPolicy'] == 'deferred-to-check-v1'
    assert run_one(app.state.store, app.state.enrichment, app.state.wiki, app.state.writing)
    assert app.state.store.job(run['job']['id'])['status'] == 'succeeded'
    calls = [app.state.writing.operations.call(item['id'])['call']
             for item in app.state.writing.operations.calls(run['runId'])['items']]
    generation = [call for call in calls if json.loads(call['request']['messages'][1]['content'])['task'] == 'writing_' + kind]
    matching = [call for call in calls if json.loads(call['request']['messages'][1]['content'])['task'] == 'writing_citation_match']
    assert len(calls) == len(generation) == 1 and matching == []
    assert generation[0]['request']['messages'] == frozen['messages']


def test_whole_document_freezes_requirements_and_section_prompt(document):
    store, gateway, service, _ = document
    # This verifies a combined request and its queue-time instructions. Input
    # budget fallback is covered independently by whole-draft planning tests.
    gateway.config['contextChars'] = 64000
    project = ready(document)
    edit(store, 'writing_section_strict', '章节甲')
    edit(store, 'writing_document_strict', '成文甲')
    edit(store, 'writing_citation_match', '逐句复核甲')
    run, job = queue_and_claim(document, project)
    assert service.get(project['id'])['runs'][0]['plan']['strategy'] == 'single'
    with store.connect() as db:
        frozen = json.loads(db.execute('SELECT input_json FROM writing_runs WHERE id=?', (run['runId'],)).fetchone()[0])
        steps = [json.loads(row[0]) for row in db.execute(
            'SELECT input_json FROM writing_document_steps WHERE run_id=? ORDER BY ordinal', (run['runId'],))]
    queued_messages = service._document_messages(frozen, steps)
    assert frozen['citationReviewPolicy'] == 'deferred-to-check-v1'
    assert all(step['citationReviewPolicy'] == 'deferred-to-check-v1'
               and 'citationMatchInstructions' not in step for step in steps)
    edit(store, 'writing_section_strict', '章节乙')
    edit(store, 'writing_document_strict', '成文乙')
    edit(store, 'writing_citation_match', '逐句复核乙')
    observed = []
    matching_observed = []
    complete = gateway.complete
    def capture(config, messages):
        if json.loads(messages[1]['content']).get('task') == 'writing_citation_match':
            matching_observed.append(messages)
        else:
            observed.append(messages)
        return complete(config, messages)
    gateway.complete = capture
    service.process(job)
    assert store.job(run['job']['id'])['status'] == 'succeeded'
    assert len(observed) == 1
    assert matching_observed == [] and gateway.matching_calls == []
    assert observed[0] == queued_messages
    payload = json.loads(observed[0][1]['content'])
    assert len(payload['sections']) == len(steps)
    shared_suffix = frozen['preferenceSnapshot']['sectionSystemSuffix']
    assert observed[0][0]['content'].endswith(shared_suffix)
    assert observed[0][0]['content'].count(shared_suffix) == 1
    assert all(shared_suffix not in item['content'] for item in payload['instructionCatalog'].values())
    for section, step in zip(payload['sections'], steps):
        writing = payload['instructionCatalog'][section['writingInstructionRef']]
        document_prompt = payload['instructionCatalog'][section['documentInstructionRef']]
        assert step['sectionInstructions'].endswith(shared_suffix)
        assert writing['content'] + shared_suffix == step['sectionInstructions']
        assert '章节甲' in writing['content'] and '章节乙' not in writing['content']
        assert writing['promptId'] == section['promptId'] == 'writing_section_strict'
        assert writing['mode'] == section['mode'] == 'strict' and writing['kind'] == 'section'
        assert writing['version'] == step['writingPromptVersion'] == 2
        assert document_prompt['content'] == step['documentPrompt']['content']
        assert '成文甲' in document_prompt['content'] and '成文乙' not in document_prompt['content']
        assert document_prompt['promptId'] == section['documentPromptId'] == 'writing_document_strict'
        assert document_prompt['mode'] == 'strict' and document_prompt['kind'] == 'document'
        assert document_prompt['version'] == section['documentPromptVersion'] == step['documentPrompt']['version'] == 2
        assert section['citationReviewPolicy'] == 'deferred-to-check-v1'
    assert '章节乙' not in json.dumps(observed, ensure_ascii=False) and '成文乙' not in json.dumps(observed, ensure_ascii=False)
    assert '成文甲' not in payload['requirements'] and '成文乙' not in payload['requirements']


@pytest.mark.parametrize('kind', ['summary', 'classification'])
def test_long_document_reduction_reads_edited_frozen_prompt(tmp_path, kind):
    store, gateway = Store(tmp_path / 'reduction'), Gateway()
    service = EnrichmentService(store, gateway)
    source = publish(store, ['Evidence and governance. ' * 500] * 3)
    id = kind + '_reduce'
    edit(store, id, '归并甲')
    service.queue(source, [kind])
    edit(store, id, '归并乙')
    assert execute(store, service)['status'] == 'succeeded'
    reduced = [messages for _, messages in gateway.calls if json.loads(messages[1]['content'])['task'] == id]
    assert reduced
    assert all('归并甲' in messages[0]['content'] and '归并乙' not in messages[0]['content'] for messages in reduced)


@pytest.mark.parametrize('operation', ['polish', 'rewrite', 'shorten', 'expand'])
def test_selection_operations_use_their_own_frozen_prompt(edit_environment, operation):
    store, gateway, service, project_id, section_id = edit_environment
    project = service.get(project_id)
    generated = service.queue(project_id, project['version'], 'section', section_id)
    service.process(store.claim())
    assert store.job(generated['job']['id'])['status'] == 'succeeded'
    project = service.get(project_id)
    content = project['sections'][0]['content']
    id = 'writing_edit_' + operation
    edit(store, id, operation + '选区甲')
    queued, job = queued_edit(edit_environment, project, 0, content.index('，'), operation)
    edit(store, id, operation + '选区乙')
    service.process(job)
    assert store.job(job['id'])['status'] == 'succeeded'
    system = gateway.calls[-1][1][0]['content']
    assert operation + '选区甲' in system and operation + '选区乙' not in system
    # Editing a template cannot turn an edit candidate into an automatic write.
    assert service.get(project_id)['sections'][0]['content'] == content
