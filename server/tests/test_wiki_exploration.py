"""Evidence-aware exploration contracts using isolated, deterministic fixture stores.

No provider or network is used. All sources below are synthetic and every quote
is recovered from the exact revision registered by the normal Wiki compiler.
"""
import copy
import json

import pytest

from server.store import Store, dumps
from server.wiki import WikiService
from server.tests.test_wiki import Gateway, execute, publish, clear_result


FIXTURE_BACKGROUND = '此来源仅用于隔离环境的图谱验收，所有内容均为人工编写的固定样例，保留独立原文段落与版本用于回查。'


class PlannedGateway(Gateway):
    """Return a fixed graph while using each statement's own registered block."""

    def __init__(self):
        super().__init__()
        self.plan = None

    def complete(self, config, messages):
        self.calls.append((copy.deepcopy(config), copy.deepcopy(messages)))
        payload = json.loads(messages[1]['content'])
        entries, relations = self.plan

        def evidence(text):
            block = next(block for block in payload['blocks'] if text in block['text'])
            return [{'blockId': block['blockId'], 'quote': text}]

        result = {'entries': [], 'relations': [], 'issues': []}
        for entry in entries:
            reference = evidence(entry['summary'])
            result['entries'].append({
                'localKey': entry['key'], 'label': entry['label'], 'type': entry['type'],
                'subtype': '', 'aliases': entry.get('aliases', []),
                'summary': {'text': entry['summary'], 'evidence': reference},
                'sections': [{'title': '来源记载', 'claims': [
                    {'text': entry['summary'], 'evidence': reference}]}],
            })
        for relation in relations:
            result['relations'].append({
                'from': relation['from'], 'to': relation['to'], 'type': relation['type'],
                'label': relation['label'], 'evidence': evidence(relation['quote']),
            })
        return {'text': json.dumps(clear_result(result), ensure_ascii=False), 'usage': {'total_tokens': 1}}


def entry(key, label, kind='concept', summary=None):
    return {'key': key, 'label': label, 'type': kind,
            'summary': summary or f'{label} 是本资料记载的独立词条。'}


def relation(source, target, label='明确关联', kind='association', quote=None):
    return {'from': source, 'to': target, 'label': label, 'type': kind,
            'quote': quote or f'词条 {source} 与词条 {target} 的关系是{label}。'}


class ExplorationBench:
    def __init__(self, root):
        self.store = Store(root)
        self.models = PlannedGateway()
        self.service = WikiService(self.store, self.models)
        self.serial = 0

    def build(self, entries, relations=(), *, url=None, title=None):
        self.serial += 1
        url = url or f'https://example.org/exploration/{self.serial}'
        # Store rejects bodies below thirty characters. Keep that requirement
        # independent of graph labels, quotes and the query-bearing blocks.
        text = [item['summary'] for item in entries] + [item['quote'] for item in relations] + [FIXTURE_BACKGROUND]
        source = publish(self.store, text, url=url, title=title or f'探索测试来源 {self.serial}')
        self.models.plan = (copy.deepcopy(entries), copy.deepcopy(relations))
        self.service.queue([source])
        job = execute(self.store, self.service)
        assert job['status'] == 'succeeded', job
        with self.store.connect() as db:
            records = db.execute('''SELECT p.entry_id FROM wiki_pages p
                JOIN wiki_publications u ON u.run_id=p.run_id
                WHERE p.source_id=?''', (source,)).fetchall()
        pages = [self.service.page(row['entry_id']) for row in records]
        return source, {page['label']: page for page in pages}


@pytest.fixture
def exploration(tmp_path):
    return ExplorationBench(tmp_path / 'exploration-data')


def assert_exact_evidence(store, references):
    assert references
    for reference in references:
        document = store.detail(reference['sourceId'], reference['revisionId'])
        block = next(block for block in document['blocks'] if block['id'] == reference['blockId'])
        assert block['text'][reference['start']:reference['end']] == reference['quote']


def knowledge_nodes(graph):
    return [node for node in graph['nodes'] if node['type'] != 'document']


def assert_closed_graph(graph):
    ids = {node['id'] for node in graph['nodes']}
    assert len(ids) == len(graph['nodes'])
    assert all(edge['from'] in ids and edge['to'] in ids for edge in graph['edges'])
    assert len({edge['id'] for edge in graph['edges']}) == len(graph['edges'])


def without_freshness(value):
    """Freshness is a read annotation; the cited historical content is fixed."""
    if isinstance(value, dict):
        return {key: without_freshness(item) for key, item in value.items() if key != 'stale'}
    if isinstance(value, list):
        return [without_freshness(item) for item in value]
    return value


