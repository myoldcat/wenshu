"""Manual takeover cancels only its delegated run and fences late replies."""
import copy
import json

import pytest

from server.store import dumps
from server.tests.test_writing_automatic import automatic, run_automatic, PROSE
from server.tests.test_writing_api import bench, create, LOCAL
from server.writing import WritingConflict, WritingService


def queue_child(service, gateway, parent, parent_job, project, section_id):
    fields = ('_automatic_parent_run', '_automatic_parent_job', '_automatic_step', '_automatic_model_config')
    previous = {name: getattr(service, name, None) for name in fields}
    try:
        service._automatic_parent_run = parent['runId']
        service._automatic_parent_job = parent_job
        service._automatic_step = 'section:' + section_id
        service._automatic_model_config = gateway.resolve(gateway.config['id'])
        return service.queue(project['id'], project['version'], 'section', section_id)
    finally:
        for name, value in previous.items():
            setattr(service, name, value)


def test_queued_takeover_is_atomic_idempotent_and_does_not_cancel_other_projects(automatic):
    store, gateway, service, project = automatic
    parent = service.queue_automatic(project['id'], project['version'])
    other = service.create({'title': '另一文章', 'brief': {'automation': 'automatic'},
        'sourceIds': project['sourceIds'], 'modelId': gateway.config['id']})
    other_run = service.queue_automatic(other['id'], other['version'])
    unrelated = store.queue('file', {'path': '/unused'}, '另一资料任务')
    before_history = service.history(project['id'])['versions']
    result = service.interrupt_automatic(project['id'], project['version'], parent['runId'])
    after = result['project']
    assert result['interrupted'] and result['cancelledJobIds'] == [parent['job']['id']]
    assert store.job(parent['job']['id'])['status'] == 'cancelled'
    assert store.job(other_run['job']['id'])['status'] == store.job(unrelated['id'])['status'] == 'queued'
    assert after['brief']['automation'] == 'manual' and after['version'] == project['version'] + 1
    assert after['sections'] == project['sections'] and after['sources'] == project['sources']
    assert after['automatic']['status'] == 'cancelled'
    assert any(item['code'] == 'manual_takeover' for item in after['automatic']['report']['issues'])
    assert len(service.history(project['id'])['versions']) == len(before_history) + 1
    again = service.interrupt_automatic(project['id'], after['version'], parent['runId'])
    assert not again['interrupted'] and again['project']['version'] == after['version']
    assert not gateway.generated


def test_running_parent_and_uncheckpointed_child_are_cancelled_without_touching_prose_or_candidate(automatic):
    store, gateway, service, project = automatic
    project = service.outline(project['id'], project['version'], [
        {'title': '已写章节', 'goal': '', 'targetWords': 100},
        {'title': '待写章节', 'goal': '', 'targetWords': 100}])
    project = service.save_section(project['id'], project['sections'][0]['id'], project['version'],
                                   {'content': PROSE + '[E1]', 'locked': True})
    block = store.detail(project['sourceIds'][0])['blocks'][0]
    with store.connect() as db:
        current = service._load(db, project['id'])
        current['sections'][0]['evidence'] = [{'id': 'E1', 'sourceId': project['sourceIds'][0],
            'revisionId': project['sources'][0]['revisionId'], 'blockId': block['id'], 'quote': block['text'],
            'start': 0, 'end': len(block['text']), 'title': '固定资料', 'url': 'https://example.test/source', 'locator': {}}]
        service._save(db, current, 'controlled_manual_evidence')
    project = service.get(project['id'])
    project = service.confirm(project['id'], project['version'])
    parent = service.queue_automatic(project['id'], project['version'])
    parent_job = store.claim()
    child = queue_child(service, gateway, parent, parent_job, project, project['sections'][1]['id'])
    assert store.claim() is None  # Automatic children belong to their parent lane.
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        child_job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (child['job']['id'],)).fetchone())
        db.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=? AND status='queued'", (child_job['id'],))
    candidate = {'section': {'content': '已保存的生成候选。', 'evidence': []}}
    with store.connect() as db:
        db.execute('UPDATE writing_runs SET result_json=? WHERE id=?', (dumps(candidate), child['runId']))
        old_history = [dict(row) for row in db.execute('SELECT * FROM writing_history WHERE project_id=? ORDER BY version', (project['id'],))]
    before = service.get(project['id'])
    result = service.interrupt_automatic(project['id'], before['version'], parent['runId'])
    assert set(result['cancelledJobIds']) == {parent['job']['id'], child['job']['id']}
    assert result['project']['sections'] == before['sections']
    assert result['project']['sections'][0]['locked'] and result['project']['sections'][0]['manual']
    assert store.job(child['job']['id'])['status'] == 'cancelled'
    with store.connect() as db:
        assert json.loads(db.execute('SELECT result_json FROM writing_runs WHERE id=?', (child['runId'],)).fetchone()[0]) == candidate
        history = [dict(row) for row in db.execute('SELECT * FROM writing_history WHERE project_id=? ORDER BY version', (project['id'],))]
    assert history[:-1] == old_history and history[-1]['reason'] == 'automatic_interrupted_to_manual'
    WritingService(store, gateway).process(child_job)
    assert not gateway.generated and service.get(project['id'])['sections'] == before['sections']


