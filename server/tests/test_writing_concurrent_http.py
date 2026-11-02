"""Real writing APIs and orchestration, isolated data and a gated HTTP provider."""
import json
from threading import Event, Lock
import time

import httpx

from server.tests.test_writing_api import LOCAL, bench, create
from server.worker import ConcurrentWorker


def until(worker, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        worker.tick()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Isolated writing work did not reach its expected state')


def test_three_articles_run_while_fourth_can_be_created_and_queues_then_finishes(bench):
    client, app, _, _, model_id = bench
    original_transport = app.state.models.transport
    releases = [Event() for _ in range(4)]
    entered, active, peak = [], 0, 0
    lock = Lock()

    def provider(request):
        nonlocal active, peak
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        with lock:
            active += 1
            peak = max(peak, active)
            if payload['task'] == 'writing_outline':
                index = len(entered)
                entered.append(payload['brief']['topic'])
            else:
                index = None
        try:
            if index is not None:
                assert releases[index].wait(30), 'Controlled provider was never released'
            # The existing deterministic provider stores its most recent prose
            # for citation checking; protect that fixture state as well.
            with lock:
                return original_transport.handle_request(request)
        finally:
            with lock:
                active -= 1

    app.state.models.transport = httpx.MockTransport(provider)
    projects, queued = [], []

    def add(index):
        project = create(bench, title=f'并发隔离文稿 {index}', brief={
            'topic': f'资料治理 {index}', 'automation': 'automatic', 'targetWords': 10000,
            'automaticOptions': {'useSelectedSources': True, 'useRecommendedSources': False,
                                 'mode': 'strict', 'includeImages': False},
        })
        response = client.post('/api/writing/projects/' + project['id'] + '/automatic',
                               headers=LOCAL, json={'expectedVersion': project['version']})
        assert response.status_code == 202, response.text
        projects.append(project)
        queued.append(response.json())
        return project

    worker = ConcurrentWorker(app.state.store, app.state.enrichment)
    def contract(raw):
        frozen = json.loads(raw)
        # Material screening legitimately updates the parent's working scope.
        # Model selection, prompts and rules must retain the queued contract.
        return {key: frozen[key] for key in ('protocol', 'modelVersion', 'compositionPrompts',
                'compositionSnapshot', 'supportPolicies', 'imageQualityPolicy',
                'automaticModelPolicy', 'automaticOptions', 'automaticOptionsPolicy')}

    try:
        for index in range(3):
            add(index)
        until(worker, lambda: len(entered) == 3)
        assert set(entered) == {p['brief']['topic'] for p in projects}
        assert all(app.state.store.job(value['job']['id'])['status'] == 'running' for value in queued)

        # Creation and queue admission still succeed while every slot is busy.
        fourth = add(3)
        for _ in range(3):
            worker.tick()
        assert app.state.store.job(queued[3]['job']['id'])['status'] == 'queued'
        listed = client.get('/api/writing/projects').json()['projects']
        assert len(listed) == 4
        assert next(p for p in listed if p['id'] == fourth['id'])['automatic']['status'] == 'queued'
        with app.state.store.connect() as db:
            frozen = {r['id']: contract(r['input_json'])
                      for r in db.execute("SELECT id,input_json FROM writing_runs WHERE kind='automatic'")}

        releases[0].set()
        until(worker, lambda: len(entered) == 4)
        assert entered[3] == fourth['brief']['topic']
        assert app.state.store.job(queued[3]['job']['id'])['status'] == 'running'
        assert not releases[1].is_set() and not releases[2].is_set()
        for release in releases:
            release.set()
        until(worker, lambda: all(app.state.store.job(value['job']['id'])['status'] == 'succeeded'
                                 for value in queued))
    finally:
        for release in releases:
            release.set()
        worker.close()

    assert peak == 3
    for project, value in zip(projects, queued):
        current = client.get('/api/writing/projects/' + project['id']).json()['project']
        assert all(section['content'] for section in current['sections'])
        assert current['automatic']['report']['title'] == project['title']
        assert current['automatic']['report']['outputAvailable']
        assert current['automatic']['status'] in ('completed', 'completed_with_issues')
        assert not any(run['status'] in ('queued', 'running') for run in current['runs'])
        with app.state.store.connect() as db:
            parent = db.execute('SELECT * FROM writing_runs WHERE id=?', (value['runId'],)).fetchone()
            assert parent['model_id'] == model_id
            assert contract(parent['input_json']) == frozen[parent['id']]
            children = db.execute('''SELECT r.project_id,j.input_json FROM writing_runs r
                JOIN jobs j ON j.id=r.job_id WHERE json_extract(j.input_json,'$.automaticParentRunId')=?''',
                                  (value['runId'],)).fetchall()
            assert children
            assert all(child['project_id'] == project['id'] for child in children)
    with app.state.store.connect() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone()