def test_relation_layers_are_consistent_between_graph_and_current_page(exploration):
    bench = exploration
    entries = [entry('shared', 'SharedTerm'), entry('person', '同名人物', 'entity')]
    links = [relation('shared', 'person')]
    _, pages = bench.build(entries, links)
    bench.build(entries, links)
    page = bench.service.page(pages['SharedTerm']['id'])
    graph = bench.service.overview()
    expected = {
        'semantic': ('model_extraction', 'cited_original'),
        'provenance': ('program_source', 'source_reference'),
        'navigation': ('program_equivalence', 'wording_equivalence'),
    }
    assert {edge['layer'] for edge in graph['edges']} == set(expected)
    by_edge = {edge['id']: edge for edge in graph['edges']}
    for edge in graph['edges']:
        assert (edge['origin'], edge['supportKind']) == expected[edge['layer']]
        assert edge['stale'] is False
        assert_exact_evidence(bench.store, edge['evidence'])
        if edge['layer'] == 'navigation':
            assert edge['derivedBy'] == 'exact_name_and_definition'
            assert len({ref['sourceId'] for ref in edge['evidence']}) == 2
        else:
            assert edge['derivedBy'] is None
    for link in page['relations']:
        if link['edgeId'] in by_edge:
            for field in ('layer', 'origin', 'supportKind', 'derivedBy', 'stale'):
                assert link[field] == by_edge[link['edgeId']][field]


def test_two_hop_search_preserves_each_edge_direction_and_version_evidence(exploration):
    bench = exploration
    source, pages = bench.build(
        [entry('seed', 'NeedleAlpha'), entry('middle', 'MiddleBeta'), entry('leaf', 'LeafGamma')],
        [relation('middle', 'seed', '上位包含', 'hierarchy'),
         relation('middle', 'leaf', '引用关联')],
    )
    result = bench.service.search('NeedleAlpha', limit=1, hops=2)
    seed, middle, leaf = (pages[name]['id'] for name in ('NeedleAlpha', 'MiddleBeta', 'LeafGamma'))
    assert [item['id'] for item in result['results']] == [seed]
    expanded = {item['id']: item for item in result['expanded']}
    assert set(expanded) == {middle, leaf}
    target = expanded[leaf]
    assert target['seedId'] == seed
    assert target['hopCount'] == 2
    assert target['pathNodeIds'] == [seed, middle, leaf]
    first, second = target['pathEdges']
    assert (first['from'], first['to'], first['traversalDirection']) == (middle, seed, 'reverse')
    assert (second['from'], second['to'], second['traversalDirection']) == (middle, leaf, 'forward')
    assert first['type'] == 'hierarchy'
    assert first['evidence'][0]['blockId'] != second['evidence'][0]['blockId']
    for step in target['pathEdges']:
        assert step['layer'] == 'semantic' and step['supportKind'] == 'cited_original'
        assert {ref['sourceId'] for ref in step['evidence']} == {source}
        assert {ref['revisionId'] for ref in step['evidence']} == {pages['NeedleAlpha']['revisionId']}
        assert_exact_evidence(bench.store, step['evidence'])
    assert bench.service.search('NeedleAlpha', limit=1, hops=0)['expanded'] == []


def test_navigation_path_is_wording_equivalence_not_semantic_support(exploration):
    bench = exploration
    _, first = bench.build([entry('shared', 'NavigationNeedle')])
    _, second = bench.build([entry('shared', 'NavigationNeedle')])
    result = bench.service.search('NavigationNeedle', limit=1, hops=1)
    assert len(result['expanded']) == 1
    target = result['expanded'][0]
    step = target['pathEdges'][0]
    assert target['hopCount'] == 1
    assert {target['id'], target['seedId']} == {first['NavigationNeedle']['id'], second['NavigationNeedle']['id']}
    assert step['layer'] == 'navigation'
    assert step['supportKind'] == 'wording_equivalence'
    assert step['derivedBy'] == 'exact_name_and_definition'
    assert '有据关系' not in target['reason']
    assert_exact_evidence(bench.store, step['evidence'])


