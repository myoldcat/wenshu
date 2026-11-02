"""Read-only exploration HTTP contracts against an isolated synthetic store."""
from fastapi.testclient import TestClient

from server.app import create_app
from server.tests.test_wiki_exploration import exploration, entry, relation


def test_navigation_group_http_pagination_returns_all_members(exploration):
    bench = exploration
    expected = set()
    first = None
    for _ in range(5):
        _, pages = bench.build([entry('shared', 'HttpPagedConcept')])
        first = first or pages['HttpPagedConcept']
        expected.add(pages['HttpPagedConcept']['id'])
    call_count = len(bench.models.calls)
    app = create_app(data_dir=bench.store.root, model_service=bench.models)
    with TestClient(app) as client:
        page = client.get('/api/wiki/pages/' + first['id'])
        assert page.status_code == 200, page.text
        group_id = page.json()['navigationGroups'][0]['id']
        offset, found = 0, []
        while True:
            response = client.get('/api/wiki/groups/' + group_id, params={'offset': offset, 'limit': 2})
            assert response.status_code == 200, response.text
            part = response.json()
            assert part['memberCount'] == 5
            found.extend(member['id'] for member in part['members'])
            if not part['hasMore']:
                break
            assert part['nextOffset'] > offset
            offset = part['nextOffset']
            assert offset < 10
        assert set(found) == expected
        assert len(found) == len(set(found)) == 5
        assert client.get('/api/wiki/groups/missing').status_code == 404
        for params in ({'limit': 0}, {'limit': 999}, {'offset': -1}):
            assert client.get('/api/wiki/groups/' + group_id, params=params).status_code == 422
    assert len(bench.models.calls) == call_count, 'Exploration must never trigger model work'


def test_neighborhood_http_bounds_and_continuation_contract(exploration):
    bench = exploration
    _, pages = bench.build([entry('root', 'HttpRoot')] + [entry(str(i), f'HttpLeaf{i}') for i in range(4)],
                           [relation('root', str(i)) for i in range(4)])
    root = pages['HttpRoot']['id']
    call_count = len(bench.models.calls)
    app = create_app(data_dir=bench.store.root, model_service=bench.models)
    with TestClient(app) as client:
        first_response = client.get('/api/wiki/neighborhood', params={'entry_id': root, 'hops': 1, 'limit': 4})
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        assert first['hasMore'] is True and len(first['nodes']) <= 4
        second_response = client.get('/api/wiki/neighborhood', params={
            'entry_id': root, 'hops': 1, 'limit': 4,
            'offset': first['nextOffset'], 'snapshot_id': first['snapshotId'],
        })
        assert second_response.status_code == 200, second_response.text
        second = second_response.json()
        assert second['snapshotId'] == first['snapshotId']
        assert not second.get('reloadRequired', False)
        assert second['hasMore'] is False
        first_ids = {node['id'] for node in first['nodes'] if node['type'] != 'document'}
        second_ids = {node['id'] for node in second['nodes'] if node['type'] != 'document'}
        assert first_ids & second_ids == {root}
        assert first_ids | second_ids == {page['id'] for page in pages.values()}
        for invalid in ({'limit': 3}, {'limit': 151}, {'hops': 3}, {'offset': -1}):
            response = client.get('/api/wiki/neighborhood', params={'entry_id': root, **invalid})
            assert response.status_code == 422, response.text
        assert client.get('/api/wiki/neighborhood', params={'entry_id': 'missing'}).status_code == 404
    assert len(bench.models.calls) == call_count
