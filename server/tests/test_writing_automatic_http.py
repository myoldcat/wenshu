"""Initial settings -> one durable API request -> saved article and final report."""
from server.tests.test_writing_api import LOCAL, bench, create
from server.worker import run_one


def test_one_request_finishes_and_reopens_with_a_separate_report(bench):
    client, app, provider, _, _ = bench
    project = create(bench, brief={'topic': '资料治理', 'automation': 'automatic', 'targetWords': 10000})
    path = '/api/writing/projects/' + project['id']
    queued = client.post(path + '/automatic', headers=LOCAL, json={'expectedVersion': project['version']})
    assert queued.status_code == 202, queued.text
    assert queued.json()['project']['automatic']['status'] == 'queued'
    assert run_one(app.state.store, app.state.enrichment, app.state.wiki, app.state.writing)
    current = client.get(path).json()['project']
    assert current['automatic']['status'] in ('completed', 'completed_with_issues')
    assert current['automatic']['report']['projectVersion'] == current['version']
    assert current['automatic']['report']['outputAvailable']
    assert all(section['content'] for section in current['sections'])
    assert all(not section['manual'] for section in current['sections'])
    assert current['outlineApproval']['method'] == 'automatic'
    assert not any(run['status'] in ('queued', 'running') for run in current['runs'])
    calls = len(provider.calls)
    reopened = client.get(path).json()['project']
    assert reopened['automatic']['report'] == current['automatic']['report']
    assert len(provider.calls) == calls
    card = next(item for item in client.get('/api/writing/projects').json()['projects'] if item['id'] == project['id'])
    assert card['automatic']['status'] == current['automatic']['status']


def test_automatic_api_fences_initial_version_and_rejects_extra_confirmations(bench):
    client = bench[0]
    project = create(bench)
    path = '/api/writing/projects/' + project['id']
    changed = client.patch(path, headers=LOCAL, json={'expectedVersion': project['version'], 'title': '新的要求'})
    assert changed.status_code == 200
    stale = client.post(path + '/automatic', headers=LOCAL, json={'expectedVersion': project['version']})
    assert stale.status_code == 409
    invalid = client.post(path + '/automatic', headers=LOCAL,
                          json={'expectedVersion': changed.json()['project']['version'], 'confirmOutline': True})
    assert invalid.status_code == 422
    assert not bench[2].calls
