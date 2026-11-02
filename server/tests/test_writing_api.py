"""HTTP -> durable writing jobs -> pinned evidence -> manual review and export."""
from collections import Counter
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.model_gateway import ModelService
from server.store import Store
from server.worker import run_one
from server.tests.test_writing_integrity import controlled_matching_result, controlled_outline_result


LOCAL = {'X-Wenshu-Client': 'local'}


class Provider:
    def __init__(self):
        self.calls = []
        self.generated_paragraphs = []

    def __call__(self, request):
        body = json.loads(request.content)
        payload = json.loads(body['messages'][1]['content'])
        self.calls.append(payload)
        if payload['task'] == 'writing_citation_match':
            result = json.loads(controlled_matching_result(payload, self.generated_paragraphs)['text'])
        elif payload['task'] == 'writing_outline':
            result = {'sections': [{'title': '资料治理原则', 'goal': '说明来源与版本的用途', 'mode': 'inherit', 'targetWords': 300}], 'issues': []}
            result = controlled_outline_result(payload, result)
        elif payload['task'] == 'writing_edit':
            result = {'replacementText': payload['selectedText'].replace('重复文字', '调整措辞'),
                      'changeSummary': '保持原意，调整表达', 'issues': [], 'supplement': False}
        else:
            assert payload['task'] == 'writing_section'
            assert payload['mode'] == 'strict'
            block = payload['blocks'][0]
            result = {'paragraphs': [{'text': '资料治理保留来源及原文版本。', 'kind': 'supported',
                                     'evidence': [{'blockId': block['blockId'], 'quote': block['text'],
                                                   'claim': '资料治理保留来源及原文版本。'}]}], 'issues': []}
            self.generated_paragraphs = result['paragraphs']
        return httpx.Response(200, json={'model': body['model'], 'choices': [{'message': {
            'content': json.dumps(result, ensure_ascii=False)}, 'finish_reason': 'stop'}],
            'usage': {'total_tokens': 25}})


@pytest.fixture
def bench(tmp_path):
    store = Store(tmp_path / 'data')
    provider = Provider()
    models = ModelService(store, transport=httpx.MockTransport(provider))
    app = create_app(data_dir=store.root, model_service=models)
    with TestClient(app) as client:
        config = client.post('/api/models', headers=LOCAL, json={'name': '写作测试', 'baseUrl': 'http://model.test/v1',
                              'model': 'writing-test', 'maxOutputTokens': 4096}).json()['model']
        assert client.put('/api/ai/preferences', headers=LOCAL, json={'summaryModelId': config['id']}).status_code == 200
        raw = '<!doctype html><html><head><meta charset="utf-8"><title>治理资料</title></head><body><article><p>资料治理应保留原始来源、固定版本和原文段落。读者能够回查每一项具体论述的依据，并核对语境和适用条件，避免将摘要错误地当成独立来源。</p></article></body></html>'.encode()
        job = client.post('/api/imports/files', headers=LOCAL, files={'files': ('sample.html', raw, 'text/html')}).json()['jobs'][0]
        assert run_one(app.state.store, app.state.enrichment, app.state.wiki, app.state.writing)
        finished = app.state.store.job(job['id'])
        assert finished['status'] == 'succeeded', finished
        yield client, app, provider, finished['source_id'], config['id']


def create(bench, **changes):
    client, _, _, source, _ = bench
    data = {'title': '写作流程验收', 'brief': {'topic': '资料治理', 'purpose': '说明原文证据的重要性'},
            'mode': 'strict', 'sourceIds': [source], **changes}
    response = client.post('/api/writing/projects', headers=LOCAL, json=data)
    assert response.status_code == 201, response.text
    return response.json()['project']


def generate(bench, project, kind='outline'):
    client, app, _, _, _ = bench
    path = f'/api/writing/projects/{project["id"]}/'
    path += 'generate-outline' if kind == 'outline' else f'sections/{project["sections"][0]["id"]}/generate'
    response = client.post(path, headers=LOCAL, json={'expectedVersion': project['version']})
    assert response.status_code == 202, response.text
    job = response.json()['job']
    assert run_one(app.state.store, app.state.enrichment, app.state.wiki, app.state.writing)
    finished = app.state.store.job(job['id'])
    assert finished['status'] == 'succeeded', finished
    return client.get(f'/api/writing/projects/{project["id"]}').json()['project']


def ready(bench):
    client = bench[0]
    project = generate(bench, create(bench))
    response = client.post(f'/api/writing/projects/{project["id"]}/confirm-outline', headers=LOCAL, json={'expectedVersion': project['version']})
    assert response.status_code == 200, response.text
    return response.json()['project']