def test_inflight_reply_cannot_overwrite_manual_takeover_and_new_manual_job_can_queue(automatic):
    store, gateway, service, project = automatic
    parent = service.queue_automatic(project['id'], project['version'])
    original = gateway.complete
    manual_job = None
    interrupted = False
    takeover_report = None
    section_id = None

    def reply_after_takeover(config, messages):
        nonlocal manual_job, interrupted, takeover_report, section_id
        response = original(config, messages)
        packet = json.loads(messages[-1]['content'])
        if packet['task'] == 'writing_section' and not interrupted:
            interrupted = True
            author = WritingService(store, gateway)
            current = author.get(project['id'])
            section_id = current['sections'][0]['id']
            switched = author.interrupt_automatic(project['id'], current['version'], parent['runId'])['project']
            takeover_report = copy.deepcopy(switched['automatic']['report'])
            saved = author.save_section(project['id'], section_id, switched['version'], {'content': '中断后人工写入的正文。'})
            manual_job = author.queue(project['id'], saved['version'], 'section', current['sections'][1]['id'])
        return response

    gateway.complete = reply_after_takeover
    service.process(store.claim())
    after = WritingService(store, gateway).get(project['id'])
    assert interrupted and after['brief']['automation'] == 'manual'
    assert after['sections'][0]['id'] == section_id and after['sections'][0]['content'] == '中断后人工写入的正文。'
    assert after['sections'][0]['manual'] and not after['sections'][1]['content']
    assert store.job(parent['job']['id'])['status'] == 'cancelled'
    assert store.job(manual_job['job']['id'])['status'] == 'queued'
    assert after['automatic']['report'] == takeover_report
    assert sum(task == 'writing_section' for task, _, _ in gateway.generated) == 1
    with store.connect() as db:
        old_children = db.execute('''SELECT j.status,r.applied FROM writing_runs r JOIN jobs j ON j.id=r.job_id
            WHERE json_extract(j.input_json,'$.automaticParentRunId')=?''', (parent['runId'],)).fetchall()
        assert old_children and all(row['status'] not in ('queued', 'running') for row in old_children)
        old_body = db.execute('''SELECT r.applied,c.response_text FROM writing_runs r JOIN writing_model_interactions c ON c.run_id=r.id
            WHERE r.project_id=? AND r.kind='section' AND r.id<>?''', (project['id'], manual_job['runId'])).fetchone()
    assert old_body and not old_body['applied'] and PROSE in old_body['response_text']


