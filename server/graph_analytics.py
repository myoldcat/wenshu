"""Read-only, snapshot-bound analytics over complete semantic Wiki projections.

Canvas budgets, navigation stars and document provenance never enter this graph.
Relations have no calibrated strength, so each endpoint pair has unit weight.
PageRank follows semantic arrows; connectivity/bridge/core measures ignore them.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
from threading import RLock

import networkx as nx

from .store import dumps, now


ANALYTICS_VERSION = 'wenshu-semantic-analytics-1'
EXACT_BETWEENNESS_LIMIT = 500
BETWEENNESS_SAMPLES = 128
BETWEENNESS_SEED = 0


def digest(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def source_snapshot(sources, pages):
    """Token shared by source overview and analytics, independent of its limit."""
    return 'source_graph_' + digest([
        'wiki-source-graph-1',
        sorted((key, source.get('current_revision'), source.get('tags_json', '[]'),
                source.get('classification_paths_json', '[]')) for key, source in sources.items()),
        sorted((page['id'], page.get('version'), page.get('revisionId'), bool(page.get('stale')))
               for page in pages),
    ])


class GraphSnapshotConflict(ValueError):
    def __init__(self, current_snapshot):
        super().__init__('图谱已更新，请刷新后重新计算重要节点')
        self.current_snapshot = current_snapshot


class GraphAnalyticsUnavailable(RuntimeError):
    pass


def validate_scope(scope, topic_id):
    if scope not in ('topic', 'all'):
        raise ValueError('图计算范围须为当前主题或全部图谱')
    if scope == 'topic' and (not isinstance(topic_id, str) or not topic_id.strip()):
        raise ValueError('当前主题图计算须提供 topic_id')
    if scope == 'all' and topic_id is not None:
        raise ValueError('全部图谱计算不能同时提供 topic_id')


class GraphAnalytics:
    """Small process-local LRU. Its lock also prevents duplicate calculations."""
    def __init__(self, capacity=8):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError('图计算缓存容量须为正整数')
        self.capacity = capacity
        self._cache = OrderedDict()
        self._lock = RLock()

    def analyze(self, *, mode, snapshot_id, nodes, edges, scope='topic', topic_id=None,
                expected_snapshot=None):
        validate_scope(scope, topic_id)
        # Reject before looking up the cache: an old cached graph is never a
        # substitute for the current source/canonical snapshot.
        if expected_snapshot is not None and expected_snapshot != snapshot_id:
            raise GraphSnapshotConflict(snapshot_id)
        key = (ANALYTICS_VERSION, mode, snapshot_id, scope, topic_id)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return {**deepcopy(self._cache[key]), 'cacheHit': True}
            result = self._compute(mode, snapshot_id, nodes, edges, scope, topic_id)
            self._cache[key] = deepcopy(result)
            while len(self._cache) > self.capacity:
                self._cache.popitem(last=False)
            return {**result, 'cacheHit': False}

    @staticmethod
    def _compute(mode, snapshot_id, nodes, edges, scope, topic_id):
        # Sorting fixes insertion order, pivot selection and tie ordering.
        by_id = {node['id']: node for node in nodes if node.get('type') != 'document'
                 and not node.get('stale') and not node.get('archived')}
        ids = sorted(by_id)
        directed = nx.DiGraph()
        directed.add_nodes_from(ids)
        excluded = {'staleNodes': sum(bool(n.get('stale') or n.get('archived')) for n in nodes),
                    'nonSemanticEdges': 0, 'staleEdges': 0, 'selfLoops': 0, 'unavailableEndpoints': 0}
        pairs = set()
        for edge in edges:
            if edge.get('layer') != 'semantic':
                excluded['nonSemanticEdges'] += 1
                continue
            if edge.get('stale') or any(ref.get('stale') for ref in edge.get('evidence', [])):
                excluded['staleEdges'] += 1
                continue
            left, right = edge.get('from'), edge.get('to')
            if left not in by_id or right not in by_id:
                excluded['unavailableEndpoints'] += 1
                continue
            if left == right:
                excluded['selfLoops'] += 1
                continue
            pairs.add((left, right))
        directed.add_edges_from(sorted(pairs))
        undirected = nx.Graph(directed)
        count = len(ids)
        approximate = count > EXACT_BETWEENNESS_LIMIT
        samples = min(BETWEENNESS_SAMPLES, count) if approximate else None
        degree = dict(undirected.degree())
        degree_centrality = nx.degree_centrality(undirected) if count > 1 else dict.fromkeys(ids, 0.0)
        try:
            pagerank = nx.pagerank(directed, alpha=0.85, tol=1e-10, max_iter=1000, weight=None)
        except nx.PowerIterationFailedConvergence as exc:
            raise GraphAnalyticsUnavailable('PageRank 尚未收敛，请稍后重试') from exc
        betweenness = nx.betweenness_centrality(undirected, k=samples, normalized=True,
                                               weight=None, seed=BETWEENNESS_SEED)
        cores = nx.core_number(undirected)
        metrics = {key: {'degree': degree[key], 'degreeCentrality': degree_centrality[key],
                         'inDegree': directed.in_degree(key), 'outDegree': directed.out_degree(key),
                         'pagerank': pagerank[key], 'betweenness': betweenness[key],
                         'coreNumber': cores[key]} for key in ids}
        rankings = {}
        for metric in ('degree', 'pagerank', 'betweenness', 'coreNumber'):
            ordered = sorted(ids, key=lambda key: (-metrics[key][metric], by_id[key].get('label', ''), key))
            rankings[metric] = [{'id': key, 'label': by_id[key].get('label', key),
                                 'type': by_id[key].get('type', ''), 'rank': rank,
                                 'value': metrics[key][metric], 'topicId': by_id[key].get('topicId')}
                                for rank, key in enumerate(ordered, 1)]
        return {'mode': mode, 'snapshotId': snapshot_id,
                'analysisId': 'analysis_' + digest([ANALYTICS_VERSION, mode, snapshot_id, scope, topic_id]),
                'scope': {'kind': scope, 'topicId': topic_id}, 'computedAt': now(),
                'graph': {'nodeCount': count, 'edgeCount': undirected.number_of_edges(),
                          'directedEdgeCount': directed.number_of_edges(), 'complete': True,
                          'excluded': excluded}, 'metrics': metrics, 'rankings': rankings,
                'algorithms': {'engine': 'networkx', 'engineVersion': nx.__version__,
                    'version': ANALYTICS_VERSION, 'weightPolicy': 'unit_per_endpoint_pair',
                    'excludedLayers': ['navigation', 'provenance'], 'stalePolicy': 'exclude',
                    'degree': {'direction': 'undirected', 'normalizedField': 'degreeCentrality'},
                    'pagerank': {'direction': 'semantic_arrows', 'alpha': 0.85, 'tol': 1e-10,
                                 'maxIterations': 1000, 'danglingPolicy': 'uniform'},
                    'betweenness': {'direction': 'undirected', 'normalized': True,
                        'approximate': approximate, 'sampleSize': samples,
                        'seed': BETWEENNESS_SEED if approximate else None,
                        'exactNodeLimit': EXACT_BETWEENNESS_LIMIT},
                    'coreNumber': {'direction': 'undirected'}}}