def test_complete_http_worker_citation_review_export_and_reopen(bench):
    client, app, provider, source, _ = bench
    project = generate(bench, ready(bench), 'section')
    assert Counter(('outline_scope' if call.get('reviewPurpose') == 'outline_scope' else call['task'])
                   for call in provider.calls) == {
        'writing_outline': 1, 'writing_section': 1}
    assert not any(call.get('reviewPurpose') == 'outline_scope' for call in provider.calls)
    section = project['sections'][0]
    assert section['content'] and section['effectiveMode'] == 'strict'
    assert project['runs'][0]['applied'] and project['runs'][0]['promptVersion']
    evidence = section['evidence'][0]
    document = client.get('/api/documents/' + source, params={'revision_id': evidence['revisionId']}).json()['document']
    original = next(block for block in document['blocks'] if block['id'] == evidence['blockId'])
    assert original['text'][evidence['start']:evidence['end']] == evidence['quote']
    path = f'/api/writing/projects/{project["id"]}'
    check = client.post(path + '/match-citations', headers=LOCAL, json={'expectedVersion': project['version']})
    assert check.status_code == 202, check.text
    assert run_one(app.state.store, app.state.enrichment, app.state.wiki, app.state.writing)
    project = client.get(path).json()['project']
    assert provider.calls[-1]['task'] == 'writing_citation_match'
    review = client.post(path + '/review', headers=LOCAL, json={'expectedVersion': project['version']})
    assert review.status_code == 200
    assert review.json()['review']['semanticVerified'] is False
    assert review.json()['review']['counts']['errors'] == 0
    assert review.json()['project']['version'] == project['version']
    export = client.get(path + '/export?format=html')
    assert export.status_code == 200 and 'attachment;' in export.headers['content-disposition']
    assert evidence['quote'] in export.text and evidence['revisionId'] in export.text
    assert '全文校验记录' not in export.text and '不代表已核验事实' not in export.text
    assert client.get(path + '/export?format=docx').status_code == 422
    history = client.get(path + '/history').json()['versions']
    assert history[0]['version'] == project['version']
    original_project = client.get(path + '/history/1').json()['project']
    assert original_project['sections'] == []
    restarted = create_app(data_dir=app.state.store.root, model_service=app.state.models)
    with TestClient(restarted) as second:
        reopened = second.get(path).json()['project']
        assert reopened['sections'] == project['sections']
        assert reopened['review']['semanticVerified'] is False


def test_cas_manual_edit_lock_and_escaped_export(bench):
    client = bench[0]
    project = generate(bench, ready(bench), 'section')
    path = f'/api/writing/projects/{project["id"]}'
    section_path = path + '/sections/' + project['sections'][0]['id']
    edited = client.patch(section_path, headers=LOCAL, json={'expectedVersion': project['version'], 'content': '<script>alert(1)</script>人工稿'})
    assert edited.status_code == 200
    latest = edited.json()['project']
    assert latest['sections'][0]['manual']
    stale = client.patch(section_path, headers=LOCAL, json={'expectedVersion': project['version'], 'content': '陈旧页面稿'})
    assert stale.status_code == 409
    locked = client.patch(section_path, headers=LOCAL, json={'expectedVersion': latest['version'], 'locked': True}).json()['project']
    assert client.patch(section_path, headers=LOCAL, json={'expectedVersion': locked['version'], 'content': '覆盖'}).status_code == 409
    assert client.post(section_path + '/generate', headers=LOCAL, json={'expectedVersion': locked['version']}).status_code == 409
    review = client.post(path + '/review', headers=LOCAL, json={'expectedVersion': locked['version']}).json()['review']
    assert any(i['code'] in ('citation_review_pending', 'citation_binding_stale') for i in review['issues'])
    assert not any(i['code'] == 'uncited_paragraph' for i in review['issues'])
    export = client.get(path + '/export').text
    assert '<script>' not in export and '&lt;script&gt;' in export
    unlocked = client.patch(section_path, headers=LOCAL, json={'expectedVersion': locked['version'], 'locked': False}).json()['project']
    assert unlocked['review'] is None


def test_generation_requires_confirmation_and_no_materials_use_extended(bench):
    client, _, provider, _, _ = bench
    project = create(bench, sourceIds=[])
    assert project['mode'] == 'extended'
    path = f'/api/writing/projects/{project["id"]}'
    project = client.put(path + '/outline', headers=LOCAL, json={'expectedVersion': project['version'], 'sections': [{'title': '第一章', 'mode': 'inherit'}]}).json()['project']
    endpoint = path + '/sections/' + project['sections'][0]['id'] + '/generate'
    assert client.post(endpoint, headers=LOCAL, json={'expectedVersion': project['version']}).status_code == 422
    project = client.post(path + '/confirm-outline', headers=LOCAL, json={'expectedVersion': project['version']}).json()['project']
    assert client.post(endpoint, headers=LOCAL, json={'expectedVersion': project['version']}).status_code == 202
    assert provider.calls == []


def test_boundaries_and_unknown_or_malformed_fields(bench):
    client = bench[0]
    assert client.post('/api/writing/projects', json={'title': 'blocked'}).status_code == 403
    assert client.post('/api/writing/projects', headers=LOCAL, json={'title': 'x', 'sourceIds': ['x'] * 21}).status_code == 422
    project = create(bench)
    path = f'/api/writing/projects/{project["id"]}'
    assert client.patch(path, headers=LOCAL, json={'expectedVersion': True, 'title': 'x'}).status_code == 422
    assert client.patch(path, headers=LOCAL, json={'expectedVersion': project['version'], 'brief': None}).status_code == 422
    assert client.put(path + '/outline', headers=LOCAL, json={'expectedVersion': project['version'], 'sections': [{'id': 'invented', 'title': 'x'}]}).status_code == 422
    assert client.get(path + '/history/99').status_code == 404


