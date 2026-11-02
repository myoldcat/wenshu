"""Preparation, persistence and generation behavior for the six-step workflow."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.store import Store
from server.writing import WritingService
from server.tests.test_writing_integrity import ControlledGateway, add_source

LOCAL = {'X-Wenshu-Client': 'local'}


class AssistGateway(ControlledGateway):
    def complete(self, config, messages):
        packet = json.loads(messages[1]['content'])
        if packet['task'].startswith('writing_assist_'):
            self.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
            result = {'title': '原始证据与知识治理'} if packet['task'].endswith('title') else {'queries': ['知识治理 原始证据', '资料 固定版本']}
            return {'text': json.dumps(result, ensure_ascii=False), 'usage': {'total_tokens': 12}}
        return super().complete(config, messages)


@pytest.fixture
def bench(tmp_path):
    store = Store(tmp_path / 'six-step')
    gateway = AssistGateway()
    service = WritingService(store, gateway)
    source = add_source(store)
    return store, gateway, service, source


def test_new_defaults_and_optional_title(bench):
    _, _, service, _ = bench
    project = service.create({'brief': {'topic': '知识治理'}})
    assert project['title'] == '知识治理'
    assert project['workflowVersion'] == 'writing-six-step-v1'
    assert project['mode'] == 'extended'
    assert project['brief']['targetWords'] == 6000
    assert project['brief']['workflow'] == 'chapter'
    long_topic = '知识治理' * 60
    project = service.create({'brief': {'topic': long_topic}})
    assert len(project['title']) == 200 and project['brief']['topic'] == long_topic


def test_assistance_operations_attach_to_saved_project(bench):
    store, _, service, source = bench
    title = service.assist('title', {'topic': '原始证据与知识治理'})
    queries = service.assist('material-queries', {'topic': '原始证据与知识治理'})
    project = service.create({'title': title['title'], 'sourceIds': [source],
        'brief': {'topic': '原始证据与知识治理', 'assistanceIds': [title['assistanceId']]},
        'materialSelection': {'recommendationId': None, 'passages': [], 'assistanceIds': [queries['assistanceId']]}})
    with store.connect() as db:
        ids = {row[0] for row in db.execute('SELECT recommendation_id FROM writing_project_recommendations WHERE project_id=?', (project['id'],))}
    assert ids == {title['assistanceId'], queries['assistanceId']}


def test_no_sources_force_extended_without_changing_model_preferences(bench):
    _, gateway, service, source = bench
    project = service.create({'title': '主题', 'sourceIds': [source], 'mode': 'strict'})
    before = gateway.get_preferences()
    project = service.patch(project['id'], project['version'], {'sourceIds': [], 'mode': 'strict'})
    assert project['mode'] == 'extended'
    assert gateway.get_preferences() == before


def test_material_confirmation_persists_user_and_system_lists(bench):
    store, _, service, source = bench
    data = {'recommendationId': None, 'passages': [], 'queries': ['原始证据'],
            'userSourceIds': [source], 'items': [{'sourceId': source, 'title': '已选资料', 'summary': '资料摘要'}], 'confirmed': True}
    project = service.create({'title': '主题', 'sourceIds': [source], 'materialSelection': data})
    reopened = WritingService(store, service.models).get(project['id'])
    assert reopened['materialSelection'] == data
    assert 'blockIds' not in reopened['sources'][0]


def test_assist_http_does_not_search_create_project_or_queue_jobs(bench):
    store, gateway, _, _ = bench
    app = create_app(data_dir=store.root, model_service=gateway)
    with TestClient(app) as client:
        for endpoint, key in (('title', 'title'), ('material-queries', 'queries')):
            result = client.post('/api/writing/assist/' + endpoint, headers=LOCAL,
                                 json={'topic': '原始证据与知识治理', 'targetWords': 6000})
            assert result.status_code == 200, result.text
            assert result.json()[key]
        assert client.get('/api/writing/projects').json()['projects'] == []
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM retrieval_snapshots').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM writing_material_interactions').fetchone()[0] == 2


def test_new_section_generation_defers_semantic_review_and_keeps_markers(bench):
    store, gateway, service, source = bench
    project = service.create({'title': '主题', 'sourceIds': [source]})
    project = service.outline(project['id'], project['version'], [{'title': '证据', 'goal': '说明证据'}])
    project = service.confirm(project['id'], project['version'])
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    service.process(store.claim())
    saved = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert gateway.matching_calls == []
    assert '[E1]' in saved['sections'][0]['content']
    assert saved['sections'][0]['citationBindingState'] == 'unreviewed'
    assert '本次正文引用规则' in gateway.calls[0][1][0]['content']


def test_bridge_paragraph_does_not_require_fabricated_citation(bench):
    store, gateway, service, source = bench
    gateway.transform = lambda result, packet: dict(result, paragraphs=[
        *result['paragraphs'], {'text': '下面接着讨论资料的保存。', 'kind': 'supported', 'evidence': []}])
    project = service.create({'title': '主题', 'sourceIds': [source]})
    project = service.outline(project['id'], project['version'], [{'title': '证据'}])
    project = service.confirm(project['id'], project['version'])
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    service.process(store.claim())
    saved = service.get(project['id'])
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    assert saved['sections'][0]['content'].endswith('下面接着讨论资料的保存。')
    assert len(saved['sections'][0]['evidence']) == 1