def test_search_order_is_stable_when_persisted_adjacency_order_changes(exploration):
    bench = exploration
    _, pages = bench.build(
        [entry('seed', 'StableNeedle')] + [entry(key, label) for key, label in
            [('left', 'LeftTerm'), ('right', 'RightTerm'), ('tail', 'TailTerm')]],
        [relation('seed', 'left'), relation('seed', 'right'), relation('left', 'tail'),
         relation('right', 'tail'), relation('tail', 'seed')],
    )
    before = bench.service.search('StableNeedle', limit=1, hops=2)
    with bench.store.connect() as db:
        for row in db.execute('SELECT entry_id,version,content_json FROM wiki_pages').fetchall():
            body = json.loads(row['content_json'])
            body['relations'].reverse()
            db.execute('UPDATE wiki_pages SET content_json=? WHERE entry_id=? AND version=?',
                       (dumps(body), row['entry_id'], row['version']))
    after = bench.service.search('StableNeedle', limit=1, hops=2)
    assert [(item['id'], item['pathNodeIds']) for item in before['expanded']] == [
        (item['id'], item['pathNodeIds']) for item in after['expanded']]
    assert len({item['id'] for item in after['expanded']}) == len(after['expanded'])
    assert pages['StableNeedle']['id'] not in {item['id'] for item in after['expanded']}
    assert after['truncation']['graph'] is True


@pytest.mark.parametrize('channel', ['lexical', 'graph', 'raw'])
def test_search_reports_the_actual_truncation_channel(exploration, channel):
    bench = exploration
    query = 'BudgetNeedle'
    if channel == 'lexical':
        bench.build([entry('first', query + 'First'), entry('second', query + 'Second')])
    elif channel == 'graph':
        bench.build([entry('seed', query)] + [entry(str(i), f'Branch{i}') for i in range(3)],
                    [relation('seed', str(i)) for i in range(3)])
    else:
        for index in range(3):
            publish(bench.store, [query + ' 是未构建 Wiki 的原文标记。', FIXTURE_BACKGROUND],
                    url=f'https://example.org/raw-budget/{index}', title=f'Raw {index}')
    result = bench.service.search(query, limit=1, hops=1)
    assert result['truncation'] == {name: name == channel for name in ('lexical', 'graph', 'raw')}
    assert result['truncated'] is True
    empty = bench.service.search('AbsentNeedleThatHasNoMatch', limit=1, hops=0)
    assert empty['truncation'] == {'lexical': False, 'graph': False, 'raw': False}
    assert empty['truncated'] is False


def test_five_source_equivalence_navigation_is_complete_without_merging_identity(exploration):
    bench = exploration
    concepts, entities = [], []
    for index in range(5):
        _, pages = bench.build([entry('shared', 'EquivalentConcept'), entry('person', 'SamePerson', 'entity')],
                               url=f'https://example.org/equivalent/{index}')
        concepts.append(pages['EquivalentConcept'])
        entities.append(pages['SamePerson'])
    concept_ids = {page['id'] for page in concepts}
    assert len(concept_ids) == 5
    assert len({page['id'] for page in entities}) == 5
    overview = bench.service.overview()
    navigation_edges = [edge for edge in overview['edges'] if edge['layer'] == 'navigation']
    assert len(navigation_edges) == 4, 'Five equivalent concepts need a sparse navigation star, not ten pairwise lines'
    assert {endpoint for edge in navigation_edges for endpoint in (edge['from'], edge['to'])} == concept_ids
    for page in concepts:
        current = bench.service.page(page['id'])
        assert len(current['navigationGroups']) == 1
        group = current['navigationGroups'][0]
        assert group['memberCount'] == 5
        assert {member['id'] for member in group['members']} == concept_ids
        nearby = bench.service.neighborhood(page['id'], hops=1, limit=20)
        assert {node['id'] for node in knowledge_nodes(nearby)} == concept_ids
        assert_closed_graph(nearby)
    for page in entities:
        current = bench.service.page(page['id'])
        assert current.get('navigationGroups', []) == []
        assert not any(link['layer'] == 'navigation' for link in current['relations'])


def test_group_pagination_is_complete_stable_and_does_not_include_different_definitions(exploration):
    bench = exploration
    ids = set()
    first = None
    for _ in range(5):
        _, pages = bench.build([entry('shared', 'PagedConcept')])
        first = first or pages['PagedConcept']
        ids.add(pages['PagedConcept']['id'])
    _, different = bench.build([entry('shared', 'PagedConcept', summary='同名概念在本资料具有另一种定义。')])
    assert bench.service.page(different['PagedConcept']['id']).get('navigationGroups', []) == []
    group_id = bench.service.page(first['id'])['navigationGroups'][0]['id']
    offset, found = 0, []
    while True:
        part = bench.service.group(group_id, offset=offset, limit=2)
        assert part['memberCount'] == 5
        assert len(part['members']) <= 2
        assert all(member['sourceId'] and member['sourceTitle'] and member['revisionId'] and member['topicId']
                   for member in part['members'])
        found.extend(member['id'] for member in part['members'])
        if not part['hasMore']:
            break
        assert part['nextOffset'] > offset
        offset = part['nextOffset']
        assert offset < 10, 'Group pagination must make bounded progress'
    assert len(found) == len(set(found)) == 5
    assert set(found) == ids
    assert [member['id'] for member in bench.service.group(group_id, limit=20)['members']] == found


