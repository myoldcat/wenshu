"""Confirmed materials are durable original evidence scopes, never derived text."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.store import Store
from server.writing import WritingConflict, WritingService
from server.writing_review import build_review
from server.tests.test_writing_integrity import ControlledGateway

LOCAL = {'X-Wenshu-Client': 'local'}


def add_source(store, number=0):
    title = f'治理参考材料 {number}'
    paragraphs = ['人工智能治理需要保存原始证据，并核对事实和相关公共政策的适用边界。',
                  '数据治理的审计制度要求记录修改历史、固定版本及人工确认情况。',
                  '企业融资分析应核实估值口径，与当前主题无关的背景不能充当证据。']
    job = store.queue('file', {}, title)
    claimed = store.claim()
    url = f'https://example.test/materials/{number}'
    parsed = {'title': title, 'body': paragraphs, 'language': 'zh', 'parser_version': 'materials-test',
              'blocks': [{'kind': 'paragraph', 'text': text, 'ordinal': i} for i, text in enumerate(paragraphs)]}
    store.publish(claimed, {'url': url}, ('<p>' + '</p><p>'.join(paragraphs) + '</p>').encode(), parsed, url)
    source_id = store.job(job['id'])['source_id']
    with store.connect() as db:
        revision = db.execute('SELECT current_revision FROM sources WHERE id=?', (source_id,)).fetchone()[0]
        blocks = [dict(row) for row in db.execute('SELECT * FROM blocks WHERE revision_id=? ORDER BY ordinal', (revision,))]
    return source_id, revision, blocks


@pytest.fixture
def materials(tmp_path):
    store = Store(tmp_path / 'isolated-materials')
    gateway = ControlledGateway()
    service = WritingService(store, gateway)
    source, revision, blocks = add_source(store)
    return store, gateway, service, source, revision, blocks


def selection(source, revision, *blocks):
    return {'recommendationId': None, 'passages': [{'sourceId': source, 'revisionId': revision, 'blockId': b['id']} for b in blocks]}


def create(materials, **changes):
    _, _, service, source, revision, blocks = materials
    return service.create({'title': '人工智能治理', 'sourceIds': [source],
                           'materialSelection': selection(source, revision, blocks[1]), **changes})


def test_confirmed_passages_survive_reopen_and_restrict_outline_and_section(materials):
    store, gateway, service, source, revision, blocks = materials
    project = create(materials)
    assert project['sources'][0]['blockIds'] == [blocks[1]['id']]
    reopened = WritingService(store, gateway).get(project['id'])
    assert reopened['materialSelection'] == project['materialSelection']
    with store.connect() as db:
        for section in (None, {'id': 'section', 'title': '治理', 'goal': '', 'mode': 'inherit', 'targetWords': 300}):
            messages, chosen = service._messages(project, 'section' if section else 'outline', section, gateway.config, db)
            assert [b['blockId'] for b in chosen] == [blocks[1]['id']]
            assert len(json.loads(messages[1]['content'])['blocks']) == 1


def test_whole_article_and_legacy_patch_keep_narrowed_scope(materials):
    store, gateway, service, source, revision, blocks = materials
    project = create(materials)
    project = service.patch(project['id'], project['version'], {'sourceIds': [source], 'brief': {'topic': '新需求'}})
    assert project['sources'][0]['blockIds'] == [blocks[1]['id']]
    project = service.patch(project['id'], project['version'], {'materialSelection': None})
    assert 'blockIds' not in project['sources'][0]
    with store.connect() as db:
        assert len(service._messages(project, 'outline', None, gateway.config, db)[1]) == 3


@pytest.mark.parametrize('kind', ['foreign_source', 'foreign_revision', 'foreign_block', 'missing_block'])
def test_rejects_forged_original_anchors(materials, kind):
    store, _, service, source, revision, blocks = materials
    other, other_revision, other_blocks = add_source(store, 1)
    value = selection(source, revision, blocks[0])
    field, replacement = {'foreign_source': ('sourceId', other), 'foreign_revision': ('revisionId', other_revision),
                          'foreign_block': ('blockId', other_blocks[0]['id']), 'missing_block': ('blockId', 'not-found')}[kind]
    value['passages'][0][field] = replacement
    with pytest.raises(ValueError):
        create(materials, materialSelection=value)
    assert service.list() == []


def test_passage_only_change_invalidates_outline_and_late_candidate(materials):
    store, _, service, source, revision, blocks = materials
    project = create(materials)
    project = service.outline(project['id'], project['version'], [{'title': '治理', 'mode': 'inherit'}])
    project = service.confirm(project['id'], project['version'])
    run = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    job = store.claim()
    project = service.patch(project['id'], project['version'], {'materialSelection': selection(source, revision, blocks[0])})
    assert not project['outlineConfirmed']
    service.process(job)
    latest = service.get(project['id'])
    assert latest['sections'][0]['content'] == ''
    assert latest['runs'][0]['candidate'] and not latest['runs'][0]['applied']
    with pytest.raises(WritingConflict, match='资料范围'):
        service.apply(project['id'], run['runId'], latest['version'])


def test_review_flags_removed_passage_without_rewriting_manual_text(materials):
    store, _, service, source, revision, blocks = materials
    project = create(materials)
    project = service.outline(project['id'], project['version'], [{'title': '治理', 'mode': 'inherit'}])
    project = service.confirm(project['id'], project['version'])
    run = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    service.process(store.claim())
    project = service.get(project['id'])
    content = project['sections'][0]['content']
    assert content
    project = service.patch(project['id'], project['version'], {'materialSelection': selection(source, revision, blocks[0])})
    assert project['sections'][0]['content'] == content
    with store.connect() as db:
        report = build_review(db, project)
    assert any(issue['code'] == 'citation_out_of_scope' for issue in report['issues'])


def test_dozens_of_articles_are_accepted_and_100_is_enforced(materials):
    store, _, service, source, _, _ = materials
    ids = [source] + [add_source(store, i)[0] for i in range(1, 41)]
    project = create(materials, sourceIds=ids, materialSelection=None)
    assert len(project['sources']) == 41
    with pytest.raises(ValueError, match='100'):
        create(materials, sourceIds=ids * 3, materialSelection=None)


def test_http_confirmation_contract_and_stale_project_version(materials):
    store, gateway, _, source, revision, blocks = materials
    app = create_app(data_dir=store.root, model_service=gateway)
    with TestClient(app) as client:
        result = client.post('/api/writing/projects', headers=LOCAL, json={'title': '主题', 'sourceIds': [source],
                             'materialSelection': selection(source, revision, blocks[1])})
        assert result.status_code == 201, result.text
        project = result.json()['project']
        endpoint = '/api/writing/projects/' + project['id']
        request = {'expectedVersion': 1, 'materialSelection': selection(source, revision, blocks[0])}
        assert client.patch(endpoint, headers=LOCAL, json=request).status_code == 200
        assert client.patch(endpoint, headers=LOCAL, json=request).status_code == 409
        forged = copy.deepcopy(request)
        forged['expectedVersion'] = 2
        forged['materialSelection']['passages'][0]['quote'] = 'model-invented'
        assert client.patch(endpoint, headers=LOCAL, json=forged).status_code == 422
