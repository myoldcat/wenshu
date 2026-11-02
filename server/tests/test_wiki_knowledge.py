"""Canonical HTTP/graph scope tests over a small, isolated synthetic corpus."""
import json

from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server.store import Store
from server.tests.test_wiki import Gateway, publish, execute
from server.wiki import WikiService
from server.wiki_knowledge import WikiKnowledgeService
from server.wiki_topics import build_topics


LOCAL = {'X-Wenshu-Client': 'local'}


@pytest.fixture
def bench(tmp_path):
    store = Store(tmp_path / 'knowledge')
    models = Gateway()
    wiki = WikiService(store, models)
    sources = []
    for i in range(3):
        source = publish(store, url=f'https://example.org/knowledge-{i}', title=f'来源{i}')
        sources.append(source)
        wiki.queue([source])
        assert execute(store, wiki)['status'] == 'succeeded'
    return store, models, wiki, WikiKnowledgeService(wiki), sources


def rows(store, name):
    with store.connect() as db:
        return sorted([tuple(row) for row in db.execute('SELECT * FROM ' + name)], key=str)


def classified(store, source, level1='artificial_intelligence', second='智能体', third='执行机制'):
    store.patch(source, {'classificationPaths': [{'level1Id': level1, 'level2': second, 'level3': third}]})


def test_hierarchical_topics_share_complete_scopes_before_canvas_budget(bench):
    store, models, wiki, knowledge, sources = bench
    for source in sources:
        classified(store, source)
    before = {name: rows(store, name) for name in ('sources', 'revisions', 'blocks', 'wiki_pages', 'wiki_publications')}
    graph = knowledge.overview(limit=4)
    assert len(graph['topics']) == 1
    assert graph['topics'][0]['label'] == '人工智能'
    assert graph['topics'][0]['sourceCount'] == 3
    assert graph['topics'][0]['collapsedPaths']
    assert graph['stats']['sourcePages'] == 6
    assert graph['stats']['pages'] == 4  # One exact concept, three distinct source entities.
    assert len(graph['nodes']) <= 4
    assert knowledge.overview(limit=100)['topics'] == graph['topics']
    assert {name: rows(store, name) for name in before} == before
    assert len(models.calls) == 3


def test_flat_legacy_labels_are_not_invented_as_classification_paths(bench):
    store, _, wiki, knowledge, sources = bench
    for source in sources:
        store.patch(source, {'tags': ['人工智能', '智能体', '执行机制']})
    graph = knowledge.overview()
    assert [t['label'] for t in graph['topics']] == ['未归类']


def test_distinct_roots_preserve_source_scope_and_history_scope(bench):
    store, _, wiki, knowledge, sources = bench
    classified(store, sources[0], 'artificial_intelligence')
    classified(store, sources[1], 'cybersecurity', '身份认证', '授权机制')
    classified(store, sources[2], 'cybersecurity', '身份认证', '授权机制')
    graph = knowledge.overview()
    topics = {t['label']: t for t in graph['topics']}
    assert {'人工智能', '网络安全'} <= set(topics)
    topic = topics['人工智能']['id']
    state = knowledge._state()
    concept = next(g for g in state['canonical']['groups'].values() if g['type'] == 'concept')
    page = knowledge.page(concept['id'], topic_id=topic)
    assert page['scopeLimited']
    assert page['sourceIds'] == [sources[0]]
    assert {e['sourceId'] for e in page['evidence']} == {sources[0]}
    history = knowledge.page(concept['id'], version=page['version'], topic_id=topic)
    assert {m['sourceId'] for m in history['members']} == {sources[0]}
    assert {e['sourceId'] for e in history['evidence']} == {sources[0]}
    assert all(e['sourceId'] == sources[0] for r in knowledge.search('知识', topic_id=topic)['results'] for e in r['evidence'])