def test_stale_source_leaves_equivalence_group_without_rewriting_history(exploration):
    bench = exploration
    first_source, first = bench.build([entry('shared', 'VersionedConcept')],
                                      url='https://example.org/versioned-first')
    _, second = bench.build([entry('shared', 'VersionedConcept')])
    _, third = bench.build([entry('shared', 'VersionedConcept')])
    first = first['VersionedConcept']
    with bench.store.connect() as db:
        before = [tuple(row) for row in db.execute('SELECT entry_id,version,content_json FROM wiki_pages ORDER BY entry_id,version')]
    historical = bench.service.page(first['id'], version=first['version'])
    assert historical.get('navigationGroups', []) == []
    assert not any(link.get('layer') == 'navigation' for link in historical['relations'])
    updated_source = publish(bench.store, ['更新资料改变了此词条的适用范围，旧定义需要重新核对。', FIXTURE_BACKGROUND],
                             url='https://example.org/versioned-first', title='更新的测试来源')
    assert updated_source == first_source
    stale = bench.service.page(first['id'])
    assert stale['stale'] is True
    assert stale.get('navigationGroups', []) == []
    current = bench.service.page(second['VersionedConcept']['id'])
    remaining = current['navigationGroups'][0]
    assert remaining['memberCount'] == 2
    assert {member['id'] for member in remaining['members']} == {
        second['VersionedConcept']['id'], third['VersionedConcept']['id']}
    assert_exact_evidence(bench.store, stale['evidence'])
    again = bench.service.page(first['id'], version=first['version'])
    assert without_freshness(again['sections']) == without_freshness(historical['sections'])
    assert again['revisionId'] == historical['revisionId']
    with bench.store.connect() as db:
        after = [tuple(row) for row in db.execute('SELECT entry_id,version,content_json FROM wiki_pages ORDER BY entry_id,version')]
    assert after == before, 'Read-time groups and freshness annotations must not alter published history'


def test_neighborhood_focus_outside_overview_is_visible_and_hops_are_bounded(exploration):
    bench = exploration
    _, pages = bench.build(
        [entry('root', 'OutsideFocus'), entry('near', 'NearPage'), entry('far', 'FarPage')],
        [relation('root', 'near'), relation('near', 'far')],
    )
    other_source, _ = bench.build([entry('unrelated', 'AAAUnrelatedTopic')])
    root, near, far = (pages[name]['id'] for name in ('OutsideFocus', 'NearPage', 'FarPage'))
    overview = bench.service.overview()
    assert overview['topicId'] == 'source:' + other_source
    assert root not in {node['id'] for node in overview['nodes']}
    one = bench.service.neighborhood(root, hops=1, limit=10)
    two = bench.service.neighborhood(root, hops=2, limit=10)
    assert {node['id'] for node in knowledge_nodes(one)} == {root, near}
    assert {node['id'] for node in knowledge_nodes(two)} == {root, near, far}
    assert one['totalCount'] == 2 and two['totalCount'] == 3
    assert_closed_graph(one)
    assert_closed_graph(two)