def utf16(value):
    return len(value.encode('utf-16-le')) // 2


def editable(bench, *, mode='strict', content='  开头😀：重复文字。\n\n后段😀：重复文字。 尾部  '):
    client = bench[0]
    project = create(bench, mode=mode)
    path = f'/api/writing/projects/{project["id"]}'
    project = client.put(path + '/outline', headers=LOCAL, json={'expectedVersion': project['version'],
                         'sections': [{'title': '手工章节'}]}).json()['project']
    response = client.patch(path + '/sections/' + project['sections'][0]['id'], headers=LOCAL,
                            json={'expectedVersion': project['version'], 'content': content})
    assert response.status_code == 200, response.text
    return response.json()['project']


def edit_body(project, *, operation='polish', exact='重复文字'):
    section = project['sections'][0]
    start = section['content'].rindex(exact)
    return {'expectedVersion': project['version'], 'expectedSectionVersion': section['version'],
            'baseContentHash': section['contentHash'], 'operation': operation,
            'selection': {'start': utf16(section['content'][:start]), 'end': utf16(section['content'][:start + len(exact)]),
                          'selectedExact': exact, 'unit': 'utf16'}}


@pytest.mark.parametrize('operation', ['polish', 'rewrite', 'shorten', 'expand'])
def test_selection_operations_are_durable_candidates_with_exact_http_application(bench, operation):
    client, app, provider, _, _ = bench
    project = editable(bench, mode='extended' if operation == 'expand' else 'strict')
    original = project['sections'][0]['content']
    assert original.startswith('  ') and original.endswith('  ')
    path = f'/api/writing/projects/{project["id"]}'
    response = client.post(path + '/sections/' + project['sections'][0]['id'] + '/edit', headers=LOCAL,
                           json=edit_body(project, operation=operation))
    assert response.status_code == 202, response.text
    assert run_one(app.state.store, app.state.enrichment, app.state.wiki, app.state.writing)
    job = app.state.store.job(response.json()['job']['id'])
    assert job['status'] == 'succeeded' and job['stage'] == 'candidate', job
    current = client.get(path).json()['project']
    assert current['version'] == project['version']
    assert current['sections'][0]['content'] == original
    run = current['runs'][0]
    assert run['kind'] == 'edit' and run['operation'] == operation and not run['applied']
    assert run['editTarget']['contentHash'] == project['sections'][0]['contentHash']
    candidate = run['candidate']['edit']
    assert not candidate['semanticVerified']
    if operation == 'expand':
        assert '【模型补充·待核实】' not in candidate['replacementText']
        assert candidate['extendedContentPolicy'] == 'retain-as-prose-v2'
    else:
        assert candidate['issues']
    restarted = create_app(data_dir=app.state.store.root, model_service=app.state.models)
    with TestClient(restarted) as second:
        persisted = second.get(path).json()['project']
        assert persisted['runs'][0]['candidate'] == run['candidate']
        applied = second.post(path + '/runs/' + run['id'] + '/apply', headers=LOCAL,
                              json={'expectedVersion': project['version']})
        assert applied.status_code == 200, applied.text
        edited = applied.json()['project']
        start = original.rindex('重复文字')
        expected = original[:start] + candidate['replacementText'] + original[start + len('重复文字'):]
        assert edited['sections'][0]['content'] == expected
        assert edited['sections'][0]['manual'] and edited['review'] is None
        assert second.get(path + '/history/' + str(project['version'])).json()['project']['sections'][0]['content'] == original
        assert second.post(path + '/runs/' + run['id'] + '/apply', headers=LOCAL,
                           json={'expectedVersion': edited['version']}).status_code == 409
    assert len(provider.calls) == 1


def test_selection_http_rejects_surrogate_middle_stale_hash_and_missing_evidence(bench):
    client, _, provider, _, _ = bench
    project = editable(bench)
    endpoint = f'/api/writing/projects/{project["id"]}/sections/{project["sections"][0]["id"]}/edit'
    original = project['sections'][0]['content']
    body = edit_body(project)
    body['baseContentHash'] = '0' * 64
    assert client.post(endpoint, headers=LOCAL, json=body).status_code == 409
    body = edit_body(project)
    body['selection']['start'] = utf16(original[:original.index('😀')]) + 1
    assert client.post(endpoint, headers=LOCAL, json=body).status_code == 422
    body = edit_body(project, operation='expand')
    assert client.post(endpoint, headers=LOCAL, json=body).status_code == 422
    body = edit_body(project)
    body['selection']['unit'] = 'codepoint'
    assert client.post(endpoint, headers=LOCAL, json=body).status_code == 422
    assert provider.calls == []
