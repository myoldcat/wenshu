"""Deterministic semantic graphs and isolated Wiki HTTP fixtures; no real model."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server.graph_analytics import GraphAnalytics, GraphSnapshotConflict
from server.store import Store, dumps
from server.tests.test_wiki import Gateway, execute, publish
from server.tests.test_wiki_knowledge import classified
from server.wiki import WikiService
from server.wiki_knowledge import WikiKnowledgeService


def node(key, **extra):
    return {'id': key, 'label': key.upper(), 'type': 'concept', 'stale': False, **extra}


def edge(left, right, **extra):
    return {'from': left, 'to': right, 'layer': 'semantic', 'stale': False, 'evidence': [], **extra}


def analyze(engine, nodes, edges, snapshot='snapshot-1', **kwargs):
    return engine.analyze(mode='source', snapshot_id=snapshot, scope='all', nodes=nodes, edges=edges, **kwargs)


def test_complete_path_ranks_bridge_sink_core_and_isolate_without_changing_inputs():
    nodes = [node(key) for key in 'abcdef']
    edges = [edge(a, b) for a, b in zip('abcd', 'bcde')]
    before = deepcopy((nodes, edges))
    result = analyze(GraphAnalytics(), nodes, edges)
    assert result['graph']['nodeCount'] == 6 and result['graph']['edgeCount'] == 4
    assert result['rankings']['betweenness'][0]['id'] == 'c'
    assert result['rankings']['pagerank'][0]['id'] == 'e'
    assert result['metrics']['c']['betweenness'] == pytest.approx(0.4)
    assert result['metrics']['b']['degree'] == 2
    assert result['metrics']['b']['inDegree'] == result['metrics']['b']['outDegree'] == 1
    assert result['metrics']['f']['degree'] == result['metrics']['f']['coreNumber'] == 0
    assert result['metrics']['c']['coreNumber'] == 1
    assert sum(item['pagerank'] for item in result['metrics'].values()) == pytest.approx(1)
    assert (nodes, edges) == before


def test_navigation_provenance_stale_and_duplicate_relations_do_not_inflate_importance():
    nodes = [node(key) for key in 'abcxyz'] + [node('old', stale=True), node('doc', type='document')]
    edges = [edge('a', 'b'), edge('b', 'c'), edge('a', 'b', label='another relation'), edge('b', 'a'),
             edge('x', 'y', layer='navigation'), edge('x', 'z', layer='navigation'),
             edge('x', 'doc', layer='provenance'), edge('x', 'a', stale=True),
             edge('x', 'b', evidence=[{'stale': True}]), edge('x', 'old'), edge('x', 'x')]
    result = analyze(GraphAnalytics(), nodes, edges)
    assert result['graph']['nodeCount'] == 6
    assert result['graph']['edgeCount'] == 2 and result['graph']['directedEdgeCount'] == 3
    assert result['metrics']['x']['degree'] == 0
    assert result['metrics']['a']['degree'] == 1
    assert result['metrics']['a']['outDegree'] == result['metrics']['a']['inDegree'] == 1
    assert result['rankings']['degree'][0]['id'] == 'b'
    assert result['graph']['excluded'] == {'staleNodes': 1, 'nonSemanticEdges': 3, 'staleEdges': 2,
                                           'selfLoops': 1, 'unavailableEndpoints': 1}


def test_triangular_core_and_empty_or_singleton_graphs():
    triangle = analyze(GraphAnalytics(), [node(key) for key in 'abcd'],
                       [edge('a', 'b'), edge('b', 'c'), edge('c', 'a'), edge('c', 'd')])
    assert {triangle['metrics'][key]['coreNumber'] for key in 'abc'} == {2}
    assert triangle['metrics']['d']['coreNumber'] == 1
    empty = analyze(GraphAnalytics(), [], [])
    assert empty['metrics'] == {} and all(not ranking for ranking in empty['rankings'].values())
    assert empty['graph']['complete'] and empty['graph']['nodeCount'] == 0
    singleton = analyze(GraphAnalytics(), [node('a')], [])
    assert singleton['metrics']['a']['degreeCentrality'] == 0
    assert singleton['metrics']['a']['pagerank'] == pytest.approx(1)


def test_sampled_betweenness_is_explicit_deterministic_and_uses_full_node_scope(monkeypatch):
    import server.graph_analytics as module
    monkeypatch.setattr(module, 'EXACT_BETWEENNESS_LIMIT', 4)
    monkeypatch.setattr(module, 'BETWEENNESS_SAMPLES', 3)
    nodes = [node(key) for key in 'abcdefg']
    edges = [edge(a, b) for a, b in zip('abcdef', 'bcdefg')]
    first = analyze(GraphAnalytics(), nodes, edges)
    second = analyze(GraphAnalytics(), list(reversed(nodes)), list(reversed(edges)))
    assert first['metrics'] == second['metrics']
    assert first['graph']['nodeCount'] == 7
    assert first['algorithms']['betweenness']['approximate']
    assert first['algorithms']['betweenness']['sampleSize'] == 3
    assert first['algorithms']['betweenness']['seed'] == 0


def test_pagerank_nonconvergence_never_caches_partial_metrics(monkeypatch):
    import networkx as nx
    from server.graph_analytics import GraphAnalyticsUnavailable

    def fail(*args, **kwargs):
        raise nx.PowerIterationFailedConvergence(1000)

    engine = GraphAnalytics()
    monkeypatch.setattr(nx, 'pagerank', fail)
    with pytest.raises(GraphAnalyticsUnavailable):
        analyze(engine, [node('a')], [])
    assert not engine._cache


def test_lru_cache_is_bounded_thread_safe_defensive_and_snapshot_conflicts_are_not_cached(monkeypatch):
    engine = GraphAnalytics(capacity=2)
    original, calls = engine._compute, []

    def compute(*args):
        calls.append(args[1])
        return original(*args)

    monkeypatch.setattr(engine, '_compute', compute)
    nodes, edges = [node(key) for key in 'abc'], [edge('a', 'b'), edge('b', 'c')]
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: analyze(engine, nodes, edges), range(16)))
    assert calls == ['snapshot-1']
    assert sum(result['cacheHit'] for result in results) == 15
    results[0]['metrics']['a']['degree'] = 999
    assert analyze(engine, nodes, edges)['metrics']['a']['degree'] == 1
    analyze(engine, nodes, edges, snapshot='snapshot-2')
    analyze(engine, nodes, edges, snapshot='snapshot-3')
    assert len(engine._cache) == 2
    assert not analyze(engine, nodes, edges)['cacheHit']
    with pytest.raises(GraphSnapshotConflict):
        analyze(engine, nodes, edges, expected_snapshot='snapshot-0')


@pytest.fixture
def bench(tmp_path):
    store = Store(tmp_path / 'analytics')
    models = Gateway()
    wiki = WikiService(store, models)
    sources = []
    for index in range(3):
        source = publish(store, url=f'https://example.org/analytics-{index}', title=f'来源{index}')
        sources.append(source)
        classified(store, source, 'artificial_intelligence' if index < 2 else 'cybersecurity')
        wiki.queue([source])
        assert execute(store, wiki)['status'] == 'succeeded'
    return store, models, wiki, WikiKnowledgeService(wiki), sources


def database_image(store):
    with store.connect() as db:
        return '\n'.join(db.iterdump())


def test_unified_first_analytics_read_never_publishes_or_enqueues_and_matches_later_overview(bench):
    store, models, wiki, knowledge, sources = bench
    before = database_image(store)
    result = knowledge.analytics(scope='all')
    assert database_image(store) == before
    assert result['graph']['nodeCount'] == 4 and result['graph']['edgeCount'] == 3
    assert len(models.calls) == 3
    overview = knowledge.overview(limit=4)
    assert overview['snapshotId'] == result['snapshotId']
    assert overview['truncated']
    assert knowledge.analytics(scope='all')['cacheHit']


def test_canonical_readonly_snapshot_works_with_sqlite_query_only_and_predicts_persisted_snapshot(bench):
    store, _, wiki, knowledge, _ = bench
    _, pages, _ = wiki._snapshot()
    before = database_image(store)
    with store.connect() as db:
        db.execute('PRAGMA query_only=ON')
        predicted = knowledge.canonical.snapshot(pages, db=db, readonly=True)
    assert database_image(store) == before
    persisted = knowledge.canonical.snapshot(pages)
    assert predicted == persisted


def test_manual_navigation_with_same_label_preserves_semantic_edge_and_analytics():
    evidence = [{'sourceId': 'source_a', 'revisionId': 'revision_a', 'blockId': 'block_a',
                 'start': 0, 'end': 4, 'quote': '人工关联', 'title': '合成证据'}]
    pages = {key: node(key) for key in ('can_a', 'can_b')}
    state = {'canonical': {'entryToCanonical': {'entry_a': 'can_a', 'entry_b': 'can_b'}},
             'pages': [{'id': 'entry_a', 'sourceId': 'source_a', 'revisionId': 'revision_a',
                        'stale': False, 'relations': [{'targetId': 'entry_b', 'direction': 'outgoing',
                            'edgeId': 'source_edge', 'type': 'association', 'label': '人工关联',
                            'evidence': evidence}]}],
             'links': [{'id': 'manual_link', 'decision_id': 'related_decision',
                        'entry_ids_json': dumps(['entry_a', 'entry_b']), 'label': '人工关联'}]}
    original = deepcopy(state)
    service = SimpleNamespace(canonical=SimpleNamespace(
        decisions=lambda: [{'id': 'related_decision', 'undone': False}]))
    semantic_only = WikiKnowledgeService._edges(service, {**state, 'links': []}, pages)
    combined = WikiKnowledgeService._edges(service, state, pages)
    assert state == original
    assert len(combined) == 2
    semantic = next(item for item in combined if item['layer'] == 'semantic')
    navigation = next(item for item in combined if item['layer'] == 'navigation')
    assert semantic == semantic_only[0]  # Includes the existing EdgeID and all evidence.
    assert semantic['evidence'] == evidence
    assert navigation['origin'] == 'manual_decision' and navigation['evidence'] == []
    assert semantic['id'] != navigation['id']
    result = GraphAnalytics().analyze(mode='unified', snapshot_id='same-label-snapshot',
        nodes=list(pages.values()), edges=combined, scope='all')
    assert result['graph']['edgeCount'] == result['graph']['directedEdgeCount'] == 1
    assert result['graph']['excluded']['nonSemanticEdges'] == 1
    assert result['metrics']['can_a']['degree'] == result['metrics']['can_b']['degree'] == 1


@pytest.mark.parametrize('unified', [False, True])
def test_http_topic_and_all_scope_are_independent_of_canvas_limits_and_navigation_stars(bench, unified):
    store, models, wiki, knowledge, sources = bench
    app = create_app(data_dir=store.root, model_service=models)
    base = '/api/wiki/knowledge' if unified else '/api/wiki'
    with TestClient(app) as client:
        tiny = client.get(base, params={'limit': 4}).json()
        large = client.get(base, params={'limit': 150}).json()
        assert tiny['snapshotId'] == large['snapshotId']
        topics = tiny['topics']
        topic = next(t['id'] for t in topics if t['label'] == '人工智能')
        result = client.get(base + '/analytics', params={'scope': 'topic', 'topic_id': topic,
                            'snapshot_id': tiny['snapshotId']})
        assert result.status_code == 200, result.text
        data = result.json()
        assert data['graph']['complete'] and data['graph']['edgeCount'] == 2
        assert data['graph']['nodeCount'] == (3 if unified else 4)
        assert all(row['topicId'] == topic for ranking in data['rankings'].values() for row in ranking)
        assert all(not key.startswith('doc:') for key in data['metrics'])
        # Source concepts have cross-source navigation, but each has just one
        # real relation and cannot gain degree from a navigation star.
        if not unified:
            assert any(edge.get('layer') == 'navigation' for edge in large['edges'])
            assert max(metric['degree'] for metric in data['metrics'].values()) == 1
        complete = client.get(base + '/analytics', params={'scope': 'all'}).json()
        assert complete['graph']['nodeCount'] == (4 if unified else 6)
        assert complete['graph']['edgeCount'] == 3
        assert any(row['topicId'] != topic for row in complete['rankings']['pagerank'])
        topic_ids = {item['id'] for item in topics}
        for row in complete['rankings']['pagerank']:
            assert row['topicId'] in topic_ids
            # Every global rank target is readable under its supplied topic,
            # including targets absent from the currently selected theme.
            detail = client.get(base + '/pages/' + row['id'], params={'topic_id': row['topicId']})
            assert detail.status_code == 200, detail.text
            neighborhood = client.get(base + '/neighborhood', params={
                'entry_id': row['id'], 'topic_id': row['topicId']}).json()
            assert not neighborhood['reloadRequired']
            assert neighborhood['graphSnapshotId'] == complete['snapshotId']
            assert any(node['id'] == row['id'] for node in neighborhood['nodes'])
        before = database_image(store)
        again = client.get(base + '/analytics', params={'scope': 'all'}).json()
        assert again['cacheHit'] and again['analysisId'] == complete['analysisId']
        assert database_image(store) == before


@pytest.mark.parametrize('unified', [False, True])
def test_neighborhood_graph_snapshot_matches_overview_across_pagination_and_rebuild(bench, unified):
    store, _, wiki, knowledge, sources = bench
    service = knowledge if unified else wiki
    overview = service.overview(limit=4)
    root = next(node['id'] for node in service.overview(limit=150)['nodes'] if node['type'] == 'concept')
    kwargs = {'topic_id': overview['topicId']} if unified else {}
    first = service.neighborhood(root, hops=2, limit=4, **kwargs)
    assert first['graphSnapshotId'] == overview['snapshotId']
    assert first['snapshotId'] != first['graphSnapshotId']  # Paging identity is distinct.
    page = first
    while page['hasMore']:
        page = service.neighborhood(root, hops=2, limit=4, offset=page['nextOffset'],
                                    snapshot_id=page['snapshotId'], **kwargs)
        assert page['graphSnapshotId'] == overview['snapshotId']
    publish(store, blocks=['新原文版本用于检查邻域不可套用此前图谱的重要节点分数。资料发生更新后，必须保留版本边界并重新核对当前语义网络。'],
            url='https://example.org/analytics-0', title='来源0')
    newer = service.overview(limit=4)
    conflicted = service.neighborhood(root, hops=2, limit=4, snapshot_id=first['snapshotId'], **kwargs)
    assert conflicted['reloadRequired']
    assert conflicted['graphSnapshotId'] == newer['snapshotId']
    assert conflicted['graphSnapshotId'] != first['graphSnapshotId']


@pytest.mark.parametrize('unified', [False, True])
def test_http_snapshot_conflict_stale_exclusion_and_scope_validation(bench, unified):
    store, models, wiki, knowledge, sources = bench
    app = create_app(data_dir=store.root, model_service=models)
    base = '/api/wiki/knowledge' if unified else '/api/wiki'
    with TestClient(app) as client:
        previous = client.get(base + '/analytics', params={'scope': 'all'}).json()
        changed = publish(store, blocks=['更新后的原文版本用于检查旧图谱结果不能继续充当当前事实。当前资料需要保留新的原始段落，并让旧版本词条从重要性计算中排除。'],
                          url='https://example.org/analytics-0', title='来源0')
        assert changed == sources[0]
        response = client.get(base + '/analytics', params={'scope': 'all', 'snapshot_id': previous['snapshotId']})
        assert response.status_code == 409
        detail = response.json()['detail']
        assert detail['reason'] == 'snapshot_changed' and detail['reloadRequired']
        assert detail['currentSnapshotId'] != previous['snapshotId']
        current = client.get(base + '/analytics', params={'scope': 'all'}).json()
        assert current['graph']['nodeCount'] == (3 if unified else 4)
        assert current['graph']['edgeCount'] == 2
        assert not current['cacheHit']
        assert client.get(base + '/analytics').status_code == 422
        assert client.get(base + '/analytics', params={'scope': 'all', 'topic_id': 'anything'}).status_code == 422
        assert client.get(base + '/analytics', params={'scope': 'visible'}).status_code == 422
        assert client.get(base + '/analytics', params={'topic_id': 'missing'}).status_code == 404
    assert len(models.calls) == 3


@pytest.mark.parametrize('base', ['/api/wiki', '/api/wiki/knowledge'])
def test_empty_http_graph_is_explicitly_complete(tmp_path, base):
    app = create_app(data_dir=tmp_path / 'empty', model_service=Gateway())
    with TestClient(app) as client:
        response = client.get(base + '/analytics', params={'scope': 'all'})
        assert response.status_code == 200, response.text
        assert response.json()['graph']['nodeCount'] == 0
        assert response.json()['metrics'] == {}