def test_neighborhood_pages_merge_all_connections_without_dangling_edges(exploration):
    bench = exploration
    _, pages = bench.build(
        [entry(key, label) for key, label in [('root', 'RootPage'), ('b', 'BranchB'),
         ('c', 'BranchC'), ('d', 'TailD'), ('e', 'TailE')]],
        [relation('root', 'b'), relation('root', 'c'), relation('b', 'd'),
         relation('c', 'e'), relation('d', 'e')],
    )
    root = pages['RootPage']['id']
    expected_ids = {page['id'] for page in pages.values()}
    expected_semantic_edges = {link['edgeId'] for page in pages.values() for link in page['relations']}
    offset, snapshot, all_nodes, all_edges, fresh = 0, None, {}, {}, []
    page_count = 0
    while True:
        part = bench.service.neighborhood(root, hops=2, limit=4, offset=offset, snapshot_id=snapshot)
        page_count += 1
        assert not part.get('reloadRequired', False)
        assert len(part['nodes']) <= 4
        assert root in {node['id'] for node in part['nodes']}
        assert_closed_graph(part)
        assert part['counts']['nodes'] == len(part['nodes'])
        assert part['counts']['documents'] == len([node for node in part['nodes'] if node['type'] == 'document'])
        assert part['totalCount'] == 5
        snapshot = snapshot or part['snapshotId']
        assert part['snapshotId'] == snapshot
        fresh.extend(node['id'] for node in knowledge_nodes(part) if node['id'] != root)
        all_nodes.update((node['id'], node) for node in part['nodes'])
        for edge in part['edges'] + part.get('mergeEdges', []):
            assert edge['from'] in all_nodes and edge['to'] in all_nodes
            all_edges[edge['id']] = edge
        assert set(part.get('requiredNodeIds', [])) <= set(all_nodes)
        if not part['hasMore']:
            break
        assert part['truncation']['nodeBudget'] is True
        assert part['nextOffset'] > offset
        offset = part['nextOffset']
        assert page_count <= 5, 'Every page must consume at least one previously unseen candidate'
    assert page_count >= 2
    assert set(fresh) == expected_ids - {root}
    assert len(fresh) == len(set(fresh))
    assert {edge_id for edge_id, edge in all_edges.items() if edge['layer'] == 'semantic'} == expected_semantic_edges
    assert {node_id for node_id, node in all_nodes.items() if node['type'] != 'document'} == expected_ids


def test_neighborhood_snapshot_change_requires_reload_instead_of_mixed_pages(exploration):
    bench = exploration
    _, pages = bench.build([entry('root', 'SnapshotRoot')] + [entry(str(i), f'SnapshotBranch{i}') for i in range(4)],
                           [relation('root', str(i)) for i in range(4)], url='https://example.org/snapshot')
    root = pages['SnapshotRoot']['id']
    first = bench.service.neighborhood(root, hops=1, limit=4)
    assert first['hasMore'] is True
    publish(bench.store, ['来源更新后旧图快照已经过期，继续翻页应要求重新载入。', FIXTURE_BACKGROUND],
            url='https://example.org/snapshot')
    next_page = bench.service.neighborhood(root, hops=1, limit=4, offset=first['nextOffset'],
                                           snapshot_id=first['snapshotId'])
    assert next_page['reloadRequired'] is True
    assert next_page['reason'] == 'snapshot_changed'
    assert next_page['nodes'] == [] and next_page['edges'] == []
    assert next_page['hasMore'] is False


def test_neighborhood_fills_later_affordable_nodes_and_defers_skipped_sources(exploration):
    bench = exploration
    _, first = bench.build([entry('shared', 'FillableGroup')])
    expected_ids = {first['FillableGroup']['id']}
    for suffix in ('One', 'Two'):
        _, pages = bench.build([entry('shared', 'FillableGroup'), entry('tail', 'LocalTail' + suffix)],
                               [relation('shared', 'tail')])
        expected_ids.update(page['id'] for page in pages.values())
    root = first['FillableGroup']['id']
    part = bench.service.neighborhood(root, hops=2, limit=5)
    # Root + its document + a second-source concept + its document cost four.
    # A third source would cost two more, but the already admitted source's
    # two-hop tail costs only one: it must fill the remaining slot.
    assert len(part['nodes']) == 5
    assert len(knowledge_nodes(part)) == 3
    assert len({node['sourceId'] for node in knowledge_nodes(part)}) == 2
    assert any(node['label'].startswith('LocalTail') for node in knowledge_nodes(part))
    assert part['hasMore'] is True
    next_part = bench.service.neighborhood(root, hops=2, limit=5, offset=part['nextOffset'],
                                          snapshot_id=part['snapshotId'])
    assert_closed_graph(part)
    assert_closed_graph(next_part)
    assert next_part['hasMore'] is False
    first_ids = {node['id'] for node in knowledge_nodes(part)}
    second_ids = {node['id'] for node in knowledge_nodes(next_part)}
    assert first_ids & second_ids == {root}
    assert first_ids | second_ids == expected_ids


@pytest.mark.parametrize('kwargs', [{'limit': 3}, {'hops': 3}, {'offset': -1}])
def test_neighborhood_rejects_nonprogressing_budget_and_invalid_bounds(exploration, kwargs):
    _, pages = exploration.build([entry('root', 'BoundaryRoot')])
    with pytest.raises(ValueError):
        exploration.service.neighborhood(pages['BoundaryRoot']['id'], **kwargs)