def test_source_entities_merge_undo_and_http_snapshot_conflict(bench):
    store, models, wiki, knowledge, _ = bench
    state = knowledge._state()
    entries = [p['id'] for p in state['pages'] if p['type'] == 'entity'][:2]
    original = rows(store, 'wiki_pages')
    app = create_app(data_dir=store.root, model_service=models)
    with TestClient(app) as client:
        graph = client.get('/api/wiki/knowledge').json()
        assert client.post('/api/wiki/knowledge/merge', json={}).status_code == 403
        response = client.post('/api/wiki/knowledge/merge', headers=LOCAL, json={
            'entryIds': entries, 'label': '共同档案对象', 'expectedSnapshotId': graph['snapshotId']})
        assert response.status_code == 200, response.text
        merged = response.json()
        assert 'decisionId' in merged
        assert client.post('/api/wiki/knowledge/merge', headers=LOCAL, json={
            'entryIds': entries, 'label': '共同档案对象', 'expectedSnapshotId': graph['snapshotId']}).status_code == 409
        proposal_state = client.get('/api/wiki/resolution/proposals').json()
        decision = next(d for d in proposal_state['decisions'] if d['id'] == merged['decisionId'])
        assert decision['undoable']
        response = client.post('/api/wiki/knowledge/undo', headers=LOCAL, json={
            'decisionId': merged['decisionId'], 'expectedSnapshotId': proposal_state['snapshotId']})
        assert response.status_code == 200, response.text
        decisions = client.get('/api/wiki/resolution/proposals').json()['decisions']
        assert all(not d['undoable'] for d in decisions if d['kind'] == 'undo')
        assert client.get('/api/wiki').json()['stats']['pages'] == 6
    assert rows(store, 'wiki_pages') == original
    assert len(models.calls) == 3  # Queuing a synthesis never makes an inline model request.


def test_manual_related_edges_do_not_pretend_to_have_raw_support(bench):
    _, _, _, knowledge, _ = bench
    state = knowledge._state()
    ids = [p['id'] for p in state['pages'] if p['type'] == 'entity'][:2]
    result = knowledge.mutate('reject', ids, state['snapshotId'], judgment='related')
    graph = knowledge.overview()
    edge = next(e for e in graph['edges'] if e.get('origin') == 'manual_decision')
    assert edge['layer'] == 'navigation' and edge['evidence'] == []
    knowledge.mutate('undo', [], result['snapshotId'], decision_id=result['decisionId'])
    assert not any(e.get('origin') == 'manual_decision' for e in knowledge.overview()['edges'])


def test_neighborhood_paging_is_bounded_and_invalidated_by_decisions(bench):
    _, _, _, knowledge, _ = bench
    state = knowledge._state()
    root = next(g['id'] for g in state['canonical']['groups'].values() if g['type'] == 'concept')
    first = knowledge.neighborhood(root, limit=4)
    assert first['hasMore'] and len(first['nodes']) <= 4
    seen = {n['id'] for n in first['nodes'] if n['type'] != 'document'}
    page = first
    while page['hasMore']:
        page = knowledge.neighborhood(root, limit=4, offset=page['nextOffset'], snapshot_id=page['snapshotId'])
        assert len(page['nodes']) <= 4
        seen.update(n['id'] for n in page['nodes'] if n['type'] != 'document')
    assert len(seen) == 4
    ids = [p['id'] for p in state['pages'] if p['type'] == 'entity'][:2]
    knowledge.mutate('reject', ids, state['snapshotId'])
    response = knowledge.neighborhood(root, limit=4, offset=first['nextOffset'], snapshot_id=first['snapshotId'])
    assert response['reloadRequired'] and response['nodes'] == []


def test_reading_and_initializing_never_enqueues_real_processing(bench):
    store, models, wiki, knowledge, _ = bench
    before = rows(store, 'jobs')
    knowledge.overview()
    knowledge.search('知识')
    state = knowledge._state()
    for key in state['canonical']['groups']:
        knowledge.page(key)
    assert rows(store, 'jobs') == before
    assert len(models.calls) == 3


def test_topic_search_does_not_recompact_or_expand_classifications(bench):
    store, _, _, knowledge, sources = bench
    classified(store, sources[0], 'artificial_intelligence', '智能体', '执行机制')
    classified(store, sources[1], 'artificial_intelligence', '模型', '训练')
    classified(store, sources[2], 'artificial_intelligence', '模型', '训练')
    before = knowledge.overview()['topics']
    knowledge.search('知识', topic_id=before[0]['id'])
    assert knowledge.overview()['topics'] == before