def test_stale_version_is_rejected_without_cancellation_then_current_version_can_take_over(automatic):
    store, _, service, project = automatic
    parent = service.queue_automatic(project['id'], project['version'])
    changed = service.patch(project['id'], project['version'], {'title': '人工最新题目'})
    with pytest.raises(WritingConflict):
        service.interrupt_automatic(project['id'], project['version'], parent['runId'])
    assert store.job(parent['job']['id'])['status'] == 'queued'
    assert service.get(project['id'])['brief']['automation'] == 'automatic'
    result = service.interrupt_automatic(project['id'], changed['version'], parent['runId'])
    assert result['project']['title'] == changed['title'] and result['project']['brief']['automation'] == 'manual'


def test_fixed_old_run_id_cannot_cancel_a_newer_parent(automatic):
    store, _, service, project = automatic
    first = service.queue_automatic(project['id'], project['version'])
    store.cancel(first['job']['id'])
    current = service.get(project['id'])
    second = service.queue_automatic(project['id'], current['version'])
    with pytest.raises(WritingConflict, match='新的'):
        service.interrupt_automatic(project['id'], current['version'], first['runId'])
    assert store.job(second['job']['id'])['status'] == 'queued'
    assert service.get(project['id'])['brief']['automation'] == 'automatic'


def test_parent_finishing_before_takeover_can_switch_mode_without_rewriting_completed_run(automatic):
    store, _, service, project = automatic
    parent, before = run_automatic(automatic)
    with store.connect() as db:
        recorded = db.execute('SELECT result_json FROM writing_runs WHERE id=?', (parent['runId'],)).fetchone()[0]
    result = service.interrupt_automatic(project['id'], before['version'], parent['runId'])
    assert not result['interrupted'] and not result['cancelledJobIds']
    assert result['project']['brief']['automation'] == 'manual'
    assert result['project']['sections'] == before['sections']
    assert store.job(parent['job']['id'])['status'] == 'succeeded'
    with store.connect() as db:
        assert db.execute('SELECT result_json FROM writing_runs WHERE id=?', (parent['runId'],)).fetchone()[0] == recorded


def test_interrupt_http_validates_version_and_fixed_run_id_before_switching(bench):
    client, app, provider, _, _ = bench
    project = create(bench, brief={'automation': 'automatic'})
    path = '/api/writing/projects/' + project['id']
    queued = client.post(path + '/automatic', headers=LOCAL, json={'expectedVersion': project['version']})
    assert queued.status_code == 202, queued.text
    run_id = queued.json()['runId']
    job_id = queued.json()['job']['id']
    before_calls = copy.deepcopy(provider.calls)
    invalid = [{}, {'expectedVersion': project['version']},
        {'expectedVersion': str(project['version']), 'runId': run_id},
        {'expectedVersion': True, 'runId': run_id},
        {'expectedVersion': project['version'], 'runId': ''},
        {'expectedVersion': project['version'], 'runId': 1},
        {'expectedVersion': project['version'], 'runId': run_id, 'cancelAll': True}]
    for payload in invalid:
        response = client.post(path + '/automatic/interrupt', headers=LOCAL, json=payload)
        assert response.status_code == 422, response.text
    assert app.state.store.job(job_id)['status'] == 'queued'
    missing = client.post(path + '/automatic/interrupt', headers=LOCAL,
        json={'expectedVersion': project['version'], 'runId': 'nonexistent-parent'})
    assert missing.status_code == 404
    result = client.post(path + '/automatic/interrupt', headers=LOCAL,
        json={'expectedVersion': project['version'], 'runId': run_id})
    assert result.status_code == 200, result.text
    assert result.json()['project']['brief']['automation'] == 'manual'
    assert app.state.store.job(job_id)['status'] == 'cancelled'
    stale = client.post(path + '/automatic/interrupt', headers=LOCAL,
        json={'expectedVersion': project['version'], 'runId': run_id})
    assert stale.status_code == 409
    assert provider.calls == before_calls
